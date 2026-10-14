# H3 — Attrition Risk

**Version:** 1.0.0  
**Autonomy:** A1 (propose only — no writes to employee contracts or compensation)  
**Go-live:** 2026-09-11  
**Primary KPI:** attrition_risk_score  
**KPI Target:** Org mean < 40 (low-risk zone)

## Purpose

Weekly flight-risk scoring of active employees to help managers take proactive retention steps.
Uses tenure + engagement signals; proposes targeted 1:1 check-ins for high-risk employees.

## Trigger

- Weekly (Monday 08:00) — `action=score`
- On-demand by HR Manager or MD

## Actions

| Action | Description                                                        | Autonomy |
|--------|--------------------------------------------------------------------|----------|
| score  | (Re)score all active employees and persist `risk_score` to DB     | A1       |
| report | Return scored list filtered by `min_tier` (high/medium/all)       | A1       |

## Scoring Model

| Signal          | Condition         | Points |
|-----------------|-------------------|--------|
| Tenure          | < 6 months        | 30     |
| Tenure          | 6–12 months       | 20     |
| Tenure          | 12–24 months      | 10     |
| Tenure          | > 24 months       | 0      |
| Engagement gap  | > 90 days         | 30     |
| Engagement gap  | 61–90 days        | 20     |
| Engagement gap  | 31–60 days        | 10     |
| Engagement gap  | ≤ 30 days         | 0      |

Max score = 100. Tiers: High ≥ 60, Medium 30–59, Low < 30.

## Constraints

- Proposals only: H3 never modifies compensation, role, or employment status
- Risk scores are informational — not to be shared with employees
- `attrition_risk_score` KPI (org mean) is populated by `compute_kpis()`, not H3 directly
- Future signal integration (exit interviews, performance data) requires HR Manager approval

## Related

- [[h2_onboarding]] — poor onboarding completion correlates with early attrition
- [[h4_payroll_check]] — headcount data cross-check
