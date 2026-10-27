"""K3 Broadcast — multi-channel campaign message dispatch.

Autonomy: A3 (bounded-autonomous — broadcasts approved templates to
opt-in contacts within daily volume caps; Marketing Manager approval
required before first broadcast).

Channels:
  whatsapp  — live BSP (Gallabox/Meta/Gupshup/Twilio); Meta-approved templates
  email     — SMTP; requires mail_send_enabled=True + smtp_* config
  telegram  — Telegram Bot API; contact must have telegram_chat_id
  voice     — Outbound IVR/TTS call (Exotel/Twilio/Ozonetel)
  push      — Firebase Cloud Messaging (FCM) to dealer mobile app
  linkedin  — Apollo.io sequence enrollment for LinkedIn outreach
  auto      — per-contact: best available channel in priority order

Actions:
  send               — dispatch messages to all un-messaged campaign contacts
  drip               — trigger next drip step for contacts due for follow-up
  schedule           — schedule campaign broadcast for a future datetime
  report             — delivery + response status summary
  send_transactional — one-off, event-triggered email (quote approved,
                        payment overdue, stock back in) outside the
                        campaign/broadcast/trickle machinery — still gets
                        full live-data personalization and tracking
"""

import logging
import smtplib
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.agents.base import BaseAgent, AgentResult

VALID_ACTIONS = {"send", "drip", "schedule", "scheduler_tick", "report", "send_transactional"}
VALID_CHANNELS = {"whatsapp", "email", "telegram", "voice", "push", "linkedin", "auto"}
VALID_ENTITIES = {"YEXIS-EL", "YEXIS-SL", "YEXIS-SOL"}

# Hard cap: never send more than this per campaign run without approval
MAX_BATCH_SIZE = 200

# Channel priority for auto mode (highest to lowest)
AUTO_CHANNEL_PRIORITY = ["whatsapp", "telegram", "email", "push", "voice", "linkedin"]

logger = logging.getLogger(__name__)


# ── Template helpers ──────────────────────────────────────────────────

def _build_template_params(template, contact, campaign) -> dict | None:
    """Resolve WhatsApp template variables from campaign and contact context."""
    var_map: dict = (template.variables_json or {}) if template.variables_json else {}
    if not var_map:
        return None
    ctx = _context(contact, campaign)
    return {str(k): str(ctx.get(v, v)) for k, v in var_map.items()} or None


def _render_template(body: str, ctx: dict, mark_unknown: bool = False) -> str:
    """Render a Jinja2 template body with campaign context.

    Supports the full Jinja2 syntax:
      {{ dealer_name }}               — variable substitution
      {% if mart_outstanding_amt %}   — conditionals
      {% for item in items %}         — loops
      {{ amount | round(0) }}         — filters

    Missing variables render as '' (via _SilentUndefined) so a bad
    context key never breaks a live send.  Render errors are caught and
    logged; the raw body is returned as a safe fallback.

    mark_unknown=True renders an undefined variable as a loud «name?» marker
    instead of a silent blank. Used for TEST sends (see campaigns test-send)
    so a typo'd merge field jumps out in the author's own inbox — real
    broadcasts always render clean (and are blocked upstream by the content
    gate anyway).
    """
    from jinja2 import Environment, Undefined

    class _SilentUndefined(Undefined):
        """Undefined variables → empty string; numeric filters → 0."""
        def __str__(self): return ""
        def __bool__(self): return False
        def __iter__(self): return iter([])
        def __len__(self): return 0
        def __int__(self): return 0
        def __float__(self): return 0.0
        def __round__(self, *a): return 0
        def __getattr__(self, key): return _SilentUndefined()
        def __getitem__(self, key): return _SilentUndefined()

    class _MarkingUndefined(_SilentUndefined):
        """Undefined variables → a visible «name?» marker (test sends only)."""
        def __str__(self):
            return f"«{self._undefined_name}?»"
        def __bool__(self): return True

    env = Environment(
        undefined=_MarkingUndefined if mark_unknown else _SilentUndefined,
        autoescape=False,
    )
    clean_ctx = {k: ("" if v is None else v) for k, v in ctx.items()}
    try:
        return env.from_string(body).render(**clean_ctx)
    except Exception as exc:
        logger.warning("Jinja2 render error: %s", exc)
        return body


def _resolve_body(template, contact, campaign, db=None, extra_ctx=None, mark_unknown=False) -> str:
    """Render template.body: {{field}} substitution + {{#if}} conditionals,
    with the context optionally enriched by live data (see
    app.services.dynamic_email), an explicit extra_ctx override (used by
    one-off transactional sends to inject event-specific fields such as
    an order number), and a per-segment prepend/append.

    Live sources are only queried when the template body actually
    references one of their fields — see referenced_sources() — so a
    plain static campaign never pays for a network call it doesn't need.
    """
    ctx = _context(contact, campaign)
    body = template.body or ""

    if db is not None:
        from app.services.dynamic_email import build_live_context, referenced_sources
        needed = referenced_sources(body)
        if needed:
            ctx.update(build_live_context(
                db, contact.contact_ref, getattr(contact, "email", None),
                display_name=contact.contact_ref, sources=needed,
            ))

    if extra_ctx:
        ctx.update(extra_ctx)

    seg = _segment_override(template, ctx["contact_type"])
    if seg.get("prepend"):
        body = seg["prepend"] + body
    if seg.get("append"):
        body = body + seg["append"]

    return _render_template(body, ctx, mark_unknown=mark_unknown)


def _segment_override(template, contact_type: str) -> dict:
    overrides = getattr(template, "segment_overrides_json", None) or {}
    return overrides.get(contact_type) or {}


def _resolve_subject(template, contact, campaign, extra_ctx=None, mark_unknown=False) -> str:
    """Segment-aware subject line.

    Priority: segment_override subject > template.subject > template.purpose > campaign name
    All are Jinja2-renderable so {{ name }}, {{ campaign_name }} etc work in subjects.
    """
    ctx = _context(contact, campaign)
    if extra_ctx:
        ctx.update(extra_ctx)
    seg = _segment_override(template, ctx["contact_type"])
    if seg.get("subject"):
        return _render_template(seg["subject"], ctx, mark_unknown=mark_unknown)
    if getattr(template, "subject", None):
        return _render_template(template.subject, ctx, mark_unknown=mark_unknown)
    return template.purpose or campaign.name


def _derive_salutation(extra: dict) -> str:
    """Honorific for {{ salutation }}.

    An explicit salutation column (mapped on upload → extra['salutation'])
    always wins. When the contact has none, fall back to one inferred from a
    gender/sex column if present, otherwise an empty string so
    'Dear {{ salutation }} {{ name }}' still reads cleanly as 'Dear Kumar'.
    Ms. (not Mrs.) is used for female contacts since marital status is unknown.
    """
    explicit = str(extra.get("salutation") or "").strip()
    if explicit:
        return explicit
    gender = str(extra.get("gender") or extra.get("sex") or "").strip().lower()
    if gender in ("m", "male"):
        return "Mr."
    if gender in ("f", "female"):
        return "Ms."
    return ""


def _context(contact, campaign) -> dict:
    # Prefer the stored display name (from Excel "Contact Name" column or source).
    # Fall back to contact_ref (often the email address) only when no name was captured.
    display_name = getattr(contact, "name", None) or contact.contact_ref
    name_parts = display_name.split() if display_name else []
    extra = getattr(contact, "extra_json", None) or {}
    cid = str(campaign.id) if getattr(campaign, "id", None) else ""
    form_link = _build_form_link(contact.contact_ref, cid, contact) if cid else ""
    return {
        "name": display_name,
        "dealer_name": display_name,     # backward-compat alias
        "first_name": name_parts[0] if name_parts else "",
        # Everything after the first token — "Kumar" in "Rajesh Kumar",
        # "Van Der Berg" in "Jan Van Der Berg". Empty for single-word names.
        "last_name": " ".join(name_parts[1:]),
        "company": getattr(contact, "company", None) or "",
        "account_name": getattr(contact, "company", None) or "",  # alias — matches the Excel header
        # All other per-contact fields captured from the source (Excel columns
        # like Title/Role/Seniority/Department/Linkedin, normalised to lowercase
        # keys) become template variables: {{ title }}, {{ role }}, etc. An
        # explicit first_name/last_name column here overrides the name split.
        **extra,
        # Resolved after **extra so an empty salutation cell falls back to the
        # gender-derived default rather than rendering blank.
        "salutation": _derive_salutation(extra),
        "campaign_name": campaign.name,
        "campaign_type": campaign.campaign_type,
        "contact_ref": contact.contact_ref,
        "contact_type": getattr(contact, "contact_type", None) or "dealer",
        "contact_email": getattr(contact, "email", None),
        # Pre-filled form landing page URL — unique per recipient.
        # Use {{ form_link }} anywhere in the template body:
        #   <a href="{{ form_link }}">Fill your details →</a>
        "form_link": form_link,
    }


