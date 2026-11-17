"""Anonymous identities for students: random funny name + id, signed so it cannot be changed.

The frontend stores the token in localStorage and drops it after 7 days (token expiry).
"""
import functools
import secrets
from dataclasses import dataclass
from pathlib import Path

from . import auth
from .config import ANON_TTL_SECONDS
from .util import new_id


@functools.lru_cache(maxsize=1)
def names() -> list[str]:
    path = Path(__file__).parent / "data" / "names.txt"
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def new_identity() -> dict:
    anon_id, name = new_id(16), secrets.choice(names())
    token = auth.sign({"t": "a", "aid": anon_id, "name": name}, ANON_TTL_SECONDS)
    payload = auth.unsign(token, "a")
    return {"anon_id": anon_id, "name": name, "token": token, "expires_at": payload["exp"]}


@dataclass
class Viewer:
    """Whoever is looking at / interacting with a board: a logged-in user or an anonymous student."""
    key: str           # "u:<user_id>" or "a:<anon_id>" — used for likes and comment ownership
    name: str
    user: dict | None = None

    @property
    def is_user(self) -> bool:
        return self.user is not None


def viewer_from_anon_token(token: str) -> Viewer | None:
    payload = auth.unsign(token or "", "a")
    return Viewer(key=f"a:{payload['aid']}", name=payload["name"]) if payload else None


def viewer_from_user(user: dict) -> Viewer:
    return Viewer(key=f"u:{user['user_id']}", name=user["name"], user=user)
