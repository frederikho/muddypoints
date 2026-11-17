"""PDF export of a board: every slide followed by its comments (for uploading to OLAT etc.)."""
import html
import io
import logging
import re
import time

import pymupdf

from . import boards, media
from .http import ApiError
from .log import get_logger, log_event

logger = get_logger(__name__)
PAGE = pymupdf.paper_rect("a4")
MARGIN = 40
CSS = """
body { font-family: sans-serif; font-size: 10pt; }
h1 { font-size: 18pt; margin: 0 0 4pt 0; }
h2 { font-size: 13pt; margin: 14pt 0 4pt 0; }
.meta { color: #666; font-size: 9pt; }
.comment { margin: 3pt 0 3pt 0; }
.author { font-weight: bold; }
.staff { color: #1d4ed8; }
"""


def _filename(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")[:60] or "board"
    return f"{base}-{time.strftime('%Y-%m-%d')}.pdf"


def build_pdf(board_id: str, include_uncommented: bool = True) -> tuple[bytes, dict]:
    data = boards.load(board_id)
    meta, slides, comments = data["meta"], data["slides"], data["comments"]
    by_slide: dict[str, list] = {}
    for c in comments:
        by_slide.setdefault(c["slide_id"], []).append(c)

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
        for c in slide_comments:
            cls = "author staff" if c.get("is_staff") else "author"
            likes = f" · {c.get('likes', 0)} likes" if c.get("likes") else ""
            text = html.escape(c["text"]).replace("\n", "<br/>")
            parts.append(f"<p class='comment'><span class='{cls}'>{html.escape(c['author_name'])}</span> "
                         f"<span class='meta'>{c['created_at'][:16].replace('T', ' ')}{likes}</span><br/>{text}</p>")

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
    """Keep a permanent PDF before a board's slides are cleared (only if there is something to keep)."""
    data = boards.load(board_id)
    if not data["slides"]:
        return None
    return _store(board_id, "archive")


def list_archives(user: dict, board_id: str) -> list[dict]:
    boards.require_board(user, board_id, "editor")
    objects = sorted(media.list_keys(f"archive/{board_id}/"), key=lambda o: o["Key"], reverse=True)
    return [{"key": o["Key"], "size_bytes": o["Size"], "created_at": o["LastModified"].isoformat(),
             "download_url": media.presigned_get(o["Key"], o["Key"].rsplit("/", 1)[-1], ttl=3600)}
            for o in objects]


def clear_board(user: dict, board_id: str, archive: bool = True) -> dict:
    boards.require_board(user, board_id, "owner")
    if not isinstance(archive, bool):
        raise ApiError(400, "invalid_input", "'archive' must be true or false")
    result = {"archive": archive_if_needed(board_id) if archive else None}
    result["removed_slides"] = boards.clear_slides(board_id)
    log_event(logger, logging.INFO, "board cleared", board_id=board_id, removed=result["removed_slides"])
    return result
