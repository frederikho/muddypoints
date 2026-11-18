import json

from muddypoints import mailer


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
    monkeypatch.setattr("muddypoints.mailer.urllib.request.urlopen", fake_urlopen)
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
    monkeypatch.setattr("muddypoints.mailer.urllib.request.urlopen", boom)
    assert mailer.send("x@uni.example", "s", "b") is False


def test_to_html_links_urls_and_escapes():
    body = "Hi <b>Al</b> & co,\n\nopen https://x.example/verify.html?token=a-b_c.d&x=1 now.\nSee (https://y.example/p).\n"
    out = mailer.to_html(body)
    assert '<a href="https://x.example/verify.html?token=a-b_c.d&amp;x=1">https://x.example/verify.html?token=a-b_c.d&amp;x=1</a> now.' in out
    assert '(<a href="https://y.example/p">https://y.example/p</a>).' in out   # trailing ")" and "." stay outside
    assert "&lt;b&gt;Al&lt;/b&gt; &amp; co,<br>" in out                        # user text can't inject HTML
    assert "<b>" not in out and "\n\n" not in out.replace("<br>\n", "")


def test_brevo_sends_html_and_text(monkeypatch):
    monkeypatch.setenv("MAIL_FROM", "boards@uni.example")
    monkeypatch.setenv("BREVO_API_KEY", "xkeysib-test")
    sent = {}
    monkeypatch.setattr("muddypoints.mailer.urllib.request.urlopen",
                        lambda req, timeout: sent.update(body=json.loads(req.data)) or FakeResponse())
    assert mailer.send("a@uni.example", "Confirm", "open https://x.example/v?t=1") is True
    assert sent["body"]["textContent"] == "open https://x.example/v?t=1"
    assert '<a href="https://x.example/v?t=1">' in sent["body"]["htmlContent"]
