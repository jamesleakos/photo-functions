from __future__ import annotations

import base64
import hashlib
import hmac
import html
import secrets
import shutil
import threading
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

from ..catalog import Catalog, sha256_file
from ..config import Settings
from ..database import Database
from ..storage import (
    BackupService,
    build_storage,
    preview_key,
    restore_catalog_snapshot,
    thumbnail_key,
    upload_catalog_snapshot,
)
from ..thumbnails import create_preview, create_thumbnail


class MagazineUpdate(BaseModel):
    issue: str
    status: str
    notes: str = ""


class TagsUpdate(BaseModel):
    tags: list[str]


EditorialFlag = Literal["flagship", "include", "candidate", "one_of", "not_included"]
EditorialFlagFilter = Literal[
    "flagship", "include", "candidate", "one_of", "not_included", "unflagged"
]
PhotoSourceFilter = Literal["camera", "phone"]
MediaFilter = Literal["photo", "video"]
DateOrder = Literal["asc", "desc"]


class EditorialFlagUpdate(BaseModel):
    flag: EditorialFlag | None


class CaptureDateItem(BaseModel):
    photo_id: int
    captured_at: datetime


class CaptureDatesUpdate(BaseModel):
    items: list[CaptureDateItem]


class VariantDecision(BaseModel):
    decision: str
    preferred_photo_id: int | None = None


class ScanRequest(BaseModel):
    path: str
    source: str = "folder"
    favorite: bool = False


SESSION_COOKIE = "photo_manager_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30


def _session_signature(username: str, password: str, issued_at: int) -> str:
    key = hashlib.sha256(f"photo-manager-session:{username}:{password}".encode()).digest()
    return hmac.new(key, f"{username}:{issued_at}".encode(), hashlib.sha256).hexdigest()


def _new_session_cookie(username: str, password: str) -> str:
    issued_at = int(time.time())
    return f"{issued_at}.{_session_signature(username, password, issued_at)}"


