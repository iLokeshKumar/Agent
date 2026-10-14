"""Business document contracts exchanged between agents.

An agent must never read another agent's raw ``task.result`` JSON: a
field renamed on one side silently breaks the other, and there is no
schema to point at when it does. Each document below is the stable,
versioned shape that one agent publishes and the next consumes.

Documents travel **by reference**, not by value. A task payload carries
the document number (``po_number``, ``grn_number``, ``invoice_number``);
the consuming agent loads the document from the database. That way there
is exactly one source of truth for what was ordered, received and
billed, and a stale copy in a task payload can never contradict it.
"""

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer


# Bump when a field changes meaning or is removed. Persisted with every
# document so an old row can still be read after the shape moves on.
CONTRACT_VERSION = 1


def normalise_item(name: str) -> str:
    """Key used to match a line across documents.

    "Laptop", "laptop " and "LAPTOP" are the same item on a PO, a GRN
    and an invoice. Matching on the raw string makes a three-way match
    fail for a reason that has nothing to do with the goods.
    """
    return " ".join(name.split()).casefold()


def to_decimal(value) -> Decimal:
    """Coerce a JSON number to an exact Decimal.

    Via ``str``, because ``Decimal(75000.1)`` keeps the float's binary
    error and money comparisons then fail by a fraction of a paisa.
    """
    if isinstance(value, Decimal):
        return value

    if value is None:
        return Decimal("0")

    return Decimal(str(value))



# Money stays a Decimal in Python so comparisons and sums are exact,
# but serialises as a JSON number to match the documented shape.
Money = Annotated[
    Decimal,
    PlainSerializer(float, return_type=float, when_used="json"),
]


class _Contract(BaseModel):
    """Shared behaviour: immutable, versioned, JSON-safe."""

    model_config = ConfigDict(frozen=True)

    contract_version: int = CONTRACT_VERSION

    def to_dict(self) -> dict:
        """JSON-safe dict — for a JSONB column or an API response."""
        return self.model_dump(mode="json")


# ── Purchase order (published by P1) ─────────────────────────────────


class PurchaseOrderLine(_Contract):
    item: str
    quantity: Money
    unit_price: Money
    total_amount: Money


class PurchaseOrderDocument(_Contract):
    purchase_id: str
    po_number: str
    vendor: str
    items: list[PurchaseOrderLine]
    total_amount: Money
    currency: str = "INR"
    status: str = "CREATED"

    def line_for(self, item: str) -> PurchaseOrderLine | None:
        """The ordered line matching `item`, or None if not on the PO."""
        key = normalise_item(item)

        for line in self.items:
            if normalise_item(line.item) == key:
                return line

        return None


# ── Goods receipt (published by S1) ──────────────────────────────────


class GoodsReceiptLine(_Contract):
    item: str
    ordered_quantity: Money
    received_quantity: Money
    unit_price: Money
    total_amount: Money
    condition: str = "GOOD"
    serial_numbers: list[str] = []
    qty_status: str = "MATCH"


class GoodsReceiptDocument(_Contract):
    grn_number: str
    po_number: str
    status: str
    supplier: str | None = None
    delivery_date: str | None = None
    receiving_person: str | None = None
    items: list[GoodsReceiptLine]
    discrepancies: list[dict] = []
    total_items_expected: Money = Decimal("0")
    total_items_received: Money = Decimal("0")


# ── Supplier invoice (registered by P1 or P4) ────────────────────────


class InvoiceLine(_Contract):
    item: str
    quantity: Money
    unit_price: Money
    total_amount: Money


class InvoiceDocument(_Contract):
    invoice_number: str
    po_number: str
    vendor: str | None = None
    invoice_amount: Money
    currency: str = "INR"
    invoice_date: str | None = None
    items: list[InvoiceLine] = []
    status: str = "RECEIVED"
    match_status: str | None = None


class SupplierInvoiceInput(BaseModel):
    """The optional ``supplier_invoice`` block on a purchase request.

    In practice the tax invoice travels with the goods, so a request may
    declare it up front. Validated here rather than trusted, because it
    is the one number the three-way match cannot derive for itself.
    """

    model_config = ConfigDict(extra="ignore")

    invoice_number: str
    invoice_amount: Decimal
    invoice_date: str | None = None
    items: list[dict] = []


# ── RFQ / quote comparison (published by P2) ─────────────────────────


class QuoteLine(_Contract):
    item: str
    quantity: Money
    unit_price: Money


class QuoteSubmission(_Contract):
    """One vendor's quote as normalised by P2."""

    vendor: str
    total_amount: Money
    currency: str = "INR"
    lead_time_days: int | None = None
    lines: list[QuoteLine] = []
    # normalised | parse_failed | price_missing
    parse_status: str = "normalised"
    notes: str | None = None
    received_at: str | None = None


class RfqDocument(_Contract):
    """The quote-comparison pack P2 publishes for the buyer.

    Quotes travel by reference too: a task names the ``rfq_number`` and
    the consumer loads the pack from the database, so a vendor's price
    is never second-guessed from a stale copy.
    """

    rfq_number: str
    vendor: str = ""
    status: str = "ISSUED"
    awarded_vendor: str | None = None
    items: list[QuoteLine] = []
    quotes: list[QuoteSubmission] = []
    issued_at: str | None = None

    def line_for(self, item: str) -> QuoteLine | None:
        """The requested line matching `item`, or None."""
        key = normalise_item(item)

        for line in self.items:
            if normalise_item(line.item) == key:
                return line

        return None

    def ranked_quotes(self) -> list[QuoteSubmission]:
        """Comparable quotes, cheapest first.

        Only normalised quotes rank; a quote that failed to parse or
        lacks a price is shown to the buyer but never recommended.
        """
        return sorted(
            (
                quote
                for quote in self.quotes
                if quote.parse_status == "normalised"
            ),
            key=lambda quote: quote.total_amount,
        )
