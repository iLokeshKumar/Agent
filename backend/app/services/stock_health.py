"""Stock-health scoring — deterministic core for S2 (never prompt-math).

Shared by the S2 agent and the nightly sku_health mart builder so both
render identical verdicts. Pure functions only; thresholds are module
constants documented in skills/s2_stock_health/policy.md.

  weeks_cover   = on_hand / avg_weekly_demand   (∞ when no demand)
  action_reco   = clearance_push | ageing_review | reorder_check | healthy
"""

AGEING_BUCKETS = {"0_30": 30, "31_60": 60, "61_90": 90, "over_90": None}

# Cover below this many weeks feeds a reorder proposal (S3 hand-off).
LOW_COVER_WEEKS = 2.0


def average_weekly_demand(weekly_units: list[float]) -> float:
    if not weekly_units:
        return 0.0
    vals = [float(u) for u in weekly_units]
    if any(v < 0 for v in vals):
        raise ValueError("weekly_units must be non-negative")
    return sum(vals) / len(vals)


def bucket_ages(ages_days: list[int]) -> dict:
    """Count units into ageing buckets by days since receipt."""
    buckets = {k: 0 for k in AGEING_BUCKETS}
    for age in ages_days:
        if age < 0:
            raise ValueError("ages must be non-negative")
        if age <= 30:
            buckets["0_30"] += 1
        elif age <= 60:
            buckets["31_60"] += 1
        elif age <= 90:
            buckets["61_90"] += 1
        else:
            buckets["over_90"] += 1
    return buckets


def weeks_cover(on_hand: int, avg_weekly: float) -> float | None:
    """Weeks of demand the current stock covers; None when no demand."""
    if on_hand < 0:
        raise ValueError("on_hand must be non-negative")
    if avg_weekly < 0:
        raise ValueError("avg_weekly must be non-negative")
    if avg_weekly == 0:
        return None
    return round(on_hand / avg_weekly, 2)


def action_reco(
    *,
    eol: bool,
    on_hand: int,
    ageing: dict,
    weeks_cover: float | None,
) -> str:
    """Single decision rule shared by S2 and the mart builder.

    Order matters: EOL wins (clear stock, never reorder), then stale
    stock (≥ half the units older than 60 days), then low cover (feeds
    S3), else healthy.
    """
    if eol and on_hand > 0:
        return "clearance_push"

    stale = ageing.get("61_90", 0) + ageing.get("over_90", 0)
    if stale > 0 and stale * 2 >= on_hand:
        return "ageing_review"

    if weeks_cover is not None and weeks_cover < LOW_COVER_WEEKS:
        return "reorder_check"

    return "healthy"


def health_record(
    *,
    sku_ref: str,
    branch: str,
    ages_days: list[int],
    weekly_units: list[float],
    eol: bool,
    period: str,
) -> dict:
    """One SKU+branch health snapshot — the S2 output row shape."""
    on_hand = len(ages_days)
    avg_weekly = average_weekly_demand(weekly_units)
    ageing = bucket_ages(ages_days)
    cover = weeks_cover(on_hand, avg_weekly)

    return {
        "sku_ref": sku_ref,
        "branch": branch,
        "period": period,
        "on_hand": on_hand,
        "ageing_buckets": ageing,
        "avg_weekly_demand": round(avg_weekly, 4),
        "weeks_cover": cover,
        "eol_flag": eol,
        "action_reco": action_reco(
            eol=eol, on_hand=on_hand, ageing=ageing, weeks_cover=cover,
        ),
    }
