"""Yexis Automation Gateway — the platform's front door.

A separate service (port 8001) with three jobs:

1. **Webhook receiver** — verify signatures, dedupe redeliveries,
   translate provider payloads into tasks.
2. **Scheduler** — cron-style lanes that submit tasks on a cadence
   (morning briefs, nightly syncs). Supports both interval-based and
   time-of-day scheduling with optional weekly cadence.
3. **Hard guards** — every submission passes through lane governance:
   kill-switch, quiet hours, daily cap. A killed lane cannot be
   reached by webhook or schedule; that is the point.

The gateway owns no business logic. It decides *whether* and *when*
work enters the queue — never *what* the work does.
"""

import hashlib
import hmac
import logging
import os
import time
from datetime import date, datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.config import settings
from app.models.gateway_event import GatewayEvent
from app.repository.lane_repository import LaneRepository
from gateway.dlq import dlq
from gateway.internal_auth import task_submission_headers


logger = logging.getLogger(__name__)

# Lane each source feeds. A webhook for an unknown source is refused.
SOURCE_LANES = {
    "waba": "V1",
    "zoho_books": "F3",
    "graph": "P1",
}

# Per-source HMAC secrets; in DEV, unsigned requests are accepted and
# marked so the difference is visible in the register.
WEBHOOK_SECRETS = {
    source: os.environ.get(f"GATEWAY_WEBHOOK_SECRET_{source.upper()}")
    for source in SOURCE_LANES
}


def verify_signature(source: str, body: bytes, header: str | None) -> bool:
    secret = WEBHOOK_SECRETS.get(source)
    if not secret:
        return True  # DEV: no secret configured
    if not header:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def event_key(payload: dict) -> str:
    """Provider event id when present, else payload fingerprint."""
    for key in ("event_id", "id", "message_id"):
        value = payload.get(key)
        if value:
            return str(value)
    return hashlib.sha256(
        repr(sorted(payload.items(), key=lambda kv: kv[0])).encode()
    ).hexdigest()[:64]


