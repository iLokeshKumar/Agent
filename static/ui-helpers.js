/**
 * Minimal helpers for the FastHTML control-room UI.
 * Toast notifications + modal open/close helpers.
 * HTMX handles all data fetching — this file is intentionally small.
 */

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------
function toast(title, detail, kind = "info") {
  const host = document.getElementById("toast-host");
  if (!host) return;
  const node = document.createElement("div");
  node.className = "toast";
  node.setAttribute("data-kind", kind);
  node.innerHTML = `<strong>${escHtml(title)}</strong><small>${escHtml(detail)}</small>`;
  host.appendChild(node);
  setTimeout(() => { node.style.opacity = "0"; node.style.transform = "translateY(8px)"; }, 2600);
  setTimeout(() => node.remove(), 3200);
}

function escHtml(v) {
  return String(v ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

// ---------------------------------------------------------------------------
// HTMX event hooks — show toast on errors
// ---------------------------------------------------------------------------
document.addEventListener("htmx:responseError", (evt) => {
  toast("Request failed", evt.detail.xhr?.statusText || "Unknown error", "err");
});

document.addEventListener("htmx:afterRequest", () => {
  const ts = document.getElementById("last-updated");
  if (ts) ts.textContent = "Last refreshed: " + new Date().toLocaleTimeString();
});

function ensureModalRoot(selector) {
  const el = typeof selector === "string" ? document.querySelector(selector) : selector;
  if (!el) return null;
  if (el.parentElement !== document.body) document.body.appendChild(el);
  return el;
}

function closeOpenModalStack() {
  ["#cmp-wizard", "#tmpl-modal", "#tmpl-approve-modal", "#cmp-send-modal"].forEach((selector) => {
    const el = document.querySelector(selector);
    if (el) el.classList.remove("open");
  });
}

function openModal(selector) {
  const el = ensureModalRoot(selector);
  if (!el) return;
  closeOpenModalStack();
  el.classList.add("open");
}

document.addEventListener("DOMContentLoaded", () => {
  ["#cmp-wizard", "#tmpl-modal", "#tmpl-approve-modal", "#cmp-send-modal"].forEach((selector) => ensureModalRoot(selector));
});
