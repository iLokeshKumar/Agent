const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const state = {
  health: null,
  agentsHealth: null,
  tasks: [],
  approvals: [],
  lanes: [],
  mailHealth: null,
  inbox: [],
  drafts: [],
  staged: [],
  tallyEntity: "YEXIS-EL",
  activity: [],
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function fmt(value) {
  if (!value) return "-";
  const text = String(value);
  if (text.includes("T")) {
    const parsed = new Date(text);
    if (!Number.isNaN(parsed.getTime())) {
      return parsed.toLocaleString();
    }
  }
  return text;
}

function badgeClass(value) {
  const text = String(value ?? "").toUpperCase();
  if (["OK", "GREEN", "HEALTHY", "APPROVED", "POSTED", "SENT", "ALLOW"].includes(text)) return "badge ok";
  if (["WARN", "YELLOW", "PENDING", "STAGED", "AWAITING_APPROVAL", "RECEIVED"].includes(text)) return "badge warn";
  if (["RED", "FAILED", "REJECTED", "POST_FAILED", "DISABLED"].includes(text)) return "badge danger";
  return "badge";
}

function setNotice(id, message, kind = "") {
  const el = document.getElementById(id);
  if (!el) return;
  el.className = kind ? `notice ${kind}` : "notice";
  el.textContent = message;
}

function pushActivity(title, detail, kind = "info") {
  state.activity.unshift({
    title,
    detail,
    kind,
    when: new Date().toLocaleTimeString(),
  });
  state.activity = state.activity.slice(0, 6);
  renderActivity();
}

function toast(title, detail, kind = "info") {
  const host = document.getElementById("toast-host");
  if (!host) return;
  const node = document.createElement("div");
  node.className = `toast`;
  node.setAttribute("data-kind", kind);
  node.innerHTML = `<strong>${escapeHtml(title)}</strong><small>${escapeHtml(detail)}</small>`;
  host.appendChild(node);
  window.setTimeout(() => {
    node.style.opacity = "0";
    node.style.transform = "translateY(8px)";
  }, 2600);
  window.setTimeout(() => node.remove(), 3200);
}

function closeOpenModalStack() {
  ["#cmp-wizard", "#tmpl-modal", "#tmpl-approve-modal", "#cmp-send-modal"].forEach((selector) => {
    const el = document.querySelector(selector);
    if (el) el.classList.remove("open");
  });
}

function openModal(selectorOrNode) {
  const el = typeof selectorOrNode === "string" ? document.querySelector(selectorOrNode) : selectorOrNode;
  if (!el) return;
  closeOpenModalStack();
  el.classList.add("open");
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const raw = await res.text();
  let body = null;
  if (raw) {
    try {
      body = JSON.parse(raw);
    } catch {
      body = raw;
    }
  }
  if (!res.ok) {
    const detail = typeof body === "object" && body && body.detail ? body.detail : raw || res.statusText;
    throw new Error(detail);
  }
  return body;
}

function renderHero() {
  const health = state.health;
  const agents = state.agentsHealth;
  const lanes = state.lanes;
  const tasks = state.tasks;
  const pendingApprovals = state.approvals;
  const mail = state.mailHealth;

  const summary = health && agents ? `${health.status} backend, ${agents.summary?.healthy ?? 0}/${agents.summary?.total_agents ?? 0} agents healthy` : "Waiting for live backend data";
  $("#status-title").textContent = summary;

  const counts = [
    ["Tasks", tasks.length],
    ["Approvals", pendingApprovals.length],
    ["Lanes", lanes.length],
    ["Mail", mail ? (mail.smtp_send_enabled ? "Send enabled" : "Draft-only") : "Loading"],
  ];

  $("#status-copy").textContent = mail
    ? `Mail intake is ${mail.pop3_configured ? "configured" : "not configured"}, SMTP is ${mail.smtp_configured ? "configured" : "not configured"}, and outbound sending is ${mail.smtp_send_enabled ? "enabled" : "disabled"}.`
    : "Waiting for mailbox status.";

  $("#hero-stats").innerHTML = counts
    .map(([label, value]) => `
      <div class="stat">
        <span class="label">${escapeHtml(label)}</span>
        <span class="value">${escapeHtml(value)}</span>
      </div>
    `)
    .join("");

  $("#hero-strip").innerHTML = [
    ["Backend", health?.status || "loading"],
    ["Agents", agents ? `${agents.summary?.healthy ?? 0}/${agents.summary?.total_agents ?? 0}` : "loading"],
    ["Tally", state.staged.length ? `${state.staged.length} staged` : "idle"],
    ["Mail", mail ? (mail.smtp_send_enabled ? "send on" : "draft only") : "loading"],
  ].map(([label, value]) => `<span class="pill">${escapeHtml(label)}: ${escapeHtml(value)}</span>`).join("");

  $("#last-updated").textContent = `Last refreshed: ${new Date().toLocaleTimeString()}`;
}

function renderActivity() {
  const feed = $("#activity-feed");
  if (!feed) return;
  if (!state.activity.length) {
    feed.innerHTML = `<div class="muted">No actions yet.</div>`;
    return;
  }
  feed.innerHTML = state.activity.map((entry) => `
    <div class="activity-item">
      <div class="left">
        <strong>${escapeHtml(entry.title)}</strong>
        <span class="muted">${escapeHtml(entry.detail)}</span>
      </div>
      <span class="when">${escapeHtml(entry.when)}</span>
    </div>
  `).join("");
}

function renderTasks() {
  const rows = state.tasks;
  if (!rows.length) {
    $("#tasks-list").innerHTML = `<div class="notice">No tasks found.</div>`;
    return;
  }

  $("#tasks-list").innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Task</th>
          <th>Status</th>
          <th>Requested by</th>
          <th>Priority</th>
          <th>Correlation</th>
          <th>Created</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map((row) => `
          <tr>
            <td>
              <strong>${escapeHtml(row.task_type)}</strong><br />
              <span class="muted">${escapeHtml(row.source || "manual")}</span>
            </td>
            <td><span class="${badgeClass(row.status)}">${escapeHtml(row.status)}</span></td>
            <td>${escapeHtml(row.requested_by || "-")}</td>
            <td>${escapeHtml(row.priority)}</td>
            <td><code>${escapeHtml(row.correlation_id)}</code></td>
            <td>${escapeHtml(fmt(row.created_at))}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

function renderAgents() {
  const rows = state.agentsHealth?.agents || [];
  if (!rows.length) {
    $("#agents-list").innerHTML = `<div class="notice">No agents reported yet.</div>`;
    return;
  }

  $("#agents-list").innerHTML = rows.map((agent) => `
    <article class="card">
      <div class="card-head">
        <div>
          <h4>${escapeHtml(agent.agent_code)} - ${escapeHtml(agent.name)}</h4>
          <div class="compact">${escapeHtml(agent.suite || "unknown")} / ${escapeHtml(agent.version || "unknown")}</div>
        </div>
        <span class="${badgeClass(agent.health)}">${escapeHtml(agent.health)}</span>
      </div>
      <div class="compact" style="margin-top:10px">${escapeHtml(agent.description || "")}</div>
      <div class="action-line" style="margin-top:12px">
        <span class="pill">Enabled: ${escapeHtml(agent.enabled)}</span>
        <span class="pill">Autonomy: ${escapeHtml(agent.autonomy_level)}</span>
        <span class="pill">Runs: ${escapeHtml(agent.stats?.total_runs ?? 0)}</span>
        <span class="pill">Success: ${escapeHtml(agent.stats?.completed ?? 0)}</span>
      </div>
    </article>
  `).join("");
}

function approvalCard(item) {
  const payload = escapeHtml(JSON.stringify(item.payload, null, 2));
  return `
    <article class="card">
      <div class="card-head">
        <div>
          <h4>${escapeHtml(item.approval_type)}</h4>
          <div class="compact">Requested by ${escapeHtml(item.requested_by || "-")}</div>
        </div>
        <span class="${badgeClass(item.status)}">${escapeHtml(item.status)}</span>
      </div>
      <pre class="compact" style="margin-top:12px">${payload}</pre>
      <div class="action-row" style="margin-top:12px">
        <button class="primary" data-approve="${escapeHtml(item.id)}">Approve</button>
        <button class="ghost" data-reject="${escapeHtml(item.id)}">Reject</button>
      </div>
    </article>
  `;
}

function renderApprovals() {
  const rows = state.approvals;
  if (!rows.length) {
    $("#approvals-list").innerHTML = `<div class="notice">No pending approvals.</div>`;
    return;
  }

  $("#approvals-list").innerHTML = rows.map(approvalCard).join("");
}

function laneCard(lane) {
  return `
    <article class="lane-card">
      <div class="row">
        <div>
          <h4>${escapeHtml(lane.lane)}</h4>
          <div class="compact">${escapeHtml(lane.mode || "supervised")}</div>
        </div>
        <span class="pill">${escapeHtml(lane.autonomy_state)}</span>
      </div>
      <div class="compact" style="margin-top:8px">${escapeHtml(lane.notes || "No notes")}</div>
      <div class="controls">
        <div class="inline">
          <label>
            Autonomy
            <select data-autonomy="${escapeHtml(lane.lane)}">
              ${["A1", "A2", "A3"].map((value) => `<option value="${value}" ${value === lane.autonomy_state ? "selected" : ""}>${value}</option>`).join("")}
            </select>
          </label>
          <label>
            Dry run
            <select data-dryrun="${escapeHtml(lane.lane)}">
              <option value="true" ${lane.dry_run ? "selected" : ""}>true</option>
              <option value="false" ${!lane.dry_run ? "selected" : ""}>false</option>
            </select>
          </label>
        </div>
        <div class="inline">
          <button class="ghost" data-kill="${escapeHtml(lane.lane)}">${lane.kill_switch ? "Release" : "Pull"} kill-switch</button>
          <button class="primary" data-lane-save="${escapeHtml(lane.lane)}">Apply lane state</button>
        </div>
      </div>
    </article>
  `;
}

function renderLanes() {
  const rows = state.lanes;
  if (!rows.length) {
    $("#lanes-list").innerHTML = `<div class="notice">No lanes found.</div>`;
    return;
  }

  $("#lanes-list").innerHTML = rows.map(laneCard).join("");
}

function renderTally() {
  const rows = state.staged;
  if (!rows.length) {
    $("#tally-list").innerHTML = `<div class="notice">No staged rows loaded.</div>`;
    return;
  }

  const hasStagedRows = rows.some((r) => r.status === "STAGED");

  $("#tally-list").innerHTML = `
    ${hasStagedRows ? `
    <div class="action-row" style="margin-bottom:10px">
      <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
        <input type="checkbox" id="tally-select-all"> Select all staged
      </label>
      <button class="primary" id="tally-approve-sel">Approve selected</button>
      <button class="ghost" id="tally-reject-sel">Reject selected</button>
    </div>` : ""}
    <table>
      <thead>
        <tr>
          <th></th>
          <th>Txn</th>
          <th>Line</th>
          <th>Date</th>
          <th>Status</th>
          <th>Account</th>
          <th>Debit</th>
          <th>Credit</th>
          <th>Voucher</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map((row) => `
          <tr>
            <td>${row.status === "STAGED" ? `<input type="checkbox" class="tally-row-check" value="${escapeHtml(row.id)}">` : ""}</td>
            <td>${escapeHtml(row.books_txn_id)}</td>
            <td>${escapeHtml(row.line_no)}</td>
            <td>${escapeHtml(row.voucher_date || "-")}</td>
            <td><span class="${badgeClass(row.status)}">${escapeHtml(row.status)}</span></td>
            <td>${escapeHtml(row.tally_account || "-")}</td>
            <td>${escapeHtml(row.debit ?? 0)}</td>
            <td>${escapeHtml(row.credit ?? 0)}</td>
            <td><code>${escapeHtml(row.tally_voucher_no || "-")}</code></td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;

  const selectAll = $("#tally-select-all");
  if (selectAll) {
    selectAll.addEventListener("change", () => {
      $$(".tally-row-check").forEach((cb) => { cb.checked = selectAll.checked; });
    });
  }
  const approveBtn = $("#tally-approve-sel");
  if (approveBtn) approveBtn.addEventListener("click", approveTallySelected);
  const rejectBtn = $("#tally-reject-sel");
  if (rejectBtn) rejectBtn.addEventListener("click", rejectTallySelected);
}

function renderMail() {
  const health = state.mailHealth;
  const sendEnabled = Boolean(health?.smtp_send_enabled);
  $("#mail-health").textContent = health
    ? `POP3: ${health.pop3_configured ? "configured" : "missing"} | SMTP: ${health.smtp_configured ? "configured" : "missing"} | Send enabled: ${sendEnabled ? "yes" : "no"}`
    : "Loading mailbox status...";

  // Inbox (received)
  const inbox = state.inbox;
  if (!inbox.length) {
    $("#inbox-list").innerHTML = `<div class="notice">No received emails. Click "Poll POP3" to check inbox.</div>`;
  } else {
    $("#inbox-list").innerHTML = inbox.map((msg) => `
      <article class="card">
        <div class="card-head">
          <div>
            <h4>${escapeHtml(msg.subject || "(no subject)")}</h4>
            <div class="compact">From ${escapeHtml(msg.sender || "-")} &nbsp;·&nbsp; ${escapeHtml((msg.created_at || "").slice(0, 16).replace("T", " "))}</div>
          </div>
          <span class="pill">${escapeHtml(msg.status || "received")}</span>
        </div>
        <pre class="compact" style="margin-top:8px;white-space:pre-wrap;max-height:120px;overflow:auto">${escapeHtml((msg.body || "").slice(0, 500))}${(msg.body || "").length > 500 ? "\n…" : ""}</pre>
      </article>
    `).join("");
  }

  const drafts = state.drafts;
  if (!drafts.length) {
    $("#drafts-list").innerHTML = `<div class="notice">No outbound drafts.</div>`;
    return;
  }

  $("#drafts-list").innerHTML = drafts.map((draft) => {
    const atts = draft.attachments || [];
    const attHtml = atts.length
      ? `<div style="margin-top:8px">
          <span class="muted" style="font-size:12px">Attachments:</span>
          <div class="action-row" style="flex-wrap:wrap;gap:6px;margin-top:4px">
            ${atts.map((a) => `
              <span class="pill" style="display:flex;align-items:center;gap:4px">
                ${escapeHtml(a.filename)}
                <span class="muted" style="font-size:10px">(${Math.round((a.size || 0) / 1024)}KB)</span>
                <button style="background:none;border:none;cursor:pointer;color:#e55" title="Remove"
                  data-remove-att="${escapeHtml(draft.id)}" data-att-name="${escapeHtml(a.filename)}">✕</button>
              </span>
            `).join("")}
          </div>
          <label style="margin-top:6px;display:block;font-size:12px">
            Add more files
            <input type="file" multiple style="display:block;margin-top:2px"
              data-add-att="${escapeHtml(draft.id)}" />
          </label>
        </div>`
      : `<label style="margin-top:8px;display:block;font-size:12px;color:#888">
          Attach files
          <input type="file" multiple style="display:block;margin-top:2px"
            data-add-att="${escapeHtml(draft.id)}" />
        </label>`;

    return `
      <article class="card">
        <div class="card-head">
          <div>
            <h4>${escapeHtml(draft.subject || "(no subject)")}</h4>
            <div class="compact">To ${escapeHtml(draft.recipient || "-")}</div>
          </div>
          <span class="${badgeClass(draft.status)}">${escapeHtml(draft.status)}</span>
        </div>
        <pre class="compact" style="margin-top:12px;white-space:pre-wrap">${escapeHtml(draft.body || "")}</pre>
        ${attHtml}
        <div class="action-row" style="margin-top:12px">
          <span class="pill">Approval: ${escapeHtml((draft.approval_id || "-").slice(0, 8))}…</span>
          <button class="primary" data-send-draft="${escapeHtml(draft.id)}" ${sendEnabled ? "" : "disabled title=\"Restart backend to enable sending\""}>Send</button>
        </div>
      </article>
    `;
  }).join("");
}

async function loadDrafts() {
  [state.drafts, state.inbox, state.mailHealth] = await Promise.all([
    api("/mail/drafts"),
    api("/mail/inbox"),
    api("/mail/health"),
  ]);
  renderMail();
}

async function loadOverview() {
  const [health, agentsHealth, tasks, approvals, lanes, mailHealth, drafts, inbox] = await Promise.all([
    api("/health"),
    api("/agents/health"),
    api("/tasks?limit=20"),
    api("/approvals?status=PENDING"),
    api("/lanes"),
    api("/mail/health"),
    api("/mail/drafts"),
    api("/mail/inbox"),
  ]);

  state.health = health;
  state.agentsHealth = agentsHealth;
  state.tasks = tasks;
  state.approvals = approvals;
  state.lanes = lanes;
  state.mailHealth = mailHealth;
  state.drafts = drafts;
  state.inbox = inbox;

  renderHero();
  renderTasks();
  renderAgents();
  renderApprovals();
  renderLanes();
  renderMail();
}

async function loadTally(entity = state.tallyEntity) {
  state.tallyEntity = entity;
  $("#tally-entity").value = entity;
  state.staged = await api(`/mcp/tally/list_staged?entity=${encodeURIComponent(entity)}&limit=50`);
  renderTally();
  setNotice("tally-status", `Loaded ${state.staged.length} staged rows for ${entity}.`);
}

async function createTask(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  let payload = {};
  try {
    payload = JSON.parse(data.get("payload"));
  } catch (error) {
    setNotice("task-form-status", `Payload JSON is invalid: ${error.message}`, "danger");
    return;
  }

  const body = {
    task_type: data.get("task_type"),
    requested_by: data.get("requested_by") || undefined,
    source: data.get("source") || undefined,
    priority: Number(data.get("priority") || 100),
    payload,
  };

  try {
    const result = await api("/tasks", {
      method: "POST",
      body: JSON.stringify(body),
    });
    setNotice("task-form-status", `Queued task ${result.task_id} (${result.status}).`);
    pushActivity("Task queued", `${body.task_type} by ${body.requested_by || "admin"}`, "ok");
    toast("Task queued", `${body.task_type} was sent to the backend.`, "ok");
    await loadOverview();
  } catch (error) {
    setNotice("task-form-status", error.message, "danger");
    pushActivity("Task failed", error.message, "danger");
    toast("Task failed", error.message, "danger");
  }
}

async function createDraft(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  const body = {
    recipient: data.get("recipient"),
    subject: data.get("subject"),
    body: data.get("body"),
  };

  try {
    const result = await api("/mail/drafts", {
      method: "POST",
      body: JSON.stringify(body),
    });

    // Upload any selected files
    const fileInput = document.getElementById("draft-files");
    const files = fileInput ? Array.from(fileInput.files) : [];
    if (files.length) {
      setNotice("draft-form-status", `Uploading ${files.length} file(s)…`);
      for (const file of files) {
        const fd = new FormData();
        fd.append("file", file);
        await fetch(`/mail/drafts/${result.id}/attachments`, { method: "POST", body: fd });
      }
      if (fileInput) fileInput.value = "";
    }

    const attachNote = files.length ? ` + ${files.length} attachment(s)` : "";
    setNotice("draft-form-status", `Draft created${attachNote}.`);
    pushActivity("Draft created", `${body.recipient} / ${body.subject}${attachNote}`, "ok");
    toast("Draft created", `Outbound mail staged for approval${attachNote}.`, "ok");
    await loadDrafts();
  } catch (error) {
    setNotice("draft-form-status", error.message, "danger");
    pushActivity("Draft failed", error.message, "danger");
    toast("Draft failed", error.message, "danger");
  }
}

async function pollMail() {
  try {
    setNotice("mail-health", "Polling POP3 inbox...");
    await api("/mail/poll?limit=25", { method: "POST" });
    setNotice("mail-health", "POP3 poll completed.");
    pushActivity("POP3 poll", "Inbox was checked for new messages.", "ok");
    toast("POP3 poll completed", "Inbox intake ran successfully.", "ok");
    await loadOverview();
  } catch (error) {
    setNotice("mail-health", error.message, "danger");
    pushActivity("POP3 poll failed", error.message, "danger");
    toast("POP3 poll failed", error.message, "danger");
  }
}

async function saveLane(lane) {
  const autonomy = $(`select[data-autonomy="${lane}"]`).value;
  const dryRun = $(`select[data-dryrun="${lane}"]`).value === "true";
  const laneRow = state.lanes.find((item) => item.lane === lane);

  try {
    await api(`/lanes/${encodeURIComponent(lane)}/autonomy`, {
      method: "POST",
      body: JSON.stringify({ state: autonomy }),
    });
    await api(`/lanes/${encodeURIComponent(lane)}/dry-run`, {
      method: "POST",
      body: JSON.stringify({ enabled: dryRun }),
    });
    setNotice("tally-status", `Lane ${lane} updated.`);
    pushActivity("Lane updated", `${lane}: autonomy ${autonomy}, dry_run ${dryRun}`, "ok");
    toast("Lane updated", `${lane} settings were saved.`, "ok");
    await loadOverview();
  } catch (error) {
    setNotice("tally-status", error.message, "danger");
    pushActivity("Lane update failed", `${lane}: ${error.message}`, "danger");
    toast("Lane update failed", error.message, "danger");
  }
}

async function toggleKill(lane) {
  const laneRow = state.lanes.find((item) => item.lane === lane);
  try {
    await api(`/lanes/${encodeURIComponent(lane)}/kill-switch`, {
      method: "POST",
      body: JSON.stringify({ pulled: !laneRow.kill_switch }),
    });
    pushActivity("Kill switch toggled", `${lane} is now ${!laneRow.kill_switch ? "pulled" : "released"}`, "warn");
    toast("Lane safety updated", `${lane} kill-switch changed.`, "warn");
    await loadOverview();
  } catch (error) {
    setNotice("tally-status", error.message, "danger");
    pushActivity("Kill switch failed", `${lane}: ${error.message}`, "danger");
    toast("Lane update failed", error.message, "danger");
  }
}

async function decideApproval(id, action) {
  const approvedBy = prompt("Your user id:", "admin");
  if (!approvedBy) return;
  const reason = prompt("Reason (optional):", "") || undefined;
  try {
    await api(`/approvals/${encodeURIComponent(id)}/${action}`, {
      method: "POST",
      body: JSON.stringify({ approved_by: approvedBy, reason }),
    });
    pushActivity("Approval decided", `${action.toUpperCase()} by ${approvedBy}`, action === "approve" ? "ok" : "warn");
    toast("Approval processed", `${action.toUpperCase()} recorded for ${approvedBy}.`, action === "approve" ? "ok" : "warn");
    await loadOverview();
  } catch (error) {
    alert(error.message);
    pushActivity("Approval failed", error.message, "danger");
    toast("Approval failed", error.message, "danger");
  }
}

async function stageVoucher(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  let entries = [];
  try {
    entries = JSON.parse(data.get("entries"));
  } catch (error) {
    setNotice("tally-stage-status", `Entries JSON is invalid: ${error.message}`, "danger");
    return;
  }

  const body = {
    entity: data.get("entity"),
    books_txn_id: data.get("books_txn_id"),
    company: data.get("company"),
    entries,
  };

  try {
    const result = await api("/mcp/tally/stage_voucher", {
      method: "POST",
      body: JSON.stringify(body),
    });
    setNotice("tally-stage-status", `Staged ${result.staged} lines.`);
    pushActivity("Voucher staged", `${body.entity} / ${body.books_txn_id}`, "ok");
    toast("Tally voucher staged", `${result.staged} line(s) prepared.`, "ok");
    await loadTally(body.entity);
  } catch (error) {
    setNotice("tally-stage-status", error.message, "danger");
    pushActivity("Voucher stage failed", error.message, "danger");
    toast("Tally staging failed", error.message, "danger");
  }
}

async function postApproved() {
  try {
    const result = await api("/mcp/tally/post_approved", {
      method: "POST",
      body: JSON.stringify({ entity: state.tallyEntity }),
    });
    setNotice("tally-status", `Posted ${result.posted_transactions.length} transactions.`);
    pushActivity("Tally post", `${result.posted_transactions.length} transaction(s) posted`, "ok");
    toast("Tally post complete", `${result.posted_transactions.length} transaction(s) posted.`, "ok");
    await loadTally(state.tallyEntity);
  } catch (error) {
    setNotice("tally-status", error.message, "danger");
    pushActivity("Tally post failed", error.message, "danger");
    toast("Tally post failed", error.message, "danger");
  }
}

async function approveTallySelected() {
  const ids = $$(".tally-row-check:checked").map((cb) => cb.value);
  if (!ids.length) { toast("Nothing selected", "Check at least one STAGED row.", "warn"); return; }
  const approvedBy = prompt("Your user id:", "admin");
  if (!approvedBy) return;
  try {
    const result = await api("/mcp/tally/approve", {
      method: "POST",
      body: JSON.stringify({ staging_ids: ids, approved_by: approvedBy }),
    });
    setNotice("tally-status", `Approved ${result.approved} voucher(s) by ${approvedBy}.`);
    pushActivity("Tally approve", `${result.approved} row(s) approved by ${approvedBy}`, "ok");
    toast("Approved", `${result.approved} voucher(s) approved.`, "ok");
    await loadTally(state.tallyEntity);
  } catch (error) {
    setNotice("tally-status", error.message, "danger");
    toast("Approve failed", error.message, "danger");
  }
}

async function rejectTallySelected() {
  const ids = $$(".tally-row-check:checked").map((cb) => cb.value);
  if (!ids.length) { toast("Nothing selected", "Check at least one STAGED row.", "warn"); return; }
  const reason = prompt("Rejection reason:", "");
  if (reason === null) return;
  try {
    const result = await api("/mcp/tally/reject", {
      method: "POST",
      body: JSON.stringify({ staging_ids: ids, reason: reason || "Rejected by accountant" }),
    });
    setNotice("tally-status", `Rejected ${result.rejected} voucher(s).`);
    pushActivity("Tally reject", `${result.rejected} row(s) rejected`, "warn");
    toast("Rejected", `${result.rejected} voucher(s) rejected.`, "warn");
    await loadTally(state.tallyEntity);
  } catch (error) {
    setNotice("tally-status", error.message, "danger");
    toast("Reject failed", error.message, "danger");
  }
}

async function fetchTrialBalance() {
  try {
    const result = await api(`/mcp/tally/fetch_trial_balance?entity=${encodeURIComponent(state.tallyEntity)}`);
    const lines = result.lines || [];
    const count = lines.length;
    setNotice("tally-status", `Trial balance loaded for ${result.entity} — ${count} ledger(s).`);

    if (!count) {
      $("#tally-list").innerHTML = `<div class="notice">No ledger data returned from Tally.</div>`;
    } else if (lines[0].ledger) {
      $("#tally-list").innerHTML = `
        <table>
          <thead><tr><th>Ledger / Group</th><th>Dr</th><th>Cr</th></tr></thead>
          <tbody>
            ${lines.map((l) => `
              <tr>
                <td>${escapeHtml(l.ledger)}</td>
                <td style="text-align:right">${escapeHtml(l.dr || "-")}</td>
                <td style="text-align:right">${escapeHtml(l.cr || "-")}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      `;
    } else {
      $("#tally-list").innerHTML = `<pre style="font-size:11px;overflow:auto;max-height:400px">${escapeHtml(lines[0]?.raw_xml || "")}</pre>`;
    }

    pushActivity("Trial balance", `${result.entity} — ${count} ledger(s)`, "info");
    toast("Trial balance loaded", `${result.entity} fetched.`, "info");
  } catch (error) {
    setNotice("tally-status", error.message, "danger");
    pushActivity("Trial balance failed", error.message, "danger");
    toast("Trial balance failed", error.message, "danger");
  }
}

async function sendDraft(id) {
  try {
    await api(`/mail/drafts/${encodeURIComponent(id)}/send`, { method: "POST" });
    pushActivity("Draft send", `${id} sent after approval`, "warn");
    toast("Draft sent", "Outbound send was approved and dispatched.", "warn");
    await loadOverview();
  } catch (error) {
    alert(error.message);
    pushActivity("Draft send failed", error.message, "danger");
    toast("Draft send failed", error.message, "danger");
  }
}

async function uploadAttachmentsToExisting(draftId, files) {
  try {
    for (const file of files) {
      const fd = new FormData();
      fd.append("file", file);
      await fetch(`/mail/drafts/${draftId}/attachments`, { method: "POST", body: fd });
    }
    toast("Attached", `${files.length} file(s) added.`, "ok");
    await loadDrafts();
  } catch (error) {
    toast("Attach failed", error.message, "danger");
  }
}

async function removeAttachment(draftId, filename) {
  try {
    await api(`/mail/drafts/${draftId}/attachments/${encodeURIComponent(filename)}`, { method: "DELETE" });
    toast("Removed", `${filename} removed.`, "ok");
    await loadDrafts();
  } catch (error) {
    toast("Remove failed", error.message, "danger");
  }
}

function wireEvents() {
  $("#refresh-all").addEventListener("click", loadAll);
  $("#reload-tasks").addEventListener("click", loadOverview);
  $("#tasks-filter").addEventListener("change", async (event) => {
    const status = event.currentTarget.value;
    const suffix = status ? `?limit=20&status=${encodeURIComponent(status)}` : "?limit=20";
    state.tasks = await api(`/tasks${suffix}`);
    renderTasks();
  });

  $("#task-form").addEventListener("submit", createTask);
  $("#draft-form").addEventListener("submit", createDraft);
  $("#mail-poll").addEventListener("click", pollMail);
  $("#mail-refresh").addEventListener("click", loadDrafts);
  $("#tally-stage-form").addEventListener("submit", stageVoucher);

  // Keep company dropdown in sync with entity selection
  const ENTITY_COMPANY = {
    "YEXIS-EL": "YEXIS ELECTRONICS - K",
    "YEXIS-SL": "YEXIS ELECTRONICS - T",
    "YEXIS-SOL": "Yexis Solutions",
  };
  const stageEntitySel = $("#tally-stage-entity");
  if (stageEntitySel) {
    stageEntitySel.addEventListener("change", () => {
      const compSel = $("#tally-stage-company");
      if (compSel) compSel.value = ENTITY_COMPANY[stageEntitySel.value] || "";
    });
  }

  $("#tally-refresh").addEventListener("click", () => loadTally(state.tallyEntity));
  $("#tally-post").addEventListener("click", postApproved);
  $("#tally-trial").addEventListener("click", fetchTrialBalance);
  $("#tally-entity").addEventListener("change", (event) => loadTally(event.currentTarget.value));

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const approve = target.closest("[data-approve]");
    const reject = target.closest("[data-reject]");
    const laneSave = target.closest("[data-lane-save]");
    const laneKill = target.closest("[data-kill]");
    const sendDraftButton = target.closest("[data-send-draft]");
    const removeAtt = target.closest("[data-remove-att]");

    if (approve) decideApproval(approve.getAttribute("data-approve"), "approve");
    if (reject) decideApproval(reject.getAttribute("data-reject"), "reject");
    if (laneSave) saveLane(laneSave.getAttribute("data-lane-save"));
    if (laneKill) toggleKill(laneKill.getAttribute("data-kill"));
    if (sendDraftButton) sendDraft(sendDraftButton.getAttribute("data-send-draft"));
    if (removeAtt) removeAttachment(removeAtt.getAttribute("data-remove-att"), removeAtt.getAttribute("data-att-name"));
  });

  document.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const addAtt = target.closest("[data-add-att]");
    if (addAtt && target.files && target.files.length) {
      uploadAttachmentsToExisting(addAtt.getAttribute("data-add-att"), Array.from(target.files));
      target.value = "";
    }
  });
}

