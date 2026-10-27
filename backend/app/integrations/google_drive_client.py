"""Google Drive — pull contact spreadsheets into K2 audience building.

Three entry points mirror the three UI modes:
  resolve_link(url)             — extract a Drive file id from a shared link
  download_file(file_id)        — one file by id (Google Sheets export to .xlsx)
  list_spreadsheets(folder_id)  — enumerate spreadsheet files in a folder

Auth: a Google service-account JSON (settings.google_service_account_json,
given as a path, raw JSON, or base64-encoded JSON) with Drive read scope. The
target files/folder must be shared with the service account's client_email
(or the project configured for domain-wide delegation). The Drive API must be
enabled on the service account's GCP project.
"""

import base64
import binascii
import io
import json
import logging
import os
import re

from app.core.config import settings

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Google Sheets are not a downloadable blob — they are exported to this MIME.
_SHEET_EXPORT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_SPREADSHEET_MIMES = {
    "application/vnd.google-apps.spreadsheet",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "text/csv",
}


class GoogleDriveError(Exception):
    pass


def _load_credentials():
    raw = settings.google_service_account_json
    if not raw:
        raise GoogleDriveError("google_service_account_json not set")

    info = None
    if os.path.exists(raw):
        with open(raw, "r", encoding="utf-8") as fh:
            info = json.load(fh)
    else:
        # Accept raw JSON or base64-encoded JSON passed inline via env.
        try:
            info = json.loads(raw)
        except json.JSONDecodeError:
            try:
                info = json.loads(base64.b64decode(raw).decode("utf-8"))
            except (binascii.Error, ValueError, json.JSONDecodeError) as exc:
                raise GoogleDriveError(
                    f"service account is not a readable path, JSON, or base64 JSON: {exc}"
                )

    try:
        from google.oauth2 import service_account
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise GoogleDriveError("google-auth not installed (pip install google-auth)") from exc
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


def _service():
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise GoogleDriveError(
            "google-api-python-client not installed (pip install google-api-python-client)"
        ) from exc
    creds = _load_credentials()
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def resolve_link(url: str | None) -> str | None:
    """Pull a Drive file id out of a share link, or return the value if it
    already looks like a bare id. Returns None when nothing id-like is found."""
    if not url:
        return None
    m = re.search(r"/d/([a-zA-Z0-9_-]{20,})", url) or re.search(r"[?&]id=([a-zA-Z0-9_-]{20,})", url)
    if m:
        return m.group(1)
    if re.fullmatch(r"[a-zA-Z0-9_-]{20,}", url.strip()):
        return url.strip()
    return None


def list_spreadsheets(folder_id: str | None = None) -> list[dict]:
    """List spreadsheet-type files, optionally restricted to one folder.
    Returns [{id, name, mimeType}]. Falls back to settings.google_drive_folder_id."""
    svc = _service()
    folder_id = folder_id or settings.google_drive_folder_id or None
    mime_clause = " or ".join(f"mimeType='{m}'" for m in _SPREADSHEET_MIMES)
    q = f"({mime_clause}) and trashed=false"
    if folder_id:
        q = f"'{folder_id}' in parents and " + q

    files, token = [], None
    while True:
        resp = svc.files().list(
            q=q,
            fields="nextPageToken, files(id,name,mimeType)",
            pageSize=100,
            pageToken=token,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        ).execute()
        files.extend(resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            break
    return files


def download_file(file_id: str) -> tuple[bytes, str]:
    """Return (content_bytes, filename). Native Google Sheets are exported to
    .xlsx; everything else is downloaded as-is."""
    from googleapiclient.http import MediaIoBaseDownload

    svc = _service()
    meta = svc.files().get(
        fileId=file_id, fields="id,name,mimeType", supportsAllDrives=True
    ).execute()
    name = meta.get("name") or file_id
    mime = meta.get("mimeType")

    if mime == "application/vnd.google-apps.spreadsheet":
        request = svc.files().export_media(fileId=file_id, mimeType=_SHEET_EXPORT)
        if not name.lower().endswith(".xlsx"):
            name += ".xlsx"
    else:
        request = svc.files().get_media(fileId=file_id, supportsAllDrives=True)

    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue(), name


def health() -> dict:
    if not settings.google_service_account_json:
        return {
            "configured": False,
            "hint": "Set GOOGLE_SERVICE_ACCOUNT_JSON (path, JSON, or base64) in backend/.env",
        }
    try:
        creds = _load_credentials()
        svc = _service()
        svc.files().list(
            pageSize=1,
            fields="files(id)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        ).execute()
        return {
            "configured": True,
            "live": True,
            "service_account": getattr(creds, "service_account_email", None),
        }
    except Exception as exc:
        return {
            "configured": True,
            "live": False,
            "error": str(exc),
            "hint": "Enable the Drive API on the SA's project and share the file/folder with its client_email.",
        }
