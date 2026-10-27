from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives in backend/.env regardless of cwd (pytest runs from repo root)
_BACKEND_ENV = str(Path(__file__).resolve().parents[2] / ".env")
_ROOT_ENV = str(Path(__file__).resolve().parents[3] / ".env")


class Settings(BaseSettings):
    app_env: str = "development"

    # Identity used when a task is submitted without `requested_by`.
    # The Policy Gate needs a real, enabled user to evaluate step 1,
    # so a task with no identity can never be executed. Until real
    # auth lands, this is the single seam that supplies it.
    default_requested_by: str = "admin"
    # Shared secret used only by the internal automation gateway when
    # submitting scheduled tasks to the authenticated task API.
    gateway_api_token: str = ""
    gateway_service_username: str = ""

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "yexis"
    postgres_user: str = "postgres"
    postgres_password: str

    # Optional provider-neutral model runtime. Model calls are disabled by
    # default; the app remains usable without an external AI provider.
    model_provider: str = "disabled"
    model_base_url: str = ""
    model_name: str = ""
    model_api_key: str = ""
    model_timeout_seconds: float = 60
    k1_model_shadow_enabled: bool = False

    # Cloud analytics store (yexis_agent_cloud) — target of the nightly
    # Local→Cloud shipper and home of the read-only cloud_db MCP tools.
    # Empty = not configured: shipper no-ops, cloud_db router returns 503.
    cloud_database_url: str = ""

    # Nightly analytics chain (marts → KPIs → cloud ship → DPDP purge).
    nightly_analytics_enabled: bool = True
    nightly_analytics_hour: int = 2  # local hour after which it may run

    # Morning digest + weekly branch scorecard delivery
    digest_hour: int = 8  # local hour for 08:00 MD digest
    digest_recipient_email: str = ""  # MD / GM email for digest delivery
    digest_recipient_wa: str = ""  # E.164 phone (no +) for WA digest

    # Headcount for revenue_per_fte KPI (set from HR system or config)
    headcount: int = 0

    # Zoho Books API
    zoho_client_id: str = ""
    zoho_client_secret: str = ""
    zoho_refresh_token: str = ""
    zoho_access_token: str = ""
    zoho_org_id: str = ""
    # Required to encrypt per-user Zoho refresh tokens at rest. Generate a
    # high-entropy secret and keep it stable across application restarts.
    zoho_token_encryption_key: str = ""
    zoho_data_center: str = "in"  # us, eu, in, com.au, jp
    zoho_redirect_uri: str = "http://127.0.0.1:8000/auth/zoho/callback"
    zoho_sync_enabled: bool = False
    zoho_sync_interval: int = 900  # seconds (15 min default)
    # India GST default for PO line items (IGST 18% — inter-state vendor)
    zoho_default_po_tax_id: str = "4133299000000034179"

    # Zoho MCP — real (no mock) — Zoho caps 300 scopes per server, so Yexis uses 2 servers
    zoho_mcp_server_url: str = ""  # legacy single URL (kept for compat)
    zoho_mcp_server_url_yexis: str = ""  # https://yexis-931298440.zohomcp.com/...
    zoho_mcp_server_url_agent: str = ""  # https://agent-931298440.zohomcp.com/...

    # Zoho MCP connector OAuth — distinct from the Books/CRM REST OAuth above.
    # The MCP servers reject the REST access token (401); they need a token
    # issued to a connector registered in the Zoho MCP admin console. These
    # values come from that console registration. authorize_url is the
    # proprietary endpoint, e.g. https://mcp.zoho.in/mcp-client/<hash>/<name>/oauth
    # resource is the MCP message URL the token is scoped to (the YE server).
    zoho_mcp_client_id: str = ""
    zoho_mcp_client_secret: str = ""
    zoho_mcp_authorize_url: str = ""
    zoho_mcp_token_url: str = "https://accounts.zoho.in/oauth/v2/token"
    zoho_mcp_redirect_uri: str = "http://127.0.0.1:8000/auth/zoho-mcp/callback"
    zoho_mcp_scopes: str = ""   # space-separated; CRM READ scopes + ZohoMCP.tool.execute
    zoho_mcp_resource: str = ""  # the MCP message URL (YE server) the token is bound to
    # Prefer MCP for Zoho campaign audiences; fall back to the REST clients.
    k2_zoho_mcp_enabled: bool = True

    # Tally Prime — real XML-over-HTTP (no mock fallback in prod)
    tally_host: str = ""
    tally_port: int = 9000
    tally_company: str = "Yexis Electronics Pvt Ltd"
    # Per-entity companies: YEXIS-EL and YEXIS-SL map to separate
    # Tally company names. Empty falls back to tally_company.
    tally_company_yexis_el: str = ""
    tally_company_yexis_sl: str = ""
    tally_company_yexis_sol: str = ""
    tally_timeout: int = 30  # seconds

    # WhatsApp Business Platform (BSP) — real (Gallabox channel)
    waba_provider: str = ""  # gallabox | gupshup | twilio | meta
    waba_api_key: str = ""     # Gallabox: API Key (the request identifier)
    waba_api_secret: str = ""  # Gallabox: API Secret — required alongside the key
    waba_api_url: str = ""
    waba_phone_number_id: str = ""  # for Gallabox: channelId (e.g. 6a8fea...)
    waba_app_name: str = ""  # Gallabox app name / Gupshup app
    waba_phone_number: str = ""  # display phone E.164, e.g. +15554092635
    waba_display_name: str = ""  # e.g. Lokesh Kumar
    waba_id: str = ""  # WABA ID (112355...)
    gateway_webhook_secret_waba: str = ""

    # Hosted mailbox integration. POP3 is receive-only; SMTP is used only
    # after an explicit approval and is disabled by default.
    pop3_host: str = ""
    pop3_port: int = 995
    pop3_username: str = ""
    pop3_password: str = ""
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    mail_send_enabled: bool = False
    # Randomized delay after each K3 campaign email send. A fixed delay is
    # itself a machine-regular pattern; a random range between two sends
    # reads closer to how a person actually sends mail.
    smtp_send_delay_min_seconds: float = 20.0
    smtp_send_delay_max_seconds: float = 75.0
    # Max campaign emails sent per scheduler tick (every 5 min — see
    # gateway/service.py k3_campaign_scheduler lane). This is what makes
    # sending "trickle" across the day instead of bursting a whole
    # audience at once: a small cap here + the 5-min tick cadence spreads
    # delivery over hours, which is what actually avoids the "sent via
    # script" signature — a burst of hundreds of identical-template
    # emails within seconds is what look like automation, not the send
    # mechanism itself.
    k3_email_trickle_batch_size: int = 5
    # Only send campaign email inside this IST hour window. Sending at
    # 3am is itself a signal nobody is manually behind the send.
    k3_email_send_window_start_hour: int = 9
    k3_email_send_window_end_hour: int = 20

    # Campaign intelligence (see app/services/campaign_intel.py)
    k3_crm_escalation_enabled: bool = True   # hot replies / hot engagement -> Zoho CRM task
    k3_crm_task_owner_id: str = ""           # Zoho CRM user id to own the tasks ("" = token owner)
    k3_hot_score_threshold: int = 5          # engagement score at which a contact goes on the call list
    k3_cold_stop_after_emails: int = 3       # stop emailing after N emails with no open/click/reply (0 = off)
    k3_ab_min_per_variant: int = 10          # min sends per variant before a winner can be picked
    k3_ab_decide_after_hours: float = 24.0   # wait this long after the last test send before deciding
    k3_ab_test_fraction: float = 0.2         # share of the audience used for the A/B test

    # Content-correctness gate (see app/services/campaign_lint.py). When on,
    # K3 refuses to broadcast a template with a blocker-level problem (leaked
    # {{tokens}}, a merge field that renders blank, broken Jinja, a missing
    # attachment, an empty body) unless an authorised caller passes
    # force=True with an override_reason. AI proofreading (spelling/grammar)
    # is author-time only (POST /templates/{id}/validate) and never blocks a
    # send, since it can false-flag brand names and SKUs.
    k3_content_gate_enabled: bool = True
    # When on, AI proofreading ALSO blocks a send if it finds spelling/grammar
    # issues (same force+override_reason escape hatch). It only blocks when a
    # model provider is configured AND returns issues — an unavailable checker
    # never blocks. Set False to keep proofreading advisory-only.
    k3_proofread_gate_enabled: bool = True
    # Comma-separated DKIM selectors to check for the sending domain (see
    # GET /mail/deliverability). If you know your selector (cPanel is usually
    # "default", Google Workspace "google"), set it here so the checker finds
    # the key reliably; common selectors are probed in addition.
    dkim_selectors: str = ""

    # ISM Campaign Assistant (Phase 1)
    ism_default_verticals: str = "Education,Government,PSU"  # default targeting for fit score
    ism_default_regions: str = "Tamil Nadu"
    ism_tier_fit_threshold: int = 70          # P1 = fit >= this AND intent >= intent threshold
    ism_tier_intent_threshold: int = 40
    ism_calls_per_isr_per_day: int = 25       # ISM team non-negotiable, used for capacity
    k3_max_touches_per_week: int = 2          # FR-SEQ-04: per contact, rolling 7 days, all channels
    ism_default_owner: str = ""               # ISR/ISM who gets tasks for contacts with no owner
    ism_presales_owner: str = ""              # who gets quote / site-visit handoffs
    ism_manager_email: str = ""               # weekly ISR cards are emailed here

    # Allowed email domains for employee registration.
    # Comma-separated, e.g. "yexis.com,yexiselectronics.com"
    # Empty = all non-blocked domains accepted.
    allowed_email_domains: str = ""

    # Apollo.io — real enrichment/sequences + LinkedIn outreach
    apollo_api_key: str = ""
    apollo_data_center: str = "in"
    # Apollo sequence ID used for LinkedIn campaign steps (pre-configured in Apollo).
    # K3 adds dealers to this sequence when channel="linkedin".
    apollo_linkedin_sequence_id: str = ""

    # RocketReach — real contact search
    rocketreach_api_key: str = ""

    # JustDial — official Leads / JD Omni partner API (K2 audience source).
    # Endpoint shape varies by contract: set the base URL, key (and secret if
    # your plan uses one) and adjust justdial_client._map_record to your fields.
    justdial_api_url: str = ""
    justdial_api_key: str = ""
    justdial_api_secret: str = ""

    # Scribd — download + parse contact documents (PDF/Excel/CSV). Optional key;
    # most docs need a reachable/shared download URL passed at build time.
    scribd_api_key: str = ""

    # Google Drive — pull contact spreadsheets (single file, folder, or link).
    # Path to a service-account JSON with Drive read access, OR base64 of it.
    # Defaults to the Firebase service account already shipped in the repo;
    # the target files/folder must be shared with its client_email.
    google_service_account_json: str = "iyexis-firebase-adminsdk-fbsvc-34f369cd49.json"
    google_drive_folder_id: str = ""

    # Google Drive MCP — backend calls a Drive MCP server over Streamable HTTP,
    # mirroring the Zoho/Apollo MCP clients. When google_drive_mcp_server_url is
    # set, K2's google_drive source talks MCP; otherwise it falls back to the
    # service-account REST client above. Token is the Bearer the server expects
    # (many Drive MCP servers use OAuth — see google_drive_mcp_client docstring).
    google_drive_mcp_server_url: str = ""
    google_drive_mcp_token: str = ""

    # Telegram Bot — campaign broadcasts + dealer chat
    telegram_bot_token: str = ""

    # Voice / IVR campaigns — Exotel (primary for India), Twilio Voice, Ozonetel
    voice_provider: str = ""      # exotel | twilio | ozonetel
    voice_api_key: str = ""       # Exotel: "SID:TOKEN" | Twilio: "SID:TOKEN" | Ozonetel: api_key
    voice_api_url: str = ""       # full endpoint override (optional)
    voice_caller_id: str = ""     # E.164 outbound number (e.g. 08039XXXXXX for Exotel)
    # TTS language for voice campaigns. Exotel supports en-IN, hi-IN, ta-IN, etc.
    voice_tts_language: str = "en-IN"

    # Firebase Cloud Messaging — push notifications to dealer app
    fcm_project_id: str = ""              # GCP project ID (for v1 HTTP API)
    fcm_server_key: str = ""              # Legacy API key (fallback when no service account)
    fcm_service_account_json: str = ""    # Base64-encoded service account JSON for v1 API

    # Platform RBAC — JWT session for the control-room UI
    jwt_secret: str = ""
    jwt_expiry_hours: int = 8

    # First/admin account — provisioned from environment configuration.
    admin_email: str = "lokeshkumar@adomita.com"
    admin_username: str = "admin"
    admin_password: str = ""

    # Email-ownership verification (6-digit code + link)
    email_verification_ttl_minutes: int = 30
    # When True, brand-new users must verify their email before first login.
    require_email_verification: bool = True

    # Approval card delivery
    approval_base_url: str = "http://localhost:8000"
    # HMAC secret for signing one-click approve/reject email links.
    # Generate with: python -c "import secrets; print(secrets.token_hex(32))"
    approval_hmac_secret: str = ""
    # Default SLA before an unactioned approval is escalated (hours).
    approval_sla_hours: int = 4

    @field_validator(
        "postgres_host",
        "postgres_db",
        "postgres_user",
        "postgres_password",
        "model_provider",
        "model_base_url",
        "model_name",
        "model_api_key",
        "cloud_database_url",
        "nightly_analytics_hour",
        "zoho_client_id",
        "zoho_client_secret",
        "zoho_refresh_token",
        "zoho_access_token",
        "zoho_org_id",
        "zoho_token_encryption_key",
        "gateway_api_token",
        "gateway_service_username",
        "zoho_data_center",
        "zoho_redirect_uri",
        "zoho_mcp_server_url",
        "zoho_mcp_server_url_yexis",
        "zoho_mcp_server_url_agent",
        "zoho_mcp_client_id",
        "zoho_mcp_client_secret",
        "zoho_mcp_authorize_url",
        "zoho_mcp_token_url",
        "zoho_mcp_redirect_uri",
        "zoho_mcp_scopes",
        "zoho_mcp_resource",
        "tally_host",
        "tally_company",
        "tally_company_yexis_el",
        "tally_company_yexis_sl",
        "tally_company_yexis_sol",
        "waba_provider",
        "waba_api_key",
        "waba_api_secret",
        "waba_api_url",
        "waba_phone_number_id",
        "waba_app_name",
        "waba_phone_number",
        "waba_display_name",
        "waba_id",
        "gateway_webhook_secret_waba",
        "pop3_host",
        "pop3_username",
        "pop3_password",
        "smtp_host",
        "smtp_username",
        "smtp_password",
        "smtp_from",
        "apollo_api_key",
        "apollo_linkedin_sequence_id",
        "rocketreach_api_key",
        "justdial_api_url",
        "justdial_api_key",
        "justdial_api_secret",
        "scribd_api_key",
        "google_service_account_json",
        "google_drive_folder_id",
        "google_drive_mcp_server_url",
        "google_drive_mcp_token",
        "telegram_bot_token",
        "voice_provider",
        "voice_api_key",
        "voice_api_url",
        "voice_caller_id",
        "voice_tts_language",
        "fcm_project_id",
        "fcm_server_key",
        "fcm_service_account_json",
        mode="before",
    )
    @classmethod
    def _strip(cls, v: object) -> object:
        if isinstance(v, str):
            # strip inline comment after value, e.g. "192.168.1.50  # comment"
            # respect that real URLs contain "https://" — only strip " # " with space
            if " # " in v:
                v = v.split(" # ", 1)[0]
            elif v.strip().startswith("#"):
                return ""
            v = v.strip().strip("\"'").strip()
            # treat explicit placeholders as empty (not configured)
            if v.lower() == "xxx" or v == "..." or "server.gallabox.com/devapi/..." in v:
                return ""
            if v == "192.168.1.50" or v == "192.168.1.50:9000":
                return ""  # placeholder example IP from docs — not a real Tally host
            return v
        return v

    model_config = SettingsConfigDict(
        env_file=(_BACKEND_ENV, _ROOT_ENV, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://"
            f"{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}"
            f"/{self.postgres_db}"
        )


settings = Settings()
