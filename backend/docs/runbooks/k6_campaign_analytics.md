# K6 Campaign Analytics — Runbook

**Agent code:** K6  
**Autonomy:** A1 (read-only — computes KPIs from CampaignContact data)

---

## Computing KPIs for a campaign

```
POST /api/agents/run
{
  "agent_code": "K6",
  "payload": {
    "action": "compute",
    "campaign_id": "<uuid>",
    "entity": "YEXIS-EL"
  }
}
```

Persists KPI snapshot to `Campaign.kpi_json`. Run after K5 responses are recorded.

---

## Viewing campaign performance

```
POST /api/agents/run
{
  "agent_code": "K6",
  "payload": {
    "action": "report",
    "entity": "YEXIS-EL"
  }
}
```

Returns a portfolio view across all broadcast/closed campaigns.

---

## Key KPIs

| KPI | Formula | Target |
|---|---|---|
| `delivery_rate_pct` | sent / audience × 100 | ≥ 95% |
| `response_rate_pct` | responded / sent × 100 | ≥ 20% |
| `conversion_rate_pct` | converted / responded × 100 | — |
| `hot_lead_rate_pct` | (interested+callback+converted) / sent × 100 | ≥ 5% |

---

## Rollback

K6 only writes to `Campaign.kpi_json` — no financial or operational writes.
Disable lane to stop KPI computation:
```
PATCH /lanes/k6_campaign_analytics
{"dry_run": true}
```

---

## Escalation

- `hot_lead_rate_pct` < 5% for 3 consecutive campaigns → Marketing Manager campaign review
- `delivery_rate_pct` < 80% → WABA delivery issue, escalate to K3 runbook escalation path
