# Live-data validation runbook — 31 agents

## Purpose

Prove that each of the 31 registered agents can read current Yexis business data, produce a correct result, follow its approval policy, and leave an auditable trace. A green health check or a passing golden case is not proof of live-data correctness.

## Runtime safety finding (2026-10-06)

The reachable API at `http://127.0.0.1:8000` returned `GREEN` health for 31 agents. `GET /lanes` showed all 31 lanes with `dry_run=false` and kill switches released. Do not submit validation tasks to this instance until its environment is confirmed and a safe validation environment is available.

`GET /promotion/preflight` returned `ready=false`: Zoho organization read succeeded and WhatsApp settings/template checks passed, but Tally at `192.168.7.2:9000` timed out. The WhatsApp check is configuration and template inventory; it does not prove a real message can be sent. This runbook does not authorize promotion or sends.

## Important implementation limits

- The normal executor's `dry_run=true` path validates payload shape, then returns a placeholder. It does **not** run the agent against business data.
- Agents can open their own database sessions and commit records. Some integrations can send or post externally. Setting `dry_run=false` is not a read-only test mode.
- The public direct-run route described in older README/training text (`POST /agents/{code}/run`) is not present in the current API router.
- The task router currently maps only 17 unique agent codes. P3, F2, F4, F5, H1–H4, S4, V2, C1–C3, and M1 have no generic `/tasks` route.
- New lane rows are seeded with `dry_run=false`; an unconfigured lane also evaluates with `dry_run=false`. Validation must fail closed.

## Required execution environment

Use a dedicated validation deployment, not the currently reachable app until its purpose is verified:

1. Read from a production replica or a current, access-controlled snapshot of real records. Prefer a database role that is read-only at the database level.
2. Give external integrations read-only credentials. Where the provider uses POST for read queries, allow only the specific read operation; deny mutation operations.
3. Route all attempted writes, approvals, document creation, emails, WhatsApp, Tally posting, and workflow chaining to a deny-and-log sink. A write attempt must be observable and must not reach a production destination.
4. Use an isolated database clone for agents whose logic writes while preparing a result. Rebuild/reset the clone between cases. Do not grant the test process production write credentials.
5. Set lane defaults to dry-run/validation-required and reject execution if the environment is production, a read-only source is absent, or the side-effect blocker is not active.
6. Restrict report access and retention, especially for HR, payroll, customer, and dealer financial data. Reports should store record references and result summaries, not unnecessary raw personal data.

The existing boolean `dry_run` is insufficient for this. Implement a separate `shadow_read_only` execution mode with explicit read adapters and an enforced write-denial boundary before running agents.

## Test design

For every agent, create a case pack from real records with expected results reviewed by the relevant business owner. Start with at least 10 representative cases per agent, including normal, boundary, missing-data, stale-data, approval-required, and refusal cases. Increase coverage for high-risk financial and outbound agents.

Each case must include:

- agent code, build/version, test-case ID, source system, entity, and source record IDs;
- source-data watermark/time and independently prepared expected answer;
- actual result, status, confidence/quality measure where applicable, and cited source records;
- correlation ID and ledger evidence;
- external calls made and write attempts blocked;
- reviewer decision, defect ID, and retest result.

### Pass criteria

- Correct expected outcome and required fields for every case; numeric outputs match the agreed source calculation/tolerance.
- Every factual answer is traceable to source records and the stated data watermark.
- Correct approval/refusal behavior, with no cross-entity leakage or duplicate action proposals.
- Every run has one traceable correlation ID and complete ledger evidence.
- **Zero production writes, sends, posts, or workflow launches.** Any attempted side effect must be blocked and logged.
- No unresolved critical/high-severity defects for the agent before sandbox write testing.

## 31-agent roster and current generic task routing

The generic task queue can exercise the routed agents only after the safe shadow mode exists. `—` means no `task_type` maps to that agent in the current `orchestrator/router.py`.

| Agent | Current task type(s) |
|---|---|
| P1 | `purchase_request` |
| P2 | `sourcing_rfq` |
| P3 | — |
| P4 | `invoice_matching`, `payment_proposal` |
| S1 | `grn_request`, `grn` |
| S2 | `stock_health` |
| S3 | `reorder`, `reorder_review` |
| S4 | — |
| F1 | `collections`, `collections_cohort_scan` |
| F2 | — |
| F3 | `books_to_tally` |
| F4 | — |
| F5 | — |
| H1 | — |
| H2 | — |
| H3 | — |
| H4 | — |
| V1 | `service_request` |
| V2 | — |
| C1 | — |
| C2 | — |
| C3 | — |
| K1 | `campaign_brief` |
| K2 | `campaign_audience` |
| K3 | `campaign_broadcast`, `campaign_scheduler_tick` |
| K4 | `campaign_dealer_promo` |
| K5 | `campaign_response` |
| K6 | `campaign_analytics` |
| M1 | — |
| M2 | `gsm_morning_brief`, `gsm_command` |
| D1 | `business_query`, `md_digest` |

Before the full campaign, implement/test a non-production shadow invocation path for all 31. Do not use `POST /tasks` on the current instance as a substitute.

## Execution sequence

1. Confirm the validation deployment points at the intended read-only replica/snapshot and that external write egress is blocked.
2. Capture `/agents/health`, `/lanes`, connector read health, source watermarks, and the deployed build ID. Stop if any lane can write or send outside the sink.
3. Run the 31 case packs in shadow mode. Save one machine-readable result per case and one roll-up per agent.
4. Have business owners review incorrect, stale, unsupported, or ungrounded results. Fix defects and rerun affected packs.
5. Run actions that need persistence only against provider sandboxes/test companies and disposable DB clones, using synthetic recipients and bounded records.
6. Publish a signed report with pass/fail/blocked status per agent. A blocked integration is not a pass.
7. Only after shadow and sandbox gates pass, plan a separately approved supervised-live canary for one lane at a time. Record a rollback owner and window; monitor before expanding.

## Current result

| Check | Result |
|---|---|
| Agent health | 31 registered; API reports `GREEN` |
| Runtime lane state | 31/31 `dry_run=false`; 31/31 kill switches released |
| Zoho read preflight | Connected |
| Tally preflight | Blocked; host timed out |
| WhatsApp preflight | Configured/template check passed; send not tested |
| Live-data agent cases executed by this runbook | 0 |
| Production side effects performed | 0 |

This is a readiness snapshot, not an agent pass report. Do not mark any agent as live-data validated based on health, golden tests, or this preflight alone.
