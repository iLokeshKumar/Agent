"""Pre-send content validation for campaign templates.

The goal is absolute: nothing incorrect reaches a customer. This module
inspects a template (subject + body) and, when a sample contact is supplied,
renders it exactly as K3 will at send time — then reports every way the
message could go out wrong.

Findings carry a severity:
  blocker — the message would be visibly broken: a leaked {{token}}, a
            merge field that renders blank, broken Jinja, a missing
            attachment, an empty body, or a link that isn't a valid web
            address. K3 refuses to send on any blocker unless an authorised
            caller overrides with a reason.
  warning — likely a mistake but not provably wrong: a field blank only for
            this particular sample contact, an empty subject (which falls
            back to the campaign name).
  info    — advisory only.

Spelling/grammar is handled separately by campaign_proofread (AI), which is
author-time only and never blocks — it can false-flag brand names and SKUs.
"""

import re
from pathlib import Path

# Live-data field prefixes are legitimate dynamic tokens even though their
# exact names aren't in the static context — see dynamic_email._SOURCE_PREFIXES.
_LIVE_PREFIXES = ("mart_", "books_", "crm_", "apollo_", "rocketreach_")

# Residue that means a token did NOT get substituted (a raw-body fallback
# after a Jinja error, or a half-written tag).
_LEFTOVER_RE = re.compile(r"\{\{|\}\}|\{%|%\}")
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)


def _finding(code: str, message: str, detail=None) -> dict:
    out = {"code": code, "message": message}
    if detail is not None:
        out["detail"] = detail
    return out


def _known_static_keys() -> set[str]:
    """The static context keys K3 always provides, derived live from
    k3_broadcast._context so this can never drift out of sync with it."""
    import types
    from app.agents.k3_broadcast import _context

    dummy_contact = types.SimpleNamespace(
        contact_ref="sample", name="Sample Name", company="Sample Co",
        email="sample@example.com", msisdn=None, extra_json={}, contact_type="dealer",
    )
    dummy_campaign = types.SimpleNamespace(
        id=None, name="Sample Campaign", campaign_type="dealer_promo",
    )
    return set(_context(dummy_contact, dummy_campaign).keys())


def _referenced_variables(source: str) -> tuple[set[str], str | None]:
    """Top-level variables referenced by a Jinja source, plus a syntax-error
    message if the source does not parse. Handles filters, conditionals and
    loops (loop variables are not reported as undeclared)."""
    from jinja2 import Environment, meta
    env = Environment()
    try:
        ast = env.parse(source or "")
    except Exception as exc:  # TemplateSyntaxError and friends
        return set(), str(exc)
    return set(meta.find_undeclared_variables(ast)), None


def _is_known(var: str, known: set[str], sample_keys: set[str]) -> bool:
    if var in known or var in sample_keys:
        return True
    return any(var.startswith(p) for p in _LIVE_PREFIXES)


