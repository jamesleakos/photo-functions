from datetime import date
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from photo_manager.catalog import Catalog
from photo_manager.database import Database
from photo_manager.iphone import (
    GIB,
    DiskReserveBreach,
    FavoriteAsset,
    _exported_uuids,
    _next_safe_batch,
    sync_favorites,
)


def test_dry_run_does_not_open_photos_or_create_export(monkeypatch, settings, tmp_path: Path):
    executable = tmp_path / "osxphotos"
    executable.touch()
    destination = tmp_path / "favorites"
    catalog = Catalog(Database(settings.database_path), settings)

    monkeypatch.setattr("photo_manager.iphone.shutil.which", lambda _: str(executable))

    def unexpected_run(*args, **kwargs):
        raise AssertionError("dry run must not invoke osxphotos")

    monkeypatch.setattr("photo_manager.iphone.subprocess.run", unexpected_run)

    report = sync_favorites(catalog, destination, dry_run=True)

    assert report.to_dict()["scanned"] == 0
    assert not destination.exists()


def test_sync_filters_a_month_and_uses_photokit(monkeypatch, settings, tmp_path: Path):
    executable = tmp_path / "osxphotos"
    executable.touch()
    destination = tmp_path / "favorites"
    catalog = Catalog(Database(settings.database_path), settings)
    captured = []

    monkeypatch.setattr("photo_manager.iphone.shutil.which", lambda _: str(executable))

    def capture_run(command, check):
        captured.extend(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("photo_manager.iphone.subprocess.run", capture_run)

    sync_favorites(
        catalog,
        destination,
        from_date=date(2024, 1, 1),
        to_date=date(2024, 2, 1),
        use_photokit=True,
        uuids=["ABC-123", "DEF-456"],
    )

    assert captured[captured.index("--from-date") + 1] == "2024-01-01"
    assert captured[captured.index("--to-date") + 1] == "2024-02-01"
    assert "--use-photokit" in captured
    assert captured.count("--uuid") == 2


def test_safe_batch_respects_size_cap_and_free_space_reserve():
    assets = [FavoriteAsset(str(index), 500 * 1024**2) for index in range(3)]

    batch, working = _next_safe_batch(
        assets,
        free_bytes=120 * GIB,
        minimum_free_bytes=100 * GIB,
        batch_original_bytes=GIB,
        max_batch_items=25,
    )

    assert len(batch) == 2
    assert working == 4 * 1000 * 1024**2


def test_safe_batch_stops_before_reserve_can_be_breached():
    with pytest.raises(RuntimeError, match="Refusing to download"):
        _next_safe_batch(
            [FavoriteAsset("one", 500 * 1024**2)],
            free_bytes=101 * GIB,
            minimum_free_bytes=100 * GIB,
            batch_original_bytes=GIB,
            max_batch_items=25,
        )


def test_live_disk_guard_stops_export_before_reserve(monkeypatch, settings, tmp_path: Path):
    executable = tmp_path / "osxphotos"
    executable.touch()
    destination = tmp_path / "favorites"
    catalog = Catalog(Database(settings.database_path), settings)

    class FakeProcess:
        returncode = None
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

    process = FakeProcess()
    monkeypatch.setattr("photo_manager.iphone.shutil.which", lambda _: str(executable))
    monkeypatch.setattr("photo_manager.iphone.subprocess.Popen", lambda command: process)
    monkeypatch.setattr(
        "photo_manager.iphone.shutil.disk_usage",
        lambda path: SimpleNamespace(free=99 * GIB),
    )

    with pytest.raises(DiskReserveBreach, match="Stopped iCloud export"):
        sync_favorites(catalog, destination, minimum_free_bytes=100 * GIB)

    assert process.terminated is True


def test_exported_uuid_tracking_requires_a_real_media_file(tmp_path: Path):
    export_db = tmp_path / ".osxphotos_export.db"
    with sqlite3.connect(export_db) as connection:
        connection.execute("CREATE TABLE export_data(uuid TEXT, filepath TEXT)")
        connection.executemany(
            "INSERT INTO export_data(uuid, filepath) VALUES (?, ?)",
            [("EXPORTED", "photo.heic"), ("MISSING", "missing.heic"), ("SIDECAR", "photo.json")],
        )
    (tmp_path / "photo.heic").write_bytes(b"media")

    assert _exported_uuids(tmp_path, {"EXPORTED", "MISSING", "SIDECAR"}) == ["EXPORTED"]
