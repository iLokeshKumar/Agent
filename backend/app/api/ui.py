"""FastHTML-powered control-room UI.

All routes here return HTMLResponse rendered via FastHTML FT components.
HTMX loads every data section lazily; the full-page route at GET /
is a lightweight shell with empty target divs that HTMX fills on load.

JSON API routes (FastAPI) remain unchanged at their existing paths.
"""
from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fasthtml.common import (  # pyright: ignore[reportMissingImports]
    Html, Head, Body, Title, Meta, Link, Style, Script,
    Div, Span, P, A, Button, H1, H2, H3, H4,
    Nav, Header, Main, Footer, Section, Article,
    Table, Thead, Tbody, Tr, Th, Td,
    Form as FtForm, Input, Select, Option, Textarea, Label,
    Code, Pre, Br, Strong, Ul, Li, Details, Summary,
    NotStr, to_xml,
)
from sqlalchemy.orm import Session

from app.core.auth import (
    COOKIE_NAME,
    CurrentUser, get_current_user, log_activity,
)
from app.core.db import get_db
from app.repository.agent_repository import AgentRepository
from app.repository.approval_repository import ApprovalRepository
from app.repository.lane_repository import LaneRepository
from app.repository.task_repository import TaskRepository
from app.ui.components import Badge, Notice, PanelHead, Pill, Spinner, fmt_dt
from app.ui.layout import Shell
from orchestrator.queue import task_queue

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ui"])

# ---------------------------------------------------------------------------
# Helper: render FT → HTMLResponse
# ---------------------------------------------------------------------------

def _html(component) -> HTMLResponse:
    return HTMLResponse(to_xml(component))


# ---------------------------------------------------------------------------
# Trigger string used by every auto-refreshing section
# ---------------------------------------------------------------------------
_AUTO = "load, ctrl-refresh from:body"
_LIST_PAGE_SIZE = 10


def _page_window(page: int, total: int, page_size: int = _LIST_PAGE_SIZE):
    page_count = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, page_count))
    return page, page_count, (page - 1) * page_size


def _page_controls(path: str, page: int, page_count: int, total: int,
                   target: str, include: str = ""):
    return Div(
        Button(
            "← Newer",
            cls="ghost",
            disabled=page <= 1,
            hx_get=f"{path}?page={max(1, page - 1)}",
            hx_target=target,
            hx_swap="innerHTML",
            hx_include=include or None,
        ),
        Span(f"Page {page} of {page_count} · {total} records", cls="muted"),
        Button(
            "Older →",
            cls="ghost",
            disabled=page >= page_count,
            hx_get=f"{path}?page={min(page_count, page + 1)}",
            hx_target=target,
            hx_swap="innerHTML",
            hx_include=include or None,
        ),
        cls="section-pagination",
    )


# ---------------------------------------------------------------------------
# MAIN PAGE
# ---------------------------------------------------------------------------

@router.get("/login", include_in_schema=False)
def login_page(request: Request) -> HTMLResponse:
    user = get_current_user(request)
    if user:
        return RedirectResponse("/", status_code=302)
    return _html(_login_page())


@router.get("/verify", include_in_schema=False)
def verify_page(request: Request, email: str = "") -> HTMLResponse:
    """Standalone page to verify an email via 6-digit code and set a password."""
    return _html(_verify_page(email))


@router.get("/", include_in_schema=False)
def home(request: Request) -> HTMLResponse:
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return _html(
        Shell(
            _demo_shell(user) if user.can("demo") else None,
            _overview_shell() if user.can("overview") else None,
            _quick_task_shell() if user.can("tasks") else None,
            _tasks_shell() if user.can("tasks") else None,
            _agents_shell() if user.can("agents") else None,
            _approvals_shell() if user.can("approvals") else None,
            _campaigns_shell() if user.can("campaigns") else None,
            _templates_shell() if user.can("templates") else None,
            _optouts_shell() if user.can("optouts") else None,
            _lanes_shell() if user.can("lanes") else None,
            _tally_shell() if user.can("tally") else None,
            _mail_shell() if user.can("mail") else None,
            _integrations_shell(user) if user.can("integrations") else None,
            _users_shell(user) if user.can("users") else None,
            _activity_shell() if user.can("activity") else None,
            current_user=user,
        )
    )


# ===========================================================================
# SECTION SHELLS  (static structure; HTMX fills the inner content)
# ===========================================================================

def _demo_shell(user: "CurrentUser | None" = None):
    uid = user.user_id if user else ""
    return Section(
        Div(
            Div(
                P("LIVE WORKFLOWS", cls="eyebrow"),
                H2("See your agents in action", cls="demo-heading"),
                P(
                    "Explore operational scenarios powered by your agent platform.",
                    cls="muted demo-intro",
                ),
                cls="demo-intro-copy",
            ),
            Span(
                "SCENARIO PLAYGROUND", id="demo-zoho-badge", cls="demo-live-badge",
            ),
            cls="demo-header",
        ),
        Div(
            id="demo-scenarios",
            cls="demo-scenarios",
            hx_get=f"/htmx/demo/scenarios?user_id={uid}",
            hx_trigger=_AUTO,
            hx_swap="innerHTML",
        ),
        Div(id="demo-output"),
        id="demo",
        cls="panel",
        style="grid-column:1/-1",
    )


def _overview_shell():
    return Section(
        Div(
            id="overview-content",
            hx_get="/htmx/overview",
            hx_trigger=_AUTO,
            hx_swap="innerHTML",
        ),
        id="overview",
        cls="panel hero",
    )


def _quick_task_shell():
    return Section(
        PanelHead("Quick task", "Create a new workflow task"),
        FtForm(
            Label(
                "Task type",
                Select(
                    Option("purchase_request", value="purchase_request"),
                    Option("grn_request", value="grn_request"),
                    Option("invoice_matching", value="invoice_matching"),
                    Option("books_to_tally", value="books_to_tally"),
                    Option("collections", value="collections"),
                    Option("ask_business", value="ask_business"),
                    Option("service_desk", value="service_desk"),
                    name="task_type",
                    required=True,
                ),
            ),
            Label("Priority", Input(name="priority", type="number", min="0", value="100")),
            Label("Source", Input(name="source", placeholder="manual")),
            Label(
                "Payload JSON",
                Textarea('{"description":"Task"}', name="payload", rows="7"),
                cls="span-2",
            ),
            Div(
                Button("Queue task", cls="primary", type="submit"),
                Div(id="task-form-result"),
                cls="span-2 actions-row",
            ),
            id="task-form",
            cls="form-grid",
            hx_post="/htmx/tasks",
            hx_target="#task-form-result",
            hx_swap="innerHTML",
        ),
    )


def _tasks_shell():
    return Section(
        PanelHead(
            "Recent tasks",
            tools=Div(
                Select(
                    Option("All statuses", value=""),
                    Option("QUEUED", value="QUEUED"),
                    Option("RUNNING", value="RUNNING"),
                    Option("SUCCESS", value="SUCCESS"),
                    Option("FAILED", value="FAILED"),
                    Option("AWAITING_APPROVAL", value="AWAITING_APPROVAL"),
                    Option("REJECTED", value="REJECTED"),
                    Option("VALIDATION_ERROR", value="VALIDATION_ERROR"),
                    id="tasks-filter",
                    name="status",
                    hx_get="/htmx/tasks",
                    hx_target="#tasks-list",
                    hx_trigger="change",
                    hx_include="this",
                ),
                Button(
                    "Reload", cls="ghost",
                    hx_get="/htmx/tasks",
                    hx_target="#tasks-list",
                    hx_include="#tasks-filter",
                ),
                cls="panel-tools",
            ),
        ),
        Div(
            Spinner(),
            id="tasks-list",
            cls="table-wrap",
            hx_get="/htmx/tasks",
            hx_trigger=_AUTO,
            hx_swap="innerHTML",
            hx_include="#tasks-filter",
        ),
        id="tasks",
        cls="panel span-2",
    )


def _agents_shell():
    return Section(
        PanelHead(
            "Agents",
            "Ready means enabled and registered; it does not check external integrations. "
            "Activity counts are recorded ledger actions.",
        ),
        Div(
            Spinner(),
            id="agents-list",
            cls="stack",
            hx_get="/htmx/agents",
            hx_trigger=_AUTO,
            hx_swap="innerHTML",
        ),
        id="agents",
        cls="panel",
    )


def _approvals_shell():
    return Section(
        PanelHead("Approvals", "Human gate"),
        Div(
            Spinner(),
            id="approvals-list",
            cls="stack",
            hx_get="/htmx/approvals",
            hx_trigger=_AUTO,
            hx_swap="innerHTML",
        ),
        id="approvals",
        cls="panel",
    )


def _lanes_shell():
    return Section(
        PanelHead("Lanes", "Autonomy, dry-run, kill-switch"),
        Div(
            Spinner(),
            id="lanes-list",
            cls="lane-grid",
            hx_get="/htmx/lanes",
            hx_trigger=_AUTO,
            hx_swap="innerHTML",
        ),
        id="lanes",
        cls="panel span-2",
    )


def _tally_shell():
    return Section(
        PanelHead("Tally", "Stage, approve, post, trial balance"),
        Div(
            _tally_stage_form(),
            Div(
                Div(
                    Button(
                        "Reload staged rows", cls="ghost",
                        hx_get="/htmx/tally/staged",
                        hx_target="#tally-list",
                        hx_include="#tally-entity",
                    ),
                    Button(
                        "Post approved", cls="primary",
                        hx_post="/htmx/tally/post",
                        hx_target="#tally-status",
                        hx_include="#tally-entity",
                    ),
                    cls="action-row",
                ),
                Div(
                    Label(
                        "Entity",
                        Select(
                            Option("YEXIS-EL (Kerala)", value="YEXIS-EL"),
                            Option("YEXIS-SL (Tamil Nadu)", value="YEXIS-SL"),
                            Option("YEXIS-SOL (Solutions)", value="YEXIS-SOL"),
                            id="tally-entity",
                            name="entity",
                        ),
                    ),
                    Button(
                        "Fetch trial balance", cls="ghost",
                        hx_get="/htmx/tally/trial-balance",
                        hx_target="#tally-list",
                        hx_include="#tally-entity",
                    ),
                    cls="action-row",
                ),
                Div("No data loaded yet.", id="tally-status", cls="notice"),
                Div(id="tally-list", cls="table-wrap"),
                cls="stack",
            ),
            cls="split",
        ),
        id="tally",
        cls="panel span-2",
    )


def _tally_stage_form():
    return FtForm(
        Label(
            "Entity",
            Select(
                Option("YEXIS-EL (Kerala)", value="YEXIS-EL"),
                Option("YEXIS-SL (Tamil Nadu)", value="YEXIS-SL"),
                Option("YEXIS-SOL (Solutions)", value="YEXIS-SOL"),
                name="entity",
                id="tally-stage-entity",
            ),
        ),
        Label("Books transaction ID", Input(name="books_txn_id", placeholder="Enter an actual transaction ID", required=True)),
        Label(
            "Company",
            Select(
                Option("YEXIS ELECTRONICS - K (Kerala)", value="YEXIS ELECTRONICS - K"),
                Option("YEXIS ELECTRONICS - T (Tamil Nadu)", value="YEXIS ELECTRONICS - T"),
                Option("Yexis Solutions", value="Yexis Solutions"),
                name="company",
                id="tally-stage-company",
            ),
        ),
        Label(
            "Entries JSON",
            Textarea(
                "[]",
                name="entries",
                rows="10",
                required=True,
            ),
            cls="span-2",
        ),
        Div(
            Button("Stage voucher", cls="primary", type="submit"),
            Div(id="tally-stage-status"),
            cls="span-2 actions-row",
        ),
        id="tally-stage-form",
        cls="form-grid",
        hx_post="/htmx/tally/stage",
        hx_target="#tally-stage-status",
        hx_swap="innerHTML",
    )


def _mail_shell():
    return Section(
        PanelHead("Mail", "POP3 intake and approval-gated drafts"),
        Div(
            Spinner(),
            id="mail-health",
            cls="notice",
            style="margin-bottom:12px",
            hx_get="/htmx/mail/health",
            hx_trigger=_AUTO,
            hx_swap="outerHTML",
        ),
        Div(
            Button(
                "Poll POP3 inbox", cls="primary",
                hx_post="/htmx/mail/poll",
                hx_target="#mail-health",
            ),
            Button(
                "Reload", cls="ghost",
                hx_get="/htmx/mail/health",
                hx_target="#mail-health",
            ),
            cls="action-row",
            style="margin-bottom:16px",
        ),
        Div(
            Div(
                Div(
                    H4("Inbox", Span(" (received)", cls="muted",
                                     style="font-weight:normal;font-size:13px"),
                       style="margin:0"),
                    Input(
                        id="inbox-search",
                        name="q",
                        type="search",
                        placeholder="Search sender, subject, or message…",
                        aria_label="Search inbox",
                        autocomplete="off",
                        hx_get="/htmx/mail/inbox",
                        hx_target="#inbox-list",
                        hx_trigger="input changed delay:350ms, search",
                        hx_include="this",
                    ),
                    Button(
                        "Refresh inbox",
                        cls="ghost",
                        hx_get="/htmx/mail/inbox",
                        hx_target="#inbox-list",
                        hx_include="#inbox-search",
                    ),
                    cls="mail-inbox-toolbar",
                ),
                Div(
                    Spinner(),
                    id="inbox-list",
                    cls="stack",
                    hx_get="/htmx/mail/inbox",
                    hx_trigger=f"{_AUTO}, mail-inbox-refresh from:body",
                    hx_include="#inbox-search",
                    hx_swap="innerHTML",
                ),
                cls="stack",
            ),
            Div(
                H4("Outbound drafts", style="margin:0 0 8px"),
                _draft_form(),
                Div(id="drafts-list", cls="stack", style="margin-top:12px",
                    hx_get="/htmx/mail/drafts",
                    hx_trigger=_AUTO,
                    hx_swap="innerHTML"),
                cls="stack",
            ),
            cls="split",
        ),
        id="mail",
        cls="panel span-2",
    )


def _draft_form():
    return FtForm(
        Label("Recipient", Input(name="recipient", placeholder="ops@example.com")),
        Label("Subject", Input(name="subject", placeholder="Draft subject")),
        Label("Body", Textarea("Hello from Yexis.", name="body", rows="5"), cls="span-2"),
        Label(
            "Attachments ",
            Span("(optional — Excel, PDF, Word, images)", cls="muted"),
            Input(type="file", id="draft-files", multiple=True,
                  accept=".xlsx,.xls,.pdf,.doc,.docx,.csv,.png,.jpg,.jpeg,.txt",
                  style="margin-top:4px"),
            cls="span-2",
        ),
        Div(
            Button("Create draft", cls="primary", type="submit"),
            Div(id="draft-form-status"),
            cls="span-2 actions-row",
        ),
        id="draft-form",
        cls="form-grid",
        hx_post="/htmx/mail/draft",
        hx_target="#draft-form-status",
        hx_swap="innerHTML",
    )


# ---------------------------------------------------------------------------
# Campaigns: the list and workflow are driven by the Yexis campaign API.
# ---------------------------------------------------------------------------

