import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from app.api.agent import router as agents_router
from app.api.approval import router as approvals_router
from app.api.auth import router as auth_router
from app.api.export import router as export_router
from app.api.health import router as health_router
from app.api.sync import router as sync_router
from app.api.task import router as tasks_router
from app.api.workflow import router as workflows_router
from app.api.mail import router as mail_router
from app.api.ops_console import router as ops_console_router
from orchestrator.worker import worker
from app.sync.scheduler import get_sync_scheduler
from app.sync.nightly import get_nightly_scheduler

logger = logging.getLogger(__name__)
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "static"


def _init_database():
    """Create tables and seed default data on first run."""
    from app.core.db import engine, SessionLocal
    from app.models.agent import Base

    # Import all models so Base.metadata knows about them
    import app.models.task  # noqa: F401
    import app.models.user  # noqa: F401
    import app.models.zoho_connection  # noqa: F401
    import app.models.access_control  # noqa: F401
    import app.models.approval  # noqa: F401
    import app.models.action_ledger  # noqa: F401
    import app.models.agent_run  # noqa: F401
    import app.models.policy  # noqa: F401
    import app.models.lane_config  # noqa: F401
    # Tally staging (stage-and-approve buffer)
    import app.models.tally_staging  # noqa: F401
    # Sync watermarks (incremental-pull cursors)
    import app.models.sync_watermark  # noqa: F401
    # Gateway webhook dedupe register
    import app.models.gateway_event  # noqa: F401
    # Purchase-to-Pay business documents
    import app.models.vendor  # noqa: F401
    import app.models.purchase_request  # noqa: F401
    import app.models.purchase_order  # noqa: F401
    import app.models.goods_receipt  # noqa: F401
    import app.models.invoice  # noqa: F401
    # Sourcing (P2 RFQ + vendor quotes)
    import app.models.rfq  # noqa: F401
    # Workflow engine
    import app.models.workflow  # noqa: F401
    # Enterprise Local/Cloud tables (real — not mock)
    # includes: DealerMart, SkuHealth, InstalledBase, VendorScorecard,
    #           KpiSnapshot, CashForecast, JobOpening, HrEmployee, OnboardingChecklist,
    #           PhysicalCountBatch, PhysicalCountLine, AmcContract, Indent,
    #           DispatchRecord, ScmClaim, Campaign, CampaignContact
    import app.models.enterprise  # noqa: F401
    # F2 scheme register + claim lifecycle
    import app.models.scheme  # noqa: F401
    # Books→Tally account mapping (F3 UAT dependency)
    import app.models.books_tally_map  # noqa: F401
    # RBAC activity audit trail
    import app.models.activity_log  # noqa: F401
    # Users, permissions + email-verification (code/link) table
    import app.models.user  # noqa: F401

    # Create missing tables (safe — won't drop existing)
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables ensured")

    # Add columns that were introduced after initial table creation
    from sqlalchemy import text as _text
    with engine.begin() as _conn:
        _conn.execute(_text(
            "ALTER TABLE email_message ADD COLUMN IF NOT EXISTS attachments JSONB NOT NULL DEFAULT '[]'"
        ))
        _conn.execute(_text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS whatsapp_number VARCHAR(20) UNIQUE"
        ))
        _conn.execute(_text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(254) UNIQUE"
        ))
        _conn.execute(_text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255)"
        ))
        _conn.execute(_text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_number VARCHAR(20) UNIQUE"
        ))
        # Approval card delivery fields (added after baseline migration)
        _conn.execute(_text(
            "ALTER TABLE approvals ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ"
        ))
        _conn.execute(_text(
            "ALTER TABLE approvals ADD COLUMN IF NOT EXISTS delivery_channel VARCHAR(20)"
        ))
        _conn.execute(_text(
            "ALTER TABLE approvals ADD COLUMN IF NOT EXISTS delivery_recipient VARCHAR(254)"
        ))
        _conn.execute(_text(
            "ALTER TABLE approvals ADD COLUMN IF NOT EXISTS sla_deadline TIMESTAMPTZ"
        ))
        _conn.execute(_text(
            "ALTER TABLE approvals ADD COLUMN IF NOT EXISTS escalated BOOLEAN NOT NULL DEFAULT FALSE"
        ))
        # Dynamic email: per-segment subject/body overrides on templates
        _conn.execute(_text(
            "ALTER TABLE template_registry ADD COLUMN IF NOT EXISTS segment_overrides_json JSONB"
        ))
        # ISM Campaign Assistant: approval gate + brief on campaigns
        for _col in (
            "ism_managed BOOLEAN NOT NULL DEFAULT FALSE",
            "ism_approved_ts TIMESTAMPTZ",
            "ism_approved_by VARCHAR(200)",
            "ism_brief_json JSONB",
        ):
            _conn.execute(_text(f"ALTER TABLE campaign ADD COLUMN IF NOT EXISTS {_col}"))
        # Email subject line (Jinja2-renderable) and reply-to per template
        _conn.execute(_text(
            "ALTER TABLE template_registry ADD COLUMN IF NOT EXISTS subject VARCHAR(300)"
        ))
        _conn.execute(_text(
            "ALTER TABLE template_registry ADD COLUMN IF NOT EXISTS reply_to VARCHAR(254)"
        ))
        # Shared email attachments per template (metadata; files on disk)
        _conn.execute(_text(
            "ALTER TABLE template_registry ADD COLUMN IF NOT EXISTS attachments_json JSONB"
        ))
        # Contact display name from Excel / source (used as {{ name }} variable)
        _conn.execute(_text(
            "ALTER TABLE campaign_contact ADD COLUMN IF NOT EXISTS name VARCHAR(200)"
        ))
        # Company / account name from Excel / source (used as {{ company }} variable)
        _conn.execute(_text(
            "ALTER TABLE campaign_contact ADD COLUMN IF NOT EXISTS company VARCHAR(300)"
        ))
        # Every other Excel column (title, role, seniority, department, ...) as
        # per-contact template variables — key = normalised column name
        _conn.execute(_text(
            "ALTER TABLE campaign_contact ADD COLUMN IF NOT EXISTS extra_json JSONB"
        ))
        # Widen msisdn to handle international formats safely (was VARCHAR(20))
        _conn.execute(_text(
            "ALTER TABLE campaign_contact ALTER COLUMN msisdn TYPE VARCHAR(30)"
        ))
        # Alternate email address — used as fallback when primary send/bounce fails
        _conn.execute(_text(
            "ALTER TABLE campaign_contact ADD COLUMN IF NOT EXISTS alt_email VARCHAR(200)"
        ))
        # RBAC: role + last_login_at on users
        _conn.execute(_text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(50) NOT NULL DEFAULT 'viewer'"
        ))
        _conn.execute(_text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ"
        ))
        # Email-ownership verification flag (login blocked until verified)
        _conn.execute(_text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT FALSE"
        ))
        # Campaign response form customisation (subtitle, field labels, toggles)
        _conn.execute(_text(
            "ALTER TABLE campaign ADD COLUMN IF NOT EXISTS form_config_json JSONB"
        ))

    _ensure_document_sequences(engine)

    # Seed default policy rules and users
    db = SessionLocal()
    try:
        from app.seeds.policy_seeds import seed_policy
        from app.seeds.user_seeds import seed_users
        from app.services.role_access import seed_role_access
        from app.seeds.workflow_seeds import seed_workflows
        from app.seeds.lane_seeds import seed_lanes
        from app.seeds.agent_seeds import seed_agents
        seed_policy(db)
        seed_users(db)
        seed_role_access(db)
        seed_workflows(db)
        seed_lanes(db)
        seed_agents(db)
    except Exception as exc:
        logger.warning("Seed skipped: %s", exc)
    finally:
        db.close()


