# Indent Planning — C1
**Version:** 1.0.0  
**Autonomy:** A1  
**Go-live:** 2026-09-11  
**Primary KPI:** `reorder_cycle_days`  
**KPI Target:** ≤ 3 days

## Purpose
Analyses inventory levels against reorder points and generates purchase indents for the SCM team. Ensures stock replenishment cycles complete within 3 days of a reorder trigger by preparing accurate, vendor-mapped indent documents. Operates in advisory mode (A1), with all indents requiring SCM Manager approval before issuance.

## Trigger
Triggered daily by a scheduled inventory health scan, or on-demand when stock-health alerts are received from the S3 Reorder agent.

## Actions
| Action | Description | Autonomy |
|--------|-------------|----------|
| scan_reorder_triggers | Identify SKUs below reorder threshold from inventory | A1 |
| calculate_indent_qty | Compute order quantity based on lead time and safety stock | A1 |
| map_preferred_vendor | Resolve SKU to preferred vendor from vendor master | A1 |
| draft_indent | Prepare indent document with SKU, qty, vendor, and urgency flag | A1 |
| submit_for_approval | Route draft indent to SCM Manager for review and sign-off | A1 (human gate) |

## Constraints
- Never issue a purchase indent without SCM Manager approval.
- Never override manually set reorder points without a change request.
- Indent quantities must not exceed 180-day demand coverage.
- All indents must reference a valid vendor from the approved vendor master.
- Reorder cycle KPI breach (> 3 days) must be flagged to SCM Manager immediately.
