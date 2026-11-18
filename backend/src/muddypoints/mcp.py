"""Remote MCP server (Streamable HTTP transport, stateless, JSON responses).

Works with any MCP client: Claude (claude.ai, desktop, Claude Code) and ChatGPT (Developer Mode apps).
Connector URL:  <FunctionUrl>/mcp/<API key>   (or POST <FunctionUrl>/mcp with `Authorization: Bearer <API key>`).
API keys are created per user in the web UI (Account -> "Use Muddy Points with AI assistants"). Every tool runs with
that user's permissions, so the owner/editor rules apply exactly as in the web UI.

To add a tool: write a function `(user, args) -> JSON-serialisable` and decorate it with @tool.
Keep business logic in the service modules (boards.py, spaces.py, ...) — tools are thin wrappers.
"""
import contextvars
import json
import logging
from typing import Any, Callable

from . import __version__, applogs, boards, export, identity, library, media, notify, schedule, spaces, uploads, users
from .http import ApiError, Request, json_response
from .log import bind, get_logger, log_event

logger = get_logger(__name__)
SUPPORTED_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
INSTRUCTIONS = """Admin interface of the course feedback boards (Muddy Points).
Hierarchy: workspace (a teaching team; members + shared files) -> boards (one per course, ONE public link for the
whole semester whose content changes) -> slides (images) -> comments/likes.
Students comment anonymously on slides via the public board URL.
Typical weekly workflow for a board: export_board_pdf (save the comments for OLAT) and then
replace_board_material with the new lecture PDF (this archives a PDF of the old comments automatically).
Owners can do everything; editors everything except deleting a workspace and managing owners. IDs come from list_* tools.
Files: every workspace has permanent, shared FILES (lecture PDFs, syllabi; reused across semesters).
- The user wants to give you files (attached in the chat, on their computer, several lectures to choose from):
  create_upload_link for the workspace and give them the link (no login; they drag PDFs in; duplicates are detected
  automatically). Do NOT try to upload attached files from your code sandbox; it is usually blocked.
- Then list_files (compact overview: title, pages, outline, where it was used before), read_file only where you need
  details (page ranges, paginated), and publish_file to put a file on a board (mode=replace for the weekly swap;
  archives the old slides+comments first).
- "What did we use before / put up week 10 again": get_board_history and list_files (published_to), then publish_file.
New material from elsewhere:
- The user gives a link (Google Drive/Dropbox share link or any public URL): use replace_board_material (weekly swap)
  or import_slides_from_url (add).
- The user attaches a file in the chat, or has the file on their computer: call get_upload_link (replace=true for a
  weekly swap) and give them the link. Do NOT try to upload attached files from a code sandbox; it is usually blocked.
  The link opens the board with the upload panel ready; they drop the file there and are done.
- prepare_slide_upload/confirm_slide_upload are only for clients with unrestricted network access (e.g. Claude Code).
Schedules (a board = one course, one URL for the whole semester, content changes per session):
- get_schedule shows the board's sessions (each = a workspace file + switch time, status planned/live/done/failed).
  At each switch time the system archives the current slides+comments as PDF, puts the session's file up, opens
  comments again and emails all workspace staff. Before the first session the board shows "coming soon".
- To plan a semester from a syllabus: read the syllabus (read_file) and the lecture files (list_files, read_file as
  needed), map files to sessions, then PROPOSE the schedule to the user as a table (date, time, title, file) and only
  call set_schedule after they confirm. Conventions unless the user says otherwise:
  * Material for a lecture goes live right AFTER the previous lecture ENDS (e.g. lectures Tue 14:00-16:00 ->
    switch Tue 16:00), so students have it a full week before their next class. The first session goes live when the
    user wants (ask; default: one week before the first lecture at the same time).
  * Times are in the board's time zone (default Europe/Berlin); pass local times like 2026-10-20T16:00.
  * Skip holidays/no-lecture weeks found in the syllabus; one session per lecture; titles like "Lecture 3: Topic".
  * If lecture times are missing in the syllabus, ask the user instead of guessing.
- view_file_page shows you a page of a file as an image (for scanned PDFs, diagrams, formulas).
Comments: list_comments returns all comments incl. replies (parent_id) and status (answered/pinned/hidden).
- reply_to_comment posts IN THE USER'S NAME (students see it as the lecturer's answer). NEVER reply on your own
  initiative. You may offer to help and draft answers, but only post when the user explicitly asks you to answer
  ("please answer the student by explaining X"); if you drafted the text yourself, show it and get an OK first.
- set_comment_status: answered (✓ shown to students), pinned (top of the slide), hidden (students don't see it).
Feedback to the Muddy Points developers (send_feedback): when you hit a limitation of Muddy Points itself - a
missing tool, confusing or surprising behaviour, an error, a workflow that needs too many steps - propose to report
it. Tell the user what you would send and send it once they agree (or right away if they asked you to report it).
One report per problem, concrete: what you tried, what happened, what would have helped. Never include student
comment texts, names, emails or other personal data. Optionally ask the user once whether you may add context about
their teaching (role, discipline, course size, ...): include ONLY what they confirmed or explicitly said - never
guess or infer it.
Testing safely: tools that delete things, post in the user's name or email other people accept dry_run=true - all
checks run and you get a description of what would happen, nothing changes. Use it when exploring or when unsure.
For tests with real effects, create a separate test board instead of touching real courses."""

