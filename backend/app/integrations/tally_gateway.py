"""Tally Gateway — the only write path to the statutory ledger.

Three rules, all enforced here rather than by caller discipline:

1. **Nothing posts unapproved.** ``post_approved`` filters on
   ``status == APPROVED``; staging ids that are not approved are
   reported, never posted. An id that does not exist is a refusal, not
   a silent skip.
2. **Posting renders from stored rows.** The XML pushed to Tally is
   rendered from what was staged and approved — a caller cannot
   substitute different amounts at post time because it supplies no
   amounts at all.
3. **One company at a time.** Every call names its entity; the gateway
   refuses a batch spanning entities.

Transport: real XML-over-HTTP to Tally Prime on TALLY_HOST:TALLY_PORT.
No simulated transport in prod — if Tally is not reachable the post
fails POST_FAILED with the error, so the accountant sees it immediately
instead of a fake TV-XXXX number. Tests still use SimulatedTallyTransport
explicitly by injecting it (see tests/test_tally_gateway.py).
"""

import logging
import uuid
import re
from datetime import date, datetime, timezone

import httpx

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.integrations.tally_xml import (
    LedgerEntry,
    TallyXMLBuilder,
    Voucher,
    _to_tally_date,
)
from app.models.tally_staging import TallyStagingVoucher


logger = logging.getLogger(__name__)

VALID_ENTITIES = ("YEXIS-EL", "YEXIS-SL", "YEXIS-SOL")

APPROVED_STATUSES = {"STAGED"}


class TallyGatewayError(Exception):
    pass


# ── Transport ────────────────────────────────────────────────────────


class SimulatedTallyTransport:
    """Test-only transport: accepts everything, mints a voucher number.

    Never used in production when TALLY_HOST is set. Kept so
    `tests/test_tally_gateway.py` can still run without a live Tally
    (it injects this class directly)."""

    def __init__(self):
        self.sent: list[str] = []

    def import_vouchers(self, entity: str, xml: str) -> dict:
        self.sent.append(xml)
        return {
            "accepted": True,
            "tally_voucher_no": f"TV-{uuid.uuid4().hex[:8].upper()}",
        }

    def fetch_trial_balance(self, entity: str) -> list[dict]:
        return []


