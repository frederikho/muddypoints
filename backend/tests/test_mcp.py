import json

import pytest
from conftest import Client, make_pdf, register


@pytest.fixture
def mcp(client, owner):
    _, key = client.post("/api/me/api-keys", {"label": "Claude"})

    def rpc(method, params=None, msg_id=1, key=key["key"], path=None):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if msg_id is not None:
            msg["id"] = msg_id
        return Client().request("POST", path or f"/mcp/{key}", raw=json.dumps(msg), token=None)

    def call(_tool, **args):
        s, d = rpc("tools/call", {"name": _tool, "arguments": args})
        assert s == 200, d
        result = d["result"]
        text = result["content"][0]["text"]
        return (json.loads(text) if not result["isError"] else text), result["isError"]

    rpc.call = call
    rpc.key = key["key"]
    return rpc


def test_initialize_and_list(mcp):
    s, d = mcp("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}})
    assert s == 200 and d["result"]["protocolVersion"] == "2025-06-18"
    assert d["result"]["capabilities"]["tools"] is not None
    s, d = mcp("initialize", {"protocolVersion": "1999-01-01"})
    assert d["result"]["protocolVersion"] == "2025-11-25"
    s, d = mcp("notifications/initialized", msg_id=None)
    assert s == 202
    s, d = mcp("tools/list")
    names = {t["name"] for t in d["result"]["tools"]}
    assert {"list_spaces", "create_board", "export_board_pdf", "replace_board_material", "get_logs"} <= names
    for t in d["result"]["tools"]:
        assert t["inputSchema"]["type"] == "object" and t["description"]
    assert mcp("ping")[1]["result"] == {}
    assert mcp("does/not/exist")[1]["error"]["code"] == -32601


def test_auth(mcp, client):
    s, d = mcp("tools/list", key="pk_wrong")
    assert s == 401
    # bearer header works too
    s, d = Client().request("POST", "/mcp", raw=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
                            token=mcp.key)
    assert s == 200
    assert Client().request("GET", f"/mcp/{mcp.key}")[0] == 405


def test_tools_end_to_end(mcp, owner, monkeypatch):
    who, err = mcp.call("whoami")
    assert not err and who["user"]["name"] == "Alice" and who["spaces"][0]["space_id"] == owner["space_id"]
    board, err = mcp.call("create_board", space_id=owner["space_id"], name="Week 2")
    assert not err and board["url"].endswith(board["board_id"])
    boards, _ = mcp.call("list_boards", space_id=owner["space_id"])
    assert [b["name"] for b in boards] == ["Week 1", "Week 2"]

    pdf = make_pdf(2)
    monkeypatch.setattr("muddypoints.media.download", lambda url: (pdf, "lecture2.pdf"))
    res, err = mcp.call("import_slides_from_url", board_id=board["board_id"], url="https://x.example/l.pdf")
    assert not err and len(res["added_slides"]) == 2
    info, _ = mcp.call("get_board", board_id=board["board_id"])
    assert [s["number"] for s in info["slides"]] == [1, 2]

    student = Client()
    student.anon = student.post("/api/identity")[1]["token"]
    student.post(f"/api/boards/{board['board_id']}/comments", {"slide_id": info["slides"][1]["slide_id"], "text": "??"})
    comments, _ = mcp.call("list_comments", board_id=board["board_id"])
    assert comments[0]["slide_number"] == 2 and comments[0]["text"] == "??"

    exp, err = mcp.call("export_board_pdf", board_id=board["board_id"])
    assert not err and exp["comments"] == 1 and exp["download_url"]

    res, err = mcp.call("replace_board_material", board_id=board["board_id"], url="https://x.example/l3.pdf")
    assert not err and res["removed_slides"] == 2 and res["archive"]["comments"] == 1
    arch, _ = mcp.call("list_board_archives", board_id=board["board_id"])
    assert len(arch) == 1

    upl, err = mcp.call("prepare_slide_upload", board_id=board["board_id"],
                        files=[{"filename": "a.pdf", "content_type": "application/pdf"}])
    assert not err and upl[0]["upload_url"].startswith("https://")


def test_tool_errors_are_reported_not_raised(mcp, owner):
    text, err = mcp.call("delete_board", board_id="doesnotexist")
    assert err and "not_found" in text
    text, err = mcp.call("rename_space", name="x")
    assert err and "missing argument" in text
    s, d = mcp("tools/call", {"name": "nope", "arguments": {}})
    assert d["error"]["code"] == -32602


def test_editor_cannot_delete_via_mcp(mcp, client, owner):
    bob = Client()
    register(bob, email="bob@uni.example", name="Bob")
    client.post(f"/api/spaces/{owner['space_id']}/members", {"email": "bob@uni.example"})
    _, key = bob.post("/api/me/api-keys", {"label": "Claude"})
    s, d = Client().request("POST", f"/mcp/{key['key']}", token=None, raw=json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "delete_board", "arguments": {"board_id": owner["board_id"]}}}))
    assert d["result"]["isError"] and "forbidden" in d["result"]["content"][0]["text"]


def test_get_logs_permissions(mcp):
    text, err = mcp.call("get_logs")
    assert err and "logs_unavailable" in text  # owner, but LOG_GROUP_NAME unset in tests


def test_api_key_never_logged(mcp):
    import logging
    from muddypoints.log import JsonFormatter
    lines = []

    class Capture(logging.Handler):
        def emit(self, record):
            lines.append(JsonFormatter().format(record))
    handler = Capture(level=logging.INFO)
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        mcp("ping")
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
    text = "\n".join(lines)
    assert lines and mcp.key not in text and "/mcp/<key>" in text
