# Yexis AI Agent Platform — Backend

FastAPI + PostgreSQL + Orchestrator + Gateway. Automates Purchase-to-Pay, Books-to-Tally, and service-desk workflows with policy-gated, auditable agents.

## Architecture

```
                ┌─────────────┐
     Webhook ──▶│   Gateway   │ (8001) ─┐
                │ 8001        │         │
   Cron tick ──▶│ scheduler   │         │  POST /tasks
                └─────────────┘         │
                                        ▼
                              ┌──────────────────┐
                              │  Backend API     │ (8000)
                              │  FastAPI         │
                              │  /health /tasks  │
                              │  /agents /lanes  │
                              │  /approvals etc  │
                              └────────┬─────────┘
                                       │  queue
                              ┌────────▼─────────┐
                              │  Orchestrator    │
                              │  worker / chain  │
                              │  workflow_engine │
                              └────────┬─────────┘
                                       │  execute
                     ┌─────────────────┼─────────────────┐
                     ▼                 ▼                 ▼
              ┌───────────┐     ┌──────────┐     ┌───────────┐
              │ P1        │     │ F3       │     │ S1/P4 etc │
              │ Purchase  │     │ Books→   │     │ GRN /     │
              │ Intake    │     │ Tally    │     │ Invoice   │
              └───────────┘     └──────────┘     └───────────┘
                                       │
                              ┌────────▼─────────┐
                              │  Tally Gateway   │ ──▶ Tally XML (port 9000)
                              │  stage→approve→  │     or SimulatedTransport
                              │  post            │
                              └──────────────────┘
                                       │
                              ┌────────▼─────────┐
                              │  PostgreSQL      │ (5432)
                              │  yexis           │
                              └──────────────────┘
```

## Prerequisites

- Python 3.11+
- PostgreSQL 16 (or Docker)
- `pip` / `venv`

## Quick Start

### Option A — Docker (recommended for full stack)

From project root `E:\ai_agent_platform\`:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
if (-not (Test-Path backend\.env)) { Copy-Item backend\.env.example backend\.env }
# Set unique POSTGRES_PASSWORD and POSTGRES_APP_PASSWORD values in root .env.
```

```bash
docker compose up --build
# backend:8000, gateway:8001 (localhost only); Postgres is internal to Compose
```

The root `.env` supplies the Compose database-role credentials; `backend\.env` supplies
application integrations. These local files are ignored by Git and excluded
from the Docker build context. For production, inject both sets of values from
a secret manager instead of copying `.env` files to the server.

Compose keeps Postgres private to its network and uses a separate application
role for backend/gateway; the bootstrap database user is not used by the app.
The role-creation script runs only when Postgres initializes a new data volume.
For an existing volume, have a DBA create the `POSTGRES_APP_USER` role and
grant it connect plus schema usage/create permissions and ownership of existing
application objects before changing the app credentials. Ensure the app role
has `public` in its search path; if startup fails with `no schema has been
selected to create in`, connect as a database administrator to the same
database and run the following in `psql`, setting the variables to the actual
database and app role names. For local runs, use `POSTGRES_DB` and `POSTGRES_USER`
from `backend\.env`; for Compose, use `POSTGRES_DB` and `POSTGRES_APP_USER`
from the root `.env`:

```sql
\set app_db 'actual_database_name'
\set app_user 'actual_application_role'
CREATE SCHEMA IF NOT EXISTS public;
GRANT CONNECT, TEMPORARY ON DATABASE :"app_db" TO :"app_user";
GRANT USAGE, CREATE ON SCHEMA public TO :"app_user";
ALTER ROLE :"app_user" IN DATABASE :"app_db" SET search_path TO public;
```

Reconnect the app's database connection/restart the backend after this change.
If the app role does not own
existing application tables, the DBA must separately grant the required
permissions or transfer ownership; do not drop the database or volume.
Do not use `docker compose down -v` to force initialization on data you need to keep.
Changing a password in `.env` does not rotate a role in an existing database:
have the DBA change the database role password and runtime secret together.
Bind ports to localhost here; put a TLS reverse proxy
in front for remote access. This Compose setup is a pilot/single-host starting
point, not a substitute for a production secret manager, managed database,
backups, and monitoring.

