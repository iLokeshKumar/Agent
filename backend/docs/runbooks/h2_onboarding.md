# H2 Onboarding — Runbook

**Agent code:** H2  
**Autonomy:** A2 (supervised — checklist auto-created, item completion requires owner)

---

## Starting onboarding

Trigger after H1 marks a position as filled and the employee record exists:

```
POST /api/agents/run
{
  "agent_code": "H2",
  "payload": {
    "action": "start",
    "employee_id": "EMP045",
    "custom_items": [
      {"item": "GST registration for new branch", "owner": "Finance", "due_offset_days": 10}
    ]
  }
}
```

Creates 10 default items + any custom items. Sets `onboarding_status=in_progress`.

---

## Marking items complete

Each item owner completes their task and confirms:

```
POST /api/agents/run
{
  "agent_code": "H2",
  "payload": {
    "action": "update",
    "employee_id": "EMP045",
    "item": "Issue laptop & accessories",
    "completed_by": "IT-Rajesh"
  }
}
```

---

## Checking progress

```
POST /api/agents/run
{
  "agent_code": "H2",
  "payload": {
    "action": "report",
    "employee_id": "EMP045"
  }
}
```

Returns open/completed/overdue counts plus full item list.

---

## Completing onboarding

After all items are done:

```
POST /api/agents/run
{
  "agent_code": "H2",
  "payload": {
    "action": "complete",
    "employee_id": "EMP045"
  }
}
```

Returns `NEEDS_APPROVAL` if any items are still open — resolve them first.

---

## Default item list

| Item                         | Owner   | Due     |
|------------------------------|---------|---------|
| Issue laptop & accessories   | IT      | Day 1   |
| Create AD / email account    | IT      | Day 1   |
| Tally access granted         | Finance | Day 2   |
| Zoho CRM access granted      | IT      | Day 2   |
| ID card issued               | Admin   | Day 3   |
| Joining kit handed over      | HR      | Day 1   |
| Policy documents signed      | HR      | Day 5   |
| Payroll details collected    | Finance | Day 5   |
| Reporting manager introduced | HR      | Day 1   |
| 30-day check-in scheduled    | HR      | Day 7   |

---

## Rollback

If started in error:
```sql
DELETE FROM onboarding_checklist WHERE employee_id = 'EMP045';
UPDATE hr_employee SET onboarding_status = 'pending' WHERE employee_id = 'EMP045';
```
