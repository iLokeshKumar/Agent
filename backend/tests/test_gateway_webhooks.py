"""Automation Gateway — webhook receiver contract tests.

Covers the three behaviours that make the gateway safe:

    * redelivered events are answered once and ignored after
    * lane governance refuses webhooks into killed lanes
    * unknown sources never enter the queue
"""

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import get_db
from app.models.action_ledger import ActionLedger
from app.models.agent import Agent
from app.models.approval import Approval
from app.models.gateway_event import GatewayEvent
from app.models.lane_config import LaneConfig
from app.models.sync_watermark import SyncWatermark
from app.models.task import Task
from app.models.tally_staging import TallyStagingVoucher
from app.models.agent import Base
from gateway.service import router


@pytest.fixture()
def env():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[
            GatewayEvent.__table__,
            Task.__table__,
            LaneConfig.__table__,
            ActionLedger.__table__,
            Approval.__table__,
            SyncWatermark.__table__,
            TallyStagingVoucher.__table__,
            Agent.__table__,
        ],
    )
    TestingSession = sessionmaker(bind=engine)
    session = TestingSession()

    # The waba lane's agent must exist for dispatch to be attempted.
    session.add(Agent(
        id=uuid.uuid4(),
        agent_code="V1",
        name="Service Desk",
        suite="Service",
        autonomy_level="A1",
        version="1.0",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        enabled=True,
    ))
    session.commit()

    def override():
        yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_db] = override

    with TestClient(test_app) as c:
        yield c, session
    test_app.dependency_overrides.clear()
    session.close()


class TestWebhookReceiver:

    def test_unknown_source_refused(self, env):
        client, _ = env
        response = client.post("/webhooks/unknown_source", json={})
        assert response.status_code == 404

    def test_first_delivery_dispatches(self, env):
        client, session = env
        response = client.post("/webhooks/waba", json={
            "event_id": "evt-001",
            "text": "My display is not working",
        })
        assert response.status_code == 200
        assert response.json()["status"] == "dispatched"

        tasks = session.query(Task).all()
        assert len(tasks) == 1
        assert tasks[0].source == "webhook:waba"

    def test_redelivery_is_ignored(self, env):
        client, session = env
        payload = {"event_id": "evt-002", "text": "hello"}

        first = client.post("/webhooks/waba", json=payload)
        second = client.post("/webhooks/waba", json=payload)

        assert first.json()["status"] == "dispatched"
        assert second.json()["status"] == "duplicate"

        tasks = session.query(Task).all()
        assert len(tasks) == 1  # exactly-once

    def test_killed_lane_rejects_webhook(self, env):
        client, session = env
        session.add(LaneConfig(lane="V1", kill_switch=True))
        session.commit()

        response = client.post("/webhooks/waba", json={
            "event_id": "evt-003", "text": "help",
        })

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "rejected"
        assert "killed" in body["reason"]

        # No task was spawned, but the attempt is on the record.
        assert session.query(Task).count() == 0
        events = session.query(GatewayEvent).all()
        assert events[0].status == "rejected"