async function loadAll() {
  $("#refresh-all").disabled = true;
  try {
    await Promise.all([
      loadOverview(),
      loadTally(state.tallyEntity),
    ]);
    pushActivity("Refresh complete", "Dashboard data reloaded.", "info");
  } catch (error) {
    setNotice("tally-status", error.message, "danger");
    pushActivity("Refresh failed", error.message, "danger");
    toast("Refresh failed", error.message, "danger");
  } finally {
    $("#refresh-all").disabled = false;
  }
}

wireEvents();
loadAll();
setInterval(loadAll, 15000);

// ═══════════════════════════════════════════════════════════════════════
// LIVE DEMO MODULE
// ═══════════════════════════════════════════════════════════════════════

const demo = {
  scenarios: [],
  activeTaskId: null,
  activeScenarioId: null,
  pollTimer: null,
  approvalId: null,
  zohoSnapshot: null,
};

const SCENARIO_COLORS = {
  f1_collections: "#f59e0b",
  f3_books_tally: "#8b5cf6",
  m2_gsm_brief:   "#10b981",
  p1_purchase:    "#3b82f6",
};

async function initDemo() {
  try {
    demo.scenarios = await api("/demo/scenarios");
    renderScenarioCards();
    // Check preflight for Zoho badge
    const pf = await api("/demo/preflight");
    const badge = document.getElementById("demo-zoho-badge");
    if (badge) {
      if (pf.zoho_configured) {
        const connectionLabel = pf.zoho_connection === "personal"
          ? "Your Zoho Connected"
          : "Shared Zoho Connected";
        badge.textContent = `● ${connectionLabel} (Org: ${pf.zoho_org_id || "–"})`;
        badge.style.background = "#052e16";
        badge.style.color = "#4ade80";
        badge.style.borderColor = "#166534";
      } else {
        badge.textContent = "✗ Zoho Not Connected";
        badge.style.background = "#450a0a";
        badge.style.color = "#f87171";
        badge.style.borderColor = "#7f1d1d";
      }
    }
  } catch (e) {
    const el = document.getElementById("demo-scenarios");
    if (el) el.innerHTML = `<p class="muted">Could not load scenarios: ${escapeHtml(e.message)}</p>`;
  }
}

