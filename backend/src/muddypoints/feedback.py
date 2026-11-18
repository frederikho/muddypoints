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
MAX_MCP_PER_DAY_PER_USER = 10
CATEGORIES = ("bug", "missing_feature", "confusing", "idea", "other")

# Optional context about the user's teaching. Only what the user agreed to share (see MCP instructions).
# Enums keep it comparable and free of personal data; the few free-text fields are short.
CONTEXT_ENUMS = {
    "role": ("lecturer", "tutor", "teaching_assistant", "admin", "other"),
    "teaching_format": ("lecture", "seminar", "flipped_classroom", "online", "other"),
    "course_size": ("<30", "30-100", "100-300", ">300"),
    "semester_phase": ("preparation", "during_semester", "wrap_up"),
    "ai_client": ("claude_ai", "claude_desktop", "claude_code", "chatgpt", "mobile", "other"),
    "tech_comfort": ("low", "medium", "high"),
}
CONTEXT_TEXT = {"discipline": 80, "course_language": 30, "goal": 300}
CONTEXT_FIELDS = (*CONTEXT_ENUMS, *CONTEXT_TEXT, "other_tools")


def clean_context(context) -> dict:
    """Validate the optional context block (unknown keys and empty values are dropped)."""
    if context in (None, ""):
        return {}
    if not isinstance(context, dict):
        raise ApiError(400, "invalid_input", "'context' must be an object")
    out = {}
    legacy = {"desktop_app": "claude_desktop"}
    if "claude_client" in context and "ai_client" not in context:  # older field name
        context = {**context, "ai_client": legacy.get(context["claude_client"], context["claude_client"])}
    for key, value in context.items():
        if value in (None, "", []):
            continue
        if key in CONTEXT_ENUMS:
            if value not in CONTEXT_ENUMS[key]:
                raise ApiError(400, "invalid_input", f"'{key}' must be one of: {', '.join(CONTEXT_ENUMS[key])}")
            out[key] = value
        elif key in CONTEXT_TEXT:
            out[key] = str(value).strip()[:CONTEXT_TEXT[key]]
        elif key == "other_tools":
            if not isinstance(value, list):
                raise ApiError(400, "invalid_input", "'other_tools' must be a list")
            out[key] = [str(t).strip()[:40] for t in value if str(t).strip()][:10]
    return out


def _context_lines(context: dict) -> str:
    if not context:
        return ""
    return "Context (shared by the user):\n" + "".join(
        f"  {k}: {', '.join(v) if isinstance(v, list) else v}\n" for k, v in context.items()) + "\n"


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
    context = clean_context(data.get("context"))
    sender = f"{user['name']} <{user['email']}>" if user else (email or "anonymous")
    item_id = sortable_id()
    db.put({"PK": "FEEDBACK", "SK": f"FEEDBACK#{item_id}", "message": message, "email": email or None,
            "user_id": user["user_id"] if user else None, "page": page, "created_at": now_iso(),
            "source": "web", "context": context or None})
    log_event(logger, logging.INFO, "feedback received", feedback_id=item_id, chars=len(message))
    mailed = False
    if settings.feedback_email:
        mailed = mailer.send(settings.feedback_email, "Muddy Points feedback",
                             f"From: {sender}\nPage: {page}\nTime: {now_iso()}\n\n{message}\n\n{_context_lines(context)}",
                             reply_to=email or (user["email"] if user else None))
    else:
        log_event(logger, logging.WARNING, "feedback stored but not mailed (FEEDBACK_EMAIL unset)", feedback_id=item_id)
    # `mailed` lets the page say the truth: the message is always stored, but only "sent" if the mail went out
    return {"ok": True, "mailed": mailed}


def assistant_name(agent: str) -> str:
    """Best guess from the MCP request's User-Agent ('Claude-User', 'openai-mcp/…', …)."""
    a = (agent or "").lower()
    if "claude" in a or "anthropic" in a:
        return "Claude"
    if "openai" in a or "chatgpt" in a:
        return "ChatGPT"
    return "AI assistant"


