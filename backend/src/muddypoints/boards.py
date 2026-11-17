"""Boards, slides, comments and likes.

Boards belong to a space and are publicly readable by anyone with the link.
All items of a board live under PK=BOARD#<bid>, so one query loads the whole board.
"""
import logging

from . import db, media, spaces
from .config import MAX_COMMENT_LENGTH, settings
from .http import ApiError, forbidden, not_found
from .identity import Viewer
from .log import get_logger, log_event
from .util import clean_name, new_id, now_iso, sortable_id

logger = get_logger(__name__)


# Background textures (pure CSS, see frontend/css/style.css `.bg-<name>`). Keep both lists in sync.
BACKGROUNDS = ("plain", "dots", "grid", "lined", "cork", "linen", "chalkboard", "sunset", "ocean")


def board_url(board_id: str) -> str:
    return f"{settings.frontend_url}/board.html?b={board_id}"


def get_board_meta(board_id: str) -> dict:
    board = db.get(f"BOARD#{board_id}", "META") if board_id else None
    if not board:
        raise not_found("Board")
    return board


def require_board(user: dict, board_id: str, min_role: str = "editor") -> tuple[dict, str]:
    board = get_board_meta(board_id)
    _, role = spaces.require_space(user, board["space_id"], min_role)
    return board, role


def public_board(board: dict) -> dict:
    return {"board_id": board["board_id"], "space_id": board["space_id"], "name": board["name"],
            "locked": bool(board.get("locked")), "created_at": board.get("created_at"),
            "updated_at": board.get("updated_at"), "url": board_url(board["board_id"]),
            "background": board.get("background") if board.get("background") in BACKGROUNDS else "plain",
            "last_activity_at": last_activity(board)}


def last_activity(board: dict) -> str | None:
    """Latest of: board/slide changes (updated_at) and the newest comment (last_activity_at)."""
    return max(filter(None, (board.get("updated_at"), board.get("last_activity_at"))), default=None)


def _check_background(background) -> str:
    if background not in BACKGROUNDS:
        raise ApiError(400, "invalid_input", f"'background' must be one of: {', '.join(BACKGROUNDS)}")
    return background


def create_board(user: dict, space_id: str, name, background="plain") -> dict:
    spaces.require_space(user, space_id, "editor")
    background = _check_background(background or "plain")
    board_id = new_id()
    now = now_iso()
    board = {"PK": f"BOARD#{board_id}", "SK": "META", "board_id": board_id, "space_id": space_id,
             "name": clean_name(name), "locked": False, "background": background, "created_at": now, "updated_at": now,
             "created_by": user["user_id"], "GSI1PK": f"SPACE#{space_id}", "GSI1SK": f"BOARD#{board_id}"}
    db.put(board)
    log_event(logger, logging.INFO, "board created", board_id=board_id, space_id=space_id)
    return public_board(board)


def list_boards(user: dict, space_id: str) -> list[dict]:
    spaces.require_space(user, space_id, "editor")
    return sorted((public_board(b) for b in db.query_gsi1(f"SPACE#{space_id}", "BOARD#")),
                  key=lambda b: b["name"].lower())


def update_board(user: dict, board_id: str, name=None, locked=None, background=None) -> dict:
    require_board(user, board_id, "editor")
    fields = {"updated_at": now_iso()}
    if name is not None:
        fields["name"] = clean_name(name)
    if locked is not None:
        if not isinstance(locked, bool):
            raise ApiError(400, "invalid_input", "'locked' must be true or false")
        fields["locked"] = locked
    if background is not None:
        fields["background"] = _check_background(background)
    board = db.update(f"BOARD#{board_id}", "META", fields)
    log_event(logger, logging.INFO, "board updated", board_id=board_id, fields=sorted(fields))
    return public_board(board)


def purge_board(board_id: str) -> None:
    """Delete a board with all its items and slide images (no permission check)."""
    db.batch_delete([(i["PK"], i["SK"]) for i in db.query(f"BOARD#{board_id}")])
    media.delete_prefix(f"boards/{board_id}/")


def delete_board(user: dict, board_id: str) -> None:
    require_board(user, board_id, "owner")
    purge_board(board_id)
    log_event(logger, logging.INFO, "board deleted", board_id=board_id)


# --- reading a whole board ------------------------------------------------------------------

def load(board_id: str) -> dict:
    """Return {'meta', 'slides' (sorted), 'comments' (oldest first), 'likes'} for a board."""
    items = db.query(f"BOARD#{board_id}")
    meta = next((i for i in items if i["SK"] == "META"), None)
    if not meta:
        raise not_found("Board")
    slides = sorted((i for i in items if i["SK"].startswith("SLIDE#")), key=lambda s: (s["position"], s["SK"]))
    comments = sorted((i for i in items if i["SK"].startswith("COMMENT#")), key=lambda c: c["SK"])
    likes = [i for i in items if i["SK"].startswith("LIKE#")]
    return {"meta": meta, "slides": slides, "comments": comments, "likes": likes}


