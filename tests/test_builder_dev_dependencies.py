from pathlib import Path
from types import SimpleNamespace

from adaos.services.builder import dev_dependencies


class _Manager:
    def __init__(self) -> None:
        self.activated: list[str] = []

    def activate_dev_runtime(self, name: str) -> str:
        self.activated.append(name)
        return "B"

    def dev_runtime_status(self, name: str) -> dict:
        return {
            "ready": True,
            "version": "1.2.3",
            "active_slot": "B",
        }


def _context(tmp_path: Path) -> SimpleNamespace:
    skills = tmp_path / "skills"
    skills.mkdir()
    workspace_skills = tmp_path / "workspace-skills"
    workspace_skills.mkdir()
    return SimpleNamespace(
        paths=SimpleNamespace(
            dev_skills_dir=lambda: skills,
            skills_dir=lambda: workspace_skills,
        )
    )


def _skill(root: Path, name: str) -> None:
    target = root / name
    target.mkdir()
    (target / "skill.yaml").write_text(f"name: {name}\n", encoding="utf-8")


def test_dev_preflight_mounts_declared_skills_without_opening_mock_provider(
    monkeypatch, tmp_path: Path
) -> None:
    ctx = _context(tmp_path)
    _skill(ctx.paths.dev_skills_dir(), "photo_skill")
    manager = _Manager()
    monkeypatch.setattr(dev_dependencies, "get_ctx", lambda: ctx)
    monkeypatch.setattr(dev_dependencies, "_skill_manager", lambda: manager)
    monkeypatch.setattr(
        dev_dependencies,
        "_scenario_manifest",
        lambda _scenario: {"depends": ["photo_skill"]},
    )
    monkeypatch.setattr(
        dev_dependencies,
        "selected_data_binding",
        lambda _scenario, **_kwargs: {
            "profile_id": "mock-a",
            "mode": "mock",
            "adapter": "prototype_data",
            "external_provider_selected": False,
            "provider_opened": False,
            "ready": True,
            "implementation_mappings": [],
            "fingerprint": "sha256:mock",
        },
    )

    result = dev_dependencies.prepare_dev_prototype_dependencies(
        "photo", webspace_id="desktop-dev"
    )

    assert result["ok"] is True
    assert manager.activated == ["photo_skill"]
    assert result["provider_opened"] is False
    assert result["data_binding"]["mode"] == "mock"


def test_dev_preflight_mounts_exact_skill_backed_cbs_but_does_not_use_it(
    monkeypatch, tmp_path: Path
) -> None:
    ctx = _context(tmp_path)
    for name in ("photo_skill", "drive_provider_skill"):
        _skill(ctx.paths.dev_skills_dir(), name)
    manager = _Manager()
    monkeypatch.setattr(dev_dependencies, "get_ctx", lambda: ctx)
    monkeypatch.setattr(dev_dependencies, "_skill_manager", lambda: manager)
    monkeypatch.setattr(
        dev_dependencies,
        "_scenario_manifest",
        lambda _scenario: {"depends": ["photo_skill"]},
    )
    monkeypatch.setattr(
        dev_dependencies,
        "selected_data_binding",
        lambda _scenario, **_kwargs: {
            "profile_id": "drive-readonly",
            "mode": "live_readonly",
            "adapter": "exact_cbs_mapping",
            "external_provider_selected": True,
            "provider_opened": False,
            "ready": True,
            "implementation_mappings": [
                {
                    "logical_ref": "photos.read",
                    "implementation_ref": "skill:drive_provider_skill.read_items@2.0.0",
                    "status": "mapped",
                }
            ],
            "fingerprint": "sha256:drive",
        },
    )

    result = dev_dependencies.prepare_dev_prototype_dependencies(
        "photo", webspace_id="desktop-dev"
    )

    assert result["ok"] is True
    assert manager.activated == ["photo_skill", "drive_provider_skill"]
    assert result["provider_opened"] is False
    assert result["data_binding"]["adapter"] == "exact_cbs_mapping"


