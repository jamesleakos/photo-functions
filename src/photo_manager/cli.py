from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from .catalog import Catalog
from .config import Settings
from .database import Database
from .iphone import OSXPhotosUnavailable, archive_favorites_year, sync_favorites
from .storage import BackupService, build_storage


def build_catalog() -> tuple[Settings, Catalog]:
    load_dotenv()
    settings = Settings.from_env()
    settings.ensure_directories()
    return settings, Catalog(Database(settings.database_path), settings)


def print_report(report: object) -> None:
    value = report.to_dict() if hasattr(report, "to_dict") else report.__dict__
    print(json.dumps(value, indent=2, default=str))


def cmd_init(_: argparse.Namespace) -> int:
    settings, _ = build_catalog()
    print(f"Catalog initialized: {settings.database_path}")
    print(f"Storage backend: {settings.storage_backend}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    _, catalog = build_catalog()

    def progress(path: Path) -> None:
        if args.verbose:
            print(path)

    report = catalog.scan(
        args.path,
        source=args.source,
        favorite=args.favorite,
        dry_run=args.dry_run,
        progress=progress,
    )
    print_report(report)
    return 1 if report.errors else 0


def cmd_iphone_favorites(args: argparse.Namespace) -> int:
    settings, catalog = build_catalog()
    try:
        report = sync_favorites(
            catalog,
            Path(args.destination).expanduser()
            if args.destination
            else settings.iphone_export_path,
            download_missing=not args.no_download_missing,
            dry_run=args.dry_run,
        )
    except OSXPhotosUnavailable as error:
        print(error, file=sys.stderr)
        return 2
    print_report(report)
    return 1 if report.errors else 0


def cmd_iphone_year(args: argparse.Namespace) -> int:
    settings, catalog = build_catalog()
    storage = build_storage(settings)

    def progress(message: str) -> None:
        print(message, flush=True)

    try:
        report = archive_favorites_year(
            catalog,
            storage,
            settings,
            args.year,
            download_missing=not args.no_download_missing,
            use_photokit=not args.no_photokit,
            minimum_free_gb=args.minimum_free_gb,
            batch_gb=args.batch_gb,
            max_batch_items=args.max_batch_items,
            progress=progress,
        )
    except OSXPhotosUnavailable as error:
        print(error, file=sys.stderr)
        return 2
    print_report(report)
    return 1 if report.errors else 0


def cmd_backup(args: argparse.Namespace) -> int:
    settings, catalog = build_catalog()
    report = BackupService(catalog, build_storage(settings), settings).run(workers=args.workers)
    print_report(report)
    return 1 if report.failed else 0


def cmd_stats(_: argparse.Namespace) -> int:
    _, catalog = build_catalog()
    print(json.dumps(catalog.stats(), indent=2))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    # The web app owns catalog initialization. In hosted mode it must be able to
    # restore the S3 snapshot before SQLite creates an empty database file.
    load_dotenv()
    settings = Settings.from_env()
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not settings.auth_username:
        print(
            "Refusing to expose an unauthenticated photo library. Set PHOTO_AUTH_USERNAME "
            "and PHOTO_AUTH_PASSWORD, or bind to 127.0.0.1.",
            file=sys.stderr,
        )
        return 2
    import uvicorn

    from .web.app import create_app

    uvicorn.run(create_app(settings), host=args.host, port=args.port)
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="photo-manager", description="End-to-end photo manager")
    commands = root.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Initialize the catalog and storage directories")
    init.set_defaults(func=cmd_init)

    scan = commands.add_parser("scan", help="Catalog a camera card or exported photo folder")
    scan.add_argument("path")
    scan.add_argument("--source", default="folder", help="Source label, such as camera or iphone")
    scan.add_argument(
        "--favorite", action="store_true", help="Mark every imported location favorite"
    )
    scan.add_argument("--dry-run", action="store_true")
    scan.add_argument("--verbose", action="store_true")
    scan.set_defaults(func=cmd_scan)

    iphone = commands.add_parser(
        "iphone-favorites", help="Incrementally export and catalog Apple Photos favorites"
    )
    iphone.add_argument("--destination")
    iphone.add_argument("--no-download-missing", action="store_true")
    iphone.add_argument("--dry-run", action="store_true")
    iphone.set_defaults(func=cmd_iphone_favorites)

    iphone_year = commands.add_parser(
        "iphone-year",
        help="Back up one year of iPhone Favorites in disk-safe batches with immediate cleanup",
    )
    iphone_year.add_argument("year", type=int)
    iphone_year.add_argument("--no-download-missing", action="store_true")
    iphone_year.add_argument("--no-photokit", action="store_true")
    iphone_year.add_argument("--minimum-free-gb", type=float, default=100)
    iphone_year.add_argument("--batch-gb", type=float, default=1)
    iphone_year.add_argument("--max-batch-items", type=int, default=25)
    iphone_year.set_defaults(func=cmd_iphone_year)

    backup = commands.add_parser("backup", help="Back up all eligible master photos")
    backup.add_argument("--workers", type=int, default=4)
    backup.set_defaults(func=cmd_backup)

    stats = commands.add_parser("stats", help="Show catalog and backup totals")
    stats.set_defaults(func=cmd_stats)

    serve = commands.add_parser("serve", help="Run the browser app locally or on a server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)
    return root


def main() -> None:
    args = parser().parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
