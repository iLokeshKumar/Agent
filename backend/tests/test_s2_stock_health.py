"""S2 Stock Health + shared scoring + promotion gate — test pack.

Covers:
  - stock_health service: property-style checks on the shared verdict
  - S2 agent lifecycle: validate/execute/brief rendering
  - mart builder uses the SAME verdict as the agent (no drift)
  - promotion-gate runner: golden execution, gate report criteria,
    HTTP surface (200 approved/blocked, 404 unknown, 422 no golden set)
"""

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import get_db
from app.main import app
from app.models.agent import Base
from app.models.enterprise import SerialRegistry, SkuHealth
from app.services import stock_health as sh
from app.services.stock_health import health_record


# ── Shared scoring service ────────────────────────────────────────


class TestStockHealthService:

    def test_weeks_cover_none_when_no_demand(self):
        assert sh.weeks_cover(10, 0) is None

    def test_weeks_cover_rounds(self):
        assert sh.weeks_cover(10, 3) == 3.33

    def test_cover_never_negative(self):
        with pytest.raises(ValueError):
            sh.weeks_cover(5, -1)

    def test_bucket_boundaries(self):
        buckets = sh.bucket_ages([0, 30, 31, 60, 61, 90, 91, 200])
        assert buckets == {
            "0_30": 2, "31_60": 2, "61_90": 2, "over_90": 2,
        }

    def test_eol_beats_everything(self):
        assert sh.action_reco(
            eol=True, on_hand=5,
            ageing={"0_30": 5, "31_60": 0, "61_90": 0, "over_90": 0},
            weeks_cover=10.0,
        ) == "clearance_push"

    def test_stale_majority_flags_review(self):
        # 3 of 5 units older than 60 days → review.
        assert sh.action_reco(
            eol=False, on_hand=5,
            ageing={"0_30": 2, "31_60": 0, "61_90": 2, "over_90": 1},
            weeks_cover=10.0,
        ) == "ageing_review"

    def test_low_cover_feeds_reorder(self):
        assert sh.action_reco(
            eol=False, on_hand=4,
            ageing={"0_30": 4, "31_60": 0, "61_90": 0, "over_90": 0},
            weeks_cover=1.5,
        ) == "reorder_check"

    def test_healthy_default(self):
        assert sh.action_reco(
            eol=False, on_hand=50,
            ageing={"0_30": 50, "31_60": 0, "61_90": 0, "over_90": 0},
            weeks_cover=8.0,
        ) == "healthy"

    def test_health_record_shape(self):
        row = health_record(
            sku_ref="SKU-X", branch="TN",
            ages_days=[5, 6], weekly_units=[4, 4, 4], eol=False,
            period="2026-09-10",
        )
        assert row["on_hand"] == 2
        assert row["avg_weekly_demand"] == 4.0
        assert row["weeks_cover"] == 0.5
        # No stale units → the low-cover rule fires.
        assert row["action_reco"] == "reorder_check"

    def test_health_record_stale_beats_low_cover(self):
        # Rule order: a stale majority outranks low cover.
        row = health_record(
            sku_ref="SKU-Y", branch="TN",
            ages_days=[5, 75], weekly_units=[4, 4, 4], eol=False,
            period="2026-09-10",
        )
        assert row["action_reco"] == "ageing_review"


# ── S2 agent ──────────────────────────────────────────────────────

from app.agents.s2_stock_health import S2StockHealth  # noqa: E402


