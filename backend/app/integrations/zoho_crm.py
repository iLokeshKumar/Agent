"""Zoho CRM REST client — REAL (no mock).

Uses the same OAuth2 refresh-token flow as zoho_books.py.
Credentials are shared: zoho_client_id / zoho_client_secret / zoho_refresh_token.

Docs: https://www.zoho.com/crm/developer/docs/api/v3/
"""

import logging
import time

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

ZOHO_CRM_BASES = {
    "us": "https://www.zohoapis.com/crm/v3",
    "eu": "https://www.zohoapis.eu/crm/v3",
    "in": "https://www.zohoapis.in/crm/v3",
    "com.au": "https://www.zohoapis.com.au/crm/v3",
    "jp": "https://www.zohoapis.jp/crm/v3",
}

ZOHO_TOKEN_URLS = {
    "us": "https://accounts.zoho.com/oauth/v2/token",
    "eu": "https://accounts.zoho.eu/oauth/v2/token",
    "in": "https://accounts.zoho.in/oauth/v2/token",
    "com.au": "https://accounts.zoho.com.au/oauth/v2/token",
    "jp": "https://accounts.zoho.jp/oauth/v2/token",
}

TOKEN_REFRESH_BUFFER = 300


class ZohoCrmError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"Zoho CRM {status_code}: {message}")


