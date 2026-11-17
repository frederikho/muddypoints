from conftest import Client, make_pdf, make_png, register, upload

from muddypoints import identity


def anon_client() -> Client:
    c = Client()
    s, ident = c.post("/api/identity", token=None, anon=None)
    assert s == 200
    c.anon = ident["token"]
    c.name = ident["name"]
    return c


def test_identity_gets_funny_name():
    s, ident = Client().post("/api/identity")
    assert ident["name"] in identity.names()
    assert len(identity.names()) > 500
    assert ident["expires_at"] > 0
    # a forged name is rejected because the token is signed
    forged = ident["token"].replace(ident["token"][5], "A" if ident["token"][5] != "A" else "B", 1)
    assert identity.viewer_from_anon_token(forged) is None


def test_upload_pdf_and_images(client, owner):
    bid = owner["board_id"]
    s, d = upload(client, bid, [("lecture.pdf", "application/pdf", make_pdf(3)),
                                ("extra.png", "image/png", make_png(400, 300))])
    assert s == 200, d
    assert len(d["added_slides"]) == 4
    assert d["added_slides"][0]["source"] == "lecture.pdf p.1"
    assert d["added_slides"][3]["source"] == "extra.png"
    assert (d["added_slides"][3]["width"], d["added_slides"][3]["height"]) == (400, 300)
    assert d["added_slides"][0]["width"] == 1920  # PDF rendered at 2x
    s, view = Client().get(f"/api/boards/{bid}")
    assert s == 200 and [sl["position"] for sl in view["slides"]] == [0, 1, 2, 3]
    assert view["slides"][0]["image_url"].startswith("https://")
    assert view["my_role"] is None


def test_upload_rejects_bad_input(client, owner):
    bid = owner["board_id"]
    s, d = client.post(f"/api/boards/{bid}/uploads", {"files": [{"filename": "x.pptx", "content_type": "application/vnd.ms-powerpoint"}]})
    assert s == 400
    s, d = upload(client, bid, [("broken.pdf", "application/pdf", b"not a pdf at all")])
    assert s == 400 and d["error"]["code"] == "unsupported_file"
    s, d = client.post(f"/api/boards/{bid}/uploads/confirm", {"upload_keys": ["uploads/otherboard/x/y.pdf"]})
    assert s == 400
    s, d = client.post(f"/api/boards/{bid}/uploads/confirm", {"upload_keys": [f"uploads/{bid}/nope/y.pdf"]})
    assert s == 404
    assert Client().post(f"/api/boards/{bid}/uploads", {"files": []})[0] == 401


def test_replace_requires_owner_and_archives(client, owner):
    bid, sid = owner["board_id"], owner["space_id"]
    upload(client, bid, [("w1.pdf", "application/pdf", make_pdf(2))])
    student = anon_client()
    slide = Client().get(f"/api/boards/{bid}")[1]["slides"][0]["slide_id"]
    student.post(f"/api/boards/{bid}/comments", {"slide_id": slide, "text": "Unclear"})

    bob = Client()
    register(bob, email="bob@uni.example", name="Bob")
    client.post(f"/api/spaces/{sid}/members", {"email": "bob@uni.example"})
    s, d = upload(bob, bid, [("w2.pdf", "application/pdf", make_pdf(1))], mode="replace")
    assert s == 403
    s, d = upload(bob, bid, [("w2.pdf", "application/pdf", make_pdf(1))], mode="append")
    assert s == 200

    s, d = upload(client, bid, [("w2.pdf", "application/pdf", make_pdf(1))], mode="replace")
    assert s == 200 and d["removed_slides"] == 3 and len(d["added_slides"]) == 1
    assert d["archive"]["comments"] == 1
    view = Client().get(f"/api/boards/{bid}")[1]
    assert len(view["slides"]) == 1 and view["slides"][0]["comments"] == []
    s, d = client.get(f"/api/boards/{bid}/archives")
    assert len(d["archives"]) == 1 and d["archives"][0]["key"].endswith(".pdf")


