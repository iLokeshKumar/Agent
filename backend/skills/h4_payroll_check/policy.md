# H4 Policy

## Approval Gates

| Condition                                  | Gate                    |
|--------------------------------------------|-------------------------|
| Payroll batch has 0 HIGH anomalies         | Finance Manager can proceed |
| Payroll batch has any HIGH anomaly         | Finance Controller must resolve before run |
| `max_gross` override for specific employee | Finance Controller      |

## Constraints

1. Payroll lines submitted to H4 are never stored — validated in memory only
2. H4 output does NOT trigger any payment workflow — Finance must take manual action
3. `status=NEEDS_APPROVAL` result requires Finance Controller sign-off before payroll run proceeds
4. Ghost employees with HIGH severity must be investigated for potential fraud before clearing

## Data

- Input: caller-supplied `payroll_lines` list (not stored)
- Source of truth: `hr_employee` table (entity + status = active)
- No output table — result returned in task response only
