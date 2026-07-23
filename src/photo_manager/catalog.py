from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .config import Settings
from .database import Database
from .metadata import IMAGE_EXTENSIONS, SUPPORTED_EXTENSIONS, MetadataExtractor, PhotoMetadata


@dataclass
class ImportReport:
    scanned: int = 0
    added: int = 0
    already_cataloged: int = 0
    exact_duplicates: int = 0
    variants_pending: int = 0
    variants_confirmed: int = 0
    unsupported: int = 0
    errors: list[str] = field(default_factory=list)
    exported_uuids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_stem(filename: str) -> str:
    stem = Path(filename).stem.lower()
    return re.sub(r"(?:[_-](?:edited|export|copy|small|large|original)|\s+\(\d+\))$", "", stem)


def _phash_distance(first: str | None, second: str | None) -> int | None:
    if not first or not second:
        return None
    try:
        return (int(first, 16) ^ int(second, 16)).bit_count()
    except ValueError:
        return None


def _capture_delta(first: str | None, second: str | None) -> float | None:
    if not first or not second:
        return None
    try:
        return abs((datetime.fromisoformat(first) - datetime.fromisoformat(second)).total_seconds())
    except ValueError:
        return None


def variant_score(first: dict[str, Any], second: dict[str, Any]) -> tuple[float, str]:
    """Return a conservative similarity score and a human-readable evidence summary."""
    score = 0.0
    evidence: list[str] = []

    distance = _phash_distance(first.get("perceptual_hash"), second.get("perceptual_hash"))
    if distance is not None:
        if distance <= 4:
            score += 0.55
        elif distance <= 8:
            score += 0.42
        elif distance <= 12:
            score += 0.25
        if distance <= 12:
            evidence.append(f"visual distance {distance}")

    delta = _capture_delta(first.get("captured_at"), second.get("captured_at"))
    if delta is not None:
        if delta <= 2:
            score += 0.35
        elif delta <= 60:
            score += 0.28
        elif delta <= 300:
            score += 0.15
        if delta <= 300:
            evidence.append(f"capture time {delta:.0f}s apart")

    if _normalized_stem(first["filename"]) == _normalized_stem(second["filename"]):
        score += 0.40
        evidence.append("matching filename")

    dimensions = (
        first.get("width"),
        first.get("height"),
        second.get("width"),
        second.get("height"),
    )
    if all(dimensions):
        first_ratio = max(dimensions[0], dimensions[1]) / min(dimensions[0], dimensions[1])
        second_ratio = max(dimensions[2], dimensions[3]) / min(dimensions[2], dimensions[3])
        ratio_delta = abs(first_ratio - second_ratio) / max(first_ratio, second_ratio)
        if ratio_delta <= 0.01:
            score += 0.10
            evidence.append("same aspect ratio")
        elif ratio_delta <= 0.05:
            score += 0.05
            evidence.append("similar aspect ratio")

    return min(score, 1.0), ", ".join(evidence) or "weak similarity"


def _quality_key(photo: dict[str, Any] | sqlite3.Row) -> tuple[int, int, int]:
    pixels = (photo["width"] or 0) * (photo["height"] or 0)
    raw_rank = (
        1
        if photo["extension"].lower()
        in {".dng", ".arw", ".cr2", ".cr3", ".nef", ".orf", ".rw2", ".raf"}
        else 0
    )
    return pixels, raw_rank, photo["size_bytes"]


def _source_tag(source: str) -> str:
    normalized = source.strip().lower()
    if normalized == "camera" or normalized.startswith(("camera-", "gopro", "drone")):
        return "source:camera"
    if normalized in {"phone", "iphone", "iphone-favorite"} or normalized.startswith(
        ("phone-", "iphone-")
    ):
        return "source:phone"
    return f"source:{normalized}"


