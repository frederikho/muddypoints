import json
import logging

import pytest
from conftest import Client

from muddypoints import media
from muddypoints.http import ApiError
from muddypoints.log import JsonFormatter, bind, clear


def test_health_and_routing(client):
    health = client.get("/api/health")[1]
    assert health["ok"] is True and health["upload_host"].endswith(".s3.eu-central-1.amazonaws.com")
    s, d = client.get("/api/nope")
    assert s == 404 and d["error"]["code"] == "not_found"
    assert client.request("PUT", "/api/health")[0] == 405
    assert client.request("POST", "/api/auth/login", raw="{not json")[0] == 400


def test_unhandled_exception_returns_500(client, monkeypatch):
    def boom(req):
        raise RuntimeError("kaputt")
    monkeypatch.setattr("muddypoints.routes.identity.new_identity", lambda: boom(None))
    s, d = client.post("/api/identity")
    assert s == 500 and d["error"]["code"] == "internal_error" and "kaputt" not in json.dumps(d)


def test_json_log_format():
    clear()
    bind(request_id="r1", user_id="u1")
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    record.data = {"board_id": "b1"}
    entry = json.loads(JsonFormatter().format(record))
    assert entry["msg"] == "hello world" and entry["request_id"] == "r1" and entry["board_id"] == "b1"
    assert entry["level"] == "INFO" and entry["ts"].endswith("Z")
    clear()


def test_client_log_endpoint(client, capsys):
    s, _ = client.post("/api/client-log", {"level": "error", "message": "TypeError: x is undefined", "url": "/board.html"})
    assert s == 200


@pytest.mark.parametrize("url", ["http://127.0.0.1/x.pdf", "http://169.254.169.254/latest", "file:///etc/passwd",
                                 "http://localhost:9001/2018-06-01/runtime/invocation/next", "ftp://x.example/a"])
def test_download_blocks_internal_urls(url):
    with pytest.raises(ApiError):
        media.download(url)


def test_share_link_normalization():
    assert media._normalize_share_link("https://drive.google.com/file/d/ABC123/view?usp=sharing") == \
        "https://drive.google.com/uc?export=download&id=ABC123"
    assert "dl=1" in media._normalize_share_link("https://www.dropbox.com/s/xyz/a.pdf?dl=0")


def test_api_key_never_logged(client):
    import io
    from conftest import register
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    old_level = root.level
    root.setLevel(logging.INFO)
    try:
        register(client)
        _, key = client.post("/api/me/api-keys", {"label": "x"})
        client.request("POST", f"/mcp/{key['key']}", raw='{"jsonrpc":"2.0","id":1,"method":"ping"}', token=None)
        client.request("POST", "/mcp", raw='{"jsonrpc":"2.0","id":1,"method":"ping"}', token=key["key"])
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    out = buf.getvalue()
    assert "/mcp/<key>" in out
    assert key["key"] not in out