def _campaigns_shell():
    return Section(
        Style("""
        #campaigns .cmp-toolbar{display:flex;align-items:center;gap:.55rem;flex-wrap:wrap}
        /* Campaign list rows styled as cards with a hover lift and a status accent. */
        #campaigns #cmp-list-wrap{display:flex;flex-direction:column;gap:.7rem;margin-top:.4rem}
        #campaigns .cmp-list-row{display:grid;grid-template-columns:minmax(180px,2fr) repeat(3,minmax(90px,1fr)) auto;gap:.7rem 1rem;align-items:center;
          padding:.95rem 1.1rem;border:1px solid var(--panel-border);border-radius:12px;background:rgba(255,255,255,.025);
          font-size:.84rem;transition:border-color .15s,background .15s,transform .06s}
        #campaigns .cmp-list-row[style*='font-weight:700']{border:none;background:none;padding:.2rem 1.1rem;transition:none}
        #campaigns .cmp-list-row:not([aria-hidden]):hover{border-color:rgba(119,209,255,.45);background:rgba(119,209,255,.05);transform:translateY(-1px)}
        #campaigns .cmp-actions{display:flex;gap:.35rem;flex-wrap:wrap;justify-content:flex-end}
        #campaigns .cmp-btn{padding:.42rem .75rem;font-size:.78rem;border-radius:8px;cursor:pointer;border:1px solid var(--panel-border);
          background:rgba(255,255,255,.05);color:var(--text);transition:border-color .12s,background .12s}
        #campaigns .cmp-btn:hover{border-color:rgba(255,255,255,.28);background:rgba(255,255,255,.09)}
        #campaigns .cmp-btn.primary{background:rgba(119,209,255,.15);border-color:var(--accent);color:var(--accent)}
        #campaigns .cmp-btn.success{background:rgba(117,231,160,.12);border-color:var(--ok);color:var(--ok)}
        #campaigns .cmp-btn.warn{background:rgba(255,189,89,.12);border-color:var(--warn);color:var(--warn)}
        #campaigns .cmp-btn.danger{color:#f87171;border-color:#f8717155}
        #campaigns .cmp-status{display:inline-flex;align-items:center;gap:.35rem;padding:.22rem .65rem;border-radius:999px;background:rgba(255,255,255,.06);font-size:.72rem;font-weight:600}
        #campaigns .cmp-status::before{content:"";width:.5rem;height:.5rem;border-radius:50%;background:currentColor}
        #campaigns .status-draft{color:var(--muted)}#campaigns .status-audience_built{color:var(--warn)}
        #campaigns .status-broadcast{color:var(--accent)}#campaigns .status-tracking{color:var(--accent-2)}#campaigns .status-closed{color:var(--ok)}
        #campaigns .cmp-detail{margin-top:1.1rem;padding:1.35rem;border:1px solid var(--panel-border);border-radius:16px;
          background:linear-gradient(180deg,rgba(119,209,255,.04),rgba(255,255,255,.02))}
        #campaigns .cmp-detail h4{margin:1.1rem 0 .5rem;font-size:.9rem;padding-bottom:.35rem;border-bottom:1px solid var(--panel-border)}
        #campaigns .cmp-detail-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.6rem;margin:.85rem 0}
        #campaigns .cmp-detail-grid .cmp-msg{margin:0;background:rgba(255,255,255,.03);border:1px solid var(--panel-border)}
        #campaigns .cmp-detail-grid small{color:var(--muted);font-size:.66rem;text-transform:uppercase;letter-spacing:.04em}
        #campaigns .cmp-control{display:grid;gap:.35rem;margin:.7rem 0;font-size:.8rem}
        #campaigns .cmp-modal{width:min(720px,calc(100vw - 2rem));max-width:calc(100vw - 2rem);max-height:min(88vh,820px);overflow:auto;background:var(--bg-2);border:1px solid var(--panel-border);border-radius:18px;padding:clamp(1rem,2.2vw,1.75rem);box-shadow:var(--shadow)}
        .cmp-overlay.open{display:flex!important}
        #campaigns .cmp-modal-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.85rem}
        #campaigns .cmp-modal-grid label{display:grid;gap:.3rem;font-size:.78rem;color:var(--muted)}
        #campaigns .cmp-modal-grid input,#campaigns .cmp-modal-grid select{background:rgba(255,255,255,.05);border:1px solid var(--panel-border);border-radius:8px;padding:.5rem .6rem;color:var(--text);font-size:.85rem}
        #campaigns .cmp-msg{margin:.7rem 0;padding:.7rem .9rem;border-radius:10px;background:rgba(255,255,255,.05);font-size:.82rem;white-space:pre-wrap}
        @media(max-width:760px){#campaigns .cmp-list-row{grid-template-columns:1fr auto}#campaigns .cmp-list-row>.cmp-mobile-hide{display:none}#campaigns .cmp-actions{justify-content:flex-start}}
        """),
        Div(
            Div(
                P("Marketing Suite", cls="eyebrow", style="color:#a78bfa"),
                H2("Campaigns", style="margin:0"),
                P("Create, build audience, broadcast and track — guided step by step.",
                  cls="muted", style="margin-top:.2rem;font-size:.85rem"),
            ),
            Div(
                Select(
                    Option("YEXIS ELECTRONICS — KERALA", value="YEXIS-EL"),
                    Option("YEXIS ELECTRONICS — TAMIL NADU", value="YEXIS-SL"),
                    Option("YEXIS SOLUTIONS", value="YEXIS-SOL"),
                    id="cmp-entity-filter",
                    name="entity",
                    style="background:rgba(255,255,255,.05);border:1px solid var(--panel-border);border-radius:6px;padding:.35rem .6rem;color:var(--text);font-size:.83rem",
                ),
                Button(
                    "Reload", cls="ghost", id="cmp-reload", style="font-size:.82rem",
                ),
                Button(
                    "Check deliverability", cls="ghost", id="cmp-deliverability",
                    style="font-size:.82rem",
                    title="Check SPF/DKIM/DMARC for your sending domain before you send",
                ),
                Button("+ New Campaign", cls="primary", id="cmp-new", style="font-size:.85rem"),
                cls="cmp-toolbar",
            ),
            style="display:flex;align-items:center;gap:1rem;margin-bottom:1.25rem",
        ),
        Div(id="cmp-deliverability-panel", style="display:none;margin-bottom:1rem"),
        Div(P("Loading campaigns…", cls="muted"), id="cmp-list-wrap"),
        Div(id="cmp-detail", style="display:none"),
        _campaigns_wizard_modal(),
        Script(src="/assets/campaigns.js?v=13"),
        id="campaigns",
        cls="panel span-2",
    )


def _campaigns_wizard_modal():
    """Create/edit campaign form. Workflow actions live in campaign detail."""
    return Div(
        Div(
            Div(
                H3("New Campaign", id="cmp-form-title", style="margin:0;flex:1"),
                Button("✕", id="cmp-wizard-close", type="button",
                       style="background:none;border:none;color:var(--muted);font-size:1.2rem;cursor:pointer"),
                style="display:flex;align-items:center;gap:.5rem;margin-bottom:1rem",
            ),
            P("Create a Yexis campaign brief. You can build its audience and send or schedule it from the campaign details.",
              cls="muted", style="font-size:.84rem"),
            Div(
                Label("Campaign name", Input(id="cmp-f-name", maxlength="200", required=True,
                                                placeholder="e.g. Tamil Nadu festive offer")),
                Label("Campaign type", Select(
                    Option("Dealer promotion", value="dealer_promo"),
                    Option("End customer", value="end_customer"),
                    Option("Scheme push", value="scheme_push"),
                    Option("Event", value="event"), id="cmp-f-type")),
                Label("Entity", Select(
                    Option("Yexis Electronics — Kerala", value="YEXIS-EL"),
                    Option("Yexis Electronics — Tamil Nadu", value="YEXIS-SL"),
                    Option("Yexis Solutions", value="YEXIS-SOL"), id="cmp-f-entity")),
                Label("Default channel", Select(
                    Option("Automatic", value="auto"), Option("Email", value="email"),
                    Option("WhatsApp", value="whatsapp"), Option("Telegram", value="telegram"),
                    Option("Voice", value="voice"), Option("Push", value="push"),
                    Option("LinkedIn", value="linkedin"), id="cmp-f-channel")),
                Label("Start date", Input(id="cmp-f-start", type="date")),
                Label("End date", Input(id="cmp-f-end", type="date")),
                Label("Budget (₹)", Input(id="cmp-f-budget", type="number", min="0", step="0.01")),
                Label("Message template", Select(Option("Choose a template later", value=""), id="cmp-f-template")),
                Label("Target SKUs (comma separated)", Input(id="cmp-f-skus", placeholder="SKU-001, SKU-002")),
                cls="cmp-modal-grid",
            ),
            Div(id="cmp-form-message", cls="cmp-msg", style="display:none"),
            Div(
                Button("Cancel", cls="ghost", id="cmp-form-cancel", type="button"),
                Button("Save campaign", cls="primary", id="cmp-form-save", type="button"),
                style="display:flex;justify-content:flex-end;gap:.5rem;margin-top:1rem",
            ),
            cls="cmp-modal",
        ),
        id="cmp-wizard",
        cls="cmp-overlay",
        style="display:none;position:fixed;inset:0;padding:clamp(12px,2vw,24px);background:rgba(0,0,0,.72);z-index:10000;overflow-y:auto;align-items:center;justify-content:center",
    )


def _templates_shell():
    return Section(
        Style("""
        .tmpl-table{width:100%;border-collapse:collapse;font-size:.83rem}
        .tmpl-table th{text-align:left;padding:.5rem .75rem;font-size:.72rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;border-bottom:1px solid var(--panel-border)}
        .tmpl-table td{padding:.6rem .75rem;border-bottom:1px solid rgba(255,255,255,.05);vertical-align:middle}
        .tmpl-table tr:hover td{background:rgba(255,255,255,.025)}
        .tmpl-badge{display:inline-block;padding:.18rem .55rem;border-radius:4px;font-size:.72rem;font-weight:600}
        .tmpl-badge.pending{background:rgba(255,189,89,.12);color:var(--warn);border:1px solid rgba(255,189,89,.3)}
        .tmpl-badge.internal{background:rgba(119,209,255,.1);color:var(--accent);border:1px solid rgba(119,209,255,.3)}
        .tmpl-badge.meta{background:rgba(52,211,153,.1);color:#34d399;border:1px solid rgba(52,211,153,.3)}
        .tmpl-badge.dlt{background:rgba(167,139,250,.1);color:#a78bfa;border:1px solid rgba(167,139,250,.3)}
        .tmpl-badge.wa{background:rgba(52,211,153,.1);color:#34d399;border:1px solid rgba(52,211,153,.25)}
        .tmpl-badge.email{background:rgba(119,209,255,.1);color:var(--accent);border:1px solid rgba(119,209,255,.25)}
        """),
        Div(
            Div(
                P("Marketing Suite", cls="eyebrow", style="color:#34d399"),
                H2("Message Templates", style="margin:0"),
                P("Templates for email, WhatsApp, and other channels.",
                  cls="muted", style="margin-top:.2rem;font-size:.85rem"),
            ),
            Div(
                Select(
                    Option("All channels", value=""),
                    Option("Email", value="email"),
                    Option("WhatsApp", value="wa"),
                    Option("Telegram", value="telegram"),
                    Option("Push", value="push"),
                    id="tmpl-ch-filter",
                    name="channel",
                    style="background:rgba(255,255,255,.05);border:1px solid var(--panel-border);border-radius:6px;padding:.35rem .6rem;color:var(--text);font-size:.83rem",
                    hx_get="/htmx/templates",
                    hx_target="#tmpl-list-wrap",
                    hx_trigger="change",
                    hx_include="#tmpl-ch-filter,#tmpl-st-filter",
                ),
                Select(
                    Option("All statuses", value=""),
                    Option("Pending", value="pending"),
                    Option("Internal", value="internal"),
                    Option("Meta-approved", value="meta"),
                    Option("DLT-approved", value="dlt"),
                    id="tmpl-st-filter",
                    name="status",
                    style="background:rgba(255,255,255,.05);border:1px solid var(--panel-border);border-radius:6px;padding:.35rem .6rem;color:var(--text);font-size:.83rem",
                    hx_get="/htmx/templates",
                    hx_target="#tmpl-list-wrap",
                    hx_trigger="change",
                    hx_include="#tmpl-ch-filter,#tmpl-st-filter",
                ),
                Button(
                    "Reload", cls="ghost", style="font-size:.82rem",
                    hx_get="/htmx/templates",
                    hx_target="#tmpl-list-wrap",
                    hx_include="#tmpl-ch-filter,#tmpl-st-filter",
                ),
                Button("+ New Template", cls="primary", id="tmpl-new",
                       style="font-size:.85rem;background:rgba(52,211,153,.15);border-color:#34d399;color:#34d399",
                       hx_get="/htmx/templates/form",
                       hx_target="#tmpl-form-host",
                       hx_swap="innerHTML",
                       onclick="document.getElementById('tmpl-modal').classList.add('open')"),
                style="margin-left:auto;display:flex;gap:.5rem;align-items:center",
            ),
            style="display:flex;align-items:center;gap:1rem;margin-bottom:1.25rem",
        ),
        Div(
            Spinner(),
            id="tmpl-list-wrap",
            hx_get="/htmx/templates",
            hx_trigger=_AUTO,
            hx_swap="innerHTML",
        ),
        Style("""
        /* The template modal toggles visibility via the 'open' class; without
           this rule the overlay stays display:none forever (the campaign
           wizard has its own .cmp-overlay.open rule, this one was missing). */
        .tmpl-overlay.open{display:flex!important}
        """),
        _template_modal(),
        id="templates",
        cls="panel span-2",
    )


def _template_modal():
    return Div(
        Div(
            Div(
                Button("✕", onclick="document.getElementById('tmpl-modal').classList.remove('open')",
                       style="margin-left:auto;background:none;border:none;color:var(--muted);"
                             "font-size:1.2rem;cursor:pointer"),
                style="display:flex;margin-bottom:.25rem",
            ),
            # Swappable form host — create/edit/preview fragments load here via HTMX.
            Div(_template_form(), id="tmpl-form-host"),
            style="background:var(--bg-2);border:1px solid var(--panel-border);border-radius:18px;"
                  "width:min(720px,calc(100vw - 2rem));max-width:calc(100vw - 2rem);max-height:min(88vh,820px);overflow-y:auto;padding:clamp(1rem,2.2vw,1.75rem);box-shadow:var(--shadow)",
        ),
        id="tmpl-modal",
        cls="tmpl-overlay",
        style="display:none;position:fixed;inset:0;padding:clamp(12px,2vw,24px);background:rgba(0,0,0,.72);z-index:210;overflow-y:auto;align-items:center;justify-content:center",
    )


