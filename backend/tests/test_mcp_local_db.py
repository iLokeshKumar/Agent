"""Local-DB MCP tools — contract tests.

Verifies the typed-tool surface: ledger append/query round-trip,
approval decide guards, watermark get/set semantics. Uses the
FastAPI TestClient against dependency-overridden SQLite.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import get_db
from app.main import app
from app.models.action_ledger import ActionLedger
from app.models.approval import Approval
from app.models.sync_watermark import SyncWatermark
from app.models.agent import Base


@pytest.fixture()
def env():
    """App client + the backing session, yielded together."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[
            ActionLedger.__table__,
            Approval.__table__,
            SyncWatermark.__table__,
        ],
    )
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()

    def override():
        yield session

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c, session
    app.dependency_overrides.clear()
    session.close()


@pytest.fixture()
def client(env):
    return env[0]


class TestLedgerTools:

    def test_append_then_query(self, client):
        corr = str(uuid.uuid4())
        response = client.post("/mcp/db/ledger/append", json={
            "action": "TOOL_CALL",
            "tool_name": "tally_gateway",
            "status": "SUCCESS",
            "correlation_id": corr,
        })
        assert response.status_code == 200
        assert response.json()["appended"] is True

        rows = client.get(
            "/mcp/db/ledger", params={"correlation_id": corr}
        ).json()
        assert len(rows) == 1
        assert rows[0]["action"] == "TOOL_CALL"
        assert rows[0]["correlation_id"] == corr

    def test_query_filters_by_task(self, client):
        task = uuid.uuid4()
        client.post("/mcp/db/ledger/append", json={
            "action": "A", "task_id": str(task),
        })
        rows = client.get(
            "/mcp/db/ledger", params={"task_id": str(task)}
        ).json()
        assert len(rows) >= 1


class TestApprovalTools:

    @staticmethod
    def _pending_approval(session):
        approval = Approval(
            id=uuid.uuid4(),
            approval_type="BUDGET_EXCEEDED",
            status="PENDING",
            requested_by="admin",
            payload={},
        )
        session.add(approval)
        session.commit()
        return approval

    def test_list_pending(self, env):
        client, session = env
        self._pending_approval(session)
        rows = client.get("/mcp/db/approvals").json()
        assert any(r["approval_type"] == "BUDGET_EXCEEDED" for r in rows)

    def test_approve_then_redecide_conflicts(self, env):
        client, session = env
        approval = self._pending_approval(session)

        first = client.post(
            f"/mcp/db/approvals/{approval.id}/approve",
            json={"decided_by": "accountant1"},
        )
        assert first.status_code == 200
        assert first.json()["status"] == "APPROVED"

        second = client.post(
            f"/mcp/db/approvals/{approval.id}/approve",
            json={"decided_by": "accountant1"},
        )
        assert second.status_code == 409


class TestWatermarkTools:

    def test_get_unset_returns_null_cursor(self, client):
        body = client.get(
            "/mcp/db/watermark/F3/zoho_books:journals"
        ).json()
        assert body["cursor"] is None

    def test_set_then_get(self, client):
        set_response = client.post(
            "/mcp/db/watermark/F3/zoho_books:journals",
            json={"cursor": "2026-08-26T00:00:00Z"},
        )
        assert set_response.json()["cursor"] == "2026-08-26T00:00:00Z"

        get_response = client.get(
            "/mcp/db/watermark/F3/zoho_books:journals"
        ).json()
        assert get_response["cursor"] == "2026-08-26T00:00:00Z"

    def test_set_overwrites(self, client):
        url = "/mcp/db/watermark/F3/zoho_books:journals"
        client.post(url, json={"cursor": "a"})
        client.post(url, json={"cursor": "b", "extra": {"n": 2}})
        body = client.get(url).json()
        assert body["cursor"] == "b"
        assert body["extra"] == {"n": 2}
