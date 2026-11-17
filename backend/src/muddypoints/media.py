"""S3 storage and rendering of uploads (PDF pages / images) into slide JPEGs.

S3 layout:
  uploads/<board_id>/<upload_id>/<filename>   raw uploads (presigned PUT, auto-expire after 1 day)
  boards/<board_id>/slides/<slide_id>.jpg     rendered slides
  exports/<board_id>/<timestamp>.pdf          on-demand comment exports (auto-expire after 7 days)
  archive/<board_id>/<timestamp>.pdf          exports made automatically before a board is cleared (kept)
"""
import ipaddress
import logging
import socket
import urllib.parse
import urllib.request

import pymupdf

from . import aws
from .config import (MAX_PAGES_PER_UPLOAD, MAX_UPLOAD_BYTES, MEDIA_URL_TTL_SECONDS, SLIDE_MAX_SIDE_PX,
                     UPLOAD_URL_TTL_SECONDS, settings)
from .http import ApiError
from .log import get_logger, log_event
from .util import new_id

logger = get_logger(__name__)
ALLOWED_CONTENT_TYPES = {
    "application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif",
    "image/webp": "webp", "image/bmp": "bmp", "image/tiff": "tiff",
}


# --- S3 helpers -----------------------------------------------------------------------------

def presigned_put(key: str, content_type: str) -> str:
    return aws.s3().generate_presigned_url(
        "put_object", Params={"Bucket": settings.bucket_name, "Key": key, "ContentType": content_type},
        ExpiresIn=UPLOAD_URL_TTL_SECONDS)


def presigned_get(key: str, download_name: str | None = None, ttl: int = MEDIA_URL_TTL_SECONDS) -> str:
    params = {"Bucket": settings.bucket_name, "Key": key}
    if download_name:
        params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
    return aws.s3().generate_presigned_url("get_object", Params=params, ExpiresIn=ttl)


def put_bytes(key: str, data: bytes, content_type: str) -> None:
    aws.s3().put_object(Bucket=settings.bucket_name, Key=key, Body=data, ContentType=content_type)


def get_bytes(key: str) -> bytes:
    s3 = aws.s3()
    try:
        head = s3.head_object(Bucket=settings.bucket_name, Key=key)
    except s3.exceptions.ClientError:
        raise ApiError(404, "upload_not_found", f"Uploaded file not found: {key} (did the upload finish?)")
    if head["ContentLength"] > MAX_UPLOAD_BYTES:
        raise ApiError(413, "too_large", f"File is larger than {MAX_UPLOAD_BYTES // 2**20} MB")
    return s3.get_object(Bucket=settings.bucket_name, Key=key)["Body"].read()


def list_keys(prefix: str) -> list[dict]:
    out = []
    for page in aws.s3().get_paginator("list_objects_v2").paginate(Bucket=settings.bucket_name, Prefix=prefix):
        out.extend(page.get("Contents", []))
    return out


def delete_keys(keys: list[str]) -> None:
    for i in range(0, len(keys), 1000):
        chunk = keys[i:i + 1000]
        if chunk:
            aws.s3().delete_objects(Bucket=settings.bucket_name,
                                    Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": True})


def delete_prefix(prefix: str) -> None:
    delete_keys([o["Key"] for o in list_keys(prefix)])


# --- rendering ------------------------------------------------------------------------------

def render(data: bytes, filename: str) -> list[dict]:
    """Turn a PDF or image into a list of {'jpeg', 'width', 'height', 'source'} (one per page)."""
    try:
        doc = pymupdf.open(stream=data)
    except Exception:
        raise ApiError(400, "unsupported_file",
                       f"'{filename}' is not a PDF or a supported image (PNG, JPEG, GIF, WEBP, BMP, TIFF). "
                       "Export PowerPoint/Keynote files as PDF first.")
    with doc:
        if doc.needs_pass:
            raise ApiError(400, "unsupported_file", f"'{filename}' is password protected")
        if doc.page_count > MAX_PAGES_PER_UPLOAD:
            raise ApiError(400, "too_many_pages", f"'{filename}' has more than {MAX_PAGES_PER_UPLOAD} pages")
        # PDFs are vector: render at up to 2x. Images open at 96dpi -> 72pt, so 4/3 restores native pixels.
        native = 2.0 if doc.is_pdf else 96 / 72
        pages = []
        for page in doc:
            zoom = min(native, SLIDE_MAX_SIDE_PX / max(page.rect.width, page.rect.height, 1))
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            if pix.colorspace and pix.colorspace.n not in (1, 3):  # e.g. CMYK -> RGB for JPEG output
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            label = f"{filename} p.{page.number + 1}" if doc.page_count > 1 else filename
            pages.append({"jpeg": pix.tobytes("jpg", jpg_quality=85), "width": pix.width,
                          "height": pix.height, "source": label})
        return pages


def store_rendered(board_id: str, pages: list[dict]) -> list[dict]:
    out = []
    for p in pages:
        slide_id = new_id()
        key = f"boards/{board_id}/slides/{slide_id}.jpg"
        put_bytes(key, p["jpeg"], "image/jpeg")
        out.append({"slide_id": slide_id, "key": key, "width": p["width"], "height": p["height"],
                    "source": p["source"]})
    return out


# --- fetching from a URL (MCP: Claude passes a link instead of uploading bytes) ---------------

def _normalize_share_link(url: str) -> str:
    """Turn common 'share' links into direct downloads."""
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc == "drive.google.com" and "/file/d/" in parsed.path:
        file_id = parsed.path.split("/file/d/")[1].split("/")[0]
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    if parsed.netloc.endswith("dropbox.com"):
        q = urllib.parse.parse_qs(parsed.query)
        q["dl"] = ["1"]
        return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(q, doseq=True)))
    return url


def _assert_public_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ApiError(400, "bad_url", f"Cannot resolve host '{host}'")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ApiError(400, "bad_url", "URLs pointing to private/internal addresses are not allowed")


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme not in ("http", "https"):
            raise ApiError(400, "bad_url", "Redirect to a non-HTTP URL")
        _assert_public_host(parsed.hostname or "")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str) -> tuple[bytes, str]:
    if not isinstance(url, str):
        raise ApiError(400, "bad_url", "'url' must be a string")
    url = _normalize_share_link(url.strip())
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ApiError(400, "bad_url", "Only http(s) URLs are supported")
    _assert_public_host(parsed.hostname)
    opener = urllib.request.build_opener(_SafeRedirects)
    request = urllib.request.Request(url, headers={"User-Agent": "muddypoints/1.0"})
    try:
        with opener.open(request, timeout=60) as resp:
            data = resp.read(MAX_UPLOAD_BYTES + 1)
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(502, "download_failed", f"Could not download the file: {exc}")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ApiError(413, "too_large", f"File is larger than {MAX_UPLOAD_BYTES // 2**20} MB")
    name = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1]) or "download"
    log_event(logger, logging.INFO, "downloaded file", host=parsed.hostname, bytes=len(data))
    return data, name