def lint_template(template, *, sample_contact=None, campaign=None, db=None) -> dict:
    """Validate a template's content.

    sample_contact / campaign: when supplied, the body is rendered exactly as
    K3 renders it so leaked-token and blank-field problems are caught against
    real data. Without a sample, only static checks (syntax, unknown tokens,
    empty body, attachments) run.

    Returns {ok, blockers, warnings, info, summary}. ok is False iff there is
    at least one blocker.
    """
    from app.agents.k3_broadcast import _resolve_body, _resolve_subject, _render_template

    blockers: list[dict] = []
    warnings: list[dict] = []
    info: list[dict] = []

    channel = getattr(template, "channel", "email")
    body_src = template.body or ""
    subject_src = getattr(template, "subject", None) or ""

    # ── 1. Jinja syntax (body + subject) ────────────────────────────────
    body_vars, body_err = _referenced_variables(body_src)
    if body_err:
        blockers.append(_finding(
            "jinja_syntax_body",
            "The message body has a template syntax error — it would be sent "
            "with raw {{ }} code instead of the finished text.",
            body_err,
        ))
    subj_vars, subj_err = _referenced_variables(subject_src)
    if subj_err:
        blockers.append(_finding(
            "jinja_syntax_subject",
            "The subject line has a template syntax error.",
            subj_err,
        ))

    # ── 2. Unknown merge tokens (typos like {{ naem }}) ─────────────────
    known = _known_static_keys()
    sample_keys: set[str] = set()
    if sample_contact is not None:
        sample_keys = set((getattr(sample_contact, "extra_json", None) or {}).keys())

    unknown = sorted(
        v for v in (body_vars | subj_vars)
        if not _is_known(v, known, sample_keys)
    )
    if unknown:
        blockers.append(_finding(
            "unknown_tokens",
            "The message uses merge fields that don't exist — these are almost "
            "always typos and would render blank for every recipient.",
            unknown,
        ))

    # ── 3. Empty body / subject ─────────────────────────────────────────
    if not re.sub(r"<[^>]+>", "", body_src).strip():
        blockers.append(_finding("empty_body", "The message body is empty."))
    if channel == "email" and not subject_src.strip():
        warnings.append(_finding(
            "empty_subject",
            "No subject line set — recipients will see the campaign name as the "
            "subject. Add a subject for a professional first impression.",
        ))

    # ── 4. Attachments present on disk ──────────────────────────────────
    for att in (getattr(template, "attachments_json", None) or []):
        path = att.get("path", "")
        if not path or not Path(path).exists():
            blockers.append(_finding(
                "attachment_missing",
                "An attachment is referenced but its file is missing — the email "
                "would be sent WITHOUT it.",
                att.get("filename") or path,
            ))

    # ── 5. Render against a real sample contact ─────────────────────────
    rendered_body = rendered_subject = None
    if sample_contact is not None and campaign is not None and not body_err:
        try:
            rendered_body = _resolve_body(template, sample_contact, campaign, db=db)
        except Exception as exc:
            blockers.append(_finding("render_failed", "Rendering the body failed.", str(exc)))
        if not subj_err:
            try:
                rendered_subject = _resolve_subject(template, sample_contact, campaign)
            except Exception:
                rendered_subject = subject_src

        if rendered_body and _LEFTOVER_RE.search(rendered_body):
            blockers.append(_finding(
                "leftover_tokens_body",
                "After rendering, the body still contains raw {{ }} markers — a "
                "template error would ship visible code to the customer.",
            ))

        # Blank merge fields for this specific sample contact.
        ctx = _sample_context(sample_contact, campaign)
        blank = sorted(
            v for v in body_vars
            if _is_known(v, known, sample_keys) and not str(ctx.get(v, "")).strip()
        )
        if blank:
            warnings.append(_finding(
                "blank_fields",
                "These merge fields are empty for the sample contact — confirm "
                "every recipient has this data, or the message reads awkwardly "
                "(e.g. 'Dear ,').",
                blank,
            ))

    # ── 6. Links that aren't usable web addresses ───────────────────────
    # Scan the rendered body when we have it — so a merge field that resolves
    # to a non-URL (e.g. href="{{ company }}" → href="Yexis Electronics") is
    # caught as it will actually ship — otherwise the raw body.
    #
    # In email only absolute http(s)/mailto:/tel: links and #anchors work. A
    # bare word or a root-relative path (href="YexisElectronics", "/about")
    # ships broken, and K3's click-tracker rewrites it into a redirect whose
    # u= target then fails with 400 "Invalid redirect target". Block it so it
    # can't be sent — an authorised caller can still override with a reason.
    link_source = rendered_body if rendered_body is not None else body_src
    for href in _HREF_RE.findall(link_source):
        h = href.strip()
        if not h or h.startswith("{{"):
            continue  # empty href (flagged elsewhere) or an unrendered token
        if h.startswith(("http://", "https://", "mailto:", "tel:", "#")):
            continue
        blockers.append(_finding(
            "broken_link",
            "A link is not a valid web address — it would be broken in the "
            "recipient's inbox. Use a full https:// URL.",
            h,
        ))

    return {
        "ok": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "info": info,
        "summary": {
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "rendered_subject": rendered_subject,
            "rendered_body": rendered_body,
            "sampled": sample_contact is not None,
        },
    }


def _sample_context(sample_contact, campaign) -> dict:
    from app.agents.k3_broadcast import _context
    return _context(sample_contact, campaign)


def format_blockers(result: dict) -> str:
    """One-line human summary of blockers for an error message."""
    parts = []
    for b in result.get("blockers", []):
        detail = b.get("detail")
        if isinstance(detail, list):
            detail = ", ".join(str(d) for d in detail)
        parts.append(b["message"] + (f" ({detail})" if detail else ""))
    return " ".join(parts)