def _extract_provider_msg_id(bsp_response: dict) -> str:
    """Extract message ID from BSP response regardless of provider shape."""
    inner = bsp_response.get("response") or {}
    if isinstance(inner, dict):
        mid = inner.get("messageId") or inner.get("message_id") or inner.get("id")
        if mid:
            return str(mid)
    messages = inner.get("messages") or []
    if messages and isinstance(messages[0], dict):
        return str(messages[0].get("id", ""))
    return ""


def _build_form_link(contact_ref: str, campaign_id: str, contact=None) -> str:
    """Build a signed, pre-filled form URL for this contact.

    Resolves as {{ form_link }} inside every campaign email body so the
    template author can write:
      <a href="{{ form_link }}">Fill your details →</a>
    and each recipient gets their own URL with name/email/phone pre-filled.
    """
    import hashlib, hmac as _hmac, urllib.parse
    from app.core.config import settings as _s
    secret = _s.approval_hmac_secret or "dev-secret"
    token_data = f"{campaign_id}:{contact_ref}"
    sig = _hmac.new(secret.encode(), token_data.encode(), hashlib.sha256).hexdigest()[:16]
    token = urllib.parse.quote(f"{token_data}:{sig}", safe="")
    base = _s.approval_base_url.rstrip("/")
    params: dict[str, str] = {"t": token}
    if contact:
        if getattr(contact, "name", None):
            params["name"] = contact.name
        if getattr(contact, "email", None):
            params["email"] = contact.email
        if getattr(contact, "msisdn", None):
            params["phone"] = contact.msisdn
        if getattr(contact, "company", None):
            params["company"] = contact.company
    return f"{base}/forms/{campaign_id}?" + urllib.parse.urlencode(params)


def _build_unsubscribe_link(contact_ref: str, campaign_id: str) -> str:
    """Build HMAC-signed opt-out URL embedded in campaign messages."""
    import hashlib, hmac, urllib.parse
    from app.core.config import settings
    secret = settings.approval_hmac_secret or "dev-secret"
    token_data = f"{contact_ref}:{campaign_id}"
    sig = hmac.new(secret.encode(), token_data.encode(), hashlib.sha256).hexdigest()[:16]
    token = urllib.parse.quote(f"{token_data}:{sig}", safe="")
    base = settings.approval_base_url.rstrip("/")
    return f"{base}/campaigns/unsubscribe?t={token}"


def _email_footer(unsub_url: str) -> str:
    """Branded marketing footer appended to every campaign email (not to
    transactional sends — those are notices, not bulk marketing).

    Table-based + inline CSS so it renders in Outlook, mirroring the
    social-row / wordmark / copyright / address / unsubscribe layout of a
    polished bulk-mail footer. Appended AFTER click-tracking rewriting, so
    these links stay direct — a footer or unsubscribe click is not counted as
    a campaign 'click', and the opt-out link must never be tracked/redirected.

    Only real, known-good links are included. To add more socials, copy the
    '· <a>…</a>' pattern in the quick-links row with a real https URL.
    """
    year = datetime.now().year
    gray, muted = "#6b7280", "#9ca3af"
    logo = ("https://yexiselectronics.com/wp-content/uploads/2023/05/"
            "cropped-cropped-logo-6-removebg-preview.png")
    return (
        '<br>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        '<tr><td align="center" style="padding:0 24px;">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        'style="width:600px;max-width:600px;border-top:1px solid #e5e7eb;">'
        # quick links / socials
        '<tr><td align="center" style="padding:20px 24px 4px 24px;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:13px;color:{gray};">'
        f'<a href="https://yexiselectronics.com/" style="color:{gray};text-decoration:none;">Website</a>'
        ' &nbsp;&middot;&nbsp; '
        '<a href="https://www.linkedin.com/company/yexiselectronics/home/" '
        f'style="color:{gray};text-decoration:none;">LinkedIn</a>'
        '</td></tr>'
        # brand logo
        '<tr><td align="center" style="padding:14px 24px 4px 24px;">'
        f'<img src="{logo}" alt="Yexis Electronics" width="120" '
        'style="display:block;border:0;outline:none;height:auto;" /></td></tr>'
        # copyright + legal entity
        '<tr><td align="center" style="padding:2px 24px;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:12px;color:{muted};line-height:1.6;">'
        f'&copy; {year} Yexis Electronics Business Solutions. All rights reserved.</td></tr>'
        # address
        '<tr><td align="center" style="padding:0 40px 2px 40px;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:12px;color:{muted};line-height:1.6;">'
        f'<a href="https://maps.app.goo.gl/8KgVmoL2MpUnHqr3A" style="color:{muted};text-decoration:none;">'
        'No. 190-192, Offis Bay Realty Private Limited, 3rd Floor, Hamid Complex, '
        'Anna Salai, Chennai - 600 006, Tamil Nadu, INDIA</a></td></tr>'
        # unsubscribe
        '<tr><td align="center" style="padding:12px 24px 24px 24px;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:12px;color:{muted};">'
        f'To stop receiving these emails, <a href="{unsub_url}" style="color:{gray};">unsubscribe here</a>.'
        '</td></tr>'
        '</table></td></tr></table>'
    )


def _load_optouts(db) -> tuple[set[str], set[str]]:
    """Global opt-out list as (contact_refs, lower-cased emails). Matching on
    email too means a re-uploaded address under a new contact_ref is still
    suppressed."""
    from app.models.enterprise import ContactOptOut
    refs: set[str] = set()
    emails: set[str] = set()
    for ref, em in db.query(ContactOptOut.contact_ref, ContactOptOut.email).all():
        refs.add(ref)
        if em:
            emails.add(em.strip().lower())
    return refs, emails


def _is_opted_out(contact, optouts: tuple[set[str], set[str]]) -> bool:
    refs, emails = optouts
    if contact.contact_ref in refs:
        return True
    for addr in (getattr(contact, "email", None), getattr(contact, "alt_email", None)):
        if addr and addr.strip().lower() in emails:
            return True
    return False


def _is_bounced(db, email_addr: str | None) -> bool:
    """True if this address has a recorded hard bounce (see GET /mail/bounces)
    and must be skipped rather than mailed again."""
    from app.services.mailbox import is_email_suppressed
    return is_email_suppressed(db, email_addr)


def _within_email_send_window() -> bool:
    """True if it's currently inside the permitted IST send window.

    Sending at 3am is itself a signal nobody is manually behind the
    send — this keeps campaign email inside plausible business hours.
    """
    from zoneinfo import ZoneInfo
    from app.core.config import settings
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
    return settings.k3_email_send_window_start_hour <= now_ist.hour < settings.k3_email_send_window_end_hour


# ── Open/click tracking ─────────────────────────────────────────────────
#
# Detailed engagement tracking (not just an aggregate rate): every campaign
# email gets a per-recipient signed token, a 1x1 open pixel, and every link
# rewritten through a click-tracking redirect. See GET
# /campaigns/{id}/engagement for the resulting per-contact, per-link log.

def _build_tracking_token(message_log_id: str, contact_ref: str, campaign_id: str) -> str:
    import hashlib, hmac, urllib.parse
    from app.core.config import settings
    secret = settings.approval_hmac_secret or "dev-secret"
    data = f"{message_log_id}:{contact_ref}:{campaign_id}"
    sig = hmac.new(secret.encode(), data.encode(), hashlib.sha256).hexdigest()[:16]
    return urllib.parse.quote(f"{data}:{sig}", safe="")


def _open_tracking_pixel_tag(message_log_id: str, contact_ref: str, campaign_id: str) -> str:
    from app.core.config import settings
    token = _build_tracking_token(message_log_id, contact_ref, campaign_id)
    base = settings.approval_base_url.rstrip("/")
    url = f"{base}/campaigns/track/open?t={token}"
    return f'<img src="{url}" width="1" height="1" alt="" style="display:none;border:0;width:1px;height:1px;" />'


def _wrap_links_for_click_tracking(
    body_html: str, message_log_id: str, contact_ref: str, campaign_id: str,
) -> str:
    """Rewrite every absolute http(s) href in the body through the
    click-tracking redirect, preserving the original destination as a query
    param.

    Only absolute http(s):// links are wrapped. mailto:/tel:/#anchor links,
    relative links, and anything that isn't a real URL (e.g. a stray
    href="CompanyName") are left untouched — wrapping those produced a
    redirect whose u= target then failed track_click's http(s) check with a
    400 "Invalid redirect target".

    Links that already point at our own /campaigns/track/click endpoint are
    left alone too, so a tracking URL pasted into a template (or carried over
    from an earlier send under a now-dead tunnel host) is never wrapped a
    second time into a nested redirect-to-a-redirect.
    """
    import html as _html
    import re, urllib.parse
    from app.core.config import settings
    token = _build_tracking_token(message_log_id, contact_ref, campaign_id)
    base = settings.approval_base_url.rstrip("/")

    def _rewrite(match: "re.Match") -> str:
        # The href may carry HTML entities (&amp; etc.) when the destination
        # was authored/pasted as rendered HTML — decode first so the real URL
        # is what we check and then re-encode.
        original_url = _html.unescape(match.group(2))
        if not original_url.lower().startswith(("http://", "https://")):
            return match.group(0)  # not an absolute URL — leave it untouched
        if "/campaigns/track/click" in original_url:
            return match.group(0)  # already a tracking redirect — don't nest
        redirect = f"{base}/campaigns/track/click?t={token}&u={urllib.parse.quote(original_url, safe='')}"
        return f"{match.group(1)}{redirect}{match.group(3)}"

    return re.sub(r'(href=["\'])([^"\']+)(["\'])', _rewrite, body_html, flags=re.IGNORECASE)


