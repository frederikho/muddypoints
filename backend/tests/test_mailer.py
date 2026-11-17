import json

from padlet import mailer


class FakeResponse:
    status = 201

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_dev_mode_only_logs():
    assert mailer.send("a@b.de", "Hi", "body") is False


def test_brevo(monkeypatch):
    monkeypatch.setenv("MAIL_FROM", "boards@uni.example")
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-test")
    sent = {}

    def fake_urlopen(req, timeout):
        sent.update(url=req.full_url, headers=dict(req.headers), body=json.loads(req.data))
        return FakeResponse()
    monkeypatch.setattr("padlet.mailer.urllib.request.urlopen", fake_urlopen)
    assert mailer.send("student@uni.example", "Reset", "link") is True
    assert sent["url"] == mailer.BREVO_URL
    assert sent["headers"]["Api-key"] == "xkeysib-test"
    assert sent["body"]["to"] == [{"email": "student@uni.example"}]
    assert sent["body"]["sender"]["email"] == "boards@uni.example"


def test_brevo_failure_does_not_raise(monkeypatch):
    monkeypatch.setenv("MAIL_FROM", "boards@uni.example")
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-test")

    def boom(req, timeout):
        raise OSError("network down")
    monkeypatch.setattr("padlet.mailer.urllib.request.urlopen", boom)
    assert mailer.send("x@uni.example", "s", "b") is False
