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
