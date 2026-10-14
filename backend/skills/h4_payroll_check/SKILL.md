# H4 — Payroll Check

**Version:** 1.0.0  
**Autonomy:** A1 (propose only — validation report only, never modifies payroll)  
**Go-live:** 2026-09-11  
**Primary KPI:** payroll_anomaly_rate  
**KPI Target:** 0 HIGH-severity anomalies per run

## Purpose

Pre-payment payroll validation gate that compares a submitted payroll batch against the
live HrEmployee registry. Catches ghost employees, missing actives, duplicates, and out-of-band
gross amounts before funds are disbursed.

## Trigger

- Monthly by Finance team before payroll processing via `/api/agents/run`
- On-demand for mid-month corrections

## Checks

| Check                    | Severity | Description                                         |
|--------------------------|----------|-----------------------------------------------------|
| Ghost employee           | HIGH     | Payroll ID not found in active registry             |
| Missing employee         | MEDIUM   | Active employee absent from payroll                 |
| Duplicate employee_id    | HIGH     | Same employee appears more than once in batch       |
| Zero/negative gross      | HIGH     | Gross pay ≤ 0                                       |
| Gross exceeds ceiling    | MEDIUM   | Gross > `max_gross` (default ₹5,00,000/month)       |
| Invalid gross format     | HIGH     | Non-numeric gross_pay value                         |

## Constraints

- H4 never processes, approves, or triggers payroll disbursement
- `max_gross` ceiling is advisory — Finance Controller can override per employee via notes
- Payroll data is never persisted to the platform database
- Missing employee (MEDIUM) may be legitimate leave-without-pay — Finance to confirm

## Related

- [[h3_attrition_risk]] — headcount data consistency cross-check
- [[h1_recruitment]] — new joins should appear in registry before first payroll run
