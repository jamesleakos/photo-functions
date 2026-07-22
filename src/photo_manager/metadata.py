from __future__ import annotations

import io
import json
import mimetypes
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import imagehash
from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover - HEIF is an optional runtime capability
    pass


IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".heic",
    ".heif",
    ".png",
    ".tif",
    ".tiff",
    ".dng",
    ".arw",
    ".cr2",
    ".cr3",
    ".nef",
    ".orf",
    ".rw2",
    ".raf",
}
VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


@dataclass
class PhotoMetadata:
    media_type: str
    width: int | None = None
    height: int | None = None
    captured_at: str | None = None
    make: str | None = None
    model: str | None = None
    lens_model: str | None = None
    perceptual_hash: str | None = None
    raw: dict[str, Any] | None = None

    def as_json(self) -> str:
        value = asdict(self)
        value.pop("perceptual_hash", None)
        value.pop("media_type", None)
        return json.dumps(value, default=str, sort_keys=True)


def _parse_exif_date(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    for candidate in (text[:19], text):
        for pattern in (
            "%Y:%m:%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                return datetime.strptime(candidate, pattern).isoformat(timespec="seconds")
            except ValueError:
                continue
    return None


def _positive_int(value: Any) -> int | None:
    try:
        number = int(float(value))
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


class MetadataExtractor:
    EXIF_FIELDS = (
        "-DateTimeOriginal",
        "-CreateDate",
        "-MediaCreateDate",
        "-ContentCreateDate",
        "-ImageWidth",
        "-ImageHeight",
        "-Make",
        "-Model",
        "-LensModel",
        "-MIMEType",
        "-Orientation",
        "-Duration",
        "-Rating",
        "-Subject",
        "-Keywords",
    )

    def __init__(self) -> None:
        self.exiftool = shutil.which("exiftool")

    def extract(self, path: Path) -> PhotoMetadata:
        raw = self._exiftool_metadata(path) if self.exiftool else {}
        guessed_type = mimetypes.guess_type(path.name)[0]
        media_type = raw.get("MIMEType")
        if not media_type or media_type == "application/octet-stream":
            media_type = guessed_type
        if path.suffix.lower() in VIDEO_EXTENSIONS and not str(media_type).startswith("video/"):
            media_type = guessed_type or "video/unknown"
        if path.suffix.lower() in IMAGE_EXTENSIONS and not str(media_type).startswith("image/"):
            media_type = guessed_type or "image/unknown"
        if not media_type:
            if path.suffix.lower() in IMAGE_EXTENSIONS:
                media_type = "image/unknown"
            else:
                media_type = "video/unknown"

        captured_at = None
        for field in ("DateTimeOriginal", "ContentCreateDate", "MediaCreateDate", "CreateDate"):
            captured_at = _parse_exif_date(raw.get(field))
            if captured_at:
                break

        width = _positive_int(raw.get("ImageWidth"))
        height = _positive_int(raw.get("ImageHeight"))
        phash = None
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            pillow = self._pillow_metadata(path)
            width = width or pillow.get("width")
            height = height or pillow.get("height")
            captured_at = captured_at or pillow.get("captured_at")
            phash = pillow.get("perceptual_hash") or self._raw_preview_hash(path)

        return PhotoMetadata(
            media_type=media_type,
            width=width,
            height=height,
            captured_at=captured_at,
            make=raw.get("Make"),
            model=raw.get("Model"),
            lens_model=raw.get("LensModel"),
            perceptual_hash=phash,
            raw=raw,
        )

    def _exiftool_metadata(self, path: Path) -> dict[str, Any]:
        result = subprocess.run(
            [self.exiftool, "-json", *self.EXIF_FIELDS, str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return {}
        try:
            rows = json.loads(result.stdout)
            return rows[0] if rows else {}
        except (json.JSONDecodeError, IndexError):
            return {}

    def _pillow_metadata(self, path: Path) -> dict[str, Any]:
        try:
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image)
                width, height = image.size
                exif = image.getexif()
                captured_at = _parse_exif_date(exif.get(36867) or exif.get(306))
                image.thumbnail((1024, 1024))
                return {
                    "width": width,
                    "height": height,
                    "captured_at": captured_at,
                    "perceptual_hash": str(imagehash.phash(image.convert("RGB"))),
                }
        except Exception:
            return {}

    def _raw_preview_hash(self, path: Path) -> str | None:
        if not self.exiftool:
            return None
        for tag in ("-PreviewImage", "-JpgFromRaw", "-ThumbnailImage"):
            result = subprocess.run(
                [self.exiftool, "-b", tag, str(path)], capture_output=True, check=False
            )
            if result.returncode != 0 or not result.stdout:
                continue
            try:
                with Image.open(io.BytesIO(result.stdout)) as image:
                    image.thumbnail((1024, 1024))
                    return str(imagehash.phash(image.convert("RGB")))
            except Exception:
                continue
        return None
