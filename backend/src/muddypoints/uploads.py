"""Getting new material onto a board: presigned uploads, URL imports, append/replace."""
import logging

from . import boards, export, library, media
from . import notify as notify_mod
from .http import ApiError
from .log import get_logger, log_event
from .util import new_id, safe_filename

logger = get_logger(__name__)
MODES = ("append", "replace")


def prepare(user: dict, board_id: str, files) -> list[dict]:
    """Step 1: get presigned PUT URLs. `files` = [{'filename': ..., 'content_type': ...}, ...]."""
    boards.require_board(user, board_id, "editor")
    if not isinstance(files, list) or not files or len(files) > 100:
        raise ApiError(400, "invalid_input", "'files' must be a list of 1-100 {filename, content_type} objects")
    out = []
    for f in files:
        ct = (f or {}).get("content_type", "") if isinstance(f, dict) else ""
        if ct not in media.ALLOWED_CONTENT_TYPES:
            raise ApiError(400, "unsupported_file",
                           f"Unsupported content type '{ct}'. Allowed: {', '.join(media.ALLOWED_CONTENT_TYPES)}")
        key = f"uploads/{board_id}/{new_id()}/{safe_filename(f.get('filename', 'file'))}"
        out.append({"upload_key": key, "upload_url": media.presigned_put(key, ct), "method": "PUT",
                    "headers": {"Content-Type": ct}})
    return out


def _check_mode(user: dict, board_id: str, mode) -> str:
    if mode not in MODES:
        raise ApiError(400, "invalid_input", "'mode' must be 'append' or 'replace'")
    boards.require_board(user, board_id, "editor")
    return mode


def _ingest(user: dict, board_id: str, blobs: list[tuple[bytes, str]], mode: str, archive: bool,
            source_files: list[dict] | None = None, notify: bool = True) -> dict:
    """Render files to slides and put them on the board. PDFs are also kept in the workspace files
    (deduplicated) and the event is recorded in the board history."""
    pages = []
    for data, name in blobs:  # render everything first: nothing changes if a file is broken
        pages.extend(media.render(data, name))
    if not pages:
        raise ApiError(400, "invalid_input", "No pages found in the uploaded files")
    board = boards.get_board_meta(board_id)
    if source_files is None:  # direct uploads / URL imports: keep PDFs in the workspace files for later reuse
        source_files = []
        for data, name in blobs:
            if data.startswith(b"%PDF"):
                entry, _ = library.add_bytes(board["space_id"], data, name, user["name"])
                source_files.append(entry)
            else:
                source_files.append({"file_id": None, "name": name, "title": name})
    result: dict = {"mode": mode}
    archive_info = None
    if mode == "replace":
        if archive:
            archive_info = result["archive"] = export.archive_if_needed(board_id)
        result["removed_slides"] = boards.clear_slides(board_id, user["name"])
    result["added_slides"] = boards.add_slides(board_id, media.store_rendered(board_id, pages), user["name"])
    result["board_url"] = boards.board_url(board_id)
    library.record_history(board_id, user["name"], mode, source_files, (archive_info or {}).get("key"))
    for f in source_files:
        if f.get("file_id"):
            library.mark_published(board["space_id"], f["file_id"], board)
    result["files"] = [{"file_id": f.get("file_id"), "name": f.get("name")} for f in source_files]
    if notify and mode == "replace":
        names = ", ".join(library.pretty_name(f["name"]) if f.get("name") else (f.get("title") or "?") for f in source_files)
        notify_mod.board_changed(board_id, f"New material: {names}", f"Replaced by {user['name']}.", actor=user)
    log_event(logger, logging.INFO, "slides ingested", board_id=board_id, mode=mode,
              added=len(result["added_slides"]), removed=result.get("removed_slides", 0))
    return result


def confirm(user: dict, board_id: str, upload_keys, mode="append", archive=True) -> dict:
    """Step 2 (after the PUTs): convert the uploaded files into slides, in the given order."""
    mode = _check_mode(user, board_id, mode)
    if not isinstance(upload_keys, list) or not upload_keys:
        raise ApiError(400, "invalid_input", "'upload_keys' must be a non-empty list")
    prefix = f"uploads/{board_id}/"
    blobs = []
    for key in upload_keys:
        if not isinstance(key, str) or not key.startswith(prefix) or ".." in key:
            raise ApiError(400, "invalid_input", f"Upload key does not belong to this board: {key}")
        blobs.append((media.get_bytes(key), key.rsplit("/", 1)[-1]))
    result = _ingest(user, board_id, blobs, mode, archive)
    media.delete_keys(list(upload_keys))
    return result


def import_from_url(user: dict, board_id: str, url, mode="append", archive=True) -> dict:
    mode = _check_mode(user, board_id, mode)
    data, name = media.download(url)
    return _ingest(user, board_id, [(data, name)], mode, archive)
