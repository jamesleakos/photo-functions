from photo_manager.database import Database


def test_editorial_flag_constraint_migrates_existing_catalog(tmp_path):
    database = Database(tmp_path / "catalog.db")
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO photos(
                   sha256, filename, extension, media_type, size_bytes
               ) VALUES (?, ?, ?, ?, ?)""",
            ("a" * 64, "existing.jpg", ".jpg", "image/jpeg", 42),
        )
        connection.executescript(
            """
            DROP INDEX idx_editorial_flags_flag;
            DROP TABLE editorial_flags;
            CREATE TABLE editorial_flags (
                photo_id INTEGER PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,
                flag TEXT NOT NULL CHECK(
                    flag IN ('flagship', 'include', 'candidate', 'one_of')
                ),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX idx_editorial_flags_flag ON editorial_flags(flag);
            INSERT INTO editorial_flags(photo_id, flag) VALUES (1, 'flagship');
            """
        )

    database.initialize()

    with database.connect() as connection:
        existing = connection.execute(
            "SELECT flag FROM editorial_flags WHERE photo_id = 1"
        ).fetchone()
        connection.execute(
            "UPDATE editorial_flags SET flag = 'not_included' WHERE photo_id = 1"
        )
        migrated = connection.execute(
            "SELECT flag FROM editorial_flags WHERE photo_id = 1"
        ).fetchone()
    assert existing["flag"] == "flagship"
    assert migrated["flag"] == "not_included"
