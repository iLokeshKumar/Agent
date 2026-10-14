"""Golden XML structure tests for all 8 TallyPrime 7 voucher types.

These tests assert the XML shape produced by each factory function
against the real TallyPrime 7 schema:

  * Correct VCHTYPE attribute on <VOUCHER>
  * PARTYLEDGERNAME present for party-bearing voucher types
  * One ALLLEDGERENTRIES.LIST per ledger leg (not batched)
  * Sign convention: debit → ISDEEMEDPOSITIVE=Yes, AMOUNT negative
                     credit → ISDEEMEDPOSITIVE=No, AMOUNT positive
  * Balance: total signed AMOUNT = 0.00 (voucher is balanced)
  * SVCOMPANYNAME present in the envelope header

Account names used throughout are standard TallyPrime 7 out-of-box
ledger/group names (not synthetic):
  Sundry Debtors, Sundry Creditors, Sales @18%, Purchase @18%,
  Output CGST, Output SGST, Output IGST, Input CGST, Input SGST,
  Input IGST, Sales Returns, Purchase Returns, HDFC Bank, Cash
"""

import xml.etree.ElementTree as ET
from decimal import Decimal

import pytest

from app.integrations.tally_xml import (
    GstLine,
    LedgerEntry,
    TallyXMLBuilder,
    Voucher,
    build_contra_voucher,
    build_credit_note_voucher,
    build_debit_note_voucher,
    build_payment_voucher,
    build_purchase_voucher,
    build_receipt_voucher,
    build_sales_voucher,
    _to_tally_date,
)

# ── Test company (matches the SVCOMPANYNAME requirement) ─────────────

COMPANY = "YEXIS-EL"
DATE = "2026-09-01"


# ── Helpers ──────────────────────────────────────────────────────────


def _parse_envelope(xml_str: str) -> ET.Element:
    return ET.fromstring(xml_str)


def _voucher_element(xml_str: str) -> ET.Element:
    root = _parse_envelope(xml_str)
    vch = root.find(".//VOUCHER")
    assert vch is not None, "no <VOUCHER> element found in XML"
    return vch


def _all_entries(vch: ET.Element) -> list[ET.Element]:
    return vch.findall("ALLLEDGERENTRIES.LIST")


def _signed_amounts(entries: list[ET.Element]) -> list[Decimal]:
    amounts = []
    for e in entries:
        amt_text = (e.findtext("AMOUNT") or "").strip()
        amounts.append(Decimal(amt_text))
    return amounts


def _assert_balanced(entries: list[ET.Element]) -> None:
    total = sum(_signed_amounts(entries))
    assert total == Decimal("0.00"), f"voucher not balanced: sum={total}"


def _assert_signs(entries: list[ET.Element]) -> None:
    """ISDEEMEDPOSITIVE=Yes must have a negative AMOUNT; No → positive."""
    for e in entries:
        idp = e.findtext("ISDEEMEDPOSITIVE")
        amt = Decimal((e.findtext("AMOUNT") or "0").strip())
        if idp == "Yes":
            assert amt < 0, (
                f"ISDEEMEDPOSITIVE=Yes must have negative AMOUNT, got {amt}"
            )
        else:
            assert amt > 0, (
                f"ISDEEMEDPOSITIVE=No must have positive AMOUNT, got {amt}"
            )


def _company_name(xml_str: str) -> str:
    root = _parse_envelope(xml_str)
    return root.findtext(".//SVCOMPANYNAME") or ""


def _build(voucher: Voucher) -> str:
    return TallyXMLBuilder(company_name=COMPANY).build_voucher_import([voucher])


# ── Standard GST line (18% intra-state: CGST 9% + SGST 9%) ──────────

def _cgst_sgst_line(ledger: str, taxable: float) -> GstLine:
    return GstLine(
        ledger_name=ledger,
        taxable_value=taxable,
        cgst_ledger="Output CGST",
        cgst_rate=0.09,
        sgst_ledger="Output SGST",
        sgst_rate=0.09,
    )


def _purchase_gst_line(ledger: str, taxable: float) -> GstLine:
    return GstLine(
        ledger_name=ledger,
        taxable_value=taxable,
        cgst_ledger="Input CGST",
        cgst_rate=0.09,
        sgst_ledger="Input SGST",
        sgst_rate=0.09,
    )


