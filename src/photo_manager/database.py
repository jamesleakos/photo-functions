from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    perceptual_hash TEXT,
    filename TEXT NOT NULL,
    extension TEXT NOT NULL,
    media_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    width INTEGER,
    height INTEGER,
    captured_at TEXT,
    make TEXT,
    model TEXT,
    lens_model TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY,
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    path TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    favorite INTEGER NOT NULL DEFAULT 0,
    available INTEGER NOT NULL DEFAULT 1,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_locations_photo ON locations(photo_id);
CREATE INDEX IF NOT EXISTS idx_locations_source ON locations(source);
CREATE INDEX IF NOT EXISTS idx_photos_captured ON photos(captured_at);

CREATE TABLE IF NOT EXISTS variant_groups (
    id INTEGER PRIMARY KEY,
    preferred_photo_id INTEGER NOT NULL REFERENCES photos(id),
    match_method TEXT NOT NULL,
    confidence REAL NOT NULL,
    review_status TEXT NOT NULL CHECK(review_status IN ('pending', 'confirmed', 'rejected')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TEXT
);

CREATE TABLE IF NOT EXISTS variant_members (
    group_id INTEGER NOT NULL REFERENCES variant_groups(id) ON DELETE CASCADE,
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    PRIMARY KEY(group_id, photo_id),
    UNIQUE(photo_id)
);

CREATE TABLE IF NOT EXISTS backups (
    id INTEGER PRIMARY KEY,
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    backend TEXT NOT NULL,
    object_key TEXT NOT NULL,
    etag TEXT,
    status TEXT NOT NULL CHECK(status IN ('uploaded', 'failed', 'missing')),
    uploaded_at TEXT,
    error TEXT,
    UNIQUE(photo_id, backend)
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS photo_tags (
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY(photo_id, tag_id)
);

CREATE TABLE IF NOT EXISTS magazine_selections (
    id INTEGER PRIMARY KEY,
    photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
    issue TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('candidate', 'selected', 'placed', 'rejected')),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(photo_id, issue)
);

CREATE INDEX IF NOT EXISTS idx_magazine_issue_status
ON magazine_selections(issue, status);
"""


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def snapshot(self, destination: Path | str) -> Path:
        """Create a consistent SQLite snapshot while the live catalog remains in use."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".creating")
        temporary.unlink(missing_ok=True)
        source = self.connect()
        target = sqlite3.connect(temporary)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        temporary.replace(destination)
        return destination

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
