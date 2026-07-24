from __future__ import annotations

import io
import json
import os
import shutil
import traceback
import uuid
from pathlib import Path

import boto3
import rawpy
from botocore.exceptions import ClientError
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener


register_heif_opener()
Image.MAX_IMAGE_PIXELS = None

ARCHIVE_BUCKET = os.environ["PHOTO_ARCHIVE_BUCKET"]
ARCHIVE_PREFIX = os.environ.get("PHOTO_ARCHIVE_PREFIX", "photo-manager").strip("/")
RAW_EXTENSIONS = frozenset({".arw", ".cr2", ".cr3", ".dng", ".nef", ".orf", ".raf", ".rw2"})
PREVIEW_SIZE = (2560, 2560)
THUMBNAIL_SIZE = (720, 720)
S3 = boto3.client("s3")


def derivative_key(kind: str, digest: str) -> str:
    prefix = f"{ARCHIVE_PREFIX}/" if ARCHIVE_PREFIX else ""
    return f"{prefix}{kind}/{digest[:2]}/{digest}.jpg"


def object_exists(bucket: str, key: str) -> bool:
    try:
        S3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as error:
        code = str(error.response.get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise


def open_source(source: Path, extension: str):
    if extension.lower() not in RAW_EXTENSIONS:
        return Image.open(source)
    with rawpy.imread(str(source)) as raw:
        embedded = raw.extract_thumb()
    if embedded.format == rawpy.ThumbFormat.JPEG:
        return Image.open(io.BytesIO(embedded.data))
    return Image.fromarray(embedded.data)


def save_jpeg(image: Image.Image, destination: Path, size: tuple[int, int], quality: int) -> None:
    resized = image.copy()
    try:
        resized.thumbnail(size, Image.Resampling.LANCZOS)
        resized.save(destination, "JPEG", quality=quality, progressive=True)
    finally:
        resized.close()


def build_derivatives(source: Path, extension: str, preview: Path, thumbnail: Path) -> None:
    with open_source(source, extension) as image:
        image.draft("RGB", PREVIEW_SIZE)
        display = ImageOps.exif_transpose(image)
        try:
            display.thumbnail(PREVIEW_SIZE, Image.Resampling.LANCZOS)
            converted = display.convert("RGB")
            try:
                save_jpeg(converted, preview, PREVIEW_SIZE, 91)
                save_jpeg(converted, thumbnail, THUMBNAIL_SIZE, 84)
            finally:
                converted.close()
        finally:
            display.close()


def validate_job(job: dict) -> tuple[str, str, str]:
    bucket = str(job.get("bucket", ""))
    original_key = str(job.get("original_key", ""))
    digest = str(job.get("sha256", "")).lower()
    prefix = f"{ARCHIVE_PREFIX}/originals/"
    if bucket != ARCHIVE_BUCKET:
        raise ValueError("Derivative job targeted an unexpected bucket")
    if not original_key.startswith(prefix):
        raise ValueError("Derivative job targeted an unexpected object prefix")
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("Derivative job has an invalid SHA-256 digest")
    return bucket, original_key, digest


def process_job(job: dict) -> None:
    bucket, original_key, digest = validate_job(job)
    preview_key = derivative_key("previews", digest)
    thumbnail_key = derivative_key("thumbnails", digest)
    if object_exists(bucket, preview_key) and object_exists(bucket, thumbnail_key):
        return

    work = Path("/tmp") / f"photo-derivative-{digest[:12]}-{uuid.uuid4().hex}"
    work.mkdir(parents=True)
    source = work / f"original{job.get('extension', Path(original_key).suffix)}"
    preview = work / "preview.jpg"
    thumbnail = work / "thumbnail.jpg"
    try:
        S3.download_file(bucket, original_key, str(source))
        build_derivatives(source, source.suffix, preview, thumbnail)
        upload_args = {
            "ContentType": "image/jpeg",
            "CacheControl": "private, max-age=31536000, immutable",
            "Metadata": {"source-sha256": digest},
        }
        S3.upload_file(str(preview), bucket, preview_key, ExtraArgs=upload_args)
        S3.upload_file(str(thumbnail), bucket, thumbnail_key, ExtraArgs=upload_args)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def lambda_handler(event: dict, _context) -> dict[str, list[dict[str, str]]]:
    failures: list[dict[str, str]] = []
    for record in event.get("Records", []):
        try:
            process_job(json.loads(record["body"]))
        except Exception:
            traceback.print_exc()
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
