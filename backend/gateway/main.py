"""Gateway entrypoint — run with:

    uvicorn gateway.main:app --host 127.0.0.1 --port 8001

The scheduler runs in a daemon thread; webhooks are served on the
event loop. Both feed tasks into the backend API's queue through the
shared database and lane governance.
"""

import logging
import os
import threading

from fastapi import FastAPI

from gateway.admin import configure as configure_admin
from gateway.admin import router as admin_router
from gateway.service import default_lanes, router, run_scheduler_loop


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000")

app = FastAPI(title="Yexis Automation Gateway")
app.include_router(router)
app.include_router(admin_router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.on_event("startup")
def start_scheduler():
    lanes = default_lanes(BACKEND_URL)
    configure_admin(lanes, BACKEND_URL)
    stop = {"flag": False}
    thread = threading.Thread(
        target=run_scheduler_loop,
        args=(lanes, lambda: stop["flag"]),
        daemon=True,
    )
    thread.start()
    app.state.scheduler_stop = stop
    logger.info("Gateway up; backend at %s; %d lanes", BACKEND_URL, len(lanes))
