"""Spaces, memberships and the two-role permission model.

owner  — can do everything (incl. deleting spaces/boards/slides/comments, managing members)
editor — everything except deleting things and managing roles/removing members
"""
import logging

from . import db, mailer, users
from .config import settings
from .http import ApiError, forbidden, not_found
from .log import get_logger, log_event
from .util import EMAIL_RE, clean_name, new_id, normalize_email, now_iso

logger = get_logger(__name__)
ROLES = ("editor", "owner")
RANK = {"editor": 1, "owner": 2}


def _check_role(role) -> str:
    if role not in ROLES:
        raise ApiError(400, "invalid_role", f"Role must be one of: {', '.join(ROLES)}")
    return role


def role_of(space_id: str, user: dict | None) -> str | None:
    if not user:
        return None
    member = db.get(f"SPACE#{space_id}", f"MEMBER#{user['user_id']}")
    return member["role"] if member else None


def require_space(user: dict, space_id: str, min_role: str = "editor") -> tuple[dict, str]:
    """Return (space_meta, role). Non-members get 404 so space ids do not leak."""
    space = db.get(f"SPACE#{space_id}", "META")
    role = role_of(space_id, user) if space else None
    if not space or not role:
        raise not_found("Space")
    if RANK[role] < RANK[min_role]:
        raise forbidden(f"Only space {min_role}s can do this")
    return space, role


def public_space(space: dict, role: str | None = None) -> dict:
    out = {"space_id": space["space_id"], "name": space["name"], "created_at": space.get("created_at")}
    if role:
        out["role"] = role
    return out


def _member_item(space_id: str, user_id: str, role: str) -> dict:
    return {"PK": f"SPACE#{space_id}", "SK": f"MEMBER#{user_id}", "space_id": space_id, "user_id": user_id,
            "role": role, "joined_at": now_iso(), "GSI1PK": f"USER#{user_id}", "GSI1SK": f"SPACE#{space_id}"}


def create_space(user: dict, name) -> dict:
    space_id = new_id()
    space = {"PK": f"SPACE#{space_id}", "SK": "META", "space_id": space_id, "name": clean_name(name),
             "created_by": user["user_id"], "created_at": now_iso()}
    db.put(space)
    db.put(_member_item(space_id, user["user_id"], "owner"))
    log_event(logger, logging.INFO, "space created", space_id=space_id)
    return public_space(space, "owner")


def list_spaces(user: dict) -> list[dict]:
    """The user's spaces with role, counts and most recent activity (newest activity first)."""
    memberships = db.query_gsi1(f"USER#{user['user_id']}", "SPACE#")
    roles = {m["space_id"]: m["role"] for m in memberships}
    out = []
    for space in db.batch_get([(f"SPACE#{sid}", "META") for sid in roles]):
        sid = space["space_id"]
        boards = db.query_gsi1(f"SPACE#{sid}", "BOARD#")
        activity = [(max(filter(None, (b.get("updated_at"), b.get("last_activity_at"))), default=""), b["name"])
                    for b in boards]
        latest = max(activity, default=(None, None))
        out.append({**public_space(space, roles[sid]), "board_count": len(boards),
                    "member_count": len(db.query(f"SPACE#{sid}", "MEMBER#")),
                    "last_activity_at": latest[0] or space.get("created_at"), "last_activity_board": latest[1]})
    return sorted(out, key=lambda s: s["last_activity_at"] or "", reverse=True)


def rename_space(user: dict, space_id: str, name) -> dict:
    _, role = require_space(user, space_id, "editor")
    space = db.update(f"SPACE#{space_id}", "META", {"name": clean_name(name)})
    return public_space(space, role)


def delete_space(user: dict, space_id: str) -> None:
    require_space(user, space_id, "owner")
    from . import boards
    for board in db.query_gsi1(f"SPACE#{space_id}", "BOARD#"):
        boards.purge_board(board["board_id"])
    db.batch_delete([(i["PK"], i["SK"]) for i in db.query(f"SPACE#{space_id}")])
    log_event(logger, logging.INFO, "space deleted", space_id=space_id)


# --- members ----------------------------------------------------------------------------------

