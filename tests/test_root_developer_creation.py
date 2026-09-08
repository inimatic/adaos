from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.services.root import service as root_service
from adaos.services.root.service import RootDeveloperService, RootServiceError


def _service_for_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    service = object.__new__(RootDeveloperService)
    config = SimpleNamespace(
        node_id="node-test",
        node_settings=SimpleNamespace(id="node-test"),
    )
    workspace = tmp_path / "dev"
    template = tmp_path / "template"
    template.mkdir()

    service._load_config = lambda: config
    service._owner_workspace = lambda _config: ("owner-test", workspace)
    service._resolve_template = lambda _kind, _template: (template, "builder_scenario")
    service._update_manifest = lambda *_args, **_kwargs: {
        "version": "0.1.0",
        "updated_at": "2026-09-08T00:00:00Z",
    }

    def copy_template(_source: Path, target: Path) -> None:
        target.mkdir(parents=True)
        (target / "scenario.yaml").write_text(
            "id: new_scenario\nname: new_scenario\nversion: 0.1.0\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(root_service, "_copy_template", copy_template)
    monkeypatch.setattr(
        root_service, "_rewrite_scenario_template_identity", lambda *_args: None
    )
    monkeypatch.setattr(
        root_service, "_sync_scenario_content_metadata", lambda *_args: None
    )
    return service, workspace


def test_create_scenario_registers_dev_artifact_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, workspace = _service_for_creation(tmp_path, monkeypatch)
    calls: list[dict] = []

    def upsert(root: Path, kind: str, target: Path, **kwargs):
        calls.append(
            {
                "root": root,
                "kind": kind,
                "target": target,
                **kwargs,
            }
        )

    monkeypatch.setattr(root_service, "upsert_workspace_registry_entry", upsert)

    result = service._create_artifact("scenarios", "new_scenario", template=None)

    assert result.path == workspace / "scenarios" / "new_scenario"
    assert calls == [
        {
            "root": workspace,
            "kind": "scenarios",
            "target": result.path,
            "version": "0.1.0",
            "updated_at": "2026-09-08T00:00:00Z",
            "extra": {
                "publisher": {
                    "owner_id": "owner-test",
                    "node_id": "node-test",
                }
            },
        }
    ]


def test_create_scenario_rolls_back_when_dev_registry_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, workspace = _service_for_creation(tmp_path, monkeypatch)
    monkeypatch.setattr(
        root_service,
        "upsert_workspace_registry_entry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("registry unavailable")
        ),
    )

    with pytest.raises(
        RootServiceError, match="Failed to register newly created scenario"
    ):
        service._create_artifact("scenarios", "new_scenario", template=None)

    assert not (workspace / "scenarios" / "new_scenario").exists()
