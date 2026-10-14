"""Reorder-point math — deterministic core for S3 (never prompt-math).

Spec rule for money/quantity decisions: judgement belongs in a Skill,
determinism belongs in a validated library. Everything here is a pure
function over numbers so it can be property-tested; the agent layer
only fetches inputs and renders the output.

Formulas (documented for the policy.md and the audit):

  avg_weekly_demand = mean(weekly_units)        (≥ 0)
  reorder_point     = ceil(avg_weekly_demand × lead_time_weeks
                            + safety_stock)
  needs_reorder     = on_hand ≤ reorder_point
  order_quantity    = max(0, ceil(target − on_hand))
                      where target = max(reorder_point,
                                         avg_weekly_demand × target_cover_weeks)
                      bumped to min_order_qty when 0 < qty < min_order_qty
"""

import math


def _num(u, what: str) -> float:
    """Coerce to float, refusing bools and NaN/inf.

    ``isinstance(True, int)`` is True in Python, so a bare type check
    would let a boolean slip into arithmetic; NaN comparisons are all
    False, so ``x < 0`` silently passes NaN through. Both are rejected
    here — red-team rule: garbage in, VALIDATION_ERROR out.
    """
    if isinstance(u, bool) or not isinstance(u, (int, float)):
        raise ValueError(f"{what} must be a number")
    v = float(u)
    if math.isnan(v) or math.isinf(v):
        raise ValueError(f"{what} must be finite")
    return v


def average_weekly_demand(weekly_units: list[float]) -> float:
    """Mean of a weekly offtake series. Empty → 0.0 (no demand)."""
    if not weekly_units:
        return 0.0
    vals = [_num(u, "weekly_units") for u in weekly_units]
    if any(v < 0 for v in vals):
        raise ValueError("weekly_units must be non-negative")
    return sum(vals) / len(vals)


def reorder_point(
    avg_weekly: float,
    lead_time_weeks: float,
    safety_stock: float,
) -> int:
    """Units on hand at which a PO must be raised. Never negative."""
    avg_weekly = _num(avg_weekly, "avg_weekly")
    lead_time_weeks = _num(lead_time_weeks, "lead_time_weeks")
    safety_stock = _num(safety_stock, "safety_stock")
    if avg_weekly < 0 or lead_time_weeks < 0 or safety_stock < 0:
        raise ValueError("reorder inputs must be non-negative")
    return int(math.ceil(avg_weekly * lead_time_weeks + safety_stock))


def needs_reorder(on_hand: int, rop: int) -> bool:
    """At-or-below the trigger means order now (equality included)."""
    return on_hand <= rop


def order_quantity(
    on_hand: int,
    rop: int,
    avg_weekly: float,
    target_cover_weeks: float = 4.0,
    min_order_qty: int = 0,
) -> int:
    """Units to order to restore cover, ≥ 0.

    Orders up to the larger of the reorder point and one target-cover
    cycle of demand, then respects any vendor minimum. Below the ROP
    but inside cover → order just enough to reach the ROP.
    """
    on_hand = _num(on_hand, "on_hand")
    target_cover_weeks = _num(target_cover_weeks, "target_cover_weeks")
    min_order_qty = _num(min_order_qty, "min_order_qty")
    if on_hand < 0 or target_cover_weeks < 0 or min_order_qty < 0:
        raise ValueError("order inputs must be non-negative")
    target = max(float(rop), avg_weekly * target_cover_weeks)
    qty = int(math.ceil(target - on_hand))
    if qty <= 0:
        return 0
    if qty < min_order_qty:
        return min_order_qty
    return qty
