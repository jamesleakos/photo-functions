from dataclasses import replace
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from photo_manager.web.app import create_app


def test_gallery_magazine_and_thumbnail_endpoints(tmp_path, settings):
    image_path = tmp_path / "editorial.jpg"
    Image.new("RGB", (1600, 1200), "#b8472d").save(image_path)
    app = create_app(settings)
    photo_id = app.state.catalog.ingest_file(image_path, "camera")
    assert photo_id == "added"
    client = TestClient(app)

    photos = client.get("/api/photos")
    assert photos.status_code == 200
    catalog_id = photos.json()[0]["id"]
    thumbnail = client.get(f"/api/photos/{catalog_id}/thumbnail")
    assert thumbnail.status_code == 200
    assert thumbnail.headers["content-type"].startswith("image/jpeg")
    preview = client.get(f"/api/photos/{catalog_id}/preview")
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("image/jpeg")
    with Image.open(BytesIO(preview.content)) as preview_image:
        assert preview_image.size == (1600, 1200)

    update = client.put(
        f"/api/photos/{catalog_id}/magazine",
        json={"issue": "Winter 2026", "status": "candidate", "notes": "Opening spread"},
    )
    assert update.status_code == 200
    selected = client.get("/api/photos", params={"issue": "Winter 2026"}).json()
    assert selected[0]["magazine_status"] == "candidate"

    flagged = client.put(
        f"/api/photos/{catalog_id}/flag",
        json={"flag": "one_of"},
    )
    assert flagged.status_code == 200
    assert flagged.json()["flag"] == "one_of"
    filtered = client.get("/api/photos", params={"flag": ["flagship", "one_of"]})
    assert [item["id"] for item in filtered.json()] == [catalog_id]
    assert filtered.json()[0]["editorial_flag"] == "one_of"
    assert client.put(f"/api/photos/{catalog_id}/flag", json={"flag": None}).status_code == 200
    assert client.get("/api/photos", params={"flag": "unflagged"}).json()[0]["id"] == catalog_id
    excluded = client.put(
        f"/api/photos/{catalog_id}/flag",
        json={"flag": "not_included"},
    )
    assert excluded.status_code == 200
    assert client.get("/api/photos", params={"flag": "not_included"}).json()[0][
        "editorial_flag"
    ] == "not_included"
    assert client.put(f"/api/photos/{catalog_id}/flag", json={"flag": None}).status_code == 200

    index = client.get("/")
    assert "Flagship" in index.text
    assert "Favourited" in index.text
    assert "Not included" in index.text
    assert 'name="media-filter"' in index.text
    assert 'id="date-sort"' in index.text
    assert '<option value="asc" selected>Oldest first</option>' in index.text
    assert 'id="photo-viewer"' in index.text
    assert 'id="viewer-flag-controls"' in index.text
    assert 'id="viewer-prev"' in index.text
    assert 'id="viewer-next"' in index.text
    assert 'id="viewer-stage"' in index.text
    assert "Working magazine issue" not in index.text


def test_photo_filter_rejects_reversed_date_range(settings):
    client = TestClient(create_app(settings))

    response = client.get(
        "/api/photos",
        params={"date_from": "2025-01-01", "date_to": "2024-01-01"},
    )

    assert response.status_code == 400


def test_photo_endpoint_sorts_by_capture_date(settings, tmp_path):
    older_path = tmp_path / "older.jpg"
    newer_path = tmp_path / "newer.jpg"
    Image.new("RGB", (640, 480), "#b8472d").save(older_path)
    Image.new("RGB", (640, 480), "#465d3c").save(newer_path)
    app = create_app(settings)
    app.state.catalog.ingest_file(older_path, "camera")
    app.state.catalog.ingest_file(newer_path, "camera")
    with app.state.catalog.database.connect() as connection:
        connection.execute(
            "UPDATE photos SET captured_at = ? WHERE filename = ?",
            ("2024-03-01T12:00:00", older_path.name),
        )
        connection.execute(
            "UPDATE photos SET captured_at = ? WHERE filename = ?",
            ("2025-09-10T12:00:00", newer_path.name),
        )
    client = TestClient(app)

    ascending = client.get("/api/photos", params={"date_order": "asc"})
    descending = client.get("/api/photos", params={"date_order": "desc"})

    assert [item["filename"] for item in ascending.json()] == [
        older_path.name,
        newer_path.name,
    ]
    assert [item["filename"] for item in descending.json()] == [
        newer_path.name,
        older_path.name,
    ]
    assert client.get("/api/photos", params={"date_order": "sideways"}).status_code == 422