def submit_from_assistant(user: dict, category, summary, details, context=None, agent: str = "") -> dict:
    """Feedback an AI assistant (Claude, ChatGPT, …) sends via MCP about Muddy Points itself."""
    if category not in CATEGORIES:
        raise ApiError(400, "invalid_input", f"'category' must be one of: {', '.join(CATEGORIES)}")
    summary = (summary or "").strip() if isinstance(summary, str) else ""
    details = (details or "").strip() if isinstance(details, str) else ""
    if not 5 <= len(summary) <= 200:
        raise ApiError(400, "invalid_input", "'summary' must be 5-200 characters")
    if len(details) > 5000:
        raise ApiError(400, "invalid_input", "'details' must be at most 5000 characters")
    context = clean_context(context)
    day = int(time.time() // 86400)
    if db.bump_counter(f"RATE#mcpfeedback#{user['user_id']}", str(day), int(time.time()) + 2 * 86400) > MAX_MCP_PER_DAY_PER_USER:
        raise ApiError(429, "too_many_requests", "Feedback limit reached for today (10 per user). Try again tomorrow.")
    item_id = sortable_id()
    assistant = assistant_name(agent)
    db.put({"PK": "FEEDBACK", "SK": f"FEEDBACK#{item_id}", "source": "assistant", "assistant": assistant,
            "agent": (agent or "")[:120], "category": category,
            "summary": summary, "message": details or summary, "user_id": user["user_id"], "user_name": user["name"],
            "context": context or None, "created_at": now_iso()})
    log_event(logger, logging.INFO, "assistant feedback received", feedback_id=item_id, category=category,
              assistant=assistant)
    mailed = False
    if settings.feedback_email:
        mailed = mailer.send(settings.feedback_email, f"[Muddy Points · {assistant} feedback] {category}: {summary}",
                             f"From: {user['name']} <{user['email']}> (via {assistant})\nTime: {now_iso()}\n"
                             f"Category: {category}\n\n{summary}\n\n{details}\n\n{_context_lines(context)}",
                             reply_to=user["email"])
    return {"ok": True, "feedback_id": item_id, "mailed": mailed,
            "note": "Thank you - the maintainers receive this. Tell the user it was sent."}


def list_all(user: dict, limit: int = 50, only_open: bool = False, source: str | None = None) -> list[dict]:
    """Superadmin only: all feedback, newest first."""
    from . import admin
    admin.require_superadmin(user)
    items = sorted(db.query("FEEDBACK", "FEEDBACK#"), key=lambda i: i["SK"], reverse=True)
    out = []
    for i in items:
        if only_open and i.get("done"):
            continue
        item_source = "assistant" if i.get("source") in ("claude", "assistant") else i.get("source", "web")
        if source and item_source != ("assistant" if source == "claude" else source):
            continue
        out.append({"feedback_id": i["SK"].split("#", 1)[1], "source": item_source,
                    "assistant": i.get("assistant") or ("Claude" if i.get("source") == "claude" else None),
                    "category": i.get("category"), "summary": i.get("summary"), "message": i.get("message"),
                    "context": i.get("context") or {}, "page": i.get("page"), "email": i.get("email"),
                    "user_id": i.get("user_id"), "user_name": i.get("user_name"), "created_at": i.get("created_at"),
                    "done": bool(i.get("done"))})
        if len(out) >= max(1, min(int(limit or 50), 500)):
            break
    return out


def set_done(user: dict, feedback_id: str, done: bool = True) -> dict:
    from . import admin
    admin.require_superadmin(user)
    if not db.get("FEEDBACK", f"FEEDBACK#{feedback_id}"):
        raise ApiError(404, "not_found", "Feedback not found")
    db.update("FEEDBACK", f"FEEDBACK#{feedback_id}", {"done": bool(done)})
    return {"feedback_id": feedback_id, "done": bool(done)}
