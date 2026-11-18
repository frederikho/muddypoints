import hashlib
import time

from conftest import Client, make_pdf, make_png, register, upload

from muddypoints import aws, db
from muddypoints.config import settings


def make_text_pdf(pages: list[str], title: str | None = None) -> bytes:
    import pymupdf
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page(width=960, height=540)
        page.insert_textbox(page.rect + (20, 20, -20, -20), text, fontsize=24 if len(text) < 200 else 6)
    if title:
        doc.set_metadata({"title": title})
        doc.set_toc([[1, t.split("\n")[0], i + 1] for i, t in enumerate(pages)])
    return doc.tobytes()


def drop_upload(token: str, files: list[tuple[str, bytes]]):
    """Simulate the drop page: hash -> check -> PUT the unknown ones -> confirm."""
    anon = Client()
    s, d = anon.post(f"/api/drop/{token}/check", {"files": [
        {"name": n, "size": len(b), "sha256": hashlib.sha256(b).hexdigest(), "content_type": "application/pdf"}
        for n, b in files]}, token=None)
    assert s == 200, d
    results = []
    for (name, blob), item in zip(files, d["files"]):
        if item["status"] == "upload":
            aws.s3().put_object(Bucket=settings.bucket_name, Key=item["upload_key"], Body=blob)
            s2, d2 = anon.post(f"/api/drop/{token}/confirm",
                               {"upload_key": item["upload_key"], "sha256": hashlib.sha256(blob).hexdigest()}, token=None)
            assert s2 == 200, d2
            results.append(d2["file"])
        else:
            results.append(item)
    return results


def link_token(client, space_id, **kw) -> str:
    s, d = client.post(f"/api/spaces/{space_id}/upload-links", kw)
    assert s == 200, d
    assert d["upload_link"].startswith("https://example.github.io/muddypoints/drop.html#")
    return d["upload_link"].split("#", 1)[1]


def test_drop_link_upload_dedup_and_read(client, owner):
    sid = owner["space_id"]
    token = link_token(client, sid, note="Lectures 1-2")
    s, info = Client().get(f"/api/drop/{token}", token=None)
    assert s == 200 and info["workspace"] == "Statistics 101" and info["note"] == "Lectures 1-2"
    l1 = make_text_pdf(["Lecture 1: Regression\nOLS basics", "Assumptions", "Residuals"], title="Lecture 1 – Regression")
    l2 = make_text_pdf(["Lecture 2: Inference", "t-tests"])
    res = drop_upload(token, [("L1.pdf", l1), ("L2.pdf", l2)])
    assert [r["status"] for r in res] == ["added", "added"]
    # same content again -> recognised, nothing uploaded
    res = drop_upload(token, [("L1-copy.pdf", l1)])
    assert res[0]["status"] == "exists"
    files = client.get(f"/api/spaces/{sid}/files")[1]["files"]
    assert len(files) == 2
    f1 = next(f for f in files if f["name"] == "L1.pdf")
    assert f1["pages"] == 3 and f1["title"] == "L1"  # the file name, not the embedded PDF title
    # stored once, content-addressed
    blobs = [o["Key"] for o in aws.s3().list_objects_v2(Bucket=settings.bucket_name, Prefix="blobs/")["Contents"]]
    assert len([k for k in blobs if k.endswith(".pdf")]) == 2


def test_global_dedup_across_workspaces(client, owner):
    pdf = make_text_pdf(["Shared syllabus"])
    drop_upload(link_token(client, owner["space_id"]), [("syllabus.pdf", pdf)])
    _, other = client.post("/api/spaces", {"name": "Algebra"})
    res = drop_upload(link_token(client, other["space"]["space_id"]), [("syllabus.pdf", pdf)])
    assert res[0]["status"] == "added"  # linked without uploading
    assert len(client.get(f"/api/spaces/{other['space']['space_id']}/files")[1]["files"]) == 1


def test_drop_link_security(client, owner):
    sid = owner["space_id"]
    assert Client().post(f"/api/spaces/{sid}/upload-links", {}, token=None)[0] == 401
    mallory = Client()
    register(mallory, email="mallory@uni.example", name="Mallory")
    assert mallory.post(f"/api/spaces/{sid}/upload-links", {})[0] == 404
    assert Client().get("/api/drop/not-a-token", token=None)[0] == 404
    token = link_token(client, sid)
    # expired link
    item = db.get(f"DROP#{__import__('muddypoints.auth', fromlist=['x']).sha256(token)}", "DROP")
    db.update(item["PK"], "DROP", {"expires_at": int(time.time()) - 1})
    assert Client().get(f"/api/drop/{token}", token=None)[0] == 410
    # a link can't read files
    assert Client().get(f"/api/spaces/{sid}/files", token=token)[0] == 401