def _template_form(t=None):
    """Compose form for a template — create mode (t is None) or edit mode.

    In edit mode the Template ID is read-only (it's the primary key), fields
    are pre-filled, the body HTML pre-loads into TinyMCE, existing attachments
    are listed, and the form PUT-updates via /htmx/templates/{id}.
    """
    editing = t is not None
    tid = t.template_id if editing else ""
    ch = t.channel if editing else "email"
    # New email templates default to "internal" (email-ready) so they can be
    # sent immediately — a fresh template left at "pending" is silently
    # rejected by K3 at send time (see k3_broadcast approval_status check).
    status = t.approval_status if editing else "internal"
    subject = (t.subject or "") if editing else ""
    reply = (t.reply_to or "") if editing else ""
    purpose = (t.purpose or "") if editing else ""
    body = (t.body or "") if editing else ""
    post_url = f"/htmx/templates/{tid}" if editing else "/htmx/templates"

    def _opt(label, val, current):
        return Option(label, value=val, selected=(val == current))

    return FtForm(
        H3("Edit Template" if editing else "New Template", style="margin:0 0 1rem"),
        Div(
            Div(
                Label("Template ID *"),
                Input(id="tmpl-f-id", name="template_id", value=tid,
                      placeholder="tmpl_diwali_email", readonly=editing),
                cls="cmp-field",
            ),
            Div(
                Label("Channel *"),
                Select(
                    _opt("Email", "email", ch), _opt("WhatsApp", "wa", ch),
                    _opt("Telegram", "telegram", ch), _opt("Push", "push", ch),
                    _opt("Voice", "voice", ch), _opt("LinkedIn", "linkedin", ch),
                    id="tmpl-f-channel", name="channel",
                ),
                cls="cmp-field",
            ),
            style="display:grid;grid-template-columns:1fr 1fr;gap:.75rem",
        ),
        Div(
            Label("Purpose"),
            Input(id="tmpl-f-purpose", name="purpose", value=purpose,
                  placeholder="e.g. Festival promo for dealers"),
            cls="cmp-field",
            style="margin-top:.5rem",
        ),
        Div(
            Div(
                Label("Subject — the email subject line ({{ name }}, {{ campaign_name }})"),
                Input(id="tmpl-f-subject", name="subject", value=subject,
                      placeholder="{{ campaign_name }} — Special Offer for {{ name }}!"),
                P("Recommended for email — this is what recipients see in their inbox.",
                  style="font-size:.72rem;color:var(--muted);margin:.25rem 0 0"),
                cls="cmp-field",
            ),
            Div(
                Label("Reply-To (where replies go — optional)"),
                Input(id="tmpl-f-reply-to", name="reply_to", type="email", value=reply,
                      placeholder="sales@adomita.com"),
                cls="cmp-field",
            ),
            style="display:grid;grid-template-columns:1fr 1fr;gap:.75rem;margin-top:.5rem",
        ),
        Div(
            Div(
                Label("Body", style="flex:1"),
                Select(
                    Option("Insert field…", value="", selected=True),
                    Option("Name", value="{{ name }}"),
                    Option("First name", value="{{ first_name }}"),
                    Option("Last name", value="{{ last_name }}"),
                    Option("Salutation (Mr./Ms./Dr.)", value="{{ salutation }}"),
                    Option("Company / Account name", value="{{ company }}"),
                    Option("Title", value="{{ title }}"),
                    Option("Role", value="{{ role }}"),
                    Option("Seniority", value="{{ seniority }}"),
                    Option("Department", value="{{ department }}"),
                    Option("LinkedIn URL", value="{{ linkedin }}"),
                    Option("Campaign name", value="{{ campaign_name }}"),
                    Option("Contact email", value="{{ contact_email }}"),
                    Option("Outstanding amount", value="{{ books_outstanding_receivable | int }}"),
                    id="tmpl-f-insert",
                    onchange=(
                        "if(this.value){var ed=window.tinymce&&tinymce.get('tmpl-f-body');"
                        "if(ed){ed.insertContent(this.value);}else{var ta=document.getElementById('tmpl-f-body');ta.value+=this.value;}"
                        "this.value='';}"
                    ),
                    style="font-size:.75rem;padding:.2rem .4rem",
                ),
                style="display:flex;align-items:center;gap:.5rem",
            ),
            Textarea(
                body,
                id="tmpl-f-body",
                name="body",
                rows="10",
                placeholder="Hi {{ name }}, ...",
            ),
            P("Rich text is sent as HTML. Personalization tokens like {{ name }} are "
              "filled per-recipient at send time — use the “Insert field” menu to add them safely.",
              style="font-size:.72rem;color:var(--muted);margin:.3rem 0 0"),
            cls="cmp-field",
            style="margin-top:.5rem",
        ),
        Div(
            Button("✓ Check content", cls="cmp-btn", type="button",
                   hx_post="/htmx/templates/check",
                   hx_include="#tmpl-f-id,#tmpl-f-channel,#tmpl-f-subject,#tmpl-f-body",
                   hx_target="#tmpl-check-results", hx_swap="innerHTML",
                   title="Check spelling, merge fields, links and attachments before sending"),
            P("Catches typos, broken/misspelt merge fields, missing attachments and "
              "(when a model provider is configured) spelling & grammar — before any customer sees it.",
              cls="muted", style="font-size:.72rem;margin:.3rem 0 0"),
            Div(id="tmpl-check-results", style="margin-top:.5rem"),
            cls="cmp-field",
            style="margin-top:.5rem",
        ),
        Div(
            Label("Attachments (images, PDF, slides — sent with every email)"),
            Input(type="file", id="tmpl-f-attach", name="file",
                  hx_post="/htmx/templates/attach",
                  hx_encoding="multipart/form-data",
                  hx_trigger="change",
                  hx_include="[name='template_id']",
                  hx_target="#tmpl-attach-list",
                  hx_swap="innerHTML"),
            P("Attach files below — sent with every email." if editing
              else "Save the template first (button below), then attach files here.",
              cls="muted", style="font-size:.72rem;margin:.3rem 0 0"),
            Div(_tmpl_attach_items(t) if editing else None,
                id="tmpl-attach-list", cls="stack", style="margin-top:.5rem"),
            cls="cmp-field",
            style="margin-top:.5rem",
        ),
        Script(
            "(function(){function go(){if(!window.tinymce){return setTimeout(go,150);}"
            "var old=tinymce.get('tmpl-f-body');if(old){old.remove();}"
            "tinymce.init({selector:'#tmpl-f-body',license_key:'gpl',height:340,menubar:false,"
            # quickbars gives a floating toolbar when you click an image — its
            # link button keeps the image selected so the link actually applies.
            "plugins:'lists link image code autolink quickbars',"
            "toolbar:'undo redo | blocks | bold italic underline forecolor backcolor | "
            "alignleft aligncenter alignright | bullist numlist | link image | code',"
            # Floating toolbar shown on image click — 'link' here applies the link
            # without the image losing selection (which happens with the main toolbar).
            "quickbars_image_toolbar:'link | rotateleft rotateright | imageoptions',"
            "quickbars_selection_toolbar:false,"
            "skin:'oxide-dark',content_css:'dark',branding:false,"
            # Keep inserted/pasted images as base64 data URIs so they persist on
            # save; the send path converts them to inline cid: images (which
            # Gmail/Outlook render, unlike base64 <img> sources).
            "paste_data_images:true,automatic_uploads:false,file_picker_types:'image',"
            # Allow the Browse button in the image dialog to pick a local file and
            # embed it as a base64 data URI — without this callback the Browse
            # button is present but does nothing, so users could only enter URLs
            # (which email clients block).
            "file_picker_callback:function(cb,val,meta){"
            "var inp=document.createElement('input');inp.type='file';inp.accept='image/*';"
            "inp.onchange=function(){"
            "var f=this.files[0];var r=new FileReader();"
            "r.onload=function(){cb(r.result,{title:f.name});};r.readAsDataURL(f);};"
            "inp.click();},"
            "setup:function(ed){ed.on('change input keyup',function(){ed.save();});}});}"
            "go();})();"
        ),
        Div(
            Label("Approval Status *"),
            Select(
                _opt("Internal (email-ready)", "internal", status),
                _opt("Pending (not sendable yet)", "pending", status),
                _opt("Meta-approved (WhatsApp)", "meta", status),
                _opt("DLT-approved (SMS)", "dlt", status),
                id="tmpl-f-status", name="approval_status",
            ),
            P("Email sends only when status is Internal — a Pending template is "
              "rejected at send time. WhatsApp needs Meta-approved.",
              style="font-size:.72rem;color:var(--muted);margin:.3rem 0 0"),
            cls="cmp-field",
            style="margin-top:.5rem",
        ),
        Div(id="tmpl-modal-notice", style="margin:.5rem 0"),
        Div(
            Button("Cancel", cls="ghost",
                   type="button",
                   onclick="document.getElementById('tmpl-modal').classList.remove('open')"),
            Button("Save changes" if editing else "Save Template", cls="primary", type="submit"),
            style="display:flex;justify-content:flex-end;gap:.5rem;margin-top:.75rem",
        ),
        hx_post=post_url,
        hx_target="#tmpl-modal-notice",
        hx_swap="innerHTML",
        style="padding:0",
    )


def _optouts_shell():
    return Section(
        Style("""
        .optout-table{width:100%;border-collapse:collapse;font-size:.83rem}
        .optout-table th{text-align:left;padding:.5rem .75rem;font-size:.72rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;border-bottom:1px solid var(--panel-border)}
        .optout-table td{padding:.6rem .75rem;border-bottom:1px solid rgba(255,255,255,.05);vertical-align:middle}
        """),
        Div(
            Div(
                P("Campaign Compliance", cls="eyebrow", style="color:#f87171"),
                H2("Opt-out List", style="margin:0"),
                P("Contacts who clicked Unsubscribe. Auto-skipped on every future campaign send.",
                  cls="muted", style="margin-top:.2rem;font-size:.85rem"),
            ),
            Div(
                Button(
                    "Reload", cls="ghost", style="font-size:.82rem",
                    hx_get="/htmx/optouts",
                    hx_target="#optout-list-wrap",
                ),
                style="margin-left:auto",
            ),
            style="display:flex;align-items:center;gap:1rem;margin-bottom:1.25rem",
        ),
        Div(
            Spinner(),
            id="optout-list-wrap",
            hx_get="/htmx/optouts",
            hx_trigger=_AUTO,
            hx_swap="innerHTML",
        ),
        id="optouts",
        cls="panel span-2",
    )


# ===========================================================================
# HTMX PARTIAL ROUTES  (return HTML fragments, no full page shell)
# ===========================================================================

# ---------------------------------------------------------------------------
# Demo scenarios
# ---------------------------------------------------------------------------

@router.get("/htmx/demo/scenarios", include_in_schema=False)
def htmx_demo_scenarios(request: Request):
    """Render scenario cards filtered by the caller's role."""
    from app.api.demo import SCENARIOS
    user = get_current_user(request)
    if not user or not user.can("demo"):
        return _html(Notice("Access denied", "err"))
    visible = SCENARIOS
    cards = [
        Div(
            Div(
                Div(s.get("icon", ""), cls="demo-card-icon"),
                Span(s.get("agent_code", "AGENT"), cls="demo-card-code"),
                cls="demo-card-top",
            ),
            Div(
                P(s.get("name", ""), cls="demo-card-name"),
                P(s.get("subtitle", ""), cls="demo-card-sub"),
                cls="demo-card-title",
            ),
            P(s.get("description", ""), cls="demo-card-desc"),
            Span(s.get("audience", "Operations"), cls="demo-card-audience"),
            Button(
                s.get("run_label", "Run scenario"),
                cls="demo-run-btn is-unavailable" if not s.get("run_enabled", True) else "demo-run-btn",
                hx_post=f"/htmx/demo/run/{s.get('id','')}",
                hx_target="#demo-output",
                hx_swap="innerHTML",
                disabled=(
                    not s.get("run_enabled", True)
                    or not user.can_run_demo(s.get("id", ""))
                ),
            ),
            cls="demo-card",
            style=f"--scenario-accent:{s.get('color','#2563eb')}",
        )
        for s in visible
    ]
    return _html(Div(*cards))


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

@router.get("/htmx/overview", include_in_schema=False)
def htmx_overview(db: Session = Depends(get_db)):
    from app.repository.agent_repository import AgentRepository as AR
    from app.repository.task_repository import TaskRepository as TR

    agents = AR(db).list_enabled()
    tasks = TR(db).list_tasks(limit=50)
    pending_approvals = ApprovalRepository(db).list_pending()

    healthy = sum(1 for a in agents if a.enabled)
    total_agents = len(agents)
    task_count = len(tasks)
    approval_count = len(pending_approvals)

    recent_tasks = tasks[:6]

    content = [
        Div(
            P("Live status", cls="eyebrow"),
            H2(f"ok backend, {healthy}/{total_agents} agents healthy",
               id="status-title"),
            P(f"{task_count} tasks · {approval_count} pending approvals",
              cls="muted", id="status-copy"),
            Div(
                Span(f"Backend: ok", cls="pill"),
                Span(f"Agents: {healthy}/{total_agents}", cls="pill"),
                Span(f"Tasks: {task_count}", cls="pill"),
                Span(f"Approvals: {approval_count}", cls="pill"),
                id="hero-strip",
                cls="hero-strip",
            ),
            cls="hero-copy",
        ),
        Div(
            Div(
                *[Div(
                    Span(label, cls="label"),
                    Span(str(val), cls="value"),
                    cls="stat",
                ) for label, val in [
                    ("Tasks", task_count),
                    ("Approvals", approval_count),
                    ("Agents", f"{healthy}/{total_agents}"),
                ]],
                id="hero-stats",
                cls="hero-stats",
            ),
            Div(
                Div(
                    Span("Activity"),
                    Span("last 6 tasks", cls="muted"),
                    cls="mini-head",
                ),
                Div(
                    *[
                        Div(
                            Div(
                                Div(
                                    Strong(t.task_type),
                                    Span(t.requested_by or "-", cls="muted"),
                                    cls="left",
                                ),
                                Span(fmt_dt(t.created_at), cls="when"),
                                cls="activity-item",
                            )
                        )
                        for t in recent_tasks
                    ] or [Div("No tasks yet.", cls="muted")],
                    id="activity-feed",
                    cls="activity-feed",
                ),
                cls="panel-mini",
            ),
            cls="hero-stack",
        ),
    ]
    return _html(Div(*content, style="display:contents"))


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@router.get("/htmx/tasks", include_in_schema=False)
def htmx_tasks(page: int = 1, status: str | None = None, db: Session = Depends(get_db)):
    from sqlalchemy import func, select
    from app.models.task import Task
    query = select(Task)
    count_query = select(func.count()).select_from(Task)
    if status:
        query = query.where(Task.status == status)
        count_query = count_query.where(Task.status == status)
    total = db.scalar(count_query) or 0
    if not total:
        return _html(Notice("No tasks found.", ""))
    page, page_count, offset = _page_window(page, total)
    tasks = list(db.execute(
        query.order_by(Task.created_at.desc(), Task.id.desc())
        .offset(offset).limit(_LIST_PAGE_SIZE)
    ).scalars())

    rows = [
        Tr(
            Td(
                Strong(t.task_type),
                Br(),
                Span(t.source or "manual", cls="muted"),
            ),
            Td(Badge(t.status)),
            Td(t.requested_by or "-"),
            Td(str(t.priority)),
            Td(Code(str(t.correlation_id)[:12] + "…" if t.correlation_id else "-")),
            Td(fmt_dt(t.created_at)),
        )
        for t in tasks
    ]
    return _html(Div(
        Table(
            Thead(Tr(Th("Task"), Th("Status"), Th("Requested by"),
                     Th("Priority"), Th("Correlation"), Th("Created"))),
            Tbody(*rows),
        ),
        _page_controls("/htmx/tasks", page, page_count, total, "#tasks-list", "#tasks-filter"),
    ))


@router.post("/htmx/tasks", include_in_schema=False)
def htmx_create_task(
    request: Request,
    task_type: str = Form(...),
    priority: int = Form(100),
    source: str = Form(""),
    payload: str = Form("{}"),
    db: Session = Depends(get_db),
):
    import json as _json
    actor = get_current_user(request)
    if not actor or not actor.can("tasks"):
        return _html(Notice("Access denied", "err"))

    try:
        payload_dict = _json.loads(payload or "{}")
    except _json.JSONDecodeError:
        return _html(Notice("Invalid payload JSON.", "err"))

    repo = TaskRepository(db)
    by = actor.username
    task = repo.create(
        task_type=task_type,
        source=source.strip() or "manual",
        priority=priority,
        requested_by=by,
        payload=payload_dict,
    )
    task_queue.put(task_id=task.id, priority=task.priority)

    return _html(
        Span(f"✓ Queued — {task_type} [{str(task.id)[:8]}]", cls="badge ok")
    )


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

@router.get("/htmx/agents", include_in_schema=False)
def htmx_agents(page: int = 1, db: Session = Depends(get_db)):
    from app.agents import list_agents as registry_list_agents

    repo = AgentRepository(db)
    registry = registry_list_agents()
    db_agents = {a.agent_code: a for a in repo.list_all()}

    all_agents = sorted(registry.items())
    page, page_count, offset = _page_window(page, len(all_agents))
    cards = []
    for code, impl in all_agents[offset:offset + _LIST_PAGE_SIZE]:
        db_agent = db_agents.get(code)
        stats = repo.get_task_stats(code)
        enabled = db_agent.enabled if db_agent else False
        status = "READY" if enabled else ("DISABLED" if db_agent else "NOT CONFIGURED")
        name = db_agent.name if db_agent else getattr(impl, "agent_name", None) or code
        suite = db_agent.suite if db_agent else "Suite not configured"
        description = db_agent.description or impl.describe() if db_agent else impl.describe()

        cards.append(
            Article(
                Div(
                    Div(
                        H4(f"{code} - {name}"),
                        Div(
                            f"{suite} / {db_agent.version if db_agent else 'Version not configured'}",
                            cls="compact",
                        ),
                    ),
                    Badge(status),
                    cls="card-head",
                ),
                Div(description, cls="compact",
                    style="margin-top:10px"),
                Div(
                    Pill("Enabled", str(enabled)),
                    Pill("Autonomy", getattr(db_agent, "autonomy_level", "?") if db_agent else "?"),
                    Pill("Ledger actions", str(stats.get("total_runs", 0) if stats else 0)),
                    Pill("Successful", str(stats.get("completed", 0) if stats else 0)),
                    cls="action-line",
                    style="margin-top:12px",
                ),
                cls="card",
            )
        )
    if not cards:
        return _html(Notice("No agents reported yet."))
    return _html(Div(
        *cards,
        _page_controls("/htmx/agents", page, page_count, len(all_agents), "#agents-list"),
    ))


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------

@router.get("/htmx/approvals", include_in_schema=False)
def htmx_approvals(page: int = 1, db: Session = Depends(get_db)):
    from sqlalchemy import func, select
    from app.models.approval import Approval
    total = db.scalar(
        select(func.count()).select_from(Approval).where(Approval.status == "PENDING")
    ) or 0
    if not total:
        return _html(Notice("No pending approvals."))
    page, page_count, offset = _page_window(page, total)
    approvals = list(db.execute(
        select(Approval)
        .where(Approval.status == "PENDING")
        .order_by(Approval.requested_at.desc(), Approval.id.desc())
        .offset(offset).limit(_LIST_PAGE_SIZE)
    ).scalars())
    return _html(Div(
        *[_approval_card(a) for a in approvals],
        _page_controls("/htmx/approvals", page, page_count, total, "#approvals-list"),
    ))


def _approval_card(a) -> Article:
    payload_text = json.dumps(a.payload, indent=2) if a.payload else "{}"
    return Article(
        Div(
            Div(
                H4(a.approval_type),
                Div(f"Requested by {a.requested_by or '-'}", cls="compact"),
            ),
            Badge(a.status),
            cls="card-head",
        ),
        Pre(payload_text, cls="compact", style="margin-top:12px"),
        Div(
            Button(
                "Approve", cls="primary",
                hx_post=f"/htmx/approvals/{a.id}/approve",
                hx_target=f"#approval-{a.id}",
                hx_swap="outerHTML",
            ),
            Button(
                "Reject", cls="ghost",
                hx_post=f"/htmx/approvals/{a.id}/reject",
                hx_target=f"#approval-{a.id}",
                hx_swap="outerHTML",
            ),
            cls="action-row",
            style="margin-top:12px",
        ),
        cls="card",
        id=f"approval-{a.id}",
    )


