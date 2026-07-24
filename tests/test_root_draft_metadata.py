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

    class _FailingClient:
        def get_draft_info(self, **_kwargs):
            raise FileNotFoundError("no previous checkpoint")

        def push_skill_draft(self, **_kwargs):
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


def test_checkpoint_recovers_remote_commit_after_local_recording_interruption(tmp_path) -> None:
    service, publication, skill, _workspace = _checkpoint_service(tmp_path)
    change_id = "builder-checkpoint-recover"
    archive = create_zip_bytes(skill)

    class _RecoveryClient:
        def get_draft_info(self, **_kwargs):
            return {
                "stored_path": "subnets/dev/nodes/node/skills/recipe_skill",
                "commit": "2" * 40,
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
    assert recorded.change_ids == (change_id,)
