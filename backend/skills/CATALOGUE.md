# Yexis AI Agent Skills Catalogue

Version policy: bump `version` on any change to `SKILL.md` or `policy.md`.
Go-live date is the date the agent passed UAT and was set to live autonomy in prod.

| Agent | Suite | version | Autonomy | Primary KPI | KPI Target | Go-live |
|---|---|---|---|---|---|---|
| C1 Indent Planning | SCM | 1.0 | A1 | `reorder_cycle_days` | ≤ 3 days | 2026-09-11 |
| C2 Dispatch Tracker | SCM | 1.0 | A2 | `dispatch_to_pod_days` | ≤ 3 days | 2026-09-11 |
| C3 SCM Claims Tracker | SCM | 1.0 | A2 | scm_claim_resolution_days | ≤ 30 days | 2026-09-11 |
| D1 Ask-the-Business | Dashboard | 1.1 | A1 | — (query service) | — | 2026-09-09 |
| F1 Collections | Finance | 1.0 | A2 | `dso_days` | ≤ 42 days | 2026-09-09 |
| F2 Scheme Claims | Finance | 1.0 | A2 | `f2_claim_accuracy_pct` | ≥ 95% | — |
| F3 Books→Tally | Finance | 1.0 | A3 | `f3_books_tally_drift_hrs` | 0 ₹ drift | 2026-09-09 |
| F4 Interentity Recon | Finance | 1.0 | A2 | interentity_open_items | < 5 unmatched > 30d | 2026-09-11 |
| F5 Cash Forecast | Finance | 1.0 | A1 | `cash_forecast_accuracy` | ≥ 80% week-1 | 2026-09-11 |
| H1 Recruitment | HR | 1.0 | A1 | `time_to_fill_days` | ≤ 30 days | 2026-09-11 |
| H2 Onboarding | HR | 1.0 | A2 | onboarding_completion | 100% by day 7 | 2026-09-11 |
| H3 Attrition Risk | HR | 1.0 | A1 | `attrition_risk_score` | org mean < 40 | 2026-09-11 |
| H4 Payroll Check | HR | 1.0 | A1 | payroll_anomaly_rate | 0 HIGH/run | 2026-09-11 |
| K1 Campaign Brief | Campaigns | 1.0 | A1 | — (campaign count) | — | 2026-09-11 |
| K2 Audience Builder | Campaigns | 1.0 | A2 | — (audience size) | — | 2026-09-11 |
| K3 Broadcast | Campaigns | 1.0 | A3 | `delivery_rate_pct` | ≥ 95% | 2026-09-11 |
| K4 Dealer Promo | Campaigns | 1.0 | A1 | `promo_conversion_rate` | ≥ 10% | 2026-09-11 |
| K5 Response Tracker | Campaigns | 1.0 | A2 | `response_rate_pct` | ≥ 20% | 2026-09-11 |
| K6 Campaign Analytics | Campaigns | 1.0 | A1 | `hot_lead_rate_pct` | ≥ 5% | 2026-09-11 |
| M1 ISM Command | ISM | 1.0 | A1 | `dso_days` | ≤ 42 days | 2026-09-11 |
| M2 GSM Command | GSM | 1.0 | A1 | — (read-only) | — | 2026-09-11 |
| P1 Purchase Intake | Purchase | 1.0 | A2 | `reorder_cycle_days` | ≤ 3 days | — |
| P2 Sourcing RFQ | Purchase | 1.0 | A2 | — | — | — |
| P3 Expediting | Purchase | 1.0 | A2 | `vendor_otif_pct` | ≥ 90% | — |
| P4 Invoice Matching | Purchase | 1.0 | A3 | `p4_auto_match_rate` | ≥ 95% | 2026-09-09 |
| S1 GRN Serialisation | Stores | 1.0 | A3 | `serial_conservation_rate` | 100% | — |
| S2 Stock Health | Stores | 1.0 | A1 | `stock_availability_pct` | ≥ 95% | — |
| S3 Reorder | Stores | 1.0 | A2 | `reorder_cycle_days` | ≤ 3 days | — |
| S4 Physical Verification | Stores | 1.0 | A2 | `serial_conservation_rate` | 100% | 2026-09-11 |
| V1 Service Desk | Service | 1.0 | A2 | — | — | — |
| V2 AMC Engine | Service | 1.0 | A2 | `amc_renewal_rate_pct` | ≥ 90% | 2026-09-11 |

## Agent registry (31 planned)

All 31 agents are now implemented. No planned agents remain.

## Skill file layout

```
backend/skills/<agent_id>/
  SKILL.md        — identity, procedure, autonomy, never-do rules
  policy.md       — approval thresholds, validation rules, tone
  read_tools.py   — mart read functions (D1 only; others use agent code)
  data_dictionary.md — field definitions (D1 only)
  example/        — example inputs and expected outputs
  output/         — sample rendered outputs
  test/           — pytest fixtures for the skill
```

## Version history

| Date | Agent | Change |
|---|---|---|
| 2026-09-09 | D1, F1, F3, P4 | Go-live (Phase 1 UAT passed) |
| 2026-09-11 | All | Catalogue created |
| 2026-09-11 | F4, F5 | Phase 2 Finance agents go-live |
| 2026-09-11 | H1, H2, H3, H4 | Phase 2 HR agents go-live |
| 2026-09-11 | S4, V2, C1, C2, C3 | Phase 3 Stores/Service/SCM agents go-live |
| 2026-09-11 | K1, K2, K3, K4, K5, K6 | Phase 3 Campaign suite go-live |
| 2026-09-11 | M1, M2 | Phase 3 Command agents go-live — all 31 agents complete |
| 2026-09-11 | All | Phase 4: registry.py, seeds, lane configs, 5 gate records, 23 KPIs |
| 2026-09-11 | D1 | Phase 5: 5 Phase-3 query types added; data_dictionary updated to 23 KPIs + Phase 3 tables |
