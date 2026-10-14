# H3 Policy

## Approval Gates

| Condition                              | Gate           |
|----------------------------------------|----------------|
| Sharing high-risk list externally      | HR Manager     |
| Changing scoring model parameters      | HR Manager + MD|

## Constraints

1. Risk scores must never be surfaced to the scored employee directly via any channel
2. Score model changes (weights, tier thresholds) require HR Manager approval + gate re-sign
3. H3 output is HR-confidential — not included in morning digest or WhatsApp broadcasts
4. `action=score` is idempotent — re-scoring overwrites `risk_score` with latest value

## Data

- Source: `hr_employee` (join_date, last_engagement_ts, status)
- Output: `hr_employee.risk_score` (integer 0–100)