def test_capture_dates_can_be_backfilled_in_one_request(tmp_path, settings):
    image_path = tmp_path / "missing-date.jpg"
    Image.new("RGB", (640, 480), "#b8472d").save(image_path)
    app = create_app(settings)
    app.state.catalog.ingest_file(image_path, "iphone-favorite", favorite=True)
    client = TestClient(app)
    photo_id = client.get("/api/photos").json()[0]["id"]

    response = client.put(
        "/api/photos/capture-dates",
        json={
            "items": [
                {
                    "photo_id": photo_id,
                    "captured_at": "2024-05-06T14:30:00",
                }
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["updated"] == 1
    assert client.get("/api/photos").json()[0]["captured_at"] == "2024-05-06T14:30:00"
    missing = client.put(
        "/api/photos/capture-dates",
        json={
            "items": [
                {
                    "photo_id": 9999,
                    "captured_at": "2024-05-06T14:30:00",
                }
            ]
        },
    )
    assert missing.status_code == 404


def test_video_gets_placeholder_thumbnail(tmp_path, settings):
    video_path = tmp_path / "clip.mov"
    video_path.write_bytes(b"not a real movie, but valid catalog test input")
    app = create_app(settings)
    app.state.catalog.ingest_file(video_path, "iphone-favorite", favorite=True)
    client = TestClient(app)
    photo_id = client.get("/api/photos").json()[0]["id"]

    response = client.get(f"/api/photos/{photo_id}/thumbnail")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "clip.mov" in response.text
    assert client.get(f"/api/photos/{photo_id}/preview").status_code == 415
    assert client.get("/api/photos", params={"media": "photo"}).json() == []
    videos = client.get("/api/photos", params={"media": "video"})
    assert [item["id"] for item in videos.json()] == [photo_id]
    assert client.get("/api/photos", params={"media": "audio"}).status_code == 422


def test_auth_protects_everything_except_health(settings):
    protected = replace(
        settings, auth_username="owner", auth_password="correct horse battery staple"
    )
    client = TestClient(create_app(protected))

    assert client.get("/health").status_code == 200
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.get("/login").status_code == 200
    assert client.get("/api/photos").status_code == 401
    assert client.get("/", auth=("owner", "correct horse battery staple")).status_code == 200


def test_login_uses_a_secure_session_instead_of_browser_dialog(settings):
    protected = replace(
        settings, auth_username="owner", auth_password="correct horse battery staple"
    )
    client = TestClient(create_app(protected))

    failed = client.post(
        "/login",
        data={"username": "owner", "password": "wrong"},
        follow_redirects=False,
    )
    assert failed.status_code == 303
    assert failed.headers["location"] == "/login?error=1"

    signed_in = client.post(
        "/login",
        data={"username": "owner", "password": "correct horse battery staple"},
        follow_redirects=False,
    )
    assert signed_in.status_code == 303
    assert signed_in.headers["location"] == "/"
    assert "photo_manager_session=" in signed_in.headers["set-cookie"]
    assert "HttpOnly" in signed_in.headers["set-cookie"]
    assert client.get("/").status_code == 200

    signed_out = client.post("/logout", follow_redirects=False)
    assert signed_out.status_code == 303
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"


def test_hosted_login_cookie_is_always_secure(settings):
    hosted = replace(
        settings,
        hosted_gallery=True,
        auth_username="owner",
        auth_password="correct horse battery staple",
    )
    client = TestClient(create_app(hosted))

    response = client.post(
        "/login",
        data={"username": "owner", "password": "correct horse battery staple"},
        follow_redirects=False,
    )

    assert "Secure" in response.headers["set-cookie"]


def test_hosted_gallery_disables_imports_and_backup(settings):
    hosted = replace(settings, hosted_gallery=True)
    client = TestClient(create_app(hosted))

    assert client.get("/api/config").json() == {"hosted_gallery": True}
    assert client.post("/api/imports/scan", json={"path": "/tmp"}).status_code == 403
    assert client.post("/api/backups/run").status_code == 403
