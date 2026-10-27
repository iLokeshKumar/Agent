"""Zoho Books API v3 client.

Handles OAuth token management, automatic refresh,
and provides methods for journals, invoices, bills,
contacts, chart of accounts, and organizations.

Docs: https://www.zoho.com/books/api/v3/
"""

import json
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings


logger = logging.getLogger(__name__)
_zoho_user = ContextVar("zoho_user", default=None)


@contextmanager
def zoho_user_context(username: str | None):
    """Select the requesting user's Zoho connection for this execution."""
    token = _zoho_user.set(username)
    try:
        yield
    finally:
        _zoho_user.reset(token)

# Zoho data center base URLs
# India moved to zohoapis.in domain (books.zoho.in returns code 9 "use zohoapis domain")
ZOHO_BOOKS_BASES = {
    "us": "https://www.zohoapis.com/books/v3",
    "eu": "https://www.zohoapis.eu/books/v3",
    "in": "https://www.zohoapis.in/books/v3",
    "com.au": "https://www.zohoapis.com.au/books/v3",
    "jp": "https://www.zohoapis.jp/books/v3",
}

# Zoho OAuth endpoints per data center
ZOHO_TOKEN_URLS = {
    "us": "https://accounts.zoho.com/oauth/v2/token",
    "eu": "https://accounts.zoho.eu/oauth/v2/token",
    "in": "https://accounts.zoho.in/oauth/v2/token",
    "com.au": "https://accounts.zoho.com.au/oauth/v2/token",
    "jp": "https://accounts.zoho.jp/oauth/v2/token",
}

# Buffer seconds before actual expiry to trigger refresh
TOKEN_REFRESH_BUFFER = 300  # 5 minutes

# Where to persist tokens on disk
TOKEN_FILE = Path(__file__).resolve().parent.parent.parent / "config" / "zoho_tokens.json"


class ZohoBooksError(Exception):
    """Zoho Books API error."""
    def __init__(self, status_code: int, message: str, code: str | None = None):
        self.status_code = status_code
        self.code = code
        super().__init__(f"Zoho Books {status_code}: {message}")


