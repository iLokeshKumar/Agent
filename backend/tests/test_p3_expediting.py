"""P3 Expediting + deterministic core — test pack.

Covers:
  - expediting service: verdicts, escalation ladder, impact triage,
    mitigation gating (type-based, not cost-based), OTIF math
  - P3 agent lifecycle: validate/execute/brief rendering/scorecards
  - promotion-gate runner: golden execution, gate criteria,
    HTTP surface (200 blocked, 404 unknown)
"""

import pytest
from fastapi.testclient import TestClient

from app.services import expediting as ex


# ── Deterministic core ────────────────────────────────────────────


class TestVerdict:

    def test_slipped_wins_even_when_confirmed(self):
        assert ex.verdict(days_late=1, confirmed=True) == "SLIPPED"

    def test_unconfirmed_inside_window(self):
        assert ex.verdict(days_late=0, confirmed=False) == "UNCONFIRMED"

    def test_due_soon_at_t_minus_3(self):
        assert ex.verdict(days_late=-3, confirmed=None) == "DUE_SOON"

    def test_on_track_before_window(self):
        assert ex.verdict(days_late=-10, confirmed=None) == "ON_TRACK"

    def test_rejects_bad_inputs(self):
        with pytest.raises(ValueError):
            ex.verdict(days_late=True, confirmed=None)
        with pytest.raises(ValueError):
            ex.verdict(days_late=0, confirmed="yes")


class TestEscalationLadder:

    def test_deep_ladder_beyond_three_days_late(self):
        assert ex.escalation(days_late=4, confirmed=None) == "buyer_call"

    def test_whatsapp_when_late(self):
        assert ex.escalation(days_late=2, confirmed=None) == (
            "whatsapp_chase"
        )

    def test_whatsapp_when_unconfirmed_even_before_due(self):
        assert ex.escalation(days_late=-2, confirmed=False) == (
            "whatsapp_chase"
        )

    def test_email_in_confirm_window(self):
        assert ex.escalation(days_late=-3, confirmed=None) == (
            "email_confirmation"
        )

    def test_none_when_healthy(self):
        assert ex.escalation(days_late=-30, confirmed=None) is None


class TestImpact:

    def test_high_when_milestone_exposed(self):
        assert ex.impact(
            days_late=2, project_milestone=True,
        ) == "HIGH"

    def test_high_when_customer_order_exposed(self):
        assert ex.impact(
            days_late=1, customer_order=True,
        ) == "HIGH"

    def test_not_high_before_slip_even_with_milestone(self):
        assert ex.impact(
            days_late=0, project_milestone=True,
        ) == "NONE"

    def test_watch_on_stockout_only(self):
        assert ex.impact(days_late=5, stockout_risk=True) == "WATCH"


class TestMitigationGating:

    def test_no_cost_types_auto_propose(self):
        options = ex.mitigation_options(
            days_late=2,
            impact_level="WATCH",
            offered=[{"type": "partial_shipment", "cost": 0}],
        )
        assert options[0]["status"] == "proposed"

    def test_priced_type_pending_regardless_of_claimed_cost(self):
        """The type decides — a fabricated cost 0 cannot launder an
        air-freight proposal into the auto-approved lane."""
        options = ex.mitigation_options(
            days_late=2,
            impact_level="WATCH",
            offered=[{"type": "air_freight", "cost": 0}],
        )
        assert options[0]["status"] == "pending_approval"

    def test_default_playbook_when_nothing_offered(self):
        options = ex.mitigation_options(
            days_late=2, impact_level="HIGH",
        )
        assert options[0] == {
            "type": "partial_shipment",
            "est_cost": 0.0,
            "status": "proposed",
        }
        assert options[1]["type"] == "air_freight"
        assert options[1]["status"] == "pending_approval"

    def test_no_mitigation_when_not_slipped(self):
        assert ex.mitigation_options(
            days_late=0, impact_level="HIGH",
        ) == []

    def test_no_mitigation_when_impact_none(self):
        assert ex.mitigation_options(
            days_late=5, impact_level="NONE",
        ) == []

    def test_rejects_hostile_option_shapes(self):
        with pytest.raises(ValueError):
            ex.mitigation_options(
                days_late=2, impact_level="WATCH",
                offered=[{"type": " "}],
            )
        with pytest.raises(ValueError):
            ex.mitigation_options(
                days_late=2, impact_level="WATCH",
                offered=[{"type": "air_freight", "cost": -1}],
            )
        with pytest.raises(ValueError):
            ex.mitigation_options(
                days_late=2, impact_level="WATCH",
                offered=[{"type": "air_freight", "cost": True}],
            )