# ── Email sender ──────────────────────────────────────────────────────

def _attach_files(msg, attachments: list | None) -> None:
    """Attach template files to an outbound message.

    Each attachment is {"path", "filename", "mime_type"} as stored on the
    template. Missing files are skipped (a since-deleted file must never
    abort a live send). Called only after the alternative body parts are in
    place, so the message is already multipart/mixed by the caller.
    """
    import mimetypes
    from email.mime.base import MIMEBase
    from email import encoders
    from pathlib import Path

    for att in (attachments or []):
        path = Path(att.get("path", ""))
        if not path.exists():
            logger.warning("Template attachment missing on disk, skipping: %s", path)
            continue
        mime = att.get("mime_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        maintype, _, subtype = mime.partition("/")
        part = MIMEBase(maintype, subtype or "octet-stream")
        part.set_payload(path.read_bytes())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition", "attachment",
            filename=att.get("filename") or path.name,
        )
        msg.attach(part)


def _embed_inline_images(html: str) -> tuple[str, list]:
    """Turn data:image;base64 <img> sources into real inline (cid:) parts.

    Rich-text editors embed inserted/pasted images as base64 data URIs, which
    Gmail and Outlook strip — so the image vanishes in the received mail.
    Replacing each with a Content-ID reference and attaching it as an inline
    image (inside a multipart/related) makes it render everywhere.

    Returns (rewritten_html, [MIMEImage, ...]).
    """
    import base64 as _b64
    import re as _re
    from email.mime.image import MIMEImage
    from email.utils import make_msgid

    images: list = []

    def _repl(m):
        subtype = (m.group("subtype") or "png").lower().split("+")[0]
        try:
            raw = _b64.b64decode(m.group("data"))
        except Exception:
            return m.group(0)  # leave malformed data URIs untouched
        cid = make_msgid()[1:-1]  # strip the surrounding < >
        img = MIMEImage(raw, _subtype=subtype)
        img.add_header("Content-ID", f"<{cid}>")
        img.add_header("Content-Disposition", "inline",
                       filename=f"image{len(images) + 1}.{subtype}")
        images.append(img)
        return f'{m.group("pre")}cid:{cid}{m.group("post")}'

    pattern = _re.compile(
        r'(?P<pre>src=["\'])data:image/(?P<subtype>[a-zA-Z0-9.+-]+);base64,'
        r'(?P<data>[^"\']+)(?P<post>["\'])',
        _re.IGNORECASE,
    )
    return pattern.sub(_repl, html), images


def _linkify_phone_numbers(html: str) -> str:
    """Wrap bare phone numbers in the body with tel: links so they're
    click-to-call in the recipient's mail app.

    Skips numbers that are already inside an <a> element or inside an HTML tag
    (attributes/URLs), and only linkifies numbers that validate as a real
    phone: an explicit +country-code number, an Indian 10-digit mobile
    (starts 6-9), 91+10 digits, or 0+10 digits. This avoids turning invoice
    numbers, amounts or dates into fake call links.
    """
    import re as _re

    def _href(raw: str) -> str | None:
        digits = _re.sub(r"\D", "", raw)
        if raw.lstrip().startswith("+"):
            return "+" + digits if 10 <= len(digits) <= 15 else None
        if len(digits) == 10 and digits[0] in "6789":
            return "+91" + digits
        if len(digits) == 12 and digits.startswith("91"):
            return "+" + digits
        if len(digits) == 11 and digits.startswith("0"):
            return "+91" + digits[1:]
        return None

    phone_re = _re.compile(r"\+?\d[\d\s().\-]{7,13}\d")

    def _sub_text(text: str) -> str:
        def _r(m):
            raw = m.group(0)
            href = _href(raw)
            return f'<a href="tel:{href}">{raw}</a>' if href else raw
        return phone_re.sub(_r, text)

    # Walk tags vs text so we never touch numbers inside a tag or an <a> link.
    out, in_a = [], 0
    for tok in _re.split(r"(<[^>]+>)", html):
        if not tok:
            continue
        if tok.startswith("<"):
            low = tok.lower()
            if low.startswith("<a") and not low.startswith("<area"):
                in_a += 1
            elif low.startswith("</a"):
                in_a = max(0, in_a - 1)
            out.append(tok)
        else:
            out.append(tok if in_a > 0 else _sub_text(tok))
    return "".join(out)


def _send_smtp_email(
    to_email: str, subject: str, body_html: str,
    unsubscribe_url: str | None = None,
    reply_to: str | None = None,
    attachments: list | None = None,
    pace: bool = True,
) -> None:
    """Send campaign email via SMTP. Port 465 → SSL, others → STARTTLS.

    Deliverability hardening (on top of the existing in-body opt-out link):
      - Message-ID header — mail with no Message-ID reads as auto-generated
        or spoofed to most filters.
      - List-Unsubscribe / List-Unsubscribe-Post (RFC 8058) — Gmail/Yahoo
        bulk-sender rules expect a one-click header, not just a body link.
      - A text/plain alternative alongside the HTML part — HTML-only bulk
        mail scores worse on most spam filters.
      - A randomized delay after send (not fixed) — see SMTP_SEND_DELAY_
        MIN/MAX_SECONDS. Combined with the small per-tick trickle batch
        in _send() below, this is what actually avoids the "sent via
        script" signature: a machine-regular gap between sends is itself
        a tell, and hundreds sent within seconds is unmistakable.
    """
    import random
    import re
    import time
    from email.utils import make_msgid

    from app.core.config import settings
    if not settings.mail_send_enabled:
        raise OSError(
            "Email sending disabled (mail_send_enabled=False). "
            "Set mail_send_enabled=True in .env after IT approval."
        )
    if not settings.smtp_host or not settings.smtp_from:
        raise OSError("SMTP not configured — smtp_host and smtp_from required.")

    # Make bare phone numbers click-to-call.
    body_html = _linkify_phone_numbers(body_html)

    # Images pasted/inserted in the rich-text editor arrive as
    # data:image;base64 <img> sources. Gmail/Outlook strip those, so the
    # picture silently disappears. Convert them to real inline parts (cid:)
    # before building the body so they render everywhere.
    body_html, inline_images = _embed_inline_images(body_html)

    # The text+HTML body is always a multipart/alternative. Inline images wrap
    # it in a multipart/related; file attachments wrap that in a
    # multipart/mixed — the nesting mail clients expect.
    body_part = MIMEMultipart("alternative")

    # Rough plain-text rendering of the HTML body for the alternative part.
    plain_text = re.sub(r"<br\s*/?>", "\n", body_html, flags=re.IGNORECASE)
    plain_text = re.sub(r"<[^>]+>", "", plain_text).strip()
    body_part.attach(MIMEText(plain_text or subject, "plain"))
    body_part.attach(MIMEText(body_html, "html"))

    if inline_images:
        content_root = MIMEMultipart("related")
        content_root.attach(body_part)
        for img in inline_images:
            content_root.attach(img)
    else:
        content_root = body_part

    if attachments:
        msg = MIMEMultipart("mixed")
        msg.attach(content_root)
        _attach_files(msg, attachments)
    else:
        msg = content_root

    msg["From"] = settings.smtp_from
    msg["To"] = to_email
    msg["Subject"] = subject
    domain = settings.smtp_from.split("@")[-1] if "@" in (settings.smtp_from or "") else None
    msg["Message-ID"] = make_msgid(domain=domain)

    if reply_to:
        msg["Reply-To"] = reply_to
    if unsubscribe_url:
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    if settings.smtp_port == 465:
        conn = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20)
    else:
        conn = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
        conn.starttls()

    with conn:
        if settings.smtp_username and settings.smtp_password:
            conn.login(settings.smtp_username, settings.smtp_password)
        conn.send_message(msg)

    # Randomized delay, not a fixed one — a fixed gap between every send is
    # itself a machine-regular pattern that a receiving server (or a human
    # comparing timestamps) can fingerprint as scripted.
    # Single sends (test / transactional) skip the bulk pacing delay so the
    # request doesn't hang for up to a minute.
    delay_min = getattr(settings, "smtp_send_delay_min_seconds", 20.0)
    delay_max = getattr(settings, "smtp_send_delay_max_seconds", 75.0)
    if pace and delay_max > 0:
        time.sleep(random.uniform(min(delay_min, delay_max), max(delay_min, delay_max)))


