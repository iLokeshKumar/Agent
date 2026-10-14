"""Enterprise tables — real Local DB per Cowork Tech Spec §6-7.

These are NOT mock tables. Every column is typed to match live systems
(Zoho IDs, Tally names, E.164 msisdn, etc.) and is append-mostly.
Agents write via typed MCP tools only — no free-form SQL in prod.

Import this file in app/main.py::_init_database() so Base.metadata
creates them on startup (create_all is idempotent).

Tables:
  Local (yexis) — wa_contact, message_log, template_registry,
                  doc_index, serial_registry, custody_event,
                  requisition, vendor_scorecard_event, promise_record,
                  object_lock, tb_reconciliation, interentity_open_items,
                  ledger_map/godown_map/costcentre_map
  Cloud mirrors (same DB for now, split later) — kpi_snapshot,
                  dealer_mart, sku_health, cash_forecast, etc. are
                  created here too; a future migration will move them
                  to yexis_agent_cloud.
"""

import uuid
from datetime import datetime, date, time

from sqlalchemy import Boolean, Date, DateTime, Index, String, Text, Integer, Float, Time, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.agent import Base

# ── Messaging & Registry ──────────────────────────────────────────

class WaContact(Base):
    """WhatsApp contact — consent + session window (BSP rules)."""
    __tablename__ = "wa_contact"

    msisdn: Mapped[str] = mapped_column(String(20), primary_key=True)  # E.164 without +
    contact_ref: Mapped[str | None] = mapped_column(String(100))  # CRM contact id
    entity: Mapped[str] = mapped_column(String(20), nullable=False, default="YEXIS-EL")
    display_name: Mapped[str | None] = mapped_column(String(200))
    language: Mapped[str] = mapped_column(String(10), default="en")
    opt_in_status: Mapped[str] = mapped_column(String(20), default="unknown")  # opt_in | opt_out | unknown
    opt_in_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_inbound_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_window_expires: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContactOptOut(Base):
    """Global opt-out register — one row per contact_ref that has unsubscribed.

    Works for any channel (email, WA, etc.) because it keys on contact_ref,
    not msisdn. K3 checks this table before sending on any channel.
    Re-subscribing deletes the row.
    """
    __tablename__ = "contact_opt_out"

    contact_ref: Mapped[str] = mapped_column(String(100), primary_key=True)
    opted_out_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    email: Mapped[str | None] = mapped_column(String(200))
    name: Mapped[str | None] = mapped_column(String(200))
    msisdn: Mapped[str | None] = mapped_column(String(20))
    campaign_id: Mapped[str | None] = mapped_column(String(100))  # last campaign they opted out from

class MessageLog(Base):
    """Every agent message on every channel — feeds kill-switch analytics."""
    __tablename__ = "message_log"
    __table_args__ = (
        Index("ix_message_log_correlation", "correlation_ref"),
        Index("ix_message_log_msisdn", "msisdn"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # in | out
    channel: Mapped[str] = mapped_column(String(20), nullable=False)  # wa | email
    lane: Mapped[str | None] = mapped_column(String(50))
    template_id: Mapped[str | None] = mapped_column(String(100))
    to_ref: Mapped[str | None] = mapped_column(String(200))
    msisdn: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(200))
    correlation_type: Mapped[str | None] = mapped_column(String(50))
    correlation_ref: Mapped[str | None] = mapped_column(String(200))
    body_hash: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="sent")  # sent|delivered|read|failed|bounced
    provider_msg_id: Mapped[str | None] = mapped_column(String(200))


