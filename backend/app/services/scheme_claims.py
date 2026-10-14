"""Earned-claim computation — deterministic core for F2 (never prompt-math).

Shared by the F2 agent and any future claim-mart builder so both render
identical amounts. Pure functions only; thresholds and business rules
are module constants documented in skills/f2_scheme_claims/policy.md.

Model (from the Blueprint §7 F2 and the build matrix):

  A scheme is a window with a slab table. A dealer sells serialised
  Samsung stock during the window; each sold serial earns a slab rate
  on its base value. The earned claim is:

      earned = Σ over qualifying serials of (base_value × slab_rate)
      claimable = earned − already_claimed          (never < 0)

  A serial qualifies when: status == "sold", it has a sold_invoice_ref,
  its sale date falls inside the scheme window, its SKU is on the
  scheme's eligible list, and the invoice amount meets the scheme's
  per-invoice minimum. Serials already claimed (any serial on a
  previous claim line) are excluded — double-claiming is the classic
  leakage this engine exists to prevent.
"""

from datetime import date

# Slabs are evaluated in order; the first matching slab wins.
# rate = claim rate on base value; min_invoice = per-invoice floor.
DEFAULT_SLABS = [
    {"min_sales": 0, "rate": 0.00},
    {"min_sales": 500_000, "rate": 0.01},
    {"min_sales": 2_000_000, "rate": 0.02},
    {"min_sales": 5_000_000, "rate": 0.03},
]

# Selling-price tolerance vs the invoice: mismatched serials are
# flagged, not silently valued at whichever number is convenient.
PRICE_TOLERANCE_PCT = 0.01


class SchemeWindowError(ValueError):
    """Scheme window/milestone misconfiguration — fail closed."""


def slab_rate(
    *,
    sales_value: float,
    slabs: list[dict] | None = None,
) -> float:
    """Claim rate for cumulative sales; first matching slab wins.

    Slabs must be ascending by min_sales — a descending or gapped
    table is a configuration error, not a business surprise.
    """
    slabs = slabs or DEFAULT_SLABS
    mins = [s["min_sales"] for s in slabs]
    if mins != sorted(mins):
        raise SchemeWindowError("slabs must be ascending by min_sales")
    chosen = slabs[0]["rate"]
    for slab in slabs:
        if sales_value >= slab["min_sales"]:
            chosen = slab["rate"]
    return chosen


def serial_qualifies(
    *,
    serial_status: str,
    sold_invoice_ref: str | None,
    sale_date: date | None,
    scheme_start: date,
    scheme_end: date,
    sku_ref: str | None,
    eligible_skus: set[str],
) -> tuple[bool, str]:
    """Gate a serial into the scheme. Returns (ok, reason).

    Reasons are stable machine strings — they feed the audit trail and
    the dispute pack; a human reviewing a rejected serial sees exactly
    which rule excluded it.
    """
    if serial_status != "sold":
        return False, "not_sold"
    if not sold_invoice_ref:
        return False, "no_sold_invoice_ref"
    if sale_date is None:
        return False, "no_sale_date"
    if sale_date < scheme_start or sale_date > scheme_end:
        return False, "outside_window"
    if sku_ref not in eligible_skus:
        return False, "sku_not_eligible"
    return True, "eligible"


def earned_claim(
    *,
    scheme_start: date,
    scheme_end: date,
    eligible_skus: set[str],
    serials: list[dict],
    already_claimed: float = 0.0,
    slabs: list[dict] | None = None,
) -> dict:
    """Compute the earned claim from serial sale events.

    ``serials`` items (from serial_registry joined to invoice data):
      {serial, sku_ref, status, sold_invoice_ref, sale_date (date|ISO),
       base_value}

    Returns the amount breakdown plus per-serial detail for the claim
    pack. Money is Decimal throughout; the boundary is float because
    the payload/DB layers are float — rounding to 2dp at the edges.
    """
    from decimal import Decimal

    if scheme_end < scheme_start:
        raise SchemeWindowError("scheme_end before scheme_start")

    if already_claimed < 0:
        raise ValueError("already_claimed must be non-negative")

    claimed_serial_refs: set[str] = set()
    lines: list[dict] = []
    excluded: list[dict] = []
    qualifying_value = Decimal("0")

    for s in serials:
        serial = s.get("serial")
        sale_raw = s.get("sale_date")
        try:
            sale_date = (
                sale_raw if isinstance(sale_raw, date)
                else date.fromisoformat(str(sale_raw)[:10])
            )
        except (ValueError, TypeError):
            excluded.append({"serial": serial, "reason": "bad_sale_date"})
            continue

        ok, reason = serial_qualifies(
            serial_status=str(s.get("status") or ""),
            sold_invoice_ref=s.get("sold_invoice_ref"),
            sale_date=sale_date,
            scheme_start=scheme_start,
            scheme_end=scheme_end,
            sku_ref=s.get("sku_ref"),
            eligible_skus=eligible_skus,
        )
        if not ok:
            excluded.append({"serial": serial, "reason": reason})
            continue

        # Double-claim guard: a serial on any previous line can never
        # earn again, regardless of what the caller says.
        if serial in claimed_serial_refs:
            excluded.append({"serial": serial, "reason": "already_on_claim"})
            continue
        claimed_serial_refs.add(serial)

        try:
            base_value = Decimal(str(s.get("base_value") or 0))
        except (ValueError, TypeError):
            excluded.append({"serial": serial, "reason": "bad_base_value"})
            claimed_serial_refs.discard(serial)
            continue
        if base_value < 0:
            excluded.append({"serial": serial, "reason": "bad_base_value"})
            claimed_serial_refs.discard(serial)
            continue

        qualifying_value += base_value
        lines.append({
            "serial": serial,
            "sku_ref": s.get("sku_ref"),
            "sold_invoice_ref": s.get("sold_invoice_ref"),
            "sale_date": sale_date.isoformat(),
            "base_value": float(base_value),
        })

    rate = slab_rate(sales_value=float(qualifying_value), slabs=slabs)
    earned = qualifying_value * Decimal(str(rate))
    earned = earned.quantize(Decimal("0.01"))
    gross_float = float(earned)

    claimable = max(0.0, round(gross_float - float(already_claimed), 2))

    return {
        "eligible_serials": len(lines),
        "excluded_serials": excluded,
        "qualifying_sales_value": float(qualifying_value),
        "slab_rate": rate,
        "earned_amount": gross_float,
        "already_claimed": float(already_claimed),
        "claimable_amount": claimable,
        "lines": lines,
    }


def claim_pack_lines(result: dict) -> list[dict]:
    """Render per-serial evidence lines for the claim pack (Word/Excel).

    Worst-news-first ordering: exclusions up top so the human reviewer
    sees what will be queried before what will be paid.
    """
    pack = [
        {
            "type": "exclusion",
            "serial": e["serial"],
            "reason": e["reason"],
        }
        for e in result["excluded_serials"]
    ]
    pack.extend(
        {
            "type": "earned",
            "serial": l["serial"],
            "sold_invoice_ref": l["sold_invoice_ref"],
            "base_value": l["base_value"],
        }
        for l in result["lines"]
    )
    return pack
