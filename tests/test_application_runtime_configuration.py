import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml

from adaos.domain.application import RuntimeSelection
from adaos.domain.artifact_release import ArtifactSourceRef, ArtifactPackageRef, ProjectRelease, ProjectCompositionLock, ProjectMemberLock
from adaos.services.applications.configuration import ApplicationConfigurationStore, ConfigurationConflict
from adaos.services.applications.runtime_channel import ApplicationRuntimeChannel, RuntimeChannelConflict
from adaos.services.applications.runtime_configuration import ApplicationRuntimeConfiguration
from adaos.services.applications.runtime_transition import ApplicationRuntimeTransition, TransitionStep
from adaos.services.policy.skill_capabilities import SkillCapabilityAdmissionError


DIGEST = "sha256:" + "a" * 64
SCHEMA = {"type": "object", "properties": {"page_size": {"type": "integer", "minimum": 1, "maximum": 50}},
          "required": ["page_size"], "additionalProperties": False}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from adaos.services.applications import runtime_configuration as module
    workspace = tmp_path / "workspace"
    path = workspace / "skills/.runtime/worker/v0.1/slot-a"
    path.mkdir(parents=True)
    manifest = {"name": "worker", "version": "0.1.0", "capabilities": ["configuration.read", "configuration.write"],
                "configuration": {"schema": SCHEMA, "defaults": {"page_size": 10}}}
    (path / "skill.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    skill = SimpleNamespace(name="worker", path=path)
    ctx = SimpleNamespace(skill_ctx=SimpleNamespace(get=lambda: skill), paths=SimpleNamespace(
        state_dir=lambda: tmp_path, workspace_dir=lambda: workspace))
    channel = ApplicationRuntimeChannel(tmp_path, "sample")
    stable = RuntimeSelection(webspace_id="desktop", application_id="sample", source="stable_installation",
                              release_digest=DIGEST, runtime_root_ref="workspace", revision=1)
    channel.select(stable, expected_revision=0)
    source = ArtifactSourceRef(forge="github", repository="owner/sample", revision="1" * 40, path_scope=("skills/worker/",))
    member = ProjectMemberLock(ref="skill:worker", package_digest=DIGEST, role="primary", exposure="application",
                               lifecycle="bound", relations=("realizes",))
    package = ArtifactPackageRef(kind="skill", artifact_id="worker", version="0.1.0", digest=DIGEST,
                                 manifest_digest=DIGEST, source_ref=source)
    composition = ProjectCompositionLock(project_definition_digest=DIGEST, profiles=(), members=(member,),
                                         project_dependencies=(), entrypoints=(), compatibility={}, lifecycle={})
    release = SimpleNamespace(project_release=ProjectRelease(project_id="sample", version="0.1.0", source_ref=source,
                              components=(package,), composition_lock=composition))
    monkeypatch.setattr(module, "ApplicationStore", lambda _: SimpleNamespace(
        list_runtime_selections=lambda: channel.read(), get_release=lambda *_: release))
    return ctx, channel, stable, manifest, release


def test_settings_are_typed_revisioned_and_survive_new_context(setup):
    ctx, _channel, _stable, _, _member = setup
    service = ApplicationRuntimeConfiguration(ctx)
    assert service.read() == {"revision": 0, "values": {"page_size": 10}}
    assert service.write({"page_size": 20}, expected_revision=0)["revision"] == 1
    assert ApplicationRuntimeConfiguration(ctx).read()["values"] == {"page_size": 20}
    with pytest.raises(ConfigurationConflict):
        service.write({"page_size": 30}, expected_revision=0)
    with pytest.raises(ConfigurationConflict):
        service.write({"page_size": 500}, expected_revision=1)


def test_beta_bindings_preserve_overrides_and_adopt_without_exposing_credentials(setup):
    ctx, channel, stable, _manifest, _member = setup
    service = ApplicationRuntimeConfiguration(ctx)
    service.write({"page_size": 20}, expected_revision=0)
    store = ApplicationConfigurationStore(ctx.paths.state_dir(), "sample", "skill:worker")
    store.set_stable(release_digest=DIGEST, schema=SCHEMA, values={"page_size": 20},
                     credentials={"token": "credential:opaque-reference"}, expected_revision=1)
    store.prepare_beta(candidate_id="candidate", release_digest=DIGEST, source_release_digest=DIGEST,
                       schema=SCHEMA, defaults={"page_size": 10}, expected_revision=2)
    beta = replace(stable, source="local_trial", runtime_root_ref="trial:candidate", revision=2)
    channel.select(beta, expected_revision=1)
    with pytest.raises(ConfigurationConflict, match="inactive"):
        service.read()
    ctx.paths.runtime_channel_ref = "trial:candidate"
    result = service.write({"page_size": 25}, expected_revision=3)
    assert result == {"revision": 4, "values": {"page_size": 25}}
    assert "credential" not in json.dumps(service.read())
    assert store.read()["beta"]["credentials"] == {"token": "credential:opaque-reference"}
    store.adopt_beta(candidate_id="candidate", expected_revision=4)
    channel.select(replace(stable, revision=3), expected_revision=2)
    ctx.paths.runtime_channel_ref = "workspace"
    assert service.read()["values"] == {"page_size": 25}


def test_development_and_shared_component_cannot_read_production_settings(setup, tmp_path):
    ctx, _channel, _stable, manifest, release = setup
    service = ApplicationRuntimeConfiguration(ctx)
    original = release.project_release
    member = original.composition_lock.members[0]
    release.project_release = replace(original, composition_lock=replace(original.composition_lock, members=(replace(member, lifecycle="shared"),)))
    with pytest.raises(ConfigurationConflict, match="owning Application"):
        service.read()
    release.project_release = original
    dev = tmp_path / "dev/worker"
    dev.mkdir(parents=True)
    (dev / "skill.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    ctx.skill_ctx.get().path = dev
    with pytest.raises(ConfigurationConflict, match="DEV stays isolated"):
        service.read()


def test_manifest_capability_and_profile_denial_are_not_bypassed(setup):
    ctx, _channel, _stable, manifest, _member = setup
    service = ApplicationRuntimeConfiguration(ctx)
    manifest["capabilities"] = []
    path = ctx.skill_ctx.get().path / "skill.yaml"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(SkillCapabilityAdmissionError, match="not_declared"):
        service.read()
    manifest["capabilities"] = ["configuration.read"]
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    profile = ctx.paths.state_dir() / "capabilities/skill_grants.json"
    profile.parent.mkdir()
    profile.write_text(json.dumps({"schema": "adaos.skill_capability_grants.v1",
        "subjects": {"skill:worker": {"deny": ["configuration.read"]}}}), encoding="utf-8")
    with pytest.raises(SkillCapabilityAdmissionError, match="profile_denied"):
        service.read()


def test_settings_cannot_change_while_cutover_is_fenced(setup):
    ctx, channel, stable, _manifest, _member = setup
    service = ApplicationRuntimeConfiguration(ctx)

    def migrate(_key):
        with pytest.raises(RuntimeChannelConflict, match="fenced"):
            service.write({"page_size": 20}, expected_revision=0)
        return {"ok": True}

    ApplicationRuntimeTransition(channel).run("fence", contract_digest=DIGEST, expected=[stable],
        target=replace(stable, revision=2), steps=[TransitionStep("migrate", migrate)])


def test_configuration_requires_explicit_schema_migration(setup):
    ctx, _channel, _stable, manifest, _member = setup
    service = ApplicationRuntimeConfiguration(ctx)
    service.write({"page_size": 20}, expected_revision=0)
    manifest["configuration"]["schema"] = {**SCHEMA, "title": "changed schema"}
    (ctx.skill_ctx.get().path / "skill.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(ConfigurationConflict, match="explicit release/schema migration"):
        service.read()


def test_dev_uses_same_settings_sdk_without_loading_real_installation(setup, tmp_path, monkeypatch):
    from adaos.services.applications import runtime_configuration as module
    ctx, _channel, _stable, manifest, _release = setup
    production = ApplicationRuntimeConfiguration(ctx)
    production.write({"page_size": 40}, expected_revision=0)
    original = ctx.skill_ctx.get().path
    dev = tmp_path / "dev/subnet/skills"
    path = dev / ".runtime/worker/v0.1/slot-a"
    path.mkdir(parents=True)
    (path / "skill.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    ctx.paths.dev_skills_dir = lambda: dev
    ctx.skill_ctx.get().path = path
    with monkeypatch.context() as patch:
        patch.setattr(module, "ApplicationStore", lambda _: pytest.fail("DEV must not inspect real installations"))
        development = ApplicationRuntimeConfiguration(ctx)
        assert development.read() == {"revision": 0, "values": {"page_size": 10}}
        development.write({"page_size": 15}, expected_revision=0)
        assert ApplicationRuntimeConfiguration(ctx).read() == {"revision": 1, "values": {"page_size": 15}}
    ctx.skill_ctx.get().path = original
    assert production.read()["values"] == {"page_size": 40}