Images share `backend/Dockerfile` — `MODULE`/`PORT` selects the service.

```bash
docker compose logs -f backend
docker compose logs -f gateway
docker compose down -v   # tear down inc. pgdata
```

### Option B — Local dev (two terminals)

**Terminal 1 — Backend (8000):**

```bash
cd E:\ai_agent_platform\backend
python -m venv agents
agents\Scripts\activate        # Windows
# source agents/bin/activate  # Linux/Mac
pip install -r requirements.txt
cp .env.example .env          # edit POSTGRES_PASSWORD, ZOHO_* etc.
uvicorn app.main:app --reload
# → http://127.0.0.1:8000
# → http://127.0.0.1:8000/docs  (Swagger)
# → http://127.0.0.1:8000/health
```

**Terminal 2 — Gateway (8001):**

```bash
agents\Scripts\activate
uvicorn gateway.main:app --port 8001
# or: uvicorn gateway.main:app --host 127.0.0.1 --port 8001
# → http://127.0.0.1:8001/gateway/health
# → http://127.0.0.1:8001/health
```

Backend must be up first — gateway posts tasks to `BACKEND_URL` (default `http://127.0.0.1:8000`, in Docker `http://backend:8000`).

### Public tunnel — required for campaign email links

Every campaign email bakes `APPROVAL_BASE_URL` into its click-tracking, open-pixel,
unsubscribe and pre-filled-form links. It **must be a stable public hostname** — if
it changes, those links in already-sent mail break. **Do not use a Cloudflare quick
tunnel** (`*.trycloudflare.com`): it rotates on every restart. The app logs a startup
warning if it detects one.

This machine uses **two ngrok agents, one per ngrok account** (a reserved ngrok
domain belongs to one account, and the free "1 session" limit is per account, so two
accounts run side by side):

| Agent | Account | Port | Permanent domain | Inspector |
|---|---|---|---|---|
| Twilio (`my-backend`, default `~/.ngrok/ngrok.yml`) | X | 6060 | `brayan-misshapen-lu.ngrok-free.dev` | 4040 |
| Yexis app (`scripts/ngrok-yexis.yml`) | Y | 8000 | `arrange-coagulant-garnet.ngrok-free.dev` | 4041 |

`APPROVAL_BASE_URL=https://arrange-coagulant-garnet.ngrok-free.dev`. Start the Yexis
tunnel in its own terminal, **in addition** to the Twilio one:

```powershell
# Terminal — Yexis ngrok (account Y). Twilio ngrok keeps running separately.
powershell -File scripts/start_tunnel.ps1
```

Notes:
- After changing `APPROVAL_BASE_URL`, **fully restart** uvicorn (env loads only at
  startup; `--reload` is not enough).
- `scripts/ngrok-yexis.yml` holds account Y's authtoken and is **git-ignored** — never
  commit it. To set up on a new machine, reserve the static domain at
  https://dashboard.ngrok.com/domains and put that account's authtoken in the file.
- Links only resolve while this machine is running uvicorn + the tunnel.
- ngrok's free tier shows a browser interstitial on first click; the ~$8/mo plan
  removes it on the same domain.

## Environment Variables

Copy `backend/.env.example` → `backend/.env`. All settings are loaded via `app/core/config.py` (pydantic-settings).

