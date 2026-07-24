from pathlib import Path

import pytest

from photo_manager.catalog import Catalog
from photo_manager.database import Database
from photo_manager.metadata import PhotoMetadata


class FixedExtractor:
    def __init__(self, values: dict[str, PhotoMetadata]):
        self.values = values

    def extract(self, path: Path) -> PhotoMetadata:
        return self.values[path.name]


def metadata(width: int, height: int, phash: str, captured: str = "2026-07-01T12:00:00"):
    return PhotoMetadata(
        media_type="image/jpeg",
        width=width,
        height=height,
        captured_at=captured,
        perceptual_hash=phash,
    )


def test_exact_duplicate_has_one_photo_and_two_locations(tmp_path, settings):
    first = tmp_path / "one.jpg"
    second = tmp_path / "two.jpg"
    first.write_bytes(b"same photo bytes")
    second.write_bytes(first.read_bytes())
    extractor = FixedExtractor({"one.jpg": metadata(100, 100, "0" * 16)})
    catalog = Catalog(Database(settings.database_path), settings, extractor)

    assert catalog.ingest_file(first, "camera") == "added"
    assert catalog.ingest_file(second, "iphone", favorite=True) == "exact_duplicates"

    with catalog.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 2
    item = catalog.list_photos()[0]
    assert item["favorite"] == 1
    assert set(item["tags"].split(", ")) == {"favorite", "source:camera", "source:phone"}


def test_high_resolution_variant_becomes_confirmed_master(tmp_path, settings):
    phone = tmp_path / "DSC001_small.jpg"
    camera = tmp_path / "DSC001.jpg"
    phone.write_bytes(b"phone export")
    camera.write_bytes(b"full resolution camera original")
    extractor = FixedExtractor(
        {
            phone.name: metadata(1200, 800, "0123456789abcdef"),
            camera.name: metadata(6000, 4000, "0123456789abcdef"),
        }
    )
    catalog = Catalog(Database(settings.database_path), settings, extractor)

    assert catalog.ingest_file(phone, "iphone", favorite=True) == "added"
    assert catalog.ingest_file(camera, "camera") == "variants_confirmed"

    groups = catalog.list_variant_groups("confirmed")
    assert len(groups) == 1
    preferred = next(item for item in groups[0]["members"] if item["is_preferred"])
    assert preferred["filename"] == camera.name
    assert [item["filename"] for item in catalog.backup_candidates()] == [camera.name]
    gallery_names = {
        item["filename"] for item in catalog.list_photos(include_nonpreferred=False)
    }
    assert gallery_names == {camera.name}
    phone_source_names = {
        item["filename"]
        for item in catalog.list_photos(source=["phone"], include_nonpreferred=False)
    }
    assert phone_source_names == {camera.name}
    favorite_names = {item["filename"] for item in catalog.list_photos(favorite=True)}
    assert camera.name in favorite_names
    camera_item = next(item for item in catalog.list_photos() if item["filename"] == camera.name)
    assert set(camera_item["tags"].split(", ")) == {"favorite", "source:camera"}


def test_similar_photos_from_same_source_are_not_collapsed(tmp_path, settings):
    first = tmp_path / "DSC1001.jpg"
    second = tmp_path / "DSC1002.jpg"
    first.write_bytes(b"first camera frame")
    second.write_bytes(b"second camera frame")
    extractor = FixedExtractor(
        {
            first.name: metadata(6000, 4000, "0123456789abcdef"),
            second.name: metadata(6000, 4000, "0123456789abcdef", "2026-07-01T12:00:01"),
        }
    )
    catalog = Catalog(Database(settings.database_path), settings, extractor)

    assert catalog.ingest_file(first, "camera") == "added"
    assert catalog.ingest_file(second, "camera") == "added"
    assert catalog.list_variant_groups("confirmed") == []


