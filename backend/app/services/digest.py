"""Morning MD digest + weekly branch scorecard — deterministic renderers.

Delivery: SMTP (M365 relay) and WhatsApp BSP template.
Both paths are gated by `mail_send_enabled` / `waba_api_key` settings;
if either is not configured the function logs a warning and returns
without raising so the 08:00 cron never silently dies.

Schedules (called from the NightlyScheduler or a dedicated cron):
  morning_digest  — daily at 08:00 IST (triggered when hour==8 and not yet sent)
  branch_scorecard — every Monday (weekday==0)
"""

import logging
import smtplib
import textwrap
from datetime import date, datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx
from sqlalchemy.orm import Session

from app.models.enterprise import (
    Campaign,
    DealerMart,
    KpiSnapshot,
    MessageLog,
    SkuHealth,
    VendorScorecard,
)
from app.sync.cache import SyncCache
from app.models.sync_watermark import SyncWatermark

logger = logging.getLogger(__name__)

DIGEST_LANE = "morning_digest"
SCORECARD_LANE = "branch_scorecard"


# ── helpers ───────────────────────────────────────────────────────


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _kpi(db: Session, kpi_id: str, period: date, entity: str = "YEXIS-EL") -> float | None:
    row = (
        db.query(KpiSnapshot)
        .filter(
            KpiSnapshot.kpi_id == kpi_id,
            KpiSnapshot.period == period,
            KpiSnapshot.entity == entity,
        )
        .first()
    )
    return row.value if row else None


def _fmt_inr(v: float | None) -> str:
    if v is None:
        return "n/a"
    if v >= 1_00_00_000:
        return f"₹{v / 1_00_00_000:.2f} Cr"
    if v >= 1_00_000:
        return f"₹{v / 1_00_000:.2f} L"
    return f"₹{v:,.0f}"


def _fmt_pct(v: float | None) -> str:
    return f"{v:.1f}%" if v is not None else "n/a"


def _fmt_days(v: float | None) -> str:
    return f"{v:.1f}d" if v is not None else "n/a"


# ── digest builders ───────────────────────────────────────────────


def build_morning_digest(
    db: Session,
    period: date | None = None,
    entity: str = "YEXIS-EL",
    cache: SyncCache | None = None,
) -> dict:
    """Assemble the morning KPI snapshot for MD consumption."""
    cache = cache or SyncCache()
    period = period or _utcnow().date()

    # AR snapshot
    total_open = _kpi(db, "total_open_receivables", period, entity)
    overdue = _kpi(db, "overdue_receivables", period, entity)
    dso = _kpi(db, "dso_days", period, entity)

    # Collections promise health
    promise_rel = _kpi(db, "promise_reliability", period, entity)
    cash_acc = _kpi(db, "cash_forecast_accuracy", period, entity)

    # P4 invoice matching
    auto_match = _kpi(db, "p4_auto_match_rate", period, entity)

    # Stock
    stock_avail = _kpi(db, "stock_availability_pct", period, entity)

    # Phase 3 ops KPIs
    amc_renewal = _kpi(db, "amc_renewal_rate_pct", period, entity)
    dispatch_pod = _kpi(db, "dispatch_to_pod_days", period, entity)

    # Phase 3 HR + Campaigns
    attrition_risk = _kpi(db, "attrition_risk_score", period, entity)
    active_campaign_count = (
        db.query(Campaign)
        .filter(Campaign.entity == entity, Campaign.status == "active")
        .count()
    )

    # Top 5 overdue dealers from mart
    top_overdue = (
        db.query(DealerMart)
        .filter(DealerMart.outstanding_amt.isnot(None))
        .order_by(DealerMart.outstanding_amt.desc())
        .limit(5)
        .all()
    )

    # SKU health: count action flags
    sku_action_counts: dict[str, int] = {}
    for sh in db.query(SkuHealth).filter(SkuHealth.period == period).all():
        reco = sh.action_reco or "ok"
        sku_action_counts[reco] = sku_action_counts.get(reco, 0) + 1

    return {
        "period": str(period),
        "entity": entity,
        "ar": {
            "total_open": total_open,
            "overdue": overdue,
            "dso_days": dso,
            "promise_reliability": promise_rel,
            "cash_forecast_accuracy": cash_acc,
        },
        "ops": {
            "p4_auto_match_rate": auto_match,
            "stock_availability_pct": stock_avail,
            "amc_renewal_rate_pct": amc_renewal,
            "dispatch_to_pod_days": dispatch_pod,
        },
        "phase3": {
            "attrition_risk_score": attrition_risk,
            "active_campaign_count": active_campaign_count,
        },
        "top_overdue_dealers": [
            {
                "dealer_ref": d.dealer_ref,
                "branch": d.branch,
                "outstanding_amt": d.outstanding_amt,
                "payment_score": d.payment_behavior_score,
            }
            for d in top_overdue
        ],
        "sku_action_counts": sku_action_counts,
    }


