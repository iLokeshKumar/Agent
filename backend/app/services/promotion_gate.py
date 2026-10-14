"""Golden-set runner & promotion gate — WP-7 assurance tooling.

Executes a Skill's golden cases (``tests/golden/<agent>/cases.json``)
against the live agent implementation and renders the machine-generated
promotion-gate report the Definition of Done requires:

  - Golden set: 100% hard-assertion pass (no partial credit — a case
    with any failed assertion fails whole)
  - Red-team pack coverage: every requested lane's agent code must
    appear in ``tests/redteam/test_redteam_pack.py``
  - Sign-off + rollback rehearsal recorded in ``backend/config/gates/
    <agent>.json`` by ops

A lane cannot be promoted (A1 → A2, or dry_run → supervised-live)
until ``gate_report()`` returns ``approved``. Ops trigger a run via
``python -m app.services.promotion_gate <AGENT_DIR>`` or
``POST /promotion/<agent>/gate-report``.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]
GOLDEN_DIR = BACKEND_DIR / "tests" / "golden"
REDTEAM_FILE = BACKEND_DIR / "tests" / "redteam" / "test_redteam_pack.py"
GATES_DIR = BACKEND_DIR / "config" / "gates"

# Skill folder → agent class path + optional constructor kwargs.
AGENT_REGISTRY: dict[str, dict] = {
    "p1_purchase_intake": {
        "module": "app.agents.p1_purchase_intake",
        "class": "P1PurchaseIntake",
        "construct": {"gateway": "MAGIC_MOCK"},
    },
    "p2_sourcing_rfq": {
        "module": "app.agents.p2_sourcing_rfq",
        "class": "P2SourcingRfq",
        "seam": ("document_session", "null_session"),
    },
    "p3_expediting": {
        "module": "app.agents.p3_expediting",
        "class": "P3Expediting",
    },
    "f1_collections": {
        "module": "app.agents.f1_collections",
        "class": "F1Collections",
    },
    "f2_scheme_claims": {
        "module": "app.agents.f2_scheme_claims",
        "class": "F2SchemeClaims",
    },
    "p4_invoice_matching": {
        "module": "app.agents.p4_invoice_matching",
        "class": "P4InvoiceMatching",
        "seam": ("_golden", "p4_golden_fixture"),
    },
    "s1_grn_serialisation": {
        "module": "app.agents.s1_grn_serialisation",
        "class": "S1GRNSerialisation",
        "seam": ("_golden", "s1_golden_fixture"),
    },
    "f3_books_to_tally": {
        "module": "app.agents.f3_books_to_tally",
        "class": "F3BooksToTally",
    },
    "d1_ask_business": {
        "module": "app.agents.d1_ask_business",
        "class": "D1AskBusiness",
    },
    "v1_service_desk": {
        "module": "app.agents.v1_service_desk",
        "class": "V1ServiceDesk",
    },
    "s2_stock_health": {
        "module": "app.agents.s2_stock_health",
        "class": "S2StockHealth",
    },
    "s3_reorder": {
        "module": "app.agents.s3_reorder",
        "class": "S3Reorder",
    },
    "f4_interentity_recon": {
        "module": "app.agents.f4_interentity_recon",
        "class": "F4InterentityRecon",
    },
    "f5_cash_forecast": {
        "module": "app.agents.f5_cash_forecast",
        "class": "F5CashForecast",
    },
    "h1_recruitment": {
        "module": "app.agents.h1_recruitment",
        "class": "H1Recruitment",
    },
    "h2_onboarding": {
        "module": "app.agents.h2_onboarding",
        "class": "H2Onboarding",
    },
    "h3_attrition_risk": {
        "module": "app.agents.h3_attrition_risk",
        "class": "H3AttritionRisk",
    },
    "h4_payroll_check": {
        "module": "app.agents.h4_payroll_check",
        "class": "H4PayrollCheck",
    },
    "s4_physical_verification": {
        "module": "app.agents.s4_physical_verification",
        "class": "S4PhysicalVerification",
    },
    "v2_amc_engine": {
        "module": "app.agents.v2_amc_engine",
        "class": "V2AmcEngine",
    },
    "c1_indent_planning": {
        "module": "app.agents.c1_indent_planning",
        "class": "C1IndentPlanning",
    },
    "c2_dispatch_tracker": {
        "module": "app.agents.c2_dispatch_tracker",
        "class": "C2DispatchTracker",
    },
    "c3_claims_tracker": {
        "module": "app.agents.c3_claims_tracker",
        "class": "C3ClaimsTracker",
    },
    "k1_campaign_brief": {
        "module": "app.agents.k1_campaign_brief",
        "class": "K1CampaignBrief",
    },
    "k2_audience_builder": {
        "module": "app.agents.k2_audience_builder",
        "class": "K2AudienceBuilder",
    },
    "k3_broadcast": {
        "module": "app.agents.k3_broadcast",
        "class": "K3Broadcast",
    },
    "k4_dealer_promo": {
        "module": "app.agents.k4_dealer_promo",
        "class": "K4DealerPromo",
    },
    "k5_response_tracker": {
        "module": "app.agents.k5_response_tracker",
        "class": "K5ResponseTracker",
    },
    "k6_campaign_analytics": {
        "module": "app.agents.k6_campaign_analytics",
        "class": "K6CampaignAnalytics",
    },
    "m1_ism_command": {
        "module": "app.agents.m1_ism_command",
        "class": "M1IsmCommand",
    },
    "m2_gsm_command": {
        "module": "app.agents.m2_gsm_command",
        "class": "M2GsmCommand",
    },
}


# Agent code → skill/golden folder. The HTTP surface speaks codes
# ("S2"); the runner speaks folder names ("s2_stock_health").
AGENT_CODE_TO_DIR: dict[str, str] = {
    "P1": "p1_purchase_intake",
    "P2": "p2_sourcing_rfq",
    "P3": "p3_expediting",
    "F1": "f1_collections",
    "F2": "f2_scheme_claims",
    "V1": "v1_service_desk",
    "P4": "p4_invoice_matching",
    "S1": "s1_grn_serialisation",
    "F3": "f3_books_to_tally",
    "D1": "d1_ask_business",
    "S2": "s2_stock_health",
    "S3": "s3_reorder",
    "F4": "f4_interentity_recon",
    "F5": "f5_cash_forecast",
    "H1": "h1_recruitment",
    "H2": "h2_onboarding",
    "H3": "h3_attrition_risk",
    "H4": "h4_payroll_check",
    "S4": "s4_physical_verification",
    "V2": "v2_amc_engine",
    "C1": "c1_indent_planning",
    "C2": "c2_dispatch_tracker",
    "C3": "c3_claims_tracker",
    "K1": "k1_campaign_brief",
    "K2": "k2_audience_builder",
    "K3": "k3_broadcast",
    "K4": "k4_dealer_promo",
    "K5": "k5_response_tracker",
    "K6": "k6_campaign_analytics",
    "M1": "m1_ism_command",
    "M2": "m2_gsm_command",
}


@dataclass
class CaseResult:
    name: str
    passed: bool
    failures: list[str] = field(default_factory=list)


@dataclass
class GoldenReport:
    agent_dir: str
    total: int
    passed: int
    results: list[CaseResult]
    gate: dict

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def approved(self) -> bool:
        return bool(self.gate.get("approved", False))


def _resolve_agent(agent_dir: str):
    """Import and construct the agent under test."""
    import importlib

    spec = AGENT_REGISTRY.get(agent_dir)
    if spec is None:
        raise KeyError(
            f"Unknown agent '{agent_dir}' — add it to AGENT_REGISTRY"
        )
    module = importlib.import_module(spec["module"])
    cls = getattr(module, spec["class"])

    kwargs = {}
    for key, how in spec.get("construct", {}).items():
        if how == "MAGIC_MOCK":
            from unittest.mock import MagicMock
            kwargs[key] = MagicMock()
    agent = cls(**kwargs)

    # Apply documented seams (DB isolation) via monkey-patching for the
    # duration of the run — the runner is a test-harness context.
    if "seam" in spec:
        target, kind = spec["seam"]
        if kind == "null_session":
            from contextlib import contextmanager
            from unittest.mock import MagicMock

            db = MagicMock()
            db.execute.return_value.scalars.return_value.first.return_value = None
            db.query.return_value.filter.return_value.first.return_value = None

            @contextmanager
            def null_session():
                yield db

            setattr(module, target, null_session)
        elif kind == "p4_golden_fixture":
            _apply_p4_golden_fixture(module)
        elif kind == "s1_golden_fixture":
            _apply_s1_golden_fixture(module)

    return agent


def _apply_p4_golden_fixture(module) -> None:
    """Patch imported repository classes on the P4 module.

    Installs Pydantic contract objects so the golden gate tests run
    without a live PostgreSQL connection. Only affects the agent module's
    own namespace — other callers of the repository classes are untouched.
    """
    from contextlib import contextmanager
    from decimal import Decimal as _D
    from unittest.mock import MagicMock

    from app.contracts.documents import (
        GoodsReceiptDocument,
        InvoiceDocument,
        PurchaseOrderDocument,
    )

    _PO = {
        "PO-2026-001": PurchaseOrderDocument(
            purchase_id="PO-2026-001", po_number="PO-2026-001",
            vendor="Samsung India Electronics", items=[],
            total_amount=_D("50000"),
        ),
        "PO-2026-003": PurchaseOrderDocument(
            purchase_id="PO-2026-003", po_number="PO-2026-003",
            vendor="Samsung India Electronics", items=[],
            total_amount=_D("75000"),
        ),
    }
    _GRN = {
        "PO-2026-001": GoodsReceiptDocument(
            grn_number="GRN-GOLDEN-001", po_number="PO-2026-001",
            status="COMPLETE", items=[],
        ),
        "PO-2026-003": GoodsReceiptDocument(
            grn_number="GRN-GOLDEN-003", po_number="PO-2026-003",
            status="COMPLETE", items=[],
        ),
    }
    _INV = {
        "INV-2026-001": InvoiceDocument(
            invoice_number="INV-2026-001", po_number="PO-2026-001",
            vendor="Samsung India Electronics",
            invoice_amount=_D("50000"),
        ),
        "INV-2026-003": InvoiceDocument(
            invoice_number="INV-2026-003", po_number="PO-2026-003",
            vendor="Samsung India Electronics",
            invoice_amount=_D("75000"),
        ),
    }

    class _PoRepo:
        def __init__(self, db): pass
        def get_by_number(self, n): return _PO.get(n)
        def get_row(self, n): return None

    class _GrnRepo:
        def __init__(self, db): pass
        def latest_for_po(self, n): return _GRN.get(n)
        def cumulative_received(self, n): return {}
        def existing_serials(self, s): return set()

    class _InvRepo:
        def __init__(self, db): pass
        def get_by_number(self, n): return _INV.get(n)

    @contextmanager
    def _session():
        yield MagicMock()

    module.PurchaseOrderRepository = _PoRepo
    module.GoodsReceiptRepository = _GrnRepo
    module.InvoiceRepository = _InvRepo
    module.document_session = _session


def _apply_s1_golden_fixture(module) -> None:
    """Patch imported repository classes and the GRN sequencer on the S1 module.

    Installs Pydantic contract objects with realistic PO lines so the
    golden gate tests run without a live PostgreSQL connection. Only
    affects the agent module's own namespace.
    """
    from contextlib import contextmanager
    from decimal import Decimal as _D
    from unittest.mock import MagicMock

    from app.contracts.documents import (
        PurchaseOrderDocument,
        PurchaseOrderLine,
    )

    _PO = {
        "PO-2026-001": PurchaseOrderDocument(
            purchase_id="PO-2026-001", po_number="PO-2026-001",
            vendor="Samsung India Electronics",
            items=[PurchaseOrderLine(
                item="Samsung QLED 55",
                quantity=_D("1"),
                unit_price=_D("50000"),
                total_amount=_D("50000"),
            )],
            total_amount=_D("50000"),
        ),
        "PO-2026-002": PurchaseOrderDocument(
            purchase_id="PO-2026-002", po_number="PO-2026-002",
            vendor="Samsung India Electronics",
            items=[PurchaseOrderLine(
                item="Samsung Frame 65",
                quantity=_D("2"),
                unit_price=_D("50000"),
                total_amount=_D("100000"),
            )],
            total_amount=_D("100000"),
        ),
    }

    class _PoRepo:
        def __init__(self, db): pass
        def get_by_number(self, n): return _PO.get(n)

    class _GrnRepo:
        def __init__(self, db): pass
        def cumulative_received(self, n): return {}
        def existing_serials(self, s): return set()

    @contextmanager
    def _session():
        yield MagicMock()

    module.PurchaseOrderRepository = _PoRepo
    module.GoodsReceiptRepository = _GrnRepo
    module.next_document_number = (
        lambda prefix, seq, now: f"{prefix}-{now.strftime('%Y%m')}-99999"
    )
    module.document_session = _session


def _assert_case(case: dict, result) -> list[str]:
    """Hard assertions per golden case. Returns failure strings."""
    failures: list[str] = []
    expect = case.get("expect", {})
    output = result.output or {}

    if result.status != expect.get("status"):
        failures.append(
            f"status: expected {expect.get('status')}, "
            f"got {result.status}"
            + (f" ({result.error})" if result.error else "")
        )

    for key in expect.get("output.contains", []):
        if key not in output:
            failures.append(f"output missing '{key}'")

    for action in expect.get("actions.contains", []):
        if not any(action in a for a in (result.actions_taken or [])):
            failures.append(f"no action matching '{action}'")

    return failures


def run_golden_set(agent_dir: str) -> GoldenReport:
    """Execute all golden cases for one agent. Never raises per-case."""
    spec = AGENT_REGISTRY.get(agent_dir)
    if spec is None:
        raise KeyError(f"Unknown agent '{agent_dir}'")

    cases_path = GOLDEN_DIR / agent_dir / "cases.json"
    cases = []
    if cases_path.exists():
        cases = json.loads(cases_path.read_text(encoding="utf-8"))

    agent = _resolve_agent(agent_dir)

    results: list[CaseResult] = []
    for case in cases:
        try:
            result = agent.run(payload=case.get("payload", {}))
            failures = _assert_case(case, result)
        except Exception as exc:  # a crash is a failure, not an abort
            failures = [f"exception: {exc}"]
        results.append(CaseResult(
            name=case.get("name", "unnamed"),
            passed=not failures,
            failures=failures,
        ))

    gate = load_gate_record(agent_dir)
    report = GoldenReport(
        agent_dir=agent_dir,
        total=len(cases),
        passed=sum(1 for r in results if r.passed),
        results=results,
        gate=gate,
    )
    return report


def load_gate_record(agent_dir: str) -> dict:
    """Ops-maintained record: sign-off + rollback rehearsal dates."""
    path = GATES_DIR / f"{agent_dir}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Unreadable gate record: %s", path)
        return {}


def redteam_coverage(agent_codes: list[str]) -> dict:
    """True when each agent code appears in the red-team pack source."""
    source = (
        REDTEAM_FILE.read_text(encoding="utf-8")
        if REDTEAM_FILE.exists() else ""
    )
    return {
        code: (code in source) for code in agent_codes
    }


def gate_report(agent_dir: str, agent_code: str) -> dict:
    """The machine-generated promotion-gate report.

    Criteria (Definition of Done):
      golden  — 100% of cases pass, ≥ 1 case exists
      redteam — agent code present in the red-team pack
      signoff — business-owner sign-off recorded by ops
      rollback — rehearsal date recorded by ops
    """
    golden = run_golden_set(agent_dir)
    redteam = redteam_coverage([agent_code])

    criteria = {
        "golden": {
            "ok": golden.total > 0 and golden.passed == golden.total,
            "total": golden.total,
            "passed": golden.passed,
        },
        "redteam": {
            "ok": all(redteam.values()),
            "agents_checked": redteam,
        },
        "signoff": {
            "ok": bool(golden.gate.get("business_owner_signoff")),
            "detail": golden.gate.get("business_owner_signoff"),
        },
        "rollback": {
            "ok": bool(golden.gate.get("rollback_rehearsed")),
            "detail": golden.gate.get("rollback_rehearsed"),
        },
    }
    approved = all(c["ok"] for c in criteria.values())

    return {
        "agent_dir": agent_dir,
        "agent_code": agent_code,
        "approved": approved,
        "criteria": criteria,
    }


def render_text(report: dict) -> str:
    """Plain-text rendering for logs/tickets."""
    lines = [
        f"PROMOTION GATE — {report['agent_code']} "
        f"({report['agent_dir']})",
        "=" * 50,
    ]
    for name, c in report["criteria"].items():
        mark = "PASS" if c["ok"] else "FAIL"
        detail = ""
        if "total" in c:
            detail = f" ({c['passed']}/{c['total']} cases)"
        lines.append(f"  [{mark}] {name}{detail}")
    lines.append("=" * 50)
    lines.append(
        "APPROVED — promotion permitted"
        if report["approved"]
        else "BLOCKED — resolve failing criteria before promotion"
    )
    return "\n".join(lines)


def main() -> None:
    """CLI: python -m app.services.promotion_gate <agent_dir> <AGENT_CODE>"""
    import sys

    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(2)
    report = gate_report(sys.argv[1], sys.argv[2])
    print(render_text(report))
    raise SystemExit(0 if report["approved"] else 1)


if __name__ == "__main__":
    main()