def _igst_line(ledger: str, taxable: float, is_sales: bool) -> GstLine:
    return GstLine(
        ledger_name=ledger,
        taxable_value=taxable,
        igst_ledger="Output IGST" if is_sales else "Input IGST",
        igst_rate=0.18,
    )


# ═══════════════════════════════════════════════════════════════════
# 1. Journal
# ═══════════════════════════════════════════════════════════════════

class TestJournalVoucher:

    def _make(self) -> str:
        voucher = Voucher(
            voucher_type="Journal",
            date=_to_tally_date(DATE),
            number="JNL-001",
            narration="Depreciation of office equipment",
            entries=[
                LedgerEntry("Depreciation", 5000.0, is_deemed_positive=True),
                LedgerEntry("Office Equipment", 5000.0, is_deemed_positive=False),
            ],
        )
        return _build(voucher)

    def test_vchtype(self):
        vch = _voucher_element(self._make())
        assert vch.get("VCHTYPE") == "Journal"

    def test_two_legs(self):
        vch = _voucher_element(self._make())
        assert len(_all_entries(vch)) == 2

    def test_no_party_ledger(self):
        vch = _voucher_element(self._make())
        assert vch.findtext("PARTYLEDGERNAME") is None

    def test_balanced(self):
        vch = _voucher_element(self._make())
        _assert_balanced(_all_entries(vch))

    def test_sign_convention(self):
        vch = _voucher_element(self._make())
        _assert_signs(_all_entries(vch))

    def test_company_name_in_header(self):
        assert _company_name(self._make()) == COMPANY


# ═══════════════════════════════════════════════════════════════════
# 2. Sales (Tax Invoice)
# ═══════════════════════════════════════════════════════════════════

class TestSalesVoucher:

    def _make(self, igst: bool = False) -> str:
        if igst:
            line = _igst_line("Sales @18%", 100000.0, is_sales=True)
        else:
            line = _cgst_sgst_line("Sales @18%", 100000.0)
        voucher = build_sales_voucher(
            party_ledger="Sundry Debtors",
            date_str=DATE,
            lines=[line],
            reference="SI-2026-001",
            narration="Sale of Samsung Galaxy A55 5G",
        )
        return _build(voucher)

    def test_vchtype(self):
        assert _voucher_element(self._make()).get("VCHTYPE") == "Sales"

    def test_party_ledger_present(self):
        vch = _voucher_element(self._make())
        assert vch.findtext("PARTYLEDGERNAME") == "Sundry Debtors"

    def test_party_leg_is_debit(self):
        vch = _voucher_element(self._make())
        debtors = next(
            e for e in _all_entries(vch)
            if e.findtext("LEDGERNAME") == "Sundry Debtors"
        )
        assert debtors.findtext("ISDEEMEDPOSITIVE") == "Yes"

    def test_four_legs_cgst_sgst(self):
        # party + sales + CGST + SGST
        vch = _voucher_element(self._make(igst=False))
        assert len(_all_entries(vch)) == 4

    def test_three_legs_igst(self):
        # party + sales + IGST
        vch = _voucher_element(self._make(igst=True))
        assert len(_all_entries(vch)) == 3

    def test_balanced(self):
        vch = _voucher_element(self._make())
        _assert_balanced(_all_entries(vch))

    def test_sign_convention(self):
        vch = _voucher_element(self._make())
        _assert_signs(_all_entries(vch))

    def test_total_invoice_amount(self):
        """Debtors leg = taxable_value * 1.18 (100000 + 9000 + 9000)."""
        vch = _voucher_element(self._make(igst=False))
        debtors = next(
            e for e in _all_entries(vch)
            if e.findtext("LEDGERNAME") == "Sundry Debtors"
        )
        # debit leg carries negative amount
        assert Decimal(debtors.findtext("AMOUNT")) == Decimal("-118000.00")


# ═══════════════════════════════════════════════════════════════════
# 3. Purchase (Vendor Bill)
# ═══════════════════════════════════════════════════════════════════

