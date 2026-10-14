"""F2 Scheme Claims — earned-claim engine + agent lifecycle.

The engine (app/services/scheme_claims.py) is deterministic code —
property-tested here per the docs' "never prompt-math for money" rule:
- slabs ladder correctly and refuse descending tables
- excluded serials never earn, with stable machine reasons
- the double-claim guard is absolute
- claimable floors at zero and respects already_claimed
- Decimal rounding to the paisa

Agent tests cover validation and the three lifecycle actions.
"""

from datetime import date

import pytest

from app.agents.f2_scheme_claims import F2SchemeClaims
from app.services.scheme_claims import (
    SchemeWindowError,
    claim_pack_lines,
    earned_claim,
    slab_rate,
)


# ═══════════════════════════════════════════════════════════════════
# Slab ladder
# ═══════════════════════════════════════════════════════════════════

class TestSlabRate:

    def test_below_first_threshold_is_zero(self):
        assert slab_rate(sales_value=499_999) == 0.0

    def test_exact_threshold_hits_slab(self):
        assert slab_rate(sales_value=500_000) == 0.01

    def test_ladder_climbs(self):
        assert slab_rate(sales_value=2_000_000) == 0.02
        assert slab_rate(sales_value=5_000_000) == 0.03
        assert slab_rate(sales_value=50_000_000) == 0.03

    def test_custom_slabs(self):
        slabs = [
            {"min_sales": 0, "rate": 0.0},
            {"min_sales": 100, "rate": 0.05},
        ]
        assert slab_rate(sales_value=150, slabs=slabs) == 0.05
        assert slab_rate(sales_value=99, slabs=slabs) == 0.0

    def test_descending_slabs_refused(self):
        slabs = [
            {"min_sales": 500_000, "rate": 0.01},
            {"min_sales": 0, "rate": 0.0},
        ]
        with pytest.raises(SchemeWindowError):
            slab_rate(sales_value=100, slabs=slabs)


# ═══════════════════════════════════════════════════════════════════
# Earned claim — exclusion + amount rules
# ═══════════════════════════════════════════════════════════════════

WINDOW = (date(2026, 8, 1), date(2026, 8, 31))
SKUS = {"SAMS-QA65"}


def _serial(serial="SN1", status="sold", invoice="INV-1",
            sale="2026-08-05", sku="SAMS-QA65", value=1_000_000):
    return {
        "serial": serial, "sku_ref": sku, "status": status,
        "sold_invoice_ref": invoice, "sale_date": sale,
        "base_value": value,
    }


def _compute(serials, already=0.0, start=None, end=None):
    return earned_claim(
        scheme_start=start or WINDOW[0],
        scheme_end=end or WINDOW[1],
        eligible_skus=SKUS,
        serials=serials,
        already_claimed=already,
    )


class TestEarnedClaim:

    def test_single_serial_earns_slab_rate(self):
        result = _compute([_serial(value=600_000)])
        assert result["eligible_serials"] == 1
        assert result["slab_rate"] == 0.01
        assert result["earned_amount"] == 6000.00
        assert result["claimable_amount"] == 6000.00

    def test_ladder_on_cumulative_value(self):
        result = _compute([
            _serial(serial="A", value=2_500_000),
            _serial(serial="B", value=2_500_000),
        ])
        # 5,000,000 → 3% slab on the whole qualifying value
        assert result["qualifying_sales_value"] == 5_000_000
        assert result["slab_rate"] == 0.03
        assert result["earned_amount"] == 150_000.00

    def test_unsold_serial_excluded_with_reason(self):
        result = _compute([_serial(status="in_stock", invoice=None)])
        assert result["eligible_serials"] == 0
        assert result["excluded_serials"] == [
            {"serial": "SN1", "reason": "not_sold"}
        ]

    def test_no_invoice_ref_excluded(self):
        result = _compute([_serial(invoice=None)])
        assert result["excluded_serials"][0]["reason"] == (
            "no_sold_invoice_ref"
        )

    def test_sale_outside_window_excluded(self):
        result = _compute([_serial(sale="2026-07-15")])
        assert result["excluded_serials"][0]["reason"] == "outside_window"

    def test_window_edges_inclusive(self):
        for edge in ("2026-08-01", "2026-08-31"):
            result = _compute([_serial(sale=edge)])
            assert result["eligible_serials"] == 1, edge

    def test_sku_not_eligible_excluded(self):
        result = _compute([_serial(sku="SAMS-OTHER")])
        assert result["excluded_serials"][0]["reason"] == "sku_not_eligible"

    def test_duplicate_serial_claimed_once(self):
        result = _compute([
            _serial(serial="SN1", value=600_000),
            _serial(serial="SN1", value=600_000),
        ])
        assert result["eligible_serials"] == 1
        reasons = [
            e["reason"] for e in result["excluded_serials"]
        ]
        assert "already_on_claim" in reasons

    def test_already_claimed_offsets_and_floors_at_zero(self):
        result = _compute([_serial(value=600_000)], already=10_000)
        assert result["earned_amount"] == 6000.00
        assert result["claimable_amount"] == 0.0

    def test_partial_offset(self):
        result = _compute([_serial(value=600_000)], already=2_000)
        assert result["claimable_amount"] == 4000.00

    def test_bad_sale_date_excluded_not_crash(self):
        result = _compute([_serial(sale="not-a-date")])
        assert result["excluded_serials"][0]["reason"] == "bad_sale_date"

    def test_negative_base_value_excluded(self):
        result = _compute([_serial(value=-1)])
        assert result["excluded_serials"][0]["reason"] == "bad_base_value"

    def test_inverted_window_refused(self):
        with pytest.raises(SchemeWindowError):
            _compute([_serial()], start=date(2026, 8, 31),
                     end=date(2026, 8, 1))

    def test_negative_already_claimed_refused(self):
        with pytest.raises(ValueError):
            _compute([_serial()], already=-1)

    def test_paisa_rounding(self):
        result = _compute([_serial(value=533_333)])
        # 1% of 533,333 = 5333.33
        assert result["earned_amount"] == 5333.33

    def test_claim_pack_exclusions_first(self):
        result = _compute([
            _serial(),
            _serial(serial="SN-BAD", status="in_stock", invoice=None),
        ])
        pack = claim_pack_lines(result)
        assert pack[0]["type"] == "exclusion"
        assert pack[-1]["type"] == "earned"