def test_data_binding_fingerprint_changes_with_exact_provider(monkeypatch) -> None:
    from adaos.services.builder import workflow

    base = {
        "selected_profile_id": "drive",
        "selected_mode": "live_readonly",
        "generation": 4,
        "profiles": [
            {
                "profile_id": "drive",
                "mode": "live_readonly",
                "logical_schema_ref": "schema:photos",
                "source_ref": "provider-account:drive/a",
                "capabilities": ["photos.read"],
                "read_policy": "scoped_live",
                "write_policy": "none",
                "implementation_mappings": [
                    {
                        "logical_ref": "photos.read",
                        "implementation_ref": "skill:adaos_drive.read",
                        "status": "mapped",
                    }
                ],
            }
        ],
    }

    class Service:
        @classmethod
        def from_context(cls):
            return cls()

        def describe(self, *_args):
            return {"data_binding": base}

    monkeypatch.setattr(workflow, "BuilderWorkflowService", Service)
    first = dev_dependencies.selected_data_binding("photos")
    base["profiles"][0]["source_ref"] = "provider-account:drive/b"
    second = dev_dependencies.selected_data_binding("photos")

    assert first["source_ref"] == "provider-account:drive/a"
    assert second["source_ref"] == "provider-account:drive/b"
    assert first["fingerprint"] != second["fingerprint"]


def test_webspace_profile_selection_overrides_project_default(monkeypatch) -> None:
    from adaos.services.builder import preview_data_profiles, workflow

    binding = {
        "selected_profile_id": "mock",
        "selected_mode": "mock",
        "generation": 7,
        "profiles": [
            {
                "profile_id": "mock",
                "mode": "mock",
                "logical_schema_ref": "schema:photos",
                "source_ref": "fixture:photos",
                "capabilities": [],
                "implementation_mappings": [],
            },
            {
                "profile_id": "drive",
                "mode": "live_readonly",
                "logical_schema_ref": "schema:photos",
                "source_ref": "provider-account:drive/a",
                "capabilities": ["photos.read"],
                "read_policy": "scoped_live",
                "write_policy": "none",
                "implementation_mappings": [
                    {
                        "logical_ref": "photos.read",
                        "implementation_ref": "skill:adaos_drive.read",
                        "status": "mapped",
                    }
                ],
            },
        ],
    }

    class WorkflowService:
        @classmethod
        def from_context(cls):
            return cls()

        def describe(self, *_args):
            return {"data_binding": binding}

    class SelectionService:
        @classmethod
        def from_context(cls):
            return cls()

        def projection(self, scenario_id, *, webspace_id):
            assert (scenario_id, webspace_id) == ("photos", "desktop-dev")
            return {
                "profile_id": "drive",
                "generation": 3,
                "source_webspace_id": "desktop",
            }

    monkeypatch.setattr(workflow, "BuilderWorkflowService", WorkflowService)
    monkeypatch.setattr(
        preview_data_profiles,
        "BuilderPreviewDataProfileService",
        SelectionService,
    )

    result = dev_dependencies.selected_data_binding(
        "photos",
        webspace_id="desktop-dev",
    )

    assert result["profile_id"] == "drive"
    assert result["mode"] == "live_readonly"
    assert result["selection_generation"] == 3
    assert result["source_webspace_id"] == "desktop"


def test_incomplete_external_binding_fails_before_skill_activation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        dev_dependencies,
        "selected_data_binding",
        lambda _scenario, **_kwargs: {
            "profile_id": "broken",
            "mode": "live_readonly",
            "ready": False,
            "fingerprint": "sha256:broken",
        },
    )

    result = dev_dependencies.prepare_dev_prototype_dependencies(
        "photo", webspace_id="desktop-dev"
    )

    assert result["ok"] is False
    assert result["error"] == "builder_data_binding_incomplete"
