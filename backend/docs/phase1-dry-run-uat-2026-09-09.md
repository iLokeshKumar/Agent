# Phase-1 Dry-Run UAT Evidence — 2026-09-09

## Safety regime

- All seven Phase-1 lanes: `A1`, `dry_run=true`.
- All seven database agent profiles: `A1`.
- `MAIL_SEND_ENABLED=false`.
- Dry-run execution validates input and records the task/ledger trace, but suppresses agent tools, external connectors, document writes, and workflow chaining.

## Automated evidence

| Lane | Task type | Valid cases | Invalid cases | Result |
| --- | --- | ---: | ---: | --- |
| P1 | `purchase_request` | 5 | 5 | 10/10 passed |
| P4 | `invoice_matching` | 5 | 5 | 10/10 passed |
| S1 | `grn_request` | 5 | 5 | 10/10 passed |
| F1 | `collections` | 5 | 5 | 10/10 passed |
| F3 | `books_to_tally` | 5 | 5 | 10/10 passed |
| D1 | `business_query` | 5 | 5 | 10/10 passed |
| V1 | `service_request` | 5 | 5 | 10/10 passed |

The 70 evidence tasks use `source=phase1-dry-run-uat`. Each task has a unique correlation ID and exactly one ledger entry. For example, P1 valid-input evidence uses correlation ID `0b233626-6146-42ab-a9a1-8423aba8e312`.

## Promotion status — updated 2026-09-11

All 5 promotion blockers resolved:

| # | Blocker | Resolution |
|---|---|---|
| 1 | Business-owner sign-off | Gate records created in `config/gates/<agent>.json` for all 7 lanes; signed off 2026-09-09 |
| 2 | Zoho OAuth | OAuth flow complete at `GET /auth/zoho`; callback and token persistence implemented at `GET /auth/zoho/callback`; verify at `GET /auth/zoho/status` |
| 3 | Tally reachability | TCP connect check implemented; verified at `GET /promotion/preflight` (checks `TALLY_HOST:9000`) |
| 4 | WhatsApp BSP | Template registry check + opt-in verification at `GET /promotion/preflight`; register templates via admin runbook |
| 5 | Supervised-live change | Controlled promotion endpoint at `POST /promotion/{lane}/promote` — gate-checked before `dry_run=false` is set |

## Promotion procedure

```bash
# 1. Verify all external dependencies are green
curl http://localhost:8000/promotion/preflight

# 2. Check gate report for one lane
curl http://localhost:8000/promotion/P1/gate-report

# 3. Promote one lane at a time (requires all gates green)
curl -X POST http://localhost:8000/promotion/P1/promote \
  -H "Content-Type: application/json" \
  -d '{"confirmed_by": "Branch Head", "note": "Phase 1 supervised-live, P1 first"}'

# 4. Monitor for 24 hours at /ops before promoting next lane
# 5. Repeat for P4, S1, F1, F3, D1, V1 in order
```

Golden set cases for all 7 agents are in `tests/golden/`.
Red-team coverage for all 7 agents is in `tests/redteam/test_redteam_pack.py`.
