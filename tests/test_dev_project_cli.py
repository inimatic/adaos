from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from adaos.apps.cli.commands import dev_project


def _source() -> dict[str, object]:
    return {
        "source_revision": "sha256:" + "a" * 64,
        "file_count": 3,
        "size_bytes": 42,
        "components": [{"ref": "skill:kanban_skill"}],
    }


def _release() -> dict[str, object]:
    return {
        "project_id": "kanban",
        "version": "0.1.0",
        "release_digest": "sha256:" + "b" * 64,
        "packages": [],
    }


def test_dev_project_list_uses_project_composition_registry(monkeypatch) -> None:
    monkeypatch.setattr(
        dev_project,
        "_list_project_records",
        lambda service, profile=None, limit=500: [
            {"id": "kanban", "version": "0.1.0", "stage": "alpha"}
        ],
    )
    monkeypatch.setattr(dev_project, "_service", lambda: SimpleNamespace())

    result = CliRunner().invoke(dev_project.app, ["list", "--json"])

    assert result.exit_code == 0, result.output
    assert '"id": "kanban"' in result.output


def test_dev_project_create_can_adopt_existing_primary_component(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def create_for_existing_component(project_id, **kwargs):
        calls.append({"project_id": project_id, **kwargs})
        return {
            "ok": True,
            "project": {"id": project_id, "version": "0.1.0"},
            "created_component": False,
        }

    monkeypatch.setattr(
        dev_project.compositions,
        "create_for_existing_component",
        create_for_existing_component,
    )

    result = CliRunner().invoke(
        dev_project.app,
        [
            "create",
            "kanban",
            "--primary-kind",
            "scenario",
            "--primary-id",
            "kanban_ui",
            "--existing",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["project_id"] == "kanban"
    assert calls[0]["component_id"] == "kanban_ui"
    assert calls[0]["entrypoints"][0]["presentation"] == "scenario:kanban_ui"
    assert '"created_component": false' in result.output


def test_dev_project_attach_uses_project_composition_authority(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def ensure_owned_component(project_id, component_ref, **kwargs):
        calls.append(
            {
                "project_id": project_id,
                "component_ref": component_ref,
                **kwargs,
            }
        )
        return {
            "ok": True,
            "idempotent": False,
            "project": {"id": project_id},
        }

    monkeypatch.setattr(
        dev_project.compositions,
        "ensure_owned_component",
        ensure_owned_component,
    )

    result = CliRunner().invoke(
        dev_project.app,
        [
            "attach",
            "builder",
            "skill:builder_skill",
            "--role",
            "implementation",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        {
            "project_id": "builder",
            "component_ref": "skill:builder_skill",
            "role": "implementation",
            "exposure": "project_only",
            "lifecycle": "bound",
        }
    ]
    assert '"idempotent": false' in result.output


def test_dev_project_fork_materializes_complete_workspace_project(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class Service:
        def create_project_local_fork(self, project_id: str, **kwargs):
            calls.append({"project_id": project_id, **kwargs})
            return {
                "ok": True,
                "status": "materialized",
                "project_id": project_id,
                "components": [{"kind": "scenario", "name": "kanban"}],
            }

    monkeypatch.setattr(dev_project, "_service", Service)

    result = CliRunner().invoke(
        dev_project.app,
        ["fork", "kanban", "--actor", "codex:e2e", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert calls == [{"project_id": "kanban", "actor": "codex:e2e", "refresh": False}]
    assert '"status": "materialized"' in result.output


def test_dev_project_fork_can_refresh_divergent_source(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class Service:
        def create_project_local_fork(self, project_id: str, **kwargs):
            calls.append({"project_id": project_id, **kwargs})
            return {
                "ok": True,
                "status": "materialized",
                "project_id": project_id,
                "strategy": "refresh_local_fork",
            }

    monkeypatch.setattr(dev_project, "_service", Service)

    result = CliRunner().invoke(
        dev_project.app,
        ["fork", "kanban", "--actor", "codex:e2e", "--refresh", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert calls == [{"project_id": "kanban", "actor": "codex:e2e", "refresh": True}]
    assert '"strategy": "refresh_local_fork"' in result.output


def test_dev_project_depend_declares_shared_dependency(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def ensure_dependency(project_id, dependency_ref, **kwargs):
        calls.append(
            {
                "project_id": project_id,
                "dependency_ref": dependency_ref,
                **kwargs,
            }
        )
        return {
            "ok": True,
            "idempotent": False,
            "project": {"id": project_id},
        }

    monkeypatch.setattr(
        dev_project.compositions,
        "ensure_dependency",
        ensure_dependency,
    )

    result = CliRunner().invoke(
        dev_project.app,
        [
            "depend",
            "builder",
            "skill:voice_chat_skill",
            "--version",
            "^0.1",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        {
            "project_id": "builder",
            "dependency_ref": "skill:voice_chat_skill",
            "version": "^0.1",
        }
    ]


def test_dev_project_push_uses_content_revision_and_can_stay_local(
    monkeypatch,
    tmp_path,
) -> None:
    installed_workspace = tmp_path / "installed-workspace"
    service = SimpleNamespace(workspace_root=installed_workspace)
    captured: dict[str, object] = {}
    monkeypatch.setattr(dev_project, "_service", lambda: service)
    monkeypatch.setattr(
        dev_project,
        "_project",
        lambda project_id: {"id": project_id, "stage": "alpha"},
    )
    monkeypatch.setattr(dev_project, "_snapshot", lambda *_: _source())
    monkeypatch.setattr(dev_project, "_dev_workspace_root", lambda *_: tmp_path)
    monkeypatch.setattr(
        dev_project,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                subnet_id="sn_test",
                node_id="node_test",
                node_settings=SimpleNamespace(id="node_test"),
            )
        ),
    )

    def build(project_id, **kwargs):
        captured.update({"project_id": project_id, **kwargs})
        return _release()

    monkeypatch.setattr(dev_project.project_cli, "_build_project_release", build)
    monkeypatch.setattr(
        dev_project.project_cli,
        "_publish_project_release",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("local-only push must not publish")
        ),
    )

    result = CliRunner().invoke(
        dev_project.app,
        ["push", "kanban", "--bump", "none", "--local-only", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert captured["revision"] == _source()["source_revision"]
    assert captured["workspace_root"] == tmp_path
    assert captured["lock_workspace_root"] == installed_workspace
    assert captured["forge"] == "content-addressed-dev"
    assert '"publication_stage": "alpha"' in result.output
    assert '"publication"' not in result.output


def test_dev_project_push_publishes_release_by_default(monkeypatch, tmp_path) -> None:
    service = SimpleNamespace()
    published: list[dict[str, object]] = []
    monkeypatch.setattr(dev_project, "_service", lambda: service)
    monkeypatch.setattr(
        dev_project,
        "_project",
        lambda project_id: {"id": project_id, "stage": "beta"},
    )
    monkeypatch.setattr(dev_project, "_snapshot", lambda *_: _source())
    monkeypatch.setattr(dev_project, "_dev_workspace_root", lambda *_: tmp_path)
    monkeypatch.setattr(
        dev_project,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                subnet_id="sn_test",
                node_settings=SimpleNamespace(id="node_test"),
            )
        ),
    )
    monkeypatch.setattr(
        dev_project.project_cli,
        "_build_project_release",
        lambda *args, **kwargs: _release(),
    )
    monkeypatch.setattr(
        dev_project.project_cli,
        "_publish_project_release",
        lambda payload, **kwargs: published.append(dict(payload))
        or {"published": True},
    )

    result = CliRunner().invoke(
        dev_project.app,
        ["push", "kanban", "--bump", "none", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert published[0]["project_id"] == "kanban"
    assert '"published": true' in result.output


def test_dev_project_push_rolls_back_version_when_build_fails(
    monkeypatch,
    tmp_path,
) -> None:
    project_root = tmp_path / "projects" / "kanban"
    project_root.mkdir(parents=True)
    manifest = project_root / "project.yaml"
    manifest.write_text("version: 1.2.3\n", encoding="utf-8")
    original = manifest.read_bytes()
    project = {
        "id": "kanban",
        "version": "1.2.3",
        "manifest_digest": "sha256:" + "c" * 64,
        "source_path": str(project_root),
        "publication": {"stage": "alpha"},
    }
    monkeypatch.setattr(dev_project, "_service", lambda: SimpleNamespace())
    monkeypatch.setattr(dev_project, "_project", lambda *_: dict(project))
    monkeypatch.setattr(dev_project, "_dev_workspace_root", lambda *_: tmp_path)
    monkeypatch.setattr(dev_project, "_occupied_project_versions", lambda *_: set())

    def replace(_project_id, replacement, **_kwargs):
        assert replacement["version"] == "1.2.4"
        manifest.write_text("version: 1.2.4\n", encoding="utf-8")
        return {**project, **replacement}

    monkeypatch.setattr(dev_project.compositions, "replace", replace)
    monkeypatch.setattr(
        dev_project,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                subnet_id="sn_test",
                node_settings=SimpleNamespace(id="node_test"),
            )
        ),
    )
    monkeypatch.setattr(
        dev_project,
        "_snapshot",
        lambda *_: (_ for _ in ()).throw(RuntimeError("broken source")),
    )

    result = CliRunner().invoke(dev_project.app, ["push", "kanban"])

    assert result.exit_code == 1
    assert manifest.read_bytes() == original


def test_dev_project_push_skips_occupied_patch_version(monkeypatch, tmp_path) -> None:
    project_root = tmp_path / "projects" / "builder"
    project_root.mkdir(parents=True)
    manifest = project_root / "project.yaml"
    manifest.write_text("version: 0.2.69\n", encoding="utf-8")
    project = {
        "id": "builder",
        "version": "0.2.69",
        "manifest_digest": "sha256:" + "c" * 64,
        "source_path": str(project_root),
        "publication": {"stage": "alpha"},
    }
    monkeypatch.setattr(dev_project, "_service", lambda: SimpleNamespace())
    monkeypatch.setattr(dev_project, "_project", lambda *_: dict(project))
    monkeypatch.setattr(dev_project, "_dev_workspace_root", lambda *_: tmp_path)
    monkeypatch.setattr(
        dev_project,
        "_occupied_project_versions",
        lambda *_: {"0.2.70"},
    )

    def replace(_project_id, replacement, **_kwargs):
        assert replacement["version"] == "0.2.71"
        manifest.write_text("version: 0.2.71\n", encoding="utf-8")
        return {**project, **replacement}

    monkeypatch.setattr(dev_project.compositions, "replace", replace)
    monkeypatch.setattr(
        dev_project,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                subnet_id="sn_test",
                node_settings=SimpleNamespace(id="node_test"),
            )
        ),
    )
    monkeypatch.setattr(dev_project, "_snapshot", lambda *_: _source())
    monkeypatch.setattr(
        dev_project.project_cli,
        "_build_project_release",
        lambda *args, **kwargs: {
            **_release(),
            "project_id": "builder",
            "version": "0.2.71",
        },
    )
    monkeypatch.setattr(
        dev_project.project_cli,
        "_publish_project_release",
        lambda *_args, **_kwargs: {"published": True},
    )

    result = CliRunner().invoke(dev_project.app, ["push", "builder", "--json"])

    assert result.exit_code == 0, result.output
    assert '"skipped_occupied_versions": [' in result.output
    assert '"0.2.70"' in result.output
    assert manifest.read_text(encoding="utf-8") == "version: 0.2.71\n"


def test_dev_project_trial_uses_primary_checkpoint_and_structured_evidence(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class RootService:
        def prepare_project_candidate_from_primary_checkpoint(self, project_id, **kwargs):
            calls.append({"project_id": project_id, **kwargs})
            return {
                "candidate": {
                    "candidate_id": "candidate.kanban",
                    "project_id": project_id,
                    "version": "0.2.0",
                    "status": "trial",
                }
            }

    monkeypatch.setattr(dev_project, "_root_service", RootService)

    result = CliRunner().invoke(
        dev_project.app,
        [
            "trial",
            "kanban",
            "--change-id",
            "change-1",
            "--change-id",
            "change-2",
            "--evidence",
            "test:kanban",
            "--zone",
            "lo",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["change_ids"] == ("change-1", "change-2")
    assert calls[0]["validation_evidence"] == {
        "status": "passed",
        "validator": "adaos.dev.project.trial",
        "refs": ["test:kanban"],
    }
    assert calls[0]["target_zone"] == "lo"
    assert '"candidate_id": "candidate.kanban"' in result.output


def test_dev_project_checkpoint_covers_all_owned_components(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        dev_project,
        "_project",
        lambda _project_id: {
            "components": {
                "owned": [
                    {"ref": "scenario:kanban"},
                    {"ref": "skill:kanban_skill"},
                ]
            }
        },
    )

    class Service:
        def checkpoint_artifact(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True, "kind": kwargs["kind"], "name": kwargs["artifact_id"]}

    monkeypatch.setattr(dev_project, "_service", Service)

    result = CliRunner().invoke(
        dev_project.app,
        [
            "checkpoint",
            "kanban",
            "--change-id",
            "change-1",
            "--message",
            "checkpoint kanban",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert [(item["kind"], item["artifact_id"]) for item in calls] == [
        ("scenario", "kanban"),
        ("skill", "kanban_skill"),
    ]
    assert all(item["metadata"]["change_id"] == "change-1" for item in calls)
    assert '"status": "checkpointed"' in result.output


def test_dev_project_candidate_is_inspectable(monkeypatch) -> None:
    service = SimpleNamespace(
        get_artifact_candidate=lambda candidate_id: {
            "candidate": {
                "candidate_id": candidate_id,
                "project_id": "kanban",
                "version": "0.2.0",
                "status": "trial",
            }
        }
    )
    monkeypatch.setattr(dev_project, "_root_service", lambda: service)

    result = CliRunner().invoke(
        dev_project.app,
        ["candidate", "candidate.kanban", "--json"],
    )

    assert result.exit_code == 0, result.output
    assert '"status": "trial"' in result.output


def test_dev_project_trial_decision_records_actor_and_evidence(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def decide(candidate_id, **kwargs):
        calls.append({"candidate_id": candidate_id, **kwargs})
        return {
            "candidate": {
                "candidate_id": candidate_id,
                "project_id": "kanban",
                "version": "0.2.0",
                "status": "accepted",
            }
        }

    monkeypatch.setattr(
        dev_project,
        "_root_service",
        lambda: SimpleNamespace(decide_artifact_candidate=decide),
    )

    result = CliRunner().invoke(
        dev_project.app,
        [
            "trial-decide",
            "candidate.kanban",
            "accept",
            "--actor",
            "codex:e2e",
            "--evidence",
            "trial:visual-check",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["accepted"] is True
    assert calls[0]["observations"] == (
        {
            "actor": "codex:e2e",
            "decision": "accepted",
            "evidence": ["trial:visual-check"],
        },
    )


def test_dev_project_promote_requires_confirmation(monkeypatch) -> None:
    service = SimpleNamespace(
        promote_artifact_candidate=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("promotion must not run without confirmation")
        )
    )
    monkeypatch.setattr(dev_project, "_root_service", lambda: service)

    result = CliRunner().invoke(dev_project.app, ["promote", "candidate.kanban"])

    assert result.exit_code == 2
    assert "requires explicit --confirm" in result.output


def test_dev_project_promote_passes_permission_receipt(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def promote_candidate(candidate_id, **kwargs):
        calls.append({"candidate_id": candidate_id, **kwargs})
        return {
            "candidate_id": candidate_id,
            "project_id": "kanban",
            "version": "0.2.0",
            "status": "published",
        }

    monkeypatch.setattr(
        dev_project,
        "_root_service",
        lambda: SimpleNamespace(promote_artifact_candidate=promote_candidate),
    )

    result = CliRunner().invoke(
        dev_project.app,
        [
            "promote",
            "candidate.kanban",
            "--confirm",
            "--actor",
            "codex:e2e",
            "--approval-id",
            "approval-1",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["permission_decision"] == {
        "approved": True,
        "actor": "codex:e2e",
        "actor_type": "user",
        "approval_id": "approval-1",
    }
    assert '"status": "published"' in result.output


def test_dev_project_publish_requires_confirmation(monkeypatch) -> None:
    service = SimpleNamespace(
        publish_project_candidate_source=lambda *_args, **_kwargs: (
            _ for _ in ()
        ).throw(AssertionError("publication must not run without confirmation"))
    )
    monkeypatch.setattr(dev_project, "_root_service", lambda: service)

    result = CliRunner().invoke(dev_project.app, ["publish", "candidate.kanban"])

    assert result.exit_code == 2
    assert "source registry publication requires explicit" in result.output
    assert "--confirm" in result.output


def test_dev_project_publish_uses_exact_candidate_and_registry_target(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def publish_source(candidate_id, **kwargs):
        calls.append({"candidate_id": candidate_id, **kwargs})
        return {
            "candidate_id": candidate_id,
            "project_id": "kanban",
            "version": "0.2.0",
            "status": "published",
            "lifecycle_phase": "registry",
        }

    monkeypatch.setattr(
        dev_project,
        "_root_service",
        lambda: SimpleNamespace(publish_project_candidate_source=publish_source),
    )

    result = CliRunner().invoke(
        dev_project.app,
        [
            "publish",
            "candidate.kanban",
            "--confirm",
            "--remote",
            "registry",
            "--branch",
            "main",
            "--message",
            "publish kanban",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        {
            "candidate_id": "candidate.kanban",
            "remote": "registry",
            "branch": "main",
            "message": "publish kanban",
            "signoff": False,
        }
    ]
    assert '"lifecycle_phase": "registry"' in result.output
