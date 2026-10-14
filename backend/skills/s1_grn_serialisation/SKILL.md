# Skill: S1 GRN & Serialisation

## Identity
WhatsApp/API-driven goods receipt with serial capture for IMEI-based
stock. Suite: Stores. Runtime: headless.

## Procedure
1. Load the PO by number; refuse unknown POs.
2. Derive outstanding quantities from what was ALREADY received
   (cumulative across GRNs), not what the receipt claims.
3. Capture serials per serialised item; check duplicates against the
   registry.
4. Post GRN; flag discrepancies at the gate:
   - delivery beyond ordered → VALIDATION_ERROR
   - extra unexpected units → OVER_RECEIVED flag, receipt still
     recorded, serials kept
5. Report grn_number + status (COMPLETE / PARTIAL / OVER_RECEIVED).

## Tool bindings
goods_receipt_repository · purchase_order_repository · numbering
service (GRN sequence)

## Autonomy & HITL
- A2 for matched receipts; discrepancies raise store-in-charge review.
- Serial duplicates are flagged, never silently overwritten.

## Never
- Accept more than ordered quantity silently.
- Discard a receipt because one line disagrees — record facts,
  flag exceptions.
