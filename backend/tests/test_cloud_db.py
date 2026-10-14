"""Cloud DB + nightly analytics — contract tests.

Covers:
  - cloud_db MCP read-only surface (tools work; no write routes exist)
  - 503-style clean refusal when the cloud store is unavailable
  - cloud shipper: skip when unconfigured, idempotent upserts when set
  - dealer_mart / sku_health / KPI builders on seeded local rows
  - nightly chain: per-stage isolation, watermark stamping
  - DPDP purge removes expired rows, keeps fresh ones
  - S3 reorder: deterministic math properties + agent lifecycle

Runs on two SQLite engines (local + simulated cloud) — no live
Postgres, no cloud store, no network.
"""

import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import mkdtemp
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import CloudDatabaseNotConfigured, get_db
from app.main import app
from app.models.agent import Base
from app.models.enterprise import (
    DealerMart,
    DocIndex,
    EmailMessage,
    InstalledBase,
    KpiSnapshot,
    PromiseRecord,
    SerialRegistry,
    SkuHealth,
)
from app.models.sync_watermark import SyncWatermark
from app.models.task import Task
from app.sync.cache import SyncCache
from app.sync.cloud_shipper import ship_local_to_cloud
from app.sync.nightly import run_nightly
from app.sync.purge import purge_expired

CLOUD_TABLES = None  # filled in the fixture (mirror tables only)


@pytest.fixture()
def env():
    """Two SQLite engines (local + simulated cloud) + app client."""
    local_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    cloud_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    tables = [
        DealerMart.__table__, KpiSnapshot.__table__,
        SkuHealth.__table__, InstalledBase.__table__,
        SerialRegistry.__table__, PromiseRecord.__table__,
        DocIndex.__table__, EmailMessage.__table__,
        Task.__table__, SyncWatermark.__table__,
    ]
    Base.metadata.create_all(bind=local_engine, tables=tables)
    Base.metadata.create_all(bind=cloud_engine, tables=tables)

    LocalSession = sessionmaker(bind=local_engine)
    CloudSession = sessionmaker(bind=cloud_engine)
    local = LocalSession()
    cloud = CloudSession()

    def override_local():
        yield local

    app.dependency_overrides[get_db] = override_local
    with TestClient(app) as c:
        yield c, local, cloud
    app.dependency_overrides.clear()
    local.close()
    cloud.close()


@pytest.fixture()
def client(env):
    return env[0]


def _tmp_cache() -> SyncCache:
    return SyncCache(cache_dir=Path(mkdtemp()))


def _patch_cloud(cloud_session):
    """Point the shipper at the simulated cloud session."""
    return patch(
        "app.sync.cloud_shipper.get_cloud_session_factory",
        return_value=lambda: cloud_session,
    )


class TestCloudDbReadOnlySurface:

    def test_no_write_routes_exist(self, client):
        response = client.get("/mcp/cloud_db/_write_surface")
        assert response.status_code == 200
        body = response.json()
        assert body["read_only"] is True
        assert body["write_methods"] == []

    def test_tools_catalogue(self, client):
        response = client.get("/mcp/cloud_db/tools")
        assert response.status_code == 200
        names = [t["name"] for t in response.json()["tools"]]
        assert "kpi.list" in names
        assert "dealer.get" in names
        assert "installed_base.get" in names


class TestCloudDbUnconfiguredRefusal:

    def test_unconfigured_store_refuses_cleanly(self, client):
        from mcp.cloud_db import server

        with patch.object(
            server,
            "get_cloud_session_factory",
            side_effect=CloudDatabaseNotConfigured(
                "CLOUD_DATABASE_URL is not set"
            ),
        ):
            response = client.get("/mcp/cloud_db/kpi")
        assert response.status_code == 503
        assert "CLOUD_DATABASE_URL" in response.json()["detail"]


class TestCloudShipper:

    def test_skip_when_unconfigured(self, env):
        _, local, _ = env
        with patch(
            "app.sync.cloud_shipper.get_cloud_session_factory",
            side_effect=CloudDatabaseNotConfigured("unset"),
        ):
            report = ship_local_to_cloud(local)
        assert report["status"] == "skipped"

    def test_upserts_mirrors_idempotently(self, env):
        _, local, cloud = env
        local.add(KpiSnapshot(
            kpi_id="dso_days", period=date(2026, 9, 9),
            entity="YEXIS-EL", branch="*", value=41.5,
            source_lane="nightly_kpi",
        ))
        local.add(DealerMart(
            dealer_ref="D-001", outstanding_amt=5000.0,
            refreshed_ts=datetime(2026, 9, 9, 2, 0, 0),
        ))
        local.commit()

        with _patch_cloud(cloud):
            report = ship_local_to_cloud(local)

        assert report["status"] == "completed"
        assert report["results"]["kpi_snapshot"]["rows"] == 1
        assert report["results"]["dealer_mart"]["rows"] == 1

        shipped = (
            cloud.query(KpiSnapshot)
            .filter_by(kpi_id="dso_days").one()
        )
        assert shipped.value == 41.5

        # Idempotent: re-ship must not duplicate.
        with _patch_cloud(cloud):
            ship_local_to_cloud(local)
        assert cloud.query(KpiSnapshot).count() == 1
        assert cloud.query(DealerMart).count() == 1

        # Watermark recorded on the local side.
        wm = (
            local.query(SyncWatermark)
            .filter_by(lane="CLOUD_SHIP", source="local_to_cloud").one()
        )
        assert wm.extra["kpi_snapshot"] == 1