# User-Agent of the current MCP request (e.g. 'Claude-User', 'openai-mcp/1.0'), so feedback knows which assistant sent it
CLIENT_AGENT: contextvars.ContextVar[str] = contextvars.ContextVar("mcp_client_agent", default="")

ToolFn = Callable[[dict, dict], Any]
TOOLS: dict[str, dict] = {}


def tool(name: str, description: str, properties: dict | None = None, required: tuple = (),
         read_only: bool = False, destructive: bool = False):
    def decorator(fn: ToolFn) -> ToolFn:
        TOOLS[name] = {
            "fn": fn,
            "spec": {
                "name": name,
                "description": description,
                "inputSchema": {"type": "object", "properties": properties or {}, "required": list(required)},
                "annotations": {"readOnlyHint": read_only, "destructiveHint": destructive},
            },
        }
        return fn
    return decorator


S = {"type": "string"}
SPACE = {"workspace_id": {**S, "description": "Workspace id (from list_workspaces)"}}
DRY = {"dry_run": {"type": "boolean", "default": False,
                   "description": "Check everything (permissions, ids, rules) and report what WOULD happen; change nothing"}}
BOARD = {"board_id": {**S, "description": "Board id (from list_boards)"}}
MODE = {"mode": {"type": "string", "enum": ["append", "replace"], "default": "replace",
                 "description": "append = add after existing slides; replace = remove all old slides, comments "
                                "and likes"},
        "archive": {"type": "boolean", "default": True,
                    "description": "With mode=replace: first save a PDF of the old slides+comments to the archive"}}


# --- account & spaces -----------------------------------------------------------------------

@tool("whoami", "Show the user this API key belongs to and their workspaces.", read_only=True)
def _whoami(user, a):
    return {"user": users.public_user(user), "workspaces": spaces.list_spaces(user)}


@tool("list_workspaces", "List the workspaces you are a member of, with your role, boards and members.", read_only=True)
def _list_workspaces(user, a):
    return spaces.list_spaces(user)


@tool("create_workspace", "Create a new workspace (you become its owner).", {"name": S}, ("name",))
def _create_workspace(user, a):
    return spaces.create_space(user, a.get("name"))


@tool("rename_workspace", "Rename a workspace.", {**SPACE, "name": S}, ("workspace_id", "name"))
def _rename_workspace(user, a):
    return spaces.rename_space(user, a["workspace_id"], a.get("name"))


@tool("delete_workspace", "Permanently delete a workspace with ALL its boards, slides, comments and files (owners only).",
      {**SPACE, **DRY}, ("workspace_id",), destructive=True)
def _delete_workspace(user, a):
    if a.get("dry_run"):
        return spaces.delete_space(user, a["workspace_id"], dry_run=True)
    spaces.delete_space(user, a["workspace_id"])
    return {"deleted": a["workspace_id"]}


@tool("list_members", "List members (with roles) and pending invites of a workspace.", SPACE, ("workspace_id",),
      read_only=True)
def _list_members(user, a):
    return spaces.list_members(user, a["workspace_id"])


@tool("invite_member", "Share a workspace with someone by email. Existing users are added immediately, others get "
      "access when they register with that email.",
      {**SPACE, "email": S, "role": {"type": "string", "enum": ["editor", "owner"], "default": "editor"}, **DRY},
      ("workspace_id", "email"))
def _invite(user, a):
    return spaces.invite(user, a["workspace_id"], a.get("email"), a.get("role", "editor"), dry_run=bool(a.get("dry_run")))


@tool("set_member_role", "Change a member's role (owners only).",
      {**SPACE, "user_id": S, "role": {"type": "string", "enum": ["editor", "owner"]}, **DRY},
      ("workspace_id", "user_id", "role"))
def _set_role(user, a):
    res = spaces.set_role(user, a["workspace_id"], a["user_id"], a.get("role"), dry_run=bool(a.get("dry_run")))
    return res or {"ok": True}


@tool("remove_member", "Remove a member from a workspace (only owners can remove owners; anyone can remove themselves).",
      {**SPACE, "user_id": S, **DRY}, ("workspace_id", "user_id"), destructive=True)
def _remove_member(user, a):
    res = spaces.remove_member(user, a["workspace_id"], a["user_id"], dry_run=bool(a.get("dry_run")))
    return res or {"ok": True}


# --- boards ---------------------------------------------------------------------------------

