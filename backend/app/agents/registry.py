from app.agents.base import BaseAgent


_registry: dict[str, BaseAgent] = {}


def register(agent_code: str, agent: BaseAgent):
    """Register an agent instance by its code."""
    _registry[agent_code] = agent


def get_agent(agent_code: str) -> BaseAgent | None:
    """Get a registered agent by code."""
    return _registry.get(agent_code)


def list_agents() -> dict[str, BaseAgent]:
    """Return all registered agents."""
    return dict(_registry)


# --- Import and register all agents ---
# This triggers registration on first import

from app.agents.p1_purchase_intake import P1PurchaseIntake  # noqa: E402
from app.agents.p2_sourcing_rfq import P2SourcingRfq  # noqa: E402
from app.agents.p3_expediting import P3Expediting  # noqa: E402
from app.agents.p4_invoice_matching import P4InvoiceMatching  # noqa: E402
from app.agents.s1_grn_serialisation import S1GRNSerialisation  # noqa: E402
from app.agents.s2_stock_health import S2StockHealth  # noqa: E402
from app.agents.s3_reorder import S3Reorder  # noqa: E402
from app.agents.f1_collections import F1Collections  # noqa: E402
from app.agents.f2_scheme_claims import F2SchemeClaims  # noqa: E402
from app.agents.f3_books_to_tally import F3BooksToTally  # noqa: E402
from app.agents.d1_ask_business import D1AskBusiness  # noqa: E402
from app.agents.v1_service_desk import V1ServiceDesk  # noqa: E402
from app.agents.f4_interentity_recon import F4InterentityRecon  # noqa: E402
from app.agents.f5_cash_forecast import F5CashForecast  # noqa: E402
from app.agents.h1_recruitment import H1Recruitment  # noqa: E402
from app.agents.h2_onboarding import H2Onboarding  # noqa: E402
from app.agents.h3_attrition_risk import H3AttritionRisk  # noqa: E402
from app.agents.h4_payroll_check import H4PayrollCheck  # noqa: E402
from app.agents.s4_physical_verification import S4PhysicalVerification  # noqa: E402
from app.agents.v2_amc_engine import V2AmcEngine  # noqa: E402
from app.agents.c1_indent_planning import C1IndentPlanning  # noqa: E402
from app.agents.c2_dispatch_tracker import C2DispatchTracker  # noqa: E402
from app.agents.c3_claims_tracker import C3ClaimsTracker  # noqa: E402
from app.agents.k1_campaign_brief import K1CampaignBrief  # noqa: E402
from app.agents.k2_audience_builder import K2AudienceBuilder  # noqa: E402
from app.agents.k3_broadcast import K3Broadcast  # noqa: E402
from app.agents.k4_dealer_promo import K4DealerPromo  # noqa: E402
from app.agents.k5_response_tracker import K5ResponseTracker  # noqa: E402
from app.agents.k6_campaign_analytics import K6CampaignAnalytics  # noqa: E402
from app.agents.m1_ism_command import M1IsmCommand  # noqa: E402
from app.agents.m2_gsm_command import M2GsmCommand  # noqa: E402

register("P1", P1PurchaseIntake())
register("P2", P2SourcingRfq())
register("P3", P3Expediting())
register("P4", P4InvoiceMatching())
register("S1", S1GRNSerialisation())
register("S2", S2StockHealth())
register("S3", S3Reorder())
register("S4", S4PhysicalVerification())
register("F1", F1Collections())
register("F2", F2SchemeClaims())
register("F3", F3BooksToTally())
register("F4", F4InterentityRecon())
register("F5", F5CashForecast())
register("H1", H1Recruitment())
register("H2", H2Onboarding())
register("H3", H3AttritionRisk())
register("H4", H4PayrollCheck())
register("V1", V1ServiceDesk())
register("V2", V2AmcEngine())
register("C1", C1IndentPlanning())
register("C2", C2DispatchTracker())
register("C3", C3ClaimsTracker())
register("K1", K1CampaignBrief())
register("K2", K2AudienceBuilder())
register("K3", K3Broadcast())
register("K4", K4DealerPromo())
register("K5", K5ResponseTracker())
register("K6", K6CampaignAnalytics())
register("M1", M1IsmCommand())
register("M2", M2GsmCommand())
register("D1", D1AskBusiness())
