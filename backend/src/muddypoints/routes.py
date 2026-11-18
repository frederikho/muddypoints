"""REST API used by the web frontend. All paths start with /api.

Auth: `Authorization: Bearer <session token>` (from /api/auth/login) — API keys (pk_...) work too.
Anonymous students send `X-Anon-Token: <token from POST /api/identity>`.
"""
import logging

from . import applogs, boards, export, identity, spaces, uploads, users
from .http import ApiError, Request, Router
from .log import bind, get_logger, log_event

router = Router()
logger = get_logger("muddypoints.routes")
frontend_logger = get_logger("frontend")


# --- request helpers ------------------------------------------------------------------------

def current_user(req: Request) -> dict | None:
    if not hasattr(req, "_user"):
        token = req.bearer
        user = None
        if token:
            user = users.user_from_api_key(token) if token.startswith("pk_") else users.user_from_session(token)
        req._user = user
        if user:
            bind(user_id=user["user_id"])
    return req._user


def require_user(req: Request) -> dict:
    user = current_user(req)
    if not user:
        raise ApiError(401, "unauthorized", "Please log in")
    return user


def current_viewer(req: Request) -> identity.Viewer | None:
    user = current_user(req)
    if user:
        return identity.viewer_from_user(user)
    viewer = identity.viewer_from_anon_token(req.headers.get("x-anon-token", ""))
    if viewer:
        bind(viewer=viewer.key)
    return viewer


def require_viewer(req: Request) -> identity.Viewer:
    viewer = current_viewer(req)
    if not viewer:
        raise ApiError(401, "no_identity", "Missing or expired anonymous identity — reload the page")
    return viewer


def body(req: Request) -> dict:
    data = req.json()
    if not isinstance(data, dict):
        raise ApiError(400, "invalid_json", "Request body must be a JSON object")
    return data


def p(req: Request, name: str) -> str:
    return req.path_params[name]


# --- misc -----------------------------------------------------------------------------------

@router.route("GET", "/api/health")
def health(req):
    from . import __version__, media
    return {"ok": True, "version": __version__, "upload_host": media.upload_host()}


@router.route("POST", "/api/identity")
def new_identity(req):
    return identity.new_identity()


@router.route("POST", "/api/client-log")
def client_log(req):
    """Frontend error reporting — lands in CloudWatch next to backend logs (logger 'frontend')."""
    data = body(req)
    level = {"error": logging.ERROR, "warn": logging.WARNING, "warning": logging.WARNING}.get(
        str(data.get("level", "error")).lower(), logging.INFO)
    log_event(frontend_logger, level, str(data.get("message", ""))[:2000],
              page=str(data.get("url", ""))[:500], context=str(data.get("context", ""))[:4000],
              user_agent=req.headers.get("user-agent", "")[:300])
    return {"ok": True}


# --- auth & account -------------------------------------------------------------------------

@router.route("POST", "/api/auth/register")
def register(req):
    d = body(req)
    user = users.register(d.get("email"), d.get("password"), d.get("name"))
    return {"user": users.public_user(user), "token": users.issue_session(user)}


@router.route("POST", "/api/auth/login")
def login(req):
    d = body(req)
    user, token = users.login(d.get("email"), d.get("password"))
    return {"user": users.public_user(user), "token": token}


@router.route("POST", "/api/auth/forgot")
def forgot(req):
    users.request_password_reset(body(req).get("email"))
    return {"ok": True}


@router.route("POST", "/api/auth/reset")
def reset(req):
    d = body(req)
    user, token = users.reset_password(d.get("token"), d.get("password"))
    return {"user": users.public_user(user), "token": token}


@router.route("GET", "/api/me")
def me(req):
    return {"user": users.public_user(require_user(req))}


@router.route("PATCH", "/api/me")
def update_me(req):
    return {"user": users.public_user(users.update_profile(require_user(req), body(req).get("name")))}


@router.route("POST", "/api/me/password")
def change_password(req):
    d = body(req)
    return {"token": users.change_password(require_user(req), d.get("old_password"), d.get("new_password"))}


@router.route("GET", "/api/me/api-keys")
def list_keys(req):
    return {"api_keys": users.list_api_keys(require_user(req))}


@router.route("POST", "/api/me/api-keys")
def create_key(req):
    return users.create_api_key(require_user(req), body(req).get("label"))


@router.route("DELETE", "/api/me/api-keys/{key_id}")
def revoke_key(req):
    users.revoke_api_key(require_user(req), p(req, "key_id"))
    return {"ok": True}


# --- spaces & members -----------------------------------------------------------------------

@router.route("GET", "/api/spaces")
def list_spaces(req):
    return {"spaces": spaces.list_spaces(require_user(req))}


@router.route("POST", "/api/spaces")
def create_space(req):
    return {"space": spaces.create_space(require_user(req), body(req).get("name"))}


