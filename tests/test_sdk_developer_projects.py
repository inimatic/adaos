from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from adaos.sdk.developer import projects


@pytest.fixture
def dev_roots(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    skills = tmp_path / "skills"
    scenarios = tmp_path / "scenarios"
    skills.mkdir()
    scenarios.mkdir()
    monkeypatch.setattr(projects, "_roots", lambda: (skills.resolve(), scenarios.resolve()))
    return skills, scenarios


def test_local_project_discovery_and_bounded_file_access(dev_roots) -> None:
    _skills, scenarios = dev_roots
    root = scenarios / "builder"
    root.mkdir()
    (root / "scenario.yaml").write_text(
        "id: builder\ntitle: Builder\nversion: 0.1.0\ndepends: [builder_control_skill]\n",
        encoding="utf-8",
    )
    (root / "builder_memory.md").write_text("initial", encoding="utf-8")

    listed = projects.list_projects(kind="scenario")
    described = projects.describe("scenario", "builder")
    before = projects.read_file("scenario", "builder", "builder_memory.md")
    written = projects.write_file("scenario", "builder", "builder_memory.md", "updated")
    after = projects.read_file("scenario", "builder", "builder_memory.md")

    assert listed[0]["id"] == "builder"
    assert listed[0]["depends"] == ["builder_control_skill"]
    assert described["title"] == "Builder"
    assert before["content"] == "initial"
    assert written["size_bytes"] == 7
    assert after["content"] == "updated"


def test_project_discovery_and_files_hide_runtime_artifacts(dev_roots) -> None:
    skills, scenarios = dev_roots
    (skills / ".runtime").mkdir()
    (skills / "_scratch").mkdir()
    root = scenarios / "builder"
    root.mkdir()
    (root / "scenario.yaml").write_text("id: builder\n", encoding="utf-8")
    cache = root / "tests" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "test_builder.pyc").write_bytes(b"compiled")
    pytest_cache = root / ".pytest_cache"
    pytest_cache.mkdir()
    (pytest_cache / "README.md").write_text("generated", encoding="utf-8")
    (root / "tests" / "test_builder.py").write_text("def test_ok(): pass\n", encoding="utf-8")

    listed_projects = projects.list_projects()
    listed_files = projects.list_files("scenario", "builder")

    assert [item["id"] for item in listed_projects] == ["builder"]
    assert [item["path"] for item in listed_files] == ["scenario.yaml", "tests/test_builder.py"]


def test_project_files_block_escape_managed_state_and_binary(dev_roots) -> None:
    _skills, scenarios = dev_roots
    (scenarios / "builder").mkdir()

    with pytest.raises(projects.DeveloperProjectError, match="outside project root"):
        projects.write_file("scenario", "builder", "../outside.md", "bad")
    with pytest.raises(projects.DeveloperProjectError, match="managed_state_file"):
        projects.write_file("scenario", "builder", "prompt_state.json", "{}")
    with pytest.raises(projects.DeveloperProjectError, match="unsupported_file_type"):
        projects.write_file("scenario", "builder", "asset.png", "bad")


def test_read_file_reports_truncation(dev_roots) -> None:
    _skills, scenarios = dev_roots
    root = scenarios / "builder"
    root.mkdir()
    (root / "notes.md").write_text("abcdefgh", encoding="utf-8")

    result = projects.read_file("scenario", "builder", "notes.md", max_bytes=4)

    assert result["content"] == "abcd"
    assert result["truncated"] is True
    assert result["editable"] is False


