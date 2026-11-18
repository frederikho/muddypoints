// Dashboard SPA (hash routing): #/ workspaces, #/space/<id> (a workspace), #/account. Login/registration when logged out.
import { api, session, el, toast, showError, busy, copyText, fmtDate, timeAgo, API_ORIGIN, backgroundPicker, cogMenu, menuItem, avatar } from "./api.js";

const main = document.getElementById("main");
const nav = document.getElementById("nav");

function renderNav() {
  const user = session.user;
  nav.replaceChildren(...(user ? [
    el("a", { href: "#/" }, "Workspaces"),
    user.is_superadmin ? el("a", { href: "#/admin" }, "Admin") : "",
    el("a", { href: "#/account" }, user.name),
    el("button", { class: "ghost sm", onclick: () => { session.clear(); location.hash = "#/"; route(); } }, "Log out"),
  ] : []));
}

// --- auth screens ----------------------------------------------------------------------------
function authView(tab = "login") {
  // one message area per form (a DOM node can only live in one place)
  const msgs = { login: el("div", { class: "form-msg" }), register: el("div", { class: "form-msg" }), forgot: el("div", { class: "form-msg" }) };
  const field = (id, label, type = "text", extra = {}) =>
    el("div", {}, el("label", { for: id }, label), el("input", { id, type, required: true, ...extra }));
  const forms = {
    login: el("form", { class: "stack" },
      field("email", "Email", "email", { autocomplete: "email" }),
      field("password", "Password", "password", { autocomplete: "current-password" }),
      msgs.login, el("button", { class: "primary", type: "submit" }, "Log in"),
      el("div", { class: "small" }, el("a", { href: "#", onclick: (e) => { e.preventDefault(); show("forgot"); } }, "Forgot your password?"))),
    register: el("form", { class: "stack" },
      field("name", "Your name (shown on your comments)", "text", { autocomplete: "name", maxlength: 80 }),
      field("email", "Email", "email", { autocomplete: "email" }),
      field("password", "Password (min. 8 characters)", "password", { autocomplete: "new-password", minlength: 8 }),
      msgs.register, el("button", { class: "primary", type: "submit" }, "Create account")),
    forgot: el("form", { class: "stack" },
      el("p", { class: "muted small" }, "We'll email you a link to choose a new password."),
      field("email", "Email", "email", { autocomplete: "email" }),
      msgs.forgot, el("button", { class: "primary", type: "submit" }, "Send reset link")),
  };
  const tabs = el("div", { class: "tabs" });
  const holder = el("div");
  function show(name) {
    Object.values(msgs).forEach((m) => m.replaceChildren());
    tabs.replaceChildren(
      ...[["login", "Log in"], ["register", "Register"]].map(([k, label]) =>
        el("button", { class: k === name ? "active" : "", type: "button", onclick: () => show(k) }, label)));
    holder.replaceChildren(forms[name]);
  }
  for (const [name, form] of Object.entries(forms)) {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const v = (id) => form.querySelector(`#${id}`)?.value;
      const btn = form.querySelector("button[type=submit]");
      btn.disabled = true;
      try {
        if (name === "forgot") {
          await api("POST", "/api/auth/forgot", { email: v("email") });
          msgs.forgot.replaceChildren(el("div", { class: "alert ok" }, "If an account exists for this email, a reset link is on its way. Check your spam folder, too."));
        } else {
          const body = name === "login" ? { email: v("email"), password: v("password") }
            : { name: v("name"), email: v("email"), password: v("password") };
          const res = await api("POST", `/api/auth/${name}`, body);
          session.set(res.token, res.user);
          const next = new URLSearchParams(location.search).get("next");
          if (next && /^board\.html\?b=[a-z0-9]+(#upload(-replace)?)?$/.test(next)) { location.href = next; return; }
          // a brand-new account owns nothing and has no access to the workspace that was open before: start at the overview
          if (name === "register") history.replaceState(null, "", location.pathname + location.search + "#/");
          route();
        }
      } catch (err) {
        msgs[name].replaceChildren(el("div", { class: "alert" }, err.message,
          err.code === "email_taken" ? el("span", {}, " ", el("a", { href: "#", onclick: (e) => { e.preventDefault(); show("login"); } }, "Log in instead"),
            " or ", el("a", { href: "#", onclick: (e) => { e.preventDefault(); show("forgot"); } }, "reset your password"), ".") : ""));
      } finally { btn.disabled = false; }
    });
  }
  show(tab);
  const authCard = el("div", { class: "card auth-card", id: "auth" }, tabs, holder);
  const goAuth = (which) => { show(which); authCard.scrollIntoView({ behavior: "smooth", block: "center" }); authCard.querySelector("input")?.focus(); };

  const mockComment = (who, text, likes, staff = false) => el("div", { class: "comment" },
    el("div", { class: `who ${staff ? "staff" : ""}` }, who, staff ? " · staff" : ""),
    el("div", { class: "text" }, text),
    el("div", { class: "meta" }, el("span", {}, "just now"), el("span", { class: `like ${likes > 2 ? "on" : ""}` }, likes > 2 ? "♥ " : "♡ ", likes)));
  const mock = el("div", { class: "mock bg-cork", "aria-hidden": "true" },
    el("div", { class: "card mock-slide" },
      el("div", { class: "mock-img" }, el("div", { class: "mock-title" }, "Sample variance"),
        el("div", { class: "mock-formula" }, "s² = Σ (xᵢ − x̄)² / (n − 1)")),
      el("div", { class: "mock-comments" },
        mockComment("🎭 Lazy Lizard", "Why n − 1 and not n?", 7),
        mockComment("🎭 Curious Capybara", "Same question! 🙋", 3),
        mockComment("Prof. Alice", "Great point — we'll start with this on Tuesday.", 1, true))));

  const spark = (cls = "") => el("img", { class: `spark ${cls}`, src: "img/claude-spark.png", alt: "", width: 20, height: 20 });
  const toolRow = (name) => el("div", { class: "tool" }, el("span", { class: "tool-dot" }), "Muddy Points ", el("code", {}, name), el("span", { class: "tool-chev" }, "›"));
  const workflows = [
    { icon: "🔁", title: "Weekly rotation", text: "Archive, clear, new slides",
      ask: "Save this week's comments as a PDF and put up lecture 5 from this link: drive.google.com/file/d/1x…",
      tools: ["export_board_pdf", "replace_board_material"],
      reply: ["I've exported this week's comments as a PDF and archived the old slides. Lecture 5 (42 slides) is now live on the board.", "The comments PDF is ready to download: Statistics I – week 4.pdf"] },
    { icon: "📅", title: "Schedule the semester", text: "Syllabus in, whole term planned",
      ask: "Here's my syllabus (Syllabus_Stats1.pdf). Schedule all 12 lectures on the Statistics I board, every Tuesday at 10:00 from 13 April.",
      tools: ["list_files", "set_schedule"],
      reply: ["I've planned all 12 sessions on the Statistics I board, every Tuesday at 10:00 (Europe/Berlin) from 13 April to 29 June. The board switches to the next lecture automatically, and you get an email each time.",
        ["11 sessions are matched to the lecture files in your workspace", "Lecture 7 has no file yet, so that session is empty. Upload it and I'll attach it", "No session falls on a public holiday"],
        "Tell me if you want to skip or move any week."] },
    { icon: "🔍", title: "Prepare class", text: "Top questions, slides to revisit",
      ask: "What were the most-liked questions on the lecture 4 board? Which slides should I revisit?",
      tools: ["list_comments"],
      reply: ["Three slides stand out:", ["Slide 12: “Why n − 1 and not n?” (14 likes)", "Slide 18: “Is this the same as the t-test?” (9 likes)", "Slide 23: “Where does the formula come from?” (6 likes)"], "I'd start with slide 12. It has more than twice as many likes as anything else."] },
    { icon: "🆕", title: "New course", text: "Board + invite your tutor",
      ask: "Create a board for Statistics II in our workspace and invite my tutor as editor.",
      tools: ["create_board", "invite_member"],
      reply: ["I created the board “Statistics II” and invited your tutor as an editor. They'll get an email.", "Students can use this link: muddypoints.app/board/k7x2…"] },
    { icon: "🧹", title: "Tidy up", text: "Lock, delete spam, reorder",
      ask: "Lock the board after class, delete the spam comment on slide 7 and move slide 9 to the front.",
      tools: ["update_board", "delete_comment", "reorder_slides"],
      reply: ["Done. The board is locked, so only staff can comment now. I deleted the spam comment on slide 7 and moved slide 9 to the front."] },
  ];
  const chat = el("div", { class: "card chat", "aria-live": "polite" });
  const tiles = el("div", { class: "ai-tiles", role: "tablist", "aria-label": "Example workflows" });
  const showWorkflow = (i) => {
    const w = workflows[i];
    chat.replaceChildren(
      el("div", { class: "user-msg" }, w.ask),
      el("div", { class: "tools" }, ...w.tools.map(toolRow)),
      el("div", { class: "claude-reply" }, ...w.reply.map((r) => Array.isArray(r) ? el("ul", {}, ...r.map((x) => el("li", {}, x))) : el("p", {}, r))),
      spark("end"),
      el("div", { class: "composer" }, el("span", {}, "Reply to Claude…"), el("span", { class: "send" }, "↑")));
    [...tiles.children].forEach((t, j) => { t.classList.toggle("on", j === i); t.setAttribute("aria-selected", j === i ? "true" : "false"); });
  };
  workflows.forEach((w, i) => tiles.append(el("button", { type: "button", role: "tab", class: "ai-tile", onclick: () => showWorkflow(i) },
    el("span", { class: "ai-tile-icon" }, w.icon), el("strong", {}, w.title), el("span", { class: "small" }, w.text))));
  showWorkflow(0);
  const step = (n, title, text) => el("div", { class: "step" }, el("div", { class: "step-n" }, n), el("h3", {}, title), el("p", { class: "muted" }, text));
  const feature = (icon, title, text) => el("div", { class: "card feature" }, el("div", { class: "feature-icon" }, icon), el("h3", {}, title), el("p", { class: "muted small" }, text));

  main.replaceChildren(el("div", { class: "landing" },
    el("section", { class: "hero" },
      el("div", { class: "hero-text" },
        el("div", { class: "eyebrow" }, "Free for lecturers & their students"),
        el("h1", {}, "Find the ", el("span", { class: "hl" }, "muddiest points"), " before the lecture starts."),
        el("p", { class: "lead" }, "Post your slides, share one link, and let students flag anonymously what they didn't understand. You walk into class knowing exactly what to explain."),
        el("div", { class: "row" },
          el("button", { class: "primary lg", onclick: () => goAuth("register") }, "Create a free staff account"),
          el("button", { class: "lg", onclick: () => goAuth("login") }, "Log in")),
        el("p", {}, el("a", { class: "demo-link", href: "board.html?b=demo" }, "👀 See a demo board: what your students see →")),
        el("p", { class: "muted small" }, "Totally free: no credit card, no trial, no paid tier. Students don't need an account — they just open the board link from their lecturer.")),
      mock),
    el("section", {},
      el("h2", { class: "center" }, "How it works"),
      el("div", { class: "steps" },
        step("1", "Upload the slides", "Drop in a PDF or a few images. Every page becomes a slide on the class board."),
        step("2", "Share one link", "Students read before class and comment on the slides that confuse them — anonymously, under a random name."),
        step("3", "Teach what matters", "Likes show which questions many share. Export everything as a PDF and load next week's material onto the same link."))),
    el("section", {},
      el("div", { class: "features" },
        feature("🎭", "Anonymous, but friendly", "No sign-up for students. Each gets a random name for the week, so asking a “stupid question” costs nothing."),
        feature("♥", "Hot spots at a glance", "Students like slides and each other's questions — the muddiest points float to the top."),
        feature("🔗", "One link per class", "Replace the material every week; the link and students' bookmarks stay the same. Old comments are archived as PDF."),
        feature("📄", "PDF export", "Every slide with its comments in one tidy PDF, ready to share with your team or upload to your course platform."),
        feature("👥", "Teaching teams", "Share a workspace with colleagues and tutors: boards, files and lecture slides are shared."),
        feature("✨", "Works with Claude & ChatGPT", "Connect your AI assistant and just ask: “export this week's questions and put up lecture 5”."))),
    el("section", { class: "ai-section" },
      el("div", { class: "eyebrow center ai-eyebrow" }, spark(), "Works with Claude and ChatGPT"),
      el("h2", { class: "center" }, "Run your boards by chatting"),
      el("p", { class: "lead center ai-lead" }, "Connect once, then just say what you need. Muddy Points itself is free; the assistant runs on your own Claude or ChatGPT plan."),
      el("div", { class: "ai-demo" }, tiles, chat),
      el("div", { class: "ai-setup" },
        el("span", { class: "ai-setup-step" }, el("b", {}, "1"), "Create a key"),
        el("span", { class: "ai-arrow" }, "→"),
        el("span", { class: "ai-setup-step" }, el("b", {}, "2"), "Add it in Claude or ChatGPT"),
        el("span", { class: "ai-arrow" }, "→"),
        el("span", { class: "ai-setup-step" }, el("b", {}, "3"), "Just ask")),
      el("p", { class: "muted small center" }, "The assistant only acts with your permissions; keys can be revoked any time.")),
    el("section", { class: "auth-section" },
      el("div", {}, el("h2", {}, "Ready for your next lecture?"),
        el("div", { class: "free-badge" }, "100% free, for staff and students"),
        el("p", { class: "muted" }, "Muddy Points costs nothing. Create a workspace for your teaching team, add one board per course and share its link with your students.")),
      authCard),
    el("footer", { class: "muted small center" }, "Muddy Points — named after the “muddiest point” classroom technique.")));
}

