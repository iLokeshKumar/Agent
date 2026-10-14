"""F2 HTTP surface — scheme register + claim lifecycle.

Runs on in-memory SQLite via the app fixture pattern from
other API tests. Covers:
  - scheme register → verify gate → claim → submit → settle
  - the verify gate: candidates cannot back claims
  - refusal paths: 404s, 409 state conflicts, 422 bad payloads
"""

from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import get_db
from app.main import app
from app.models.agent import Base
from app.models.scheme import SchemeClaim, SchemeMaster


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    # Only the tables this surface touches — full metadata.create_all
    # trips over Postgres-only DDL (gen_random_uuid()) on SQLite.
    tables = [
        SchemeMaster.__table__, SchemeClaim.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)

    def override():
        yield TestingSession()

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _scheme_payload(scheme_id="SCH-API-1"):
    return {
        "scheme_id": scheme_id,
        "vendor": "Samsung",
        "window_start": "2026-08-01",
        "window_end": "2026-08-31",
        "eligible_skus": ["SAMS-QA65"],
    }


COMPUTATION = {
    "eligible_serials": 1,
    "excluded_serials": [],
    "qualifying_sales_value": 600000.0,
    "slab_rate": 0.01,
    "earned_amount": 6000.0,
    "already_claimed": 0.0,
    "claimable_amount": 6000.0,
    "lines": [],
}


class TestSchemeRegister:

    def test_register_and_verify(self, client):
        r = client.post("/f2/schemes", json=_scheme_payload())
        assert r.status_code == 201
        assert r.json()["status"] == "candidate"

        v = client.post(
            "/f2/schemes/SCH-API-1/verify",
            json={"verified_by": "commercial_head"},
        )
        assert v.status_code == 200
        assert v.json()["status"] == "verified"
        assert v.json()["verified_by"] == "commercial_head"

    def test_duplicate_scheme_conflicts(self, client):
        client.post("/f2/schemes", json=_scheme_payload())
        r = client.post("/f2/schemes", json=_scheme_payload())
        assert r.status_code == 409

    def test_inverted_window_fails_at_input(self, client):
        r = client.post("/f2/schemes", json={
            **_scheme_payload("SCH-BAD"),
            "window_start": "2026-08-31",
            "window_end": "2026-08-01",
        })
        assert r.status_code == 422

    def test_verify_unknown_scheme_404(self, client):
        r = client.post(
            "/f2/schemes/SCH-GHOST/verify",
            json={"verified_by": "x"},
        )
        assert r.status_code == 404

    def test_verify_is_idempotent(self, client):
        client.post("/f2/schemes", json=_scheme_payload())
        first = client.post(
            "/f2/schemes/SCH-API-1/verify", json={"verified_by": "a"}
        )
        second = client.post(
            "/f2/schemes/SCH-API-1/verify", json={"verified_by": "b"}
        )
        assert first.json()["status"] == "verified"
        assert second.json()["already"] is True
        # First verifier sticks — the decision is already ledgered.
        assert second.json()["verified_by"] == "a"


class TestClaimLifecycle:

    def _verified_scheme(self, client, scheme_id="SCH-API-1"):
        client.post("/f2/schemes", json=_scheme_payload(scheme_id))
        client.post(
            f"/f2/schemes/{scheme_id}/verify",
            json={"verified_by": "head"},
        )

    def test_full_lifecycle(self, client):
        self._verified_scheme(client)

        c = client.post("/f2/claims", json={
            "scheme_id": "SCH-API-1",
            "computation": COMPUTATION,
        })
        assert c.status_code == 201
        claim_id = c.json()["claim_id"]
        assert c.json()["amount_computed"] == 6000.0

        s = client.post(
            f"/f2/claims/{claim_id}/submit",
            json={"submitted_by": "commercial_head"},
        )
        assert s.status_code == 200
        assert s.json()["status"] == "submitted"
        assert s.json()["amount_claimed"] == 6000.0

        st = client.post(
            f"/f2/claims/{claim_id}/settle",
            json={"amount_received": 5800.0, "settled_by": "finance",
                  "variance_reason": " Handling fee"},
        )
        assert st.status_code == 200
        assert st.json()["status"] == "partial"
        assert st.json()["variance"] == -200.0

        # Full settlement closes it out
        st2 = client.post(
            f"/f2/claims/{claim_id}/settle",
            json={"amount_received": 6000.0, "settled_by": "finance"},
        )
        assert st2.json()["status"] == "settled"

    def test_candidate_scheme_cannot_back_claim(self, client):
        client.post("/f2/schemes", json=_scheme_payload("SCH-CAND"))
        r = client.post("/f2/claims", json={
            "scheme_id": "SCH-CAND",
            "computation": COMPUTATION,
        })
        assert r.status_code == 422
        assert "verified" in r.json()["detail"]

    def test_claim_on_unknown_scheme_404(self, client):
        r = client.post("/f2/claims", json={
            "scheme_id": "SCH-GHOST",
            "computation": COMPUTATION,
        })
        assert r.status_code == 404

    def test_claim_rejects_bad_computation(self, client):
        self._verified_scheme(client, "SCH-API-2")
        r = client.post("/f2/claims", json={
            "scheme_id": "SCH-API-2",
            "computation": {"claimable_amount": -5},
        })
        assert r.status_code == 422

    def test_settle_before_submit_conflicts(self, client):
        self._verified_scheme(client, "SCH-API-3")
        c = client.post("/f2/claims", json={
            "scheme_id": "SCH-API-3",
            "computation": COMPUTATION,
        })
        claim_id = c.json()["claim_id"]
        r = client.post(
            f"/f2/claims/{claim_id}/settle",
            json={"amount_received": 100, "settled_by": "x"},
        )
        assert r.status_code == 409

    def test_double_submit_conflicts(self, client):
        self._verified_scheme(client, "SCH-API-4")
        c = client.post("/f2/claims", json={
            "scheme_id": "SCH-API-4",
            "computation": COMPUTATION,
        })
        claim_id = c.json()["claim_id"]
        client.post(
            f"/f2/claims/{claim_id}/submit",
            json={"submitted_by": "a"},
        )
        r = client.post(
            f"/f2/claims/{claim_id}/submit",
            json={"submitted_by": "b"},
        )
        assert r.status_code == 409

    def test_list_filters(self, client):
        self._verified_scheme(client, "SCH-API-5")
        client.post("/f2/claims", json={
            "scheme_id": "SCH-API-5",
            "computation": COMPUTATION,
        })
        r = client.get("/f2/claims", params={"scheme_id": "SCH-API-5"})
        assert r.status_code == 200
        assert len(r.json()["claims"]) == 1
        r = client.get(
            "/f2/claims", params={"status": "computed"}
        )
        assert any(
            c["scheme_id"] == "SCH-API-5" for c in r.json()["claims"]
        )