# ── A/B testing ───────────────────────────────────────────────────────

def _assign_ab_variant(campaign, contact_index: int) -> str:
    """Assign A/B variant deterministically from contact index."""
    ab = (campaign.ab_test_json or {}) if campaign.ab_test_json else {}
    variants = ab.get("variants", [])
    if not variants:
        return ""
    return variants[contact_index % len(variants)].get("name", "A")


def _get_ab_template_id(campaign, variant: str, default_template_id: str) -> str:
    """Resolve template_id for A/B variant."""
    ab = (campaign.ab_test_json or {}) if campaign.ab_test_json else {}
    for v in ab.get("variants", []):
        if v.get("name") == variant:
            return v.get("template_id") or default_template_id
    return default_template_id


# ── Content-correctness gate ──────────────────────────────────────────

def _run_content_gate(db, template, campaign, sample_contact, payload) -> "AgentResult | None":
    """Structural content gate — see app/services/campaign_lint.

    Returns a VALIDATION_ERROR AgentResult to ABORT the send when the
    template has a blocker-level problem (leaked {{tokens}}, a merge field
    that renders blank, broken Jinja, a missing attachment, an empty body),
    or None to let the send proceed.

    When k3_proofread_gate_enabled is on, AI proofreading also blocks — but
    only when a model provider is configured AND it returns spelling/grammar
    issues; an unavailable checker never blocks.

    An authorised caller may override any blocker by passing force=True AND a
    non-empty override_reason — the override is logged with the blocker codes.
    Disabled entirely by k3_content_gate_enabled=False.
    """
    from app.core.config import settings
    if not getattr(settings, "k3_content_gate_enabled", True):
        return None

    from app.services.campaign_lint import lint_template, format_blockers
    result = lint_template(template, sample_contact=sample_contact, campaign=campaign, db=db)

    # AI proofread — spelling/grammar. Blocks only when it actually ran and
    # found issues (cached by content hash so trickle ticks don't re-bill).
    if getattr(settings, "k3_proofread_gate_enabled", True):
        from app.services.campaign_proofread import proofread_cached
        summary = result.get("summary") or {}
        subj = summary.get("rendered_subject") or getattr(template, "subject", None) or ""
        body = summary.get("rendered_body") or (template.body or "")
        proof = proofread_cached(subj, body)
        result["proofread"] = proof
        if proof.get("available") and proof.get("issues"):
            result["ok"] = False
            result["blockers"].append({
                "code": "spelling_grammar",
                "message": f"{len(proof['issues'])} spelling/grammar issue(s) found — "
                           "fix them or override.",
                "detail": [f"{i.get('text', '')} → {i.get('fix', '')}"
                           for i in proof["issues"]],
            })

    if result["ok"]:
        return None

    if payload.get("force"):
        reason = str(payload.get("override_reason") or "").strip()
        if not reason:
            return AgentResult(
                status="VALIDATION_ERROR",
                error="Content check failed and force=True was given without an "
                      "override_reason. Provide override_reason explaining why it "
                      "is safe to send.",
                output={"content_check": result},
            )
        logger.warning(
            "K3 content gate OVERRIDDEN by %s for template %s: %s | blockers=%s",
            payload.get("requested_by") or "unknown",
            getattr(template, "template_id", "?"), reason,
            [b["code"] for b in result["blockers"]],
        )
        return None

    return AgentResult(
        status="VALIDATION_ERROR",
        error="Content check failed — nothing was sent. " + format_blockers(result),
        output={"content_check": result},
    )


# ── Agent ─────────────────────────────────────────────────────────────

