// Public board page: slides with likes and comments. Staff (space members) also get a management toolbar.
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
  const canDelete = c.mine || state.my_role === "owner";
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
    state.my_role === "owner" && el("button", { class: "ghost sm danger", title: "Delete slide", onclick: async () => {
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
  document.getElementById("staff-link").classList.toggle("hidden", !my_role);
  renderToolbar();
  slideNodes.clear();
  const host = document.getElementById("slides");
  host.replaceChildren(...(slides.length ? slides.map((s, i) => slideNode(s, i, slides.length))
    : [el("div", { class: "card empty" }, my_role ? "No slides yet — upload a PDF or images above." : "No material has been posted yet. Check back later!")]));
}

function applyBackground(bg) {
  for (const [id] of BACKGROUNDS) document.body.classList.remove(`bg-${id}`);
  document.body.classList.add(`bg-${bg || "plain"}`);
}

// --- staff toolbar ---------------------------------------------------------------------------
function renderToolbar() {
  const bar = document.getElementById("toolbar");
  const role = state.my_role;
  bar.classList.toggle("hidden", !role);
  if (!role) return;
  const fileInput = el("input", { type: "file", multiple: true, accept: "application/pdf,image/*", class: "hidden" });
  const mode = el("select", { style: "width:auto" }, el("option", { value: "append" }, "Add after existing slides"),
    role === "owner" && el("option", { value: "replace" }, "Replace all slides (archives comments as PDF)"));
  const status = el("span", { class: "muted small" });
  const uploadBtn = el("button", { class: "primary", onclick: () => fileInput.click() }, "⬆ Upload PDF / images");
  fileInput.addEventListener("change", () => busy(uploadBtn, () => upload([...fileInput.files], mode.value, status)));
  const exportBtn = el("button", { onclick: () => busy(exportBtn, async () => {
    const r = await api("POST", `/api/boards/${boardId}/export`, {});
    location.href = r.download_url; // served as attachment -> downloads, page stays
  }) }, "⬇ Export PDF with comments");
  const lockBtn = el("button", { onclick: () => busy(lockBtn, async () => {
    await api("PATCH", `/api/boards/${boardId}`, { locked: !state.board.locked }); refresh(true);
  }) }, state.board.locked ? "🔓 Reopen comments" : "🔒 Close comments");
  const archivesBtn = el("button", { onclick: () => busy(archivesBtn, async () => {
    const { archives } = await api("GET", `/api/boards/${boardId}/archives`);
    if (!archives.length) return toast("No archived PDFs yet");
    const list = el("div", { class: "card stack", style: "width:100%" }, el("strong", {}, "Archived PDFs"),
      ...archives.map((a) => el("div", {}, el("a", { href: a.download_url, target: "_blank" }, a.key.split("/").pop()))));
    bar.append(list);
  }) }, "Archive");
  const clearBtn = role === "owner" && el("button", { class: "danger", onclick: () => busy(clearBtn, async () => {
    if (!confirm("Remove ALL slides, comments and likes from this board? A PDF of the current state is archived first.")) return;
    await api("POST", `/api/boards/${boardId}/clear`, { archive: true }); toast("Board cleared"); refresh(true);
  }) }, "Clear board");
  const picker = backgroundPicker(state.board.background, async (bg) => {
    applyBackground(bg);
    const { board } = await api("PATCH", `/api/boards/${boardId}`, { background: bg });
    state.board = board;
  });
  bar.replaceChildren(uploadBtn, mode, fileInput, status, el("span", { class: "spacer", style: "flex:1" }),
    exportBtn, lockBtn, archivesBtn, clearBtn, el("div", { class: "row", style: "width:100%" }, el("span", { class: "muted small" }, "Background:"), picker));
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
    if (location.hash) document.querySelector(location.hash)?.scrollIntoView();
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
