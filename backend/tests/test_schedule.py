import json

import pytest
from conftest import Client, make_pdf, register
from test_library import drop_upload, link_token, make_text_pdf
from test_mcp import mcp  # noqa: F401  (fixture)

from muddypoints import db, schedule
from muddypoints.handler import lambda_handler


@pytest.fixture
def setup(client, owner, monkeypatch):
    """Workspace with 3 lecture files and a board; sent emails are captured; time is controllable."""
    mails = []
    monkeypatch.setattr("muddypoints.mailer.send", lambda to, subject, body, reply_to=None: mails.append((to, subject)) or True)
    files = drop_upload(link_token(client, owner["space_id"]), [
        ("L1.pdf", make_text_pdf(["Lecture 1"], title="Lecture 1: Intro")),
        ("L2.pdf", make_text_pdf(["Lecture 2", "more"], title="Lecture 2: Markets")),
        ("L3.pdf", make_text_pdf(["Lecture 3", "a", "b"], title="Lecture 3: Taxes"))])
    clock = {"now": "2026-10-01T08:00:00Z"}
    monkeypatch.setattr(schedule, "_now", lambda: clock["now"])
    return {**owner, "files": [f["file_id"] for f in files], "mails": mails, "clock": clock}


def plan(client, s, times):
    return [
        client.post(f"/api/boards/{s['board_id']}/sessions", {"file_id": fid, "starts_at": t})[1]["session"]
        for fid, t in zip(s["files"], times)]


def test_times_are_local_to_board_timezone(client, setup):
    s = setup
    sess = plan(client, s, ["2026-10-20T16:00", "2026-10-27T16:00"])  # DST ends on Oct 25 in Europe/Berlin
    assert sess[0]["starts_at"] == "2026-10-20T14:00:00Z" and sess[0]["starts_at_local"] == "Tue 2026-10-20 16:00"
    assert sess[1]["starts_at"] == "2026-10-27T15:00:00Z"
    explicit = client.post(f"/api/boards/{s['board_id']}/sessions",
                           {"file_id": s["files"][2], "starts_at": "2026-11-03T16:00:00+01:00"})[1]["session"]
    assert explicit["starts_at"] == "2026-11-03T15:00:00Z"
    assert client.post(f"/api/boards/{s['board_id']}/sessions", {"file_id": s["files"][0], "starts_at": "soon"})[0] == 400
    assert client.patch(f"/api/boards/{s['board_id']}", {"timezone": "Mars/Olympus"})[0] == 400


def test_coming_soon_then_switches_exactly_once(client, setup):
    s = setup
    plan(client, s, ["2026-10-20T16:00", "2026-10-27T16:00"])
    view = Client().get(f"/api/boards/{s['board_id']}")[1]
    assert view["slides"] == [] and view["upcoming"]["title"] == "Lecture 1: Intro"
    assert view["upcoming"]["starts_at_local"] == "Tue 2026-10-20 16:00"

    s["clock"]["now"] = "2026-10-20T14:00:00Z"  # 16:00 Berlin
    assert schedule.advance(s["board_id"])["status"] == "live"
    assert schedule.advance(s["board_id"]) is None  # exactly once
    view = Client().get(f"/api/boards/{s['board_id']}")[1]
    assert len(view["slides"]) == 1 and view["upcoming"] is None
    sched = client.get(f"/api/boards/{s['board_id']}/schedule")[1]
    assert [x["status"] for x in sched["sessions"]] == ["live", "planned"]
    assert s["mails"] and "Now showing “Lecture 1: Intro”" in s["mails"][-1][1]


def test_switch_archives_comments_and_reopens(client, setup):
    s, bid = setup, setup["board_id"]
    plan(client, s, ["2026-10-20T16:00", "2026-10-27T16:00"])
    s["clock"]["now"] = "2026-10-20T14:00:00Z"
    schedule.advance(bid)
    student = Client()
    student.anon = student.post("/api/identity", token=None)[1]["token"]
    slide = Client().get(f"/api/boards/{bid}")[1]["slides"][0]["slide_id"]
    student.post(f"/api/boards/{bid}/comments", {"slide_id": slide, "text": "Why?"}, token=None)
    client.patch(f"/api/boards/{bid}", {"locked": True})
    s["clock"]["now"] = "2026-10-27T15:00:00Z"
    schedule.advance(bid)
    view = Client().get(f"/api/boards/{bid}")[1]
    assert len(view["slides"]) == 2 and all(not x["comments"] for x in view["slides"])
    assert view["board"]["locked"] is False  # new material starts with open comments
    archives = client.get(f"/api/boards/{bid}/archives")[1]["archives"]
    assert len(archives) == 1
    sched = client.get(f"/api/boards/{bid}/schedule")[1]["sessions"]
    assert [x["status"] for x in sched] == ["done", "live"]
    hist = client.get(f"/api/boards/{bid}/history")[1]["history"]
    assert hist[0]["by"] == "Schedule" and hist[0]["archived_previous_material"]


def test_page_load_triggers_due_switch_and_skips_missed(client, setup):
    s, bid = setup, setup["board_id"]
    plan(client, s, ["2026-10-20T16:00", "2026-10-27T16:00", "2026-11-03T16:00"])
    s["clock"]["now"] = "2026-10-28T09:00:00Z"  # two sessions are due, nobody switched yet
    view = Client().get(f"/api/boards/{bid}")[1]  # a student opens the page
    assert len(view["slides"]) == 2  # Lecture 2 (latest due), Lecture 1 skipped
    statuses = [x["status"] for x in client.get(f"/api/boards/{bid}/schedule")[1]["sessions"]]
    assert statuses == ["skipped", "live", "planned"]