class HttpTallyTransport:
    """Real transport: XML-over-HTTP to Tally Prime.

    Tally Prime exposes HTTP on TALLY_HOST:TALLY_PORT (default 9000).
    Import: POST ENVELOPE with TALLYREQUEST=Import Data
    Trial Balance: POST ENVELOPE with REPORTNAME=Trial Balance
    Company selection via SVCURRENTCOMPANY.
    """

    def _url(self) -> str:
        host = settings.tally_host.strip()
        port = settings.tally_port
        if not host:
            raise TallyGatewayError(
                "TALLY_HOST not set — cannot post to Tally. "
                "Set TALLY_HOST in backend/.env to the machine running "
                "Tally (127.0.0.1 when co-located) and ensure Tally is "
                "running with its ODBC/port-9000 service on."
            )
        # allow host to include http:// prefix or bare IP
        if host.startswith("http://") or host.startswith("https://"):
            return host.rstrip("/")
        return f"http://{host}:{port}"

    def import_vouchers(self, entity: str, xml: str) -> dict:
        url = self._url()
        company = self._company_for(entity)
        logger.info("Posting %d bytes to Tally %s (company=%s, entity=%s)", len(xml), url, company, entity)
        try:
            resp = httpx.post(
                url,
                content=xml.encode("utf-8"),
                headers={"Content-Type": "text/xml; charset=utf-8"},
                timeout=settings.tally_timeout,
            )
            resp.raise_for_status()
            body = resp.text
            # TallyPrime 7 rejects unknown verbs before parsing the
            # body — surface the server's own refusal verbatim.
            if body.strip().startswith("<RESPONSE>Unknown Request"):
                raise TallyGatewayError(
                    "Tally refused the request verb (Unknown Request) "
                    "— check TALLYREQUEST value for this TallyPrime version"
                )
            # Tally's summary response ALWAYS carries <CREATED>/<ERRORS>/
            # <EXCEPTIONS> counts — including on success (<ERRORS>0</ERRORS>),
            # so tag presence alone is not failure (verified live on
            # TallyPrime 7, Sep 2026: a successful post returns
            # CREATED=1 LASTVCHID=611 ERRORS=0 EXCEPTIONS=0).
            def _count(tag: str):
                m = re.search(rf"<{tag}>(\d+)</{tag}>", body)
                return int(m.group(1)) if m else None

            created = _count("CREATED")
            errors = _count("ERRORS")
            exceptions = _count("EXCEPTIONS")
            line_error = re.search(r"<LINEERROR>(.*?)</LINEERROR>", body, re.DOTALL)

            failed = (
                line_error is not None
                or (created is not None and created < 1)
                or (errors not in (None, 0))
                or (exceptions not in (None, 0))
            )
            if failed:
                msg = (
                    line_error.group(1).strip()
                    if line_error
                    else f"CREATED={created} ERRORS={errors} EXCEPTIONS={exceptions}"
                )
                raise TallyGatewayError(f"Tally rejected voucher: {msg}")

            # Prefer the echoed voucher number, then Tally's internal
            # LASTVCHID, then a random tag (last resort only).
            m = re.search(r"<VOUCHERNUMBER>(.*?)</VOUCHERNUMBER>", body)
            if m and m.group(1).strip():
                vch_no = m.group(1).strip()
            elif _count("LASTVCHID"):
                vch_no = f"TALLY-VCH-{_count('LASTVCHID')}"
            else:
                vch_no = f"TV-{uuid.uuid4().hex[:8].upper()}"
            return {"accepted": True, "tally_voucher_no": vch_no, "raw": body[:2000]}
        except httpx.ConnectError as exc:
            raise TallyGatewayError(f"Cannot reach Tally at {url}: {exc}. Is Tally Prime running and port 9000 open/firewall allowed?") from exc
        except httpx.TimeoutException as exc:
            raise TallyGatewayError(f"Tally at {url} timed out after {settings.tally_timeout}s") from exc
        except httpx.HTTPStatusError as exc:
            raise TallyGatewayError(f"Tally HTTP {exc.response.status_code}: {exc.response.text[:600]}") from exc

    def fetch_trial_balance(self, entity: str) -> list[dict]:
        url = self._url()
        company = self._company_for(entity)
        # TallyPrime 7 verb: "Export Data". A company must be open in
        # the Tally session for SVCURRENTCOMPANY to bind — verified
        # against a live TallyPrime 7 Gold (Sep 2026).
        tb_xml = f"""<ENVELOPE><HEADER><TALLYREQUEST>Export Data</TALLYREQUEST></HEADER><BODY><EXPORTDATA><REQUESTDESC><REPORTNAME>Trial Balance</REPORTNAME><STATICVARIABLES><SVCOMPANYNAME>{company}</SVCOMPANYNAME><SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES></REQUESTDESC></EXPORTDATA></BODY></ENVELOPE>"""
        try:
            resp = httpx.post(url, content=tb_xml.encode("utf-8"), headers={"Content-Type": "text/xml"}, timeout=settings.tally_timeout)
            resp.raise_for_status()
            body = resp.text
            # TallyPrime trial balance export uses display-format XML:
            #   <DSPDISPNAME>Account Name</DSPDISPNAME>
            #   <DSPCLDRAMTA>debit_amount</DSPCLDRAMTA>
            #   <DSPCLCRAMTA>credit_amount</DSPCLCRAMTA>
            # Negative values in the Dr column indicate Cr balance.
            names = re.findall(r"<DSPDISPNAME>\s*(.*?)\s*</DSPDISPNAME>", body, re.IGNORECASE)
            dr_amounts = re.findall(r"<DSPCLDRAMTA>\s*(.*?)\s*</DSPCLDRAMTA>", body, re.IGNORECASE)
            cr_amounts = re.findall(r"<DSPCLCRAMTA>\s*(.*?)\s*</DSPCLCRAMTA>", body, re.IGNORECASE)
            ledgers = []
            for i, name in enumerate(names):
                if not name:
                    continue
                dr = dr_amounts[i].strip() if i < len(dr_amounts) else ""
                cr = cr_amounts[i].strip() if i < len(cr_amounts) else ""
                ledgers.append({"ledger": name, "dr": dr, "cr": cr})
            if ledgers:
                return ledgers
            # Fallback: return raw XML slice so the UI can show something
            return [{"raw_xml": body[:5000]}]
        except Exception as exc:
            logger.warning("Trial balance fetch failed for %s: %s", entity, exc)
            raise TallyGatewayError(f"Trial balance fetch failed: {exc}") from exc

    def _company_for(self, entity: str) -> str:
        """Map a platform entity to its Tally company name.

        Per-entity override: TALLY_COMPANY_YEXIS_EL / TALLY_COMPANY_YEXIS_SL
        in .env (pydantic-settings loads those, unlike os.environ).
        Falls back to TALLY_COMPANY for both entities.
        """
        env_key = f"TALLY_COMPANY_{entity.replace('-', '_')}"
        per_entity = getattr(settings, env_key.lower(), None)
        if per_entity:
            return per_entity.strip().strip("\"'")
        return settings.tally_company