def view(board_id: str, viewer: Viewer | None) -> dict:
    """Public representation of a board incl. slide image URLs, comments and like state."""
    data = load(board_id)
    my_likes = {l["target"] for l in data["likes"] if viewer and l["viewer"] == viewer.key}
    role = spaces.role_of(data["meta"]["space_id"], viewer.user) if viewer and viewer.user else None
    by_slide: dict[str, list] = {}
    for c in data["comments"]:
        by_slide.setdefault(c["slide_id"], []).append({
            "comment_id": c["comment_id"], "text": c["text"], "author_name": c["author_name"],
            "is_staff": bool(c.get("is_staff")), "created_at": c["created_at"], "likes": c.get("likes", 0),
            "liked": c["comment_id"] in my_likes,
            "mine": bool(viewer and viewer.key == c["author_key"]),
        })
    slides = [{
        "slide_id": s["slide_id"], "position": s["position"], "image_url": media.presigned_get(s["key"]),
        "width": s.get("width"), "height": s.get("height"), "source": s.get("source"),
        "likes": s.get("likes", 0), "liked": s["slide_id"] in my_likes,
        "comments": by_slide.get(s["slide_id"], []),
    } for s in data["slides"]]
    return {"board": public_board(data["meta"]), "slides": slides, "my_role": role,
            "viewer": {"name": viewer.name, "is_user": viewer.is_user} if viewer else None}


# --- comments -------------------------------------------------------------------------------

def add_comment(board_id: str, slide_id, text, viewer: Viewer) -> dict:
    board = get_board_meta(board_id)
    is_staff = bool(viewer.user and spaces.role_of(board["space_id"], viewer.user))
    if board.get("locked") and not is_staff:
        raise ApiError(423, "board_locked", "Comments are closed for this board")
    if not isinstance(slide_id, str) or not db.get(f"BOARD#{board_id}", f"SLIDE#{slide_id}"):
        raise not_found("Slide")
    text = (text or "").strip() if isinstance(text, str) else ""
    if not text:
        raise ApiError(400, "invalid_input", "Comment must not be empty")
    if len(text) > MAX_COMMENT_LENGTH:
        raise ApiError(400, "invalid_input", f"Comment is too long (max {MAX_COMMENT_LENGTH} characters)")
    comment_id = sortable_id()
    item = {"PK": f"BOARD#{board_id}", "SK": f"COMMENT#{comment_id}", "comment_id": comment_id,
            "slide_id": slide_id, "text": text, "author_name": viewer.name, "author_key": viewer.key,
            "is_staff": is_staff, "likes": 0, "created_at": now_iso()}
    db.put(item)
    db.update(f"BOARD#{board_id}", "META", {"last_activity_at": item["created_at"]})
    log_event(logger, logging.INFO, "comment added", board_id=board_id, slide_id=slide_id, comment_id=comment_id)
    return {"comment_id": comment_id, "slide_id": slide_id, "text": text, "author_name": viewer.name,
            "is_staff": is_staff, "created_at": item["created_at"], "likes": 0, "liked": False, "mine": True}


def list_comments(user: dict, board_id: str) -> list[dict]:
    require_board(user, board_id, "editor")
    data = load(board_id)
    positions = {s["slide_id"]: idx + 1 for idx, s in enumerate(data["slides"])}
    return [{"comment_id": c["comment_id"], "slide_id": c["slide_id"], "slide_number": positions.get(c["slide_id"]),
             "author_name": c["author_name"], "is_staff": bool(c.get("is_staff")), "text": c["text"],
             "likes": c.get("likes", 0), "created_at": c["created_at"]} for c in data["comments"]]


def _delete_likes_for(board_id: str, target_ids: set[str]) -> None:
    likes = db.query(f"BOARD#{board_id}", "LIKE#")
    db.batch_delete([(l["PK"], l["SK"]) for l in likes if l["target"] in target_ids])


def delete_comment(board_id: str, comment_id: str, viewer: Viewer | None) -> None:
    """Space owners can delete any comment; authors (anonymous or not) can delete their own."""
    board = get_board_meta(board_id)
    comment = db.get(f"BOARD#{board_id}", f"COMMENT#{comment_id}")
    if not comment:
        raise not_found("Comment")
    is_author = viewer is not None and viewer.key == comment["author_key"]
    is_owner = bool(viewer and viewer.user and spaces.role_of(board["space_id"], viewer.user) == "owner")
    if not (is_author or is_owner):
        raise forbidden("Only the author or a space owner can delete this comment")
    db.delete(f"BOARD#{board_id}", f"COMMENT#{comment_id}")
    _delete_likes_for(board_id, {comment_id})
    log_event(logger, logging.INFO, "comment deleted", board_id=board_id, comment_id=comment_id,
              by="author" if is_author else "owner")