def test_comments_and_likes(client, owner):
    bid = owner["board_id"]
    upload(client, bid, [("w1.pdf", "application/pdf", make_pdf(2))])
    slide = Client().get(f"/api/boards/{bid}")[1]["slides"][0]["slide_id"]
    student = anon_client()

    # anonymous users need an identity
    assert Client().post(f"/api/boards/{bid}/comments", {"slide_id": slide, "text": "hi"})[0] == 401
    s, d = student.post(f"/api/boards/{bid}/comments", {"slide_id": slide, "text": "  What is a p-value?  "})
    assert s == 200 and d["comment"]["author_name"] == student.name and d["comment"]["text"] == "What is a p-value?"
    cid = d["comment"]["comment_id"]
    assert student.post(f"/api/boards/{bid}/comments", {"slide_id": slide, "text": ""})[0] == 400
    assert student.post(f"/api/boards/{bid}/comments", {"slide_id": slide, "text": "x" * 2001})[0] == 400
    assert student.post(f"/api/boards/{bid}/comments", {"slide_id": "nope", "text": "x"})[0] == 404

    # staff comment is flagged
    s, d = client.post(f"/api/boards/{bid}/comments", {"slide_id": slide, "text": "See chapter 3"})
    assert d["comment"]["is_staff"] and d["comment"]["author_name"] == "Alice"

    # likes toggle, per viewer
    other = anon_client()
    assert student.post(f"/api/boards/{bid}/likes", {"slide_id": slide})[1] == {"liked": True, "likes": 1}
    assert other.post(f"/api/boards/{bid}/likes", {"slide_id": slide})[1] == {"liked": True, "likes": 2}
    assert student.post(f"/api/boards/{bid}/likes", {"slide_id": slide})[1] == {"liked": False, "likes": 1}
    assert other.post(f"/api/boards/{bid}/likes", {"comment_id": cid})[1]["likes"] == 1
    assert other.post(f"/api/boards/{bid}/likes", {})[0] == 400

    view = other.get(f"/api/boards/{bid}")[1]
    s0 = view["slides"][0]
    assert s0["likes"] == 1 and s0["liked"] is True
    assert [c["text"] for c in s0["comments"]] == ["What is a p-value?", "See chapter 3"]
    assert s0["comments"][0]["liked"] and not s0["comments"][0]["mine"]
    assert view["viewer"]["name"] == other.name

    # only author or owner can delete
    assert other.delete(f"/api/boards/{bid}/comments/{cid}")[0] == 403
    assert student.delete(f"/api/boards/{bid}/comments/{cid}")[0] == 200
    staff_cid = view["slides"][0]["comments"][1]["comment_id"]
    assert client.delete(f"/api/boards/{bid}/comments/{staff_cid}")[0] == 200
    assert other.get(f"/api/boards/{bid}")[1]["slides"][0]["comments"] == []


def test_locked_board(client, owner):
    bid = owner["board_id"]
    upload(client, bid, [("w1.pdf", "application/pdf", make_pdf(1))])
    slide = Client().get(f"/api/boards/{bid}")[1]["slides"][0]["slide_id"]
    client.patch(f"/api/boards/{bid}", {"locked": True})
    student = anon_client()
    assert student.post(f"/api/boards/{bid}/comments", {"slide_id": slide, "text": "late"})[0] == 423
    assert student.post(f"/api/boards/{bid}/likes", {"slide_id": slide})[0] == 423
    assert client.post(f"/api/boards/{bid}/comments", {"slide_id": slide, "text": "staff ok"})[0] == 200


def test_reorder_delete_clear(client, owner):
    bid = owner["board_id"]
    upload(client, bid, [("w1.pdf", "application/pdf", make_pdf(3))])
    ids = [s["slide_id"] for s in Client().get(f"/api/boards/{bid}")[1]["slides"]]
    assert client.post(f"/api/boards/{bid}/slides/reorder", {"slide_ids": ids[:2]})[0] == 400
    assert client.post(f"/api/boards/{bid}/slides/reorder", {"slide_ids": ids[::-1]})[0] == 200
    assert [s["slide_id"] for s in Client().get(f"/api/boards/{bid}")[1]["slides"]] == ids[::-1]
    student = anon_client()
    student.post(f"/api/boards/{bid}/comments", {"slide_id": ids[0], "text": "q"})
    assert client.delete(f"/api/boards/{bid}/slides/{ids[0]}")[0] == 200
    view = Client().get(f"/api/boards/{bid}")[1]
    assert len(view["slides"]) == 2
    s, d = client.post(f"/api/boards/{bid}/clear", {"archive": False})
    assert s == 200 and d["removed_slides"] == 2 and d["archive"] is None
    assert Client().get(f"/api/boards/{bid}")[1]["slides"] == []


def test_export_pdf(client, owner):
    import pymupdf
    from muddypoints import export
    bid = owner["board_id"]
    upload(client, bid, [("w1.pdf", "application/pdf", make_pdf(2))])
    slide = Client().get(f"/api/boards/{bid}")[1]["slides"][0]["slide_id"]
    anon_client().post(f"/api/boards/{bid}/comments", {"slide_id": slide, "text": "Warum ist das so? Äpfel & <Birnen>"})
    s, d = client.post(f"/api/boards/{bid}/export", {})
    assert s == 200 and d["comments"] == 1 and d["download_url"].startswith("https://")
    assert d["filename"].startswith("Week-1-")
    pdf, info = export.build_pdf(bid)
    text = "".join(p.get_text() for p in pymupdf.open(stream=pdf))
    assert "Warum ist das so? Äpfel & <Birnen>" in text and "Slide 2" in text
    pdf, _ = export.build_pdf(bid, include_uncommented=False)
    assert "Slide 2" not in "".join(p.get_text() for p in pymupdf.open(stream=pdf))


def test_board_background(client, owner):
    bid, sid = owner["board_id"], owner["space_id"]
    assert Client().get(f"/api/boards/{bid}")[1]["board"]["background"] == "plain"
    s, d = client.patch(f"/api/boards/{bid}", {"background": "cork"})
    assert s == 200 and d["board"]["background"] == "cork"
    assert Client().get(f"/api/boards/{bid}")[1]["board"]["background"] == "cork"
    assert client.get(f"/api/spaces/{sid}")[1]["boards"][0]["background"] == "cork"
    assert client.patch(f"/api/boards/{bid}", {"background": "url(evil)"})[0] == 400
    s, d = client.post(f"/api/spaces/{sid}/boards", {"name": "W2", "background": "chalkboard"})
    assert d["board"]["background"] == "chalkboard"
    assert client.post(f"/api/spaces/{sid}/boards", {"name": "W3", "background": "nope"})[0] == 400
