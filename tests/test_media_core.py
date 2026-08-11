from __future__ import annotations

from pathlib import Path

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
    with pytest.raises(ValueError, match="unsupported_media_source"):
        media_core.media_resource_content_path("clip.mp4", source="catalog")


def test_media_core_range_parser_matches_http_range_semantics() -> None:
    assert media_core.parse_media_range(None, size=10) is None
    assert media_core.parse_media_range("bytes=2-5", size=10) == (2, 5)
    assert media_core.parse_media_range("bytes=4-", size=10) == (4, 9)
    assert media_core.parse_media_range("bytes=-3", size=10) == (7, 9)

    with pytest.raises(ValueError):
        media_core.parse_media_range("bytes=11-12", size=10)
    with pytest.raises(ValueError):
        media_core.parse_media_range("bytes=1-2,4-5", size=10)