@tool("list_boards", "List the boards of a workspace incl. their public URLs.", SPACE, ("workspace_id",), read_only=True)
def _list_boards(user, a):
    return boards.list_boards(user, a["workspace_id"])


BACKGROUND = {"background": {"type": "string", "enum": list(boards.BACKGROUNDS),
                              "description": "Background texture of the board page"}}


@tool("create_board", "Create a board (usually one per course) in a workspace. Returns its public URL.", {**SPACE, "name": S, **BACKGROUND},
      ("workspace_id", "name"))
def _create_board(user, a):
    return boards.create_board(user, a["workspace_id"], a.get("name"), a.get("background"))


@tool("get_board", "Board overview: settings, totals and slides (number, id, source file, likes, comments). By default "
      "only slides with likes or comments are listed (compact); compact=false lists every slide.",
      {**BOARD, "compact": {"type": "boolean", "default": True}}, ("board_id",), read_only=True)
def _get_board(user, a):
    boards.require_board(user, a["board_id"], "editor")
    v = boards.view(a["board_id"], identity.viewer_from_user(user))
    slides = [{"number": i + 1, "slide_id": s["slide_id"], "source": s["source"], "likes": s["likes"],
               "comments": len(s["comments"]) + sum(len(c.get("replies", [])) for c in s["comments"])}
              for i, s in enumerate(v["slides"])]
    compact = a.get("compact", True) is not False
    shown = [s for s in slides if s["likes"] or s["comments"]] if compact else slides
    return {"board": v["board"], "your_role": v["my_role"],
            "totals": {"slides": len(slides), "slides_with_activity": len([s for s in slides if s["likes"] or s["comments"]]),
                       "comments": sum(s["comments"] for s in slides), "likes": sum(s["likes"] for s in slides)},
            "slides": shown, **({"note": "Only slides with likes/comments are listed; compact=false shows all."}
                                if compact and len(shown) < len(slides) else {})}


@tool("update_board", "Rename a board, lock it (locked = students can no longer comment or like), change its "
      "background texture and/or its time zone (IANA name, default Europe/Berlin).",
      {**BOARD, "name": S, "locked": {"type": "boolean"}, **BACKGROUND, "timezone": S}, ("board_id",))
def _update_board(user, a):
    return boards.update_board(user, a["board_id"], a.get("name"), a.get("locked"), a.get("background"),
                               a.get("timezone"))


@tool("delete_board", "Permanently delete a board incl. slides and comments. Its URL stops working.",
      {**BOARD, **DRY}, ("board_id",), destructive=True)
def _delete_board(user, a):
    res = boards.delete_board(user, a["board_id"], dry_run=bool(a.get("dry_run")))
    return res or {"deleted": a["board_id"]}


@tool("list_comments", "All comments of a board, oldest first, with the slide number they belong to.", BOARD,
      ("board_id",), read_only=True)
def _list_comments(user, a):
    return boards.list_comments(user, a["board_id"])


@tool("reply_to_comment", "Post a reply under a student's comment, IN THE USER'S NAME (shown as staff). Only when the "
      "user explicitly asked for it (see instructions). Marks the question as answered.",
      {**BOARD, "comment_id": S, "text": S, **DRY}, ("board_id", "comment_id", "text"))
def _reply(user, a):
    return boards.add_comment(a["board_id"], None, a.get("text"), identity.viewer_from_user(user), a["comment_id"],
                              dry_run=bool(a.get("dry_run")))


@tool("set_comment_status", "Mark a comment answered (✓ visible to students), pin it (top of its slide) or hide it "
      "(invisible to students, still visible to staff and in exports).",
      {**BOARD, "comment_id": S, "answered": {"type": "boolean"}, "pinned": {"type": "boolean"},
       "hidden": {"type": "boolean"}}, ("board_id", "comment_id"))
def _comment_status(user, a):
    return boards.set_comment_status(user, a["board_id"], a["comment_id"], a.get("answered"), a.get("pinned"),
                                     a.get("hidden"))


@tool("delete_comment", "Delete a comment (and its replies).", {**BOARD, "comment_id": S, **DRY},
      ("board_id", "comment_id"), destructive=True)
def _delete_comment(user, a):
    boards.require_board(user, a["board_id"], "editor")
    res = boards.delete_comment(a["board_id"], a["comment_id"], identity.viewer_from_user(user),
                                dry_run=bool(a.get("dry_run")))
    return res or {"ok": True}


# --- slides ---------------------------------------------------------------------------------

@tool("import_slides_from_url",
      "Download a PDF or image from a URL (direct link, or a share link from Google Drive, Dropbox, Seafile, Nextcloud/sciebo) and turn it into "
      "slides (one per PDF page).", {**BOARD, "url": S, **MODE}, ("board_id", "url"))
def _import_url(user, a):
    return uploads.import_from_url(user, a["board_id"], a.get("url"), a.get("mode", "replace"), a.get("archive", True))


