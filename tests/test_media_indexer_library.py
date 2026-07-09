import json

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

    assert resolved == clip.resolve()
    assert payload["mime_type"] == "audio/mpeg"
