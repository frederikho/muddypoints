"""Operator view: all workspaces on the platform. Read-only, for the superadmin(s) in SUPERADMIN_EMAILS."""
import logging

from . import db, spaces
from .config import settings
from .http import ApiError
from .log import get_logger, log_event

logger = get_logger(__name__)


def is_superadmin(user: dict | None) -> bool:
    """The email must be listed AND verified, otherwise anyone could register that address first."""
    return bool(user and user.get("email_verified_at") and user["email"] in settings.superadmin_emails)


def require_superadmin(user: dict) -> None:
    if not is_superadmin(user):
        raise ApiError(404, "not_found", "Not found")  # 404 like other hidden resources: do not reveal the feature


def list_all_spaces(user: dict) -> list[dict]:
    require_superadmin(user)
    ids = db.all_space_ids()
    log_event(logger, logging.INFO, "admin listed all spaces", user_id=user["user_id"], spaces=len(ids))
    return spaces.overview({sid: None for sid in ids}, with_emails=True)
