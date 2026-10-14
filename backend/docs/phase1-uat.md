# Phase-1 supervised UAT

## Safety state

Keep P1, P4, S1, F1, F3, D1 and V1 at `A1` and `dry_run=true`. Do not set
`MAIL_SEND_ENABLED=true`. F3 remains enabled and visible, but is out of
scope until Zoho and Tally are available.

In dry-run mode the executor validates input and records task/ledger evidence,
but it must not invoke agent tools, external connectors, document writes, or
workflow chaining.

Use the local operations console at `http://127.0.0.1:8000/ops` to
watch tasks, approvals, lane state and audit correlation IDs.

## Test pack

Use copies of real, low-risk business records. Do not use production
customer contacts for the first run.

| Lane | Cases | Acceptance evidence |
| --- | ---: | --- |
| P1 | 10 | Valid request, missing field, within budget and above-budget approval path |
| S1 | 10 | Valid serial capture, duplicate serial, short/excess receipt and mismatch |
| P4 | 10 | Full match, price variance, quantity variance, vendor mismatch and approved exception |
| F1 | 10 | Segmentation and proposed cadence only; no actual outbound message |
| D1 | 10 | Figures reconcile to the agreed source spreadsheet and every answer has a drill-down reference |
| V1 | 10 | Service/RMA/unknown-warranty routing, SLA assignment and human escalation |
| Mail | 5 | POP3 duplicate suppression and internal draft creation only; SMTP remains disabled |

## Run procedure

1. Confirm `/health`, `/agents/health`, `/mail/health` and `/lanes` are green/expected.
2. Record the case ID, expected result and the returned `correlation_id` for every task.
3. Review each generated approval or exception in `/ops`.
4. For each case, inspect `/mcp/db/ledger?correlation_id=<id>` and confirm the same ID appears on the task and ledger rows.
5. Record every unexpected `FAILED`, `VALIDATION_ERROR`, duplicate document or missing ledger row as a defect.
6. Obtain the named business owner's sign-off for each lane before any dry-run flag is removed.

## Promotion gate

Do not remove `dry_run` from a lane until its UAT pack passes, its owner
signs off, all actions are visible in the ledger, and a rollback procedure
has been rehearsed. Production sends require a separately approved change.