def test_rejects_non_pdf_and_hash_mismatch(client, owner):
    token = link_token(client, owner["space_id"])
    anon = Client()
    png = make_png()
    s, d = anon.post(f"/api/drop/{token}/check", {"files": [
        {"name": "photo.png", "sha256": hashlib.sha256(png).hexdigest(), "content_type": "image/png"}]}, token=None)
    assert d["files"][0]["status"] == "rejected"
    pdf = make_text_pdf(["x"])
    s, d = anon.post(f"/api/drop/{token}/check", {"files": [
        {"name": "a.pdf", "sha256": "0" * 64, "content_type": "application/pdf"}]}, token=None)
    key = d["files"][0]["upload_key"]
    aws.s3().put_object(Bucket=settings.bucket_name, Key=key, Body=pdf)
    s, d = anon.post(f"/api/drop/{token}/confirm", {"upload_key": key, "sha256": "0" * 64}, token=None)
    assert s == 400 and d["error"]["code"] == "hash_mismatch"
    # fake PDF content is rejected after upload
    s, d = anon.post(f"/api/drop/{token}/check", {"files": [
        {"name": "b.pdf", "sha256": hashlib.sha256(b"not a pdf").hexdigest(), "content_type": "application/pdf"}]}, token=None)
    key = d["files"][0]["upload_key"]
    aws.s3().put_object(Bucket=settings.bucket_name, Key=key, Body=b"not a pdf")
    assert anon.post(f"/api/drop/{token}/confirm", {"upload_key": key, "sha256": hashlib.sha256(b"not a pdf").hexdigest()},
                     token=None)[0] == 400


def test_publish_history_and_reuse(client, owner):
    sid, bid = owner["space_id"], owner["board_id"]
    res = drop_upload(link_token(client, sid), [("L1.pdf", make_pdf(2)), ("L2.pdf", make_pdf(3))])
    f1, f2 = res[0]["file_id"], res[1]["file_id"]
    s, d = client.post(f"/api/spaces/{sid}/files/{f1}/publish", {"board_id": bid, "mode": "replace"})
    assert s == 200 and len(d["added_slides"]) == 2
    s, d = client.post(f"/api/spaces/{sid}/files/{f2}/publish", {"board_id": bid, "mode": "replace"})
    assert len(d["added_slides"]) == 3
    hist = client.get(f"/api/boards/{bid}/history")[1]["history"]
    assert [h["files"][0]["file_id"] for h in hist] == [f2, f1]
    assert hist[0]["archived_previous_material"] is False  # no comments -> nothing worth archiving
    files = {f["file_id"]: f for f in client.get(f"/api/spaces/{sid}/files")[1]["files"]}
    assert files[f1]["published_to"][0]["board"] == "Week 1"
    # reuse: publish f1 again
    assert client.post(f"/api/spaces/{sid}/files/{f1}/publish", {"board_id": bid, "mode": "replace"})[0] == 200
    assert len(Client().get(f"/api/boards/{bid}")[1]["slides"]) == 2
    # board of another workspace is refused
    _, other = client.post("/api/spaces", {"name": "Other"})
    _, ob = client.post(f"/api/spaces/{other['space']['space_id']}/boards", {"name": "X"})
    assert client.post(f"/api/spaces/{sid}/files/{f1}/publish", {"board_id": ob["board"]["board_id"]})[0] == 400


def test_board_panel_uploads_land_in_files(client, owner):
    sid, bid = owner["space_id"], owner["board_id"]
    pdf = make_pdf(2)
    upload(client, bid, [("week1.pdf", "application/pdf", pdf), ("pic.png", "image/png", make_png())])
    files = client.get(f"/api/spaces/{sid}/files")[1]["files"]
    assert [f["name"] for f in files] == ["week1.pdf"]  # images are not kept as files
    # the same PDF via an upload link is recognised
    assert drop_upload(link_token(client, sid), [("again.pdf", pdf)])[0]["status"] == "exists"
    hist = client.get(f"/api/boards/{bid}/history")[1]["history"]
    assert {f["name"] for f in hist[0]["files"]} == {"week1.pdf", "pic.png"}