@tool("replace_board_material",
      "Weekly rotation: archive a PDF of the current slides+comments, remove them, and load new material from a URL "
      "(PDF/image). The board URL stays the same. Owners only.", {**BOARD, "url": S}, ("board_id", "url"),
      destructive=True)
def _replace(user, a):
    return uploads.import_from_url(user, a["board_id"], a.get("url"), "replace", True)


@tool("get_upload_link",
      "Link for the user to upload a PDF/images to a board themselves (opens the board with the upload panel ready). "
      "Use this whenever the user attaches a file in the chat or wants to upload a file from their computer.",
      {**BOARD, "replace": {"type": "boolean", "default": False,
                            "description": "Preselect 'Replace all slides' (weekly swap; old slides and "
                                           "comments are archived as PDF first)"}}, ("board_id",), read_only=True)
def _upload_link(user, a):
    board, _ = boards.require_board(user, a["board_id"], "editor")
    replace = bool(a.get("replace"))
    url = boards.board_url(board["board_id"]) + ("#upload-replace" if replace else "#upload")
    return {"upload_link": url, "board": board["name"], "mode": "replace" if replace else "append",
            "tell_the_user": f"Open this link, log in if asked, and drop your PDF into the upload panel: {url}"
                             + (" ('Replace all slides' is preselected; the current slides and comments are "
                                "archived as PDF first.)" if replace else "")}


@tool("prepare_slide_upload",
      "Only for clients with unrestricted network access (e.g. Claude Code), NOT for chat sandboxes (claude.ai, ChatGPT) - use "
      "get_upload_link there. Step 1 of a direct upload: returns presigned S3 PUT URLs (valid 1h). PUT each file's "
      "bytes with the given Content-Type header, then call confirm_slide_upload with the upload_keys.",
      {**BOARD, "files": {"type": "array", "items": {"type": "object", "properties": {
          "filename": S, "content_type": {"type": "string", "description": "e.g. application/pdf, image/png"}},
          "required": ["filename", "content_type"]}}}, ("board_id", "files"))
def _prepare(user, a):
    host = media.upload_host()
    page = boards.board_url(a["board_id"]) + "#upload"
    return {"uploads": uploads.prepare(user, a["board_id"], a.get("files")), "upload_host": host,
            "upload_page_url": page,
            "if_upload_blocked": f"If your sandbox's network policy blocks {host}: give the user this link to upload the "
                                 f"file themselves in one step: {page} (if they want to replace the slides, they choose "
                                 "'Replace all slides' there). A public share link of the file also works "
                                 "(import_slides_from_url / replace_board_material)."}


@tool("confirm_slide_upload", "Step 2 of a direct upload: convert uploaded files into slides (in the given order).",
      {**BOARD, "upload_keys": {"type": "array", "items": S}, **MODE}, ("board_id", "upload_keys"))
def _confirm(user, a):
    return uploads.confirm(user, a["board_id"], a.get("upload_keys"), a.get("mode", "replace"), a.get("archive", True))


@tool("reorder_slides", "Set the slide order. Pass every slide_id of the board exactly once.",
      {**BOARD, "slide_ids": {"type": "array", "items": S}}, ("board_id", "slide_ids"))
def _reorder(user, a):
    return boards.reorder_slides(user, a["board_id"], a.get("slide_ids"))


@tool("delete_slide", "Delete one slide with its comments.", {**BOARD, "slide_id": S, **DRY},
      ("board_id", "slide_id"), destructive=True)
def _delete_slide(user, a):
    res = boards.delete_slide(user, a["board_id"], a["slide_id"], dry_run=bool(a.get("dry_run")))
    return res or {"ok": True}


@tool("clear_board", "Remove all slides, comments and likes from a board. By default an archive PDF "
      "is saved first (only if there are comments).", {**BOARD, "archive": {"type": "boolean", "default": True}, **DRY},
      ("board_id",), destructive=True)
def _clear(user, a):
    return export.clear_board(user, a["board_id"], a.get("archive", True), dry_run=bool(a.get("dry_run")))


# --- workspace files ------------------------------------------------------------------------

@tool("create_upload_link",
      "Temporary upload page for a workspace (no login needed): the user drags in PDFs, which are stored permanently "
      "in the workspace's files (not on a board; duplicates are recognised). Use whenever the user wants to give you "
      "files. Afterwards call list_files.",
      {**SPACE, "hours": {"type": "integer", "default": 24, "description": "How long the LINK works, 1-168 hours"},
       "note": {"type": "string", "description": "Short text shown on the upload page, e.g. 'Lectures 4-6'"}},
      ("workspace_id",))
def _create_link(user, a):
    return library.create_link(user, a["workspace_id"], a.get("hours", 24), a.get("note"))


