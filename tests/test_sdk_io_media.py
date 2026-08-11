from __future__ import annotations

from pathlib import Path

import pytest


Image = pytest.importorskip("PIL.Image", reason="SDK image variants require the optional Pillow dependency")


def test_sdk_io_media_creates_cached_variant_and_publish_descriptor(monkeypatch, tmp_path):
    from adaos.sdk.io import media as sdk_media

    source = tmp_path / "source.jpg"
    Image.new("RGB", (2400, 1200), color=(64, 128, 192)).save(source, "JPEG", quality=92)
    media_store = tmp_path / "media"
    media_store.mkdir()
    monkeypatch.setattr(sdk_media, "media_file_path", lambda filename: media_store / filename)

    variant, cached = sdk_media.cached_image_variant(
        source,
        max_size=(1280, 720),
        label="fullscreen-test",
        quality=84,
    )
    second, second_cached = sdk_media.cached_image_variant(
        source,
        max_size=(1280, 720),
        label="fullscreen-test",
        quality=84,
    )
    descriptor = sdk_media.publish_media_file(
        variant,
        content_ref="content:demo",
        namespace="demo",
        variant="fullscreen",
        api_token="token",
    )

    assert variant.parent == source.parent / ".adaos-thumbs"
    assert cached is False
    assert second == variant
    assert second_cached is True
    assert descriptor["ok"] is True
    assert descriptor["schema"] == "adaos.media.resource.v1"
    assert descriptor["source"] == "media_server"
    assert descriptor["id"] == descriptor["filename"]
    assert descriptor["browser_route"] == "hub_browser_media"
    assert descriptor["browser_path"].startswith("/media/files/content/")
    assert descriptor["node_url"].startswith("/api/node/media/files/content/")
    assert Path(descriptor["path"]).exists()


def test_sdk_io_media_advertises_endpoint_direct_urls(monkeypatch, tmp_path):
    from adaos.sdk.io import media as sdk_media

    source = tmp_path / "source.jpg"
    Image.new("RGB", (320, 180), color=(64, 128, 192)).save(source, "JPEG", quality=82)
    media_store = tmp_path / "media"
    media_store.mkdir()
    monkeypatch.setattr(sdk_media, "media_file_path", lambda filename: media_store / filename)
    monkeypatch.setenv("ADAOS_REDEVICE_MEDIA_BASES", "http://192.168.0.30:8778")

    descriptor = sdk_media.publish_media_file(
        source,
        content_ref="content:direct",
        namespace="demo",
        variant="endpoint",
        api_token="token",
    )

    assert descriptor["direct_urls"]
    assert descriptor["direct_urls"][0].startswith("http://192.168.0.30:8778/api/node/media/files/content/")
    assert descriptor["content_url_candidates"][0] == descriptor["direct_urls"][0]
    assert descriptor["delivery"]["preferred_route"] == "hub_direct_http"


def test_sdk_io_media_publishes_without_agent_context(monkeypatch, tmp_path):
    from adaos.sdk.io import media as sdk_media

    source = tmp_path / "source.jpg"
    Image.new("RGB", (320, 180), color=(64, 128, 192)).save(source, "JPEG", quality=82)
    base_dir = tmp_path / ".adaos"
    media_runtime = base_dir / "workspace" / "skills" / ".runtime" / "mediaserver"
    media_runtime.mkdir(parents=True)
    (media_runtime / "current_version").write_text("0.8.0", encoding="utf-8")
    monkeypatch.setattr(sdk_media, "current_base_dir", lambda: base_dir)
    monkeypatch.setenv("ADAOS_REDEVICE_MEDIA_BASES", "http://192.168.0.30:7425")

    def no_agent_context(_filename: str):
        raise RuntimeError("AgentContext is not initialized. Call set_ctx(...) during app bootstrap.")

    monkeypatch.setattr(sdk_media, "media_file_path", no_agent_context)

    descriptor = sdk_media.publish_media_file(
        source,
        content_ref="content:no-agent",
        namespace="demo",
        variant="endpoint",
        api_token="token",
    )

    assert descriptor["ok"] is True
    assert descriptor["direct_urls"][0].startswith("http://192.168.0.30:7425/api/node/media/files/content/")
    assert Path(descriptor["path"]).is_file()
    assert ".runtime" in descriptor["path"]
    assert "mediaserver" in descriptor["path"]