function renderScenarioCards() {
  const el = document.getElementById("demo-scenarios");
  if (!el) return;
  el.innerHTML = demo.scenarios.map(s => {
    const color = SCENARIO_COLORS[s.id] || "#6b7280";
    return `
      <div class="demo-card" id="card-${s.id}">
        <div class="demo-card-icon" style="color:${color}">${escapeHtml(s.icon)}</div>
        <p class="demo-card-name">${escapeHtml(s.name)}</p>
        <p class="demo-card-sub">${escapeHtml(s.subtitle)}</p>
        <p class="demo-card-desc">${escapeHtml(s.description)}</p>
        <p class="demo-card-audience">Audience: ${escapeHtml(s.audience)}</p>
        <button class="demo-run-btn${s.run_enabled === false ? " is-unavailable" : ""}" id="run-${s.id}" style="background:${color}"
          ${s.run_enabled === false ? "disabled" : ""}
          onclick="runScenario('${s.id}')">${escapeHtml(s.run_label || "▶ Run")}</button>
      </div>`;
  }).join("");
}

async function runScenario(scenarioId) {
  // Reset UI
  stopDemoPoll();
  demo.activeScenarioId = scenarioId;
  demo.approvalId = null;

  // Disable all run buttons
  demo.scenarios.forEach(s => {
    const btn = document.getElementById(`run-${s.id}`);
    if (btn && s.run_enabled !== false) {
      btn.disabled = true;
      btn.textContent = scenarioId === s.id ? "▶ Running…" : "▶ Run";
    }
  });

  const scenario = demo.scenarios.find(s => s.id === scenarioId);
  const color = SCENARIO_COLORS[scenarioId] || "#6b7280";

  // Show output panel with loading state
  const outputPanel = document.getElementById("demo-output");
  outputPanel.style.display = "block";
  outputPanel.scrollIntoView({ behavior: "smooth", block: "start" });

  document.getElementById("demo-output-title").textContent = scenario ? scenario.name : scenarioId;
  document.getElementById("demo-output-title").style.color = color;
  setDemoStatus("QUEUED");
  document.getElementById("demo-zoho-panel").innerHTML = `<p class="muted" style="padding:.5rem">Fetching from Zoho Books…</p>`;
  document.getElementById("demo-result-panel").innerHTML = `<p class="muted" style="padding:.5rem">Waiting for agent…</p>`;
  document.getElementById("demo-ledger-panel").innerHTML = "";
  document.getElementById("demo-approval-card").style.display = "none";
  document.getElementById("demo-task-meta").textContent = "";

  try {
    const run = await api(`/demo/run/${scenarioId}`, { method: "POST" });
    demo.activeTaskId = run.task_id;
    demo.zohoSnapshot = run.zoho_snapshot;

    renderZohoSnapshot(run.zoho_snapshot, scenarioId);
    document.getElementById("demo-task-meta").textContent =
      `Task: ${run.task_id.slice(0,8)}… | Correlation: ${(run.correlation_id||"").slice(0,8)}…`;

    // Start polling
    demo.pollTimer = setInterval(() => pollDemoTask(run.task_id), 1200);

  } catch (e) {
    setDemoStatus("FAILED");
    document.getElementById("demo-result-panel").innerHTML =
      `<p style="color:#f87171;padding:.5rem">Error: ${escapeHtml(e.message)}</p>`;
    resetRunButtons();
  }
}

function renderZohoSnapshot(snap, scenarioId) {
  const el = document.getElementById("demo-zoho-panel");
  if (!snap) { el.innerHTML = `<p class="muted" style="padding:.5rem">No snapshot</p>`; return; }

  let html = "";

  if (scenarioId === "f1_collections") {
    html = `
      <div class="demo-kv"><span class="demo-kv-key">Source</span><span class="demo-kv-val">Zoho Books</span></div>
      <div class="demo-kv"><span class="demo-kv-key">Invoices found</span><span class="demo-kv-val">${snap.invoice_count ?? "–"}</span></div>
      <div class="demo-kv"><span class="demo-kv-key">Total outstanding</span><span class="demo-kv-val">₹${Number(snap.total_outstanding||0).toLocaleString("en-IN")}</span></div>
      <div style="margin-top:.5rem;font-size:.75rem;color:var(--muted)">Customers:<br>${(snap.customers||[]).map(c=>`• ${escapeHtml(c)}`).join("<br>")}</div>`;
  } else if (scenarioId === "f3_books_tally") {
    html = `
      <div class="demo-kv"><span class="demo-kv-key">Source</span><span class="demo-kv-val">Zoho Books</span></div>
      <div class="demo-kv"><span class="demo-kv-key">Date range</span><span class="demo-kv-val" style="font-size:.75rem">${escapeHtml(snap.date_range||"")}</span></div>
      <div style="margin-top:.5rem;font-size:.75rem;color:var(--muted)">${escapeHtml(snap.note||"")}</div>`;
  } else if (scenarioId === "m2_gsm_brief") {
    html = `
      <div class="demo-kv"><span class="demo-kv-key">Source</span><span class="demo-kv-val">DealerMart</span></div>
      <div style="margin-top:.5rem;font-size:.75rem;color:var(--muted)">${escapeHtml(snap.note||"")}</div>`;
  } else if (scenarioId === "p1_purchase") {
    const item = (snap.items||[])[0] || {};
    html = `
      <div class="demo-kv"><span class="demo-kv-key">Vendor</span><span class="demo-kv-val" style="font-size:.75rem">${escapeHtml(snap.vendor||"")}</span></div>
      <div class="demo-kv"><span class="demo-kv-key">Item</span><span class="demo-kv-val" style="font-size:.75rem">${escapeHtml(item.item||"")}</span></div>
      <div class="demo-kv"><span class="demo-kv-key">Qty</span><span class="demo-kv-val">${item.quantity||""} units</span></div>
      <div class="demo-kv"><span class="demo-kv-key">Unit price</span><span class="demo-kv-val">₹${Number(item.unit_price||0).toLocaleString("en-IN")}</span></div>
      <div class="demo-kv"><span class="demo-kv-key">Budget limit</span><span class="demo-kv-val">${escapeHtml(snap.budget_limit||"")}</span></div>`;
  } else {
    html = Object.entries(snap).map(([k,v]) =>
      `<div class="demo-kv"><span class="demo-kv-key">${escapeHtml(k)}</span><span class="demo-kv-val">${escapeHtml(String(v))}</span></div>`
    ).join("");
  }

  el.innerHTML = html;
}

async function pollDemoTask(taskId) {
  try {
    const data = await api(`/demo/task/${taskId}`);
    setDemoStatus(data.status);
    renderLedger(data.ledger || []);

    if (data.status === "AWAITING_APPROVAL" && data.approval) {
      demo.approvalId = data.approval.approval_id;
      showApprovalCard(data.approval);
    } else {
      document.getElementById("demo-approval-card").style.display = "none";
    }

    if (data.status === "COMPLETED" || data.status === "FAILED" || data.status === "VALIDATION_ERROR") {
      stopDemoPoll();
      resetRunButtons();
      renderAgentOutput(data, demo.activeScenarioId);
    }

    if (data.status === "RUNNING" || data.status === "QUEUED") {
      document.getElementById("demo-result-panel").innerHTML =
        `<p class="muted" style="padding:.5rem;text-align:center">
          <span style="animation:pulse 1.4s infinite;display:inline-block">⟳</span> Agent processing…</p>`;
    }

  } catch (e) {
    console.error("Poll error:", e);
  }
}

function setDemoStatus(status) {
  const el = document.getElementById("demo-output-status");
  const dot = document.getElementById("demo-status-dot");
  if (!el) return;
  el.textContent = status;
  el.className = badgeClass(status);
  const running = ["QUEUED","RUNNING","AWAITING_APPROVAL"].includes(status);
  if (dot) {
    dot.style.background = status === "COMPLETED" ? "#4ade80" : status === "FAILED" ? "#f87171" : "#f59e0b";
    dot.style.animation = running ? "pulse 1.4s infinite" : "none";
  }
}

function renderLedger(entries) {
  const el = document.getElementById("demo-ledger-panel");
  if (!el) return;
  if (!entries.length) { el.innerHTML = `<p class="muted" style="padding:.3rem">No entries yet</p>`; return; }
  el.innerHTML = entries.map(e => {
    const ts = e.ts ? new Date(e.ts).toLocaleTimeString() : "–";
    const statusColor = e.status === "SUCCESS" ? "#4ade80" : e.status === "BLOCKED" ? "#f87171" : "#f59e0b";
    return `<div class="demo-ledger-entry">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span style="font-weight:600">${escapeHtml(e.tool||e.action||"–")}</span>
        <span style="color:${statusColor};font-size:.72rem">${escapeHtml(e.status)}</span>
      </div>
      <div style="color:var(--muted);font-size:.72rem">${ts} · ${escapeHtml(e.approval_status||"–")}</div>
    </div>`;
  }).join("");
}

function showApprovalCard(approval) {
  const card = document.getElementById("demo-approval-card");
  const reason = document.getElementById("demo-approval-reason");
  if (!card || !reason) return;
  const payload = approval.payload || {};
  const text = payload.reason || `${approval.approval_type} — please review and decide.`;
  reason.textContent = text;
  card.style.display = "block";

  document.getElementById("demo-approve-btn").onclick = async () => {
    if (!demo.approvalId) return;
    try {
      await api(`/demo/approve/${demo.approvalId}`, { method: "POST" });
      card.style.display = "none";
      toast("Approved", "Agent is resuming…", "ok");
      demo.pollTimer = setInterval(() => pollDemoTask(demo.activeTaskId), 1200);
    } catch (e) {
      toast("Approval failed", e.message, "danger");
    }
  };

  document.getElementById("demo-reject-btn").onclick = () => {
    card.style.display = "none";
    setDemoStatus("REJECTED");
    toast("Rejected", "Approval rejected. No action taken.", "warn");
    stopDemoPoll();
    resetRunButtons();
  };
}

