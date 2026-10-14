# M1 ISM Command — Runbook

**Agent code:** M1  
**Autonomy:** A1 (read-only — all outputs advisory; ISM acts via other agents)

---

## Morning pipeline check

```
POST /api/agents/run
{
  "agent_code": "M1",
  "payload": {
    "action": "pipeline",
    "entity": "YEXIS-EL",
    "branch": "Chennai"
  }
}
```

Returns: total outstanding, overdue dealer list (top 10), dealer count.

---

## Weekly visit plan

```
POST /api/agents/run
{
  "agent_code": "M1",
  "payload": {
    "action": "visits",
    "entity": "YEXIS-EL"
  }
}
```

Returns top-10 priority dealers for the week, ranked by outstanding × (100 - payment_score).

---

## Target tracking

```
POST /api/agents/run
{
  "agent_code": "M1",
  "payload": {
    "action": "targets",
    "entity": "YEXIS-EL"
  }
}
```

Returns `dso_days` vs 42-day target and total open receivables.

---

## Urgent alerts

```
POST /api/agents/run
{
  "agent_code": "M1",
  "payload": {
    "action": "alerts",
    "entity": "YEXIS-EL"
  }
}
```

Returns: `high_overdue_dealers` (d61_90 or over_90 > 0), `stalled_dealers` (no order > 30 days).

---

## Downstream actions

- High overdue dealer → hand off to F1 for collection call / stop-supply
- Stalled dealer → hand off to K1/K3 for campaign push
- Visit completed → ISM logs visit note manually via CRM

---

## Rollback

M1 writes nothing. No rollback needed. Disable lane if output is incorrect:
```
PATCH /lanes/m1_ism_command
{"dry_run": true}
```
