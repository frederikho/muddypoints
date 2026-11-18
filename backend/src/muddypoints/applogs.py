"""Reading the app's own CloudWatch logs (exposed to admins via the MCP tool `get_logs`)."""
import json
import time

from . import aws, db
from .config import settings
from .http import ApiError, forbidden

LEVEL_PATTERNS = {
    "ERROR": '{ ($.level = "ERROR") || ($.level = "CRITICAL") }',
    "WARNING": '{ ($.level = "WARNING") || ($.level = "ERROR") || ($.level = "CRITICAL") }',
    "INFO": '{ $.level = * }',
}


def fetch(user: dict, minutes=60, level="INFO", contains=None, limit=100) -> dict:
    if not db.query_gsi1(f"USER#{user['user_id']}", "SPACE#"):
        raise forbidden("Only members of at least one workspace can read logs")
    if not settings.log_group:
        raise ApiError(503, "logs_unavailable", "LOG_GROUP_NAME is not configured (local dev?)")
    level = (level or "INFO").upper()
    if level not in LEVEL_PATTERNS:
        raise ApiError(400, "invalid_input", "level must be INFO, WARNING or ERROR")
    minutes = max(1, min(int(minutes or 60), 7 * 24 * 60))
    limit = max(1, min(int(limit or 100), 500))
    kwargs = {"logGroupName": settings.log_group, "startTime": int((time.time() - minutes * 60) * 1000),
              "filterPattern": LEVEL_PATTERNS[level]}
    events: list[dict] = []
    while True:
        resp = aws.logs().filter_log_events(**kwargs)
        for e in resp.get("events", []):
            try:
                entry = json.loads(e["message"])
            except json.JSONDecodeError:
                continue
            if contains and contains.lower() not in e["message"].lower():
                continue
            events.append(entry)
        token = resp.get("nextToken")
        if not token or len(events) >= 5000:
            break
        kwargs["nextToken"] = token
    return {"log_group": settings.log_group, "minutes": minutes, "level": level,
            "total_matched": len(events), "events": events[-limit:]}
