// Upload page for temporary upload links (drop.html#<token>): no login, PDFs go into the workspace's files.
// Each file is hashed (SHA-256) in the browser first; files the server already has are not uploaded again.
import { api, el, ApiError, fmtDate } from "./api.js";

const token = location.hash.slice(1);
const intro = document.getElementById("intro");
const zone = document.getElementById("dropzone");
const input = document.getElementById("file-input");
const list = document.getElementById("file-list");
const summary = document.getElementById("summary");
const PARALLEL_UPLOADS = 3;
const counts = { added: 0, exists: 0, failed: 0 };
// Tell other open Muddy Points tabs (workspace page, board panel) to refresh their file lists.
const channel = "BroadcastChannel" in window ? new BroadcastChannel("muddypoints") : null;
const announce = () => channel?.postMessage({ type: "files-changed" });

function row(file) {
  const status = el("span", { class: "file-status muted small" }, "waiting…");
  const bar = el("progress", { max: 100, value: 0, class: "hidden" });
  const li = el("li", { class: "file-row" }, el("span", { class: "file-name" }, "📄 ", file.name),
    el("span", { class: "muted small" }, `${(file.size / 1048576).toFixed(1)} MB`), bar, status);
  list.append(li);
  return {
    set(text, kind = "") { status.textContent = text; status.className = `file-status small ${kind || "muted"}`; },
    progress(p) { bar.classList.toggle("hidden", p === null); if (p !== null) bar.value = p; },
  };
}

async function sha256(file) {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function put(url, headers, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    for (const [k, v] of Object.entries(headers)) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => (xhr.status < 300 ? resolve() : reject(new Error(`upload failed (${xhr.status})`)));
    xhr.onerror = () => reject(new Error("network error during upload"));
    xhr.send(file);
  });
}

function updateSummary(total) {
  const done = counts.added + counts.exists + counts.failed;
  summary.replaceChildren(done < total ? el("p", { class: "muted" }, `${done} of ${total} done…`)
    : el("div", { class: `alert ${counts.failed ? "" : "ok"}` },
      `Done: ${counts.added} added, ${counts.exists} already in the files${counts.failed ? `, ${counts.failed} failed` : ""}. `,
      counts.failed ? "You can drop the failed ones again." : "You can close this page and tell Claude you're done."));
}

async function handle(files) {
  files = [...files].filter((f) => f.size > 0);
  if (!files.length) return;
  const rows = files.map(row);
  const total = list.children.length;
  updateSummary(total);
  // 1. hash everything locally
  const infos = [];
  for (let i = 0; i < files.length; i++) {
    rows[i].set("checking…");
    infos.push({ name: files[i].name, size: files[i].size, content_type: files[i].type || "application/pdf", sha256: await sha256(files[i]) });
  }
  // 2. ask the server which files it already has
  let plan;
  try {
    ({ files: plan } = await api("POST", `/api/drop/${encodeURIComponent(token)}/check`, { files: infos }));
  } catch (e) {
    rows.forEach((r) => r.set(e.message, "danger-text")); counts.failed += files.length; updateSummary(total); return;
  }
  // 3. upload the unknown ones (a few in parallel), confirm each
  const queue = plan.map((p, i) => ({ p, i }));
  const finish = (i, text, kind, key) => {
    rows[i].progress(null); rows[i].set(text, kind); counts[key] += 1; updateSummary(total);
    if (key === "added") announce();
  };
  async function worker() {
    while (queue.length) {
      const { p, i } = queue.shift();
      if (p.status === "exists") { finish(i, "✓ already in the files", "ok-text", "exists"); continue; }
      if (p.status === "added") { finish(i, "✓ added", "ok-text", "added"); continue; }
      if (p.status === "rejected") { finish(i, `✗ ${p.error}`, "danger-text", "failed"); continue; }
      try {
        rows[i].set("uploading…"); rows[i].progress(0);
        await put(p.upload_url, p.headers, files[i], (pct) => rows[i].progress(pct));
        rows[i].set("processing…"); rows[i].progress(null);
        const { file } = await api("POST", `/api/drop/${encodeURIComponent(token)}/confirm`, { upload_key: p.upload_key, sha256: infos[i].sha256 });
        finish(i, file.status === "exists" ? "✓ already in the files" : `✓ added (${file.pages} pages)`, "ok-text", file.status === "exists" ? "exists" : "added");
      } catch (e) {
        finish(i, `✗ ${e.message}`, "danger-text", "failed");
      }
    }
  }
  await Promise.all(Array.from({ length: PARALLEL_UPLOADS }, worker));
}

async function init() {
  if (!token) {
    intro.replaceChildren(el("h1", {}, "Upload link incomplete"), el("p", { class: "muted" }, "Ask for a new upload link."));
    return;
  }
  if (!window.crypto?.subtle) {
    intro.replaceChildren(el("div", { class: "alert" }, "This browser can't check files before uploading. Please use a current browser."));
    return;
  }
  try {
    const info = await api("GET", `/api/drop/${encodeURIComponent(token)}`);
    intro.replaceChildren(
      el("h1", {}, "Upload files to “", info.workspace, "”"),
      info.note ? el("p", {}, info.note) : "",
      el("p", { class: "muted small" }, "PDF only. Files are kept permanently in the workspace and only go on a board when someone puts them there. ",
        "Files that are already there are recognised and not uploaded twice. ",
        `This link works until ${fmtDate(info.expires_at)}${info.created_by ? ` (created by ${info.created_by})` : ""}.`));
    zone.classList.remove("hidden");
  } catch (e) {
    intro.replaceChildren(el("h1", {}, e.status === 410 ? "This upload link has expired" : "Upload link not valid"),
      el("p", { class: "muted" }, e instanceof ApiError ? e.message : "Please ask for a new link."));
  }
}

input.addEventListener("change", () => { handle(input.files); input.value = ""; });
["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.remove("over"); }));
zone.addEventListener("drop", (e) => handle(e.dataTransfer.files));
window.addEventListener("dragover", (e) => e.preventDefault()); // dropping outside the zone must not open the PDF
window.addEventListener("drop", (e) => e.preventDefault());
init();
