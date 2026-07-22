from __future__ import annotations

import base64
import html
import secrets
import shutil
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..catalog import Catalog, sha256_file
from ..config import Settings
from ..database import Database
from ..storage import (
    BackupService,
    build_storage,
    restore_catalog_snapshot,
    thumbnail_key,
    upload_catalog_snapshot,
)
from ..thumbnails import create_thumbnail


class MagazineUpdate(BaseModel):
    issue: str
    status: str
    notes: str = ""


class TagsUpdate(BaseModel):
    tags: list[str]


class VariantDecision(BaseModel):
    decision: str
    preferred_photo_id: int | None = None


class ScanRequest(BaseModel):
    path: str
    source: str = "folder"
    favorite: bool = False


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_directories()
    storage = build_storage(settings)
    if settings.cloud_catalog_sync and not settings.database_path.exists():
        restore_catalog_snapshot(storage, settings)
    catalog = Catalog(Database(settings.database_path), settings)
    web_root = Path(__file__).parent
    catalog_sync_lock = threading.Lock()
    thumbnail_lock = threading.BoundedSemaphore(4)

    def persist_catalog() -> None:
        if settings.cloud_catalog_sync:
            upload_catalog_snapshot(catalog, storage, settings)

    def hosted_mutation(action) -> None:
        with catalog_sync_lock:
            action()
            persist_catalog()

    app = FastAPI(title="Photo Manager", version="0.1.0")
    app.state.settings = settings
    app.state.catalog = catalog
    app.state.storage = storage
    app.mount("/static", StaticFiles(directory=web_root / "static"), name="static")

    @app.middleware("http")
    async def basic_auth(request: Request, call_next):
        if not settings.auth_username or request.url.path == "/health":
            return await call_next(request)
        authorization = request.headers.get("Authorization", "")
        valid = False
        if authorization.startswith("Basic "):
            try:
                decoded = base64.b64decode(authorization[6:]).decode("utf-8")
                username, password = decoded.split(":", 1)
                valid = secrets.compare_digest(
                    username, settings.auth_username
                ) and secrets.compare_digest(password, settings.auth_password or "")
            except (ValueError, UnicodeDecodeError):
                valid = False
        if not valid:
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Photo Manager"'},
            )
        return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=FileResponse)
    def index() -> FileResponse:
        return FileResponse(web_root / "templates" / "index.html")

    @app.get("/api/stats")
    def stats() -> dict:
        return catalog.stats()

    @app.get("/api/config")
    def config() -> dict[str, bool]:
        return {"hosted_gallery": settings.hosted_gallery}

    @app.get("/api/photos")
    def photos(
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        source: str | None = None,
        favorite: bool | None = None,
        issue: str | None = None,
        magazine_status: str | None = None,
        backup_status: str | None = None,
        tag: str | None = None,
        year: int | None = Query(None, ge=1900, le=2200),
    ) -> list[dict]:
        return catalog.list_photos(
            limit=limit,
            offset=offset,
            source=source,
            favorite=favorite,
            issue=issue,
            magazine_status=magazine_status,
            backup_status=backup_status,
            tag=tag,
            year=year,
        )

    def local_or_restored(photo_id: int) -> tuple[dict, Path]:
        photo = catalog.get_photo(photo_id)
        if not photo:
            raise HTTPException(404, "Photo not found")
        local = catalog.available_path(photo_id)
        if local:
            return photo, local
        if not photo.get("object_key"):
            raise HTTPException(404, "No available local or backup copy")
        restored = settings.thumbnail_dir / "restored" / f"{photo['sha256']}{photo['extension']}"
        if not restored.exists():
            storage.download(photo["object_key"], restored)
        return photo, restored

    @app.get("/api/photos/{photo_id}/thumbnail")
    def thumbnail(photo_id: int):
        photo = catalog.get_photo(photo_id)
        if not photo:
            raise HTTPException(404, "Photo not found")
        if photo["media_type"].startswith("video/"):
            label = html.escape(photo["filename"])
            return Response(
                content=(
                    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 720 540'>"
                    "<rect width='720' height='540' fill='#30352f'/>"
                    "<circle cx='360' cy='235' r='62' fill='#b8472d'/>"
                    "<path d='M345 200 L395 235 L345 270 Z' fill='white'/>"
                    f"<text x='360' y='345' fill='white' text-anchor='middle' "
                    f"font-family='sans-serif' font-size='24'>{label}</text></svg>"
                ),
                media_type="image/svg+xml",
            )
        destination = settings.thumbnail_dir / f"{photo['sha256']}.jpg"
        if destination.exists():
            return FileResponse(destination, media_type="image/jpeg")
        try:
            with thumbnail_lock:
                if destination.exists():
                    return FileResponse(destination, media_type="image/jpeg")
                local = catalog.available_path(photo_id)
                if local:
                    create_thumbnail(local, destination)
                else:
                    if not photo.get("object_key"):
                        raise HTTPException(404, "No available local or backup copy")
                    if settings.cloud_catalog_sync:
                        try:
                            storage.download(thumbnail_key(settings, photo), destination)
                            return FileResponse(destination, media_type="image/jpeg")
                        except Exception:
                            destination.unlink(missing_ok=True)
                    restored = (
                        settings.thumbnail_dir
                        / "restored"
                        / (f"{photo['sha256']}{photo['extension']}")
                    )
                    try:
                        storage.download(photo["object_key"], restored)
                        create_thumbnail(restored, destination)
                        if settings.cloud_catalog_sync:
                            storage.put(
                                destination,
                                thumbnail_key(settings, photo),
                                sha256_file(destination),
                            )
                    finally:
                        restored.unlink(missing_ok=True)
        except Exception as error:
            raise HTTPException(415, f"Cannot create thumbnail: {error}") from error
        return FileResponse(destination, media_type="image/jpeg")

    @app.get("/api/photos/{photo_id}/original")
    def original(photo_id: int):
        photo = catalog.get_photo(photo_id)
        if not photo:
            raise HTTPException(404, "Photo not found")
        local = catalog.available_path(photo_id)
        if local:
            return FileResponse(local, filename=photo["filename"], media_type=photo["media_type"])
        if not photo.get("object_key"):
            raise HTTPException(404, "No available copy")
        url = storage.presigned_url(photo["object_key"])
        if url:
            return RedirectResponse(url)
        _, restored = local_or_restored(photo_id)
        return FileResponse(restored, filename=photo["filename"], media_type=photo["media_type"])

    @app.put("/api/photos/{photo_id}/magazine")
    def magazine(photo_id: int, update: MagazineUpdate) -> dict[str, str]:
        if not catalog.get_photo(photo_id):
            raise HTTPException(404, "Photo not found")
        try:
            hosted_mutation(
                lambda: catalog.set_magazine_selection(
                    photo_id, update.issue, update.status, update.notes
                )
            )
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return {"status": "updated"}

    @app.put("/api/photos/{photo_id}/tags")
    def tags(photo_id: int, update: TagsUpdate) -> dict[str, str]:
        if not catalog.get_photo(photo_id):
            raise HTTPException(404, "Photo not found")
        hosted_mutation(lambda: catalog.set_tags(photo_id, update.tags))
        return {"status": "updated"}

    @app.get("/api/variant-groups")
    def variant_groups(status: str = "pending") -> list[dict]:
        if status not in {"pending", "confirmed", "rejected"}:
            raise HTTPException(400, "Invalid review status")
        return catalog.list_variant_groups(status)

    @app.post("/api/variant-groups/{group_id}/decision")
    def variant_decision(group_id: int, update: VariantDecision) -> dict[str, str]:
        try:
            hosted_mutation(
                lambda: catalog.decide_variant_group(
                    group_id, update.decision, update.preferred_photo_id
                )
            )
        except KeyError as error:
            raise HTTPException(404, "Variant group not found") from error
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        return {"status": "updated"}

    @app.post("/api/imports/scan")
    def scan(update: ScanRequest) -> dict:
        if settings.hosted_gallery:
            raise HTTPException(403, "Imports are disabled on the hosted gallery")
        try:
            return catalog.scan(update.path, update.source, update.favorite).to_dict()
        except FileNotFoundError as error:
            raise HTTPException(404, str(error)) from error

    @app.post("/api/imports/upload")
    async def upload(files: list[UploadFile] = File(...), favorite: bool = False) -> JSONResponse:
        if settings.hosted_gallery:
            raise HTTPException(403, "Uploads are disabled on the hosted gallery")
        reports = []
        batch = settings.upload_dir / uuid.uuid4().hex
        batch.mkdir(parents=True, exist_ok=True)
        for item in files:
            safe_name = Path(item.filename or "upload").name
            destination = batch / uuid.uuid4().hex / safe_name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("wb") as handle:
                shutil.copyfileobj(item.file, handle)
            try:
                result = catalog.ingest_file(destination, "browser-upload", favorite=favorite)
                reports.append({"filename": safe_name, "result": result})
            except Exception as error:
                reports.append({"filename": safe_name, "result": "error", "error": str(error)})
        return JSONResponse({"files": reports})

    @app.post("/api/backups/run")
    def run_backup() -> dict:
        if settings.hosted_gallery:
            raise HTTPException(403, "Backups are managed by the local archive workflow")
        report = BackupService(catalog, storage, settings).run()
        return report.__dict__

    return app
