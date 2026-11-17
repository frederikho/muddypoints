import base64
import json
import os

import pytest

os.environ.update({
    "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_SESSION_TOKEN": "testing",
    "AWS_REGION": "eu-central-1", "AWS_DEFAULT_REGION": "eu-central-1",
    "TABLE_NAME": "muddypoints-test", "BUCKET_NAME": "muddypoints-test-media", "SECRET_KEY": "test-secret",
    "FRONTEND_URL": "https://example.github.io/muddypoints", "MAIL_FROM": "", "LOG_LEVEL": "WARNING",
})
os.environ.pop("AWS_ENDPOINT_URL", None)

from moto import mock_aws  # noqa: E402

from muddypoints import aws  # noqa: E402
from muddypoints.handler import lambda_handler  # noqa: E402
from muddypoints.localinfra import create_resources  # noqa: E402


@pytest.fixture(autouse=True)
def aws_env():
    with mock_aws():
        aws.reset()
        create_resources()
        yield
    aws.reset()


class Client:
    """Calls the Lambda handler with Function-URL-shaped events."""

    def __init__(self):
        self.token = None   # session token or API key
        self.anon = None    # anonymous identity token

    def request(self, method, path, body=None, token=..., anon=..., raw=None, headers=None):
        token = self.token if token is ... else token
        anon = self.anon if anon is ... else anon
        h = {"content-type": "application/json", **(headers or {})}
        if token:
            h["authorization"] = f"Bearer {token}"
        if anon:
            h["x-anon-token"] = anon
        path, _, qs = path.partition("?")
        payload = raw if raw is not None else (json.dumps(body) if body is not None else "")
        event = {"rawPath": path, "rawQueryString": qs, "headers": h,
                 "requestContext": {"http": {"method": method, "path": path, "sourceIp": "1.2.3.4"}},
                 "body": base64.b64encode(payload.encode()).decode(), "isBase64Encoded": True}
        resp = lambda_handler(event, None)
        data = json.loads(resp["body"]) if resp["body"] else None
        return resp["statusCode"], data

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body if body is not None else {}, **kw)

    def patch(self, path, body=None, **kw):
        return self.request("PATCH", path, body or {}, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)


@pytest.fixture
def client():
    return Client()


def register(client: Client, email="alice@uni.example", name="Alice", password="password123") -> dict:
    status, data = client.post("/api/auth/register", {"email": email, "password": password, "name": name}, token=None)
    assert status == 200, data
    client.token = data["token"]
    return data["user"]


@pytest.fixture
def owner(client):
    """A registered user (logged in on `client`) owning one space with one board."""
    user = register(client)
    _, sp = client.post("/api/spaces", {"name": "Statistics 101"})
    _, bd = client.post(f"/api/spaces/{sp['space']['space_id']}/boards", {"name": "Week 1"})
    return {"user": user, "space_id": sp["space"]["space_id"], "board_id": bd["board"]["board_id"],
            "token": client.token}


def make_pdf(pages=3) -> bytes:
    import pymupdf
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page(width=960, height=540)
        page.insert_text((80, 120), f"Slide {i + 1}", fontsize=48)
    return doc.tobytes()


def make_png(w=400, h=300) -> bytes:
    import pymupdf
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, w, h), False)
    pix.clear_with(180)
    return pix.tobytes("png")


def upload(client: Client, board_id: str, files: list[tuple[str, str, bytes]], mode="append", archive=True):
    """Simulate the browser flow: prepare -> PUT to S3 -> confirm."""
    from muddypoints.config import settings
    status, data = client.post(f"/api/boards/{board_id}/uploads",
                               {"files": [{"filename": n, "content_type": ct} for n, ct, _ in files]})
    assert status == 200, data
    for (name, ct, blob), up in zip(files, data["uploads"]):
        aws.s3().put_object(Bucket=settings.bucket_name, Key=up["upload_key"], Body=blob, ContentType=ct)
    return client.post(f"/api/boards/{board_id}/uploads/confirm",
                       {"upload_keys": [u["upload_key"] for u in data["uploads"]], "mode": mode, "archive": archive})
