"""Expediting decision rules — deterministic core for P3 (never prompt-math).

Shared by the P3 agent; thresholds are module constants documented in
skills/p3_expediting/policy.md. Pure functions only.

  days_late        = today − committed_date          (> 0 = slipped)
  verdict          = SLIPPED | UNCONFIRMED | DUE_SOON | ON_TRACK
  escalation       = buyer_call | whatsapp_chase | email_confirmation | none
  mitigation.state = proposed | pending_approval    (cost is never
                     committed by the agent — spend needs a buyer)
"""

import math
from datetime import date, timedelta

# Days before the committed date when a confirmation request goes out
# (the spec's "T-3 days" rule).
CONFIRM_WINDOW_DAYS = 3

# Days of silence before the chase escalates from email to WhatsApp.
CHASE_AFTER_DAYS = 3

# Verdicts, worst first — ranking order for rendered briefs.
VERDICT_ORDER = ["SLIPPED", "UNCONFIRMED", "DUE_SOON", "ON_TRACK"]

# Mitigation types the agent may *propose* without buyer pre-approval
# because they carry no incremental spend: split what's already here,
# or switch to a vendor already on the P2 award list. Anything with a
# price tag (air freight, premium alternate, partial-ship-with-buyback…)
# lands in pending_approval — the type decides, never the claimed cost,
# so a fabricated "cost: 0" cannot launder an expensive option through.
NO_COST_MITIGATIONS = {"partial_shipment", "alternate_vendor"}


def parse_date(value) -> date:
    """Strict ISO date parse — used by validate() to fail closed."""
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError(f"date must be an ISO string, got {type(value).__name__}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid ISO date {value!r}") from exc


def days_late(committed: date, today: date) -> int:
    """Positive = the PO is already past its committed date."""
    if not isinstance(committed, date) or not isinstance(today, date):
        raise ValueError("days_late needs two dates")
    return (today - committed).days


def verdict(
    *,
    days_late: int,
    confirmed: bool | None,
) -> str:
    """One decision rule for the chase cycle.

    Order matters: a slipped PO stays SLIPPED even if the vendor
    answered; a non-confirmation inside the window is UNCONFIRMED
    (chase now), the confirmation window itself is DUE_SOON.
    """
    if not isinstance(days_late, int) or isinstance(days_late, bool):
        raise ValueError("days_late must be an int")
    if confirmed not in (None, True, False):
        raise ValueError("confirmed must be a bool or None")

    if days_late > 0:
        return "SLIPPED"
    if confirmed is False:
        return "UNCONFIRMED"
    if days_late >= -CONFIRM_WINDOW_DAYS:
        return "DUE_SOON"
    return "ON_TRACK"


def escalation(
    *,
    days_late: int,
    confirmed: bool | None,
) -> str | None:
    """The vendor contact ladder: email → WhatsApp → flagged call task
    for the buyer. None = no action owed today.

    SLIPPED and UNCONFIRMED both chase — silence is treated as
    slippage-in-waiting — and the step deepens with delay.
    """
    if days_late > CHASE_AFTER_DAYS:
        return "buyer_call"
    if days_late > 0 or confirmed is False:
        return "whatsapp_chase"
    if days_late >= -CONFIRM_WINDOW_DAYS:
        return "email_confirmation"
    return None


def impact(
    *,
    days_late: int,
    project_milestone: bool = False,
    customer_order: bool = False,
    stockout_risk: bool = False,
) -> str:
    """Downstream-impact triage for a reported delay.

    HIGH  — a project milestone or a customer order is exposed:
            proactive update to requester/PM + mitigation proposal.
    WATCH — stock-out risk only, no hard commitment exposed.
    NONE  — early warning, nothing downstream yet.
    """
    for flag in (project_milestone, customer_order, stockout_risk):
        if not isinstance(flag, bool):
            raise ValueError("impact flags must be booleans")
    if not isinstance(days_late, int) or isinstance(days_late, bool):
        raise ValueError("days_late must be an int")

    if days_late > 0 and (project_milestone or customer_order):
        return "HIGH"
    if stockout_risk:
        return "WATCH"
    return "NONE"


def mitigation_options(
    *,
    days_late: int,
    impact_level: str,
    offered: list[dict] | None = None,
) -> list[dict]:
    """Mitigation proposals for a slipped PO, worst impact first.

    Each option is {type, est_cost, status}. Only the documented
    no-cost types auto-propose; anything else — regardless of any
    claimed cost in the payload — goes to the buyer as
    pending_approval. The agent never commits spend.
    """
    if days_late <= 0:
        return []

    options: list[dict] = []
    seen: set[str] = set()
    for opt in offered or []:
        mtype = opt.get("type")
        if not isinstance(mtype, str) or not mtype.strip():
            raise ValueError("mitigation.type is required")
        cost = opt.get("cost", 0)
        if isinstance(cost, bool) or not isinstance(cost, (int, float)):
            raise ValueError("mitigation.cost must be a number")
        if not math.isfinite(float(cost)) or cost < 0:
            raise ValueError("mitigation.cost must be finite and ≥ 0")
        if mtype in seen:
            continue
        seen.add(mtype)
        options.append({
            "type": mtype,
            "est_cost": float(cost),
            "status": (
                "proposed"
                if mtype in NO_COST_MITIGATIONS
                else "pending_approval"
            ),
        })

    # Nothing offered → the default playbook, scaled to impact: split
    # the receipt at no cost; on HIGH impact queue the priced option
    # for the buyer. The playbook is impact-gated, but anything the
    # caller *offered* is always evaluated — an offered mitigation can
    # never be silently dropped by classifying impact as NONE.
    if not options:
        if impact_level == "NONE":
            return []
        options.append({
            "type": "partial_shipment",
            "est_cost": 0.0,
            "status": "proposed",
        })
        if impact_level == "HIGH":
            options.append({
                "type": "air_freight",
                "est_cost": 0.0,  # buyer prices it — agent never commits
                "status": "pending_approval",
            })
    return options


def otif(on_time: int, total: int) -> float | None:
    """On-time-in-full ratio for a vendor scorecard; None when no data.

    Zero-tolerance: total=0 returns None (no fabricated 100%).
    """
    if isinstance(on_time, bool) or not isinstance(on_time, int) or on_time < 0:
        raise ValueError("on_time must be an int ≥ 0")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError("total must be an int ≥ 0")
    if on_time > total:
        raise ValueError("on_time cannot exceed total")
    if total == 0:
        return None
    return round(on_time / total, 4)


def default_confirm_by(committed: date) -> date:
    """When the T-3 confirmation request should fire."""
    return committed - timedelta(days=CONFIRM_WINDOW_DAYS)
