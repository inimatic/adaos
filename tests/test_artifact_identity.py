from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from adaos.apps.cli.commands import maintenance as maintenance_cli
from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    ArtifactSourceRef,
    ProjectRelease,
    WorkspaceLock,
    WorkspaceSlot,
)
from adaos.services.artifact_identity import (
    ArtifactIdentityDiagnosticError,
    explain_workspace_artifact_identity,
)
from adaos.services.artifact_pipeline.storage import atomic_write_json
from adaos.services.workspace_registry import (
    set_workspace_registry_channel,
    upsert_workspace_registry_entry,
)


_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64


def _historical_fixture() -> Path:
    return (
        Path(__file__).parent
        / "fixtures"
        / "artifact_migration"
        / "workspace_v1_incomplete"
    )


def _sealed_workspace(workspace: Path) -> tuple[ProjectRelease, ArtifactPackageRef]:
    scenario_dir = workspace / "scenarios" / "recipes_alias"
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "scenario.yaml").write_text(
        "id: recipes\nversion: 1.2.3\ntitle: Recipes\n",
        encoding="utf-8",
    )
    upsert_workspace_registry_entry(workspace, "scenarios", scenario_dir)
    source = ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-registry",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("scenarios/recipes/",),
    )
    package = ArtifactPackageRef(
        kind="scenario",
        artifact_id="recipes",
        version="1.2.3",
        digest=_DIGEST_A,
        manifest_digest=_DIGEST_B,
        source_ref=source,
    )
    release = ProjectRelease(
        project_id="recipes",
        version="1.2.3",
        source_ref=source,
        components=(package,),
    ).seal()
    set_workspace_registry_channel(
        workspace,
        "scenarios",
        "recipes",
        channel="stable",
        release=release,
    )
    lock = WorkspaceLock(
        lock_revision=1,
        updated_at="2026-07-27T00:00:00Z",
        slots=(
            WorkspaceSlot(
                slot_id="primary",
                project_id="recipes",
                release="recipes@1.2.3",
                release_digest=release.release_digest,
            ),
        ),
        components=(package,),
    )
    atomic_write_json(workspace / ".adaos" / "workspace.lock.json", lock.to_dict())
    return release, package


def test_identity_explanation_keeps_historical_alias_and_yaml_authority(tmp_path: Path):
    workspace = tmp_path / "workspace"
    shutil.copytree(_historical_fixture(), workspace)

    result = explain_workspace_artifact_identity(
        workspace,
        kind="scenario",
        name_or_id="recipes",
    )

    assert result["read_only"] is True
    assert result["registry"]["canonical_id"] == "recipes"
    assert result["registry"]["install_name"] == "recipes_legacy"
    assert result["registry"]["version"]["authority"] == "scenario.yaml"
    assert result["registry"]["version"]["value"].startswith("0.0.0-legacy.")
    assert result["registry"]["version"]["value"] != "9.9.9"
    assert result["registry"]["version"]["compatibility"]["publishable"] is False
    assert result["source"]["status"] == "legacy_path_only"
    assert result["channel"]["status"] == "not_resolved"
    assert result["release"]["status"] == "not_resolved"
    assert result["package"]["status"] == "not_resolved"
    assert result["activation"]["status"] == "legacy_unlocked"
    assert "canonical_manifest_version_missing" in result["warnings"]


def test_identity_explanation_links_channel_pointer_to_active_workspace_lock(tmp_path: Path):
    workspace = tmp_path / "workspace"
    release, package = _sealed_workspace(workspace)

    result = explain_workspace_artifact_identity(
        workspace,
        kind="scenario",
        name_or_id="recipes_alias",
    )

    assert result["project_ref"]["project_id"] == "recipes"
    assert result["registry"]["canonical_id"] == "recipes"
    assert result["registry"]["install_name"] == "recipes_alias"
    assert result["source"]["status"] == "immutable"
    assert result["source"]["ref"] == release.source_ref.to_dict()
    assert result["release"]["status"] == "resolved"
    assert result["release"]["project_id"] == "recipes"
    assert result["release"]["reference"] == "recipes@1.2.3"
    assert result["release"]["version"] == "1.2.3"
    assert result["release"]["digest"] == release.release_digest
    assert result["release"]["active_slots"][0]["release"] == "recipes@1.2.3"
    assert result["package"]["digest"] == package.digest
    assert result["package"]["active"] == package.to_dict()
    assert result["activation"]["status"] == "active_selected_package"
    assert result["activation"]["component"] == package.to_dict()
    assert result["activation"]["slots"][0]["slot_id"] == "primary"
    assert result["warnings"] == []


def test_identity_explanation_fails_closed_for_corrupt_workspace_lock(tmp_path: Path):
    workspace = tmp_path / "workspace"
    _sealed_workspace(workspace)
    (workspace / ".adaos" / "workspace.lock.json").write_text("{", encoding="utf-8")

    with pytest.raises(ArtifactIdentityDiagnosticError, match="cannot trust WorkspaceLock"):
        explain_workspace_artifact_identity(
            workspace,
            kind="scenario",
            name_or_id="recipes",
        )


def test_maintenance_cli_exposes_read_only_artifact_identity(tmp_path: Path):
    workspace = tmp_path / "workspace"
    release, _ = _sealed_workspace(workspace)

    result = CliRunner().invoke(
        maintenance_cli.app,
        [
            "artifact-identity",
            "recipes",
            "--kind",
            "scenario",
            "--workspace",
            str(workspace),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema"] == "adaos.artifact.identity_explanation.v1"
    assert payload["read_only"] is True
    assert payload["release"]["digest"] == release.release_digest
