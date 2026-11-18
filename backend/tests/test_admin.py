from conftest import register

from muddypoints import auth, db


def _verify(client, email):
    uid = db.get(f"EMAIL#{email}", "EMAIL")["user_id"]
    token = auth.sign({"t": "v", "uid": uid, "em": email}, 60)
    assert client.post("/api/auth/verify", {"token": token}, token=None)[0] == 200


def test_superadmin_sees_all_spaces(client, monkeypatch):
    monkeypatch.setenv("SUPERADMIN_EMAILS", "Boss@uni.example, other@uni.example")
    register(client, email="alice@uni.example", name="Alice")
    assert client.post("/api/spaces", {"name": "Alice's course"})[0] in (200, 201)
    register(client, email="bob@uni.example", name="Bob")
    assert client.post("/api/spaces", {"name": "Bob's course"})[0] in (200, 201)
    me = register(client, email="boss@uni.example", name="Boss")
    assert me["is_superadmin"] is False                     # listed, but not verified yet
    assert client.get("/api/admin/spaces")[0] == 404
    _verify(client, "boss@uni.example")
    assert client.get("/api/me")[1]["user"]["is_superadmin"] is True
    s, d = client.get("/api/admin/spaces")
    assert s == 200
    names = {sp["name"] for sp in d["spaces"]}
    assert names == {"Alice's course", "Bob's course"}      # not a member of either
    alice = next(sp for sp in d["spaces"] if sp["name"] == "Alice's course")
    assert alice["members"][0]["email"] == "alice@uni.example" and "role" not in alice
    assert client.get("/api/spaces")[1]["spaces"] == []      # the normal list is unchanged


def test_admin_hidden_from_everyone_else(client, monkeypatch):
    monkeypatch.setenv("SUPERADMIN_EMAILS", "boss@uni.example")
    assert client.get("/api/admin/spaces", token=None)[0] == 401
    register(client, email="alice@uni.example")
    _verify(client, "alice@uni.example")
    assert client.get("/api/me")[1]["user"]["is_superadmin"] is False
    assert client.get("/api/admin/spaces")[0] == 404


def test_admin_disabled_without_setting(client):
    register(client, email="boss@uni.example")
    _verify(client, "boss@uni.example")
    assert client.get("/api/admin/spaces")[0] == 404
