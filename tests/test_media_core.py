from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from adaos.services import media_core


def test_media_core_builds_media_resource_descriptor(tmp_path: Path) -> None:
    media_file = tmp_path / "clip.mp3"
    media_file.write_bytes(b"abcdef")

    resource = media_core.media_resource_from_path(
        media_file,
        source="media_indexer",
        resource_id="a" * 32,
        playback_id="a" * 32,
        mime_type="audio/mpeg",
    )
    public = resource.to_public_dict()

    assert public["schema"] == media_core.MEDIA_RESOURCE_SCHEMA
    assert public["id"] == "a" * 32
    assert public["source"] == "media_indexer"
    assert public["name"] == "clip.mp3"
    assert public["mime_type"] == "audio/mpeg"
    assert public["content_path"] == f"/api/node/media-indexer/content/{'a' * 32}"
    assert public["routed_content_path"] == f"/media/media-indexer/content/{'a' * 32}"


def test_media_core_content_paths_validate_source_contract() -> None:
    assert media_core.media_resource_content_path("clip.mp4", source="media_server") == "/media/files/content/clip.mp4"
    assert (
        media_core.media_resource_content_path("b" * 32, source="media_indexer", browser=False)
        == f"/api/node/media-indexer/content/{'b' * 32}"
    )
    assert (
        media_core.media_resource_content_path("ref_clip", source="media_reference", browser=False)
        == "/api/node/media/resources/content/ref_clip"
    )
    with pytest.raises(ValueError, match="unsupported_media_source"):
        media_core.media_resource_content_path("clip.mp4", source="catalog")


def test_inline_content_disposition_encodes_unicode_filename_for_http_headers() -> None:
    value = media_core.inline_content_disposition("Фильм 01.mp4")

    assert value.startswith('inline; filename="01.mp4"; filename*=UTF-8\'\'')
    assert "%D0%A4%D0%B8%D0%BB%D1%8C%D0%BC%2001.mp4" in value
    value.encode("latin-1")


def test_media_core_range_parser_matches_http_range_semantics() -> None:
    assert media_core.parse_media_range(None, size=10) is None
    assert media_core.parse_media_range("bytes=2-5", size=10) == (2, 5)
    assert media_core.parse_media_range("bytes=4-", size=10) == (4, 9)
    assert media_core.parse_media_range("bytes=-3", size=10) == (7, 9)

    with pytest.raises(ValueError):
        media_core.parse_media_range("bytes=11-12", size=10)
    with pytest.raises(ValueError):
        media_core.parse_media_range("bytes=1-2,4-5", size=10)


def test_media_reference_serves_original_file_without_copying(tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    source = library / "clip.mp4"
    source.write_bytes(b"original-media-bytes")
    db_path = tmp_path / "state" / "media_references.sqlite3"

    resource = media_core.register_media_reference(
        source,
        root=library,
        content_ref="library:clip.mp4",
        namespace="media-center",
        db_path=db_path,
    )
    resolved = media_core.resolve_media_reference(resource.id, db_path=db_path)

    assert resource.path == source.resolve()
    assert resolved.path == source.resolve()
    assert resolved.path.read_bytes() == b"original-media-bytes"
    assert resolved.content_path == f"/api/node/media/resources/content/{resource.id}"
    assert resolved.routed_content_path == f"/media/resources/content/{resource.id}"
    assert resolved.metadata["storage_mode"] == "reference"
    assert list(tmp_path.rglob("*.mp4")) == [source]


def test_media_reference_revalidates_root_boundary_on_read(tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    source = library / "clip.mp4"
    source.write_bytes(b"inside")
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside")
    db_path = tmp_path / "state" / "media_references.sqlite3"
    resource = media_core.register_media_reference(source, root=library, db_path=db_path)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE media_references SET source_path = ? WHERE resource_id = ?",
            (str(outside), resource.id),
        )
        connection.commit()

    with pytest.raises(PermissionError, match="path_outside_media_reference_root"):
        media_core.resolve_media_reference(resource.id, db_path=db_path)


def test_media_reference_prefers_mapped_drive_alias_for_unc_path(tmp_path: Path) -> None:
    mapped_root = tmp_path / "mapped"
    target = mapped_root / "Video" / "clip.avi"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"RIFF")

    resolved = media_core._prefer_mapped_drive_path(
        r"\\server\home\Video\clip.avi",
        mappings=((r"\\server\home", str(mapped_root)),),
    )

    assert resolved == target


def test_media_reference_resolves_existing_unc_rows_through_mapped_alias(monkeypatch, tmp_path: Path) -> None:
    mapped_root = tmp_path / "mapped"
    target = mapped_root / "Video" / "clip.avi"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"RIFF")
    monkeypatch.setattr(
        media_core,
        "_windows_mapped_drive_roots",
        lambda: ((r"\\server\home", str(mapped_root)),),
    )

    resolved, root = media_core._resolve_media_reference_target(
        r"\\server\home\Video\clip.avi",
        r"\\server\home\Video",
    )

    assert resolved == target.resolve()
    assert root == target.parent.resolve()


def test_unregister_media_references_is_exact_idempotent_and_preserves_files(tmp_path: Path) -> None:
    from adaos.services.media_reference_registry import unregister_media_references

    library = tmp_path / "library"
    library.mkdir()
    first = library / "first.mp4"
    second = library / "second.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    db_path = tmp_path / "state" / "media_references.sqlite3"
    first_ref = media_core.register_media_reference(first, root=library, db_path=db_path)
    second_ref = media_core.register_media_reference(second, root=library, db_path=db_path)

    removed = unregister_media_references(
        [first_ref.id, first_ref.id, "ref_missing"],
        db_path=db_path,
    )

    assert removed == {
        "ok": True,
        "requested_count": 2,
        "deleted_count": 1,
        "missing_count": 1,
        "resource_ids": [first_ref.id, "ref_missing"],
    }
    with pytest.raises(FileNotFoundError, match="media_reference_not_found"):
        media_core.resolve_media_reference(first_ref.id, db_path=db_path)
    assert media_core.resolve_media_reference(second_ref.id, db_path=db_path).path == second.resolve()
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"