def _ensure_document_sequences(engine) -> None:
    """Create the document-number sequences, once each.

    Document numbers must be unique for the lifetime of the database:
    S1 receives goods against a PO number and P4 matches invoices
    against both. An in-process counter cannot do that — it restarts
    with the process and is not shared between workers — so the source
    of truth is a Postgres sequence.

    On first creation each sequence is advanced past any number already
    issued from the old in-process counters, so historical records are
    never re-used.
    """
    from app.services.numbering import GRN_SEQUENCE, PO_SEQUENCE, RFQ_SEQUENCE

    # Highest PO number ever issued, read out of the task history.
    po_seed = r"""
        SELECT COALESCE(MAX(
            (regexp_match(
                result->'output'->>'po_number', '(\d+)$'
            ))[1]::bigint
        ), 1000)
        FROM tasks
        WHERE result->'output'->>'po_number' IS NOT NULL
    """

    # Same for GRNs, whose numbers came from S1's old _grn_counter.
    grn_seed = r"""
        SELECT COALESCE(MAX(
            (regexp_match(
                COALESCE(
                    result->'output'->'summary'->>'grn_number',
                    result->'output'->'grn'->>'grn_number'
                ), '(\d+)$'
            ))[1]::bigint
        ), 5000)
        FROM tasks
        WHERE result->'output'->'summary'->>'grn_number' IS NOT NULL
           OR result->'output'->'grn'->>'grn_number' IS NOT NULL
    """

    _ensure_sequence(engine, PO_SEQUENCE, po_seed, minimum=1000)
    _ensure_sequence(engine, GRN_SEQUENCE, grn_seed, minimum=5000)

    # RFQ numbers start fresh — P2 is their first and only issuer.
    rfq_seed = r"""
        SELECT COALESCE(MAX(
            (regexp_match(
                result->'output'->>'rfq_number', '(\d+)$'
            ))[1]::bigint
        ), 2000)
        FROM tasks
        WHERE result->'output'->>'rfq_number' IS NOT NULL
    """

    _ensure_sequence(engine, RFQ_SEQUENCE, rfq_seed, minimum=2000)


