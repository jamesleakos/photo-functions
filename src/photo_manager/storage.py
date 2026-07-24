from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from botocore.exceptions import ClientError

from .catalog import Catalog, sha256_file
from .config import Settings
from .derivatives import DerivativeDispatcher


@dataclass
class StoredObject:
    key: str
    etag: str | None = None


class StorageBackend(Protocol):
    name: str

    def put(self, source: Path, key: str, sha256: str) -> StoredObject: ...

    def download(self, key: str, destination: Path) -> None: ...

    def presigned_url(self, key: str, expires_seconds: int = 900) -> str | None: ...

    def exists(self, key: str) -> bool: ...


class LocalStorage:
    name = "local"

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, source: Path, key: str, sha256: str) -> StoredObject:
        destination = self.root / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and sha256_file(destination) == sha256:
            return StoredObject(key=key, etag=sha256)
        temporary = destination.with_suffix(destination.suffix + ".uploading")
        shutil.copy2(source, temporary)
        if sha256_file(temporary) != sha256:
            temporary.unlink(missing_ok=True)
            raise IOError(f"Checksum verification failed for {source}")
        temporary.replace(destination)
        return StoredObject(key=key, etag=sha256)

    def download(self, key: str, destination: Path) -> None:
        source = self.root / key
        if not source.exists():
            raise FileNotFoundError(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    def presigned_url(self, key: str, expires_seconds: int = 900) -> str | None:
        return None

    def exists(self, key: str) -> bool:
        return (self.root / key).exists()


class S3Storage:
    name = "s3"

    def __init__(self, settings: Settings):
        import boto3

        self.bucket = settings.s3_bucket
        self.storage_class = settings.s3_storage_class
        self.client = boto3.client(
            "s3", region_name=settings.s3_region, endpoint_url=settings.s3_endpoint_url
        )

    def put(self, source: Path, key: str, sha256: str) -> StoredObject:
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=key)
            if head.get("Metadata", {}).get("sha256") == sha256:
                return StoredObject(key=key, etag=head.get("ETag", "").strip('"') or None)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code not in {"404", "NoSuchKey", "NotFound"}:
                raise

        extra_args: dict[str, object] = {"Metadata": {"sha256": sha256}}
        if source.suffix.lower() in {".jpg", ".jpeg"}:
            extra_args.update(
                {
                    "ContentType": "image/jpeg",
                    "CacheControl": "private, max-age=31536000, immutable",
                }
            )
        if self.storage_class:
            extra_args["StorageClass"] = self.storage_class
        self.client.upload_file(str(source), self.bucket, key, ExtraArgs=extra_args)
        head = self.client.head_object(Bucket=self.bucket, Key=key)
        return StoredObject(key=key, etag=head.get("ETag", "").strip('"') or None)

    def download(self, key: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(destination))

    def presigned_url(self, key: str, expires_seconds: int = 900) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_seconds,
        )

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise


def build_storage(settings: Settings) -> StorageBackend:
    if settings.storage_backend == "local":
        return LocalStorage(settings.local_storage_path)
    return S3Storage(settings)


def object_key(settings: Settings, photo: dict) -> str:
    prefix = f"{settings.s3_prefix}/" if settings.s3_prefix else ""
    digest = photo["sha256"]
    return f"{prefix}originals/{digest[:2]}/{digest}{photo['extension']}"


def catalog_snapshot_key(settings: Settings) -> str:
    prefix = f"{settings.s3_prefix}/" if settings.s3_prefix else ""
    return f"{prefix}metadata/catalog-latest.db"


def thumbnail_key(settings: Settings, photo: dict) -> str:
    prefix = f"{settings.s3_prefix}/" if settings.s3_prefix else ""
    digest = photo["sha256"]
    return f"{prefix}thumbnails/{digest[:2]}/{digest}.jpg"


def preview_key(settings: Settings, photo: dict) -> str:
    prefix = f"{settings.s3_prefix}/" if settings.s3_prefix else ""
    digest = photo["sha256"]
    return f"{prefix}previews/{digest[:2]}/{digest}.jpg"


def upload_catalog_snapshot(
    catalog: Catalog, storage: StorageBackend, settings: Settings
) -> StoredObject:
    snapshot = catalog.database.snapshot(settings.data_dir / "catalog-snapshot.db")
    return storage.put(snapshot, catalog_snapshot_key(settings), sha256_file(snapshot))


def restore_catalog_snapshot(storage: StorageBackend, settings: Settings) -> Path:
    """Atomically restore the authoritative catalog before SQLite opens it."""
    destination = settings.database_path
    temporary = destination.with_suffix(destination.suffix + ".restoring")
    temporary.unlink(missing_ok=True)
    try:
        storage.download(catalog_snapshot_key(settings), temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


@dataclass
class BackupReport:
    eligible: int = 0
    uploaded: int = 0
    unavailable: int = 0
    failed: int = 0
    catalog_uploaded: bool = False
    errors: list[str] | None = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


class BackupService:
    def __init__(self, catalog: Catalog, storage: StorageBackend, settings: Settings):
        self.catalog = catalog
        self.storage = storage
        self.settings = settings
        self.derivatives = DerivativeDispatcher(settings)

    def run(self, workers: int = 4) -> BackupReport:
        candidates = self.catalog.backup_candidates()
        report = BackupReport(eligible=len(candidates))

        def upload_one(photo: dict) -> tuple[str, str | None]:
            path = self.catalog.available_path(photo["id"])
            key = object_key(self.settings, photo)
            if not path:
                self.catalog.record_backup(photo["id"], key, "missing", error="No local copy")
                return "unavailable", None
            try:
                stored = self.storage.put(path, key, photo["sha256"])
                self.catalog.record_backup(photo["id"], stored.key, "uploaded", stored.etag)
                if photo["media_type"].startswith("image/") and self.derivatives.enabled:
                    queued_photo = dict(photo)
                    queued_photo["object_key"] = stored.key
                    try:
                        self.derivatives.enqueue(queued_photo)
                    except Exception:
                        # The original is safely backed up. A hosted gallery request or a
                        # later backfill can retry this optional derivative job.
                        pass
                return "uploaded", None
            except Exception as error:
                self.catalog.record_backup(photo["id"], key, "failed", error=str(error))
                return "failed", f"{path}: {error}"

        with ThreadPoolExecutor(max_workers=max(1, min(workers, 16))) as executor:
            futures = [executor.submit(upload_one, photo) for photo in candidates]
            for future in as_completed(futures):
                status, error = future.result()
                if status == "uploaded":
                    report.uploaded += 1
                elif status == "unavailable":
                    report.unavailable += 1
                else:
                    report.failed += 1
                    if error:
                        report.errors.append(error)
        try:
            upload_catalog_snapshot(self.catalog, self.storage, self.settings)
            report.catalog_uploaded = True
        except Exception as error:
            report.failed += 1
            report.errors.append(f"Catalog snapshot: {error}")
        return report
