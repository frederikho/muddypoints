import time

from conftest import register

from padlet import auth, db


def test_password_hash_roundtrip():
    h = auth.hash_password("secret-pass")
    assert auth.verify_password("secret-pass", h)
    assert not auth.verify_password("wrong", h)
    assert not auth.verify_password("x", "garbage")


def test_signed_tokens_reject_tampering_and_wrong_kind():
    token = auth.sign({"t": "s", "uid": "u1"}, 60)
    assert auth.unsign(token, "s")["uid"] == "u1"
    assert auth.unsign(token, "a") is None
    body, mac = token.split(".")
    assert auth.unsign(body + "x." + mac, "s") is None
    assert auth.unsign(auth.sign({"t": "s"}, -1), "s") is None
    assert auth.unsign("nonsense", "s") is None


def test_register_login_me(client):
    user = register(client)
    assert user["email"] == "alice@uni.example"
    status, data = client.get("/api/me")
    assert status == 200 and data["user"]["name"] == "Alice"
    status, data = client.post("/api/auth/login", {"email": "ALICE@uni.example ", "password": "password123"}, token=None)
    assert status == 200 and data["token"]
    status, data = client.post("/api/auth/login", {"email": "alice@uni.example", "password": "nope"}, token=None)
    assert status == 401


def test_register_validation(client):
    register(client)
    s, d = client.post("/api/auth/register", {"email": "alice@uni.example", "password": "password123", "name": "X"})
    assert s == 409 and d["error"]["code"] == "email_taken"
    s, d = client.post("/api/auth/register", {"email": "bob@uni.example", "password": "short", "name": "Bob"})
    assert s == 400 and d["error"]["code"] == "weak_password"
    s, d = client.post("/api/auth/register", {"email": "not-an-email", "password": "password123", "name": "Bob"})
    assert s == 400
    s, d = client.post("/api/auth/register", {"email": "bob@uni.example", "password": "password123", "name": ""})
    assert s == 400


def test_allowed_domains(client, monkeypatch):
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAINS", "uni.example")
    s, d = client.post("/api/auth/register", {"email": "x@gmail.com", "password": "password123", "name": "X"})
    assert s == 403
    register(client, email="y@uni.example")


def test_unauthorized(client):
    assert client.get("/api/me")[0] == 401
    assert client.get("/api/me", token="garbage")[0] == 401


def test_password_reset_flow(client, monkeypatch):
    register(client)
    old_token = client.token
    sent = []
    monkeypatch.setattr("padlet.mailer.send", lambda to, subject, body: sent.append((to, body)) or True)
    assert client.post("/api/auth/forgot", {"email": "alice@uni.example"}, token=None)[0] == 200
    # same answer for unknown emails (no account enumeration), but no mail
    assert client.post("/api/auth/forgot", {"email": "nobody@uni.example"}, token=None)[0] == 200
    assert len(sent) == 1 and sent[0][0] == "alice@uni.example"
    link = next(w for w in sent[0][1].split() if w.startswith("https://"))
    assert link.startswith("https://example.github.io/padlet/reset.html?token=")
    token = link.split("token=")[1]
    s, d = client.post("/api/auth/reset", {"token": token, "password": "newpassword1"}, token=None)
    assert s == 200 and d["token"]
    # token is single use
    assert client.post("/api/auth/reset", {"token": token, "password": "another123"}, token=None)[0] == 400
    # old sessions are revoked, new password works
    assert client.get("/api/me", token=old_token)[0] == 401
    assert client.post("/api/auth/login", {"email": "alice@uni.example", "password": "newpassword1"}, token=None)[0] == 200


def test_expired_reset_token(client):
    register(client)
    from padlet import auth as a
    token = "abc"
    db.put({"PK": f"RESET#{a.sha256(token)}", "SK": "RESET", "user_id": "x", "ttl": int(time.time()) - 5})
    assert client.post("/api/auth/reset", {"token": token, "password": "newpassword1"}, token=None)[0] == 400


def test_change_password_and_profile(client):
    register(client)
    s, d = client.post("/api/me/password", {"old_password": "wrong", "new_password": "newpassword1"})
    assert s == 400
    s, d = client.post("/api/me/password", {"old_password": "password123", "new_password": "newpassword1"})
    assert s == 200
    old = client.token
    client.token = d["token"]
    assert client.get("/api/me", token=old)[0] == 401
    s, d = client.patch("/api/me", {"name": "Prof. Alice"})
    assert d["user"]["name"] == "Prof. Alice"


def test_api_keys(client):
    register(client)
    s, key = client.post("/api/me/api-keys", {"label": "Claude Web"})
    assert s == 200 and key["key"].startswith("pk_")
    s, d = client.get("/api/me", token=key["key"])
    assert s == 200 and d["user"]["name"] == "Alice"
    s, d = client.get("/api/me/api-keys")
    assert [k["label"] for k in d["api_keys"]] == ["Claude Web"]
    assert "hash" not in d["api_keys"][0]
    assert client.delete(f"/api/me/api-keys/{key['key_id']}")[0] == 200
    assert client.get("/api/me", token=key["key"])[0] == 401
