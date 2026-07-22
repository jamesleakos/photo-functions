from pathlib import Path

import pytest

from photo_manager.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    value = Settings(
        data_dir=data,
        database_path=data / "catalog.db",
        thumbnail_dir=data / "thumbnails",
        upload_dir=data / "uploads",
        iphone_export_path=data / "iphone",
        storage_backend="local",
        local_storage_path=data / "archive",
        s3_bucket=None,
        s3_region="us-east-1",
        s3_endpoint_url=None,
        s3_prefix="photo-manager",
        s3_storage_class=None,
        auth_username=None,
        auth_password=None,
        variant_suggest_threshold=0.72,
        variant_confirm_threshold=0.90,
    )
    value.ensure_directories()
    return value