function renderAgentOutput(data, scenarioId) {
  const el = document.getElementById("demo-result-panel");
  if (!el) return;

  if (data.status === "FAILED" || data.status === "VALIDATION_ERROR") {
    const msg = data.error?.error || JSON.stringify(data.error) || "Unknown error";
    el.innerHTML = `<div style="padding:.75rem;color:#f87171">
      <strong>Error</strong><br><span style="font-size:.85rem">${escapeHtml(msg)}</span></div>`;
    return;
  }

  const output = data.result?.output || {};

  if (scenarioId === "f1_collections") {
    renderF1Output(el, output);
  } else if (scenarioId === "f3_books_tally") {
    renderF3Output(el, output);
  } else if (scenarioId === "m2_gsm_brief") {
    renderM2Output(el, output);
  } else if (scenarioId === "p1_purchase") {
    renderP1Output(el, output);
  } else {
    el.innerHTML = `<pre style="font-size:.75rem;padding:.5rem;white-space:pre-wrap;word-break:break-all">${escapeHtml(JSON.stringify(output, null, 2))}</pre>`;
  }
}

function renderF1Output(el, output) {
  const summary = output.summary || {};
  const aging = output.aging_summary || {};
  const invoices = output.processed_invoices || [];

  const agingBuckets = [
    { key:"current",     label:"Current",   color:"#4ade80" },
    { key:"days_1_30",   label:"1–30 days", color:"#facc15" },
    { key:"days_31_60",  label:"31–60",     color:"#fb923c" },
    { key:"days_61_90",  label:"61–90",     color:"#f87171" },
    { key:"over_90",     label:"90+ days",  color:"#dc2626" },
  ];

  const maxTotal = Math.max(...agingBuckets.map(b => aging[b.key]?.total || 0), 1);

  let html = `
    <div style="padding:.75rem">
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:.5rem;margin-bottom:1rem">
        <div style="text-align:center"><div style="font-size:1.4rem;font-weight:700">₹${Number(summary.total_outstanding||0).toLocaleString("en-IN")}</div><div style="font-size:.75rem;color:var(--muted)">Total Outstanding</div></div>
        <div style="text-align:center"><div style="font-size:1.4rem;font-weight:700">${summary.total_invoices||0}</div><div style="font-size:.75rem;color:var(--muted)">Invoices</div></div>
        <div style="text-align:center"><div style="font-size:1.4rem;font-weight:700;color:#f87171">${summary.oldest_overdue_days||0}d</div><div style="font-size:.75rem;color:var(--muted)">Oldest Overdue</div></div>
      </div>

      <p style="font-size:.8rem;font-weight:600;margin:.5rem 0 .4rem;color:var(--muted)">AGING BUCKETS</p>`;

  agingBuckets.forEach(b => {
    const count = aging[b.key]?.count || 0;
    const total = aging[b.key]?.total || 0;
    const pct = Math.round((total / maxTotal) * 100);
    html += `<div style="margin-bottom:.4rem">
      <div style="display:flex;justify-content:space-between;font-size:.78rem;margin-bottom:.2rem">
        <span style="color:${b.color}">${b.label}</span>
        <span>${count} inv · ₹${Number(total).toLocaleString("en-IN")}</span>
      </div>
      <div style="background:rgba(255,255,255,.07);border-radius:3px;height:6px">
        <div style="width:${pct}%;background:${b.color};height:6px;border-radius:3px;transition:width .5s"></div>
      </div>
    </div>`;
  });

  html += `<p style="font-size:.8rem;font-weight:600;margin:.9rem 0 .4rem;color:var(--muted)">PROPOSED REMINDERS (Top 6)</p>`;

  invoices.slice(0,6).forEach(inv => {
    const overdue = inv.days_overdue > 0;
    html += `<div class="demo-invoice-row">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <span style="font-weight:600;font-size:.82rem">${escapeHtml(inv.customer)}</span>
        <span style="color:${overdue?"#f87171":"#4ade80"};font-size:.78rem;font-weight:600">₹${Number(inv.outstanding_amount).toLocaleString("en-IN")}</span>
      </div>
      <div style="font-size:.75rem;color:var(--muted)">${escapeHtml(inv.invoice_number)} · ${inv.days_overdue > 0 ? `<span style="color:#f87171">${inv.days_overdue}d overdue</span>` : "current"}</div>
      <div style="font-size:.75rem;margin-top:.2rem;padding:.3rem;background:rgba(255,255,255,.04);border-radius:4px;font-style:italic">"${escapeHtml(inv.message||"")}"</div>
    </div>`;
  });

  html += `</div>`;
  el.innerHTML = html;
}

function renderF3Output(el, output) {
  const entries = output.journal_entries || output.entries || output.staged_entries || [];
  const count = output.total_entries ?? output.entry_count ?? entries.length;
  const staged = output.staged_count ?? output.tally_entries?.length ?? count;

  let html = `<div style="padding:.75rem">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:.5rem;margin-bottom:1rem">
      <div style="text-align:center"><div style="font-size:1.6rem;font-weight:700">${count}</div><div style="font-size:.75rem;color:var(--muted)">Journal Entries Fetched</div></div>
      <div style="text-align:center"><div style="font-size:1.6rem;font-weight:700;color:#8b5cf6">${staged}</div><div style="font-size:.75rem;color:var(--muted)">Staged for Tally</div></div>
    </div>`;

  if (output.reconciliation_report) {
    const r = output.reconciliation_report;
    html += `<div style="padding:.6rem;background:rgba(139,92,246,.08);border:1px solid rgba(139,92,246,.3);border-radius:6px;font-size:.8rem;margin-bottom:.75rem">
      <strong>Reconciliation</strong><br>
      Books total: ₹${Number(r.books_total||0).toLocaleString("en-IN")} · Tally total: ₹${Number(r.tally_total||0).toLocaleString("en-IN")}<br>
      Drift: ${r.drift ?? "0"} · Status: <span style="color:${r.status==="OK"?"#4ade80":"#f87171"}">${r.status||"–"}</span>
    </div>`;
  }

  const tally_entries = output.tally_entries || output.entries || [];
  if (tally_entries.length) {
    html += `<p style="font-size:.8rem;font-weight:600;margin:0 0 .4rem;color:var(--muted)">STAGED VOUCHERS (preview)</p>`;
    tally_entries.slice(0,5).forEach(e => {
      html += `<div class="demo-invoice-row" style="font-size:.78rem">
        <div style="display:flex;justify-content:space-between">
          <span>${escapeHtml(e.narration||e.description||e.voucher_type||"Journal")}</span>
          <span style="font-weight:600">₹${Number(e.amount||e.total||0).toLocaleString("en-IN")}</span>
        </div>
        <div style="color:var(--muted)">${escapeHtml(e.date||"")} · ${escapeHtml(e.voucher_type||"JV")}</div>
      </div>`;
    });
  } else {
    html += `<p class="muted" style="font-size:.82rem">Agent staged entries — review in the F3 console at <a href="/f3/console" target="_blank">/f3/console</a>.</p>`;
  }

  html += `<div style="margin-top:.75rem;padding:.6rem;background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.3);border-radius:6px;font-size:.8rem">
    ⚠ Nothing posts to Tally until the accountant approves in the F3 console.
  </div></div>`;

  el.innerHTML = html;
}

function renderM2Output(el, output) {
  const total_dealers = output.total_dealers ?? 0;
  const total_outstanding = output.total_outstanding ?? 0;
  const branches = output.branches || {};

  let html = `<div style="padding:.75rem">
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:.5rem;margin-bottom:1rem">
      <div style="text-align:center"><div style="font-size:1.6rem;font-weight:700">${total_dealers}</div><div style="font-size:.75rem;color:var(--muted)">Total Dealers</div></div>
      <div style="text-align:center"><div style="font-size:1.6rem;font-weight:700">₹${Number(total_outstanding).toLocaleString("en-IN")}</div><div style="font-size:.75rem;color:var(--muted)">Total Outstanding</div></div>
    </div>`;

  if (Object.keys(branches).length) {
    html += `<p style="font-size:.8rem;font-weight:600;margin:0 0 .4rem;color:var(--muted)">BRANCH PORTFOLIO</p>`;
    Object.entries(branches).forEach(([branch, data]) => {
      html += `<div class="demo-invoice-row">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <span style="font-weight:600;font-size:.82rem">${escapeHtml(branch)}</span>
          <span style="font-size:.8rem">₹${Number(data.total_outstanding||0).toLocaleString("en-IN")}</span>
        </div>
        <div style="font-size:.75rem;color:var(--muted)">${data.dealer_count||0} dealers · ${data.overdue_count||0} overdue</div>
      </div>`;
    });
  } else {
    html += `<div style="padding:.75rem;background:rgba(16,185,129,.06);border:1px solid rgba(16,185,129,.3);border-radius:6px;font-size:.85rem">
      <strong>M2 Territory View</strong><br>
      Entity: ${escapeHtml(output.entity||"YEXIS-EL")} · As of: ${escapeHtml(output.as_of||"today")}<br>
      <span class="muted">DealerMart is empty — run the nightly mart builder to populate it from Zoho CRM. Agent executed successfully.</span>
    </div>`;
  }

  const kpis = [
    {label:"Total Outstanding",val:output.total_open_receivables,prefix:"₹",suffix:""},
    {label:"Overdue Receivables",val:output.overdue_receivables,prefix:"₹",suffix:""},
    {label:"DSO",val:output.dso_days,prefix:"",suffix:" days"},
    {label:"Stock Availability",val:output.stock_availability_pct,prefix:"",suffix:"%"},
    {label:"Vendor OTIF",val:output.vendor_otif_pct,prefix:"",suffix:"%"},
    {label:"AMC Renewal Rate",val:output.amc_renewal_rate_pct,prefix:"",suffix:"%"},
  ].filter(k => k.val != null);

  if (kpis.length) {
    html += `<p style="font-size:.8rem;font-weight:600;margin:.8rem 0 .4rem;color:var(--muted)">KPI TOPLINE</p>`;
    kpis.forEach(k => {
      html += `<div class="demo-kv">
        <span class="demo-kv-key">${escapeHtml(k.label)}</span>
        <span class="demo-kv-val">${k.prefix}${Number(k.val).toLocaleString("en-IN")}${k.suffix}</span>
      </div>`;
    });
  }

  html += `</div>`;
  el.innerHTML = html;
}

function renderP1Output(el, output) {
  const status = output.status || output.decision;
  const po_number = output.po_number || output.purchase_order?.po_number;
  const total = output.total_amount ?? output.total;
  const items = output.items || output.purchase_order?.items || [];
  const approvalRequired = output.approval_required || status === "NEEDS_APPROVAL";

  let html = `<div style="padding:.75rem">`;

  if (po_number) {
    html += `<div style="text-align:center;padding:.75rem;background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.3);border-radius:8px;margin-bottom:1rem">
      <div style="font-size:1.5rem;font-weight:700;color:#3b82f6">${escapeHtml(po_number)}</div>
      <div style="font-size:.8rem;color:var(--muted)">Purchase Order Created</div>
      ${total != null ? `<div style="font-size:1rem;font-weight:600;margin-top:.25rem">₹${Number(total).toLocaleString("en-IN")}</div>` : ""}
    </div>`;
  }

  if (approvalRequired) {
    const reason = output.reason || `Budget approval required — total exceeds threshold`;
    html += `<div style="padding:.6rem;background:rgba(245,158,11,.06);border:1px solid rgba(245,158,11,.3);border-radius:6px;font-size:.82rem;margin-bottom:.75rem">
      ⚠ <strong>Budget Gate Triggered</strong><br>${escapeHtml(reason)}
    </div>`;
  }

  html += `<div class="demo-kv"><span class="demo-kv-key">Vendor</span><span class="demo-kv-val" style="font-size:.78rem">${escapeHtml(output.vendor||"Samsung India Electronics Pvt. Ltd.")}</span></div>`;
  html += `<div class="demo-kv"><span class="demo-kv-key">Status</span><span class="demo-kv-val" style="font-size:.78rem">${escapeHtml(String(status||"–"))}</span></div>`;

  if (items.length) {
    html += `<p style="font-size:.8rem;font-weight:600;margin:.8rem 0 .4rem;color:var(--muted)">LINE ITEMS</p>`;
    items.forEach(item => {
      const line = (item.quantity||1) * (item.unit_price||0);
      html += `<div class="demo-invoice-row">
        <div style="font-size:.82rem;font-weight:600">${escapeHtml(item.item||item.description||"–")}</div>
        <div style="font-size:.78rem;color:var(--muted);display:flex;justify-content:space-between">
          <span>Qty: ${item.quantity||1} × ₹${Number(item.unit_price||0).toLocaleString("en-IN")}</span>
          <span style="font-weight:600">₹${Number(line).toLocaleString("en-IN")}</span>
        </div>
      </div>`;
    });
  }

  if (output.actions_taken?.length) {
    html += `<p style="font-size:.75rem;margin-top:.75rem;color:var(--muted)">Actions: ${output.actions_taken.join(" → ")}</p>`;
  }

  html += `</div>`;
  el.innerHTML = html;
}

function stopDemoPoll() {
  if (demo.pollTimer) { clearInterval(demo.pollTimer); demo.pollTimer = null; }
}

function resetRunButtons() {
  demo.scenarios.forEach(s => {
    const btn = document.getElementById(`run-${s.id}`);
    if (btn && s.run_enabled !== false) {
      btn.disabled = false;
      btn.textContent = "▶ Run";
    }
  });
}

initDemo();

// ══════════════════════════════════════════════════════════════════════════════
// CAMPAIGN MANAGER
// ══════════════════════════════════════════════════════════════════════════════

const cmp = {
  campaigns: [],
  selected: null,   // currently selected campaign object
  wizardStep: 1,
  wizardSource: "dealermart",
  wizardChannel: "auto",
};

// ── helpers ───────────────────────────────────────────────────────────────────

function cmpEntity() {
  return document.getElementById("cmp-entity-filter")?.value || "YEXIS-EL";
}

function cmpStatusClass(s) {
  return `status-${s}`;
}

function cmpStatusLabel(s) {
  const map = {
    draft: "Draft",
    audience_built: "Audience Built",
    broadcast: "Broadcast",
    tracking: "Tracking",
    closed: "Closed",
  };
  return map[s] || s;
}

function cmpTypeLabel(t) {
  const map = {
    dealer_promo: "Dealer Promo",
    end_customer: "End Customer",
    scheme_push: "Scheme Push",
    event: "Event",
  };
  return map[t] || t;
}

function cmpEntityLabel(e) {
  const map = {
    "YEXIS-EL": "YEXIS ELECTRONICS — KERALA",
    "YEXIS-SL": "YEXIS ELECTRONICS — TAMIL NADU",
    "YEXIS-SOL": "YEXIS SOLUTIONS",
  };
  return map[e] || e;
}

function cmpShowNotice(id, msg, kind = "ok") {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = `<div class="cmp-notice ${kind}">${escapeHtml(msg)}</div>`;
}

