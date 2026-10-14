"""P2 — Sourcing / RFQ.

Runs RFQs for non-catalogue and project-BOM items: issues to approved
vendors, collects and normalises their quotes, and builds the ranked
comparison the buyer awards from. The spec is explicit that the buyer
selects the vendor — P2 recommends, a human awards, and only then does
the award travel to P1 for the purchase order.

Modes (explicit ``mode`` field, else inferred from the payload):

    issue    — create the RFQ document for the named approved vendors
    collect  — file vendor quotes against an issued RFQ
    compare  — normalise, rank, and recommend an award (human gate)

Like P1, the agent itself does not write business documents: it
returns the outcome and the executor's recorder persists it, so the
document and the task status land in one transaction and a retried
task cannot issue a second RFQ. Reading is different — collect and
compare load the stored RFQ by number, because a stale payload copy
must never contradict what a vendor actually offered.

Ranking includes only quotes that normalised cleanly — a quote that
failed to parse is shown to the buyer but never recommended.
"""

import uuid
from datetime import datetime, timezone

from app.agents.base import BaseAgent, AgentResult
from app.contracts.documents import (
    QuoteLine,
    QuoteSubmission,
    to_decimal,
)
from app.services.documents import document_session


class P2SourcingRfq(BaseAgent):
    """Sourcing / RFQ Agent — P2

    issue → collect → compare → (buyer award) → hand to P1 for PO.
    """

    agent_code = "P2"
    agent_name = "Sourcing RFQ"

    # Modes the agent understands.
    MODES = ("issue", "collect", "compare")

    def __init__(self):
        super().__init__()

    def describe(self) -> str:
        return (
            "Sourcing and RFQ: issues RFQs to approved vendors, "
            "collects and normalises quotes, and recommends an award "
            "for the buyer to approve before P1 raises the PO."
        )

    # ── Validation (structural only — never touches the DB) ─────────

    def validate(self, payload: dict) -> str | None:
        if not payload:
            return "Payload is empty"

        mode = payload.get("mode") or self._infer_mode(payload)

        if mode not in self.MODES:
            return (
                f"Unknown mode '{mode}' — expected one of "
                f"{', '.join(self.MODES)}"
            )

        if mode == "issue":
            return self._validate_issue(payload)

        if mode == "collect":
            return self._validate_collect(payload)

        return self._validate_compare(payload)

    @staticmethod
    def _validate_issue(payload: dict) -> str | None:
        items = payload.get("items")

        if not items or not isinstance(items, list):
            return "Payload must contain 'items' as a non-empty list"

        for i, item in enumerate(items):
            if not item.get("item"):
                return f"Item {i}: 'item' name is required"
            if item.get("quantity") is None or item["quantity"] <= 0:
                return f"Item {i}: 'quantity' must be > 0"

        vendors = payload.get("vendors")

        if not vendors or not isinstance(vendors, list):
            return (
                "Payload must contain 'vendors' — the approved vendors "
                "to quote, named explicitly until vendor-master "
                "resolution ships"
            )

        if any(not str(v).strip() for v in vendors):
            return "Vendors must be non-empty names"

        return None

    @staticmethod
    def _validate_collect(payload: dict) -> str | None:
        if not payload.get("rfq_number"):
            return "'rfq_number' is required"

        quotes = payload.get("quotes")

        if not quotes or not isinstance(quotes, list):
            return "Payload must contain 'quotes' as a non-empty list"

        for i, quote in enumerate(quotes):
            if not quote.get("vendor"):
                return f"Quote {i}: 'vendor' is required"

            total = quote.get("total_amount")

            # A missing price is not refused here: it files as
            # price_missing and the buyer sees it excluded from the
            # ranking. A negative price is an error, not a missing one.
            if total is not None and to_decimal(total) < 0:
                return f"Quote {i}: 'total_amount' must be >= 0"

        return None

    @staticmethod
    def _validate_compare(payload: dict) -> str | None:
        has_rfq = bool(payload.get("rfq_number"))
        has_quotes = bool(payload.get("quotes"))

        if not has_rfq and not has_quotes:
            return (
                "Compare needs 'rfq_number' (quotes on file) or "
                "inline 'quotes' to compare"
            )

        return None

    @staticmethod
    def _infer_mode(payload: dict) -> str:
        if payload.get("quotes") and payload.get("rfq_number"):
            return "collect"

        if payload.get("rfq_number"):
            return "compare"

        return "issue"

    # ── Execution ───────────────────────────────────────────────────

    def execute(
        self,
        payload: dict,
        task_id: uuid.UUID | None = None,
        requested_by: str | None = None,
    ) -> AgentResult:
        mode = payload.get("mode") or self._infer_mode(payload)

        if mode == "issue":
            return self._issue(payload, requested_by)

        if mode == "collect":
            return self._collect(payload)

        return self._compare(payload, task_id)

    # ── issue ───────────────────────────────────────────────────────

    def _issue(
        self,
        payload: dict,
        requested_by: str | None,
    ) -> AgentResult:
        now = datetime.now(timezone.utc)
        vendors = [str(v).strip() for v in payload["vendors"]]
        items = payload["items"]

        rfq_number = self._generate_rfq_number(now)

        self.logger.info(
            "P2: issued RFQ %s to %d vendor(s)",
            rfq_number, len(vendors),
        )

        return AgentResult(
            status="SUCCESS",
            output={
                "mode": "issue",
                "rfq_number": rfq_number,
                "status": "ISSUED",
                "vendors": vendors,
                "items": items,
                "currency": payload.get("currency", "INR"),
                "need_by": payload.get("need_by"),
                "requested_by": requested_by,
                "created_at": now.isoformat(),
                "summary": {
                    "rfq_number": rfq_number,
                    "vendors": len(vendors),
                    "lines": len(items),
                },
            },
            actions_taken=[
                f"identified_vendors:{len(vendors)}",
                f"issued_rfq:{rfq_number}",
            ],
        )

    # ── collect ─────────────────────────────────────────────────────

    def _collect(self, payload: dict) -> AgentResult:
        rfq_number = payload["rfq_number"]
        quotes = payload["quotes"]

        # The RFQ must already exist — quotes are filed against a
        # document that was issued, never invented alongside.
        with document_session() as db:
            from app.repository.rfq_repository import RfqRepository

            rfq_row = RfqRepository(db).get_row(rfq_number)

        if rfq_row is None:
            return AgentResult(
                status="VALIDATION_ERROR",
                error=(
                    f"RFQ {rfq_number} not found — quotes cannot be "
                    "filed against a document that was never issued"
                ),
            )

        normalised = 0

        recorded_quotes = []

        for quote in quotes:
            parse_status = self._parse_status(quote)

            if parse_status == "normalised":
                normalised += 1

            recorded_quotes.append({
                "vendor": str(quote["vendor"]).strip(),
                "total_amount": (
                    str(to_decimal(quote["total_amount"]))
                    if quote.get("total_amount") is not None
                    else None
                ),
                "currency": quote.get(
                    "currency", payload.get("currency", "INR")
                ),
                "lead_time_days": quote.get("lead_time_days"),
                "lines": quote.get("lines") or [],
                "notes": quote.get("notes"),
                "parse_status": parse_status,
            })

        self.logger.info(
            "P2: collected %d quote(s) for RFQ %s (%d normalised)",
            len(quotes), rfq_number, normalised,
        )

        return AgentResult(
            status="SUCCESS",
            output={
                "mode": "collect",
                "rfq_number": rfq_number,
                "status": "QUOTES_PENDING",
                "quotes_collected": len(quotes),
                "quotes_normalised": normalised,
                "quotes_unparseable": len(quotes) - normalised,
                # The recorder persists these; kept in the output so
                # the comparison pack can be rebuilt from the task
                # result alone.
                "quotes": recorded_quotes,
                "summary": {
                    "rfq_number": rfq_number,
                    "quotes_collected": len(quotes),
                    "quotes_normalised": normalised,
                },
            },
            actions_taken=[
                f"collected_quote:{quote['vendor']}"
                for quote in quotes
            ],
        )

    # ── compare ─────────────────────────────────────────────────────

    def _compare(
        self,
        payload: dict,
        task_id: uuid.UUID | None,
    ) -> AgentResult:
        now = datetime.now(timezone.utc)

        rfq_number = payload.get("rfq_number")

        quotes, load_error = self._load_quotes(payload, rfq_number)

        if load_error is not None:
            return AgentResult(
                status="VALIDATION_ERROR",
                error=load_error,
            )

        # ── Normalise ────────────────────────────────────────────────
        submissions = [
            self._normalise(quote) for quote in quotes
        ]

        comparable = [
            quote
            for quote in submissions
            if quote.parse_status == "normalised"
        ]

        actions = [
            f"loaded_quotes:{len(submissions)}",
            f"normalised_quotes:{len(comparable)}",
        ]

        if not comparable:
            return AgentResult(
                status="VALIDATION_ERROR",
                error=(
                    "No comparable quotes — every quote failed to "
                    "normalise or lacks a price; nothing to rank"
                ),
            )

        currencies = {quote.currency for quote in comparable}

        if len(currencies) > 1:
            return AgentResult(
                status="VALIDATION_ERROR",
                error=(
                    "Quotes are in different currencies "
                    f"({', '.join(sorted(currencies))}) and cannot be "
                    "ranked without an FX rate — escalating to a human"
                ),
            )

        # ── Rank ─────────────────────────────────────────────────────
        ranked = sorted(
            comparable, key=lambda quote: quote.total_amount
        )

        recommended = ranked[0]

        comparison = [
            {
                "rank": position + 1,
                "vendor": quote.vendor,
                "total_amount": float(quote.total_amount),
                "currency": quote.currency,
                "lead_time_days": quote.lead_time_days,
                "parse_status": quote.parse_status,
            }
            for position, quote in enumerate(ranked)
        ]

        # Quotes that could not be compared still reach the buyer.
        excluded = [
            {
                "vendor": quote.vendor,
                "parse_status": quote.parse_status,
            }
            for quote in submissions
            if quote.parse_status != "normalised"
        ]

        actions.append(f"recommended_award:{recommended.vendor}")

        warning = None

        if len(ranked) == 1:
            warning = (
                "Only one comparable quote — awarding without "
                "competition"
            )

        award_items = (
            [
                {
                    "item": line.item,
                    "quantity": float(line.quantity),
                    "unit_price": float(line.unit_price),
                }
                for line in recommended.lines
            ]
            if recommended.lines
            else self._requested_items(payload)
        )

        award_payload = {
            "vendor": recommended.vendor,
            "items": award_items,
            "currency": recommended.currency,
            "rfq_number": rfq_number,
        }

        summary = {
            "rfq_number": rfq_number,
            "quotes_compared": len(ranked),
            "quotes_excluded": len(excluded),
            "recommended_vendor": recommended.vendor,
            "recommended_total": float(recommended.total_amount),
        }

        # ── Award gate: the buyer decides ────────────────────────────
        # The spec is explicit: P2 recommends (A1); the human awards.
        if not self._approval_granted(task_id):
            self.logger.info(
                "P2: recommending %s at %s %s for RFQ %s — awaiting "
                "buyer award",
                recommended.vendor,
                recommended.currency,
                recommended.total_amount,
                rfq_number or "(inline quotes)",
            )

            return AgentResult(
                status="NEEDS_APPROVAL",
                output={
                    "mode": "compare",
                    "approval_type": "AWARD_RECOMMENDATION",
                    "approval_payload": {
                        **award_payload,
                        "recommended_total": float(
                            recommended.total_amount
                        ),
                        "quotes_compared": len(ranked),
                    },
                    "rfq_number": rfq_number,
                    "comparison": comparison,
                    "excluded_quotes": excluded,
                    "recommended_vendor": recommended.vendor,
                    "recommended_total": float(
                        recommended.total_amount
                    ),
                    "reason": (
                        f"Recommended {recommended.vendor} at "
                        f"{recommended.currency} "
                        f"{recommended.total_amount} from "
                        f"{len(ranked)} comparable quote(s). Buyer "
                        "must select the vendor."
                    ),
                    "warning": warning,
                    "summary": summary,
                    "action_required": (
                        "Buyer must approve the award before a "
                        "purchase order is raised."
                    ),
                },
                actions_taken=actions + ["awaiting_buyer_award"],
            )

        # ── Approved: record the award, then hand off to P1 ──────────
        awarded = self._record_award(
            rfq_number, recommended.vendor
        )

        if awarded:
            actions.append(
                f"award_recorded:{recommended.vendor}"
            )

        output = {
            "mode": "compare",
            "rfq_number": rfq_number,
            "status": "AWARDED" if awarded else "COMPLETED",
            "awarded_vendor": recommended.vendor,
            "awarded_total": float(recommended.total_amount),
            "currency": recommended.currency,
            "comparison": comparison,
            "excluded_quotes": excluded,
            "award_payload": award_payload,
            "warning": warning,
            "summary": summary,
            "next_step": (
                "Raise the purchase order via P1 using award_payload"
            ),
            "compared_at": now.isoformat(),
        }

        self.logger.info(
            "P2: award recorded — %s at %s %s for RFQ %s",
            recommended.vendor,
            recommended.currency,
            recommended.total_amount,
            rfq_number,
        )

        return AgentResult(
            status="SUCCESS",
            output=output,
            actions_taken=actions,
        )

    # ── Private helpers ──────────────────────────────────────────────

    def _load_quotes(
        self,
        payload: dict,
        rfq_number: str | None,
    ) -> tuple[list[dict], str | None]:
        """Quotes to compare — inline, or loaded from the stored RFQ.

        Returns ``(quotes, error)``. Inline quotes are used as given;
        stored quotes come from the RFQ document so a stale payload can
        never contradict what the vendors actually offered.
        """
        inline = payload.get("quotes")

        if inline:
            return inline, None

        with document_session() as db:
            from app.repository.rfq_repository import RfqRepository

            document = RfqRepository(db).get_by_number(rfq_number)

        if document is None:
            return None, (
                f"RFQ {rfq_number} not found — nothing to compare"
            )

        stored = [
            {
                "vendor": quote.vendor,
                "total_amount": quote.total_amount,
                "currency": quote.currency,
                "lead_time_days": quote.lead_time_days,
                "lines": [line.model_dump() for line in quote.lines],
                "parse_status": quote.parse_status,
                "notes": quote.notes,
            }
            for quote in document.quotes
        ]

        if not stored:
            return None, (
                f"RFQ {rfq_number} has no quotes on file yet — "
                "collect quotes before comparing"
            )

        return stored, None

    @staticmethod
    def _parse_status(quote: dict) -> str:
        """Whether this quote can take part in a ranking.

        A price is the minimum bar. Line detail is optional — a
        single-total quote is still comparable on its amount.
        """
        total = quote.get("total_amount")

        if total is None or to_decimal(total) < 0:
            return "price_missing"

        return "normalised"

    @staticmethod
    def _normalise(quote: dict) -> QuoteSubmission:
        """One raw quote into the comparable shape."""
        parse_status = P2SourcingRfq._parse_status(quote)

        lines = [
            QuoteLine(
                item=line.get("item", ""),
                quantity=to_decimal(line.get("quantity")),
                unit_price=to_decimal(line.get("unit_price")),
            )
            for line in (quote.get("lines") or [])
        ]

        return QuoteSubmission(
            vendor=str(quote.get("vendor", "")).strip(),
            total_amount=to_decimal(quote.get("total_amount")),
            currency=quote.get("currency", "INR"),
            lead_time_days=quote.get("lead_time_days"),
            lines=lines,
            parse_status=parse_status,
            notes=quote.get("notes"),
        )

    @staticmethod
    def _requested_items(payload: dict) -> list[dict]:
        """The RFQ's requested lines, for the award hand-off."""
        return [
            {
                "item": item.get("item", ""),
                "quantity": item.get("quantity"),
                "unit_price": item.get("unit_price"),
            }
            for item in (payload.get("items") or [])
        ]

    def _record_award(
        self,
        rfq_number: str | None,
        vendor: str,
    ) -> bool:
        """Persist the buyer's award, when there is an RFQ to award.

        Inline comparisons (no rfq_number) are reports, not documents —
        there is nothing to mark. An award on a stored RFQ needs the
        row; a missing row is logged, not invented.
        """
        if not rfq_number:
            return False

        with document_session() as db:
            from app.repository.rfq_repository import RfqRepository

            awarded = RfqRepository(db).set_awarded_vendor(
                rfq_number, vendor
            )

            if awarded is None:
                return False

            db.commit()

        return True

    def _generate_rfq_number(self, now: datetime) -> str:
        """Next RFQ number from the database sequence.

        Same rule as PO and GRN numbers: a Postgres sequence, so
        numbers survive restarts and never collide between workers.
        """
        from app.services.numbering import (
            RFQ_SEQUENCE,
            next_document_number,
        )

        return next_document_number("RFQ", RFQ_SEQUENCE, now)
