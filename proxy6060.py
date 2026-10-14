"""
Reverse proxy on port 6060.
  /webhook/*  → gateway  on port 8001
  /*          → backend  on port 8000
Run: python proxy6060.py
"""
import asyncio
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response
import uvicorn

app = FastAPI()
_client = httpx.AsyncClient(timeout=60)

ROUTES = [
    ("/webhook/", "http://localhost:8001"),
    ("/wa-web/",  "http://localhost:8002"),   # WhatsApp Web Playwright bridge
]
FALLBACK = "http://localhost:8000"


def _target(path: str) -> str:
    for prefix, upstream in ROUTES:
        if path.startswith(prefix):
            return upstream
    return FALLBACK


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy(path: str, request: Request):
    upstream = _target(f"/{path}")
    url = f"{upstream}/{path}"
    params = str(request.url.query)
    if params:
        url = f"{url}?{params}"
    body = await request.body()
    resp = await _client.request(
        method=request.method,
        url=url,
        headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
        content=body,
    )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=6060)
