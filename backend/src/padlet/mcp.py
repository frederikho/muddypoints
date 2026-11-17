"""Remote MCP server (Streamable HTTP transport, stateless, JSON responses).

Connector URL for Claude:  <FunctionUrl>/mcp/<API key>
(or POST <FunctionUrl>/mcp with header `Authorization: Bearer <API key>`).
API keys are created per user in the web UI (Account -> Claude / MCP access). Every tool runs with
that user's permissions, so the owner/editor rules apply exactly as in the web UI.

To add a tool: write a function `(user, args) -> JSON-serialisable` and decorate it with @tool.
Keep business logic in the service modules (boards.py, spaces.py, ...) — tools are thin wrappers.
"""
import json
import logging
from typing import Any, Callable

from . import __version__, applogs, boards, export, identity, spaces, uploads, users
from .http import ApiError, Request, json_response
from .log import bind, get_logger, log_event

logger = get_logger(__name__)
SUPPORTED_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
INSTRUCTIONS = """Admin interface of the course feedback boards (a Padlet-like tool).
Hierarchy: space (e.g. a course) -> boards (one public link per class) -> slides (images) -> comments/likes.
Students comment anonymously on slides via the public board URL.
Typical weekly workflow for a board: export_board_pdf (save the comments for OLAT) and then
replace_board_material with the new lecture PDF (this archives a PDF of the old comments automatically).
Owners can delete things; editors can do everything else. IDs come from list_* tools."""

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
SPACE = {"space_id": {**S, "description": "Space id (from list_spaces)"}}
BOARD = {"board_id": {**S, "description": "Board id (from list_boards)"}}
MODE = {"mode": {"type": "string", "enum": ["append", "replace"], "default": "append",
                 "description": "append = add after existing slides; replace = remove all old slides, comments "
                                "and likes (owners only)"},
        "archive": {"type": "boolean", "default": True,
                    "description": "With mode=replace: first save a PDF of the old slides+comments to the archive"}}


# --- account & spaces -----------------------------------------------------------------------

@tool("whoami", "Show the user this API key belongs to and their spaces.", read_only=True)
def _whoami(user, a):
    return {"user": users.public_user(user), "spaces": spaces.list_spaces(user)}


@tool("list_spaces", "List the spaces you are a member of, with your role.", read_only=True)
def _list_spaces(user, a):
    return spaces.list_spaces(user)


@tool("create_space", "Create a new space (you become its owner).", {"name": S}, ("name",))
def _create_space(user, a):
    return spaces.create_space(user, a.get("name"))


@tool("rename_space", "Rename a space.", {**SPACE, "name": S}, ("space_id", "name"))
def _rename_space(user, a):
    return spaces.rename_space(user, a["space_id"], a.get("name"))


@tool("delete_space", "Permanently delete a space with ALL its boards, slides and comments (owners only).",
      SPACE, ("space_id",), destructive=True)
def _delete_space(user, a):
    spaces.delete_space(user, a["space_id"])
    return {"deleted": a["space_id"]}


@tool("list_members", "List members (with roles) and pending invites of a space.", SPACE, ("space_id",),
      read_only=True)
def _list_members(user, a):
    return spaces.list_members(user, a["space_id"])


@tool("invite_member", "Share a space with someone by email. Existing users are added immediately, others get "
      "access when they register with that email.",
      {**SPACE, "email": S, "role": {"type": "string", "enum": ["editor", "owner"], "default": "editor"}},
      ("space_id", "email"))
def _invite(user, a):
    return spaces.invite(user, a["space_id"], a.get("email"), a.get("role", "editor"))


@tool("set_member_role", "Change a member's role (owners only).",
      {**SPACE, "user_id": S, "role": {"type": "string", "enum": ["editor", "owner"]}},
      ("space_id", "user_id", "role"))
def _set_role(user, a):
    spaces.set_role(user, a["space_id"], a["user_id"], a.get("role"))
    return {"ok": True}


@tool("remove_member", "Remove a member from a space (owners only; anyone can remove themselves).",
      {**SPACE, "user_id": S}, ("space_id", "user_id"), destructive=True)
def _remove_member(user, a):
    spaces.remove_member(user, a["space_id"], a["user_id"])
    return {"ok": True}


# --- boards ---------------------------------------------------------------------------------

@tool("list_boards", "List the boards of a space incl. their public URLs.", SPACE, ("space_id",), read_only=True)
def _list_boards(user, a):
    return boards.list_boards(user, a["space_id"])


@tool("create_board", "Create a board in a space. Returns its public URL.", {**SPACE, "name": S},
      ("space_id", "name"))
def _create_board(user, a):
    return boards.create_board(user, a["space_id"], a.get("name"))


@tool("get_board", "Board overview: slides (ids, order, source file, likes, comment count) and settings.", BOARD,
      ("board_id",), read_only=True)
def _get_board(user, a):
    boards.require_board(user, a["board_id"], "editor")
    v = boards.view(a["board_id"], identity.viewer_from_user(user))
    return {"board": v["board"], "your_role": v["my_role"], "slides": [
        {"number": i + 1, "slide_id": s["slide_id"], "source": s["source"], "likes": s["likes"],
         "comments": len(s["comments"])} for i, s in enumerate(v["slides"])]}


@tool("update_board", "Rename a board and/or lock it (locked = students can no longer comment or like).",
      {**BOARD, "name": S, "locked": {"type": "boolean"}}, ("board_id",))
def _update_board(user, a):
    return boards.update_board(user, a["board_id"], a.get("name"), a.get("locked"))


@tool("delete_board", "Permanently delete a board incl. slides and comments (owners only). Its URL stops working.",
      BOARD, ("board_id",), destructive=True)