class EmailMessage(Base):
    """Mailbox intake and internal draft register.

    POP3 cannot create server-side drafts, so drafts live here until a
    named approver permits SMTP delivery.  The external message body is
    retained only for the operational retention period set by policy.
    """
    __tablename__ = "email_message"
    __table_args__ = (
        UniqueConstraint("mailbox_uid", name="uq_email_message_uid"),
        Index("ix_email_message_correlation", "correlation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mailbox_uid: Mapped[str | None] = mapped_column(String(300), nullable=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # in | out
    status: Mapped[str] = mapped_column(String(30), nullable=False)  # received | draft | pending_approval | sent | failed
    sender: Mapped[str | None] = mapped_column(String(320))
    recipient: Mapped[str | None] = mapped_column(String(320))
    subject: Mapped[str | None] = mapped_column(String(500))
    body: Mapped[str | None] = mapped_column(Text)
    # List of {filename, path, size, mime_type} dicts — populated by the upload endpoint
    attachments: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approval_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    provider_message_id: Mapped[str | None] = mapped_column(String(300))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmailBounceEvent(Base):
    """A bounce notification (NDR) detected in polled mailbox traffic.

    Gives visibility into *why* an address bounced (SMTP status code +
    diagnostic text), not just that it did. A 'hard' event permanently
    suppresses that address from future K3 campaign email sends — see
    app.services.mailbox.is_email_suppressed(). A 'soft' event (mailbox
    full, greylisting, temporary DNS failure) is recorded for visibility
    only and does not suppress, since it's usually transient.
    """
    __tablename__ = "email_bounce_event"
    __table_args__ = (Index("ix_email_bounce_event_email", "email"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    bounce_class: Mapped[str] = mapped_column(String(10), nullable=False)  # hard | soft | unknown
    smtp_code: Mapped[str | None] = mapped_column(String(20))
    diagnostic: Mapped[str | None] = mapped_column(Text)
    source_subject: Mapped[str | None] = mapped_column(String(500))
    email_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class EmailEngagementEvent(Base):
    """Detailed open/click tracking for K3 campaign email.

    One row per open (pixel hit) or click (tracked-link redirect), so
    engagement can be broken down per contact and per link, not just as
    an aggregate rate. See GET /campaigns/{id}/engagement.
    """
    __tablename__ = "email_engagement_event"
    __table_args__ = (
        Index("ix_email_engagement_event_campaign", "campaign_id"),
        Index("ix_email_engagement_event_message_log", "message_log_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_log_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    contact_ref: Mapped[str | None] = mapped_column(String(100))
    event_type: Mapped[str] = mapped_column(String(10), nullable=False)  # open | click
    url: Mapped[str | None] = mapped_column(Text)  # original destination — click events only
    user_agent: Mapped[str | None] = mapped_column(String(500))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class TemplateRegistry(Base):
    """Approved templates — no outbound without a row here."""
    __tablename__ = "template_registry"

    template_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)  # wa | email
    purpose: Mapped[str | None] = mapped_column(String(100))
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    variables_json: Mapped[dict | None] = mapped_column(JSONB)
    approval_status: Mapped[str] = mapped_column(String(20), default="pending")  # meta | dlt | internal | pending
    approved_by: Mapped[str | None] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer, default=1)
    # Email-specific fields
    subject: Mapped[str | None] = mapped_column(String(300))   # Jinja2-renderable subject line
    reply_to: Mapped[str | None] = mapped_column(String(254))  # Reply-To address
    # Per-segment (contact_type) subject/body overrides for dynamic email —
    # {"dealer": {"subject": "...", "prepend": "...", "append": "..."}, ...}
    segment_overrides_json: Mapped[dict | None] = mapped_column(JSONB)
    # Email attachments shared across every recipient of this template —
    # list of {"filename", "path", "size", "mime_type"}. Files live on disk
    # under backend/attachments/templates/{template_id}/; the DB holds metadata.
    attachments_json: Mapped[list | None] = mapped_column(JSONB)

class DocIndex(Base):
    """Indexed inbound documents — files on encrypted on-prem volume, DB holds extraction."""
    __tablename__ = "doc_index"
    __table_args__ = (Index("ix_doc_index_class", "doc_class"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    received_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    source: Mapped[str] = mapped_column(String(20))  # outlook | wa | upload
    source_ref: Mapped[str | None] = mapped_column(String(200))
    sender_ref: Mapped[str | None] = mapped_column(String(200))
    doc_class: Mapped[str] = mapped_column(String(30))  # vendor_invoice | quote | circular | cv | pod | kyc | other
    file_uri: Mapped[str | None] = mapped_column(String(500))
    pages: Mapped[int | None] = mapped_column(Integer)
    extract_json: Mapped[dict | None] = mapped_column(JSONB)
    confidence: Mapped[float | None] = mapped_column(Float)
    linked_object_type: Mapped[str | None] = mapped_column(String(50))
    linked_ref: Mapped[str | None] = mapped_column(String(200))
    retention_class: Mapped[str | None] = mapped_column(String(30))
    purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class DocExtraction(Base):
    """WP-4 extraction results — one row per extraction attempt.

    The human-review fallback lives here: anything the extractor pulls
    out below ``review_threshold`` lands with ``needs_review=True`` and
    never reaches a downstream agent until a human confirms (or fixes)
    the payload. Confirmed rows record who and when, keeping the
    extraction auditable end-to-end.
    """
    __tablename__ = "doc_extraction"
    __table_args__ = (Index("ix_doc_extraction_review", "needs_review", "kind"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    doc_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # → doc_index.id
    kind: Mapped[str] = mapped_column(String(30), nullable=False)  # scheme_circular | kyc | serial_capture
    ocr_provider: Mapped[str | None] = mapped_column(String(30))  # text | tesseract | llm_vision
    pages: Mapped[int | None] = mapped_column(Integer)
    fields_json: Mapped[dict | None] = mapped_column(JSONB)  # extracted key→value
    fields_conf_json: Mapped[dict | None] = mapped_column(JSONB)  # key→0..1 confidence
    overall_confidence: Mapped[float] = mapped_column(Float, default=0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reason: Mapped[str | None] = mapped_column(String(200))
    reviewed_by: Mapped[str | None] = mapped_column(String(100))
    reviewed_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    downstream_ref: Mapped[str | None] = mapped_column(String(200))  # scheme_id / serial after accept
    status: Mapped[str] = mapped_column(String(20), default="extracted")  # extracted|accepted|rejected|expired
    created_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

# ── Serialised truth ──────────────────────────────────────────────

class SerialRegistry(Base):
    """The serialised truth for S1/S4/F2/V1 — every Samsung serial's state."""
    __tablename__ = "serial_registry"

    serial: Mapped[str] = mapped_column(String(100), primary_key=True)
    sku_ref: Mapped[str | None] = mapped_column(String(100))
    entity: Mapped[str] = mapped_column(String(20), nullable=False)
    grn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    received_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="in_stock")  # in_stock|reserved|issued_project|demo_out|sold|rma|returned|written_off
    location_ref: Mapped[str | None] = mapped_column(String(100))
    custodian_type: Mapped[str | None] = mapped_column(String(20))  # branch|gsm|dealer|customer|site
    custodian_ref: Mapped[str | None] = mapped_column(String(200))
    project_ref: Mapped[str | None] = mapped_column(String(100))
    sold_invoice_ref: Mapped[str | None] = mapped_column(String(100))
    last_event_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

class CustodyEvent(Base):
    """Photo-proof chain for demo & project material."""
    __tablename__ = "custody_event"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    serial: Mapped[str] = mapped_column(String(100), nullable=False)
    event: Mapped[str] = mapped_column(String(20))  # issue | return | transfer | convert
    from_ref: Mapped[str | None] = mapped_column(String(200))
    to_ref: Mapped[str | None] = mapped_column(String(200))
    challan_ref: Mapped[str | None] = mapped_column(String(100))
    photo_uri: Mapped[str | None] = mapped_column(String(500))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    confirmed_by: Mapped[str | None] = mapped_column(String(100))

# ── Purchase & SCM ────────────────────────────────────────────────

class Requisition(Base):
    """P1 working object before it becomes a Books PO."""
    __tablename__ = "requisition"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    requester_ref: Mapped[str | None] = mapped_column(String(100))
    entity: Mapped[str] = mapped_column(String(20), nullable=False)
    mode: Mapped[str] = mapped_column(String(20))  # stock | project | indirect
    lines_json: Mapped[dict | None] = mapped_column(JSONB)
    need_by: Mapped[date | None] = mapped_column(Date)
    project_ref: Mapped[str | None] = mapped_column(String(100))
    budget_check: Mapped[str | None] = mapped_column(String(20))  # pass | fail | override
    status: Mapped[str] = mapped_column(String(30), default="new")  # new|approved|po_created|rejected
    po_ref: Mapped[str | None] = mapped_column(String(100))

class VendorScorecardEvent(Base):
    """Raw vendor OTIF events — aggregates roll to Cloud vendor_scorecard."""
    __tablename__ = "vendor_scorecard_event"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    po_ref: Mapped[str | None] = mapped_column(String(100))
    event: Mapped[str] = mapped_column(String(20))  # confirm | delay | deliver
    promised_dt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_dt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    qty_var: Mapped[float | None] = mapped_column(Float)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

class PromiseRecord(Base):
    __tablename__ = "promise_record"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str | None] = mapped_column(String(50))
    account_ref: Mapped[str | None] = mapped_column(String(100))
    invoice_ref: Mapped[str | None] = mapped_column(String(100))
    promised_amount: Mapped[float | None] = mapped_column(Float)
    promised_date: Mapped[date | None] = mapped_column(Date)
    made_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    outcome: Mapped[str] = mapped_column(String(20), default="pending")  # kept|broken|partial|pending

class ObjectLock(Base):
    """Advisory serialisation per business object — one agent at a time."""
    __tablename__ = "object_lock"
    __table_args__ = (UniqueConstraint("object_type", "object_ref", name="uq_object_lock"),)

    object_type: Mapped[str] = mapped_column(String(50), primary_key=True)
    object_ref: Mapped[str] = mapped_column(String(200), primary_key=True)
    holder_lane: Mapped[str | None] = mapped_column(String(50))
    acquired_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

# ── Finance staging ───────────────────────────────────────────────

class TbReconciliation(Base):
    """Nightly tripwire for F3 — non-zero drift blocks next staging cycle."""
    __tablename__ = "tb_reconciliation"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity: Mapped[str] = mapped_column(String(20), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    books_tb_hash: Mapped[str | None] = mapped_column(String(100))
    tally_tb_hash: Mapped[str | None] = mapped_column(String(100))
    drift_amount: Mapped[float] = mapped_column(Float, default=0)
    drift_lines_json: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="clean")  # clean|investigating|resolved
    resolved_note: Mapped[str | None] = mapped_column(Text)

class InterentityOpenItem(Base):
    """F4 working set — daily fingerprint matching across both Books orgs."""
    __tablename__ = "interentity_open_items"
    __table_args__ = (Index("ix_interentity_status", "match_status"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    doc_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entity_from: Mapped[str] = mapped_column(String(20))
    entity_to: Mapped[str] = mapped_column(String(20))
    doc_type: Mapped[str] = mapped_column(String(20))  # po | invoice | payment | journal
    doc_no: Mapped[str | None] = mapped_column(String(100))
    doc_ref: Mapped[str | None] = mapped_column(String(100))
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    match_status: Mapped[str] = mapped_column(String(20), default="open")  # open|suggested|matched|disputed
    matched_group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    suggestion_json: Mapped[dict | None] = mapped_column(JSONB)
    age_days: Mapped[int | None] = mapped_column(Integer)
    settled_period: Mapped[str | None] = mapped_column(String(20))

class LedgerMap(Base):
    __tablename__ = "ledger_map"
    __table_args__ = (UniqueConstraint("books_code", "entity", name="uq_ledger_map"),)

    books_code: Mapped[str] = mapped_column(String(200), primary_key=True)
    tally_ledger: Mapped[str] = mapped_column(String(200), nullable=False)
    entity: Mapped[str] = mapped_column(String(20), primary_key=True)
    gst_treatment: Mapped[str | None] = mapped_column(String(50))
    effective_from: Mapped[date | None] = mapped_column(Date)

class GodownMap(Base):
    __tablename__ = "godown_map"
    __table_args__ = (UniqueConstraint("books_code", "entity", name="uq_godown_map"),)

    books_code: Mapped[str] = mapped_column(String(200), primary_key=True)
    tally_godown: Mapped[str] = mapped_column(String(200), nullable=False)
    entity: Mapped[str] = mapped_column(String(20), primary_key=True)
    effective_from: Mapped[date | None] = mapped_column(Date)

class CostCentreMap(Base):
    __tablename__ = "costcentre_map"
    __table_args__ = (UniqueConstraint("books_code", "entity", name="uq_cc_map"),)

    books_code: Mapped[str] = mapped_column(String(200), primary_key=True)
    tally_costcentre: Mapped[str] = mapped_column(String(200), nullable=False)
    entity: Mapped[str] = mapped_column(String(20), primary_key=True)
    effective_from: Mapped[date | None] = mapped_column(Date)

# ── Cloud/analytics mirrors (same DB for now, split to yexis_agent_cloud later) ──

class KpiSnapshot(Base):
    __tablename__ = "kpi_snapshot"
    __table_args__ = (UniqueConstraint("kpi_id", "period", "entity", "branch", name="uq_kpi_snapshot"),)

    kpi_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    period: Mapped[date] = mapped_column(Date, primary_key=True)
    entity: Mapped[str | None] = mapped_column(String(20), primary_key=True)
    branch: Mapped[str | None] = mapped_column(String(50), primary_key=True)
    owner_role: Mapped[str | None] = mapped_column(String(50))
    value: Mapped[float | None] = mapped_column(Float)
    target: Mapped[float | None] = mapped_column(Float)
    baseline: Mapped[float | None] = mapped_column(Float)
    source_lane: Mapped[str | None] = mapped_column(String(50))
    computed_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

class DealerMart(Base):
    """Nightly join powering M2 dealer cards — real Books+CRM+F1+S2 data."""
    __tablename__ = "dealer_mart"

    dealer_ref: Mapped[str] = mapped_column(String(100), primary_key=True)
    branch: Mapped[str | None] = mapped_column(String(50))
    outstanding_amt: Mapped[float | None] = mapped_column(Float)
    overdue_buckets_json: Mapped[dict | None] = mapped_column(JSONB)
    payment_behavior_score: Mapped[float | None] = mapped_column(Float)
    last_order_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    offtake_13wk_json: Mapped[dict | None] = mapped_column(JSONB)
    scheme_eligibility_json: Mapped[dict | None] = mapped_column(JSONB)
    stock_opportunity_json: Mapped[dict | None] = mapped_column(JSONB)
    refreshed_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

class SkuHealth(Base):
    __tablename__ = "sku_health"
    __table_args__ = (UniqueConstraint("sku_ref", "branch", "period", name="uq_sku_health"),)

    sku_ref: Mapped[str] = mapped_column(String(100), primary_key=True)
    branch: Mapped[str] = mapped_column(String(50), primary_key=True)
    period: Mapped[date] = mapped_column(Date, primary_key=True)
    on_hand: Mapped[int | None] = mapped_column(Integer)
    weeks_cover: Mapped[float | None] = mapped_column(Float)
    ageing_buckets_json: Mapped[dict | None] = mapped_column(JSONB)
    eol_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    action_reco: Mapped[str | None] = mapped_column(String(100))

class InstalledBase(Base):
    """V1/V2 spine — serials mirrored (not mastered) from Local serial_registry."""
    __tablename__ = "installed_base"

    asset_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    serial: Mapped[str | None] = mapped_column(String(100))
    customer_ref: Mapped[str | None] = mapped_column(String(100))
    site_text: Mapped[str | None] = mapped_column(String(300))
    product_ref: Mapped[str | None] = mapped_column(String(100))
    sold_invoice_ref: Mapped[str | None] = mapped_column(String(100))
    install_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    warranty_end: Mapped[date | None] = mapped_column(Date)
    amc_contract_ref: Mapped[str | None] = mapped_column(String(100))
    amc_end: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="active")
    last_service_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fault_count_12m: Mapped[int | None] = mapped_column(Integer, default=0)


class CashForecast(Base):
    """F5 13-week rolling cash forecast per entity."""
    __tablename__ = "cash_forecast"
    __table_args__ = (UniqueConstraint("entity", "period", "week_offset", name="uq_cash_forecast"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity: Mapped[str] = mapped_column(String(20), nullable=False)
    period: Mapped[date] = mapped_column(Date, nullable=False)
    week_offset: Mapped[int] = mapped_column(Integer, nullable=False)  # 0 = current week, 1-12 = future
    predicted_inflow: Mapped[float | None] = mapped_column(Float)
    predicted_outflow: Mapped[float | None] = mapped_column(Float)
    net_position: Mapped[float | None] = mapped_column(Float)
    confidence_pct: Mapped[float | None] = mapped_column(Float)
    computed_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class JobOpening(Base):
    """H1 working object — one row per open position."""
    __tablename__ = "job_opening"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    department: Mapped[str | None] = mapped_column(String(100))
    entity: Mapped[str] = mapped_column(String(20), nullable=False, default="YEXIS-EL")
    requisition_ref: Mapped[str | None] = mapped_column(String(100))
    opened_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    target_fill_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|filled|cancelled|on_hold
    filled_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class HrEmployee(Base):
    """Employee registry — H1-H4 reference spine."""
    __tablename__ = "hr_employee"

    employee_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    department: Mapped[str | None] = mapped_column(String(100))
    role: Mapped[str | None] = mapped_column(String(100))
    entity: Mapped[str] = mapped_column(String(20), nullable=False, default="YEXIS-EL")
    join_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|resigned|terminated
    manager_ref: Mapped[str | None] = mapped_column(String(100))
    risk_score: Mapped[float | None] = mapped_column(Float)
    last_engagement_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    onboarding_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|in_progress|complete


class OnboardingChecklist(Base):
    """H2 onboarding tasks per employee."""
    __tablename__ = "onboarding_checklist"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id: Mapped[str] = mapped_column(String(100), nullable=False)
    item: Mapped[str] = mapped_column(String(200), nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner: Mapped[str | None] = mapped_column(String(100))


class VendorScorecard(Base):
    """Nightly vendor OTIF mart — aggregated from vendor_scorecard_event."""
    __tablename__ = "vendor_scorecard"
    __table_args__ = (UniqueConstraint("vendor_ref", "period", "entity", name="uq_vendor_scorecard"),)

    vendor_ref: Mapped[str] = mapped_column(String(100), primary_key=True)
    period: Mapped[date] = mapped_column(Date, primary_key=True)
    entity: Mapped[str] = mapped_column(String(20), primary_key=True, default="YEXIS-EL")
    total_events: Mapped[int] = mapped_column(Integer, default=0)
    on_time_pct: Mapped[float | None] = mapped_column(Float)
    full_qty_pct: Mapped[float | None] = mapped_column(Float)
    otif_pct: Mapped[float | None] = mapped_column(Float)
    avg_delay_days: Mapped[float | None] = mapped_column(Float)
    refreshed_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


# ── Phase 3 tables ────────────────────────────────────────────────────


class PhysicalCountBatch(Base):
    """S4 physical verification — one batch per count exercise."""
    __tablename__ = "physical_count_batch"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity: Mapped[str] = mapped_column(String(20), nullable=False)
    branch: Mapped[str | None] = mapped_column(String(50))
    started_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    closed_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    counted_by: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|reconciled|approved
    total_counted: Mapped[int] = mapped_column(Integer, default=0)
    matched: Mapped[int] = mapped_column(Integer, default=0)
    missing_count: Mapped[int] = mapped_column(Integer, default=0)
    extra_count: Mapped[int] = mapped_column(Integer, default=0)
    mismatch_count: Mapped[int] = mapped_column(Integer, default=0)


class PhysicalCountLine(Base):
    """Each serial scanned during S4 count."""
    __tablename__ = "physical_count_line"
    __table_args__ = (UniqueConstraint("batch_id", "serial", name="uq_count_line_batch_serial"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    serial: Mapped[str] = mapped_column(String(100), nullable=False)
    sku_ref: Mapped[str | None] = mapped_column(String(100))
    scanned_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    result: Mapped[str | None] = mapped_column(String(20))  # matched|missing|extra|status_mismatch


class AmcContract(Base):
    """V2 AMC contract — links InstalledBase asset to a service contract."""
    __tablename__ = "amc_contract"
    __table_args__ = (UniqueConstraint("contract_ref", name="uq_amc_contract_ref"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contract_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(100), nullable=False)
    customer_ref: Mapped[str | None] = mapped_column(String(100))
    entity: Mapped[str] = mapped_column(String(20), nullable=False, default="YEXIS-EL")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|expired|renewed|cancelled
    renewal_ref: Mapped[str | None] = mapped_column(String(100))  # child contract_ref on renewal
    renewed_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Indent(Base):
    """C1 indent planning — replenishment indent suggestion per SKU."""
    __tablename__ = "indent"
    __table_args__ = (UniqueConstraint("sku_ref", "entity", "period", name="uq_indent_sku_period"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sku_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    entity: Mapped[str] = mapped_column(String(20), nullable=False, default="YEXIS-EL")
    period: Mapped[date] = mapped_column(Date, nullable=False)
    qty_suggested: Mapped[int] = mapped_column(Integer, nullable=False)
    qty_approved: Mapped[int | None] = mapped_column(Integer)
    weeks_cover_now: Mapped[float | None] = mapped_column(Float)
    avg_weekly_offtake: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|submitted|approved|rejected
    submitted_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class DispatchRecord(Base):
    """C2 dispatch — tracks Samsung→Yexis and Yexis→Dealer movements."""
    __tablename__ = "dispatch_record"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dispatch_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    dispatch_type: Mapped[str] = mapped_column(String(20), nullable=False)  # inbound|outbound
    from_ref: Mapped[str | None] = mapped_column(String(100))  # vendor/branch
    to_ref: Mapped[str | None] = mapped_column(String(100))   # branch/dealer
    entity: Mapped[str] = mapped_column(String(20), nullable=False, default="YEXIS-EL")
    invoice_ref: Mapped[str | None] = mapped_column(String(100))
    qty: Mapped[int | None] = mapped_column(Integer)
    dispatch_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expected_delivery: Mapped[date | None] = mapped_column(Date)
    pod_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pod_ref: Mapped[str | None] = mapped_column(String(200))  # POD document reference
    status: Mapped[str] = mapped_column(String(20), default="dispatched")  # dispatched|in_transit|delivered|delayed|exception
    delay_reason: Mapped[str | None] = mapped_column(Text)
    created_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ScmClaim(Base):
    """C3 SCM-level claims — Samsung channel claims and logistics claims."""
    __tablename__ = "scm_claim"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    claim_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    claim_type: Mapped[str] = mapped_column(String(30), nullable=False)  # short_supply|damage|price_diff|scheme|logistics
    entity: Mapped[str] = mapped_column(String(20), nullable=False, default="YEXIS-EL")
    counterparty_ref: Mapped[str | None] = mapped_column(String(100))  # Samsung / logistics partner
    invoice_ref: Mapped[str | None] = mapped_column(String(100))
    amount_claimed: Mapped[float | None] = mapped_column(Float)
    amount_settled: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|submitted|acknowledged|settled|rejected
    raised_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    submitted_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settled_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    evidence_refs_json: Mapped[dict | None] = mapped_column(JSONB)


class Campaign(Base):
    """K1-K6 campaign register — one row per marketing campaign."""
    __tablename__ = "campaign"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    campaign_type: Mapped[str] = mapped_column(String(30), nullable=False)  # dealer_promo|end_customer|scheme_push|event
    entity: Mapped[str] = mapped_column(String(20), nullable=False, default="YEXIS-EL")
    target_skus_json: Mapped[dict | None] = mapped_column(JSONB)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|audience_built|broadcast|tracking|closed
    message_template_id: Mapped[str | None] = mapped_column(String(100))
    budget: Mapped[float | None] = mapped_column(Float)
    created_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    closed_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    kpi_json: Mapped[dict | None] = mapped_column(JSONB)  # K6 analytics snapshot
    # Scheduler: broadcast fires automatically at this UTC timestamp.
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A/B testing: {"variants": [{"name": "A", "template_id": "t1"}, {"name": "B", "template_id": "t2"}]}
    ab_test_json: Mapped[dict | None] = mapped_column(JSONB)
    # Drip: true when CampaignSequenceStep rows define follow-up steps.
    sequence_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    # Default channel for this campaign (overridden per-contact in auto mode).
    default_channel: Mapped[str] = mapped_column(String(20), default="auto")  # auto|whatsapp|email|telegram|voice|push|linkedin
    # Response form customisation: subtitle, field labels/toggles, submit text.
    form_config_json: Mapped[dict | None] = mapped_column(JSONB)
    # ISM Campaign Assistant: an ISM-managed campaign sends nothing until the
    # ISM approves it on the console (accounts, segments, sequence, capacity).
    ism_managed: Mapped[bool] = mapped_column(Boolean, default=False)
    ism_approved_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ism_approved_by: Mapped[str | None] = mapped_column(String(200))
    ism_brief_json: Mapped[dict | None] = mapped_column(JSONB)  # target verticals/regions, pipeline target, window


class CampaignContact(Base):
    """K2-K5 — each dealer/contact enrolled in a campaign."""
    __tablename__ = "campaign_contact"
    __table_args__ = (UniqueConstraint("campaign_id", "contact_ref", name="uq_campaign_contact"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    contact_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    contact_type: Mapped[str] = mapped_column(String(20), default="dealer")  # dealer|customer|lead
    msisdn: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(200))
    telegram_chat_id: Mapped[str | None] = mapped_column(String(50))
    fcm_token: Mapped[str | None] = mapped_column(String(500))
    ab_variant: Mapped[str | None] = mapped_column(String(5))  # A | B
    sequence_step: Mapped[int] = mapped_column(Integer, default=0)  # 0=not started
    last_step_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    name: Mapped[str | None] = mapped_column(String(200))      # display name from Excel / source
    company: Mapped[str | None] = mapped_column(String(300))    # company / account name from Excel / source
    extra_json: Mapped[dict | None] = mapped_column(JSONB)      # all other Excel columns (title, role, seniority, ...) → template vars
    alt_email: Mapped[str | None] = mapped_column(String(200))  # fallback address when primary fails/bounces
    message_sent_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_log_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    response_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_type: Mapped[str | None] = mapped_column(String(30))  # interested|not_interested|callback|converted|no_response
    notes: Mapped[str | None] = mapped_column(Text)


class CampaignSequenceStep(Base):
    """Drip sequence step — defines follow-up messages for a campaign.

    step_number: 1 = first follow-up after initial blast, 2 = second, etc.
    delay_hours: hours after the previous step (or campaign broadcast for step 1).
    condition: when to trigger — always | no_response | interested | callback
    """
    __tablename__ = "campaign_sequence_step"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    delay_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=72)
    template_id: Mapped[str] = mapped_column(String(100), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), default="auto")
    condition: Mapped[str] = mapped_column(String(30), default="no_response")  # always|no_response|interested|callback


class CampaignFormSubmission(Base):
    """Response collected via the per-recipient pre-filled form linked in a campaign email.

    One row per submission. A contact may submit more than once (the latest
    submission always wins when syncing to Zoho CRM).
    """
    __tablename__ = "campaign_form_submission"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    contact_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(50))
    address: Mapped[str | None] = mapped_column(Text)
    requirements: Mapped[str | None] = mapped_column(Text)
    extra_json: Mapped[dict | None] = mapped_column(JSONB)     # any additional fields for future use
    zoho_lead_id: Mapped[str | None] = mapped_column(String(100))
    zoho_sync_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    zoho_error: Mapped[str | None] = mapped_column(Text)


# ── ISM Campaign Assistant (Phase 1) ─────────────────────────────────
# Account-level intelligence and the ISR work queue live here, in the
# platform's own database: the Zoho CRM Free edition (5,000-record cap,
# no custom fields or tags) cannot hold them. When the CRM is upgraded,
# tasks and deals are also pushed to / pulled from Zoho; these tables stay
# the source the agents read.

class AccountProfile(Base):
    """One target account (organisation) with its fit/intent scoring."""
    __tablename__ = "account_profile"
    __table_args__ = (
        Index("ix_account_profile_norm", "norm_name"),
        Index("ix_account_profile_tier", "tier"),
    )

    account_id: Mapped[str] = mapped_column(String(100), primary_key=True)  # Zoho CRM account id (kept record)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    norm_name: Mapped[str] = mapped_column(String(300), nullable=False)
    region: Mapped[str | None] = mapped_column(String(30))          # Tamil Nadu | Kerala | Other
    region_source: Mapped[str | None] = mapped_column(String(30))   # ism | name | crm
    vertical: Mapped[str | None] = mapped_column(String(40))
    vertical_source: Mapped[str | None] = mapped_column(String(30)) # name | ism | crm
    owner_isr: Mapped[str | None] = mapped_column(String(100))
    pilot: Mapped[bool] = mapped_column(Boolean, default=False)
    off_limits: Mapped[bool] = mapped_column(Boolean, default=False)
    website: Mapped[str | None] = mapped_column(String(300))
    phones: Mapped[str | None] = mapped_column(String(300))
    emails: Mapped[str | None] = mapped_column(Text)
    email_on_dead_isp: Mapped[bool] = mapped_column(Boolean, default=False)
    crm_record_count: Mapped[int] = mapped_column(Integer, default=1)
    roles_json: Mapped[dict | None] = mapped_column(JSONB)          # {"Economic Buyer": [contact_ref,...], ...}
    last_order_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_order_value: Mapped[float | None] = mapped_column(Float)
    open_deal_count: Mapped[int] = mapped_column(Integer, default=0)
    open_deal_value: Mapped[float] = mapped_column(Float, default=0.0)
    fit_score: Mapped[int] = mapped_column(Integer, default=0)
    fit_reasons_json: Mapped[list | None] = mapped_column(JSONB)
    intent_score: Mapped[float] = mapped_column(Float, default=0.0)
    intent_updated_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tier: Mapped[str | None] = mapped_column(String(5))             # P1 | P2 | P3
    notes: Mapped[str | None] = mapped_column(Text)
    updated_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class IsrTask(Base):
    """A manual touch or follow-up for an ISR (call, LinkedIn, reply, handoff...)."""
    __tablename__ = "isr_task"
    __table_args__ = (
        Index("ix_isr_task_owner_status", "owner_isr", "status"),
        Index("ix_isr_task_campaign", "campaign_id"),
        Index("ix_isr_task_contact", "contact_ref"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    contact_ref: Mapped[str | None] = mapped_column(String(100))
    account_id: Mapped[str | None] = mapped_column(String(100))
    owner_isr: Mapped[str | None] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20), nullable=False)   # call|linkedin|reply|engagement|handoff|research
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)                  # context + script
    variants_json: Mapped[list | None] = mapped_column(JSONB)       # AI draft variants
    picked_variant: Mapped[int | None] = mapped_column(Integer)
    draft_edited: Mapped[bool | None] = mapped_column(Boolean)
    priority: Mapped[str] = mapped_column(String(10), default="High")
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="open")  # open|done|skipped
    outcome: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(60))           # e.g. drip:step3, reply, engagement
    crm_task_id: Mapped[str | None] = mapped_column(String(40))
    created_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    done_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CampaignDeal(Base):
    """An opportunity attributed to campaigns. Logged here directly while the
    CRM cannot create Deals; synced from Zoho CRM Deals once it can."""
    __tablename__ = "campaign_deal"
    __table_args__ = (Index("ix_campaign_deal_campaign", "campaign_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    crm_deal_id: Mapped[str | None] = mapped_column(String(40), unique=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    account_id: Mapped[str | None] = mapped_column(String(100))
    contact_ref: Mapped[str | None] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    stage: Mapped[str] = mapped_column(String(60), nullable=False)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    probability: Mapped[int | None] = mapped_column(Integer)
    owner_isr: Mapped[str | None] = mapped_column(String(100))
    expected_close: Mapped[date | None] = mapped_column(Date)
    created_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    closed_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attribution_json: Mapped[dict | None] = mapped_column(JSONB)
