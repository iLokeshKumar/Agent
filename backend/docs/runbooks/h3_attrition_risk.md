# H3 Attrition Risk — Runbook

**Agent code:** H3  
**Autonomy:** A1 (propose only — scores informational, never modifies employment)

---

## Weekly scoring run

H3 runs weekly (Monday 08:00) via the nightly scheduler or on-demand:

```
POST /api/agents/run
{
  "agent_code": "H3",
  "payload": {
    "action": "score",
    "entity": "YEXIS-EL"
  }
}
```

Scores all active employees and writes `risk_score` to `hr_employee`.

---

## Viewing the risk report

```
POST /api/agents/run
{
  "agent_code": "H3",
  "payload": {
    "action": "report",
    "entity": "YEXIS-EL",
    "min_tier": "high"
  }
}
```

`min_tier` options: `high` (default), `medium` (includes medium + high).

---

## Interpreting scores

| Score | Tier   | Recommended Action                              |
|-------|--------|-------------------------------------------------|
| 60–100| HIGH   | Manager 1:1 within 7 days                       |
| 30–59 | MEDIUM | Manager check-in within 30 days                 |
| 0–29  | LOW    | No action required                              |

**Score components:**
- Tenure < 6 months: 30 pts (new joiners at risk of early exit)
- Tenure 6–12 months: 20 pts (critical retention window)
- Engagement gap > 90 days: 30 pts

---

## Confidentiality

Risk scores are HR-confidential. Do NOT:
- Share with the scored employee
- Include in morning digest or WhatsApp broadcasts
- Reference in performance reviews without HR Manager approval

---

## KPI

`attrition_risk_score` (org mean risk) is computed nightly by `compute_kpis()`.
Target: org mean < 40.

---

## Rollback

If scores were written in error:
```sql
UPDATE hr_employee SET risk_score = NULL WHERE entity = 'YEXIS-EL';
```
Scores are re-derived on next run.
