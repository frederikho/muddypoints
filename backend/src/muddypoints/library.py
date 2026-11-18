"""Workspace files (PDF library), deduplicated storage, temporary upload ("drop") links, board history.

Files are PERMANENT and shared by all members of a workspace (reuse across semesters). Only upload links expire.
A file never appears on a board by itself: it has to be published (by a person, by Claude, or by the schedule).

Storage is content-addressed: every distinct PDF is stored once, keyed by its SHA-256, no matter how many
workspaces use it. The browser hashes files before uploading and skips files the server already has; the server
re-hashes every upload, so a client cannot register content under a wrong hash.

S3:
  blobs/<sha256>.pdf          the PDF
  blobs/<sha256>.text.json    extracted text, one string per page
  uploads/drop/<id>/<name>    raw upload before verification (expires after 1 day via lifecycle rule)
DynamoDB:
  BLOB#<sha256>       META               size, pages, title, outline, chars   (global, extraction cache)
  SPACE#<sid>         FILE#<file_id>     sha256, name, title, pages, size, outline, uploaded_at/by, published_to
  SPACE#<sid>         FILEHASH#<sha256>  file_id                             (dedup within the workspace)
  DROP#<sha256(tok)>  DROP               space_id, created_by, expires_at, max_files, file_count, ttl
  BOARD#<bid>         HIST#<sortable_id> file_id, name, mode, at, by, archive_key  (what went live when)
"""
import hashlib
import json
import logging
import secrets
import time
from datetime import datetime, timezone

import pymupdf

from . import auth, aws, boards, db, media, spaces
from .config import settings
from .http import ApiError, not_found
from .log import get_logger, log_event
from .util import new_id, now_iso, safe_filename, sortable_id

logger = get_logger(__name__)
DEFAULT_LINK_HOURS = 24
MAX_LINK_HOURS = 24 * 7
MAX_FILES_PER_LINK = 200
DEFAULT_READ_CHARS = 15000
MAX_READ_CHARS = 60000
PDF = "application/pdf"


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pretty_name(filename: str) -> str:
    """'OdN_VL_9_-_Stated_Preference_Methods.pdf' -> 'OdN VL 9 - Stated Preference Methods' (used as the title:
    embedded PDF titles are mostly useless, e.g. 'PowerPoint Presentation')."""
    base = filename[:-4] if filename.lower().endswith(".pdf") else filename
    return " ".join(base.replace("_", " ").replace("+", " ").split()) or filename


