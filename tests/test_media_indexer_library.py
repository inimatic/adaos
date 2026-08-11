import json
import sqlite3

from adaos.services import media_indexer_library


def test_media_indexer_resolver_accepts_payload_alias_root(monkeypatch, tmp_path):
    alias_root = tmp_path / "server" / "home" / "Video" / "share" / "!Ada" / "test"
    alias_root.mkdir(parents=True)
    clip = alias_root / "song.mp3"
    clip.write_bytes(b"fake")
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "schema": 1,
                "indexed_directory": "A:/Video/share/!Ada/test",
                "text_docs": [
                    {
                        "payload": {
                            "playback_id": "a" * 32,
                            "full_path": str(clip),
                            "mime_type": "audio/mpeg",
                        }
                    },
                ],
                "image_docs": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(media_indexer_library, "_metadata_candidates", lambda: [metadata_path])

    resolved, payload = media_indexer_library.resolve_media_indexer_content("a" * 32)
    resource = media_indexer_library.resolve_media_indexer_resource("a" * 32)

    assert resolved == clip.resolve()
    assert payload["mime_type"] == "audio/mpeg"
    assert payload["content_path"] == f"/api/node/media-indexer/content/{'a' * 32}"
    assert resource.source == "media_indexer"
    assert resource.to_public_dict()["routed_content_path"] == f"/media/media-indexer/content/{'a' * 32}"


def test_media_indexer_resolver_prefers_compact_playback_index(monkeypatch, tmp_path):
    media_root = tmp_path / "library"
    media_root.mkdir()
    clip = media_root / "clip.mp4"
    clip.write_bytes(b"fake")
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text("legacy metadata must not be parsed", encoding="utf-8")
    playback_path = tmp_path / media_indexer_library.MEDIA_INDEXER_PLAYBACK_INDEX
    connection = sqlite3.connect(playback_path)
    try:
        connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute(
            "CREATE TABLE items (playback_id TEXT PRIMARY KEY, name TEXT NOT NULL, full_path TEXT NOT NULL, payload_json TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            ("indexed_directory", str(media_root)),
        )
        payload = {
            "playback_id": "b" * 32,
            "full_path": str(clip),
            "mime_type": "video/mp4",
        }
        connection.execute(
            "INSERT INTO items(playback_id, name, full_path, payload_json) VALUES (?, ?, ?, ?)",
            (payload["playback_id"], clip.name, str(clip), json.dumps(payload)),
        )
        connection.commit()
    finally:
        connection.close()
    monkeypatch.setattr(media_indexer_library, "_metadata_candidates", lambda: [metadata_path])

    resolved, payload = media_indexer_library.resolve_media_indexer_content("b" * 32)
    resource = media_indexer_library.resolve_media_indexer_resource("b" * 32)

    assert resolved == clip.resolve()
    assert payload["mime_type"] == "video/mp4"
    assert resource.id == "b" * 32
    assert resource.content_path == f"/api/node/media-indexer/content/{'b' * 32}"
