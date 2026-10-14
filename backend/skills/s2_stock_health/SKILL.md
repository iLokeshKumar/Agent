# Skill: S2 Stock Health

## Identity
Weekly ageing/EOL scoring across serialised stock. Suite: Stores.
Runtime: supervised (read-only brief). Autonomy: A1 at seed. Feeds S3
(reorder) and the sales owner (clearance).

## Procedure
1. Pull per-SKU+branch unit ages (days since receipt) from
   serial_registry and offtake history (13-week units).
2. Score deterministically via app/services/stock_health.py — ageing
   buckets, weeks cover, EOL flag. Never adjust numbers by hand.
3. Render the stock-health brief: worst recommendations first
   (clearance_push → ageing_review → reorder_check → healthy).
4. Route: `reorder_check` rows → S3 for reorder proposals;
   `clearance_push` rows → sales owner with the EOL evidence;
   `ageing_review` rows → stores owner for a physical-count check.
5. S2 writes no business objects. It observes and recommends.

## Autonomy & HITL
- A1: the brief is reviewed by the stores owner each week.
- Safe to promote to A2 (auto-send brief) after golden ≥95% and
  sign-off: the brief is read-only, so blast radius is zero.

## Never
- Reorder an EOL SKU — clearance first (S3 must not see it as demand).
- Alter a computed verdict; if inputs look wrong, fix the registry.
- Mix branches in one row — stock is serialised per branch.
- Quote weeks_cover as a number when there is no offtake data — say
  "no offtake" instead of inventing one.
