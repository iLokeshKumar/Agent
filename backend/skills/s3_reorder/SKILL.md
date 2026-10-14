# Skill: S3 Reorder

## Identity
Weekly reorder proposals from offtake history and lead times.
Suite: Stores. Runtime: supervised (proposals only — P1 raises the PO,
a human approves it). Feeds P1 purchase_request payloads.

## Procedure
1. Collect weekly offtake per SKU+branch (offtake feed / sku_health).
2. Compute reorder point: `ceil(avg_weekly_demand × lead_time_weeks +
   safety_stock)` — deterministic library output, never prompt math.
3. Flag every SKU with `on_hand ≤ reorder_point`.
4. Recommend order quantity: cover to `max(reorder_point,
   avg_weekly × target_cover_weeks)`, respecting vendor minimums.
5. Render proposals table (SKU, branch, on-hand, ROP, qty) and hand to
   P1 as purchase_request candidates.
6. Never place orders, never contact vendors — S3 is propose-only.

## Autonomy & HITL
- A1 at seed: every proposal reviewed by the stores owner.
- Promotion path: A1 → A2 (auto-route to P1 intake) after golden set
  ≥95% and business-owner sign-off.

## Never
- Order or commit spend — procurement authority belongs to P1 + human.
- Override a computed reorder point with a "gut feel" number; if the
  inputs are wrong, fix the inputs.
- Mix branches in one proposal line — stock is serialised per branch.
