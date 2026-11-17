// Dashboard SPA (hash routing): #/ spaces, #/space/<id>, #/account. Login/registration when logged out.
import { api, session, el, toast, showError, busy, copyText, fmtDate, API_ORIGIN } from "./api.js";

const main = document.getElementById("main");
const nav = document.getElementById("nav");

function boardLink(boardId) {
  return new URL(`board.html?b=${boardId}`, location.href).href;
}

function renderNav() {
  const user = session.user;
  nav.replaceChildren(...(user ? [
    el("a", { href: "#/" }, "Spaces"),
    el("a", { href: "#/account" }, user.name),
    el("button", { class: "ghost sm", onclick: () => { session.clear(); location.hash = "#/"; route(); } }, "Log out"),
  ] : []));
}

// --- auth screens ----------------------------------------------------------------------------
function authView(tab = "login") {
  const msg = el("div");
  const field = (id, label, type = "text", extra = {}) =>
    el("div", {}, el("label", { for: id }, label), el("input", { id, type, required: true, ...extra }));
  const forms = {
    login: el("form", { class: "stack" },
      field("email", "Email", "email", { autocomplete: "email" }),
      field("password", "Password", "password", { autocomplete: "current-password" }),
      msg, el("button", { class: "primary", type: "submit" }, "Log in"),
      el("div", { class: "small" }, el("a", { href: "#", onclick: (e) => { e.preventDefault(); show("forgot"); } }, "Forgot your password?"))),
    register: el("form", { class: "stack" },
      field("name", "Your name (shown on your comments)", "text", { autocomplete: "name", maxlength: 80 }),
      field("email", "Email", "email", { autocomplete: "email" }),
      field("password", "Password (min. 8 characters)", "password", { autocomplete: "new-password", minlength: 8 }),
      msg, el("button", { class: "primary", type: "submit" }, "Create account")),
    forgot: el("form", { class: "stack" },
      el("p", { class: "muted small" }, "We'll email you a link to choose a new password."),
      field("email", "Email", "email", { autocomplete: "email" }),
      msg, el("button", { class: "primary", type: "submit" }, "Send reset link")),
  };
  const tabs = el("div", { class: "tabs" });
  const holder = el("div");
  function show(name) {
    msg.replaceChildren();
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
          msg.replaceChildren(el("div", { class: "alert ok" }, "If an account exists for this email, a reset link is on its way."));
        } else {
          const body = name === "login" ? { email: v("email"), password: v("password") }
            : { name: v("name"), email: v("email"), password: v("password") };
          const res = await api("POST", `/api/auth/${name}`, body);
          session.set(res.token, res.user);
          route();
        }
      } catch (err) {
        msg.replaceChildren(el("div", { class: "alert" }, err.message));
      } finally { btn.disabled = false; }
    });
  }
  show(tab);
  main.replaceChildren(el("div", { class: "container narrow", style: "padding-top:24px" },
    el("h1", {}, "Course Boards"),
    el("p", { class: "muted" }, "Share lecture slides with students and collect their questions. Staff accounts only — students just open the board link."),
    el("div", { class: "card" }, tabs, holder)));
}

// --- spaces ----------------------------------------------------------------------------------
async function spacesView() {
  const { spaces } = await api("GET", "/api/spaces");
  const input = el("input", { type: "text", placeholder: "New space, e.g. “Statistics 101 – WS 26/27”", maxlength: 120 });
  const create = el("form", { class: "row", style: "max-width:560px" }, input, el("button", { class: "primary", type: "submit" }, "Create space"));
  create.addEventListener("submit", (e) => {
    e.preventDefault();
    busy(create.querySelector("button"), async () => {
      const { space } = await api("POST", "/api/spaces", { name: input.value });
      location.hash = `#/space/${space.space_id}`;
    });
  });
  main.replaceChildren(
    el("h1", {}, "Your spaces"),
    create,
    el("div", { class: "grid", style: "margin-top:20px" },
      spaces.length ? spaces.map((s) => el("div", { class: "card link", onclick: () => (location.hash = `#/space/${s.space_id}`) },
        el("h3", {}, s.name), el("span", { class: `badge ${s.role}` }, s.role)))
        : el("p", { class: "muted" }, "No spaces yet. A space usually is one course; inside it you create one board per class.")));
}

