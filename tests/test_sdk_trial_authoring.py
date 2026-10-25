from dataclasses import replace
import inspect
from types import SimpleNamespace

import pytest

from adaos.services.agent_context import get_ctx, use_ctx
from adaos.services.applications.trial_runtime import NativeTrialRuntime, TrialRuntimeUnavailable
from adaos.services.policy.caller import CallerAccessDenied
from adaos.sdk.core import authoring
from adaos.adapters.sdk.inproc_skill_context import _current_skill


def _contexts(tmp_path):
    from adaos.adapters.fs.path_provider import PathProvider
    current = get_ctx()
    owner = replace(current, paths=PathProvider(current.settings))
    trial = NativeTrialRuntime(owner, "authoring-test", "sha256:" + "a" * 64, tmp_path / "trial", ()).context()
    return owner, trial


def _allow(monkeypatch):
    calls = []
    monkeypatch.setattr("adaos.services.policy.skill_capabilities.require_skill_capability",
                        lambda ctx, cap: calls.append((ctx, cap)))
    monkeypatch.setattr("adaos.sdk.access.require", lambda cap: calls.append((get_ctx(), cap)))
    return calls


def test_ordinary_trial_paths_remain_immutable(tmp_path):
    _, trial = _contexts(tmp_path)
    with use_ctx(trial), pytest.raises(TrialRuntimeUnavailable, match="DEV is unavailable"):
        trial.paths.dev_dir()


def test_authoring_without_skill_scope_does_not_delegate(tmp_path):
    _, trial = _contexts(tmp_path)
    with use_ctx(trial), pytest.raises(CallerAccessDenied, match="skill_scope_missing"):
        with authoring.development_context("test"):
            pytest.fail("unbound Trial acquired platform authority")


def test_authorized_operation_delegates_only_for_the_call_and_keeps_identity(tmp_path, monkeypatch):
    owner, trial = _contexts(tmp_path)
    calls = _allow(monkeypatch)
    skill = SimpleNamespace(name="qualified_builder")
    token = _current_skill.set(skill)
    try:
        with use_ctx(trial):
            private_state = trial.paths.state_dir()
            with authoring.development_context("test") as delegated:
                assert delegated is owner
                assert get_ctx().skill_ctx.get() is skill
                assert get_ctx().paths.dev_dir() == owner.paths.dev_dir()
            assert get_ctx() is trial
            assert get_ctx().paths.state_dir() == private_state
            with pytest.raises(TrialRuntimeUnavailable):
                trial.paths.dev_dir()
        assert calls == [(trial, "applications.develop"), (owner, "applications.develop")]
    finally:
        _current_skill.reset(token)


def test_denied_capability_cannot_delegate_even_for_an_authenticated_owner(tmp_path, monkeypatch):
    owner, trial = _contexts(tmp_path)
    calls = _allow(monkeypatch)
    def deny(ctx, cap):
        raise PermissionError("capability not declared")
    monkeypatch.setattr("adaos.services.policy.skill_capabilities.require_skill_capability", deny)
    token = _current_skill.set(SimpleNamespace(name="chat"))
    try:
        with use_ctx(trial), pytest.raises(PermissionError, match="not declared"):
            with authoring.development_context("test"):
                pytest.fail("ordinary app acquired DEV")
        assert calls == []
    finally:
        _current_skill.reset(token)


def test_wrapping_preserves_signature_contract_and_async_context(tmp_path, monkeypatch):
    import asyncio
    owner, trial = _contexts(tmp_path)
    _allow(monkeypatch)
    async def original(project_id: str, *, limit: int = 10):
        assert get_ctx() is owner
        await asyncio.sleep(0)
        assert get_ctx() is owner
        return project_id, limit
    original.__adaos_contract__ = {"effects": ["read_only"]}
    operation = authoring._govern(original)
    assert inspect.signature(operation) == inspect.signature(original)
    assert operation.__adaos_contract__ == original.__adaos_contract__
    token = _current_skill.set(SimpleNamespace(name="builder"))
    try:
        with use_ctx(trial):
            assert asyncio.run(operation("project", limit=4)) == ("project", 4)
            assert get_ctx() is trial
    finally:
        _current_skill.reset(token)


def test_pure_helpers_and_execution_ports_are_not_delegated():
    from adaos.sdk.builder import prototype, semantic_ui
    from adaos.sdk.developer import compositions, prototypes
    for function in (prototype.compile_semantic, prototype.submit_request, prototype.candidate_status,
                     prototype.start_data_runtime, semantic_ui.replay, compositions.validate,
                     compositions.normalized_definition, prototypes.derive_resource_specs):
        assert not getattr(function, "__adaos_authoring_operation__", False)
    for function in (semantic_ui.propose, prototype.reconcile_semantic_revision,
                     compositions.get, prototypes.materialize_resources):
        assert getattr(function, "__adaos_authoring_operation__", False)