// ── list rendering ─────────────────────────────────────────────────────────────

async function cmpLoad() {
  const wrap = document.getElementById("cmp-list-wrap");
  if (!wrap) return;
  wrap.innerHTML = `<p class="muted">Loading…</p>`;
  try {
    const res = await fetch(`/campaigns?entity=${cmpEntity()}&limit=50`);
    cmp.campaigns = res.ok ? await res.json() : [];
  } catch {
    cmp.campaigns = [];
  }
  cmpRenderList();
}

function cmpRenderList() {
  const wrap = document.getElementById("cmp-list-wrap");
  if (!wrap) return;
  if (!cmp.campaigns.length) {
    wrap.innerHTML = `<p class="muted">No campaigns yet. Click <strong>+ New Campaign</strong> to create one.</p>`;
    return;
  }
  let html = `<div style="overflow-x:auto">`;
  html += `<div style="display:grid;grid-template-columns:2fr 1fr 1fr 1fr auto;gap:.5rem .75rem;font-size:.75rem;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em;padding:.3rem 0;border-bottom:1px solid var(--panel-border)">
    <span>Campaign</span><span>Type</span><span>Status</span><span>Audience</span><span>Actions</span>
  </div>`;
  cmp.campaigns.forEach(c => {
    const isSelected = cmp.selected?.campaign_id === c.campaign_id;
    html += `<div class="cmp-list-row${isSelected ? ' ' : ''}" style="${isSelected ? 'background:rgba(119,209,255,.05);border-radius:6px;padding:.7rem .5rem;' : ''}">
      <div>
        <div style="font-weight:600;font-size:.88rem">${escapeHtml(c.name)}</div>
        <div style="font-size:.75rem;color:var(--muted)">${escapeHtml(c.campaign_ref)} · ${c.start_date || '—'} → ${c.end_date || '—'}</div>
        <div style="font-size:.72rem;color:var(--accent-2);margin-top:.1rem">${escapeHtml(cmpEntityLabel(c.entity))}</div>
      </div>
      <div style="font-size:.8rem">${escapeHtml(cmpTypeLabel(c.campaign_type))}</div>
      <div><span class="cmp-status ${cmpStatusClass(c.status)}">${escapeHtml(cmpStatusLabel(c.status))}</span></div>
      <div style="font-size:.82rem">${c.audience_count} contacts<br><span style="font-size:.72rem;color:var(--muted)">${c.sent_count} sent</span>${c.scheduled_at ? `<br><span style="font-size:.7rem;color:var(--warn)">📅 ${new Date(c.scheduled_at).toLocaleString('en-IN',{timeZone:'Asia/Kolkata',dateStyle:'short',timeStyle:'short'})}</span>` : ''}</div>
      <div class="cmp-actions">${cmpActionButtons(c)}</div>
    </div>`;
  });
  html += `</div>`;
  wrap.innerHTML = html;

  // Wire action buttons
  wrap.querySelectorAll("[data-cmp-action]").forEach(btn => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.cmpId;
      const action = btn.dataset.cmpAction;
      const campaign = cmp.campaigns.find(c => c.campaign_id === id);
      if (campaign) cmpHandleAction(campaign, action, btn);
    });
  });
  wrap.querySelectorAll("[data-cmp-select]").forEach(el => {
    el.addEventListener("click", () => {
      const id = el.dataset.cmpSelect;
      const campaign = cmp.campaigns.find(c => c.campaign_id === id);
      if (campaign) cmpSelectCampaign(campaign);
    });
  });
}

function cmpActionButtons(c) {
  let btns = `<button class="cmp-btn" data-cmp-action="select" data-cmp-id="${c.campaign_id}">View</button>`;
  if (c.status === "draft") {
    btns += `<button class="cmp-btn primary" data-cmp-action="audience" data-cmp-id="${c.campaign_id}">Build Audience</button>`;
  }
  if (c.status === "audience_built") {
    btns += `<button class="cmp-btn warn" data-cmp-action="send" data-cmp-id="${c.campaign_id}">Send Broadcast</button>`;
    btns += `<button class="cmp-btn" data-cmp-action="schedule" data-cmp-id="${c.campaign_id}">Schedule</button>`;
  }
  if (["broadcast","tracking","closed"].includes(c.status)) {
    btns += `<button class="cmp-btn success" data-cmp-action="analytics" data-cmp-id="${c.campaign_id}">Analytics</button>`;
  }
  return btns;
}

// ── campaign detail ───────────────────────────────────────────────────────────