class Catalog:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        extractor: MetadataExtractor | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.extractor = extractor or MetadataExtractor()
        self.database.initialize()

    def scan(
        self,
        root: Path | str,
        source: str,
        favorite: bool = False,
        dry_run: bool = False,
        progress: Callable[[Path], None] | None = None,
    ) -> ImportReport:
        root = Path(root).expanduser().resolve()
        if not root.exists():
            raise FileNotFoundError(root)
        files = (
            [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
        )
        report = ImportReport()
        for path in files:
            if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                report.unsupported += 1
                continue
            report.scanned += 1
            if progress:
                progress(path)
            try:
                result = self.ingest_file(path, source, favorite=favorite, dry_run=dry_run)
                setattr(report, result, getattr(report, result) + 1)
            except Exception as error:
                report.errors.append(f"{path}: {error}")
        return report

    def ingest_file(
        self, path: Path | str, source: str, favorite: bool = False, dry_run: bool = False
    ) -> str:
        path = Path(path).expanduser().resolve()
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported media type: {path.suffix}")
        digest = sha256_file(path)

        with self.database.connect() as connection:
            location = connection.execute(
                "SELECT id, photo_id FROM locations WHERE path = ?", (str(path),)
            ).fetchone()
            existing = connection.execute(
                "SELECT id FROM photos WHERE sha256 = ?", (digest,)
            ).fetchone()

        if dry_run:
            if location:
                return "already_cataloged"
            return "exact_duplicates" if existing else "added"

        if location and existing and location["photo_id"] == existing["id"]:
            with self.database.connect() as connection:
                connection.execute(
                    """UPDATE locations SET source = ?, favorite = MAX(favorite, ?),
                       available = 1, last_seen_at = CURRENT_TIMESTAMP WHERE path = ?""",
                    (source, int(favorite), str(path)),
                )
                connection.commit()
            self._refresh_automatic_tags(existing["id"])
            return "already_cataloged"

        if existing:
            self._upsert_location(existing["id"], path, source, favorite)
            self._refresh_automatic_tags(existing["id"])
            return "exact_duplicates"

        metadata = self.extractor.extract(path)
        with self.database.transaction() as connection:
            if location:
                # Temporary export paths can be reused for different content on a
                # later run (for example, AppleScript versus PhotoKit versions).
                # Preserve the old provenance as an unavailable tombstone while
                # freeing the real path for the newly observed content.
                connection.execute(
                    """UPDATE locations SET path = ?, available = 0,
                       last_seen_at = CURRENT_TIMESTAMP WHERE id = ?""",
                    (f"replaced://{location['id']}/{path}", location["id"]),
                )
            cursor = connection.execute(
                """INSERT INTO photos(
                    sha256, perceptual_hash, filename, extension, media_type, size_bytes,
                    width, height, captured_at, make, model, lens_model, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    digest,
                    metadata.perceptual_hash,
                    path.name,
                    path.suffix.lower(),
                    metadata.media_type,
                    path.stat().st_size,
                    metadata.width,
                    metadata.height,
                    metadata.captured_at,
                    metadata.make,
                    metadata.model,
                    metadata.lens_model,
                    metadata.as_json(),
                ),
            )
            photo_id = cursor.lastrowid
            connection.execute(
                "INSERT INTO locations(photo_id, path, source, favorite) VALUES (?, ?, ?, ?)",
                (photo_id, str(path), source, int(favorite)),
            )

        match = self._find_best_variant(photo_id, path.name, metadata, source)
        if not match:
            self._refresh_automatic_tags(photo_id)
            return "added"
        status = self._group_variant(photo_id, match)
        self._refresh_automatic_tags(photo_id)
        return f"variants_{status}"

    def _upsert_location(self, photo_id: int, path: Path, source: str, favorite: bool) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO locations(photo_id, path, source, favorite)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET photo_id = excluded.photo_id,
                     source = excluded.source, favorite = MAX(locations.favorite, excluded.favorite),
                     available = 1, last_seen_at = CURRENT_TIMESTAMP""",
                (photo_id, str(path), source, int(favorite)),
            )
            connection.commit()

    def _find_best_variant(
        self, photo_id: int, filename: str, metadata: PhotoMetadata, source: str
    ) -> dict[str, Any] | None:
        if Path(filename).suffix.lower() not in IMAGE_EXTENSIONS:
            return None
        current = {
            "filename": filename,
            "perceptual_hash": metadata.perceptual_hash,
            "captured_at": metadata.captured_at,
            "width": metadata.width,
            "height": metadata.height,
        }
        with self.database.connect() as connection:
            candidates = connection.execute(
                """SELECT p.*, vm.group_id, vg.review_status,
                     GROUP_CONCAT(DISTINCT l.source) sources
                   FROM photos p
                   JOIN locations l ON l.photo_id = p.id
                   LEFT JOIN variant_members vm ON vm.photo_id = p.id
                   LEFT JOIN variant_groups vg ON vg.id = vm.group_id
                   WHERE p.id != ? AND p.media_type LIKE 'image/%'
                     AND EXISTS (
                       SELECT 1 FROM locations cross_source
                       WHERE cross_source.photo_id = p.id AND cross_source.source != ?
                     )
                   GROUP BY p.id""",
                (photo_id, source),
            ).fetchall()

        best: dict[str, Any] | None = None
        current_source_tag = _source_tag(source)
        for row in candidates:
            candidate = dict(row)
            if candidate.get("review_status") == "rejected":
                continue
            candidate_source_tags = {
                _source_tag(value) for value in (candidate.get("sources") or "").split(",") if value
            }
            # Perceptual matching solves the camera-original versus phone-export case.
            # Never collapse two camera shots (or two phone edits) merely because they
            # look alike; byte-identical files were already handled by SHA-256 above.
            if candidate_source_tags == {current_source_tag}:
                continue
            score, method = variant_score(current, candidate)
            if score < self.settings.variant_suggest_threshold:
                continue
            if not best or score > best["score"]:
                best = {"photo": candidate, "score": score, "method": method}
        return best

    def _group_variant(self, photo_id: int, match: dict[str, Any]) -> str:
        candidate = match["photo"]
        status = (
            "confirmed" if match["score"] >= self.settings.variant_confirm_threshold else "pending"
        )
        with self.database.transaction() as connection:
            new_photo = connection.execute(
                "SELECT * FROM photos WHERE id = ?", (photo_id,)
            ).fetchone()
            preferred_id = (
                photo_id if _quality_key(new_photo) > _quality_key(candidate) else candidate["id"]
            )
            group_id = candidate.get("group_id")
            if group_id:
                existing_group = connection.execute(
                    "SELECT * FROM variant_groups WHERE id = ?", (group_id,)
                ).fetchone()
                current_preferred = connection.execute(
                    "SELECT * FROM photos WHERE id = ?", (existing_group["preferred_photo_id"],)
                ).fetchone()
                if _quality_key(current_preferred) > _quality_key(new_photo):
                    preferred_id = current_preferred["id"]
                combined_status = (
                    "confirmed"
                    if existing_group["review_status"] == "confirmed" and status == "confirmed"
                    else "pending"
                )
                connection.execute(
                    """UPDATE variant_groups SET preferred_photo_id = ?, confidence = MIN(confidence, ?),
                       match_method = match_method || '; ' || ?, review_status = ? WHERE id = ?""",
                    (preferred_id, match["score"], match["method"], combined_status, group_id),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO variant_members(group_id, photo_id) VALUES (?, ?)",
                    (group_id, photo_id),
                )
                return combined_status

            cursor = connection.execute(
                """INSERT INTO variant_groups(
                    preferred_photo_id, match_method, confidence, review_status
                ) VALUES (?, ?, ?, ?)""",
                (preferred_id, match["method"], match["score"], status),
            )
            group_id = cursor.lastrowid
            connection.executemany(
                "INSERT INTO variant_members(group_id, photo_id) VALUES (?, ?)",
                [(group_id, candidate["id"]), (group_id, photo_id)],
            )
        return status

    def _refresh_automatic_tags(self, photo_id: int) -> None:
        """Refresh system-owned source and favorite tags for a photo/variant group."""
        with self.database.transaction() as connection:
            membership = connection.execute(
                """SELECT vm.group_id, vg.review_status
                   FROM variant_members vm JOIN variant_groups vg ON vg.id = vm.group_id
                   WHERE vm.photo_id = ?""",
                (photo_id,),
            ).fetchone()
            if membership and membership["review_status"] == "confirmed":
                members = [
                    row["photo_id"]
                    for row in connection.execute(
                        "SELECT photo_id FROM variant_members WHERE group_id = ?",
                        (membership["group_id"],),
                    ).fetchall()
                ]
            else:
                members = [photo_id]

            placeholders = ",".join("?" for _ in members)
            favorite = bool(
                connection.execute(
                    f"SELECT MAX(favorite) value FROM locations WHERE photo_id IN ({placeholders})",
                    members,
                ).fetchone()["value"]
            )
            favorite_tag = "favorite" if favorite else "not-favorite"

            for member_id in members:
                sources = connection.execute(
                    "SELECT DISTINCT source FROM locations WHERE photo_id = ?", (member_id,)
                ).fetchall()
                desired = {_source_tag(row["source"]) for row in sources}
                desired.add(favorite_tag)
                connection.execute(
                    """DELETE FROM photo_tags WHERE photo_id = ? AND tag_id IN (
                       SELECT id FROM tags WHERE name LIKE 'source:%' OR name IN ('favorite', 'not-favorite')
                    )""",
                    (member_id,),
                )
                for name in sorted(desired):
                    connection.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (name,))
                    tag_id = connection.execute(
                        "SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,)
                    ).fetchone()["id"]
                    connection.execute(
                        "INSERT OR IGNORE INTO photo_tags(photo_id, tag_id) VALUES (?, ?)",
                        (member_id, tag_id),
                    )

    def release_tree(self, root: Path | str) -> int:
        """Mark temporary files unavailable after their content is safely in storage."""
        root = Path(root).expanduser().resolve()
        prefix = str(root) + "/%"
        with self.database.transaction() as connection:
            locations = connection.execute(
                "SELECT id, photo_id FROM locations WHERE path = ? OR path LIKE ?",
                (str(root), prefix),
            ).fetchall()
            for location in locations:
                safe = connection.execute(
                    """SELECT 1
                       WHERE EXISTS (
                         SELECT 1 FROM backups b
                         WHERE b.photo_id = ? AND b.backend = ? AND b.status = 'uploaded'
                       ) OR EXISTS (
                         SELECT 1 FROM variant_members vm
                         JOIN variant_groups vg ON vg.id = vm.group_id
                         JOIN backups b ON b.photo_id = vg.preferred_photo_id
                         WHERE vm.photo_id = ? AND vg.review_status = 'confirmed'
                           AND b.backend = ? AND b.status = 'uploaded'
                       )""",
                    (
                        location["photo_id"],
                        self.settings.storage_backend,
                        location["photo_id"],
                        self.settings.storage_backend,
                    ),
                ).fetchone()
                if not safe:
                    raise RuntimeError(
                        f"Refusing to release {root}: photo {location['photo_id']} is not safely backed up"
                    )
            connection.execute(
                "UPDATE locations SET available = 0 WHERE path = ? OR path LIKE ?",
                (str(root), prefix),
            )
        return len(locations)

    def list_photos(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        source: str | None = None,
        favorite: bool | None = None,
        issue: str | None = None,
        magazine_status: str | None = None,
        backup_status: str | None = None,
        tag: str | None = None,
        year: int | None = None,
        include_nonpreferred: bool = True,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if not include_nonpreferred:
            clauses.append(
                """(vg.review_status IS NULL OR vg.review_status != 'confirmed'
                   OR vg.preferred_photo_id = p.id)"""
            )
        if source:
            clauses.append(
                "EXISTS (SELECT 1 FROM locations sx WHERE sx.photo_id = p.id AND sx.source = ?)"
            )
            parameters.append(source)
        if favorite is not None:
            clauses.append(
                """EXISTS (
                    SELECT 1 FROM locations fx
                    WHERE fx.favorite = ? AND (
                        fx.photo_id = p.id OR (vg.review_status = 'confirmed' AND fx.photo_id IN (
                            SELECT sibling.photo_id FROM variant_members sibling
                            WHERE sibling.group_id = vm.group_id
                        ))
                    )
                )"""
            )
            parameters.append(int(favorite))
        if issue:
            clauses.append("ms.issue = ?")
            parameters.append(issue)
        if magazine_status:
            clauses.append("ms.status = ?")
            parameters.append(magazine_status)
        if backup_status:
            if backup_status == "not_uploaded":
                clauses.append("b.status IS NULL")
            else:
                clauses.append("b.status = ?")
                parameters.append(backup_status)
        if tag:
            clauses.append(
                """EXISTS (SELECT 1 FROM photo_tags pt JOIN tags t ON t.id = pt.tag_id
                   WHERE pt.photo_id = p.id AND t.name = ? COLLATE NOCASE)"""
            )
            parameters.append(tag)
        if year is not None:
            clauses.append("substr(COALESCE(p.captured_at, p.added_at), 1, 4) = ?")
            parameters.append(str(year))
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        parameters.extend([min(max(limit, 1), 500), max(offset, 0)])
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""SELECT p.*,
                    EXISTS(
                        SELECT 1 FROM locations f
                        WHERE f.favorite = 1 AND (
                            f.photo_id = p.id OR (vg.review_status = 'confirmed' AND f.photo_id IN (
                                SELECT sibling.photo_id FROM variant_members sibling
                                WHERE sibling.group_id = vm.group_id
                            ))
                        )
                    ) favorite,
                    (SELECT group_concat(DISTINCT source) FROM locations s WHERE s.photo_id = p.id) sources,
                    (SELECT path FROM locations l WHERE l.photo_id = p.id AND l.available = 1 LIMIT 1) local_path,
                    vg.id variant_group_id, vg.preferred_photo_id, vg.review_status variant_status,
                    b.status backup_status, b.object_key,
                    ms.issue magazine_issue, ms.status magazine_status, ms.notes magazine_notes,
                    (SELECT group_concat(t.name, ', ') FROM photo_tags pt JOIN tags t ON t.id = pt.tag_id
                     WHERE pt.photo_id = p.id) tags,
                    (SELECT group_concat(t.name, ', ') FROM photo_tags pt JOIN tags t ON t.id = pt.tag_id
                     WHERE pt.photo_id = p.id AND t.name NOT LIKE 'source:%'
                       AND t.name NOT IN ('favorite', 'not-favorite')) user_tags
                FROM photos p
                LEFT JOIN variant_members vm ON vm.photo_id = p.id
                LEFT JOIN variant_groups vg ON vg.id = vm.group_id
                LEFT JOIN backups b ON b.photo_id = p.id AND b.backend = ?
                LEFT JOIN magazine_selections ms ON ms.photo_id = p.id {where}
                GROUP BY p.id
                ORDER BY COALESCE(p.captured_at, p.added_at) DESC, p.id DESC
                LIMIT ? OFFSET ?""",
                [self.settings.storage_backend, *parameters],
            ).fetchall()
        return [dict(row) for row in rows]

    def get_photo(self, photo_id: int) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT p.*,
                   (SELECT path FROM locations l WHERE l.photo_id = p.id AND l.available = 1 LIMIT 1)
                     local_path,
                   (SELECT object_key FROM backups b WHERE b.photo_id = p.id
                     AND b.backend = ? AND b.status = 'uploaded' LIMIT 1) object_key
                   FROM photos p WHERE p.id = ?""",
                (self.settings.storage_backend, photo_id),
            ).fetchone()
        return dict(row) if row else None

    def available_path(self, photo_id: int) -> Path | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT path FROM locations WHERE photo_id = ? AND available = 1 ORDER BY id LIMIT 1",
                (photo_id,),
            ).fetchone()
        if row and Path(row["path"]).exists():
            return Path(row["path"])
        return None

    def list_variant_groups(self, status: str = "pending") -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            groups = connection.execute(
                "SELECT * FROM variant_groups WHERE review_status = ? ORDER BY confidence DESC",
                (status,),
            ).fetchall()
            result = []
            for group in groups:
                members = connection.execute(
                    """SELECT p.*, (p.id = ?) is_preferred,
                       (SELECT path FROM locations l WHERE l.photo_id = p.id AND l.available = 1 LIMIT 1) local_path
                       FROM variant_members vm JOIN photos p ON p.id = vm.photo_id
                       WHERE vm.group_id = ? ORDER BY is_preferred DESC""",
                    (group["preferred_photo_id"], group["id"]),
                ).fetchall()
                item = dict(group)
                item["members"] = [dict(member) for member in members]
                result.append(item)
        return result

    def decide_variant_group(
        self, group_id: int, decision: str, preferred_photo_id: int | None
    ) -> None:
        if decision not in {"confirmed", "rejected"}:
            raise ValueError("decision must be confirmed or rejected")
        with self.database.transaction() as connection:
            group = connection.execute(
                "SELECT * FROM variant_groups WHERE id = ?", (group_id,)
            ).fetchone()
            if not group:
                raise KeyError(group_id)
            selected = preferred_photo_id or group["preferred_photo_id"]
            member = connection.execute(
                "SELECT 1 FROM variant_members WHERE group_id = ? AND photo_id = ?",
                (group_id, selected),
            ).fetchone()
            if not member:
                raise ValueError("Preferred photo must be a member of the group")
            connection.execute(
                """UPDATE variant_groups SET preferred_photo_id = ?, review_status = ?,
                   reviewed_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (selected, decision, group_id),
            )
        self._refresh_automatic_tags(selected)

    def set_magazine_selection(
        self, photo_id: int, issue: str, status: str, notes: str = ""
    ) -> None:
        if status not in {"candidate", "selected", "placed", "rejected"}:
            raise ValueError("Invalid magazine status")
        issue = issue.strip()
        if not issue:
            raise ValueError("Magazine issue is required")
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO magazine_selections(photo_id, issue, status, notes)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(photo_id, issue) DO UPDATE SET status = excluded.status,
                     notes = excluded.notes, updated_at = CURRENT_TIMESTAMP""",
                (photo_id, issue, status, notes.strip()),
            )
            connection.commit()

    def set_tags(self, photo_id: int, tags: Iterable[str]) -> None:
        normalized = sorted(
            {
                tag.strip()
                for tag in tags
                if tag.strip()
                and not tag.strip().lower().startswith("source:")
                and tag.strip().lower() not in {"favorite", "not-favorite"}
            }
        )
        with self.database.transaction() as connection:
            connection.execute(
                """DELETE FROM photo_tags WHERE photo_id = ? AND tag_id IN (
                   SELECT id FROM tags
                   WHERE name NOT LIKE 'source:%' AND name NOT IN ('favorite', 'not-favorite')
                )""",
                (photo_id,),
            )
            for name in normalized:
                connection.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (name,))
                tag_id = connection.execute(
                    "SELECT id FROM tags WHERE name = ? COLLATE NOCASE", (name,)
                ).fetchone()["id"]
                connection.execute(
                    "INSERT INTO photo_tags(photo_id, tag_id) VALUES (?, ?)", (photo_id, tag_id)
                )

    def backup_candidates(self) -> list[dict[str, Any]]:
        """Return ungrouped photos, all unreviewed variants, and confirmed preferred masters."""
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT p.* FROM photos p
                   LEFT JOIN variant_members vm ON vm.photo_id = p.id
                   LEFT JOIN variant_groups vg ON vg.id = vm.group_id
                   LEFT JOIN backups b ON b.photo_id = p.id AND b.backend = ?
                   WHERE (b.status IS NULL OR b.status != 'uploaded')
                   AND (vm.group_id IS NULL OR vg.review_status != 'confirmed'
                        OR vg.preferred_photo_id = p.id)
                   ORDER BY p.id""",
                (self.settings.storage_backend,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_backup(
        self,
        photo_id: int,
        object_key: str,
        status: str,
        etag: str | None = None,
        error: str | None = None,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO backups(photo_id, backend, object_key, etag, status, uploaded_at, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(photo_id, backend) DO UPDATE SET object_key = excluded.object_key,
                     etag = excluded.etag, status = excluded.status,
                     uploaded_at = excluded.uploaded_at, error = excluded.error""",
                (
                    photo_id,
                    self.settings.storage_backend,
                    object_key,
                    etag,
                    status,
                    datetime.now(timezone.utc).isoformat() if status == "uploaded" else None,
                    error,
                ),
            )
            connection.commit()

    def stats(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) photos, COALESCE(SUM(size_bytes), 0) bytes,
                   COALESCE(SUM(CASE WHEN b.status = 'uploaded' THEN 1 ELSE 0 END), 0) backed_up,
                   COALESCE(SUM(CASE WHEN vg.review_status = 'pending' THEN 1 ELSE 0 END), 0)
                       pending_variants,
                   COALESCE(SUM(CASE WHEN ms.status IN ('candidate', 'selected', 'placed')
                       THEN 1 ELSE 0 END), 0)
                       magazine_photos
                   FROM photos p
                   LEFT JOIN backups b ON b.photo_id = p.id AND b.backend = ?
                   LEFT JOIN variant_members vm ON vm.photo_id = p.id
                   LEFT JOIN variant_groups vg ON vg.id = vm.group_id
                   LEFT JOIN magazine_selections ms ON ms.photo_id = p.id""",
                (self.settings.storage_backend,),
            ).fetchone()
        return dict(row)
