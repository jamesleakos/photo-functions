from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from .catalog import Catalog, ImportReport
from .config import Settings
from .metadata import SUPPORTED_EXTENSIONS
from .storage import BackupService, StorageBackend


GIB = 1024**3
UUID_PATTERN = re.compile(r"^[0-9A-Fa-f-]{36}$")


class OSXPhotosUnavailable(RuntimeError):
    pass


class DiskReserveBreach(RuntimeError):
    pass


def sync_favorites(
    catalog: Catalog,
    destination: Path,
    *,
    download_missing: bool = True,
    dry_run: bool = False,
    from_date: date | None = None,
    to_date: date | None = None,
    use_photokit: bool = False,
    uuids: list[str] | None = None,
    minimum_free_bytes: int | None = None,
) -> ImportReport:
    """Incrementally export Apple Photos favorites, then ingest their originals."""
    environment_executable = Path(sys.executable).with_name("osxphotos")
    executable = shutil.which("osxphotos")
    if not executable and environment_executable.exists():
        executable = str(environment_executable)
    if not executable:
        raise OSXPhotosUnavailable(
            "osxphotos is not installed. Install the iPhone integration with "
            "`pip install -e '.[iphone]'`."
        )
    # Treat the wrapper's dry run as a truly side-effect-free readiness check.
    # osxphotos' own --dry-run still opens and processes the full library, which
    # can look like a real export and may take a long time for large libraries.
    if dry_run:
        return ImportReport()
    destination.mkdir(parents=True, exist_ok=True)
    command = [
        executable,
        "export",
        str(destination),
        "--favorite",
        "--update",
        "--directory",
        "{created.year}/{created.mm}",
        "--sidecar",
        "json",
    ]
    if download_missing:
        command.append("--download-missing")
    if from_date:
        command.extend(["--from-date", from_date.isoformat()])
    if to_date:
        command.extend(["--to-date", to_date.isoformat()])
    for uuid in uuids or []:
        command.extend(["--uuid", uuid])
    if use_photokit:
        command.append("--use-photokit")
    if minimum_free_bytes is None:
        result = subprocess.run(command, check=False)
        returncode = result.returncode
    else:
        process = subprocess.Popen(command)
        while process.poll() is None:
            free_bytes = shutil.disk_usage(destination).free
            if free_bytes < minimum_free_bytes:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise DiskReserveBreach(
                    f"Stopped iCloud export with {free_bytes / GIB:.1f} GiB free to protect "
                    f"the {minimum_free_bytes / GIB:.1f} GiB reserve"
                )
            time.sleep(2)
        returncode = process.returncode
    if returncode != 0:
        raise RuntimeError(f"osxphotos export failed with exit code {returncode}")
    report = catalog.scan(destination, source="iphone-favorite", favorite=True)
    report.exported_uuids = _exported_uuids(destination, set(uuids or []))
    return report


def _exported_uuids(destination: Path, requested: set[str]) -> list[str]:
    """Return requested UUIDs that have at least one exported media file on disk."""
    export_database = destination / ".osxphotos_export.db"
    if not requested or not export_database.exists():
        return []
    connection = sqlite3.connect(export_database)
    try:
        rows = connection.execute("SELECT uuid, filepath FROM export_data").fetchall()
    finally:
        connection.close()
    found: set[str] = set()
    requested = {uuid.upper() for uuid in requested}
    for uuid, filepath in rows:
        normalized_uuid = str(uuid).upper()
        path = Path(filepath)
        if not path.is_absolute():
            path = destination / path
        if (
            normalized_uuid in requested
            and path.suffix.lower() in SUPPORTED_EXTENSIONS
            and path.exists()
        ):
            found.add(normalized_uuid)
    return sorted(found)


@dataclass(frozen=True)
class FavoriteAsset:
    uuid: str
    original_bytes: int