def list_members(user: dict, space_id: str) -> dict:
    require_space(user, space_id, "editor")
    items = db.query(f"SPACE#{space_id}")
    members = [i for i in items if i["SK"].startswith("MEMBER#")]
    profiles = {u["user_id"]: u for u in db.batch_get([(f"USER#{m['user_id']}", "PROFILE") for m in members])}
    return {
        "members": sorted(
            ({"user_id": m["user_id"], "role": m["role"], "joined_at": m.get("joined_at"),
              "name": profiles.get(m["user_id"], {}).get("name", "?"),
              "email": profiles.get(m["user_id"], {}).get("email", "?")} for m in members),
            key=lambda m: (-RANK[m["role"]], m["name"].lower())),
        "invites": [{"email": i["email"], "role": i["role"], "invited_at": i.get("invited_at")}
                    for i in items if i["SK"].startswith("INVITE#")],
    }


def invite(user: dict, space_id: str, email, role="editor") -> dict:
    """Add an existing user directly, or store a pending invite applied when they register."""
    role = _check_role(role)
    space, my_role = require_space(user, space_id, "editor")
    if role == "owner" and my_role != "owner":
        raise forbidden("Only owners can add other owners")
    email = normalize_email(email if isinstance(email, str) else "")
    if not EMAIL_RE.match(email):
        raise ApiError(400, "invalid_email", "Please enter a valid email address")
    invitee = users.get_user_by_email(email)
    link = settings.frontend_url + "/"
    if invitee:
        if role_of(space_id, invitee):
            raise ApiError(409, "already_member", "This user is already a member of the space")
        db.put(_member_item(space_id, invitee["user_id"], role))
        mailer.send(email, f"You were added to '{space['name']}'",
                    f"Hi {invitee['name']},\n\n{user['name']} added you as {role} to the space "
                    f"'{space['name']}'.\nLog in here: {link}\n")
        log_event(logger, logging.INFO, "member added", space_id=space_id, member_id=invitee["user_id"], role=role)
        return {"status": "added", "email": email, "role": role, "user_id": invitee["user_id"]}
    db.put({"PK": f"SPACE#{space_id}", "SK": f"INVITE#{email}", "space_id": space_id, "email": email, "role": role,
            "invited_by": user["user_id"], "invited_at": now_iso(),
            "GSI1PK": f"INVITE#{email}", "GSI1SK": f"SPACE#{space_id}"})
    mailer.send(email, f"Invitation to '{space['name']}'",
                f"Hi,\n\n{user['name']} invited you as {role} to the space '{space['name']}'.\n"
                f"Register with this email address to get access: {link}\n")
    log_event(logger, logging.INFO, "invite created", space_id=space_id, role=role)
    return {"status": "invited", "email": email, "role": role}


def accept_pending_invites(user: dict) -> None:
    for inv in db.query_gsi1(f"INVITE#{user['email']}", "SPACE#"):
        db.put(_member_item(inv["space_id"], user["user_id"], inv["role"]))
        db.delete(f"SPACE#{inv['space_id']}", f"INVITE#{user['email']}")
        log_event(logger, logging.INFO, "invite accepted", space_id=inv["space_id"], role=inv["role"])


def revoke_invite(user: dict, space_id: str, email: str) -> None:
    require_space(user, space_id, "owner")
    if not db.delete(f"SPACE#{space_id}", f"INVITE#{normalize_email(email)}", if_exists=True):
        raise not_found("Invite")


def _owner_count(space_id: str) -> int:
    return sum(1 for m in db.query(f"SPACE#{space_id}", "MEMBER#") if m["role"] == "owner")


def set_role(user: dict, space_id: str, member_id: str, role) -> None:
    role = _check_role(role)
    require_space(user, space_id, "owner")
    member = db.get(f"SPACE#{space_id}", f"MEMBER#{member_id}")
    if not member:
        raise not_found("Member")
    if member["role"] == "owner" and role != "owner" and _owner_count(space_id) <= 1:
        raise ApiError(409, "last_owner", "A space needs at least one owner")
    db.update(f"SPACE#{space_id}", f"MEMBER#{member_id}", {"role": role})
    log_event(logger, logging.INFO, "member role changed", space_id=space_id, member_id=member_id, role=role)


def remove_member(user: dict, space_id: str, member_id: str) -> None:
    """Owners can remove anyone; every member can remove themselves (leave)."""
    leaving = member_id == user["user_id"]
    require_space(user, space_id, "editor" if leaving else "owner")
    member = db.get(f"SPACE#{space_id}", f"MEMBER#{member_id}")
    if not member:
        raise not_found("Member")
    if member["role"] == "owner" and _owner_count(space_id) <= 1:
        raise ApiError(409, "last_owner", "A space needs at least one owner")
    db.delete(f"SPACE#{space_id}", f"MEMBER#{member_id}")
    log_event(logger, logging.INFO, "member removed", space_id=space_id, member_id=member_id)
