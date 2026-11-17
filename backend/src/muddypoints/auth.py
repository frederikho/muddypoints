"""Password hashing, signed tokens and API keys (stdlib only)."""
import base64
import hashlib
import hmac
import json
import secrets
import time

from .config import settings

PBKDF2_ITERATIONS = 310_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ITERATIONS).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$")
    except (ValueError, AttributeError):
        return False
    if algo != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
    return hmac.compare_digest(candidate, digest)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _mac(payload: str) -> str:
    return _b64(hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).digest())


def sign(payload: dict, ttl_seconds: int) -> str:
    """Create a compact signed token: base64(json).signature. Contains 'exp' (unix seconds)."""
    body = _b64(json.dumps({**payload, "exp": int(time.time()) + ttl_seconds}, separators=(",", ":")).encode())
    return f"{body}.{_mac(body)}"


def unsign(token: str, kind: str) -> dict | None:
    """Return the payload if the token is authentic, unexpired and of the given kind ('t' field)."""
    try:
        body, mac = token.split(".")
        if not hmac.compare_digest(mac, _mac(body)):
            return None
        payload = json.loads(_unb64(body))
    except (ValueError, AttributeError, json.JSONDecodeError):
        return None
    if payload.get("t") != kind or payload.get("exp", 0) < time.time():
        return None
    return payload


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


API_KEY_PREFIX = "pk_"


def new_api_key() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def new_reset_token() -> str:
    return secrets.token_urlsafe(32)