class TestPurchaseVoucher:

    def _make(self) -> str:
        line = _purchase_gst_line("Purchase @18%", 50000.0)
        voucher = build_purchase_voucher(
            party_ledger="Sundry Creditors",
            date_str=DATE,
            lines=[line],
            reference="PI-2026-001",
            narration="Purchase of Samsung handsets",
        )
        return _build(voucher)

    def test_vchtype(self):
        assert _voucher_element(self._make()).get("VCHTYPE") == "Purchase"

    def test_party_ledger_present(self):
        vch = _voucher_element(self._make())
        assert vch.findtext("PARTYLEDGERNAME") == "Sundry Creditors"

    def test_creditors_leg_is_credit(self):
        vch = _voucher_element(self._make())
        cred = next(
            e for e in _all_entries(vch)
            if e.findtext("LEDGERNAME") == "Sundry Creditors"
        )
        assert cred.findtext("ISDEEMEDPOSITIVE") == "No"

    def test_four_legs(self):
        # purchase + Input CGST + Input SGST + creditors
        vch = _voucher_element(self._make())
        assert len(_all_entries(vch)) == 4

    def test_balanced(self):
        vch = _voucher_element(self._make())
        _assert_balanced(_all_entries(vch))

    def test_sign_convention(self):
        vch = _voucher_element(self._make())
        _assert_signs(_all_entries(vch))

    def test_creditors_amount(self):
        """Creditors = 50000 * 1.18 = 59000."""
        vch = _voucher_element(self._make())
        cred = next(
            e for e in _all_entries(vch)
            if e.findtext("LEDGERNAME") == "Sundry Creditors"
        )
        assert Decimal(cred.findtext("AMOUNT")) == Decimal("59000.00")


# ═══════════════════════════════════════════════════════════════════
# 4. Receipt (Customer Payment)
# ═══════════════════════════════════════════════════════════════════

class TestReceiptVoucher:

    def _make(self) -> str:
        voucher = build_receipt_voucher(
            party_ledger="Sundry Debtors",
            bank_or_cash_ledger="HDFC Bank",
            amount=118000.0,
            date_str=DATE,
            reference="RCT-2026-001",
            narration="Receipt from dealer against invoice SI-2026-001",
        )
        return _build(voucher)

    def test_vchtype(self):
        assert _voucher_element(self._make()).get("VCHTYPE") == "Receipt"

    def test_two_legs(self):
        vch = _voucher_element(self._make())
        assert len(_all_entries(vch)) == 2

    def test_bank_is_debit(self):
        vch = _voucher_element(self._make())
        bank = next(
            e for e in _all_entries(vch)
            if e.findtext("LEDGERNAME") == "HDFC Bank"
        )
        assert bank.findtext("ISDEEMEDPOSITIVE") == "Yes"

    def test_debtors_is_credit(self):
        vch = _voucher_element(self._make())
        dr = next(
            e for e in _all_entries(vch)
            if e.findtext("LEDGERNAME") == "Sundry Debtors"
        )
        assert dr.findtext("ISDEEMEDPOSITIVE") == "No"

    def test_balanced(self):
        vch = _voucher_element(self._make())
        _assert_balanced(_all_entries(vch))

    def test_sign_convention(self):
        vch = _voucher_element(self._make())
        _assert_signs(_all_entries(vch))


# ═══════════════════════════════════════════════════════════════════
# 5. Payment (Vendor Payment)
# ═══════════════════════════════════════════════════════════════════

class TestPaymentVoucher:

    def _make(self) -> str:
        voucher = build_payment_voucher(
            party_ledger="Sundry Creditors",
            bank_or_cash_ledger="HDFC Bank",
            amount=59000.0,
            date_str=DATE,
            reference="PAY-2026-001",
            narration="Payment to vendor against PI-2026-001",
        )
        return _build(voucher)

    def test_vchtype(self):
        assert _voucher_element(self._make()).get("VCHTYPE") == "Payment"

    def test_two_legs(self):
        vch = _voucher_element(self._make())
        assert len(_all_entries(vch)) == 2

    def test_creditors_is_debit(self):
        vch = _voucher_element(self._make())
        cred = next(
            e for e in _all_entries(vch)
            if e.findtext("LEDGERNAME") == "Sundry Creditors"
        )
        assert cred.findtext("ISDEEMEDPOSITIVE") == "Yes"

    def test_bank_is_credit(self):
        vch = _voucher_element(self._make())
        bank = next(
            e for e in _all_entries(vch)
            if e.findtext("LEDGERNAME") == "HDFC Bank"
        )
        assert bank.findtext("ISDEEMEDPOSITIVE") == "No"

    def test_balanced(self):
        vch = _voucher_element(self._make())
        _assert_balanced(_all_entries(vch))

    def test_sign_convention(self):
        vch = _voucher_element(self._make())
        _assert_signs(_all_entries(vch))


