"""F2 Scheme Claims — HTTP surface (schemes + claims).

The scheme register is human-verified before any claim computes: the
verify endpoint stamps verified_by/verified_ts and refuses to verify
inconsistent windows. Claims are created through the agent's compute
result (computation_json keeps the full audit trail).

House convention for actor identity: the request body names the acting
user (verified_by / submitted_by / settled_by), exactly like the
approvals router's approved_by — the value is recorded verbatim in the
row and belongs in the ledger.
"""

import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.scheme import SchemeClaim, SchemeMaster

router = APIRouter(prefix="/f2", tags=["f2-scheme-claims"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Schemes ───────────────────────────────────────────────────────

class SchemeIn(BaseModel):
    scheme_id: str
    vendor: str
    scheme_type: str = "slab"
    window_start: date
    window_end: date
    eligible_skus: list[str]
    slabs: list[dict] | None = None
    per_invoice_min: float | None = None
    circular_doc_ref: str | None = None
    parsed_confidence: float | None = None

    @field_validator("window_end")
    @classmethod
    def window_ordered(cls, v, info):
        start = info.data.get("window_start")
        if start is not None and v < start:
            raise ValueError("window_end before window_start")
        return v


class VerifyIn(BaseModel):
    verified_by: str


def _rules_json(s: SchemeIn) -> dict:
    rules: dict = {"eligible_skus": s.eligible_skus}
    if s.slabs is not None:
        rules["slabs"] = s.slabs
    if s.per_invoice_min is not None:
        rules["per_invoice_min"] = s.per_invoice_min
    return rules


@router.post("/schemes", status_code=201)
def register_scheme(scheme: SchemeIn, db: Session = Depends(get_db)):
    """Register a parsed circular as a candidate scheme (not yet live)."""
    if db.get(SchemeMaster, scheme.scheme_id) is not None:
        raise HTTPException(409, f"Scheme '{scheme.scheme_id}' already exists")
    row = SchemeMaster(
        scheme_id=scheme.scheme_id,
        vendor=scheme.vendor,
        scheme_type=scheme.scheme_type,
        window_start=scheme.window_start,
        window_end=scheme.window_end,
        eligibility_rules_json=_rules_json(scheme),
        circular_doc_ref=scheme.circular_doc_ref,
        parsed_confidence=scheme.parsed_confidence,
        status="candidate",
    )
    db.add(row)
    db.commit()
    return {"scheme_id": row.scheme_id, "status": row.status}


@router.post("/schemes/{scheme_id}/verify")
def verify_scheme(scheme_id: str, body: VerifyIn, db: Session = Depends(get_db)):
    """Human verification gate — a candidate becomes computable."""
    row = db.get(SchemeMaster, scheme_id)
    if row is None:
        raise HTTPException(404, f"Scheme '{scheme_id}' not found")
    if row.status == "verified":
        return {"scheme_id": scheme_id, "status": "verified",
                "verified_by": row.verified_by, "already": True}
    if row.window_end < row.window_start:
        raise HTTPException(
            422, "Scheme window inverted — fix the register before verifying"
        )
    row.status = "verified"
    row.verified_by = body.verified_by
    row.verified_ts = _now()
    db.commit()
    return {"scheme_id": scheme_id, "status": row.status,
            "verified_by": row.verified_by}


@router.get("/schemes")
def list_schemes(db: Session = Depends(get_db)):
    rows = db.query(SchemeMaster).order_by(
        SchemeMaster.created_ts.desc()
    ).all()
    return {
        "schemes": [
            {
                "scheme_id": r.scheme_id,
                "vendor": r.vendor,
                "scheme_type": r.scheme_type,
                "window_start": r.window_start.isoformat(),
                "window_end": r.window_end.isoformat(),
                "status": r.status,
                "verified_by": r.verified_by,
            }
            for r in rows
        ]
    }


@router.get("/schemes/{scheme_id}")
def get_scheme(scheme_id: str, db: Session = Depends(get_db)):
    row = db.get(SchemeMaster, scheme_id)
    if row is None:
        raise HTTPException(404, f"Scheme '{scheme_id}' not found")
    return {
        "scheme_id": row.scheme_id,
        "vendor": row.vendor,
        "scheme_type": row.scheme_type,
        "window_start": row.window_start.isoformat(),
        "window_end": row.window_end.isoformat(),
        "status": row.status,
        "verified_by": row.verified_by,
        "verified_ts": row.verified_ts.isoformat() if row.verified_ts else None,
        "eligibility_rules_json": row.eligibility_rules_json,
        "circular_doc_ref": row.circular_doc_ref,
        "parsed_confidence": row.parsed_confidence,
    }


# ── Claims ────────────────────────────────────────────────────────

class ClaimIn(BaseModel):
    scheme_id: str
    entity: str = "YEXIS-EL"
    dealer_ref: str | None = None
    computation: dict  # the F2 agent's output.computation — audited as-is


@router.post("/claims", status_code=201)
def create_claim(claim: ClaimIn, db: Session = Depends(get_db)):
    """Persist a computed claim. The computation JSON is stored verbatim
    so the claim pack can be re-derived exactly at any later date."""
    scheme = db.get(SchemeMaster, claim.scheme_id)
    if scheme is None:
        raise HTTPException(404, f"Scheme '{claim.scheme_id}' not found")
    if scheme.status != "verified":
        raise HTTPException(
            422,
            f"Scheme '{claim.scheme_id}' is '{scheme.status}' — only "
            "verified schemes may back a claim",
        )

    computation = claim.computation or {}
    claimable = computation.get("claimable_amount")
    if not isinstance(claimable, (int, float)) or claimable < 0:
        raise HTTPException(
            422, "computation.claimable_amount must be a non-negative number"
        )

    claim_id = f"CLM-{uuid.uuid4().hex[:12].upper()}"
    row = SchemeClaim(
        claim_id=claim_id,
        scheme_id=claim.scheme_id,
        entity=claim.entity,
        dealer_ref=claim.dealer_ref,
        amount_computed=float(claimable),
        status="computed",
        computation_json=computation,
    )
    db.add(row)
    db.commit()
    return {"claim_id": claim_id, "status": row.status,
            "amount_computed": row.amount_computed}


@router.get("/claims")
def list_claims(
    scheme_id: str | None = None,
    status: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(SchemeClaim)
    if scheme_id:
        q = q.filter(SchemeClaim.scheme_id == scheme_id)
    if status:
        q = q.filter(SchemeClaim.status == status)
    rows = q.order_by(SchemeClaim.created_ts.desc()).all()
    return {
        "claims": [
            {
                "claim_id": r.claim_id,
                "scheme_id": r.scheme_id,
                "entity": r.entity,
                "dealer_ref": r.dealer_ref,
                "amount_computed": r.amount_computed,
                "amount_claimed": r.amount_claimed,
                "amount_received": r.amount_received,
                "status": r.status,
                "created_ts": (
                    r.created_ts.isoformat() if r.created_ts else None
                ),
            }
            for r in rows
        ]
    }


class SubmitIn(BaseModel):
    submitted_by: str


@router.post("/claims/{claim_id}/submit")
def submit_claim(claim_id: str, body: SubmitIn, db: Session = Depends(get_db)):
    """computed → submitted (a human takes responsibility for the pack)."""
    row = db.get(SchemeClaim, claim_id)
    if row is None:
        raise HTTPException(404, f"Claim '{claim_id}' not found")
    if row.status != "computed":
        raise HTTPException(
            409, f"Claim is '{row.status}' — only 'computed' claims can be submitted"
        )
    row.status = "submitted"
    row.amount_claimed = row.amount_computed
    row.claimed_ts = _now()
    db.commit()
    return {"claim_id": claim_id, "status": row.status,
            "amount_claimed": row.amount_claimed}


class SettlementIn(BaseModel):
    amount_received: float
    settled_by: str
    variance_reason: str | None = None

    @field_validator("amount_received")
    @classmethod
    def non_negative(cls, v):
        if v < 0:
            raise ValueError("amount_received must be ≥ 0")
        return v


@router.post("/claims/{claim_id}/settle")
def settle_claim(claim_id: str, body: SettlementIn, db: Session = Depends(get_db)):
    """submitted → settled | partial, with variance reason."""
    row = db.get(SchemeClaim, claim_id)
    if row is None:
        raise HTTPException(404, f"Claim '{claim_id}' not found")
    if row.status not in ("submitted", "partial"):
        raise HTTPException(
            409, f"Claim is '{row.status}' — settle only after submission"
        )

    claimed = row.amount_claimed or 0.0
    received = float(body.amount_received)
    variance = round(received - claimed, 2)

    row.status = "settled" if abs(variance) <= 0.01 else "partial"
    row.amount_received = received
    row.variance_reason = body.variance_reason
    db.commit()
    return {
        "claim_id": claim_id,
        "status": row.status,
        "amount_received": row.amount_received,
        "variance": variance,
    }
