"""Tally XML builder for importing journal entries.

Generates Tally-compatible XML that can be imported via:
  - Tally GUI: Import → XML
  - Tally ODBC/HTTP API: POST to http://localhost:9000

Reference: Tally.ERP 9 / TallyPrime XML schema
"""

import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime
from dataclasses import dataclass, field


# Tally date format: YYYYMMDD
TALLY_DATE_FORMAT = "%Y%m%d"


@dataclass
class LedgerEntry:
    """Single ledger line in a voucher."""
    ledger_name: str
    amount: float  # positive = debit, negative = credit
    is_deemed_positive: bool = False  # True = debit
    cost_centre: str | None = None
    narration: str | None = None


@dataclass
class Voucher:
    """A Tally voucher (journal, payment, receipt, etc.)."""
    voucher_type: str  # Journal, Payment, Receipt, Sales, etc.
    date: str  # YYYYMMDD
    number: str | None = None
    party_ledger: str | None = None
    narration: str = ""
    entries: list[LedgerEntry] = field(default_factory=list)
    reference: str | None = None


class TallyXMLBuilder:
    """Builds Tally-compatible XML for import."""

    def __init__(self, company_name: str = "Default Company"):
        self.company_name = company_name

    def build_voucher_import(
        self,
        vouchers: list[Voucher],
    ) -> str:
        """Build XML for importing a list of vouchers.

        Returns a pretty-printed XML string.
        """
        root = ET.Element("ENVELOPE")

        # Header
        # TallyPrime 7 accepts "Import Data" (ERP-9's "Import Vouchers"
        # is answered with "Unknown Request, cannot be processed" —
        # verified against a live TallyPrime 7 Gold, Sep 2026).
        header = ET.SubElement(root, "HEADER")
        tally_request = ET.SubElement(header, "TALLYREQUEST")
        tally_request.text = "Import Data"

        # Body
        body = ET.SubElement(root, "BODY")
        import_data = ET.SubElement(body, "IMPORTDATA")

        # Request description
        request_desc = ET.SubElement(import_data, "REQUESTDESC")
        report_name = ET.SubElement(request_desc, "REPORTNAME")
        report_name.text = "Vouchers"

        static_vars = ET.SubElement(
            request_desc, "STATICVARIABLES"
        )
        # TallyPrime 7 binds SVCOMPANYNAME and REJECTS
        # SVCURRENTCOMPANY ("Could not set 'SVCurrentCompany' to
        # '...'"), even when both are present — verified live against
        # TallyPrime 7 Gold, Sep 2026 (bind matrix over Import and
        # Export). SVCOMPANYNAME alone is the TP7-safe form.
        ET.SubElement(static_vars, "SVCOMPANYNAME").text = self.company_name

        # Request data
        request_data = ET.SubElement(import_data, "REQUESTDATA")

        for voucher in vouchers:
            self._build_voucher_element(request_data, voucher)

        return self._pretty_xml(root)

    def build_ledger_import(
        self,
        ledgers: list[dict],
    ) -> str:
        """Build XML for importing chart of accounts.

        Each ledger dict should have:
          - name: str
          - group: str (e.g., "Current Assets", "Sales Account")
          - opening_balance: float (optional)
        """
        root = ET.Element("ENVELOPE")

        header = ET.SubElement(root, "HEADER")
        tally_request = ET.SubElement(header, "TALLYREQUEST")
        tally_request.text = "Import Data"

        body = ET.SubElement(root, "BODY")
        import_data = ET.SubElement(body, "IMPORTDATA")

        request_desc = ET.SubElement(import_data, "REQUESTDESC")
        report_name = ET.SubElement(request_desc, "REPORTNAME")
        report_name.text = "All Masters"

        static_vars = ET.SubElement(
            request_desc, "STATICVARIABLES"
        )
        # SVCOMPANYNAME only — see note in build_voucher_import above.
        ET.SubElement(static_vars, "SVCOMPANYNAME").text = self.company_name

        request_data = ET.SubElement(import_data, "REQUESTDATA")

        for ledger in ledgers:
            msg = ET.SubElement(
                request_data, "TALLYMESSAGE"
            )
            msg.set("xmlns:UDF", "TallyUDF")

            ledger_elem = ET.SubElement(msg, "LEDGER")
            ledger_elem.set("ACTION", "Create")

            name = ET.SubElement(ledger_elem, "NAME")
            name.text = ledger.get("name", "")

            group = ET.SubElement(ledger_elem, "GROUP")
            group.text = ledger.get("group", "Current Assets")

            if ledger.get("opening_balance"):
                ob = ET.SubElement(
                    ledger_elem, "OPENINGBALANCE"
                )
                ob.text = str(ledger["opening_balance"])

        return self._pretty_xml(root)

    def build_company_select(
        self,
        company_name: str,
    ) -> str:
        """Build XML to select a company in Tally."""
        root = ET.Element("ENVELOPE")

        header = ET.SubElement(root, "HEADER")
        tally_request = ET.SubElement(header, "TALLYREQUEST")
        tally_request.text = "Select Company"

        body = ET.SubElement(root, "BODY")
        desc = ET.SubElement(body, "DESC")
        static_vars = ET.SubElement(desc, "STATICVARIABLES")
        svc = ET.SubElement(static_vars, "SVCURRENTCOMPANY")
        svc.text = company_name

        return self._pretty_xml(root)

    # ----------------------------------------------------------------
    # Internal: build voucher element
    # ----------------------------------------------------------------

    def _build_voucher_element(
        self,
        parent: ET.Element,
        voucher: Voucher,
    ):
        """Add a single VOUCHER element to the XML tree."""
        msg = ET.SubElement(parent, "TALLYMESSAGE")
        msg.set("xmlns:UDF", "TallyUDF")

        vch = ET.SubElement(msg, "VOUCHER")
        vch.set("VCHTYPE", voucher.voucher_type)
        vch.set("ACTION", "Create")

        # Date
        date_elem = ET.SubElement(vch, "DATE")
        date_elem.text = voucher.date

        # Voucher number
        if voucher.number:
            num = ET.SubElement(vch, "VOUCHERNUMBER")
            num.text = voucher.number

        # Party ledger
        if voucher.party_ledger:
            party = ET.SubElement(vch, "PARTYLEDGERNAME")
            party.text = voucher.party_ledger

        # Narration
        if voucher.narration:
            narr = ET.SubElement(vch, "NARRATION")
            narr.text = voucher.narration

        # Reference
        if voucher.reference:
            ref = ET.SubElement(vch, "REFERENCE")
            ref.text = voucher.reference

        # Ledger entries — ONE ALLLEDGERENTRIES.LIST element per leg
        # (Tally treats the ".LIST" suffix as a repeated collection;
        # two legs inside a single list element land the voucher in
        # EXCEPTIONS on TallyPrime 7, verified live Sep 2026).
        for entry in voucher.entries:
            entries_list = ET.SubElement(vch, "ALLLEDGERENTRIES.LIST")

            ledger_name = ET.SubElement(entries_list, "LEDGERNAME")
            ledger_name.text = entry.ledger_name

            # ISDEEMEDPOSITIVE: Yes = debit, No = credit
            is_dp = ET.SubElement(entries_list, "ISDEEMEDPOSITIVE")
            is_dp.text = "Yes" if entry.is_deemed_positive else "No"

            # Amount: signed per Tally convention — debit is NEGATIVE
            # with ISDEEMEDPOSITIVE=Yes, credit is positive with No
            # (verified live against TallyPrime 7, Sep 2026: a voucher
            # with positive debit amounts is silently dropped into
            # EXCEPTIONS).
            amount = ET.SubElement(entries_list, "AMOUNT")
            amount.text = (
                f"{-abs(entry.amount):.2f}"
                if entry.is_deemed_positive
                else f"{abs(entry.amount):.2f}"
            )

            # Cost centre (optional)
            if entry.cost_centre:
                cc_list = ET.SubElement(
                    entries_list, "ALLOCATIONS.LIST"
                )
                cc = ET.SubElement(cc_list, "ALLOCATIONS")
                cc_name = ET.SubElement(cc, "COSTCENTRENAME")
                cc_name.text = entry.cost_centre
                cc_amt = ET.SubElement(cc, "AMOUNT")
                cc_amt.text = (
                    f"{-abs(entry.amount):.2f}"
                    if entry.is_deemed_positive
                    else f"{abs(entry.amount):.2f}"
                )

    # ----------------------------------------------------------------
    # Internal: pretty print XML
    # ----------------------------------------------------------------

    def _pretty_xml(self, root: ET.Element) -> str:
        """Convert ElementTree to pretty-printed string."""
        rough = ET.tostring(root, encoding="unicode")
        parsed = minidom.parseString(rough)
        pretty = parsed.toprettyxml(
            indent="  ", encoding=None
        )

        # Remove minidom XML declaration
        lines = pretty.split("\n")
        if lines[0].startswith("<?xml"):
            lines = lines[1:]

        return "\n".join(lines)