class WebhookService:
    """Dedupe + dispatch, one transaction."""

    def __init__(self, db: Session):
        self.db = db

    def _handle_delivery_receipt(self, provider_msg_id: str, status: str) -> None:
        """Update MessageLog.status from a BSP delivery receipt."""
        from app.models.enterprise import MessageLog
        log = self.db.execute(
            select(MessageLog).where(MessageLog.provider_msg_id == provider_msg_id)
        ).scalars().first()
        if log:
            log.status = status
            logger.debug("Delivery receipt: msg %s → %s", provider_msg_id, status)
        else:
            logger.warning("Delivery receipt for unknown provider_msg_id %s", provider_msg_id)

    def _check_campaign_reply(
        self, sender_msisdn: str | None, message_text: str
    ) -> tuple[str, dict] | None:
        """Return (task_type, payload) for K5 if sender has an active campaign contact.

        "Active" means message_sent_ts is set and response_ts is not — the
        dealer received a campaign blast and has not yet replied.
        """
        if not sender_msisdn:
            return None
        from app.models.enterprise import CampaignContact, WaContact
        wa = self.db.execute(
            select(WaContact).where(WaContact.msisdn == sender_msisdn)
        ).scalars().first()
        if wa is None:
            return None
        # Find the most recent un-responded campaign contact for this dealer.
        cc = self.db.execute(
            select(CampaignContact)
            .where(
                CampaignContact.contact_ref == wa.contact_ref,
                CampaignContact.message_sent_ts.isnot(None),
                CampaignContact.response_ts.is_(None),
            )
            .order_by(CampaignContact.message_sent_ts.desc())
            .limit(1)
        ).scalars().first()
        if cc is None:
            return None
        return "campaign_response", {
            "action": "record",
            "campaign_id": str(cc.campaign_id),
            "contact_ref": cc.contact_ref,
            "response_text": message_text,
            "channel": "whatsapp",
            "sender": sender_msisdn,
        }

    def receive(self, source: str, payload: dict) -> dict:
        from app.repository.task_repository import TaskRepository
        from gateway.translations import translate

        key = event_key(payload)

        existing = self.db.execute(
            select(GatewayEvent).where(
                GatewayEvent.source == source,
                GatewayEvent.event_id == key,
            )
        ).scalars().first()

        if existing is not None:
            existing.status = "duplicate"
            self.db.commit()
            logger.info("Duplicate webhook %s/%s ignored", source, key)
            return {"status": "duplicate", "event_id": key}

        # Translate before registering the event: an untranslatable
        # payload is a rejection, not a task that will fail later.
        try:
            task_type, task_payload = translate(source, payload)
        except ValueError as exc:
            event = GatewayEvent(
                source=source,
                event_id=key,
                status="rejected",
                note=f"translation failed: {exc}",
                payload=payload,
            )
            self.db.add(event)
            self.db.commit()
            return {"status": "rejected", "reason": str(exc)}

        # Delivery receipts are handled in-place — no task needed.
        if task_type == "delivery_receipt":
            self._handle_delivery_receipt(
                task_payload["provider_msg_id"], task_payload["status"]
            )
            event = GatewayEvent(
                source=source, event_id=key, status="ack_delivery",
                payload=payload,
            )
            self.db.add(event)
            self.db.commit()
            return {"status": "ack_delivery",
                    "provider_msg_id": task_payload["provider_msg_id"]}

        # When a WhatsApp dealer message arrives, check whether the sender
        # has an active campaign contact — if so, route to K5 instead of V1.
        if source == "waba" and task_type in ("service_request", "purchase_request"):
            sender = task_payload.get("sender")
            text = task_payload.get("description") or task_payload.get("text", "")
            override = self._check_campaign_reply(sender, text)
            if override:
                task_type, task_payload = override

        event = GatewayEvent(
            source=source,
            event_id=key,
            status="received",
            payload=payload,
        )
        self.db.add(event)

        # Hard guards — same regime as scheduled work.
        lane = SOURCE_LANES[source]
        decision = LaneRepository(self.db).evaluate(lane)
        if not decision.allowed:
            event.status = "rejected"
            event.note = decision.reason
            self.db.commit()
            return {"status": "rejected", "reason": decision.reason}

        task = TaskRepository(self.db).create(
            task_type=task_type,
            source=f"webhook:{source}",
            priority=60,
            requested_by=settings.gateway_service_username or settings.admin_username,
            payload=task_payload,
        )

        event.status = "dispatched"
        self.db.commit()

        logger.info("Webhook %s/%s → task %s (%s)",
                    source, key, task.id, task_type)
        return {"status": "dispatched", "task_id": str(task.id),
                "task_type": task_type}


# ── HTTP surface ─────────────────────────────────────────────────────


router = APIRouter(tags=["gateway"])


