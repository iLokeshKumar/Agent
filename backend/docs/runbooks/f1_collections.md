# F1 Collections — Agent Runbook

Suite: Finance | Autonomy: A2 | Go-live: 2026-09-09

## Purpose
Segmented dunning cadence. Sends payment reminders over WhatsApp / Outlook
according to tier (T-3, due-today, overdue 1-30, 31-60, 60+). Escalates
chronic non-payers to stop-supply recommendations (human decides).

## Normal operation
1. Nightly: `dealer_mart` refreshed with latest outstanding / overdue
2. F1 runs each morning; builds cohort from `dealer_mart` overdue buckets
3. Excludes accounts with `ObjectLock` (disputed)
4. Sends reminders at A2 autonomy; logs every send to `message_log`
5. Parses inbound replies: promise → `promise_record`; dispute → `object_lock`

## Key thresholds
- Tier 1 (1-30 days overdue): WhatsApp, gentle tone, A2 autonomous
- Tier 2 (31-60 days): WhatsApp + email, firmer tone, A2 autonomous
- Tier 3 (61-90 days): Email, formal tone, human review recommended
- Tier 4 (90+ days): A1 proposal only — human approves before send

## Approval gate
Stop-supply recommendations require branch-head or GM sign-off.
Never automated. Always written to `action_ledger` as `PROPOSED`.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No reminders sent | `mail_send_enabled=False` or WA not configured | Check settings; verify template approval status |
| Disputed account receiving reminders | `object_lock` record missing | Add `ObjectLock` row for the account |
| DSO not improving | Cohort selection incorrect | Check `dealer_mart.overdue_buckets_json` data; verify date parsing |
| Promise records not being created | Inbound parsing failure | Check `message_log` for inbound parse errors |

## KPI target
DSO: 62 → 50 → 42 days over 3 quarters.
Monitor: `SELECT value FROM kpi_snapshot WHERE kpi_id='dso_days' ORDER BY period DESC LIMIT 10;`