// --- spaces ----------------------------------------------------------------------------------

// A quiet "+ New …" link that expands into a one-line form. onCreate(name) may throw (shown as toast).
function inlineCreate(label, placeholder, onCreate) {
  const input = el("input", { type: "text", placeholder, maxlength: 120, required: true });
  const form = el("form", { class: "row inline-create hidden" }, input,
    el("button", { class: "primary sm", type: "submit" }, "Create"),
    el("button", { class: "ghost sm", type: "button", onclick: () => toggle(false) }, "Cancel"));
  const button = el("button", { class: "ghost sm", type: "button", onclick: () => toggle(true) }, `+ ${label}`);
  function toggle(open) {
    form.classList.toggle("hidden", !open);
    button.classList.toggle("hidden", open);
    if (open) input.focus(); else input.value = "";
  }
  form.addEventListener("submit", (e) => { e.preventDefault(); busy(form.querySelector("button[type=submit]"), () => onCreate(input.value)); });
  return { button, form };
}

const MAX_BOARD_PREVIEWS = 6;
const MAX_AVATARS = 7;

function activityLine(at, by, where) {
  return el("div", { class: "small" }, "Last activity ",
    el("strong", { title: fmtDate(at) }, timeAgo(at)),
    by ? el("span", {}, " by ", el("strong", {}, by)) : "",
    where ? el("span", { class: "muted" }, ` · ${where}`) : "");
}

