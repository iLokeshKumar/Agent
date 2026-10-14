"""In-process Dead Letter Queue for the Gateway scheduler.

When a lane submission fails (backend unreachable, HTTP error), the item
is pushed here rather than silently dropped. On each scheduler tick,
items whose backoff window has elapsed are retried. After MAX_RETRIES
the item is marked exhausted and a CRITICAL alert is logged — at that
point an operator must drain or clear the DLQ manually.

The DLQ is in-memory: it survives lane failures but not gateway restarts.
For the scheduler this is acceptable — a restart will trigger the lane
naturally on the next tick. Webhook failures are already persisted in
gateway_events with status='rejected'.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

from gateway.internal_auth import task_submission_headers

logger = logging.getLogger(__name__)

MAX_RETRIES = 5
_BASE_DELAY_S = 30  # first retry after 30 s; doubles each time


@dataclass
class DLQItem:
    lane_name: str
    task_type: str
    payload: dict
    api_url: str
    error: str
    attempts: int = 0
    next_retry_at: float = field(default_factory=time.time)
    first_failed_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def record_failure(self, error: str) -> None:
        self.error = error
        self.attempts += 1
        delay = _BASE_DELAY_S * (2 ** min(self.attempts - 1, 6))
        self.next_retry_at = time.time() + delay
        if self.exhausted:
            logger.critical(
                "DLQ lane=%s exhausted after %d attempts; last error: %s",
                self.lane_name,
                self.attempts,
                error,
            )
        else:
            logger.warning(
                "DLQ lane=%s attempt=%d; retry in %.0fs; error: %s",
                self.lane_name,
                self.attempts,
                delay,
                error,
            )

    @property
    def exhausted(self) -> bool:
        return self.attempts >= MAX_RETRIES

    def due(self) -> bool:
        return not self.exhausted and time.time() >= self.next_retry_at


class GatewayDLQ:
    """Thread-safe DLQ for the gateway scheduler thread."""

    def __init__(self) -> None:
        self._items: dict[str, DLQItem] = {}  # keyed by lane_name

    def push(self, lane_name: str, task_type: str, payload: dict,
             api_url: str, error: str) -> None:
        if lane_name in self._items:
            self._items[lane_name].record_failure(error)
        else:
            item = DLQItem(
                lane_name=lane_name,
                task_type=task_type,
                payload=payload,
                api_url=api_url,
                error=error,
            )
            item.record_failure(error)  # sets attempts=1 and backoff
            self._items[lane_name] = item

    def due_items(self) -> list[DLQItem]:
        return [i for i in self._items.values() if i.due()]

    def remove(self, lane_name: str) -> None:
        self._items.pop(lane_name, None)

    def drain(self, client: "httpx.Client") -> int:
        """Force-retry all items ignoring backoff. Returns count drained."""
        drained = 0
        for item in list(self._items.values()):
            try:
                resp = client.post(
                    f"{item.api_url}/tasks",
                    json={
                        "task_type": item.task_type,
                        "payload": item.payload,
                    },
                    headers=task_submission_headers(),
                    timeout=10,
                )
                if resp.status_code == 200:
                    logger.info("DLQ drained lane=%s", item.lane_name)
                    self.remove(item.lane_name)
                    drained += 1
                else:
                    logger.warning(
                        "DLQ drain lane=%s refused: %s",
                        item.lane_name,
                        resp.status_code,
                    )
            except Exception as exc:
                logger.error("DLQ drain lane=%s error: %s", item.lane_name, exc)
        return drained

    def status(self) -> list[dict]:
        return [
            {
                "lane": i.lane_name,
                "task_type": i.task_type,
                "attempts": i.attempts,
                "exhausted": i.exhausted,
                "last_error": i.error,
                "first_failed_at": i.first_failed_at.isoformat(),
                "next_retry_in_s": max(0.0, i.next_retry_at - time.time()),
            }
            for i in self._items.values()
        ]

    def depth(self) -> int:
        return len(self._items)


# Module-level singleton shared by service.py and admin.py
dlq = GatewayDLQ()