def _ensure_sequence(
    engine,
    name: str,
    seed_sql: str,
    minimum: int,
) -> None:
    """Create one sequence and advance it past existing numbers."""
    from sqlalchemy import text

    with engine.begin() as conn:
        already_exists = conn.execute(
            text(
                "SELECT 1 FROM pg_class "
                "WHERE relname = :name AND relkind = 'S'"
            ),
            {"name": name},
        ).scalar()

        if already_exists:
            return

        # CREATE SEQUENCE cannot take a bound parameter for the name.
        # `name` is a module-level constant, never external input.
        conn.execute(text(f"CREATE SEQUENCE {name}"))

        highest = conn.execute(text(seed_sql)).scalar() or minimum

        conn.execute(
            text("SELECT setval(CAST(:name AS regclass), :value)"),
            {"name": name, "value": max(int(highest), minimum)},
        )

    print(f"Sequence {name} ready — next number after {highest}")



@asynccontextmanager
async def lifespan(app: FastAPI):

    _init_database()
    worker.start()
    get_sync_scheduler().start()
    get_nightly_scheduler().start()

    yield

    get_nightly_scheduler().stop()
    get_sync_scheduler().stop()
    worker.stop()


app = FastAPI(
    title="Yexis Agent Platform",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def authorize_dashboard_sections(request: Request, call_next):
    """Apply database-backed view/edit/run grants to every HTMX section route."""
    path = request.url.path
    if not path.startswith("/htmx/"):
        return await call_next(request)

    if path.startswith("/htmx/roles"):
        from app.core.auth import get_current_user
        actor = get_current_user(request)
        if not actor or actor.role != "admin":
            return HTMLResponse("Admin only", status_code=403)
        return await call_next(request)

    section_routes = (
        ("/htmx/integrations", "integrations"),
        ("/htmx/approvals", "approvals"),
        ("/htmx/agents", "agents"),
        ("/htmx/campaigns", "campaigns"),
        ("/htmx/templates", "templates"),
        ("/htmx/optouts", "optouts"),
        ("/htmx/lanes", "lanes"),
        ("/htmx/tally", "tally"),
        ("/htmx/mail", "mail"),
        ("/htmx/users", "users"),
        ("/htmx/activity", "activity"),
        ("/htmx/tasks", "tasks"),
        ("/htmx/demo", "demo"),
        ("/htmx/overview", "overview"),
    )
    section = next(
        (name for prefix, name in section_routes if path.startswith(prefix)),
        None,
    )
    if section is None:
        return await call_next(request)

    from app.core.auth import get_current_user
    actor = get_current_user(request)
    if not actor:
        return HTMLResponse("Sign in required", status_code=401)

    method = request.method.upper()
    action = "view" if method in {"GET", "HEAD"} else "edit"
    if path.startswith("/htmx/demo/run/"):
        scenario_id = path.rsplit("/", 1)[-1]
        allowed = actor.can_run_demo(scenario_id)
    elif path.startswith("/htmx/demo/approve/") or path.startswith("/htmx/demo/reject/"):
        allowed = actor.can("approvals", "edit")
    else:
        allowed = actor.can(section, action)
    if not allowed:
        return HTMLResponse(
            f"Permission denied: {action} access to {section}.",
            status_code=403,
        )
    return await call_next(request)


app.include_router(health_router)
app.include_router(agents_router)
app.include_router(tasks_router)
app.include_router(approvals_router)
app.include_router(auth_router)
app.include_router(export_router)
app.include_router(sync_router)
app.include_router(workflows_router)
app.include_router(mail_router)
app.include_router(ops_console_router)

from app.api.lane import router as lanes_router  # noqa: E402
app.include_router(lanes_router)

from app.api.mcp_tally import router as mcp_tally_router  # noqa: E402
app.include_router(mcp_tally_router)

from app.api.mcp_local_db import router as mcp_local_db_router  # noqa: E402
app.include_router(mcp_local_db_router)

from app.api.mcp_cloud_db import router as mcp_cloud_db_router  # noqa: E402
app.include_router(mcp_cloud_db_router)

from app.api.mcp_whatsapp import router as mcp_whatsapp_router  # noqa: E402
app.include_router(mcp_whatsapp_router)

from app.api.users import router as users_router  # noqa: E402
app.include_router(users_router)

from app.api.campaigns import router as campaigns_router  # noqa: E402
app.include_router(campaigns_router)

from app.api.ism import router as ism_router  # noqa: E402
app.include_router(ism_router)

from app.api.mcp_whatsapp_web import router as whatsapp_web_router  # noqa: E402
app.include_router(whatsapp_web_router)

from app.api.mcp_rocketreach import router as mcp_rocketreach_router  # noqa: E402
app.include_router(mcp_rocketreach_router)

from app.api.mcp_zoho import router as mcp_zoho_router  # noqa: E402
app.include_router(mcp_zoho_router)

from app.api.f2_claims import router as f2_claims_router  # noqa: E402
app.include_router(f2_claims_router)

from app.api.f3_console import router as f3_console_router  # noqa: E402
app.include_router(f3_console_router)

from app.api.promotion import router as promotion_router  # noqa: E402
app.include_router(promotion_router)

from app.api.demo import router as demo_router  # noqa: E402
app.include_router(demo_router)

from app.api.templates import router as templates_router  # noqa: E402
app.include_router(templates_router)

from app.api.forms import router as forms_router  # noqa: E402
app.include_router(forms_router)

from app.api.auth_local import router as auth_local_router  # noqa: E402
app.include_router(auth_local_router)

# FastHTML control-room UI (replaces index.html + app.js)
from app.api.ui import router as ui_router  # noqa: E402
app.include_router(ui_router)

# Static assets (CSS, ui-helpers.js) — served from frontend/
if FRONTEND_DIR.exists():
    app.mount(
        "/assets",
        StaticFiles(directory=str(FRONTEND_DIR), html=False),
        name="frontend-assets",
    )