@router.post("/htmx/approvals/{approval_id}/approve", include_in_schema=False)
def htmx_approve(
    approval_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    repo = ApprovalRepository(db)
    approval = repo.get(approval_id)
    if not approval:
        return _html(Notice("Approval not found.", "err"))

    approval.status = "APPROVED"
    approval.approved_by = "control_room"
    from datetime import datetime, timezone
    approval.decided_at = datetime.now(timezone.utc)
    db.commit()

    # Resume the task if there is one
    if approval.task_id:
        task_repo = TaskRepository(db)
        task = task_repo.get(approval.task_id)
        if task and task.status == "AWAITING_APPROVAL":
            task.status = "QUEUED"
            db.commit()
            task_queue.put(task_id=task.id, priority=task.priority)

    return _html(
        Article(
            Div(
                H4(approval.approval_type),
                Badge("APPROVED"),
                cls="card-head",
            ),
            Div("✓ Approved by control_room", cls="compact",
                style="margin-top:8px;color:var(--ok)"),
            cls="card",
            id=f"approval-{approval_id}",
        )
    )


@router.post("/htmx/approvals/{approval_id}/reject", include_in_schema=False)
def htmx_reject(
    approval_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    repo = ApprovalRepository(db)
    approval = repo.get(approval_id)
    if not approval:
        return _html(Notice("Approval not found.", "err"))

    approval.status = "REJECTED"
    approval.approved_by = "control_room"
    from datetime import datetime, timezone
    approval.decided_at = datetime.now(timezone.utc)
    db.commit()

    return _html(
        Article(
            Div(
                H4(approval.approval_type),
                Badge("REJECTED"),
                cls="card-head",
            ),
            Div("✗ Rejected by control_room", cls="compact",
                style="margin-top:8px;color:var(--danger)"),
            cls="card",
            id=f"approval-{approval_id}",
        )
    )


# ---------------------------------------------------------------------------
# Lanes
# ---------------------------------------------------------------------------

@router.get("/htmx/lanes", include_in_schema=False)
def htmx_lanes(page: int = 1, db: Session = Depends(get_db)):
    from sqlalchemy import func, select
    from app.models.lane_config import LaneConfig
    total = db.scalar(select(func.count()).select_from(LaneConfig)) or 0
    if not total:
        return _html(Notice("No lanes found."))
    page, page_count, offset = _page_window(page, total)
    lanes = list(db.execute(
        select(LaneConfig).order_by(LaneConfig.lane.asc())
        .offset(offset).limit(_LIST_PAGE_SIZE)
    ).scalars())
    return _html(Div(
        *[_lane_card(lane) for lane in lanes],
        _page_controls("/htmx/lanes", page, page_count, total, "#lanes-list"),
    ))


def _lane_card(lane) -> Article:
    return Article(
        Div(
            Div(
                H4(lane.lane),
                Div(lane.mode or "supervised", cls="compact"),
            ),
            Span(lane.autonomy_state or "A1", cls="pill"),
            cls="row",
        ),
        Div(lane.notes or "No notes", cls="compact", style="margin-top:8px"),
        Div(
            Div(
                Label(
                    "Autonomy",
                    Select(
                        Option("A1", value="A1", selected=(lane.autonomy_state == "A1")),
                        Option("A2", value="A2", selected=(lane.autonomy_state == "A2")),
                        Option("A3", value="A3", selected=(lane.autonomy_state == "A3")),
                        name="state",
                        hx_post=f"/htmx/lanes/{lane.lane}/autonomy",
                        hx_target=f"#lane-{lane.lane}",
                        hx_swap="outerHTML",
                        hx_include="this",
                        hx_trigger="change",
                    ),
                ),
                Label(
                    "Dry run",
                    Select(
                        Option("true", value="true", selected=lane.dry_run),
                        Option("false", value="false", selected=not lane.dry_run),
                        name="enabled",
                        hx_post=f"/htmx/lanes/{lane.lane}/dry-run",
                        hx_target=f"#lane-{lane.lane}",
                        hx_swap="outerHTML",
                        hx_include="this",
                        hx_trigger="change",
                    ),
                ),
                cls="inline",
            ),
            FtForm(
                Input(type="hidden", name="pulled",
                      value=str(not lane.kill_switch).lower()),
                Button(
                    ("Release" if lane.kill_switch else "Pull") + " kill-switch",
                    cls="ghost",
                    type="submit",
                ),
                hx_post=f"/htmx/lanes/{lane.lane}/kill-switch",
                hx_target=f"#lane-{lane.lane}",
                hx_swap="outerHTML",
                style="display:inline",
            ),
            cls="controls",
        ),
        cls="lane-card",
        id=f"lane-{lane.lane}",
    )


@router.post("/htmx/lanes/{lane_id}/autonomy", include_in_schema=False)
def htmx_lane_autonomy(
    lane_id: str,
    state: str = Form(...),
    db: Session = Depends(get_db),
):
    repo = LaneRepository(db)
    try:
        lane = repo.set_autonomy(lane_id, state)
    except ValueError as exc:
        return _html(Notice(str(exc), "err"))
    db.commit()
    return _html(_lane_card(lane))


@router.post("/htmx/lanes/{lane_id}/dry-run", include_in_schema=False)
def htmx_lane_dryrun(
    lane_id: str,
    enabled: str = Form(...),
    db: Session = Depends(get_db),
):
    repo = LaneRepository(db)
    try:
        lane = repo.set_dry_run(lane_id, enabled.lower() == "true")
    except ValueError as exc:
        return _html(Notice(str(exc), "err"))
    db.commit()
    return _html(_lane_card(lane))


@router.post("/htmx/lanes/{lane_id}/kill-switch", include_in_schema=False)
async def htmx_lane_kill_switch(
    lane_id: str,
    pulled: bool = Form(False),
    db: Session = Depends(get_db),
):
    repo = LaneRepository(db)
    try:
        lane = repo.set_kill_switch(lane_id, pulled)
    except ValueError as exc:
        return _html(Notice(str(exc), "err"))
    db.commit()
    return _html(_lane_card(lane))


# ---------------------------------------------------------------------------
# Tally
# ---------------------------------------------------------------------------

@router.post("/htmx/tally/stage", include_in_schema=False)
async def htmx_tally_stage(request: Request, db: Session = Depends(get_db)):
    """Stage voucher entries via TallyGateway."""
    from app.integrations.tally_gateway import TallyGateway, TallyGatewayError
    form = await request.form()
    try:
        entries = json.loads(form.get("entries", "[]"))
        TallyGateway(db).stage_entries(
            entity=form.get("entity", "YEXIS-EL"),
            books_txn_id=form.get("books_txn_id", ""),
            entries=entries,
            company=form.get("company", ""),
        )
        db.commit()
        return _html(Span("✓ Staged successfully", cls="badge ok"))
    except json.JSONDecodeError:
        return _html(Notice("Invalid entries JSON.", "err"))
    except TallyGatewayError as exc:
        return _html(Notice(str(exc), "err"))
    except Exception as exc:
        return _html(Notice(str(exc), "err"))


@router.get("/htmx/tally/staged", include_in_schema=False)
def htmx_tally_staged(entity: str = "YEXIS-EL", page: int = 1, db: Session = Depends(get_db)):
    from app.integrations.tally_gateway import TallyGateway
    from app.models.tally_staging import TallyStagingVoucher
    from sqlalchemy import func
    total = db.query(func.count(TallyStagingVoucher.id)).filter(
        TallyStagingVoucher.entity == entity,
        TallyStagingVoucher.status == "STAGED",
    ).scalar() or 0
    if not total:
        return _html(Notice("No staged rows.", ""))
    page, page_count, offset = _page_window(page, total)
    try:
        rows = TallyGateway(db).list_staged(
            entity=entity, limit=_LIST_PAGE_SIZE, offset=offset,
        )
    except Exception as exc:
        logger.exception("Could not load staged Tally rows")
        return _html(Notice(f"Could not load staged vouchers: {exc}", "err"))
    if not rows:
        return _html(Notice("No staged rows on this page.", ""))
    table_rows = [
        Tr(
            Td(str(r.id)[:8]),
            Td(r.books_txn_id or ""),
            Td(Badge(r.status or "")),
            Td(fmt_dt(r.created_at)),
        )
        for r in rows
    ]
    return _html(Div(
        Table(
            Thead(Tr(Th("ID"), Th("Books Txn"), Th("Status"), Th("Created"))),
            Tbody(*table_rows),
        ),
        _page_controls(
            "/htmx/tally/staged", page, page_count, total,
            "#tally-list", "#tally-entity",
        ),
    ))


@router.post("/htmx/tally/post", include_in_schema=False)
def htmx_tally_post(entity: str = Form("YEXIS-EL"), db: Session = Depends(get_db)):
    from app.integrations.tally_gateway import TallyGateway, TallyGatewayError
    try:
        result = TallyGateway(db).post_approved(entity=entity)
        db.commit()
        posted = result.get("posted", 0) if isinstance(result, dict) else 0
        return _html(Span(f"✓ Posted {posted} vouchers", cls="badge ok"))
    except TallyGatewayError as exc:
        return _html(Notice(str(exc), "err"))
    except Exception as exc:
        return _html(Notice(str(exc), "err"))


@router.get("/htmx/tally/trial-balance", include_in_schema=False)
def htmx_tally_trial(entity: str = "YEXIS-EL", page: int = 1, db: Session = Depends(get_db)):
    from app.integrations.tally_gateway import TallyGateway, TallyGatewayError
    try:
        rows_data = TallyGateway(db).trial_balance(entity)
    except TallyGatewayError as exc:
        return _html(Notice(str(exc), "err"))
    except Exception as exc:
        return _html(Notice(str(exc), "err"))
    if not rows_data:
        return _html(Notice("No trial balance rows.", ""))
    total = len(rows_data)
    page, page_count, offset = _page_window(page, total)
    rows_data = rows_data[offset:offset + _LIST_PAGE_SIZE]
    table_rows = [
        Tr(
            Td(str(row.get("account", ""))),
            Td(str(row.get("debit", ""))),
            Td(str(row.get("credit", ""))),
            Td(str(row.get("closing", ""))),
        )
        for row in rows_data
    ]
    return _html(Div(
        Table(
            Thead(Tr(Th("Account"), Th("Debit"), Th("Credit"), Th("Closing"))),
            Tbody(*table_rows),
        ),
        _page_controls(
            "/htmx/tally/trial-balance", page, page_count, total,
            "#tally-list", "#tally-entity",
        ),
    ))


# ---------------------------------------------------------------------------
# Mail
# ---------------------------------------------------------------------------

@router.get("/htmx/mail/health", include_in_schema=False)
def htmx_mail_health():
    from app.services.mailbox import configuration_status
    try:
        data = configuration_status()
    except Exception:
        data = {}
    pop3 = data.get("pop3_configured", False)
    smtp = data.get("smtp_configured", False)
    send_on = data.get("smtp_send_enabled", False)
    kind = "ok" if (pop3 or smtp) else ""
    msg = (
        f"Mail: POP3 {'✓' if pop3 else '✗'} · "
        f"SMTP {'✓' if smtp else '✗'} · "
        f"Send {'enabled' if send_on else 'draft-only'}"
    )
    return _html(
        Div(msg, id="mail-health", cls=f"notice {kind}",
            style="margin-bottom:12px",
            hx_get="/htmx/mail/health",
            hx_trigger=_AUTO,
            hx_swap="outerHTML")
    )


@router.post("/htmx/mail/poll", include_in_schema=False)
def htmx_mail_poll(db: Session = Depends(get_db)):
    from app.services.mailbox import poll_pop3
    try:
        rows = poll_pop3(db, 25)
        response = _html(Span(f"✓ Fetched {len(rows)} new messages", cls="badge ok"))
        response.headers["HX-Trigger"] = "mail-inbox-refresh"
        return response
    except Exception as exc:
        return _html(Notice(str(exc), "err"))


@router.get("/htmx/mail/inbox", include_in_schema=False)
def htmx_mail_inbox(page: int = 1, q: str = "", db: Session = Depends(get_db)):
    from sqlalchemy import func, or_, select as sa_select
    from app.models.enterprise import EmailMessage
    page_size = 10
    page = max(1, page)
    q = q.strip()[:200]
    filters = [EmailMessage.direction == "in"]
    if q:
        escaped_q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        search = f"%{escaped_q}%"
        filters.append(or_(
            EmailMessage.sender.ilike(search, escape="\\"),
            EmailMessage.subject.ilike(search, escape="\\"),
            EmailMessage.body.ilike(search, escape="\\"),
        ))
    total = db.scalar(
        sa_select(func.count()).select_from(EmailMessage).where(*filters)
    ) or 0
    if not total:
        return _html(Notice(
            f'No inbox messages match "{q}".' if q else "No inbox messages."
        ))

    page_count = (total + page_size - 1) // page_size
    page = min(page, page_count)
    rows = (
        db.execute(
            sa_select(EmailMessage)
            .where(*filters)
            .order_by(EmailMessage.created_at.desc(), EmailMessage.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).scalars().all()
    )
    items = [
        Article(
            Div(
                Div(
                    Strong(m.sender or "-"),
                    Div(m.subject or "-", cls="compact"),
                ),
                Span(fmt_dt(m.created_at), cls="when"),
                cls="card-head",
            ),
            Details(
                Summary("Read message"),
                Pre((m.body or "No plain-text message body.")[:4000],
                    cls="mail-preview"),
                cls="mail-message-preview",
            ),
            cls="card",
        )
        for m in rows
    ]
    return _html(
        Div(
            *items,
            Div(
                Button(
                    "← Newer",
                    cls="ghost",
                    disabled=page <= 1,
                    hx_get=f"/htmx/mail/inbox?page={max(1, page - 1)}",
                    hx_target="#inbox-list",
                    hx_swap="innerHTML",
                    hx_include="#inbox-search",
                ),
                Span(
                    f"Page {page} of {page_count} · {total} "
                    f"{'message' if total == 1 else 'messages'}",
                    cls="muted",
                ),
                Button(
                    "Older →",
                    cls="ghost",
                    disabled=page >= page_count,
                    hx_get=f"/htmx/mail/inbox?page={min(page_count, page + 1)}",
                    hx_target="#inbox-list",
                    hx_swap="innerHTML",
                    hx_include="#inbox-search",
                ),
                cls="mail-pagination",
            ),
        )
    )


@router.get("/htmx/mail/drafts", include_in_schema=False)
def htmx_mail_drafts(page: int = 1, db: Session = Depends(get_db)):
    from sqlalchemy import func, select as sa_select
    from app.models.enterprise import EmailMessage
    total = db.scalar(
        sa_select(func.count()).select_from(EmailMessage)
        .where(EmailMessage.direction == "out")
    ) or 0
    if not total:
        return _html(Notice("No drafts."))
    page, page_count, offset = _page_window(page, total)
    rows = (
        db.execute(
            sa_select(EmailMessage)
            .where(EmailMessage.direction == "out")
            .order_by(EmailMessage.created_at.desc(), EmailMessage.id.desc())
            .offset(offset)
            .limit(_LIST_PAGE_SIZE)
        ).scalars().all()
    )
    items = [
        Article(
            Div(
                Div(
                    Strong(m.recipient or "-"),
                    Div(m.subject or "-", cls="compact"),
                ),
                Badge(m.status or "draft"),
                cls="card-head",
            ),
            cls="card",
        )
        for m in rows
    ]
    return _html(Div(
        *items,
        _page_controls("/htmx/mail/drafts", page, page_count, total, "#drafts-list"),
    ))


@router.post("/htmx/mail/draft", include_in_schema=False)
async def htmx_mail_draft(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        from app.models.enterprise import EmailMessage
        msg = EmailMessage(
            direction="out",
            status="draft",
            recipient=form.get("recipient", ""),
            subject=form.get("subject", ""),
            body=form.get("body", ""),
            attachments=[],
        )
        db.add(msg)
        db.commit()
        return _html(Span("✓ Draft created", cls="badge ok"))
    except Exception as exc:
        return _html(Notice(str(exc), "err"))


# ---------------------------------------------------------------------------
# Campaigns (list only — wizard uses original JS)
# ---------------------------------------------------------------------------

@router.get("/htmx/campaigns", include_in_schema=False)
def htmx_campaigns(entity: str = "YEXIS-EL", page: int = 1, db: Session = Depends(get_db)):
    from app.models.enterprise import Campaign, CampaignContact
    from sqlalchemy import func
    total = db.query(func.count(Campaign.id)).filter(Campaign.entity == entity).scalar() or 0
    if not total:
        return _html(P("No campaigns found.", cls="muted"))
    page, page_count, offset = _page_window(page, total)
    rows = (
        db.query(Campaign)
        .filter(Campaign.entity == entity)
        .order_by(Campaign.created_ts.desc(), Campaign.id.desc())
        .offset(offset)
        .limit(_LIST_PAGE_SIZE)
        .all()
    )
    campaigns = [
        {
            "id": str(c.id),
            "name": c.name,
            "campaign_type": c.campaign_type,
            "status": c.status,
            "created_at": c.created_ts,
            "contact_count": db.query(func.count(CampaignContact.id)).filter(
                CampaignContact.campaign_id == c.id
            ).scalar() or 0,
        }
        for c in rows
    ]
    rows = [
        Div(
            Div(
                Strong(c.get("name", "-")),
                Div(c.get("campaign_type", "-"), cls="compact"),
            ),
            Span(c.get("status", "-"),
                 cls=f"badge status-{c.get('status', 'draft')}"),
            Span(fmt_dt(c.get("created_at")), cls="muted",
                 style="font-size:.78rem"),
            Div(
                Button("View", cls="cmp-btn",
                       hx_get=f"/htmx/campaigns/{c.get('id', '')}/detail",
                       hx_target="#cmp-detail",
                       hx_swap="innerHTML"),
                cls="cmp-actions",
            ),
            cls="cmp-list-row",
        )
        for c in campaigns
    ]
    return _html(Div(
        *rows,
        _page_controls("/htmx/campaigns", page, page_count, total, "#cmp-list-wrap", "#cmp-entity-filter"),
        Div(id="cmp-detail"),
    ))


@router.get("/htmx/campaigns/{campaign_id}/detail", include_in_schema=False)
def htmx_campaign_detail(campaign_id: str, db: Session = Depends(get_db)):
    from app.models.enterprise import Campaign, CampaignContact
    from sqlalchemy import func
    row = db.query(Campaign).filter(Campaign.id == campaign_id).first()
    if not row:
        c = {}
    else:
        c = {
            "id": str(row.id),
            "name": row.name,
            "campaign_type": row.campaign_type,
            "status": row.status,
            "created_at": row.created_ts,
            "contact_count": db.query(func.count(CampaignContact.id)).filter(
                CampaignContact.campaign_id == row.id
            ).scalar() or 0,
        }

    if not c:
        return _html(Notice("Campaign not found.", "err"))

    return _html(
        Div(
            H3(c.get("name", "-"), style="margin:0"),
            Span(fmt_dt(c.get("created_at")), cls="muted", style="font-size:.8rem"),
            Div(
                Div(
                    Span("Type", cls="muted", style="font-size:.78rem"),
                    Strong(c.get("campaign_type", "-")),
                    cls="cmp-kpi",
                ),
                Div(
                    Span("Contacts", cls="muted", style="font-size:.78rem"),
                    Strong(str(c.get("contact_count", 0))),
                    cls="cmp-kpi",
                ),
                style="display:flex;gap:.75rem;margin-top:.75rem",
            ),
        )
    )


# ---------------------------------------------------------------------------
# Templates (list + create)
# ---------------------------------------------------------------------------

@router.get("/htmx/templates", include_in_schema=False)
def htmx_templates(channel: str = "", status: str = "", page: int = 1, db: Session = Depends(get_db)):
    from app.models.enterprise import TemplateRegistry
    from sqlalchemy import func
    q = db.query(TemplateRegistry)
    if channel:
        q = q.filter(TemplateRegistry.channel == channel)
    if status:
        q = q.filter(TemplateRegistry.approval_status == status)
    total = q.with_entities(func.count(TemplateRegistry.template_id)).scalar() or 0
    if not total:
        return _html(P("No templates found.", cls="muted"))
    page, page_count, offset = _page_window(page, total)
    rows = q.order_by(TemplateRegistry.template_id).offset(offset).limit(_LIST_PAGE_SIZE).all()
    templates = [
        {
            "template_id": t.template_id,
            "channel": t.channel,
            "purpose": t.purpose,
            "approval_status": t.approval_status,
            "created_at": getattr(t, "created_at", None),
        }
        for t in rows
    ]
    ch_badge_cls = {
        "email": "tmpl-badge email",
        "wa": "tmpl-badge wa",
        "telegram": "tmpl-badge",
        "push": "tmpl-badge",
        "voice": "tmpl-badge",
        "linkedin": "tmpl-badge",
    }
    st_badge_cls = {
        "pending": "tmpl-badge pending",
        "internal": "tmpl-badge internal",
        "meta": "tmpl-badge meta",
        "dlt": "tmpl-badge dlt",
    }

    _open_modal = "document.getElementById('tmpl-modal').classList.add('open')"

    def _actions(tid):
        return Td(
            Button("Preview", cls="ghost", style="font-size:.72rem;padding:.2rem .55rem",
                   hx_get=f"/htmx/templates/{tid}/preview",
                   hx_target="#tmpl-form-host", hx_swap="innerHTML",
                   onclick=_open_modal),
            Button("Edit", cls="primary", style="font-size:.72rem;padding:.2rem .55rem",
                   hx_get=f"/htmx/templates/form?id={tid}",
                   hx_target="#tmpl-form-host", hx_swap="innerHTML",
                   onclick=_open_modal),
            style="display:flex;gap:.4rem;justify-content:flex-end",
        )

    rows = [
        Tr(
            Td(Strong(t.get("template_id", "-"))),
            Td(Span(t.get("channel", "-"),
                    cls=ch_badge_cls.get(t.get("channel", ""), "tmpl-badge"))),
            Td(t.get("purpose", "-")),
            Td(Span(t.get("approval_status", "pending"),
                    cls=st_badge_cls.get(t.get("approval_status", "pending"), "tmpl-badge pending"))),
            Td(fmt_dt(t.get("created_at"))),
            _actions(t.get("template_id", "")),
        )
        for t in templates
    ]

    return _html(Div(
        Table(
            Thead(Tr(Th("ID"), Th("Channel"), Th("Purpose"), Th("Status"), Th("Created"),
                     Th("Actions", style="text-align:right"))),
            Tbody(*rows),
            cls="tmpl-table",
        ),
        _page_controls(
            "/htmx/templates", page, page_count, total,
            "#tmpl-list-wrap", "#tmpl-ch-filter,#tmpl-st-filter",
        ),
    ))


@router.post("/htmx/templates", include_in_schema=False)
async def htmx_create_template(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    tid = (form.get("template_id", "") or "").strip()
    if not tid:
        return _html(Notice("Template ID is required.", "err"))
    try:
        from app.models.enterprise import TemplateRegistry
        if db.get(TemplateRegistry, tid):
            return _html(Notice(f"Template '{tid}' already exists.", "err"))
        channel = form.get("channel", "email") or "email"
        t = TemplateRegistry(
            template_id=tid,
            channel=channel,
            purpose=form.get("purpose", "") or "",
            language=form.get("language", "en") or "en",
            subject=form.get("subject") or None,
            reply_to=form.get("reply_to") or None,
            body=form.get("body", "") or "",
            approval_status=form.get("approval_status", "pending") or "pending",
        )
        db.add(t)
        db.commit()
        # Re-render the form in EDIT mode (the template now exists), retargeting
        # the swap to the whole form host instead of the inline notice. The
        # attachment box only works once the row exists, so this flips the
        # create form straight into the edit form with attachments live — no
        # "save first, then reopen to attach" round-trip.
        return HTMLResponse(
            to_xml(Div(
                Span("✓ Template saved — add attachments below, then set approval if needed.",
                     cls="badge ok"),
                _template_form(t),
            )),
            headers={"HX-Retarget": "#tmpl-form-host", "HX-Reswap": "innerHTML"},
        )
    except Exception as exc:
        return _html(Notice(str(exc), "err"))


def _tmpl_attach_items(template):
    """FT component: the current attachment list for a template (embeddable)."""
    atts = template.attachments_json or []
    if not atts:
        return P("No attachments yet.", cls="muted", style="font-size:.72rem;margin:0")
    rows = []
    for a in atts:
        fn = a.get("filename", "")
        kb = max(1, (a.get("size") or 0) // 1024)
        rows.append(Div(
            Span(fn, style="font-family:monospace;font-size:.78rem;overflow:hidden;text-overflow:ellipsis"),
            Span(f"{kb} KB", cls="muted", style="font-size:.7rem"),
            Button("✕", cls="ghost", type="button",
                   title="Remove attachment",
                   hx_post="/htmx/templates/attach/delete",
                   hx_vals=json.dumps({"template_id": template.template_id, "filename": fn}),
                   hx_target="#tmpl-attach-list", hx_swap="innerHTML",
                   style="margin-left:auto;color:var(--danger);padding:0 .4rem"),
            style="display:flex;align-items:center;gap:.5rem;padding:.3rem .5rem;"
                  "border:1px solid var(--panel-border);border-radius:6px",
        ))
    return Div(*rows, cls="stack", style="gap:.35rem")


def _tmpl_attach_list(template):
    """HTMX response wrapper around _tmpl_attach_items (upload/delete targets)."""
    return _html(_tmpl_attach_items(template))


@router.post("/htmx/templates/attach", include_in_schema=False)
async def htmx_template_attach(request: Request, db: Session = Depends(get_db)):
    import mimetypes
    from pathlib import Path
    from app.models.enterprise import TemplateRegistry

    form = await request.form()
    tid = (form.get("template_id", "") or "").strip()
    upload = form.get("file")
    if not tid:
        return _html(Notice("Enter a Template ID and save the template first.", "err"))
    t = db.get(TemplateRegistry, tid)
    if t is None:
        return _html(Notice(f"Save template '{tid}' first, then attach files.", "err"))
    if upload is None or not getattr(upload, "filename", ""):
        return _tmpl_attach_list(t)

    attach_dir = Path(__file__).resolve().parents[2] / "attachments" / "templates" / tid
    attach_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(upload.filename).name
    dest = attach_dir / safe_name
    counter = 1
    while dest.exists():
        dest = attach_dir / f"{dest.stem}_{counter}{dest.suffix}"
        counter += 1
    content = await upload.read()
    dest.write_bytes(content)

    mime_type = getattr(upload, "content_type", None) or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    meta = {"filename": dest.name, "path": str(dest), "size": len(content), "mime_type": mime_type}
    t.attachments_json = list(t.attachments_json or []) + [meta]
    db.commit()
    return _tmpl_attach_list(t)


@router.post("/htmx/templates/attach/delete", include_in_schema=False)
async def htmx_template_attach_delete(request: Request, db: Session = Depends(get_db)):
    from pathlib import Path
    from app.models.enterprise import TemplateRegistry

    form = await request.form()
    tid = (form.get("template_id", "") or "").strip()
    filename = (form.get("filename", "") or "").strip()
    t = db.get(TemplateRegistry, tid)
    if t is None:
        return _html(Notice("Template not found.", "err"))
    updated = [a for a in (t.attachments_json or []) if a.get("filename") != filename]
    if len(updated) != len(t.attachments_json or []):
        dest = Path(__file__).resolve().parents[2] / "attachments" / "templates" / tid / filename
        if dest.exists():
            dest.unlink()
        t.attachments_json = updated
        db.commit()
    return _tmpl_attach_list(t)


def _render_check(lint: dict, pr: dict | None):
    """FT fragment: the content-check results shown under the composer."""
    items = []
    blockers = lint.get("blockers", [])
    warnings = lint.get("warnings", [])
    if lint.get("ok"):
        items.append(Span("✓ No blocking problems — safe to send.", cls="badge ok"))
    else:
        items.append(Span(
            f"✕ {len(blockers)} blocking problem(s) — this template will NOT send until fixed.",
            cls="badge", style="background:#7f1d1d;color:#fff"))

    def _line(f, color):
        detail = f.get("detail")
        if isinstance(detail, list):
            detail = ", ".join(str(d) for d in detail)
        return Div(
            Strong(f["message"], style=f"color:{color}"),
            (P(str(detail), cls="muted", style="font-size:.72rem;margin:.1rem 0 0") if detail else None),
            style="padding:.35rem .5rem;border:1px solid var(--panel-border);"
                  "border-radius:6px;margin-top:.35rem")

    for b in blockers:
        items.append(_line(b, "#fca5a5"))
    for w in warnings:
        items.append(_line(w, "#fcd34d"))

    if pr is None:
        pass
    elif not pr.get("available"):
        items.append(P("Spelling/grammar check did not run: " + (pr.get("reason") or ""),
                       cls="muted", style="font-size:.72rem;margin-top:.5rem"))
    elif not pr.get("issues"):
        items.append(P(f"✓ Spelling/grammar: no issues found ({pr.get('model') or 'model'}).",
                       cls="muted", style="font-size:.72rem;margin-top:.5rem"))
    else:
        rows = [
            Div(Strong(it.get("text", "")),
                Span(" → " + it.get("fix", ""), style="color:#86efac"),
                P(it.get("problem", ""), cls="muted", style="font-size:.7rem;margin:.1rem 0 0"),
                style="padding:.35rem .5rem;border:1px solid var(--panel-border);"
                      "border-radius:6px;margin-top:.35rem")
            for it in pr["issues"]
        ]
        items.append(Div(H4("Spelling / grammar suggestions",
                            style="margin:.6rem 0 .2rem;font-size:.8rem"), *rows))

    return Div(*[i for i in items if i is not None])


@router.post("/htmx/templates/check", include_in_schema=False)
async def htmx_template_check(request: Request, db: Session = Depends(get_db)):
    """Run the content-correctness check on the CURRENT composer contents
    (not the saved row), so the author sees problems before saving/sending.
    Attachments are read from the saved template when it exists."""
    import types
    from app.models.enterprise import TemplateRegistry
    from app.services.campaign_lint import lint_template
    from app.services.campaign_proofread import proofread as _proofread

    form = await request.form()
    tid = (form.get("template_id", "") or "").strip()
    saved = db.get(TemplateRegistry, tid) if tid else None
    tmpl = types.SimpleNamespace(
        channel=form.get("channel", "email") or "email",
        subject=form.get("subject") or None,
        body=form.get("body", "") or "",
        purpose=(getattr(saved, "purpose", "") or ""),
        segment_overrides_json=getattr(saved, "segment_overrides_json", None),
        attachments_json=getattr(saved, "attachments_json", None) or [],
    )
    # Static checks only here (no sample contact in the composer); the send
    # gate repeats the check against a real recipient for blank-field detection.
    lint = lint_template(tmpl, sample_contact=None, campaign=None, db=None)
    pr = _proofread(form.get("subject") or "", form.get("body", "") or "")
    return _html(_render_check(lint, pr))


# NOTE: these {template_id} routes are declared AFTER the literal
# /htmx/templates/attach routes above so the literals win route matching.

@router.get("/htmx/templates/form", include_in_schema=False)
def htmx_template_form(id: str = "", db: Session = Depends(get_db)):
    """Return the compose form fragment — blank (create) or pre-filled (edit)."""
    from app.models.enterprise import TemplateRegistry
    t = db.get(TemplateRegistry, id) if id else None
    if id and t is None:
        return _html(Notice(f"Template '{id}' not found.", "err"))
    return _html(_template_form(t))


@router.post("/htmx/templates/{template_id}", include_in_schema=False)
async def htmx_update_template(template_id: str, request: Request, db: Session = Depends(get_db)):
    """Update an existing template from the edit form."""
    from app.models.enterprise import TemplateRegistry
    t = db.get(TemplateRegistry, template_id)
    if t is None:
        return _html(Notice(f"Template '{template_id}' not found.", "err"))
    form = await request.form()
    if form.get("channel"):
        t.channel = form.get("channel")
    t.purpose = form.get("purpose", "") or ""
    t.subject = form.get("subject") or None
    t.reply_to = form.get("reply_to") or None
    t.body = form.get("body", "") or ""
    if form.get("approval_status"):
        t.approval_status = form.get("approval_status")
    t.version = (t.version or 1) + 1
    db.commit()
    return _html(Span("✓ Template updated", cls="badge ok"))


@router.get("/htmx/templates/{template_id}/preview", include_in_schema=False)
def htmx_template_preview(template_id: str, db: Session = Depends(get_db)):
    """Read-only preview — renders without inventing contact data."""
    from app.models.enterprise import TemplateRegistry
    from app.api.templates import _jinja_render
    t = db.get(TemplateRegistry, template_id)
    if t is None:
        return _html(Notice(f"Template '{template_id}' not found.", "err"))

    try:
        body_html = _jinja_render(t.body or "", {})
    except Exception:
        body_html = t.body or ""
    subject = t.subject or ""
    if subject:
        try:
            subject = _jinja_render(subject, {})
        except Exception:
            pass
    atts = t.attachments_json or []

    def _meta(lbl, val):
        return Div(Strong(lbl, style="color:var(--muted);font-size:.72rem"),
                   Span(val, style="font-size:.82rem"),
                   style="display:flex;gap:.5rem;align-items:baseline;margin-bottom:.3rem")

    return _html(Div(
        H3(f"Preview — {template_id}", style="margin:0 0 .75rem"),
        _meta("Subject:", subject or "—"),
        _meta("Reply-To:", t.reply_to or "—"),
        _meta("Attachments:", ", ".join(a.get("filename", "") for a in atts) if atts else "—"),
        P("Rendered without contact data; personalized fields are blank.",
          cls="muted", style="font-size:.72rem;margin:.5rem 0 .3rem"),
        Div(NotStr(body_html),
            style="border:1px solid var(--panel-border);border-radius:8px;padding:1rem;"
                  "background:#ffffff;color:#111;max-height:50vh;overflow:auto"),
        Div(
            Button("Close", cls="ghost", type="button",
                   onclick="document.getElementById('tmpl-modal').classList.remove('open')"),
            Button("Edit this template", cls="primary", type="button",
                   hx_get=f"/htmx/templates/form?id={template_id}",
                   hx_target="#tmpl-form-host", hx_swap="innerHTML"),
            style="display:flex;justify-content:flex-end;gap:.5rem;margin-top:1rem",
        ),
    ))


# ---------------------------------------------------------------------------
# Opt-outs
# ---------------------------------------------------------------------------

@router.get("/htmx/optouts", include_in_schema=False)
def htmx_optouts(page: int = 1, db: Session = Depends(get_db)):
    from app.models.enterprise import ContactOptOut
    from sqlalchemy import func
    total = db.query(func.count(ContactOptOut.contact_ref)).scalar() or 0
    if not total:
        return _html(Div("No opt-outs on record.", cls="optout-empty"))
    page, page_count, offset = _page_window(page, total)
    rows_data = (
        db.query(ContactOptOut)
        .order_by(ContactOptOut.opted_out_at.desc(), ContactOptOut.contact_ref.desc())
        .offset(offset)
        .limit(_LIST_PAGE_SIZE)
        .all()
    )
    rows = [
        Tr(
            Td(r.msisdn or r.email or r.contact_ref or "-"),
            Td(getattr(r, "channel", "-") or "-"),
            Td(fmt_dt(r.opted_out_at)),
        )
        for r in rows_data
    ]
    return _html(Div(
        Table(
            Thead(Tr(Th("Contact"), Th("Channel"), Th("Opted out"))),
            Tbody(*rows),
            cls="optout-table",
        ),
        _page_controls("/htmx/optouts", page, page_count, total, "#optout-list-wrap"),
    ))


# ===========================================================================
# DEMO — run scenarios + poll task status
# ===========================================================================

_DEMO_PULSE = """
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.demo-pulse{animation:pulse 1.4s infinite}
.demo-col-head{font-size:.72rem;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.04em;margin-bottom:.5rem}
.demo-kv{display:flex;justify-content:space-between;padding:.25rem 0;border-bottom:1px solid var(--border)}
.demo-kv:last-child{border-bottom:none}
.demo-kv-key{color:var(--muted);font-size:.78rem}
.demo-kv-val{font-weight:600;font-size:.82rem;text-align:right}
.demo-ledger-entry{padding:.3rem 0;border-bottom:1px solid var(--border);font-size:.78rem}
.demo-ledger-entry:last-child{border-bottom:none}
"""


def _zoho_snapshot_html(snap: dict):
    if not snap:
        return Div(
            "Source details are not available in this view. See the results for what the agent found.",
            cls="panel-mini demo-source-empty",
        )
    labels = {
        "source": "Information source",
        "data_mode": "Data type",
        "invoice_count": "Invoices reviewed",
        "total_outstanding": "Outstanding balance",
        "customers": "Customers",
        "date_range": "Date range",
        "journal_lines": "Journal entries",
        "dealers": "Dealers included",
        "kpi_rows": "Performance measures",
        "note": "Note",
        "vendor": "Supplier",
        "items": "Requested items",
        "budget_limit": "Budget limit",
    }
    items = []
    for key, value in snap.items():
        if key == "fetched_at" or value in (None, ""):
            continue
        if key == "data_mode":
            value = "Live data" if value == "live" else "Not verified as live"
        elif key == "customers" and isinstance(value, list):
            value = ", ".join(str(name) for name in value)
        elif key == "items" and isinstance(value, list):
            value = ", ".join(
                str(item.get("item", "Item")) for item in value
                if isinstance(item, dict)
            )
        elif key == "total_outstanding":
            value = _demo_money(value)
        items.append((labels.get(key, key.replace("_", " ").capitalize()), str(value)))
    return Div(
        *[Div(
            Span(k, cls="demo-kv-key"),
            Span(v[:60] if len(v) > 60 else v, cls="demo-kv-val"),
            cls="demo-kv",
        ) for k, v in items],
        cls="panel-mini",
        style="font-size:.82rem;line-height:1.6;min-height:120px",
    )


def _demo_money(value) -> str:
    try:
        return f"₹{float(value):,.2f}"
    except (TypeError, ValueError):
        return "₹0.00"


def _ledger_html(entries: list):
    if not entries:
        return Div("No ledger entries yet.", cls="muted", style="font-size:.78rem")
    return Div(
        *[Div(
            Span(e.get("action", "-"), style="font-weight:600"),
            Span(" · " + (e.get("tool") or ""), cls="muted"),
            Br(),
            Badge(e.get("status", "")),
            cls="demo-ledger-entry",
        ) for e in entries[-8:]],
    )


def _demo_output_panel(
    scenario_name: str,
    task_type: str,
    status: str,
    task_id: str,
    zoho_snap: dict,
    result_html,
    ledger_entries: list,
    approval_info: dict | None = None,
    poll: bool = False,
):
    status_color = (
        "#f59e0b"
        if status in ("QUEUED", "RUNNING", "AWAITING_APPROVAL")
        else "#4ade80"
        if status in ("SUCCESS", "COMPLETED")
        else "#f87171"
    )

    poll_div = None
    if poll and status in ("QUEUED", "RUNNING", "AWAITING_APPROVAL"):
        poll_div = Div(
            id="demo-poll",
            hx_get=f"/htmx/demo/poll/{task_id}",
            hx_trigger="every 2s",
            hx_target="#demo-output",
            hx_swap="innerHTML",
        )

    approval_card = None
    if approval_info:
        approval_card = Div(
            P("⚠ Human Approval Required", style="margin:0 0 .5rem;font-weight:600;color:#f59e0b"),
            P(approval_info.get("approval_type", ""), cls="muted",
              style="margin:0 0 .75rem;font-size:.85rem"),
            Div(
                Button(
                    "✓ Approve", cls="primary",
                    style="background:#16a34a;border-color:#16a34a",
                    hx_post=f"/htmx/demo/approve/{approval_info['approval_id']}",
                    hx_target="#demo-output",
                    hx_swap="innerHTML",
                ),
                Button("✗ Reject", cls="ghost", style="color:#ef4444;border-color:#ef4444",
                       hx_post=f"/htmx/demo/reject/{approval_info['approval_id']}",
                       hx_target="#demo-output",
                       hx_swap="innerHTML"),
                style="display:flex;gap:.5rem",
            ),
            style="margin-top:.75rem;padding:1rem;border:2px solid #f59e0b;border-radius:8px;background:rgba(245,158,11,.05)",
        )

    return Div(
        Style(_DEMO_PULSE),
        Div(
            Div(
                style=f"width:10px;height:10px;border-radius:50%;background:{status_color};"
                      "box-shadow:0 0 0 4px rgba(245,158,11,.25)",
                cls="demo-pulse" if status in ("QUEUED", "RUNNING", "AWAITING_APPROVAL") else "",
            ),
            H3(scenario_name, style="margin:0;font-size:1.1rem"),
            Badge({
                "SUCCESS": "COMPLETED",
                "FAILED": "NEEDS ATTENTION",
                "VALIDATION_ERROR": "CHECK INPUT",
                "AWAITING_APPROVAL": "AWAITING APPROVAL",
                "RUNNING": "IN PROGRESS",
                "QUEUED": "STARTING",
            }.get(status, status.replace("_", " "))),
            style="display:flex;align-items:center;gap:.75rem;margin-bottom:1rem",
        ),
        Div(
            Div(
                P("About this run", cls="demo-col-head"),
                _zoho_snapshot_html(zoho_snap),
                cls="demo-output-source",
            ),
            Div(
                P("What happened", cls="demo-col-head"),
                Div(
                    result_html or Div("Running…", cls="muted"),
                    style="min-height:120px;max-height:620px;overflow-y:auto",
                    cls="panel-mini",
                ),
                approval_card,
                cls="demo-output-results",
            ),
            Details(
                Summary("Technical run details"),
                Div(_ledger_html(ledger_entries), cls="panel-mini",
                    style="font-size:.78rem;min-height:80px"),
                Div(f"Run reference: {task_id[:12]}…", cls="demo-run-reference"),
                cls="demo-technical-details",
            ),
            cls="demo-output-layout",
        ),
        poll_div,
        cls="demo-output-card",
    )


def _format_result(result: dict | None, task_type: str):
    """Render demo task results as human-readable summaries."""
    if not result:
        return None
    error = result.get("error")
    if error:
        error_text = str(error)
        if "trial" in error_text.lower() and "upgrade" in error_text.lower():
            return Div(
                H4("Zoho Books needs an active subscription", cls="demo-result-title"),
                P(
                    "The account trial has ended, so Zoho Books did not create the purchase order. "
                    "Renew or upgrade the Zoho Books plan before trying again.",
                    cls="demo-result-copy",
                ),
                cls="demo-result-message is-warning",
            )
        return Div(
            H4("The scenario could not finish", cls="demo-result-title"),
            P(error_text, cls="demo-result-copy"),
            P(
                "Check the connected service or configuration, then try again.",
                cls="demo-result-hint",
            ),
            cls="demo-result-message is-warning",
        )
    output = result.get("output") or result
    if not isinstance(output, dict):
        return Div(P(str(output), cls="demo-result-copy"), cls="demo-result-message")

    if task_type == "collections":
        summary = output.get("summary") or {}
        aging = output.get("aging_summary") or {}
        invoices = output.get("processed_invoices") or []
        buckets = (
            ("current", "Not overdue"),
            ("days_1_30", "1–30 days overdue"),
            ("days_31_60", "31–60 days overdue"),
            ("days_61_90", "61–90 days overdue"),
            ("over_90", "More than 90 days overdue"),
        )
        follow_up = {
            "REMINDER": "Send a payment reminder",
            "CALL": "Arrange a follow-up call",
            "LEGAL_NOTICE": "Escalate for further action",
        }.get(summary.get("follow_up_action"), "Review the recommended follow-up")
        return Div(
            P(
                f"{summary.get('total_overdue', 0)} of "
                f"{summary.get('total_invoices', len(invoices))} invoices are overdue. "
                f"The oldest is {summary.get('oldest_overdue_days', 0)} days past due.",
                cls="demo-result-lead",
            ),
            Div(
                Div(Span(_demo_money(summary.get("total_outstanding", 0)), cls="demo-metric-value"),
                    Span("Outstanding balance", cls="demo-metric-label"), cls="demo-metric"),
                Div(Span(str(summary.get("total_invoices", len(invoices))), cls="demo-metric-value"),
                    Span("Invoices reviewed", cls="demo-metric-label"), cls="demo-metric"),
                Div(Span(str(summary.get("total_overdue", 0)), cls="demo-metric-value"),
                    Span("Overdue invoices", cls="demo-metric-label"), cls="demo-metric"),
                cls="demo-metrics",
            ),
            H4("Overdue by age", cls="demo-result-title"),
            Div(
                *[
                    Div(
                        Span(label),
                        Span(
                            f"{(aging.get(key) or {}).get('count', 0)} invoices · "
                            f"{_demo_money((aging.get(key) or {}).get('total', 0))}",
                            cls="demo-aging-value",
                        ),
                        cls="demo-aging-row",
                    )
                    for key, label in buckets
                    if (aging.get(key) or {}).get("count", 0)
                ],
                cls="demo-aging-list",
            ),
            H4("Customers to follow up", cls="demo-result-title"),
            Div(
                *[
                    Div(
                        Div(
                            Strong(str(invoice.get("customer") or "Customer")),
                            Span(_demo_money(invoice.get("outstanding_amount", 0)),
                                 cls="demo-invoice-amount"),
                            cls="demo-invoice-heading",
                        ),
                        P(
                            f"Invoice {invoice.get('invoice_number', '—')} · "
                            f"{invoice.get('days_overdue', 0)} days overdue",
                            cls="demo-invoice-meta",
                        ),
                        P(
                            f"Suggested next step: {follow_up}. No message has been sent.",
                            cls="demo-invoice-next",
                        ),
                        cls="demo-human-invoice",
                    )
                    for invoice in invoices[:8]
                ] or [P("No invoices were returned for this run.", cls="demo-result-copy")],
                cls="demo-human-invoices",
            ),
            cls="demo-human-result",
        )

    if task_type == "books_to_tally":
        summary = output.get("summary") or {}
        reconciliation = output.get("reconciliation") or {}
        entries = output.get("staged_entries") or []
        count = summary.get("total_entries", output.get("staged_count", len(entries)))
        balanced = summary.get("balanced", reconciliation.get("is_balanced"))
        unmapped = summary.get(
            "unmapped_count", len(reconciliation.get("unmapped_accounts") or [])
        )
        return Div(
            P(
                f"Reviewed {count} journal "
                f"{'entry' if count == 1 else 'entries'} for "
                f"{summary.get('period', output.get('period', 'the selected period'))}.",
                cls="demo-result-lead",
            ),
            Div(
                Div(Span(str(count), cls="demo-metric-value"),
                    Span("Journal entries", cls="demo-metric-label"), cls="demo-metric"),
                Div(Span(_demo_money(summary.get("total_debit", 0)), cls="demo-metric-value"),
                    Span("Debits", cls="demo-metric-label"), cls="demo-metric"),
                Div(Span(_demo_money(summary.get("total_credit", 0)), cls="demo-metric-value"),
                    Span("Credits", cls="demo-metric-label"), cls="demo-metric"),
                cls="demo-metrics",
            ),
            Div(
                Strong("Reconciliation: "),
                Span(
                    "Balanced" if balanced else "Needs review" if balanced is not None else "Not available",
                    cls="demo-result-emphasis",
                ),
                Span(f"{unmapped} account mappings need review." if unmapped else "All accounts were mapped."),
                cls="demo-result-callout",
            ),
            P(
                "Entries are staged for accountant review. Nothing has been posted to Tally.",
                cls="demo-result-hint",
            ),
            cls="demo-human-result",
        )

    if task_type == "gsm_command":
        branches = output.get("branches") or {}
        branch_rows = [
            Div(
                Strong(str(branch)),
                Span(
                    f"{data.get('dealer_count', 0)} dealers · "
                    f"{data.get('overdue_count', 0)} with overdue balances · "
                    f"{_demo_money(data.get('total_outstanding', 0))} outstanding",
                    cls="demo-branch-meta",
                ),
                cls="demo-branch-row",
            )
            for branch, data in branches.items()
        ]
        return Div(
            P(
                f"Territory overview for {output.get('entity', 'the selected business')} "
                f"as of {output.get('as_of', 'today')}.",
                cls="demo-result-lead",
            ),
            Div(
                Div(Span(str(output.get("total_dealers", 0)), cls="demo-metric-value"),
                    Span("Dealers in portfolio", cls="demo-metric-label"), cls="demo-metric"),
                Div(Span(_demo_money(output.get("total_outstanding", 0)), cls="demo-metric-value"),
                    Span("Outstanding balance", cls="demo-metric-label"), cls="demo-metric"),
                cls="demo-metrics",
            ),
            H4("By branch", cls="demo-result-title"),
            Div(
                *(branch_rows if branch_rows else [
                    P(
                        "No branch-level dealer data was available for this run.",
                        cls="demo-result-copy",
                    )
                ]),
                cls="demo-branch-list",
            ),
            P(
                "This is a read-only management summary; it does not change business records.",
                cls="demo-result-hint",
            ),
            cls="demo-human-result",
        )

    if task_type == "purchase_request":
        items = output.get("items") or (output.get("purchase_order") or {}).get("items") or []
        total = output.get("total_amount", output.get("total"))
        po_number = output.get("po_number") or (output.get("purchase_order") or {}).get("po_number")
        approval_required = output.get("approval_required") or output.get("status") == "NEEDS_APPROVAL"
        line_items = [
            Div(
                Strong(str(item.get("item") or item.get("description") or "Requested item")),
                Span(
                    f"{item.get('quantity', 1)} × {_demo_money(item.get('unit_price', 0))}",
                    cls="demo-branch-meta",
                ),
                cls="demo-branch-row",
            )
            for item in items if isinstance(item, dict)
        ]
        return Div(
            P(
                "Purchase order created." if po_number else (
                    "This request needs approval before an order can be created."
                    if approval_required else "Purchase request reviewed."
                ),
                cls="demo-result-lead",
            ),
            Div(
                Div(Span(str(po_number or "Pending"), cls="demo-metric-value"),
                    Span("Purchase order", cls="demo-metric-label"), cls="demo-metric"),
                Div(Span(_demo_money(total), cls="demo-metric-value"),
                    Span("Request total", cls="demo-metric-label"), cls="demo-metric"),
                cls="demo-metrics",
            ),
            Div(
                Strong("Supplier: "),
                Span(str(output.get("vendor") or "Not specified")),
                cls="demo-result-callout",
            ),
            H4("Requested items", cls="demo-result-title"),
            Div(*line_items, cls="demo-branch-list"),
            P(
                str(output.get("reason") or output.get("warning") or
                    "Review the purchase request and its approval status before taking the next step."),
                cls="demo-result-hint",
            ),
            cls="demo-human-result",
        )

    lines = []
    for k, v in output.items():
        if not isinstance(v, (list, dict)):
            lines.append(Div(Strong(k.replace("_", " ").capitalize() + ": "), Span(str(v)[:160])))
    return Div(*lines) if lines else None


@router.post("/htmx/demo/run/{scenario_id}", include_in_schema=False)
def htmx_demo_run(scenario_id: str, request: Request, db: Session = Depends(get_db)):
    from app.api.demo import SCENARIOS, _run_scenario
    from fastapi import HTTPException as _HTTPException
    actor = get_current_user(request)
    if not actor or not actor.can_run_demo(scenario_id):
        return _html(Notice("You don't have permission to run this scenario.", "err"))
    scenario_map = {s["id"]: s for s in SCENARIOS}
    scenario = scenario_map.get(scenario_id)
    if not scenario:
        return _html(Notice(f"Unknown scenario: {scenario_id}", "err"))
    try:
        data = _run_scenario(scenario_id, db, requested_by=actor.username)
    except _HTTPException as exc:
        return _html(Notice(f"Error: {exc.detail}", "err"))
    except Exception as exc:
        return _html(Notice(str(exc), "err"))
    task_id = data.get("task_id", "")
    log_activity(db, actor, "demo_run",
                 resource_type="scenario", resource_id=scenario_id,
                 detail={"task_id": task_id},
                 ip=request.client.host if request.client else None)
    zoho_snap = data.get("zoho_snapshot") or {}
    return _html(
        _demo_output_panel(
            scenario_name=scenario["name"],
            task_type=scenario["task_type"],
            status="QUEUED",
            task_id=task_id,
            zoho_snap=zoho_snap,
            result_html=None,
            ledger_entries=[],
            approval_info=None,
            poll=True,
        )
    )


@router.get("/htmx/demo/poll/{task_id}", include_in_schema=False)
def htmx_demo_poll(task_id: str, db: Session = Depends(get_db)):
    import uuid as _uuid
    from app.models.action_ledger import ActionLedger
    from app.api.demo import SCENARIOS
    try:
        tid = _uuid.UUID(task_id)
    except ValueError:
        return _html(Notice(f"Bad task_id: {task_id}", "err"))
    task = TaskRepository(db).get(tid)
    if task is None:
        return _html(Notice("Task not found.", "err"))
    ledger_rows = (
        db.query(ActionLedger)
        .filter(ActionLedger.task_id == tid)
        .order_by(ActionLedger.created_at)
        .all()
    )
    ledger = [
        {
            "action": e.action,
            "status": e.status,
            "tool": e.tool_name,
            "approval_status": e.approval_status,
            "ts": e.created_at.isoformat() if e.created_at else None,
        }
        for e in ledger_rows
    ]
    approval_info = None
    if task.status == "AWAITING_APPROVAL":
        pending = ApprovalRepository(db).get_pending_by_task(task.id)
        if pending:
            approval_info = {
                "approval_id": str(pending.id),
                "approval_type": pending.approval_type,
                "payload": pending.payload,
            }
    status = task.status
    task_type = task.task_type or ""
    still_running = status in ("QUEUED", "RUNNING")
    result_html = None if still_running else _format_result(task.result, task_type)
    scenario_name = next(
        (s["name"] for s in SCENARIOS if s["task_type"] == task_type), task_type
    )
    return _html(
        _demo_output_panel(
            scenario_name=scenario_name,
            task_type=task_type,
            status=status,
            task_id=task_id,
            zoho_snap={},
            result_html=result_html,
            ledger_entries=ledger,
            approval_info=approval_info,
            poll=still_running or status == "AWAITING_APPROVAL",
        )
    )


@router.post("/htmx/demo/approve/{approval_id}", include_in_schema=False)
def htmx_demo_approve(approval_id: str, db: Session = Depends(get_db)):
    import uuid as _uuid
    try:
        aid = _uuid.UUID(approval_id)
    except ValueError:
        return _html(Notice(f"Bad approval_id: {approval_id}", "err"))
    approval_repo = ApprovalRepository(db)
    approval = approval_repo.get(aid)
    if approval is None:
        return _html(Notice("Approval not found.", "err"))
    if approval.status != "PENDING":
        return _html(Notice(f"Approval is {approval.status}, not PENDING.", "err"))
    approval_repo.approve(approval, approved_by="admin", reason="Demo approval")
    task = TaskRepository(db).get(approval.task_id)
    task_id = ""
    if task and task.status == "AWAITING_APPROVAL":
        task.status = "QUEUED"
        db.commit()
        task_queue.put(task_id=task.id, priority=5)
        task_id = str(task.id)
    else:
        db.commit()
    return _html(
        Div(
            Span("✓ Approved — task re-queued", cls="badge ok"),
            Div(id="demo-poll",
                hx_get=f"/htmx/demo/poll/{task_id}",
                hx_trigger="every 2s",
                hx_target="#demo-output",
                hx_swap="innerHTML") if task_id else None,
        )
    )


@router.post("/htmx/demo/reject/{approval_id}", include_in_schema=False)
def htmx_demo_reject(approval_id: str, db: Session = Depends(get_db)):
    import uuid as _uuid
    try:
        aid = _uuid.UUID(approval_id)
    except ValueError:
        return _html(Notice(f"Bad approval_id: {approval_id}", "err"))
    approval_repo = ApprovalRepository(db)
    approval = approval_repo.get(aid)
    if approval is None:
        return _html(Notice("Approval not found.", "err"))
    if approval.status == "PENDING":
        approval_repo.reject(approval, approved_by="demo_user", reason="Demo rejection")
        if approval.task_id:
            task = TaskRepository(db).get(approval.task_id)
            if task and task.status == "AWAITING_APPROVAL":
                TaskRepository(db).reject_task(task, "Demo rejection")
        db.commit()
    return _html(Span("✗ Rejected", cls="badge danger"))


# ===========================================================================
# LOGIN PAGE
# ===========================================================================

def _login_page() -> Html:
    return Html(
        Head(
            Meta(charset="utf-8"),
            Meta(name="viewport", content="width=device-width, initial-scale=1"),
            Title("Login — Yexis Control Room"),
            Link(rel="stylesheet", href="/assets/styles.css"),
            Style("""
.login-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:var(--bg)}
.login-card{background:var(--bg-2);border:1px solid var(--panel-border);border-radius:14px;
  padding:2.5rem 2rem;width:100%;max-width:380px;box-shadow:0 8px 40px #0008}
.login-card h1{margin:0 0 .25rem;font-size:1.4rem}
.login-card .sub{color:var(--muted);font-size:.85rem;margin-bottom:2rem}
.login-card label{display:flex;flex-direction:column;gap:.35rem;font-size:.82rem;
  color:var(--muted);margin-bottom:1rem}
.login-card input{background:var(--bg);border:1px solid var(--panel-border);border-radius:6px;
  padding:.55rem .75rem;color:var(--text);font-size:.9rem;outline:none}
.login-card input:focus{border-color:var(--accent)}
.pw-wrap{position:relative;display:flex;align-items:center}
.pw-eye{position:absolute;right:.5rem;top:50%;transform:translateY(-50%);background:none;
  border:none;cursor:pointer;font-size:1rem;padding:.1rem .25rem;line-height:1}
.login-card .err{color:#f87171;font-size:.82rem;margin-bottom:.75rem;display:none}
.login-card .err.visible{display:block}
.login-btn{width:100%;margin-top:.5rem}
"""),
        ),
        Body(
            Div(
                Div(
                    H1("Yexis Control Room"),
                    P("Sign in to your account", cls="sub"),
                    FtForm(
                        Label("Email or username",
                              Input(name="username", type="text",
                                    placeholder="you@company.com or admin",
                                    autocomplete="username", required=True)),
                        Label("Password",
                              Div(
                                  Input(name="password", type="password", id="login-pw",
                                        placeholder="••••••••", autocomplete="current-password",
                                        required=True, style="width:100%;padding-right:2.4rem"),
                                  Button("👁", type="button", tabindex="-1",
                                         cls="pw-eye",
                                         onclick="var i=document.getElementById('login-pw');"
                                                 "i.type=i.type==='password'?'text':'password';"
                                                 "this.textContent=i.type==='password'?'👁':'🙈';"),
                                  cls="pw-wrap",
                              )),
                        Div(id="login-err", cls="err"),
                        Button("Sign in", type="submit", cls="primary login-btn"),
                        method="post",
                        action="/auth/login",
                        hx_post="/auth/login",
                        hx_target="#login-err",
                        hx_swap="innerHTML",
                        hx_on__htmx_response_error="document.getElementById('login-err').classList.add('visible');document.getElementById('login-err').textContent=event.detail.xhr.responseText",
                    ),
                    P(A("First time here? Verify your email →", href="/verify"),
                      cls="sub", style="margin-top:1rem;text-align:center"),
                    cls="login-card",
                ),
                cls="login-wrap",
            ),
        ),
    )


def _verify_page(email: str = "") -> Html:
    return Html(
        Head(
            Meta(charset="utf-8"),
            Meta(name="viewport", content="width=device-width, initial-scale=1"),
            Title("Verify email — Yexis Control Room"),
            Link(rel="stylesheet", href="/assets/styles.css"),
            Style("""
.login-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;background:var(--bg)}
.login-card{background:var(--bg-2);border:1px solid var(--panel-border);border-radius:14px;
  padding:2.5rem 2rem;width:100%;max-width:400px;box-shadow:0 8px 40px #0008}
.login-card h1{margin:0 0 .25rem;font-size:1.35rem}
.login-card .sub{color:var(--muted);font-size:.85rem;margin-bottom:1.5rem}
.login-card label{display:flex;flex-direction:column;gap:.35rem;font-size:.82rem;color:var(--muted);margin-bottom:1rem}
.login-card input{background:var(--bg);border:1px solid var(--panel-border);border-radius:6px;
  padding:.55rem .75rem;color:var(--text);font-size:.9rem;outline:none}
.login-card input:focus{border-color:var(--accent)}
.pw-wrap{position:relative;display:flex;align-items:center}
.pw-eye{position:absolute;right:.5rem;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;font-size:1rem}
.login-btn{width:100%;margin-top:.5rem}
.msg{font-size:.83rem;margin:.5rem 0;min-height:1.1rem}
.msg.ok{color:var(--ok)} .msg.err{color:#f87171}
"""),
        ),
        Body(
            Div(
                Div(
                    H1("Verify your email"),
                    P("Enter the 6-digit code we emailed you, or click the link in that email.",
                      cls="sub"),
                    Label("Email",
                          Input(id="v-email", type="email", value=email,
                                placeholder="you@company.com")),
                    Label("6-digit code",
                          Input(id="v-code", type="text", inputmode="numeric",
                                maxlength="6", placeholder="123456")),
                    Label("Set a password (leave blank if you already have one)",
                          Div(
                              Input(id="v-pw", type="password", placeholder="••••••••",
                                    style="width:100%;padding-right:2.4rem"),
                              Button("👁", type="button", tabindex="-1", cls="pw-eye",
                                     onclick="var i=document.getElementById('v-pw');"
                                             "i.type=i.type==='password'?'text':'password';"
                                             "this.textContent=i.type==='password'?'👁':'🙈';"),
                              cls="pw-wrap",
                          )),
                    Div("", id="v-msg", cls="msg"),
                    Button("Verify", type="button", cls="primary login-btn", id="v-submit"),
                    P(A("Resend code", href="#", id="v-resend"), " · ",
                      A("Back to sign in", href="/login"),
                      cls="sub", style="margin-top:1rem;text-align:center"),
                    cls="login-card",
                ),
                cls="login-wrap",
            ),
            Script("""
(function(){
  var msg=document.getElementById('v-msg');
  function show(t,ok){msg.textContent=t;msg.className='msg '+(ok?'ok':'err');}
  document.getElementById('v-submit').addEventListener('click',async function(){
    var email=document.getElementById('v-email').value.trim();
    var code=document.getElementById('v-code').value.trim();
    var pw=document.getElementById('v-pw').value;
    if(!email||!code){show('Enter your email and the 6-digit code.',false);return;}
    show('Verifying…',true);
    try{
      var r=await fetch('/auth/verify-email',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({email:email,code:code,password:pw||null})});
      var d=await r.json().catch(function(){return {};});
      if(!r.ok){show(d.detail||'Verification failed.',false);return;}
      show('Verified! Redirecting to sign in…',true);
      setTimeout(function(){location.href='/login?verify=ok';},1200);
    }catch(e){show(e.message,false);}
  });
  document.getElementById('v-resend').addEventListener('click',async function(ev){
    ev.preventDefault();
    var email=document.getElementById('v-email').value.trim();
    if(!email){show('Enter your email first, then resend.',false);return;}
    show('Sending…',true);
    try{
      var r=await fetch('/auth/send-code',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({identifier:email})});
      var d=await r.json().catch(function(){return {};});
      show(r.ok?'A new code and link were emailed.':(d.detail||'Could not send.'),r.ok);
    }catch(e){show(e.message,false);}
  });
})();
"""),
        ),
    )


# ===========================================================================
# USERS SECTION
# ===========================================================================

def _users_shell(user: "CurrentUser | None" = None):
    return Section(
        PanelHead(
            "Users & Roles",
            tools=Button(
                "+ New user",
                id="new-user-btn",
                cls="ghost",
                hx_get="/htmx/users/new-form",
                hx_target="#users-new-row",
                hx_swap="innerHTML",
            ),
        ),
        Div(id="users-new-row"),
        Div(
            id="users-table",
            hx_get="/htmx/users",
            hx_trigger=f"{_AUTO}, roles-updated from:body",
            hx_swap="innerHTML",
        ),
        _role_admin_shell() if user and user.role == "admin" else None,
        id="users",
        cls="panel",
        style="grid-column:1/-1",
    )


def _role_admin_shell():
    return Section(
        PanelHead(
            "Role permissions",
            "Control who can view, edit, and run each area. Admin is a protected superuser role.",
        ),
        FtForm(
            Label("New role", Input(name="name", required=True, placeholder="e.g. sales_manager")),
            Button("Create role", type="submit", cls="primary"),
            hx_post="/htmx/roles",
            hx_target="#role-admin-message",
            hx_swap="innerHTML",
            cls="inline",
        ),
        Div(id="role-admin-message"),
        Div(
            id="roles-panel",
            hx_get="/htmx/roles",
            hx_trigger="load, roles-updated from:body",
            hx_swap="innerHTML",
        ),
        id="role-permissions",
        cls="panel",
        style="grid-column:1/-1",
    )


@router.get("/htmx/roles", include_in_schema=False)
def htmx_roles(request: Request, role: str = "", db: Session = Depends(get_db)):
    actor = get_current_user(request)
    if not actor or actor.role != "admin":
        return _html(Notice("Admin only", "err"))
    from app.models.access_control import RoleDefinition
    roles = db.query(RoleDefinition).order_by(RoleDefinition.name).all()
    if not roles:
        return _html(Notice("No roles are configured.", "err"))
    selected = next(
        (item for item in roles if item.name == role),
        next((item for item in roles if item.name not in {"admin", "system"}), roles[0]),
    )
    return _html(Div(
        Label(
            "Choose role",
            Select(
                *[
                    Option(item.name, value=item.name, selected=item.name == selected.name)
                    for item in roles
                ],
                name="role",
                hx_get="/htmx/roles/editor",
                hx_target="#role-editor",
                hx_swap="innerHTML",
                hx_trigger="change",
                hx_include="this",
            ),
        ),
        Div(_role_permissions_editor(db, selected), id="role-editor"),
        cls="stack",
    ))


@router.get("/htmx/roles/editor", include_in_schema=False)
def htmx_role_editor(request: Request, role: str, db: Session = Depends(get_db)):
    actor = get_current_user(request)
    if not actor or actor.role != "admin":
        return _html(Notice("Admin only", "err"))
    from app.models.access_control import RoleDefinition
    role_row = db.get(RoleDefinition, role)
    if role_row is None:
        return _html(Notice("Role not found.", "err"))
    return _html(_role_permissions_editor(db, role_row))


def _role_permissions_editor(db: Session, role):
    from app.services.role_access import PERMISSION_LABELS, get_role_grants
    grants = get_role_grants(db, role.name)
    if role.name in {"admin", "system"}:
        return P(
            "This built-in role is protected and cannot be edited or deleted."
            if role.name == "admin" else
            "System role has no interactive permissions and cannot be edited or deleted.",
            cls="muted",
        )

    rows = []
    for section, label in PERMISSION_LABELS.items():
        checks = []
        for action in ("view", "edit", "run"):
            available = (
                action == "run" and section.startswith("demo:")
                or action == "view" and section == "demo"
                or action in {"view", "edit"} and not section.startswith("demo:")
            )
            checks.append(
                Td(
                    Input(
                        type="checkbox",
                        name=f"{section}:{action}",
                        value="on",
                        checked=(section, action) in grants,
                        disabled=not available,
                    ),
                    style="text-align:center",
                )
            )
        rows.append(Tr(Td(label), *checks))
    return Div(
        FtForm(
            Input(type="hidden", name="role", value=role.name),
            Table(
                Thead(Tr(Th("Area / action"), Th("View"), Th("Edit"), Th("Run"))),
                Tbody(*rows),
                cls="data-table",
            ),
            Div(
                Button("Save permissions", type="submit", cls="primary"),
                Button(
                    "Delete role",
                    type="button",
                    cls="ghost",
                    hx_delete=f"/htmx/roles/{role.name}",
                    hx_target="#role-admin-message",
                    hx_swap="innerHTML",
                    hx_confirm=f"Delete role '{role.name}'? Users must be reassigned first.",
                ),
                cls="actions-row",
            ),
            hx_post=f"/htmx/roles/{role.name}/permissions",
            hx_target="#role-editor",
            hx_swap="innerHTML",
        ),
        P(
            "Edit and run grants require View. The Run column is available for individual live scenarios.",
            cls="muted",
        ),
    )


@router.post("/htmx/roles", include_in_schema=False)
async def htmx_create_role(request: Request, db: Session = Depends(get_db)):
    actor = get_current_user(request)
    if not actor or actor.role != "admin":
        return _html(Notice("Admin only", "err"))
    from app.services.role_access import create_role
    form = await request.form()
    try:
        role = create_role(db, str(form.get("name") or ""))
    except ValueError as exc:
        return _html(Notice(str(exc), "err"))
    log_activity(db, actor, "role_create", resource_type="role", resource_id=role.name)
    response = _html(
        Notice(f"Role '{role.name}' created. It has no permissions until you grant them.", "ok")
    )
    response.headers["HX-Trigger"] = "roles-updated"
    return response


@router.post("/htmx/roles/{role}/permissions", include_in_schema=False)
async def htmx_save_role_permissions(
    role: str,
    request: Request,
    db: Session = Depends(get_db),
):
    actor = get_current_user(request)
    if not actor or actor.role != "admin":
        return _html(Notice("Admin only", "err"))
    from app.models.access_control import RoleDefinition
    from app.services.role_access import set_role_grants
    role_row = db.get(RoleDefinition, role)
    if role_row is None:
        return _html(Notice("Role not found.", "err"))
    form = await request.form()
    grants = set()
    for key, value in form.multi_items():
        if value != "on" or ":" not in key:
            continue
        section, action = key.rsplit(":", 1)
        grants.add((section, action))
    try:
        set_role_grants(db, role, grants)
    except (LookupError, ValueError) as exc:
        return _html(Notice(str(exc), "err"))
    log_activity(
        db, actor, "role_permissions_update", resource_type="role",
        resource_id=role, detail={"grant_count": len(grants)},
    )
    return _html(Div(Notice("Permissions saved.", "ok"), _role_permissions_editor(db, role_row)))


@router.delete("/htmx/roles/{role}", include_in_schema=False)
def htmx_delete_role(role: str, request: Request, db: Session = Depends(get_db)):
    actor = get_current_user(request)
    if not actor or actor.role != "admin":
        return _html(Notice("Admin only", "err"))
    from app.services.role_access import delete_role
    try:
        delete_role(db, role)
    except (LookupError, ValueError) as exc:
        return _html(Notice(str(exc), "err"))
    log_activity(db, actor, "role_delete", resource_type="role", resource_id=role)
    response = _html(Notice(f"Role '{role}' deleted.", "ok"))
    response.headers["HX-Trigger"] = "roles-updated"
    return response


def _integrations_shell(user: "CurrentUser"):
    return Section(
        PanelHead(
            "Integrations",
            "Connect your own Zoho account. Other providers use shared administrator-managed settings.",
            tools=Button(
                "Refresh",
                cls="ghost",
                hx_get="/htmx/integrations",
                hx_target="#integrations-content",
                hx_swap="innerHTML",
            ),
        ),
        Div(
            id="integrations-content",
            hx_get="/htmx/integrations",
            hx_trigger=_AUTO,
            hx_swap="innerHTML",
        ),
        id="integrations",
        cls="panel span-2",
    )


@router.get("/htmx/integrations", include_in_schema=False)
def htmx_integrations(request: Request):
    actor = get_current_user(request)
    if not actor or not actor.can("integrations"):
        return _html(Notice("Access denied", "err"))

    from app.core.config import settings
    from app.integrations.zoho_books import get_zoho_client
    from app.services.zoho_connections import user_zoho_status

    personal_zoho = user_zoho_status(actor.username)
    shared_client = get_zoho_client()
    shared_zoho = shared_client.configured
    can_connect_personal = bool(
        settings.zoho_client_id
        and settings.zoho_client_secret
        and settings.zoho_token_encryption_key
    )
    zoho_label = (
        "Personal account connected" if personal_zoho.get("connected")
        else "Using shared .env fallback" if shared_zoho
        else "Not connected"
    )
    zoho_status_cls = "ok" if personal_zoho.get("connected") or shared_zoho else "warn"
    zoho_org = personal_zoho.get("organization_name") or (
        shared_client.organization_id if shared_zoho else ""
    )
    zoho_actions = (
        Button(
            "Disconnect personal account",
            cls="ghost",
            hx_post="/htmx/integrations/zoho/disconnect",
            hx_target="#integrations-content",
            hx_swap="innerHTML",
            hx_confirm="Disconnect your personal Zoho account? Tasks will use the shared .env connection if configured.",
        )
        if personal_zoho.get("connected")
        else A(
            "Connect Zoho",
            href="/auth/zoho/user/connect",
            target="_blank",
            rel="noreferrer",
            cls="primary",
        )
        if can_connect_personal
        else Span("Personal connection setup required", cls="muted")
    )
    zoho_card = _integration_card(
        "Zoho Books + CRM",
        zoho_label,
        zoho_status_cls,
        "Use your own Zoho organization for work you submit. Without a personal connection, the platform falls back to the shared server configuration.",
        "Personal OAuth · shared fallback available",
        zoho_org,
        zoho_actions,
        config_hint=(
            "ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_TOKEN_ENCRYPTION_KEY"
            if actor.role == "admin" else "Ask your administrator to enable personal Zoho connections."
        ),
    )

    integrations = [
        ("Tally Prime", bool(settings.tally_host), "TALLY_HOST, TALLY_PORT", "Shared accounting server connection."),
        ("Email inbox (POP3)", bool(settings.pop3_host and settings.pop3_username and settings.pop3_password), "POP3_HOST, POP3_USERNAME, POP3_PASSWORD", "Shared mailbox for receiving and reviewing email."),
        ("Email sending (SMTP)", bool(settings.smtp_host and settings.smtp_username and settings.smtp_password and settings.smtp_from), "SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM", "Shared sender. Sending is additionally controlled by MAIL_SEND_ENABLED and approval policy."),
        ("WhatsApp", bool(settings.waba_provider and settings.waba_api_key and settings.waba_api_url), "WABA_PROVIDER, WABA_API_KEY, WABA_API_URL", "Shared WhatsApp Business provider."),
        ("Telegram", bool(settings.telegram_bot_token), "TELEGRAM_BOT_TOKEN", "Shared bot connection."),
        ("Apollo", bool(settings.apollo_api_key), "APOLLO_API_KEY", "Shared sales-enrichment account."),
        ("RocketReach", bool(settings.rocketreach_api_key), "ROCKETREACH_API_KEY", "Shared contact-enrichment account."),
        ("Voice / IVR", bool(settings.voice_provider and settings.voice_api_key), "VOICE_PROVIDER, VOICE_API_KEY", "Shared voice provider."),
        ("Firebase push", bool(settings.fcm_project_id and (settings.fcm_service_account_json or settings.fcm_server_key)), "FCM_PROJECT_ID and FCM_SERVICE_ACCOUNT_JSON (or FCM_SERVER_KEY)", "Shared push-notification project."),
        ("Zoho MCP", bool(settings.zoho_mcp_server_url or settings.zoho_mcp_server_url_yexis or settings.zoho_mcp_server_url_agent), "ZOHO_MCP_SERVER_URL_YEXIS and ZOHO_MCP_SERVER_URL_AGENT", "Shared Zoho MCP tools; separate from the personal Books / CRM OAuth connection."),
    ]
    cards = [
        _integration_card(
            name,
            (
                "Configured · sending disabled"
                if name == "Email sending (SMTP)" and configured and not settings.mail_send_enabled
                else "Configured in server .env" if configured
                else "Needs administrator setup"
            ),
            "ok" if configured else "warn",
            description,
            "Shared · administrator managed",
            "",
            None,
            config_hint=hint if actor.role == "admin" else "Ask your administrator to configure this provider.",
        )
        for name, configured, hint, description in integrations
    ]
    return _html(
        Div(
            P(
                "Provider status reflects saved configuration only; it does not claim the remote service is reachable.",
                cls="muted integration-note",
            ),
            Div(zoho_card, *cards, cls="integration-grid"),
        )
    )


@router.post("/htmx/integrations/zoho/disconnect", include_in_schema=False)
def htmx_disconnect_user_zoho(request: Request):
    actor = get_current_user(request)
    if not actor or not actor.can("integrations"):
        return _html(Notice("Access denied", "err"))
    from app.services.zoho_connections import disconnect_user_zoho
    disconnect_user_zoho(actor.username)
    return htmx_integrations(request)


def _integration_card(
    name: str,
    status: str,
    status_class: str,
    description: str,
    ownership: str,
    detail: str,
    actions=None,
    config_hint: str = "",
):
    return Section(
        Div(
            H3(name),
            Span(status, cls=f"integration-status {status_class}"),
            cls="integration-card-head",
        ),
        P(description, cls="integration-description"),
        P(ownership, cls="integration-owner"),
        P(f"Organization: {detail}" if detail else "", cls="integration-detail") if detail else None,
        P(config_hint, cls="integration-detail") if config_hint else None,
        Div(actions, cls="integration-actions") if actions else None,
        cls="integration-card",
    )


@router.get("/htmx/users", include_in_schema=False)
def htmx_users(request: Request, page: int = 1, db: Session = Depends(get_db)):
    actor = get_current_user(request)
    if not actor or not actor.can("users"):
        return _html(Notice("Access denied", "err"))
    from app.models.user import User
    from app.models.access_control import RoleDefinition
    from sqlalchemy import func
    role_names = list(
        db.query(RoleDefinition.name)
        .filter(RoleDefinition.name != "system")
        .order_by(RoleDefinition.name)
        .all()
    )
    role_names = [row[0] for row in role_names]
    total = db.query(func.count(User.id)).scalar() or 0
    if not total:
        return _html(Notice("No users found.", ""))
    page, page_count, offset = _page_window(page, total)
    users = db.query(User).order_by(User.username.asc(), User.id.asc()).offset(offset).limit(_LIST_PAGE_SIZE).all()
    from app.ui.layout import _ROLE_COLOR
    rows = []
    for u in users:
        rc = _ROLE_COLOR.get(u.role or "viewer", "#94a3b8")
        role_badge = Span(
            u.role or "viewer",
            style=f"font-size:.72rem;padding:.15rem .5rem;border-radius:999px;"
                  f"background:{rc}22;color:{rc};border:1px solid {rc}55",
        )
        last_login = u.last_login_at.strftime("%d %b %Y %H:%M") if u.last_login_at else "—"
        pw_set = "✓" if u.password_hash else Span("not set", style="color:#f87171;font-size:.8rem")
        actions = []
        if actor.role == "admin":
            actions.append(
                Select(
                    *[Option(r, value=r, selected=(r == u.role)) for r in role_names],
                    hx_put=f"/auth/users/{u.id}/role",
                    hx_vals=f'{{"role":"__value__"}}',
                    hx_target=f"#user-row-{u.id}",
                    hx_swap="outerHTML",
                    onchange="this.setAttribute('hx-vals',JSON.stringify({role:this.value}));htmx.trigger(this,'change')",
                    style="font-size:.78rem;padding:.2rem .35rem;background:var(--bg);color:var(--text);"
                          "border:1px solid var(--panel-border);border-radius:4px",
                )
            )
        rows.append(
            Tr(
                Td(u.username),
                Td(u.display_name or "—", cls="muted"),
                Td(role_badge),
                Td(last_login, cls="muted"),
                Td(pw_set),
                Td(*actions),
                id=f"user-row-{u.id}",
            )
        )
    return _html(
        Div(
            Table(
                Thead(Tr(
                    Th("Username"), Th("Display name"), Th("Role"),
                    Th("Last login"), Th("Password"), Th(""),
                )),
                Tbody(*rows),
                cls="data-table",
            ),
            _page_controls("/htmx/users", page, page_count, total, "#users-table"),
        )
    )


@router.get("/htmx/users/new-form", include_in_schema=False)
def htmx_users_new_form(request: Request, db: Session = Depends(get_db)):
    actor = get_current_user(request)
    if not actor or actor.role != "admin":
        return _html(Notice("Admin only", "err"))
    from app.models.access_control import RoleDefinition
    roles = (
        db.query(RoleDefinition.name)
        .filter(RoleDefinition.name != "system")
        .order_by(RoleDefinition.name)
        .all()
    )
    return _html(
        FtForm(
            Div(
                Input(name="username", placeholder="username (optional — defaults to email)",
                      style="flex:1"),
                Input(name="email", type="email", placeholder="work email (verified)",
                      style="flex:1"),
                Input(name="display_name", placeholder="Display Name",
                      style="flex:1"),
                Select(
                    *[Option(row[0], value=row[0]) for row in roles],
                    name="role",
                ),
                Input(name="password", type="password", placeholder="temp password (optional)",
                      style="flex:1"),
                Button("Create", type="submit", cls="primary"),
                style="display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;padding:1rem 0",
            ),
            hx_post="/htmx/users/create",
            hx_target="#users-new-row",
            hx_swap="innerHTML",
        )
    )


@router.post("/htmx/users/create", include_in_schema=False)
async def htmx_users_create(
    request: Request,
    db: Session = Depends(get_db),
):
    actor = get_current_user(request)
    if not actor or actor.role != "admin":
        return _html(Notice("Admin only", "err"))
    form = await request.form()
    username = (form.get("username") or "").strip()
    email_raw = (form.get("email") or "").strip()
    display_name = (form.get("display_name") or "").strip()
    role = form.get("role") or "viewer"
    password = form.get("password") or ""
    from app.models.access_control import RoleDefinition
    if db.get(RoleDefinition, role) is None:
        return _html(Notice("Choose an existing role.", "err"))
    if role == "system":
        return _html(Notice("The system role is reserved for the gateway service account.", "err"))

    from app.models.user import User
    from app.core.validation import validate_email
    from app.core.config import settings

    # Validate the email (real domain, not disposable/personal per policy).
    email = None
    if email_raw:
        from app.api.users import _allowed_domains
        try:
            email = validate_email(email_raw, allowed_domains=_allowed_domains())
        except ValueError as exc:
            return _html(Notice(str(exc), "err"))
        if db.query(User).filter(User.email == email).first():
            return _html(Notice(f"Email '{email}' already in use", "err"))

    # Username optional — default to the email.
    if not username:
        if not email:
            return _html(Notice("Provide a username or an email", "err"))
        username = email
    if db.query(User).filter(User.username == username).first():
        return _html(Notice(f"Username '{username}' already exists", "err"))

    import bcrypt as _bcrypt
    pw_hash = _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode() if password else None
    user = User(
        id=uuid.uuid4(),
        username=username,
        display_name=display_name or username,
        email=email,
        role=role,
        email_verified=False,
        password_hash=pw_hash,
        enabled=True,
    )
    db.add(user)
    db.commit()

    # Send the verification code + link so the new user can prove ownership.
    verify_note = ""
    if email and settings.require_email_verification:
        try:
            from app.services import email_otp
            email_otp.generate_and_send(db, user)
            verify_note = f" — a verification code + link were emailed to {email}"
        except Exception as exc:
            verify_note = f" — but the verification email failed to send ({exc}); resend from /verify"

    log_activity(db, actor, "user_create",
                 resource_type="user", resource_id=username,
                 detail={"role": role, "email": email},
                 ip=request.client.host if request.client else None)
    return _html(
        Div(
            Notice(f"User '{username}' created with role {role}{verify_note}", "ok"),
            # Trigger a reload of the users table
            Div(hx_get="/htmx/users", hx_trigger="load", hx_target="#users-table", hx_swap="innerHTML"),
        )
    )


# ===========================================================================
# ACTIVITY LOG SECTION
# ===========================================================================

def _activity_shell():
    return Section(
        PanelHead("Activity log", "Who did what and when"),
        Div(
            id="activity-content",
            hx_get="/htmx/activity",
            hx_trigger=_AUTO,
            hx_swap="innerHTML",
        ),
        id="activity",
        cls="panel",
        style="grid-column:1/-1",
    )


@router.get("/htmx/activity", include_in_schema=False)
def htmx_activity(request: Request, page: int = 1, db: Session = Depends(get_db)):
    actor = get_current_user(request)
    if not actor or not actor.can("activity"):
        return _html(Notice("Access denied", "err"))
    from app.models.activity_log import ActivityLog
    from app.ui.layout import _ROLE_COLOR
    from sqlalchemy import func
    total = db.query(func.count(ActivityLog.id)).scalar() or 0
    if not total:
        return _html(P("No activity yet.", cls="muted"))
    page, page_count, offset = _page_window(page, total)
    rows_q = (
        db.query(ActivityLog)
        .order_by(ActivityLog.ts.desc(), ActivityLog.id.desc())
        .offset(offset)
        .limit(_LIST_PAGE_SIZE)
        .all()
    )
    rows = []
    for r in rows_q:
        rc = _ROLE_COLOR.get(r.role or "viewer", "#94a3b8")
        role_pill = Span(
            r.role or "—",
            style=f"font-size:.68rem;padding:.12rem .42rem;border-radius:999px;"
                  f"background:{rc}22;color:{rc};border:1px solid {rc}55",
        )
        ts_str = r.ts.strftime("%d %b %Y %H:%M:%S") if r.ts else "—"
        detail_str = ""
        if r.detail:
            try:
                detail_str = json.dumps(r.detail, ensure_ascii=False)[:120]
            except Exception:
                detail_str = str(r.detail)[:120]
        rows.append(Tr(
            Td(ts_str, cls="muted", style="white-space:nowrap;font-size:.8rem"),
            Td(r.username or "—"),
            Td(role_pill),
            Td(r.action, style="font-family:monospace;font-size:.82rem"),
            Td(r.resource_type or "—", cls="muted"),
            Td(r.resource_id or "—", cls="muted", style="font-family:monospace;font-size:.8rem"),
            Td(Span(detail_str, title=detail_str), cls="muted", style="font-size:.78rem;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"),
            Td(r.ip_address or "—", cls="muted", style="font-size:.78rem"),
        ))
    if not rows:
        return _html(P("No activity yet.", cls="muted"))
    return _html(
        Div(
            Table(
                Thead(Tr(
                    Th("Time"), Th("User"), Th("Role"), Th("Action"),
                    Th("Resource"), Th("ID"), Th("Detail"), Th("IP"),
                )),
                Tbody(*rows),
                cls="data-table",
            ),
            _page_controls("/htmx/activity", page, page_count, total, "#activity-content"),
            style="overflow-x:auto",
        )
    )
