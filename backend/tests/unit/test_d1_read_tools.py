"""Unit tests for D1 Ask-the-Business read tools (Phase 3 additions).

Runs entirely in memory via MagicMock — no DB or Zoho required.
"""

from datetime import date, datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ──────────────────────────────────────────────────────────


def _make_db():
    """Return a fresh MagicMock db session."""
    return MagicMock()


def _make_query_chain(rows):
    """Mock chain: db.query(...).filter(...).order_by(...).limit(...).all() → rows."""
    q = MagicMock()
    q.filter.return_value = q
    q.order_by.return_value = q
    q.limit.return_value = q
    q.all.return_value = rows
    q.notin_.return_value = q
    return q


# ── read_amc_contracts ────────────────────────────────────────────────


class TestReadAmcContracts:
    def _make_contract(self, ref, status, end_date, renewal_ref=None):
        c = MagicMock()
        c.contract_ref = ref
        c.asset_id = f"SN-{ref}"
        c.customer_ref = "CUST-001"
        c.entity = "YEXIS-EL"
        c.start_date = date(2025, 9, 11)
        c.end_date = end_date
        c.value = 5000.0
        c.status = status
        c.renewal_ref = renewal_ref
        return c

    def test_returns_list_for_active_contracts(self):
        from skills.d1_ask_business.read_tools import read_amc_contracts

        today = date(2026, 9, 11)
        contracts = [
            self._make_contract("AMC-001", "active", date(2026, 12, 1)),
            self._make_contract("AMC-002", "active", date(2026, 10, 15)),
        ]
        db = _make_db()
        db.query.return_value = _make_query_chain(contracts)

        result = read_amc_contracts(db, entity="YEXIS-EL", status="active", today=today)

        assert len(result) == 2
        assert result[0]["contract_ref"] == "AMC-001"
        assert result[0]["days_to_expiry"] == (date(2026, 12, 1) - today).days
        assert result[0]["status"] == "active"

    def test_within_days_filter(self):
        from skills.d1_ask_business.read_tools import read_amc_contracts

        db = _make_db()
        db.query.return_value = _make_query_chain([])
        result = read_amc_contracts(db, within_days=30)
        assert result == []

    def test_renewed_contract_shows_renewal_ref(self):
        from skills.d1_ask_business.read_tools import read_amc_contracts

        today = date(2026, 9, 11)
        contracts = [
            self._make_contract("AMC-003", "renewed", date(2026, 11, 1), renewal_ref="AMC-003-R1"),
        ]
        db = _make_db()
        db.query.return_value = _make_query_chain(contracts)
        result = read_amc_contracts(db, today=today)
        assert result[0]["renewal_ref"] == "AMC-003-R1"


# ── read_campaign_summary ─────────────────────────────────────────────


class TestReadCampaignSummary:
    def _make_campaign(self, ref, status, kpi_json=None):
        c = MagicMock()
        c.campaign_ref = ref
        c.name = f"Campaign {ref}"
        c.campaign_type = "dealer_promo"
        c.entity = "YEXIS-EL"
        c.status = status
        c.start_date = date(2026, 9, 1)
        c.end_date = date(2026, 9, 30)
        c.budget = 50000.0
        c.kpi_json = kpi_json
        c.created_ts = datetime(2026, 9, 1, tzinfo=timezone.utc)
        return c

    def test_returns_campaigns_with_kpi(self):
        from skills.d1_ask_business.read_tools import read_campaign_summary

        kpi = {"delivery_rate_pct": 95.0, "response_rate_pct": 22.5}
        campaigns = [
            self._make_campaign("CMP-001", "closed", kpi),
            self._make_campaign("CMP-002", "broadcast"),
        ]
        db = _make_db()
        db.query.return_value = _make_query_chain(campaigns)

        result = read_campaign_summary(db)

        assert len(result) == 2
        assert result[0]["campaign_ref"] == "CMP-001"
        assert result[0]["kpi"]["delivery_rate_pct"] == 95.0

    def test_empty_kpi_returned_as_empty_dict(self):
        from skills.d1_ask_business.read_tools import read_campaign_summary

        campaigns = [self._make_campaign("CMP-003", "draft", None)]
        db = _make_db()
        db.query.return_value = _make_query_chain(campaigns)

        result = read_campaign_summary(db)
        assert result[0]["kpi"] == {}


# ── read_dispatch_status ──────────────────────────────────────────────