def _delete_board(user, a):
    boards.delete_board(user, a["board_id"])
    return {"deleted": a["board_id"]}


@tool("list_comments", "All comments of a board, oldest first, with the slide number they belong to.", BOARD,
      ("board_id",), read_only=True)
def _list_comments(user, a):
    return boards.list_comments(user, a["board_id"])


@tool("delete_comment", "Delete a comment (owners only).", {**BOARD, "comment_id": S}, ("board_id", "comment_id"),
      destructive=True)
def _delete_comment(user, a):
    boards.require_board(user, a["board_id"], "owner")
    boards.delete_comment(a["board_id"], a["comment_id"], identity.viewer_from_user(user))
    return {"ok": True}


# --- slides ---------------------------------------------------------------------------------

@tool("import_slides_from_url",
      "Download a PDF or image from a public URL (direct link, Google Drive or Dropbox share link) and turn it into "
      "slides (one per PDF page).", {**BOARD, "url": S, **MODE}, ("board_id", "url"))
def _import_url(user, a):
    return uploads.import_from_url(user, a["board_id"], a.get("url"), a.get("mode", "append"), a.get("archive", True))


@tool("replace_board_material",
      "Weekly rotation: archive a PDF of the current slides+comments, remove them, and load new material from a URL "
      "(PDF/image). The board URL stays the same. Owners only.", {**BOARD, "url": S}, ("board_id", "url"),
      destructive=True)
def _replace(user, a):
    return uploads.import_from_url(user, a["board_id"], a.get("url"), "replace", True)


@tool("prepare_slide_upload",
      "Step 1 of a direct upload: returns presigned S3 PUT URLs (valid 1h). PUT each file's bytes with the given "
      "Content-Type header, then call confirm_slide_upload with the upload_keys.",
      {**BOARD, "files": {"type": "array", "items": {"type": "object", "properties": {
          "filename": S, "content_type": {"type": "string", "description": "e.g. application/pdf, image/png"}},
          "required": ["filename", "content_type"]}}}, ("board_id", "files"))
def _prepare(user, a):
    return uploads.prepare(user, a["board_id"], a.get("files"))


@tool("confirm_slide_upload", "Step 2 of a direct upload: convert uploaded files into slides (in the given order).",
      {**BOARD, "upload_keys": {"type": "array", "items": S}, **MODE}, ("board_id", "upload_keys"))
def _confirm(user, a):
    return uploads.confirm(user, a["board_id"], a.get("upload_keys"), a.get("mode", "append"), a.get("archive", True))


@tool("reorder_slides", "Set the slide order. Pass every slide_id of the board exactly once.",
      {**BOARD, "slide_ids": {"type": "array", "items": S}}, ("board_id", "slide_ids"))
def _reorder(user, a):
    return boards.reorder_slides(user, a["board_id"], a.get("slide_ids"))


@tool("delete_slide", "Delete one slide with its comments (owners only).", {**BOARD, "slide_id": S},
      ("board_id", "slide_id"), destructive=True)
def _delete_slide(user, a):
    boards.delete_slide(user, a["board_id"], a["slide_id"])
    return {"ok": True}


@tool("clear_board", "Remove all slides, comments and likes from a board (owners only). By default an archive PDF "
      "is saved first.", {**BOARD, "archive": {"type": "boolean", "default": True}}, ("board_id",), destructive=True)
def _clear(user, a):
    return export.clear_board(user, a["board_id"], a.get("archive", True))


# --- export & archive -----------------------------------------------------------------------

@tool("export_board_pdf", "Create a PDF with every slide followed by its comments; returns a download link "
      "(valid 1 hour) to save it / upload to OLAT.",
      {**BOARD, "include_uncommented": {"type": "boolean", "default": True,
                                        "description": "Also include slides without comments"}},
      ("board_id",), read_only=True)
def _export(user, a):
    return export.export_board(user, a["board_id"], a.get("include_uncommented", True))


@tool("list_board_archives", "List the PDFs archived automatically whenever the board was cleared/replaced.", BOARD,
      ("board_id",), read_only=True)
def _archives(user, a):
    return export.list_archives(user, a["board_id"])


# --- debugging ------------------------------------------------------------------------------

@tool("get_logs", "Read recent application logs (backend + frontend errors) from CloudWatch for debugging. "
      "Only for users owning at least one space.",
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
        return {"content": [{"type": "text", "text": json.dumps(result, default=str, ensure_ascii=False, indent=1)}],
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
            "serverInfo": {"name": "padlet-boards", "version": __version__},
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
    return _error(msg_id, -32601, f"Method not found: {method}")


def handle(req: Request) -> dict:
    if req.method == "GET":  # no server-initiated SSE stream in stateless mode
        return json_response(405, {"error": "Use POST"}, {"allow": "POST, DELETE"})
    if req.method == "DELETE":
        return json_response(200, None)
    if req.method != "POST":
        return json_response(405, {"error": "Use POST"}, {"allow": "POST"})
    key = req.path[len("/mcp/"):] if req.path.startswith("/mcp/") else req.bearer
    user = users.user_from_api_key(key.strip("/"))
    if not user:
        log_event(logger, logging.WARNING, "mcp unauthorized")
        return json_response(401, _error(None, -32001, "Invalid or missing API key. Create one in the web UI under "
                                                        "Account -> Claude / MCP access."))
    bind(user_id=user["user_id"], channel="mcp")
    try:
        payload = json.loads(req.raw_body or b"null")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return json_response(400, _error(None, -32700, "Parse error"))
    if isinstance(payload, list):
        responses = [r for r in (_dispatch(user, m) for m in payload) if r is not None]
        return json_response(200, responses) if responses else json_response(202, None)
    response = _dispatch(user, payload)
    return json_response(200, response) if response is not None else json_response(202, None)