# ----------------------------------------------------------------
# Helpers: convert Zoho-style journal entries to Tally vouchers
# ----------------------------------------------------------------

def zoho_lines_to_tally_vouchers(
    journal_lines: list[dict],
    account_map: dict[str, str] | None = None,
    company_name: str = "Default Company",
) -> list[Voucher]:
    """Convert Zoho journal lines to Tally vouchers.

    Groups lines by journal_number (or date if no number)
    to form balanced vouchers.
    """
    if account_map is None:
        account_map = {}

    # Group lines by journal reference
    grouped: dict[str, list[dict]] = {}
    for line in journal_lines:
        key = (
            line.get("journal_number")
            or line.get("reference", "")
            or line.get("journal_date", "unknown")
        )
        grouped.setdefault(key, []).append(line)

    vouchers = []

    for ref, lines in grouped.items():
        entries = []
        date_str = ""
        narration = ""

        for line in lines:
            account_name = line.get("account_name", "")
            tally_name = account_map.get(account_name, account_name)

            debit = float(line.get("debit", 0) or 0)
            credit = float(line.get("credit", 0) or 0)

            # Tally convention: debit = positive, credit = negative
            if debit > 0:
                entries.append(LedgerEntry(
                    ledger_name=tally_name,
                    amount=debit,
                    is_deemed_positive=True,
                ))
            elif credit > 0:
                entries.append(LedgerEntry(
                    ledger_name=tally_name,
                    amount=credit,
                    is_deemed_positive=False,
                ))

            date_str = line.get("journal_date", "")
            narration = line.get("journal_notes", "") or narration

        # Format date to Tally YYYYMMDD
        tally_date = _to_tally_date(date_str)

        vouchers.append(Voucher(
            voucher_type="Journal",
            date=tally_date,
            number=ref if ref != "unknown" else None,
            narration=narration,
            entries=entries,
            reference=ref,
        ))

    return vouchers