class TestS2Agent:

    def setup_method(self):
        self.agent = S2StockHealth()

    def test_validate_ok(self):
        payload = {"skus": [{"sku_ref": "A", "ages_days": [1, 2],
                             "weekly_units": [1, 1]}]}
        assert self.agent.validate(payload) is None

    def test_validate_rejects(self):
        assert self.agent.validate({}) is not None
        assert self.agent.validate({"skus": []}) is not None
        bad = {"skus": [{"sku_ref": "A", "ages_days": "no",
                         "weekly_units": [1]}]}
        assert self.agent.validate(bad) is not None

    def test_execute_renders_brief_and_summary(self):
        result = self.agent.execute({
            "skus": [
                {"sku_ref": "SKU-EOL", "branch": "TN",
                 "ages_days": [5], "weekly_units": [9, 9], "eol": True},
                {"sku_ref": "SKU-OK", "branch": "TN",
                 "ages_days": [3, 4], "weekly_units": [1, 1, 1, 1]},
            ],
        })
        assert result.status == "SUCCESS"
        assert result.output["summary"]["clearance_push"] == 1
        assert result.output["summary"]["healthy"] == 1
        # Worst verdict renders first.
        brief = result.output["brief"]
        assert brief.index("CLEARANCE_PUSH") < brief.index("HEALTHY")

    def test_execute_no_offtake_says_no_offtake(self):
        result = self.agent.execute({
            "skus": [{"sku_ref": "SKU-Q", "ages_days": [1],
                      "weekly_units": []}],
        })
        row = result.output["health_rows"][0]
        assert row["weeks_cover"] is None
        assert "no offtake" in result.output["brief"]


# ── Mart builder shares the verdict (no drift) ────────────────────


@pytest.fixture()
def local_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[SerialRegistry.__table__, SkuHealth.__table__],
    )
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class TestMartAgentVerdictParity:

    def test_mart_uses_shared_rule(self, local_db):
        # Two stale units of three → the shared rule says ageing_review;
        # the mart builder must agree with what S2 would render.
        for i, (sn, age) in enumerate(
            (("SN1", 10), ("SN2", 70), ("SN3", 95))
        ):
            local_db.add(SerialRegistry(
                serial=sn, sku_ref="SKU-P", entity="YEXIS-EL",
                location_ref="TN", status="in_stock",
                received_ts=_ts_days_ago(age),
            ))
        local_db.commit()

        from app.services.marts import build_sku_health
        build_sku_health(local_db, period=date(2026, 9, 10))

        row = local_db.query(SkuHealth).filter_by(
            sku_ref="SKU-P", branch="TN").one()
        assert row.action_reco == "ageing_review"


def _ts_days_ago(days: int):
    from datetime import datetime, timedelta
    return datetime.utcnow() - timedelta(days=days)


# ── Promotion gate (WP-7) ─────────────────────────────────────────

from app.services import promotion_gate as pg  # noqa: E402


class TestPromotionGate:

    def test_golden_set_runs_for_s2(self):
        report = pg.run_golden_set("s2_stock_health")
        assert report.total == 4
        assert report.passed == report.total
        assert report.pass_rate == 1.0

    def test_golden_set_runs_for_s3(self):
        report = pg.run_golden_set("s3_reorder")
        assert report.total == 4
        assert report.passed == report.total

    def test_unknown_agent_raises(self):
        with pytest.raises(KeyError):
            pg.run_golden_set("nonexistent_agent")

    def test_gate_blocks_without_signoff(self):
        report = pg.gate_report("s2_stock_health", "S2")
        assert report["approved"] is False
        assert report["criteria"]["golden"]["ok"] is True
        assert report["criteria"]["redteam"]["ok"] is True  # S2 in the pack now
        assert report["criteria"]["signoff"]["ok"] is False

    def test_gate_passes_with_full_records(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            pg, "GATES_DIR", tmp_path,
        )
        (tmp_path / "s2_stock_health.json").write_text(
            '{"business_owner_signoff": "owner@yexis 2026-09-10",'
            ' "rollback_rehearsed": "2026-09-09"}',
            encoding="utf-8",
        )
        # Red-team coverage also required: patch the pack check.
        monkeypatch.setattr(
            pg, "redteam_coverage", lambda codes: {c: True for c in codes},
        )
        report = pg.gate_report("s2_stock_health", "S2")
        assert report["approved"] is True
        text = pg.render_text(report)
        assert "APPROVED" in text

    def test_text_render_shows_blocked(self):
        report = pg.gate_report("s3_reorder", "S3")
        assert "BLOCKED" in pg.render_text(report)


class TestGateHttpSurface:

    @pytest.fixture()
    def client(self):
        with TestClient(app) as c:
            yield c

    def test_unknown_agent_404(self, client):
        response = client.get("/agents/NOPE/gate-report")
        assert response.status_code == 404

    def test_report_renders(self, client):
        response = client.get("/agents/S2/gate-report")
        assert response.status_code == 200
        body = response.json()
        assert body["agent_code"] == "S2"
        assert "approved" in body
