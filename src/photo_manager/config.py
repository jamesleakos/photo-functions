from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _path_env(name: str, default: Path) -> Path:
    return Path(os.environ.get(name, str(default))).expanduser().resolve()


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_path: Path
    thumbnail_dir: Path
    upload_dir: Path
    iphone_export_path: Path
    storage_backend: str
    local_storage_path: Path
    s3_bucket: str | None
    s3_region: str
    s3_endpoint_url: str | None
    s3_prefix: str
    s3_storage_class: str | None
    auth_username: str | None
    auth_password: str | None
    variant_suggest_threshold: float
    variant_confirm_threshold: float
    cloud_catalog_sync: bool = False
    hosted_gallery: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = _path_env("PHOTO_DATA_DIR", Path.home() / ".photo-manager")
        settings = cls(
            data_dir=data_dir,
            database_path=_path_env("PHOTO_DATABASE_PATH", data_dir / "catalog.db"),
            thumbnail_dir=_path_env("PHOTO_THUMBNAIL_DIR", data_dir / "thumbnails"),
            upload_dir=_path_env("PHOTO_UPLOAD_DIR", data_dir / "uploads"),
            iphone_export_path=_path_env("PHOTO_IPHONE_EXPORT_PATH", data_dir / "iphone-favorites"),
            storage_backend=os.environ.get("PHOTO_STORAGE_BACKEND", "local").lower(),
            local_storage_path=_path_env("PHOTO_LOCAL_STORAGE_PATH", data_dir / "archive"),
            s3_bucket=os.environ.get("PHOTO_S3_BUCKET") or None,
            s3_region=os.environ.get("PHOTO_S3_REGION", "us-east-1"),
            s3_endpoint_url=os.environ.get("PHOTO_S3_ENDPOINT_URL") or None,
            s3_prefix=os.environ.get("PHOTO_S3_PREFIX", "photo-manager").strip("/"),
            s3_storage_class=os.environ.get("PHOTO_S3_STORAGE_CLASS") or None,
            auth_username=os.environ.get("PHOTO_AUTH_USERNAME") or None,
            auth_password=os.environ.get("PHOTO_AUTH_PASSWORD") or None,
            variant_suggest_threshold=float(
                os.environ.get("PHOTO_VARIANT_SUGGEST_THRESHOLD", "0.72")
            ),
            variant_confirm_threshold=float(
                os.environ.get("PHOTO_VARIANT_CONFIRM_THRESHOLD", "0.90")
            ),
            cloud_catalog_sync=_bool_env("PHOTO_CLOUD_CATALOG_SYNC"),
            hosted_gallery=_bool_env("PHOTO_HOSTED_GALLERY"),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.storage_backend not in {"local", "s3"}:
            raise ValueError("PHOTO_STORAGE_BACKEND must be 'local' or 's3'")
        if self.storage_backend == "s3" and not self.s3_bucket:
            raise ValueError("PHOTO_S3_BUCKET is required for the s3 backend")
        if bool(self.auth_username) != bool(self.auth_password):
            raise ValueError("Set both PHOTO_AUTH_USERNAME and PHOTO_AUTH_PASSWORD")
        if self.cloud_catalog_sync and self.storage_backend != "s3":
            raise ValueError("PHOTO_CLOUD_CATALOG_SYNC requires PHOTO_STORAGE_BACKEND=s3")
        if not 0 <= self.variant_suggest_threshold <= self.variant_confirm_threshold <= 1:
            raise ValueError("Variant thresholds must satisfy 0 <= suggest <= confirm <= 1")

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.database_path.parent,
            self.thumbnail_dir,
            self.upload_dir,
            self.iphone_export_path,
        ):
            path.mkdir(parents=True, exist_ok=True)
        if self.storage_backend == "local":
            self.local_storage_path.mkdir(parents=True, exist_ok=True)
