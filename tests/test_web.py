from dataclasses import replace

from fastapi.testclient import TestClient
from PIL import Image

from photo_manager.web.app import create_app


def test_gallery_magazine_and_thumbnail_endpoints(tmp_path, settings):
    image_path = tmp_path / "editorial.jpg"
    Image.new("RGB", (640, 480), "#b8472d").save(image_path)
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

    update = client.put(
        f"/api/photos/{catalog_id}/magazine",
        json={"issue": "Winter 2026", "status": "candidate", "notes": "Opening spread"},
    )
    assert update.status_code == 200
    selected = client.get("/api/photos", params={"issue": "Winter 2026"}).json()
    assert selected[0]["magazine_status"] == "candidate"


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


def test_auth_protects_everything_except_health(settings):
    protected = replace(
        settings, auth_username="owner", auth_password="correct horse battery staple"
    )
    client = TestClient(create_app(protected))

    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 401
    assert client.get("/", auth=("owner", "correct horse battery staple")).status_code == 200


def test_hosted_gallery_disables_imports_and_backup(settings):
    hosted = replace(settings, hosted_gallery=True)
    client = TestClient(create_app(hosted))

    assert client.get("/api/config").json() == {"hosted_gallery": True}
    assert client.post("/api/imports/scan", json={"path": "/tmp"}).status_code == 403
    assert client.post("/api/backups/run").status_code == 403
