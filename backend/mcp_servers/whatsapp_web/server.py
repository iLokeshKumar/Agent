"""WhatsApp Web automation service via Playwright.

Runs as a SEPARATE process on port 8002.
Start with:  python -m mcp.whatsapp_web.server

Flow:
  1. On startup → opens Chromium, loads web.whatsapp.com
  2. If not logged in → QR code appears; someone on the machine scans it once
  3. Session is saved to disk → survives restarts without re-scanning
  4. Backend calls /send, /status, /qr over HTTP

Limitations vs WABA API:
  - One WhatsApp account (personal number that owns the session)
  - WhatsApp can detect automation and ban the account
  - Breaks when WhatsApp Web updates their DOM
  - Cannot receive webhooks — polling only

Use WABA API for production. Use this for quick internal pilots.
"""

import asyncio
import base64
import logging
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from pydantic import BaseModel

logger = logging.getLogger(__name__)

SESSION_DIR = Path(__file__).resolve().parents[3] / ".wa_session"
SESSION_DIR.mkdir(exist_ok=True)

PORT = int(os.environ.get("WA_WEB_PORT", "8002"))

app = FastAPI(title="WhatsApp Web Bridge", version="1.0.0")

# ---------------------------------------------------------------------------
# Global browser state
# ---------------------------------------------------------------------------
_playwright = None
_browser: Browser | None = None
_context: BrowserContext | None = None
_page: Page | None = None
_ready = False   # True once WhatsApp Web is past the QR screen


async def _launch():
    global _playwright, _browser, _context, _page, _ready

    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(
        headless=False,       # visible window so user can scan QR
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )

    storage = SESSION_DIR / "storage.json"
    if storage.exists():
        _context = await _browser.new_context(storage_state=str(storage))
        logger.info("Loaded saved WhatsApp session from %s", storage)
    else:
        _context = await _browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )

    _page = await _context.new_page()
    await _page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")

    # Wait up to 60s for either QR code or the main chat list
    logger.info("Waiting for WhatsApp Web to load...")
    try:
        await _page.wait_for_selector(
            '[data-testid="qrcode"], [data-testid="chat-list"], '
            'canvas[aria-label="Scan me!"], [aria-label="Chat list"]',
            timeout=60_000,
        )
    except Exception:
        logger.warning("WhatsApp Web load timeout — proceeding anyway")

    # Check if already logged in
    chat_list = await _page.query_selector('[data-testid="chat-list"], [aria-label="Chat list"]')
    if chat_list:
        _ready = True
        await _save_session()
        logger.info("WhatsApp Web: already logged in, session ready")
    else:
        logger.info("WhatsApp Web: QR code visible — scan it at http://localhost:%d/qr", PORT)


async def _save_session():
    storage = SESSION_DIR / "storage.json"
    await _context.storage_state(path=str(storage))
    logger.info("WhatsApp session saved to %s", storage)


async def _ensure_logged_in():
    global _ready
    if not _page:
        raise RuntimeError("Browser not initialised")
    chat = await _page.query_selector('[data-testid="chat-list"], [aria-label="Chat list"]')
    if chat:
        _ready = True
        return True
    _ready = False
    return False


# ---------------------------------------------------------------------------
# FastAPI lifecycle
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def startup():
    asyncio.create_task(_launch())


@app.on_event("shutdown")
async def shutdown():
    if _context:
        await _save_session()
    if _browser:
        await _browser.close()
    if _playwright:
        await _playwright.stop()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/status")
async def status():
    logged_in = await _ensure_logged_in() if _page else False
    return {
        "ready": logged_in,
        "session_saved": (SESSION_DIR / "storage.json").exists(),
        "scan_qr_at": f"http://localhost:{PORT}/qr" if not logged_in else None,
    }


