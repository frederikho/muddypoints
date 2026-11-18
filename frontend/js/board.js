// Public board page: slides with likes and comments. Staff (workspace members) also get a settings side panel (⚙).
import { api, session, storage, el, toast, showError, busy, timeAgo, ApiError, BACKGROUNDS, backgroundPicker } from "./api.js";

const boardId = new URLSearchParams(location.search).get("b");
const IDENTITY_KEY = "muddypoints.identity";
const REFRESH_MS = 20000;

let state = null;           // last response of GET /api/boards/<id>
let anonToken = null;
const slideNodes = new Map(); // slide_id -> {root, comments, likeBtn}

// --- anonymous identity: random funny name, kept 7 days in localStorage, then replaced ---------
async function ensureIdentity() {
  if (session.token) return null; // logged-in staff comment under their own name
  const saved = storage.get(IDENTITY_KEY);
  if (saved?.token && saved.expires_at * 1000 > Date.now()) return saved;
  storage.del(IDENTITY_KEY);
  const fresh = await api("POST", "/api/identity");
  storage.set(IDENTITY_KEY, fresh);
  return fresh;
}

const call = (method, path, body) => api(method, path, body, { anonToken });

async function load() {
  return call("GET", `/api/boards/${boardId}`);
}

// --- rendering -------------------------------------------------------------------------------
function likeButton(item, onToggle) {
  const btn = el("button", { class: `ghost sm like ${item.liked ? "on" : ""}`, title: "Like", onclick: () => busy(btn, onToggle) },
    item.liked ? "♥" : "♡", " ", String(item.likes || 0));
  return btn;
}

function commentNode(slide, c) {
  const canDelete = c.mine || !!state.my_role; // author or any workspace member
  return el("div", { class: "comment" },
    el("div", { class: `who ${c.is_staff ? "staff" : ""}` }, c.author_name, c.is_staff ? " · staff" : ""),
    el("div", { class: "text" }, c.text),
    el("div", { class: "meta" },
      el("span", { title: new Date(c.created_at).toLocaleString() }, timeAgo(c.created_at)),
      likeButton(c, async () => {
        const r = await call("POST", `/api/boards/${boardId}/likes`, { comment_id: c.comment_id });
        Object.assign(c, r); renderComments(slide);
      }),
      canDelete && el("button", { class: "ghost sm danger", onclick: async () => {
        if (!confirm("Delete this comment?")) return;
        await call("DELETE", `/api/boards/${boardId}/comments/${c.comment_id}`).catch(showError);
        slide.comments = slide.comments.filter((x) => x.comment_id !== c.comment_id); renderComments(slide);
      } }, "Delete")));
}

function renderComments(slide) {
  const node = slideNodes.get(slide.slide_id);
  if (!node) return;
  const list = node.comments;
  const atBottom = list.scrollTop + list.clientHeight >= list.scrollHeight - 10;
  list.replaceChildren(...(slide.comments.length
    ? slide.comments.map((c) => commentNode(slide, c))
    : [el("p", { class: "muted small" }, state.board.locked
      ? "Comments are closed."
      : "No questions yet. Something unclear? Ask here — it's anonymous.")]));
  if (atBottom) list.scrollTop = list.scrollHeight;
  node.likeBtn.replaceWith(node.likeBtn = slideLikeButton(slide));
  node.count.textContent = `${slide.comments.length} comment${slide.comments.length === 1 ? "" : "s"}`;
}

function slideLikeButton(slide) {
  return likeButton(slide, async () => {
    const r = await call("POST", `/api/boards/${boardId}/likes`, { slide_id: slide.slide_id });
    Object.assign(slide, r); renderComments(slide);
  });
}

