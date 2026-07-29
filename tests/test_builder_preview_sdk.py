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

    def set_selected_project(
        self,
        *,
        source_webspace_id,
        object_type,
        object_id,
        title,
        description,
        persist_projection,
    ):
        self.selection = {
            "object_type": object_type,
            "object_id": object_id,
            "title": title,
            "description": description,
        }
        return {"selection": dict(self.selection)}


def test_refresh_follow_active_target_updates_metadata_without_materializing(monkeypatch) -> None:
    service = _Workbench()
    monkeypatch.setattr(preview, "_service", lambda: service)

    result = preview.refresh_follow_active_target(
        "scenario",
        "recipes",
        revision="005",
        source_webspace_id="desktop",
        title="Кулинарные рецепты",
        description="Каталог рецептов",
    )

    assert result["ok"] is True
    assert result["materialization"] == "deferred"
    assert result["target"]["revision"] == "005"
    assert result["target"]["label"] == "proto: recipes · UI 005"
    assert result["binding"]["selection"]["title"] == "Кулинарные рецепты"
    assert result["selection"]["description"] == "Каталог рецептов"
    assert len(service.set_calls) == 1


def test_refresh_follow_active_target_preserves_explicit_snapshot(monkeypatch) -> None:
    service = _Workbench(follow_active=False)
    monkeypatch.setattr(preview, "_service", lambda: service)

    result = preview.refresh_follow_active_target(
        "scenario",
        "recipes",
        revision="005",
        source_webspace_id="desktop",
    )

    assert result["skipped"] == "preview_target_not_following_active"
    assert result["binding"]["preview_target"]["revision"] == "003"
    assert service.set_calls == []