@router.route("GET", "/api/spaces/{space_id}")
def get_space(req):
    user = require_user(req)
    space, role = spaces.require_space(user, p(req, "space_id"))
    return {"space": spaces.public_space(space, role), "boards": boards.list_boards(user, p(req, "space_id"))}


@router.route("PATCH", "/api/spaces/{space_id}")
def rename_space(req):
    return {"space": spaces.rename_space(require_user(req), p(req, "space_id"), body(req).get("name"))}


@router.route("DELETE", "/api/spaces/{space_id}")
def delete_space(req):
    spaces.delete_space(require_user(req), p(req, "space_id"))
    return {"ok": True}


@router.route("GET", "/api/spaces/{space_id}/members")
def list_members(req):
    return spaces.list_members(require_user(req), p(req, "space_id"))


@router.route("POST", "/api/spaces/{space_id}/members")
def invite(req):
    d = body(req)
    return spaces.invite(require_user(req), p(req, "space_id"), d.get("email"), d.get("role", "editor"))


@router.route("PATCH", "/api/spaces/{space_id}/members/{user_id}")
def set_role(req):
    spaces.set_role(require_user(req), p(req, "space_id"), p(req, "user_id"), body(req).get("role"))
    return {"ok": True}


@router.route("DELETE", "/api/spaces/{space_id}/members/{user_id}")
def remove_member(req):
    spaces.remove_member(require_user(req), p(req, "space_id"), p(req, "user_id"))
    return {"ok": True}


@router.route("DELETE", "/api/spaces/{space_id}/invites/{email}")
def revoke_invite(req):
    from urllib.parse import unquote
    spaces.revoke_invite(require_user(req), p(req, "space_id"), unquote(p(req, "email")))
    return {"ok": True}


@router.route("POST", "/api/spaces/{space_id}/boards")
def create_board(req):
    d = body(req)
    return {"board": boards.create_board(require_user(req), p(req, "space_id"), d.get("name"), d.get("background"))}


# --- boards ---------------------------------------------------------------------------------

@router.route("GET", "/api/boards/{board_id}")
def get_board(req):
    return boards.view(p(req, "board_id"), current_viewer(req))


@router.route("PATCH", "/api/boards/{board_id}")
def update_board(req):
    d = body(req)
    return {"board": boards.update_board(require_user(req), p(req, "board_id"), d.get("name"), d.get("locked"),
                                         d.get("background"))}


@router.route("DELETE", "/api/boards/{board_id}")
def delete_board(req):
    boards.delete_board(require_user(req), p(req, "board_id"))
    return {"ok": True}


@router.route("POST", "/api/boards/{board_id}/uploads")
def prepare_uploads(req):
    return {"uploads": uploads.prepare(require_user(req), p(req, "board_id"), body(req).get("files"))}


@router.route("POST", "/api/boards/{board_id}/uploads/confirm")
def confirm_uploads(req):
    d = body(req)
    return uploads.confirm(require_user(req), p(req, "board_id"), d.get("upload_keys"),
                           d.get("mode", "append"), d.get("archive", True))


@router.route("POST", "/api/boards/{board_id}/slides/reorder")
def reorder(req):
    return {"slide_ids": boards.reorder_slides(require_user(req), p(req, "board_id"), body(req).get("slide_ids"))}


@router.route("DELETE", "/api/boards/{board_id}/slides/{slide_id}")
def delete_slide(req):
    boards.delete_slide(require_user(req), p(req, "board_id"), p(req, "slide_id"))
    return {"ok": True}


@router.route("POST", "/api/boards/{board_id}/clear")
def clear_board(req):
    return export.clear_board(require_user(req), p(req, "board_id"), body(req).get("archive", True))


@router.route("POST", "/api/boards/{board_id}/export")
def export_board(req):
    return export.export_board(require_user(req), p(req, "board_id"), body(req).get("include_uncommented", True))


@router.route("GET", "/api/boards/{board_id}/archives")
def archives(req):
    return {"archives": export.list_archives(require_user(req), p(req, "board_id"))}


@router.route("POST", "/api/boards/{board_id}/comments")
def add_comment(req):
    d = body(req)
    return {"comment": boards.add_comment(p(req, "board_id"), d.get("slide_id"), d.get("text"), require_viewer(req))}


@router.route("DELETE", "/api/boards/{board_id}/comments/{comment_id}")
def delete_comment(req):
    boards.delete_comment(p(req, "board_id"), p(req, "comment_id"), current_viewer(req))
    return {"ok": True}


@router.route("POST", "/api/boards/{board_id}/likes")
def toggle_like(req):
    d = body(req)
    return boards.toggle_like(p(req, "board_id"), require_viewer(req), d.get("slide_id"), d.get("comment_id"))


@router.route("GET", "/api/logs")
def get_logs(req):
    q = req.query
    return applogs.fetch(require_user(req), q.get("minutes", 60), q.get("level", "INFO"), q.get("contains"),
                         q.get("limit", 100))
