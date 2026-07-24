from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover
    pass


def create_thumbnail(source: Path, destination: Path, size: tuple[int, int] = (720, 720)) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_mtime >= source.stat().st_mtime:
        return destination
    preview = _extract_preview(source)
    if preview is not None:
        with Image.open(io.BytesIO(preview)) as image:
            _save_thumbnail(image, destination, size)
        return destination
    try:
        with Image.open(source) as image:
            _save_thumbnail(image, destination, size)
            return destination
    except Exception:
        preview = _extract_preview(source)
        if preview is None:
            raise
        with Image.open(io.BytesIO(preview)) as image:
            _save_thumbnail(image, destination, size)
        return destination


def create_preview(
    source: Path,
    destination: Path,
    size: tuple[int, int] = (3200, 3200),
) -> Path:
    """Create a high-quality, browser-compatible preview from an original."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_mtime >= source.stat().st_mtime:
        return destination
    try:
        with Image.open(source) as image:
            _save_thumbnail(image, destination, size, quality=91)
            return destination
    except Exception:
        preview = _extract_preview(source)
        if preview is None:
            raise
        with Image.open(io.BytesIO(preview)) as image:
            _save_thumbnail(image, destination, size, quality=91)
        return destination


def _save_thumbnail(
    image: Image.Image,
    destination: Path,
    size: tuple[int, int],
    *,
    quality: int = 84,
) -> None:
    image.draft("RGB", size)
    display_image = ImageOps.exif_transpose(image)
    try:
        display_image.thumbnail(size, Image.Resampling.LANCZOS)
        converted = display_image.convert("RGB")
        try:
            converted.save(
                destination,
                "JPEG",
                quality=quality,
                optimize=True,
                progressive=True,
            )
        finally:
            converted.close()
    finally:
        display_image.close()


def _extract_preview(source: Path) -> bytes | None:
    exiftool = shutil.which("exiftool")
    if not exiftool:
        return None
    for tag in ("-PreviewImage", "-JpgFromRaw", "-ThumbnailImage"):
        result = subprocess.run(
            [exiftool, "-b", tag, str(source)], capture_output=True, check=False
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
    return None
