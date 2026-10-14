# ISM Command — M1
**Version:** 1.0.0  
**Autonomy:** A1  
**Go-live:** 2026-09-11  
**Primary KPI:** `dso_days`  
**KPI Target:** ≤ 42 days

## Purpose
Provides the Internal Sales Manager (ISM) with a unified command view of the internal sales team's performance, pipeline health, outstanding receivables, and DSO (Days Sales Outstanding). Surfaces actionable insights and follow-up priorities to keep DSO at or below 42 days. Operates in advisory mode (A1) — all actions are recommendations for the ISM to execute.

## Trigger
Triggered daily at 08:00 IST to generate the ISM morning briefing. Also triggered on-demand when the ISM requests a pipeline or receivables snapshot.

## Actions
| Action | Description | Autonomy |
|--------|-------------|----------|
| compute_dso | Calculate current DSO from receivables and sales data | A1 |
| build_pipeline_snapshot | Summarise open opportunities by stage, value, and ageing | A1 |
| flag_overdue_receivables | Identify invoices past due date with outstanding balances | A1 |
| prioritise_follow_ups | Rank accounts by DSO impact for ISM call list | A1 |
| generate_ism_briefing | Produce daily ISM command dashboard with KPIs and priorities | A1 |
| alert_dso_breach | Notify ISM immediately when DSO exceeds 42 days | A1 |

## Constraints
- Read-only access to sales, receivables, and CRM data; the agent never modifies any record.
- The ISM briefing must be delivered by 08:30 IST; delays trigger an automatic retry.
- DSO calculation must use the standard formula: (AR Outstanding / Revenue) × Days in Period.
- Never surface individual customer financial data to team members below ISM level.
- Pipeline data must be sourced from the live CRM; stale data older than 24 hours is rejected.