def _is_sha256(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


# --- blobs (content-addressed storage) ------------------------------------------------------

def _blob_key(sha: str) -> str:
    return f"blobs/{sha}.pdf"


def _extract(data: bytes, name: str) -> dict:
    if not data.startswith(b"%PDF"):
        raise ApiError(400, "unsupported_file", f"'{name}' is not a PDF. Only PDF files are accepted.")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        raise ApiError(400, "unsupported_file", f"'{name}' is not a readable PDF")
    with doc:
        if doc.needs_pass:
            raise ApiError(400, "unsupported_file", f"'{name}' is password protected")
        texts = [page.get_text().strip() for page in doc]
        first_line = next((line.strip() for t in texts[:2] for line in t.splitlines() if line.strip()), "")
        title = ((doc.metadata or {}).get("title") or "").strip() or first_line[:120] or name
        outline = [{"level": lvl, "title": t[:120], "page": p} for lvl, t, p in doc.get_toc()[:60] if lvl <= 2]
        return {"pages": doc.page_count, "title": title, "outline": outline, "texts": texts,
                "chars": sum(len(t) for t in texts)}


def _ensure_blob(data: bytes, name: str) -> dict:
    """Store bytes under their SHA-256 (once) and return the blob metadata."""
    sha = hashlib.sha256(data).hexdigest()
    meta = db.get(f"BLOB#{sha}", "META")
    if meta:
        return meta
    info = _extract(data, name)
    media.put_bytes(_blob_key(sha), data, PDF)
    media.put_bytes(f"blobs/{sha}.text.json", json.dumps(info["texts"], ensure_ascii=False).encode(), "application/json")
    meta = {"PK": f"BLOB#{sha}", "SK": "META", "sha256": sha, "size": len(data), "pages": info["pages"],
            "title": info["title"], "outline": info["outline"], "chars": info["chars"], "created_at": now_iso()}
    db.put(meta)
    log_event(logger, logging.INFO, "blob stored", sha256=sha[:12], pages=info["pages"], bytes=len(data))
    return meta


def _entry_for_blob(space_id: str, meta: dict, name: str, by: str) -> tuple[dict, bool]:
    """Workspace file entry for a blob; returns (entry, created). Same content twice -> same entry."""
    sha = meta["sha256"]
    ref = db.get(f"SPACE#{space_id}", f"FILEHASH#{sha}")
    if ref:
        existing = db.get(f"SPACE#{space_id}", f"FILE#{ref['file_id']}")
        if existing:
            return existing, False
    file_id = sortable_id()
    entry = {"PK": f"SPACE#{space_id}", "SK": f"FILE#{file_id}", "file_id": file_id, "sha256": sha,
             "name": safe_filename(name), "title": pretty_name(safe_filename(name)), "pdf_title": meta["title"],
             "pages": meta["pages"], "size": meta["size"],
             "outline": meta.get("outline", []), "chars": meta.get("chars", 0), "uploaded_at": now_iso(),
             "uploaded_by": by, "published_to": []}
    if not db.put({"PK": f"SPACE#{space_id}", "SK": f"FILEHASH#{sha}", "file_id": file_id}, if_absent=True):
        ref = db.get(f"SPACE#{space_id}", f"FILEHASH#{sha}")  # lost a race with a parallel upload
        return db.get(f"SPACE#{space_id}", f"FILE#{ref['file_id']}"), False
    db.put(entry)
    log_event(logger, logging.INFO, "file added", space_id=space_id, file_id=file_id, sha256=sha[:12])
    return entry, True


def add_bytes(space_id: str, data: bytes, name: str, by: str) -> tuple[dict, bool]:
    """Register a PDF (e.g. uploaded via a board's panel) in the workspace files. Returns (entry, created)."""
    return _entry_for_blob(space_id, _ensure_blob(data, name), name, by)


# --- upload links ---------------------------------------------------------------------------

def create_link(user: dict, space_id: str, hours=DEFAULT_LINK_HOURS, note=None) -> dict:
    space, _ = spaces.require_space(user, space_id, "editor")
    try:
        hours = max(1, min(int(hours or DEFAULT_LINK_HOURS), MAX_LINK_HOURS))
    except (TypeError, ValueError):
        raise ApiError(400, "invalid_input", "'hours' must be a number")
    token = secrets.token_urlsafe(24)
    expires = int(time.time()) + hours * 3600
    db.put({"PK": f"DROP#{auth.sha256(token)}", "SK": "DROP", "space_id": space_id, "created_by": user["name"],
            "note": (str(note)[:200] if note else None), "expires_at": expires, "max_files": MAX_FILES_PER_LINK,
            "file_count": 0, "ttl": expires + 86400})
    log_event(logger, logging.INFO, "upload link created", space_id=space_id, hours=hours)
    url = f"{settings.frontend_url}/drop.html#{token}"
    return {"upload_link": url, "workspace": space["name"], "expires_at": _iso(expires), "valid_hours": hours,
            "tell_the_user": f"Open this link and drag your PDFs in (no login needed; the link works for {hours} h, "
                             f"the files are kept permanently in the workspace '{space['name']}'). Tell me when "
                             f"you're done: {url}"}


def _link(token: str) -> dict:
    item = db.get(f"DROP#{auth.sha256(token)}", "DROP") if token else None
    if not item:
        raise ApiError(404, "invalid_link", "This upload link is invalid. Ask for a new one.")
    if item["expires_at"] < time.time():
        raise ApiError(410, "link_expired", "This upload link has expired. Ask for a new one.")
    return item


def _count(token: str, link: dict) -> None:
    if link.get("file_count", 0) >= link["max_files"]:
        raise ApiError(429, "too_many_files", f"This link accepts at most {link['max_files']} files")
    db.increment(f"DROP#{auth.sha256(token)}", "DROP", "file_count", 1)
    link["file_count"] = link.get("file_count", 0) + 1


def link_info(token: str) -> dict:
    link = _link(token)
    space = db.get(f"SPACE#{link['space_id']}", "META") or {}
    return {"workspace": space.get("name", "?"), "created_by": link.get("created_by"), "note": link.get("note"),
            "expires_at": _iso(link["expires_at"]), "files_left": link["max_files"] - link.get("file_count", 0)}


def check(token: str, files) -> list[dict]:
    """Step 1 of the drop page: the browser sends name/size/sha256 per file. Known content is linked without upload."""
    link = _link(token)
    if not isinstance(files, list) or not files or len(files) > MAX_FILES_PER_LINK:
        raise ApiError(400, "invalid_input", f"'files' must be a list of 1-{MAX_FILES_PER_LINK} files")
    out = []
    for f in files:
        f = f if isinstance(f, dict) else {}
        name, sha = str(f.get("name") or "file.pdf"), str(f.get("sha256") or "").lower()
        if f.get("content_type") != PDF and not name.lower().endswith(".pdf"):
            out.append({"name": name, "status": "rejected", "error": "Only PDF files are accepted"})
            continue
        if not _is_sha256(sha):
            raise ApiError(400, "invalid_input", "Each file needs its SHA-256 hash")
        ref = db.get(f"SPACE#{link['space_id']}", f"FILEHASH#{sha}")
        if ref:
            out.append({"name": name, "status": "exists", "file_id": ref["file_id"]})
            continue
        meta = db.get(f"BLOB#{sha}", "META")
        if meta:  # content already stored (e.g. by another workspace): just add an entry, no upload needed
            _count(token, link)
            entry, _ = _entry_for_blob(link["space_id"], meta, name, f"upload link of {link.get('created_by')}")
            out.append({"name": name, "status": "added", "file_id": entry["file_id"]})
            continue
        key = f"uploads/drop/{new_id(12)}/{safe_filename(name)}"
        out.append({"name": name, "status": "upload", "upload_key": key, "upload_url": media.presigned_put(key, PDF),
                    "headers": {"Content-Type": PDF}})
    return out


def confirm(token: str, upload_key, sha256) -> dict:
    """Step 2 (after the PUT): verify the hash, store the blob once, add the workspace entry."""
    link = _link(token)
    if not isinstance(upload_key, str) or not upload_key.startswith("uploads/drop/") or ".." in upload_key:
        raise ApiError(400, "invalid_input", "Invalid upload key")
    data = media.get_bytes(upload_key)
    actual = hashlib.sha256(data).hexdigest()
    media.delete_keys([upload_key])
    if sha256 and actual != str(sha256).lower():
        raise ApiError(400, "hash_mismatch", "The file changed during upload. Please try again.")
    name = upload_key.rsplit("/", 1)[-1]
    _count(token, link)
    entry, created = add_bytes(link["space_id"], data, name, f"upload link of {link.get('created_by')}")
    return {"name": name, "status": "added" if created else "exists", "file_id": entry["file_id"],
            "title": pretty_name(entry["name"]), "pages": entry["pages"]}


# --- workspace files (staff / Claude) -------------------------------------------------------

META_FIELDS = ("lecture_number", "topic", "semester", "tags", "notes")


def _public(item: dict) -> dict:
    out = {k: item.get(k) for k in ("file_id", "name", "pages", "size", "outline", "uploaded_at", "uploaded_by",
                                    "published_to")}
    out["title"] = pretty_name(item["name"])  # always the file name (older entries stored the embedded PDF title)
    out["meta"] = {k: item[k] for k in META_FIELDS if item.get(k) not in (None, "", [])}
    return out


def _scheduled_by_file(space_id: str) -> dict[str, list[dict]]:
    """file_id -> planned/live sessions on the workspace's boards (so the file list can show 'scheduled on …')."""
    out: dict[str, list[dict]] = {}
    for board in db.query_gsi1(f"SPACE#{space_id}", "BOARD#"):
        for s in db.query(f"BOARD#{board['board_id']}", "SESSION#"):
            if s.get("status") not in ("planned", "live", "switching"):
                continue
            file_ids = [f["file_id"] for f in s.get("files") or []] or [s.get("file_id")]
            for fid in filter(None, file_ids):
                out.setdefault(fid, []).append({"board_id": board["board_id"], "board": board["name"],
                                                "starts_at": s["starts_at"], "status": s["status"]})
    for sessions in out.values():
        sessions.sort(key=lambda x: x["starts_at"])
    return out


def list_files(user: dict, space_id: str, with_outline: bool = True, query: str | None = None,
               semester: str | None = None, tag: str | None = None) -> list[dict]:
    """Workspace files, newest first, with metadata, publish history and planned/live sessions.
    Optional filters: query (words in name/topic/tags/notes), semester, tag."""
    spaces.require_space(user, space_id, "editor")
    items = sorted(db.query(f"SPACE#{space_id}", "FILE#"), key=lambda i: i["SK"], reverse=True)
    scheduled = _scheduled_by_file(space_id)
    out = []
    for item in items:
        f = _public(item)
        f["scheduled"] = scheduled.get(f["file_id"], [])
        meta = f["meta"]
        if semester and str(meta.get("semester", "")).lower() != str(semester).lower():
            continue
        if tag and str(tag).lower() not in [t.lower() for t in meta.get("tags", [])]:
            continue
        if query:
            hay = " ".join([f["title"], f["name"], str(meta.get("topic", "")), " ".join(meta.get("tags", [])),
                            str(meta.get("notes", "")), str(meta.get("lecture_number", ""))]).lower()
            if not all(w in hay for w in str(query).lower().split()):
                continue
        if not with_outline:
            f.pop("outline", None)
        out.append(f)
    return out


def update_metadata(user: dict, space_id: str, file_id: str, fields: dict) -> dict:
    """Set lecture_number / topic / semester / tags / notes (None or "" clears a field)."""
    spaces.require_space(user, space_id, "editor")
    get_file(space_id, file_id)
    updates, clear = {}, []
    for key in META_FIELDS:
        if key not in fields:
            continue
        value = fields[key]
        if value in (None, "", []):
            clear.append(key)
        elif key == "lecture_number":
            try:
                updates[key] = int(value)
            except (TypeError, ValueError):
                raise ApiError(400, "invalid_input", "lecture_number must be a whole number")
        elif key == "tags":
            if not isinstance(value, list):
                raise ApiError(400, "invalid_input", "tags must be a list of strings")
            updates[key] = [str(t).strip()[:40] for t in value if str(t).strip()][:20]
        else:
            updates[key] = str(value).strip()[:500 if key == "notes" else 120]
    if updates:
        db.update(f"SPACE#{space_id}", f"FILE#{file_id}", updates)
    if clear:
        db.remove_attrs(f"SPACE#{space_id}", f"FILE#{file_id}", clear)
    return _public(get_file(space_id, file_id))


def import_url(user: dict, space_id: str, url) -> dict:
    """Download a PDF from a URL (direct link or Drive/Dropbox/Seafile/Nextcloud share link) into the files."""
    spaces.require_space(user, space_id, "editor")
    data, name = media.download(url)
    if not name.lower().endswith(".pdf"):
        name = (name or "download") + ".pdf"
    entry, created = add_bytes(space_id, data, name, user["name"])
    return {**_public(entry), "status": "added" if created else "exists"}


def get_file(space_id: str, file_id: str) -> dict:
    item = db.get(f"SPACE#{space_id}", f"FILE#{file_id}") if file_id else None
    if not item:
        raise not_found("File", file_id)
    return item


def download_url(user: dict, space_id: str, file_id: str, inline: bool = False) -> dict:
    """Presigned link to the PDF: as download, or inline (opens in the browser's PDF viewer)."""
    spaces.require_space(user, space_id, "editor")
    item = get_file(space_id, file_id)
    return {"download_url": media.presigned_get(_blob_key(item["sha256"]), item["name"], ttl=3600, inline=inline)}


def file_bytes(item: dict) -> bytes:
    return media.get_bytes(_blob_key(item["sha256"]))


def read(user: dict, space_id: str, file_id: str, from_page=1, to_page=None, max_chars=DEFAULT_READ_CHARS) -> dict:
    """Text of a page range, capped at max_chars; returns next_from_page to continue."""
    spaces.require_space(user, space_id, "editor")
    item = get_file(space_id, file_id)
    texts = json.loads(media.get_bytes(f"blobs/{item['sha256']}.text.json"))
    try:
        start = max(1, int(from_page or 1))
        end = min(len(texts), int(to_page)) if to_page else len(texts)
        budget = max(1000, min(int(max_chars or DEFAULT_READ_CHARS), MAX_READ_CHARS))
    except (TypeError, ValueError):
        raise ApiError(400, "invalid_input", "from_page, to_page and max_chars must be numbers")
    pages, used, page_no = [], 0, start
    while page_no <= end:
        text = texts[page_no - 1]
        if pages and used + len(text) > budget:
            break
        pages.append({"page": page_no, "text": text[:budget]})
        used += len(text)
        page_no += 1
    return {"file_id": file_id, "name": item["name"], "total_pages": len(texts), "pages": pages,
            "next_from_page": page_no if page_no <= end else None}


def publish(user: dict, space_id: str, file_id: str, board_id: str, mode="append", archive=True) -> dict:
    from . import uploads  # uploads imports library
    item = get_file(space_id, file_id)
    board = boards.get_board_meta(board_id)
    if board["space_id"] != space_id:
        raise ApiError(400, "invalid_input", "The board belongs to a different workspace than the file")
    mode = uploads._check_mode(user, board_id, mode)
    return uploads._ingest(user, board_id, [(file_bytes(item), item["name"])], mode, archive,
                           source_files=[item])


def mark_published(space_id: str, file_id: str, board: dict) -> None:
    item = db.get(f"SPACE#{space_id}", f"FILE#{file_id}")
    if item:
        used = (item.get("published_to") or []) + [{"board_id": board["board_id"], "board": board["name"],
                                                     "at": now_iso()}]
        db.update(f"SPACE#{space_id}", f"FILE#{file_id}", {"published_to": used[-50:]})


def delete(user: dict, space_id: str, file_id: str, dry_run: bool = False) -> dict | None:
    spaces.require_space(user, space_id, "editor")
    item = get_file(space_id, file_id)
    if dry_run:
        return boards.dry(f"delete '{pretty_name(item['name'])}' from the workspace files (boards that show it are not affected)")
    db.delete(f"SPACE#{space_id}", f"FILE#{file_id}")
    db.delete(f"SPACE#{space_id}", f"FILEHASH#{item['sha256']}")
    # The blob itself stays (it may be used by other workspaces; storage is cheap).
    log_event(logger, logging.INFO, "file deleted", space_id=space_id, file_id=file_id, name=item["name"])


def page_image(user: dict, space_id: str, file_id: str, page: int = 1, max_side: int = 1400) -> tuple[bytes, dict]:
    """JPEG of one page (for Claude to look at scanned slides, diagrams, formulas)."""
    spaces.require_space(user, space_id, "editor")
    item = get_file(space_id, file_id)
    try:
        page = int(page or 1)
    except (TypeError, ValueError):
        raise ApiError(400, "invalid_input", "'page' must be a number")
    with pymupdf.open(stream=file_bytes(item), filetype="pdf") as doc:
        if not 1 <= page <= doc.page_count:
            raise ApiError(400, "invalid_input", f"'page' must be between 1 and {doc.page_count}")
        p = doc[page - 1]
        zoom = min(2.0, max_side / max(p.rect.width, p.rect.height, 1))
        pix = p.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("jpg", jpg_quality=80), {"file": item["name"], "page": page, "pages": doc.page_count}


MAX_SHEET_PAGES = 12


def contact_sheet(user: dict, space_id: str, file_id: str, from_page=1, to_page=None) -> tuple[bytes, dict]:
    """Up to 12 pages as a labelled thumbnail grid in ONE image (cheap overview for Claude)."""
    spaces.require_space(user, space_id, "editor")
    item = get_file(space_id, file_id)
    with pymupdf.open(stream=file_bytes(item), filetype="pdf") as src:
        try:
            start = max(1, int(from_page or 1))
            end = min(src.page_count, int(to_page) if to_page else start + MAX_SHEET_PAGES - 1, start + MAX_SHEET_PAGES - 1)
        except (TypeError, ValueError):
            raise ApiError(400, "invalid_input", "from_page and to_page must be numbers")
        if start > src.page_count:
            raise ApiError(400, "invalid_input", f"The file has only {src.page_count} pages")
        numbers = list(range(start, end + 1))
        cols = 3 if len(numbers) > 4 else 2 if len(numbers) > 1 else 1
        rows = -(-len(numbers) // cols)
        first = src[start - 1].rect
        cell_w, label_h, gap = 400.0, 18.0, 10.0
        cell_h = cell_w * first.height / max(first.width, 1)
        sheet = pymupdf.open()
        page = sheet.new_page(width=cols * (cell_w + gap) + gap, height=rows * (cell_h + label_h + gap) + gap)
        for i, n in enumerate(numbers):
            x = gap + (i % cols) * (cell_w + gap)
            y = gap + (i // cols) * (cell_h + label_h + gap)
            page.insert_text((x, y + 13), f"Page {n}", fontsize=12, color=(0.25, 0.25, 0.25))
            rect = pymupdf.Rect(x, y + label_h, x + cell_w, y + label_h + cell_h)
            page.draw_rect(rect, color=(0.8, 0.8, 0.8), width=0.5)
            page.show_pdf_page(rect, src, n - 1)
        zoom = min(2.0, 1800 / page.rect.width)
        jpeg = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("jpg", jpg_quality=78)
        sheet.close()
        return jpeg, {"file": item["name"], "pages_shown": [numbers[0], numbers[-1]], "total_pages": src.page_count}


# --- board history --------------------------------------------------------------------------

def record_history(board_id: str, by: str, mode: str, files: list[dict], archive_key: str | None,
                   archive_skipped: str | None = None) -> None:
    db.put({"PK": f"BOARD#{board_id}", "SK": f"HIST#{sortable_id()}", "at": now_iso(), "by": by, "mode": mode,
            "files": [{"file_id": f.get("file_id"), "name": f.get("name"),
                       "title": pretty_name(f["name"]) if f.get("name") else f.get("title")} for f in files],
            "archive_key": archive_key, "archive_skipped": archive_skipped})


def history(user: dict, board_id: str) -> list[dict]:
    board, _ = boards.require_board(user, board_id, "editor")
    items = sorted(db.query(f"BOARD#{board_id}", "HIST#"), key=lambda i: i["SK"], reverse=True)
    return [{"at": i["at"], "by": i.get("by"), "mode": i.get("mode"),
             "files": [{**f, "title": pretty_name(f["name"]) if f.get("name") else f.get("title")}
                       for f in i.get("files", [])],
             "archived_previous_material": bool(i.get("archive_key")),
             **({"archive_skipped": i["archive_skipped"]} if i.get("archive_skipped") else {}),
             "workspace_id": board["space_id"]}
            for i in items]