@tool("list_files", "The workspace's files (newest first): name, title, page count, outline, metadata (lecture_number, "
      "topic, semester, tags, notes), where each file was published before and where it is scheduled. Cheap overview - "
      "read details with read_file / view_file_pages. Optional filters.",
      {**SPACE, "query": {"type": "string", "description": "Words that must appear in name/topic/tags/notes"},
       "semester": S, "tag": S, "limit": {"type": "integer", "default": 50, "description": "Max 200"},
       "offset": {"type": "integer", "default": 0},
       "include_outline": {"type": "boolean", "default": False, "description": "Add each file's table of contents"}},
      ("workspace_id",), read_only=True)
def _list_files(user, a):
    files = library.list_files(user, a["workspace_id"], with_outline=bool(a.get("include_outline")),
                               query=a.get("query"), semester=a.get("semester"), tag=a.get("tag"))
    try:
        limit, offset = max(1, min(int(a.get("limit") or 50), 200)), max(0, int(a.get("offset") or 0))
    except (TypeError, ValueError):
        raise ApiError(400, "invalid_input", "limit and offset must be numbers")
    page = files[offset:offset + limit]
    return {"total": len(files), "offset": offset, "files": page,
            "next_offset": offset + limit if offset + limit < len(files) else None}


@tool("update_file_metadata", "Set metadata on a workspace file so it can be matched to syllabus weeks reliably: "
      "lecture_number, topic, semester (e.g. 'WS 2026/27'), tags, notes. Fill it after reading a file. Only the "
      "fields you pass are changed; list field names in 'clear' to remove them.",
      {**SPACE, "file_id": S, "lecture_number": {"type": "integer"}, "topic": S, "semester": S,
       "tags": {"type": "array", "items": S}, "notes": S,
       "clear": {"type": "array", "items": {"type": "string", "enum": ["lecture_number", "topic", "semester", "tags", "notes"]},
                 "description": "Fields to remove"}}, ("workspace_id", "file_id"))
def _update_file_meta(user, a):
    fields = {k: a[k] for k in library.META_FIELDS if k in a}
    fields.update({k: None for k in (a.get("clear") or []) if k in library.META_FIELDS})
    return library.update_metadata(user, a["workspace_id"], a["file_id"], fields)


@tool("import_file_from_url", "Download a PDF from a URL into the workspace files (direct links and Google Drive, "
      "Dropbox, Seafile, Nextcloud/sciebo share links). Duplicates are recognised. Does not touch any board.",
      {**SPACE, "url": S}, ("workspace_id", "url"))
def _import_file(user, a):
    return library.import_url(user, a["workspace_id"], a.get("url"))


@tool("read_file", "Extracted text of a file, page by page. Read in chunks: returns next_from_page when there is "
      "more. Use page ranges to look only at what you need.",
      {**SPACE, "file_id": S, "from_page": {"type": "integer", "default": 1}, "to_page": {"type": "integer"},
       "max_chars": {"type": "integer", "default": 15000, "description": "Up to 60000"}},
      ("workspace_id", "file_id"), read_only=True)
def _read_file(user, a):
    return library.read(user, a["workspace_id"], a["file_id"], a.get("from_page", 1), a.get("to_page"),
                        a.get("max_chars", 15000))


@tool("publish_file", "Put a workspace file on a board (same workspace) as slides. mode=replace swaps the board's "
      "material (archives a PDF of the old slides+comments first). The file stays in the workspace files.",
      {**SPACE, "file_id": S, **BOARD, **MODE}, ("workspace_id", "file_id", "board_id"))
def _publish_file(user, a):
    return library.publish(user, a["workspace_id"], a["file_id"], a["board_id"], a.get("mode", "replace"),
                           a.get("archive", True))


@tool("view_file_page", "Look at one page of a workspace file as an image (scanned PDFs, diagrams, formulas, layout).",
      {**SPACE, "file_id": S, "page": {"type": "integer", "default": 1}}, ("workspace_id", "file_id"), read_only=True)
def _view_page(user, a):
    import base64
    jpeg, info = library.page_image(user, a["workspace_id"], a["file_id"], a.get("page", 1))
    return {"_mcp_content": [{"type": "image", "data": base64.b64encode(jpeg).decode(), "mimeType": "image/jpeg"},
                             {"type": "text", "text": f"{info['file']}, page {info['page']} of {info['pages']}"}]}


@tool("view_file_pages", "Overview of up to 12 pages of a workspace file as ONE image (labelled thumbnail grid). "
      "Cheaper than view_file_page for checking what a lecture contains; use view_file_page for details.",
      {**SPACE, "file_id": S, "from_page": {"type": "integer", "default": 1},
       "to_page": {"type": "integer", "description": "Last page (max 12 pages per call)"}},
      ("workspace_id", "file_id"), read_only=True)
def _view_pages(user, a):
    import base64
    jpeg, info = library.contact_sheet(user, a["workspace_id"], a["file_id"], a.get("from_page", 1), a.get("to_page"))
    return {"_mcp_content": [{"type": "image", "data": base64.b64encode(jpeg).decode(), "mimeType": "image/jpeg"},
                             {"type": "text", "text": f"{info['file']}: pages {info['pages_shown'][0]}-"
                                                      f"{info['pages_shown'][1]} of {info['total_pages']}"}]}


