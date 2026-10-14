# H1 — Recruitment

**Version:** 1.0.0  
**Autonomy:** A1 (propose only)  
**Go-live:** 2026-09-11  
**Primary KPI:** time_to_fill_days  
**KPI Target:** ≤ 30 days average

## Purpose

Manages Yexis Electronics hiring pipeline end-to-end:
- Creates job openings from approved department requisitions
- Drafts JD summaries for HR Manager review
- Tracks days-open per position and alerts on overdue positions

## Trigger

- HR team or department head submits hiring requisition via `/api/agents/run`

## Actions

| Action  | Description                                             | Autonomy |
|---------|---------------------------------------------------------|----------|
| intake  | Open new JobOpening, generate JD draft for HR review   | A1       |
| update  | Change status of an opening (filled, cancelled, on_hold)| A1       |
| report  | Pipeline summary with time-to-fill KPI                 | A1       |

## JD Draft

Auto-generated draft includes title, department, summary paragraph, and standard requirements list.
Always marked `status: draft — pending HR Manager review before publication`.
HR Manager must review and publish; H1 never publishes directly.

## Constraints

- All openings default to entity `YEXIS-EL` if not specified
- JDs are never posted to job portals autonomously — always HR Manager gate
- `time_to_fill_days` KPI is computed by `compute_kpis()` in `marts.py`, not by H1

## Related

- [[h2_onboarding]] — triggered after H1 marks an opening as filled