# --- likes ----------------------------------------------------------------------------------

def toggle_like(board_id: str, viewer: Viewer, slide_id=None, comment_id=None) -> dict:
    board = get_board_meta(board_id)
    if board.get("locked") and not (viewer.user and spaces.role_of(board["space_id"], viewer.user)):
        raise ApiError(423, "board_locked", "This board is closed")
    if bool(slide_id) == bool(comment_id):
        raise ApiError(400, "invalid_input", "Pass exactly one of 'slide_id' or 'comment_id'")
    target = slide_id or comment_id
    target_sk = f"SLIDE#{slide_id}" if slide_id else f"COMMENT#{comment_id}"
    if not isinstance(target, str) or not db.get(f"BOARD#{board_id}", target_sk):
        raise not_found("Slide" if slide_id else "Comment")
    like_key = (f"BOARD#{board_id}", f"LIKE#{target}#{viewer.key}")
    if db.put({"PK": like_key[0], "SK": like_key[1], "target": target, "viewer": viewer.key,
               "created_at": now_iso()}, if_absent=True):
        db.increment(f"BOARD#{board_id}", target_sk, "likes", 1)
        liked = True
    else:
        db.delete(*like_key)
        db.increment(f"BOARD#{board_id}", target_sk, "likes", -1)
        liked = False
    item = db.get(f"BOARD#{board_id}", target_sk) or {}
    return {"liked": liked, "likes": max(0, item.get("likes", 0))}


# --- slides ---------------------------------------------------------------------------------

def add_slides(board_id: str, rendered: list[dict]) -> list[dict]:
    """Persist already-uploaded slide images (output of media.process_upload) at the end of the board."""
    existing = db.query(f"BOARD#{board_id}", "SLIDE#")
    pos = max((s["position"] for s in existing), default=-1)
    created = []
    for r in rendered:
        pos += 1
        item = {"PK": f"BOARD#{board_id}", "SK": f"SLIDE#{r['slide_id']}", "slide_id": r["slide_id"],
                "key": r["key"], "width": r["width"], "height": r["height"], "source": r["source"],
                "position": pos, "likes": 0, "created_at": now_iso()}
        db.put(item)
        created.append({k: item[k] for k in ("slide_id", "position", "width", "height", "source")})
    db.update(f"BOARD#{board_id}", "META", {"updated_at": now_iso()})
    return created


def reorder_slides(user: dict, board_id: str, slide_ids) -> list[str]:
    require_board(user, board_id, "editor")
    existing = {s["slide_id"] for s in db.query(f"BOARD#{board_id}", "SLIDE#")}
    if not isinstance(slide_ids, list) or set(slide_ids) != existing or len(slide_ids) != len(existing):
        raise ApiError(400, "invalid_input", "'slide_ids' must list every slide of the board exactly once")
    for pos, sid in enumerate(slide_ids):
        db.update(f"BOARD#{board_id}", f"SLIDE#{sid}", {"position": pos})
    log_event(logger, logging.INFO, "slides reordered", board_id=board_id)
    return slide_ids


def _remove_slides(board_id: str, slide_ids: set[str]) -> None:
    data = load(board_id)
    doomed_comments = {c["comment_id"] for c in data["comments"] if c["slide_id"] in slide_ids}
    keys = [(s["PK"], s["SK"]) for s in data["slides"] if s["slide_id"] in slide_ids]
    keys += [(c["PK"], c["SK"]) for c in data["comments"] if c["comment_id"] in doomed_comments]
    keys += [(l["PK"], l["SK"]) for l in data["likes"] if l["target"] in slide_ids | doomed_comments]
    db.batch_delete(keys)
    media.delete_keys([s["key"] for s in data["slides"] if s["slide_id"] in slide_ids])


def delete_slide(user: dict, board_id: str, slide_id: str) -> None:
    require_board(user, board_id, "owner")
    if not db.get(f"BOARD#{board_id}", f"SLIDE#{slide_id}"):
        raise not_found("Slide")
    _remove_slides(board_id, {slide_id})
    log_event(logger, logging.INFO, "slide deleted", board_id=board_id, slide_id=slide_id)


def clear_slides(board_id: str) -> int:
    """Remove all slides, comments and likes (no permission check — callers check owner)."""
    ids = {s["slide_id"] for s in db.query(f"BOARD#{board_id}", "SLIDE#")}
    if ids:
        _remove_slides(board_id, ids)
    # comments that somehow reference no slide are removed too
    leftovers = db.query(f"BOARD#{board_id}", "COMMENT#") + db.query(f"BOARD#{board_id}", "LIKE#")
    db.batch_delete([(i["PK"], i["SK"]) for i in leftovers])
    db.update(f"BOARD#{board_id}", "META", {"updated_at": now_iso()})
    return len(ids)