async function spaceView(spaceId) {
  const [{ space, boards }, members] = await Promise.all([
    api("GET", `/api/spaces/${spaceId}`), api("GET", `/api/spaces/${spaceId}/members`)]);
  const isOwner = space.role === "owner";
  const me = session.user;
  const refresh = () => spaceView(spaceId).catch(showError);

  // header
  const title = el("h1", {}, space.name);
  const header = el("div", { class: "section-title", style: "margin-top:0" }, title, el("div", { class: "row" },
    el("button", { class: "sm", onclick: async () => {
      const name = prompt("New name for this space:", space.name);
      if (name) { await api("PATCH", `/api/spaces/${spaceId}`, { name }).catch(showError); refresh(); }
    } }, "Rename"),
    isOwner && el("button", { class: "sm danger", onclick: async () => {
      if (prompt(`This deletes the space with ALL boards, slides and comments.\nType the space name to confirm:`) !== space.name) return;
      await api("DELETE", `/api/spaces/${spaceId}`).then(() => (location.hash = "#/")).catch(showError);
    } }, "Delete space")));

  // boards
  const boardName = el("input", { type: "text", placeholder: "New board, e.g. “Lecture 3”", maxlength: 120 });
  const boardForm = el("form", { class: "row", style: "max-width:560px" }, boardName, el("button", { class: "primary", type: "submit" }, "Create board"));
  boardForm.addEventListener("submit", (e) => {
    e.preventDefault();
    busy(boardForm.querySelector("button"), async () => { await api("POST", `/api/spaces/${spaceId}/boards`, { name: boardName.value }); refresh(); });
  });
  const boardRows = boards.map((b) => el("tr", {},
    el("td", {}, el("a", { href: `board.html?b=${b.board_id}` }, el("strong", {}, b.name)), " ", b.locked && el("span", { class: "badge locked" }, "locked")),
    el("td", { class: "muted small" }, fmtDate(b.updated_at)),
    el("td", { class: "row", style: "justify-content:flex-end" },
      el("button", { class: "sm", onclick: () => copyText(boardLink(b.board_id)) }, "Copy link"),
      el("button", { class: "sm", onclick: async () => {
        const name = prompt("Rename board:", b.name);
        if (name) { await api("PATCH", `/api/boards/${b.board_id}`, { name }).catch(showError); refresh(); }
      } }, "Rename"),
      el("button", { class: "sm", onclick: async () => { await api("PATCH", `/api/boards/${b.board_id}`, { locked: !b.locked }).catch(showError); refresh(); } },
        b.locked ? "Unlock" : "Lock"),
      isOwner && el("button", { class: "sm danger", onclick: async () => {
        if (!confirm(`Delete board “${b.name}” with all slides and comments? Its link stops working.`)) return;
        await api("DELETE", `/api/boards/${b.board_id}`).catch(showError); refresh();
      } }, "Delete"))));

  // members
  const inviteEmail = el("input", { type: "email", placeholder: "colleague@university.edu", required: true });
  const inviteRole = el("select", { style: "width:auto" }, el("option", { value: "editor" }, "Editor"), isOwner && el("option", { value: "owner" }, "Owner"));
  const inviteForm = el("form", { class: "row", style: "max-width:560px" }, inviteEmail, inviteRole, el("button", { class: "primary", type: "submit" }, "Invite"));
  inviteForm.addEventListener("submit", (e) => {
    e.preventDefault();
    busy(inviteForm.querySelector("button"), async () => {
      const r = await api("POST", `/api/spaces/${spaceId}/members`, { email: inviteEmail.value, role: inviteRole.value });
      toast(r.status === "added" ? "Added to the space" : "Invitation saved — they get access when they register with this email");
      refresh();
    });
  });
  const memberRows = members.members.map((m) => el("tr", {},
    el("td", {}, el("strong", {}, m.name), el("div", { class: "muted small" }, m.email)),
    el("td", {}, isOwner && m.user_id !== me.user_id
      ? (() => {
        const sel = el("select", { style: "width:auto", onchange: async () => {
          await api("PATCH", `/api/spaces/${spaceId}/members/${m.user_id}`, { role: sel.value }).catch(showError); refresh();
        } }, ...["editor", "owner"].map((r) => el("option", { value: r, selected: r === m.role }, r)));
        return sel;
      })()
      : el("span", { class: `badge ${m.role}` }, m.role)),
    el("td", { style: "text-align:right" }, (isOwner || m.user_id === me.user_id) && el("button", { class: "sm danger", onclick: async () => {
      const leaving = m.user_id === me.user_id;
      if (!confirm(leaving ? "Leave this space?" : `Remove ${m.name} from this space?`)) return;
      await api("DELETE", `/api/spaces/${spaceId}/members/${m.user_id}`).then(() => (leaving ? (location.hash = "#/") : refresh())).catch(showError);
    } }, m.user_id === me.user_id ? "Leave" : "Remove"))));
  const inviteRows = members.invites.map((i) => el("tr", {},
    el("td", {}, i.email, el("div", { class: "muted small" }, "invited — not registered yet")),
    el("td", {}, el("span", { class: "badge" }, i.role)),
    el("td", { style: "text-align:right" }, isOwner && el("button", { class: "sm danger", onclick: async () => {
      await api("DELETE", `/api/spaces/${spaceId}/invites/${encodeURIComponent(i.email)}`).catch(showError); refresh();
    } }, "Revoke"))));

  main.replaceChildren(
    el("div", { class: "small" }, el("a", { href: "#/" }, "← All spaces")),
    header,
    el("p", { class: "muted small" }, `You are ${space.role === "owner" ? "an owner" : "an editor"} of this space.`,
      space.role === "editor" ? " Editors can do everything except deleting and managing roles." : ""),
    el("div", { class: "section-title" }, el("h2", {}, "Boards")),
    boardForm,
    el("div", { class: "card", style: "margin-top:12px;padding:4px 12px" },
      boards.length ? el("table", {}, el("tbody", {}, boardRows)) : el("p", { class: "muted" }, "No boards yet.")),
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
  const profile = el("form", { class: "card stack" }, el("h2", {}, "Profile"),
    el("div", {}, el("label", {}, "Email"), el("div", { class: "muted" }, user.email)),
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

  const newKeyBox = el("div");
  const label = el("input", { type: "text", placeholder: "Label, e.g. “Claude Web”", maxlength: 60 });
  const keyForm = el("form", { class: "row" }, label, el("button", { class: "primary", type: "submit" }, "Create key"));
  keyForm.addEventListener("submit", (e) => {
    e.preventDefault();
    busy(keyForm.querySelector("button"), async () => {
      const k = await api("POST", "/api/me/api-keys", { label: label.value || "Claude" });
      const url = `${API_ORIGIN}/mcp/${k.key}`;
      await refresh();
      document.getElementById("new-key").replaceChildren(el("div", { class: "alert ok stack" },
        el("strong", {}, "Your MCP connector URL — copy it now, it is shown only once:"),
        el("div", { class: "code" }, url),
        el("button", { class: "sm", type: "button", onclick: () => copyText(url) }, "Copy URL")));
    });
  });
  newKeyBox.id = "new-key";
  const keyRows = api_keys.map((k) => el("tr", {},
    el("td", {}, el("strong", {}, k.label), el("div", { class: "muted small" }, `${k.prefix}…`)),
    el("td", { class: "muted small" }, "created ", fmtDate(k.created_at), el("br"), k.last_used_at ? `last used ${fmtDate(k.last_used_at)}` : "never used"),
    el("td", { style: "text-align:right" }, el("button", { class: "sm danger", onclick: async () => {
      if (!confirm(`Revoke “${k.label}”? Claude connectors using it stop working.`)) return;
      await api("DELETE", `/api/me/api-keys/${k.key_id}`).catch(showError); refresh();
    } }, "Revoke"))));

  main.replaceChildren(el("h1", {}, "Account"),
    el("div", { class: "grid", style: "grid-template-columns:repeat(auto-fit,minmax(300px,1fr))" }, profile, pwForm),
    el("div", { class: "card stack", style: "margin-top:16px" },
      el("h2", {}, "Claude / MCP access"),
      el("p", { class: "muted small" }, "Create a key, then in Claude (Settings → Connectors → Add custom connector) paste the connector URL. Claude can then manage your spaces and boards with your permissions. Treat the URL like a password."),
      keyForm, newKeyBox,
      api_keys.length ? el("table", {}, el("tbody", {}, keyRows)) : el("p", { class: "muted small" }, "No keys yet.")));
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
    else await spacesView();
  } catch (e) {
    if (!session.token) return authView();
    main.replaceChildren(el("div", { class: "alert" }, e.message), el("p", {}, el("a", { href: "#/" }, "← Back to your spaces")));
  }
}

window.addEventListener("hashchange", route);
route();
