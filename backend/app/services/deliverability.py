"""Sender-authentication checker: SPF, DKIM and DMARC for the sending domain.

Since 2024 Gmail/Yahoo reject bulk mail that is not authenticated and aligned,
so these three DNS records are the single biggest factor in whether a campaign
lands in the inbox or in spam. This runs live DNS lookups and reports what is
configured, what is missing, and how to fix it — so it can be checked before a
campaign goes out (see GET /mail/deliverability).

Everything here is read-only DNS; nothing is sent.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Selectors to probe when the operator hasn't told us theirs. DKIM has no way
# to discover the selector from DNS alone — it's embedded in the signature of a
# sent message — so "not found" never fully proves DKIM is absent (a custom
# selector may exist). These cover the common mail hosts.
_COMMON_SELECTORS = [
    "default", "google", "selector1", "selector2", "s1", "s2",
    "k1", "k2", "dkim", "mail", "smtp", "mx", "key1", "zoho", "cpanel",
]

_STATUS_RANK = {"pass": 0, "warn": 1, "fail": 2}


def _resolver():
    import dns.resolver
    r = dns.resolver.Resolver()
    r.timeout = 3.0
    r.lifetime = 6.0
    return r


def _txt(resolver, name: str) -> list[str]:
    """All TXT strings at `name`, joined per record. [] on any lookup failure
    (NXDOMAIN, no answer, timeout) — a checker treats 'no record' as the
    answer, not an error."""
    try:
        answer = resolver.resolve(name, "TXT")
    except Exception:
        return []
    out: list[str] = []
    for rdata in answer:
        try:
            out.append(b"".join(rdata.strings).decode("utf-8", "replace"))
        except Exception:
            out.append(str(rdata).strip('"'))
    return out


def _check_spf(resolver, domain: str) -> dict:
    records = [t for t in _txt(resolver, domain) if t.lower().startswith("v=spf1")]
    if not records:
        return {"status": "fail", "found": False, "record": None,
                "message": "No SPF record. Add a TXT record 'v=spf1 ... -all' "
                           "authorising your mail server to send for this domain."}
    if len(records) > 1:
        return {"status": "fail", "found": True, "record": records,
                "message": "Multiple SPF records — this is invalid and most "
                           "servers then ignore SPF entirely. Merge into one."}
    rec = records[0]
    low = rec.lower()
    if low.endswith("-all"):
        msg, status = "SPF present with a hard fail-all (-all). Strongest.", "pass"
    elif low.endswith("~all"):
        msg, status = "SPF present with soft fail (~all). Acceptable.", "pass"
    elif "+all" in low or low.endswith("?all"):
        msg, status = ("SPF present but ends with a permissive qualifier "
                       "(+all/?all) — effectively no protection. Use -all or ~all.", "warn")
    else:
        msg, status = ("SPF present but has no 'all' mechanism — unknown senders "
                       "aren't rejected. Append ' -all' (or ' ~all').", "warn")
    return {"status": status, "found": True, "record": rec, "message": msg}


def _check_dmarc(resolver, domain: str) -> dict:
    records = [t for t in _txt(resolver, f"_dmarc.{domain}")
               if t.lower().startswith("v=dmarc1")]
    if not records:
        return {"status": "fail", "found": False, "record": None, "policy": None,
                "message": "No DMARC record. Add a TXT record at "
                           f"'_dmarc.{domain}' e.g. 'v=DMARC1; p=quarantine; "
                           "rua=mailto:dmarc@" + domain + "'."}
    rec = records[0]
    policy = None
    for part in rec.split(";"):
        k, _, v = part.strip().partition("=")
        if k.strip().lower() == "p":
            policy = v.strip().lower()
            break
    if policy in ("quarantine", "reject"):
        status = "pass"
        msg = f"DMARC present with an enforcing policy (p={policy}). Good."
    elif policy == "none":
        status = "warn"
        msg = ("DMARC present but p=none — monitoring only, it does not protect "
               "deliverability. Once SPF+DKIM are aligned, move to p=quarantine "
               "then p=reject.")
    else:
        status = "warn"
        msg = f"DMARC present but policy is unclear (p={policy!r})."
    return {"status": status, "found": True, "record": rec, "policy": policy, "message": msg}


def _check_dkim(resolver, domain: str, selectors: list[str]) -> dict:
    checked, found = [], []
    seen = set()
    for sel in selectors:
        sel = (sel or "").strip()
        if not sel or sel in seen:
            continue
        seen.add(sel)
        checked.append(sel)
        txts = _txt(resolver, f"{sel}._domainkey.{domain}")
        if any("v=dkim1" in t.lower() or "p=" in t for t in txts):
            found.append(sel)
    if found:
        return {"status": "pass", "found": True,
                "selectors_found": found, "selectors_checked": checked,
                "message": f"DKIM key found at selector(s): {', '.join(found)}."}
    return {"status": "fail", "found": False,
            "selectors_found": [], "selectors_checked": checked,
            "message": "No DKIM key at the selectors checked "
                       f"({', '.join(checked)}). DKIM is required for bulk mail. "
                       "If you use a custom selector, re-check with "
                       "?selector=<yours>. Otherwise enable DKIM on your mail host."}


def check_deliverability(domain: str | None = None, extra_selectors: str | None = None) -> dict:
    """Check SPF/DKIM/DMARC for `domain` (default: the domain of smtp_from).

    extra_selectors: comma-separated DKIM selectors to try first, in addition
    to settings.dkim_selectors and a common-selector probe list.
    """
    from app.core.config import settings

    from_addr = settings.smtp_from or ""
    if not domain:
        domain = from_addr.split("@")[-1].strip() if "@" in from_addr else from_addr.strip()
    domain = (domain or "").strip().lower().rstrip(".")
    if not domain:
        return {"error": "No domain to check — set SMTP_FROM or pass ?domain="}

    selectors: list[str] = []
    for src in (extra_selectors, settings.dkim_selectors):
        if src:
            selectors.extend(s for s in str(src).split(",") if s.strip())
    selectors.extend(_COMMON_SELECTORS)

    try:
        resolver = _resolver()
    except Exception as exc:  # dnspython missing / misconfigured
        logger.warning("Deliverability check could not start: %s", exc)
        return {"error": f"DNS resolver unavailable: {exc}", "domain": domain}

    spf = _check_spf(resolver, domain)
    dmarc = _check_dmarc(resolver, domain)
    dkim = _check_dkim(resolver, domain, selectors)

    overall = max((spf["status"], dmarc["status"], dkim["status"]),
                  key=lambda s: _STATUS_RANK.get(s, 0))

    notes = []
    if overall == "fail":
        notes.append("Bulk mail to Gmail/Yahoo will likely be rejected or "
                     "spam-filtered until the failing records are fixed.")
    if dmarc.get("policy") == "none" and spf["status"] == "pass" and dkim["status"] == "pass":
        notes.append("SPF and DKIM look good — tightening DMARC from p=none to "
                     "p=quarantine is the next step.")

    return {
        "domain": domain,
        "from_address": from_addr or None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "overall": overall,
        "spf": spf,
        "dkim": dkim,
        "dmarc": dmarc,
        "notes": notes,
    }
