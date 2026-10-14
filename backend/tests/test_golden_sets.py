"""Golden-set runner — curated cases per agent, data-driven.

Cases live in ``tests/golden/<agent>/cases.json``. Each case names the
expected status and optionally which keys must appear in output and
which actions must have been taken. The runner is generic: adding a
golden case is a JSON edit, not code — mirroring how the workflow
engine made orchestration data.

Per the docs' promotion gate: an agent earns autonomy only with a
golden set at 100% hard assertions. This runner is that gate in
embryo; LLM-as-judge rubrics arrive with the Claude runtime.
"""

import json
import os
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from app.agents.p1_purchase_intake import P1PurchaseIntake
from app.agents.p2_sourcing_rfq import P2SourcingRfq
import app.agents.p2_sourcing_rfq as p2_mod
from app.agents.f1_collections import F1Collections
from app.agents.v1_service_desk import V1ServiceDesk
from app.services import numbering as numbering_mod


GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "golden")

AGENTS = {
    "p1_purchase_intake": P1PurchaseIntake,
    "p2_sourcing_rfq": P2SourcingRfq,
    "f1_collections": F1Collections,
    "v1_service_desk": V1ServiceDesk,
}


def _load_cases():
    cases = []
    for agent_dir, cls in AGENTS.items():
        path = os.path.join(GOLDEN_DIR, agent_dir, "cases.json")
        if not os.path.isfile(path):
            continue
        for case in json.load(open(path, encoding="utf-8")):
            cases.append(pytest.param(
                agent_dir, cls, case, id=f"{agent_dir}:{case['name']}"
            ))
    return cases


CASES = _load_cases()


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(numbering_mod, "next_document_number",
                        lambda *a, **k: "PO-GOLDEN-0001")

    # P2's collect/compare load documents by number; the fake session
    # returns no rows, so execution takes the honest "unknown RFQ"
    # path instead of reaching for a database.
    db = MagicMock()
    db.execute.return_value.scalars.return_value.first.return_value = None

    @contextmanager
    def fake_p2_session():
        yield db

    monkeypatch.setattr(p2_mod, "document_session", fake_p2_session)
    yield


@pytest.mark.parametrize("agent_dir,cls,case", CASES)
def test_golden_case(agent_dir, cls, case):
    if agent_dir == "p1_purchase_intake":
        gateway = MagicMock()
        gateway.create_purchase_order.return_value = MagicMock(
            purchase_id="PO-GOLDEN-0001", status="CREATED",
        )
        agent = cls(gateway=gateway)
    else:
        agent = cls()

    result = agent.run(payload=case["payload"])
    expectations = case["expect"]

    # status
    assert result.status == expectations["status"], (
        f"{case['name']}: expected {expectations['status']}, "
        f"got {result.status} ({result.error})"
    )

    output = result.output or {}

    # output.contains — named keys must be present (non-empty)
    for key in expectations.get("output.contains", []):
        assert key in output and output[key], (
            f"{case['name']}: output missing '{key}'"
        )

    # actions.contains — some action substring recorded
    for action in expectations.get("actions.contains", []):
        assert any(action in a for a in result.actions_taken), (
            f"{case['name']}: no action matching '{action}'"
        )