class TestOtif:

    def test_rounds_to_four(self):
        assert ex.otif(2, 3) == 0.6667

    def test_no_data_is_none_never_fabricated(self):
        assert ex.otif(0, 0) is None

    def test_rejects_impossible_counts(self):
        with pytest.raises(ValueError):
            ex.otif(5, 4)
        with pytest.raises(ValueError):
            ex.otif(-1, 4)
        with pytest.raises(ValueError):
            ex.otif(True, 4)


# ── P3 agent ──────────────────────────────────────────────────────

from app.agents.p3_expediting import P3Expediting  # noqa: E402


class TestP3Agent:

    def setup_method(self):
        self.agent = P3Expediting()

    def test_validate_ok(self):
        assert self.agent.validate({
            "pos": [{"po_number": "PO-1", "vendor": "V",
                     "committed_date": "2026-09-01"}],
        }) is None

    def test_validate_rejects(self):
        assert self.agent.validate({}) is not None
        assert self.agent.validate({"pos": []}) is not None
        bad = {"pos": [{"po_number": "PO-1", "vendor": "V",
                        "committed_date": "not-a-date"}]}
        assert self.agent.validate(bad) is not None
        bad2 = {"pos": [{"po_number": "PO-1", "vendor": "V",
                         "committed_date": "2026-09-01",
                         "confirmed": "yes"}]}
        assert self.agent.validate(bad2) is not None

    def test_execute_full_lifecycle(self):
        result = self.agent.execute({
            "today": "2026-09-02",
            "pos": [
                {"po_number": "PO-A", "vendor": "V1",
                 "committed_date": "2026-08-30",
                 "project_milestone": True},
                {"po_number": "PO-B", "vendor": "V2",
                 "committed_date": "2026-09-20"},
            ],
        })
        assert result.status == "SUCCESS"
        a, b = result.output["po_rows"]
        assert a["verdict"] == "SLIPPED"
        assert a["escalation"] == "whatsapp_chase"
        assert a["impact"] == "HIGH"
        # Default playbook attached for the slipped HIGH-impact PO.
        assert a["mitigation"]["status"] in ("proposed", "pending_approval")
        assert b["verdict"] == "ON_TRACK"
        assert b["escalation"] is None
        assert result.output["summary"]["slipped"] == 1
        assert result.output["summary"]["on_track"] == 1

    def test_execute_with_scorecards(self):
        result = self.agent.execute({
            "today": "2026-09-02",
            "pos": [{"po_number": "PO-C", "vendor": "V3",
                     "committed_date": "2026-09-01"}],
            "vendor_history": {"V3": {"on_time": 9, "total": 10}},
        })
        assert result.output["vendor_scorecards"]["V3"]["otif"] == 0.9

    def test_brief_worst_first(self):
        result = self.agent.execute({
            "today": "2026-09-02",
            "pos": [
                {"po_number": "PO-OK", "vendor": "V",
                 "committed_date": "2027-01-01"},
                {"po_number": "PO-LATE", "vendor": "V",
                 "committed_date": "2026-08-01"},
            ],
        })
        brief = result.output["brief"]
        assert brief.index("SLIPPED") < brief.index("ON_TRACK")

    def test_all_errors_fails(self):
        result = self.agent.execute({
            "pos": [{"po_number": "PO-X", "vendor": "V",
                     "committed_date": "nope"}],
        })
        assert result.status == "FAILED"


# ── Promotion gate (WP-7) ─────────────────────────────────────────

from app.services import promotion_gate as pg  # noqa: E402


class TestP3PromotionGate:

    def test_golden_set_runs_for_p3(self):
        report = pg.run_golden_set("p3_expediting")
        assert report.total == 6
        assert report.passed == report.total
        assert report.pass_rate == 1.0

    def test_gate_blocks_without_signoff(self):
        report = pg.gate_report("p3_expediting", "P3")
        assert report["approved"] is False
        assert report["criteria"]["golden"]["ok"] is True
        assert report["criteria"]["redteam"]["ok"] is True  # P3 in the pack
        assert report["criteria"]["signoff"]["ok"] is False
        assert "BLOCKED" in pg.render_text(report)

    def test_http_surface_renders(self):
        from app.main import app

        with TestClient(app) as client:
            response = client.get("/agents/P3/gate-report")
        assert response.status_code == 200
        body = response.json()
        assert body["agent_code"] == "P3"
        assert body["approved"] is False

    def test_registry_maps_p3(self):
        assert pg.AGENT_CODE_TO_DIR["P3"] == "p3_expediting"