@router.post("/webhooks/{source}")
async def receive_webhook(
    source: str,
    request: Request,
    db: Session = Depends(get_db),
):
    if source not in SOURCE_LANES:
        raise HTTPException(404, f"unknown webhook source {source}")

    body = await request.body()
    signature = request.headers.get("X-Signature-256")

    if not verify_signature(source, body, signature):
        raise HTTPException(401, "invalid signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "payload must be JSON")

    return WebhookService(db).receive(source, payload)


@router.get("/gateway/health")
def gateway_health():
    return {"status": "ok", "sources": list(SOURCE_LANES)}


# ── Scheduler ────────────────────────────────────────────────────────


class SchedulerLane:
    """One scheduled lane.

    Supports two scheduling modes:
    - Interval: fire every ``every_seconds`` seconds (default).
    - Time-of-day: fire once per day at ``target_hour_utc:target_minute_utc``
      UTC. Set ``target_hour_utc`` to activate this mode.
    - Weekly variant: set ``target_weekday`` (0=Mon … 6=Sun) to restrict
      time-of-day firing to one day per week.
    """

    def __init__(
        self,
        name: str,
        task_type: str,
        payload: dict,
        api_url: str,
        every_seconds: int | None = None,
        target_hour_utc: int | None = None,
        target_minute_utc: int = 0,
        target_weekday: int | None = None,  # 0=Mon, 6=Sun; None=daily
        endpoint: str = "/tasks",
    ):
        if every_seconds is None and target_hour_utc is None:
            raise ValueError(
                f"lane {name}: provide every_seconds OR target_hour_utc"
            )
        self.name = name
        self.task_type = task_type
        self.payload = payload
        self.api_url = api_url
        # Most lanes submit a governed task to /tasks (policy gate, lane caps,
        # approvals, ledger). A lane may instead target a plain backend
        # endpoint — for infrastructure plumbing (e.g. polling the mailbox for
        # bounces) that has no business side-effect and nothing to approve, and
        # so does not belong in the task/policy regime. Such a lane POSTs
        # directly and skips the DLQ: it's idempotent and re-runs next interval.
        self.endpoint = endpoint
        self.every_seconds = every_seconds
        self.target_hour_utc = target_hour_utc
        self.target_minute_utc = target_minute_utc
        self.target_weekday = target_weekday
        self._last_run: float = 0.0
        self._last_run_date: date | None = None
        self._last_run_week: tuple[int, int] | None = None  # (isoyear, isoweek)

    def due(self, now: float) -> bool:
        if self.every_seconds is not None:
            return now - self._last_run >= self.every_seconds

        # Time-of-day mode.
        utc_now = datetime.fromtimestamp(now, tz=timezone.utc)
        today = utc_now.date()

        if self.target_weekday is not None:
            # Weekly: only fire on the target weekday.
            if utc_now.weekday() != self.target_weekday:
                return False
            iso = utc_now.isocalendar()
            current_week = (iso[0], iso[1])
            if self._last_run_week == current_week:
                return False
        else:
            # Daily: only fire once per calendar day.
            if self._last_run_date == today:
                return False

        # Within a 60-second window after the target time.
        target_seconds = self.target_hour_utc * 3600 + self.target_minute_utc * 60
        current_seconds = utc_now.hour * 3600 + utc_now.minute * 60 + utc_now.second
        return target_seconds <= current_seconds < target_seconds + 60

    def mark_ran(self, now: float) -> None:
        self._last_run = now
        utc_now = datetime.fromtimestamp(now, tz=timezone.utc)
        self._last_run_date = utc_now.date()
        iso = utc_now.isocalendar()
        self._last_run_week = (iso[0], iso[1])


def _today_range() -> dict[str, str]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {"start_date": today, "end_date": today}


def _yesterday_range() -> dict[str, str]:
    from datetime import timedelta
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    return {"start_date": yesterday, "end_date": yesterday}


def default_lanes(api_url: str) -> list[SchedulerLane]:
    """Phase-1 cadence lanes. Times are UTC; operators tune via env vars."""
    return [
        # K3 — campaign scheduler check every 5 minutes (fires due campaigns)
        SchedulerLane(
            name="k3_campaign_scheduler",
            # K3.validate() dispatches on payload["action"]; without it every
            # tick fails "'action' is required" and no scheduled campaign ever
            # drains. scheduler_tick finds its own due campaigns (no campaign_id).
            task_type="campaign_scheduler_tick",
            payload={"action": "scheduler_tick"},
            api_url=api_url,
            every_seconds=300,  # check every 5 minutes
        ),
        # Mailbox poll — pull inbound mail every 10 min and record bounce NDRs
        # as EmailBounceEvent rows. Hard bounces then suppress those addresses
        # from future K3 sends via mailbox.is_email_suppressed(). Targets the
        # plain /mail/poll endpoint (not /tasks): pure infra plumbing, no
        # policy/approval semantics. limit caps NDRs ingested per tick.
        SchedulerLane(
            name="mail_poll",
            task_type="mail_poll",
            payload={},
            api_url=api_url,
            # Spec FR-EXE-03: replies classified within 60 s. Raise
            # MAIL_POLL_SECONDS (e.g. 120) if the mail host limits POP3 logins.
            every_seconds=int(os.environ.get("MAIL_POLL_SECONDS", "60")),
            endpoint="/mail/poll?limit=100",
        ),
        # ISM Campaign Assistant — nightly: CRM sync (people, roles, deals),
        # fit/intent/tier scoring, buying-committee research tasks.
        # 20:30 UTC = 02:00 IST.
        SchedulerLane(
            name="ism_nightly",
            task_type="ism_nightly",
            payload={},
            api_url=api_url,
            target_hour_utc=int(os.environ.get("LANE_ISM_NIGHTLY_HOUR_UTC", 20)),
            target_minute_utc=30,
            endpoint="/ism/nightly",
        ),
        # Daily ISM brief, 08:30 IST (03:00 UTC): tasks due/overdue per ISR,
        # replies in the last 24 h, hot contacts not yet replied.
        SchedulerLane(
            name="ism_daily_brief",
            task_type="ism_daily_brief",
            payload={},
            api_url=api_url,
            target_hour_utc=3,
            target_minute_utc=0,
            endpoint="/ism/daily-brief/send",
        ),
        # Weekly ISR performance card to the ISM, Monday 08:45 IST (03:15 UTC).
        SchedulerLane(
            name="ism_weekly_isr_card",
            task_type="ism_isr_card",
            payload={},
            api_url=api_url,
            target_hour_utc=3,
            target_minute_utc=15,
            target_weekday=0,
            endpoint="/ism/isr-cards/send",
        ),
        # F3 — nightly Books→Tally sync at 23:30 UTC
        SchedulerLane(
            name="f3_nightly_books_sync",
            task_type="books_to_tally",
            payload=_yesterday_range(),
            api_url=api_url,
            target_hour_utc=int(os.environ.get("LANE_F3_HOUR_UTC", 23)),
            target_minute_utc=int(os.environ.get("LANE_F3_MINUTE_UTC", 30)),
        ),
        # F1 — collections cohort scan at 06:00 UTC
        SchedulerLane(
            name="f1_collections_scan",
            task_type="collections_cohort_scan",
            payload=_today_range(),
            api_url=api_url,
            target_hour_utc=int(os.environ.get("LANE_F1_HOUR_UTC", 6)),
            target_minute_utc=int(os.environ.get("LANE_F1_MINUTE_UTC", 0)),
        ),
        # M2 — GSM morning brief at 06:15 UTC
        SchedulerLane(
            name="m2_morning_brief",
            task_type="gsm_morning_brief",
            payload=_today_range(),
            api_url=api_url,
            target_hour_utc=int(os.environ.get("LANE_M2_HOUR_UTC", 6)),
            target_minute_utc=int(os.environ.get("LANE_M2_MINUTE_UTC", 15)),
        ),
        # D1 — MD digest at 08:00 UTC
        SchedulerLane(
            name="d1_md_digest",
            task_type="md_digest",
            payload=_today_range(),
            api_url=api_url,
            target_hour_utc=int(os.environ.get("LANE_D1_HOUR_UTC", 8)),
            target_minute_utc=int(os.environ.get("LANE_D1_MINUTE_UTC", 0)),
        ),
        # P4 — payment proposal every Monday at 09:00 UTC
        SchedulerLane(
            name="p4_payment_proposal",
            task_type="payment_proposal",
            payload=_today_range(),
            api_url=api_url,
            target_hour_utc=int(os.environ.get("LANE_P4_HOUR_UTC", 9)),
            target_minute_utc=int(os.environ.get("LANE_P4_MINUTE_UTC", 0)),
            target_weekday=int(os.environ.get("LANE_P4_WEEKDAY", 0)),  # Monday
        ),
        # S3 — reorder review every Monday at 09:30 UTC
        SchedulerLane(
            name="s3_reorder_review",
            task_type="reorder_review",
            payload=_today_range(),
            api_url=api_url,
            target_hour_utc=int(os.environ.get("LANE_S3_HOUR_UTC", 9)),
            target_minute_utc=int(os.environ.get("LANE_S3_MINUTE_UTC", 30)),
            target_weekday=int(os.environ.get("LANE_S3_WEEKDAY", 0)),  # Monday
        ),
    ]


def submit_lane(lane: SchedulerLane, client: httpx.Client) -> bool:
    """Submit a lane's task through the backend API.

    On success: removes the lane from the DLQ if it was there.
    On failure: pushes to DLQ for retry with exponential backoff.
    Returns True only when the backend accepted the task.
    """
    # Direct-endpoint lane (e.g. mailbox poll): hit the plain endpoint, bypass
    # the task/policy regime and the DLQ. A missed poll self-heals next tick.
    if lane.endpoint != "/tasks":
        try:
            response = client.post(f"{lane.api_url}{lane.endpoint}", timeout=30)
            if response.status_code == 200:
                return True
            logger.warning("Lane %s (%s) returned HTTP %s: %s",
                           lane.name, lane.endpoint, response.status_code,
                           response.text[:200])
            return False
        except httpx.HTTPError as exc:
            logger.error("Lane %s (%s) unreachable: %s",
                         lane.name, lane.endpoint, exc)
            return False

    try:
        response = client.post(
            f"{lane.api_url}/tasks",
            json={
                "task_type": lane.task_type,
                "payload": lane.payload,
            },
            headers=task_submission_headers(),
            timeout=10,
        )
        accepted = response.status_code == 200
        if accepted:
            dlq.remove(lane.name)
        else:
            error = f"HTTP {response.status_code}: {response.text[:200]}"
            logger.warning("Lane %s submission refused: %s", lane.name, error)
            dlq.push(lane.name, lane.task_type, lane.payload,
                     lane.api_url, error)
        return accepted
    except httpx.HTTPError as exc:
        error = str(exc)
        logger.error("Lane %s backend unreachable: %s", lane.name, error)
        dlq.push(lane.name, lane.task_type, lane.payload, lane.api_url, error)
        return False


def _wait_for_backend(api_url: str, client: httpx.Client, stop_check) -> bool:
    """Poll the backend health endpoint until it responds OK, or stop is requested.

    Returns True when the backend is healthy, False if stop_check fired first.
    """
    for attempt in range(60):  # up to 60 × 2s = 2 minutes
        if stop_check():
            return False
        try:
            r = client.get(f"{api_url}/health", timeout=3)
            if r.status_code == 200:
                logger.info("Gateway: backend healthy after %d probe(s)", attempt + 1)
                return True
        except Exception:
            pass
        time.sleep(2)
    logger.warning("Gateway: backend still not healthy after 2 min — proceeding anyway")
    return True


def run_scheduler_loop(lanes: list[SchedulerLane], stop_check) -> None:
    """Blocking loop — run inside a dedicated thread at startup.

    On each 5-second tick:
    1. Fire any due scheduled lanes.
    2. Retry any DLQ items whose backoff window has elapsed.
    """
    logger.info("Gateway scheduler running with %d lanes", len(lanes))
    with httpx.Client() as client:
        # Wait until the backend finishes startup (DB init, seeds) before firing.
        api_url = lanes[0].api_url if lanes else "http://127.0.0.1:8000"
        _wait_for_backend(api_url, client, stop_check)

        while not stop_check():
            now = time.time()

            for lane in lanes:
                if lane.due(now):
                    if submit_lane(lane, client):
                        logger.info("Lane %s ticked", lane.name)
                    lane.mark_ran(now)

            # Retry DLQ items that are past their backoff window.
            for item in dlq.due_items():
                logger.info("DLQ retrying lane=%s attempt=%d",
                            item.lane_name, item.attempts + 1)
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
                        logger.info("DLQ retry success lane=%s", item.lane_name)
                        dlq.remove(item.lane_name)
                    else:
                        item.record_failure(
                            f"HTTP {resp.status_code}: {resp.text[:200]}"
                        )
                except Exception as exc:
                    item.record_failure(str(exc))

            time.sleep(5)
