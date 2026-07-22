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
    try:
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail(size)
            image.convert("RGB").save(destination, "JPEG", quality=84, optimize=True)
            return destination
    except Exception:
        preview = _extract_preview(source)
        if preview is None:
            raise
        with Image.open(io.BytesIO(preview)) as image:
            image.thumbnail(size)
            image.convert("RGB").save(destination, "JPEG", quality=84, optimize=True)
        return destination


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