def test_scheduler_event_runs_due_boards(client, setup):
    s = setup
    plan(client, s, ["2026-10-20T16:00"])
    s["clock"]["now"] = "2026-10-20T14:05:00Z"
    out = lambda_handler({"source": "muddypoints.scheduler"}, None)
    assert out["ok"] and out["boards"][s["board_id"]]["status"] == "live"
    assert lambda_handler({"source": "muddypoints.scheduler"}, None)["boards"] == {}


def test_failed_switch_is_reported(client, setup):
    s, bid = setup, setup["board_id"]
    plan(client, s, ["2026-10-20T16:00"])
    sess = db.query(f"BOARD#{bid}", "SESSION#")[0]
    db.update(sess["PK"], sess["SK"], {"sha256": "0" * 64})  # file content missing
    s["clock"]["now"] = "2026-10-20T14:00:00Z"
    assert schedule.advance(bid)["status"] == "failed"
    assert [x["status"] for x in client.get(f"/api/boards/{bid}/schedule")[1]["sessions"]] == ["failed"]
    assert "FAILED" in s["mails"][-1][1]


def test_notifications_go_to_staff_with_opt_out(client, setup):
    s, bid, sid = setup, setup["board_id"], setup["space_id"]
    bob, carol = Client(), Client()
    register(bob, email="bob@uni.example", name="Bob")
    register(carol, email="carol@uni.example", name="Carol")
    client.post(f"/api/spaces/{sid}/members", {"email": "bob@uni.example"})
    client.post(f"/api/spaces/{sid}/members", {"email": "carol@uni.example"})
    assert carol.request("PUT", f"/api/boards/{bid}/notifications", {"enabled": False})[1] == {"enabled": False}
    assert carol.get(f"/api/boards/{bid}/notifications")[1] == {"enabled": False}
    s["mails"].clear()
    # manual replace by Bob -> everyone else except opted-out Carol
    client.post(f"/api/spaces/{sid}/files/{s['files'][0]}/publish", {"board_id": bid, "mode": "replace"}, token=bob.token)
    assert sorted(m[0] for m in s["mails"]) == ["alice@uni.example"]
    s["mails"].clear()
    plan(client, s, ["2026-10-20T16:00"])
    s["clock"]["now"] = "2026-10-20T14:00:00Z"
    schedule.advance(bid)  # scheduled switch -> all staff except opted-out
    assert sorted(m[0] for m in s["mails"]) == ["alice@uni.example", "bob@uni.example"]


def test_edit_and_remove_sessions(client, setup):
    s, bid = setup, setup["board_id"]
    sess = plan(client, s, ["2026-10-20T16:00", "2026-10-27T16:00"])
    upd = client.patch(f"/api/boards/{bid}/sessions/{sess[1]['session_id']}", {"starts_at": "2026-10-28T12:00", "title": "Moved"})[1]
    assert upd["session"]["starts_at_local"] == "Wed 2026-10-28 12:00" and upd["session"]["title"] == "Moved"
    assert client.delete(f"/api/boards/{bid}/sessions/{sess[1]['session_id']}")[0] == 200
    s["clock"]["now"] = "2026-10-20T14:00:00Z"
    schedule.advance(bid)
    assert client.delete(f"/api/boards/{bid}/sessions/{sess[0]['session_id']}")[0] == 409  # live
    assert client.patch(f"/api/boards/{bid}/sessions/{sess[0]['session_id']}", {"title": "x"})[0] == 409
    assert Client().get(f"/api/boards/{bid}/schedule", token=None)[0] == 401
    assert db.get(f"BOARD#{bid}", "META").get("next_switch_at") is None


def test_schedule_and_view_page_via_mcp(mcp, client, setup):
    s = setup
    res, err = mcp.call("set_schedule", board_id=s["board_id"], sessions=[
        {"file_id": s["files"][0], "starts_at": "2026-10-20T16:00", "title": "Lecture 1: Intro"},
        {"file_id": s["files"][1], "starts_at": "2026-10-27T16:00"}])
    assert not err and [x["starts_at_local"] for x in res["sessions"]] == ["Tue 2026-10-20 16:00", "Tue 2026-10-27 16:00"]
    res, _ = mcp.call("set_schedule", board_id=s["board_id"], sessions=[
        {"file_id": s["files"][2], "starts_at": "2026-11-03T16:00"}])
    assert len(res["sessions"]) == 1  # planned sessions replaced
    got, _ = mcp.call("get_schedule", board_id=s["board_id"])
    assert got["timezone"] == "Europe/Berlin" and got["sessions"][0]["title"] == "Lecture 3: Taxes"
    status, d = mcp("tools/call", {"name": "view_file_page", "arguments": {
        "workspace_id": s["space_id"], "file_id": s["files"][1], "page": 2}})
    content = d["result"]["content"]
    assert content[0]["type"] == "image" and content[0]["mimeType"] == "image/jpeg" and len(content[0]["data"]) > 1000
    assert "page 2 of 2" in content[1]["text"]