def test_sdk_io_media_does_not_expand_implicit_loopback_base(monkeypatch, tmp_path):
    from adaos.sdk.io import media as sdk_media

    source = tmp_path / "source.jpg"
    Image.new("RGB", (320, 180), color=(64, 128, 192)).save(source, "JPEG", quality=82)
    media_store = tmp_path / "media"
    media_store.mkdir()
    monkeypatch.setattr(sdk_media, "media_file_path", lambda filename: media_store / filename)
    monkeypatch.setenv("ADAOS_SELF_BASE_URL", "http://127.0.0.1:8777")
    monkeypatch.delenv("ADAOS_REDEVICE_MEDIA_BASES", raising=False)
    monkeypatch.delenv("ADAOS_MEDIA_DIRECT_BASES", raising=False)
    monkeypatch.delenv("ADAOS_MEDIA_DIRECT_EXPAND_LOOPBACK", raising=False)
    monkeypatch.setattr(sdk_media, "_local_ipv4_addresses", lambda: ["192.168.0.30"])

    descriptor = sdk_media.publish_media_file(
        source,
        content_ref="content:loopback",
        namespace="demo",
        variant="endpoint",
        api_token="token",
    )

    assert descriptor["direct_urls"] == []
    assert all("192.168.0.30" not in item for item in descriptor["content_url_candidates"])
    assert descriptor["delivery"]["preferred_route"] == "node_media_file"


def test_sdk_io_media_can_check_cached_variant_without_creating(tmp_path):
    from adaos.sdk.io import media as sdk_media

    source = tmp_path / "source.jpg"
    Image.new("RGB", (2400, 1200), color=(64, 128, 192)).save(source, "JPEG", quality=92)

    variant, cached = sdk_media.cached_image_variant(
        source,
        max_size=(3840, 2160),
        label="fullscreen-test",
        quality=88,
        create=False,
    )

    assert cached is False
    assert variant.parent == source.parent / ".adaos-thumbs"
    assert not variant.exists()


def test_sdk_io_media_exposes_resource_descriptor_helpers():
    from adaos.sdk.io import media as sdk_media

    descriptor = sdk_media.media_resource_descriptor(
        resource_id="clip-1",
        source="media_center",
        name="clip.mp4",
        mime_type="video/mp4",
        size_bytes=42,
        content_path="/api/node/media/files/content/clip.mp4",
    )

    assert descriptor["schema"] == "adaos.media.resource.v1"
    assert descriptor["resource_id"] == "clip-1"
    assert descriptor["source"] == "media_center"
    assert sdk_media.media_resource_content_path("clip.mp4", source="media_server") == "/media/files/content/clip.mp4"
    assert (
        sdk_media.media_indexer_content_path("c" * 32, browser=False)
        == f"/api/node/media-indexer/content/{'c' * 32}"
    )


def test_sdk_io_media_lists_normalized_resources(monkeypatch, tmp_path):
    from adaos.sdk.io import media as sdk_media
    from adaos.services import media_core, media_indexer_library

    server_clip = tmp_path / "server.mp4"
    indexer_song = tmp_path / "indexer.mp3"
    server_clip.write_bytes(b"server")
    indexer_song.write_bytes(b"indexer")
    server_resource = media_core.media_resource_from_path(
        server_clip,
        source="media_server",
        resource_id=server_clip.name,
    )
    indexer_resource = media_core.media_resource_from_path(
        indexer_song,
        source="media_indexer",
        resource_id="e" * 32,
        playback_id="e" * 32,
    )
    monkeypatch.setattr(sdk_media, "iter_media_store_resources", lambda: iter([server_resource]))
    monkeypatch.setattr(media_indexer_library, "iter_media_indexer_resources", lambda: iter([indexer_resource]))

    items = sdk_media.list_media_resources(source="all")

    assert {item["source"] for item in items} == {"media_server", "media_indexer"}
    assert all(item["schema"] == "adaos.media.resource.v1" for item in items)
    assert sdk_media.list_media_resources(source="media_store")[0]["source"] == "media_server"
    assert sdk_media.list_media_resources(source="media_indexer", limit=1)[0]["resource_id"] == "e" * 32
    with pytest.raises(ValueError, match="unsupported_media_source"):
        sdk_media.list_media_resources(source="catalog")
