# H2 — Onboarding

**Version:** 1.0.0  
**Autonomy:** A2 (supervised — checklist creation automatic, item completion needs owner)  
**Go-live:** 2026-09-11  
**Primary KPI:** onboarding_completion_rate  
**KPI Target:** 100 % completion by end of day 7

## Purpose

Manages the new-hire onboarding checklist from first day through policy sign-off.
Ensures IT, Finance, Admin, and HR tasks are tracked and nothing is missed.

## Trigger

- H1 marks a position as filled → HR team triggers `action=start` for the hired employee
- Item owners complete tasks → `action=update` per item

## Actions

| Action   | Description                                           | Autonomy |
|----------|-------------------------------------------------------|----------|
| start    | Create 10-item default checklist for new hire        | A2       |
| update   | Mark a specific item as completed (owner confirms)   | A2       |
| complete | Assert all items done; flip onboarding_status=done   | A2       |
| report   | Progress snapshot with overdue items highlighted     | A1       |

## Default Checklist Items

| Item                          | Owner   | Due (days from join) |
|-------------------------------|---------|---------------------|
| Issue laptop & accessories    | IT      | Day 1               |
| Create AD / email account     | IT      | Day 1               |
| Tally access granted          | Finance | Day 2               |
| Zoho CRM access granted       | IT      | Day 2               |
| ID card issued                | Admin   | Day 3               |
| Joining kit handed over       | HR      | Day 1               |
| Policy documents signed       | HR      | Day 5               |
| Payroll details collected     | Finance | Day 5               |
| Reporting manager introduced  | HR      | Day 1               |
| 30-day check-in scheduled     | HR      | Day 7               |

Custom items can be added via `custom_items` array in the `start` payload.

## Constraints

- `action=complete` is blocked if any item remains open
- Once `start` is called, re-calling it is rejected (idempotent guard)
- Item completion timestamps are immutable once written

## Related

- [[h1_recruitment]] — upstream: H1 provides the employee record
- [[h3_attrition_risk]] — downstream: onboarding quality affects early engagement score