function cmpSelectCampaign(c) {
  cmp.selected = c;
  cmpRenderList(); // re-render to highlight selected
  const detail = document.getElementById("cmp-detail");
  const nameEl = document.getElementById("cmp-detail-name");
  const statusEl = document.getElementById("cmp-detail-status");
  const bodyEl = document.getElementById("cmp-detail-body");
  const kpisEl = document.getElementById("cmp-detail-kpis");
  const noticeEl = document.getElementById("cmp-detail-notice");
  if (!detail) return;

  nameEl.textContent = c.name;
  statusEl.textContent = cmpStatusLabel(c.status);
  statusEl.className = `badge cmp-status ${cmpStatusClass(c.status)}`;
  noticeEl.innerHTML = "";
  kpisEl.style.display = "none";

  // Scheduled banner — shown prominently when campaign has a future or pending scheduled time
  let scheduledBanner = "";
  if (c.scheduled_at) {
    const schedDt = new Date(c.scheduled_at);
    const schedLabel = schedDt.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short" });
    const isPast = schedDt < new Date();
    const hasSent = (c.sent_count || 0) > 0;
    if (!hasSent && isPast) {
      scheduledBanner = `<div class="cmp-notice warn" style="margin-bottom:.75rem">⏳ Scheduled for ${escapeHtml(schedLabel)} IST — scheduler tick pending (fires every 5 min). If it's been more than 10 min, check the Gateway logs.</div>`;
    } else if (!hasSent) {
      scheduledBanner = `<div class="cmp-notice ok" style="margin-bottom:.75rem">📅 Queued to send at ${escapeHtml(schedLabel)} IST</div>`;
    } else {
      scheduledBanner = `<div class="cmp-notice ok" style="margin-bottom:.75rem">✓ Fired at ${escapeHtml(schedLabel)} IST — ${c.sent_count} message(s) sent</div>`;
    }
  }

  let html = scheduledBanner;
  html += `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.5rem;font-size:.82rem;margin-bottom:.75rem">`;
  const schedDisplay = c.scheduled_at
    ? new Date(c.scheduled_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short" }) + " IST"
    : "—";
  const fields = [
    ["Type", cmpTypeLabel(c.campaign_type)],
    ["Entity", cmpEntityLabel(c.entity)],
    ["Dates", `${c.start_date || "—"} → ${c.end_date || "—"}`],
    ["Budget", c.budget ? `₹${Number(c.budget).toLocaleString("en-IN")}` : "—"],
    ["Channel", c.default_channel || "auto"],
    ["Template", c.message_template_id || "—"],
    ["Scheduled For", schedDisplay],
    ["Audience", `${c.audience_count} contacts`],
    ["Sent", `${c.sent_count} messages`],
  ];
  fields.forEach(([k,v]) => {
    html += `<div class="demo-kv"><span class="demo-kv-key">${escapeHtml(k)}</span><span class="demo-kv-val">${escapeHtml(String(v))}</span></div>`;
  });
  html += `</div>`;

  // Contextual action buttons
  if (c.status === "draft") {
    html += `<button class="cmp-btn primary" style="margin-top:.25rem" onclick="cmpHandleAction(cmp.selected,'audience',this)">▶ Build Audience Now</button>`;
    html += ` <button class="cmp-btn" style="margin-top:.25rem" onclick="cmpUploadContacts('${c.campaign_id}')">📂 Upload Excel</button>`;
  } else if (c.status === "audience_built") {
    html += `<button class="cmp-btn" style="margin-top:.25rem" onclick="cmpUploadContacts('${c.campaign_id}')">📂 Upload Excel</button>`;
    html += ` <button class="cmp-btn warn" style="margin-top:.25rem" onclick="cmpOpenSendModal(cmp.selected,'now')">📣 Send Broadcast</button>`;
    html += ` <button class="cmp-btn" style="margin-top:.25rem" onclick="cmpOpenSendModal(cmp.selected,'schedule')">📅 Schedule</button>`;
    html += ` <button class="cmp-btn" style="margin-top:.25rem" onclick="cmpHandleAction(cmp.selected,'analytics',this)">📊 View Analytics</button>`;
  } else if (["broadcast","tracking","closed"].includes(c.status)) {
    html += `<button class="cmp-btn success" style="margin-top:.25rem" onclick="cmpHandleAction(cmp.selected,'analytics',this)">📊 Refresh Analytics</button>`;
  }

  // Contacts viewer — available on any campaign that has an audience
  if ((c.audience_count || 0) > 0) {
    const sentN    = c.sent_count || 0;
    const totalN   = c.audience_count || 0;
    const pendingN = Math.max(0, totalN - sentN);
    html += `
      <div id="cmp-contacts-wrap" style="margin-top:1rem">
        <div style="display:flex;gap:.5rem;align-items:center;flex-wrap:wrap">
          <button class="cmp-btn" onclick="cmpLoadContacts('${c.campaign_id}','sent')">📬 Sent (${sentN})</button>
          <button class="cmp-btn" onclick="cmpLoadContacts('${c.campaign_id}','pending')">⏳ Pending (${pendingN})</button>
          <button class="cmp-btn" style="color:#f87171;border-color:#f87171" onclick="cmpLoadContacts('${c.campaign_id}','failed')">❌ Failed / Bounced</button>
          <button class="cmp-btn" onclick="cmpLoadContacts('${c.campaign_id}','all')">👥 All (${totalN})</button>
        </div>
        <div id="cmp-contacts-table" style="margin-top:.6rem"></div>
      </div>`;
  }

  // Existing KPIs
  const kpi = c.kpi || {};
  if (kpi.delivery_rate_pct != null) {
    const kpiData = [
      ["Delivered", `${kpi.delivery_rate_pct}%`],
      ["Response Rate", `${kpi.response_rate_pct}%`],
      ["Conversion", `${kpi.conversion_rate_pct}%`],
      ["Hot Leads", `${kpi.hot_lead_rate_pct}%`],
    ];
    kpisEl.style.display = "grid";
    kpisEl.innerHTML = kpiData.map(([lbl,val]) =>
      `<div class="cmp-kpi"><div class="cmp-kpi-val">${escapeHtml(val)}</div><div class="cmp-kpi-lbl">${escapeHtml(lbl)}</div></div>`
    ).join("");
  }

  bodyEl.innerHTML = html;
  detail.style.display = "block";
  detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ── contacts viewer ───────────────────────────────────────────────────────────

async function cmpLoadContacts(campaignId, status) {
  const wrap = document.getElementById("cmp-contacts-table");
  if (!wrap) return;
  wrap.innerHTML = `<p class="muted" style="font-size:.82rem">Loading…</p>`;

  try {
    const res = await fetch(`/campaigns/${campaignId}/contacts?status=${status}&limit=200`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to load contacts");

    const contacts = data.contacts || [];
    if (!contacts.length) {
      wrap.innerHTML = `<p class="muted" style="font-size:.82rem">No contacts in this view.</p>`;
      return;
    }

    const statusLabel = { sent: "Sent", pending: "Pending", failed: "Failed / Bounced", all: "All" }[status] || status;
    const isFailed = status === "failed";
    let t = `<div style="font-size:.78rem;color:var(--muted);margin-bottom:.4rem">${escapeHtml(statusLabel)} — ${data.total} contact(s) · showing first ${contacts.length}</div>`;
    t += `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:.8rem">`;
    t += `<thead><tr style="border-bottom:1px solid var(--border)">
      <th style="text-align:left;padding:4px 8px">Name</th>
      <th style="text-align:left;padding:4px 8px">Email</th>
      <th style="text-align:left;padding:4px 8px">Phone</th>
      <th style="text-align:left;padding:4px 8px">Sent At</th>
      ${isFailed ? `<th style="text-align:left;padding:4px 8px">Reason</th>` : `<th style="text-align:left;padding:4px 8px">Response</th>`}
    </tr></thead><tbody>`;

    for (const c of contacts) {
      const sentAt = c.sent_at
        ? new Date(c.sent_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "short", timeStyle: "short" })
        : `<span class="muted">—</span>`;
      const sentDot = c.sent_at
        ? (c.failure_reason ? `<span style="color:#f87171">✗</span> ` : `<span style="color:#22c55e">●</span> `)
        : `<span style="color:#94a3b8">○</span> `;
      const bounceTag = c.bounced ? ` <span style="font-size:.7rem;background:#7f1d1d;color:#fca5a5;padding:1px 5px;border-radius:4px">bounced</span>` : "";
      const altBadge = c.alt_email ? ` <span style="font-size:.7rem;color:var(--muted)" title="Alt: ${escapeHtml(c.alt_email)}">+alt</span>` : "";
      const emailDisplay = c.email ? `${escapeHtml(c.email)}${bounceTag}${altBadge}` : `<span class="muted">—</span>`;
      const phone = c.msisdn ? escapeHtml(c.msisdn) : `<span class="muted">—</span>`;
      const lastCol = isFailed
        ? (c.failure_reason ? `<span style="color:#f87171">${escapeHtml(c.failure_reason)}</span>` : `<span class="muted">—</span>`)
        : (c.response_type ? `<span style="color:#22c55e">${escapeHtml(c.response_type)}</span>` : `<span class="muted">—</span>`);
      const rowStyle = c.failure_reason ? `style="border-bottom:1px solid var(--border);background:rgba(248,113,113,.05)"` : `style="border-bottom:1px solid var(--border)"`;
      t += `<tr ${rowStyle}>
        <td style="padding:4px 8px">${escapeHtml(c.name || c.contact_ref)}</td>
        <td style="padding:4px 8px">${emailDisplay}</td>
        <td style="padding:4px 8px">${phone}</td>
        <td style="padding:4px 8px">${sentDot}${sentAt}</td>
        <td style="padding:4px 8px">${lastCol}</td>
      </tr>`;
    }
    t += `</tbody></table></div>`;
    wrap.innerHTML = t;
  } catch (err) {
    wrap.innerHTML = `<p style="color:#f87171;font-size:.82rem">Error: ${escapeHtml(err.message)}</p>`;
  }
}

// ── action handlers ────────────────────────────────────────────────────────────

async function cmpHandleAction(campaign, action, btn) {
  if (action === "select") { cmpSelectCampaign(campaign); return; }
  const noticeEl = document.getElementById("cmp-detail-notice");

  if (btn) { btn.disabled = true; btn.textContent = "Working…"; }

  try {
    if (action === "audience") {
      cmpShowNotice("cmp-detail-notice", "Building audience — pulling from DealerMart, Zoho Books, CRM…", "warn");
      const res = await fetch(`/campaigns/${campaign.campaign_id}/audience`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: "all" }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Audience build failed");
      const errs = data.source_errors || {};
      const errKeys = Object.keys(errs);
      const errMsg = errKeys.length
        ? ` ⚠ ${errKeys.join(", ")} failed — contacts from remaining sources only.`
        : "";
      cmpShowNotice("cmp-detail-notice",
        `✓ Audience built — ${data.total_audience} contacts (${data.added} newly added).${errMsg}`,
        errKeys.length ? "warn" : "ok");
      await cmpLoad();
      const updated = cmp.campaigns.find(c => c.campaign_id === campaign.campaign_id);
      if (updated) cmpSelectCampaign(updated);

    } else if (action === "send") {
      if (btn) { btn.disabled = false; btn.textContent = "Send Broadcast"; }
      cmpOpenSendModal(campaign, "now");
      return;

    } else if (action === "schedule") {
      if (btn) { btn.disabled = false; btn.textContent = "Schedule"; }
      cmpOpenSendModal(campaign, "schedule");
      return;

    } else if (action === "analytics") {
      cmpShowNotice("cmp-detail-notice", "Computing analytics…", "warn");
      const res = await fetch(`/campaigns/${campaign.campaign_id}/analytics`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Analytics failed");
      const kpisEl = document.getElementById("cmp-detail-kpis");
      if (kpisEl) {
        const kpiData = [
          ["Delivered", `${data.delivery_rate_pct ?? 0}%`],
          ["Response Rate", `${data.response_rate_pct ?? 0}%`],
          ["Conversion", `${data.conversion_rate_pct ?? 0}%`],
          ["Hot Leads", `${data.hot_lead_rate_pct ?? 0}%`],
          ["Total Audience", data.total_audience ?? 0],
          ["Messages Sent", data.messages_sent ?? 0],
          ["Responses", data.responses ?? 0],
          ["Converted", data.converted ?? 0],
        ];
        kpisEl.style.display = "grid";
        kpisEl.innerHTML = kpiData.map(([lbl,val]) =>
          `<div class="cmp-kpi"><div class="cmp-kpi-val">${escapeHtml(String(val))}</div><div class="cmp-kpi-lbl">${escapeHtml(lbl)}</div></div>`
        ).join("");
      }
      noticeEl.innerHTML = "";
    }
  } catch (err) {
    cmpShowNotice("cmp-detail-notice", `Error: ${err.message}`, "err");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = btn.dataset.cmpAction === "audience" ? "Build Audience" : btn.dataset.cmpAction === "send" ? "Send Broadcast" : "Analytics"; }
  }
}

// ── send / schedule modal ─────────────────────────────────────────────────────

function cmpOpenSendModal(campaign, mode = "now") {
  if (!campaign) return;
  cmp.sendModalCampaign = campaign;
  document.getElementById("csm-info").innerHTML =
    `<strong>${escapeHtml(campaign.name)}</strong><br>` +
    `<span style="color:var(--muted)">${escapeHtml(cmpEntityLabel(campaign.entity))} · ${campaign.audience_count} contacts in audience</span>`;
  const tmplId = campaign.message_template_id || "";
  document.getElementById("csm-template").value = tmplId;
  // Default schedule datetime: tomorrow 10:00 local time
  const d = new Date(); d.setDate(d.getDate() + 1); d.setHours(10, 0, 0, 0);
  document.getElementById("csm-dt").value = d.toISOString().slice(0, 16);
  document.getElementById("csm-notice").innerHTML = "";
  document.getElementById("csm-preview-body").style.display = "none";
  document.getElementById("csm-preview-meta").textContent = "";
  // Show preview section only for email templates
  const tmplObj = (typeof tmpl !== "undefined" ? tmpl.list : []).find(t => t.template_id === tmplId);
  const showPreview = tmplObj?.channel === "email" || (!tmplObj && tmplId);
  document.getElementById("csm-preview-wrap").style.display = showPreview ? "" : "none";
  cmpSendModalSetMode(mode);
  document.getElementById("cmp-send-modal").classList.add("open");
  // Reveal preview wrap when template input changes
  document.getElementById("csm-template").oninput = () => {
    const v = document.getElementById("csm-template").value.trim();
    document.getElementById("csm-preview-wrap").style.display = v ? "" : "none";
    document.getElementById("csm-preview-body").style.display = "none";
  };
}

async function cmpLoadSendPreview() {
  const templateId = (document.getElementById("csm-template")?.value || "").trim();
  const bodyEl = document.getElementById("csm-preview-body");
  const metaEl = document.getElementById("csm-preview-meta");
  if (!templateId || !bodyEl) return;
  bodyEl.style.display = "none";
  metaEl.textContent = "Loading preview…";
  try {
    const res = await fetch(`/templates/${templateId}/preview`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    const data = await res.json();
    if (!res.ok) { metaEl.textContent = data.detail || "Preview failed"; return; }
    const fromLine = `From: Yexis Electronics`;
    const replyLine = data.reply_to ? ` · Reply-To: ${data.reply_to}` : "";
    const subj = data.rendered_subject || data.subject || "";
    metaEl.innerHTML = `<span style="color:var(--muted)">${escapeHtml(fromLine)}${escapeHtml(replyLine)}</span>${subj ? `<br><strong>Subject:</strong> ${escapeHtml(subj)}` : ""}`;
    bodyEl.innerHTML = data.rendered_body || "(empty)";
    bodyEl.style.display = "block";
  } catch (e) {
    metaEl.textContent = "Preview failed: " + e.message;
  }
}

function cmpSendModalSetMode(mode) {
  cmp.sendModalMode = mode;
  document.querySelectorAll(".csm-tab").forEach(b => b.classList.toggle("selected", b.dataset.mode === mode));
  document.getElementById("csm-sched-block").style.display = mode === "schedule" ? "block" : "none";
  document.getElementById("csm-title").textContent = mode === "schedule" ? "Schedule Broadcast" : "Send Broadcast Now";
  document.getElementById("csm-submit").textContent = mode === "schedule" ? "📅 Schedule" : "📣 Send Now";
}

async function cmpSendModalSubmit() {
  const campaign = cmp.sendModalCampaign;
  const templateId = (document.getElementById("csm-template")?.value || "").trim();
  const noticeEl = document.getElementById("csm-notice");
  if (!templateId) {
    noticeEl.innerHTML = `<div class="cmp-notice err">Template ID is required.</div>`;
    return;
  }
  const submitBtn = document.getElementById("csm-submit");
  submitBtn.disabled = true;
  const origText = submitBtn.textContent;
  submitBtn.textContent = "Working…";
  try {
    if (cmp.sendModalMode === "schedule") {
      const rawDt = document.getElementById("csm-dt")?.value;
      if (!rawDt) { noticeEl.innerHTML = `<div class="cmp-notice err">Please set a date and time.</div>`; return; }
      const scheduledAt = new Date(rawDt).toISOString();
      const res = await fetch(`/campaigns/${campaign.campaign_id}/schedule`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ template_id: templateId, scheduled_at: scheduledAt }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Schedule failed");
      const displayDt = new Date(data.scheduled_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short" });
      noticeEl.innerHTML = `<div class="cmp-notice ok">✓ Scheduled for ${escapeHtml(displayDt)} IST</div>`;
      await cmpLoad();
      setTimeout(() => document.getElementById("cmp-send-modal").classList.remove("open"), 2000);
    } else {
      noticeEl.innerHTML = `<div class="cmp-notice warn">Sending — dispatching to ${campaign.audience_count} contacts…</div>`;
      const res = await fetch(`/campaigns/${campaign.campaign_id}/send`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ template_id: templateId, channel: campaign.default_channel || "auto" }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Broadcast failed");
      const sent = data.sent_total ?? data.sent ?? 0;
      noticeEl.innerHTML = `<div class="cmp-notice ok">✓ ${sent} messages sent${data.remaining_unsent > 0 ? ` · ${data.remaining_unsent} deferred (trickle/window)` : ""}.</div>`;
      await cmpLoad();
      const updated = cmp.campaigns.find(c => c.campaign_id === campaign.campaign_id);
      if (updated) cmpSelectCampaign(updated);
      setTimeout(() => document.getElementById("cmp-send-modal").classList.remove("open"), 2000);
    }
  } catch (err) {
    noticeEl.innerHTML = `<div class="cmp-notice err">Error: ${escapeHtml(err.message)}</div>`;
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = origText;
  }
}

async function cmpUploadContacts(campaignId) {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".xlsx,.xls";
  input.onchange = async () => {
    const file = input.files[0];
    if (!file) return;
    cmpShowNotice("cmp-detail-notice", `Uploading ${file.name}…`, "warn");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`/campaigns/${campaignId}/contacts/upload`, {
        method: "POST",
        body: fd,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Upload failed");
      cmpShowNotice("cmp-detail-notice",
        `✓ Uploaded ${file.name} — ${data.added} added, ${data.skipped_duplicate} duplicates skipped. Total audience: ${data.total_audience}`, "ok");
      await cmpLoad();
      const updated = cmp.campaigns.find(c => c.campaign_id === campaignId);
      if (updated) cmpSelectCampaign(updated);
    } catch (err) {
      cmpShowNotice("cmp-detail-notice", `Upload error: ${err.message}`, "err");
    }
  };
  input.click();
}

// ── wizard ────────────────────────────────────────────────────────────────────

function wzHandleFileDrop(files) {
  const file = files && files[0];
  if (!file) return;
  cmp.wizardFile = file;
  const lbl = document.getElementById("wz-file-label");
  if (lbl) lbl.innerHTML = `✅ <strong>${escapeHtml(file.name)}</strong> <span style="color:var(--muted);font-size:.78rem">(${(file.size/1024).toFixed(1)} KB) — ready to upload after campaign creation</span>`;
  const dz = document.getElementById("wz-drop-zone");
  if (dz) { dz.style.borderColor = "var(--ok)"; dz.style.background = "rgba(117,231,160,.06)"; }
}

function cmpOpenWizard() {
  cmp.wizardStep = 1;
  cmp.wizardSource = "dealermart";
  cmp.wizardChannel = "auto";
  cmp.wizardFile = null;
  // reset fields
  ["wz-name","wz-budget","wz-skus","wz-kw","wz-loc","wz-title"].forEach(id => {
    const el = document.getElementById(id); if (el) el.value = "";
  });
  const wzTmplSel = document.getElementById("wz-template");
  if (wzTmplSel) wzTmplSel.value = "";
  document.getElementById("wz-tmpl-preview").style.display = "none";
  const today = new Date().toISOString().slice(0,10);
  const end = new Date(Date.now() + 14*864e5).toISOString().slice(0,10);
  const startEl = document.getElementById("wz-start"); if (startEl) startEl.value = today;
  const endEl = document.getElementById("wz-end"); if (endEl) endEl.value = end;
  document.getElementById("wz-per-page").value = "50";
  document.getElementById("wz-notice").innerHTML = "";
  // reset source buttons
  document.querySelectorAll(".cmp-source-btn").forEach(b => b.classList.remove("selected"));
  document.querySelector('[data-src="dealermart"]')?.classList.add("selected");
  // reset channel buttons
  document.querySelectorAll(".cmp-channel-btn").forEach(b => b.classList.remove("selected"));
  document.querySelector('[data-ch="auto"]')?.classList.add("selected");
  // reset file drop zone
  const lbl = document.getElementById("wz-file-label");
  if (lbl) lbl.innerHTML = `📂 Click to browse, or drag &amp; drop a file here<br><span style="font-size:.72rem">Supported: .xlsx, .xls, .csv</span>`;
  const dz = document.getElementById("wz-drop-zone");
  if (dz) { dz.style.borderColor = ""; dz.style.background = ""; }
  const fi = document.getElementById("wz-file-input");
  if (fi) fi.value = "";
  cmpWizardGoTo(1);
  openModal("#cmp-wizard");
}

function cmpWizardGoTo(step) {
  cmp.wizardStep = step;
  ["wz-s1","wz-s2","wz-s3"].forEach((id, i) => {
    const el = document.getElementById(id);
    if (el) el.style.display = i === step - 1 ? "block" : "none";
  });
  ["ws0","ws1","ws2"].forEach((id, i) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.className = "cmp-step" + (i < step - 1 ? " done" : i === step - 1 ? " active" : "");
  });
  const labels = ["Step 1 of 3 — Campaign Brief","Step 2 of 3 — Audience Sources","Step 3 of 3 — Channel & Review"];
  const lbl = document.getElementById("wz-step-label");
  if (lbl) lbl.textContent = labels[step-1];
  const back = document.getElementById("wz-back");
  const next = document.getElementById("wz-next");
  if (back) back.style.display = step > 1 ? "block" : "none";
  if (next) next.textContent = step < 3 ? "Next →" : "Create Campaign";
  if (step === 3) cmpBuildReview();
  const apolloFields = document.getElementById("wz-apollo-fields");
  if (apolloFields) apolloFields.style.display = (cmp.wizardSource === "apollo" || cmp.wizardSource === "all") ? "block" : "none";
  const excelFields = document.getElementById("wz-excel-fields");
  if (excelFields) excelFields.style.display = cmp.wizardSource === "excel" ? "block" : "none";
}

function cmpBuildReview() {
  const el = document.getElementById("wz-review");
  if (!el) return;
  const name = document.getElementById("wz-name")?.value || "—";
  const type = document.getElementById("wz-type")?.value || "—";
  const entity = document.getElementById("wz-entity")?.value || "—";
  const start = document.getElementById("wz-start")?.value || "—";
  const end = document.getElementById("wz-end")?.value || "—";
  const budget = document.getElementById("wz-budget")?.value;
  const tmpl = document.getElementById("wz-template")?.value || "—";
  const skus = document.getElementById("wz-skus")?.value || "—";
  el.innerHTML = `
    <p style="font-weight:600;margin:0 0 .5rem;color:var(--accent-2)">Review before creating</p>
    <div class="demo-kv"><span class="demo-kv-key">Name</span><span class="demo-kv-val">${escapeHtml(name)}</span></div>
    <div class="demo-kv"><span class="demo-kv-key">Type</span><span class="demo-kv-val">${escapeHtml(cmpTypeLabel(type))}</span></div>
    <div class="demo-kv"><span class="demo-kv-key">Entity</span><span class="demo-kv-val">${escapeHtml(cmpEntityLabel(entity))}</span></div>
    <div class="demo-kv"><span class="demo-kv-key">Dates</span><span class="demo-kv-val">${escapeHtml(start)} → ${escapeHtml(end)}</span></div>
    <div class="demo-kv"><span class="demo-kv-key">Budget</span><span class="demo-kv-val">${budget ? `₹${Number(budget).toLocaleString("en-IN")}` : "—"}</span></div>
    <div class="demo-kv"><span class="demo-kv-key">Template</span><span class="demo-kv-val">${escapeHtml(tmpl)}</span></div>
    <div class="demo-kv"><span class="demo-kv-key">Target SKUs</span><span class="demo-kv-val">${escapeHtml(skus)}</span></div>
    <div class="demo-kv"><span class="demo-kv-key">Audience Source</span><span class="demo-kv-val">${escapeHtml({ dealermart:"DealerMart", zoho_books:"Zoho Books", zoho_crm:"Zoho CRM", apollo:"Apollo.io", excel:"Upload Excel", all:"All Sources" }[cmp.wizardSource] || cmp.wizardSource)}</span></div>
    <div class="demo-kv"><span class="demo-kv-key">Channel</span><span class="demo-kv-val">${escapeHtml(cmp.wizardChannel)}</span></div>
    <p style="margin:.75rem 0 0;font-size:.78rem;color:var(--muted)">The campaign will be created in <strong>draft</strong> status. You can build the audience and send the broadcast from the campaign list.</p>
  `;
}

async function cmpWizardSubmit() {
  const noticeEl = document.getElementById("wz-notice");
  const nextBtn = document.getElementById("wz-next");
  const name = document.getElementById("wz-name")?.value?.trim();
  if (!name) { noticeEl.innerHTML = `<div class="cmp-notice err">Campaign name is required.</div>`; cmpWizardGoTo(1); return; }
  if (nextBtn) { nextBtn.disabled = true; nextBtn.textContent = "Creating…"; }
  noticeEl.innerHTML = `<div class="cmp-notice warn">Creating campaign…</div>`;
  try {
    const skuRaw = document.getElementById("wz-skus")?.value?.trim();
    const skus = skuRaw ? skuRaw.split(",").map(s => s.trim()).filter(Boolean) : [];
    const body = {
      name,
      campaign_type: document.getElementById("wz-type")?.value,
      entity: document.getElementById("wz-entity")?.value,
      start_date: document.getElementById("wz-start")?.value || undefined,
      end_date: document.getElementById("wz-end")?.value || undefined,
      budget: document.getElementById("wz-budget")?.value ? Number(document.getElementById("wz-budget").value) : undefined,
      message_template_id: document.getElementById("wz-template")?.value?.trim() || undefined,
      target_skus: skus.length ? skus : undefined,
      default_channel: cmp.wizardChannel,
      requested_by: "ui",
    };
    const res = await fetch("/campaigns", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Creation failed");
    noticeEl.innerHTML = `<div class="cmp-notice ok">✓ Campaign created — ${escapeHtml(data.campaign_ref)}</div>`;
    const createdId = data.campaign_id;
    const wasExcel = cmp.wizardSource === "excel";
    const fileToUpload = wasExcel ? cmp.wizardFile : null;
    if (wasExcel && !fileToUpload) {
      noticeEl.innerHTML = `<div class="cmp-notice warn">✓ Campaign created — ${escapeHtml(data.campaign_ref)}. No file selected; use <strong>Upload Excel</strong> in the campaign detail to add contacts.</div>`;
    }
    setTimeout(async () => {
      closeOpenModalStack();
      await cmpLoad();
      if (wasExcel && fileToUpload && createdId) {
        // Upload the pre-selected file directly
        cmpShowNotice("cmp-detail-notice", `Uploading ${escapeHtml(fileToUpload.name)}…`, "warn");
        const fd = new FormData(); fd.append("file", fileToUpload);
        try {
          const upRes = await fetch(`/campaigns/${createdId}/contacts/upload`, { method: "POST", body: fd });
          const upData = await upRes.json();
          if (!upRes.ok) throw new Error(upData.detail || "Upload failed");
          await cmpLoad();
          const fresh = cmp.campaigns.find(c => c.campaign_id === createdId);
          if (fresh) cmpSelectCampaign(fresh);
          cmpShowNotice("cmp-detail-notice",
            `✓ ${escapeHtml(fileToUpload.name)} uploaded — ${upData.added} contacts added, ${upData.skipped_duplicate} duplicates skipped. Total audience: ${upData.total_audience}`, "ok");
        } catch (err) {
          cmpShowNotice("cmp-detail-notice", `Upload error: ${escapeHtml(err.message)}`, "err");
        }
      } else if (!wasExcel && createdId) {
        const fresh = cmp.campaigns.find(c => c.campaign_id === createdId);
        if (fresh) cmpSelectCampaign(fresh);
      }
    }, 900);
  } catch (err) {
    noticeEl.innerHTML = `<div class="cmp-notice err">Error: ${escapeHtml(err.message)}</div>`;
    if (nextBtn) { nextBtn.disabled = false; nextBtn.textContent = "Create Campaign"; }
  }
}

// ── init ──────────────────────────────────────────────────────────────────────

async function wzLoadTemplateDropdown() {
  const sel = document.getElementById("wz-template");
  if (!sel) return;
  try {
    const res = await fetch("/templates");
    if (!res.ok) return;
    const list = await res.json();
    sel.innerHTML = `<option value="">— Select a template (optional) —</option>` +
      list.map(t => `<option value="${escapeHtml(t.template_id)}" data-channel="${escapeHtml(t.channel)}" data-subject="${escapeHtml(t.subject||'')}" data-status="${escapeHtml(t.approval_status)}">${escapeHtml(t.template_id)} — ${escapeHtml(t.purpose||t.channel)}</option>`).join("");
    sel.addEventListener("change", () => {
      const opt = sel.options[sel.selectedIndex];
      const prev = document.getElementById("wz-tmpl-preview");
      if (!sel.value || !prev) { prev && (prev.style.display = "none"); return; }
      document.getElementById("wz-tmpl-preview-id").textContent = sel.value;
      const chEl = document.getElementById("wz-tmpl-preview-ch");
      const ch = opt.dataset.channel || "";
      chEl.textContent = ch.toUpperCase();
      chEl.className = `tmpl-badge ${ch}`;
      const stEl = document.getElementById("wz-tmpl-preview-status");
      stEl.textContent = opt.dataset.status || "";
      stEl.className = `tmpl-badge ${opt.dataset.status || ""}`;
      const sub = opt.dataset.subject;
      document.getElementById("wz-tmpl-preview-subject").textContent = sub ? `Subject: ${sub}` : "";
      prev.style.display = "";
    });
  } catch (e) { /* non-critical */ }
}

function initCampaigns() {
  // Source buttons (mutually exclusive OR multi by clicking "all")
  document.querySelectorAll(".cmp-source-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      if (btn.dataset.src === "all") {
        document.querySelectorAll(".cmp-source-btn").forEach(b => b.classList.remove("selected"));
        btn.classList.add("selected");
        cmp.wizardSource = "all";
      } else if (btn.dataset.src === "excel") {
        // Excel is exclusive — selecting it deselects everything else
        document.querySelectorAll(".cmp-source-btn").forEach(b => b.classList.remove("selected"));
        btn.classList.add("selected");
        cmp.wizardSource = "excel";
      } else {
        document.querySelector('[data-src="all"]')?.classList.remove("selected");
        document.querySelector('[data-src="excel"]')?.classList.remove("selected");
        btn.classList.toggle("selected");
        const sel = Array.from(document.querySelectorAll(".cmp-source-btn.selected")).map(b => b.dataset.src);
        cmp.wizardSource = sel.length === 1 ? sel[0] : sel.length > 1 ? "all" : "dealermart";
      }
      // Show/hide contextual fields
      const apolloFields = document.getElementById("wz-apollo-fields");
      if (apolloFields) apolloFields.style.display = (cmp.wizardSource === "apollo" || cmp.wizardSource === "all") ? "block" : "none";
      const excelFields = document.getElementById("wz-excel-fields");
      if (excelFields) excelFields.style.display = cmp.wizardSource === "excel" ? "block" : "none";
    });
  });

  // Send/Schedule modal wiring
  document.getElementById("csm-close")?.addEventListener("click", () => document.getElementById("cmp-send-modal").classList.remove("open"));
  document.getElementById("csm-cancel")?.addEventListener("click", () => document.getElementById("cmp-send-modal").classList.remove("open"));
  document.getElementById("cmp-send-modal")?.addEventListener("click", e => { if (e.target === e.currentTarget) e.currentTarget.classList.remove("open"); });
  document.getElementById("csm-submit")?.addEventListener("click", cmpSendModalSubmit);
  document.getElementById("csm-preview-btn")?.addEventListener("click", cmpLoadSendPreview);
  document.querySelectorAll(".csm-tab").forEach(btn => btn.addEventListener("click", () => cmpSendModalSetMode(btn.dataset.mode)));

  // Channel buttons
  document.querySelectorAll(".cmp-channel-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".cmp-channel-btn").forEach(b => b.classList.remove("selected"));
      btn.classList.add("selected");
      cmp.wizardChannel = btn.dataset.ch;
    });
  });

  // Wizard navigation
  document.getElementById("wz-next")?.addEventListener("click", () => {
    if (cmp.wizardStep < 3) {
      if (cmp.wizardStep === 1 && !document.getElementById("wz-name")?.value?.trim()) {
        document.getElementById("wz-notice").innerHTML = `<div class="cmp-notice err">Please enter a campaign name.</div>`;
        return;
      }
      document.getElementById("wz-notice").innerHTML = "";
      cmpWizardGoTo(cmp.wizardStep + 1);
    } else {
      cmpWizardSubmit();
    }
  });
  document.getElementById("wz-back")?.addEventListener("click", () => {
    if (cmp.wizardStep > 1) cmpWizardGoTo(cmp.wizardStep - 1);
  });
  document.getElementById("cmp-new")?.addEventListener("click", cmpOpenWizard);
  document.getElementById("cmp-wizard-close")?.addEventListener("click", () => {
    closeOpenModalStack();
  });
  document.getElementById("cmp-wizard")?.addEventListener("click", e => {
    if (e.target === e.currentTarget) closeOpenModalStack();
  });
  document.getElementById("cmp-reload")?.addEventListener("click", cmpLoad);
  document.getElementById("cmp-entity-filter")?.addEventListener("change", cmpLoad);

  cmpLoad();
  wzLoadTemplateDropdown();
  cmpCheckScheduler();
  setInterval(cmpCheckScheduler, 30_000);
}

