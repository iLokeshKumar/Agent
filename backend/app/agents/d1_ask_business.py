import uuid
from datetime import datetime, timezone, timedelta

from app.agents.base import BaseAgent, AgentResult
from app.integrations.zoho_books import (
    get_zoho_client,
    ZohoBooksError,
)


class D1AskBusiness(BaseAgent):
    """Ask the Business Agent — D1

    Handles business intelligence queries using Zoho Books:
    - Sales summaries (from invoices)
    - Expense reports (from bills)
    - Outstanding receivables/payables
    - Top customers/vendors
    - Cash flow
    - Profit & loss
    - Period comparisons

    Falls back to placeholder data if Zoho not configured.
    """

    agent_code = "D1"
    agent_name = "Ask the Business"

    def __init__(self):
        super().__init__()

    SUPPORTED_QUERIES = {
        # Zoho Books live queries
        "sales_summary",
        "expense_summary",
        "receivables_aging",
        "payables_aging",
        "top_customers",
        "top_vendors",
        "cash_flow",
        "profit_loss",
        "period_comparison",
        # Mart-based queries (nightly data — no Zoho call)
        "dealer_outstanding",
        "overdue_dealers",
        "dealer_card",
        "sku_position",
        "sku_actions",
        "vendor_scorecard",
        "kpi_snapshot",
        "branch_kpis",
        "installed_base",
        "amc_expiry",
        # Phase 3 mart queries
        "amc_contracts",
        "campaign_analytics",
        "dispatch_status",
        "scm_claims",
        "attrition_risk",
    }

    _MART_QUERIES = {
        "dealer_outstanding",
        "overdue_dealers",
        "dealer_card",
        "sku_position",
        "sku_actions",
        "vendor_scorecard",
        "kpi_snapshot",
        "branch_kpis",
        "installed_base",
        "amc_expiry",
        # Phase 3
        "amc_contracts",
        "campaign_analytics",
        "dispatch_status",
        "scm_claims",
        "attrition_risk",
    }

    def describe(self) -> str:
        return (
            "Business intelligence and management queries. "
            "Generates reports from Zoho Books data."
        )

    def validate(self, payload: dict) -> str | None:
        if not payload:
            return "Payload is empty"

        query_type = payload.get("query_type")
        if not query_type:
            return "'query_type' is required"

        if query_type not in self.SUPPORTED_QUERIES:
            return (
                f"Unknown query_type: '{query_type}'. "
                f"Supported: {', '.join(sorted(self.SUPPORTED_QUERIES))}"
            )

        return None

    def execute(
        self,
        payload: dict,
        task_id: uuid.UUID | None = None,
        requested_by: str | None = None,
    ) -> AgentResult:
        now = datetime.now(timezone.utc)

        query_type = payload["query_type"]
        period = payload.get("period", "this_month")
        start_date = payload.get("start_date")
        end_date = payload.get("end_date")
        filters = payload.get("filters", {})

        # Resolve date range
        start_date, end_date = self._resolve_dates(
            period, start_date, end_date
        )

        # Route mart queries to local DB — no Zoho call needed
        if query_type in self._MART_QUERIES:
            report, source = self._run_mart_query(query_type, payload)
            actions = [
                f"executed_query:{query_type}",
                "generated_report",
                f"source:{source}",
            ]
        else:
            client = get_zoho_client()
            source = "zoho_live" if client.configured else "placeholder"

            generators = {
                "sales_summary": self._report_sales,
                "expense_summary": self._report_expenses,
                "receivables_aging": self._report_receivables,
                "payables_aging": self._report_payables,
                "top_customers": self._report_top_customers,
                "top_vendors": self._report_top_vendors,
                "cash_flow": self._report_cash_flow,
                "profit_loss": self._report_profit_loss,
                "period_comparison": self._report_comparison,
            }

            generator = generators.get(query_type)
            if generator:
                report = generator(client, start_date, end_date, filters)
            else:
                report = {"message": f"Query '{query_type}' not implemented"}

            report["source"] = source
            report["period"] = f"{start_date} to {end_date}"

            actions = [
                f"executed_query:{query_type}",
                "generated_report",
                f"source:{source}",
            ]

        output = {
            "query_type": query_type,
            "period": period,
            "report": report,
            "generated_at": now.isoformat(),
            "requested_by": requested_by,
        }

        self.logger.info(
            "D1 query '%s' for period '%s' — source:%s",
            query_type,
            period,
            source,
        )

        return AgentResult(
            status="SUCCESS",
            output=output,
            actions_taken=actions,
        )

    # ----------------------------------------------------------------
    # Mart query router (nightly data — no Zoho call)
    # ----------------------------------------------------------------

    def _run_mart_query(self, query_type: str, payload: dict) -> tuple[dict, str]:
        """Route a mart query to the appropriate read-tool and return (report, source)."""
        from datetime import date as _date
        from app.core.db import SessionLocal
        from skills.d1_ask_business.read_tools import (
            read_amc_contracts,
            read_amc_expiry,
            read_attrition_risk,
            read_branch_kpis,
            read_campaign_summary,
            read_dealer_card,
            read_dealer_outstanding,
            read_dispatch_status,
            read_installed_base,
            read_kpi,
            read_overdue_dealers,
            read_scm_claims,
            read_sku_actions,
            read_sku_position,
            read_vendor_card,
        )

        entity = payload.get("entity", "YEXIS-EL")
        period_str = payload.get("period_date")
        period = _date.fromisoformat(period_str) if period_str else _date.today()

        db = SessionLocal()
        try:
            if query_type == "dealer_outstanding":
                data = read_dealer_outstanding(
                    db, branch=payload.get("branch"), top_n=payload.get("top_n", 20)
                )
                return {"dealers": data, "source": "dealer_mart"}, "nightly_mart"

            if query_type == "overdue_dealers":
                data = read_overdue_dealers(
                    db, branch=payload.get("branch"), top_n=payload.get("top_n", 10)
                )
                return {"dealers": data, "source": "dealer_mart"}, "nightly_mart"

            if query_type == "dealer_card":
                dealer_ref = payload.get("dealer_ref", "")
                data = read_dealer_card(db, dealer_ref)
                return {"dealer": data, "source": "dealer_mart"}, "nightly_mart"

            if query_type == "sku_position":
                data = read_sku_position(db, sku_ref=payload.get("sku_ref", ""), period=period)
                return {"positions": data, "source": "sku_health"}, "nightly_mart"

            if query_type == "sku_actions":
                data = read_sku_actions(
                    db, period=period, action_filter=payload.get("action_filter")
                )
                return {"skus": data, "source": "sku_health"}, "nightly_mart"

            if query_type == "vendor_scorecard":
                data = read_vendor_card(db, vendor_ref=payload.get("vendor_ref", ""), period=period)
                return {"vendor": data, "source": "vendor_scorecard"}, "nightly_mart"

            if query_type == "kpi_snapshot":
                data = read_kpi(
                    db,
                    kpi_id=payload.get("kpi_id", "dso_days"),
                    period=period,
                    entity=entity,
                    branch=payload.get("branch", "*"),
                )
                return {"kpi": data, "source": "kpi_snapshot"}, "nightly_mart"

            if query_type == "branch_kpis":
                data = read_branch_kpis(db, period=period, entity=entity)
                return {"branches": data, "source": "kpi_snapshot"}, "nightly_mart"

            if query_type == "installed_base":
                data = read_installed_base(
                    db,
                    customer_ref=payload.get("customer_ref"),
                    product_ref=payload.get("product_ref"),
                )
                return {"assets": data, "source": "installed_base"}, "nightly_mart"

            if query_type == "amc_expiry":
                data = read_amc_expiry(db, within_days=payload.get("within_days", 90))
                return {"expiring_amcs": data, "source": "installed_base"}, "nightly_mart"

            if query_type == "amc_contracts":
                data = read_amc_contracts(
                    db,
                    entity=entity,
                    status=payload.get("status"),
                    within_days=payload.get("within_days"),
                )
                return {"amc_contracts": data, "source": "amc_contract"}, "nightly_mart"

            if query_type == "campaign_analytics":
                data = read_campaign_summary(
                    db,
                    entity=entity,
                    status=payload.get("status"),
                    top_n=payload.get("top_n", 10),
                )
                return {"campaigns": data, "source": "campaign"}, "nightly_mart"

            if query_type == "dispatch_status":
                data = read_dispatch_status(
                    db,
                    entity=entity,
                    status=payload.get("status"),
                    overdue_only=payload.get("overdue_only", False),
                )
                return {"dispatches": data, "source": "dispatch_record"}, "nightly_mart"

            if query_type == "scm_claims":
                data = read_scm_claims(
                    db,
                    entity=entity,
                    status=payload.get("status"),
                    claim_type=payload.get("claim_type"),
                )
                return {"claims": data, "source": "scm_claim"}, "nightly_mart"

            if query_type == "attrition_risk":
                data = read_attrition_risk(db, entity=entity, tier=payload.get("tier"))
                return {"employees": data, "source": "hr_employee"}, "nightly_mart"

            return {"message": f"Mart query '{query_type}' not routed"}, "nightly_mart"

        finally:
            db.close()

    # ----------------------------------------------------------------
    # Date helpers
    # ----------------------------------------------------------------

    def _resolve_dates(
        self,
        period: str,
        start_date: str | None,
        end_date: str | None,
    ) -> tuple[str, str]:
        """Resolve period shorthand to concrete dates."""
        now = datetime.now(timezone.utc)

        if start_date and end_date:
            return start_date, end_date

        period_map = {
            "today": (
                now.strftime("%Y-%m-%d"),
                now.strftime("%Y-%m-%d"),
            ),
            "yesterday": (
                (now - timedelta(days=1)).strftime("%Y-%m-%d"),
                (now - timedelta(days=1)).strftime("%Y-%m-%d"),
            ),
            "this_week": (
                (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d"),
                now.strftime("%Y-%m-%d"),
            ),
            "this_month": (
                now.replace(day=1).strftime("%Y-%m-%d"),
                now.strftime("%Y-%m-%d"),
            ),
            "last_month": (
                (now.replace(day=1) - timedelta(days=1)).replace(day=1).strftime("%Y-%m-%d"),
                (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m-%d"),
            ),
            "this_quarter": (
                now.replace(
                    month=((now.month - 1) // 3) * 3 + 1, day=1
                ).strftime("%Y-%m-%d"),
                now.strftime("%Y-%m-%d"),
            ),
            "this_year": (
                now.replace(month=1, day=1).strftime("%Y-%m-%d"),
                now.strftime("%Y-%m-%d"),
            ),
        }

        if period in period_map:
            return period_map[period]

        # Default: this month
        return (
            now.replace(day=1).strftime("%Y-%m-%d"),
            now.strftime("%Y-%m-%d"),
        )

    # ----------------------------------------------------------------
    # Report generators — live data when Zoho configured
    # ----------------------------------------------------------------

    def _report_sales(
        self, client, start_date, end_date, filters
    ) -> dict:
        """Sales summary from Zoho invoices."""
        if not client or not client.configured:
            return self._placeholder("sales_summary")

        try:
            invoices = client.list_invoices(
                start_date=start_date,
                end_date=end_date,
            )
        except ZohoBooksError as exc:
            self.logger.error("Failed to fetch invoices: %s", exc)
            return {"type": "sales_summary", "error": str(exc)}

        total_sales = 0.0
        invoice_count = 0
        product_totals: dict[str, float] = {}

        for inv in invoices:
            total = float(inv.get("total", 0) or 0)
            total_sales += total
            invoice_count += 1

            for line in inv.get("line_items", []):
                item_name = line.get("name", "Unknown")
                line_total = float(line.get("item_total", 0) or 0)
                product_totals[item_name] = (
                    product_totals.get(item_name, 0) + line_total
                )

        # Sort products by revenue
        top_products = sorted(
            product_totals.items(), key=lambda x: x[1], reverse=True
        )[:10]

        return {
            "type": "sales_summary",
            "total_sales": round(total_sales, 2),
            "invoice_count": invoice_count,
            "average_invoice_value": (
                round(total_sales / invoice_count, 2)
                if invoice_count > 0
                else 0
            ),
            "top_products": [
                {"name": name, "revenue": round(rev, 2)}
                for name, rev in top_products
            ],
        }

    def _report_expenses(
        self, client, start_date, end_date, filters
    ) -> dict:
        """Expense summary from Zoho bills."""
        if not client or not client.configured:
            return self._placeholder("expense_summary")

        try:
            bills = client.list_bills(
                start_date=start_date,
                end_date=end_date,
            )
        except ZohoBooksError as exc:
            self.logger.error("Failed to fetch bills: %s", exc)
            return {"type": "expense_summary", "error": str(exc)}

        total_expenses = 0.0
        bill_count = 0
        vendor_totals: dict[str, float] = {}

        for bill in bills:
            total = float(bill.get("total", 0) or 0)
            total_expenses += total
            bill_count += 1

            vendor_name = bill.get("vendor_name", "Unknown")
            vendor_totals[vendor_name] = (
                vendor_totals.get(vendor_name, 0) + total
            )

        return {
            "type": "expense_summary",
            "total_expenses": round(total_expenses, 2),
            "bill_count": bill_count,
            "by_vendor": {
                k: round(v, 2)
                for k, v in sorted(
                    vendor_totals.items(),
                    key=lambda x: x[1],
                    reverse=True,
                )[:20]
            },
        }

    def _report_receivables(
        self, client, start_date, end_date, filters
    ) -> dict:
        """Receivables aging from unpaid invoices."""
        if not client or not client.configured:
            return self._placeholder("receivables_aging")

        try:
            unpaid = client.list_invoices(status="unpaid")
        except ZohoBooksError as exc:
            self.logger.error("Failed to fetch receivables: %s", exc)
            return {"type": "receivables_aging", "error": str(exc)}

        aging = {
            "current": {"count": 0, "total": 0.0},
            "1_30_days": {"count": 0, "total": 0.0},
            "31_60_days": {"count": 0, "total": 0.0},
            "61_90_days": {"count": 0, "total": 0.0},
            "over_90_days": {"count": 0, "total": 0.0},
        }

        now = datetime.now(timezone.utc)
        total_outstanding = 0.0

        for inv in unpaid:
            amount = float(inv.get("balance", 0) or 0)
            total_outstanding += amount

            due_str = inv.get("due_date", "")
            try:
                due_date = datetime.strptime(due_str, "%Y-%m-%d")
            except (ValueError, TypeError):
                due_date = now

            days_overdue = max(0, (now - due_date).days)

            if days_overdue <= 0:
                bucket = "current"
            elif days_overdue <= 30:
                bucket = "1_30_days"
            elif days_overdue <= 60:
                bucket = "31_60_days"
            elif days_overdue <= 90:
                bucket = "61_90_days"
            else:
                bucket = "over_90_days"

            aging[bucket]["count"] += 1
            aging[bucket]["total"] += amount

        return {
            "type": "receivables_aging",
            "total_outstanding": round(total_outstanding, 2),
            "invoice_count": len(unpaid),
            "aging": {
                k: {"count": v["count"], "total": round(v["total"], 2)}
                for k, v in aging.items()
            },
        }

    def _report_payables(
        self, client, start_date, end_date, filters
    ) -> dict:
        """Payables aging from unpaid bills."""
        if not client or not client.configured:
            return self._placeholder("payables_aging")

        try:
            unpaid = client.list_bills(status="unpaid")
        except ZohoBooksError as exc:
            self.logger.error("Failed to fetch payables: %s", exc)
            return {"type": "payables_aging", "error": str(exc)}

        aging = {
            "current": {"count": 0, "total": 0.0},
            "1_30_days": {"count": 0, "total": 0.0},
            "31_60_days": {"count": 0, "total": 0.0},
            "61_90_days": {"count": 0, "total": 0.0},
            "over_90_days": {"count": 0, "total": 0.0},
        }

        now = datetime.now(timezone.utc)
        total_outstanding = 0.0

        for bill in unpaid:
            amount = float(bill.get("balance", 0) or 0)
            total_outstanding += amount

            due_str = bill.get("due_date", "")
            try:
                due_date = datetime.strptime(due_str, "%Y-%m-%d")
            except (ValueError, TypeError):
                due_date = now

            days_overdue = max(0, (now - due_date).days)

            if days_overdue <= 0:
                bucket = "current"
            elif days_overdue <= 30:
                bucket = "1_30_days"
            elif days_overdue <= 60:
                bucket = "31_60_days"
            elif days_overdue <= 90:
                bucket = "61_90_days"
            else:
                bucket = "over_90_days"

            aging[bucket]["count"] += 1
            aging[bucket]["total"] += amount

        return {
            "type": "payables_aging",
            "total_outstanding": round(total_outstanding, 2),
            "bill_count": len(unpaid),
            "aging": {
                k: {"count": v["count"], "total": round(v["total"], 2)}
                for k, v in aging.items()
            },
        }

    def _report_top_customers(
        self, client, start_date, end_date, filters
    ) -> dict:
        """Top customers by revenue."""
        if not client or not client.configured:
            return self._placeholder("top_customers")

        try:
            invoices = client.list_invoices(
                start_date=start_date,
                end_date=end_date,
            )
        except ZohoBooksError as exc:
            return {"type": "top_customers", "error": str(exc)}

        customer_revenue: dict[str, dict] = {}

        for inv in invoices:
            name = inv.get("customer_name", "Unknown")
            total = float(inv.get("total", 0) or 0)

            if name not in customer_revenue:
                customer_revenue[name] = {
                    "name": name,
                    "revenue": 0.0,
                    "invoice_count": 0,
                }
            customer_revenue[name]["revenue"] += total
            customer_revenue[name]["invoice_count"] += 1

        ranked = sorted(
            customer_revenue.values(),
            key=lambda x: x["revenue"],
            reverse=True,
        )[:20]

        return {
            "type": "top_customers",
            "customers": [
                {
                    "name": c["name"],
                    "revenue": round(c["revenue"], 2),
                    "invoice_count": c["invoice_count"],
                }
                for c in ranked
            ],
        }

    def _report_top_vendors(
        self, client, start_date, end_date, filters
    ) -> dict:
        """Top vendors by spend."""
        if not client or not client.configured:
            return self._placeholder("top_vendors")

        try:
            bills = client.list_bills(
                start_date=start_date,
                end_date=end_date,
            )
        except ZohoBooksError as exc:
            return {"type": "top_vendors", "error": str(exc)}

        vendor_spend: dict[str, dict] = {}

        for bill in bills:
            name = bill.get("vendor_name", "Unknown")
            total = float(bill.get("total", 0) or 0)

            if name not in vendor_spend:
                vendor_spend[name] = {
                    "name": name,
                    "spend": 0.0,
                    "bill_count": 0,
                }
            vendor_spend[name]["spend"] += total
            vendor_spend[name]["bill_count"] += 1

        ranked = sorted(
            vendor_spend.values(),
            key=lambda x: x["spend"],
            reverse=True,
        )[:20]

        return {
            "type": "top_vendors",
            "vendors": [
                {
                    "name": v["name"],
                    "spend": round(v["spend"], 2),
                    "bill_count": v["bill_count"],
                }
                for v in ranked
            ],
        }

    def _report_cash_flow(
        self, client, start_date, end_date, filters
    ) -> dict:
        """Cash flow from invoices (inflow) and bills (outflow)."""
        if not client or not client.configured:
            return self._placeholder("cash_flow")

        inflow = 0.0
        outflow = 0.0

        try:
            invoices = client.list_invoices(
                start_date=start_date,
                end_date=end_date,
            )
            for inv in invoices:
                status = inv.get("status", "")
                if status == "paid":
                    inflow += float(inv.get("total", 0) or 0)
        except ZohoBooksError as exc:
            return {"type": "cash_flow", "error": str(exc)}

        try:
            bills = client.list_bills(
                start_date=start_date,
                end_date=end_date,
            )
            for bill in bills:
                status = bill.get("status", "")
                if status == "paid":
                    outflow += float(bill.get("total", 0) or 0)
        except ZohoBooksError as exc:
            return {"type": "cash_flow", "error": str(exc)}

        return {
            "type": "cash_flow",
            "inflow": round(inflow, 2),
            "outflow": round(outflow, 2),
            "net": round(inflow - outflow, 2),
        }

    def _report_profit_loss(
        self, client, start_date, end_date, filters
    ) -> dict:
        """Profit & loss from revenue (invoices) and expenses (bills)."""
        if not client or not client.configured:
            return self._placeholder("profit_loss")

        revenue = 0.0
        expenses = 0.0

        try:
            invoices = client.list_invoices(
                start_date=start_date,
                end_date=end_date,
            )
            for inv in invoices:
                revenue += float(inv.get("total", 0) or 0)
        except ZohoBooksError as exc:
            return {"type": "profit_loss", "error": str(exc)}

        try:
            bills = client.list_bills(
                start_date=start_date,
                end_date=end_date,
            )
            for bill in bills:
                expenses += float(bill.get("total", 0) or 0)
        except ZohoBooksError as exc:
            return {"type": "profit_loss", "error": str(exc)}

        return {
            "type": "profit_loss",
            "revenue": round(revenue, 2),
            "expenses": round(expenses, 2),
            "net_profit": round(revenue - expenses, 2),
            "margin": (
                round((revenue - expenses) / revenue * 100, 1)
                if revenue > 0
                else 0
            ),
        }

    def _report_comparison(
        self, client, start_date, end_date, filters
    ) -> dict:
        """Period comparison: current vs previous same-length period."""
        if not client or not client.configured:
            return self._placeholder("period_comparison")

        # Calculate previous period of same length
        try:
            s = datetime.strptime(start_date, "%Y-%m-%d")
            e = datetime.strptime(end_date, "%Y-%m-%d")
            length = (e - s).days
            prev_end = s - timedelta(days=1)
            prev_start = prev_end - timedelta(days=length)
            prev_start_str = prev_start.strftime("%Y-%m-%d")
            prev_end_str = prev_end.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            return self._placeholder("period_comparison")

        # Current period
        current = self._get_period_summary(
            client, start_date, end_date
        )
        # Previous period
        previous = self._get_period_summary(
            client, prev_start_str, prev_end_str
        )

        # Variance
        variance = {}
        for key in current:
            if isinstance(current[key], (int, float)) and key in previous:
                diff = current[key] - previous[key]
                pct = (
                    round(diff / previous[key] * 100, 1)
                    if previous[key] != 0
                    else 0
                )
                variance[key] = {"change": round(diff, 2), "pct": pct}

        return {
            "type": "period_comparison",
            "current_period": current,
            "previous_period": previous,
            "variance": variance,
            "period_label": f"{start_date} to {end_date}",
            "previous_label": f"{prev_start_str} to {prev_end_str}",
        }

    def _get_period_summary(
        self, client, start_date: str, end_date: str
    ) -> dict:
        """Get revenue and expense summary for a period."""
        revenue = 0.0
        invoice_count = 0
        expenses = 0.0
        bill_count = 0

        try:
            for inv in client.list_invoices(
                start_date=start_date, end_date=end_date
            ):
                revenue += float(inv.get("total", 0) or 0)
                invoice_count += 1
        except ZohoBooksError:
            pass

        try:
            for bill in client.list_bills(
                start_date=start_date, end_date=end_date
            ):
                expenses += float(bill.get("total", 0) or 0)
                bill_count += 1
        except ZohoBooksError:
            pass

        return {
            "revenue": round(revenue, 2),
            "invoice_count": invoice_count,
            "expenses": round(expenses, 2),
            "bill_count": bill_count,
            "net_profit": round(revenue - expenses, 2),
        }

    # ----------------------------------------------------------------
    # Placeholder (when Zoho not configured)
    # ----------------------------------------------------------------

    def _placeholder(self, query_type: str) -> dict:
        """Return placeholder data when Zoho is not configured."""
        return {
            "type": query_type,
            "note": (
                "Zoho Books not configured. "
                "Set ZOHO_* env vars for live data."
            ),
        }