@tool("delete_file", "Delete a file from the workspace files (boards that already show it are not affected).",
      {**SPACE, "file_id": S, **DRY}, ("workspace_id", "file_id"), destructive=True)
def _delete_file(user, a):
    res = library.delete(user, a["workspace_id"], a["file_id"], dry_run=bool(a.get("dry_run")))
    return res or {"ok": True}


@tool("get_board_history", "What was put on a board when and by whom (newest first), incl. the file ids - use it to "
      "find material from earlier weeks/semesters and publish it again.", BOARD, ("board_id",), read_only=True)
def _board_history(user, a):
    return library.history(user, a["board_id"])


# --- schedules ------------------------------------------------------------------------------

SESSION_SPEC = {"type": "object", "properties": {
    "file_ids": {"type": "array", "items": {"type": "string"},
                 "description": "Workspace files shown together in this session, in this order (e.g. two lectures in "
                                "one week). Use this OR file_id."},
    "file_id": {"type": "string", "description": "Single workspace file (from list_files)"},
    "starts_at": {"type": "string", "description": "Switch time, local to the board's time zone, e.g. 2026-10-20T16:00 "
                                                   "(or ISO-8601 with offset)"},
    "title": {"type": "string", "description": "e.g. 'Lecture 3: Externalities' (default: the file's title)"}},
    "required": ["starts_at"]}


@tool("get_schedule", "The board's sessions (planned/live/done) with local times, plus the board's time zone and the "
      "current local time.", BOARD, ("board_id",), read_only=True)
def _get_schedule(user, a):
    return schedule.get_schedule(user, a["board_id"])


@tool("set_schedule", "Replace all PLANNED sessions of a board with this list (live/done sessions stay as history). "
      "Use dry_run=true first to validate and preview (nothing is saved; warnings list sessions in the past, which "
      "would go live within 5 minutes - only the latest past one, earlier past ones get 'skipped'). "
      "Only save after the user confirmed the proposed schedule.",
      {**BOARD, "sessions": {"type": "array", "items": SESSION_SPEC}, "dry_run": {"type": "boolean", "default": False}},
      ("board_id", "sessions"))
def _set_schedule(user, a):
    return schedule.set_schedule(user, a["board_id"], a.get("sessions"), dry_run=bool(a.get("dry_run")))


@tool("add_session", "Add one planned session to a board's schedule (one or several files).",
      {**BOARD, **SESSION_SPEC["properties"]}, ("board_id", "starts_at"))
def _add_session(user, a):
    return schedule.add_session(user, a["board_id"], {k: a.get(k) for k in ("file_id", "file_ids", "starts_at", "title")})


@tool("update_session", "Change time, title or files of a planned session (file_ids replaces the session's files).",
      {**BOARD, "session_id": S, **SESSION_SPEC["properties"]}, ("board_id", "session_id"))
def _update_session(user, a):
    return schedule.update_session(user, a["board_id"], a["session_id"], a.get("starts_at"), a.get("title"),
                                   a.get("file_id"), a.get("file_ids"))


@tool("run_session_now", "Make a session live immediately (archives the current material as usual) and keep the "
      "schedule in sync. Works for planned/skipped/failed sessions; a 'done' session is shown again as a copy.",
      {**BOARD, "session_id": S}, ("board_id", "session_id"))
def _run_now(user, a):
    return schedule.run_now(user, a["board_id"], a["session_id"])


@tool("remove_session", "Remove a planned (or past) session from a board's schedule.",
      {**BOARD, "session_id": S, **DRY}, ("board_id", "session_id"), destructive=True)
def _remove_session(user, a):
    res = schedule.remove_session(user, a["board_id"], a["session_id"], dry_run=bool(a.get("dry_run")))
    return res or {"ok": True}


@tool("set_notifications", "Switch the 'board changed' emails on/off for the current user and this board.",
      {**BOARD, "enabled": {"type": "boolean"}}, ("board_id", "enabled"))
def _set_notifications(user, a):
    return notify.set_enabled(user, a["board_id"], bool(a.get("enabled")))


# --- export & archive -----------------------------------------------------------------------

@tool("export_board_pdf", "Create a PDF with every slide followed by its comments; returns a download link "
      "(valid 1 hour) to save it / upload to OLAT.",
      {**BOARD, "include_uncommented": {"type": "boolean", "default": True,
                                        "description": "Also include slides without comments"}},
      ("board_id",), read_only=True)
def _export(user, a):
    return export.export_board(user, a["board_id"], a.get("include_uncommented", True))


