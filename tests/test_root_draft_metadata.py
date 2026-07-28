from __future__ import annotations

import json
import io
import hashlib
import zipfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.services import conversation_store
from adaos.services.artifact_pipeline import ArtifactPublicationService
from adaos.services.root.service import (
    RootDeveloperService,
    RootServiceError,
    create_zip_bytes,
    _extract_zip_bytes,
    _normalize_draft_metadata,
    _parse_draft_commit_metadata,
)


def test_draft_metadata_is_allowlisted_and_round_trips_from_git_trailers() -> None:
    normalized = _normalize_draft_metadata(
        {
            "change_id": "builder_change_123",
            "conversation_id": "conv.skill.builder_skill.default",
            "topic_id": "prompt-project:scenario:recipes",
            "revision": "007",
            "model": "gpt-5",
            "source_message_ids": ["m.request", "m.result"],
            "transcript": "must not be copied to Git",
        }
    )

    assert "transcript" not in normalized
    assert normalized["source_message_ids"] == ["m.request", "m.result"]
    parsed = _parse_draft_commit_metadata(
        "Update recipes\n\n"
        "AdaOS-Change-Id: builder_change_123\n"
        "AdaOS-Conversation-Id: conv.skill.builder_skill.default\n"
        "AdaOS-Topic-Id: prompt-project:scenario:recipes\n"
        "AdaOS-Revision: 007\n"
        "AdaOS-Model: gpt-5\n"
        "AdaOS-Source-Messages: m.request,m.result\n"
    )

    assert parsed == normalized


def test_forge_reconciliation_recovers_builder_chat_only_once(tmp_path) -> None:
    suffix = uuid4().hex[:10]
    conversation_id = f"conv.builder.recovery.{suffix}"
    topic_id = f"prompt-project:scenario:recipes_{suffix}"
    change_id = f"builder_change_{suffix}"
    target = tmp_path / f"recipes_{suffix}"
    revision_dir = target / "ui_revisions"
    revision_dir.mkdir(parents=True)
    (revision_dir / "007.json").write_text(
        json.dumps(
            {
                "request": {"text": "Add a favorites filter"},
                "llm": {"comment": "Added the favorites filter."},
            }
        ),
        encoding="utf-8",
    )
    service = object.__new__(RootDeveloperService)
    metadata = {
        "change_id": change_id,
        "conversation_id": conversation_id,
        "topic_id": topic_id,
        "thread_id": topic_id,
        "revision": "007",
        "model": "gpt-5",
    }

    first = service._reconcile_builder_change_from_forge(
        kind="scenarios",
        name=f"recipes_{suffix}",
        target=target,
        commit="abc123",
        message="Added the favorites filter.",
        metadata=metadata,
    )
    second = service._reconcile_builder_change_from_forge(
        kind="scenarios",
        name=f"recipes_{suffix}",
        target=target,
        commit="abc123",
        message="Added the favorites filter.",
        metadata=metadata,
    )

    projection = conversation_store.list_projection(conversation_id, thread_id=topic_id, limit=10)
    change = conversation_store.get_development_change(change_id)
    assert first and first["messages_recovered"] == 2
    assert second and second["messages_recovered"] == 0
    assert [item["text"] for item in projection["messages"]] == [
        "Add a favorites filter",
        "Added the favorites filter.",
    ]
    assert change and change["status"] == "recovered"
    assert change["meta"]["synthetic_chat"] is True


