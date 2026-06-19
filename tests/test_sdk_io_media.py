from __future__ import annotations

from pathlib import Path

from PIL import Image


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
