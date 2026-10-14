"""Input validation for user registration.

Three layers for email:
  1. Domain blocklist — free, personal, temp/disposable providers
  2. DNS MX check    — domain must have live mail servers
  3. Format check    — basic RFC-5322 structure

Phone / WhatsApp:
  - Google libphonenumber (phonenumbers) — validates format,
    country code, and number type (mobile preferred for WA)
"""

import re
import socket
from functools import lru_cache

# ---------------------------------------------------------------------------
# Email — blocked domains
# ---------------------------------------------------------------------------

# Free / personal providers
_FREE_DOMAINS: set[str] = {
    "gmail.com", "googlemail.com",
    "yahoo.com", "yahoo.in", "yahoo.co.in", "yahoo.co.uk", "yahoo.co.id",
    "hotmail.com", "hotmail.in", "hotmail.co.uk",
    "outlook.com", "outlook.in",
    "live.com", "live.in", "live.co.uk",
    "rediffmail.com",
    "icloud.com", "me.com", "mac.com",
    "aol.com",
    "protonmail.com", "proton.me",
    "tutanota.com", "tuta.io",
    "zoho.com",          # personal Zoho — company Zoho uses their own domain
    "in.com", "sify.com",
}

# Known temp / disposable providers (curated subset — extend as needed)
_TEMP_DOMAINS: set[str] = {
    "mailinator.com", "guerrillamail.com", "guerrillamail.info",
    "guerrillamailblock.com", "grr.la", "sharklasers.com",
    "spam4.me", "trashmail.com", "trashmail.net", "trashmail.io",
    "dispostable.com", "yopmail.com", "yopmail.fr",
    "maildrop.cc", "spamgourmet.com",
    "10minutemail.com", "10minutemail.net", "10minutemail.org",
    "fakeinbox.com", "mailnull.com", "spambox.us",
    "discard.email", "throwaway.email",
    "tempmail.com", "temp-mail.org", "tmpmail.net",
    "getairmail.com", "mailtemp.info", "tempr.email",
    "nwytg.com", "armyspy.com", "cuvox.de",
    "dayrep.com", "einrot.com", "fleckens.hu",
    "gustr.com", "jourrapide.com", "rhyta.com",
    "superrito.com", "teleworm.us",
}

_BLOCKED_DOMAINS: set[str] = _FREE_DOMAINS | _TEMP_DOMAINS

_EMAIL_RE = re.compile(r"^[^@\s]+@([^@\s]+\.[^@\s]+)$", re.IGNORECASE)


@lru_cache(maxsize=256)
def _mx_exists(domain: str) -> bool:
    """Return True if domain has at least one MX record."""
    try:
        import dns.resolver
        dns.resolver.resolve(domain, "MX", lifetime=5)
        return True
    except Exception:
        # Fallback: plain A-record lookup (some small domains skip MX)
        try:
            socket.getaddrinfo(domain, None)
            return True
        except OSError:
            return False


def validate_email(email: str, allowed_domains: list[str] | None = None) -> str:
    """Validate and normalise an email address.

    Args:
        email:           Raw input from the user.
        allowed_domains: If provided, ONLY these domains are accepted
                         (e.g. ["yexis.com", "yexiselectronics.com"]).
                         When None, all non-blocked domains are accepted.

    Returns the lowercased email on success.
    Raises ValueError with a human-readable message on failure.
    """
    email = email.strip().lower()

    m = _EMAIL_RE.match(email)
    if not m:
        raise ValueError("Invalid email format.")

    domain = m.group(1).lower()

    if allowed_domains:
        allowed_lower = {d.lower() for d in allowed_domains}
        if domain not in allowed_lower:
            allowed_str = ", ".join(sorted(allowed_lower))
            raise ValueError(
                f"Only company email addresses are accepted ({allowed_str})."
            )
    else:
        if domain in _TEMP_DOMAINS:
            raise ValueError("Disposable / temporary email addresses are not allowed.")
        if domain in _FREE_DOMAINS:
            raise ValueError(
                "Personal email addresses (Gmail, Yahoo, Hotmail, etc.) are not allowed. "
                "Please use your company email."
            )

    if not _mx_exists(domain):
        raise ValueError(f"The domain '{domain}' does not appear to have a mail server. "
                         "Please check the email address.")

    return email


# ---------------------------------------------------------------------------
# Phone / WhatsApp number validation
# ---------------------------------------------------------------------------

def validate_phone(number: str, label: str = "Phone number") -> str:
    """Validate and normalise a phone number to E.164.

    Args:
        number: Raw input — may include spaces, dashes, brackets.
        label:  Field name used in error messages.

    Returns the E.164 string (e.g. "+919876543210") on success.
    Raises ValueError on failure.
    """
    try:
        import phonenumbers
        from phonenumbers import PhoneNumberType
    except ImportError:
        raise RuntimeError("phonenumbers library not installed — run: pip install phonenumbers")

    raw = number.strip()

    # If no leading +, assume India (+91) as default country
    if not raw.startswith("+"):
        raw = "+91" + raw.lstrip("0")

    try:
        parsed = phonenumbers.parse(raw)
    except phonenumbers.NumberParseException as exc:
        raise ValueError(f"{label}: could not parse — {exc}")

    if not phonenumbers.is_valid_number(parsed):
        raise ValueError(f"{label}: '{number}' is not a valid phone number.")

    if not phonenumbers.is_possible_number(parsed):
        raise ValueError(f"{label}: '{number}' does not look like a real number.")

    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def validate_whatsapp(number: str) -> str:
    """Like validate_phone but additionally warns if the number type
    is unlikely to support WhatsApp (e.g. landline / fixed-line).

    Returns E.164 string.
    Raises ValueError on hard failures; landline numbers raise ValueError too
    since WhatsApp requires a mobile-capable number.
    """
    try:
        import phonenumbers
        from phonenumbers import PhoneNumberType, number_type
    except ImportError:
        raise RuntimeError("phonenumbers library not installed")

    e164 = validate_phone(number, label="WhatsApp number")
    raw = number.strip()
    if not raw.startswith("+"):
        raw = "+91" + raw.lstrip("0")

    parsed = phonenumbers.parse(raw)
    ntype = number_type(parsed)

    MOBILE_TYPES = {
        PhoneNumberType.MOBILE,
        PhoneNumberType.FIXED_LINE_OR_MOBILE,
        PhoneNumberType.PERSONAL_NUMBER,
        PhoneNumberType.VOIP,
    }
    if ntype not in MOBILE_TYPES:
        raise ValueError(
            "WhatsApp number must be a mobile number, not a landline."
        )

    return e164
