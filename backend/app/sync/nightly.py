"""Nightly analytics cadence — marts → KPIs → cloud ship → DPDP purge.

One entry point (`run_nightly`) called by the Gateway lane (via the
`nightly_analytics` task type), the /sync/nightly endpoint for manual
runs, and tests. Order matters:

  1. build marts        (dealer_mart, sku_health — local + cache)
  2. compute KPIs       (kpi_snapshot — needs marts' view of state)
  3. ship Local→Cloud   (mirrors the fresh rows upward)
  4. DPDP purge         (independent hygiene; failure must not block 1-3)

Every step is individually try/excepted: one failing stage reports
itself and lets the others run — a night with a broken cache should
still purge expired PII.
"""

import logging
import threading
import time
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models.sync_watermark import SyncWatermark
from app.services.marts import (
    build_dealer_mart,
    build_installed_base,
    build_sku_health,
    build_vendor_scorecard,
    compute_kpis,
)
from app.sync.cache import SyncCache
from app.sync.cloud_shipper import ship_local_to_cloud
from app.sync.purge import purge_expired

logger = logging.getLogger(__name__)

NIGHTLY_LANE = "NIGHTLY"
NIGHTLY_SOURCE = "analytics_chain"


def run_nightly(
    local_db: Session,
    period: date | None = None,
    eol_skus: set[str] | None = None,
    cache: SyncCache | None = None,
) -> dict:
    """Run the full nightly analytics chain. Never raises."""
    cache = cache or SyncCache()
    report: dict[str, dict] = {}

    stages = (
        ("dealer_mart",
         lambda: build_dealer_mart(local_db, cache=cache)),
        ("sku_health",
         lambda: build_sku_health(
             local_db, period=period, eol_skus=eol_skus, cache=cache)),
        ("vendor_scorecard",
         lambda: build_vendor_scorecard(local_db, period=period)),
        ("installed_base",
         lambda: build_installed_base(local_db, cache=cache)),
        ("kpis",
         lambda: compute_kpis(local_db, period=period, cache=cache)),
        ("cloud_ship",
         lambda: ship_local_to_cloud(local_db)),
        ("dpdp_purge",
         lambda: purge_expired(local_db)),
    )

    for name, fn in stages:
        try:
            report[name] = fn()
        except Exception as exc:
            logger.exception("Nightly stage %s failed", name)
            report[name] = {"status": "error", "error": str(exc)}

    failed = [k for k, v in report.items() if v.get("status") == "error"]
    report["_summary"] = {
        "ok": not failed,
        "failed_stages": failed,
    }

    _stamp_run(local_db)
    return report


def _stamp_run(local_db: Session) -> None:
    """Record today's run in sync_watermark — idempotent per date."""
    today = date.today().isoformat()
    wm = (
        local_db.query(SyncWatermark)
        .filter(
            SyncWatermark.lane == NIGHTLY_LANE,
            SyncWatermark.source == NIGHTLY_SOURCE,
        )
        .first()
    )
    if wm is None:
        wm = SyncWatermark(
            lane=NIGHTLY_LANE, source=NIGHTLY_SOURCE, cursor=today
        )
        local_db.add(wm)
    else:
        wm.cursor = today
    local_db.commit()


def nightly_due(local_db: Session, now: datetime | None = None) -> bool:
    """True when today's chain has not run yet and the hour has come."""
    from app.core.config import settings

    now = now or datetime.now()
    if now.hour < settings.nightly_analytics_hour:
        return False
    wm = (
        local_db.query(SyncWatermark)
        .filter(
            SyncWatermark.lane == NIGHTLY_LANE,
            SyncWatermark.source == NIGHTLY_SOURCE,
        )
        .first()
    )
    return wm is None or wm.cursor != date.today().isoformat()


class NightlyScheduler:
    """Background thread running the analytics chain once per day."""

    def __init__(self):
        self.running = False
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        from app.core.config import settings

        if not settings.nightly_analytics_enabled:
            logger.info("Nightly analytics scheduler disabled")
            return
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(
            target=self._loop, daemon=True, name="nightly-analytics"
        )
        self.thread.start()
        logger.info("Nightly analytics scheduler started")

    def stop(self) -> None:
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)

    def _loop(self) -> None:
        from app.core.db import SessionLocal
        from app.services.digest import run_digest_if_due

        time.sleep(30)  # let the app finish booting
        while self.running:
            try:
                db = SessionLocal()
                try:
                    if nightly_due(db):
                        logger.info("Nightly analytics due — running")
                        report = run_nightly(db)
                        logger.info(
                            "Nightly analytics done: %s",
                            report.get("_summary"),
                        )
                    digest_results = run_digest_if_due(db)
                    if digest_results:
                        logger.info("Digest results: %s", list(digest_results.keys()))
                finally:
                    db.close()
            except Exception:
                logger.exception("Nightly analytics loop failed")

            for _ in range(300):  # re-check every 5 minutes
                if not self.running:
                    return
                time.sleep(1)


_nightly_scheduler: NightlyScheduler | None = None


def get_nightly_scheduler() -> NightlyScheduler:
    global _nightly_scheduler
    if _nightly_scheduler is None:
        _nightly_scheduler = NightlyScheduler()
    return _nightly_scheduler
