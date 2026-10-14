# H2 Policy

## Approval Gates

| Condition                            | Gate           |
|--------------------------------------|----------------|
| `action=start` for new hire          | HR Manager     |
| `action=complete` (all items done)   | HR Manager     |
| Adding non-standard checklist items  | HR Manager     |

## Constraints

1. `action=complete` must not be called unless `onboarding_status = in_progress`
2. Item completions cannot be reversed — if an item was marked done in error, HR Manager must raise a correction task
3. Default item list is versioned — changes require HR Manager + IT sign-off
4. Payroll access (Tally) must be granted by Finance — HR cannot self-complete this item

## Data

- Table: `onboarding_checklist` (employee_id + item composite key)
- Employee status: `hr_employee.onboarding_status` (pending → in_progress → done)
