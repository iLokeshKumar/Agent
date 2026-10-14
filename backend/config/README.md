# Config

| File | Status | Description |
|------|--------|-------------|
| `.env` (not committed) | Active | Copy from `../.env.example` — all runtime config via `app/core/config.py` |
| `zoho_tokens.json` | Generated | Persisted OAuth tokens (`access_token`, `refresh_token`, `token_expiry`) — written by `app/integrations/zoho_books.py:TOKEN_FILE`, git-ignored |
| `cache/` | Runtime | Watermark / sync cache, git-ignored |
| `agents.yaml` | Stub | Legacy agent manifest — not read at runtime (agents use `app/agents/registry.py`). Safe to delete. |
| `database.yaml` | Stub | Legacy DB manifest — not read (DB via `DATABASE_URL` / `POSTGRES_*`). Safe to delete. |

Do not commit `.env` or `zoho_tokens.json`.
