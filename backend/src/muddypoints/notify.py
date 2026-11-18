"""Emails to a board's staff whenever its content changes (manual replace/clear or scheduled switch).

Every owner and editor of the workspace gets them, unless they switched them off for that board
(`BOARD#<bid> / NOTIFYOFF#<uid>`).
"""
import logging

from . import boards, db, mailer
from .log import get_logger, log_event

logger = get_logger(__name__)


def is_enabled(board_id: str, user_id: str) -> bool:
    return db.get(f"BOARD#{board_id}", f"NOTIFYOFF#{user_id}") is None


def set_enabled(user: dict, board_id: str, enabled: bool) -> dict:
    boards.require_board(user, board_id, "editor")
    if enabled:
        db.delete(f"BOARD#{board_id}", f"NOTIFYOFF#{user['user_id']}")
    else:
        db.put({"PK": f"BOARD#{board_id}", "SK": f"NOTIFYOFF#{user['user_id']}", "user_id": user["user_id"]})
    return {"enabled": bool(enabled)}


def recipients(board: dict, exclude_user_id: str | None = None) -> list[dict]:
    members = db.query(f"SPACE#{board['space_id']}", "MEMBER#")
    off = {i["user_id"] for i in db.query(f"BOARD#{board['board_id']}", "NOTIFYOFF#")}
    ids = [m["user_id"] for m in members if m["user_id"] not in off and m["user_id"] != exclude_user_id]
    return db.batch_get([(f"USER#{uid}", "PROFILE") for uid in ids])


def board_changed(board_id: str, headline: str, details: str = "", actor: dict | None = None) -> int:
    """Send '<board>: <headline>' to all staff of the board (except the person who did it). Returns #mails."""
    board = boards.get_board_meta(board_id)
    sent = 0
    for person in recipients(board, actor["user_id"] if actor and actor.get("user_id") != "system" else None):
        body = (f"Hi {person['name']},\n\n{headline}\n\n" + (f"{details}\n\n" if details else "")
                + f"Board: {board['name']}\n{boards.board_url(board_id)}\n\n"
                "The comments of the previous material are archived as PDF (board → ⚙ → Archive).\n"
                "To stop these emails for this board: board → ⚙ → Notifications.\n")
        if mailer.send(person["email"], f"[Muddy Points] {board['name']}: {headline}", body):
            sent += 1
    log_event(logger, logging.INFO, "board change notified", board_id=board_id, recipients=sent)
    return sent
