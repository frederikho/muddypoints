"""Board schedules: a board (= course, one URL) shows a sequence of sessions; each session is a workspace file
that goes live at a given time, replacing the previous session (whose slides + comments are archived as PDF).

DynamoDB:
  BOARD#<bid>  SESSION#<session_id>  file_id, sha256, name, title, starts_at (UTC ISO), status, error,
                                     switched_at, archive_key; while status == planned also
                                     GSI1PK = "SCHED", GSI1SK = "<starts_at>#<bid>#<session_id>"
  BOARD#<bid>  META                  ... timezone (default Europe/Berlin), next_switch_at (earliest planned)

Statuses: planned -> switching -> live -> done   (or failed / skipped)

Switches are executed by `advance(board_id)`, which is called by
  * the scheduler (EventBridge, every 5 minutes -> handler -> run_due), and
  * every page load of the board whose next_switch_at has passed (so nobody ever sees stale material).
A conditional update (planned -> switching) guarantees each switch happens exactly once.
"""
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import boards, db, library, media, notify
from .http import ApiError, not_found
from .log import get_logger, log_event
from .util import clean_name, new_id, now_iso

logger = get_logger(__name__)
DEFAULT_TZ = "Europe/Berlin"
SYSTEM_USER = {"user_id": "system", "name": "Schedule"}


# --- time helpers ---------------------------------------------------------------------------

def board_tz(board: dict) -> ZoneInfo:
    try:
        return ZoneInfo(board.get("timezone") or DEFAULT_TZ)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TZ)


def parse_time(value, tz: ZoneInfo) -> str:
    """Accept ISO-8601 with offset/Z, or local 'YYYY-MM-DDTHH:MM' (interpreted in the board's time zone).
    Returns UTC 'YYYY-MM-DDTHH:MM:SSZ'."""
    if not isinstance(value, str) or not value.strip():
        raise ApiError(400, "invalid_input", "'starts_at' must be a date-time like 2026-10-20T16:00")
    raw = value.strip().replace(" ", "T")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        raise ApiError(400, "invalid_input", f"Can't read the date-time '{value}' (use e.g. 2026-10-20T16:00)")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local(utc_iso: str | None, tz: ZoneInfo) -> str | None:
    if not utc_iso:
        return None
    return datetime.fromisoformat(utc_iso.replace("Z", "+00:00")).astimezone(tz).strftime("%a %Y-%m-%d %H:%M")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- reading & editing the schedule ---------------------------------------------------------

def _sessions(board_id: str) -> list[dict]:
    return sorted(db.query(f"BOARD#{board_id}", "SESSION#"), key=lambda s: (s["starts_at"], s["SK"]))


def _public(s: dict, tz: ZoneInfo) -> dict:
    return {"session_id": s["session_id"], "title": s["title"], "file_id": s.get("file_id"), "name": s.get("name"),
            "starts_at": s["starts_at"], "starts_at_local": local(s["starts_at"], tz), "status": s["status"],
            "switched_at": s.get("switched_at"), "error": s.get("error")}


def _refresh_next_switch(board_id: str) -> None:
    planned = [s["starts_at"] for s in _sessions(board_id) if s["status"] == "planned"]
    if planned:
        db.update(f"BOARD#{board_id}", "META", {"next_switch_at": min(planned)})
    else:
        db.remove_attrs(f"BOARD#{board_id}", "META", ["next_switch_at"])


def get_schedule(user: dict, board_id: str) -> dict:
    board, _ = boards.require_board(user, board_id, "editor")
    tz = board_tz(board)
    return {"board": board["name"], "timezone": str(tz), "now_local": local(_now(), tz),
            "sessions": [_public(s, tz) for s in _sessions(board_id)]}


def _new_session(user: dict, board: dict, spec: dict) -> dict:
    if not isinstance(spec, dict):
        raise ApiError(400, "invalid_input", "Each session must be an object with file_id and starts_at")
    tz = board_tz(board)
    f = library.get_file(board["space_id"], spec.get("file_id"))
    starts_at = parse_time(spec.get("starts_at"), tz)
    title = clean_name(spec.get("title") or f.get("title") or f["name"], "title", 160)
    sid = new_id()
    return {"PK": f"BOARD#{board['board_id']}", "SK": f"SESSION#{sid}", "session_id": sid, "file_id": f["file_id"],
            "sha256": f["sha256"], "name": f["name"], "title": title, "starts_at": starts_at, "status": "planned",
            "created_by": user["name"], "created_at": now_iso(),
            "GSI1PK": "SCHED", "GSI1SK": f"{starts_at}#{board['board_id']}#{sid}"}


def add_session(user: dict, board_id: str, spec: dict) -> dict:
    board, _ = boards.require_board(user, board_id, "editor")
    item = _new_session(user, board, spec)
    db.put(item)
    _refresh_next_switch(board_id)
    log_event(logger, logging.INFO, "session planned", board_id=board_id, session_id=item["session_id"],
              starts_at=item["starts_at"])
    return _public(item, board_tz(board))


def set_schedule(user: dict, board_id: str, sessions, replace_planned: bool = True) -> dict:
    """Replace all *planned* sessions with the given list (done/live sessions are kept as history)."""
    board, _ = boards.require_board(user, board_id, "editor")
    if not isinstance(sessions, list) or len(sessions) > 100:
        raise ApiError(400, "invalid_input", "'sessions' must be a list (max 100)")
    items = [_new_session(user, board, spec) for spec in sessions]  # validate everything first
    if replace_planned:
        db.batch_delete([(s["PK"], s["SK"]) for s in _sessions(board_id) if s["status"] == "planned"])
    for item in items:
        db.put(item)
    _refresh_next_switch(board_id)
    log_event(logger, logging.INFO, "schedule set", board_id=board_id, sessions=len(items))
    return get_schedule(user, board_id)