def test_reused_temporary_path_preserves_old_provenance(tmp_path, settings):
    path = tmp_path / "export.jpg"
    path.write_bytes(b"first exported version")
    extractor = FixedExtractor({path.name: metadata(1200, 800, "0123456789abcdef")})
    catalog = Catalog(Database(settings.database_path), settings, extractor)

    assert catalog.ingest_file(path, "iphone-favorite", favorite=True) == "added"
    path.write_bytes(b"different content at the same temporary path")
    assert catalog.ingest_file(path, "iphone-favorite", favorite=True) == "added"

    with catalog.database.connect() as connection:
        locations = connection.execute(
            "SELECT path, available FROM locations ORDER BY id"
        ).fetchall()
    assert len(locations) == 2
    assert locations[0]["path"].startswith("replaced://")
    assert locations[0]["available"] == 0
    assert locations[1]["path"] == str(path.resolve())
    assert locations[1]["available"] == 1


def test_user_tags_preserve_automatic_source_and_favorite_tags(tmp_path, settings):
    photo = tmp_path / "favorite.jpg"
    photo.write_bytes(b"favorite bytes")
    extractor = FixedExtractor({photo.name: metadata(1200, 800, "0123456789abcdef")})
    catalog = Catalog(Database(settings.database_path), settings, extractor)
    catalog.ingest_file(photo, "iphone-favorite", favorite=True)

    catalog.set_tags(1, ["landscape", "source:fake", "not-favorite"])

    item = catalog.list_photos()[0]
    assert set(item["tags"].split(", ")) == {"favorite", "landscape", "source:phone"}
    assert item["user_tags"] == "landscape"


def test_ambiguous_variant_keeps_both_backup_eligible(tmp_path, settings):
    first = tmp_path / "IMG_1000.jpg"
    second = tmp_path / "IMG_1000_copy.jpg"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    extractor = FixedExtractor(
        {
            first.name: metadata(3000, 2000, "0000000000000000"),
            second.name: metadata(3000, 2000, "ffffffffffffffff", "2026-07-01T12:00:30"),
        }
    )
    catalog = Catalog(Database(settings.database_path), settings, extractor)
    catalog.ingest_file(first, "phone", favorite=True)

    assert catalog.ingest_file(second, "camera") == "variants_pending"
    assert len(catalog.backup_candidates()) == 2
    camera_item = next(item for item in catalog.list_photos() if item["filename"] == second.name)
    assert camera_item["favorite"] == 0
    assert "not-favorite" in camera_item["tags"]

    group = catalog.list_variant_groups("pending")[0]
    catalog.decide_variant_group(group["id"], "confirmed", group["members"][0]["id"])
    assert len(catalog.backup_candidates()) == 1
    camera_item = next(item for item in catalog.list_photos() if item["filename"] == second.name)
    assert camera_item["favorite"] == 1
    assert "favorite" in camera_item["tags"]


def test_magazine_selection_and_tags_round_trip(tmp_path, settings):
    photo = tmp_path / "cover.jpg"
    photo.write_bytes(b"cover")
    catalog = Catalog(
        Database(settings.database_path),
        settings,
        FixedExtractor({photo.name: metadata(5000, 3300, "abcdef0123456789")}),
    )
    catalog.ingest_file(photo, "camera")
    photo_id = catalog.list_photos()[0]["id"]

    catalog.set_magazine_selection(photo_id, "Autumn 2026", "selected", "Cover option")
    catalog.set_tags(photo_id, ["landscape", "cover", "landscape"])

    result = catalog.list_photos(issue="Autumn 2026")[0]
    assert result["magazine_status"] == "selected"
    assert set(result["tags"].split(", ")) == {
        "cover",
        "landscape",
        "not-favorite",
        "source:camera",
    }
    assert set(result["user_tags"].split(", ")) == {"cover", "landscape"}