function slideNode(slide, index, total) {
  const staff = !!state.my_role;
  const textarea = el("textarea", { placeholder: "What is unclear on this slide?", maxlength: 2000, rows: 2 });
  const form = el("form", { class: "comment-form" }, textarea,
    el("div", { class: "row" }, el("span", { class: "muted small", style: "margin-right:auto" }, "Ctrl+Enter to send"),
      el("button", { class: "primary sm", type: "submit" }, "Comment")));
  const submit = () => busy(form.querySelector("button"), async () => {
    if (!textarea.value.trim()) return;
    const { comment } = await call("POST", `/api/boards/${boardId}/comments`, { slide_id: slide.slide_id, text: textarea.value });
    textarea.value = "";
    slide.comments.push(comment);
    renderComments(slide);
    node.comments.scrollTop = node.comments.scrollHeight;
  });
  form.addEventListener("submit", (e) => { e.preventDefault(); submit(); });
  textarea.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submit(); } });
  if (state.board.locked && !staff) form.classList.add("hidden");

  const img = el("img", { src: slide.image_url, alt: `Slide ${index + 1}`, loading: index < 2 ? "eager" : "lazy",
    width: slide.width, height: slide.height, onclick: () => lightbox(slide.image_url) });
  const count = el("span", { class: "muted small" });
  const manage = staff ? [
    el("button", { class: "ghost sm", title: "Move up", disabled: index === 0, onclick: () => move(index, -1) }, "↑"),
    el("button", { class: "ghost sm", title: "Move down", disabled: index === total - 1, onclick: () => move(index, 1) }, "↓"),
    el("button", { class: "ghost sm danger", title: "Delete slide", onclick: async () => {
      if (!confirm(`Delete slide ${index + 1} and its comments?`)) return;
      await api("DELETE", `/api/boards/${boardId}/slides/${slide.slide_id}`).catch(showError); refresh(true);
    } }, "🗑"),
  ] : [];
  const node = {
    comments: el("div", { class: "comments" }),
    likeBtn: el("span"),
    count,
  };
  node.root = el("section", { class: "card slide", id: `slide-${index + 1}` },
    el("div", { class: "media" }, img, el("div", { class: "bar" },
      el("a", { class: "slide-num", href: `#slide-${index + 1}` }, `Slide ${index + 1}`), ...manage, node.likeBtn)),
    el("div", { class: "discussion" }, el("div", { class: "row", style: "margin-bottom:4px" }, count), node.comments, form));
  slideNodes.set(slide.slide_id, node);
  renderComments(slide);
  return node.root;
}

function lightbox(src) {
  const box = el("div", { class: "lightbox", onclick: () => box.remove() }, el("img", { src }));
  document.addEventListener("keydown", function esc(e) { if (e.key === "Escape") { box.remove(); document.removeEventListener("keydown", esc); } });
  document.body.append(box);
}

function renderAll() {
  const { board, slides, viewer, my_role } = state;
  document.title = `${board.name} · Muddy Points`;
  document.getElementById("board-title").textContent = board.name;
  document.getElementById("locked").classList.toggle("hidden", !board.locked);
  applyBackground(board.background);
  document.getElementById("who").replaceChildren(viewer ? el("span", { class: "identity-chip", title: viewer.is_user ? "Logged in" : "Your anonymous name for this week (assigned randomly)" },
    viewer.is_user ? "👤 " : "🎭 ", viewer.name) : "");
  renderPanel();
  slideNodes.clear();
  const host = document.getElementById("slides");
  host.replaceChildren(...(slides.length ? slides.map((s, i) => slideNode(s, i, slides.length))
    : [state.upcoming ? comingSoon(state.upcoming)
      : el("div", { class: "card empty" }, my_role ? "No slides yet — open ⚙ (top right) to upload a PDF or images." : "No material has been posted yet. Check back later!")]));
}

function applyBackground(bg) {
  for (const [id] of BACKGROUNDS) document.body.classList.remove(`bg-${id}`);
  document.body.classList.add(`bg-${bg || "plain"}`);
}

// --- staff side panel (⚙ in the top bar) ------------------------------------------------------
const panel = document.getElementById("panel");
const backdrop = document.getElementById("panel-backdrop");
const settingsBtn = document.getElementById("settings-btn");
let uploading = false;

function setPanel(open) {
  panel.classList.toggle("open", open);
  backdrop.classList.toggle("hidden", !open);
  panel.setAttribute("aria-hidden", String(!open));
  settingsBtn.setAttribute("aria-expanded", String(open));
  if (open) loadPanelData();
}
settingsBtn.addEventListener("click", () => setPanel(!panel.classList.contains("open")));
backdrop.addEventListener("click", () => setPanel(false));
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && panel.classList.contains("open")) setPanel(false); });

