# F4 — Interentity Reconciliation

**Version:** 1.0.0  
**Autonomy:** A2 (supervised — suggests matches, humans confirm)  
**Go-live:** 2026-09-11  
**Primary KPI:** interentity_open_items_count  
**KPI Target:** < 5 items unmatched > 30 days

## Purpose

Nightly fingerprint matching across YEXIS-EL and YEXIS-SL interentity open items.
Reduces manual reconciliation effort from ~2 hours/week to < 10 minutes/week.

## Trigger

- Nightly scheduler (02:00) — `action=match`
- On-demand by Finance Controller via `/api/agents/run`

## Actions

| Action  | Description                                                      | Autonomy |
|---------|------------------------------------------------------------------|----------|
| match   | Fingerprint-match open items; mark `suggested` on clear pairs   | A2       |
| report  | Show all items grouped by match_status                           | A1       |
| settle  | Reserved — requires Finance Controller approval to set matched   | Manual   |

## Matching Algorithm

1. Load `InterentityOpenItem` rows where `match_status = open` for both legs
2. Fingerprint key: `doc_type + amount (±0.1%) + doc_date (±5 days)`
3. Single match → `confidence=high` (exact amount + same-day) or `medium`
4. Multiple candidates → `confidence=ambiguous` — surfaces for human pick
5. No match → `unmatched` — surfaces for manual entry

## Constraints

- Never marks `match_status = matched` without human approval
- All Decimal arithmetic — no float for amount comparison
- Action Ledger entry for every `suggested` state change

## Related

- [[f3_books_to_tally]] — upstream: TB data feeds InterentityOpenItem
- [[f5_cash_forecast]] — downstream: matched items reduce open AP/AR pools