def test_editorial_flags_and_filters_combine_across_dimensions(tmp_path, settings):
    camera = tmp_path / "camera.jpg"
    phone = tmp_path / "phone.jpg"
    later = tmp_path / "later.jpg"
    camera.write_bytes(b"camera editorial photo")
    phone.write_bytes(b"phone editorial photo")
    later.write_bytes(b"later unflagged photo")
    extractor = FixedExtractor(
        {
            camera.name: metadata(6000, 4000, "0000000000000000", "2024-02-10T09:00:00"),
            phone.name: metadata(3000, 2000, "5555555555555555", "2024-06-15T12:00:00"),
            later.name: metadata(6000, 4000, "aaaaaaaaaaaaaaaa", "2025-01-05T15:00:00"),
        }
    )
    catalog = Catalog(Database(settings.database_path), settings, extractor)
    catalog.ingest_file(camera, "camera")
    catalog.ingest_file(phone, "iphone-favorite", favorite=True)
    catalog.ingest_file(later, "camera")
    by_name = {item["filename"]: item for item in catalog.list_photos()}

    catalog.set_editorial_flag(by_name[camera.name]["id"], "flagship")
    catalog.set_editorial_flag(by_name[phone.name]["id"], "include")

    filtered = catalog.list_photos(
        source=["phone"],
        favorite=True,
        editorial_flags=["include", "candidate"],
        date_from="2024-01-01",
        date_to="2024-12-31",
    )
    assert [item["filename"] for item in filtered] == [phone.name]
    flag_or_unflagged = {
        item["filename"]
        for item in catalog.list_photos(editorial_flags=["flagship", "unflagged"])
    }
    assert flag_or_unflagged == {camera.name, later.name}
    both_sources = {
        item["filename"] for item in catalog.list_photos(source=["camera", "phone"])
    }
    assert both_sources == {camera.name, phone.name, later.name}
    assert {
        item["filename"]
        for item in catalog.list_photos(date_from="2024-01-01", date_to="2024-12-31")
    } == {camera.name, phone.name}

    catalog.set_editorial_flag(by_name[phone.name]["id"], None)
    assert catalog.list_photos(editorial_flags=["include"]) == []
    assert {
        item["filename"] for item in catalog.list_photos(editorial_flags=["unflagged"])
    } == {phone.name, later.name}
    catalog.set_editorial_flag(by_name[later.name]["id"], "not_included")
    excluded = catalog.list_photos(editorial_flags=["not_included"])
    assert [item["filename"] for item in excluded] == [later.name]
    assert excluded[0]["editorial_flag"] == "not_included"
    with pytest.raises(ValueError):
        catalog.set_editorial_flag(by_name[camera.name]["id"], "maybe")
    with pytest.raises(KeyError):
        catalog.set_editorial_flag(9999, "candidate")


def test_one_of_groups_have_explicit_finish_boundaries(tmp_path, settings):
    paths = [tmp_path / f"choice-{index}.jpg" for index in range(3)]
    extractor = FixedExtractor(
        {
            path.name: metadata(
                6000,
                4000,
                f"{index:016x}",
                f"2024-03-0{index + 1}T10:00:00",
            )
            for index, path in enumerate(paths)
        }
    )
    for path in paths:
        path.write_bytes(path.name.encode())
    catalog = Catalog(Database(settings.database_path), settings, extractor)
    for path in paths:
        catalog.ingest_file(path, "camera")
    photo_ids = {
        item["filename"]: item["id"]
        for item in catalog.list_photos()
    }

    first = catalog.set_editorial_flag(photo_ids[paths[0].name], "one_of")
    second = catalog.set_editorial_flag(photo_ids[paths[1].name], "one_of")

    assert first["active"] is True
    assert first["member_count"] == 1
    assert second == {
        "active": True,
        "group_id": first["group_id"],
        "member_count": 2,
    }
    catalog.set_editorial_flag(photo_ids[paths[1].name], "candidate")
    assert catalog.current_one_of_group()["member_count"] == 1
    catalog.set_editorial_flag(photo_ids[paths[1].name], "one_of")

    finished = catalog.finish_current_one_of_group()

    assert finished == {
        "active": False,
        "group_id": first["group_id"],
        "member_count": 2,
    }
    assert catalog.current_one_of_group() == {
        "active": False,
        "group_id": None,
        "member_count": 0,
    }
    catalog.set_editorial_flag(photo_ids[paths[0].name], "include")
    next_group = catalog.set_editorial_flag(photo_ids[paths[2].name], "one_of")
    assert next_group["group_id"] != first["group_id"]
    assert next_group["member_count"] == 1
    catalog.set_editorial_flag(photo_ids[paths[2].name], None)
    assert catalog.current_one_of_group()["active"] is False
    with catalog.database.connect() as connection:
        historical_members = connection.execute(
            "SELECT COUNT(*) count FROM one_of_group_members WHERE group_id = ?",
            (first["group_id"],),
        ).fetchone()["count"]
    assert historical_members == 2


