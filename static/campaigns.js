(() => {
  const root = document.getElementById("campaigns");
  if (!root) return;

  function closeOpenModalStack() {
    ["#cmp-wizard", "#tmpl-modal"].forEach((selector) => {
      const el = document.querySelector(selector);
      if (el) el.classList.remove("open");
    });
  }

  function openModal(selector) {
    const el = document.querySelector(selector);
    if (!el) return;
    closeOpenModalStack();
    el.classList.add("open");
  }

  // Panels use backdrop-filter, which creates stacking contexts. Portal the
  // fixed dialogs to body so later dashboard panels cannot cover or clip them.
  const modal = document.getElementById("cmp-wizard");
  if (modal && modal.parentElement !== document.body) document.body.appendChild(modal);
  const tmplModal = document.getElementById("tmpl-modal");
  if (tmplModal && tmplModal.parentElement !== document.body) document.body.appendChild(tmplModal);

  // The template form is loaded into #tmpl-form-host via HTMX; TinyMCE inits
  // from an inline <script> inside the fragment, which HTMX does NOT execute
  // on swap. Re-run it after every swap into that host.
  function initTmplEditor() {
    if (!window.tinymce) return;
    const old = tinymce.get("tmpl-f-body");
    if (old) old.remove();
    tinymce.init({
      selector: "#tmpl-f-body", license_key: "gpl", height: 340, menubar: false,
      plugins: "lists link image code autolink",
      toolbar: "undo redo | blocks | bold italic underline forecolor backcolor | alignleft aligncenter alignright | bullist numlist | link image | code",
      skin: "oxide-dark", content_css: "dark", branding: false,
      // Keep pasted/inserted images as base64 so they persist on save; the send
      // path converts them to inline cid: images that email clients render.
      paste_data_images: true, automatic_uploads: false, file_picker_types: "image",
      setup: (ed) => { ed.on("change input keyup", () => ed.save()); },
    });
  }
  document.body.addEventListener("htmx:afterSwap", (event) => {
    if (event.target && event.target.id === "tmpl-form-host") initTmplEditor();
  });

  const state = {
    campaigns: [], templates: [], selected: null, editing: null, upload: null,
    page: 1, pageSize: 10, totalCampaigns: 0,
  };
  const num = (v) => Number(v || 0);
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);
  const entityNames = {
    "YEXIS-EL": "Yexis Electronics — Kerala",
    "YEXIS-SL": "Yexis Electronics — Tamil Nadu",
    "YEXIS-SOL": "Yexis Solutions",
  };
  const typeNames = {
    dealer_promo: "Dealer promotion", end_customer: "End customer",
    scheme_push: "Scheme push", event: "Event",
  };
  const statusNames = {
    draft: "Draft", audience_built: "Audience built", broadcast: "Broadcast",
    tracking: "Tracking", closed: "Closed",
  };
  const channelNames = {
    auto: "Automatic", email: "Email", whatsapp: "WhatsApp", telegram: "Telegram",
    voice: "Voice", push: "Push", linkedin: "LinkedIn",
  };

  function templateById(tid) {
    return state.templates.find((t) => t.template_id === tid) || null;
  }

  function templateIsPending(tid) {
    const t = templateById(tid);
    return !!t && (t.approval_status || "") === "pending";
  }

  function syncApproveButton(id) {
    const btn = $(`cmp-approve-${id}`);
    if (!btn) return;
    const sel = $(`cmp-template-${id}`);
    const pending = templateIsPending(sel?.value || "");
    btn.style.display = pending ? "" : "none";
  }

  function syncPreview(id) {
    const box = $(`cmp-tpl-preview-${id}`);
    if (!box) return;
    const sel = $(`cmp-template-${id}`);
    const tid = sel?.value || "";
    if (!tid || !box.dataset.open) { box.style.display = "none"; box.innerHTML = ""; return; }
    if (!tid) { box.style.display = "none"; box.innerHTML = ""; return; }
    box.style.display = "block";
    box.innerHTML = '<p class="muted" style="font-size:.78rem">Rendering preview…</p>';
    fetch(`/templates/${encodeURIComponent(tid)}/preview`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
      .then(async (resp) => {
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.detail || `Preview failed (${resp.status})`);
        const atts = (data.attachments || []).map((a) => a.filename).join(", ");
        box.innerHTML = `
          <p class="muted" style="font-size:.74rem;margin:0 0 .35rem">Subject: <strong>${esc(data.rendered_subject || data.subject || "(campaign name used as fallback)")}</strong>${atts ? ` · Attachments: ${esc(atts)}` : ""}</p>
          <div style="border:1px solid var(--panel-border);border-radius:8px;padding:1rem;background:#ffffff;color:#111;max-height:18rem;overflow:auto;font-size:.85rem">${data.rendered_body || "(empty body)"}</div>
          <p class="muted" style="font-size:.7rem;margin:.35rem 0 0">Rendered with sample data — every recipient gets their own values via {{ fields }}, open/click tracking added at send time.</p>`;
      })
      .catch((error) => {
        box.innerHTML = `<p class="cmp-msg" role="alert" style="margin:0">${esc(error.message)}</p>`;
      });
  }

  async function approveTemplate(id) {
    const sel = $(`cmp-template-${id}`);
    const tid = sel?.value || "";
    if (!tid) return;
    const t = templateById(tid);
    if (!window.confirm(`Approve template “${tid}” for sending? This marks it as reviewed and ready to broadcast.`)) return;
    // WhatsApp templates need 'meta' status; everything else sends fine as 'internal'.
    const status = t && t.channel === "wa" ? "meta" : "internal";
    try {
      await request(`/templates/${encodeURIComponent(tid)}/approve`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approval_status: status, approved_by: "ui-campaign" }),
      });
      await loadTemplates();
      detailMessage(id, `Template “${tid}” approved (${status}). Ready to send.`);
    } catch (error) {
      detailMessage(id, error.message, true);
    }
  }

  async function request(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.error || `Request failed (${response.status})`);
    return data;
  }

  function flash(message, isError = false) {
    const el = $("cmp-form-message");
    if (!el) return;
    el.textContent = message;
    el.style.display = "block";
    el.style.color = isError ? "#f87171" : "var(--text)";
  }

  function templateLabel(t) {
    const pending = (t.approval_status || "") === "pending" ? " ⚠ pending approval" : "";
    return `${t.template_id} — ${t.purpose || t.channel} (${t.channel})${pending}`;
  }

  function templateOptionsHtml(selectedId) {
    return '<option value="">Select message template</option>' + state.templates.map((t) =>
      `<option value="${esc(t.template_id)}" ${t.template_id === (selectedId || null) ? "selected" : ""}>${esc(templateLabel(t))}</option>`
    ).join("");
  }

  async function loadTemplates() {
    try {
      state.templates = await request("/templates");
      const select = $("cmp-f-template");
      if (select) {
        select.innerHTML = '<option value="">Choose a template later</option>' + state.templates.map((t) =>
          `<option value="${esc(t.template_id)}">${esc(templateLabel(t))}</option>`
        ).join("");
      }
      // Notify any open campaign detail so its dropdowns pick up new templates.
      document.dispatchEvent(new CustomEvent("cmp:templates-reloaded"));
    } catch (error) {
      console.warn("Could not load campaign templates", error);
    }
  }

  // Keep every open template dropdown in sync after the composer modal closes.
  document.addEventListener("cmp:templates-reloaded", () => {
    document.querySelectorAll("select[id^='cmp-template-']").forEach((sel) => {
      const id = sel.id.replace("cmp-template-", "");
      const campaign = state.campaigns.find((c) => c.campaign_id === id);
      const current = sel.value || (campaign && campaign.message_template_id) || "";
      sel.innerHTML = templateOptionsHtml(current);
      syncApproveButton(id);
    });
  });

  function openTemplateComposer() {
    // Reuse the Message Templates compose modal (same rich editor, insert-field
    // menu and attachments as the Templates panel). When it closes, refresh.
    const modal = document.getElementById("tmpl-modal");
    if (!modal) { window.alert("Template composer is not available on this page (templates permission missing)."); return; }
    const host = $("tmpl-form-host");
    if (host && window.htmx) {
      htmx.ajax("GET", "/htmx/templates/form", { target: "#tmpl-form-host", swap: "innerHTML" });
    }
    openModal("#tmpl-modal");
    const obs = new MutationObserver(() => {
      if (!modal.classList.contains("open")) {
        obs.disconnect();
        loadTemplates();
      }
    });
    obs.observe(modal, { attributes: true, attributeFilter: ["class"] });
  }

  async function loadCampaigns(keepSelection = true) {
    const wrap = $("cmp-list-wrap");
    if (!wrap) return;
    wrap.innerHTML = '<p class="muted">Loading campaigns…</p>';
    try {
      const entity = encodeURIComponent($("cmp-entity-filter")?.value || "YEXIS-EL");
      const response = await fetch(
        `/campaigns?entity=${entity}&limit=${state.pageSize}&offset=${(state.page - 1) * state.pageSize}`
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || data.error || `Request failed (${response.status})`);
      state.campaigns = data;
      state.totalCampaigns = Number(response.headers.get("X-Total-Count") || data.length);
      const pageCount = Math.max(1, Math.ceil(state.totalCampaigns / state.pageSize));
      if (state.page > pageCount) {
        state.page = pageCount;
        return loadCampaigns(keepSelection);
      }
      if (!keepSelection || !state.selected) state.selected = null;
      else state.selected = state.campaigns.find((c) => c.campaign_id === state.selected.campaign_id) || state.selected;
      renderList();
      if (state.selected) renderDetail(state.selected);
      else { $("cmp-detail").style.display = "none"; }
      markPolled();
    } catch (error) {
      wrap.innerHTML = `<p class="cmp-msg" role="alert">Could not load campaigns: ${esc(error.message)}</p>`;
    }
  }

  // ── Silent background refresh ──────────────────────────────────────────
  // Campaign sends trickle out over hours (K3 caps each tick), so the list
  // and the open detail panel would otherwise sit frozen until a manual
  // Reload. This polls quietly in the background: it never shows the
  // "Loading…" flash, patches only the live numbers/status in place, and
  // never touches a control you're interacting with.
  const POLL_MS = 10000;
  let pollTimer = null;
  let lastPolledAt = 0;

  // True while the user is focused on an input/select/textarea inside the
  // detail panel — we must not rebuild those controls out from under them.
  function detailHasFocus() {
    const el = document.activeElement;
    return !!(el && el.closest("#cmp-detail") &&
              /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName));
  }

  // Patch just the display-only numbers/status — safe to call any time, it
  // touches no interactive control and never steals focus.
  function patchDetail(c) {
    const badge = $("cmp-m-statusbadge");
    if (badge) {
      badge.textContent = statusNames[c.status] || c.status;
      badge.className = `cmp-status status-${c.status}`;
    }
    const typeStatus = $("cmp-m-typestatus");
    if (typeStatus) typeStatus.textContent =
      `${typeNames[c.campaign_type] || c.campaign_type} · ${statusNames[c.status] || c.status}`;
    const audSent = $("cmp-m-audsent");
    if (audSent) audSent.textContent = `${num(c.audience_count)} / ${num(c.sent_count)}`;
    const audLine = $("cmp-m-audienceline");
    if (audLine) audLine.textContent = `${num(c.audience_count)} contacts · ${num(c.sent_count)} sent`;
  }

  async function silentLoad() {
    try {
      const entity = encodeURIComponent($("cmp-entity-filter")?.value || "YEXIS-EL");
      const response = await fetch(
        `/campaigns?entity=${entity}&limit=${state.pageSize}&offset=${(state.page - 1) * state.pageSize}`
      );
      if (!response.ok) return;              // silent — never disturb the UI on a transient error
      const data = await response.json().catch(() => null);
      if (!Array.isArray(data)) return;
      state.campaigns = data;
      state.totalCampaigns = Number(response.headers.get("X-Total-Count") || data.length);
      renderList();                          // list has no inputs — always safe to rebuild
      if (state.selected) {
        const fresh = state.campaigns.find((c) => c.campaign_id === state.selected.campaign_id);
        if (fresh) {
          state.selected = fresh;
          const box = $("cmp-detail");
          // Status changed → the workflow controls differ, so a full re-render
          // is needed, but only when the user isn't mid-edit. Otherwise just
          // patch the numbers in place.
          if (box && box.dataset.renderedStatus !== fresh.status && !detailHasFocus()) {
            renderDetail(fresh, { scroll: false });
          } else {
            patchDetail(fresh);
          }
        }
      }
      markPolled();
    } catch (_e) {
      /* network blip — try again on the next tick */
    }
  }

  function ensurePollLabel() {
    let el = $("cmp-poll-status");
    if (!el) {
      const reload = $("cmp-reload");
      if (!reload || !reload.parentNode) return null;
      el = document.createElement("span");
      el.id = "cmp-poll-status";
      el.className = "muted";
      el.style.cssText = "font-size:.72rem;margin-left:.5rem;white-space:nowrap";
      reload.parentNode.insertBefore(el, reload.nextSibling);
    }
    return el;
  }

  function updatePolledLabel() {
    const el = ensurePollLabel();
    if (!el || !lastPolledAt) return;
    const secs = Math.round((Date.now() - lastPolledAt) / 1000);
    el.textContent = secs < 5 ? "● live · just now" : `● live · updated ${secs}s ago`;
  }

  function markPolled() {
    lastPolledAt = Date.now();
    updatePolledLabel();
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(() => {
      if (document.hidden) return;                                   // tab in background
      if (document.querySelector("#cmp-wizard.open, #tmpl-modal.open")) return;  // a dialog is open
      silentLoad();
    }, POLL_MS);
    setInterval(updatePolledLabel, 1000);   // keep the "Xs ago" label ticking
    // Returning to the tab after a while: refresh at once rather than waiting.
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) silentLoad();
    });
  }

  // ── Deliverability (SPF/DKIM/DMARC) panel ──────────────────────────────
  const DELIV_COLORS = { pass: "#4ade80", warn: "#fbbf24", fail: "#f87171" };
  const DELIV_ICON = { pass: "✓", warn: "!", fail: "✕" };

  function renderDeliverability(d) {
    if (d.error) {
      return `<div class="cmp-msg" role="alert">Could not check deliverability: ${esc(d.error)}</div>`;
    }
    const chip = (label, rec) => {
      const st = rec.status || "warn";
      const color = DELIV_COLORS[st] || "#9ca3af";
      const found = rec.record
        ? `<div class="muted" style="font-size:.7rem;margin-top:.25rem;word-break:break-all;font-family:monospace">${esc(Array.isArray(rec.record) ? rec.record.join(" | ") : rec.record)}</div>`
        : "";
      return `<div style="border:1px solid var(--panel-border);border-left:3px solid ${color};border-radius:8px;padding:.55rem .7rem">
        <div style="display:flex;align-items:center;gap:.4rem">
          <span style="color:${color};font-weight:700">${DELIV_ICON[st] || "?"}</span>
          <strong style="font-size:.82rem">${esc(label)}</strong>
          <span style="color:${color};font-size:.72rem;text-transform:uppercase;font-weight:700">${esc(st)}</span>
        </div>
        <div style="font-size:.76rem;margin-top:.25rem">${esc(rec.message || "")}</div>
        ${found}
      </div>`;
    };
    const overall = d.overall || "warn";
    const oc = DELIV_COLORS[overall] || "#9ca3af";
    const notes = (d.notes || []).map((n) => `<li>${esc(n)}</li>`).join("");
    return `<div style="border:1px solid var(--panel-border);border-radius:12px;padding:1rem;background:rgba(255,255,255,.03)">
      <div style="display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;margin-bottom:.7rem">
        <strong style="font-size:.9rem">Sending domain: ${esc(d.domain || "—")}</strong>
        <span style="background:${oc};color:#0b1020;font-weight:700;font-size:.72rem;padding:.15rem .55rem;border-radius:999px;text-transform:uppercase">${esc(overall)}</span>
        <span class="muted" style="font-size:.72rem;margin-left:auto">${esc(d.from_address || "")}</span>
        <button class="cmp-btn" data-cmp="close-deliverability" style="font-size:.72rem">Close</button>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:.6rem">
        ${chip("SPF", d.spf || {})}${chip("DKIM", d.dkim || {})}${chip("DMARC", d.dmarc || {})}
      </div>
      ${notes ? `<ul class="muted" style="font-size:.74rem;margin:.7rem 0 0;padding-left:1.1rem">${notes}</ul>` : ""}
    </div>`;
  }

  async function checkDeliverability() {
    const panel = $("cmp-deliverability-panel");
    const btn = $("cmp-deliverability");
    if (!panel) return;
    panel.style.display = "block";
    panel.innerHTML = '<p class="muted">Checking SPF, DKIM and DMARC…</p>';
    if (btn) btn.disabled = true;
    try {
      const resp = await fetch("/mail/deliverability");
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.detail || data.error || `Request failed (${resp.status})`);
      panel.innerHTML = renderDeliverability(data);
    } catch (e) {
      panel.innerHTML = `<div class="cmp-msg" role="alert">Could not check deliverability: ${esc(e.message)}</div>`;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function renderList() {
    const wrap = $("cmp-list-wrap");
    if (!wrap) return;
    if (!state.campaigns.length) {
      wrap.innerHTML = '<p class="muted">No campaigns yet. Select <strong>+ New Campaign</strong> to create your first one.</p>';
      return;
    }
    const header = `<div class="cmp-list-row muted" aria-hidden="true" style="font-size:.72rem;font-weight:700;text-transform:uppercase">
      <span>Campaign</span><span class="cmp-mobile-hide">Type</span><span>Status</span><span class="cmp-mobile-hide">Audience / sent</span><span>Actions</span></div>`;
    const rows = state.campaigns.map((c) => {
      const id = esc(c.campaign_id);
      const isDraft = c.status === "draft";
      const buttons = `<div class="cmp-actions">
        <button class="cmp-btn" data-cmp="view" data-id="${id}">View</button>
        ${isDraft ? `<button class="cmp-btn" data-cmp="edit" data-id="${id}">Edit</button><button class="cmp-btn danger" data-cmp="delete" data-id="${id}">Delete</button>` : ""}
      </div>`;
      return `<div class="cmp-list-row">
        <div><strong>${esc(c.name)}</strong><div class="muted" style="font-size:.74rem">${esc(c.campaign_ref)} · ${esc(entityNames[c.entity] || c.entity)}</div></div>
        <span class="cmp-mobile-hide">${esc(typeNames[c.campaign_type] || c.campaign_type)}</span>
        <span class="cmp-status status-${esc(c.status)}">${esc(statusNames[c.status] || c.status)}</span>
        <span class="cmp-mobile-hide">${Number(c.audience_count || 0)} audience · ${Number(c.sent_count || 0)} sent</span>
        ${buttons}</div>`;
    }).join("");
    const pageCount = Math.max(1, Math.ceil(state.totalCampaigns / state.pageSize));
    const pagination = `<div class="section-pagination">
      <button class="ghost" data-cmp-page="${state.page - 1}" ${state.page <= 1 ? "disabled" : ""}>← Newer</button>
      <span class="muted">Page ${state.page} of ${pageCount} · ${state.totalCampaigns} campaigns</span>
      <button class="ghost" data-cmp-page="${state.page + 1}" ${state.page >= pageCount ? "disabled" : ""}>Older →</button>
    </div>`;
    wrap.innerHTML = header + rows + pagination;
  }

  function renderDetail(c, opts = {}) {
    const box = $("cmp-detail");
    if (!box) return;
    state.selected = c;
    const id = esc(c.campaign_id);
    const audienceBuilt = c.status === "audience_built";
    const hasAudience = Number(c.audience_count || 0) > 0;
    const sourceOptions = ["dealermart", "zoho_books", "zoho_crm", "all", "apollo", "rocketreach", "justdial", "scribd", "google_drive"]
      .map((src) => `<option value="${src}">${({dealermart:"DealerMart",zoho_books:"Zoho Books",zoho_crm:"Zoho CRM",all:"All available sources",apollo:"Apollo prospects",rocketreach:"RocketReach",justdial:"JustDial",scribd:"Scribd document",google_drive:"Google Drive"})[src]}</option>`).join("");
    const templateOptions = templateOptionsHtml(c.message_template_id);
    const channelOptions = Object.entries(channelNames).map(([key, label]) =>
      `<option value="${key}" ${(c.default_channel || "auto") === key ? "selected" : ""}>${label}</option>`
    ).join("");
    const workflow = c.status === "draft" ? `
      <h4>1 · Build audience</h4>
      <div class="cmp-toolbar">
        <select id="cmp-source-${id}" aria-label="Audience source">${sourceOptions}</select>
        <button class="cmp-btn primary" data-cmp="audience" data-id="${id}">Build audience</button>
        <input type="file" accept=".xlsx,.xls,.csv" id="cmp-upload-file-${id}" aria-label="Upload contacts file">
        <button class="cmp-btn" data-cmp="upload" data-id="${id}">Upload contacts</button>
      </div><p class="muted" style="font-size:.76rem">Draft campaigns can be edited or deleted. Building an audience moves the campaign to the next stage.</p>
      <h4>Preview / send a test</h4>
      <div class="cmp-toolbar">
        <select id="cmp-template-${id}" aria-label="Message template">${templateOptions}</select>
        <button class="cmp-btn" data-cmp="new-template" data-id="${id}" title="Compose a custom template, then pick it here">＋ New template</button>
        <button class="cmp-btn" data-cmp="refresh-templates" data-id="${id}" title="Reload the template list">↻</button>
      </div>
      <label class="cmp-control">Send a test to yourself before building the audience — see exactly how it lands (works even before approval)
        <div class="cmp-toolbar" style="margin-top:.3rem">
          <input id="cmp-test-email-${id}" type="email" placeholder="you@example.com"
            style="min-width:230px;background:rgba(255,255,255,.05);border:1px solid var(--panel-border);border-radius:8px;padding:.45rem .6rem;color:var(--text);font-size:.83rem">
          <button class="cmp-btn" data-cmp="test-send" data-id="${id}">✉ Send test email</button>
        </div>
      </label>
      <p class="muted" style="font-size:.74rem">Tests use placeholder values (e.g. “Sample Name”) until an audience exists; once contacts are added, tests render with a real sample contact.</p>` : `
      <h4>1 · Audience</h4><p id="cmp-m-audienceline">${Number(c.audience_count || 0)} contacts · ${Number(c.sent_count || 0)} sent</p>`;
    const delivery = audienceBuilt ? `
      <h4>2 · Review and send</h4>
      <div class="cmp-toolbar">
        <select id="cmp-channel-${id}" aria-label="Broadcast channel">${channelOptions}</select>
        <select id="cmp-template-${id}" aria-label="Message template">${templateOptions}</select>
        <button class="cmp-btn primary" data-cmp="new-template" data-id="${id}" title="Compose a custom template with the rich editor, then pick it here">＋ New template</button>
        <button class="cmp-btn" data-cmp="refresh-templates" data-id="${id}" title="Reload the template list">↻</button>
      </div>
      <p class="muted" style="font-size:.74rem">Need a custom design? ＋ New template opens the full template composer (rich text, images, attachments). After saving there, click ↻ or it refreshes automatically — then select it above. Templates marked “pending approval” must be approved before sending.</p>
      <label class="cmp-control">Schedule (optional, local time)<input id="cmp-schedule-${id}" type="datetime-local"></label>
      <label class="cmp-control" style="display:flex;align-items:center;gap:.45rem;font-size:.78rem">
        <input type="checkbox" id="cmp-preview-toggle-${id}" data-cmp="toggle-preview" data-id="${id}" style="width:auto">
        Show template preview (rendered with sample data)
      </label>
      <div id="cmp-tpl-preview-${id}" style="display:none"></div>
      <label class="cmp-control">Send a test to yourself first — see exactly how it lands (works even before approval)
        <div class="cmp-toolbar" style="margin-top:.3rem">
          <input id="cmp-test-email-${id}" type="email" placeholder="you@example.com"
            style="min-width:230px;background:rgba(255,255,255,.05);border:1px solid var(--panel-border);border-radius:8px;padding:.45rem .6rem;color:var(--text);font-size:.83rem">
          <button class="cmp-btn" data-cmp="test-send" data-id="${id}">✉ Send test email</button>
        </div>
      </label>
      <div class="cmp-toolbar">
        <button class="cmp-btn success" data-cmp="approve-template" data-id="${id}" id="cmp-approve-${id}" style="display:none" title="Approve the selected pending template so it can be sent">✓ Approve template</button>
        <button class="cmp-btn warn" data-cmp="send" data-id="${id}">Send now</button><button class="cmp-btn" data-cmp="schedule" data-id="${id}">Schedule</button>
      </div>` : "";
    const tracking = ["audience_built", "broadcast", "tracking", "closed"].includes(c.status) ? `
      <h4>2 · Delivery and performance</h4>
      <div class="cmp-toolbar">
        <button class="cmp-btn" data-cmp="contacts" data-status="all" data-id="${id}">All recipients</button>
        <button class="cmp-btn success" data-cmp="contacts" data-status="sent" data-id="${id}">Sent</button>
        <button class="cmp-btn" data-cmp="contacts" data-status="pending" data-id="${id}">Pending</button>
        <button class="cmp-btn danger" data-cmp="contacts" data-status="failed" data-id="${id}">Failed / bounced</button>
        ${["broadcast", "tracking", "closed"].includes(c.status) ? `<button class="cmp-btn" data-cmp="analytics" data-id="${id}">Refresh analytics</button><button class="cmp-btn" data-cmp="engagement" data-id="${id}">View opens and clicks</button>` : ""}
      </div>` : "";
    const metrics = `<div class="cmp-detail-grid">
      <div class="cmp-msg"><small>Name</small><br><strong>${esc(c.name)}</strong></div>
      <div class="cmp-msg"><small>Entity</small><br><strong>${esc(entityNames[c.entity] || c.entity)}</strong></div>
      <div class="cmp-msg"><small>Type / status</small><br><strong id="cmp-m-typestatus">${esc(typeNames[c.campaign_type] || c.campaign_type)} · ${esc(statusNames[c.status] || c.status)}</strong></div>
      <div class="cmp-msg"><small>Audience / sent</small><br><strong id="cmp-m-audsent">${Number(c.audience_count || 0)} / ${Number(c.sent_count || 0)}</strong></div>
      <div class="cmp-msg"><small>Dates</small><br><strong>${esc(c.start_date || "—")} → ${esc(c.end_date || "—")}</strong></div>
      <div class="cmp-msg"><small>Budget</small><br><strong>${c.budget == null ? "—" : `₹${Number(c.budget).toLocaleString("en-IN")}`}</strong></div>
    </div>`;
    const formEditor = `
      <h4 style="margin-top:1rem">Response form</h4>
      <p class="muted" style="font-size:.76rem">Customise the form contacts fill when they click <code>&#123;&#123; form_link &#125;&#125;</code> in your email. Changes apply immediately — no need to re-send.</p>
      <div id="cmp-form-editor-${id}">
        <button class="cmp-btn" data-cmp="edit-form" data-id="${id}">Edit form fields</button>
      </div>`;
    box.innerHTML = `<div style="display:flex;align-items:center;gap:.7rem;flex-wrap:wrap"><h3 style="margin:0;flex:1">${esc(c.name)}</h3><span id="cmp-m-statusbadge" class="cmp-status status-${esc(c.status)}">${esc(statusNames[c.status] || c.status)}</span><button class="cmp-btn" data-cmp="close-detail">Close</button></div>
      ${metrics}<div id="cmp-detail-message-${id}" class="cmp-msg" style="display:none"></div>${workflow}${delivery}${formEditor}${tracking}
      <div id="cmp-output-${id}" class="cmp-msg" style="display:none;max-height:20rem;overflow:auto"></div>`;
    box.style.display = "block";
    // Remember what status the controls were built for, so the silent poll
    // knows when a full re-render is needed (controls change) vs. a cheap
    // in-place number patch (see patchDetail).
    box.dataset.renderedStatus = c.status;
    if (opts.scroll !== false) box.scrollIntoView({ behavior: "smooth", block: "nearest" });
    syncApproveButton(id);
  }

  function openForm(c = null) {
    state.editing = c;
    $("cmp-form-title").textContent = c ? "Edit draft campaign" : "New Campaign";
    $("cmp-form-save").textContent = c ? "Save changes" : "Create campaign";
    $("cmp-f-name").value = c?.name || "";
    $("cmp-f-type").value = c?.campaign_type || "dealer_promo";
    $("cmp-f-entity").value = c?.entity || $("cmp-entity-filter").value;
    $("cmp-f-channel").value = c?.default_channel || "auto";
    $("cmp-f-start").value = c?.start_date || "";
    $("cmp-f-end").value = c?.end_date || "";
    $("cmp-f-budget").value = c?.budget ?? "";
    $("cmp-f-template").value = c?.message_template_id || "";
    $("cmp-f-skus").value = c?.target_skus?.join(", ") || "";
    $("cmp-form-message").style.display = "none";
    openModal("#cmp-wizard");
    $("cmp-f-name").focus();
  }

  async function saveForm() {
    const save = $("cmp-form-save");
    const name = $("cmp-f-name").value.trim();
    if (!name) { flash("Campaign name is required.", true); $("cmp-f-name").focus(); return; }
    const start = $("cmp-f-start").value;
    const end = $("cmp-f-end").value;
    if (start && end && end < start) { flash("End date must be on or after start date.", true); return; }
    const rawBudget = $("cmp-f-budget").value;
    const body = {
      name,
      campaign_type: $("cmp-f-type").value,
      entity: $("cmp-f-entity").value,
      default_channel: $("cmp-f-channel").value,
      start_date: start || null,
      end_date: end || null,
      budget: rawBudget === "" ? null : Number(rawBudget),
      message_template_id: $("cmp-f-template").value || null,
      target_skus: $("cmp-f-skus").value.split(",").map((s) => s.trim()).filter(Boolean),
    };
    save.disabled = true;
    try {
      if (state.editing) {
        await request(`/campaigns/${encodeURIComponent(state.editing.campaign_id)}`, {
          method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        });
      } else {
        await request("/campaigns", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      }
      closeOpenModalStack();
      await loadCampaigns(false);
    } catch (error) { flash(error.message, true); }
    finally { save.disabled = false; }
  }

  function detailMessage(id, message, isError = false) {
    const el = $(`cmp-detail-message-${id}`);
    if (!el) return;
    el.textContent = message;
    el.style.display = "block";
    el.style.color = isError ? "#f87171" : "var(--text)";
  }

  // Render a contact's email as a mailto: link and phone as a tel: link so
  // they're one click to mail/call. tel: needs a + and digits only.
  function contactLinks(c) {
    const parts = [];
    if (c.email) parts.push(`<a href="mailto:${esc(c.email)}" style="color:var(--accent)">${esc(c.email)}</a>`);
    if (c.msisdn) {
      const dial = "+" + String(c.msisdn).replace(/[^\d]/g, "");
      parts.push(`<a href="tel:${esc(dial)}" style="color:var(--accent)" title="Call ${esc(dial)}">📞 ${esc(dial)}</a>`);
    }
    return parts.length ? parts.join("<br>") : esc(c.contact_ref || "—");
  }

  function renderContacts(result) {
    if (!result.contacts?.length) return `<p class="muted">No recipients in this view (${Number(result.total || 0)} total).</p>`;
    const rows = result.contacts.map((c) => `<tr>
      <td>${esc(c.name || "—")}</td><td>${contactLinks(c)}</td>
      <td>${esc(c.sent_at ? new Date(c.sent_at).toLocaleString() : "Pending")}</td>
      <td>${c.failure_reason ? `<span style="color:#f87171">${esc(c.failure_reason)}</span>` : c.sent_at ? "Sent" : "Pending"}${c.bounced && !c.failure_reason ? " · bounced" : ""}</td>
    </tr>`).join("");
    return `<p class="muted">Showing ${result.contacts.length} of ${Number(result.total || 0)} recipients. “Sent” means the mail server accepted the message; check opens/clicks or your mail provider for inbox delivery/bounces.</p>
      <div style="overflow:auto"><table style="width:100%;border-collapse:collapse"><thead><tr><th align="left">Name</th><th align="left">Email / phone</th><th align="left">Sent at (local time)</th><th align="left">Status</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  // ── Friendly result summary (replaces raw JSON dumps) ───────────────────────
  function renderSummary(action, r) {
    const kv = (label, val, color) =>
      `<div style="padding:.5rem .7rem;border:1px solid var(--panel-border);border-radius:8px;background:rgba(255,255,255,.03)">
        <small style="color:var(--muted);font-size:.66rem;text-transform:uppercase;letter-spacing:.04em">${esc(label)}</small>
        <b style="display:block;margin-top:.1rem;font-size:1.05rem${color ? `;color:${color}` : ""}">${esc(val)}</b></div>`;
    const grid = (cells) => `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:.5rem">${cells.join("")}</div>`;
    if (action === "send") {
      const parts = [kv("Sent now", num(r.sent_total), "var(--ok)")];
      if (r.deferred_email_trickle_cap) parts.push(kv("Queued (trickle)", num(r.deferred_email_trickle_cap), "var(--warn)"));
      if (r.deferred_email_outside_window) parts.push(kv("Waiting (send window)", num(r.deferred_email_outside_window), "var(--warn)"));
      if (r.remaining_unsent) parts.push(kv("Remaining", num(r.remaining_unsent)));
      if (r.failed) parts.push(kv("Failed", num(r.failed), "#f87171"));
      if (r.suppressed_optout) parts.push(kv("Opted out", num(r.suppressed_optout)));
      if (r.suppressed_bounce) parts.push(kv("Bounced (skipped)", num(r.suppressed_bounce)));
      return grid(parts) + `<p class="muted" style="font-size:.74rem;margin:.5rem 0 0">${r.remaining_unsent
        ? "Remaining recipients trickle out automatically every 5 min inside the 09:00–20:00 IST window."
        : "All eligible recipients have been sent."}</p>`;
    }
    if (action === "audience") {
      return grid([kv("Audience total", num(r.total_audience ?? r.total)), kv("Newly added", num(r.added)),
        kv("Duplicates skipped", num(r.skipped_duplicate ?? r.skipped))]);
    }
    if (action === "schedule") {
      return `Scheduled for <b>${esc(new Date(r.scheduled_at).toLocaleString())}</b> via ${esc(channelNames[r.channel] || r.channel)}.`;
    }
    if (action === "analytics" || action === "engagement") {
      const s = r.summary || r;
      const cells = Object.entries(s).filter(([, v]) => typeof v !== "object")
        .map(([k, v]) => kv(k.replace(/_/g, " "), Array.isArray(v) ? v.length : v));
      return cells.length ? grid(cells) : `<pre style="overflow:auto;font-size:.74rem;margin:0">${esc(JSON.stringify(r, null, 2))}</pre>`;
    }
    return `<pre style="overflow:auto;font-size:.74rem;margin:0;max-height:18rem">${esc(JSON.stringify(r, null, 2))}</pre>`;
  }

  // ── Zoho-style column mapping before import ──────────────────────────────────
  const STD_TARGETS = ["name", "first_name", "salutation", "email", "phone", "company", "ignore"];

  function ensureMapModal() {
    let ov = $("cmp-map-overlay");
    if (ov) return ov;
    ov = document.createElement("div");
    ov.id = "cmp-map-overlay";
    ov.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:10001;display:none;align-items:center;justify-content:center;padding:clamp(12px,2vw,24px);overflow-y:auto";
    ov.innerHTML = `<div style="width:min(720px,calc(100vw - 2rem));max-width:calc(100vw - 2rem);max-height:min(88vh,820px);overflow:auto;background:var(--bg-2);border:1px solid var(--panel-border);border-radius:18px;padding:clamp(1rem,2.2vw,1.75rem);box-shadow:var(--shadow)">
      <div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.25rem">
        <h3 style="margin:0;flex:1">Map spreadsheet columns</h3>
        <button class="cmp-btn" id="cmp-map-x">✕</button>
      </div>
      <div id="cmp-map-sheet-bar" style="display:none;align-items:center;gap:.6rem;margin-bottom:.6rem;padding:.5rem .7rem;background:rgba(255,255,255,.04);border-radius:8px">
        <span style="font-size:.8rem;color:var(--muted)">Sheet:</span>
        <select id="cmp-map-sheet-sel" style="background:rgba(255,255,255,.06);border:1px solid var(--panel-border);border-radius:6px;padding:.3rem .5rem;color:var(--text);font-size:.82rem;flex:1"></select>
        <span id="cmp-map-sheet-loading" style="font-size:.75rem;color:var(--muted);display:none">Loading…</span>
      </div>
      <p class="muted" id="cmp-map-sub" style="font-size:.8rem;margin:0 0 .8rem"></p>
      <div id="cmp-map-rows"></div>
      <div id="cmp-map-err" class="cmp-msg" style="display:none;color:#f87171"></div>
      <div style="display:flex;justify-content:flex-end;gap:.5rem;margin-top:1rem">
        <button class="cmp-btn" id="cmp-map-cancel">Cancel</button>
        <button class="cmp-btn primary" id="cmp-map-confirm">Import contacts</button>
      </div></div>`;
    document.body.appendChild(ov);
    const close = () => ov.style.display = "none";
    ov.querySelector("#cmp-map-x").onclick = close;
    ov.querySelector("#cmp-map-cancel").onclick = close;
    ov.onclick = (e) => { if (e.target === ov) close(); };
    ov.querySelector("#cmp-map-confirm").onclick = confirmMapping;
    return ov;
  }

  function _renderMappingColumns(preview) {
    const opts = preview.target_options;
    $("cmp-map-rows").innerHTML = preview.columns.map((col) => {
      const isStd = STD_TARGETS.includes(col.guess);
      const selVal = isStd ? col.guess : "__custom__";
      const customName = isStd ? col.normalized : col.guess;
      const optionsHtml = opts.map((o) =>
        `<option value="${esc(o.value)}" ${o.value === selVal ? "selected" : ""}>${esc(o.label)}</option>`).join("");
      const samples = col.samples.length ? esc(col.samples.slice(0, 3).join("   ·   ")) : "—";
      return `<div class="cmp-map-row" data-idx="${col.index}" style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;align-items:center;padding:.7rem 0;border-bottom:1px solid rgba(255,255,255,.06)">
        <div style="min-width:0">
          <div style="font-weight:600;font-size:.86rem">${esc(col.header)}${!isStd ? '<span style="font-size:.64rem;padding:.1rem .4rem;border-radius:999px;margin-left:.4rem;background:rgba(119,209,255,.14);color:var(--accent)">custom</span>' : ""}</div>
          <div class="muted" style="font-size:.72rem;margin-top:.2rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">e.g. ${samples}</div>
        </div>
        <div>
          <select class="cmp-map-sel" style="width:100%;background:rgba(255,255,255,.05);border:1px solid var(--panel-border);border-radius:7px;padding:.42rem .55rem;color:var(--text);font-size:.82rem">${optionsHtml}</select>
          <input class="cmp-map-custom" placeholder="variable name (e.g. title)" value="${esc(customName)}"
            style="width:100%;margin-top:.4rem;background:rgba(255,255,255,.05);border:1px solid var(--panel-border);border-radius:7px;padding:.42rem .55rem;color:var(--text);font-size:.82rem;display:${selVal === "__custom__" ? "block" : "none"}">
        </div></div>`;
    }).join("");
    $("cmp-map-rows").querySelectorAll(".cmp-map-row").forEach((row) => {
      const sel = row.querySelector(".cmp-map-sel");
      const cust = row.querySelector(".cmp-map-custom");
      sel.onchange = () => { cust.style.display = sel.value === "__custom__" ? "block" : "none"; };
    });
  }

  function openMappingModal(preview) {
    ensureMapModal();
    // Sheet picker — only shown for multi-sheet workbooks
    const sheetBar = $("cmp-map-sheet-bar");
    const sheetSel = $("cmp-map-sheet-sel");
    if (preview.sheets && preview.sheets.length > 1) {
      sheetSel.innerHTML = preview.sheets.map((s) => {
        const label = `${s.name}  (${s.rows} rows)`;
        return `<option value="${esc(s.name)}" ${s.name === preview.sheet ? "selected" : ""}>${esc(label)}</option>`;
      }).join("");
      sheetBar.style.display = "flex";
      sheetSel.onchange = async () => {
        const loading = $("cmp-map-sheet-loading");
        sheetSel.disabled = true; loading.style.display = "";
        try {
          const form = new FormData();
          form.append("file", state.upload.file);
          form.append("sheet", sheetSel.value);
          const p = await request(`/campaigns/${encodeURIComponent(state.upload.id)}/contacts/preview`, { method: "POST", body: form });
          state.upload.sheet = p.sheet;
          $("cmp-map-sub").textContent = _mapSubText(p);
          _renderMappingColumns(p);
        } catch (e) {
          $("cmp-map-err").textContent = e.message; $("cmp-map-err").style.display = "block";
        } finally {
          sheetSel.disabled = false; loading.style.display = "none";
        }
      };
    } else {
      sheetBar.style.display = "none";
    }
    state.upload.sheet = preview.sheet || null;
    $("cmp-map-sub").textContent = _mapSubText(preview);
    _renderMappingColumns(preview);
    $("cmp-map-err").style.display = "none";
    $("cmp-map-overlay").style.display = "flex";
  }

  function _mapSubText(preview) {
    const sheetPart = preview.sheet ? ` · Sheet: ${preview.sheet}` : "";
    return `${preview.filename}${sheetPart} — ${preview.total_rows} rows. We guessed each column; adjust any that are wrong. Map at least one Email or Phone.`;
  }

  async function confirmMapping() {
    if (!state.upload) return;
    const mapping = {};
    let hasReach = false;
    $("cmp-map-rows").querySelectorAll(".cmp-map-row").forEach((row) => {
      const sel = row.querySelector(".cmp-map-sel").value;
      let target = sel;
      if (sel === "__custom__") {
        const name = row.querySelector(".cmp-map-custom").value.trim().toLowerCase().replace(/\s+/g, "_");
        if (!name) return;
        target = name;
      }
      if (target === "ignore") return;
      if (target === "email" || target === "phone") hasReach = true;
      mapping[row.dataset.idx] = target;
    });
    const errEl = $("cmp-map-err");
    if (!hasReach) {
      errEl.textContent = "Map at least one column to Email or Phone so contacts can be reached.";
      errEl.style.display = "block";
      return;
    }
    const btn = $("cmp-map-confirm");
    btn.disabled = true; btn.textContent = "Importing…";
    try {
      const form = new FormData();
      form.append("file", state.upload.file);
      form.append("mapping", JSON.stringify(mapping));
      if (state.upload.sheet) form.append("sheet", state.upload.sheet);
      const result = await request(`/campaigns/${encodeURIComponent(state.upload.id)}/contacts/upload`, { method: "POST", body: form });
      $("cmp-map-overlay").style.display = "none";
      await loadCampaigns(true);
      const updated = state.campaigns.find((c) => c.campaign_id === state.upload.id);
      if (updated) renderDetail(updated);
      detailMessage(state.upload.id, `Imported ${num(result.added)} contacts · ${num(result.skipped_duplicate)} duplicates skipped · ${num(result.skipped_no_contact)} without email/phone. Audience total: ${num(result.total_audience)}.`);
    } catch (e) {
      errEl.textContent = e.message; errEl.style.display = "block";
    } finally {
      btn.disabled = false; btn.textContent = "Import contacts";
    }
  }

  async function startUpload(id) {
    const file = $(`cmp-upload-file-${id}`)?.files[0];
    if (!file) throw new Error("Choose an Excel or CSV contact file first.");
    state.upload = { id, file, sheet: null };
    detailMessage(id, "Reading file…");
    const form = new FormData();
    form.append("file", file);
    const preview = await request(`/campaigns/${encodeURIComponent(id)}/contacts/preview`, { method: "POST", body: form });
    const sheetInfo = preview.sheet ? ` (sheet: ${preview.sheet})` : "";
    detailMessage(id, `Detected ${preview.columns.length} columns, ${preview.total_rows} rows${sheetInfo} — confirm the column mapping.`);
    openMappingModal(preview);
  }

  // ── Response form editor ─────────────────────────────────────────────────────

  const _FE_TYPES = [
    { value: "text",     label: "Short text" },
    { value: "textarea", label: "Long text"  },
    { value: "email",    label: "Email"      },
    { value: "tel",      label: "Phone/Tel"  },
    { value: "number",   label: "Number"     },
    { value: "url",      label: "URL"        },
  ];

  function _feTypeOpts(selected) {
    return _FE_TYPES.map((t) =>
      `<option value="${t.value}"${t.value === selected ? " selected" : ""}>${t.label}</option>`
    ).join("");
  }

  function _feRowHtml(f) {
    const enabled  = f.enabled !== false ? "checked" : "";
    const required = f.required ? "checked" : "";
    return `<div class="cmp-fe-row" data-name="${esc(f.name)}"
      style="display:grid;grid-template-columns:auto auto 1fr auto auto;align-items:center;gap:.45rem;
             padding:.48rem .2rem;border-bottom:1px solid rgba(255,255,255,.06)">
      <div style="display:flex;flex-direction:column;gap:1px">
        <button type="button" class="cmp-btn cmp-fe-up"
          style="padding:.06rem .3rem;font-size:.66rem;line-height:1.3;min-width:0">↑</button>
        <button type="button" class="cmp-btn cmp-fe-dn"
          style="padding:.06rem .3rem;font-size:.66rem;line-height:1.3;min-width:0">↓</button>
      </div>
      <input type="checkbox" class="cmp-fe-enabled" title="Show on form" ${enabled}
        style="width:15px;height:15px;cursor:pointer;flex-shrink:0">
      <div>
        <input class="cmp-fe-label" type="text" value="${esc(f.label || f.name)}" placeholder="Field label"
          style="width:100%;background:rgba(255,255,255,.05);border:1px solid var(--panel-border);
                 border-radius:6px;padding:.28rem .46rem;color:var(--text);font-size:.82rem;margin-bottom:2px">
        <select class="cmp-fe-type"
          style="background:var(--bg-2);border:1px solid var(--panel-border);border-radius:5px;
                 padding:.16rem .36rem;color:var(--text);font-size:.72rem;max-width:120px">
          ${_feTypeOpts(f.type || "text")}
        </select>
      </div>
      <label style="font-size:.72rem;display:flex;align-items:center;gap:.22rem;white-space:nowrap;cursor:pointer">
        <input type="checkbox" class="cmp-fe-req" ${required} style="width:13px;height:13px"> Req.
      </label>
      <button type="button" class="cmp-btn cmp-fe-del" title="Delete this field"
        style="padding:.18rem .4rem;font-size:.76rem;color:#f87171;border-color:rgba(248,113,113,.3);flex-shrink:0">✕</button>
    </div>`;
  }

  function _feRefreshButtons() {
    const rows = [...$("cmp-fe-fields").querySelectorAll(".cmp-fe-row")];
    rows.forEach((row, idx) => {
      const up = row.querySelector(".cmp-fe-up");
      const dn = row.querySelector(".cmp-fe-dn");
      if (up) up.disabled = idx === 0;
      if (dn) dn.disabled = idx === rows.length - 1;
    });
  }

  function _feAddRow() {
    const tmp = "custom_" + Date.now();
    const wrap = document.createElement("div");
    wrap.innerHTML = _feRowHtml({ name: tmp, label: "", type: "text", enabled: true, required: false });
    const rowEl = wrap.firstElementChild;
    rowEl.dataset.isNew = "1";
    $("cmp-fe-fields").appendChild(rowEl);
    rowEl.querySelector(".cmp-fe-label").focus();
    _feRefreshButtons();
  }

  function _feSlugify(label) {
    const s = label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/, "");
    return s || "field_" + Date.now();
  }

  function _ensureFormEditorModal() {
    let ov = $("cmp-fe-overlay");
    if (ov) return ov;
    ov = document.createElement("div");
    ov.id = "cmp-fe-overlay";
    ov.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:10002;display:none;align-items:center;justify-content:center;padding:clamp(12px,2vw,24px);overflow-y:auto";
    ov.innerHTML = `<div style="width:min(640px,calc(100vw - 2rem));max-height:min(92vh,900px);overflow:auto;background:var(--bg-2);border:1px solid var(--panel-border);border-radius:18px;padding:clamp(1rem,2.2vw,1.75rem);box-shadow:var(--shadow)">
      <div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.25rem">
        <h3 style="margin:0;flex:1">Edit response form</h3>
        <button class="cmp-btn" id="cmp-fe-x">✕</button>
      </div>
      <p class="muted" style="font-size:.78rem;margin-bottom:1rem">Contacts see this form when they click your email link.
        Add fields, rename, reorder with ↑↓, toggle on/off, or delete any field. <strong>No code needed.</strong></p>

      <div class="field-group" style="margin-bottom:.85rem">
        <label style="font-size:.82rem;font-weight:600;display:block;margin-bottom:.28rem">Intro text</label>
        <input id="cmp-fe-subtitle" type="text" placeholder="Please share your details and we'll get back to you shortly."
          style="width:100%;background:rgba(255,255,255,.05);border:1px solid var(--panel-border);border-radius:7px;padding:.42rem .55rem;color:var(--text);font-size:.84rem">
      </div>

      <div class="field-group" style="margin-bottom:1rem">
        <label style="font-size:.82rem;font-weight:600;display:block;margin-bottom:.28rem">Submit button text</label>
        <input id="cmp-fe-submit" type="text" placeholder="Send My Details →"
          style="width:100%;background:rgba(255,255,255,.05);border:1px solid var(--panel-border);border-radius:7px;padding:.42rem .55rem;color:var(--text);font-size:.84rem">
      </div>

      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:.4rem">
        <label style="font-size:.82rem;font-weight:600">Form fields
          <span class="muted" style="font-size:.72rem;font-weight:400"> — ↑↓ reorder · ✕ delete</span>
        </label>
        <button type="button" class="cmp-btn primary" id="cmp-fe-add"
          style="font-size:.78rem;padding:.28rem .65rem">+ Add field</button>
      </div>
      <div id="cmp-fe-fields" style="margin-bottom:.75rem;min-height:2rem"></div>

      <div id="cmp-fe-err" style="display:none;color:#f87171;font-size:.82rem;background:rgba(248,113,113,.08);
           border:1px solid rgba(248,113,113,.25);border-radius:6px;padding:.4rem .6rem;margin-bottom:.5rem"></div>
      <div style="display:flex;justify-content:flex-end;gap:.5rem;margin-top:1rem">
        <button class="cmp-btn" id="cmp-fe-cancel">Cancel</button>
        <button class="cmp-btn primary" id="cmp-fe-save">Save form</button>
      </div>
    </div>`;
    document.body.appendChild(ov);
    const close = () => { ov.style.display = "none"; };
    $("cmp-fe-x").onclick = close;
    $("cmp-fe-cancel").onclick = close;
    ov.onclick = (e) => { if (e.target === ov) close(); };
    $("cmp-fe-add").onclick = _feAddRow;

    $("cmp-fe-fields").addEventListener("click", (e) => {
      const btn = e.target.closest("button");
      if (!btn) return;
      const row = btn.closest(".cmp-fe-row");
      if (!row) return;
      if (btn.classList.contains("cmp-fe-del")) {
        row.remove(); _feRefreshButtons(); return;
      }
      const container = $("cmp-fe-fields");
      const rows = [...container.querySelectorAll(".cmp-fe-row")];
      const idx  = rows.indexOf(row);
      if (btn.classList.contains("cmp-fe-up") && idx > 0) {
        container.insertBefore(row, rows[idx - 1]);
        _feRefreshButtons();
      } else if (btn.classList.contains("cmp-fe-dn") && idx < rows.length - 1) {
        rows[idx + 1].insertAdjacentElement("afterend", row);
        _feRefreshButtons();
      }
    });
    return ov;
  }

  async function openFormEditor(id) {
    _ensureFormEditorModal();
    let cfg;
    try {
      cfg = await request(`/forms/${encodeURIComponent(id)}/config`);
    } catch (e) {
      detailMessage(id, "Could not load form config: " + e.message, true);
      return;
    }
    $("cmp-fe-subtitle").value = cfg.subtitle || "";
    $("cmp-fe-submit").value   = cfg.submit_label || "";

    $("cmp-fe-fields").innerHTML = (cfg.fields || []).map((f) => _feRowHtml(f)).join("");
    _feRefreshButtons();

    $("cmp-fe-save").onclick = () => saveFormConfig(id);
    $("cmp-fe-err").style.display = "none";
    $("cmp-fe-overlay").style.display = "flex";
  }

  async function saveFormConfig(id) {
    const btn = $("cmp-fe-save");
    btn.disabled = true; btn.textContent = "Saving…";
    try {
      const subtitle    = $("cmp-fe-subtitle").value.trim();
      const submitLabel = $("cmp-fe-submit").value.trim();
      const fields    = [];
      const usedNames = new Set();

      $("cmp-fe-fields").querySelectorAll(".cmp-fe-row").forEach((row) => {
        let name = row.dataset.name || "";
        const labelEl  = row.querySelector(".cmp-fe-label");
        const labelVal = labelEl ? labelEl.value.trim() : "";
        const typeEl   = row.querySelector(".cmp-fe-type");
        if (row.dataset.isNew === "1" || !name || name.startsWith("custom_")) {
          name = _feSlugify(labelVal);
          let base = name, n = 1;
          while (usedNames.has(name)) name = `${base}_${n++}`;
        }
        usedNames.add(name);
        fields.push({
          name,
          label:    labelVal || name.replace(/_/g, " "),
          type:     typeEl ? typeEl.value : "text",
          enabled:  row.querySelector(".cmp-fe-enabled").checked,
          required: row.querySelector(".cmp-fe-req").checked,
        });
      });

      await request(`/forms/${encodeURIComponent(id)}/config`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subtitle, submit_label: submitLabel, fields }),
      });
      $("cmp-fe-overlay").style.display = "none";
      detailMessage(id, "Form saved. Changes are live immediately.");
    } catch (e) {
      $("cmp-fe-err").textContent = e.message;
      $("cmp-fe-err").style.display = "block";
    } finally {
      btn.disabled = false;
      btn.textContent = "Save form";
    }
  }

  async function doAction(action, id, button) {
    if (action === "close-detail") { $("cmp-detail").style.display = "none"; return; }
    if (action === "close-deliverability") {
      const p = $("cmp-deliverability-panel");
      if (p) { p.style.display = "none"; p.innerHTML = ""; }
      return;
    }
    const campaign = state.campaigns.find((c) => c.campaign_id === id);
    if (!campaign) return;
    if (action === "view") { renderDetail(campaign); return; }
    if (action === "edit") { openForm(campaign); return; }
    if (action === "delete") {
      if (!window.confirm(`Delete draft “${campaign.name}”? This also removes its unsent audience rows.`)) return;
      try { await request(`/campaigns/${encodeURIComponent(id)}`, { method: "DELETE" }); await loadCampaigns(false); }
      catch (error) { window.alert(error.message); }
      return;
    }
    if (action === "new-template") { openTemplateComposer(); return; }
    if (action === "refresh-templates") {
      await loadTemplates();
      detailMessage(id, `Template list refreshed (${state.templates.length} templates).`);
      return;
    }
    if (action === "approve-template") { await approveTemplate(id); return; }
    if (action === "edit-form") { await openFormEditor(id); return; }
    if (action === "test-send") {
      const email = ($(`cmp-test-email-${id}`)?.value || "").trim();
      const template = $(`cmp-template-${id}`)?.value || "";
      if (!email || !email.includes("@")) { detailMessage(id, "Enter a valid email address to send the test to.", true); return; }
      if (!template) { detailMessage(id, "Choose a message template first.", true); return; }
      if (button) button.disabled = true;
      detailMessage(id, `Sending test email to ${email}…`);
      try {
        const r = await request(`/campaigns/${encodeURIComponent(id)}/test-send`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ to_email: email, template_id: template }),
        });
        detailMessage(id, `✓ Test email sent to ${r.to_email} — subject “${r.subject}”. Check your inbox (and spam).`);
      } catch (error) { detailMessage(id, error.message, true); }
      finally { if (button) button.disabled = false; }
      return;
    }
    if (action === "upload") {
      if (button) button.disabled = true;
      try { await startUpload(id); }
      catch (error) { detailMessage(id, error.message, true); }
      finally { if (button) button.disabled = false; }
      return;
    }
    if (action === "toggle-preview") {
      const box = $(`cmp-tpl-preview-${id}`);
      if (box) { box.dataset.open = button.checked ? "1" : ""; syncPreview(id); }
      return;
    }

    const message = $(`cmp-detail-message-${id}`);
    const output = $(`cmp-output-${id}`);
    if (button) button.disabled = true;
    if (message) { message.style.display = "block"; message.textContent = "Working…"; }
    try {
      let result;
      if (action === "contacts") {
        result = await request(`/campaigns/${encodeURIComponent(id)}/contacts?status=${encodeURIComponent(button?.dataset.status || "all")}&limit=200`);
        if (output) { output.innerHTML = renderContacts(result); output.style.display = "block"; }
        if (message) { message.textContent = `Recipient status loaded (${result.total} records).`; message.style.color = "var(--text)"; }
        return;
      } else if (action === "audience") {
        const source = $(`cmp-source-${id}`).value;
        const body = { source };
        // Sources that need an extra input before they can build.
        if (source === "scribd") {
          const url = window.prompt("Paste the Scribd / document download URL (PDF, Excel, or CSV):");
          if (!url) return;
          body.document_url = url.trim();
        } else if (source === "google_drive") {
          const link = window.prompt("Paste a Google Drive file/folder link or id (leave blank to use the default folder):");
          if (link && link.trim()) {
            const v = link.trim();
            if (/\/folders\//.test(v)) body.folder_id = (v.match(/\/folders\/([a-zA-Z0-9_-]+)/) || [])[1] || v;
            else body.drive_link = v;
          }
        } else if (source === "justdial") {
          const kw = window.prompt("JustDial search — keywords/category (e.g. 'electronics dealers'):");
          if (kw && kw.trim()) body.keywords = kw.trim();
          const city = window.prompt("JustDial search — city (optional):");
          if (city && city.trim()) body.location = city.trim();
        }
        result = await request(`/campaigns/${encodeURIComponent(id)}/audience`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
        });
      } else if (action === "send" || action === "schedule") {
        const template = $(`cmp-template-${id}`).value;
        const channel = $(`cmp-channel-${id}`).value;
        if (!template) throw new Error("Choose a message template before sending or scheduling.");
        if (action === "send" && !window.confirm(`Send “${campaign.name}” to ${campaign.audience_count} contacts using ${channelNames[channel] || channel}?`)) return;
        if (action === "schedule") {
          const localTime = $(`cmp-schedule-${id}`).value;
          if (!localTime) throw new Error("Choose the date and time to schedule this campaign.");
          result = await request(`/campaigns/${encodeURIComponent(id)}/schedule`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ template_id: template, scheduled_at: new Date(localTime).toISOString(), channel }),
          });
        } else {
          result = await request(`/campaigns/${encodeURIComponent(id)}/send`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ template_id: template, channel }),
          });
        }
      } else if (action === "analytics") {
        result = await request(`/campaigns/${encodeURIComponent(id)}/analytics`);
      } else if (action === "engagement") {
        result = await request(`/campaigns/${encodeURIComponent(id)}/engagement`);
      }
      if (message) { message.textContent = action === "audience" ? `Audience ready: ${result.total_audience ?? result.contacts_fetched ?? 0} contacts.` : "Done."; message.style.color = "var(--text)"; }
      await loadCampaigns(true);
      const updated = state.campaigns.find((c) => c.campaign_id === id);
      if (updated) renderDetail(updated);
      const updatedOutput = $(`cmp-output-${id}`);
      if (updatedOutput && result) { updatedOutput.innerHTML = renderSummary(action, result); updatedOutput.style.display = "block"; }
    } catch (error) { detailMessage(id, error.message, true); }
    finally { if (button) button.disabled = false; }
  }

  root.addEventListener("click", (event) => {
    const pageButton = event.target.closest("[data-cmp-page]");
    if (pageButton && !pageButton.disabled) {
      state.page = Number(pageButton.dataset.cmpPage);
      loadCampaigns();
      return;
    }
    const button = event.target.closest("[data-cmp]");
    if (button) { doAction(button.dataset.cmp, button.dataset.id, button); return; }
  });
  $("cmp-new").addEventListener("click", () => openForm());
  $("cmp-form-save").addEventListener("click", saveForm);
  $("cmp-form-cancel").addEventListener("click", () => $("cmp-wizard").classList.remove("open"));
  $("cmp-wizard-close").addEventListener("click", () => $("cmp-wizard").classList.remove("open"));
  $("cmp-wizard").addEventListener("click", (event) => {
    if (event.target === event.currentTarget) event.currentTarget.classList.remove("open");
  });
  $("cmp-reload").addEventListener("click", () => loadCampaigns());
  $("cmp-deliverability")?.addEventListener("click", checkDeliverability);
  $("cmp-entity-filter").addEventListener("change", () => {
    state.page = 1;
    loadCampaigns(false);
  });
  // Approve-button visibility follows the selected template everywhere.
  root.addEventListener("change", (event) => {
    const sel = event.target.closest("select[id^='cmp-template-']");
    if (sel) syncApproveButton(sel.id.replace("cmp-template-", ""));
  });
  loadTemplates();
  loadCampaigns(false);
  startPolling();
})();