@app.get("/qr", response_class=HTMLResponse)
async def qr_page():
    """Render QR code as a web page so user can scan with phone."""
    if not _page:
        return HTMLResponse("<h2>Browser not ready yet — wait 5 seconds and refresh</h2>")

    logged_in = await _ensure_logged_in()
    if logged_in:
        return HTMLResponse(
            "<h2 style='color:green'>✅ WhatsApp Web is logged in and ready!</h2>"
            "<p>You can close this tab.</p>"
        )

    # Capture QR canvas as PNG
    qr_img_b64 = None
    try:
        # Try canvas first (old WA Web)
        canvas = await _page.query_selector('canvas[aria-label="Scan me!"]')
        if canvas:
            png = await canvas.screenshot()
            qr_img_b64 = base64.b64encode(png).decode()
        else:
            # Try the QR container div
            qr_div = await _page.query_selector('[data-testid="qrcode"]')
            if qr_div:
                png = await qr_div.screenshot()
                qr_img_b64 = base64.b64encode(png).decode()
    except Exception as exc:
        logger.warning("QR screenshot failed: %s", exc)

    if qr_img_b64:
        return HTMLResponse(f"""
        <html><body style="font-family:sans-serif;text-align:center;padding:40px">
        <h2>📱 Scan with WhatsApp on your phone</h2>
        <p>Open WhatsApp → Linked Devices → Link a Device</p>
        <img src="data:image/png;base64,{qr_img_b64}"
             style="width:300px;height:300px;border:2px solid #ccc;border-radius:8px"/>
        <p>This page auto-refreshes every 10 seconds.</p>
        <script>setTimeout(()=>location.reload(), 10000)</script>
        </body></html>
        """)

    return HTMLResponse("""
    <html><body style="font-family:sans-serif;text-align:center;padding:40px">
    <h2>⏳ Loading WhatsApp Web...</h2>
    <p>Refreshing in 5 seconds...</p>
    <script>setTimeout(()=>location.reload(), 5000)</script>
    </body></html>
    """)


class SendRequest(BaseModel):
    to: str        # phone number or name of existing contact
    message: str
    is_group: bool = False


@app.post("/send")
async def send_message(req: SendRequest):
    if not _page:
        raise HTTPException(status_code=503, detail="Browser not ready")

    logged_in = await _ensure_logged_in()
    if not logged_in:
        raise HTTPException(
            status_code=503,
            detail=f"Not logged in — scan QR at http://localhost:{PORT}/qr",
        )

    try:
        # Use WhatsApp Web API URL for direct-to-number messaging
        phone = req.to.replace("+", "").replace(" ", "").replace("-", "")
        if req.is_group:
            # For groups, search by name
            await _send_to_contact(req.to, req.message)
        else:
            # Direct number send via wa.me URL (most reliable, bypasses contact search)
            url = f"https://web.whatsapp.com/send?phone={phone}&text={req.message}"
            await _page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(3)

            # Find and click send button
            sent = await _click_send()
            if not sent:
                raise HTTPException(status_code=422, detail="Could not find send button — number may not be on WhatsApp")

        await asyncio.sleep(1)
        await _save_session()
        return {"status": "sent", "to": req.to}

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Send failed")
        raise HTTPException(status_code=500, detail=str(exc))


async def _click_send() -> bool:
    """Try multiple selectors for the send button."""
    selectors = [
        '[data-testid="send"]',
        'button[aria-label="Send"]',
        'span[data-icon="send"]',
        '[data-tab="11"]',
    ]
    for sel in selectors:
        try:
            btn = await _page.wait_for_selector(sel, timeout=5_000)
            if btn:
                await btn.click()
                return True
        except Exception:
            continue
    return False


async def _send_to_contact(name: str, message: str):
    """Search for a contact by name and send a message."""
    # Click search
    search_selectors = [
        '[data-testid="chat-list-search"]',
        '[title="Search input textbox"]',
        'div[contenteditable="true"][data-tab="3"]',
    ]
    for sel in search_selectors:
        try:
            search = await _page.wait_for_selector(sel, timeout=5_000)
            if search:
                await search.click()
                await search.fill(name)
                break
        except Exception:
            continue

    await asyncio.sleep(2)

    # Click first result
    result_selectors = [
        '[data-testid="cell-frame-container"]',
        '._21S-L span[title]',
    ]
    for sel in result_selectors:
        try:
            result = await _page.wait_for_selector(sel, timeout=5_000)
            if result:
                await result.click()
                break
        except Exception:
            continue

    await asyncio.sleep(1)

    # Type message in compose box
    compose_selectors = [
        '[data-testid="conversation-compose-box-input"]',
        'div[contenteditable="true"][data-tab="10"]',
        '[title="Type a message"]',
    ]
    for sel in compose_selectors:
        try:
            box = await _page.wait_for_selector(sel, timeout=5_000)
            if box:
                await box.click()
                await box.fill(message)
                break
        except Exception:
            continue

    await _click_send()


@app.get("/screenshot")
async def screenshot():
    """Return a base64 screenshot of the current browser state (debugging)."""
    if not _page:
        raise HTTPException(status_code=503, detail="Browser not ready")
    png = await _page.screenshot()
    return {"image_b64": base64.b64encode(png).decode()}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
