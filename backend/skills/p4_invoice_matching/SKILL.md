# Skill: P4 Invoice Matching

## Identity
Three-way match (PO ↔ GRN ↔ supplier invoice) with continuous
tolerance checking. Suite: Purchase. Runtime: headless.

## Procedure
1. **Load by reference**: PO and invoice are read from stored
   documents via their numbers — the payload carries numbers only.
2. **Verify the chain**: unknown PO, missing GRN, or unregistered
   invoice is a VALIDATION_ERROR. Nothing is fabricated.
3. **Match vendor leg** (exact), **amount leg** to ₹0.01 tolerance,
   **line level** price/quantity against ordered and received.
4. **Outcome**:
   - clean → FULLY_MATCHED, register match, stage for books
   - variance → raise `INVOICE_MISMATCH` approval with full diff
5. On approval-granted re-run: honour the human decision, still
   record the match as a mismatch.

## Tool bindings
| Tool | Use |
|---|---|
| purchase_order_repository | load PO + items |
| goods_receipt_repository | cumulative received quantities |
| invoice_repository | register/match/mark_posted |

## Autonomy & HITL
- A2 for clean matches; A1 for any mismatch or payment release.
- Duplicate invoices are refused (fingerprint on number).

## Never
- Match an invoice with no GRN behind it.
- Round away a variance to force a match.