def build_branch_scorecard(
    db: Session,
    period: date | None = None,
    entity: str = "YEXIS-EL",
) -> dict:
    """Weekly per-branch KPI scorecard for branch managers."""
    period = period or _utcnow().date()
    week_start = period - timedelta(days=period.weekday())

    kpi_ids = [
        "dso_days", "overdue_receivables", "promise_reliability",
        "stock_availability_pct", "vendor_otif_pct", "p4_auto_match_rate",
        "f3_books_tally_drift_hrs", "amc_renewal_rate_pct",
        "dispatch_to_pod_days", "scm_claim_resolution_days",
        "campaign_delivery_rate_pct", "campaign_response_rate_pct",
        "attrition_risk_score",
    ]

    branches_seen: set[str] = set()
    rows = (
        db.query(KpiSnapshot)
        .filter(KpiSnapshot.period == period, KpiSnapshot.entity == entity)
        .all()
    )
    for r in rows:
        if r.branch and r.branch != "*":
            branches_seen.add(r.branch)

    if not branches_seen:
        branches_seen = {"*"}

    scorecard: dict[str, dict] = {}
    for branch in sorted(branches_seen):
        branch_kpis = {}
        for kpi_id in kpi_ids:
            row = (
                db.query(KpiSnapshot)
                .filter(
                    KpiSnapshot.kpi_id == kpi_id,
                    KpiSnapshot.period == period,
                    KpiSnapshot.entity == entity,
                    KpiSnapshot.branch == branch,
                )
                .first()
            )
            branch_kpis[kpi_id] = row.value if row else None
        scorecard[branch] = branch_kpis

    return {
        "week_starting": str(week_start),
        "period": str(period),
        "entity": entity,
        "branches": scorecard,
    }


# ── text renderers ────────────────────────────────────────────────


def render_morning_digest_text(data: dict) -> str:
    ar = data["ar"]
    ops = data["ops"]
    ph3 = data.get("phase3", {})
    period = data["period"]

    dealers_block = ""
    for d in data["top_overdue_dealers"]:
        score_str = (
            f" | Score: {d['payment_score']:.2f}"
            if d["payment_score"] is not None else ""
        )
        dealers_block += (
            f"  • {d['dealer_ref']} ({d['branch'] or '—'}): "
            f"{_fmt_inr(d['outstanding_amt'])}{score_str}\n"
        )
    if not dealers_block:
        dealers_block = "  (no open receivables)\n"

    sku_block = "  " + ", ".join(
        f"{k}: {v}" for k, v in sorted(data["sku_action_counts"].items())
    ) if data["sku_action_counts"] else "  (no SKU data)"

    active_campaigns = ph3.get("active_campaign_count", 0)
    attrition = ph3.get("attrition_risk_score")

    return textwrap.dedent(f"""\
        *Yexis Electronics — Morning Digest* | {period}

        *AR Health*
          Total open:   {_fmt_inr(ar["total_open"])}
          Overdue:      {_fmt_inr(ar["overdue"])}
          DSO:          {_fmt_days(ar["dso_days"])}
          Promise rel:  {_fmt_pct(ar["promise_reliability"])}
          Cash fcst:    {_fmt_pct(ar["cash_forecast_accuracy"])}

        *Operations*
          P4 auto-match:    {_fmt_pct(ops["p4_auto_match_rate"])}
          Stock avail:      {_fmt_pct(ops["stock_availability_pct"])}
          AMC renewal:      {_fmt_pct(ops["amc_renewal_rate_pct"])}
          Dispatch→POD:     {_fmt_days(ops["dispatch_to_pod_days"])}

        *People & Campaigns*
          Attrition risk:   {f"{attrition:.1f}" if attrition is not None else "n/a"} (mean score)
          Active campaigns: {active_campaigns}

        *Top 5 Outstanding Dealers*
        {dealers_block}
        *SKU Action Flags*
        {sku_block}
    """)