class ZohoCrmClient:
    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        refresh_token: str | None = None,
        data_center: str | None = None,
        access_token: str | None = None,
        token_expiry: float = 0,
        token_save_callback=None,
    ):
        self.client_id = settings.zoho_client_id if client_id is None else client_id
        self.client_secret = settings.zoho_client_secret if client_secret is None else client_secret
        self.refresh_token = settings.zoho_refresh_token if refresh_token is None else refresh_token
        self.data_center = (settings.zoho_data_center if data_center is None else data_center) or "in"
        self.base_url = ZOHO_CRM_BASES.get(self.data_center, ZOHO_CRM_BASES["in"])
        self.token_url = ZOHO_TOKEN_URLS.get(self.data_center, ZOHO_TOKEN_URLS["in"])
        self._access_token: str = settings.zoho_access_token if access_token is None else access_token
        self._token_expiry = token_expiry
        self._token_save_callback = token_save_callback

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)

    def _refresh_token_if_needed(self) -> None:
        if not self.configured:
            raise ZohoCrmError(0, "Zoho CRM not configured — set zoho_client_id/client_secret/refresh_token")
        if self._access_token and time.time() < self._token_expiry - TOKEN_REFRESH_BUFFER:
            return
        resp = httpx.post(
            self.token_url,
            data={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            raise ZohoCrmError(resp.status_code, f"Token refresh failed: {resp.text[:300]}")
        data = resp.json()
        if "access_token" not in data:
            raise ZohoCrmError(0, f"No access_token in response: {data}")
        self._access_token = data["access_token"]
        self._token_expiry = time.time() + int(data.get("expires_in", 3600))
        if "refresh_token" in data:
            self.refresh_token = data["refresh_token"]
        if self._token_save_callback:
            self._token_save_callback(self._access_token, self.refresh_token, None)
        logger.debug("Zoho CRM token refreshed, expires in %ss", data.get("expires_in"))

    def _get(self, path: str, params: dict | None = None) -> dict:
        self._refresh_token_if_needed()
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = httpx.get(
            url,
            params=params or {},
            headers={"Authorization": f"Zoho-oauthtoken {self._access_token}"},
            timeout=20,
        )
        if resp.status_code == 401:
            # Token may have expired mid-flight — force refresh once
            self._token_expiry = 0
            self._refresh_token_if_needed()
            resp = httpx.get(
                url,
                params=params or {},
                headers={"Authorization": f"Zoho-oauthtoken {self._access_token}"},
                timeout=20,
            )
        if resp.status_code not in (200, 201):
            raise ZohoCrmError(resp.status_code, resp.text[:400])
        return resp.json()

    def _request(self, method: str, path: str, params: dict | None = None,
                 json: dict | None = None) -> dict:
        """Generic call for writes and searches. Zoho returns 204 (no body)
        when a search finds nothing — that comes back as {}."""
        self._refresh_token_if_needed()
        url = f"{self.base_url}/{path.lstrip('/')}"

        def _do():
            return httpx.request(
                method, url, params=params or {}, json=json,
                headers={"Authorization": f"Zoho-oauthtoken {self._access_token}"},
                timeout=20,
            )

        resp = _do()
        if resp.status_code == 401:
            self._token_expiry = 0
            self._refresh_token_if_needed()
            resp = _do()
        if resp.status_code == 204:
            return {}
        if resp.status_code not in (200, 201, 202):
            raise ZohoCrmError(resp.status_code, resp.text[:400])
        return resp.json()

    def search_by_email(self, module: str, email: str) -> list[dict]:
        """Find Contacts/Leads records by email address."""
        data = self._request("GET", f"{module}/search", params={"email": email})
        return data.get("data") or []

    def create_record(self, module: str, record: dict) -> str:
        """Create one record; returns its Zoho id."""
        data = self._request("POST", module, json={"data": [record], "trigger": ["workflow"]})
        row = (data.get("data") or [{}])[0]
        if row.get("status") != "success":
            raise ZohoCrmError(0, f"create {module} failed: {str(row)[:400]}")
        return row["details"]["id"]

    def create_task(
        self, subject: str, description: str, due_date: str, priority: str = "High",
        who_id: str | None = None, what_id: str | None = None, se_module: str | None = None,
        owner_id: str | None = None,
    ) -> str:
        """Create a Task. who_id links a Contact; what_id + se_module links
        any other record (e.g. a Lead)."""
        record: dict = {
            "Subject": subject[:255],
            "Description": description,
            "Due_Date": due_date,
            "Priority": priority,
            "Status": "Not Started",
        }
        if who_id:
            record["Who_Id"] = who_id
        if what_id and se_module:
            record["What_Id"] = what_id
            record["$se_module"] = se_module
        if owner_id:
            record["Owner"] = {"id": owner_id}
        return self.create_record("Tasks", record)

    def update_record(self, module: str, record_id: str, record: dict) -> None:
        """Update an existing record by its Zoho id."""
        data = self._request("PUT", f"{module}/{record_id}", json={"data": [record]})
        row = (data.get("data") or [{}])[0]
        if row.get("status") not in ("success", None):
            raise ZohoCrmError(0, f"update {module}/{record_id} failed: {str(row)[:400]}")

    def list_accounts(self, page: int = 1, per_page: int = 200) -> list[dict]:
        """Return CRM Account records with contact fields."""
        fields = "Account_Name,Phone,Email,Billing_City,Billing_State,Owner,Website"
        try:
            data = self._get("Accounts", {
                "fields": fields,
                "page": page,
                "per_page": min(per_page, 200),
            })
            return data.get("data", [])
        except ZohoCrmError as exc:
            logger.warning("Zoho CRM list_accounts failed: %s", exc)
            raise

    def list_contacts(self, page: int = 1, per_page: int = 200) -> list[dict]:
        """Return CRM Contact records."""
        fields = "First_Name,Last_Name,Phone,Email,Account_Name,Mobile,Mailing_City,Mailing_State"
        try:
            data = self._get("Contacts", {
                "fields": fields,
                "page": page,
                "per_page": min(per_page, 200),
            })
            return data.get("data", [])
        except ZohoCrmError as exc:
            logger.warning("Zoho CRM list_contacts failed: %s", exc)
            raise


def get_zoho_crm_client() -> ZohoCrmClient:
    from app.integrations.zoho_books import get_zoho_client
    books_client = get_zoho_client()
    return ZohoCrmClient(
        client_id=books_client.client_id,
        client_secret=books_client.client_secret,
        refresh_token=books_client.refresh_token,
        data_center=books_client.data_center,
        access_token=books_client.access_token,
        token_expiry=books_client._token_expiry,
        token_save_callback=books_client._token_save_callback,
    )
