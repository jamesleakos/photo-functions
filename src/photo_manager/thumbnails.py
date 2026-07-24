from __future__ import annotations

import ctypes
import gc
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


RAW_EXTENSIONS = frozenset({".arw", ".cr2", ".cr3", ".dng", ".nef", ".orf", ".raf", ".rw2"})


def create_thumbnail(source: Path, destination: Path, size: tuple[int, int] = (720, 720)) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_mtime >= source.stat().st_mtime:
        return destination
    preview = _extract_preview(source)
    if preview is not None:
        with Image.open(io.BytesIO(preview)) as image:
            _save_thumbnail(image, destination, size)
        release_image_memory()
        return destination
    try:
        with Image.open(source) as image:
            _save_thumbnail(image, destination, size)
            release_image_memory()
            return destination
    except Exception:
        preview = _extract_preview(source)
        if preview is None:
            raise
        with Image.open(io.BytesIO(preview)) as image:
            _save_thumbnail(image, destination, size)
        release_image_memory()
        return destination


def create_preview(
    source: Path,
    destination: Path,
    size: tuple[int, int] = (2560, 2560),
) -> Path:
    """Create a high-quality, browser-compatible preview from an original."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_mtime >= source.stat().st_mtime:
        return destination
    if source.suffix.lower() in RAW_EXTENSIONS:
        embedded = _extract_preview(source)
        if embedded is not None:
            with Image.open(io.BytesIO(embedded)) as image:
                _save_thumbnail(image, destination, size, quality=91)
            release_image_memory()
            return destination
    try:
        with Image.open(source) as image:
            _save_thumbnail(image, destination, size, quality=91)
            release_image_memory()
            return destination
    except Exception:
        preview = _extract_preview(source)
        if preview is None:
            raise
        with Image.open(io.BytesIO(preview)) as image:
            _save_thumbnail(image, destination, size, quality=91)
        release_image_memory()
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


def release_image_memory() -> None:
    """Return large native image buffers to the OS when the allocator supports it."""
    gc.collect()
    try:
        libc = ctypes.CDLL("libc.so.6")
        trim = libc.malloc_trim
        trim.argtypes = [ctypes.c_size_t]
        trim.restype = ctypes.c_int
        trim(0)
    except (AttributeError, OSError):
        pass