class TestMartBuilders:

    def test_dealer_mart_aggregates_outstanding(self, env):
        _, local, _ = env
        local.add(PromiseRecord(account_ref="D-001", outcome="kept"))
        local.commit()

        cache = _tmp_cache()
        cache.put("invoices", [
            {"customer_id": "D-001", "balance": 1000,
             "due_date": "2026-01-01", "date": "2026-01-05"},
            {"customer_id": "D-001", "balance": 0,
             "due_date": "2026-09-01", "date": "2026-09-01"},
        ])
        cache.put("contacts", [
            {"contact_id": "D-001", "company_name": "Alpha Traders"},
        ])

        from app.services.marts import build_dealer_mart
        result = build_dealer_mart(local, cache=cache)

        assert result["upserted"] == 1
        row = local.query(DealerMart).filter_by(dealer_ref="D-001").one()
        assert row.outstanding_amt == 1000
        assert row.payment_behavior_score == 1.0
        assert row.branch == "Alpha Traders"

    def test_sku_health_counts_serials_and_flags_eol(self, env):
        _, local, _ = env
        for sn in ("SN1", "SN2"):
            local.add(SerialRegistry(
                serial=sn, sku_ref="SKU-A", entity="YEXIS-EL",
                location_ref="TN", status="in_stock",
                received_ts=datetime(2026, 9, 1),
            ))
        local.add(SerialRegistry(
            serial="SN3", sku_ref="SKU-A", entity="YEXIS-EL",
            location_ref="TN", status="sold",  # excluded from on-hand
        ))
        local.commit()

        from app.services.marts import build_sku_health
        result = build_sku_health(
            local, period=date(2026, 9, 10), eol_skus={"SKU-A"},
        )

        assert result["upserted"] == 1
        row = local.query(SkuHealth).filter_by(
            sku_ref="SKU-A", branch="TN").one()
        assert row.on_hand == 2
        assert row.eol_flag is True
        assert row.action_reco == "clearance_push"

    def test_kpis_computed_with_lineage(self, env):
        _, local, _ = env
        for outcome, n in (("kept", 3), ("broken", 1)):
            for _ in range(n):
                local.add(PromiseRecord(
                    account_ref="D-001", outcome=outcome,
                ))
        local.add(Task(
            id=uuid.uuid4(), task_type="invoice_matching",
            status="SUCCESS", payload={},
            result={"output": {"match_result": {
                "match_status": "FULLY_MATCHED"}}},
            correlation_id=uuid.uuid4(),
            created_at=datetime.now(),
        ))
        local.commit()

        cache = _tmp_cache()
        cache.put("invoices", [
            {"customer_id": "D", "balance": 0,
             "date": "2026-08-01", "due_date": "2026-08-15"},
        ])

        from app.services.marts import compute_kpis
        result = compute_kpis(local, cache=cache, period=date(2026, 9, 10))

        assert result["kpis"]["promise_reliability"] == 0.75
        assert result["kpis"]["p4_auto_match_rate"] == 1.0
        assert result["kpis"]["dso_days"] == (
            (date(2026, 9, 10) - date(2026, 8, 1)).days
        )

        row = local.query(KpiSnapshot).filter_by(
            kpi_id="promise_reliability").one()
        assert row.source_lane == "nightly_kpi"


class TestNightlyChain:

    def test_all_stages_run_and_report(self, env):
        _, local, cloud = env

        with _patch_cloud(cloud):
            report = run_nightly(local, cache=_tmp_cache())

        assert set(report) >= {
            "dealer_mart", "sku_health", "kpis",
            "cloud_ship", "dpdp_purge", "_summary",
        }
        assert report["_summary"]["ok"] is True

        wm = (
            local.query(SyncWatermark)
            .filter_by(lane="NIGHTLY", source="analytics_chain").one()
        )
        assert wm.cursor == date.today().isoformat()

    def test_one_failing_stage_does_not_block_others(self, env):
        _, local, cloud = env

        with patch(
            "app.sync.nightly.build_dealer_mart",
            side_effect=RuntimeError("boom"),
        ):
            with _patch_cloud(cloud):
                report = run_nightly(local, cache=_tmp_cache())

        assert report["dealer_mart"]["status"] == "error"
        assert report["_summary"]["failed_stages"] == ["dealer_mart"]
        assert report["dpdp_purge"]["status"] == "completed"
        assert report["cloud_ship"]["status"] == "completed"


