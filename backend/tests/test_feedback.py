from conftest import Client, register


def test_feedback_stored_and_mailed(client, monkeypatch):
    monkeypatch.setenv("FEEDBACK_EMAIL", "maintainer@example.org")
    sent = []
    monkeypatch.setattr("muddypoints.mailer.send", lambda to, subject, body, reply_to=None: sent.append((to, body, reply_to)) or True)
    s, d = Client().post("/api/feedback", {"message": "Please add dark mode for boards", "email": "prof@uni.example",
                                           "page": "/board.html"}, token=None)
    assert s == 200 and d == {"ok": True, "mailed": True}
    assert sent[0][0] == "maintainer@example.org" and "dark mode" in sent[0][1] and sent[0][2] == "prof@uni.example"
    from muddypoints import db
    assert db.query("FEEDBACK")[0]["message"] == "Please add dark mode for boards"


def test_feedback_logged_in_user_and_validation(client, monkeypatch):
    sent = []
    monkeypatch.setenv("FEEDBACK_EMAIL", "maintainer@example.org")
    monkeypatch.setattr("muddypoints.mailer.send", lambda to, subject, body, reply_to=None: sent.append(body) or True)
    register(client)
    sent.clear()  # registration also sends a verification mail
    assert client.post("/api/feedback", {"message": "Great tool, thanks!"})[0] == 200
    assert "Alice <alice@uni.example>" in sent[0]
    assert client.post("/api/feedback", {"message": "hi"})[0] == 400
    assert client.post("/api/feedback", {"message": "valid message", "email": "nope"})[0] == 400


def test_feedback_honeypot_and_rate_limit(client, monkeypatch):
    sent = []
    monkeypatch.setenv("FEEDBACK_EMAIL", "maintainer@example.org")
    monkeypatch.setattr("muddypoints.mailer.send", lambda *a, **k: sent.append(a) or True)
    assert Client().post("/api/feedback", {"message": "buy cheap pills", "website": "spam.example"}, token=None)[0] == 200
    assert sent == []
    statuses = [Client().post("/api/feedback", {"message": f"idea number {i}"}, token=None)[0] for i in range(7)]
    assert statuses[:5] == [200] * 5 and statuses[5] == 429


def test_feedback_reports_whether_it_was_mailed(client, monkeypatch):
    # no FEEDBACK_EMAIL: stored, but the page must not claim it was sent
    s, d = client.post("/api/feedback", {"message": "Please add dark mode"}, token=None)
    assert s == 200 and d == {"ok": True, "mailed": False}
    monkeypatch.setenv("FEEDBACK_EMAIL", "maintainer@example.org")
    monkeypatch.setattr("muddypoints.mailer.send", lambda *a, **k: False)  # provider failure
    assert client.post("/api/feedback", {"message": "Please add dark mode"}, token=None)[1]["mailed"] is False
    monkeypatch.setattr("muddypoints.mailer.send", lambda *a, **k: True)
    assert client.post("/api/feedback", {"message": "Please add dark mode"}, token=None)[1]["mailed"] is True


def test_web_feedback_with_context(client, monkeypatch):
    sent = []
    monkeypatch.setenv("FEEDBACK_EMAIL", "maintainer@example.org")
    monkeypatch.setattr("muddypoints.mailer.send", lambda to, subject, body, reply_to=None: sent.append(body) or True)
    s, d = Client().post("/api/feedback", {"message": "Please add LaTeX support", "context": {
        "role": "lecturer", "discipline": "Physics", "course_size": "100-300", "unknown": "dropped"}}, token=None)
    assert s == 200 and "role: lecturer" in sent[0] and "discipline: Physics" in sent[0] and "unknown" not in sent[0]
    s, d = Client().post("/api/feedback", {"message": "Another idea here", "context": {"course_size": "huge"}}, token=None)
    assert s == 400


def test_claude_feedback_and_superadmin_list(client, monkeypatch):
    from test_admin import _verify
    from muddypoints import feedback, users
    sent = []
    monkeypatch.setenv("FEEDBACK_EMAIL", "maintainer@example.org")
    monkeypatch.setenv("SUPERADMIN_EMAILS", "boss@uni.example")
    monkeypatch.setattr("muddypoints.mailer.send", lambda to, subject, body, reply_to=None: sent.append((subject, body)) or True)
    alice = register(client, email="alice@uni.example", name="Alice")
    _, key = client.post("/api/me/api-keys", {"label": "Claude"})
    sent.clear()  # drop the registration's verification mail
    import json
    call = lambda name, args, k=key["key"], ua="Claude-User": Client().request("POST", f"/mcp/{k}", token=None, raw=json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}),
        headers={"user-agent": ua})[1]["result"]
    r = call("send_feedback", {"category": "missing_feature", "summary": "Sessions should take several files",
                               "details": "Week 9 has two lectures.", "context": {"role": "lecturer", "other_tools": ["OLAT"]}})
    assert not r["isError"] and sent[0][0] == "[Muddy Points · Claude feedback] missing_feature: Sessions should take several files"
    assert "via Claude" in sent[0][1] and "other_tools: OLAT" in sent[0][1]
    assert call("send_feedback", {"category": "rant", "summary": "nope nope"})["isError"]
    # superadmin-only list: Alice gets 'not found'
    assert call("list_feedback", {})["isError"]
    assert client.get("/api/admin/feedback")[0] == 404
    boss = Client()
    register(boss, email="boss@uni.example", name="Boss")
    _verify(boss, "boss@uni.example")
    s, d = boss.get("/api/admin/feedback")
    assert s == 200 and d["feedback"][0]["source"] == "assistant" and d["feedback"][0]["assistant"] == "Claude"
    assert d["feedback"][0]["context"]["role"] == "lecturer"
    fid = d["feedback"][0]["feedback_id"]
    assert boss.patch(f"/api/admin/feedback/{fid}", {"done": True})[1]["done"] is True
    assert boss.get("/api/admin/feedback?open=1")[1]["feedback"] == []
    # ChatGPT is recognised from its user agent; old field name claude_client still accepted
    sent.clear()
    r = call("send_feedback", {"category": "bug", "summary": "Image results not shown",
                               "context": {"claude_client": "desktop_app"}}, ua="openai-mcp/1.0.0")
    assert not r["isError"] and sent[0][0].startswith("[Muddy Points · ChatGPT feedback] bug:")
    assert "ai_client: claude_desktop" in sent[0][1]
    assert boss.get("/api/admin/feedback?source=assistant")[1]["feedback"][0]["assistant"] == "ChatGPT"
    # rate limit: 10 per user per day
    for i in range(8):
        call("send_feedback", {"category": "idea", "summary": f"idea number {i}"})
    r = call("send_feedback", {"category": "idea", "summary": "one too many"})
    assert r["isError"] and "limit" in r["content"][0]["text"].lower()
