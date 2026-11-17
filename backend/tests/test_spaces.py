from conftest import Client, register


def test_space_crud_and_listing(client, owner):
    s, d = client.get("/api/spaces")
    assert [sp["name"] for sp in d["spaces"]] == ["Statistics 101"]
    assert d["spaces"][0]["role"] == "owner"
    sid = owner["space_id"]
    s, d = client.patch(f"/api/spaces/{sid}", {"name": "Stats"})
    assert d["space"]["name"] == "Stats"
    s, d = client.get(f"/api/spaces/{sid}")
    assert d["boards"][0]["name"] == "Week 1"
    assert d["boards"][0]["url"].endswith(f"board.html?b={owner['board_id']}")
    assert client.delete(f"/api/spaces/{sid}")[0] == 200
    assert client.get(f"/api/spaces/{sid}")[0] == 404
    assert client.get(f"/api/boards/{owner['board_id']}")[0] == 404


def test_non_member_sees_404(client, owner):
    other = Client()
    register(other, email="mallory@uni.example", name="Mallory")
    assert other.get(f"/api/spaces/{owner['space_id']}")[0] == 404
    assert other.patch(f"/api/boards/{owner['board_id']}", {"name": "hacked"})[0] == 404


def test_invite_existing_user_and_editor_permissions(client, owner):
    bob = Client()
    bob_user = register(bob, email="bob@uni.example", name="Bob")
    sid, bid = owner["space_id"], owner["board_id"]
    s, d = client.post(f"/api/spaces/{sid}/members", {"email": "bob@uni.example"})
    assert s == 200 and d["status"] == "added" and d["role"] == "editor"
    assert client.post(f"/api/spaces/{sid}/members", {"email": "bob@uni.example"})[0] == 409
    # editor can view, rename, create boards, lock ...
    assert bob.get(f"/api/spaces/{sid}")[0] == 200
    assert bob.patch(f"/api/spaces/{sid}", {"name": "Stats (Bob)"})[0] == 200
    assert bob.post(f"/api/spaces/{sid}/boards", {"name": "Week 2"})[0] == 200
    assert bob.patch(f"/api/boards/{bid}", {"locked": True})[0] == 200
    # ... but cannot delete or manage roles
    assert bob.delete(f"/api/boards/{bid}")[0] == 403
    assert bob.delete(f"/api/spaces/{sid}")[0] == 403
    assert bob.post(f"/api/spaces/{sid}/members", {"email": "x@uni.example", "role": "owner"})[0] == 403
    assert bob.patch(f"/api/spaces/{sid}/members/{owner['user']['user_id']}", {"role": "editor"})[0] == 403
    assert bob.delete(f"/api/spaces/{sid}/members/{owner['user']['user_id']}")[0] == 403
    # editors may invite other editors and leave themselves
    assert bob.post(f"/api/spaces/{sid}/members", {"email": "carol@uni.example"})[1]["status"] == "invited"
    assert bob.delete(f"/api/spaces/{sid}/members/{bob_user['user_id']}")[0] == 200
    assert bob.get(f"/api/spaces/{sid}")[0] == 404


def test_pending_invite_applied_on_registration(client, owner):
    sid = owner["space_id"]
    client.post(f"/api/spaces/{sid}/members", {"email": "Carol@Uni.example", "role": "owner"})
    s, d = client.get(f"/api/spaces/{sid}/members")
    assert d["invites"] == [{"email": "carol@uni.example", "role": "owner", "invited_at": d["invites"][0]["invited_at"]}]
    carol = Client()
    register(carol, email="carol@uni.example", name="Carol")
    s, d = carol.get("/api/spaces")
    assert d["spaces"][0]["role"] == "owner"
    s, d = client.get(f"/api/spaces/{sid}/members")
    assert d["invites"] == [] and len(d["members"]) == 2


def test_revoke_invite(client, owner):
    sid = owner["space_id"]
    client.post(f"/api/spaces/{sid}/members", {"email": "dave@uni.example"})
    assert client.delete(f"/api/spaces/{sid}/invites/dave%40uni.example")[0] == 200
    assert client.get(f"/api/spaces/{sid}/members")[1]["invites"] == []


def test_last_owner_protection(client, owner):
    sid, uid = owner["space_id"], owner["user"]["user_id"]
    s, d = client.patch(f"/api/spaces/{sid}/members/{uid}", {"role": "editor"})
    assert s == 409 and d["error"]["code"] == "last_owner"
    assert client.delete(f"/api/spaces/{sid}/members/{uid}")[0] == 409


def test_promote_and_remove(client, owner):
    sid = owner["space_id"]
    bob = Client()
    bob_user = register(bob, email="bob@uni.example", name="Bob")
    client.post(f"/api/spaces/{sid}/members", {"email": "bob@uni.example"})
    assert client.patch(f"/api/spaces/{sid}/members/{bob_user['user_id']}", {"role": "owner"})[0] == 200
    assert bob.delete(f"/api/boards/{owner['board_id']}")[0] == 200
    assert client.delete(f"/api/spaces/{sid}/members/{bob_user['user_id']}")[0] == 200
