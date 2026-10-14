# Policy: F3 Books→Tally Sync

## Mapping rules
- Every Zoho account must map to a Tally ledger; gaps surface as
  `UNMAPPED: <account>` and count in `unmapped_count`.
- Mapping tables are versioned; changes freeze during UAT cycles.

## Balance check
- tolerance: exact to ₹0.01
- unbalanced → SUCCESS with warning + is_balanced=false (facts
  reported, nothing staged silently wrong)

## Entity discipline
- Staging rows carry entity = YEXIS-EL | YEXIS-SL.
- Only F4 may ever hold dual-entity read; this agent stays
  single-entity per run.

## Idempotency
- Restaging the same books_txn_id returns existing STAGED rows —
  never duplicates.
