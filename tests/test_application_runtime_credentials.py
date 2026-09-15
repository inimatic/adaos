import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml

from test_application_runtime_configuration import setup, SCHEMA, DIGEST
from adaos.domain.personalization_access import SubjectRef
from adaos.services.applications.configuration import ApplicationConfigurationStore, ConfigurationConflict
from adaos.services.applications.runtime_configuration import ApplicationRuntimeConfiguration
from adaos.services.applications.runtime_credentials import ApplicationRuntimeCredentials
from adaos.services.applications.runtime_channel import RuntimeChannelConflict
from adaos.services.applications.runtime_transition import ApplicationRuntimeTransition, TransitionStep
from adaos.services.policy.caller import verified_caller
from adaos.services.policy.skill_capabilities import SkillCapabilityAdmissionError


class Vault:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def put(self, key, value):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


@pytest.fixture
def credentials(setup):
    ctx, channel, stable, manifest, release = setup
    ctx.settings = SimpleNamespace(owner_id="owner")
    ctx.credential_vault = Vault()
    ctx.secrets = SimpleNamespace(get=lambda *_: pytest.fail("No process-wide secret fallback"))
    manifest["capabilities"].extend(["secrets.read", "secrets.write"])
    manifest["configuration"]["credentials"] = {"token": {"purpose": "Read an explicitly configured external API"}}
    (ctx.skill_ctx.get().path / "skill.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with verified_caller(SubjectRef("user", "owner")):
        yield ctx, channel, stable, manifest, release


def test_credentials_inherit_and_adopt_without_plaintext_in_configuration(credentials):
    ctx, channel, stable, manifest, _release = credentials
    secrets = ApplicationRuntimeCredentials(ctx)
    secrets.put("token", "synthetic-stable-secret")
    store = ApplicationConfigurationStore(ctx.paths.state_dir(), "sample", "skill:worker")
    original = store.read()["stable"]["credentials"]
    assert secrets.get("token") == "synthetic-stable-secret"
    store.prepare_beta(candidate_id="candidate", release_digest=DIGEST, source_release_digest=DIGEST,
                       schema=SCHEMA, defaults={"page_size": 10}, expected_revision=store.read()["revision"])
    channel.select(replace(stable, source="local_trial", runtime_root_ref="trial:candidate", revision=2), expected_revision=1)
    with pytest.raises(ConfigurationConflict, match="inactive"):
        secrets.get("token")
    ctx.paths.runtime_channel_ref = "trial:candidate"
    assert ApplicationRuntimeCredentials(ctx).get("token") == "synthetic-stable-secret"
    secrets.put("token", "synthetic-beta-secret")
    assert store.read()["stable"]["credentials"] == original
    assert store.read()["beta"]["credentials"] != original
    store.adopt_beta(candidate_id="candidate", expected_revision=store.read()["revision"])
    channel.select(replace(stable, revision=3), expected_revision=2)
    ctx.paths.runtime_channel_ref = "workspace"
    assert ApplicationRuntimeCredentials(ctx).get("token") == "synthetic-beta-secret"
    assert "synthetic-stable-secret" not in store.path.read_text(encoding="utf-8")
    assert "synthetic-beta-secret" not in json.dumps(manifest)
    assert "credential:" not in json.dumps(ApplicationRuntimeConfiguration(ctx).read())


def test_revocation_is_live_and_snapshot_bindings_cannot_resurrect_value(credentials):
    ctx, channel, stable, _manifest, _release = credentials
    service = ApplicationRuntimeCredentials(ctx)
    service.put("token", "synthetic-one")
    key, raw = next(iter(ctx.credential_vault.values.items()))
    ctx.credential_vault.values[key] = json.dumps({**json.loads(raw), "value": "synthetic-rotated"})
    assert service.get("token") == "synthetic-rotated"
    store = ApplicationConfigurationStore(ctx.paths.state_dir(), "sample", "skill:worker")
    store.prepare_beta(candidate_id="candidate", release_digest=DIGEST, source_release_digest=DIGEST,
                       schema=SCHEMA, defaults={"page_size": 10}, expected_revision=store.read()["revision"])
    channel.select(replace(stable, source="local_trial", runtime_root_ref="trial:candidate", revision=2), expected_revision=1)
    ctx.paths.runtime_channel_ref = "trial:candidate"
    service.delete("token")
    assert service.get("token") is None
    store.deactivate_beta(candidate_id="candidate", expected_revision=store.read()["revision"])
    channel.select(replace(stable, revision=3), expected_revision=2)
    ctx.paths.runtime_channel_ref = "workspace"
    assert service.get("token") is None
    assert key not in ctx.credential_vault.values


@pytest.mark.parametrize("caller", [None, SubjectRef("user", "guest"), SubjectRef("service", "owner")])
def test_only_verified_owner_can_resolve_or_change_credentials(credentials, caller):
    ctx, *_ = credentials
    service = ApplicationRuntimeCredentials(ctx)
    with verified_caller(caller):
        with pytest.raises(PermissionError, match="verified local owner"):
            service.get("token")
        with pytest.raises(PermissionError, match="verified local owner"):
            service.put("token", "synthetic")
    assert not ctx.credential_vault.values


def test_declared_slots_profile_and_vault_availability_fail_closed(credentials):
    ctx, _channel, _stable, manifest, _release = credentials
    service = ApplicationRuntimeCredentials(ctx)
    with pytest.raises(PermissionError, match="purpose must be declared"):
        service.put("undeclared", "synthetic")
    manifest["capabilities"].remove("secrets.read")
    path = ctx.skill_ctx.get().path / "skill.yaml"
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(SkillCapabilityAdmissionError, match="not_declared"):
        service.get("token")
    manifest["capabilities"].append("secrets.read")
    path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    ctx.credential_vault = None
    from adaos.sdk.data.secrets import _active_secrets_service
    with pytest.raises(PermissionError, match="no legacy fallback"):
        _active_secrets_service(ctx).get("token")


def test_another_owner_envelope_is_not_a_valid_credential(credentials):
    ctx, *_ = credentials
    service = ApplicationRuntimeCredentials(ctx)
    service.put("token", "synthetic")
    key, raw = next(iter(ctx.credential_vault.values.items()))
    record = json.loads(raw)
    record["identity"]["application_id"] = "another_application"
    ctx.credential_vault.values[key] = json.dumps(record)
    with pytest.raises(PermissionError, match="ownership record"):
        service.get("token")


def test_copied_reference_cannot_resolve_from_another_application(credentials, monkeypatch):
    from adaos.services.applications import runtime_configuration
    from adaos.services.applications.runtime_channel import ApplicationRuntimeChannel
    ctx, _channel, stable, _manifest, release = credentials
    service = ApplicationRuntimeCredentials(ctx)
    service.put("token", "synthetic-private")
    original = ApplicationConfigurationStore(ctx.paths.state_dir(), "sample", "skill:worker").read()
    channel = ApplicationRuntimeChannel(ctx.paths.state_dir(), "another_application")
    channel.select(replace(stable, application_id="another_application"), expected_revision=0)
    monkeypatch.setattr(runtime_configuration, "ApplicationStore", lambda _: SimpleNamespace(
        list_runtime_selections=lambda: channel.read(), get_release=lambda *_: release))
    target = ApplicationConfigurationStore(ctx.paths.state_dir(), "another_application", "skill:worker")
    target.set_stable(release_digest=DIGEST, schema=SCHEMA, values={"page_size": 10},
                      credentials=original["stable"]["credentials"], expected_revision=0)
    assert service.get("token") is None


def test_profile_denial_overrides_declared_secrets_capability(credentials):
    ctx, *_ = credentials
    profile = ctx.paths.state_dir() / "capabilities/skill_grants.json"
    profile.parent.mkdir()
    profile.write_text(json.dumps({"schema": "adaos.skill_capability_grants.v1", "subjects": {
        "skill:worker": {"deny": ["secrets.read", "secrets.write"]}}}), encoding="utf-8")
    with pytest.raises(SkillCapabilityAdmissionError, match="profile_denied"):
        ApplicationRuntimeCredentials(ctx).get("token")
    with pytest.raises(SkillCapabilityAdmissionError, match="profile_denied"):
        ApplicationRuntimeCredentials(ctx).put("token", "synthetic")


def test_development_credentials_never_lookup_production_installations(credentials, tmp_path, monkeypatch):
    from adaos.services.applications import runtime_configuration
    ctx, _channel, _stable, manifest, _release = credentials
    service = ApplicationRuntimeCredentials(ctx)
    service.put("token", "synthetic-production")
    original = ctx.skill_ctx.get().path
    dev = tmp_path / "dev/subnet/skills"
    path = dev / ".runtime/worker/v0.1/slot-a"
    path.mkdir(parents=True)
    (path / "skill.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    ctx.paths.dev_skills_dir = lambda: dev
    ctx.skill_ctx.get().path = path
    with monkeypatch.context() as patch:
        patch.setattr(runtime_configuration, "ApplicationStore", lambda _: pytest.fail("No production inspection in DEV"))
        assert service.get("token") is None
        service.put("token", "synthetic-development")
        assert service.get("token") == "synthetic-development"
    ctx.skill_ctx.get().path = original
    assert service.get("token") == "synthetic-production"


def test_transition_fence_prevents_secret_read_or_write(credentials):
    ctx, channel, stable, _manifest, _release = credentials
    service = ApplicationRuntimeCredentials(ctx)

    def check(_key):
        with pytest.raises(RuntimeChannelConflict, match="fenced"):
            service.get("token")
        with pytest.raises(RuntimeChannelConflict, match="fenced"):
            service.put("token", "synthetic")
        return {"ok": True}

    ApplicationRuntimeTransition(channel).run("test-fence", contract_digest=DIGEST, expected=[stable],
        target=replace(stable, revision=2), steps=[TransitionStep("check", check)])
    assert not ctx.credential_vault.values