def test_real_source_read_uses_platform_dev_but_restores_private_trial_state(tmp_path, monkeypatch):
    from adaos.sdk.developer import projects
    owner, trial = _contexts(tmp_path)
    _allow(monkeypatch)
    root = owner.paths.dev_scenarios_dir() / "owned_project"
    root.mkdir(parents=True)
    (root / "scenario.yaml").write_text("id: owned_project\nversion: 1.0.0\n", encoding="utf-8")
    token = _current_skill.set(SimpleNamespace(name="qualified_builder"))
    try:
        with use_ctx(trial):
            result = projects.read_file("scenario", "owned_project", "scenario.yaml")
            assert "id: owned_project" in result["content"]
            assert get_ctx() is trial
            with pytest.raises(TrialRuntimeUnavailable):
                get_ctx().paths.dev_dir()
    finally:
        _current_skill.reset(token)


def test_verified_caller_is_still_required_after_manifest_admission(tmp_path, monkeypatch):
    from adaos.services.policy.caller import verified_caller
    _, trial = _contexts(tmp_path)
    monkeypatch.setattr("adaos.services.policy.skill_capabilities.require_skill_capability", lambda *args: None)
    token = _current_skill.set(SimpleNamespace(name="qualified_builder"))
    try:
        with use_ctx(trial), verified_caller(None), pytest.raises(CallerAccessDenied, match="not_authenticated"):
            with authoring.development_context("test"):
                pytest.fail("anonymous caller acquired DEV")
    finally:
        _current_skill.reset(token)


def test_real_sealed_manifest_without_development_capability_is_denied(tmp_path, monkeypatch):
    from adaos.services.policy.skill_capabilities import SkillCapabilityAdmissionError
    _, trial = _contexts(tmp_path)
    source = tmp_path / "chat-package"
    source.mkdir()
    (source / "skill.yaml").write_text("name: chat\ncapabilities: [workspace.read]\n", encoding="utf-8")
    monkeypatch.setattr("adaos.sdk.access.require", lambda _: pytest.fail("undeclared capability must fail first"))
    token = _current_skill.set(SimpleNamespace(name="chat", path=source))
    try:
        with use_ctx(trial), pytest.raises(SkillCapabilityAdmissionError, match="not_declared"):
            with authoring.development_context("test"):
                pytest.fail("ordinary Trial acquired DEV")
    finally:
        _current_skill.reset(token)


def test_execution_port_never_reuses_a_stable_manager_for_a_trial(tmp_path, monkeypatch):
    from adaos.adapters.builder.legacy_dev_skill import PersonalChatGPTPrototypeExecution
    owner, trial = _contexts(tmp_path)
    monkeypatch.setattr("adaos.adapters.db.SqliteSkillRegistry", lambda _: object())
    monkeypatch.setattr("adaos.services.skill.manager.SkillManager", lambda **kwargs: SimpleNamespace(context=get_ctx()))
    port = PersonalChatGPTPrototypeExecution()
    with use_ctx(owner):
        stable = port._manager()
        assert port._manager() is stable
    with use_ctx(trial):
        beta = port._manager()
        assert beta is not stable
        assert beta.context is trial
        assert port._manager() is beta
    with use_ctx(owner):
        assert port._manager() is not beta


def test_trial_candidate_observation_never_resolves_mutable_dev(tmp_path, monkeypatch):
    from adaos.sdk.builder import prototype
    from adaos.adapters.builder.legacy_dev_skill import WorkspaceSkillPrototypeExecution
    _, trial = _contexts(tmp_path)
    class Manager:
        def run_tool(self, skill, tool, payload, **kwargs):
            assert get_ctx() is trial
            assert (skill, tool) == ("builder_skill", "get_session")
            assert payload == {"session_id": "personal-session", "webspace_id": "host"}
            return {"status": "complete"}
        def run_dev_tool(self, *args, **kwargs):
            pytest.fail("Trial session observation must not execute mutable DEV")
    port = WorkspaceSkillPrototypeExecution()
    port._skill_manager = Manager()
    monkeypatch.setattr(prototype, "_WORKSPACE_PORT", port)
    monkeypatch.setattr(prototype, "_compatibility_execution_port",
                        lambda: pytest.fail("Trial observation must not fall back"))
    with use_ctx(trial):
        result = prototype.candidate_status("personal-session", webspace_id="host")
    assert result["status"] == "complete"
    assert result["sdk"]["execution_adapter"] == "workspace_skill.v1"


def test_authoring_failure_restores_the_invoking_trial(tmp_path, monkeypatch):
    owner, trial = _contexts(tmp_path)
    _allow(monkeypatch)
    def operation():
        assert get_ctx() is owner
        raise ValueError("source conflict")
    token = _current_skill.set(SimpleNamespace(name="qualified_builder"))
    try:
        with use_ctx(trial):
            with pytest.raises(ValueError, match="source conflict"):
                authoring._govern(operation)()
            assert get_ctx() is trial
    finally:
        _current_skill.reset(token)