def _to_tally_date(date_str: str) -> str:
    """Convert YYYY-MM-DD to YYYYMMDD for Tally."""
    if not date_str:
        return datetime.now().strftime(TALLY_DATE_FORMAT)

    # Try common formats
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime(TALLY_DATE_FORMAT)
        except ValueError:
            continue

    # Fallback: return as-is if already YYYYMMDD
    if len(date_str) == 8 and date_str.isdigit():
        return date_str

    return datetime.now().strftime(TALLY_DATE_FORMAT)


def generate_tally_xml_from_entries(
    staged_entries: list[dict],
    account_map: dict[str, str] | None = None,
    company_name: str = "Default Company",
) -> str:
    """One-shot: staged entries → Tally XML string.

    This is the main function agents call.
    """
    vouchers = zoho_lines_to_tally_vouchers(
        staged_entries, account_map, company_name
    )

    builder = TallyXMLBuilder(company_name=company_name)
    return builder.build_voucher_import(vouchers)


# ----------------------------------------------------------------
# Per-type voucher factories
# ----------------------------------------------------------------
# TallyPrime 7 GST conventions (verified live, Sep 2026):
#   - debit leg: ISDEEMEDPOSITIVE=Yes, AMOUNT negative
#   - credit leg: ISDEEMEDPOSITIVE=No, AMOUNT positive
#   - PARTYLEDGERNAME at voucher level AND as first ALLLEDGERENTRIES.LIST
#     for Sales/Purchase/Receipt/Payment/CreditNote/DebitNote