def render_branch_scorecard_text(data: dict) -> str:
    week = data["week_starting"]
    lines = [f"*Yexis Branch Scorecard — Week of {week}*\n"]
    for branch, kpis in data["branches"].items():
        attrition = kpis.get("attrition_risk_score")
        lines.append(f"  Branch: {branch}")
        lines.append(f"    DSO:               {_fmt_days(kpis.get('dso_days'))}")
        lines.append(f"    Overdue AR:        {_fmt_inr(kpis.get('overdue_receivables'))}")
        lines.append(f"    Promise rel:       {_fmt_pct(kpis.get('promise_reliability'))}")
        lines.append(f"    Stock avail:       {_fmt_pct(kpis.get('stock_availability_pct'))}")
        lines.append(f"    Vendor OTIF:       {_fmt_pct(kpis.get('vendor_otif_pct'))}")
        lines.append(f"    P4 auto-match:     {_fmt_pct(kpis.get('p4_auto_match_rate'))}")
        lines.append(f"    TB drift hrs:      {kpis.get('f3_books_tally_drift_hrs', 'n/a')}")
        lines.append(f"    AMC renewal:       {_fmt_pct(kpis.get('amc_renewal_rate_pct'))}")
        lines.append(f"    Dispatch→POD:      {_fmt_days(kpis.get('dispatch_to_pod_days'))}")
        lines.append(f"    SCM claim res:     {_fmt_days(kpis.get('scm_claim_resolution_days'))}")
        lines.append(f"    Campaign delivery: {_fmt_pct(kpis.get('campaign_delivery_rate_pct'))}")
        lines.append(f"    Campaign response: {_fmt_pct(kpis.get('campaign_response_rate_pct'))}")
        lines.append(f"    Attrition risk:    {f'{attrition:.1f}' if attrition is not None else 'n/a'}")
        lines.append("")
    return "\n".join(lines)


# ── delivery ──────────────────────────────────────────────────────


def _send_smtp(subject: str, body: str) -> bool:
    from app.core.config import settings
    if not settings.mail_send_enabled:
        logger.info("SMTP delivery disabled — digest not sent via email")
        return False
    if not settings.smtp_host:
        logger.warning("smtp_host not configured — skipping email digest")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from
        msg["To"] = getattr(settings, "digest_recipient_email", settings.smtp_from)
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port) as srv:
            srv.login(settings.smtp_username, settings.smtp_password)
            srv.send_message(msg)
        logger.info("Digest sent via SMTP to %s", msg["To"])
        return True
    except Exception:
        logger.exception("SMTP delivery failed")
        return False


def _send_whatsapp(body: str, db: Session) -> bool:
    """Send plain-text digest via WhatsApp BSP (Gallabox/Gupshup text message)."""
    from app.core.config import settings
    if not settings.waba_api_key or not settings.waba_api_url:
        logger.info("WhatsApp not configured — skipping WA digest")
        return False
    recipient = getattr(settings, "digest_recipient_wa", "")
    if not recipient:
        logger.warning("digest_recipient_wa not set — skipping WA digest")
        return False
    try:
        payload = {
            "channelId": settings.waba_phone_number_id,
            "channelType": "whatsapp",
            "recipient": {"phone": recipient},
            "message": {"type": "text", "text": body},
        }
        resp = httpx.post(
            f"{settings.waba_api_url}/v1/messages",
            json=payload,
            headers={"x-api-key": settings.waba_api_key},
            timeout=15,
        )
        resp.raise_for_status()
        db.add(MessageLog(
            direction="out",
            channel="wa",
            lane=DIGEST_LANE,
            to_ref=recipient,
            msisdn=recipient.lstrip("+"),
            status="sent",
            provider_msg_id=(resp.json() or {}).get("messageId"),
        ))
        db.commit()
        logger.info("Digest sent via WhatsApp to %s", recipient)
        return True
    except Exception:
        logger.exception("WhatsApp delivery failed")
        return False