def test_root_draft_archive_extraction_rejects_path_traversal(tmp_path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../outside.txt", "unsafe")

    try:
        _extract_zip_bytes(buffer.getvalue(), tmp_path / "artifact")
    except RootServiceError as exc:
        assert "escapes artifact root" in str(exc)
    else:
        raise AssertionError("path traversal archive must be rejected")


def test_root_draft_archive_replaces_existing_artifact_transactionally(tmp_path) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    (target / "previous.txt").write_text("previous", encoding="utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("current.txt", "current")

    _extract_zip_bytes(buffer.getvalue(), target)

    assert (target / "current.txt").read_text(encoding="utf-8") == "current"
    assert not (target / "previous.txt").exists()
    assert not list(tmp_path.glob(".artifact.update-*"))
    assert not list(tmp_path.glob(".artifact.backup-*"))


def test_root_draft_archive_keeps_existing_artifact_when_backup_rename_fails(tmp_path, monkeypatch) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    (target / "previous.txt").write_text("previous", encoding="utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("current.txt", "current")
    original_replace = type(target).replace

    def locked_replace(path, destination):
        if path == target:
            raise PermissionError("target is locked")
        return original_replace(path, destination)

    monkeypatch.setattr(type(target), "replace", locked_replace)

    try:
        _extract_zip_bytes(buffer.getvalue(), target)
    except PermissionError as exc:
        assert "target is locked" in str(exc)
    else:
        raise AssertionError("locked target must reject the update")

    assert (target / "previous.txt").read_text(encoding="utf-8") == "previous"
    assert not (target / "current.txt").exists()
    assert not list(tmp_path.glob(".artifact.update-*"))
    assert not list(tmp_path.glob(".artifact.backup-*"))


def test_root_draft_archive_rolls_back_when_staged_activation_fails(tmp_path, monkeypatch) -> None:
    target = tmp_path / "artifact"
    target.mkdir()
    (target / "previous.txt").write_text("previous", encoding="utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("current.txt", "current")
    original_replace = type(target).replace

    def failing_activation(path, destination):
        if path.name.startswith(".artifact.update-"):
            raise OSError("activation failed")
        return original_replace(path, destination)

    monkeypatch.setattr(type(target), "replace", failing_activation)

    try:
        _extract_zip_bytes(buffer.getvalue(), target)
    except OSError as exc:
        assert "activation failed" in str(exc)
    else:
        raise AssertionError("failed activation must propagate")

    assert (target / "previous.txt").read_text(encoding="utf-8") == "previous"
    assert not (target / "current.txt").exists()
    assert not list(tmp_path.glob(".artifact.update-*"))
    assert not list(tmp_path.glob(".artifact.backup-*"))


def test_default_template_alias_resolves_to_the_builtin_default(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    builtin = tmp_path / "builtin"
    expected = builtin / "scenario_default"
    expected.mkdir(parents=True)
    service = object.__new__(RootDeveloperService)
    service._workspace_templates_dir = lambda _kind: workspace
    service._builtin_templates_dir = lambda _kind: builtin
    service._default_template_name = lambda _kind: "scenario_default"

    path, prototype = service._resolve_template("scenarios", "default")

    assert path == expected
    assert prototype == "default"


def test_subscription_activation_does_not_invent_runtime_skip_policies() -> None:
    captured: dict[str, object] = {}

    class _Publication:
        def activate_subscription_update(self, project_id: str, **kwargs):
            captured["project_id"] = project_id
            captured.update(kwargs)
            raise RuntimeError("runtime adapter required")

    service = object.__new__(RootDeveloperService)
    service._load_config = lambda: SimpleNamespace()
    service._artifact_publication_service = lambda _cfg: _Publication()

    with pytest.raises(RuntimeError, match="runtime adapter required"):
        service.activate_artifact_subscription(
            "recipes",
            expected_plan_digest="sha256:" + "a" * 64,
        )

    assert captured["project_id"] == "recipes"
    assert captured["reload_runtime"] is None
    assert captured["health_check"] is None
    assert captured["reload_policy"] is None
    assert captured["health_policy"] is None


def test_subscription_activation_exposes_operation_identity() -> None:
    class _Record:
        def __init__(self, payload):
            self.payload = payload

        def to_dict(self):
            return dict(self.payload)

    activation = SimpleNamespace(
        operation_id="activation-123",
        status="completed",
        idempotent_replay=True,
        workspace_lock=_Record({"lock_digest": "sha256:" + "b" * 64}),
    )
    updated = SimpleNamespace(
        pointer=SimpleNamespace(release="recipes@2.0.0", release_digest="sha256:" + "c" * 64),
        activation=activation,
        subscription=_Record({"project_id": "recipes", "policy": "notify"}),
    )

    class _Publication:
        def activate_subscription_update(self, _project_id: str, **_kwargs):
            return updated

    service = object.__new__(RootDeveloperService)
    service._load_config = lambda: SimpleNamespace()
    service._artifact_publication_service = lambda _cfg: _Publication()

    result = service.activate_artifact_subscription(
        "recipes",
        expected_plan_digest="sha256:" + "a" * 64,
        reload_policy={"mode": "skip", "approved_by": "test", "reason": "bounded test"},
        health_policy={"mode": "skip", "approved_by": "test", "reason": "bounded test"},
    )

    assert result["activation_operation_id"] == "activation-123"
    assert result["activation_status"] == "completed"
    assert result["idempotent_replay"] is True


def test_subscription_inspection_reuses_one_channel_notice() -> None:
    notice = SimpleNamespace(
        available=True,
        to_dict=lambda: {
            "available": True,
            "activation_allowed": True,
            "subscription": {"project_id": "recipes", "policy": "notify"},
        },
    )
    calls: list[object] = []

    class _Plan:
        def to_dict(self):
            return {"plan_digest": "sha256:" + "a" * 64, "activation": {"target_release": "recipes@2.0.0"}}

    class _Publication:
        def check_subscription(self, project_id: str):
            calls.append(("check", project_id))
            return notice

        def plan_subscription_update(self, project_id: str, *, notice=None):
            calls.append(("plan", project_id, notice))
            return _Plan()

    service = object.__new__(RootDeveloperService)
    service._load_config = lambda: SimpleNamespace()
    service._artifact_publication_service = lambda _cfg: _Publication()

    result = service.inspect_artifact_subscription_update("recipes")

    assert result["update_plan"]["activation"]["target_release"] == "recipes@2.0.0"
    assert calls == [("check", "recipes"), ("plan", "recipes", notice)]


class _UnusedPublicationRemote:
    def __getattr__(self, name):
        raise AssertionError(f"publication remote must not be called: {name}")


def _checkpoint_service(tmp_path: Path):
    workspace = tmp_path / "dev"
    skill = workspace / "skills" / "recipe_skill"
    skill.mkdir(parents=True)
    (skill / "skill.yaml").write_text(
        "name: recipe_skill\nversion: 1.0.0\ndependencies: []\n",
        encoding="utf-8",
    )
    publication = ArtifactPublicationService(
        state_root=tmp_path / "state",
        workspace_root=tmp_path / "installed",
        remote=_UnusedPublicationRemote(),
    )
    service = object.__new__(RootDeveloperService)
    config = SimpleNamespace(
        owner_id="owner",
        node_id="node",
        node_settings=SimpleNamespace(id="node"),
        dev_settings=SimpleNamespace(forge_repo="inimatic/registry"),
    )
    service._load_config = lambda: config
    service._owner_workspace = lambda _cfg: ("owner", workspace)
    service._validate_artifact_preflight = lambda *_args: None
    service._artifact_publication_service = lambda _cfg: publication
    service._mtls_material_for_role = lambda *_args: ("cert", "key", True)
    return service, publication, skill, workspace


def test_checkpoint_reuses_completed_change_without_version_bump_or_remote_write(tmp_path) -> None:
    service, publication, skill, _workspace = _checkpoint_service(tmp_path)
    change_id = "builder-checkpoint-1"
    source_ref = ArtifactSourceRef(
        forge="adaos-root",
        repository="inimatic/registry",
        revision="1" * 40,
        path_scope=("subnets/dev/nodes/node/skills/recipe_skill/",),
    )
    publication.record_push(
        kind="skill",
        artifact_id="recipe_skill",
        artifact_dir=skill,
        source_ref=source_ref,
        change_ids=(change_id,),
    )

    class _Client:
        def __getattr__(self, name):
            raise AssertionError(f"Root client must not be called: {name}")

    service._client = lambda _cfg: _Client()
    result = service._push_artifact(
        "skills",
        "recipe_skill",
        message="checkpoint",
        metadata={"change_id": change_id},
    )

    assert result.version == "1.0.0"
    assert result.commit == "1" * 40
    assert result.package_digest
    assert "version: 1.0.0" in (skill / "skill.yaml").read_text(encoding="utf-8")


def test_checkpoint_rolls_back_local_manifest_and_registry_when_remote_write_fails(
    tmp_path,
) -> None:
    service, _publication, skill, workspace = _checkpoint_service(tmp_path)
    registry = workspace / "registry.json"
    registry.write_text('{"version": 1, "skills": [], "scenarios": []}\n', encoding="utf-8")
    original_manifest = (skill / "skill.yaml").read_bytes()
    original_registry = registry.read_bytes()

    calls = {"push": 0}

    class _FailingClient:
        def get_draft_info(self, **_kwargs):
            raise FileNotFoundError("no previous checkpoint")

        def push_skill_draft(self, **_kwargs):
            calls["push"] += 1
            raise RuntimeError("remote unavailable")

    service._client = lambda _cfg: _FailingClient()

    with pytest.raises(RuntimeError, match="remote unavailable"):
        service._push_artifact(
            "skills",
            "recipe_skill",
            message="checkpoint",
            metadata={"change_id": "builder-checkpoint-2"},
        )

    assert (skill / "skill.yaml").read_bytes() == original_manifest
    assert registry.read_bytes() == original_registry

    with pytest.raises(RootServiceError, match="unresolved"):
        service._push_artifact(
            "skills",
            "recipe_skill",
            message="checkpoint",
            metadata={"change_id": "builder-checkpoint-2"},
        )

    assert calls["push"] == 1
    assert (skill / "skill.yaml").read_bytes() == original_manifest


def test_checkpoint_recovers_remote_commit_after_local_recording_interruption(tmp_path) -> None:
    service, publication, skill, _workspace = _checkpoint_service(tmp_path)
    change_id = "builder-checkpoint-recover"
    archive = create_zip_bytes(skill)

    class _RecoveryClient:
        def get_draft_info(self, **_kwargs):
            return {
                "stored_path": "subnets/dev/nodes/node/skills/recipe_skill",
                "commit": "2" * 40,
                "tree_sha": "3" * 40,
                "sha256": hashlib.sha256(archive).hexdigest(),
                "metadata": {"change_id": change_id},
            }

        def push_skill_draft(self, **_kwargs):
            raise AssertionError("recovery must not create a second Forge commit")

    service._client = lambda _cfg: _RecoveryClient()
    result = service._push_artifact(
        "skills",
        "recipe_skill",
        message="checkpoint",
        metadata={"change_id": change_id},
    )

    recorded = publication.load_pushed_source("skill", "recipe_skill")
    assert result.version == "1.0.0"
    assert result.commit == "2" * 40
    assert recorded.source_ref.revision == "2" * 40
    assert recorded.source_tree == "3" * 40
    assert recorded.change_ids == (change_id,)


def test_checkpoint_reconciles_unknown_remote_outcome_without_second_write(tmp_path) -> None:
    service, publication, skill, _workspace = _checkpoint_service(tmp_path)
    change_id = "builder-checkpoint-timeout"
    state: dict[str, object] = {"pushes": 0}

    class _CommitThenTimeoutClient:
        def get_draft_info(self, **_kwargs):
            receipt = state.get("receipt")
            if isinstance(receipt, dict):
                return receipt
            raise FileNotFoundError("no previous checkpoint")

        def push_skill_draft(self, **kwargs):
            state["pushes"] = int(state["pushes"]) + 1
            state["receipt"] = {
                "stored_path": "subnets/dev/nodes/node/skills/recipe_skill",
                "commit": "4" * 40,
                "tree_sha": "5" * 40,
                "sha256": kwargs["sha256"],
                "metadata": {"change_id": change_id},
            }
            raise TimeoutError("response was lost after commit")

    service._client = lambda _cfg: _CommitThenTimeoutClient()

    with pytest.raises(TimeoutError, match="response was lost"):
        service._push_artifact(
            "skills",
            "recipe_skill",
            message="checkpoint",
            metadata={"change_id": change_id},
        )

    assert "version: 1.0.0" in (skill / "skill.yaml").read_text(encoding="utf-8")

    result = service._push_artifact(
        "skills",
        "recipe_skill",
        message="checkpoint",
        metadata={"change_id": change_id},
    )

    recorded = publication.load_pushed_source("skill", "recipe_skill")
    assert state["pushes"] == 1
    assert result.commit == "4" * 40
    assert recorded.source_tree == "5" * 40
    assert "version: 1.0.1" in (skill / "skill.yaml").read_text(encoding="utf-8")


def test_checkpoint_replays_prepared_archive_when_remote_receipt_is_unchanged(tmp_path) -> None:
    service, publication, skill, _workspace = _checkpoint_service(tmp_path)
    previous_change_id = "builder-checkpoint-previous"
    change_id = "builder-checkpoint-replay"
    previous_ref = ArtifactSourceRef(
        forge="adaos-root",
        repository="inimatic/registry",
        revision="6" * 40,
        path_scope=("subnets/dev/nodes/node/skills/recipe_skill/",),
    )
    previous = publication.record_push(
        kind="skill",
        artifact_id="recipe_skill",
        artifact_dir=skill,
        source_ref=previous_ref,
        change_ids=(previous_change_id,),
        source_tree="7" * 40,
    )
    state: dict[str, object] = {"pushes": 0}
    previous_receipt = {
        "stored_path": "subnets/dev/nodes/node/skills/recipe_skill",
        "commit": previous_ref.revision,
        "tree_sha": previous.source_tree,
        "sha256": previous.package.digest.removeprefix("sha256:"),
        "metadata": {"change_id": previous_change_id},
    }

    class _FailBeforeCommitThenSucceedClient:
        def get_draft_info(self, **_kwargs):
            return previous_receipt

        def push_skill_draft(self, **kwargs):
            state["pushes"] = int(state["pushes"]) + 1
            if state["pushes"] == 1:
                raise TimeoutError("request failed before commit")
            return {
                "stored_path": previous_receipt["stored_path"],
                "commit": "8" * 40,
                "tree_sha": "9" * 40,
                "sha256": kwargs["sha256"],
                "metadata": {"change_id": change_id},
            }

        def get_draft_source_tree(self, **_kwargs):
            raise AssertionError("the successful response already contains a source tree")

    service._client = lambda _cfg: _FailBeforeCommitThenSucceedClient()

    with pytest.raises(TimeoutError, match="failed before commit"):
        service._push_artifact(
            "skills",
            "recipe_skill",
            message="checkpoint",
            metadata={"change_id": change_id},
        )

    assert "version: 1.0.0" in (skill / "skill.yaml").read_text(encoding="utf-8")

    result = service._push_artifact(
        "skills",
        "recipe_skill",
        message="checkpoint",
        metadata={"change_id": change_id},
    )

    assert state["pushes"] == 2
    assert result.commit == "8" * 40
    assert result.version == "1.0.1"
    assert "version: 1.0.1" in (skill / "skill.yaml").read_text(encoding="utf-8")
