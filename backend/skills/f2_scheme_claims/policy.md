# Policy: F2 Scheme Claims

## Scheme register gate
- `scheme_master.status` must be `verified` (verified_by set) before
  any computation. Candidates compute nothing.
- Window dates are the circular's dates, not "the last quarter".

## Slab ladder (default — override per scheme via eligibility_rules_json)
| cumulative qualifying sales | rate |
|---|---|
| ≥ ₹5,000,000 | 3% |
| ≥ ₹2,000,000 | 2% |
| ≥ ₹500,000 | 1% |
| < ₹500,000 | 0% |

First matching slab wins (highest rate achieved). Slabs must ascend by
min_sales — a descending table is a configuration error and refuses to
compute.

## Exclusion rules (stable machine reasons, shown in the pack)
| reason | meaning |
|---|---|
| not_sold | serial status ≠ sold |
| no_sold_invoice_ref | sold but never invoiced — no proof |
| no_sale_date | sale date unknown |
| outside_window | sale date outside scheme window |
| sku_not_eligible | SKU not on the scheme's eligible list |
| already_on_claim | serial already on a claim line — hard guard |
| bad_base_value | missing/negative/unknown base value |

## Money rules
- Decimal arithmetic; round to ₹0.01 at the edges.
- claimable = earned − already_claimed, floored at 0 — never negative.
- Overclaim flag when claimed > computed + ₹0.01; reconcile before
  submission.

## Settlement variance
- |variance| ≤ ₹0.01 → settled.
- received < claimed → partial (chase or accept; reason recorded).
- received > claimed → settled but verify the credit notes.

## Accuracy target
- Claim accuracy to 99% (Blueprint KPI). Every computation stores its
  full engine output in `scheme_claim.computation_json` for audit.