# ═══════════════════════════════════════════════════════════════════
# 6. Credit Note (Sales Return)
# ═══════════════════════════════════════════════════════════════════

class TestCreditNoteVoucher:

    def _make(self) -> str:
        line = _cgst_sgst_line("Sales Returns", 20000.0)
        # Rename output tax ledgers for the return (same names as original)
        line.cgst_ledger = "Output CGST"
        line.sgst_ledger = "Output SGST"
        voucher = build_credit_note_voucher(
            party_ledger="Sundry Debtors",
            date_str=DATE,
            lines=[line],
            reference="CN-2026-001",
            narration="Sales return against SI-2026-001",
        )
        return _build(voucher)

    def test_vchtype(self):
        assert _voucher_element(self._make()).get("VCHTYPE") == "Credit Note"

    def test_party_ledger_present(self):
        vch = _voucher_element(self._make())
        assert vch.findtext("PARTYLEDGERNAME") == "Sundry Debtors"

    def test_four_legs(self):
        vch = _voucher_element(self._make())
        assert len(_all_entries(vch)) == 4

    def test_debtors_is_credit(self):
        vch = _voucher_element(self._make())
        dr = next(
            e for e in _all_entries(vch)
            if e.findtext("LEDGERNAME") == "Sundry Debtors"
        )
        assert dr.findtext("ISDEEMEDPOSITIVE") == "No"

    def test_sales_returns_is_debit(self):
        vch = _voucher_element(self._make())
        sr = next(
            e for e in _all_entries(vch)
            if e.findtext("LEDGERNAME") == "Sales Returns"
        )
        assert sr.findtext("ISDEEMEDPOSITIVE") == "Yes"

    def test_balanced(self):
        vch = _voucher_element(self._make())
        _assert_balanced(_all_entries(vch))

    def test_sign_convention(self):
        vch = _voucher_element(self._make())
        _assert_signs(_all_entries(vch))


# ═══════════════════════════════════════════════════════════════════
# 7. Debit Note (Purchase Return)
# ═══════════════════════════════════════════════════════════════════

class TestDebitNoteVoucher:

    def _make(self) -> str:
        line = GstLine(
            ledger_name="Purchase Returns",
            taxable_value=10000.0,
            cgst_ledger="Input CGST",
            cgst_rate=0.09,
            sgst_ledger="Input SGST",
            sgst_rate=0.09,
        )
        voucher = build_debit_note_voucher(
            party_ledger="Sundry Creditors",
            date_str=DATE,
            lines=[line],
            reference="DN-2026-001",
            narration="Purchase return against PI-2026-001",
        )
        return _build(voucher)

    def test_vchtype(self):
        assert _voucher_element(self._make()).get("VCHTYPE") == "Debit Note"

    def test_party_ledger_present(self):
        vch = _voucher_element(self._make())
        assert vch.findtext("PARTYLEDGERNAME") == "Sundry Creditors"

    def test_four_legs(self):
        vch = _voucher_element(self._make())
        assert len(_all_entries(vch)) == 4

    def test_creditors_is_debit(self):
        vch = _voucher_element(self._make())
        cred = next(
            e for e in _all_entries(vch)
            if e.findtext("LEDGERNAME") == "Sundry Creditors"
        )
        assert cred.findtext("ISDEEMEDPOSITIVE") == "Yes"

    def test_purchase_returns_is_credit(self):
        vch = _voucher_element(self._make())
        pr = next(
            e for e in _all_entries(vch)
            if e.findtext("LEDGERNAME") == "Purchase Returns"
        )
        assert pr.findtext("ISDEEMEDPOSITIVE") == "No"

    def test_balanced(self):
        vch = _voucher_element(self._make())
        _assert_balanced(_all_entries(vch))

    def test_sign_convention(self):
        vch = _voucher_element(self._make())
        _assert_signs(_all_entries(vch))


# ═══════════════════════════════════════════════════════════════════
# 8. Contra (Cash / Bank Transfer)
# ═══════════════════════════════════════════════════════════════════