def test_photo_date_order_keeps_missing_capture_dates_last(tmp_path, settings):
    oldest = tmp_path / "oldest.jpg"
    newest = tmp_path / "newest.jpg"
    unknown = tmp_path / "unknown.jpg"
    oldest.write_bytes(b"oldest photo")
    newest.write_bytes(b"newest photo")
    unknown.write_bytes(b"photo with no embedded capture date")
    extractor = FixedExtractor(
        {
            oldest.name: metadata(6000, 4000, "0000000000000000", "2024-01-02T09:00:00"),
            newest.name: metadata(6000, 4000, "5555555555555555", "2025-12-30T17:00:00"),
            unknown.name: metadata(1200, 900, "aaaaaaaaaaaaaaaa", None),
        }
    )
    catalog = Catalog(Database(settings.database_path), settings, extractor)
    catalog.ingest_file(oldest, "camera")
    catalog.ingest_file(newest, "camera")
    catalog.ingest_file(unknown, "iphone-favorite", favorite=True)

    assert [item["filename"] for item in catalog.list_photos(date_order="desc")] == [
        newest.name,
        oldest.name,
        unknown.name,
    ]
    assert [item["filename"] for item in catalog.list_photos(date_order="asc")] == [
        oldest.name,
        newest.name,
        unknown.name,
    ]
    assert catalog.list_photos(date_from="2026-01-01") == []
    with pytest.raises(ValueError):
        catalog.list_photos(date_order="sideways")


def test_capture_dates_are_updated_atomically(tmp_path, settings):
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    first.write_bytes(b"first missing date")
    second.write_bytes(b"second missing date")
    extractor = FixedExtractor(
        {
            first.name: metadata(1200, 900, "0000000000000000", None),
            second.name: metadata(1200, 900, "5555555555555555", None),
        }
    )
    catalog = Catalog(Database(settings.database_path), settings, extractor)
    catalog.ingest_file(first, "iphone-favorite", favorite=True)
    catalog.ingest_file(second, "iphone-favorite", favorite=True)
    by_name = {item["filename"]: item for item in catalog.list_photos()}

    updated = catalog.set_capture_dates(
        {
            by_name[first.name]["id"]: "2024-01-02T09:30:00",
            by_name[second.name]["id"]: "2024-06-15T14:45:00",
        }
    )

    assert updated == 2
    assert {
        item["filename"]: item["captured_at"] for item in catalog.list_photos()
    } == {
        first.name: "2024-01-02T09:30:00",
        second.name: "2024-06-15T14:45:00",
    }
    with pytest.raises(ValueError):
        catalog.set_capture_dates({by_name[first.name]["id"]: "not-a-date"})
    with pytest.raises(KeyError):
        catalog.set_capture_dates(
            {
                by_name[first.name]["id"]: "2025-01-01T00:00:00",
                9999: "2025-01-01T00:00:00",
            }
        )
    assert catalog.get_photo(by_name[first.name]["id"])["captured_at"] == "2024-01-02T09:30:00"


def test_photo_and_video_filters_allow_either_or_both(tmp_path, settings):
    photo = tmp_path / "still.jpg"
    video = tmp_path / "clip.mov"
    photo.write_bytes(b"still image")
    video.write_bytes(b"moving image")
    extractor = FixedExtractor(
        {
            photo.name: metadata(3000, 2000, "1234567890abcdef"),
            video.name: PhotoMetadata(
                media_type="video/quicktime",
                captured_at="2026-07-02T12:00:00",
            ),
        }
    )
    catalog = Catalog(Database(settings.database_path), settings, extractor)
    catalog.ingest_file(photo, "camera")
    catalog.ingest_file(video, "camera")

    assert [item["filename"] for item in catalog.list_photos(media=["photo"])] == [
        photo.name
    ]
    assert [item["filename"] for item in catalog.list_photos(media=["video"])] == [
        video.name
    ]
    assert {
        item["filename"] for item in catalog.list_photos(media=["photo", "video"])
    } == {photo.name, video.name}
    with pytest.raises(ValueError):
        catalog.list_photos(media=["audio"])
