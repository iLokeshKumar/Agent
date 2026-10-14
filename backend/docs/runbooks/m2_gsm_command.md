# M2 GSM Command — Runbook

**Agent code:** M2  
**Autonomy:** A1 (read-only — entity-wide intelligence for senior leadership)

---

## Territory overview

```
POST /api/agents/run
{
  "agent_code": "M2",
  "payload": {
    "action": "territory",
    "entity": "YEXIS-EL"
  }
}
```

Returns dealer count, total outstanding, and branch-level breakdown.

---

## Individual dealer scorecard

```
POST /api/agents/run
{
  "agent_code": "M2",
  "payload": {
    "action": "scorecard",
    "entity": "YEXIS-EL",
    "dealer_ref": "DLR-0042"
  }
}
```

Returns full DealerMart card: outstanding, overdue buckets, payment score,
last order, 13-week offtake, scheme eligibility, stock opportunity.

---

## KPI dashboard

```
POST /api/agents/run
{
  "agent_code": "M2",
  "payload": {
    "action": "kpis",
    "entity": "YEXIS-EL",
    "kpi_ids": ["dso_days", "stock_availability_pct", "vendor_otif_pct"]
  }
}
```

Omit `kpi_ids` to get the full default 9-KPI dashboard.

---

## Executive topline

```
POST /api/agents/run
{
  "agent_code": "M2",
  "payload": {
    "action": "topline",
    "entity": "YEXIS-EL"
  }
}
```

Returns: total AR, overdue AR, DSO, stock availability, OTIF, forecast accuracy, AMC renewal rate.

---

## When KPIs show NULL

KPIs return `null` when `compute_kpis()` has not yet run for the current period.
Trigger the nightly mart chain or run `POST /api/sync/nightly` to refresh.

---

## Rollback

M2 writes nothing. No rollback needed.
```
PATCH /lanes/m2_gsm_command
{"dry_run": true}
```
