"""Feedback / feature-idea form: stored in DynamoDB and mailed to the maintainer (FEEDBACK_EMAIL, private)."""
import logging
import time

from . import db, mailer
from .config import settings
from .http import ApiError
from .log import get_logger, log_event
from .util import EMAIL_RE, now_iso, sortable_id

logger = get_logger(__name__)
MAX_PER_HOUR_PER_IP = 5


def submit(data: dict, ip: str, user: dict | None) -> dict:
    if (data.get("website") or "").strip():  # honeypot field, invisible to humans
        log_event(logger, logging.INFO, "feedback honeypot triggered")
        return {"ok": True, "mailed": True}
    message = (data.get("message") or "").strip() if isinstance(data.get("message"), str) else ""
    if len(message) < 5:
        raise ApiError(400, "invalid_input", "Please write a few words")
    if len(message) > 5000:
        raise ApiError(400, "invalid_input", "Please keep it under 5000 characters")
    email = (data.get("email") or "").strip() if isinstance(data.get("email"), str) else ""
    if email and not EMAIL_RE.match(email):
        raise ApiError(400, "invalid_email", "Please enter a valid email address (or leave it empty)")
    hour = int(time.time() // 3600)
    if db.bump_counter(f"RATE#feedback#{ip or '?'}", str(hour), int(time.time()) + 7200) > MAX_PER_HOUR_PER_IP:
        raise ApiError(429, "too_many_requests", "Thanks! You've sent a lot of feedback already — please try later.")
    page = str(data.get("page") or "")[:300]
    sender = f"{user['name']} <{user['email']}>" if user else (email or "anonymous")
    item_id = sortable_id()
    db.put({"PK": "FEEDBACK", "SK": f"FEEDBACK#{item_id}", "message": message, "email": email or None,
            "user_id": user["user_id"] if user else None, "page": page, "created_at": now_iso()})
    log_event(logger, logging.INFO, "feedback received", feedback_id=item_id, chars=len(message))
    mailed = False
    if settings.feedback_email:
        mailed = mailer.send(settings.feedback_email, "Muddy Points feedback",
                             f"From: {sender}\nPage: {page}\nTime: {now_iso()}\n\n{message}\n",
                             reply_to=email or (user["email"] if user else None))
    else:
        log_event(logger, logging.WARNING, "feedback stored but not mailed (FEEDBACK_EMAIL unset)", feedback_id=item_id)
    # `mailed` lets the page say the truth: the message is always stored, but only "sent" if the mail went out
    return {"ok": True, "mailed": mailed}
