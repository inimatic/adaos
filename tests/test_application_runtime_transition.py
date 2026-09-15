from dataclasses import replace

import pytest

from adaos.domain.application import RuntimeSelection
from adaos.services.applications.runtime_channel import ApplicationRuntimeChannel, RuntimeChannelConflict
from adaos.services.applications.runtime_transition import ApplicationRuntimeTransition, TransitionStep


DIGEST = "sha256:" + "a" * 64


def initial(root):
    channel = ApplicationRuntimeChannel(root, "sample")
    stable = RuntimeSelection(webspace_id="desktop", application_id="sample", source="stable_installation",
                              release_digest=DIGEST, runtime_root_ref="workspace", revision=1)
    channel.select(stable, expected_revision=0)
    beta = replace(stable, source="local_trial", runtime_root_ref="trial:candidate", revision=2)
    return channel, stable, beta


def test_transition_fences_both_channels_and_commits_all_webspaces(tmp_path):
    channel, stable, beta = initial(tmp_path)
    channel.select(replace(stable, webspace_id="mobile"), expected_revision=0)
    expected = channel.read()
    effects = []

    def migrate(key):
        for root in ["workspace", "trial:candidate"]:
            with pytest.raises(RuntimeChannelConflict, match="fenced"):
                with channel.execution(root, DIGEST):
                    pytest.fail("No runtime may run during migration")
        with pytest.raises(RuntimeChannelConflict, match="fenced"):
            channel.select(beta, expected_revision=1)
        effects.append(key)
        return {"ok": True, "snapshot_digest": DIGEST}

    steps = [TransitionStep("snapshot-and-migrate", migrate)]
    runner = ApplicationRuntimeTransition(channel)
    result = runner.run("cycle1", contract_digest=DIGEST, expected=expected, target=beta, steps=steps)
    assert result["completed"]
    assert len(effects) == 1
    assert {v.runtime_root_ref for v in channel.read()} == {"trial:candidate"}
    assert {v.revision for v in channel.read()} == {2}
    with channel.execution("trial:candidate", DIGEST):
        pass
    assert runner.run("cycle1", contract_digest=DIGEST, expected=expected, target=beta, steps=steps) == result
    assert len(effects) == 1


def test_crash_retains_fence_and_resumes_only_unacknowledged_effect(tmp_path):
    channel, stable, beta = initial(tmp_path)
    calls = []
    fail = True

    def snapshot(key):
        calls.append(("snapshot", key))
        return {"ok": True}

    def migrate(key):
        calls.append(("migrate", key))
        if fail:
            raise SystemExit("simulated process interruption after effect")
        return {"ok": True}

    steps = [TransitionStep("snapshot", snapshot), TransitionStep("migrate", migrate)]
    with pytest.raises(SystemExit):
        ApplicationRuntimeTransition(channel).run("cycle1", contract_digest=DIGEST, expected=[stable], target=beta, steps=steps)
    restored = ApplicationRuntimeChannel(tmp_path, "sample")
    with pytest.raises(RuntimeChannelConflict, match="fenced"):
        with restored.execution("workspace", DIGEST):
            pass
    fail = False
    result = ApplicationRuntimeTransition(restored).run("cycle1", contract_digest=DIGEST, expected=[stable], target=beta, steps=steps)
    assert result["completed"]
    assert [name for name, _ in calls] == ["snapshot", "migrate", "migrate"]
    assert calls[1][1] == calls[2][1]


def test_pending_transition_cannot_be_replaced_or_relabelled(tmp_path):
    channel, stable, beta = initial(tmp_path)
    runner = ApplicationRuntimeTransition(channel)
    steps = [TransitionStep("effect", lambda _: {"ok": False})]
    with pytest.raises(RuntimeChannelConflict, match="not verified"):
        runner.run("cycle1", contract_digest=DIGEST, expected=[stable], target=beta, steps=steps)
    with pytest.raises(RuntimeChannelConflict, match="different intent"):
        runner.run("cycle1", contract_digest="sha256:" + "b" * 64, expected=[stable], target=beta, steps=steps)
    with pytest.raises(RuntimeChannelConflict, match="fenced"):
        runner.run("cycle2", contract_digest=DIGEST, expected=[stable], target=beta, steps=steps)


def test_busy_runtime_prevents_any_effect_and_stale_intent_is_rejected(tmp_path):
    channel, stable, beta = initial(tmp_path)
    runner = ApplicationRuntimeTransition(channel)
    steps = [TransitionStep("effect", lambda _: pytest.fail("Must not start effects"))]
    with channel.execution("workspace", DIGEST):
        with pytest.raises(RuntimeChannelConflict, match="executing"):
            runner.run("cycle1", contract_digest=DIGEST, expected=[stable], target=beta, steps=steps)
    channel.select(beta, expected_revision=1)
    with pytest.raises(RuntimeChannelConflict, match="changed before"):
        runner.run("cycle1", contract_digest=DIGEST, expected=[stable], target=beta, steps=steps)


def test_new_release_components_are_fenced_before_selection_commits(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from adaos.services.applications import runtime_selection

    channel, stable, beta = initial(tmp_path)
    beta = replace(beta, release_digest="sha256:" + "b" * 64)

    def release(_app, digest):
        name = "new_skill" if digest == beta.release_digest else "old_skill"
        return SimpleNamespace(project_release=SimpleNamespace(components=[SimpleNamespace(kind="skill", artifact_id=name)]))

    monkeypatch.setattr(runtime_selection, "ApplicationStore", lambda _: SimpleNamespace(
        list_runtime_selections=lambda: channel.read(), get_release=release))
    ctx = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path, runtime_channel_ref="trial:candidate"))

    def activate(_key):
        assert ApplicationRuntimeChannel.list_selections(tmp_path) == (stable,)
        assert beta in ApplicationRuntimeChannel.list_selections(tmp_path, include_pending=True)
        with pytest.raises(RuntimeChannelConflict, match="fenced"):
            with runtime_selection.application_execution(ctx, "new_skill"):
                pytest.fail("New skill must not escape pending application cutover")
        return {"ok": True}

    ApplicationRuntimeTransition(channel).run("cycle1", contract_digest=DIGEST, expected=[stable], target=beta,
                                               steps=[TransitionStep("activate", activate)])
    assert ApplicationRuntimeChannel.list_selections(tmp_path, include_pending=True) == (beta,)
