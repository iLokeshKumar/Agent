"""Page shell: full HTML document, topbar, nav, footer."""
from __future__ import annotations

from fasthtml.common import *

QUILL_CSS = "https://cdn.jsdelivr.net/npm/quill@2.0.3/dist/quill.snow.css"
QUILL_JS = "https://cdn.jsdelivr.net/npm/quill@2.0.3/dist/quill.js"
HTMX_JS = "https://unpkg.com/htmx.org@1.9.12"
# TinyMCE self-hosted GPL build (no API key / no cloud account needed).
# Used as the rich-text editor for email template bodies.
TINYMCE_JS = "https://cdn.jsdelivr.net/npm/tinymce@7/tinymce.min.js"

_QUILL_OVERRIDES = """
.ql-toolbar{background:rgba(255,255,255,.04);border-color:var(--panel-border)!important;border-radius:6px 6px 0 0}
.ql-container{background:rgba(0,0,0,.2);border-color:var(--panel-border)!important;border-radius:0 0 6px 6px;font-family:inherit;font-size:.83rem;color:var(--text);min-height:180px}
.ql-editor{color:var(--text);min-height:180px;line-height:1.6}
.ql-editor.ql-blank::before{color:var(--muted);font-style:normal}
.ql-snow .ql-stroke{stroke:var(--muted)!important}
.ql-snow .ql-fill{fill:var(--muted)!important}
.ql-snow .ql-picker{color:var(--muted)!important}
.ql-snow .ql-picker-options{background:var(--bg-2)!important;border-color:var(--panel-border)!important}
.ql-snow.ql-toolbar button:hover .ql-stroke,.ql-snow .ql-toolbar button:hover .ql-stroke{stroke:var(--accent)!important}
"""

_ROLE_COLOR = {
    "admin":    "#f59e0b",
    "finance":  "#60a5fa",
    "gsm":      "#a78bfa",
    "purchase": "#34d399",
    "ops":      "#fb923c",
    "viewer":   "#94a3b8",
    "system":   "#6b7280",
}


def Shell(*sections, current_user=None) -> Html:
    """Full page document with ambient blobs, topbar, nav, main grid."""
    visible = [s for s in sections if s is not None]
    return Html(
        Head(
            Meta(charset="utf-8"),
            Meta(name="viewport", content="width=device-width, initial-scale=1"),
            Title("Yexis Control Room"),
            Meta(name="description",
                 content="Operational dashboard for the Yexis AI Agent Platform"),
            Link(rel="stylesheet", href="/assets/styles.css?v=3"),
            Link(rel="stylesheet", href=QUILL_CSS),
            Style(_QUILL_OVERRIDES),
            Script(src=HTMX_JS),
        ),
        Body(
            Div(
                Div(cls="ambient ambient-a"),
                Div(cls="ambient ambient-b"),
                _topbar(current_user),
                _navbar(current_user),
                Main(*visible, cls="grid", id="main-grid"),
                Footer(
                    Span("Waiting for first refresh...", id="last-updated"),
                    Span("Served by the backend at the same origin.", cls="muted"),
                    cls="footer",
                ),
                cls="page-shell",
            ),
            Div(id="toast-host", cls="toast-host",
                aria_live="polite", aria_atomic="true"),
            Script(src="/assets/ui-helpers.js"),
            Script(src="/assets/app.js"),
            Script(src=QUILL_JS),
            Script(src=TINYMCE_JS, referrerpolicy="origin"),
        ),
    )


def _topbar(current_user=None) -> Header:
    actions = []
    if current_user:
        rc = _ROLE_COLOR.get(current_user.role, "#94a3b8")
        actions.append(
            Div(
                Span(current_user.username, style="font-weight:600;font-size:.85rem"),
                Span(current_user.role,
                     style=f"font-size:.72rem;padding:.2rem .55rem;border-radius:999px;"
                           f"background:{rc}22;color:{rc};border:1px solid {rc}55;margin-left:.4rem"),
                style="display:flex;align-items:center;gap:.25rem",
            )
        )
        # Use HTML Form (not FastAPI Form) for logout — no alias conflict here
        actions.append(
            Form(
                Button("Logout", type="submit",
                       cls="ghost",
                       style="font-size:.82rem;color:var(--muted);cursor:pointer"),
                method="post", action="/auth/logout",
            )
        )
    actions += [
        A("API docs", href="/docs", target="_blank", rel="noreferrer", cls="ghost"),
        Button(
            "Refresh",
            id="refresh-all",
            cls="primary",
            onclick="htmx.trigger(document.body, 'ctrl-refresh')",
        ),
    ]
    return Header(
        Div(
            P("Yexis AI Agent Platform", cls="eyebrow"),
            H1("Control Room"),
            P("Supervised operations for tasks, approvals, lanes, Tally, Zoho,"
              " and mailbox intake.", cls="subtitle"),
        ),
        Div(*actions, cls="topbar-actions"),
        cls="topbar",
    )


def _navbar(current_user=None) -> Nav:
    can = current_user.can if current_user else lambda _section: False
    links = []
    if can("demo"):
        links.append(A("▶ Live", href="#demo", style="color:#f59e0b;font-weight:700"))
    links.append(A("Overview", href="#overview"))
    if can("tasks"):
        links.append(A("Tasks", href="#tasks"))
    if can("agents"):
        links.append(A("Agents", href="#agents"))
    if can("approvals"):
        links.append(A("Approvals", href="#approvals"))
    if can("campaigns"):
        links.append(A("Campaigns", href="#campaigns", style="color:#a78bfa;font-weight:600"))
    if can("templates"):
        links.append(A("Templates", href="#templates", style="color:#34d399;font-weight:600"))
    if can("optouts"):
        links.append(A("Opt-outs", href="#optouts", style="color:#f87171;font-weight:600"))
    if can("lanes"):
        links.append(A("Lanes", href="#lanes"))
    if can("tally"):
        links.append(A("Tally", href="#tally"))
    if can("mail"):
        links.append(A("Mail", href="#mail"))
    if can("integrations"):
        links.append(A("Integrations", href="#integrations"))
    if can("users"):
        links.append(A("Users", href="#users", style="color:#f59e0b;font-weight:600"))
    if can("activity"):
        links.append(A("Activity", href="#activity"))
    return Nav(*links, cls="nav")