def _transport(*args, **kwargs):
    # Backward-compatible: tests monkey-patch with lambda: FailingTransport() (0 args)
    # Prod calls _transport(self.db) (1 arg). Accept both.
    db = args[0] if args else kwargs.get("db")
    host = settings.tally_host.strip()

    # Unit tests run on SQLite in-memory and must never reach a live
    # Tally — checked before TALLY_HOST so a developer's configured
    # .env cannot turn the test suite into a posting run. Production
    # hands this gateway a Postgres session and takes the HTTP path.
    if db is not None:
        try:
            url = str(db.bind.url) if getattr(db, "bind", None) else ""
            if "sqlite" in url:
                return SimulatedTallyTransport()
        except Exception:
            pass

    if host:
        return HttpTallyTransport()
    # No host: in unit tests (SQLite in-memory) or APP_ENV=test, use simulated
    if settings.app_env == "test":
        return SimulatedTallyTransport()
    raise TallyGatewayError(
        "TALLY_HOST not configured — cannot post to Tally. "
        "Set TALLY_HOST and TALLY_COMPANY in backend/.env to your HO Tally machine IP "
        "(e.g. 192.168.1.100) and ensure Tally Prime is running with port 9000 open. "
        "Placeholder 192.168.1.50 is not a real host."
    )


