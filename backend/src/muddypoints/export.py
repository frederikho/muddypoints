"""PDF export of a board: every slide followed by its comments (for uploading to OLAT etc.)."""
import html
import io
import logging
import re
import time

import pymupdf

from . import boards, db, media
from .http import ApiError
from .log import get_logger, log_event
from .util import now_iso, sortable_id

logger = get_logger(__name__)
PAGE = pymupdf.paper_rect("a4")
MARGIN = 40
CSS = """
body { font-family: sans-serif; font-size: 10pt; }
h1 { font-size: 18pt; margin: 0 0 4pt 0; }
h2 { font-size: 13pt; margin: 14pt 0 4pt 0; }
.meta { color: #666; font-size: 9pt; }
.comment { margin: 3pt 0 3pt 0; }
.reply { margin-left: 18pt; }
.author { font-weight: bold; }
.staff { color: #1d4ed8; }
"""


def _filename(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")[:60] or "board"
    return f"{base}-{time.strftime('%Y-%m-%d')}.pdf"


def build_pdf(board_id: str, include_uncommented: bool = True) -> tuple[bytes, dict]:
    data = boards.load(board_id)
    meta, slides, comments = data["meta"], data["slides"], data["comments"]
    by_slide = boards.thread_comments(comments, include_hidden=True)  # staff export: everything, marked

    archive = pymupdf.Archive()
    width = PAGE.width - 2 * MARGIN
    parts = [f"<h1>{html.escape(meta['name'])}</h1>",
             f"<p class='meta'>Exported {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} · "
             f"{len(slides)} slides · {len(comments)} comments</p>"]
    for idx, s in enumerate(slides, start=1):
        slide_comments = by_slide.get(s["slide_id"], [])
        if not slide_comments and not include_uncommented:
            continue
        name = f"slide{idx}.jpg"
        archive.add(media.get_bytes(s["key"]), name)
        h = width * s["height"] / s["width"] if s.get("width") else width * 0.75
        h = min(h, 330)  # leave room for comments on the same page
        w = h * s["width"] / s["height"] if s.get("height") else width
        parts.append(f"<h2>Slide {idx}</h2><p class='meta'>{html.escape(s.get('source') or '')} · "
                     f"{s.get('likes', 0)} likes · {len(slide_comments)} comments</p>")
        parts.append(f"<img src='{name}' width='{w:.0f}' height='{h:.0f}'/>")
        if not slide_comments:
            parts.append("<p class='meta'>No comments.</p>")
        def render(c: dict, reply: bool = False) -> str:
            cls = "author staff" if c.get("is_staff") else "author"
            likes = f" · {c.get('likes', 0)} likes" if c.get("likes") else ""
            flags = "".join(f" · {f}" for f, on in (("✓ answered", c.get("answered")), ("pinned", c.get("pinned")),
                                                     ("[hidden]", c.get("hidden"))) if on)
            text = html.escape(c["text"]).replace("\n", "<br/>")
            return (f"<p class='comment{' reply' if reply else ''}'>{'↳ ' if reply else ''}"
                    f"<span class='{cls}'>{html.escape(c['author_name'])}</span> "
                    f"<span class='meta'>{c['created_at'][:16].replace('T', ' ')}{likes}{flags}</span><br/>{text}</p>")
        for c in slide_comments:
            parts.append(render(c))
            parts.extend(render(r, reply=True) for r in c.get("replies", []))

    story = pymupdf.Story("<body>" + "".join(parts) + "</body>", user_css=CSS, archive=archive)
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    more = True
    while more:
        device = writer.begin_page(PAGE)
        more, _ = story.place(PAGE + (MARGIN, MARGIN, -MARGIN, -MARGIN))
        story.draw(device)
        writer.end_page()
    writer.close()
    return buf.getvalue(), {"filename": _filename(meta["name"]), "slides": len(slides), "comments": len(comments)}


def _store(board_id: str, folder: str, include_uncommented: bool = True) -> dict:
    pdf, info = build_pdf(board_id, include_uncommented)
    key = f"{folder}/{board_id}/{time.strftime('%Y%m%d-%H%M%S')}-{info['filename']}"
    media.put_bytes(key, pdf, "application/pdf")
    log_event(logger, logging.INFO, "board exported", board_id=board_id, key=key, bytes=len(pdf))
    return {**info, "key": key, "download_url": media.presigned_get(key, info["filename"], ttl=3600)}


def export_board(user: dict, board_id: str, include_uncommented: bool = True) -> dict:
    boards.require_board(user, board_id, "editor")
    return _store(board_id, "exports", include_uncommented)


def archive_if_needed(board_id: str) -> dict | None:
    """Keep a permanent PDF before a board's slides are cleared - only if students commented (otherwise it would
    just duplicate the slides, whose source file is kept in the workspace files anyway)."""
    data = boards.load(board_id)
    if not data["slides"] or not data["comments"]:
        return None
    result = _store(board_id, "archive")
    _store_comment_data(board_id, data, result["key"])
    return result


def archive_with_reason(board_id: str, wanted: bool = True) -> tuple[dict | None, str | None]:
    """(archive info, None) or (None, why no archive was made) - callers report the reason to users/agents."""
    if not wanted:
        return None, "archive=false was requested"
    data = boards.load(board_id)
    if not data["slides"]:
        return None, "the board was empty"
    if not data["comments"]:
        return None, "no comments on the previous material, so there was nothing worth archiving (the files stay in the workspace files)"
    return archive_if_needed(board_id), None


def comment_snapshot(data: dict) -> list[dict]:
    """Slides with their threaded comments (staff view incl. hidden) as plain data."""
    threaded = boards.thread_comments(data["comments"], include_hidden=True)
    return [{"slide_number": i + 1, "source": s.get("source"), "likes": s.get("likes", 0),
             "comments": threaded.get(s["slide_id"], [])} for i, s in enumerate(data["slides"])]


def _store_comment_data(board_id: str, data: dict, pdf_key: str) -> None:
    """Structured copy of the archived comments (JSON in S3 + small index item) for later analysis."""
    import json
    history = sorted(db.query(f"BOARD#{board_id}", "HIST#"), key=lambda i: i["SK"])
    material = [f.get("title") or f.get("name") for f in (history[-1].get("files", []) if history else [])]
    slides = comment_snapshot(data)
    json_key = pdf_key[:-4] + ".comments.json"
    doc = {"board": data["meta"]["name"], "archived_at": now_iso(), "material": material, "slides": slides}
    media.put_bytes(json_key, json.dumps(doc, ensure_ascii=False).encode(), "application/json")
    count = sum(len(s["comments"]) + sum(len(c["replies"]) for c in s["comments"]) for s in slides)
    db.put({"PK": f"BOARD#{board_id}", "SK": f"ARCH#{sortable_id()}", "archived_at": doc["archived_at"],
            "material": material, "json_key": json_key, "pdf_key": pdf_key, "comment_count": count})


def archived_comments(user: dict, board_id: str, limit: int = 5, offset: int = 0, include_current: bool = True) -> dict:
    """Comments of earlier materials (newest first) as data, optionally plus the current ones."""
    import json
    board, _ = boards.require_board(user, board_id, "editor")
    try:
        limit, offset = max(1, min(int(limit or 5), 20)), max(0, int(offset or 0))
    except (TypeError, ValueError):
        raise ApiError(400, "invalid_input", "limit and offset must be numbers")
    index = sorted(db.query(f"BOARD#{board_id}", "ARCH#"), key=lambda i: i["SK"], reverse=True)
    archives = [{**json.loads(media.get_bytes(i["json_key"])), "comment_count": i.get("comment_count")}
                for i in index[offset:offset + limit]]
    pdf_count = len([o for o in media.list_keys(f"archive/{board_id}/") if o["Key"].endswith(".pdf")])
    out = {"board": board["name"], "total_archives": len(index), "archives": archives,
           "pdf_only_archives": max(0, pdf_count - len(index)),
           "note": "Archives created before comment data was stored exist only as PDF (list_board_archives)."
           if pdf_count > len(index) else None,
           "next_offset": offset + limit if offset + limit < len(index) else None}
    if include_current and offset == 0:
        out["current"] = {"material": [f.get("title") or f.get("name") for f in (
            sorted(db.query(f"BOARD#{board_id}", "HIST#"), key=lambda i: i["SK"])[-1:] or [{}])[0].get("files", [])],
            "slides": comment_snapshot(boards.load(board_id))}
    return out


def list_archives(user: dict, board_id: str, with_urls: bool = True, only_key: str | None = None) -> list[dict]:
    boards.require_board(user, board_id, "editor")
    objects = sorted((o for o in media.list_keys(f"archive/{board_id}/") if o["Key"].endswith(".pdf")),
                     key=lambda o: o["Key"], reverse=True)
    if only_key:
        objects = [o for o in objects if o["Key"] == only_key]
        if not objects:
            raise ApiError(404, "not_found", f"Archive '{only_key}' not found")
    return [{"key": o["Key"], "size_bytes": o["Size"], "created_at": o["LastModified"].isoformat(),
             **({"download_url": media.presigned_get(o["Key"], o["Key"].rsplit("/", 1)[-1], ttl=3600)} if with_urls else {})}
            for o in objects]


def clear_board(user: dict, board_id: str, archive: bool = True, dry_run: bool = False) -> dict:
    boards.require_board(user, board_id, "editor")
    if not isinstance(archive, bool):
        raise ApiError(400, "invalid_input", "'archive' must be true or false")
    if dry_run:
        data = boards.load(board_id)
        will_archive = archive and data["slides"] and data["comments"]
        return boards.dry(f"remove {len(data['slides'])} slide(s), {len(data['comments'])} comment(s) and all likes, "
                          + ("archiving them as PDF first" if will_archive else "WITHOUT an archive (no comments or archive=false)")
                          + ", and email all workspace staff")
    info, reason = archive_with_reason(board_id, archive)
    result = {"archive": info, **({"archive_skipped": reason} if reason else {})}
    result["removed_slides"] = boards.clear_slides(board_id, user["name"])
    from . import notify  # export <- notify <- boards: import lazily
    notify.board_changed(board_id, "Board cleared", f"Cleared by {user['name']}.", actor=user)
    log_event(logger, logging.INFO, "board cleared", board_id=board_id, removed=result["removed_slides"])
    return result