async function spacesView() {
  const { spaces } = await api("GET", "/api/spaces");
  const create = inlineCreate("New workspace", "Team or chair, e.g. “Chair of Environmental Economics”", async (name) => {
    const { space } = await api("POST", "/api/spaces", { name });
    location.hash = `#/space/${space.space_id}`;
  });
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const card = (s) => {
    const shownBoards = s.boards.slice(0, MAX_BOARD_PREVIEWS);
    const moreBoards = s.board_count - shownBoards.length;
    const shownMembers = s.members.slice(0, MAX_AVATARS);
    return el("div", { class: "card link space-card", onclick: () => (location.hash = `#/space/${s.space_id}`) },
      el("div", { class: "row", style: "justify-content:space-between;flex-wrap:nowrap" },
        el("h3", { style: "margin:0" }, s.name), el("span", { class: `badge ${s.role}` }, s.role)),
      activityLine(s.last_activity_at, s.last_activity_by, s.last_activity_board),
      el("div", { class: "mini-boards" },
        shownBoards.length ? shownBoards.map((b) => el("a", { class: "mini-board", href: `board.html?b=${b.board_id}`, title: `Open “${b.name}”`,
          onclick: (e) => e.stopPropagation() }, el("span", { class: `mini-thumb bg-${b.background}` }), el("span", { class: "mini-name" }, b.name)))
          : el("span", { class: "muted small" }, "No boards yet"),
        moreBoards > 0 && el("span", { class: "mini-more" }, `+${moreBoards}`)),
      el("div", { class: "row", style: "justify-content:space-between" },
        el("div", { class: "avatar-stack", title: s.members.map((m) => m.name).join(", ") },
          shownMembers.map((m) => avatar(m.user_id, `${m.name} (${m.role})`, 26)),
          s.member_count > shownMembers.length && el("span", { class: "avatar more" }, `+${s.member_count - shownMembers.length}`)),
        el("span", { class: "muted small" }, `${plural(s.board_count, "board")} · ${plural(s.member_count, "member")}`)));
  };
  main.replaceChildren(
    el("div", { class: "section-title", style: "margin-top:0" }, el("h1", { style: "margin:0" }, "Your workspaces"), create.button),
    create.form,
    el("div", { class: "grid", style: "margin-top:16px;grid-template-columns:repeat(auto-fill,minmax(320px,1fr))" },
      spaces.length ? spaces.map(card)
        : el("p", { class: "muted" }, "No workspaces yet. A workspace is your teaching team; inside it you create one board per course. Use “+ New workspace” above.")));
}