@dataclass
class GstLine:
    """One product/service leg for a GST-taxable voucher.

    Use ``to_credit_entries()`` for Sales/Debit Note (income side).
    Use ``to_debit_entries()`` for Purchase/Credit Note (expense side).
    """
    ledger_name: str          # sales or purchase account in Tally
    taxable_value: float      # pre-tax amount
    cgst_ledger: str | None = None
    cgst_rate: float = 0.0    # fraction: 0.09 for 9%
    sgst_ledger: str | None = None
    sgst_rate: float = 0.0
    igst_ledger: str | None = None
    igst_rate: float = 0.0
    cost_centre: str | None = None

    def total(self) -> float:
        return round(
            self.taxable_value * (
                1 + self.cgst_rate + self.sgst_rate + self.igst_rate
            ),
            2,
        )

    def to_credit_entries(self) -> list[LedgerEntry]:
        """Credit legs: used for Sales and Debit Note."""
        entries: list[LedgerEntry] = [
            LedgerEntry(
                self.ledger_name, self.taxable_value,
                is_deemed_positive=False,
                cost_centre=self.cost_centre,
            )
        ]
        if self.cgst_ledger and self.cgst_rate > 0:
            entries.append(LedgerEntry(
                self.cgst_ledger,
                round(self.taxable_value * self.cgst_rate, 2),
                is_deemed_positive=False,
            ))
        if self.sgst_ledger and self.sgst_rate > 0:
            entries.append(LedgerEntry(
                self.sgst_ledger,
                round(self.taxable_value * self.sgst_rate, 2),
                is_deemed_positive=False,
            ))
        if self.igst_ledger and self.igst_rate > 0:
            entries.append(LedgerEntry(
                self.igst_ledger,
                round(self.taxable_value * self.igst_rate, 2),
                is_deemed_positive=False,
            ))
        return entries

    def to_debit_entries(self) -> list[LedgerEntry]:
        """Debit legs: used for Purchase and Credit Note."""
        entries: list[LedgerEntry] = [
            LedgerEntry(
                self.ledger_name, self.taxable_value,
                is_deemed_positive=True,
                cost_centre=self.cost_centre,
            )
        ]
        if self.cgst_ledger and self.cgst_rate > 0:
            entries.append(LedgerEntry(
                self.cgst_ledger,
                round(self.taxable_value * self.cgst_rate, 2),
                is_deemed_positive=True,
            ))
        if self.sgst_ledger and self.sgst_rate > 0:
            entries.append(LedgerEntry(
                self.sgst_ledger,
                round(self.taxable_value * self.sgst_rate, 2),
                is_deemed_positive=True,
            ))
        if self.igst_ledger and self.igst_rate > 0:
            entries.append(LedgerEntry(
                self.igst_ledger,
                round(self.taxable_value * self.igst_rate, 2),
                is_deemed_positive=True,
            ))
        return entries


def build_sales_voucher(
    party_ledger: str,
    date_str: str,
    lines: list[GstLine],
    reference: str | None = None,
    narration: str = "",
) -> Voucher:
    """Sales tax invoice — AR: debtors debit, sales + output tax credit."""
    total_invoice = round(sum(ln.total() for ln in lines), 2)
    entries: list[LedgerEntry] = [
        LedgerEntry(party_ledger, total_invoice, is_deemed_positive=True),
    ]
    for ln in lines:
        entries.extend(ln.to_credit_entries())
    return Voucher(
        voucher_type="Sales",
        date=_to_tally_date(date_str),
        number=reference,
        party_ledger=party_ledger,
        narration=narration,
        entries=entries,
        reference=reference,
    )


