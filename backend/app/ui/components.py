"""Shared FastHTML FT component helpers for the control-room UI."""
from __future__ import annotations

from datetime import datetime

from fasthtml.common import *


# ---------------------------------------------------------------------------
# Badges
# ---------------------------------------------------------------------------

def badge_cls(value: str) -> str:
    v = str(value or "").upper()
    if v in {"OK", "GREEN", "HEALTHY", "READY", "APPROVED", "POSTED", "SENT", "ALLOW",
             "SUCCESS", "COMPLETED"}:
        return "badge ok"
    if v in {"WARN", "YELLOW", "PENDING", "STAGED", "AWAITING_APPROVAL",
             "RECEIVED", "QUEUED", "RUNNING"}:
        return "badge warn"
    if v in {"RED", "FAILED", "REJECTED", "POST_FAILED", "DISABLED",
             "VALIDATION_ERROR"}:
        return "badge danger"
    return "badge"


def Badge(value: str) -> Span:
    return Span(str(value or ""), cls=badge_cls(str(value or "")))


# ---------------------------------------------------------------------------
# Date formatting
# ---------------------------------------------------------------------------

def fmt_dt(dt) -> str:
    if dt is None:
        return "-"
    if isinstance(dt, str):
        if not dt or dt in ("None", "null"):
            return "-"
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return str(dt)
    try:
        return dt.strftime("%b %d, %H:%M")
    except (AttributeError, ValueError):
        return str(dt)


# ---------------------------------------------------------------------------
# Notice / alert boxes
# ---------------------------------------------------------------------------

def Notice(msg: str, kind: str = "") -> Div:
    cls = f"notice {kind}".strip() if kind else "notice"
    return Div(str(msg), cls=cls)


# ---------------------------------------------------------------------------
# Panel layout helpers
# ---------------------------------------------------------------------------

def PanelHead(title: str, subtitle: str = "", tools=None) -> Div:
    left = [H3(title)]
    if subtitle:
        left.append(Span(subtitle, cls="muted"))
    left_div = Div(*left)
    return Div(left_div, tools, cls="panel-head") if tools else Div(left_div, cls="panel-head")


def Pill(label: str, value="") -> Span:
    text = f"{label}: {value}" if value != "" else label
    return Span(str(text), cls="pill")


# ---------------------------------------------------------------------------
# Spinner (shown while HTMX loads)
# ---------------------------------------------------------------------------

def Spinner() -> Div:
    return Div(Span("Loading…", cls="muted"), cls="notice")