# ── public entry points ───────────────────────────────────────────


def send_morning_digest(
    db: Session,
    period: date | None = None,
    entity: str = "YEXIS-EL",
    cache: SyncCache | None = None,
) -> dict:
    """Build and deliver the 08:00 MD digest. Never raises."""
    data = build_morning_digest(db, period=period, entity=entity, cache=cache)
    text = render_morning_digest_text(data)
    subject = f"Yexis Morning Digest — {data['period']}"
    email_ok = _send_smtp(subject, text)
    wa_ok = _send_whatsapp(text, db)
    return {
        "period": data["period"],
        "email_sent": email_ok,
        "wa_sent": wa_ok,
        "data": data,
    }


def send_branch_scorecard(
    db: Session,
    period: date | None = None,
    entity: str = "YEXIS-EL",
) -> dict:
    """Build and deliver the weekly branch scorecard. Never raises."""
    data = build_branch_scorecard(db, period=period, entity=entity)
    text = render_branch_scorecard_text(data)
    subject = f"Yexis Branch Scorecard — Week of {data['week_starting']}"
    email_ok = _send_smtp(subject, text)
    wa_ok = _send_whatsapp(text, db)
    return {
        "week_starting": data["week_starting"],
        "email_sent": email_ok,
        "wa_sent": wa_ok,
        "data": data,
    }


# ── digest scheduler integration ─────────────────────────────────


DIGEST_SOURCE = "morning_digest"
SCORECARD_SOURCE = "weekly_scorecard"


def digest_due(db: Session, now: datetime | None = None) -> bool:
    """True when today's morning digest has not been sent and it is 08:00+."""
    from app.core.config import settings as _cfg

    now = now or datetime.now()
    digest_hour = int(getattr(_cfg, "digest_hour", 8))
    if now.hour < digest_hour:
        return False
    wm = (
        db.query(SyncWatermark)
        .filter(SyncWatermark.lane == DIGEST_LANE, SyncWatermark.source == DIGEST_SOURCE)
        .first()
    )
    today = date.today().isoformat()
    return wm is None or wm.cursor != today


def scorecard_due(db: Session, now: datetime | None = None) -> bool:
    """True on Monday after 08:00 if scorecard not yet sent this week."""
    from app.core.config import settings as _cfg

    now = now or datetime.now()
    digest_hour = int(getattr(_cfg, "digest_hour", 8))
    if now.weekday() != 0 or now.hour < digest_hour:
        return False
    today = date.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    wm = (
        db.query(SyncWatermark)
        .filter(SyncWatermark.lane == SCORECARD_LANE, SyncWatermark.source == SCORECARD_SOURCE)
        .first()
    )
    return wm is None or wm.cursor != week_start


def _stamp_digest(db: Session, lane: str, source: str, cursor: str) -> None:
    wm = (
        db.query(SyncWatermark)
        .filter(SyncWatermark.lane == lane, SyncWatermark.source == source)
        .first()
    )
    if wm is None:
        db.add(SyncWatermark(lane=lane, source=source, cursor=cursor))
    else:
        wm.cursor = cursor
    db.commit()


def run_digest_if_due(db: Session, cache: SyncCache | None = None) -> dict:
    """Called from the nightly/morning scheduler loop. Never raises."""
    now = datetime.now()
    results: dict[str, dict] = {}

    if digest_due(db, now):
        try:
            results["morning_digest"] = send_morning_digest(db, cache=cache)
            _stamp_digest(db, DIGEST_LANE, DIGEST_SOURCE, date.today().isoformat())
        except Exception:
            logger.exception("Morning digest failed")
            results["morning_digest"] = {"status": "error"}

    if scorecard_due(db, now):
        try:
            results["branch_scorecard"] = send_branch_scorecard(db)
            today = date.today()
            week_start = (today - timedelta(days=today.weekday())).isoformat()
            _stamp_digest(db, SCORECARD_LANE, SCORECARD_SOURCE, week_start)
        except Exception:
            logger.exception("Branch scorecard failed")
            results["branch_scorecard"] = {"status": "error"}

    return results