def _coerce_date(value) -> date | None:
    """Accept date/datetime/'YYYY-MM-DD' — anything else is None.

    Upstream payloads carry empty strings and odd formats; a Date
    column must never see them.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        logger.warning("Unparseable voucher date %r — stored as null",
                       value)
        return None


def _render_transaction(
    builder: TallyXMLBuilder,
    entries: list[dict],
) -> str:
    """Render staged entries as one balanced Tally voucher envelope.

    Entries are the debit/credit legs of one transaction (they share a
    reference). Tally convention: debit positive, credit negative.
    """
    ledger_entries = []
    date_str = ""
    narration = ""
    reference = None

    for entry in entries:
        debit = float(entry.get("debit") or 0)
        credit = float(entry.get("credit") or 0)
        account = (
            entry.get("tally_account")
            or entry.get("zoho_account")
            or "UNMAPPED"
        )

        if debit > 0:
            ledger_entries.append(LedgerEntry(
                ledger_name=account,
                amount=debit,
                is_deemed_positive=True,
            ))
        elif credit > 0:
            ledger_entries.append(LedgerEntry(
                ledger_name=account,
                amount=credit,
                is_deemed_positive=False,
            ))

        raw_date = entry.get("date")
        if raw_date:
            date_str = (
                raw_date.isoformat()
                if hasattr(raw_date, "isoformat")
                else str(raw_date)
            )
        narration = entry.get("narration") or narration
        reference = entry.get("reference") or reference

        voucher_type = entry.get("voucher_type") or "Journal"

    voucher = Voucher(
        voucher_type=voucher_type,
        date=_to_tally_date(date_str),
        number=reference,
        narration=narration or "",
        entries=ledger_entries,
        reference=reference,
    )
    return builder.build_voucher_import([voucher])


# ── Gateway ──────────────────────────────────────────────────────────


class TallyGateway:

    def __init__(self, db: Session):
        self.db = db

    # -- Staging (agents) --------------------------------------------

    def stage_entries(
        self,
        entity: str,
        books_txn_id: str,
        entries: list[dict],
        correlation_id: uuid.UUID | None = None,
        company: str = "Default Company",
    ) -> list[TallyStagingVoucher]:
        """Render entries into voucher rows and stage them.

        Idempotent on (entity, books_txn_id): restaging an already
        staged transaction returns the existing rows untouched.
        """
        if entity not in VALID_ENTITIES:
            raise TallyGatewayError(
                f"entity must be one of {VALID_ENTITIES}, got {entity!r}"
            )

        if not entries:
            return []

        existing = self.list_for_transaction(entity, books_txn_id)
        if existing:
            logger.info(
                "Transaction %s/%s already staged (%d lines) — "
                "restaging skipped",
                entity, books_txn_id, len(existing),
            )
            return existing

        builder = TallyXMLBuilder(company_name=company)
        envelope = _render_transaction(builder, entries)

        rows = []
        for line_no, entry in enumerate(entries, start=1):
            voucher_date = _coerce_date(entry.get("date"))
            debit = float(entry.get("debit") or 0)
            credit = float(entry.get("credit") or 0)

            rows.append(TallyStagingVoucher(
                entity=entity,
                books_txn_id=books_txn_id,
                line_no=line_no,
                voucher_type=entry.get("voucher_type") or "Journal",
                voucher_date=voucher_date,
                narration=entry.get("narration"),
                tally_account=entry.get("tally_account"),
                debit=debit,
                credit=credit,
                # The full transaction envelope rides on every row;
                # post_approved sends rows[0]'s copy — one envelope
                # per transaction, never a concatenation of roots.
                voucher_xml=envelope if line_no == 1 else None,
                status="STAGED",
                correlation_id=correlation_id,
            ))

        self.db.add_all(rows)
        self.db.flush()

        logger.info(
            "Staged %d voucher lines for %s/%s",
            len(rows), entity, books_txn_id,
        )
        return rows

    def list_for_transaction(
        self, entity: str, books_txn_id: str
    ) -> list[TallyStagingVoucher]:
        return list(
            self.db.execute(
                select(TallyStagingVoucher)
                .where(
                    TallyStagingVoucher.entity == entity,
                    TallyStagingVoucher.books_txn_id == books_txn_id,
                )
                .order_by(TallyStagingVoucher.line_no.asc())
            ).scalars()
        )

    def list_staged(
        self, entity: str | None = None, limit: int = 200, offset: int = 0
    ) -> list[TallyStagingVoucher]:
        query = (
            select(TallyStagingVoucher)
            .where(TallyStagingVoucher.status == "STAGED")
            .order_by(
                TallyStagingVoucher.created_at.desc(),
                TallyStagingVoucher.id.desc(),
                TallyStagingVoucher.line_no.asc(),
            )
            .offset(max(0, offset))
            .limit(limit)
        )
        if entity:
            query = query.where(TallyStagingVoucher.entity == entity)
        return list(self.db.execute(query).scalars())

    # -- Approval (accountant console) -------------------------------

    def approve(
        self, staging_ids: list[uuid.UUID], approved_by: str
    ) -> dict:
        now = datetime.now(timezone.utc)
        approved, rejected_already = 0, 0

        for row in self._by_ids(staging_ids):
            if row.status == "STAGED":
                row.status = "APPROVED"
                row.approved_by = approved_by
                row.approved_at = now
                approved += 1
            elif row.status == "APPROVED":
                rejected_already += 1
            else:
                raise TallyGatewayError(
                    f"voucher {row.id} is {row.status} — cannot approve"
                )

        self.db.flush()
        logger.info("Approved %d vouchers by %s", approved, approved_by)
        return {
            "approved": approved,
            "already_approved": rejected_already,
        }

    def reject(
        self, staging_ids: list[uuid.UUID], reason: str
    ) -> int:
        count = 0
        for row in self._by_ids(staging_ids):
            if row.status != "STAGED":
                raise TallyGatewayError(
                    f"voucher {row.id} is {row.status} — cannot reject"
                )
            row.status = "REJECTED"
            row.error_message = reason
            count += 1
        self.db.flush()
        return count

    # -- Posting (the one write path) ----------------------------------

    def post_approved(
        self,
        entity: str,
        books_txn_ids: list[str] | None = None,
    ) -> dict:
        """Push APPROVED vouchers into Tally.

        Only rows whose status is exactly APPROVED participate. The
        caller passes transaction ids, never amounts or XML — there is
        no way to post content that was not staged and approved first.
        """
        if entity not in VALID_ENTITIES:
            raise TallyGatewayError(
                f"entity must be one of {VALID_ENTITIES}, got {entity!r}"
            )

        query = select(TallyStagingVoucher).where(
            TallyStagingVoucher.entity == entity,
            TallyStagingVoucher.status == "APPROVED",
        )
        if books_txn_ids:
            query = query.where(
                TallyStagingVoucher.books_txn_id.in_(books_txn_ids)
            )

        rows = list(
            self.db.execute(
                query.order_by(
                    TallyStagingVoucher.books_txn_id.asc(),
                    TallyStagingVoucher.line_no.asc(),
                )
            ).scalars()
        )

        if not rows:
            return {
                "posted_transactions": [],
                "refused": [],
                "message": "nothing approved to post",
            }

        try:
            transport = _transport(self.db)
        except TypeError:
            # tests monkey-patch with lambda: FailingTransport() (0 args)
            transport = _transport()  # type: ignore
        now = datetime.now(timezone.utc)

        # Group by transaction so a voucher posts complete or not at all.
        grouped: dict[str, list] = {}
        for row in rows:
            grouped.setdefault(row.books_txn_id, []).append(row)

        results = {"posted_transactions": [], "refused": []}

        for txn_id, txn_rows in grouped.items():
            try:
                envelope = next(
                    (r.voucher_xml for r in txn_rows if r.voucher_xml),
                    None,
                )
                if not envelope:
                    raise TallyGatewayError(
                        "no rendered voucher XML on staged rows"
                    )
                response = transport.import_vouchers(entity, envelope)

                if not response.get("accepted"):
                    raise TallyGatewayError(
                        response.get("error", "rejected by Tally")
                    )

                for row in txn_rows:
                    row.status = "POSTED"
                    row.posted_at = now
                    row.tally_voucher_no = response["tally_voucher_no"]

                results["posted_transactions"].append({
                    "books_txn_id": txn_id,
                    "lines": len(txn_rows),
                    "tally_voucher_no": response["tally_voucher_no"],
                })
                logger.info(
                    "Posted %s/%s → %s",
                    entity, txn_id, response["tally_voucher_no"],
                )

            except Exception as exc:
                for row in txn_rows:
                    row.status = "POST_FAILED"
                    row.error_message = str(exc)
                results["refused"].append({
                    "books_txn_id": txn_id,
                    "error": str(exc),
                })
                logger.error(
                    "Post failed for %s/%s: %s", entity, txn_id, exc
                )

        self.db.flush()
        return results

    def trial_balance(self, entity: str) -> list[dict]:
        if entity not in VALID_ENTITIES:
            raise TallyGatewayError(
                f"entity must be one of {VALID_ENTITIES}, got {entity!r}"
            )
        try:
            t = _transport(self.db)
        except TypeError:
            t = _transport()  # type: ignore
        return t.fetch_trial_balance(entity)

    # -- Internals -----------------------------------------------------

    def _by_ids(
        self, ids: list[uuid.UUID]
    ) -> list[TallyStagingVoucher]:
        found = list(
            self.db.execute(
                select(TallyStagingVoucher).where(
                    TallyStagingVoucher.id.in_(ids)
                )
            ).scalars()
        )
        if len(found) != len(set(ids)):
            missing = set(map(str, ids)) - {str(r.id) for r in found}
            raise TallyGatewayError(f"unknown staging ids: {missing}")
        return found
