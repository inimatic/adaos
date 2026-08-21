from __future__ import annotations

from types import SimpleNamespace

from adaos.services.media_delivery_activity import (
    begin_media_delivery,
    end_media_delivery,
    media_delivery_activity_snapshot,
    reset_media_delivery_activity_for_tests,
    touch_media_delivery,
)


def test_media_delivery_activity_tracks_kinds_and_releases_streams() -> None:
    reset_media_delivery_activity_for_tests()
    audio = begin_media_delivery(media_type="audio/mpeg", now=10.0)
    video = begin_media_delivery(media_type="video/mp4", now=10.0)
    touch_media_delivery(video, now=20.0)

    snapshot = media_delivery_activity_snapshot(now=30.0)

    assert snapshot["active"] is True
    assert snapshot["active_streams"] == 2
    assert snapshot["kind_counts"] == {"audio": 1, "video": 1, "other": 0}

    end_media_delivery(audio, now=31.0)
    end_media_delivery(video, now=31.0)
    assert media_delivery_activity_snapshot(now=31.0)["active"] is False


def test_media_delivery_activity_prunes_abandoned_streams() -> None:
    reset_media_delivery_activity_for_tests()
    begin_media_delivery(media_type="video/mp4", now=10.0)

    snapshot = media_delivery_activity_snapshot(now=131.0)

    assert snapshot["active"] is False
    assert snapshot["tracked_streams"] == 0


def test_node_media_range_releases_activity_when_iteration_finishes(monkeypatch) -> None:
    from adaos.apps.api import node_api

    events: list[object] = []
    monkeypatch.setattr(
        node_api,
        "begin_media_delivery",
        lambda **kwargs: events.append(("begin", kwargs["media_type"])) or "lease-1",
    )
    monkeypatch.setattr(
        node_api,
        "touch_media_delivery",
        lambda lease_id: events.append(("touch", lease_id)),
    )
    monkeypatch.setattr(
        node_api,
        "end_media_delivery",
        lambda lease_id: events.append(("end", lease_id)),
    )
    monkeypatch.setattr(
        node_api,
        "file_range_iter",
        lambda *args, **kwargs: iter((b"one", b"two")),
    )

    chunks = list(
        node_api._tracked_media_file_range(
            SimpleNamespace(mime_type="video/mp4", path="ignored"),
            start=0,
            end=5,
        )
    )

    assert chunks == [b"one", b"two"]
    assert events == [
        ("begin", "video/mp4"),
        ("touch", "lease-1"),
        ("touch", "lease-1"),
        ("end", "lease-1"),
    ]