// --- operator view: every workspace on the platform (read-only; only for SUPERADMIN_EMAILS) ---------
function feedbackSection(items, reload) {
  const label = (f) => f.source === "web" ? "Website" : `via ${f.assistant || "AI assistant"}`;
  const card = (f) => el("div", { class: `card stack feedback-item ${f.done ? "is-done" : ""}` },
    el("div", { class: "row", style: "justify-content:space-between" },
      el("div", { class: "row", style: "gap:6px" },
        el("span", { class: `badge ${f.source !== "web" ? "owner" : ""}` }, label(f)),
        f.category ? el("span", { class: "badge" }, f.category.replace("_", " ")) : "",
        el("span", { class: "muted small" }, fmtDate(f.created_at), " · ", f.user_name || f.email || (f.user_id ? "logged-in user" : "anonymous"))),
      el("label", { class: "check small" }, el("input", { type: "checkbox", checked: f.done, onchange: (e) => busy(null, async () => {
        await api("PATCH", `/api/admin/feedback/${f.feedback_id}`, { done: e.target.checked }); reload(); }) }), " done")),
    f.summary ? el("strong", {}, f.summary) : "",
    el("div", { class: "feedback-text" }, f.message),
    Object.keys(f.context || {}).length ? el("div", { class: "row", style: "gap:4px" },
      ...Object.entries(f.context).map(([k, v]) => el("span", { class: "badge", title: k }, `${k.replace("_", " ")}: ${Array.isArray(v) ? v.join(", ") : v}`))) : "",
    f.page ? el("div", { class: "muted small" }, "Page: ", f.page) : "");
  const open = items.filter((f) => !f.done);
  return el("section", {},
    el("div", { class: "section-title" }, el("h2", { style: "margin:0" }, `Feedback (${open.length} open)`)),
    items.length ? el("div", { class: "stack" }, ...items.map(card)) : el("p", { class: "muted" }, "No feedback yet."));
}

async function adminView() {
  const [{ spaces }, { feedback }] = await Promise.all([api("GET", "/api/admin/spaces"), api("GET", "/api/admin/feedback")]);
  spaces.sort((a, b) => (b.last_activity_at || "").localeCompare(a.last_activity_at || ""));
  const row = (s) => el("tr", {},
    el("td", {}, el("strong", {}, s.name), el("div", { class: "muted small" }, s.space_id)),
    el("td", {}, s.members.map((m) => el("div", { class: "small" }, `${m.name} `, el("span", { class: "muted" }, `${m.email} · ${m.role}`)))),
    el("td", {}, s.board_count
      ? [...s.boards.map((b) => el("div", { class: "small" }, el("a", { href: `board.html?b=${b.board_id}` }, b.name))),
         s.board_count > s.boards.length ? el("div", { class: "muted small" }, `+${s.board_count - s.boards.length} more`) : ""]
      : el("span", { class: "muted small" }, "–")),
    el("td", { class: "small" }, s.last_activity_at ? el("span", { title: fmtDate(s.last_activity_at) }, timeAgo(s.last_activity_at)) : "–",
      s.last_activity_by ? el("div", { class: "muted" }, s.last_activity_by) : ""));
  main.replaceChildren(
    el("h1", { style: "margin-top:0" }, `All workspaces (${spaces.length})`),
    el("p", { class: "muted small" }, "Operator view, read-only. Only visible to accounts listed in SUPERADMIN_EMAILS with a confirmed email address."),
    el("div", { class: "card", style: "padding:4px 12px;overflow-x:auto" },
      el("table", {}, el("thead", {}, el("tr", {}, ["Workspace", "Members", "Boards", "Last activity"].map((h) => el("th", {}, h)))),
        el("tbody", {}, spaces.map(row)))),
    feedbackSection(feedback, () => adminView().catch(showError)));
}

// --- workspace files ---------------------------------------------------------------------------

// Opens a fresh upload link in a new tab (window opened synchronously so popup blockers don't interfere).
async function openUploadLink(spaceId) {
  const win = window.open("", "_blank");
  try {
    const { upload_link } = await api("POST", `/api/spaces/${spaceId}/upload-links`, {});
    if (win) win.location.href = upload_link; else location.href = upload_link;
  } catch (e) { win?.close(); showError(e); }
}

function putOnBoardDialog(spaceId, file, boards, done) {
  const board = el("select", {}, ...boards.map((b) => el("option", { value: b.board_id }, b.name)));
  const mode = el("select", {}, el("option", { value: "replace" }, "Replace the board's current slides (archived as PDF first)"),
    el("option", { value: "append" }, "Add after the current slides"));
  const status = el("div");
  const close = () => overlay.remove();
  const form = el("form", { class: "card stack dialog" },
    el("div", { class: "panel-head" }, el("h2", {}, "Put on a board"), el("button", { class: "ghost", type: "button", onclick: close }, "✕")),
    el("p", { class: "muted small" }, file.title || file.name, ` · ${file.pages} pages`),
    el("div", {}, el("label", {}, "Board"), board), el("div", {}, el("label", {}, "How"), mode), status,
    el("div", { class: "row", style: "justify-content:flex-end" }, el("button", { type: "button", onclick: close }, "Cancel"),
      el("button", { class: "primary", type: "submit" }, "Put on board")));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    busy(form.querySelector("button[type=submit]"), async () => {
      status.replaceChildren(el("p", { class: "muted small" }, "Converting pages to slides…"));
      try {
        await api("POST", `/api/spaces/${spaceId}/files/${file.file_id}/publish`, { board_id: board.value, mode: mode.value });
        close(); toast("Done — the board shows the new material"); done();
      } catch (err) { status.replaceChildren(el("div", { class: "alert" }, err.message)); }
    });
  });
  const overlay = el("div", { class: "dialog-backdrop", onclick: (e) => { if (e.target === overlay) close(); } }, form);
  document.body.append(overlay);
}

