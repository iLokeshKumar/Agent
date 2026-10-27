"""Google Drive MCP client for backend — REAL (Streamable HTTP, no mock).

Mirrors app/integrations/zoho_mcp_client.py: the backend talks JSON-RPC to a
Google Drive MCP server (initialize -> tools/list -> tools/call) over httpx,
handling both JSON and SSE (text/event-stream) responses.

Config (backend/.env):
  GOOGLE_DRIVE_MCP_SERVER_URL   the MCP server endpoint (Streamable HTTP)
  GOOGLE_DRIVE_MCP_TOKEN        Bearer token the server expects (optional)

Reality check (same as Apollo MCP): most Google Drive MCP servers authenticate
with interactive user OAuth. A headless backend has no browser, so without a
pre-provisioned token the server returns 401 — which means REACHABLE but not
authorized. For fully autonomous builds use the service-account REST client
(google_drive_client.py) instead; this client is for an MCP server that accepts
a static/long-lived token or sits behind an MCP gateway that holds the session.

Tool names below default to the common Google Drive connector naming. If your
MCP server names its tools differently, override them here — they are the one
place that is server-specific (see _map_file).
"""

import base64
import json
import logging
import re

import httpx

from app.core.config import settings
from app.integrations.google_drive_client import resolve_link  # reuse link->id parser

logger = logging.getLogger(__name__)

# Server-specific tool names — adjust to your Drive MCP server's contract.
SEARCH_TOOL = "search_files"
DOWNLOAD_TOOL = "download_file_content"
METADATA_TOOL = "get_file_metadata"

_SHEET_MIME = "application/vnd.google-apps.spreadsheet"


class GoogleDriveMCPError(Exception):
    pass


def _parse_body(r: httpx.Response) -> dict:
    """Parse an MCP response as JSON, falling back to the last SSE data frame."""
    try:
        return r.json()
    except Exception:
        m = re.search(r"data:\s*(\{.*\})", r.text, re.DOTALL)
        if m:
            return json.loads(m.group(1))
        return {"raw": r.text[:2000]}


class GoogleDriveMCPClient:
    def __init__(self, server_url: str, token: str | None = None):
        self.server_url = server_url
        self.token = token
        self._session_id: str | None = None

    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        return headers

    def _post(self, method: str, params: dict | None, req_id: int) -> dict:
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        r = httpx.post(self.server_url, json=payload, headers=self._headers(), timeout=30)
        if r.status_code == 401:
            raise GoogleDriveMCPError(
                "Google Drive MCP 401 — reachable but not authorized. The server wants "
                "OAuth/token auth. Provide GOOGLE_DRIVE_MCP_TOKEN, or use the "
                "service-account REST client for headless builds."
            )
        r.raise_for_status()
        sid = r.headers.get("mcp-session-id") or r.headers.get("Mcp-Session-Id")
        if sid:
            self._session_id = sid
        data = _parse_body(r)
        if isinstance(data, dict) and data.get("error"):
            raise GoogleDriveMCPError(f"MCP error on {method}: {data['error']}")
        return data

    def initialize(self) -> dict:
        return self._post(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "yexis-backend", "version": "1.0"},
            },
            req_id=1,
        )

    def list_tools(self) -> list[dict]:
        self.initialize()
        data = self._post("tools/list", {}, req_id=2)
        return data.get("result", {}).get("tools", [])

    def call_tool(self, tool_name: str, arguments: dict | None = None) -> dict:
        self.initialize()
        data = self._post("tools/call", {"name": tool_name, "arguments": arguments or {}}, req_id=3)
        return data.get("result", data)

    # ── high-level helpers (server-specific shapes handled best-effort) ──

    @staticmethod
    def _result_payloads(result: dict) -> list:
        """Flatten an MCP tools/call result into usable payloads: parsed JSON
        from text blocks, plus any structuredContent."""
        out = []
        if not isinstance(result, dict):
            return out
        if isinstance(result.get("structuredContent"), (dict, list)):
            out.append(result["structuredContent"])
        for block in result.get("content", []) or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text"):
                try:
                    out.append(json.loads(block["text"]))
                except (ValueError, TypeError):
                    out.append(block["text"])
            elif block.get("type") == "resource":
                out.append(block.get("resource", {}))
        return out

    def _map_file(self, rec: dict) -> dict | None:
        """Normalise a file record from the MCP server. ADJUST keys to match
        your server's search result shape."""
        if not isinstance(rec, dict):
            return None
        fid = rec.get("id") or rec.get("file_id") or rec.get("fileId")
        if not fid:
            return None
        return {
            "id": fid,
            "name": rec.get("name") or rec.get("title") or fid,
            "mimeType": rec.get("mimeType") or rec.get("mime_type") or "",
        }

    def search_files(self, query: str | None = None, folder_id: str | None = None) -> list[dict]:
        """Find spreadsheet files via the MCP server. Returns [{id,name,mimeType}]."""
        folder_id = folder_id or settings.google_drive_folder_id or None
        args: dict = {}
        # Most Drive MCP servers accept a free-text query; spell out spreadsheets.
        q = query or "spreadsheet OR csv OR xlsx"
        args["query"] = q
        if folder_id:
            args["folder_id"] = folder_id
            args["parent"] = folder_id  # cover alternate arg names
        result = self.call_tool(SEARCH_TOOL, args)

        files: list[dict] = []
        for payload in self._result_payloads(result):
            records = payload
            if isinstance(payload, dict):
                records = payload.get("files") or payload.get("results") or payload.get("items") or []
            if isinstance(records, list):
                for rec in records:
                    mapped = self._map_file(rec)
                    if mapped:
                        files.append(mapped)
        return files

    def download_file(self, file_id: str) -> tuple[bytes, str]:
        """Download a file's bytes via the MCP server. Returns (bytes, name).
        Handles base64 resource blobs and plain text (CSV) content."""
        result = self.call_tool(DOWNLOAD_TOOL, {"file_id": file_id, "fileId": file_id})
        name = file_id
        for payload in self._result_payloads(result):
            if isinstance(payload, dict):
                name = payload.get("name") or payload.get("filename") or name
                blob = payload.get("blob") or payload.get("data") or payload.get("content")
                if isinstance(blob, str) and blob:
                    try:
                        return base64.b64decode(blob), name
                    except (base64.binascii.Error, ValueError):
                        return blob.encode("utf-8"), name
            elif isinstance(payload, str) and payload.strip():
                return payload.encode("utf-8"), name
        raise GoogleDriveMCPError(
            f"MCP {DOWNLOAD_TOOL} returned no file content for {file_id} "
            "(check the tool name and result shape against your server)"
        )


_client: GoogleDriveMCPClient | None = None


def get_client() -> GoogleDriveMCPClient:
    global _client
    if not settings.google_drive_mcp_server_url:
        raise GoogleDriveMCPError(
            "Google Drive MCP not configured — set GOOGLE_DRIVE_MCP_SERVER_URL in backend/.env"
        )
    if _client is None:
        _client = GoogleDriveMCPClient(
            settings.google_drive_mcp_server_url, settings.google_drive_mcp_token or None
        )
    return _client


def health() -> dict:
    if not settings.google_drive_mcp_server_url:
        return {"configured": False, "hint": "Set GOOGLE_DRIVE_MCP_SERVER_URL in backend/.env"}
    try:
        tools = get_client().list_tools()
        return {"configured": True, "live": True, "tool_count": len(tools),
                "tools": [t.get("name") for t in tools][:20]}
    except Exception as exc:
        return {"configured": True, "live": False, "error": str(exc)}
