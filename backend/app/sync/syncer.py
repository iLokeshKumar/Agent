"""Zoho Books data syncer.

Periodically pulls data from Zoho Books API and stores
it in the local JSON cache for fast agent access.

Sync targets:
  - invoices     (for D1: sales, receivables)
  - bills        (for D1: expenses, payables)
  - contacts     (for D1: top customers/vendors)
  - journals     (for F3: Books to Tally)
  - chart_of_accounts (for F3: account mapping)
"""

import logging
from datetime import datetime, timezone, timedelta

from app.integrations.zoho_books import (
    get_zoho_client,
    ZohoBooksError,
)
from app.sync.cache import SyncCache


logger = logging.getLogger(__name__)

# What to sync and their cache keys
SYNC_TARGETS = [
    "invoices",
    "bills",
    "contacts",
    "journals",
    "chart_of_accounts",
    "invoice_line_items_13wk",
]


class ZohoSyncer:
    """Pulls data from Zoho Books and caches it locally."""

    def __init__(self, cache: SyncCache | None = None):
        self.cache = cache or SyncCache()
        self.last_sync: dict[str, str] = {}
        self.errors: dict[str, str] = {}

    def sync_all(self) -> dict:
        """Run a full sync of all targets.

        Returns a summary of what was synced.
        """
        client = get_zoho_client()

        if not client.configured:
            logger.warning("Zoho not configured — skipping sync")
            return {
                "status": "skipped",
                "reason": "Zoho Books not configured",
            }

        try:
            client.ensure_authenticated()
        except ZohoBooksError as exc:
            logger.error("Zoho sync skipped because authentication failed: %s", exc)
            results = {
                target: {"status": "error", "error": str(exc)}
                for target in SYNC_TARGETS
            }
            self.errors.update({target: str(exc) for target in SYNC_TARGETS})
            self._write_sync_status(results)
            return {
                "status": "error",
                "error": str(exc),
                "results": results,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        logger.info("Starting full Zoho data sync")
        results = {}

        for target in SYNC_TARGETS:
            try:
                count = self._sync_target(target, client)
                results[target] = {
                    "status": "ok",
                    "count": count,
                }
                self.last_sync[target] = datetime.now(
                    timezone.utc
                ).isoformat()
                self.errors.pop(target, None)

            except ZohoBooksError as exc:
                logger.error(
                    "Sync failed for %s: %s", target, exc
                )
                results[target] = {
                    "status": "error",
                    "error": str(exc),
                }
                self.errors[target] = str(exc)

            except Exception as exc:
                logger.exception("Sync failed for %s", target)
                results[target] = {
                    "status": "error",
                    "error": str(exc),
                }
                self.errors[target] = str(exc)

        # Write sync status
        self._write_sync_status(results)

        logger.info("Full sync completed: %s", results)

        return {
            "status": "completed",
            "results": results,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def sync_target(self, target: str) -> dict:
        """Sync a single target."""
        client = get_zoho_client()

        if not client.configured:
            return {"status": "skipped", "reason": "Not configured"}

        try:
            count = self._sync_target(target, client)
            self.last_sync[target] = datetime.now(
                timezone.utc
            ).isoformat()
            self.errors.pop(target, None)

            return {"status": "ok", "count": count}

        except Exception as exc:
            logger.exception("Sync failed for %s", target)
            self.errors[target] = str(exc)
            return {"status": "error", "error": str(exc)}

    # ----------------------------------------------------------------
    # Target-specific sync methods
    # ----------------------------------------------------------------

    def _sync_target(self, target: str, client) -> int:
        """Sync a specific data type. Returns record count."""
        dispatch = {
            "invoices": self._sync_invoices,
            "bills": self._sync_bills,
            "contacts": self._sync_contacts,
            "journals": self._sync_journals,
            "chart_of_accounts": self._sync_chart_of_accounts,
            "invoice_line_items_13wk": self._sync_invoice_line_items_13wk,
        }

        syncer = dispatch.get(target)
        if syncer is None:
            raise ValueError(f"Unknown sync target: {target}")

        return syncer(client)

    def _sync_invoices(self, client) -> int:
        """Sync all invoices (no date filter — full cache)."""
        logger.info("Syncing invoices from Zoho")

        invoices = client.list_invoices()
        # Strip heavy nested fields to keep cache lean
        lean = [self._lean_invoice(inv) for inv in invoices]

        self.cache.put("invoices", lean)
        return len(lean)

    def _sync_bills(self, client) -> int:
        """Sync all bills."""
        logger.info("Syncing bills from Zoho")

        bills = client.list_bills()
        lean = [self._lean_bill(bill) for bill in bills]

        self.cache.put("bills", lean)
        return len(lean)

    def _sync_contacts(self, client) -> int:
        """Sync all contacts."""
        logger.info("Syncing contacts from Zoho")

        contacts = client.list_contacts()
        lean = [self._lean_contact(c) for c in contacts]

        self.cache.put("contacts", lean)
        return len(lean)

    def _sync_journals(self, client) -> int:
        """Sync journals for current month."""
        now = datetime.now(timezone.utc)
        start = now.replace(day=1).strftime("%Y-%m-%d")
        end = now.strftime("%Y-%m-%d")

        logger.info(
            "Syncing journals from Zoho: %s to %s", start, end
        )

        journals = client.list_journals(start, end)

        # Also fetch lines for each journal
        enriched = []
        for j in journals:
            jid = j.get("journal_id", "")
            try:
                lines = client.list_journal_lines(jid)
                j["lines"] = lines
            except ZohoBooksError:
                j["lines"] = []
            enriched.append(j)

        self.cache.put("journals", enriched)
        return len(enriched)

    def _sync_chart_of_accounts(self, client) -> int:
        """Sync chart of accounts."""
        logger.info("Syncing chart of accounts from Zoho")

        accounts = client.list_chart_of_accounts()
        self.cache.put("chart_of_accounts", accounts)
        return len(accounts)

    def _sync_invoice_line_items_13wk(self, client) -> int:
        """Sync invoice line items for the last 13 weeks (91 days).

        Zoho's list-invoices response includes line_items[] on each invoice.
        We preserve only the fields needed for offtake computation, keeping
        the cache compact. This is the feed for DealerMart.offtake_13wk_json.
        """
        now = datetime.now(timezone.utc)
        start_date = (now - timedelta(weeks=13)).strftime("%Y-%m-%d")
        end_date = now.strftime("%Y-%m-%d")

        logger.info(
            "Syncing invoice line items (13wk) from Zoho: %s to %s",
            start_date, end_date,
        )

        raw_invoices = client.list_invoices(
            start_date=start_date,
            end_date=end_date,
            status="sent",
        )

        records = []
        for inv in raw_invoices:
            line_items = inv.get("line_items") or []
            if not line_items:
                continue
            records.append({
                "invoice_id": inv.get("invoice_id", ""),
                "date": inv.get("date", ""),
                "customer_id": inv.get("customer_id", ""),
                "customer_name": inv.get("customer_name", ""),
                "lines": [
                    {
                        "item_id": li.get("item_id", ""),
                        "name": li.get("name", ""),
                        "quantity": float(li.get("quantity") or 0),
                        "rate": float(li.get("rate") or 0),
                        "item_total": float(li.get("item_total") or 0),
                    }
                    for li in line_items
                ],
            })

        self.cache.put("invoice_line_items_13wk", records)
        return len(records)

    # ----------------------------------------------------------------
    # Lean data extractors (strip nested objects)
    # ----------------------------------------------------------------

    @staticmethod
    def _lean_invoice(inv: dict) -> dict:
        """Extract key fields from invoice, strip line_items etc."""
        return {
            "invoice_id": inv.get("invoice_id", ""),
            "invoice_number": inv.get("invoice_number", ""),
            "date": inv.get("date", ""),
            "due_date": inv.get("due_date", ""),
            "customer_name": inv.get("customer_name", ""),
            "customer_id": inv.get("customer_id", ""),
            "total": inv.get("total", 0),
            "balance": inv.get("balance", 0),
            "status": inv.get("status", ""),
            "payment_terms": inv.get("payment_terms", ""),
        }

    @staticmethod
    def _lean_bill(bill: dict) -> dict:
        """Extract key fields from bill."""
        return {
            "bill_id": bill.get("bill_id", ""),
            "bill_number": bill.get("bill_number", ""),
            "date": bill.get("date", ""),
            "due_date": bill.get("due_date", ""),
            "vendor_name": bill.get("vendor_name", ""),
            "vendor_id": bill.get("vendor_id", ""),
            "total": bill.get("total", 0),
            "balance": bill.get("balance", 0),
            "status": bill.get("status", ""),
        }

    @staticmethod
    def _lean_contact(contact: dict) -> dict:
        """Extract key fields from contact."""
        return {
            "contact_id": contact.get("contact_id", ""),
            "contact_name": contact.get("contact_name", ""),
            "company_name": contact.get("company_name", ""),
            "contact_type": contact.get("contact_type", ""),
            "email": contact.get("email", ""),
            "phone": contact.get("phone", ""),
            "balance": contact.get("balance", 0),
            "receivables": contact.get("receivables", 0),
            "payables": contact.get("payables", 0),
        }

    # ----------------------------------------------------------------
    # Sync status persistence
    # ----------------------------------------------------------------

    def _write_sync_status(self, results: dict):
        """Write sync status to cache."""
        status = {
            "last_full_sync": datetime.now(timezone.utc).isoformat(),
            "results": results,
            "per_target": self.last_sync,
            "errors": self.errors,
        }
        self.cache.put("_sync_status", [status])

    def get_sync_status(self) -> dict:
        """Get current sync status."""
        cached = self.cache.get("_sync_status")
        meta = self.cache.get_all_metadata()

        last_status = {}
        if cached and cached.get("data"):
            last_status = cached["data"][0]

        return {
            "last_full_sync": last_status.get("last_full_sync"),
            "per_target_sync": last_status.get("per_target", {}),
            "last_results": last_status.get("results", {}),
            "errors": last_status.get("errors", {}),
            "cache_metadata": {
                k: v for k, v in meta.items() if not k.startswith("_")
            },
        }