class K3Broadcast(BaseAgent):
    """Broadcast Agent — K3

    Multi-channel campaign broadcast with drip sequences, A/B testing,
    scheduled delivery, and opt-out portal support.
    """

    agent_code = "K3"
    agent_name = "Broadcast"

    def describe(self) -> str:
        return (
            "Multi-channel campaign broadcast: WhatsApp, Email, Telegram, "
            "Voice/IVR, Push (FCM), LinkedIn (Apollo). Supports A/B testing, "
            "drip sequences, scheduled dispatch, and opt-out."
        )

    def validate(self, payload: dict) -> str | None:
        if payload is None:
            return "Payload is empty"
        action = payload.get("action")
        if not action:
            return "'action' is required"
        if action not in VALID_ACTIONS:
            return f"action must be one of {sorted(VALID_ACTIONS)}"
        # scheduler_tick is a platform-internal event; no campaign_id needed
        if action == "scheduler_tick":
            return None
        # send_transactional is a one-off event-triggered send; no campaign
        if action == "send_transactional":
            if not payload.get("template_id"):
                return "'template_id' is required for send_transactional"
            if not payload.get("to_email"):
                return "'to_email' is required for send_transactional"
            return None
        if not payload.get("campaign_id"):
            return "'campaign_id' is required"
        if action == "send":
            if not payload.get("template_id"):
                return "'template_id' is required for send"
            ch = payload.get("channel", "auto")
            if ch not in VALID_CHANNELS:
                return f"channel must be one of {sorted(VALID_CHANNELS)}"
        if action == "schedule":
            if not payload.get("template_id"):
                return "'template_id' is required for schedule"
            if not payload.get("scheduled_at"):
                return "'scheduled_at' (ISO datetime UTC) is required for schedule"
            ch = payload.get("channel", "auto")
            if ch not in VALID_CHANNELS:
                return f"channel must be one of {sorted(VALID_CHANNELS)}"
        return None

    def execute(
        self,
        payload: dict,
        task_id: uuid.UUID | None = None,
        requested_by: str | None = None,
    ) -> AgentResult:
        from app.core.db import SessionLocal
        db = SessionLocal()
        try:
            return self._run(db, payload, task_id)
        finally:
            db.close()

    def _run(self, db, payload: dict, task_id: uuid.UUID | None) -> AgentResult:
        from app.models.enterprise import (
            Campaign, CampaignContact, CampaignSequenceStep,
            MessageLog, TemplateRegistry, WaContact,
        )

        action = payload["action"]

        # scheduler_tick has no campaign_id — it finds its own campaigns
        if action == "scheduler_tick":
            return self._scheduler_tick(db, CampaignContact, MessageLog, TemplateRegistry, WaContact)

        # send_transactional is a one-off send — no campaign involved
        if action == "send_transactional":
            return self._send_transactional(db, payload, MessageLog, TemplateRegistry)

        campaign_id = payload["campaign_id"]
        campaign = db.get(Campaign, campaign_id)
        if campaign is None:
            return AgentResult(status="VALIDATION_ERROR", error=f"Campaign '{campaign_id}' not found")

        if action == "report":
            return self._report(db, campaign, campaign_id, CampaignContact)

        if action == "schedule":
            return self._schedule(db, campaign, payload)

        if action == "send":
            channel = payload.get("channel") or campaign.default_channel or "auto"
            return self._send(
                db, payload, campaign, campaign_id, channel,
                CampaignContact, MessageLog, TemplateRegistry, WaContact,
                step=0,
            )

        if action == "drip":
            return self._drip(
                db, campaign, campaign_id,
                CampaignContact, CampaignSequenceStep, MessageLog, TemplateRegistry, WaContact,
            )

        if action == "scheduler_tick":
            return self._scheduler_tick(db, CampaignContact, MessageLog, TemplateRegistry, WaContact)

        return AgentResult(status="VALIDATION_ERROR", error=f"Unknown action '{action}'")

    # ── scheduler_tick ───────────────────────────────────────────────

    def _scheduler_tick(
        self, db, CampaignContact, MessageLog, TemplateRegistry, WaContact,
    ) -> AgentResult:
        """Called every 5 min by the gateway scheduler (k3_campaign_scheduler
        lane). Fires/re-fires campaigns whose scheduled_at has passed and
        that still have unsent contacts.

        Deliberately re-fires a campaign already in 'broadcast' status, not
        just the first 'audience_built' -> 'broadcast' transition — that's
        what makes the email trickle cap in _send() actually spread sends
        across many ticks/hours instead of only ever running once. A
        campaign with nothing left pending is skipped (cheap COUNT query)
        so this doesn't do pointless work forever once a campaign finishes.
        """
        from app.models.enterprise import Campaign
        now = datetime.now(timezone.utc)

        due = db.query(Campaign).filter(
            Campaign.scheduled_at <= now,
            Campaign.scheduled_at.isnot(None),
            Campaign.status.in_(("audience_built", "broadcast")),
        ).all()

        fired = []
        errors = []

        # Hot engagement -> Zoho CRM call tasks, for every live campaign
        # (including fully-sent ones — people keep opening after the send).
        escalated = 0
        try:
            from app.services.campaign_intel import escalate_hot_engagement
            for live in db.query(Campaign).filter(Campaign.status.in_(("broadcast", "tracking"))).all():
                escalated += escalate_hot_engagement(db, live)
        except Exception as exc:
            errors.append(f"hot-lead escalation: {exc}")

        # Follow-up steps: run the drip for every live campaign with a sequence,
        # so call/LinkedIn tasks and follow-up emails happen on schedule.
        drips = []
        try:
            from app.models.enterprise import CampaignSequenceStep
            for live in db.query(Campaign).filter(Campaign.status == "broadcast",
                                                  Campaign.sequence_enabled.is_(True)).all():
                r = self._drip(db, live, str(live.id), CampaignContact, CampaignSequenceStep,
                               MessageLog, TemplateRegistry, WaContact)
                if r.output:
                    drips.append({"campaign_ref": live.campaign_ref, **{k: r.output.get(k) for k in (
                        "drip_messages_sent", "isr_tasks_created", "deferred_touch_cap")}})
                elif r.error:
                    errors.append(f"{live.campaign_ref} drip: {r.error}")
        except Exception as exc:
            errors.append(f"drip: {exc}")

        for campaign in due:
            remaining = db.query(CampaignContact).filter(
                CampaignContact.campaign_id == campaign.id,
                CampaignContact.message_sent_ts == None,  # noqa: E711
            ).count()
            if remaining == 0:
                continue  # fully sent — nothing left to trickle out

            template_id = campaign.message_template_id
            if not template_id:
                errors.append(f"{campaign.campaign_ref}: no message_template_id set")
                continue
            result = self._send(
                db,
                {"template_id": template_id, "campaign_id": str(campaign.id)},
                campaign, str(campaign.id),
                campaign.default_channel or "auto",
                CampaignContact, MessageLog, TemplateRegistry, WaContact,
            )
            fired.append({
                "campaign_ref": campaign.campaign_ref,
                "status": result.status,
                "sent": result.output.get("sent_total", 0) if result.output else 0,
                "remaining_unsent": result.output.get("remaining_unsent") if result.output else None,
            })

        return AgentResult(
            status="SUCCESS",
            output={"tick_ts": now.isoformat(), "fired": fired, "errors": errors,
                    "crm_tasks_created": escalated, "drips": drips},
            actions_taken=[f"scheduler_tick_fired_{len(fired)}_campaigns"],
        )

    # ── schedule ──────────────────────────────────────────────────────

    def _schedule(self, db, campaign, payload: dict) -> AgentResult:
        raw = payload["scheduled_at"]
        try:
            from datetime import datetime as _dt
            scheduled = _dt.fromisoformat(raw.replace("Z", "+00:00"))
            if scheduled.tzinfo is None:
                from datetime import timezone as _tz
                scheduled = scheduled.replace(tzinfo=_tz.utc)
        except ValueError as exc:
            return AgentResult(status="VALIDATION_ERROR", error=f"Invalid scheduled_at: {exc}")

        campaign.scheduled_at = scheduled
        campaign.message_template_id = payload["template_id"]
        campaign.default_channel = payload.get("channel") or campaign.default_channel or "auto"
        db.commit()
        return AgentResult(
            status="SUCCESS",
            output={
                "campaign_id": str(campaign.id),
                "campaign_name": campaign.name,
                "scheduled_at": scheduled.isoformat(),
                "template_id": payload["template_id"],
                "channel": campaign.default_channel,
            },
            actions_taken=["campaign_scheduled"],
        )

    # ── transactional (one-off, event-triggered) ────────────────────────

    def _send_transactional(
        self, db, payload: dict, MessageLog, TemplateRegistry,
    ) -> AgentResult:
        """One-off email tied to a real event (quote approved, payment
        overdue, stock back in) — not a campaign. Sent immediately,
        bypassing the broadcast/trickle/send-window pacing that exists
        specifically to slow down *bulk* sends: there's exactly one
        recipient here, so there's nothing to burst.

        Still gets: full live-data personalization (all dynamic-email
        sources — Zoho Books/CRM, Apollo, RocketReach, local/cloud mart —
        gated the same way as campaigns, only queried if the template
        references them), an open-tracking pixel, and click-tracked links.

        Does NOT get: the marketing unsubscribe footer/List-Unsubscribe
        header (this is a transactional notice, not bulk marketing mail —
        CAN-SPAM/GDPR both distinguish the two). Hard-bounced addresses
        are still skipped, same suppression list as campaigns.
        """
        import types

        template_id = payload["template_id"]
        to_email = payload["to_email"]
        contact_ref = payload.get("contact_ref") or to_email.split("@")[0]
        event_type = payload.get("event_type") or "transactional"
        extra_ctx = payload.get("context") or {}

        template = db.query(TemplateRegistry).filter(
            TemplateRegistry.template_id == template_id,
        ).first()
        if template is None:
            return AgentResult(
                status="VALIDATION_ERROR",
                error=f"Template '{template_id}' not registered in TemplateRegistry.",
            )
        if template.channel != "email":
            return AgentResult(
                status="VALIDATION_ERROR",
                error=f"Template '{template_id}' is channel={template.channel!r}, not 'email'.",
            )
        if template.approval_status == "pending":
            return AgentResult(
                status="VALIDATION_ERROR",
                error=f"Template '{template_id}' approval_status='pending'. Approve it first.",
            )

        if _is_bounced(db, to_email):
            return AgentResult(
                status="SUCCESS",
                output={
                    "template_id": template_id,
                    "to_email": to_email,
                    "event_type": event_type,
                    "sent": False,
                    "reason": "suppressed_hard_bounce",
                },
                actions_taken=[],
            )

        # Lightweight stand-ins for contact/campaign — _resolve_body,
        # _resolve_subject and the live-context lookup only need a handful
        # of attributes off each, not real rows.
        contact = types.SimpleNamespace(
            contact_ref=contact_ref,
            email=to_email,
            contact_type=payload.get("contact_type") or "dealer",
        )
        campaign = types.SimpleNamespace(
            id=None,
            name=payload.get("campaign_name") or event_type,
            campaign_type=event_type,
        )

        # Same content gate as broadcasts — a transactional notice that ships
        # raw {{tokens}} or a missing attachment is just as wrong.
        gate = _run_content_gate(db, template, campaign, contact, payload)
        if gate is not None:
            return gate

        log = MessageLog(
            direction="out",
            channel="email",
            lane="k3_transactional",
            template_id=template.template_id,
            to_ref=contact_ref,
            email=to_email,
            correlation_type="transactional",
            correlation_ref=event_type,
            status="queued",
        )
        db.add(log)
        db.flush()

        body_html = _resolve_body(template, contact, campaign, db=db, extra_ctx=extra_ctx)
        body_html = _wrap_links_for_click_tracking(
            body_html, str(log.id), contact_ref, "",
        )
        body_html += _open_tracking_pixel_tag(str(log.id), contact_ref, "")
        subject = _resolve_subject(template, contact, campaign, extra_ctx=extra_ctx)

        try:
            _send_smtp_email(to_email, subject, body_html, unsubscribe_url=None,
                             reply_to=getattr(template, "reply_to", None),
                             attachments=getattr(template, "attachments_json", None))
            log.status = "sent"
            db.commit()
        except Exception as exc:
            log.status = "failed"
            db.commit()
            logger.warning("K3 transactional send failed for %s: %s", to_email, exc)
            return AgentResult(
                status="ERROR",
                error=f"Send failed: {exc}",
                output={"message_log_id": str(log.id), "to_email": to_email},
            )

        return AgentResult(
            status="SUCCESS",
            output={
                "message_log_id": str(log.id),
                "template_id": template_id,
                "to_email": to_email,
                "event_type": event_type,
                "sent": True,
            },
            actions_taken=["transactional_email_sent"],
        )

    # ── report ────────────────────────────────────────────────────────

    def _report(self, db, campaign, campaign_id, CampaignContact) -> AgentResult:
        contacts = db.query(CampaignContact).filter(
            CampaignContact.campaign_id == campaign.id
        ).all()

        by_channel: dict[str, int] = {}
        for c in contacts:
            pass  # future: track channel per contact from MessageLog

        return AgentResult(
            status="SUCCESS",
            output={
                "campaign_id": campaign_id,
                "campaign_name": campaign.name,
                "status": campaign.status,
                "scheduled_at": campaign.scheduled_at.isoformat() if campaign.scheduled_at else None,
                "total_audience": len(contacts),
                "sent": sum(1 for c in contacts if c.message_sent_ts),
                "pending": sum(1 for c in contacts if not c.message_sent_ts),
                "responded": sum(1 for c in contacts if c.response_ts),
                "ab_test_enabled": bool(campaign.ab_test_json),
                "sequence_enabled": campaign.sequence_enabled,
            },
            actions_taken=["broadcast_report_generated"],
        )

    # ── drip ──────────────────────────────────────────────────────────

    def _drip(
        self, db, campaign, campaign_id,
        CampaignContact, CampaignSequenceStep, MessageLog, TemplateRegistry, WaContact,
    ) -> AgentResult:
        if getattr(campaign, "ism_managed", False) and not getattr(campaign, "ism_approved_ts", None):
            return AgentResult(
                status="VALIDATION_ERROR",
                error="ISM-managed campaign is awaiting ISM approval on the console (POST /ism/campaigns/{id}/approve). Nothing is sent before that.",
            )
        if not campaign.sequence_enabled:
            return AgentResult(
                status="VALIDATION_ERROR",
                error="Campaign does not have sequence_enabled=True. Add sequence steps first.",
            )

        steps = db.query(CampaignSequenceStep).filter(
            CampaignSequenceStep.campaign_id == campaign.id
        ).order_by(CampaignSequenceStep.step_number).all()

        if not steps:
            return AgentResult(status="VALIDATION_ERROR", error="No sequence steps defined.")

        from datetime import timedelta
        now = datetime.now(timezone.utc)
        triggered = 0
        optouts = _load_optouts(db)
        from app.services.campaign_intel import contact_scores, is_cold_stop
        scores = contact_scores(db, campaign)
        cold_stopped = 0
        from app.services.isr_queue import create_task, today_ist, touch_cap, touches_last_7d
        cap = touch_cap(campaign)
        capped = paused = tasks_created = waiting_on_isr = 0
        held_unapproved: list[str] = []
        from app.core.config import settings as _settings
        email_window_open = _within_email_send_window()
        email_trickle_cap = _settings.k3_email_trickle_batch_size
        email_sent_this_call = deferred_email = 0

        for step in steps:
            template = db.query(TemplateRegistry).filter(
                TemplateRegistry.template_id == step.template_id
            ).first()
            if template is None:
                logger.warning("Drip step %d: template %s not found", step.step_number, step.template_id)
                continue
            if template.approval_status == "pending":
                # Edited or never approved: hold this step until the ISM approves
                # the wording again (console -> Templates).
                held_unapproved.append(step.template_id)
                continue

            # Find contacts due for this step
            candidates = db.query(CampaignContact).filter(
                CampaignContact.campaign_id == campaign.id,
                CampaignContact.sequence_step == step.step_number - 1,
                CampaignContact.message_sent_ts.isnot(None),
            ).all()

            for contact in candidates:
                # Never drip to someone who has unsubscribed.
                if _is_opted_out(contact, optouts):
                    continue
                # Stop on reply by default: once a contact has replied, only
                # steps written for responders ("interested"/"callback")
                # still go out — a person should take over, not the sequence.
                if contact.response_ts and step.condition not in ("interested", "callback"):
                    continue
                if contact.response_type == "not_interested":
                    continue
                if step.condition == "interested" and contact.response_type != "interested":
                    continue
                if step.condition == "callback" and contact.response_type != "callback":
                    continue

                # Check delay
                last_ts = contact.last_step_ts or contact.message_sent_ts
                if last_ts and (now - last_ts) < timedelta(hours=step.delay_hours):
                    continue

                # Out of office: paused until the stated return date.
                pu = (contact.extra_json or {}).get("paused_until")
                if pu and datetime.fromisoformat(pu) > now:
                    paused += 1
                    continue
                # FR-SEQ-04: at most `cap` touches per contact in any 7 days.
                if touches_last_7d(db, contact.contact_ref, now) >= cap:
                    capped += 1
                    continue

                # Manual steps (phone, LinkedIn) are never automated: they
                # become a task in the ISR's queue with the script attached.
                if step.channel in ("call", "linkedin") and (contact.extra_json or {}).get("first_touch") == "call" \
                        and step.template_id == (campaign.ism_brief_json or {}).get("first_call_template"):
                    # Phone-only contact already had this call script as their first touch.
                    contact.sequence_step = step.step_number
                    contact.last_step_ts = now
                    continue
                if step.channel in ("call", "linkedin"):
                    from app.models.enterprise import IsrTask as _IT
                    if db.query(_IT).filter(_IT.contact_ref == contact.contact_ref, _IT.status == "open",
                                            _IT.kind.in_(("call", "linkedin"))).first():
                        # The previous manual touch isn't done yet: wait, don't stack tasks.
                        waiting_on_isr += 1
                        continue
                    import re as _re
                    script = _re.sub(r"<[^>]+>", " ", _resolve_body(template, contact, campaign, db=db))
                    script = _re.sub(r"[ \t]+", " ", script).strip()
                    who = contact.company or contact.name or contact.contact_ref
                    create_task(
                        db, kind=step.channel, contact_ref=contact.contact_ref,
                        account_id=(contact.extra_json or {}).get("account_id"), campaign_id=campaign.id,
                        subject=f"{'Call' if step.channel == 'call' else 'LinkedIn'}: {who} ({campaign.name}, step {step.step_number})",
                        body=(f"{script}\n\nPhone: {contact.msisdn or '-'} | Email: {contact.email or '-'}\n"
                              "LinkedIn steps are done by hand: view the profile, connect with a short note, no pitch."
                              if step.channel == "linkedin" else
                              f"{script}\n\nPhone: {contact.msisdn or '-'} | Email: {contact.email or '-'}\n"
                              "Log the outcome when you complete the task."),
                        due=today_ist(), priority="High", source=f"drip:step{step.step_number}", email=contact.email,
                    )
                    contact.sequence_step = step.step_number
                    contact.last_step_ts = now
                    tasks_created += 1
                    continue

                # Send this step
                wa = db.query(WaContact).filter(
                    WaContact.contact_ref == contact.contact_ref
                ).first()
                effective_ch = self._pick_channel(
                    step.channel, contact.msisdn, contact.email,
                    contact.telegram_chat_id, contact.fcm_token, wa,
                )
                if effective_ch is None:
                    # The contact can't receive this step's channel (e.g. an
                    # email step for a phone-only contact): record it as
                    # skipped so later steps (calls, LinkedIn) still happen.
                    contact.sequence_step = step.step_number
                    contact.last_step_ts = now
                    contact.notes = ((contact.notes + "\n") if contact.notes else "") + \
                        f"[K3] step {step.step_number} ({step.channel}) skipped: no address for that channel."
                    continue
                if effective_ch == "email" and _is_bounced(db, contact.email):
                    # Hard-bounced previously — mark this step done so it
                    # isn't retried on the next drip run, and move on.
                    contact.sequence_step = step.step_number
                    contact.last_step_ts = now
                    continue
                if effective_ch == "email" and (not email_window_open or email_sent_this_call >= email_trickle_cap):
                    # Same pacing as first sends: inside the IST window and a
                    # few per run, so follow-ups never burst either.
                    deferred_email += 1
                    continue
                if effective_ch == "email" and is_cold_stop(scores.get(contact.contact_ref)):
                    # N emails, no open/click/reply — stop emailing them. Mailing
                    # people who never engage hurts inbox placement for the rest.
                    contact.sequence_step = step.step_number
                    contact.last_step_ts = now
                    if "[K3] cold-stop" not in (contact.notes or ""):
                        contact.notes = ((contact.notes + "\n") if contact.notes else "") + \
                            "[K3] cold-stop: no opens/clicks/replies, email follow-ups stopped."
                    cold_stopped += 1
                    continue

                log = MessageLog(
                    direction="out",
                    channel=effective_ch,
                    lane=f"k3_drip_{campaign_id[:8]}_s{step.step_number}",
                    template_id=step.template_id,
                    to_ref=contact.contact_ref,
                    msisdn=contact.msisdn if effective_ch == "wa" else None,
                    email=contact.email if effective_ch == "email" else None,
                    correlation_type="campaign",
                    correlation_ref=campaign_id,
                    status="queued",
                )
                db.add(log)
                db.flush()

                success = self._dispatch(
                    effective_ch, contact, campaign, template, log,
                    contact.msisdn, contact.email,
                    contact.telegram_chat_id, contact.fcm_token,
                    db=db,
                )
                if success:
                    contact.sequence_step = step.step_number
                    contact.last_step_ts = now
                    triggered += 1
                    if effective_ch == "email":
                        email_sent_this_call += 1

        db.commit()
        return AgentResult(
            status="SUCCESS",
            output={"campaign_id": campaign_id, "drip_messages_sent": triggered,
                    "cold_stopped": cold_stopped, "isr_tasks_created": tasks_created,
                    "deferred_touch_cap": capped, "paused_out_of_office": paused,
                    "touch_cap_per_7_days": cap, "deferred_email_pacing": deferred_email,
                    "waiting_on_open_isr_task": waiting_on_isr,
                    "steps_held_template_pending": held_unapproved},
            actions_taken=[f"drip_sent_{triggered}"],
        )

    # ── send ──────────────────────────────────────────────────────────

    def _send(
        self, db, payload, campaign, campaign_id, channel,
        CampaignContact, MessageLog, TemplateRegistry, WaContact,
        step: int = 0,
    ) -> AgentResult:
        if campaign.status not in ("audience_built", "broadcast"):
            return AgentResult(
                status="VALIDATION_ERROR",
                error=f"Campaign must be in audience_built/broadcast status (current: {campaign.status}). Run K2 first.",
            )
        if getattr(campaign, "ism_managed", False) and not getattr(campaign, "ism_approved_ts", None):
            return AgentResult(
                status="VALIDATION_ERROR",
                error="ISM-managed campaign is awaiting ISM approval on the console (POST /ism/campaigns/{id}/approve). Nothing is sent before that.",
            )

        template_id = payload["template_id"]
        template = db.query(TemplateRegistry).filter(
            TemplateRegistry.template_id == template_id,
        ).first()
        if template is None:
            return AgentResult(
                status="VALIDATION_ERROR",
                error=f"Template '{template_id}' not registered in TemplateRegistry.",
            )

        if channel == "whatsapp" and template.approval_status != "meta":
            return AgentResult(
                status="VALIDATION_ERROR",
                error=f"Template '{template_id}' approval_status={template.approval_status!r}. "
                      "WhatsApp broadcasts require approval_status='meta'.",
            )
        if channel == "email" and template.approval_status == "pending":
            return AgentResult(
                status="VALIDATION_ERROR",
                error=f"Template '{template_id}' approval_status='pending'. Approve it first.",
            )

        # A/B test: track variant if campaign has ab_test_json
        ab_enabled = bool(campaign.ab_test_json and
                          (campaign.ab_test_json or {}).get("variants"))

        pending = db.query(CampaignContact).filter(
            CampaignContact.campaign_id == campaign.id,
            CampaignContact.message_sent_ts == None,  # noqa: E711
        ).limit(MAX_BATCH_SIZE).all()

        if not pending:
            return AgentResult(
                status="SUCCESS",
                output={"campaign_id": campaign_id, "message": "All contacts have been messaged."},
                actions_taken=[],
            )

        # Content gate — render the template against a real recipient and
        # refuse to broadcast anything visibly broken. Blocks on leaked
        # tokens, typo'd merge fields, broken Jinja, missing attachments or an
        # empty body (override with force=True + override_reason).
        gate = _run_content_gate(db, template, campaign, pending[0], payload)
        if gate is not None:
            return gate

        now = datetime.now(timezone.utc)
        counts: dict[str, int] = {ch: 0 for ch in VALID_CHANNELS}
        counts["failed"] = 0
        counts["suppressed_bounce"] = 0
        counts["suppressed_optout"] = 0
        counts["deferred_trickle"] = 0
        counts["deferred_outside_window"] = 0

        from app.core.config import settings as _settings
        email_trickle_cap = _settings.k3_email_trickle_batch_size
        email_sent_this_call = 0
        email_window_open = _within_email_send_window()

        # Load the full opt-out set once — O(1) per contact in the loop
        optouts = _load_optouts(db)

        # A/B: send to a test slice first, hold everyone else back, then send
        # the rest the winning variant (see campaign_intel.decide_ab_winner).
        counts["deferred_ab_test"] = 0
        counts["deferred_touch_cap"] = 0
        counts["deferred_paused"] = 0
        counts["deferred_wave"] = 0
        # ISM pilot waves: only contacts in released waves are sent to.
        released_waves = (campaign.ism_brief_json or {}).get("waves_released") \
            if getattr(campaign, "ism_managed", False) else None
        from app.services.isr_queue import touch_cap, touches_last_7d
        cap = touch_cap(campaign)
        ab_winner = None
        ab_slots_left = 0
        ab_assigned: dict[str, int] = {}
        if ab_enabled:
            from app.services.campaign_intel import ab_test_size, decide_ab_winner
            ab_winner = decide_ab_winner(db, campaign)
            if not ab_winner:
                audience = db.query(CampaignContact).filter(
                    CampaignContact.campaign_id == campaign.id).count()
                names = [v.get("name") for v in (campaign.ab_test_json or {}).get("variants", [])
                         if v.get("name")]
                ab_assigned = {n: 0 for n in names}
                for (v,) in db.query(CampaignContact.ab_variant).filter(
                        CampaignContact.campaign_id == campaign.id,
                        CampaignContact.ab_variant.isnot(None)).all():
                    if v in ab_assigned:
                        ab_assigned[v] += 1
                ab_slots_left = max(0, ab_test_size(campaign, audience) - sum(ab_assigned.values()))

        def _template_for(variant: str):
            tid = _get_ab_template_id(campaign, variant, template_id)
            return (db.query(TemplateRegistry).filter(TemplateRegistry.template_id == tid).first()
                    or template)

        for idx, contact in enumerate(pending):
            if _is_opted_out(contact, optouts):
                counts["suppressed_optout"] += 1
                continue
            if released_waves is not None and (contact.extra_json or {}).get("wave") \
                    and contact.extra_json["wave"] not in released_waves:
                counts["deferred_wave"] += 1
                continue

            # A/B: a contact keeps the variant it was assigned even if it was
            # deferred on an earlier tick (previously it silently fell back to
            # the default template, which skewed the test).
            if not ab_enabled:
                eff_template = template
            elif contact.ab_variant:
                eff_template = _template_for(contact.ab_variant)
            elif ab_winner:
                eff_template = _template_for(ab_winner)
            elif ab_slots_left > 0 and ab_assigned:
                contact.ab_variant = min(ab_assigned, key=ab_assigned.get)  # keep groups balanced
                ab_assigned[contact.ab_variant] += 1
                ab_slots_left -= 1
                eff_template = _template_for(contact.ab_variant)
            else:
                counts["deferred_ab_test"] += 1  # waiting for the test result
                continue

            wa = db.query(WaContact).filter(
                WaContact.contact_ref == contact.contact_ref
            ).first()

            effective_ch = self._pick_channel(
                channel, contact.msisdn, contact.email,
                contact.telegram_chat_id, contact.fcm_token, wa,
            )
            if effective_ch is None:
                if getattr(campaign, "ism_managed", False) and contact.msisdn:
                    # ISM campaigns: a phone-only account starts with a call
                    # task (first touch) instead of failing, then follows the
                    # rest of the sequence like everyone else.
                    import re as _re
                    from app.services.isr_queue import create_task, today_ist
                    call_tid = (campaign.ism_brief_json or {}).get("first_call_template")
                    call_tpl = (db.query(TemplateRegistry).filter(TemplateRegistry.template_id == call_tid).first()
                                if call_tid else None) or eff_template
                    script = _re.sub(r"<[^>]+>", " ", _resolve_body(call_tpl, contact, campaign, db=db))
                    script = _re.sub(r"[ \t]+", " ", script).strip()
                    from sqlalchemy.orm.attributes import flag_modified as _fm
                    contact.extra_json = {**(contact.extra_json or {}), "first_touch": "call"}
                    _fm(contact, "extra_json")
                    create_task(
                        db, kind="call", contact_ref=contact.contact_ref,
                        account_id=(contact.extra_json or {}).get("account_id"), campaign_id=campaign.id,
                        subject=f"Call (first touch, no email on file): {contact.company or contact.name or contact.contact_ref} ({campaign.name})",
                        body=f"{script}\n\nPhone: {contact.msisdn}\nAsk for an email address and the right decision-maker; log both in the outcome.",
                        due=today_ist(), priority="High", source="first_touch", email=None,
                    )
                    contact.message_sent_ts = now
                    contact.last_step_ts = now
                    counts["isr_first_touch_calls"] = counts.get("isr_first_touch_calls", 0) + 1
                    continue
                counts["failed"] += 1
                continue

            if effective_ch == "email":
                if _is_bounced(db, contact.email):
                    # Primary bounced — try to promote alt_email before giving up
                    alt = getattr(contact, "alt_email", None)
                    if alt and not _is_bounced(db, alt):
                        contact.email = alt
                        contact.alt_email = None
                    else:
                        # Both addresses bounced (or no alt) — mark handled so campaign completes
                        contact.message_sent_ts = now
                        contact.notes = ((contact.notes + "\n") if contact.notes else "") + \
                            "[K3] Skipped — this address hard-bounced previously (see GET /mail/bounces)."
                        counts["suppressed_bounce"] += 1
                        continue
                if not email_window_open:
                    # Outside the permitted send hours — leave pending for
                    # a later tick rather than sending at an hour that
                    # itself reads as automated.
                    counts["deferred_outside_window"] += 1
                    continue
                if email_sent_this_call >= email_trickle_cap:
                    # Trickle cap for this call/tick reached — leave the
                    # rest pending. With the 5-min scheduler tick, this is
                    # what spreads sends across the day instead of
                    # bursting the whole audience in one shot.
                    counts["deferred_trickle"] += 1
                    continue

            pu = (contact.extra_json or {}).get("paused_until")
            if pu and datetime.fromisoformat(pu) > now:
                counts["deferred_paused"] += 1
                continue
            if touches_last_7d(db, contact.contact_ref, now) >= cap:
                counts["deferred_touch_cap"] += 1
                continue

            log = MessageLog(
                direction="out",
                channel=effective_ch,
                lane=f"k3_broadcast_{campaign_id[:8]}",
                template_id=eff_template.template_id,
                to_ref=contact.contact_ref,
                msisdn=contact.msisdn if effective_ch == "wa" else None,
                email=contact.email if effective_ch == "email" else None,
                correlation_type="campaign",
                correlation_ref=campaign_id,
                status="queued",
            )
            db.add(log)
            db.flush()

            msisdn = contact.msisdn or (wa.msisdn if wa else None)
            success = self._dispatch(
                effective_ch, contact, campaign, eff_template, log,
                msisdn, contact.email, contact.telegram_chat_id, contact.fcm_token,
                db=db,
            )
            if success:
                contact.message_sent_ts = now
                contact.message_log_id = log.id
                contact.last_step_ts = now
                counts[effective_ch] = counts.get(effective_ch, 0) + 1
                if effective_ch == "email":
                    email_sent_this_call += 1
            else:
                counts["failed"] += 1

        campaign.status = "broadcast"
        db.commit()

        _non_channel_keys = (
            "failed", "suppressed_bounce", "suppressed_optout", "deferred_trickle",
            "deferred_outside_window", "deferred_ab_test", "deferred_touch_cap", "deferred_paused",
            "isr_first_touch_calls", "deferred_wave",
        )
        total_sent = sum(v for k, v in counts.items() if k not in _non_channel_keys)
        remaining = db.query(CampaignContact).filter(
            CampaignContact.campaign_id == campaign.id,
            CampaignContact.message_sent_ts == None,  # noqa: E711
        ).count()

        return AgentResult(
            status="SUCCESS",
            output={
                "campaign_id": campaign_id,
                "campaign_name": campaign.name,
                "channel_mode": channel,
                "sent_total": total_sent,
                "by_channel": {k: v for k, v in counts.items()
                               if v > 0 and k not in _non_channel_keys},
                "failed": counts["failed"],
                "suppressed_bounce": counts["suppressed_bounce"],
                "suppressed_optout": counts["suppressed_optout"],
                # Not sent yet, but not failures either — see next tick.
                "deferred_email_trickle_cap": counts["deferred_trickle"],
                "deferred_email_outside_window": counts["deferred_outside_window"],
                "remaining_unsent": remaining,
                "batch_cap": MAX_BATCH_SIZE,
                "email_trickle_cap_per_call": email_trickle_cap,
                "more_batches_needed": remaining > 0,
                "ab_test_active": ab_enabled,
                "ab_winner": ab_winner,
                "deferred_ab_test": counts["deferred_ab_test"],
                "deferred_touch_cap": counts["deferred_touch_cap"],
                "deferred_paused": counts["deferred_paused"],
                "isr_first_touch_calls": counts.get("isr_first_touch_calls", 0),
                "deferred_wave_not_released": counts["deferred_wave"],
            },
            actions_taken=[f"broadcast_sent_{total_sent}_messages"],
        )

    # ── dispatch ──────────────────────────────────────────────────────

    def _dispatch(
        self, channel: str, contact, campaign, template, log,
        msisdn: str | None, email_addr: str | None,
        telegram_chat_id: str | None, fcm_token: str | None,
        db=None,
    ) -> bool:
        """Send via the given channel. Returns True on success, False on error."""
        try:
            if channel == "wa":
                from mcp_servers.whatsapp.server import send_template as _waba, WhatsAppError
                params = _build_template_params(template, contact, campaign)
                bsp = _waba(
                    to=msisdn,
                    template_name=template.template_id,
                    language=template.language or "en",
                    params=params,
                )
                log.status = "sent"
                log.provider_msg_id = _extract_provider_msg_id(bsp)

            elif channel == "email":
                body_html = _resolve_body(template, contact, campaign, db=db)
                # Rewrite every link for click tracking before adding the
                # unsubscribe footer (which is tracked separately by the
                # unsubscribe portal itself, not as a campaign "click").
                body_html = _wrap_links_for_click_tracking(
                    body_html, str(log.id), contact.contact_ref, str(campaign.id),
                )
                unsub = _build_unsubscribe_link(contact.contact_ref, str(campaign.id))
                body_html += _email_footer(unsub)
                # Open-tracking pixel — last, so it never affects visible content.
                body_html += _open_tracking_pixel_tag(str(log.id), contact.contact_ref, str(campaign.id))
                subject = _resolve_subject(template, contact, campaign)
                reply_to = getattr(template, "reply_to", None)
                attachments = getattr(template, "attachments_json", None)
                try:
                    _send_smtp_email(email_addr, subject, body_html,
                                     unsubscribe_url=unsub, reply_to=reply_to,
                                     attachments=attachments)
                    log.status = "sent"
                except Exception as _primary_exc:
                    alt = getattr(contact, "alt_email", None)
                    if alt and alt != email_addr:
                        logger.warning(
                            "K3 primary email %s failed (%s) — retrying with alt_email %s",
                            email_addr, _primary_exc, alt,
                        )
                        _send_smtp_email(alt, subject, body_html,
                                         unsubscribe_url=unsub, reply_to=reply_to,
                                         attachments=attachments)
                        # Alt succeeded — promote it so future sends/drips use it directly
                        log.email = alt
                        log.status = "sent"
                        contact.email = alt
                        contact.alt_email = None
                    else:
                        raise

            elif channel == "telegram":
                from mcp_servers.telegram.server import send_message as _tg, TelegramError
                text = _resolve_body(template, contact, campaign, db=db)
                # Add opt-out link to Telegram message
                unsub = _build_unsubscribe_link(contact.contact_ref, str(campaign.id))
                text += f'\n\n<a href="{unsub}">Unsubscribe</a>'
                result = _tg(chat_id=telegram_chat_id, text=text)
                log.status = "sent"
                log.provider_msg_id = str(result.get("message_id", ""))

            elif channel == "voice":
                from mcp_servers.voice.server import make_call as _voice, VoiceError
                tts_text = _resolve_body(template, contact, campaign, db=db)
                # Strip HTML for voice TTS
                import re
                tts_clean = re.sub(r"<[^>]+>", " ", tts_text).strip()
                result = _voice(to=msisdn, tts_text=tts_clean)
                log.status = "sent"

            elif channel == "push":
                from app.integrations.fcm import send_push as _push, FcmError
                title = template.purpose or campaign.name
                body_text = _resolve_body(template, contact, campaign, db=db)
                import re
                body_clean = re.sub(r"<[^>]+>", " ", body_text).strip()[:500]
                _push(
                    fcm_token=fcm_token,
                    title=title,
                    body=body_clean,
                    data={"campaign_id": str(campaign.id), "contact_ref": contact.contact_ref},
                )
                log.status = "sent"

            elif channel == "linkedin":
                self._send_linkedin(contact, campaign)
                log.status = "sent"

            else:
                log.status = "failed"
                return False

            return True

        except Exception as exc:
            log.status = "failed"
            logger.warning(
                "K3 %s send failed for contact %s: %s",
                channel, contact.contact_ref, exc,
            )
            return False

    def _send_linkedin(self, contact, campaign) -> None:
        """Enroll dealer in Apollo LinkedIn sequence."""
        from app.core.config import settings
        import httpx
        sequence_id = settings.apollo_linkedin_sequence_id
        if not sequence_id:
            raise ValueError("apollo_linkedin_sequence_id not configured")
        if not settings.apollo_api_key:
            raise ValueError("apollo_api_key not configured")

        resp = httpx.post(
            "https://api.apollo.io/v1/emailer_campaigns/add_contact_ids",
            headers={"Content-Type": "application/json", "Cache-Control": "no-cache"},
            json={
                "api_key": settings.apollo_api_key,
                "id": sequence_id,
                "contact_ids": [contact.contact_ref],
            },
            timeout=15,
        )
        if resp.status_code not in (200, 201):
            raise ValueError(f"Apollo sequence enroll failed: {resp.status_code} {resp.text[:300]}")

    # ── channel picker ────────────────────────────────────────────────

    @staticmethod
    def _pick_channel(
        requested: str,
        msisdn: str | None,
        email_addr: str | None,
        telegram_chat_id: str | None,
        fcm_token: str | None,
        wa,
    ) -> str | None:
        """Return effective internal channel string or None to skip contact."""
        wa_opted_out = wa is not None and wa.opt_in_status == "opt_out"

        if requested == "whatsapp":
            if not msisdn or wa_opted_out:
                return None
            return "wa"

        if requested == "email":
            # Respect opt-out even for email-only contacts
            if wa_opted_out:
                return None
            return "email" if email_addr else None

        if wa_opted_out:
            return None  # contact opted out — skip on any channel

        if requested == "telegram":
            return "telegram" if telegram_chat_id else None

        if requested == "voice":
            return "voice" if msisdn else None

        if requested == "push":
            return "push" if fcm_token else None

        if requested == "linkedin":
            return "linkedin"

        # auto: pick best available channel
        if msisdn and not wa_opted_out:
            return "wa"
        if telegram_chat_id:
            return "telegram"
        if email_addr:
            return "email"
        if fcm_token:
            return "push"
        if msisdn:
            return "voice"
        return None