# ═══════════════════════════════════════════════════════════════════
# Agent lifecycle
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def agent():
    return F2SchemeClaims()


class TestF2Agent:

    def test_compute_success_shape(self, agent):
        result = agent.run(payload={
            "action": "compute",
            "scheme_id": "SCH-1",
            "window": {"start": "2026-08-01", "end": "2026-08-31"},
            "eligible_skus": ["SAMS-QA65"],
            "serials": [_serial()],
        })
        assert result.status == "SUCCESS"
        assert result.output["summary"]["earned_amount"] == 10_000.00
        assert "claim_pack_lines" in result.output

    def test_compute_string_window_accepted(self, agent):
        result = agent.run(payload={
            "action": "compute",
            "scheme_id": "SCH-1",
            "window": {"start": "2026-08-01", "end": "2026-08-31"},
            "eligible_skus": ["SAMS-QA65"],
            "serials": [_serial()],
        })
        assert result.status == "SUCCESS"

    def test_compute_bad_window_is_validation_error(self, agent):
        result = agent.run(payload={
            "action": "compute",
            "scheme_id": "SCH-1",
            "window": {"start": "nope", "end": "2026-08-31"},
            "eligible_skus": ["SAMS-QA65"],
            "serials": [_serial()],
        })
        assert result.status == "VALIDATION_ERROR"

    def test_compute_inverted_window_is_validation_error(self, agent):
        result = agent.run(payload={
            "action": "compute",
            "scheme_id": "SCH-1",
            "window": {"start": "2026-08-31", "end": "2026-08-01"},
            "eligible_skus": ["SAMS-QA65"],
            "serials": [_serial()],
        })
        assert result.status == "VALIDATION_ERROR"

    def test_record_claim_flags_overclaim(self, agent):
        result = agent.run(payload={
            "action": "record_claim",
            "scheme_id": "SCH-1",
            "amount_claimed": 50_000,
            "amount_computed": 44_000,
        })
        assert result.status == "SUCCESS"
        assert result.output["overclaim_flag"] is True
        assert "OVERCLAIM" in result.output["note"]

    def test_record_settlement_full(self, agent):
        result = agent.run(payload={
            "action": "record_settlement",
            "scheme_id": "SCH-1",
            "amount_received": 44_000,
            "amount_claimed": 44_000,
        })
        assert result.output["status"] == "settled"

    def test_record_settlement_partial(self, agent):
        result = agent.run(payload={
            "action": "record_settlement",
            "scheme_id": "SCH-1",
            "amount_received": 40_000,
            "amount_claimed": 44_000,
        })
        assert result.output["status"] == "partial"
        assert result.output["variance"] == -4000.0

    def test_empty_payload_rejected(self, agent):
        assert agent.run(payload={}).status == "VALIDATION_ERROR"

    def test_missing_scheme_id_rejected(self, agent):
        result = agent.run(payload={"action": "compute"})
        assert result.status == "VALIDATION_ERROR"