class TestContraVoucher:

    def _make(self) -> str:
        voucher = build_contra_voucher(
            source_ledger="Cash",
            destination_ledger="HDFC Bank",
            amount=200000.0,
            date_str=DATE,
            reference="CTR-2026-001",
            narration="Cash deposit to HDFC bank account",
        )
        return _build(voucher)

    def test_vchtype(self):
        assert _voucher_element(self._make()).get("VCHTYPE") == "Contra"

    def test_no_party_ledger(self):
        vch = _voucher_element(self._make())
        assert vch.findtext("PARTYLEDGERNAME") is None

    def test_two_legs(self):
        vch = _voucher_element(self._make())
        assert len(_all_entries(vch)) == 2

    def test_destination_is_debit(self):
        vch = _voucher_element(self._make())
        bank = next(
            e for e in _all_entries(vch)
            if e.findtext("LEDGERNAME") == "HDFC Bank"
        )
        assert bank.findtext("ISDEEMEDPOSITIVE") == "Yes"

    def test_source_is_credit(self):
        vch = _voucher_element(self._make())
        cash = next(
            e for e in _all_entries(vch)
            if e.findtext("LEDGERNAME") == "Cash"
        )
        assert cash.findtext("ISDEEMEDPOSITIVE") == "No"

    def test_balanced(self):
        vch = _voucher_element(self._make())
        _assert_balanced(_all_entries(vch))

    def test_sign_convention(self):
        vch = _voucher_element(self._make())
        _assert_signs(_all_entries(vch))


# ═══════════════════════════════════════════════════════════════════
# Cross-cutting: envelope structure for all 8 types
# ═══════════════════════════════════════════════════════════════════

class TestEnvelopeStructure:
    """SVCOMPANYNAME and TALLYREQUEST are present for every voucher type."""

    def _xmls(self):
        line_s = _cgst_sgst_line("Sales @18%", 10000.0)
        line_p = _purchase_gst_line("Purchase @18%", 10000.0)
        line_cn = GstLine(
            ledger_name="Sales Returns", taxable_value=5000.0,
            cgst_ledger="Output CGST", cgst_rate=0.09,
            sgst_ledger="Output SGST", sgst_rate=0.09,
        )
        line_dn = GstLine(
            ledger_name="Purchase Returns", taxable_value=5000.0,
            cgst_ledger="Input CGST", cgst_rate=0.09,
            sgst_ledger="Input SGST", sgst_rate=0.09,
        )
        return [
            _build(Voucher("Journal", _to_tally_date(DATE), entries=[
                LedgerEntry("Office Expenses", 1000.0, True),
                LedgerEntry("Cash", 1000.0, False),
            ])),
            _build(build_sales_voucher("Sundry Debtors", DATE, [line_s])),
            _build(build_purchase_voucher("Sundry Creditors", DATE, [line_p])),
            _build(build_receipt_voucher("Sundry Debtors", "HDFC Bank", 5000.0, DATE)),
            _build(build_payment_voucher("Sundry Creditors", "HDFC Bank", 5000.0, DATE)),
            _build(build_credit_note_voucher("Sundry Debtors", DATE, [line_cn])),
            _build(build_debit_note_voucher("Sundry Creditors", DATE, [line_dn])),
            _build(build_contra_voucher("Cash", "HDFC Bank", 5000.0, DATE)),
        ]

    def test_tally_request_is_import_data(self):
        for xml in self._xmls():
            root = _parse_envelope(xml)
            assert root.findtext(".//TALLYREQUEST") == "Import Data", (
                "TALLYREQUEST must be 'Import Data' for TallyPrime 7"
            )

    def test_svcompanyname_present(self):
        for xml in self._xmls():
            assert _company_name(xml) == COMPANY

    def test_reportname_is_vouchers(self):
        for xml in self._xmls():
            root = _parse_envelope(xml)
            assert root.findtext(".//REPORTNAME") == "Vouchers"

    def test_all_vouchers_balanced(self):
        for xml in self._xmls():
            vch = _voucher_element(xml)
            entries = _all_entries(vch)
            _assert_balanced(entries)

    def test_all_sign_conventions_correct(self):
        for xml in self._xmls():
            vch = _voucher_element(xml)
            _assert_signs(_all_entries(vch))

    def test_action_is_create(self):
        for xml in self._xmls():
            vch = _voucher_element(xml)
            assert vch.get("ACTION") == "Create"
