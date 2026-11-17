import re
import secrets
import time
from datetime import datetime, timezone

_ALPHABET = "abcdefghijkmnopqrstuvwxyz23456789"  # no l/0/1 lookalikes
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def new_id(length: int = 10) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def sortable_id() -> str:
    """Time-ordered id (ms timestamp + random suffix), so SK order == creation order."""
    return f"{int(time.time() * 1000):013d}{new_id(5)}"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def clean_name(value, field: str = "name", max_len: int = 120) -> str:
    from .http import ApiError
    value = (value or "").strip() if isinstance(value, str) else ""
    if not value:
        raise ApiError(400, "invalid_input", f"'{field}' must not be empty")
    if len(value) > max_len:
        raise ApiError(400, "invalid_input", f"'{field}' is too long (max {max_len} characters)")
    return value


def safe_filename(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "file").strip())[-100:]
    return name.strip("._") or "file"