| Variable | Required | Default | Description |
|---|---|---|---|
| `APP_ENV` | No | `development` | `development`/`production` |
| `POSTGRES_HOST` | No | `localhost` | DB host |
| `POSTGRES_PORT` | No | `5432` | DB port |
| `POSTGRES_DB` | No | `yexis` | DB name |
| `POSTGRES_USER` | No | `postgres` | DB user |
| `POSTGRES_PASSWORD` | **Yes** | — | DB password; inject from a secret manager in production |
| `DATABASE_URL` | No | derived | Overrides host/port/db/user/pass if set |
| `MODEL_PROVIDER` | No | `disabled` | `openai_compatible`, `anthropic`, or `disabled` |
| `MODEL_BASE_URL` | For model calls | `""` | Provider API base URL |
| `MODEL_NAME` | For model calls | `""` | Model identifier at the selected provider |
| `MODEL_API_KEY` | Provider-dependent | `""` | Provider credential; never commit it |
| `MODEL_TIMEOUT_SECONDS` | No | `60` | Request timeout for the provider-neutral model client |
| `K1_MODEL_SHADOW_ENABLED` | No | `false` | Enable K1 draft-only shadow output; never sends campaign messages |
| `ZOHO_CLIENT_ID` | For live Zoho | `""` | Zoho API Console client id |
| `ZOHO_CLIENT_SECRET` | For live Zoho | `""` | Zoho client secret |
| `ZOHO_REFRESH_TOKEN` | For live Zoho | `""` | OAuth refresh token |
| `ZOHO_ACCESS_TOKEN` | For live Zoho | `""` | OAuth access token (auto-refreshed) |
| `ZOHO_ORG_ID` | For live Zoho | `""` | Zoho Books org id |
| `ZOHO_DATA_CENTER` | No | `in` | `us`/`eu`/`in`/`com.au`/`jp` |
| `ZOHO_REDIRECT_URI` | No | `http://127.0.0.1:8000/auth/zoho/callback` | OAuth callback |
| `ZOHO_SYNC_ENABLED` | No | `false` | Enable background sync |
| `ZOHO_SYNC_INTERVAL` | No | `900` | Sync interval seconds |
| `TALLY_HOST` | For real Tally | `""` | Tally Prime host IP/URL for XML-over-HTTP posting |
| `TALLY_PORT` | No | `9000` | Tally HTTP port |
| `TALLY_COMPANY` | No | `Yexis Electronics Pvt Ltd` | Default Tally company name |
| `MAIL_SEND_ENABLED` | No | `false` | Allow explicit SMTP send of approved outbound drafts |
| `APPROVAL_BASE_URL` | For campaign email | `http://localhost:8000` | Stable public base URL for tracking/unsubscribe/form links — see [Public tunnel](#public-tunnel--required-for-campaign-email-links). Must not be a `*.trycloudflare.com` quick tunnel. |
| `BACKEND_URL` | Gateway only | `http://127.0.0.1:8000` | Backend URL for scheduler |
| `LANE_F3_SECONDS` | No | `86400` | Nightly sync cadence seconds |
| `GATEWAY_WEBHOOK_SECRET_WABA` | No | `""` | HMAC secret for WhatsApp |
| `GATEWAY_WEBHOOK_SECRET_ZOHO_BOOKS` | No | `""` | HMAC for Zoho webhooks |
| `GATEWAY_WEBHOOK_SECRET_GRAPH` | No | `""` | HMAC for Graph |

## Database & Seeds

On startup `app/main.py:_init_database()` ensures tables and seeds:

- **23 risk classifications** + **9 policy rules** (`app/seeds/policy_seeds.py`)
- **2 users**: `admin` (human), `gateway` (service identity for webhooks/lanes) (`app/seeds/user_seeds.py`)
- Workflows + lane configs

Sequences `po_number_seq` / `grn_number_seq` are created once and advanced past any historic numbers so IDs never recycle.

To reset:

```bash
# drop and recreate via docker
docker compose down -v && docker compose up --build
# or local psql
psql -U postgres -d yexis -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
uvicorn app.main:app --reload  # re-seeds
```

## API Reference

Base: `http://127.0.0.1:8000`

| Method | Path | Description |
|---|---|---|
| GET | `/health` | DB connectivity check |
| POST | `/tasks` | Create task (`task_type`, `payload`, `requested_by`) |
| GET | `/tasks/{id}` | Fetch task + result |
| GET | `/agents` | List agents + `enabled` |
| POST | `/agents/{code}/run` | Direct agent run |
| GET/POST | `/approvals` | List / decide approvals |
| GET | `/lanes` | Lane governance status |
| POST | `/lanes/{lane}/evaluate` | Check allowed/reason |
| GET | `/sync/status` | Sync watermarks |
| POST | `/workflows/run` | Run workflow chain |
| GET | `/mail/health` | POP3/SMTP configuration state |
| POST | `/mail/poll` | Manual POP3 intake, duplicate-safe |
| GET | `/mail/drafts` | Internal outbound drafts |
| POST | `/mail/drafts/{id}/send` | Explicit approval-gated SMTP send |
| GET | `/mcp/tally/*` | Tally staging (list/approve/post) |
| GET | `/mcp/local_db/*` | Local DB MCP tools |
| GET | `/export` | Export endpoints |

Swagger: `http://127.0.0.1:8000/docs`

**Gateway** (`http://127.0.0.1:8001`):

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Gateway liveness |
| GET | `/gateway/health` | Sources + liveness |
| POST | `/webhooks/{source}` | `waba`/`zoho_books`/`graph` → task |
| GET | `/ops` | Local operations console |

## Provider-neutral model runtime

Optional model calls go through `app/services/model_provider.py`, not
provider-specific agent code. `openai_compatible` supports OpenAI, Mistral,
Ollama, vLLM, and compatible chat-completions endpoints; `anthropic` uses
Anthropic's Messages API. Set `MODEL_PROVIDER`, `MODEL_BASE_URL`, `MODEL_NAME`,
and (where needed) `MODEL_API_KEY` in the runtime environment. Switching the
configured provider does not require changing agent source. No automatic
provider failover is performed.

The adapter only returns generated text and usage. It does not give a model
direct access to tools or business systems: validate model output in
application code and keep policy decisions, approvals, and writes in the
existing deterministic services. See `docs/model-provider.md` for endpoint
examples and a safe rollout path. The adapter is infrastructure; the agents
still need explicit, tested call sites before model reasoning affects their
behavior.

## Agents

All in `app/agents/` — extend `BaseAgent` (`app/agents/base.py`), registered in `app/agents/registry.py`.

| Code | Name | File | Trigger | Description |
|---|---|---|---|---|
| **P1** | Purchase Intake | `p1_purchase_intake.py` | `purchase_request` | Validates PR, checks budget, creates PO (`PO-XXXX`) or `AWAITING_APPROVAL` |
| **S1** | GRN Serialisation | `s1_grn_serialisation.py` | `grn` | Creates Goods Receipt `GRN-XXXX`, serialises line items |
| **P4** | Invoice Matching | `p4_invoice_matching.py` | `invoice_matching` | 3-way match PR/PO/GRN→Invoice, flags variances |
| **F3** | Books to Tally | `f3_books_to_tally.py` | `books_to_tally` | Fetches Zoho journals → Tally XML + reconciliation (see Integrations) |
| **F1** | Collections | `f1_collections.py` | `collections` | AR follow-up |
| **D1** | Ask Business | `d1_ask_business.py` | `ask_business` | NL query over books/contacts |
| **V1** | Service Desk | `v1_service_desk.py` | `service_desk` | WhatsApp/Graph intake via gateway |

Validate → Execute contract: `validate(payload) -> str|None`; `execute(payload, task_id, requested_by) -> AgentResult(status, output, actions_taken)`.

## Integrations

### Zoho Books — `app/integrations/zoho_books.py` (570 LOC)

Full OAuth2 client for `api/v3` (journals, invoices, bills, contacts, COA, organizations). Handles token refresh, pagination, per-data-center base URLs.

**Credentials required for live sync** (from https://api-console.zoho.com/):

1. Register client → scopes: `ZohoBooks.journals.READ`, `ZohoBooks.invoices.READ`, etc.
2. Complete OAuth flow → `refresh_token`, `access_token`, `org_id`.
3. Set in `.env`:

```env
ZOHO_CLIENT_ID=1000.xxxxx
ZOHO_CLIENT_SECRET=yyyyy
ZOHO_REFRESH_TOKEN=1000.zzzzz
ZOHO_ACCESS_TOKEN=1000.aaaaa
ZOHO_ORG_ID=123456789
ZOHO_DATA_CENTER=in
```

Tokens persist to `config/zoho_tokens.json` (auto-refreshed 5 min before expiry) via `TOKEN_FILE`. `ZohoBooksClient.configured` checks all 5 fields.

**Fallback (dev):** If `configured == False`, `F3BooksToTally.execute()` logs

```
Zoho Books not configured — using payload data
```

and uses `payload.journal_entries` instead of live API. The nightly gateway lane `nightly_books_sync` (`gateway/service.py`) now sends yesterday's date (`_yesterday_range()`) — without creds it produces a zero-entry reconciliation (balanced, `total_entries: 0`). This lets the full stage→approve pipeline run end-to-end in DEV.

**Key methods:** `list_journals`, `get_all_journal_lines_for_period`, `list_chart_of_accounts`, `list_invoices`, `list_bills`, `list_contacts`.

### Tally — `app/integrations/tally_gateway.py` (433 LOC) + `tally_xml.py` (356 LOC)

Statutory ledger write path — **stage → approve → post**, three hard rules enforced in `TallyGateway`:

1. Nothing posts unapproved (`status == STAGED` → `APPROVED` only via `approve()`).
2. Post renders from stored rows (`voucher_xml` saved at stage time) — caller cannot substitute amounts.
3. One company at a time (`VALID_ENTITIES = ("YEXIS-EL", "YEXIS-SL")`).

**Transport:**

- If `TALLY_HOST` is unset, unit tests can still inject `SimulatedTallyTransport`.
- If `TALLY_HOST` is set, `HttpTallyTransport` sends real XML-over-HTTP to the Tally Prime host and marks the staged rows `POSTED` only after Tally accepts them.
- If the host is unreachable or rejects the voucher, the rows become `POST_FAILED` with the error message.
- Per-entity company names can be overridden with `TALLY_COMPANY_YEXIS_EL` and `TALLY_COMPANY_YEXIS_SL`; otherwise `TALLY_COMPANY` is used.

**XML Builder** (`tally_xml.py`): `TallyXMLBuilder` → `ENVELOPE/HEADER/BODY/IMPORTDATA` with `VOUCHER`, `ALLLEDGERENTRIES.LIST`, `ISDEEMEDPOSITIVE`, `AMOUNT`. Helpers `zoho_lines_to_tally_vouchers` + `generate_tally_xml_from_entries`.

**Usage:**

```python
from app.integrations.tally_gateway import TallyGateway
gw = TallyGateway(db)
rows = gw.stage_entries(entity="YEXIS-EL", books_txn_id="txn-123", entries=[...])
gw.approve([r.id for r in rows], approved_by="admin")
result = gw.post_approved(entity="YEXIS-EL")  # → {posted_transactions, refused}
```

Date handling via `_coerce_date` — accepts `date`/`datetime`/`YYYY-MM-DD`, empty strings → `None`.

## Gateway Lanes & Webhooks

`gateway/service.py` — front door (port 8001):

- **Webhooks** `POST /webhooks/{source}` — verifies `X-Signature-256` HMAC (`GATEWAY_WEBHOOK_SECRET_*`), dedupes via `GatewayEvent` (`event_key` = `event_id`/`id`/`message_id` or payload hash), translates via `gateway/translations.py`, enforces lane governance (kill-switch, quiet hours, daily cap) before `Task` creation (`requested_by: "gateway"`).

- **Scheduler** — `default_lanes(BACKEND_URL)` returns:

```python
SchedulerLane(name="nightly_books_sync", every_seconds=86400, task_type="books_to_tally", payload={"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"})
```

Cadence via `LANE_F3_SECONDS`. Runs in daemon thread `run_scheduler_loop` (sleeps 5s, checks `due()`). Posts to `POST {BACKEND_URL}/tasks` with `requested_by: "gateway"` (service user — must exist in `user_seeds.py`).

## MCP (Model Context Protocol)

`backend/mcp/` — MCP tool servers (for agent tool-use):

| Folder | Status | API |
|---|---|---|
| `mcp/tally/` | **Active** | `app/api/mcp_tally.py` — staging list/approve/post, trial balance |
| `mcp/local_db/` | **Active** | `app/api/mcp_local_db.py` — SQL tools over local DB |
| `mcp/cloud_db/` | **Stub** | Empty dir, `.gitkeep` — planned cloud DB tools |
| `mcp/whatsapp/` | **Stub** | Planned WABA tools (currently via gateway `waba` webhook) |
| `mcp/rocketreach/` | **Stub** | Planned enrichment tools |

Stubs are ignored at runtime; routers only expose tally/local_db.

## Config / Docs / Deployment

| Path | Status | Content |
|---|---|---|
| `backend/config/` | Active | `.gitkeep`, `cache/`, `agents.yaml` (empty), `database.yaml` (empty), `zoho_tokens.json` (generated at runtime) |
| `backend/docs/` | Empty | Add `api.md`, `runbook.md` as needed |
| `backend/deployment/` | Empty | Add `Dockerfile.prod`, `k8s/` etc. |
| `backend/docker/` | Empty stub | Remove — `backend/Dockerfile` is the image |
| `backend/skills/` | Active | Agent skill definitions |
| `config/agents.yaml` (repo root) | Legacy | Duplicate of `backend/config/agents.yaml`, safe to delete |

## Tests & Checks

```bash
cd backend
pytest tests/ -q                          # unit + contract (SQLite, no services)
pytest tests/redteam/ -q                  # red-team pack (mandatory in CI)
pytest tests/test_gateway_translations.py # webhook translation
pytest tests/test_lane_governance.py
pytest tests/test_tally_gateway.py

# Vertical checks against live server (8000 up):
python ../lifecycle_check.py              # P1: under/over budget, approve/reject
python ../p2p_check.py                    # PR→P1→PO→S1→GRN→P4→F3 (reads SQL, no writes)
```

CI: `.github/workflows/ci.yml` — runs on `push` to `backend/**`, Python 3.11, `pytest tests/ -q` + `pytest tests/redteam/ -q`.

## Project Structure

```
backend/
  app/
    main.py              # lifespan, DB init, router includes
    core/                # config, db, policy, approval
    api/                 # 11 routers
    agents/              # P1,S1,P4,F3,F1,D1,V1 + base, registry
    integrations/        # zoho_books.py, tally_gateway.py, tally_xml.py
    models/              # task, user, approval, tally_staging, lane_config, etc.
    seeds/               # policy, user, workflow, lane
    sync/                # scheduler, syncer, cache, watermark
    gateways/            # mock_procurement, procurement_base
    repository/          # lane, task, approval, etc.
    policy/              # engine, rules, models
  gateway/
    main.py              # FastAPI + scheduler thread + /health
    service.py           # WebhookService, SchedulerLane, default_lanes
    translations.py      # provider payload → task
  orchestrator/
    worker.py            # task worker
    executor.py          # agent execution
    workflow_engine.py   # DAG engine
    chain.py / queue.py / router.py
  mcp/                   # tally, local_db (active); cloud_db etc. (stubs)
  config/                # zoho_tokens.json (runtime), cache/
  tests/                 # + redteam/
  Dockerfile             # shared image (MODULE/PORT)
  requirements.txt
  .env.example
../docker-compose.yml    # postgres + backend + gateway
../lifecycle_check.py    # P1 check
../p2p_check.py          # P2P vertical
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `User 'gateway:nightly_books_sync' not found` | Old gateway sent `gateway:{lane}` as user | Pull latest `gateway/service.py:234` — now `requested_by: "gateway"` |
| `Validation failed: 'start_date' is required` | Nightly lane sent `""` | Fixed `_yesterday_range()` in `gateway/service.py:222` — sends `YYYY-MM-DD` |
| `Zoho Books not configured — using payload data` | `ZOHO_*` not set | Expected in DEV; set creds in `.env` for live journals |
| `404 /health` on gateway | Missing route | Added `@app.get("/health")` in `gateway/main.py:27` — also `/gateway/health` exists |
| `TALLY_HOST set but HTTP transport not implemented` | Old docs only | Code now uses real XML-over-HTTP when `TALLY_HOST` is set |
| `Users already seeded — skipped` | Normal on reload | — |
| `POST /tasks 200 OK` but task `FAILED` | Check `GET /tasks/{id}` → `error` field; often payload validation | Ensure `start_date`/`end_date` `YYYY-MM-DD`, `entity` in `VALID_ENTITIES` |

## License

Internal — Yexis.