def _valid_session_cookie(value: str | None, username: str, password: str) -> bool:
    if not value:
        return False
    try:
        issued_text, signature = value.split(".", 1)
        issued_at = int(issued_text)
    except (TypeError, ValueError):
        return False
    age = int(time.time()) - issued_at
    if age < -300 or age > SESSION_MAX_AGE:
        return False
    expected = _session_signature(username, password, issued_at)
    return secrets.compare_digest(signature, expected)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_directories()
    storage = build_storage(settings)
    if settings.cloud_catalog_sync and not settings.database_path.exists():
        restore_catalog_snapshot(storage, settings)
    catalog = Catalog(Database(settings.database_path), settings)
    web_root = Path(__file__).parent
    catalog_sync_lock = threading.Lock()
    # A full-resolution HEIC can occupy well over 100 MB while Pillow decodes it.
    # Serialize hosted generation so a fresh gallery page cannot exhaust a small
    # Render instance; local machines can retain modest parallelism.
    thumbnail_lock = threading.BoundedSemaphore(1 if settings.hosted_gallery else 4)

    def persist_catalog() -> None:
        if settings.cloud_catalog_sync:
            upload_catalog_snapshot(catalog, storage, settings)

    def hosted_mutation(action):
        with catalog_sync_lock:
            result = action()
            persist_catalog()
            return result

    app = FastAPI(title="Photo Manager", version="0.1.0")
    app.state.settings = settings
    app.state.catalog = catalog
    app.state.storage = storage
    app.mount("/static", StaticFiles(directory=web_root / "static"), name="static")

    def valid_basic_auth(request: Request) -> bool:
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(authorization[6:]).decode("utf-8")
            username, password = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            return False
        return secrets.compare_digest(
            username, settings.auth_username or ""
        ) and secrets.compare_digest(password, settings.auth_password or "")

    @app.middleware("http")
    async def session_auth(request: Request, call_next):
        public_path = (
            request.url.path == "/health"
            or request.url.path == "/login"
            or request.url.path.startswith("/static/")
        )
        if not settings.auth_username or public_path:
            return await call_next(request)
        valid_session = _valid_session_cookie(
            request.cookies.get(SESSION_COOKIE),
            settings.auth_username,
            settings.auth_password or "",
        )
        if not valid_session and not valid_basic_auth(request):
            if request.url.path.startswith("/api/"):
                return JSONResponse({"detail": "Sign in required"}, status_code=401)
            return RedirectResponse("/login", status_code=303)
        return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/login", response_class=FileResponse)
    def login_page(request: Request):
        if not settings.auth_username:
            return RedirectResponse("/", status_code=303)
        if _valid_session_cookie(
            request.cookies.get(SESSION_COOKIE),
            settings.auth_username,
            settings.auth_password or "",
        ):
            return RedirectResponse("/", status_code=303)
        return FileResponse(web_root / "templates" / "login.html")

    @app.post("/login")
    def login(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
    ) -> RedirectResponse:
        valid = bool(settings.auth_username) and secrets.compare_digest(
            username, settings.auth_username or ""
        ) and secrets.compare_digest(password, settings.auth_password or "")
        if not valid:
            return RedirectResponse("/login?error=1", status_code=303)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            _new_session_cookie(username, password),
            max_age=SESSION_MAX_AGE,
            httponly=True,
            secure=settings.hosted_gallery or request.url.scheme == "https",
            samesite="lax",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/logout")
    def logout() -> RedirectResponse:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="lax")
        response.headers["Cache-Control"] = "no-store"
        return response

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
        source: list[PhotoSourceFilter] = Query(default=[]),
        favorite: bool | None = None,
        issue: str | None = None,
        magazine_status: str | None = None,
        backup_status: str | None = None,
        tag: str | None = None,
        year: int | None = Query(None, ge=1900, le=2200),
        include_nonpreferred: bool = False,
        flag: list[EditorialFlagFilter] = Query(default=[]),
        media: list[MediaFilter] = Query(default=[]),
        date_from: date | None = None,
        date_to: date | None = None,
        date_order: DateOrder = "desc",
    ) -> list[dict]:
        if date_from and date_to and date_from > date_to:
            raise HTTPException(400, "Start date must be on or before end date")
        return catalog.list_photos(
            limit=limit,
            offset=offset,
            source=source or None,
            favorite=favorite,
            issue=issue,
            magazine_status=magazine_status,
            backup_status=backup_status,
            tag=tag,
            year=year,
            include_nonpreferred=include_nonpreferred,
            editorial_flags=flag,
            media=media or None,
            date_from=date_from.isoformat() if date_from else None,
            date_to=date_to.isoformat() if date_to else None,
            date_order=date_order,
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

    @app.get("/api/photos/{photo_id}/preview")
    def preview(photo_id: int):
        photo = catalog.get_photo(photo_id)
        if not photo:
            raise HTTPException(404, "Photo not found")
        if not photo["media_type"].startswith("image/"):
            raise HTTPException(415, "High-resolution previews are available for photos only")
        temporary_preview = settings.hosted_gallery and settings.cloud_catalog_sync
        preview_name = (
            f"{photo['sha256']}-{uuid.uuid4().hex}.jpg"
            if temporary_preview
            else f"{photo['sha256']}.jpg"
        )
        destination = settings.thumbnail_dir / "previews" / preview_name

        def preview_response() -> FileResponse:
            cleanup = (
                BackgroundTask(destination.unlink, missing_ok=True)
                if temporary_preview
                else None
            )
            return FileResponse(
                destination,
                media_type="image/jpeg",
                headers={"Cache-Control": "private, max-age=31536000, immutable"},
                background=cleanup,
            )

        if destination.exists():
            return preview_response()
        try:
            with thumbnail_lock:
                if destination.exists():
                    return preview_response()
                local = catalog.available_path(photo_id)
                if local:
                    create_preview(local, destination)
                else:
                    if not photo.get("object_key"):
                        raise HTTPException(404, "No available local or backup copy")
                    if settings.cloud_catalog_sync:
                        try:
                            storage.download(preview_key(settings, photo), destination)
                            return preview_response()
                        except Exception:
                            destination.unlink(missing_ok=True)
                    restored = (
                        settings.thumbnail_dir
                        / "restored"
                        / f"{photo['sha256']}{photo['extension']}"
                    )
                    try:
                        storage.download(photo["object_key"], restored)
                        create_preview(restored, destination)
                        if settings.cloud_catalog_sync:
                            storage.put(
                                destination,
                                preview_key(settings, photo),
                                sha256_file(destination),
                            )
                    finally:
                        restored.unlink(missing_ok=True)
        except HTTPException:
            if temporary_preview:
                destination.unlink(missing_ok=True)
            raise
        except Exception as error:
            if temporary_preview:
                destination.unlink(missing_ok=True)
            raise HTTPException(415, f"Cannot create high-resolution preview: {error}") from error
        return preview_response()

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

    @app.put("/api/photos/capture-dates")
    def capture_dates(update: CaptureDatesUpdate) -> dict[str, int | str]:
        if len(update.items) > 500:
            raise HTTPException(400, "At most 500 capture dates can be updated at once")
        values = {
            item.photo_id: item.captured_at.isoformat(timespec="seconds")
            for item in update.items
        }
        try:
            hosted_mutation(lambda: catalog.set_capture_dates(values))
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        return {"status": "updated", "updated": len(values)}

    @app.put("/api/photos/{photo_id}/flag")
    def editorial_flag(
        photo_id: int, update: EditorialFlagUpdate
    ) -> dict:
        try:
            group = hosted_mutation(
                lambda: catalog.set_editorial_flag(photo_id, update.flag)
            )
        except KeyError as error:
            raise HTTPException(404, "Photo not found") from error
        return {"status": "updated", "flag": update.flag, "one_of_group": group}

    @app.get("/api/one-of-groups/current")
    def current_one_of_group() -> dict:
        return catalog.current_one_of_group()

    @app.post("/api/one-of-groups/current/finish")
    def finish_current_one_of_group() -> dict:
        try:
            return hosted_mutation(catalog.finish_current_one_of_group)
        except LookupError as error:
            raise HTTPException(409, str(error)) from error

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