const FILES_PER_PAGE = 20;
const fileListState = {}; // per workspace: { q, filter, sort, page } — survives automatic refreshes
const naturalCompare = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" }).compare;

function filesSection(spaceId, files, boards, refresh) {
  const st = fileListState[spaceId] ||= { q: "", filter: "all", sort: "newest", page: 0 };
  const usedOn = (f) => [...new Set((f.published_to || []).map((u) => u.board))];
  const label = (f) => f.title || f.name;
  const scheduled = (f) => f.scheduled || [];
  const fmtShort = (iso) => new Date(iso).toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const metaBadges = (f) => {
    const m = f.meta || {};
    return [m.lecture_number != null && `Lecture ${m.lecture_number}`, m.topic, m.semester, ...(m.tags || [])]
      .filter(Boolean).map((t) => el("span", { class: "badge" }, t));
  };
  const row = (f) => el("tr", {},
    el("td", {}, el("strong", {}, label(f)), el("div", { class: "muted small row", style: "gap:4px" }, `${f.pages} page${f.pages === 1 ? "" : "s"}`, ...metaBadges(f))),
    el("td", { class: "muted small" }, el("span", { title: fmtDate(f.uploaded_at) }, timeAgo(f.uploaded_at)),
      f.uploaded_by ? el("div", {}, f.uploaded_by) : ""),
    el("td", { class: "small" },
      ...scheduled(f).map((x) => el("div", {}, x.status === "live" ? "● live on " : "⏱ scheduled: ", el("strong", {}, x.board),
        x.status === "live" ? "" : ` · ${fmtShort(x.starts_at)}`)),
      usedOn(f).length ? el("div", { class: "muted" }, "used on: ", usedOn(f).join(", ")) : "",
      !scheduled(f).length && !usedOn(f).length ? el("span", { class: "muted" }, "not used yet") : ""),
    el("td", { style: "text-align:right" }, cogMenu((close) => [
      menuItem("Put on a board…", () => { close(); boards.length ? putOnBoardDialog(spaceId, f, boards, refresh) : toast("Create a board first", "error"); }),
      menuItem("Download", async () => {
        close();
        const w = window.open("", "_blank");
        try { const { download_url } = await api("GET", `/api/spaces/${spaceId}/files/${f.file_id}/download`); w.location.href = download_url; }
        catch (e) { w?.close(); showError(e); }
      }),
      menuItem("Delete…", async () => {
        close();
        if (!confirm(`Delete “${label(f)}” from the workspace files? Boards that show it are not affected.`)) return;
        await api("DELETE", `/api/spaces/${spaceId}/files/${f.file_id}`).catch(showError); refresh();
      }, "danger"),
    ], "File actions")));

  const listBox = el("div");
  function renderList() {
    const words = st.q.toLowerCase().split(/\s+/).filter(Boolean);
    let shown = files.filter((f) => {
      const m = f.meta || {};
      const hay = `${label(f)} ${f.name} ${usedOn(f).join(" ")} ${scheduled(f).map((x) => x.board).join(" ")} ${m.topic || ""} ${m.semester || ""} ${(m.tags || []).join(" ")} ${m.lecture_number ?? ""}`.toLowerCase();
      if (!words.every((w) => hay.includes(w))) return false;
      if (st.filter === "scheduled") return scheduled(f).length > 0;
      if (st.filter === "used") return usedOn(f).length > 0;
      if (st.filter === "unused") return usedOn(f).length === 0 && scheduled(f).length === 0;
      return true;
    });
    if (st.sort === "name") shown.sort((x, y) => naturalCompare(label(x), label(y)));
    else if (st.sort === "used") shown.sort((x, y) => (y.published_to || []).length - (x.published_to || []).length);
    else shown.sort((x, y) => (y.uploaded_at || "").localeCompare(x.uploaded_at || ""));
    const pages = Math.max(1, Math.ceil(shown.length / FILES_PER_PAGE));
    st.page = Math.min(st.page, pages - 1);
    const start = st.page * FILES_PER_PAGE;
    const pageItems = shown.slice(start, start + FILES_PER_PAGE);
    const go = (delta) => { st.page += delta; renderList(); };
    listBox.replaceChildren(
      pageItems.length ? el("div", { class: "card", style: "padding:4px 12px" }, el("table", {}, el("tbody", {}, pageItems.map(row))))
        : el("p", { class: "muted" }, "No files match."),
      el("div", { class: "row pager" },
        el("span", { class: "muted small" }, shown.length ? `${start + 1}–${start + pageItems.length} of ${shown.length}` +
          (shown.length !== files.length ? ` (filtered from ${files.length})` : "") : ""),
        pages > 1 && el("span", { class: "spacer", style: "flex:1" }),
        pages > 1 && el("button", { class: "sm", type: "button", disabled: st.page === 0, onclick: () => go(-1) }, "‹ Previous"),
        pages > 1 && el("span", { class: "small" }, `Page ${st.page + 1} of ${pages}`),
        pages > 1 && el("button", { class: "sm", type: "button", disabled: st.page >= pages - 1, onclick: () => go(1) }, "Next ›")));
  }

  const search = el("input", { type: "search", placeholder: "Search files…", value: st.q, class: "files-search",
    oninput: (e) => { st.q = e.target.value; st.page = 0; renderList(); } });
  const opt = (value, text, current) => el("option", { value, selected: value === current }, text);
  const filter = el("select", { style: "width:auto", onchange: (e) => { st.filter = e.target.value; st.page = 0; renderList(); } },
    opt("all", "All files", st.filter), opt("scheduled", "Scheduled", st.filter), opt("used", "Used on a board", st.filter),
    opt("unused", "Not used or scheduled", st.filter));
  const sort = el("select", { style: "width:auto", onchange: (e) => { st.sort = e.target.value; st.page = 0; renderList(); } },
    opt("newest", "Newest first", st.sort), opt("name", "Name A–Z", st.sort), opt("used", "Most used", st.sort));
  if (files.length) renderList();

  const uploadBtn = el("button", { class: "ghost sm", type: "button", onclick: () => openUploadLink(spaceId) }, "+ Upload files");
  return el("section", { id: "files-section", "data-space": spaceId },
    el("div", { class: "section-title" }, el("h2", { style: "margin:0" }, "Files"), uploadBtn),
    el("p", { class: "muted small", style: "margin-top:-6px" }, "Lecture PDFs and syllabi shared by everyone in this workspace. Kept permanently, so you can reuse them next semester."),
    files.length ? el("div", { class: "row files-toolbar" }, search, filter, sort) : "",
    files.length ? listBox
      : el("p", { class: "muted" }, "No files yet. PDFs you put on a board are added here automatically, or use “+ Upload files”."));
}