class TestDpdpPurge:

    def test_expired_conversational_rows_purged(self, env):
        _, local, _ = env
        old = datetime.utcnow() - timedelta(days=120)
        fresh = datetime.utcnow()

        local.add(DocIndex(
            source="wa", doc_class="kyc",
            retention_class="conversational", purge_after=old,
        ))
        local.add(DocIndex(
            source="upload", doc_class="vendor_invoice",
            retention_class="transactional", purge_after=None,
        ))
        local.add(EmailMessage(
            direction="in", status="received",
            subject="old", created_at=old,
        ))
        local.add(EmailMessage(
            direction="in", status="received",
            subject="fresh", created_at=fresh,
        ))
        local.commit()

        result = purge_expired(local)

        assert result["deleted"]["doc_index"] == 1
        assert result["deleted"]["email_message"] == 1
        assert local.query(DocIndex).count() == 1      # transactional kept
        assert local.query(EmailMessage).count() == 1  # fresh kept


# ── S3 reorder ────────────────────────────────────────────────────

from app.agents.s3_reorder import S3Reorder  # noqa: E402
from app.services.reorder import (  # noqa: E402
    average_weekly_demand,
    needs_reorder,
    order_quantity,
    reorder_point,
)


class TestReorderMath:
    """Property-style checks on the deterministic core."""

    def test_reorder_point_monotonic_in_demand(self):
        base = reorder_point(10, 3, 20)
        higher = reorder_point(11, 3, 20)
        assert higher > base

    def test_reorder_point_monotonic_in_lead_time(self):
        base = reorder_point(10, 2, 5)
        higher = reorder_point(10, 5, 5)
        assert higher > base

    def test_reorder_point_never_negative(self):
        assert reorder_point(0, 0, 0) == 0

    def test_at_or_below_rop_triggers(self):
        assert needs_reorder(50, 50)
        assert needs_reorder(49, 50)
        assert not needs_reorder(51, 50)

    def test_order_quantity_respects_minimum(self):
        qty = order_quantity(
            on_hand=30, rop=40, avg_weekly=10,
            target_cover_weeks=4, min_order_qty=25,
        )
        # target = max(40, 40) = 40 → qty 15 → bumped to MOQ 25
        assert qty == 25

    def test_order_quantity_zero_when_covered(self):
        assert order_quantity(
            on_hand=100, rop=40, avg_weekly=10,
        ) == 0

    def test_average_demand_rejects_negatives(self):
        with pytest.raises(ValueError):
            average_weekly_demand([5, -1, 3])


class TestS3Agent:

    def setup_method(self):
        self.agent = S3Reorder()

    VALID = {
        "sku_demands": [
            {"sku_ref": "SKU-A", "branch": "TN",
             "weekly_units": [10, 10, 10, 10],
             "on_hand": 25, "lead_time_weeks": 3,
             "safety_stock": 10, "min_order_qty": 20},
            {"sku_ref": "SKU-B", "branch": "TN",
             "weekly_units": [5, 0, 5, 0],
             "on_hand": 100, "lead_time_weeks": 2},
        ],
    }

    def test_validate_ok(self):
        assert self.agent.validate(self.VALID) is None

    def test_validate_rejects_bad(self):
        assert self.agent.validate({}) is not None
        assert self.agent.validate({"sku_demands": []}) is not None
        bad = {"sku_demands": [{"sku_ref": "X",
                                "weekly_units": [-2],
                                "on_hand": 1,
                                "lead_time_weeks": 1}]}
        assert self.agent.validate(bad) is not None

    def test_execute_full_lifecycle(self):
        result = self.agent.execute(self.VALID)
        assert result.status == "SUCCESS"

        a, b = result.output["proposals"]
        # SKU-A: ROP = ceil(10*3+10) = 40; on_hand 25 ≤ 40 → reorder.
        assert a["needs_reorder"] is True
        assert a["reorder_point"] == 40
        # target = max(40, 10*4) = 40 → qty 15 → bumped to MOQ 20.
        assert a["recommended_qty"] == 20
        # SKU-B: avg 2.5, ROP = ceil(2.5*2) = 5; 100 > 5 → no order.
        assert b["needs_reorder"] is False
        assert b["recommended_qty"] == 0
        assert result.output["summary"]["needs_reorder"] == 1

    def test_execute_all_errors_fails(self):
        bad = {"sku_demands": [
            {"sku_ref": "X", "weekly_units": "nope",
             "on_hand": 1, "lead_time_weeks": 1},
        ]}
        result = self.agent.execute(bad)
        assert result.status == "FAILED"
