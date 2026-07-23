from io import BytesIO

from PIL import Image

from photo_manager import thumbnails


def test_thumbnail_prefers_embedded_preview(monkeypatch, tmp_path):
    source = tmp_path / "large-original.jpg"
    destination = tmp_path / "thumbnail.jpg"
    Image.new("RGB", (1200, 800), "blue").save(source)
    preview_buffer = BytesIO()
    Image.new("RGB", (400, 300), "red").save(preview_buffer, "JPEG")
    monkeypatch.setattr(thumbnails, "_extract_preview", lambda _: preview_buffer.getvalue())

    thumbnails.create_thumbnail(source, destination)

    with Image.open(destination) as result:
        red, green, blue = result.resize((1, 1)).getpixel((0, 0))
    assert red > 200
    assert green < 40
    assert blue < 40
