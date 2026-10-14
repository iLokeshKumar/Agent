# H4 Payroll Check — Runbook

**Agent code:** H4  
**Autonomy:** A1 (propose only — validation only, never processes payroll)

---

## Running a payroll check

Before every monthly payroll run, Finance submits the payroll batch for validation:

```
POST /api/agents/run
{
  "agent_code": "H4",
  "payload": {
    "entity": "YEXIS-EL",
    "payroll_lines": [
      {"employee_id": "EMP001", "gross_pay": 45000, "department": "Sales"},
      {"employee_id": "EMP002", "gross_pay": 65000, "department": "Finance"}
    ],
    "max_gross": 500000
  }
}
```

`max_gross` is optional (default ₹5,00,000). Set lower for branch-specific runs.

---

## Reading the result

| Field                  | Meaning                                                   |
|------------------------|-----------------------------------------------------------|
| `headcount_match`      | `true` if payroll count = active registry count           |
| `high_severity`        | Number of HIGH anomalies — must be 0 to proceed           |
| `medium_severity`      | Number of MEDIUM anomalies — Finance review required      |
| `recommendation`       | Clear / Low Risk / DO NOT PROCESS                         |
| `anomalies`            | Array with type, severity, employee_id, detail            |

---

## Anomaly types

| Type                  | Severity | Action                                              |
|-----------------------|----------|-----------------------------------------------------|
| `ghost_employee`      | HIGH     | Investigate — possible fraud or data error          |
| `duplicate_employee`  | HIGH     | Remove duplicate line, re-submit                    |
| `zero_or_negative_gross` | HIGH  | Correct gross pay, re-submit                        |
| `invalid_gross`       | HIGH     | Fix data format, re-submit                          |
| `missing_employee`    | MEDIUM   | Confirm LWP / employee exit before processing       |
| `gross_exceeds_ceiling` | MEDIUM | Finance Controller approval to override ceiling     |

---

## Process gate

- `status=NEEDS_APPROVAL`: Finance Controller must resolve HIGH anomalies before payroll run
- `status=SUCCESS` with `medium_severity > 0`: Finance Manager reviews MEDIUM items, then may proceed
- `status=SUCCESS` with no anomalies: Proceed with payroll processing

---

## Data privacy

Payroll lines submitted to H4 are validated in memory only — never stored.
No payroll data appears in the Action Ledger or task output beyond the
validation result (anomaly descriptions only).

---

## Rollback

H4 writes nothing to the database. Disable the lane to prevent future runs:
```
PATCH /lanes/h4_payroll_check
{"dry_run": true}
```