def test_download_and_delete(client, owner):
    sid = owner["space_id"]
    fid = drop_upload(link_token(client, sid), [("L1.pdf", make_pdf(1))])[0]["file_id"]
    s, d = client.get(f"/api/spaces/{sid}/files/{fid}/download")
    assert s == 200 and "blobs/" in d["download_url"]
    bob = Client()
    register(bob, email="bob@uni.example", name="Bob")
    client.post(f"/api/spaces/{sid}/members", {"email": "bob@uni.example"})
    assert bob.delete(f"/api/spaces/{sid}/files/{fid}")[0] == 200  # editors may delete files
    assert client.get(f"/api/spaces/{sid}/files")[1]["files"] == []


def test_titles_are_file_names():
    from muddypoints.library import pretty_name
    assert pretty_name("OdN_VL_9_-_Stated_Preference_Methods.pdf") == "OdN VL 9 - Stated Preference Methods"
    assert pretty_name("OdN+VL+1+-+Einfuhrung.pdf") == "OdN VL 1 - Einfuhrung"


def test_archive_only_when_there_are_comments(client, owner):
    sid, bid = owner["space_id"], owner["board_id"]
    upload(client, bid, [("w1.pdf", "application/pdf", make_pdf(2))])
    s, d = upload(client, bid, [("w2.pdf", "application/pdf", make_pdf(1))], mode="replace")
    assert d.get("archive") is None and client.get(f"/api/boards/{bid}/archives")[1]["archives"] == []
    student = Client()
    student.anon = student.post("/api/identity", token=None)[1]["token"]
    slide = Client().get(f"/api/boards/{bid}")[1]["slides"][0]["slide_id"]
    student.post(f"/api/boards/{bid}/comments", {"slide_id": slide, "text": "?"}, token=None)
    s, d = upload(client, bid, [("w3.pdf", "application/pdf", make_pdf(3))], mode="replace")
    assert d["archive"]["comments"] == 1 and len(client.get(f"/api/boards/{bid}/archives")[1]["archives"]) == 1


def test_inline_view_link(client, owner):
    sid = owner["space_id"]
    fid = drop_upload(link_token(client, sid), [("L1.pdf", make_pdf(1))])[0]["file_id"]
    inline = client.get(f"/api/spaces/{sid}/files/{fid}/download?inline=1")[1]["download_url"]
    download = client.get(f"/api/spaces/{sid}/files/{fid}/download")[1]["download_url"]
    assert "inline" in inline and "attachment" not in inline and "attachment" in download


def test_metadata_filters_and_url_import(client, owner, monkeypatch):
    sid = owner["space_id"]
    fid = drop_upload(link_token(client, sid), [("OdN_VL_5.pdf", make_pdf(2))])[0]["file_id"]
    s, d = client.patch(f"/api/spaces/{sid}/files/{fid}", {"lecture_number": "5", "topic": "Public goods",
                                                            "semester": "WS 2026/27", "tags": ["externalities", " "]})
    assert s == 200 and d["file"]["meta"] == {"lecture_number": 5, "topic": "Public goods", "semester": "WS 2026/27",
                                              "tags": ["externalities"]}
    assert client.patch(f"/api/spaces/{sid}/files/{fid}", {"lecture_number": "five"})[0] == 400
    from muddypoints import library
    user = __import__("muddypoints.users", fromlist=["x"]).get_user(owner["user"]["user_id"])
    assert [f["file_id"] for f in library.list_files(user, sid, query="public goods")] == [fid]
    assert library.list_files(user, sid, semester="ws 2026/27")[0]["file_id"] == fid
    assert library.list_files(user, sid, tag="Externalities")[0]["file_id"] == fid
    assert library.list_files(user, sid, query="regression") == []
    client.patch(f"/api/spaces/{sid}/files/{fid}", {"topic": None})
    assert "topic" not in library.list_files(user, sid)[0]["meta"]
    pdf = make_pdf(3)
    monkeypatch.setattr("muddypoints.media.download", lambda url: (pdf, "syllabus"))
    res = library.import_url(user, sid, "https://seafile.example/f/abc/")
    assert res["status"] == "added" and res["name"] == "syllabus.pdf" and res["pages"] == 3
    assert library.import_url(user, sid, "https://seafile.example/f/abc/")["status"] == "exists"