class TestReadDispatchStatus:
    def _make_dispatch(self, ref, status, expected_delivery, pod_ts=None):
        d = MagicMock()
        d.dispatch_ref = ref
        d.dispatch_type = "outbound"
        d.from_ref = "HO"
        d.to_ref = "DEALER-001"
        d.qty = 5
        d.entity = "YEXIS-EL"
        d.status = status
        d.dispatch_ts = datetime(2026, 9, 1, tzinfo=timezone.utc)
        d.expected_delivery = expected_delivery
        d.pod_ts = pod_ts
        return d

    def test_returns_in_transit_dispatches(self):
        from skills.d1_ask_business.read_tools import read_dispatch_status

        dispatches = [
            self._make_dispatch("DSP-001", "in_transit", date(2026, 9, 10)),
        ]
        db = _make_db()
        db.query.return_value = _make_query_chain(dispatches)

        result = read_dispatch_status(db, today=date(2026, 9, 11))
        assert len(result) == 1
        assert result[0]["dispatch_ref"] == "DSP-001"
        assert result[0]["overdue_days"] == 1  # expected 9/10, today is 9/11

    def test_delivered_dispatch_has_pod_ts(self):
        from skills.d1_ask_business.read_tools import read_dispatch_status

        pod = datetime(2026, 9, 9, tzinfo=timezone.utc)
        dispatches = [
            self._make_dispatch("DSP-002", "delivered", date(2026, 9, 10), pod_ts=pod),
        ]
        db = _make_db()
        db.query.return_value = _make_query_chain(dispatches)

        result = read_dispatch_status(db, today=date(2026, 9, 11))
        assert result[0]["pod_ts"] is not None
        assert result[0]["overdue_days"] == 0  # pod_ts set → not overdue


# ── read_scm_claims ────────────────────────────────────────────────────


class TestReadScmClaims:
    def _make_claim(self, ref, claim_type, status, amount):
        c = MagicMock()
        c.claim_ref = ref
        c.claim_type = claim_type
        c.counterparty_ref = "SAMSUNG-IN"
        c.invoice_ref = "INV-001"
        c.amount_claimed = amount
        c.amount_settled = None
        c.status = status
        c.raised_ts = datetime(2026, 9, 1, tzinfo=timezone.utc)
        c.submitted_ts = None
        return c

    def test_returns_open_claims(self):
        from skills.d1_ask_business.read_tools import read_scm_claims

        claims = [
            self._make_claim("CLM-001", "short_supply", "open", 12000.0),
            self._make_claim("CLM-002", "damage", "submitted", 5000.0),
        ]
        db = _make_db()
        db.query.return_value = _make_query_chain(claims)

        result = read_scm_claims(db)
        assert len(result) == 2
        assert result[0]["claim_ref"] == "CLM-001"
        assert result[0]["amount_claimed"] == 12000.0

    def test_zero_amount_claim_included(self):
        from skills.d1_ask_business.read_tools import read_scm_claims

        claims = [self._make_claim("CLM-003", "logistics", "open", 0.0)]
        db = _make_db()
        db.query.return_value = _make_query_chain(claims)
        result = read_scm_claims(db)
        assert result[0]["amount_claimed"] == 0.0


# ── read_attrition_risk ────────────────────────────────────────────────


class TestReadAttritionRisk:
    def _make_employee(self, eid, name, dept, score):
        e = MagicMock()
        e.employee_id = eid
        e.name = name
        e.department = dept
        e.role = "Sales Executive"
        e.entity = "YEXIS-EL"
        e.status = "active"
        e.risk_score = score
        e.last_engagement_ts = None
        return e

    def test_tiers_assigned_correctly(self):
        from skills.d1_ask_business.read_tools import read_attrition_risk

        employees = [
            self._make_employee("E001", "Alice", "Sales", 65.0),   # High
            self._make_employee("E002", "Bob", "Finance", 45.0),   # Medium
            self._make_employee("E003", "Carol", "IT", 20.0),      # Low
        ]
        db = _make_db()
        db.query.return_value = _make_query_chain(employees)

        result = read_attrition_risk(db)
        tiers = {r["employee_id"]: r["risk_tier"] for r in result}
        assert tiers["E001"] == "High"
        assert tiers["E002"] == "Medium"
        assert tiers["E003"] == "Low"

    def test_sorted_by_risk_score_desc(self):
        from skills.d1_ask_business.read_tools import read_attrition_risk

        employees = [
            self._make_employee("E001", "Alice", "Sales", 20.0),
            self._make_employee("E002", "Bob", "Finance", 75.0),
            self._make_employee("E003", "Carol", "IT", 45.0),
        ]
        db = _make_db()
        db.query.return_value = _make_query_chain(employees)

        result = read_attrition_risk(db)
        scores = [r["risk_score"] for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_tier_filter_high_only(self):
        from skills.d1_ask_business.read_tools import read_attrition_risk

        employees = [
            self._make_employee("E001", "Alice", "Sales", 65.0),
            self._make_employee("E002", "Bob", "Finance", 45.0),
        ]
        db = _make_db()
        db.query.return_value = _make_query_chain(employees)

        result = read_attrition_risk(db, tier="High")
        assert all(r["risk_tier"] == "High" for r in result)
        assert len(result) == 1

    def test_none_risk_score_marked_unknown(self):
        from skills.d1_ask_business.read_tools import read_attrition_risk

        employees = [self._make_employee("E099", "Dan", "HR", None)]
        db = _make_db()
        db.query.return_value = _make_query_chain(employees)

        result = read_attrition_risk(db)
        assert result[0]["risk_tier"] == "Unknown"
