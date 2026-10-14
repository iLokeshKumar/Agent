# H1 Recruitment — Runbook

**Agent code:** H1  
**Autonomy:** A1 (propose only — HR Manager publishes JDs)

---

## Opening a new position

```
POST /api/agents/run
{
  "agent_code": "H1",
  "payload": {
    "action": "intake",
    "title": "Sales Executive",
    "department": "Sales",
    "entity": "YEXIS-EL",
    "target_fill_date": "2026-10-11",
    "notes": "Replacement for Ravi Shankar who resigned."
  }
}
```

Response includes `opening_id` and `jd_draft`. HR Manager must review
the draft and publish to job portals manually — H1 never publishes.

---

## Updating an opening

```
POST /api/agents/run
{
  "agent_code": "H1",
  "payload": {
    "action": "update",
    "opening_id": "<uuid>",
    "status": "filled",
    "notes": "Hired: Arjun Mehta. Joining 2026-10-01."
  }
}
```

Valid statuses: `open`, `filled`, `cancelled`, `on_hold`.

---

## Pipeline report

```
POST /api/agents/run
{
  "agent_code": "H1",
  "payload": {
    "action": "report",
    "entity": "YEXIS-EL"
  }
}
```

Returns total openings, open count, filled count, avg fill days, and
overdue flags (target_fill_date < today).

---

## After a position is filled

1. Run H1 `action=update, status=filled` to close the opening
2. Create the new HrEmployee row via admin API or HR system sync
3. Trigger H2 `action=start` for the new employee to begin onboarding

---

## Rollback

H1 only creates `JobOpening` rows — no external systems affected.
To undo an erroneous opening:
```sql
DELETE FROM job_opening WHERE id = '<uuid>';
```
Or set `status=cancelled` via H1 update action.

---

## KPI

`time_to_fill_days` is computed nightly by `compute_kpis()` from filled
positions. Target: ≤ 30 days average.
