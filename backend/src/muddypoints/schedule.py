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


GENERIC_TITLES = {"powerpoint presentation", "powerpoint-präsentation", "presentation", "präsentation", "untitled",
                  "microsoft powerpoint"}


MAX_FILES_PER_SESSION = 10


def session_files(s: dict) -> list[dict]:
    """[{file_id, sha256, name}] - sessions can hold several files (older sessions: one file in top-level fields)."""
    return s.get("files") or [{"file_id": s.get("file_id"), "sha256": s.get("sha256"), "name": s.get("name")}]


def session_title(s: dict) -> str:
    """Stored title, unless it is a meaningless embedded PDF title (older sessions) -> the file name(s)."""
    title = s.get("title") or ""
    if not title or title.strip().lower() in GENERIC_TITLES or title.lower().startswith("microsoft powerpoint - "):
        return " + ".join(library.pretty_name(f.get("name") or "Session") for f in session_files(s))
    return title


def _files_for(board: dict, spec: dict) -> list[dict]:
    ids = spec.get("file_ids") if spec.get("file_ids") is not None else [spec.get("file_id")]
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
        raise ApiError(400, "invalid_input", "Each session needs file_id or a non-empty list file_ids")
    if len(ids) > MAX_FILES_PER_SESSION:
        raise ApiError(400, "invalid_input", f"At most {MAX_FILES_PER_SESSION} files per session")
    files = []
    for fid in dict.fromkeys(ids):  # keep order, drop duplicates
        f = library.get_file(board["space_id"], fid)
        files.append({"file_id": f["file_id"], "sha256": f["sha256"], "name": f["name"]})
    return files


def _file_fields(files: list[dict]) -> dict:
    # top-level fields mirror the first file (compatibility with single-file sessions)
    return {"files": files, "file_id": files[0]["file_id"], "sha256": files[0]["sha256"], "name": files[0]["name"]}


