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


def test_existing_one_of_flags_become_an_open_group(tmp_path):
    database = Database(tmp_path / "catalog.db")
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO photos(
                   sha256, filename, extension, media_type, size_bytes
               ) VALUES (?, ?, ?, ?, ?)""",
            ("b" * 64, "legacy-one-of.jpg", ".jpg", "image/jpeg", 84),
        )
        connection.execute(
            "INSERT INTO editorial_flags(photo_id, flag) VALUES (1, 'one_of')"
        )
        connection.executescript(
            """
            DROP INDEX idx_one_of_group_members_photo;
            DROP TABLE one_of_group_members;
            DROP INDEX idx_one_of_groups_single_open;
            DROP TABLE one_of_groups;
            """
        )

    database.initialize()

    with database.connect() as connection:
        group = connection.execute(
            """SELECT g.status, COUNT(m.photo_id) member_count
               FROM one_of_groups g
               JOIN one_of_group_members m ON m.group_id = g.id
               GROUP BY g.id"""
        ).fetchone()
    assert group["status"] == "open"
    assert group["member_count"] == 1