// Re-render only the Files section (keeps open menus/inputs elsewhere on the page untouched).
async function reloadFiles() {
  const box = document.getElementById("files-section");
  if (!box || document.querySelector("#files-section .popover:not(.hidden), .dialog-backdrop")) return;
  if (box.contains(document.activeElement) && document.activeElement.matches("input, select")) return; // user is typing
  const spaceId = box.dataset.space;
  try {
    const [{ boards }, { files }] = await Promise.all([api("GET", `/api/spaces/${spaceId}`), api("GET", `/api/spaces/${spaceId}/files`)]);
    if (document.getElementById("files-section") !== box) return; // user navigated away meanwhile
    box.replaceWith(filesSection(spaceId, files, boards, () => spaceView(spaceId).catch(showError)));
  } catch { /* ignore: next refresh will try again */ }
}
if ("BroadcastChannel" in window) {
  new BroadcastChannel("muddypoints").onmessage = (e) => { if (e.data?.type === "files-changed") reloadFiles(); };
}
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") reloadFiles(); });

async function spaceView(spaceId) {
  const [{ space, boards }, members, { files }] = await Promise.all([
    api("GET", `/api/spaces/${spaceId}`), api("GET", `/api/spaces/${spaceId}/members`),
    api("GET", `/api/spaces/${spaceId}/files`)]);
  const isOwner = space.role === "owner";
  const me = session.user;
  const refresh = () => spaceView(spaceId).catch(showError);

  // header
  const title = el("h1", {}, space.name);
  const header = el("div", { class: "section-title", style: "margin-top:0" }, title, el("div", { class: "row" },
    el("button", { class: "sm", onclick: async () => {
      const name = prompt("New name for this workspace:", space.name);
      if (name) { await api("PATCH", `/api/spaces/${spaceId}`, { name }).catch(showError); refresh(); }
    } }, "Rename"),
    isOwner && el("button", { class: "sm danger", onclick: async () => {
      if (prompt(`This deletes the workspace with ALL boards, slides, comments and files.\nType the workspace name to confirm:`) !== space.name) return;
      await api("DELETE", `/api/spaces/${spaceId}`).then(() => (location.hash = "#/")).catch(showError);
    } }, "Delete workspace")));

  // boards
  const createBoard = inlineCreate("New board", "Board name, e.g. “Lecture 3”", async (name) => {
    await api("POST", `/api/spaces/${spaceId}/boards`, { name }); refresh();
  });
  const boardCards = boards.map((b) => {
    const thumb = el("a", { class: `board-thumb bg-${b.background || "plain"}`, href: `board.html?b=${b.board_id}`, title: "Open board" },
      el("span", { class: "name" }, b.name));
    const patch = async (fields) => { await api("PATCH", `/api/boards/${b.board_id}`, fields); };
    const menu = cogMenu((close) => [
      menuItem("Rename…", async () => {
        close();
        const name = prompt("Rename board:", b.name);
        if (name) { await patch({ name }).catch(showError); refresh(); }
      }),
      menuItem(b.locked ? "🔓 Reopen comments" : "🔒 Close comments", async () => { close(); await patch({ locked: !b.locked }).catch(showError); refresh(); }),
      menuItem("Delete board…", async () => {
        close();
        if (!confirm(`Delete board “${b.name}” with all slides and comments? Its link stops working.`)) return;
        await api("DELETE", `/api/boards/${b.board_id}`).catch(showError); refresh();
      }, "danger"),
      el("div", { class: "menu-label" }, "Background"),
      backgroundPicker(b.background, async (bg) => {
        await patch({ background: bg });
        thumb.className = `board-thumb bg-${bg}`;
        b.background = bg;
      }),
    ], "Board settings");
    return el("div", { class: "card board-card" }, thumb, menu, el("div", { class: "body" },
      b.next_switch_at && el("div", { class: "small" }, "⏱ Next material: ", el("strong", {}, fmtDate(b.next_switch_at))),
      el("div", { class: "row muted small" }, el("span", { title: fmtDate(b.last_activity_at) }, "Last activity ", timeAgo(b.last_activity_at),
        b.last_activity_by ? ` by ${b.last_activity_by}` : ""),
        b.locked && el("span", { class: "badge locked" }, "comments closed"))));
  });

  // members
  const inviteEmail = el("input", { type: "email", placeholder: "colleague@university.edu", required: true });
  const inviteRole = el("select", { style: "width:auto" }, el("option", { value: "editor" }, "Editor"), isOwner && el("option", { value: "owner" }, "Owner"));
  const inviteForm = el("form", { class: "row", style: "max-width:560px" }, inviteEmail, inviteRole, el("button", { class: "primary", type: "submit" }, "Invite"));
  inviteForm.addEventListener("submit", (e) => {
    e.preventDefault();
    busy(inviteForm.querySelector("button"), async () => {
      const r = await api("POST", `/api/spaces/${spaceId}/members`, { email: inviteEmail.value, role: inviteRole.value });
      toast(r.status === "added" ? "Added to the workspace" : "Invitation saved — they get access when they register with this email");
      refresh();
    });
  });
  const memberRows = members.members.map((m) => el("tr", {},
    el("td", {}, el("div", { class: "row", style: "flex-wrap:nowrap" }, avatar(m.user_id, m.name, 32),
      el("div", {}, el("strong", {}, m.name), el("div", { class: "muted small" }, m.email)))),
    el("td", {}, isOwner && m.user_id !== me.user_id
      ? (() => {
        const sel = el("select", { style: "width:auto", onchange: async () => {
          await api("PATCH", `/api/spaces/${spaceId}/members/${m.user_id}`, { role: sel.value }).catch(showError); refresh();
        } }, ...["editor", "owner"].map((r) => el("option", { value: r, selected: r === m.role }, r)));
        return sel;
      })()
      : el("span", { class: `badge ${m.role}` }, m.role)),
    el("td", { style: "text-align:right" }, (isOwner || m.user_id === me.user_id || m.role === "editor") && el("button", { class: "sm danger", onclick: async () => {
      const leaving = m.user_id === me.user_id;
      if (!confirm(leaving ? "Leave this workspace?" : `Remove ${m.name} from this workspace?`)) return;
      await api("DELETE", `/api/spaces/${spaceId}/members/${m.user_id}`).then(() => (leaving ? (location.hash = "#/") : refresh())).catch(showError);
    } }, m.user_id === me.user_id ? "Leave" : "Remove"))));
  const inviteRows = members.invites.map((i) => el("tr", {},
    el("td", {}, i.email, el("div", { class: "muted small" }, "invited — not registered yet")),
    el("td", {}, el("span", { class: "badge" }, i.role)),
    el("td", { style: "text-align:right" }, el("button", { class: "sm danger", onclick: async () => {
      await api("DELETE", `/api/spaces/${spaceId}/invites/${encodeURIComponent(i.email)}`).catch(showError); refresh();
    } }, "Revoke"))));

  main.replaceChildren(
    el("div", { class: "small" }, el("a", { href: "#/" }, "← All workspaces")),
    header,
    el("p", { class: "muted small" }, `You are ${space.role === "owner" ? "an owner" : "an editor"} of this workspace.`,
      space.role === "editor" ? " Editors can do everything except deleting the workspace and managing owners." : ""),
    el("div", { class: "section-title" }, el("h2", { style: "margin:0" }, "Boards"), createBoard.button),
    createBoard.form,
    boards.length ? el("div", { class: "grid", style: "margin-top:12px;grid-template-columns:repeat(auto-fill,minmax(260px,1fr))" }, boardCards)
      : el("p", { class: "muted" }, "No boards yet."),
    filesSection(spaceId, files, boards, refresh),
    el("div", { class: "section-title" }, el("h2", {}, "Members")),
    inviteForm,
    el("div", { class: "card", style: "margin-top:12px;padding:4px 12px" }, el("table", {}, el("tbody", {}, memberRows, inviteRows))));
}