initCampaigns();

// ══════════════════════════════════════════════════════════════════════
//  TEMPLATES  — list, create, edit, preview, approve, delete
// ══════════════════════════════════════════════════════════════════════

const tmpl = {
  list: [],
  editingId: null,
  approvingId: null,
  quill: null,
};

function tmplInitQuill() {
  if (tmpl.quill || typeof Quill === "undefined") return;
  tmpl.quill = new Quill("#tmpl-quill-editor", {
    theme: "snow",
    placeholder: "Write your email here. Use {{ name }} to personalise…",
    modules: {
      toolbar: [
        ["bold", "italic", "underline"],
        [{ color: [] }, { background: [] }],
        [{ size: ["small", false, "large", "huge"] }],
        [{ list: "ordered" }, { list: "bullet" }],
        ["link", "image"],
        ["clean"],
      ],
    },
  });
}

function tmplSetEditorMode(channel) {
  const isEmail = channel === "email";
  document.getElementById("tmpl-email-fields").style.display = isEmail ? "" : "none";
  document.getElementById("tmpl-quill-wrap").style.display = isEmail ? "" : "none";
  document.getElementById("tmpl-f-body-plain").style.display = isEmail ? "none" : "";
  document.getElementById("tmpl-var-btns").style.display = isEmail ? "" : "none";
}

function tmplStatusBadge(s) {
  const labels = { pending: "Pending", internal: "Internal", meta: "Meta ✓", dlt: "DLT ✓" };
  return `<span class="tmpl-badge ${s}">${labels[s] || s}</span>`;
}

function tmplChannelBadge(ch) {
  return `<span class="tmpl-badge ${ch}">${ch.toUpperCase()}</span>`;
}

async function cmpCheckScheduler() {
  const dot = document.getElementById("cmp-sched-dot");
  const label = document.getElementById("cmp-sched-label");
  if (!dot || !label) return;
  try {
    const res = await fetch("/gateway/ops");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const lane = (data.scheduler || []).find(l => l.name === "k3_campaign_scheduler");
    if (!lane) {
      dot.style.background = "var(--warn)";
      label.textContent = "Scheduler lane not found — Gateway may be misconfigured";
      return;
    }
    if (lane.in_dlq) {
      dot.style.background = "var(--danger)";
      label.style.color = "var(--danger)";
      label.textContent = "⚠ k3_campaign_scheduler is in the dead-letter queue — last tick failed";
      return;
    }
    dot.style.background = "#34d399";
    dot.style.boxShadow = "0 0 0 3px rgba(52,211,153,.2)";
    label.style.color = "var(--text)";
    if (lane.last_ran_iso) {
      const lastRan = new Date(lane.last_ran_iso);
      const lastRanLabel = lastRan.toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit" });
      const nextSecs = lane.next_due_secs;
      let nextLabel = "";
      if (nextSecs !== null && nextSecs !== undefined) {
        if (nextSecs <= 0) nextLabel = " · next tick imminent";
        else if (nextSecs < 60) nextLabel = ` · next tick in ${nextSecs}s`;
        else nextLabel = ` · next tick in ${Math.ceil(nextSecs / 60)}m`;
      }
      label.textContent = `● Scheduler running — last tick ${lastRanLabel} IST${nextLabel} (every 5 min)`;
    } else {
      label.textContent = "● Scheduler running — no tick yet this session";
    }
  } catch (e) {
    if (dot) { dot.style.background = "var(--warn)"; }
    if (label) { label.style.color = "var(--warn)"; label.textContent = `Scheduler status unavailable (${e.message})`; }
  }
}

async function tmplLoad() {
  const ch = document.getElementById("tmpl-ch-filter")?.value || "";
  const st = document.getElementById("tmpl-st-filter")?.value || "";
  const params = new URLSearchParams();
  if (ch) params.set("channel", ch);
  if (st) params.set("status", st);
  const wrap = document.getElementById("tmpl-list-wrap");
  if (!wrap) return;
  wrap.innerHTML = `<p class="muted">Loading…</p>`;
  try {
    const res = await fetch(`/templates?${params}`);
    tmpl.list = await res.json();
    tmplRenderList();
  } catch (e) {
    wrap.innerHTML = `<p style="color:var(--danger)">Failed to load templates: ${e.message}</p>`;
  }
}