def update_session(user: dict, board_id: str, session_id: str, starts_at=None, title=None, file_id=None) -> dict:
    board, _ = boards.require_board(user, board_id, "editor")
    s = db.get(f"BOARD#{board_id}", f"SESSION#{session_id}")
    if not s:
        raise not_found("Session")
    if s["status"] != "planned":
        raise ApiError(409, "not_planned", "Only planned sessions can be changed")
    tz, fields = board_tz(board), {}
    if starts_at is not None:
        fields["starts_at"] = parse_time(starts_at, tz)
        fields["GSI1SK"] = f"{fields['starts_at']}#{board_id}#{session_id}"
    if title is not None:
        fields["title"] = clean_name(title, "title", 160)
    if file_id is not None:
        f = library.get_file(board["space_id"], file_id)
        fields.update({"file_id": f["file_id"], "sha256": f["sha256"], "name": f["name"]})
    if fields:
        s = db.update(f"BOARD#{board_id}", f"SESSION#{session_id}", fields)
        _refresh_next_switch(board_id)
    return _public(s, tz)


def remove_session(user: dict, board_id: str, session_id: str) -> None:
    boards.require_board(user, board_id, "editor")
    s = db.get(f"BOARD#{board_id}", f"SESSION#{session_id}")
    if not s:
        raise not_found("Session")
    if s["status"] in ("switching", "live"):
        raise ApiError(409, "session_live", "The live session can't be removed; schedule the next one instead")
    db.delete(f"BOARD#{board_id}", f"SESSION#{session_id}")
    _refresh_next_switch(board_id)


def upcoming(board_id: str) -> dict | None:
    """The next planned session (for 'coming soon' on an empty board)."""
    planned = [s for s in _sessions(board_id) if s["status"] == "planned"]
    if not planned:
        return None
    board = boards.get_board_meta(board_id)
    return {"title": planned[0]["title"], "starts_at": planned[0]["starts_at"],
            "starts_at_local": local(planned[0]["starts_at"], board_tz(board))}


# --- executing switches ---------------------------------------------------------------------

def advance(board_id: str, now: str | None = None) -> dict | None:
    """Make the latest due session live (exactly once). Earlier due sessions that never ran are 'skipped'."""
    from . import uploads  # uploads -> library -> ... (avoid import cycle at module load)
    now = now or _now()
    due = [s for s in _sessions(board_id) if s["status"] == "planned" and s["starts_at"] <= now]
    if not due:
        return None
    target, skipped = due[-1], due[:-1]
    key = (f"BOARD#{board_id}", f"SESSION#{target['session_id']}")
    if not db.update_if(*key, {"status": "switching"}, "status", "planned"):
        return None  # another invocation is doing this switch
    db.remove_attrs(*key, ["GSI1PK", "GSI1SK"])
    for s in skipped:
        if db.update_if(s["PK"], s["SK"], {"status": "skipped"}, "status", "planned"):
            db.remove_attrs(s["PK"], s["SK"], ["GSI1PK", "GSI1SK"])
    board = boards.get_board_meta(board_id)
    try:
        data = media.get_bytes(f"blobs/{target['sha256']}.pdf")
        result = uploads._ingest(SYSTEM_USER, board_id, [(data, target["name"])], "replace", True,
                                 source_files=[{"file_id": target.get("file_id"), "name": target["name"],
                                                "title": target["title"]}], notify=False)
    except Exception as exc:
        message = getattr(exc, "message", None) or repr(exc)
        db.update(*key, {"status": "failed", "error": message[:500]})
        _refresh_next_switch(board_id)
        logger.exception("scheduled switch failed", extra={"data": {"board_id": board_id}})
        notify.board_changed(board_id, f"Scheduled switch to “{target['title']}” FAILED",
                             f"Error: {message}\nThe board still shows the previous material.")
        return {"status": "failed", "error": message}
    for s in _sessions(board_id):
        if s["status"] == "live":
            db.update(s["PK"], s["SK"], {"status": "done", "ended_at": now_iso(),
                                         "archive_key": (result.get("archive") or {}).get("key")})
    db.update(*key, {"status": "live", "switched_at": now_iso()})
    if board.get("locked"):
        db.update(f"BOARD#{board_id}", "META", {"locked": False})  # new material starts with open comments
    _refresh_next_switch(board_id)
    log_event(logger, logging.INFO, "scheduled switch done", board_id=board_id, session_id=target["session_id"],
              skipped=len(skipped))
    notify.board_changed(board_id, f"Now showing “{target['title']}”",
                         f"Scheduled switch at {local(target['starts_at'], board_tz(board))}.")
    return {"status": "live", "session_id": target["session_id"], "skipped": len(skipped)}


def advance_if_due(board: dict) -> None:
    """Cheap check used on every board page load."""
    nxt = board.get("next_switch_at")
    if nxt and nxt <= _now():
        try:
            advance(board["board_id"])
        except Exception:
            logger.exception("advance on page load failed", extra={"data": {"board_id": board["board_id"]}})


def run_due() -> dict:
    """Entry point for the scheduler: switch every board that has a due session."""
    now = _now()
    due = db.query_gsi1_until("SCHED", now + "~")
    board_ids = list(dict.fromkeys(i["GSI1SK"].split("#")[1] for i in due))
    results = {}
    for bid in board_ids:
        try:
            results[bid] = advance(bid, now)
        except Exception as exc:  # one broken board must not stop the others
            logger.exception("scheduler: board failed", extra={"data": {"board_id": bid}})
            results[bid] = {"status": "error", "error": repr(exc)}
    log_event(logger, logging.INFO, "scheduler run", due_boards=len(board_ids))
    return results