def test_update_metadata_updates_only_scenario_yaml_and_preserves_scenario_json_content(dev_roots) -> None:
    _skills, scenarios = dev_roots
    root = scenarios / "builder"
    root.mkdir()
    (root / "scenario.yaml").write_text(
        "id: builder\ntitle: Old\ndescription: before\ntype: desktop\nversion: 0.1.0\n",
        encoding="utf-8",
    )
    (root / "scenario.json").write_text(
        '{"id":"builder","title":"Old","description":"before","type":"desktop",'
        '"version":"0.1.0","ui":{"application":{"desktop":{"pageSchema":{"id":"builder"}}}}}\n',
        encoding="utf-8",
    )

    result = projects.update_metadata(
        "scenario",
        "builder",
        title="Builder Workbench",
        description="SDK-backed",
        project_type="desktop",
    )

    json_content = projects._read_manifest(root / "scenario.json")
    yaml_manifest = projects._read_manifest(root / "scenario.yaml")
    assert result["title"] == "Builder Workbench"
    assert result["updated_manifests"] == ["scenario.yaml"]
    assert yaml_manifest["description"] == "SDK-backed"
    assert json_content["description"] == "before"
    assert json_content["ui"]["application"]["desktop"]["pageSchema"]["id"] == "builder"


def test_update_metadata_rejects_project_type_change(dev_roots) -> None:
    _skills, scenarios = dev_roots
    root = scenarios / "builder"
    root.mkdir()
    (root / "scenario.yaml").write_text(
        "id: builder\ntype: desktop\nversion: 0.1.0\n",
        encoding="utf-8",
    )

    with pytest.raises(projects.DeveloperProjectError, match="immutable after creation"):
        projects.update_metadata("scenario", "builder", project_type="mobile")

    assert projects.describe("scenario", "builder")["project_type"] == "desktop"


@dataclass
class _Result:
    name: str
    path: Path
    commit: str | None = None


class _DeveloperService:
    def create_scenario(self, name, template=None):
        return _Result(name=name, path=Path(f"/dev/scenarios/{name}"))

    def push_scenario(self, name, *, message=None, metadata=None):
        return _Result(name=name, path=Path(f"/dev/scenarios/{name}"), commit="abc123")

    def update_scenario(self, name):
        return _Result(name=name, path=Path(f"/dev/scenarios/{name}"), commit="def456")

    def publish_scenario(self, name, **_kwargs):
        return _Result(name=name, path=Path(f"/workspace/scenarios/{name}"))

    def prepare_artifact_candidate(self, kind, name, *, change_ids, validation_evidence=None):
        return {
            "ok": True,
            "candidate": {"candidate_id": f"{name}-candidate", "change_ids": list(change_ids)},
        }

    def decide_artifact_candidate(self, candidate_id, *, accepted, observations=()):
        return {
            "ok": True,
            "candidate": {
                "candidate_id": candidate_id,
                "status": "accepted" if accepted else "rejected",
                "observations": list(observations),
            },
        }

    def prepare_rebased_artifact_candidate(
        self,
        stale_candidate_id,
        kind,
        name,
        *,
        validation_evidence=None,
    ):
        return {
            "ok": True,
            "replaces_candidate_id": stale_candidate_id,
            "candidate": {
                "candidate_id": f"{name}-rebased",
                "kind": kind,
                "validation_evidence": validation_evidence,
            },
        }

    def promote_artifact_candidate(self, candidate_id, *, permission_decision=None):
        return {
            "ok": True,
            "candidate_id": candidate_id,
            "kind": "scenario",
            "name": "builder",
            "release": "builder@1.0.0",
            "permission_decision": permission_decision,
        }

    def check_artifact_subscription(self, project_id):
        return {"ok": True, "project_id": project_id, "available": True}

    def plan_artifact_subscription_update(self, project_id):
        return {
            "ok": True,
            "project_id": project_id,
            "plan_digest": "sha256:" + "a" * 64,
        }

    def inspect_artifact_subscription_update(self, project_id):
        return {
            "ok": True,
            "subscription": {"project_id": project_id, "policy": "notify"},
            "available": True,
            "activation_allowed": True,
            "update_plan": {
                "plan_digest": "sha256:" + "a" * 64,
                "activation": {"target_release": f"{project_id}@1.1.0"},
            },
        }

    def activate_artifact_subscription(
        self,
        project_id,
        *,
        idempotency_key=None,
        expected_plan_digest=None,
        permission_decision=None,
    ):
        return {
            "ok": True,
            "project_id": project_id,
            "release": f"{project_id}@1.1.0",
            "idempotency_key": idempotency_key,
            "expected_plan_digest": expected_plan_digest,
            "permission_decision": permission_decision,
        }


