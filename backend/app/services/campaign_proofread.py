"""AI proofreading for campaign copy — catches the spelling/grammar mistakes
a structural validator never could.

This is author-time only (surfaced via POST /templates/{id}/validate) and
NEVER blocks a send: a language model can false-flag brand names, product
SKUs and Indian-English usage, so its output is advisory. Spelling is a
human-review aid; the hard send gate is campaign_lint (structural).

Uses the provider-neutral model client (app.services.model_provider), so it
works with whatever MODEL_PROVIDER is configured. For Claude Haiku set
MODEL_PROVIDER=anthropic, MODEL_NAME=claude-haiku-4-5. When no provider is
configured it returns {"available": False, ...} rather than failing.
"""

import hashlib
import json
import logging
import re

logger = logging.getLogger(__name__)

# Proofread results cached by a hash of the exact content, so repeated sends
# of the same wording (e.g. a scheduled campaign re-firing every 5 min as it
# trickles out) don't re-call the model. Only genuine (available) results are
# cached, never a transient "provider unavailable".
_CACHE: dict[str, dict] = {}

_SYSTEM = (
    "You are a meticulous proofreader for customer-facing marketing email at "
    "Yexis Electronics, an Indian electronics distributor. You are given the "
    "subject line and the plain-text body of one email.\n\n"
    "Report ONLY genuine errors that would embarrass the sender:\n"
    "  - misspellings and typos\n"
    "  - grammar and punctuation mistakes\n"
    "  - doubled words ('the the'), missing/extra spaces\n"
    "  - obvious leftover placeholder or draft text\n\n"
    "Do NOT flag, and do NOT rewrite for, any of the following:\n"
    "  - brand names, company names, product model numbers or SKUs\n"
    "  - Indian English spelling and phrasing (e.g. 'kindly', 'prepone')\n"
    "  - personalization placeholders in double braces like {{ name }}\n"
    "  - style, tone or marketing preference\n\n"
    "Respond with ONLY a JSON object, no prose:\n"
    '{"issues":[{"text":"<the exact wrong text>","problem":"<what is wrong>",'
    '"fix":"<the correction>"}]}\n'
    "Return an empty issues array if the copy is clean."
)


def _html_to_text(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html or "", flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _parse_issues(raw: str) -> list[dict]:
    """Pull the JSON object out of the model's reply, defensively."""
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(raw[start:end + 1])
    except (ValueError, TypeError):
        return []
    issues = data.get("issues") if isinstance(data, dict) else None
    if not isinstance(issues, list):
        return []
    clean = []
    for it in issues:
        if isinstance(it, dict) and it.get("text"):
            clean.append({
                "text": str(it.get("text", ""))[:300],
                "problem": str(it.get("problem", ""))[:300],
                "fix": str(it.get("fix", ""))[:300],
            })
    return clean


def proofread(subject: str, body_html: str) -> dict:
    """Return {available, issues, model, reason}. available=False means the
    model provider is off or unreachable — the caller should surface that the
    spelling check could not run, not treat the copy as clean."""
    from app.services.model_provider import (
        ChatMessage, ModelProviderError, get_model_client,
    )

    body_text = _html_to_text(body_html)
    if not body_text and not (subject or "").strip():
        return {"available": True, "issues": [], "model": None, "reason": "empty"}

    try:
        client = get_model_client()
    except Exception as exc:  # provider disabled / misconfigured
        logger.info("Proofread unavailable: %s", exc)
        return {
            "available": False,
            "issues": [],
            "model": None,
            "reason": "No model provider configured. Set MODEL_PROVIDER / "
                      "MODEL_NAME / MODEL_API_KEY to enable AI proofreading.",
        }

    user = f"SUBJECT: {subject or '(none)'}\n\nBODY:\n{body_text}"
    try:
        resp = client.complete(
            messages=[ChatMessage(role="user", content=user)],
            system=_SYSTEM,
            max_tokens=1024,
            temperature=0,
        )
    except ModelProviderError as exc:
        logger.warning("Proofread model call failed: %s", exc)
        return {"available": False, "issues": [], "model": None,
                "reason": f"Proofread service error: {exc}"}

    return {
        "available": True,
        "issues": _parse_issues(resp.content),
        "model": resp.model,
        "reason": None,
    }


def proofread_cached(subject: str, body_html: str) -> dict:
    """proofread(), memoised on a hash of the exact subject+body. Used by the
    send gate so a campaign trickling out over many ticks is proofread once,
    not on every tick. Unavailable results are not cached (so enabling the
    provider later takes effect immediately)."""
    key = hashlib.sha256(
        ((subject or "") + "\x00" + (body_html or "")).encode("utf-8")
    ).hexdigest()
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    result = proofread(subject, body_html)
    if result.get("available"):
        _CACHE[key] = result
    return result
