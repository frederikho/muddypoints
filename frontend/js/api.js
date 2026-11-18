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

// --- abstract avatars: deterministic per user id, 2-3 colours of one palette, simple shapes, no faces ---
const AVATAR_PALETTES = [
  ["#3b82f6", "#bfdbfe", "#1e3a8a"], // blue
  ["#14b8a6", "#99f6e4", "#115e59"], // turquoise
  ["#f97316", "#fed7aa", "#9a3412"], // orange
  ["#8b5cf6", "#ddd6fe", "#4c1d95"], // purple
  ["#ec4899", "#fbcfe8", "#9d174d"], // pink
  ["#22c55e", "#bbf7d0", "#166534"], // green
  ["#eab308", "#fef08a", "#854d0e"], // yellow
  ["#64748b", "#cbd5e1", "#1e293b"], // slate
];
function hashString(str) {
  let h = 2166136261;
  for (const ch of String(str)) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619); }
  return h >>> 0;
}
export function avatar(seed, name = "", size = 28) {
  const pick = (salt, n) => hashString(`${salt}:${seed}`) % n; // independent "random" choices per property
  const [a, b, c] = AVATAR_PALETTES[pick("palette", AVATAR_PALETTES.length)];
  const n = (salt, lo, hi) => lo + pick(salt, hi - lo + 1);
  const shapes = [
    `<circle cx="${n("x", 8, 32)}" cy="${n("y", 10, 30)}" r="${n("r", 13, 21)}" fill="${b}"/>`,
    `<polygon points="0,40 40,0 40,40" fill="${b}"/><circle cx="${n("x", 8, 16)}" cy="${n("y", 8, 16)}" r="6" fill="${c}"/>`,
    `<rect x="-10" y="${n("y", 10, 18)}" width="60" height="9" fill="${b}"/><rect x="-10" y="${n("y2", 25, 31)}" width="60" height="5" fill="${c}"/>`,
    `<circle cx="0" cy="40" r="${n("r", 22, 30)}" fill="${b}"/><circle cx="40" cy="0" r="${n("r2", 11, 17)}" fill="${c}"/>`,
    `<circle cx="${n("x", 12, 18)}" cy="22" r="12" fill="${b}"/><circle cx="${n("x2", 24, 30)}" cy="${n("y", 14, 24)}" r="9" fill="${c}" fill-opacity=".85"/>`,
    `<path d="M0 ${n("y", 18, 26)} Q20 ${n("q", 4, 14)} 40 ${n("y2", 18, 26)} V40 H0Z" fill="${b}"/><circle cx="${n("x", 10, 30)}" cy="32" r="4" fill="${c}"/>`,
  ][pick("shape", 6)];
  const span = el("span", { class: "avatar", title: name, style: `width:${size}px;height:${size}px` });
  span.innerHTML = `<svg viewBox="0 0 40 40" width="${size}" height="${size}" aria-hidden="true"><rect width="40" height="40" fill="${a}"/>`
    + `<g transform="rotate(${pick("rot", 4) * 90} 20 20)">${shapes}</g></svg>`; // only computed numbers/colours, no user content
  return span;
}

// --- ⚙ popover menu: build(close) returns the menu's children; closes on outside click / Escape ---
let openMenu = null;
function closeMenu() {
  if (!openMenu) return;
  openMenu.querySelector(".popover").classList.add("hidden");
  openMenu.querySelector(".cog").setAttribute("aria-expanded", "false");
  openMenu = null;
}
document.addEventListener("click", (e) => { if (openMenu && !e.composedPath().includes(openMenu)) closeMenu(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });

export function cogMenu(build, title = "Settings") {
  const pop = el("div", { class: "popover hidden", role: "menu" });
  const btn = el("button", { class: "cog", type: "button", title, "aria-label": title, "aria-haspopup": "true", "aria-expanded": "false" }, "⚙");
  const wrap = el("div", { class: "cog-wrap" }, btn, pop);
  btn.addEventListener("click", () => {
    const wasOpen = openMenu === wrap;
    closeMenu();
    if (wasOpen) return;
    pop.replaceChildren(...build(closeMenu).filter(Boolean));
    pop.classList.remove("hidden");
    btn.setAttribute("aria-expanded", "true");
    openMenu = wrap;
  });
  return wrap;
}

export function menuItem(label, onclick, cls = "") {
  return el("button", { class: `menu-item ${cls}`, type: "button", role: "menuitem", onclick }, label);
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

// --- feedback dialog: any element with [data-feedback] opens it ------------------------------------
export function openFeedback() {
  const message = el("textarea", { rows: 5, maxlength: 5000, required: true, placeholder: "What would make Muddy Points better? Bugs, ideas, feature wishes…" });
  const email = el("input", { type: "email", placeholder: "you@university.edu (optional, if you'd like an answer)", value: session.user?.email || "" });
  const honeypot = el("input", { type: "text", name: "website", tabindex: "-1", autocomplete: "off", class: "hp", "aria-hidden": "true" });
  const status = el("div");
  const close = () => overlay.remove();
  const form = el("form", { class: "card stack dialog", role: "dialog", "aria-label": "Feedback" },
    el("div", { class: "panel-head" }, el("h2", {}, "💡 Feedback & feature ideas"),
      el("button", { class: "ghost", type: "button", "aria-label": "Close", onclick: close }, "✕")),
    el("p", { class: "muted small" }, "Goes straight to the person who maintains Muddy Points. Not public."),
    message, email, honeypot, status,
    el("div", { class: "row", style: "justify-content:flex-end" },
      el("button", { type: "button", onclick: close }, "Cancel"), el("button", { class: "primary", type: "submit" }, "Send")));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    busy(form.querySelector("button[type=submit]"), async () => {
      try {
        const res = await api("POST", "/api/feedback", { message: message.value, email: email.value, website: honeypot.value,
          page: location.pathname + location.search });
        form.replaceChildren(el("h2", {}, "Thank you! 🙌"), el("p", { class: "muted" }, res.mailed ? "Your message has been sent."
          : "Your message was saved, but the email to the maintainer could not be sent, so it may take a while until someone reads it."),
          el("div", { class: "row", style: "justify-content:flex-end" }, el("button", { class: "primary", type: "button", onclick: close }, "Close")));
      } catch (err) {
        status.replaceChildren(el("div", { class: "alert" }, err.message));
      }
    });
  });
  const overlay = el("div", { class: "dialog-backdrop", onclick: (e) => { if (e.target === overlay) close(); } }, form);
  document.addEventListener("keydown", function esc(e) { if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc); } });
  document.body.append(overlay);
  message.focus();
}
document.addEventListener("click", (e) => {
  const trigger = e.target.closest?.("[data-feedback]");
  if (trigger) { e.preventDefault(); openFeedback(); }
});