// --- account ---------------------------------------------------------------------------------
async function accountView() {
  const [{ user }, { api_keys }] = await Promise.all([api("GET", "/api/me"), api("GET", "/api/me/api-keys")]);
  session.set(session.token, user);
  renderNav();
  const refresh = () => accountView().catch(showError);

  const nameInput = el("input", { type: "text", value: user.name, maxlength: 80 });
  const profile = el("form", { class: "card stack" },
    el("div", { class: "row", style: "flex-wrap:nowrap;gap:14px" }, avatar(user.user_id, user.name, 56),
      el("div", {}, el("h2", { style: "margin:0" }, user.name), el("div", { class: "muted small" }, "Your picture is generated from your account — everyone sees the same one."))),
    el("div", {}, el("label", {}, "Email"), el("div", { class: "muted" }, user.email, " ",
      user.email_verified ? el("span", {}, "✓ confirmed") : el("span", {}, "· not confirmed yet"))),
    user.email_verified ? "" : el("div", { class: "alert" }, "Please confirm your email address with the link we sent you. ",
      el("a", { href: "#", onclick: (e) => { e.preventDefault(); busy(e.target, async () => { await api("POST", "/api/me/verify-email"); toast("Verification email sent"); }); } }, "Send it again")),
    el("div", {}, el("label", {}, "Name"), nameInput),
    el("button", { class: "primary", type: "submit" }, "Save"));
  profile.addEventListener("submit", (e) => {
    e.preventDefault();
    busy(profile.querySelector("button"), async () => { const r = await api("PATCH", "/api/me", { name: nameInput.value }); session.set(session.token, r.user); renderNav(); toast("Saved"); });
  });

  const oldPw = el("input", { type: "password", required: true, autocomplete: "current-password" });
  const newPw = el("input", { type: "password", required: true, minlength: 8, autocomplete: "new-password" });
  const pwForm = el("form", { class: "card stack" }, el("h2", {}, "Change password"),
    el("div", {}, el("label", {}, "Current password"), oldPw),
    el("div", {}, el("label", {}, "New password (min. 8 characters)"), newPw),
    el("button", { class: "primary", type: "submit" }, "Change password"),
    el("p", { class: "muted small" }, "This logs you out on all other devices."));
  pwForm.addEventListener("submit", (e) => {
    e.preventDefault();
    busy(pwForm.querySelector("button"), async () => {
      const r = await api("POST", "/api/me/password", { old_password: oldPw.value, new_password: newPw.value });
      session.set(r.token, user); oldPw.value = newPw.value = ""; toast("Password changed");
    });
  });

  const copyBtn = (text, label = "Copy") => el("button", { class: "sm", type: "button", onclick: () => copyText(text) }, `📋 ${label}`);
  const newKeyBox = el("div", { id: "new-key" });
  const createBtn = el("button", { class: "primary", type: "button", onclick: () => busy(createBtn, async () => {
    const k = await api("POST", "/api/me/api-keys", { label: `AI assistant (${new Date().toLocaleDateString()})` });
    const url = `${API_ORIGIN}/mcp/${k.key}`;
    await refresh();
    document.getElementById("new-key").replaceChildren(el("div", { class: "alert ok stack" },
      el("strong", {}, "Your connector link (shown only once):"), el("div", { class: "code" }, url), copyBtn(url, "Copy link")));
  }) }, "Create connector link");
  const keyRows = api_keys.map((k) => el("tr", {},
    el("td", {}, el("strong", {}, k.label), el("div", { class: "muted small" }, `${k.prefix}…`)),
    el("td", { class: "muted small" }, k.last_used_at ? `last used ${fmtDate(k.last_used_at)}` : "never used"),
    el("td", { style: "text-align:right" }, el("button", { class: "sm danger", onclick: async () => {
      if (!confirm(`Revoke “${k.label}”? Assistants using it stop working.`)) return;
      await api("DELETE", `/api/me/api-keys/${k.key_id}`).catch(showError); refresh();
    } }, "Revoke"))));
  const steps = {
    claude: [
      el("li", {}, "In Claude (claude.ai or the desktop app): ", el("strong", {}, "Settings → Connectors → Add custom connector"), "."),
      el("li", {}, "Name ", el("code", {}, "Muddy Points"), " ", copyBtn("Muddy Points"), ", paste the link, click ", el("strong", {}, "Add"), "."),
      el("li", {}, "Start a new chat and ask: “Which boards do I have?”")],
    chatgpt: [
      el("li", {}, "In ChatGPT (web): ", el("strong", {}, "Settings → Apps / Connectors → Advanced"), ", switch on ", el("strong", {}, "Developer mode"), ". Needs Plus, Pro, Business, Enterprise or Edu; on Business/Enterprise an admin may have to allow it."),
      el("li", {}, el("strong", {}, "Create"), " an app: name ", el("code", {}, "Muddy Points"), " ", copyBtn("Muddy Points"), ", paste the link as MCP server URL, authentication ", el("strong", {}, "No authentication"), "."),
      el("li", {}, "In a new chat open ", el("strong", {}, "+ → Developer mode"), ", tick Muddy Points and ask: “Which boards do I have?” ChatGPT asks before it changes anything.")],
  };
  const stepList = el("ol", { class: "steps-list", start: 2 });
  const tabBtns = {};
  const showTab = (name) => {
    stepList.replaceChildren(...steps[name]);
    for (const [k, b] of Object.entries(tabBtns)) b.classList.toggle("active", k === name);
    try { localStorage.setItem("muddypoints.aiTab", name); } catch { /* ignore */ }
  };
  for (const [k, label] of [["claude", "Claude"], ["chatgpt", "ChatGPT"]]) {
    tabBtns[k] = el("button", { type: "button", onclick: () => showTab(k) }, label);
  }
  let savedTab = "claude";
  try { savedTab = localStorage.getItem("muddypoints.aiTab") || "claude"; } catch { /* ignore */ }
  showTab(steps[savedTab] ? savedTab : "claude");
  const claudeBox = el("div", { class: "card stack", style: "margin-top:16px" },
    el("h2", {}, "Use Muddy Points with AI assistants"),
    el("div", { class: "tabs", style: "margin-bottom:4px" }, ...Object.values(tabBtns)),
    el("ol", { class: "steps-list" }, el("li", {}, createBtn, newKeyBox)),
    stepList,
    el("p", { class: "muted small" }, "One link works for both. Keep it private. Lost it? Revoke it and create a new one."),
    api_keys.length ? el("details", {}, el("summary", { class: "small" }, `Your connector links (${api_keys.length})`),
      el("table", {}, el("tbody", {}, keyRows))) : "");

  main.replaceChildren(el("h1", {}, "Account"),
    el("div", { class: "grid", style: "grid-template-columns:repeat(auto-fit,minmax(300px,1fr))" }, profile, pwForm),
    claudeBox);
}

// --- router ----------------------------------------------------------------------------------
async function route() {
  renderNav();
  if (!session.token) return authView();
  const hash = location.hash.replace(/^#\/?/, "");
  const [view, id] = hash.split("/");
  try {
    if (view === "space" && id) await spaceView(id);
    else if (view === "account") await accountView();
    else if (view === "admin") await adminView();
    else await spacesView();
  } catch (e) {
    if (!session.token) return authView();
    main.replaceChildren(el("div", { class: "alert" }, e.message), el("p", {}, el("a", { href: "#/" }, "← Back to your workspaces")));
  }
}

window.addEventListener("hashchange", route);
route();