def test_lifecycle_results_are_plain_json_values(monkeypatch) -> None:
    monkeypatch.setattr(projects, "_service", lambda: _DeveloperService())

    created = projects.create("scenario", "builder")
    pushed = projects.push("scenario", "builder", message="checkpoint")
    published = projects.publish("scenario", "builder", dry_run=True)

    assert created["name"] == "builder"
    assert Path(created["path"]).parts[-3:] == ("dev", "scenarios", "builder")
    assert created["commit"] is None
    assert pushed["commit"] == "abc123"
    assert Path(published["path"]).parts[-3:] == ("workspace", "scenarios", "builder")
    with pytest.raises(projects.DeveloperProjectError, match="DEV draft update is retired"):
        projects.update("scenario", "builder")


def test_candidate_lifecycle_requires_change_and_explicit_decision(monkeypatch) -> None:
    monkeypatch.setattr(projects, "_service", lambda: _DeveloperService())

    prepared = projects.prepare_candidate(
        "scenario",
        "builder",
        change_ids=["change-1"],
        validation_evidence={"status": "passed"},
    )
    accepted = projects.decide_candidate(
        prepared["candidate"]["candidate_id"],
        accepted=True,
        observations=[{"decision": "looks_good"}],
    )
    promoted = projects.promote_candidate(
        prepared["candidate"]["candidate_id"],
        permission_decision={"approved": True, "actor": "user:test"},
    )
    rebased = projects.prepare_rebased_candidate(
        prepared["candidate"]["candidate_id"],
        "scenario",
        "builder",
        validation_evidence={"status": "passed"},
    )
    notice = projects.check_subscription("builder")
    inspection = projects.inspect_subscription_update("builder")
    update_plan = projects.plan_subscription_update("builder")
    subscription_update = projects.activate_subscription(
        "scenario",
        "builder",
        idempotency_key="update-builder-1",
        expected_plan_digest=update_plan["plan_digest"],
        permission_decision={"approved": True, "actor": "user:test"},
    )

    assert accepted["candidate"]["status"] == "accepted"
    assert promoted["release"] == "builder@1.0.0"
    assert promoted["permission_decision"]["actor"] == "user:test"
    assert rebased["candidate"]["candidate_id"] == "builder-rebased"
    assert notice["available"] is True
    assert inspection["update_plan"]["activation"]["target_release"] == "builder@1.1.0"
    assert update_plan["plan_digest"].startswith("sha256:")
    assert subscription_update["idempotency_key"] == "update-builder-1"
    assert subscription_update["expected_plan_digest"] == update_plan["plan_digest"]
    assert subscription_update["permission_decision"]["approved"] is True

    with pytest.raises(projects.DeveloperProjectError, match="at least one Builder Change"):
        projects.prepare_candidate("scenario", "builder", change_ids=[])


def test_apply_subscription_update_uses_runtime_coordinator(monkeypatch) -> None:
    from adaos.services import artifact_subscription_update as update_service

    calls: list[dict] = []
    ctx = object()

    class _Coordinator:
        def __init__(self, value) -> None:
            assert value is ctx

        async def update(self, kind: str, project_id: str, **kwargs):
            calls.append({"kind": kind, "project_id": project_id, **kwargs})
            return {"ok": True, "mode": "package_activation", "updated": True}

    monkeypatch.setattr(projects, "require_ctx", lambda _capability: ctx)
    monkeypatch.setattr(update_service, "ArtifactSubscriptionUpdateCoordinator", _Coordinator)
    result = asyncio.run(
        projects.apply_subscription_update(
            "scenario",
            "builder",
            expected_plan_digest="sha256:" + "a" * 64,
            idempotency_key="builder-update-1",
            webspace_id="desktop",
        )
    )

    assert result["mode"] == "package_activation"
    assert calls[0]["expected_plan_digest"] == "sha256:" + "a" * 64
    assert calls[0]["idempotency_key"] == "builder-update-1"
