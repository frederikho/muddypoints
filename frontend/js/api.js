// Shared helpers: API client, session storage, error reporting, tiny DOM helpers.
const cfg = window.MUDDYPOINTS_CONFIG || {};
export const API_BASE = (cfg.apiBase || "").replace(/\/$/, "");
export const API_ORIGIN = API_BASE || window.location.origin;

const SESSION_KEY = "muddypoints.session";

function storageGet(key) { try { return JSON.parse(localStorage.getItem(key)); } catch { return null; } }
function storageSet(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } }
function storageDel(key) { try { localStorage.removeItem(key); } catch { /* ignore */ } }
export const storage = { get: storageGet, set: storageSet, del: storageDel };

export const session = {
  get() { return storageGet(SESSION_KEY); },          // {token, user} or null
  get token() { return this.get()?.token || null; },
  get user() { return this.get()?.user || null; },
  set(token, user) { storageSet(SESSION_KEY, { token, user }); },
  clear() { storageDel(SESSION_KEY); },
};

export class ApiError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}

export async function api(method, path, body, { anonToken } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (session.token) headers["Authorization"] = `Bearer ${session.token}`;
  if (anonToken) headers["X-Anon-Token"] = anonToken;
  let resp;
  try {
    resp = await fetch(API_BASE + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) {
    throw new ApiError(0, "network", "Cannot reach the server. Check your connection.");
  }
  const data = resp.status === 204 ? null : await resp.json().catch(() => null);
  if (!resp.ok) {
    if (resp.status === 401 && session.token && path !== "/api/auth/login") session.clear();
    const err = data?.error || {};
    throw new ApiError(resp.status, err.code || "error", err.message || `Request failed (${resp.status})`);
  }
  return data;
}

// --- error reporting: frontend errors end up in CloudWatch (logger "frontend") ---------------
let reported = 0;
export function reportError(message, context = "") {
  if (reported++ > 20) return; // don't flood
  try {
    fetch(API_BASE + "/api/client-log", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ level: "error", message: String(message).slice(0, 2000), context: String(context).slice(0, 4000), url: location.pathname + location.search }),
    }).catch(() => {});
  } catch { /* ignore */ }
}
window.addEventListener("error", (e) => reportError(e.message, `${e.filename}:${e.lineno} ${e.error?.stack || ""}`));
window.addEventListener("unhandledrejection", (e) => {
  if (e.reason instanceof ApiError && e.reason.status && e.reason.status < 500) return;
  reportError(e.reason?.message || String(e.reason), e.reason?.stack || "");
});

// --- board backgrounds (CSS classes .bg-<id>; ids must match boards.BACKGROUNDS in the backend) ---
export const BACKGROUNDS = [
  ["plain", "Plain"], ["dots", "Dots"], ["grid", "Graph paper"], ["lined", "Notebook"], ["cork", "Cork board"],
  ["linen", "Linen"], ["chalkboard", "Chalkboard"], ["sunset", "Sunset"], ["ocean", "Ocean"],
];

// Row of clickable texture swatches; calls onPick(id) when one is chosen.
export function backgroundPicker(current, onPick) {
  const row = el("div", { class: "swatches", title: "Board background" });
  const render = (active) => row.replaceChildren(...BACKGROUNDS.map(([id, label]) =>
    el("button", { type: "button", class: `bg-swatch bg-${id} ${id === active ? "active" : ""}`, title: label, "aria-label": `Background: ${label}`,
      onclick: async () => { render(id); try { await onPick(id); } catch (e) { render(active); showError(e); } } })));
  render(current || "plain");
  return row;
}

// --- DOM helpers -----------------------------------------------------------------------------
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "html") node.innerHTML = v; // only ever used with static strings
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
}

export function toast(message, kind = "info") {
  let host = document.getElementById("toasts");
  if (!host) { host = el("div", { id: "toasts" }); document.body.append(host); }
  const t = el("div", { class: `toast ${kind}` }, message);
  host.append(t);
  setTimeout(() => t.remove(), kind === "error" ? 6000 : 3000);
}

export function showError(e) {
  console.error(e);
  toast(e?.message || String(e), "error");
}

export function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function timeAgo(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

export async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied to clipboard"); }
  catch { prompt("Copy this:", text); }
}

// Run an async action with a button disabled while it runs; errors become toasts.
export async function busy(button, fn) {
  const prev = button?.disabled;
  if (button) button.disabled = true;
  try { return await fn(); } catch (e) { showError(e); } finally { if (button) button.disabled = prev; }
}