@tool("get_archived_comments", "Comments (with replies, likes, answered/pinned/hidden) of a board's EARLIER materials as "
      "data, newest first, plus the current ones - e.g. to summarise the most-liked muddy points per lecture across "
      "the semester. Paginate with offset. (Only material archived after this feature existed has data.)",
      {**BOARD, "limit": {"type": "integer", "default": 5, "description": "Archives per call, max 20"},
       "offset": {"type": "integer", "default": 0}, "include_current": {"type": "boolean", "default": True}},
      ("board_id",), read_only=True)
def _archived_comments(user, a):
    return export.archived_comments(user, a["board_id"], a.get("limit", 5), a.get("offset", 0),
                                    a.get("include_current", True))


@tool("list_board_archives", "List the PDFs archived when the board was cleared/replaced (only when there were "
      "comments). Without download links by default; pass with_urls=true, or key=<archive key> for one link.",
      {**BOARD, "with_urls": {"type": "boolean", "default": False}, "key": S}, ("board_id",), read_only=True)
def _archives(user, a):
    return export.list_archives(user, a["board_id"], with_urls=bool(a.get("with_urls")) or bool(a.get("key")),
                                only_key=a.get("key"))


# --- debugging ------------------------------------------------------------------------------

@tool("send_feedback",
      "Report a problem or idea about Muddy Points ITSELF to its developers (missing tool, bug, confusing behaviour, "
      "idea). Tell the user what you send and get their OK first (unless they asked you to report it). No personal "
      "data or student comment texts. 'context' is optional and may only contain what the user confirmed. Max 10/day.",
      {"category": {"type": "string", "enum": ["bug", "missing_feature", "confusing", "idea", "other"]},
       "summary": {"type": "string", "description": "One line, 5-200 characters"},
       "details": {"type": "string", "description": "What you tried, what happened, what would have helped"},
       "context": {"type": "object", "description": "Optional, ONLY what the user agreed to share", "properties": {
           "role": {"type": "string", "enum": ["lecturer", "tutor", "teaching_assistant", "admin", "other"]},
           "discipline": {"type": "string", "description": "e.g. environmental economics"},
           "teaching_format": {"type": "string", "enum": ["lecture", "seminar", "flipped_classroom", "online", "other"]},
           "course_size": {"type": "string", "enum": ["<30", "30-100", "100-300", ">300"]},
           "course_language": {"type": "string"},
           "semester_phase": {"type": "string", "enum": ["preparation", "during_semester", "wrap_up"]},
           "goal": {"type": "string", "description": "What the user was trying to achieve"},
           "other_tools": {"type": "array", "items": {"type": "string"}, "description": "e.g. OLAT, Seafile"},
           "ai_client": {"type": "string", "enum": ["claude_ai", "claude_desktop", "claude_code", "chatgpt", "mobile", "other"],
                         "description": "Which AI assistant/app the user works with"},
           "tech_comfort": {"type": "string", "enum": ["low", "medium", "high"]}}}},
      ("category", "summary"))
def _send_feedback(user, a):
    from . import feedback
    return feedback.submit_from_assistant(user, a.get("category"), a.get("summary"), a.get("details"), a.get("context"),
                                          agent=CLIENT_AGENT.get())


@tool("list_feedback", "Platform operators (superadmins) only: feedback from the web form and from AI assistants, "
      "newest first. Others get 'not found'.",
      {"only_open": {"type": "boolean", "default": True}, "source": {"type": "string", "enum": ["web", "assistant"]},
       "limit": {"type": "integer", "default": 50}}, read_only=True)
def _list_feedback(user, a):
    from . import feedback
    return feedback.list_all(user, a.get("limit", 50), a.get("only_open", True), a.get("source"))


@tool("mark_feedback_done", "Platform operators (superadmins) only: mark a feedback entry as done (or open again).",
      {"feedback_id": S, "done": {"type": "boolean", "default": True}}, ("feedback_id",))
def _feedback_done(user, a):
    from . import feedback
    return feedback.set_done(user, a["feedback_id"], a.get("done", True))


@tool("get_logs", "Read recent application logs (backend + frontend errors) from CloudWatch for debugging. "
      "For workspace members.",
      {"minutes": {"type": "integer", "default": 60}, "level": {"type": "string", "enum": ["INFO", "WARNING", "ERROR"],
                                                                "default": "WARNING"},
       "contains": {"type": "string", "description": "Only entries containing this text (e.g. a request_id)"},
       "limit": {"type": "integer", "default": 100}}, read_only=True)
def _logs(user, a):
    return applogs.fetch(user, a.get("minutes", 60), a.get("level", "WARNING"), a.get("contains"), a.get("limit", 100))


# --- JSON-RPC plumbing ----------------------------------------------------------------------

