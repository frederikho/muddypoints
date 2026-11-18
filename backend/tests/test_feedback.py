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
