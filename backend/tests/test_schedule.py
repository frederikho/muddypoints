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
        ("Lecture_1_Intro.pdf", make_text_pdf(["Lecture 1"], title="PowerPoint Presentation")),
        ("Lecture_2_Markets.pdf", make_text_pdf(["Lecture 2", "more"], title="PowerPoint Presentation")),
        ("Lecture_3_Taxes.pdf", make_text_pdf(["Lecture 3", "a", "b"], title="PowerPoint Presentation"))])
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
    assert view["slides"] == [] and view["upcoming"]["title"] == "Lecture 1 Intro"
    assert view["upcoming"]["starts_at_local"] == "Tue 2026-10-20 16:00"

    s["clock"]["now"] = "2026-10-20T14:00:00Z"  # 16:00 Berlin
    assert schedule.advance(s["board_id"])["status"] == "live"
    assert schedule.advance(s["board_id"]) is None  # exactly once
    view = Client().get(f"/api/boards/{s['board_id']}")[1]
    assert len(view["slides"]) == 1 and view["upcoming"] is None
    sched = client.get(f"/api/boards/{s['board_id']}/schedule")[1]
    assert [x["status"] for x in sched["sessions"]] == ["live", "planned"]
    assert s["mails"] and "Now showing “Lecture 1 Intro”" in s["mails"][-1][1]


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
    broken = [{**f, "sha256": "0" * 64} for f in sess["files"]]
    db.update(sess["PK"], sess["SK"], {"files": broken, "sha256": "0" * 64})  # file content missing
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
    # manual replace by Bob -> all staff incl. Bob himself (confirmation), except opted-out Carol
    client.post(f"/api/spaces/{sid}/files/{s['files'][0]}/publish", {"board_id": bid, "mode": "replace"}, token=bob.token)
    assert sorted(m[0] for m in s["mails"]) == ["alice@uni.example", "bob@uni.example"]
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
    assert got["timezone"] == "Europe/Berlin" and got["sessions"][0]["title"] == "Lecture 3 Taxes"
    status, d = mcp("tools/call", {"name": "view_file_page", "arguments": {
        "workspace_id": s["space_id"], "file_id": s["files"][1], "page": 2}})
    content = d["result"]["content"]
    assert content[0]["type"] == "image" and content[0]["mimeType"] == "image/jpeg" and len(content[0]["data"]) > 1000
    assert "page 2 of 2" in content[1]["text"]


def test_generic_session_titles_show_file_name(client, setup):
    s = setup
    plan(client, s, ["2026-10-20T16:00"])
    sess = db.query(f"BOARD#{s['board_id']}", "SESSION#")[0]
    assert sess["title"] == "Lecture 1 Intro"  # new sessions default to the file name
    db.update(sess["PK"], sess["SK"], {"title": "PowerPoint Presentation"})  # like sessions created before the fix
    got = client.get(f"/api/boards/{s['board_id']}/schedule")[1]["sessions"][0]
    assert got["title"] == "Lecture 1 Intro"
    assert Client().get(f"/api/boards/{s['board_id']}")[1]["upcoming"]["title"] == "Lecture 1 Intro"


def test_view_file_pages_contact_sheet(mcp, client, setup):
    s = setup
    from test_library import drop_upload, link_token, make_text_pdf
    fid = drop_upload(link_token(client, s["space_id"]), [("Long.pdf", make_text_pdf([f"Page {i}" for i in range(1, 21)]))])[0]["file_id"]
    status, d = mcp("tools/call", {"name": "view_file_pages", "arguments": {"workspace_id": s["space_id"], "file_id": fid, "from_page": 3}})
    content = d["result"]["content"]
    assert content[0]["type"] == "image" and "pages 3-14 of 20" in content[1]["text"]
    status, d = mcp("tools/call", {"name": "view_file_pages", "arguments": {"workspace_id": s["space_id"], "file_id": fid, "from_page": 19, "to_page": 40}})
    assert "pages 19-20 of 20" in d["result"]["content"][1]["text"]


def test_multi_file_sessions_dry_run_and_run_now(client, setup, mcp):
    s, bid = setup, setup["board_id"]
    # week with two lectures: one session, two files, shown in order
    res, err = mcp.call("set_schedule", board_id=bid, dry_run=True, sessions=[
        {"file_ids": [s["files"][0], s["files"][1]], "starts_at": "2026-09-30T16:00"},
        {"file_id": s["files"][2], "starts_at": "2026-10-27T16:00"}])
    assert not err and res["dry_run"] and not res["saved"] and len(res["warnings"]) == 1  # first one is in the past
    assert "within 5 minutes" in res["warnings"][0] and res["sessions"][0]["title"] == "Lecture 1 Intro + Lecture 2 Markets"
    assert client.get(f"/api/boards/{bid}/schedule")[1]["sessions"] == []  # nothing saved
    res, err = mcp.call("set_schedule", board_id=bid, sessions=[
        {"file_ids": [s["files"][0], s["files"][1]], "starts_at": "2026-10-20T16:00"},
        {"file_id": s["files"][2], "starts_at": "2026-10-27T16:00"}])
    assert res["saved"] and res["warnings"] == [] and len(res["sessions"][0]["files"]) == 2
    s["clock"]["now"] = "2026-10-20T14:00:00Z"
    schedule.advance(bid)
    view = Client().get(f"/api/boards/{bid}")[1]
    assert [sl["source"] for sl in view["slides"]] == ["Lecture_1_Intro.pdf", "Lecture_2_Markets.pdf p.1",
                                                       "Lecture_2_Markets.pdf p.2"]
    # run the next session now instead of waiting
    nxt = [x for x in client.get(f"/api/boards/{bid}/schedule")[1]["sessions"] if x["status"] == "planned"][0]
    s["clock"]["now"] = "2026-10-21T09:00:00Z"
    res, err = mcp.call("run_session_now", board_id=bid, session_id=nxt["session_id"])
    assert not err and [x["status"] for x in res["sessions"]] == ["done", "live"]
    assert len(Client().get(f"/api/boards/{bid}")[1]["slides"]) == 3
    # showing a done session again creates a copy, the history stays
    first = res["sessions"][0]
    s["clock"]["now"] = "2026-10-22T09:00:00Z"
    res, err = mcp.call("run_session_now", board_id=bid, session_id=first["session_id"])
    assert not err and [x["status"] for x in res["sessions"]] == ["done", "done", "live"]
    text, err = mcp.call("run_session_now", board_id=bid, session_id=res["sessions"][2]["session_id"])
    assert err and "already live" in text
    # editing files of a planned session
    p = client.post(f"/api/boards/{bid}/sessions", {"file_id": s["files"][0], "starts_at": "2026-12-01T16:00"})[1]["session"]
    upd = client.patch(f"/api/boards/{bid}/sessions/{p['session_id']}", {"file_ids": [s["files"][2], s["files"][0]]})[1]
    assert [f["file_id"] for f in upd["session"]["files"]] == [s["files"][2], s["files"][0]]


def test_files_show_where_they_are_scheduled(client, setup):
    s = setup
    plan(client, s, ["2026-10-20T16:00"])
    files = {f["file_id"]: f for f in client.get(f"/api/spaces/{s['space_id']}/files")[1]["files"]}
    assert files[s["files"][0]]["scheduled"][0]["board"] == "Week 1"
    assert files[s["files"][1]]["scheduled"] == []
