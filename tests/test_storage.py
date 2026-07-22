from pathlib import Path

from photo_manager.catalog import Catalog
from photo_manager.database import Database
from photo_manager.metadata import PhotoMetadata
from photo_manager.storage import (
    BackupService,
    LocalStorage,
    catalog_snapshot_key,
    restore_catalog_snapshot,
    upload_catalog_snapshot,
)


class Extractor:
    def extract(self, path: Path) -> PhotoMetadata:
        return PhotoMetadata(media_type="image/jpeg", width=100, height=100)


def test_local_backup_is_content_addressed_and_idempotent(tmp_path, settings):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"original bytes")
    catalog = Catalog(Database(settings.database_path), settings, Extractor())
    catalog.ingest_file(photo, "camera")
    service = BackupService(catalog, LocalStorage(settings.local_storage_path), settings)

    first = service.run()
    second = service.run()

    assert first.uploaded == 1
    assert first.catalog_uploaded is True
    assert second.eligible == 0
    stored = list(settings.local_storage_path.rglob("*.jpg"))
    assert len(stored) == 1
    assert stored[0].read_bytes() == photo.read_bytes()
    assert (settings.local_storage_path / "photo-manager/metadata/catalog-latest.db").exists()

    assert catalog.release_tree(tmp_path) == 1
    assert catalog.available_path(1) is None


def test_catalog_snapshot_can_restore_atomically(tmp_path, settings):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"snapshot bytes")
    catalog = Catalog(Database(settings.database_path), settings, Extractor())
    catalog.ingest_file(photo, "camera")
    storage = LocalStorage(settings.local_storage_path)
    upload_catalog_snapshot(catalog, storage, settings)
    settings.database_path.unlink()

    restored = restore_catalog_snapshot(storage, settings)

    assert restored == settings.database_path
    assert Catalog(Database(restored), settings, Extractor()).stats()["photos"] == 1
    assert (settings.local_storage_path / catalog_snapshot_key(settings)).exists()