function tmplRenderList() {
  const wrap = document.getElementById("tmpl-list-wrap");
  if (!wrap) return;
  if (!tmpl.list.length) {
    wrap.innerHTML = `<p class="muted">No templates found. Click "+ New Template" to create one.</p>`;
    return;
  }
  const rows = tmpl.list.map(t => {
    const bodySnippet = (t.body || "").replace(/\n/g, " ").slice(0, 80) + ((t.body || "").length > 80 ? "…" : "");
    return `<tr>
      <td><strong style="font-size:.84rem">${t.template_id}</strong><br><span style="font-size:.72rem;color:var(--muted)">${t.purpose || "—"}</span></td>
      <td>${tmplChannelBadge(t.channel)}</td>
      <td>${tmplStatusBadge(t.approval_status)}</td>
      <td style="color:var(--muted);font-size:.78rem;font-family:monospace;max-width:260px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${bodySnippet}</td>
      <td>
        <div style="display:flex;gap:.3rem;flex-wrap:nowrap">
          <button class="cmp-btn" onclick="tmplPreview('${t.template_id}')">Preview</button>
          <button class="cmp-btn primary" onclick="tmplOpenEdit('${t.template_id}')">Edit</button>
          <button class="cmp-btn warn" onclick="tmplOpenApprove('${t.template_id}')">Status</button>
          <button class="cmp-btn" style="color:var(--danger);border-color:var(--danger)" onclick="tmplDelete('${t.template_id}')">Delete</button>
        </div>
      </td>
    </tr>`;
  }).join("");
  wrap.innerHTML = `<table class="tmpl-table">
    <thead><tr>
      <th>Template</th><th>Channel</th><th>Status</th><th>Body preview</th><th>Actions</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

async function tmplPreview(templateId) {
  const pane = document.getElementById("tmpl-preview-pane");
  const bodyEl = document.getElementById("tmpl-preview-body");
  const idEl = document.getElementById("tmpl-preview-id");
  const chEl = document.getElementById("tmpl-preview-channel");
  const purposeEl = document.getElementById("tmpl-preview-purpose");
  if (!pane) return;

  bodyEl.textContent = "Rendering…";
  pane.style.display = "block";
  pane.scrollIntoView({ behavior: "smooth", block: "nearest" });

  try {
    const res = await fetch(`/templates/${templateId}/preview`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    const data = await res.json();
    if (!res.ok) { bodyEl.textContent = data.detail || "Error"; return; }
    const t = tmpl.list.find(x => x.template_id === templateId) || {};
    idEl.textContent = templateId;
    chEl.className = `tmpl-badge ${t.channel || ""}`;
    chEl.textContent = (t.channel || "").toUpperCase();
    purposeEl.textContent = data.purpose || "";
    bodyEl.textContent = data.rendered_body;
  } catch (e) {
    bodyEl.textContent = "Failed: " + e.message;
  }
}

function tmplOpenCreate() {
  tmpl.editingId = null;
  document.getElementById("tmpl-modal-title").textContent = "New Template";
  document.getElementById("tmpl-f-id").value = "";
  document.getElementById("tmpl-f-id").disabled = false;
  document.getElementById("tmpl-f-channel").value = "email";
  document.getElementById("tmpl-f-purpose").value = "";
  document.getElementById("tmpl-f-language").value = "en";
  document.getElementById("tmpl-f-subject").value = "";
  document.getElementById("tmpl-f-reply-to").value = "";
  document.getElementById("tmpl-f-body-plain").value = "";
  document.getElementById("tmpl-f-status").value = "pending";
  document.getElementById("tmpl-modal-notice").innerHTML = "";
  tmplSetEditorMode("email");
  tmplInitQuill();
  if (tmpl.quill) tmpl.quill.setContents([]);
  openModal("#tmpl-modal");
}

function tmplOpenEdit(templateId) {
  const t = tmpl.list.find(x => x.template_id === templateId);
  if (!t) return;
  tmpl.editingId = templateId;
  const ch = t.channel || "email";
  document.getElementById("tmpl-modal-title").textContent = "Edit Template";
  document.getElementById("tmpl-f-id").value = t.template_id;
  document.getElementById("tmpl-f-id").disabled = true;
  document.getElementById("tmpl-f-channel").value = ch;
  document.getElementById("tmpl-f-purpose").value = t.purpose || "";
  document.getElementById("tmpl-f-language").value = t.language || "en";
  document.getElementById("tmpl-f-subject").value = t.subject || "";
  document.getElementById("tmpl-f-reply-to").value = t.reply_to || "";
  document.getElementById("tmpl-f-body-plain").value = ch !== "email" ? (t.body || "") : "";
  document.getElementById("tmpl-f-status").value = t.approval_status || "pending";
  document.getElementById("tmpl-modal-notice").innerHTML = "";
  tmplSetEditorMode(ch);
  tmplInitQuill();
  if (ch === "email" && tmpl.quill) {
    // Body is HTML — paste it into Quill
    tmpl.quill.clipboard.dangerouslyPasteHTML(t.body || "");
  }
  openModal("#tmpl-modal");
}

function tmplCloseModal() {
  closeOpenModalStack();
}

async function tmplModalSubmit() {
  const notice = document.getElementById("tmpl-modal-notice");
  const id = document.getElementById("tmpl-f-id").value.trim();
  const channel = document.getElementById("tmpl-f-channel").value;
  const isEmail = channel === "email";
  // For email: get HTML from Quill; for others: plain textarea
  let bodyValue = isEmail
    ? (tmpl.quill ? tmpl.quill.root.innerHTML : "")
    : document.getElementById("tmpl-f-body-plain").value;
  // Quill empty state produces "<p><br></p>" — treat as empty
  if (bodyValue === "<p><br></p>") bodyValue = "";
  const body = {
    template_id: id,
    channel,
    purpose: document.getElementById("tmpl-f-purpose").value.trim(),
    language: document.getElementById("tmpl-f-language").value.trim() || "en",
    subject: document.getElementById("tmpl-f-subject").value.trim() || null,
    reply_to: document.getElementById("tmpl-f-reply-to").value.trim() || null,
    body: bodyValue,
    approval_status: document.getElementById("tmpl-f-status").value,
  };
  if (!body.body) { notice.innerHTML = `<div class="cmp-notice err">Body is required.</div>`; return; }
  if (!tmpl.editingId && !id) { notice.innerHTML = `<div class="cmp-notice err">Template ID is required.</div>`; return; }

  notice.innerHTML = `<div class="cmp-notice">Saving…</div>`;
  try {
    const url = tmpl.editingId ? `/templates/${tmpl.editingId}` : "/templates";
    const method = tmpl.editingId ? "PUT" : "POST";
    const res = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await res.json();
    if (!res.ok) { notice.innerHTML = `<div class="cmp-notice err">${data.detail || "Save failed"}</div>`; return; }
    notice.innerHTML = `<div class="cmp-notice ok">Saved ✓</div>`;
    setTimeout(tmplCloseModal, 700);
    tmplLoad();
  } catch (e) {
    notice.innerHTML = `<div class="cmp-notice err">${e.message}</div>`;
  }
}

function tmplOpenApprove(templateId) {
  const t = tmpl.list.find(x => x.template_id === templateId);
  if (!t) return;
  tmpl.approvingId = templateId;
  document.getElementById("tmpl-approve-info").textContent = `Template: ${templateId} — current status: ${t.approval_status}`;
  document.getElementById("tmpl-approve-status").value = t.approval_status || "pending";
  document.getElementById("tmpl-approve-notice").innerHTML = "";
  openModal("#tmpl-approve-modal");
}

function tmplCloseApprove() {
  closeOpenModalStack();
}

async function tmplApproveSubmit() {
  if (!tmpl.approvingId) return;
  const notice = document.getElementById("tmpl-approve-notice");
  const status = document.getElementById("tmpl-approve-status").value;
  notice.innerHTML = `<div class="cmp-notice">Updating…</div>`;
  try {
    const res = await fetch(`/templates/${tmpl.approvingId}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approval_status: status, approved_by: "ui" }),
    });
    const data = await res.json();
    if (!res.ok) { notice.innerHTML = `<div class="cmp-notice err">${data.detail || "Failed"}</div>`; return; }
    notice.innerHTML = `<div class="cmp-notice ok">Status updated ✓</div>`;
    setTimeout(tmplCloseApprove, 700);
    tmplLoad();
  } catch (e) {
    notice.innerHTML = `<div class="cmp-notice err">${e.message}</div>`;
  }
}

async function tmplDelete(templateId) {
  if (!confirm(`Delete template "${templateId}"? This cannot be undone.`)) return;
  try {
    const res = await fetch(`/templates/${templateId}`, { method: "DELETE" });
    if (!res.ok) { const d = await res.json(); alert(d.detail || "Delete failed"); return; }
    tmplLoad();
    const pane = document.getElementById("tmpl-preview-pane");
    if (pane && document.getElementById("tmpl-preview-id")?.textContent === templateId) {
      pane.style.display = "none";
    }
  } catch (e) {
    alert("Delete failed: " + e.message);
  }
}

function initTemplates() {
  const tmplModal = document.getElementById("tmpl-modal");
  const tmplApproveModal = document.getElementById("tmpl-approve-modal");
  const cmpSendModal = document.getElementById("cmp-send-modal");
  const cmpWizard = document.getElementById("cmp-wizard");
  [tmplModal, tmplApproveModal, cmpSendModal, cmpWizard].forEach((modal) => {
    if (modal && modal.parentElement !== document.body) document.body.appendChild(modal);
  });

  document.getElementById("tmpl-reload")?.addEventListener("click", tmplLoad);
  document.getElementById("tmpl-ch-filter")?.addEventListener("change", tmplLoad);
  document.getElementById("tmpl-st-filter")?.addEventListener("change", tmplLoad);
  document.getElementById("tmpl-new")?.addEventListener("click", tmplOpenCreate);

  // Channel selector toggles email-specific fields and editor mode
  document.getElementById("tmpl-f-channel")?.addEventListener("change", e => {
    tmplSetEditorMode(e.target.value);
    if (e.target.value === "email") { tmplInitQuill(); }
  });

  // Variable quick-insert buttons → insert into Quill (email) or plain textarea
  document.querySelectorAll(".tmpl-var-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const v = btn.dataset.var;
      const ch = document.getElementById("tmpl-f-channel")?.value || "email";
      if (ch === "email" && tmpl.quill) {
        const range = tmpl.quill.getSelection(true);
        tmpl.quill.insertText(range ? range.index : tmpl.quill.getLength() - 1, v);
      } else {
        const ta = document.getElementById("tmpl-f-body-plain");
        if (!ta) return;
        const s = ta.selectionStart, e2 = ta.selectionEnd;
        ta.value = ta.value.slice(0, s) + v + ta.value.slice(e2);
        ta.selectionStart = ta.selectionEnd = s + v.length;
        ta.focus();
      }
    });
  });

  document.getElementById("tmpl-modal-close")?.addEventListener("click", tmplCloseModal);
  document.getElementById("tmpl-modal-cancel")?.addEventListener("click", tmplCloseModal);
  document.getElementById("tmpl-modal")?.addEventListener("click", e => {
    if (e.target === e.currentTarget) tmplCloseModal();
  });
  document.getElementById("tmpl-modal-submit")?.addEventListener("click", tmplModalSubmit);

  document.getElementById("tmpl-approve-close")?.addEventListener("click", tmplCloseApprove);
  document.getElementById("tmpl-approve-cancel")?.addEventListener("click", tmplCloseApprove);
  document.getElementById("tmpl-approve-modal")?.addEventListener("click", e => {
    if (e.target === e.currentTarget) tmplCloseApprove();
  });
  document.getElementById("tmpl-approve-submit")?.addEventListener("click", tmplApproveSubmit);

  document.getElementById("tmpl-preview-close")?.addEventListener("click", () => {
    document.getElementById("tmpl-preview-pane").style.display = "none";
  });

  tmplLoad();
}

initTemplates();

// ══════════════════════════════════════════════════════════════════════
//  OPT-OUTS  — list + re-subscribe
// ══════════════════════════════════════════════════════════════════════

async function optoutLoad() {
  const wrap = document.getElementById("optout-list-wrap");
  if (!wrap) return;
  wrap.innerHTML = `<p class="muted">Loading…</p>`;
  try {
    const res = await fetch("/campaigns/optouts");
    const list = await res.json();
    if (!list.length) {
      wrap.innerHTML = `<div class="optout-empty">✓ No opted-out contacts — your list is clean.</div>`;
      return;
    }
    const rows = list.map(r => {
      const dt = r.opted_out_at
        ? new Date(r.opted_out_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short" })
        : "—";
      const name = escapeHtml(r.name || "—");
      const email = escapeHtml(r.email || "—");
      const phone = escapeHtml(r.msisdn || "—");
      const ref = escapeHtml(r.contact_ref);
      return `<tr>
        <td>
          <strong style="font-size:.85rem">${name}</strong>
          <br><span style="font-size:.72rem;color:var(--muted)">${ref}</span>
        </td>
        <td style="font-size:.82rem">${email}</td>
        <td style="font-size:.82rem">${phone}</td>
        <td style="font-size:.78rem;color:var(--muted)">${dt} IST</td>
        <td>
          <button class="cmp-btn" style="color:var(--ok);border-color:var(--ok)"
            onclick="optoutResubscribe('${ref.replace(/'/g,"\\'")}', this)">
            ↩ Re-subscribe
          </button>
        </td>
      </tr>`;
    }).join("");
    wrap.innerHTML = `
      <div style="margin-bottom:.6rem;font-size:.82rem;color:var(--muted)">
        ${list.length} contact${list.length !== 1 ? "s" : ""} on the suppression list
      </div>
      <table class="optout-table">
        <thead><tr>
          <th>Contact</th><th>Email</th><th>Phone</th><th>Opted Out</th><th>Action</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  } catch (e) {
    wrap.innerHTML = `<p style="color:var(--danger)">Failed to load: ${e.message}</p>`;
  }
}

async function optoutResubscribe(contactRef, btn) {
  if (!confirm(`Re-subscribe "${contactRef}"? They will receive future campaign emails again.`)) return;
  btn.disabled = true;
  btn.textContent = "Working…";
  try {
    const res = await fetch(`/campaigns/optouts/${encodeURIComponent(contactRef)}/resubscribe`, { method: "POST" });
    const data = await res.json();
    if (!res.ok) { alert(data.detail || "Failed"); btn.disabled = false; btn.textContent = "↩ Re-subscribe"; return; }
    // Remove the row immediately without full reload
    btn.closest("tr").style.opacity = "0.4";
    btn.textContent = "Done ✓";
    setTimeout(optoutLoad, 800);
  } catch (e) {
    alert("Error: " + e.message);
    btn.disabled = false;
    btn.textContent = "↩ Re-subscribe";
  }
}

function initOptouts() {
  document.getElementById("optout-reload")?.addEventListener("click", optoutLoad);
  optoutLoad();
}

initOptouts();