def build_purchase_voucher(
    party_ledger: str,
    date_str: str,
    lines: list[GstLine],
    reference: str | None = None,
    narration: str = "",
) -> Voucher:
    """Purchase bill — AP: purchase + input tax debit, creditors credit."""
    total_bill = round(sum(ln.total() for ln in lines), 2)
    entries: list[LedgerEntry] = []
    for ln in lines:
        entries.extend(ln.to_debit_entries())
    entries.append(LedgerEntry(party_ledger, total_bill, is_deemed_positive=False))
    return Voucher(
        voucher_type="Purchase",
        date=_to_tally_date(date_str),
        number=reference,
        party_ledger=party_ledger,
        narration=narration,
        entries=entries,
        reference=reference,
    )


def build_receipt_voucher(
    party_ledger: str,
    bank_or_cash_ledger: str,
    amount: float,
    date_str: str,
    reference: str | None = None,
    narration: str = "",
) -> Voucher:
    """Customer payment receipt — bank debit, debtors credit."""
    return Voucher(
        voucher_type="Receipt",
        date=_to_tally_date(date_str),
        number=reference,
        party_ledger=party_ledger,
        narration=narration,
        entries=[
            LedgerEntry(bank_or_cash_ledger, amount, is_deemed_positive=True),
            LedgerEntry(party_ledger, amount, is_deemed_positive=False),
        ],
        reference=reference,
    )


def build_payment_voucher(
    party_ledger: str,
    bank_or_cash_ledger: str,
    amount: float,
    date_str: str,
    reference: str | None = None,
    narration: str = "",
) -> Voucher:
    """Vendor payment — creditors debit, bank credit."""
    return Voucher(
        voucher_type="Payment",
        date=_to_tally_date(date_str),
        number=reference,
        party_ledger=party_ledger,
        narration=narration,
        entries=[
            LedgerEntry(party_ledger, amount, is_deemed_positive=True),
            LedgerEntry(bank_or_cash_ledger, amount, is_deemed_positive=False),
        ],
        reference=reference,
    )


def build_credit_note_voucher(
    party_ledger: str,
    date_str: str,
    lines: list[GstLine],
    reference: str | None = None,
    narration: str = "",
) -> Voucher:
    """Sales return credit note — sales return debit, debtors credit.

    Reverses a prior Sales voucher: the customer returns goods so
    revenue and output-tax are debited back and the receivable is credited.
    """
    total_return = round(sum(ln.total() for ln in lines), 2)
    entries: list[LedgerEntry] = []
    for ln in lines:
        entries.extend(ln.to_debit_entries())
    entries.append(LedgerEntry(party_ledger, total_return, is_deemed_positive=False))
    return Voucher(
        voucher_type="Credit Note",
        date=_to_tally_date(date_str),
        number=reference,
        party_ledger=party_ledger,
        narration=narration,
        entries=entries,
        reference=reference,
    )


def build_debit_note_voucher(
    party_ledger: str,
    date_str: str,
    lines: list[GstLine],
    reference: str | None = None,
    narration: str = "",
) -> Voucher:
    """Purchase return debit note — creditors debit, purchase return credit.

    Reverses a prior Purchase voucher: goods returned to vendor so the
    payable and input-tax are reversed.
    """
    total_return = round(sum(ln.total() for ln in lines), 2)
    entries: list[LedgerEntry] = [
        LedgerEntry(party_ledger, total_return, is_deemed_positive=True),
    ]
    for ln in lines:
        entries.extend(ln.to_credit_entries())
    return Voucher(
        voucher_type="Debit Note",
        date=_to_tally_date(date_str),
        number=reference,
        party_ledger=party_ledger,
        narration=narration,
        entries=entries,
        reference=reference,
    )


def build_contra_voucher(
    source_ledger: str,
    destination_ledger: str,
    amount: float,
    date_str: str,
    reference: str | None = None,
    narration: str = "",
) -> Voucher:
    """Cash/bank contra — destination bank debit, source bank/cash credit.

    Used for cash deposit to bank, bank withdrawal, inter-bank transfer.
    No party ledger — both legs are cash/bank accounts.
    """
    return Voucher(
        voucher_type="Contra",
        date=_to_tally_date(date_str),
        number=reference,
        party_ledger=None,
        narration=narration,
        entries=[
            LedgerEntry(destination_ledger, amount, is_deemed_positive=True),
            LedgerEntry(source_ledger, amount, is_deemed_positive=False),
        ],
        reference=reference,
    )
