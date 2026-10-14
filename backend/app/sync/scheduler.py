"""Background scheduler for periodic Zoho data sync.

Runs in a daemon thread, syncing Zoho Books data
at a configurable interval.
"""

import logging
import threading
import time

from app.core.config import settings
from app.sync.syncer import ZohoSyncer
from app.sync.cache import SyncCache


logger = logging.getLogger(__name__)

# Default sync interval: 15 minutes (in seconds)
DEFAULT_SYNC_INTERVAL = 900


class SyncScheduler:
    """Periodically sync Zoho Books data in background."""

    def __init__(
        self,
        syncer: ZohoSyncer | None = None,
        interval_seconds: int | None = None,
    ):
        self.syncer = syncer or ZohoSyncer()
        self.interval = (
            interval_seconds
            or settings.zoho_sync_interval
            or DEFAULT_SYNC_INTERVAL
        )
        self.running = False
        self.thread: threading.Thread | None = None
        self.last_run: str | None = None
        self.enabled = settings.zoho_sync_enabled

    def start(self):
        """Start the background sync thread."""
        if not self.enabled:
            logger.info("Zoho sync scheduler disabled")
            return

        if self.running:
            return

        self.running = True

        self.thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="zoho-sync",
        )
        self.thread.start()

        logger.info(
            "Zoho sync scheduler started (interval=%ds)",
            self.interval,
        )

    def stop(self):
        """Stop the background sync thread."""
        self.running = False

        if self.thread:
            self.thread.join(timeout=2)

        logger.info("Zoho sync scheduler stopped")

    def trigger_now(self) -> dict:
        """Trigger an immediate sync (non-blocking)."""
        logger.info("Manual sync triggered")

        try:
            result = self.syncer.sync_all()
            self.last_run = result.get("timestamp")
            return result
        except Exception as exc:
            logger.exception("Manual sync failed")
            return {"status": "error", "error": str(exc)}

    def _run_loop(self):
        """Main sync loop — runs in background thread."""
        # Wait a bit before first sync (let app start up)
        time.sleep(10)

        while self.running:
            try:
                logger.info("Running scheduled Zoho sync")
                result = self.syncer.sync_all()
                self.last_run = result.get("timestamp")

                synced = sum(
                    1
                    for r in result.get("results", {}).values()
                    if r.get("status") == "ok"
                )
                total = len(result.get("results", {}))

                logger.info(
                    "Scheduled sync done: %d/%d targets OK",
                    synced,
                    total,
                )

            except Exception:
                logger.exception("Scheduled sync failed")

            # Sleep in small chunks so we can stop quickly
            for _ in range(self.interval):
                if not self.running:
                    break
                time.sleep(1)


# Singleton
_sync_scheduler: SyncScheduler | None = None


def get_sync_scheduler() -> SyncScheduler:
    """Get or create the sync scheduler singleton."""
    global _sync_scheduler
    if _sync_scheduler is None:
        _sync_scheduler = SyncScheduler()
    return _sync_scheduler