def _public(s: dict, tz: ZoneInfo) -> dict:
    files = [{"file_id": f["file_id"], "name": f["name"], "title": library.pretty_name(f["name"] or "")}
             for f in session_files(s)]
    return {"session_id": s["session_id"], "title": session_title(s), "files": files,
            "file_id": s.get("file_id"), "name": s.get("name"),
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
    files = _files_for(board, spec)
    starts_at = parse_time(spec.get("starts_at"), tz)
    title = clean_name(spec.get("title") or " + ".join(library.pretty_name(f["name"]) for f in files), "title", 160)
    sid = new_id()
    return {"PK": f"BOARD#{board['board_id']}", "SK": f"SESSION#{sid}", "session_id": sid, **_file_fields(files),
            "title": title, "starts_at": starts_at, "status": "planned",
            "created_by": user["name"], "created_at": now_iso(),
            "GSI1PK": "SCHED", "GSI1SK": f"{starts_at}#{board['board_id']}#{sid}"}


def add_session(user: dict, board_id: str, spec: dict) -> dict:
    board, _ = boards.require_board(user, board_id, "editor")
    item = _new_session(user, board, spec)
    db.put(item)
    _refresh_next_switch(board_id)
    log_event(logger, logging.INFO, "session planned", board_id=board_id, session_id=item["session_id"],
              starts_at=item["starts_at"])
    out = _public(item, board_tz(board))
    warnings = _warnings([item], board_tz(board), [x for x in _sessions(board_id) if x["SK"] != item["SK"]])
    return {**out, "warnings": warnings} if warnings else out


def run_now(user: dict, board_id: str, session_id: str) -> dict:
    """Make a session live immediately (keeps the schedule in sync). A 'done' session is copied (its history stays)."""
    board, _ = boards.require_board(user, board_id, "editor")
    s = db.get(f"BOARD#{board_id}", f"SESSION#{session_id}")
    if not s:
        raise not_found("Session", session_id)
    if s["status"] in ("live", "switching"):
        raise ApiError(409, "session_live", "This session is already live")
    now = _now()
    if s["status"] == "done":
        copy = _new_session(user, board, {"file_ids": [f["file_id"] for f in session_files(s)], "starts_at": now,
                                          "title": session_title(s)})
        db.put(copy)
        session_id = copy["session_id"]
    else:  # planned / skipped / failed
        db.update(s["PK"], s["SK"], {"status": "planned", "starts_at": now, "error": None, "GSI1PK": "SCHED",
                                     "GSI1SK": f"{now}#{board_id}#{session_id}"})
    result = advance(board_id, now)
    if not result or result.get("session_id") != session_id:
        raise ApiError(409, "not_switched", f"The switch did not happen: {result}")
    return {**get_schedule(user, board_id), "switch": result}


PAST_NOTE = ("is in the past: it goes live at the next scheduler run (within 5 minutes). If several sessions are "
             "in the past, only the latest of them goes live; the earlier ones are marked 'skipped'.")


CLOSE_MINUTES = 60


def _warnings(items: list[dict], tz: ZoneInfo, others: list[dict] | None = None) -> list[str]:
    """Non-blocking hints: past times, sessions at the same/very close times, the same file in several sessions.
    `others` = sessions that stay planned besides the new ones (checked together with them)."""
    now = _now()
    out = [f"Session '{session_title(i)}' at {local(i['starts_at'], tz)} {PAST_NOTE}" for i in items
           if i["starts_at"] <= now]
    planned = sorted(items + [o for o in (others or []) if o.get("status") == "planned"], key=lambda x: x["starts_at"])
    to_dt = lambda iso: datetime.fromisoformat(iso.replace("Z", "+00:00"))
    for a, b in zip(planned, planned[1:]):
        gap = (to_dt(b["starts_at"]) - to_dt(a["starts_at"])).total_seconds() / 60
        if gap == 0:
            out.append(f"Sessions '{session_title(a)}' and '{session_title(b)}' start at the same time "
                       f"({local(a['starts_at'], tz)}); only the later one would stay visible.")
        elif gap < CLOSE_MINUTES:
            out.append(f"Sessions '{session_title(a)}' and '{session_title(b)}' start only {gap:.0f} minutes apart "
                       f"({local(a['starts_at'], tz)} / {local(b['starts_at'], tz)}) - intended?")
    seen: dict[str, list[dict]] = {}
    names: dict[str, str] = {}
    for sess in planned:
        for f in session_files(sess):
            seen.setdefault(f["file_id"], []).append(sess)
            names[f["file_id"]] = library.pretty_name(f.get("name") or "")
    for file_id, sessions in seen.items():
        if len(sessions) > 1:
            out.append(f"File '{names[file_id]}' is scheduled in {len(sessions)} sessions: "
                       + ", ".join(local(x["starts_at"], tz) for x in sessions) + " - intended?")
    return out


def set_schedule(user: dict, board_id: str, sessions, replace_planned: bool = True, dry_run: bool = False) -> dict:
    """Replace all *planned* sessions with the given list (done/live sessions are kept as history).
    dry_run=True validates and returns the resulting schedule + warnings without saving anything."""
    board, _ = boards.require_board(user, board_id, "editor")
    if not isinstance(sessions, list) or len(sessions) > 100:
        raise ApiError(400, "invalid_input", "'sessions' must be a list (max 100)")
    items = [_new_session(user, board, spec) for spec in sessions]  # validate everything first
    tz = board_tz(board)
    kept_planned = [] if replace_planned else [x for x in _sessions(board_id) if x["status"] == "planned"]
    warnings = _warnings(items, tz, kept_planned)
    if dry_run:
        kept = [s for s in _sessions(board_id) if not (replace_planned and s["status"] == "planned")]
        preview = sorted(kept + items, key=lambda s: (s["starts_at"], s["SK"]))
        return {"dry_run": True, "saved": False, "board": board["name"], "timezone": str(tz),
                "sessions": [_public(s, tz) for s in preview], "warnings": warnings}
    if replace_planned:
        db.batch_delete([(s["PK"], s["SK"]) for s in _sessions(board_id) if s["status"] == "planned"])
    for item in items:
        db.put(item)
    _refresh_next_switch(board_id)
    log_event(logger, logging.INFO, "schedule set", board_id=board_id, sessions=len(items))
    return {**get_schedule(user, board_id), "saved": True, "warnings": warnings}


def update_session(user: dict, board_id: str, session_id: str, starts_at=None, title=None, file_id=None,
                   file_ids=None) -> dict:
    board, _ = boards.require_board(user, board_id, "editor")
    s = db.get(f"BOARD#{board_id}", f"SESSION#{session_id}")
    if not s:
        raise not_found("Session", session_id)
    if s["status"] != "planned":
        raise ApiError(409, "not_planned", "Only planned sessions can be changed")
    tz, fields = board_tz(board), {}
    if starts_at is not None:
        fields["starts_at"] = parse_time(starts_at, tz)
        fields["GSI1SK"] = f"{fields['starts_at']}#{board_id}#{session_id}"
    if title is not None:
        fields["title"] = clean_name(title, "title", 160)
    if file_ids is not None or file_id is not None:
        fields.update(_file_fields(_files_for(board, {"file_ids": file_ids} if file_ids is not None else
                                              {"file_id": file_id})))
    if fields:
        s = db.update(f"BOARD#{board_id}", f"SESSION#{session_id}", fields)
        _refresh_next_switch(board_id)
    return _public(s, tz)


def remove_session(user: dict, board_id: str, session_id: str, dry_run: bool = False) -> dict | None:
    boards.require_board(user, board_id, "editor")
    s = db.get(f"BOARD#{board_id}", f"SESSION#{session_id}")
    if not s:
        raise not_found("Session", session_id)
    if s["status"] in ("switching", "live"):
        raise ApiError(409, "session_live", "The live session can't be removed; schedule the next one instead")
    if dry_run:
        return boards.dry(f"remove the {s['status']} session '{session_title(s)}' ({s['starts_at']})")
    db.delete(f"BOARD#{board_id}", f"SESSION#{session_id}")
    _refresh_next_switch(board_id)


def upcoming(board_id: str) -> dict | None:
    """The next planned session (for 'coming soon' on an empty board)."""
    planned = [s for s in _sessions(board_id) if s["status"] == "planned"]
    if not planned:
        return None
    board = boards.get_board_meta(board_id)
    return {"title": session_title(planned[0]), "starts_at": planned[0]["starts_at"],
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
        files = session_files(target)
        blobs = [(media.get_bytes(f"blobs/{f['sha256']}.pdf"), f["name"]) for f in files]
        result = uploads._ingest(SYSTEM_USER, board_id, blobs, "replace", True,
                                 source_files=[{"file_id": f.get("file_id"), "name": f["name"]} for f in files],
                                 notify=False)
    except Exception as exc:
        message = getattr(exc, "message", None) or repr(exc)
        db.update(*key, {"status": "failed", "error": message[:500]})
        _refresh_next_switch(board_id)
        logger.exception("scheduled switch failed", extra={"data": {"board_id": board_id}})
        notify.board_changed(board_id, f"Scheduled switch to “{session_title(target)}” FAILED",
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
    notify.board_changed(board_id, f"Now showing “{session_title(target)}”",
                         f"Scheduled switch at {local(target['starts_at'], board_tz(board))}.")
    return {"status": "live", "session_id": target["session_id"], "skipped": len(skipped),
            "archived": bool(result.get("archive")), **({"archive_skipped": result["archive_skipped"]}
                                                       if result.get("archive_skipped") else {})}


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
