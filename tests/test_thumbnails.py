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


def test_high_quality_preview_prefers_original_and_preserves_more_detail(monkeypatch, tmp_path):
    source = tmp_path / "large-original.jpg"
    destination = tmp_path / "preview.jpg"
    Image.new("RGB", (4000, 3000), "blue").save(source)
    preview_buffer = BytesIO()
    Image.new("RGB", (400, 300), "red").save(preview_buffer, "JPEG")
    monkeypatch.setattr(thumbnails, "_extract_preview", lambda _: preview_buffer.getvalue())

    thumbnails.create_preview(source, destination)

    with Image.open(destination) as result:
        assert result.size == (2560, 1920)
        red, green, blue = result.resize((1, 1)).getpixel((0, 0))
    assert red < 40
    assert green < 40
    assert blue > 200


def test_raw_preview_prefers_memory_efficient_embedded_jpeg(monkeypatch, tmp_path):
    source = tmp_path / "camera.arw"
    source.write_bytes(b"raw placeholder")
    destination = tmp_path / "preview.jpg"
    preview_buffer = BytesIO()
    Image.new("RGB", (3000, 2000), "red").save(preview_buffer, "JPEG")
    monkeypatch.setattr(thumbnails, "_extract_preview", lambda _: preview_buffer.getvalue())

    thumbnails.create_preview(source, destination)

    with Image.open(destination) as result:
        assert result.size == (2560, 1707)
        red, green, blue = result.resize((1, 1)).getpixel((0, 0))
    assert red > 200
    assert green < 40
    assert blue < 40
