from __future__ import annotations

from adaos.sdk.builder import preview


class _Workbench:
    def __init__(self, *, follow_active: bool = True) -> None:
        self.target = {
            "schema": "adaos.builder.preview_target.v1",
            "object_type": "scenario",
            "object_id": "recipes",
            "stage": "prototype",
            "revision": "003",
            "label": "proto: recipes · UI 003",
            "follow_active": follow_active,
        }
        self.selection = {"object_id": "recipes", "title": "Old title"}
        self.set_calls: list[dict[str, object]] = []

    def resolve_source_webspace_id(self, value):
        return value or "desktop"

    def get_workspace_binding(self, _source):
        return {"preview_target": dict(self.target), "selection": dict(self.selection)}

    def set_preview_target(self, *, source_webspace_id, target):
        self.set_calls.append({"source_webspace_id": source_webspace_id, "target": dict(target)})
        self.target = dict(target)
        return {"preview_target": dict(self.target), "selection": dict(self.selection)}


def test_refresh_follow_active_target_updates_metadata_without_materializing(monkeypatch) -> None:
    service = _Workbench()
    selections: list[dict[str, object]] = []
    monkeypatch.setattr(preview, "_service", lambda: service)

    def _select_project(object_type, object_id, **kwargs):
        selections.append({"object_type": object_type, "object_id": object_id, **kwargs})
        service.selection = {"object_id": object_id, "title": "Кулинарные рецепты"}
        return {"ok": True, "binding": {"selection": dict(service.selection)}}

    monkeypatch.setattr(preview, "select_project", _select_project)

    result = preview.refresh_follow_active_target(
        "scenario",
        "recipes",
        revision="005",
        source_webspace_id="desktop",
    )

    assert result["ok"] is True
    assert result["materialization"] == "deferred"
    assert result["target"]["revision"] == "005"
    assert result["target"]["label"] == "proto: recipes · UI 005"
    assert result["binding"]["selection"]["title"] == "Кулинарные рецепты"
    assert selections == [
        {
            "object_type": "scenario",
            "object_id": "recipes",
            "source_webspace_id": "desktop",
            "ensure_ready": False,
            "wait_for_rebuild": False,
            "publish_event": False,
        }
    ]
    assert len(service.set_calls) == 1


def test_refresh_follow_active_target_preserves_explicit_snapshot(monkeypatch) -> None:
    service = _Workbench(follow_active=False)
    monkeypatch.setattr(preview, "_service", lambda: service)
    monkeypatch.setattr(
        preview,
        "select_project",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("selection must not move")),
    )

    result = preview.refresh_follow_active_target(
        "scenario",
        "recipes",
        revision="005",
        source_webspace_id="desktop",
    )

    assert result["skipped"] == "preview_target_not_following_active"
    assert result["binding"]["preview_target"]["revision"] == "003"
    assert service.set_calls == []
