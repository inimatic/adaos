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
