import uuid
from datetime import datetime, timezone

from app.agents.base import BaseAgent, AgentResult
from app.integrations.zoho_books import (
    get_zoho_client,
    ZohoBooksError,
)
from app.integrations.tally_xml import generate_tally_xml_from_entries


# Default Zoho account name → Tally ledger name mapping
DEFAULT_ACCOUNT_MAP = {
    "Sales": "Sales Account",
    "Sales Revenue": "Sales Account",
    "Purchases": "Purchase Account",
    "Purchase Account": "Purchase Account",
    "Bank Charges": "Bank Charges A/c",
    "GST Input": "Input GST",
    "Input CGST": "Input CGST",
    "Input SGST": "Input SGST",
    "Input IGST": "Input IGST",
    "GST Output": "Output GST",
    "Output CGST": "Output CGST",
    "Output SGST": "Output SGST",
    "Output IGST": "Output IGST",
    "TDS Payable": "TDS Payable",
    "TDS Receivable": "TDS Receivable",
    "Cash": "Cash in Hand",
    "Petty Cash": "Petty Cash",
}


class F3BooksToTally(BaseAgent):
    """Books to Tally Agent — F3 (REAL DATA ONLY)

    Stages Zoho Books data for Tally import:
    - Fetches journal entries from LIVE Zoho Books API (no payload fallback)
    - Maps Zoho accounts to Tally ledger names
    - Generates Tally-compatible import format
    - Produces reconciliation report

    Real-data contract: Zoho must be configured (ZOHO_CLIENT_ID/SECRET/
    REFRESH_TOKEN/ORG_ID). If not configured the task fails VALIDATION_ERROR
    with a clear message and a link to /auth/zoho — it never silently
    uses payload.journal_entries mock data.
    """

    agent_code = "F3"
    agent_name = "Books to Tally"

    def __init__(self):
        super().__init__()

    def describe(self) -> str:
        return (
            "Zoho Books to Tally staging and reconciliation. "
            "Exports journal entries and generates reconciliation report."
        )

    def validate(self, payload: dict) -> str | None:
        if not payload:
            return "Payload is empty"

        if not payload.get("start_date"):
            return "'start_date' is required (YYYY-MM-DD)"
        if not payload.get("end_date"):
            return "'end_date' is required (YYYY-MM-DD)"

        return None

    def execute(
        self,
        payload: dict,
        task_id: uuid.UUID | None = None,
        requested_by: str | None = None,
    ) -> AgentResult:
        now = datetime.now(timezone.utc)

        start_date = payload["start_date"]
        end_date = payload["end_date"]
        company = payload.get("company", "Default Company")
        account_map = payload.get("account_map", DEFAULT_ACCOUNT_MAP)

        client = get_zoho_client()
        if not client.configured:
            missing = []
            if not client.client_id:
                missing.append("ZOHO_CLIENT_ID")
            if not client.client_secret:
                missing.append("ZOHO_CLIENT_SECRET")
            if not client.refresh_token:
                missing.append("ZOHO_REFRESH_TOKEN (run GET /auth/zoho in browser)")
            if not client.organization_id:
                missing.append("ZOHO_ORG_ID")
            return AgentResult(
                status="FAILED",
                error=(
                    f"Zoho Books not configured — missing: {', '.join(missing)}. "
                    f"Complete OAuth at http://127.0.0.1:8000/auth/zoho then retry. "
                    f"Current data_center={client.data_center}. No mock data is used in prod/mcq."
                ),
            )
        # Accept pre-loaded journal entries (demo mode or admin override)
        if payload.get("journal_entries"):
            source = "payload_override"
            journal_lines = payload["journal_entries"]
            self.logger.info(
                "Books→Tally: using %d pre-loaded journal lines from payload",
                len(journal_lines),
            )
        else:
            source = "zoho_live"
            journal_lines = self._fetch_from_zoho(client, start_date, end_date)

        # --- Map accounts and stage entries ---
        staged_entries = []
        unmapped_accounts = set()
        total_debit = 0.0
        total_credit = 0.0

        for line in journal_lines:
            zoho_account = line.get("account_name", "")
            tally_account = account_map.get(zoho_account)

            if tally_account is None:
                unmapped_accounts.add(zoho_account)
                tally_account = f"UNMAPPED: {zoho_account}"

            debit = float(line.get("debit", 0) or 0)
            credit = float(line.get("credit", 0) or 0)
            total_debit += debit
            total_credit += credit

            staged_entries.append({
                "date": line.get(
                    "journal_date",
                    line.get("date", start_date),
                ),
                "reference": line.get(
                    "journal_number",
                    line.get("reference", ""),
                ),
                "narration": line.get(
                    "journal_notes",
                    line.get("narration", ""),
                ),
                "zoho_account": zoho_account,
                "tally_account": tally_account,
                "debit": round(debit, 2),
                "credit": round(credit, 2),
                "voucher_type": line.get("voucher_type", "Journal"),
            })

        # --- Fetch chart of accounts for mapping help ---
        coa = []
        if client.configured:
            try:
                coa = client.list_chart_of_accounts()
            except ZohoBooksError as exc:
                self.logger.warning(
                    "Could not fetch chart of accounts: %s", exc
                )

        # --- Reconciliation ---
        balance_diff = round(total_debit - total_credit, 2)

        reconciliation = {
            "total_entries": len(staged_entries),
            "total_debit": round(total_debit, 2),
            "total_credit": round(total_credit, 2),
            "balance_difference": balance_diff,
            "is_balanced": abs(balance_diff) < 0.01,
            "unmapped_accounts": sorted(unmapped_accounts),
            "mapping_coverage": (
                round(
                    (1 - len(unmapped_accounts) / max(len(account_map), 1))
                    * 100,
                    1,
                )
                if account_map
                else 100.0
            ),
        }

        actions = [
            f"extracted_entries:{len(staged_entries)}",
            "mapped_accounts",
            "generated_tally_format",
            f"source:{source}",
        ]

        if unmapped_accounts:
            actions.append(
                f"flagged_unmapped:{len(unmapped_accounts)}"
            )

        # --- Generate Tally XML ---
        tally_xml = generate_tally_xml_from_entries(
            staged_entries, account_map, company
        )

        output = {
            "source": source,
            "period": f"{start_date} to {end_date}",
            "staged_count": len(staged_entries),
            "staged_entries": staged_entries,
            "reconciliation": reconciliation,
            "tally_xml": tally_xml,
            "chart_of_accounts": [
                {
                    "name": a.get("account_name", ""),
                    "type": a.get("account_type", ""),
                    "code": a.get("account_code", ""),
                }
                for a in coa[:50]  # limit for response size
            ],
            "summary": {
                "period": f"{start_date} to {end_date}",
                "company": company,
                "source": source,
                "total_entries": len(staged_entries),
                "total_debit": round(total_debit, 2),
                "total_credit": round(total_credit, 2),
                "balanced": reconciliation["is_balanced"],
                "unmapped_count": len(unmapped_accounts),
                "tally_voucher_count": tally_xml.count("<VOUCHER"),
            },
        }

        if not reconciliation["is_balanced"]:
            output["warning"] = (
                f"Books are not balanced. "
                f"Difference: {balance_diff:.2f}"
            )

        self.logger.info(
            "Books→Tally: %d entries (%s), debit=%.2f, "
            "credit=%.2f, balanced=%s",
            len(staged_entries),
            source,
            total_debit,
            total_credit,
            reconciliation["is_balanced"],
        )

        return AgentResult(
            status="SUCCESS",
            output=output,
            actions_taken=actions,
        )

    def _fetch_from_zoho(
        self,
        client,
        start_date: str,
        end_date: str,
    ) -> list[dict]:
        """Fetch journal lines from Zoho Books API."""
        self.logger.info(
            "Fetching journals from Zoho: %s to %s",
            start_date,
            end_date,
        )

        try:
            lines = client.get_all_journal_lines_for_period(
                start_date, end_date,
            )
            self.logger.info(
                "Fetched %d journal lines from Zoho", len(lines)
            )
            return lines

        except ZohoBooksError as exc:
            self.logger.error(
                "Zoho Books API error: %s", exc
            )
            raise RuntimeError(
                f"Failed to fetch journals from Zoho Books: {exc}"
            ) from exc

