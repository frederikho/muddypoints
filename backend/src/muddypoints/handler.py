"""Lambda entry point. Routes /api/* to the REST router and /mcp* to the MCP server."""
import logging
import re
import time
import traceback

from . import log, mcp
from .http import ApiError, Request, error_response, json_response
from .routes import router
from .util import new_id

logger = log.get_logger("muddypoints.handler")


_SECRET_PATHS = [(re.compile(r"^/mcp/[^/]+"), "/mcp/<key>"), (re.compile(r"^/api/drop/[^/]+"), "/api/drop/<token>")]


def redact_path(path: str) -> str:
    """Never log secrets that live in the URL path (MCP API keys, upload-link tokens)."""
    for pattern, replacement in _SECRET_PATHS:
        path = pattern.sub(replacement, path)
    return path


def lambda_handler(event: dict, context) -> dict:
    if event.get("source") == "muddypoints.scheduler":  # EventBridge Scheduler, every 5 minutes
        log.clear()
        log.bind(request_id=getattr(context, "aws_request_id", None) or new_id(), channel="scheduler")
        from . import schedule
        try:
            return {"ok": True, "boards": schedule.run_due()}
        except Exception:
            logger.exception("scheduler run crashed")
            return {"ok": False}
    started = time.time()
    log.clear()
    req = Request.from_event(event)
    # Never log the secret part of /mcp/<api key>.
    safe_path = redact_path(req.path)
    log.bind(request_id=getattr(context, "aws_request_id", None) or new_id(), method=req.method, path=safe_path)
    status = 500
    try:
        if req.path == "/mcp" or req.path.startswith("/mcp/"):
            response = mcp.handle(req)
        else:
            result = router.resolve(req)(req)
            response = json_response(200, result)
    except ApiError as err:
        response = error_response(err)
        if err.status >= 500:
            logger.error("api error", extra={"data": {"code": err.code, "error": err.message}})
    except Exception as exc:  # unexpected bug: log full traceback, return a generic error
        logger.error("unhandled exception", extra={"data": {"error": repr(exc), "exc": traceback.format_exc()}})
        response = error_response(ApiError(500, "internal_error", "Something went wrong (see logs)"))
    status = response["statusCode"]
    log.log_event(logger, logging.WARNING if status >= 500 else logging.INFO, "request",
                  status=status, duration_ms=int((time.time() - started) * 1000))
    return response