class ZohoBooksClient:
    """Zoho Books API client with automatic token refresh."""

    def __init__(
        self,
        access_token: str | None = None,
        refresh_token: str | None = None,
        organization_id: str | None = None,
        data_center: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        token_save_callback=None,
    ):
        self.access_token = settings.zoho_access_token if access_token is None else access_token
        self.refresh_token = settings.zoho_refresh_token if refresh_token is None else refresh_token
        self.organization_id = settings.zoho_org_id if organization_id is None else organization_id
        self.data_center = settings.zoho_data_center if data_center is None else data_center
        self.client_id = settings.zoho_client_id if client_id is None else client_id
        self.client_secret = settings.zoho_client_secret if client_secret is None else client_secret
        self._token_save_callback = token_save_callback

        self._token_expiry: float = 0  # epoch when token expires

        self.base_url = ZOHO_BOOKS_BASES.get(
            self.data_center, ZOHO_BOOKS_BASES["us"]
        )
        self.token_url = ZOHO_TOKEN_URLS.get(
            self.data_center, ZOHO_TOKEN_URLS["us"]
        )

        # Load persisted tokens
        self._load_tokens()

    @property
    def configured(self) -> bool:
        """Check if all required credentials are set."""
        return bool(
            self.access_token
            and self.refresh_token
            and self.organization_id
            and self.client_id
            and self.client_secret
        )

    # ----------------------------------------------------------------
    # Token persistence
    # ----------------------------------------------------------------

    def _load_tokens(self):
        """Load tokens from disk if available."""
        if self._token_save_callback is not None:
            return
        if not TOKEN_FILE.exists():
            return

        try:
            data = json.loads(TOKEN_FILE.read_text())
            if data.get("access_token"):
                self.access_token = data["access_token"]
            if data.get("refresh_token"):
                self.refresh_token = data["refresh_token"]
            # .env ZOHO_ORG_ID is authoritative — only fall back to
            # tokens.json org_id when .env has nothing set.
            if not settings.zoho_org_id and data.get("organization_id"):
                self.organization_id = data["organization_id"]
            if data.get("token_expiry"):
                self._token_expiry = data["token_expiry"]
            logger.info("Loaded Zoho tokens from %s", TOKEN_FILE)
        except Exception:
            logger.warning("Could not load Zoho tokens from %s", TOKEN_FILE)

    def save_tokens(self):
        """Persist tokens to disk."""
        if self._token_save_callback is not None:
            self._token_save_callback(
                self.access_token,
                self.refresh_token,
                self.organization_id,
            )
            return
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            # Always prefer .env org_id so stale file values never win
            "organization_id": settings.zoho_org_id or self.organization_id,
            "token_expiry": self._token_expiry,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }

        TOKEN_FILE.write_text(json.dumps(data, indent=2))
        logger.info("Saved Zoho tokens to %s", TOKEN_FILE)

    def set_tokens(
        self,
        access_token: str,
        refresh_token: str,
        expires_in: int = 3600,
        organization_id: str | None = None,
    ):
        """Set tokens from OAuth callback and persist."""
        self.access_token = access_token
        self.refresh_token = refresh_token
        self._token_expiry = time.time() + expires_in
        if organization_id:
            self.organization_id = organization_id
        self.save_tokens()

    def clear_tokens(self):
        """Clear all tokens (revoke)."""
        self.access_token = ""
        self.refresh_token = ""
        self._token_expiry = 0
        if TOKEN_FILE.exists():
            TOKEN_FILE.unlink()
        logger.info("Cleared Zoho tokens")

    def _ensure_token(self):
        """Refresh access token if expired or about to expire."""
        if not self.configured:
            raise ZohoBooksError(
                0,
                "Zoho Books not configured. "
                "Set ZOHO_* environment variables.",
            )

        if time.time() < self._token_expiry - TOKEN_REFRESH_BUFFER:
            return

        self._refresh_access_token()

    def ensure_authenticated(self):
        """Ensure a valid access token before starting a multi-request operation."""
        self._ensure_token()

    def _refresh_access_token(self):
        """Refresh the OAuth access token."""
        logger.info("Refreshing Zoho Books access token")

        try:
            response = httpx.post(
                self.token_url,
                data={
                    "refresh_token": self.refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "refresh_token",
                },
                timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
            )
            response.raise_for_status()

            data = response.json()
            self.access_token = data["access_token"]

            # Zoho tokens typically last 1 hour
            expires_in = data.get("expires_in", 3600)
            self._token_expiry = time.time() + expires_in

            # Zoho may return a new refresh token
            if "refresh_token" in data:
                self.refresh_token = data["refresh_token"]

            # Persist updated tokens
            self.save_tokens()

            logger.info(
                "Zoho Books token refreshed, expires in %ds",
                expires_in,
            )

        except httpx.HTTPStatusError as exc:
            logger.exception("Zoho Books token refresh failed")
            raise ZohoBooksError(
                exc.response.status_code,
                f"Token refresh failed: {exc.response.text}",
            ) from exc

        except Exception as exc:
            logger.exception("Zoho Books token refresh error")
            raise ZohoBooksError(
                0,
                f"Token refresh error: {exc}",
            ) from exc

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_data: dict | None = None,
    ) -> dict:
        """Make an authenticated API request."""
        self._ensure_token()

        url = f"{self.base_url}/{path}"

        # Add organization_id to params
        if params is None:
            params = {}
        params["organization_id"] = self.organization_id

        headers = {
            "Authorization": f"Zoho-oauthtoken {self.access_token}",
            "Content-Type": "application/json",
        }

        try:
            response = httpx.request(
                method,
                url,
                params=params,
                json=json_data,
                headers=headers,
                timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0),
            )
            response.raise_for_status()

            data = response.json()
            return data

        except httpx.HTTPStatusError as exc:
            body = exc.response.text
            logger.error(
                "Zoho Books API error: %s %s -> %s: %s",
                method,
                path,
                exc.response.status_code,
                body,
            )
            raise ZohoBooksError(
                exc.response.status_code,
                body,
            ) from exc

    # ----------------------------------------------------------------
    # Journals (for F3 — Books to Tally)
    # ----------------------------------------------------------------

    def list_journals(
        self,
        start_date: str,
        end_date: str,
        page: int = 1,
        per_page: int = 200,
    ) -> list[dict]:
        """List journal entries for a date range.

        Args:
            start_date: YYYY-MM-DD
            end_date: YYYY-MM-DD
        """
        all_journals = []
        current_page = page

        while True:
            data = self._request(
                "GET",
                "journals",
                params={
                    "date_start": start_date,
                    "date_end": end_date,
                    "page": current_page,
                    "per_page": per_page,
                },
            )

            journals = data.get("journals", [])
            all_journals.extend(journals)

            # Check pagination
            has_more = data.get("page_context", {}).get(
                "has_more_page", False
            )
            if not has_more:
                break
            current_page += 1

        return all_journals

    def get_journal(self, journal_id: str) -> dict:
        """Get a single journal with line details."""
        data = self._request("GET", f"journals/{journal_id}")
        return data.get("journal", {})

    def list_journal_lines(
        self,
        journal_id: str,
    ) -> list[dict]:
        """Get all lines for a journal entry."""
        data = self._request(
            "GET",
            f"journals/{journal_id}/lines",
        )
        return data.get("journal_lines", [])

    # ----------------------------------------------------------------
    # Chart of Accounts (for F3 — account mapping)
    # ----------------------------------------------------------------

    def list_chart_of_accounts(
        self,
        page: int = 1,
        per_page: int = 200,
    ) -> list[dict]:
        """List all accounts in the chart of accounts."""
        all_accounts = []
        current_page = page

        while True:
            data = self._request(
                "GET",
                "chartofaccounts",
                params={
                    "page": current_page,
                    "per_page": per_page,
                },
            )

            accounts = data.get("chartofaccounts", [])
            all_accounts.extend(accounts)

            has_more = data.get("page_context", {}).get(
                "has_more_page", False
            )
            if not has_more:
                break
            current_page += 1

        return all_accounts

    # ----------------------------------------------------------------
    # Invoices (for D1 — sales/receivables)
    # ----------------------------------------------------------------

    def list_invoices(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
        page: int = 1,
        per_page: int = 200,
    ) -> list[dict]:
        """List invoices with optional filters."""
        all_invoices = []
        current_page = page

        while True:
            params: dict[str, Any] = {
                "page": current_page,
                "per_page": per_page,
            }
            if start_date:
                params["date_start"] = start_date
            if end_date:
                params["date_end"] = end_date
            if status:
                params["status"] = status

            data = self._request("GET", "invoices", params=params)
            invoices = data.get("invoices", [])
            all_invoices.extend(invoices)

            has_more = data.get("page_context", {}).get(
                "has_more_page", False
            )
            if not has_more:
                break
            current_page += 1

        return all_invoices

    def get_invoice(self, invoice_id: str) -> dict:
        """Get a single invoice."""
        data = self._request("GET", f"invoices/{invoice_id}")
        return data.get("invoice", {})

    # ----------------------------------------------------------------
    # Bills (for D1 — payables)
    # ----------------------------------------------------------------

    def list_bills(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        status: str | None = None,
        page: int = 1,
        per_page: int = 200,
    ) -> list[dict]:
        """List vendor bills with optional filters."""
        all_bills = []
        current_page = page

        while True:
            params: dict[str, Any] = {
                "page": current_page,
                "per_page": per_page,
            }
            if start_date:
                params["date_start"] = start_date
            if end_date:
                params["date_end"] = end_date
            if status:
                params["status"] = status

            data = self._request("GET", "bills", params=params)
            bills = data.get("bills", [])
            all_bills.extend(bills)

            has_more = data.get("page_context", {}).get(
                "has_more_page", False
            )
            if not has_more:
                break
            current_page += 1

        return all_bills

    # ----------------------------------------------------------------
    # Contacts (for D1 — top customers/vendors)
    # ----------------------------------------------------------------

    def list_contacts(
        self,
        contact_type: str | None = None,
        page: int = 1,
        per_page: int = 200,
    ) -> list[dict]:
        """List contacts, optionally filtered by type."""
        all_contacts = []
        current_page = page

        while True:
            params: dict[str, Any] = {
                "page": current_page,
                "per_page": per_page,
            }
            if contact_type:
                params["contact_type"] = contact_type

            data = self._request("GET", "contacts", params=params)
            contacts = data.get("contacts", [])
            all_contacts.extend(contacts)

            has_more = data.get("page_context", {}).get(
                "has_more_page", False
            )
            if not has_more:
                break
            current_page += 1

        return all_contacts

    def get_contact(self, contact_id: str) -> dict:
        """Get a single contact."""
        data = self._request("GET", f"contacts/{contact_id}")
        return data.get("contact", {})

    def get_contact_statements(
        self,
        contact_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """Get contact statement with balance info."""
        params: dict[str, Any] = {}
        if start_date:
            params["date_start"] = start_date
        if end_date:
            params["date_end"] = end_date

        return self._request(
            "GET",
            f"contacts/{contact_id}/statements",
            params=params,
        )

    # ----------------------------------------------------------------
    # Organizations
    # ----------------------------------------------------------------

    def get_organization(self) -> dict:
        """Get organization details."""
        # /organizations lists all orgs for this token — must NOT include organization_id param
        # (that param is for module endpoints, not the discovery endpoint)
        self._ensure_token()
        url = f"{self.base_url}/organizations"
        headers = {"Authorization": f"Zoho-oauthtoken {self.access_token}"}
        import httpx as _httpx
        resp = _httpx.get(url, headers=headers, timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0))
        resp.raise_for_status()
        orgs = resp.json().get("organizations", [])
        if not orgs:
            return {}
        # Prefer the org matching our configured org_id
        for org in orgs:
            if str(org.get("organization_id", "")) == str(self.organization_id):
                return org
        return orgs[0]

    # ----------------------------------------------------------------
    # Purchase Orders (for P1 — real PO creation)
    # ----------------------------------------------------------------

    def create_purchase_order(self, payload: dict) -> dict:
        """Create a PO in live Zoho Books. Raises ZohoBooksError on failure."""
        data = self._request("POST", "purchaseorders", json_data=payload)
        return data.get("purchaseorder", data)

    def get_purchase_order(self, po_id: str) -> dict:
        data = self._request("GET", f"purchaseorders/{po_id}")
        return data.get("purchaseorder", {})

    def list_purchase_orders(self, page: int = 1, per_page: int = 200) -> list[dict]:
        data = self._request("GET", "purchaseorders", params={"page": page, "per_page": per_page})
        return data.get("purchaseorders", [])

    def search_contacts(self, keyword: str, contact_type: str | None = None) -> list[dict]:
        """Live search contacts by keyword (real Zoho data)."""
        params: dict[str, Any] = {"search_text": keyword, "page": 1, "per_page": 20}
        if contact_type:
            params["contact_type"] = contact_type
        try:
            data = self._request("GET", "contacts", params=params)
            return data.get("contacts", [])
        except ZohoBooksError:
            # fallback: list and filter locally
            all_contacts = self.list_contacts(contact_type=contact_type, per_page=50)
            kw = keyword.lower()
            return [c for c in all_contacts if kw in c.get("contact_name", "").lower()]

    def search_items(self, keyword: str) -> list[dict]:
        """Live search items by keyword."""
        try:
            data = self._request("GET", "items", params={"search_text": keyword, "page": 1, "per_page": 20})
            return data.get("items", [])
        except ZohoBooksError:
            return []

    # ----------------------------------------------------------------
    # Bulk journal lines (for F3 — all lines in date range)
    # ----------------------------------------------------------------

    def get_all_journal_lines_for_period(
        self,
        start_date: str,
        end_date: str,
    ) -> list[dict]:
        """Fetch all journal lines for a period.

        Returns a flattened list of journal lines with
        journal metadata attached.
        """
        journals = self.list_journals(start_date, end_date)
        all_lines = []

        for journal in journals:
            journal_id = journal.get("journal_id", "")
            journal_date = journal.get("date", "")
            journal_number = journal.get("journal_number", "")
            notes = journal.get("notes", "")

            lines = self.list_journal_lines(journal_id)

            for line in lines:
                line["journal_date"] = journal_date
                line["journal_number"] = journal_number
                line["journal_notes"] = notes
                all_lines.append(line)

        return all_lines


# Singleton — lazy initialized
_client: ZohoBooksClient | None = None


def get_zoho_client() -> ZohoBooksClient:
    """Use the requesting user's connection, falling back to shared .env."""
    username = _zoho_user.get()
    if username:
        from app.services.zoho_connections import (
            get_user_zoho_connection,
            update_user_zoho_refresh_token,
        )
        stored = get_user_zoho_connection(username)
        if stored:
            credentials, _ = stored
            return ZohoBooksClient(
                access_token="refresh-required",
                refresh_token=credentials["refresh_token"],
                organization_id=credentials["organization_id"],
                data_center=credentials["data_center"],
                token_save_callback=lambda _access, refresh, _org: (
                    update_user_zoho_refresh_token(username, refresh)
                ),
            )

    global _client
    if _client is None:
        _client = ZohoBooksClient()
    return _client
