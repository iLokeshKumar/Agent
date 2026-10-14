# Policy: P4 Invoice Matching

## Match tolerances
| Dimension | Rule |
|---|---|
| Vendor | exact (case-normalised) |
| Invoice total vs PO+GRN | abs(variance) < ₹0.01 |
| Line unit price | abs(diff) < ₹0.01 |
| Quantity | exact; over-receipt handled by S1, not here |

## Hard refusals
- No GRN on file → VALIDATION_ERROR (the chain stops honestly)
- Duplicate invoice number → refused, flagged
- Unapproved mismatch → nothing posted anywhere

## Mismatch approval payload
Always include: invoice_number, po_number, grn_number, expected
amount, claimed amount, variance, per-line diffs.