@dataclass
class YearArchiveReport:
    year: int
    total_assets: int = 0
    completed_assets: int = 0
    deferred_assets: int = 0
    completed_batches: int = 0
    imported: int = 0
    uploaded: int = 0
    released_files: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _osxphotos_executable() -> str:
    environment_executable = Path(sys.executable).with_name("osxphotos")
    executable = shutil.which("osxphotos")
    if not executable and environment_executable.exists():
        executable = str(environment_executable)
    if not executable:
        raise OSXPhotosUnavailable(
            "osxphotos is not installed. Install the iPhone integration with "
            "`pip install -e '.[iphone]'`."
        )
    return executable


def list_favorite_assets(year: int) -> list[FavoriteAsset]:
    """Read Favorite UUIDs and original sizes without downloading media."""
    result = subprocess.run(
        [
            _osxphotos_executable(),
            "query",
            "--favorite",
            "--year",
            str(year),
            "--print",
            "{uuid}\t{photo.original_filesize}",
            "--quiet",
            "--mute",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"osxphotos query failed: {result.stderr.strip()}")
    assets: list[FavoriteAsset] = []
    for line in result.stdout.splitlines():
        uuid, separator, size = line.strip().partition("\t")
        if not separator or not UUID_PATTERN.fullmatch(uuid):
            continue
        try:
            original_bytes = max(0, int(size or 0))
        except ValueError:
            original_bytes = 0
        assets.append(FavoriteAsset(uuid=uuid.upper(), original_bytes=original_bytes))
    if not assets:
        raise RuntimeError(f"No iPhone Favorites found for {year}")
    return assets


def _load_completed(progress_path: Path, year: int) -> set[str]:
    if not progress_path.exists():
        return set()
    try:
        value = json.loads(progress_path.read_text())
    except (OSError, json.JSONDecodeError):
        return set()
    if value.get("year") != year:
        return set()
    return {str(uuid).upper() for uuid in value.get("completed", [])}


def _save_completed(progress_path: Path, year: int, completed: set[str]) -> None:
    temporary = progress_path.with_suffix(progress_path.suffix + ".creating")
    temporary.write_text(json.dumps({"year": year, "completed": sorted(completed)}, indent=2))
    temporary.replace(progress_path)


def _next_safe_batch(
    assets: list[FavoriteAsset],
    *,
    free_bytes: int,
    minimum_free_bytes: int,
    batch_original_bytes: int,
    max_batch_items: int,
) -> tuple[list[FavoriteAsset], int]:
    batch: list[FavoriteAsset] = []
    original_bytes = 0
    for asset in assets:
        estimate = max(asset.original_bytes, 64 * 1024**2)
        if batch and (
            len(batch) >= max_batch_items or original_bytes + estimate > batch_original_bytes
        ):
            break
        batch.append(asset)
        original_bytes += estimate

    # Allow for the original, edited/RAW/Live Photo components, and osxphotos'
    # temporary staging copy. Shrink until the hard free-space reserve is safe.
    while batch:
        required = max(2 * GIB, original_bytes * 4)
        if free_bytes - required >= minimum_free_bytes:
            return batch, required
        removed = batch.pop()
        original_bytes -= max(removed.original_bytes, 64 * 1024**2)
    raise RuntimeError(
        "Refusing to download the next iCloud asset because the configured free-space reserve "
        "cannot be maintained"
    )


def archive_favorites_year(
    catalog: Catalog,
    storage: StorageBackend,
    settings: Settings,
    year: int,
    *,
    download_missing: bool = True,
    use_photokit: bool = True,
    minimum_free_gb: float = 100,
    batch_gb: float = 1,
    max_batch_items: int = 25,
    progress: Callable[[str], None] | None = None,
) -> YearArchiveReport:
    """Export, back up, and release small size-capped batches with a disk reserve."""
    if minimum_free_gb < 10:
        raise ValueError("minimum_free_gb must be at least 10")
    if batch_gb <= 0 or max_batch_items <= 0:
        raise ValueError("batch_gb and max_batch_items must be positive")

    export_root = settings.iphone_export_path.expanduser().resolve()
    export_root.mkdir(parents=True, exist_ok=True)
    destination = export_root / f"stream-{year}-current"
    progress_path = settings.data_dir / f"iphone-favorites-{year}-progress.json"
    assets = list_favorite_assets(year)
    completed = _load_completed(progress_path, year)
    deferred: set[str] = set()
    remaining = [asset for asset in assets if asset.uuid not in completed]
    report = YearArchiveReport(year=year, total_assets=len(assets), completed_assets=len(completed))

    while remaining:
        disk = shutil.disk_usage(export_root)
        batch, reserved_working_bytes = _next_safe_batch(
            remaining,
            free_bytes=disk.free,
            minimum_free_bytes=int(minimum_free_gb * GIB),
            batch_original_bytes=int(batch_gb * GIB),
            max_batch_items=max_batch_items,
        )
        batch_number = report.completed_batches + 1
        if progress:
            progress(
                f"iPhone Favorites batch {batch_number}: downloading {len(batch)} assets; "
                f"{disk.free / GIB:.1f} GiB free, {minimum_free_gb:.0f} GiB protected, "
                f"up to {reserved_working_bytes / GIB:.1f} GiB working space"
            )
        try:
            imported = sync_favorites(
                catalog,
                destination,
                download_missing=download_missing,
                use_photokit=use_photokit,
                uuids=[asset.uuid for asset in batch],
                # The live watcher trips with an additional margin so cleanup can
                # finish while the promised reserve remains intact.
                minimum_free_bytes=int((minimum_free_gb + 5) * GIB),
            )
        except DiskReserveBreach:
            if destination.parent == export_root:
                shutil.rmtree(destination, ignore_errors=True)
            raise
        report.imported += imported.scanned
        if imported.errors:
            report.errors.extend(imported.errors)
            break
        requested_uuids = {asset.uuid for asset in batch}
        exported_uuids = set(imported.exported_uuids)
        missing_uuids = requested_uuids - exported_uuids
        deferred.update(missing_uuids)
        if progress and missing_uuids:
            progress(
                f"iPhone Favorites batch {batch_number}: {len(missing_uuids)} assets were not "
                "exported and will remain incomplete for a later retry"
            )

        if progress:
            progress(f"iPhone Favorites batch {batch_number}: uploading to AWS")
        backed_up = BackupService(catalog, storage, settings).run()
        report.uploaded += backed_up.uploaded
        if backed_up.failed:
            report.errors.extend(backed_up.errors or [])
            break

        released = catalog.release_tree(destination)
        # The destination is constructed directly under the configured export root.
        # Never recursively remove an arbitrary or user-provided path here.
        if destination.parent != export_root:
            raise RuntimeError(f"Unsafe temporary export path: {destination}")
        shutil.rmtree(destination)
        report.released_files += released

        # Persist the catalog state after temporary locations become unavailable.
        snapshot = BackupService(catalog, storage, settings).run()
        if snapshot.failed:
            report.errors.extend(snapshot.errors or [])
            break

        completed.update(exported_uuids)
        _save_completed(progress_path, year, completed)
        remaining = [
            asset for asset in assets if asset.uuid not in completed and asset.uuid not in deferred
        ]
        report.completed_assets = len(completed)
        report.deferred_assets = len(deferred)
        report.completed_batches += 1
        if progress:
            free_after = shutil.disk_usage(export_root).free / GIB
            progress(
                f"iPhone Favorites batch {batch_number}: verified and cleaned immediately; "
                f"{report.completed_assets}/{report.total_assets} assets complete, "
                f"{free_after:.1f} GiB free"
            )

    if deferred:
        report.errors.append(
            f"{len(deferred)} Favorites could not be exported and remain incomplete for retry"
        )
    return report
