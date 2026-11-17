"""Accounts: registration, login, password reset, profile, API keys (for MCP)."""
import logging
import time

from . import auth, db, mailer
from .config import RESET_TTL_SECONDS, SESSION_TTL_SECONDS, settings
from .http import ApiError
from .log import get_logger, log_event
from .util import EMAIL_RE, clean_name, new_id, normalize_email, now_iso

logger = get_logger(__name__)
MIN_PASSWORD_LENGTH = 8


def public_user(item: dict) -> dict:
    return {"user_id": item["user_id"], "email": item["email"], "name": item["name"],
            "created_at": item.get("created_at")}


def _check_password(password) -> str:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise ApiError(400, "weak_password", f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > 256:
        raise ApiError(400, "invalid_input", "Password is too long")
    return password


def _check_email(email) -> str:
    email = normalize_email(email if isinstance(email, str) else "")
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise ApiError(400, "invalid_email", "Please enter a valid email address")
    return email


def get_user(user_id: str) -> dict | None:
    return db.get(f"USER#{user_id}", "PROFILE")


def get_user_by_email(email: str) -> dict | None:
    ref = db.get(f"EMAIL#{normalize_email(email)}", "EMAIL")
    return get_user(ref["user_id"]) if ref else None


def register(email, password, name) -> dict:
    email = _check_email(email)
    _check_password(password)
    name = clean_name(name, "name", 80)
    domains = settings.allowed_email_domains
    if domains and email.split("@", 1)[1] not in domains:
        raise ApiError(403, "domain_not_allowed",
                       "Registration is restricted to these email domains: " + ", ".join(domains))
    user_id = new_id(12)
    if not db.put({"PK": f"EMAIL#{email}", "SK": "EMAIL", "user_id": user_id}, if_absent=True):
        raise ApiError(409, "email_taken", "An account with this email already exists")
    item = {"PK": f"USER#{user_id}", "SK": "PROFILE", "user_id": user_id, "email": email, "name": name,
            "pw_hash": auth.hash_password(password), "pw_version": 1, "created_at": now_iso()}
    try:
        db.put(item)
    except Exception:
        db.delete(f"EMAIL#{email}", "EMAIL")
        raise
    log_event(logger, logging.INFO, "user registered", new_user_id=user_id)
    from . import spaces  # local import: spaces depends on users
    spaces.accept_pending_invites(item)
    return item


def issue_session(user: dict) -> str:
    return auth.sign({"t": "s", "uid": user["user_id"], "pv": user.get("pw_version", 1)}, SESSION_TTL_SECONDS)


def login(email, password) -> tuple[dict, str]:
    user = get_user_by_email(email if isinstance(email, str) else "")
    if not user or not auth.verify_password(password if isinstance(password, str) else "", user["pw_hash"]):
        log_event(logger, logging.INFO, "login failed")
        raise ApiError(401, "invalid_credentials", "Email or password is wrong")
    log_event(logger, logging.INFO, "login", user_id=user["user_id"])
    return user, issue_session(user)


def user_from_session(token: str) -> dict | None:
    payload = auth.unsign(token, "s")
    if not payload:
        return None
    user = get_user(payload["uid"])
    if not user or user.get("pw_version", 1) != payload.get("pv"):
        return None  # password changed since token was issued -> token revoked
    return user


def request_password_reset(email) -> None:
    """Always succeeds from the caller's view (no account enumeration)."""
    user = get_user_by_email(email if isinstance(email, str) else "")
    if not user:
        log_event(logger, logging.INFO, "password reset requested for unknown email")
        return
    token = auth.new_reset_token()
    db.put({"PK": f"RESET#{auth.sha256(token)}", "SK": "RESET", "user_id": user["user_id"],
            "ttl": int(time.time()) + RESET_TTL_SECONDS})
    link = f"{settings.frontend_url}/reset.html?token={token}"
    mailer.send(user["email"], "Reset your password",
                f"Hi {user['name']},\n\nopen this link within one hour to choose a new password:\n\n{link}\n\n"
                "If you did not request this, you can ignore this email.\n")
    log_event(logger, logging.INFO, "password reset requested", user_id=user["user_id"])


def _set_password(user: dict, password: str) -> dict:
    return db.update(f"USER#{user['user_id']}", "PROFILE",
                     {"pw_hash": auth.hash_password(password), "pw_version": user.get("pw_version", 1) + 1})


def reset_password(token, password) -> tuple[dict, str]:
    _check_password(password)
    if not isinstance(token, str) or not token:
        raise ApiError(400, "invalid_token", "Reset link is invalid or expired")
    key = f"RESET#{auth.sha256(token)}"
    ref = db.get(key, "RESET")
    if not ref or ref.get("ttl", 0) < time.time():
        raise ApiError(400, "invalid_token", "Reset link is invalid or expired")
    user = get_user(ref["user_id"])
    if not user:
        raise ApiError(400, "invalid_token", "Reset link is invalid or expired")
    db.delete(key, "RESET")
    user = _set_password(user, password)
    log_event(logger, logging.INFO, "password reset", user_id=user["user_id"])
    return user, issue_session(user)


def change_password(user: dict, old_password, new_password) -> str:
    if not auth.verify_password(old_password if isinstance(old_password, str) else "", user["pw_hash"]):
        raise ApiError(400, "invalid_credentials", "Current password is wrong")
    _check_password(new_password)
    user = _set_password(user, new_password)
    return issue_session(user)  # all other sessions are now invalid


def update_profile(user: dict, name) -> dict:
    return db.update(f"USER#{user['user_id']}", "PROFILE", {"name": clean_name(name, "name", 80)})


# --- API keys (used to authenticate the MCP connector) ---------------------------------------

def create_api_key(user: dict, label) -> dict:
    label = clean_name(label or "Claude", "label", 60)
    key, key_id = auth.new_api_key(), new_id(8)
    digest = auth.sha256(key)
    created = now_iso()
    db.put({"PK": f"APIKEY#{digest}", "SK": "APIKEY", "user_id": user["user_id"], "key_id": key_id})
    db.put({"PK": f"USER#{user['user_id']}", "SK": f"APIKEY#{key_id}", "key_id": key_id, "hash": digest,
            "label": label, "prefix": key[:7], "created_at": created})
    log_event(logger, logging.INFO, "api key created", key_id=key_id)
    return {"key_id": key_id, "label": label, "key": key, "created_at": created}


def list_api_keys(user: dict) -> list[dict]:
    return [{k: i.get(k) for k in ("key_id", "label", "prefix", "created_at", "last_used_at")}
            for i in db.query(f"USER#{user['user_id']}", "APIKEY#")]


def revoke_api_key(user: dict, key_id: str) -> None:
    item = db.get(f"USER#{user['user_id']}", f"APIKEY#{key_id}")
    if not item:
        raise ApiError(404, "not_found", "API key not found")
    db.delete(f"APIKEY#{item['hash']}", "APIKEY")
    db.delete(f"USER#{user['user_id']}", f"APIKEY#{key_id}")
    log_event(logger, logging.INFO, "api key revoked", key_id=key_id)


def user_from_api_key(key: str) -> dict | None:
    if not key or not key.startswith(auth.API_KEY_PREFIX):
        return None
    ref = db.get(f"APIKEY#{auth.sha256(key)}", "APIKEY")
    if not ref:
        return None
    try:
        db.update(f"USER#{ref['user_id']}", f"APIKEY#{ref['key_id']}", {"last_used_at": now_iso()})
    except Exception:
        pass
    return get_user(ref["user_id"])