def _result(msg_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _call_tool(user: dict, params: dict) -> dict:
    name, args = params.get("name"), params.get("arguments") or {}
    entry = TOOLS.get(name)
    if not entry:
        raise KeyError(name)
    bind(tool=name)
    try:
        result = entry["fn"](user, args)
        log_event(logger, logging.INFO, "mcp tool call", tool=name, ok=True)
        if isinstance(result, dict) and "_mcp_content" in result:  # e.g. images
            return {"content": result["_mcp_content"], "isError": False}
        text = json.dumps(result, default=str, ensure_ascii=False, indent=1).replace('"space_id"', '"workspace_id"')
        return {"content": [{"type": "text", "text": text}],
                "isError": False}
    except ApiError as err:
        log_event(logger, logging.INFO, "mcp tool call", tool=name, ok=False, code=err.code)
        return {"content": [{"type": "text", "text": f"Error ({err.code}): {err.message}"}], "isError": True}
    except KeyError as err:
        return {"content": [{"type": "text", "text": f"Error: missing argument {err}"}], "isError": True}
    except Exception:
        logger.exception("mcp tool crashed", extra={"data": {"tool": name}})
        from .log import _context
        rid = _context.get().get("request_id")
        return {"content": [{"type": "text", "text": f"Internal error in tool '{name}'. Look it up with "
                             f"get_logs(contains='{rid}', level='ERROR')."}], "isError": True}


def _dispatch(user: dict, msg: dict) -> dict | None:
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid request")
    method, msg_id, params = msg["method"], msg.get("id"), msg.get("params") or {}
    if "id" not in msg:  # notification (e.g. notifications/initialized): no response
        return None
    if method == "initialize":
        requested = params.get("protocolVersion")
        return _result(msg_id, {
            "protocolVersion": requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "muddypoints", "version": __version__},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": [t["spec"] for t in TOOLS.values()]})
    if method == "tools/call":
        try:
            return _result(msg_id, _call_tool(user, params))
        except KeyError:
            return _error(msg_id, -32602, f"Unknown tool: {params.get('name')}")
    if method in ("resources/list", "prompts/list"):
        return _result(msg_id, {method.split("/")[0]: []})
    if method == "resources/templates/list":
        return _result(msg_id, {"resourceTemplates": []})
    if method == "logging/setLevel":
        return _result(msg_id, {})
    return _error(msg_id, -32601, f"Method not found: {method}")


def _empty(status: int, headers: dict | None = None) -> dict:
    """Response without body (and without content-type), e.g. 202 for notifications."""
    return {"statusCode": status, "headers": headers or {}, "body": ""}


def _api_key(req: Request) -> str:
    """Key from the URL (/mcp/<key>), else from `Authorization: Bearer <key>` or `X-API-Key` headers."""
    path_key = req.path[len("/mcp/"):].strip("/") if req.path.startswith("/mcp/") else ""
    return path_key or req.bearer or req.headers.get("x-api-key", "").strip()


def _describe(payload) -> str:
    msgs = payload if isinstance(payload, list) else [payload]
    return ",".join(str(m.get("method", "?")) if isinstance(m, dict) else "?" for m in msgs)


def handle(req: Request) -> dict:
    client = {"user_agent": req.headers.get("user-agent", "")[:200], "accept": req.headers.get("accept", "")[:100],
              "protocol": req.headers.get("mcp-protocol-version", "")}
    if req.method in ("GET", "HEAD"):  # no server-initiated SSE stream in stateless mode (allowed by the spec)
        log_event(logger, logging.INFO, "mcp non-post", http_method=req.method, **client)
        return _empty(405, {"allow": "POST, DELETE, OPTIONS"})
    if req.method == "OPTIONS":
        return _empty(204, {"allow": "POST, DELETE, OPTIONS"})
    if req.method == "DELETE":  # session termination: nothing to clean up
        return _empty(204)
    if req.method != "POST":
        return _empty(405, {"allow": "POST, DELETE, OPTIONS"})
    user = users.user_from_api_key(_api_key(req))
    if not user:
        log_event(logger, logging.WARNING, "mcp unauthorized", **client)
        return json_response(401, _error(None, -32001, "Invalid or missing API key. Create one in the web UI under "
                                                        "Account -> Use Muddy Points with AI assistants."))
    bind(user_id=user["user_id"], channel="mcp")
    CLIENT_AGENT.set(client["user_agent"])
    try:
        payload = json.loads(req.raw_body or b"null")
    except (json.JSONDecodeError, UnicodeDecodeError):
        log_event(logger, logging.WARNING, "mcp parse error", body_start=req.raw_body[:200].decode(errors="replace"), **client)
        return json_response(400, _error(None, -32700, "Parse error"))
    if isinstance(payload, dict) and payload.get("method") == "initialize":
        client["client_info"] = (payload.get("params") or {}).get("clientInfo")
        client["requested_protocol"] = (payload.get("params") or {}).get("protocolVersion")
    log_event(logger, logging.INFO, "mcp message", rpc=_describe(payload), **client)
    if isinstance(payload, list):
        responses = [r for r in (_dispatch(user, m) for m in payload) if r is not None]
        return json_response(200, responses) if responses else _empty(202)
    response = _dispatch(user, payload)
    return json_response(200, response) if response is not None else _empty(202)
