"""F3 Books-Sync console — accountant daily view.

Provides three data endpoints plus an HTML shell at /f3:

  GET  /f3                      HTML accountant console (SPA shell)
  GET  /f3/staging-summary      voucher staging counts by type + status
  POST /f3/bulk-approve         bulk-approve all STAGED rows for an entity
  GET  /f3/drift-report         Books vs Tally trial balance comparison
  GET  /f3/mapping              Books→Tally account mappings for an entity
  POST /f3/mapping              upsert one mapping row
  GET  /f3/exceptions           POST_FAILED and REJECTED rows (actionable)

The console page polls these endpoints and renders without a JS framework.
"""

import uuid
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.integrations.tally_gateway import TallyGateway, TallyGatewayError, VALID_ENTITIES
from app.models.tally_staging import TallyStagingVoucher
from app.models.books_tally_map import BooksTallyMap

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/f3", tags=["f3-console"])


# ── Staging summary ───────────────────────────────────────────────────


@router.get("/staging-summary")
def staging_summary(
    entity: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Voucher counts by (entity, voucher_type, status).

    Exceptions (POST_FAILED / REJECTED) are returned first so the
    accountant sees the critical items at the top of the console.
    """
    q = (
        select(
            TallyStagingVoucher.entity,
            TallyStagingVoucher.voucher_type,
            TallyStagingVoucher.status,
            func.count().label("count"),
            func.sum(TallyStagingVoucher.debit).label("total_debit"),
        )
        .group_by(
            TallyStagingVoucher.entity,
            TallyStagingVoucher.voucher_type,
            TallyStagingVoucher.status,
        )
        .order_by(
            # exceptions first
            func.case(
                (TallyStagingVoucher.status == "POST_FAILED", 0),
                (TallyStagingVoucher.status == "REJECTED", 1),
                else_=2,
            ),
            TallyStagingVoucher.entity,
            TallyStagingVoucher.voucher_type,
        )
    )
    if entity:
        q = q.where(TallyStagingVoucher.entity == entity)

    rows = db.execute(q).all()
    return [
        {
            "entity": r.entity,
            "voucher_type": r.voucher_type,
            "status": r.status,
            "count": r.count,
            "total_debit": float(r.total_debit or 0),
            "exception": r.status in ("POST_FAILED", "REJECTED"),
        }
        for r in rows
    ]


# ── Exceptions list ───────────────────────────────────────────────────


@router.get("/exceptions")
def staging_exceptions(
    entity: str | None = Query(default=None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """POST_FAILED and REJECTED rows — the accountant's action queue."""
    q = (
        select(TallyStagingVoucher)
        .where(TallyStagingVoucher.status.in_(["POST_FAILED", "REJECTED"]))
        .order_by(TallyStagingVoucher.created_at.desc())
        .limit(limit)
    )
    if entity:
        q = q.where(TallyStagingVoucher.entity == entity)

    rows = list(db.execute(q).scalars())
    return [_staging_row(r) for r in rows]


# ── Bulk approve ──────────────────────────────────────────────────────


class BulkApproveRequest(BaseModel):
    entity: str
    approved_by: str
    books_txn_ids: list[str] | None = None  # None → all STAGED for entity


@router.post("/bulk-approve")
def bulk_approve(
    request: BulkApproveRequest,
    db: Session = Depends(get_db),
):
    """Approve all (or selected) STAGED vouchers for one entity.

    Called from the accountant console after reviewing the staging
    summary. The gateway's post_approved() still needs a separate call
    to push APPROVED rows into Tally.
    """
    if request.entity not in VALID_ENTITIES:
        raise HTTPException(400, f"unknown entity — must be one of {VALID_ENTITIES}")

    q = select(TallyStagingVoucher).where(
        TallyStagingVoucher.entity == request.entity,
        TallyStagingVoucher.status == "STAGED",
    )
    if request.books_txn_ids:
        q = q.where(TallyStagingVoucher.books_txn_id.in_(request.books_txn_ids))

    rows = list(db.execute(q).scalars())
    if not rows:
        return {"approved": 0, "message": "no STAGED rows found for approval"}

    gateway = TallyGateway(db)
    result = gateway.approve([r.id for r in rows], request.approved_by)
    db.commit()
    return result


# ── Post approved ──────────────────────────────────────────────────────


class PostApprovedRequest(BaseModel):
    entity: str
    books_txn_ids: list[str] | None = None


@router.post("/post-approved")
def post_approved(
    request: PostApprovedRequest,
    db: Session = Depends(get_db),
):
    """Push APPROVED vouchers into Tally.

    Separate from bulk-approve so the accountant can review before
    posting. Requires TALLY_HOST to be configured in .env.
    """
    if request.entity not in VALID_ENTITIES:
        raise HTTPException(400, f"unknown entity — must be one of {VALID_ENTITIES}")
    try:
        result = TallyGateway(db).post_approved(
            request.entity, request.books_txn_ids
        )
    except TallyGatewayError as exc:
        raise HTTPException(502, str(exc))
    db.commit()
    return result


# ── Drift report ──────────────────────────────────────────────────────


@router.get("/drift-report")
def drift_report(
    entity: str = Query(...),
    db: Session = Depends(get_db),
):
    """Trial balance comparison: staging totals vs Tally live TB.

    Returns Books-side debit totals per Tally ledger alongside whatever
    Tally's trial-balance endpoint returns. When Tally is unreachable the
    tally_tb key is null and an error message is included.
    """
    if entity not in VALID_ENTITIES:
        raise HTTPException(400, f"unknown entity — must be one of {VALID_ENTITIES}")

    # Books-side: sum of posted vouchers per tally_account
    posted_rows = db.execute(
        select(
            TallyStagingVoucher.tally_account,
            func.sum(TallyStagingVoucher.debit).label("total_debit"),
            func.sum(TallyStagingVoucher.credit).label("total_credit"),
            func.count().label("count"),
        )
        .where(
            TallyStagingVoucher.entity == entity,
            TallyStagingVoucher.status == "POSTED",
        )
        .group_by(TallyStagingVoucher.tally_account)
        .order_by(TallyStagingVoucher.tally_account)
    ).all()

    books_tb = [
        {
            "ledger": r.tally_account or "(unmapped)",
            "debit": float(r.total_debit or 0),
            "credit": float(r.total_credit or 0),
            "voucher_count": r.count,
        }
        for r in posted_rows
    ]

    # Tally-side TB (requires live Tally connection)
    tally_tb = None
    tally_error = None
    try:
        tally_tb = TallyGateway(db).trial_balance(entity)
    except TallyGatewayError as exc:
        tally_error = str(exc)
        logger.warning("Drift report: Tally TB unavailable for %s: %s", entity, exc)

    # Identify drift: accounts present in Books-side but missing from Tally
    if tally_tb:
        tally_ledgers = {row.get("ledger", ""): row for row in tally_tb}
        for row in books_tb:
            tally_entry = tally_ledgers.get(row["ledger"])
            row["tally_dr"] = tally_entry.get("dr", "") if tally_entry else None
            row["tally_cr"] = tally_entry.get("cr", "") if tally_entry else None
            row["drift"] = tally_entry is None

    return {
        "entity": entity,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "books_tb": books_tb,
        "tally_tb": tally_tb,
        "tally_error": tally_error,
    }


# ── Account mapping CRUD ──────────────────────────────────────────────


class MappingUpsertRequest(BaseModel):
    entity: str
    books_account_id: str
    books_account_name: str
    books_account_type: str | None = None
    tally_ledger_name: str | None = None
    tally_group: str | None = None
    cost_centre: str | None = None
    mapped_by: str
    notes: str | None = None


@router.get("/mapping")
def list_mapping(
    entity: str = Query(...),
    active_only: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    """List all Books→Tally mappings for one entity."""
    q = (
        select(BooksTallyMap)
        .where(BooksTallyMap.entity == entity)
        .order_by(BooksTallyMap.books_account_name)
    )
    if active_only:
        q = q.where(BooksTallyMap.active == True)  # noqa: E712
    rows = list(db.execute(q).scalars())
    return [_mapping_row(r) for r in rows]


@router.post("/mapping")
def upsert_mapping(
    request: MappingUpsertRequest,
    db: Session = Depends(get_db),
):
    """Create or update one Books→Tally ledger mapping.

    The finance owner uses the console UI to fill this table. Only the
    accountant can edit; no agent writes here.
    """
    if request.entity not in VALID_ENTITIES:
        raise HTTPException(400, f"unknown entity — must be one of {VALID_ENTITIES}")

    existing = db.execute(
        select(BooksTallyMap).where(
            BooksTallyMap.entity == request.entity,
            BooksTallyMap.books_account_id == request.books_account_id,
        )
    ).scalars().first()

    now = datetime.now(timezone.utc)
    if existing is None:
        row = BooksTallyMap(
            entity=request.entity,
            books_account_id=request.books_account_id,
            books_account_name=request.books_account_name,
            books_account_type=request.books_account_type,
            tally_ledger_name=request.tally_ledger_name,
            tally_group=request.tally_group,
            cost_centre=request.cost_centre,
            active=True,
            mapped_by=request.mapped_by,
            mapped_at=now,
            notes=request.notes,
        )
        db.add(row)
    else:
        existing.books_account_name = request.books_account_name
        existing.books_account_type = request.books_account_type
        existing.tally_ledger_name = request.tally_ledger_name
        existing.tally_group = request.tally_group
        existing.cost_centre = request.cost_centre
        existing.mapped_by = request.mapped_by
        existing.mapped_at = now
        existing.notes = request.notes
        existing.active = True
        row = existing

    db.commit()
    return _mapping_row(row)


# ── HTML console ──────────────────────────────────────────────────────


@router.get("", response_class=HTMLResponse, include_in_schema=False)
def f3_console():
    """F3 accountant daily view — staging summary, bulk approve, drift."""
    return _HTML


# ── Serialisers ───────────────────────────────────────────────────────


def _staging_row(r: TallyStagingVoucher) -> dict:
    return {
        "id": str(r.id),
        "entity": r.entity,
        "books_txn_id": r.books_txn_id,
        "line_no": r.line_no,
        "voucher_type": r.voucher_type,
        "voucher_date": r.voucher_date.isoformat() if r.voucher_date else None,
        "tally_account": r.tally_account,
        "debit": r.debit,
        "credit": r.credit,
        "status": r.status,
        "approved_by": r.approved_by,
        "tally_voucher_no": r.tally_voucher_no,
        "error_message": r.error_message,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _mapping_row(r: BooksTallyMap) -> dict:
    return {
        "id": str(r.id),
        "entity": r.entity,
        "books_account_id": r.books_account_id,
        "books_account_name": r.books_account_name,
        "books_account_type": r.books_account_type,
        "tally_ledger_name": r.tally_ledger_name,
        "tally_group": r.tally_group,
        "cost_centre": r.cost_centre,
        "active": r.active,
        "mapped_by": r.mapped_by,
        "mapped_at": r.mapped_at.isoformat() if r.mapped_at else None,
        "notes": r.notes,
    }


# ── HTML shell ────────────────────────────────────────────────────────


_HTML = """\
<!doctype html><html><head><meta charset='utf-8'>
<title>F3 Books-Sync Console — Yexis</title>
<style>
body{font:13px system-ui;margin:0;background:#f0f4f8;color:#1a2433}
header{background:#1a3a5c;color:#fff;padding:10px 20px;display:flex;align-items:center;gap:12px}
header h1{margin:0;font-size:18px}
.entity-bar{background:#e8edf2;padding:6px 20px;display:flex;gap:8px;border-bottom:1px solid #d0d7e0}
.entity-btn{border:1px solid #aab;border-radius:4px;padding:3px 10px;cursor:pointer;background:#fff}
.entity-btn.active{background:#1a3a5c;color:#fff;border-color:#1a3a5c}
main{padding:16px 20px;display:grid;grid-template-columns:1fr 1fr;gap:16px}
.card{background:#fff;border:1px solid #d0d7e0;border-radius:8px;padding:14px}
.card h2{margin:0 0 10px;font-size:15px;color:#1a3a5c}
table{width:100%;border-collapse:collapse;font-size:12px}
th{background:#f0f4f8;padding:6px 8px;text-align:left;font-weight:600}
td{padding:5px 8px;border-bottom:1px solid #eef0f3;vertical-align:top}
.pill{display:inline-block;border-radius:10px;padding:2px 7px;font-size:11px;font-weight:600}
.STAGED{background:#e5f3ff;color:#0070d2}
.APPROVED{background:#e4f5e1;color:#2d7a20}
.POSTED{background:#d4f0d4;color:#1a5c1a}
.POST_FAILED,.REJECTED{background:#fde8e8;color:#b42318}
.exc-row td{background:#fff5f5}
button{border:0;border-radius:4px;padding:5px 10px;cursor:pointer;background:#1a3a5c;color:#fff;font-size:12px}
button.secondary{background:#e8edf2;color:#1a2433}
input,select{border:1px solid #ccd4dd;border-radius:4px;padding:4px 8px;font-size:12px;width:100%}
.muted{color:#8899aa;font-size:12px}
.full-width{grid-column:1/-1}
.tab-bar{display:flex;gap:0;border-bottom:2px solid #d0d7e0;margin-bottom:10px}
.tab{padding:6px 14px;cursor:pointer;color:#667;font-size:13px}
.tab.active{border-bottom:2px solid #1a3a5c;color:#1a3a5c;font-weight:600;margin-bottom:-2px}
#post-result{margin-top:8px;padding:8px;border-radius:4px;display:none}
.success-msg{background:#e4f5e1;color:#2d7a20}
.error-msg{background:#fde8e8;color:#b42318}
</style></head>
<body>
<header>
  <h1>F3 Books-Sync Console</h1>
  <span class='muted' id='last-refresh'></span>
</header>
<div class='entity-bar'>
  <span style='color:#667;font-size:12px;line-height:28px'>Entity:</span>
  <button class='entity-btn active' onclick="setEntity('YEXIS-EL')">YEXIS-EL</button>
  <button class='entity-btn' onclick="setEntity('YEXIS-SL')">YEXIS-SL</button>
  <button class='entity-btn' onclick="setEntity('YEXIS-SOL')">YEXIS-SOL</button>
  <span style='flex:1'></span>
  <button class='secondary' onclick='load()'>Refresh</button>
</div>
<main>
  <div class='card'>
    <h2>Staging Summary</h2>
    <div id='summary'>Loading…</div>
  </div>
  <div class='card'>
    <h2>Bulk Actions</h2>
    <p class='muted'>Review the staging summary, then approve all STAGED rows for this entity.</p>
    <p><label>Your user ID: <input id='approver' value='accountant' style='width:160px'></label></p>
    <button onclick='bulkApprove()'>Approve All STAGED</button>
    &nbsp;
    <button onclick='postApproved()'>Post APPROVED to Tally</button>
    <div id='post-result'></div>
  </div>
  <div class='card full-width'>
    <div class='tab-bar'>
      <div class='tab active' onclick="showTab('exceptions')">Exceptions</div>
      <div class='tab' onclick="showTab('drift')">Drift Report</div>
      <div class='tab' onclick="showTab('mapping')">Account Mapping</div>
    </div>
    <div id='tab-exceptions'><div id='exceptions'>Loading…</div></div>
    <div id='tab-drift' style='display:none'><div id='drift'>Click tab to load.</div></div>
    <div id='tab-mapping' style='display:none'><div id='mapping'>Click tab to load.</div></div>
  </div>
</main>
<script>
const esc=v=>String(v??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
let entity='YEXIS-EL';
let activeTab='exceptions';

function setEntity(e){
  entity=e;
  document.querySelectorAll('.entity-btn').forEach(b=>{
    b.classList.toggle('active',b.textContent===e);
  });
  load();
}

function showTab(tab){
  activeTab=tab;
  ['exceptions','drift','mapping'].forEach(t=>{
    document.getElementById('tab-'+t).style.display=t===tab?'':'none';
  });
  document.querySelectorAll('.tab').forEach((el,i)=>{
    el.classList.toggle('active',['exceptions','drift','mapping'][i]===tab);
  });
  if(tab==='drift')loadDrift();
  if(tab==='mapping')loadMapping();
}

async function get(u){const r=await fetch(u);if(!r.ok)throw new Error(await r.text());return r.json()}
async function post(u,body){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!r.ok)throw new Error(await r.text());return r.json()}

function pillStatus(s){return`<span class="pill ${esc(s)}">${esc(s)}</span>`}

async function load(){
  document.getElementById('last-refresh').textContent='Refreshing…';
  try{
    const[summary,exceptions]=await Promise.all([
      get(`/f3/staging-summary?entity=${entity}`),
      get(`/f3/exceptions?entity=${entity}&limit=50`)
    ]);
    renderSummary(summary);
    renderExceptions(exceptions);
    document.getElementById('last-refresh').textContent=`Last: ${new Date().toLocaleTimeString()}`;
  }catch(e){
    document.getElementById('summary').innerHTML=`<p style="color:red">${esc(e.message)}</p>`;
  }
}

function renderSummary(rows){
  if(!rows.length){document.getElementById('summary').innerHTML='<p class="muted">No staging rows found.</p>';return;}
  let html='<table><thead><tr><th>Voucher Type</th><th>Status</th><th>Count</th><th>Debit Total</th></tr></thead><tbody>';
  for(const r of rows){
    html+=`<tr class="${r.exception?'exc-row':''}"><td>${esc(r.voucher_type)}</td><td>${pillStatus(r.status)}</td><td>${r.count}</td><td>₹${r.total_debit.toLocaleString('en-IN',{minimumFractionDigits:2})}</td></tr>`;
  }
  html+='</tbody></table>';
  document.getElementById('summary').innerHTML=html;
}

function renderExceptions(rows){
  const el=document.getElementById('exceptions');
  if(!rows.length){el.innerHTML='<p class="muted">No exceptions.</p>';return;}
  let html='<table><thead><tr><th>Entity</th><th>Txn ID</th><th>Type</th><th>Status</th><th>Error</th></tr></thead><tbody>';
  for(const r of rows){
    html+=`<tr><td>${esc(r.entity)}</td><td><code>${esc(r.books_txn_id)}</code></td><td>${esc(r.voucher_type)}</td><td>${pillStatus(r.status)}</td><td style="color:#b42318;max-width:300px;word-break:break-all">${esc(r.error_message)}</td></tr>`;
  }
  html+='</tbody></table>';
  el.innerHTML=html;
}

async function bulkApprove(){
  const approver=document.getElementById('approver').value.trim();
  if(!approver){alert('Enter your user ID');return;}
  showPostResult('Approving…','');
  try{
    const r=await post('/f3/bulk-approve',{entity,approved_by:approver});
    showPostResult(`Approved ${r.approved} rows (${r.already_approved} already approved).`,'success-msg');
    load();
  }catch(e){showPostResult(e.message,'error-msg');}
}

async function postApproved(){
  const approver=document.getElementById('approver').value.trim();
  if(!approver){alert('Enter your user ID');return;}
  if(!confirm(`Post all APPROVED vouchers for ${entity} to Tally?`))return;
  showPostResult('Posting to Tally…','');
  try{
    const r=await post('/f3/post-approved',{entity});
    const posted=r.posted_transactions.length;
    const refused=r.refused.length;
    showPostResult(`Posted ${posted} transaction(s). Refused: ${refused}.${refused?' Check exceptions tab.':''}`,'success-msg');
    load();
  }catch(e){showPostResult(e.message,'error-msg');}
}

function showPostResult(msg,cls){
  const el=document.getElementById('post-result');
  el.textContent=msg;
  el.className=cls;
  el.style.display='block';
}

async function loadDrift(){
  document.getElementById('drift').innerHTML='Loading trial balance…';
  try{
    const d=await get(`/f3/drift-report?entity=${entity}`);
    let html=`<p class="muted">As of: ${esc(d.as_of)}</p>`;
    if(d.tally_error){html+=`<p style="color:#b42318">Tally unavailable: ${esc(d.tally_error)}</p>`;}
    if(d.books_tb.length){
      html+='<table><thead><tr><th>Ledger (Tally)</th><th>Books Dr</th><th>Books Cr</th><th>Tally Dr</th><th>Tally Cr</th><th>Drift?</th></tr></thead><tbody>';
      for(const r of d.books_tb){
        const driftFlag=r.drift===true?'<span style="color:#b42318">⚠ missing</span>':'';
        html+=`<tr><td>${esc(r.ledger)}</td><td>${r.debit.toLocaleString()}</td><td>${r.credit.toLocaleString()}</td><td>${esc(r.tally_dr??'—')}</td><td>${esc(r.tally_cr??'—')}</td><td>${driftFlag}</td></tr>`;
      }
      html+='</tbody></table>';
    }else{html+='<p class="muted">No posted vouchers found.</p>';}
    document.getElementById('drift').innerHTML=html;
  }catch(e){document.getElementById('drift').innerHTML=`<p style="color:red">${esc(e.message)}</p>`;}
}

async function loadMapping(){
  document.getElementById('mapping').innerHTML='Loading mappings…';
  try{
    const rows=await get(`/f3/mapping?entity=${entity}&active_only=false`);
    let html=`<p class="muted">${rows.length} account(s). Empty rows need mapping before F3 can post to Tally.</p>`;
    html+='<table><thead><tr><th>Books Account</th><th>Type</th><th>Tally Ledger</th><th>Group</th><th>Cost Centre</th><th>Status</th></tr></thead><tbody>';
    for(const r of rows){
      const missing=!r.tally_ledger_name?'<span style="color:#b42318">⚠ unmapped</span>':'';
      html+=`<tr><td>${esc(r.books_account_name)}</td><td>${esc(r.books_account_type||'')}</td><td>${esc(r.tally_ledger_name||'')}${missing}</td><td>${esc(r.tally_group||'')}</td><td>${esc(r.cost_centre||'')}</td><td>${r.active?'Active':'<span class="muted">Inactive</span>'}</td></tr>`;
    }
    if(!rows.length){html+='<tr><td colspan="6" class="muted">No mappings yet. Finance owner must complete these before F3 UAT.</td></tr>';}
    html+='</tbody></table>';
    document.getElementById('mapping').innerHTML=html;
  }catch(e){document.getElementById('mapping').innerHTML=`<p style="color:red">${esc(e.message)}</p>`;}
}

load();
setInterval(load,30000);
</script>
</body></html>
"""