const section = (title, ...children) => el("section", { class: "panel-section" }, el("h3", {}, title), ...children);

function comingSoon(up) {
  const when = new Date(up.starts_at);
  return el("div", { class: "card empty coming-soon" },
    el("div", { class: "coming-soon-label" }, "Coming soon"),
    el("h2", {}, up.title),
    el("p", {}, "Available from ", el("strong", {}, when.toLocaleString(undefined, { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" }))),
    el("p", { class: "muted small" }, "Bookmark this page — the material appears here automatically."));
}

// datetime-local <-> ISO helpers (inputs are in the viewer's local time)
const toLocalInput = (iso) => { const d = new Date(iso); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16); };
const fromLocalInput = (v) => new Date(v).toISOString();
const STATUS = { planned: ["planned", "badge"], switching: ["switching…", "badge"], live: ["live now", "badge owner"],
  done: ["done", "badge"], skipped: ["skipped", "badge"], failed: ["failed", "badge locked"] };

async function loadSchedule() {
  const box = document.getElementById("schedule-box");
  if (!box) return;
  try {
    const [{ sessions }, { files }] = await Promise.all([api("GET", `/api/boards/${boardId}/schedule`),
      api("GET", `/api/spaces/${state.board.space_id}/files`)]);
    const reload = () => loadSchedule();
    const rows = sessions.map((x) => {
      const [label, cls] = STATUS[x.status] || [x.status, "badge"];
      const editable = x.status === "planned";
      const time = editable
        ? el("input", { type: "datetime-local", value: toLocalInput(x.starts_at), class: "session-time", onchange: (e) => busy(null, async () => {
          await api("PATCH", `/api/boards/${boardId}/sessions/${x.session_id}`, { starts_at: fromLocalInput(e.target.value) }); reload(); }) })
        : el("span", { class: "small" }, new Date(x.starts_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }));
      return el("div", { class: "session-row" },
        el("div", { class: "row", style: "justify-content:space-between;flex-wrap:nowrap" }, time, el("span", { class: cls }, label)),
        el("div", { class: "row", style: "justify-content:space-between;flex-wrap:nowrap" },
          x.file_id ? el("a", { href: "#", class: "small", title: `Open ${x.name || "PDF"} in a new tab`, onclick: (e) => { e.preventDefault(); viewFile(x.file_id); } }, "📄 ", x.title)
            : el("span", { class: "small" }, x.title),
          x.status !== "live" && x.status !== "switching" && el("button", { class: "ghost sm danger", type: "button", title: "Remove",
            onclick: (e) => busy(e.currentTarget, async () => { await api("DELETE", `/api/boards/${boardId}/sessions/${x.session_id}`); reload(); }) }, "✕")),
        x.error ? el("div", { class: "alert small" }, x.error) : "");
    });
    // add a session: default = one week after the last one, else tomorrow 16:00
    const last = sessions.length ? new Date(sessions[sessions.length - 1].starts_at) : null;
    const next = last ? new Date(last.getTime() + 7 * 864e5) : (() => { const d = new Date(Date.now() + 864e5); d.setHours(16, 0, 0, 0); return d; })();
    const pick = el("select", {}, ...files.map((f) => el("option", { value: f.file_id }, `${f.title || f.name}`)));
    const when = el("input", { type: "datetime-local", value: toLocalInput(next.toISOString()) });
    const add = el("button", { class: "block", type: "button", onclick: () => busy(add, async () => {
      const { session } = await api("POST", `/api/boards/${boardId}/sessions`, { file_id: pick.value, starts_at: fromLocalInput(when.value) });
      if (new Date(session.starts_at) <= new Date()) await refresh(true); // already due -> the board switches now
      else reload();
    }) }, "+ Add session");
    box.replaceChildren(
      el("p", { class: "muted small" }, "At each time the current slides and comments are archived as PDF and the session's file goes live. Times in your local time."),
      ...(rows.length ? rows : [el("p", { class: "muted small" }, "No sessions planned. Tip: ask Claude to plan the semester from your syllabus.")]),
      files.length ? el("div", { class: "stack add-session" }, pick, when, add)
        : el("p", { class: "muted small" }, "Upload lecture PDFs to the workspace files first."));
  } catch (e) { box.replaceChildren(el("p", { class: "alert" }, e.message)); }
}

async function loadNotifications() {
  const box = document.getElementById("notify-box");
  if (!box) return;
  try {
    const { enabled } = await api("GET", `/api/boards/${boardId}/notifications`);
    const cb = el("input", { type: "checkbox", checked: enabled, onchange: () => busy(null, async () => {
      await api("PUT", `/api/boards/${boardId}/notifications`, { enabled: cb.checked }); toast(cb.checked ? "Emails on" : "Emails off");
    }) });
    box.replaceChildren(el("label", { class: "check" }, cb, " Email me whenever this board's material changes"));
  } catch (e) { box.replaceChildren(el("p", { class: "alert" }, e.message)); }
}

if ("BroadcastChannel" in window) {
  new BroadcastChannel("muddypoints").onmessage = (e) => {
    if (e.data?.type === "files-changed" && panel.classList.contains("open")) { loadFiles(); loadSchedule(); }
  };
}

function loadPanelData() {
  loadArchives(); loadFiles(); loadHistory(); loadSchedule(); loadNotifications();
}

const publish = (fileId, mode) => api("POST", `/api/spaces/${state.board.space_id}/files/${fileId}/publish`,
  { board_id: boardId, mode });

// Opens a workspace PDF in the browser's viewer (new tab; window opened first so popup blockers don't interfere).
async function viewFile(fileId) {
  const win = window.open("", "_blank");
  try {
    const { download_url } = await api("GET", `/api/spaces/${state.board.space_id}/files/${fileId}/download?inline=1`);
    if (win) win.location.href = download_url; else location.href = download_url;
  } catch (e) { win?.close(); showError(e); }
}

async function loadFiles() {
  const box = document.getElementById("files-box");
  if (!box) return;
  box.replaceChildren(el("p", { class: "muted small" }, "Loading…"));
  try {
    const { files } = await api("GET", `/api/spaces/${state.board.space_id}/files`);
    if (!files.length) {
      box.replaceChildren(el("p", { class: "muted small" }, "No files in this workspace yet. PDFs you upload here are kept there automatically."));
      return;
    }
    const pick = el("select", {}, ...files.map((f) => el("option", { value: f.file_id }, `${f.title || f.name} (${f.pages} p.)`)));
    const go = el("button", { class: "block", type: "button", onclick: () => busy(go, async () => {
      const f = files.find((x) => x.file_id === pick.value);
      if (!confirm(`Replace the current slides with “${f.title || f.name}”? The current slides and comments are archived as PDF first.`)) return;
      await publish(f.file_id, "replace"); toast("New material is live"); await refresh(true);
    }) }, "⇄ Replace slides with this file");
    const view = el("button", { class: "ghost sm", type: "button", onclick: () => viewFile(pick.value) }, "👁 View this file");
    box.replaceChildren(pick, view, go);
  } catch (e) { box.replaceChildren(el("p", { class: "alert" }, e.message)); }
}

async function loadHistory() {
  const box = document.getElementById("history-box");
  if (!box) return;
  try {
    const { history } = await api("GET", `/api/boards/${boardId}/history`);
    if (!history.length) { box.replaceChildren(el("p", { class: "muted small" }, "Nothing yet.")); return; }
    box.replaceChildren(...history.slice(0, 30).map((h) => {
      const reusable = h.files.find((f) => f.file_id);
      return el("div", { class: "history-row" },
        el("div", {}, el("strong", { class: "small" }, h.files.map((f) => f.title || f.name).join(", ")),
          el("div", { class: "muted small" }, new Date(h.at).toLocaleString(), h.by ? ` · ${h.by}` : "", h.mode === "append" ? " · added" : "")),
        reusable && el("button", { class: "ghost sm", type: "button", title: "Put this material on the board again", onclick: (e) => busy(e.currentTarget, async () => {
          if (!confirm(`Show “${reusable.title || reusable.name}” again? The current slides and comments are archived as PDF first.`)) return;
          await publish(reusable.file_id, "replace"); toast("Material is live again"); await refresh(true);
        }) }, "Use again"));
    }));
  } catch (e) { box.replaceChildren(el("p", { class: "alert" }, e.message)); }
}

async function loadArchives() {
  const list = document.getElementById("archive-list");
  if (!list) return;
  list.replaceChildren(el("p", { class: "muted small" }, "Loading…"));
  try {
    const { archives } = await api("GET", `/api/boards/${boardId}/archives`);
    list.replaceChildren(...(archives.length
      ? archives.map((a) => el("a", { class: "archive-link", href: a.download_url, target: "_blank" }, "📄 ", a.key.split("/").pop()))
      : [el("p", { class: "muted small" }, "No archived PDFs yet. A PDF is archived automatically whenever the slides are replaced or the board is cleared.")]));
  } catch (e) {
    list.replaceChildren(el("p", { class: "alert" }, e.message));
  }
}

function renderPanel() {
  const role = state.my_role;
  settingsBtn.classList.toggle("hidden", !role);
  if (!role) { setPanel(false); return; }
  const wasOpen = panel.classList.contains("open");
  const scrollTop = panel.scrollTop;

  const fileInput = el("input", { type: "file", multiple: true, accept: "application/pdf,image/*", class: "hidden" });
  const mode = el("select", {}, el("option", { value: "replace" }, "Replace all slides (old ones are archived)"),
    el("option", { value: "append" }, "Add after existing slides"));
  const status = el("div", { class: "muted small", id: "upload-status" });
  const uploadBtn = el("button", { class: "primary block", disabled: uploading, onclick: () => fileInput.click() }, "⬆ Upload PDF / images");
  fileInput.addEventListener("change", () => busy(uploadBtn, async () => {
    uploading = true;
    try { await upload([...fileInput.files], mode.value, status); } finally { uploading = false; fileInput.value = ""; }
  }));

  const exportBtn = el("button", { class: "block", onclick: () => busy(exportBtn, async () => {
    const r = await api("POST", `/api/boards/${boardId}/export`, {});
    location.href = r.download_url; // served as attachment -> downloads, page stays
  }) }, "⬇ Export PDF with comments");
  const lockBtn = el("button", { class: "block", onclick: () => busy(lockBtn, async () => {
    await api("PATCH", `/api/boards/${boardId}`, { locked: !state.board.locked }); await refresh(true);
  }) }, state.board.locked ? "🔓 Reopen comments" : "🔒 Close comments");
  const renameBtn = el("button", { class: "block", onclick: () => busy(renameBtn, async () => {
    const name = prompt("Rename board:", state.board.name);
    if (name) { await api("PATCH", `/api/boards/${boardId}`, { name }); await refresh(true); }
  }) }, "✏️ Rename board");
  const picker = backgroundPicker(state.board.background, async (bg) => {
    applyBackground(bg);
    const { board } = await api("PATCH", `/api/boards/${boardId}`, { background: bg });
    state.board = board;
  });
  const clearBtn = el("button", { class: "danger block", onclick: () => busy(clearBtn, async () => {
    if (!confirm("Remove ALL slides, comments and likes from this board? A PDF of the current state is archived first.")) return;
    await api("POST", `/api/boards/${boardId}/clear`, { archive: true }); toast("Board cleared"); await refresh(true);
  }) }, "Clear board…");

  panel.replaceChildren(
    el("div", { class: "panel-head" }, el("h2", {}, "Board settings"),
      el("button", { class: "ghost", type: "button", "aria-label": "Close", onclick: () => setPanel(false) }, "✕")),
    section("Slides", uploadBtn, mode, fileInput, status,
      el("p", { class: "muted small" }, "PDF pages and images become one slide each. Reorder or delete single slides with the controls under each slide.")),
    section("Comments", lockBtn, exportBtn),
    section("Background", picker),
    section("Board", renameBtn),
    section("Schedule", el("div", { id: "schedule-box", class: "stack" })),
    section("From workspace files", el("div", { id: "files-box", class: "stack" }),
      el("button", { class: "ghost sm", type: "button", onclick: () => openUploadLink() }, "+ Upload more files (opens upload page)")),
    section("History", el("div", { id: "history-box", class: "stack" })),
    section("Archive", el("div", { id: "archive-list", class: "stack" })),
    section("Notifications", el("div", { id: "notify-box" })),
    section("Danger zone", clearBtn));
  panel.scrollTop = scrollTop;
  if (wasOpen) loadPanelData();
}

// Opens a fresh no-login upload page for this board's workspace in a new tab.
async function openUploadLink() {
  const win = window.open("", "_blank");
  try {
    const { upload_link } = await api("POST", `/api/spaces/${state.board.space_id}/upload-links`, {});
    if (win) win.location.href = upload_link; else location.href = upload_link;
  } catch (e) { win?.close(); showError(e); }
}

// Links like board.html?b=<id>#upload (e.g. handed out by Claude) open the upload panel directly.
function openUploadFromLink(replace = false) {
  if (!state.my_role) {
    if (!session.token) {
      location.href = `./?next=${encodeURIComponent(`board.html${location.search}${location.hash}`)}`;
    } else {
      toast("Only members of this board's workspace can upload.", "error");
    }
    return;
  }
  setPanel(true);
  const modeSelect = panel.querySelector("select");
  if (replace && modeSelect?.querySelector("option[value=replace]")) modeSelect.value = "replace";
  const btn = panel.querySelector("button.primary");
  btn?.focus();
  btn?.classList.add("pulse");
}

async function upload(files, mode, status) {
  if (!files.length) return;
  if (mode === "replace" && !confirm("Replace all current slides? Their comments are saved to the archive as PDF first.")) return;
  try {
    status.textContent = "Preparing…";
    const { uploads } = await api("POST", `/api/boards/${boardId}/uploads`,
      { files: files.map((f) => ({ filename: f.name, content_type: f.type || "application/octet-stream" })) });
    for (let i = 0; i < files.length; i++) {
      status.textContent = `Uploading ${i + 1}/${files.length}…`;
      const r = await fetch(uploads[i].upload_url, { method: "PUT", headers: uploads[i].headers, body: files[i] });
      if (!r.ok) throw new ApiError(r.status, "upload_failed", `Upload of ${files[i].name} failed (${r.status})`);
    }
    status.textContent = "Converting to slides… (large PDFs take a moment)";
    const res = await api("POST", `/api/boards/${boardId}/uploads/confirm`, { upload_keys: uploads.map((u) => u.upload_key), mode });
    toast(`${res.added_slides.length} slide(s) added`);
    await refresh(true);
  } finally {
    status.textContent = "";
  }
}

async function move(index, delta) {
  const ids = state.slides.map((s) => s.slide_id);
  [ids[index], ids[index + delta]] = [ids[index + delta], ids[index]];
  await api("POST", `/api/boards/${boardId}/slides/reorder`, { slide_ids: ids }).catch(showError);
  refresh(true);
}

// --- refresh: full re-render only if slides changed, otherwise update comments in place --------
async function refresh(force = false) {
  const next = await load();
  const sameSlides = state && next.slides.map((s) => s.slide_id).join() === state.slides.map((s) => s.slide_id).join();
  const sameMeta = state && next.board.locked === state.board.locked && next.board.name === state.board.name
    && JSON.stringify(next.upcoming) === JSON.stringify(state.upcoming)
    && next.board.background === state.board.background;
  if (!force && sameSlides && sameMeta) {
    next.slides.forEach((s, i) => {
      s.image_url = state.slides[i].image_url; // keep old URL so images don't reload
      state.slides[i] = s;
      renderComments(s);
    });
    state.board = next.board;
  } else {
    state = next;
    renderAll();
  }
}

async function init() {
  if (!boardId) {
    document.getElementById("board-title").textContent = "No board selected";
    return;
  }
  try {
    const identity = await ensureIdentity();
    anonToken = identity?.token || null;
    await refresh(true);
    if (location.hash === "#upload" || location.hash === "#upload-replace") openUploadFromLink(location.hash === "#upload-replace");
    else if (location.hash) document.querySelector(location.hash)?.scrollIntoView();
  } catch (e) {
    document.getElementById("board-title").textContent = e.status === 404 ? "Board not found" : "Could not load board";
    document.getElementById("slides").replaceChildren(el("div", { class: "card empty" },
      e.status === 404 ? "This board does not exist (anymore). Ask your lecturer for the current link." : e.message));
    return;
  }
  setInterval(() => { if (document.visibilityState === "visible") refresh().catch(() => {}); }, REFRESH_MS);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refresh().catch(() => {}); });
}

init();
