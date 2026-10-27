"""Scribd — download contact documents for K2 to parse.

Scribd hosts documents (PDF / Excel / CSV). This fetches a document the server
can actually reach: a direct or shared download URL, or a document the
configured account has rights to.

Reality check: many Scribd documents are paywalled or require a logged-in
session and cannot be downloaded programmatically. When a URL returns an HTML
viewer page instead of a file, the document must be downloaded manually and
brought in through the Excel/CSV upload path instead.
"""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class ScribdError(Exception):
    pass


def fetch_document(url: str) -> tuple[bytes, str]:
    """Download a reachable document URL. Returns (content_bytes, suffix)
    where suffix is one of .pdf / .csv / .xlsx."""
    if not url:
        raise ScribdError("A document URL is required for the Scribd source")

    headers = {"User-Agent": "Mozilla/5.0 (compatible; YexisAudienceBot/1.0)"}
    if settings.scribd_api_key:
        headers["Authorization"] = f"Bearer {settings.scribd_api_key}"

    try:
        r = httpx.get(url, headers=headers, timeout=30, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise ScribdError(
            f"HTTP {exc.response.status_code} fetching document"
        ) from exc
    except Exception as exc:
        raise ScribdError(str(exc)) from exc

    ctype = r.headers.get("content-type", "").lower()
    low = url.lower()
    if "pdf" in ctype or low.endswith(".pdf"):
        suffix = ".pdf"
    elif "csv" in ctype or low.endswith(".csv"):
        suffix = ".csv"
    elif "spreadsheet" in ctype or "excel" in ctype or low.endswith((".xlsx", ".xls")):
        suffix = ".xlsx"
    elif "html" in ctype:
        raise ScribdError(
            "URL returned an HTML page, not a file — the document is likely behind "
            "Scribd login/paywall. Download it manually and use Upload contacts."
        )
    else:
        # Default to PDF: Scribd's most common document type.
        suffix = ".pdf"
    return r.content, suffix


def health() -> dict:
    return {
        "configured": True,
        "hint": (
            "Pass a reachable 'document_url' when building with source=scribd. "
            "Paywalled Scribd docs must be downloaded manually and uploaded."
        ),
    }
