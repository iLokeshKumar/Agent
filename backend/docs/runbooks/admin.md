# Platform Admin Runbook

Covers: Alembic migrations, Tally Prime onboarding, WhatsApp template registration.

---

## 1. Alembic database migrations

### First-time setup

```bash
cd backend
pip install -e ".[dev]"
alembic init alembic          # only needed once
# Set sqlalchemy.url in alembic/env.py to read from settings:
#   from app.core.config import settings
#   config.set_main_option("sqlalchemy.url", settings.postgres_url)
```

### Generating a migration

```bash
alembic revision --autogenerate -m "add vendor_scorecard table"
# Review the generated file in alembic/versions/ before applying
alembic upgrade head
```

### Common migration scenarios

| Scenario | Command |
|---|---|
| New table added to enterprise.py | `alembic revision --autogenerate` |
| Column renamed | Edit migration by hand — autogenerate drops+adds |
| Rollback last migration | `alembic downgrade -1` |
| Check current revision | `alembic current` |
| Show pending | `alembic history --verbose` |

### Safety rules
- Never run `alembic upgrade` against prod without a backup
- Never modify a migration that has already been applied to prod
- Always test migration on staging DB first
- For large tables (> 1M rows), use `--sql` to generate SQL and review lock scope

---

## 2. Tally Prime onboarding

### Prerequisites
- Tally Prime 4.x installed on the local server
- Company created: **YEXIS-EL** and/or **YEXIS-SL**
- TCP/IP Tally gateway enabled (Tools → F12 Configure → TDL → Allow Remote Access)

### Network configuration
See: `backend/docs/tally-network-setup.md`

### Setting `.env` values

```dotenv
TALLY_HOST=localhost          # or IP of Tally machine
TALLY_PORT=9000               # default Tally XML port
TALLY_COMPANY=Yexis Electronics Private Limited
TALLY_COMPANY_YEXIS_EL=Yexis Electronics Private Limited
TALLY_COMPANY_YEXIS_SL=Yexis Softwares Private Limited
```

### Ledger map seeding

Run after initial migration to seed the `ledger_map` table:

```bash
python -m app.tools.seed_ledger_map --entity YEXIS-EL --file ledger_map_yexis_el.csv
```

The CSV format is: `books_code,tally_ledger,gst_treatment`

### F3 dry run

```bash
curl -X POST http://localhost:8000/agents/f3/run \
  -H "Content-Type: application/json" \
  -d '{"entity": "YEXIS-EL", "dry_run": true}'
```

Expected: `{"status": "ok", "staged": N, "drift_amount": 0.0}`

---

## 3. WhatsApp BSP — Gallabox template registration

### Account setup checklist
1. Register business on Meta Business Manager
2. Apply for WABA (WhatsApp Business Account) — 5–7 days for approval
3. Add phone number under the WABA
4. Connect to Gallabox: Settings → Channels → WhatsApp → Add Number
5. Note: `channelId` (= `waba_phone_number_id`), `WABA ID`, API key

### Set `.env` values

```dotenv
WABA_PROVIDER=gallabox
WABA_API_KEY=<gallabox_api_key>
WABA_API_URL=https://api.gallabox.com
WABA_PHONE_NUMBER_ID=<gallabox_channelId>
WABA_PHONE_NUMBER=+91XXXXXXXXXX
WABA_DISPLAY_NAME=Yexis Electronics
WABA_ID=<meta_waba_id>
```

### Registering a message template

1. Create template in Gallabox dashboard (Templates → New)
2. Category: UTILITY (for dunning/reminders) or MARKETING (for campaigns)
3. Submit for Meta approval — 24–48 hours
4. Once approved, add a row to `template_registry`:

```sql
INSERT INTO template_registry
  (template_id, channel, purpose, language, body, approval_status, approved_by, version)
VALUES
  ('f1_reminder_gentle', 'wa', 'dunning_tier1', 'en',
   'Dear {{1}}, your payment of ₹{{2}} was due on {{3}}. Please arrange payment at your earliest.',
   'meta', 'Gallabox Dashboard', 1);
```

### DLT registration (India only)

All promotional templates require DLT (Distributed Ledger Technology) registration
via TRAI. For UTILITY (transactional) templates DLT is optional but recommended.

1. Register on Airtel / Jio / BSNL DLT portal
2. Entity registration + header registration + template registration
3. Add DLT Template ID to `template_registry.variables_json`:
   `{"dlt_template_id": "1234567890123"}`

### Consent management

Every customer in `wa_contact` must have `opt_in_status = 'opt_in'` before
receiving outbound messages. Agents check this automatically. To bulk-import
opt-in status from a CSV:

```bash
python -m app.tools.import_wa_optins --file optins.csv
```

CSV format: `msisdn,opt_in_status,opt_in_ts`

---

## 4. Nightly analytics — manual trigger

```bash
curl -X POST http://localhost:8000/sync/nightly \
  -H "Content-Type: application/json" \
  -d '{"entity": "YEXIS-EL"}'
```

To check last run:

```sql
SELECT cursor FROM sync_watermark WHERE lane = 'NIGHTLY' AND source = 'analytics_chain';
```

---

## 5. Adding a new entity (YEXIS-SL onboarding)

1. Create Zoho Books org for YEXIS-SL, obtain `zoho_org_id_sl`
2. Set `.env`:  `ZOHO_ORG_ID_SL=<id>`
3. Run `alembic upgrade head` (entity column already nullable)
4. Seed `ledger_map`, `godown_map`, `costcentre_map` for YEXIS-SL
5. Add `TALLY_COMPANY_YEXIS_SL` to `.env`
6. Test F3 dry run for YEXIS-SL

---

## 6. Secrets rotation

- Zoho OAuth refresh token: re-run the consent flow at `/zoho/auth/start`
- Gallabox API key: rotate in dashboard, update `.env`, restart app
- SMTP password: update `.env`, restart app
- DB password: update `.env` + connection string, restart app
- **Never commit `.env` to git** — it is listed in `.gitignore`
