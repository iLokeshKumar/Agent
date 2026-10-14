"""Local operations console for the Yexis Full Platform (31 agents)."""

import os

import httpx
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter(tags=["operations"])

_GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8001")


@router.get("/gateway/ops")
def gateway_ops_proxy():
    """Proxy /gateway/ops to the gateway process (port 8001)."""
    try:
        r = httpx.get(f"{_GATEWAY_URL}/gateway/ops", timeout=3.0)
        return JSONResponse(content=r.json(), status_code=r.status_code)
    except Exception as exc:
        return JSONResponse(
            content={"error": f"Gateway unreachable: {exc}", "scheduler": [], "lanes": [], "dlq_depth": 0},
            status_code=503,
        )


@router.get("/ops", response_class=HTMLResponse, include_in_schema=False)
def operations_console():
    return """<!doctype html><html><head><meta charset='utf-8'><title>Yexis Operations</title>
<style>body{font:14px system-ui;margin:28px;background:#f6f8fb;color:#182230}h1{margin-bottom:4px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}.card{background:#fff;border:1px solid #dce3ed;border-radius:10px;padding:16px}table{width:100%;border-collapse:collapse}td,th{padding:8px;border-bottom:1px solid #e7ebf0;text-align:left;vertical-align:top}.pill{border-radius:12px;padding:3px 8px;font-size:12px;background:#e8f0fe}button{border:0;border-radius:6px;padding:6px 10px;margin-right:4px;background:#155eef;color:#fff;cursor:pointer}button.reject{background:#b42318}.muted{color:#667085}pre{white-space:pre-wrap;max-height:180px;overflow:auto}</style></head>
<body><h1>Yexis Full Platform Operations</h1><p class='muted'>31-agent platform console (10 suites). Live lanes execute autonomously per tier; dry-run lanes remain gated.</p>
<div class='grid'><section class='card'><h2>Platform</h2><pre id='health'>Loading…</pre></section><section class='card'><h2>Lane governance</h2><div id='lanes'>Loading…</div></section></div>
<section class='card'><h2>Pending approvals</h2><div id='approvals'>Loading…</div></section>
<section class='card'><h2>Recent tasks and audit correlation</h2><div id='tasks'>Loading…</div></section>
<section class='card'><h2>Internal email drafts</h2><div id='drafts'>Loading…</div></section>
<script>
const esc=v=>String(v??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function get(u){const r=await fetch(u);if(!r.ok)throw new Error(await r.text());return r.json()}
async function decide(id,action){const approved_by=prompt('Your Yexis user ID:', 'admin');if(!approved_by)return;const reason=prompt('Reason (optional):','');const r=await fetch(`/approvals/${id}/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({approved_by,reason})});if(!r.ok)alert(await r.text());load()}
function table(rows,cols){if(!rows.length)return '<p class="muted">None</p>';return `<table><thead><tr>${cols.map(c=>`<th>${esc(c[0])}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${cols.map(c=>`<td>${c[1](row)}</td>`).join('')}</tr>`).join('')}</tbody></table>`}
async function load(){try{const [h,lanes,approvals,tasks,drafts]=await Promise.all([get('/agents/health'),get('/lanes'),get('/approvals?status=PENDING'),get('/tasks?limit=20'),get('/mail/drafts')]);
document.querySelector('#health').textContent=JSON.stringify(h.summary,null,2);
document.querySelector('#lanes').innerHTML=table(lanes,[['Lane',x=>esc(x.lane)],['State',x=>`<span class="pill">${esc(x.autonomy_state)}</span>`],['Dry run',x=>esc(x.dry_run)],['Kill',x=>esc(x.kill_switch)]]);
document.querySelector('#approvals').innerHTML=table(approvals,[['Type',x=>esc(x.approval_type)],['Requested by',x=>esc(x.requested_by)],['Details',x=>`<pre>${esc(JSON.stringify(x.payload))}</pre>`],['Action',x=>`<button onclick="decide('${x.id}','approve')">Approve</button><button class="reject" onclick="decide('${x.id}','reject')">Reject</button>`]]);
document.querySelector('#tasks').innerHTML=table(tasks,[['Type',x=>esc(x.task_type)],['Status',x=>esc(x.status)],['Correlation ID',x=>`<code>${esc(x.correlation_id)}</code>`],['Error',x=>esc((x.error||{}).error||'')]]);
document.querySelector('#drafts').innerHTML=table(drafts,[['To',x=>esc(x.recipient)],['Subject',x=>esc(x.subject)],['Status',x=>esc(x.status)],['Approval',x=>esc(x.approval_id||'')]]);
}catch(e){document.body.insertAdjacentHTML('beforeend',`<p>${esc(e.message)}</p>`)} }
load();setInterval(load,15000);
</script></body></html>"""
