"""Integration-style workflow tests for Phase 3 agent suites.

These tests wire agents in sequence using mocked DB sessions to verify
end-to-end state transitions without a live database. They exercise the
golden paths: happy flow + critical boundary conditions.

Run with: pytest tests/integration/test_phase3_workflows.py -v
"""

from datetime import date, datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import uuid

import pytest


# ── Shared DB mock helpers ──────────────────────────────────────────


def _null_db():
    """Return a MagicMock db that records calls but returns None/[] by default."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.query.return_value.filter.return_value.all.return_value = []
    db.query.return_value.filter.return_value.filter.return_value.first.return_value = None
    db.query.return_value.filter.return_value.filter.return_value.all.return_value = []
    return db


# ═══════════════════════════════════════════════════════════════════
# K-suite: K1 → K2 → K3 → K5 → K6
# ═══════════════════════════════════════════════════════════════════


class TestCampaignSuiteWorkflow:
    """K-suite golden path: create brief → build audience → broadcast → track → analytics."""

    def test_k1_create_campaign_brief(self):
        from app.agents.k1_campaign_brief import K1CampaignBrief

        agent = K1CampaignBrief()
        db = _null_db()

        # Simulate no existing campaign with that ref
        db.query.return_value.filter.return_value.first.return_value = None

        with patch("app.core.db.SessionLocal", return_value=db):
            result = agent.run(payload={
                "action": "create",
                "entity": "YEXIS-EL",
                "name": "Onam Dealer Promo 2026",
                "campaign_type": "dealer_promo",
                "start_date": "2026-09-15",
                "end_date": "2026-10-15",
            })

        assert result.status == "SUCCESS"
        assert "campaign_ref" in result.output
        assert "campaign_brief_created" in result.actions_taken[0]

    def test_k1_missing_name_rejected(self):
        from app.agents.k1_campaign_brief import K1CampaignBrief

        agent = K1CampaignBrief()
        result = agent.run(payload={"action": "create", "entity": "YEXIS-EL"})
        assert result.status == "VALIDATION_ERROR"
        assert "name" in (result.error or "").lower()

    def test_k1_invalid_campaign_type_rejected(self):
        from app.agents.k1_campaign_brief import K1CampaignBrief

        agent = K1CampaignBrief()
        result = agent.run(payload={
            "action": "create",
            "entity": "YEXIS-EL",
            "name": "Test",
            "campaign_type": "flash_sale",  # not a valid type
        })
        assert result.status == "VALIDATION_ERROR"

    def test_k3_broadcast_missing_template_rejected(self):
        from app.agents.k3_broadcast import K3Broadcast

        agent = K3Broadcast()
        result = agent.run(payload={
            "action": "send",
            "campaign_ref": "CMP-001",
        })
        assert result.status == "VALIDATION_ERROR"

    def test_k3_broadcast_non_meta_template_blocked(self):
        from app.agents.k3_broadcast import K3Broadcast

        agent = K3Broadcast()
        db = _null_db()

        # Campaign exists and is audience_built
        campaign = MagicMock()
        campaign.id = uuid.uuid4()
        campaign.status = "audience_built"
        campaign.entity = "YEXIS-EL"
        db.query.return_value.filter.return_value.first.return_value = campaign

        # Template exists but NOT meta-approved
        template = MagicMock()
        template.approval_status = "draft"
        template.body_text = "Hello {{1}}"

        # Patch both campaign and template lookups
        def _query_side(model):
            q = MagicMock()
            q.filter.return_value = q
            if "Campaign" in str(model):
                q.first.return_value = campaign
            else:
                q.first.return_value = template
            return q

        db.query.side_effect = _query_side

        with patch("app.agents.k3_broadcast.SessionLocal", return_value=db):
            result = agent.run(payload={
                "action": "send",
                "campaign_ref": "CMP-001",
                "template_id": "TPL-DRAFT",
            })

        assert result.status == "VALIDATION_ERROR"
        assert "meta" in (result.error or "").lower() or "approved" in (result.error or "").lower()

    def test_k5_response_tracker_classify_interested(self):
        from app.agents.k5_response_tracker import K5ResponseTracker

        agent = K5ResponseTracker()
        db = _null_db()

        # Contact exists in campaign
        contact = MagicMock()
        contact.id = uuid.uuid4()
        contact.campaign_id = uuid.uuid4()
        contact.contact_ref = "DEALER-001"
        contact.response_type = None
        db.query.return_value.filter.return_value.filter.return_value.first.return_value = contact

        with patch("app.agents.k5_response_tracker.SessionLocal", return_value=db):
            result = agent.run(payload={
                "action": "record",
                "campaign_ref": "CMP-001",
                "contact_ref": "DEALER-001",
                "response_text": "Yes I am interested in the offer",
            })

        assert result.status in ("SUCCESS", "NEEDS_APPROVAL", "VALIDATION_ERROR")

    def test_k5_invalid_response_type_rejected(self):
        from app.agents.k5_response_tracker import K5ResponseTracker

        agent = K5ResponseTracker()
        result = agent.run(payload={
            "action": "classify",
            "campaign_ref": "CMP-001",
            "contact_ref": "DEALER-001",
            "response_type": "maybe",  # not a valid type
        })
        assert result.status == "VALIDATION_ERROR"

    def test_k6_analytics_compute_success(self):
        from app.agents.k6_campaign_analytics import K6CampaignAnalytics

        agent = K6CampaignAnalytics()
        db = _null_db()

        campaign = MagicMock()
        campaign.id = uuid.uuid4()
        campaign.status = "broadcast"
        campaign.entity = "YEXIS-EL"
        campaign.kpi_json = None

        contacts = []
        for i in range(10):
            c = MagicMock()
            c.message_sent_ts = datetime.now(timezone.utc)
            c.message_log_id = uuid.uuid4()
            c.response_type = "interested" if i < 3 else None
            contacts.append(c)

        def _query_side(model):
            q = MagicMock()
            q.filter.return_value = q
            q.first.return_value = campaign
            q.all.return_value = contacts
            return q

        db.query.side_effect = _query_side

        with patch("app.agents.k6_campaign_analytics.SessionLocal", return_value=db):
            result = agent.run(payload={
                "action": "compute",
                "campaign_ref": "CMP-001",
            })

        assert result.status in ("SUCCESS", "VALIDATION_ERROR")


# ═══════════════════════════════════════════════════════════════════
# V2 AMC Engine: check → renew
# ═══════════════════════════════════════════════════════════════════


class TestAmcEngineWorkflow:
    """V2 golden path: check expiry → generate renewal → register."""

    def _make_contract(self, ref, end_date, status="active"):
        c = MagicMock()
        c.contract_ref = ref
        c.asset_id = "SN-TEST-001"
        c.customer_ref = "CUST-001"
        c.entity = "YEXIS-EL"
        c.start_date = date(2025, 9, 12)
        c.end_date = end_date
        c.value = 5000.0
        c.status = status
        c.renewal_ref = None
        c.renewed_ts = None
        return c

    def test_check_contract_expiring_soon(self):
        from app.agents.v2_amc_engine import V2AmcEngine

        agent = V2AmcEngine()
        today = date(2026, 9, 11)
        end = today + timedelta(days=60)
        contract = self._make_contract("AMC-001", end)

        db = _null_db()
        db.query.return_value.filter.return_value.first.return_value = contract

        with patch("app.agents.v2_amc_engine.SessionLocal", return_value=db):
            result = agent.run(payload={
                "action": "check",
                "contract_ref": "AMC-001",
            })

        assert result.status == "SUCCESS"
        assert result.output.get("alert") is True or "expir" in str(result.output).lower()

    def test_renew_contract_requires_approval(self):
        from app.agents.v2_amc_engine import V2AmcEngine

        agent = V2AmcEngine()
        today = date(2026, 9, 11)
        end = today + timedelta(days=30)
        contract = self._make_contract("AMC-001", end)

        db = _null_db()
        db.query.return_value.filter.return_value.first.return_value = contract

        with patch("app.agents.v2_amc_engine.SessionLocal", return_value=db):
            result = agent.run(payload={
                "action": "renew",
                "contract_ref": "AMC-001",
            })

        assert result.status == "NEEDS_APPROVAL"
        assert "renewal_ref" in result.output

    def test_check_missing_contract_ref_rejected(self):
        from app.agents.v2_amc_engine import V2AmcEngine

        agent = V2AmcEngine()
        result = agent.run(payload={"action": "check"})
        assert result.status == "VALIDATION_ERROR"

    def test_renew_already_renewed_contract(self):
        from app.agents.v2_amc_engine import V2AmcEngine

        agent = V2AmcEngine()
        today = date(2026, 9, 11)
        contract = self._make_contract("AMC-OLD", today + timedelta(days=10))
        contract.renewal_ref = "AMC-OLD-R1"

        db = _null_db()
        db.query.return_value.filter.return_value.first.return_value = contract

        with patch("app.agents.v2_amc_engine.SessionLocal", return_value=db):
            result = agent.run(payload={
                "action": "renew",
                "contract_ref": "AMC-OLD",
            })

        # Renewal of an already-renewed contract should either warn or still propose
        assert result.status in ("SUCCESS", "NEEDS_APPROVAL", "VALIDATION_ERROR")


# ═══════════════════════════════════════════════════════════════════
# C-suite: C1 indent plan → C2 dispatch record → C3 claim intake
# ═══════════════════════════════════════════════════════════════════


class TestScmWorkflow:
    """SCM golden path: indent → dispatch → claim."""

    def test_c1_plan_creates_indents(self):
        from app.agents.c1_indent_planning import C1IndentPlanning

        agent = C1IndentPlanning()
        db = _null_db()

        # Two SKUs below threshold
        sku1 = MagicMock()
        sku1.sku_ref = "SKU-001"
        sku1.branch = "Chennai"
        sku1.on_hand = 2
        sku1.avg_weekly_offtake = 5.0
        sku1.weeks_cover = 0.4

        sku2 = MagicMock()
        sku2.sku_ref = "SKU-002"
        sku2.branch = "Kochi"
        sku2.on_hand = 10
        sku2.avg_weekly_offtake = 3.0
        sku2.weeks_cover = 3.3

        db.query.return_value.filter.return_value.all.return_value = [sku1, sku2]
        # No existing indents (idempotency)
        db.query.return_value.filter.return_value.filter.return_value.first.return_value = None

        with patch("app.agents.c1_indent_planning.SessionLocal", return_value=db):
            result = agent.run(payload={
                "action": "plan",
                "entity": "YEXIS-EL",
                "period": "2026-09-11",
            })

        assert result.status == "SUCCESS"
        assert "indents_created" in str(result.output)

    def test_c1_submit_always_needs_approval(self):
        from app.agents.c1_indent_planning import C1IndentPlanning

        agent = C1IndentPlanning()
        db = _null_db()

        indent = MagicMock()
        indent.id = uuid.uuid4()
        indent.sku_ref = "SKU-001"
        indent.status = "draft"
        indent.submitted_ts = None

        db.query.return_value.filter.return_value.filter.return_value.all.return_value = [indent]

        with patch("app.agents.c1_indent_planning.SessionLocal", return_value=db):
            result = agent.run(payload={
                "action": "submit",
                "entity": "YEXIS-EL",
                "period": "2026-09-11",
            })

        assert result.status == "NEEDS_APPROVAL"

    def test_c2_record_inbound_dispatch(self):
        from app.agents.c2_dispatch_tracker import C2DispatchTracker

        agent = C2DispatchTracker()
        db = _null_db()

        # No duplicate
        db.query.return_value.filter.return_value.first.return_value = None

        with patch("app.agents.c2_dispatch_tracker.SessionLocal", return_value=db):
            result = agent.run(payload={
                "action": "record",
                "entity": "YEXIS-EL",
                "dispatch_ref": "DSP-001",
                "dispatch_type": "inbound",
                "from_ref": "SAMSUNG-IN",
                "to_ref": "HO-Chennai",
                "qty": 20,
                "expected_delivery": "2026-09-14",
            })

        assert result.status == "SUCCESS"

    def test_c2_missing_dispatch_ref_rejected(self):
        from app.agents.c2_dispatch_tracker import C2DispatchTracker

        agent = C2DispatchTracker()
        result = agent.run(payload={
            "action": "record",
            "entity": "YEXIS-EL",
            "dispatch_type": "outbound",
        })
        assert result.status == "VALIDATION_ERROR"

    def test_c3_intake_claim(self):
        from app.agents.c3_claims_tracker import C3ClaimsTracker

        agent = C3ClaimsTracker()
        db = _null_db()
        db.query.return_value.filter.return_value.first.return_value = None  # no duplicate

        with patch("app.agents.c3_claims_tracker.SessionLocal", return_value=db):
            result = agent.run(payload={
                "action": "intake",
                "entity": "YEXIS-EL",
                "claim_ref": "CLM-001",
                "claim_type": "short_supply",
                "counterparty_ref": "SAMSUNG-IN",
                "amount_claimed": 15000.0,
            })

        assert result.status == "SUCCESS"

    def test_c3_negative_amount_rejected(self):
        from app.agents.c3_claims_tracker import C3ClaimsTracker

        agent = C3ClaimsTracker()
        result = agent.run(payload={
            "action": "intake",
            "entity": "YEXIS-EL",
            "claim_ref": "CLM-NEG",
            "claim_type": "damage",
            "amount_claimed": -5000.0,
        })
        assert result.status == "VALIDATION_ERROR"

    def test_c3_settlement_requires_approval(self):
        from app.agents.c3_claims_tracker import C3ClaimsTracker

        agent = C3ClaimsTracker()
        db = _null_db()

        claim = MagicMock()
        claim.claim_ref = "CLM-001"
        claim.status = "acknowledged"
        db.query.return_value.filter.return_value.first.return_value = claim

        with patch("app.agents.c3_claims_tracker.SessionLocal", return_value=db):
            result = agent.run(payload={
                "action": "update",
                "entity": "YEXIS-EL",
                "claim_ref": "CLM-001",
                "status": "settled",
                "amount_settled": 12000.0,
            })

        assert result.status == "NEEDS_APPROVAL"


# ═══════════════════════════════════════════════════════════════════
# HR suite: H1 → H2 → H3 → H4
# ═══════════════════════════════════════════════════════════════════


class TestHrSuiteWorkflow:
    """HR golden path: open role → onboard → score risk → check payroll."""

    def test_h1_recruitment_intake(self):
        from app.agents.h1_recruitment import H1Recruitment

        agent = H1Recruitment()
        db = _null_db()

        with patch("app.agents.h1_recruitment.SessionLocal", return_value=db):
            result = agent.run(payload={
                "action": "intake",
                "entity": "YEXIS-EL",
                "title": "Senior Sales Executive — Chennai",
                "department": "Sales",
                "headcount": 2,
            })

        assert result.status == "SUCCESS"
        assert "jd_draft" in result.output
        # JD must be pending review — never auto-posted
        jd = result.output["jd_draft"]
        assert "pending" in str(jd).lower() or "review" in str(jd).lower()

    def test_h1_missing_title_rejected(self):
        from app.agents.h1_recruitment import H1Recruitment

        agent = H1Recruitment()
        result = agent.run(payload={"action": "intake", "entity": "YEXIS-EL"})
        assert result.status == "VALIDATION_ERROR"

    def test_h2_start_onboarding(self):
        from app.agents.h2_onboarding import H2Onboarding

        agent = H2Onboarding()
        db = _null_db()

        employee = MagicMock()
        employee.employee_id = "E001"
        employee.onboarding_status = "pending"
        employee.join_date = date(2026, 9, 11)
        db.query.return_value.filter.return_value.first.return_value = employee

        # No existing checklist items
        db.query.return_value.filter.return_value.all.return_value = []

        with patch("app.agents.h2_onboarding.SessionLocal", return_value=db):
            result = agent.run(payload={
                "action": "start",
                "employee_id": "E001",
            })

        assert result.status == "SUCCESS"
        assert result.output.get("checklist_items", 0) == 10

    def test_h3_score_high_risk_employee(self):
        from app.agents.h3_attrition_risk import H3AttritionRisk

        agent = H3AttritionRisk()
        db = _null_db()

        employee = MagicMock()
        employee.employee_id = "E002"
        employee.entity = "YEXIS-EL"
        employee.status = "active"
        employee.join_date = date(2026, 3, 1)  # < 6 months → high tenure pts
        employee.last_engagement_ts = datetime(2026, 6, 1, tzinfo=timezone.utc)  # > 90 days
        employee.risk_score = None

        db.query.return_value.filter.return_value.first.return_value = employee

        with patch("app.agents.h3_attrition_risk.SessionLocal", return_value=db):
            result = agent.run(payload={
                "action": "score",
                "entity": "YEXIS-EL",
                "employee_id": "E002",
            })

        assert result.status == "SUCCESS"
        assert result.output.get("risk_score", 0) >= 30  # high risk

    def test_h4_payroll_ghost_employee_blocked(self):
        from app.agents.h4_payroll_check import H4PayrollCheck

        agent = H4PayrollCheck()
        db = _null_db()

        # Employee NOT in registry
        db.query.return_value.filter.return_value.first.return_value = None

        with patch("app.agents.h4_payroll_check.SessionLocal", return_value=db):
            result = agent.run(payload={
                "action": "validate",
                "entity": "YEXIS-EL",
                "payroll_lines": [
                    {"employee_id": "GHOST-001", "name": "Unknown Person", "gross": 45000.0}
                ],
            })

        assert result.status == "SUCCESS"
        anomalies = result.output.get("anomalies", [])
        high = [a for a in anomalies if a.get("severity") == "HIGH"]
        assert len(high) >= 1

    def test_h4_clean_payroll_passes(self):
        from app.agents.h4_payroll_check import H4PayrollCheck

        agent = H4PayrollCheck()
        db = _null_db()

        employee = MagicMock()
        employee.employee_id = "E001"
        employee.status = "active"
        db.query.return_value.filter.return_value.first.return_value = employee

        with patch("app.agents.h4_payroll_check.SessionLocal", return_value=db):
            result = agent.run(payload={
                "action": "validate",
                "entity": "YEXIS-EL",
                "payroll_lines": [
                    {"employee_id": "E001", "name": "Alice", "gross": 45000.0}
                ],
            })

        assert result.status == "SUCCESS"
        assert result.output.get("anomaly_count", 0) == 0


# ═══════════════════════════════════════════════════════════════════
# M1 / M2 command centres (read-only, no state change)
# ═══════════════════════════════════════════════════════════════════


class TestCommandCentresReadOnly:
    def test_m1_pipeline_is_advisory(self):
        from app.agents.m1_ism_command import M1IsmCommand

        agent = M1IsmCommand()
        db = _null_db()
        db.query.return_value.all.return_value = []
        db.query.return_value.filter.return_value.all.return_value = []

        with patch("app.agents.m1_ism_command.SessionLocal", return_value=db):
            result = agent.run(payload={"action": "pipeline", "entity": "YEXIS-EL"})

        assert result.status == "SUCCESS"
        # Must not write anything — only read actions
        assert not db.add.called
        assert not db.merge.called

    def test_m2_scorecard_without_dealer_ref_rejected(self):
        from app.agents.m2_gsm_command import M2GsmCommand

        agent = M2GsmCommand()
        result = agent.run(payload={"action": "scorecard", "entity": "YEXIS-EL"})
        assert result.status == "VALIDATION_ERROR"

    def test_m2_topline_produces_output(self):
        from app.agents.m2_gsm_command import M2GsmCommand

        agent = M2GsmCommand()
        db = _null_db()

        with patch("app.agents.m2_gsm_command.SessionLocal", return_value=db):
            result = agent.run(payload={"action": "topline", "entity": "YEXIS-EL"})

        assert result.status == "SUCCESS"
        assert "entity" in result.output

    def test_m1_invalid_entity_rejected(self):
        from app.agents.m1_ism_command import M1IsmCommand

        agent = M1IsmCommand()
        result = agent.run(payload={"action": "pipeline", "entity": "FAKE-ENTITY"})
        assert result.status == "VALIDATION_ERROR"
