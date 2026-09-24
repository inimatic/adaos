from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import multiprocessing
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from adaos.domain.application import RuntimeSelection
from adaos.services.applications.runtime_channel import ApplicationRuntimeChannel, RuntimeChannelConflict
from adaos.services.applications.store import ApplicationRevisionConflict


DIGEST = "sha256:" + "a" * 64


def selection(webspace="desktop", *, beta=False, revision=1):
    return RuntimeSelection(webspace_id=webspace, application_id="sample",
        source="local_trial" if beta else "stable_installation", release_digest=DIGEST,
        runtime_root_ref="trial:candidate" if beta else "workspace", revision=revision)


def test_one_channel_across_webspaces_and_cas(tmp_path):
    channel = ApplicationRuntimeChannel(tmp_path, "sample")
    channel.select(selection(), expected_revision=0)
    channel.select(selection("mobile"), expected_revision=0)
    channel.select(selection(beta=True, revision=2), expected_revision=1)
    values = channel.read()
    assert {item.runtime_root_ref for item in values} == {"trial:candidate"}
    assert {item.revision for item in values} == {2}
    with pytest.raises(ApplicationRevisionConflict):
        channel.select(selection("mobile", revision=2), expected_revision=1)
    with pytest.raises(RuntimeChannelConflict, match="Another Webspace"):
        channel.select(selection("new"), expected_revision=0)
    with channel.execution("trial:candidate", DIGEST):
        pass
    with pytest.raises(RuntimeChannelConflict, match="inactive"):
        with channel.execution("workspace", DIGEST):
            pytest.fail("Stable must not execute")


def test_conflicting_legacy_channels_are_not_silently_reconciled(tmp_path):
    channel = ApplicationRuntimeChannel(tmp_path, "sample")
    with pytest.raises(RuntimeChannelConflict, match="explicit reconciliation"):
        channel.select(selection(revision=2), expected_revision=1,
                       legacy=[selection(), selection("mobile", beta=True)])
    assert channel.read() is None


def test_idle_legacy_selection_is_adopted_without_changing_revision(tmp_path):
    channel = ApplicationRuntimeChannel(tmp_path, "sample")
    current = selection()
    with channel.execution("workspace", DIGEST, legacy=[current]):
        assert channel.read() == (current,)


def test_list_selections_does_not_realpath_each_channel(tmp_path, monkeypatch):
    channel = ApplicationRuntimeChannel(tmp_path, "sample")
    current = selection()
    channel.select(current, expected_revision=0)

    monkeypatch.setattr(
        Path,
        "resolve",
        lambda *_args, **_kwargs: pytest.fail(
            "trusted runtime channel paths must not require filesystem realpath"
        ),
    )

    assert ApplicationRuntimeChannel.list_selections(tmp_path) == (current,)


def test_concurrent_readers_keep_cutover_blocked_even_after_caller_stops_waiting(tmp_path):
    channel = ApplicationRuntimeChannel(tmp_path, "sample")
    channel.select(selection(), expected_revision=0)
    entered = threading.Barrier(3)
    finish = threading.Event()

    def execute():
        with channel.execution("workspace", DIGEST):
            entered.wait(timeout=5)
            assert finish.wait(10)

    with ThreadPoolExecutor(max_workers=2) as pool:
        readers = [pool.submit(execute) for _ in range(2)]
        entered.wait(timeout=5)
        try:
            with pytest.raises(TimeoutError):
                readers[0].result(timeout=0.01)
            with pytest.raises(RuntimeChannelConflict, match="executing"):
                channel.select(selection(beta=True, revision=2), expected_revision=1)
            other = ApplicationRuntimeChannel(tmp_path, "unrelated")
            other.select(replace(selection(), application_id="unrelated"), expected_revision=0)
            assert channel.read()[0].runtime_root_ref == "workspace"
        finally:
            finish.set()
        for reader in readers:
            reader.result(timeout=5)
    channel.select(selection(beta=True, revision=2), expected_revision=1)


def _process_reader(root, ready, finish):
    with ApplicationRuntimeChannel(Path(root), "sample").execution("workspace", DIGEST):
        ready.set()
        assert finish.wait(10)


def test_another_process_holds_the_execution_lease(tmp_path):
    channel = ApplicationRuntimeChannel(tmp_path, "sample")
    channel.select(selection(), expected_revision=0)
    ctx = multiprocessing.get_context("spawn")
    ready, finish = ctx.Event(), ctx.Event()
    process = ctx.Process(target=_process_reader, args=(str(tmp_path), ready, finish))
    process.start()
    try:
        assert ready.wait(10)
        with pytest.raises(RuntimeChannelConflict, match="executing"):
            channel.select(selection(beta=True, revision=2), expected_revision=1)
    finally:
        finish.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0
    channel.select(selection(beta=True, revision=2), expected_revision=1)


def test_rejected_tool_releases_lease(tmp_path):
    channel = ApplicationRuntimeChannel(tmp_path, "sample")
    channel.select(selection(), expected_revision=0)
    with pytest.raises(ValueError):
        with channel.execution("workspace", DIGEST):
            raise ValueError("tool failed")
    channel.select(selection(beta=True, revision=2), expected_revision=1)


def test_native_execution_uses_node_authority_and_rejects_stale_beta(tmp_path, monkeypatch):
    from adaos.services.applications import runtime_selection

    channel = ApplicationRuntimeChannel(tmp_path, "sample")
    channel.select(selection(beta=True), expected_revision=0)
    release = SimpleNamespace(project_release=SimpleNamespace(components=[SimpleNamespace(kind="skill", artifact_id="worker")]))
    class Store:
        def __init__(self, state):
            assert state == tmp_path
        def list_runtime_selections(self):
            return channel.read()
        def get_release(self, *_args):
            return release
    monkeypatch.setattr(runtime_selection, "ApplicationStore", Store)
    stable = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path))
    beta = SimpleNamespace(authority_state_dir=tmp_path, paths=SimpleNamespace(
        state_dir=lambda: tmp_path / "isolated", runtime_channel_ref="trial:candidate"))
    with pytest.raises(RuntimeChannelConflict, match="inactive"):
        with runtime_selection.application_execution(stable, "worker"):
            pytest.fail("Stable must not execute during Beta")
    with runtime_selection.application_execution(beta, "worker"):
        with pytest.raises(RuntimeChannelConflict, match="executing"):
            channel.select(selection(revision=2), expected_revision=1)
    channel.select(selection(revision=2), expected_revision=1)
    with pytest.raises(RuntimeChannelConflict, match="inactive"):
        with runtime_selection.application_execution(beta, "worker"):
            pytest.fail("Old Beta must not execute after acceptance")
    with runtime_selection.application_execution(stable, "worker"):
        pass


def test_trial_resolution_is_node_wide_but_never_overrides_dev(tmp_path, monkeypatch):
    from adaos.services.agent_context import get_ctx
    from adaos.services.applications import runtime_selection
    from adaos.services.workspaces import index

    choices = [selection(beta=True), selection("mobile", beta=True)]
    release = SimpleNamespace(accepted_candidate_id="candidate", project_release=SimpleNamespace(
        components=[SimpleNamespace(kind="scenario", artifact_id="screen")]))
    monkeypatch.setattr(runtime_selection, "ApplicationStore", lambda _: SimpleNamespace(
        list_runtime_selections=lambda: choices, get_release=lambda *_args: release))
    monkeypatch.setattr(index, "get_workspace", lambda name: SimpleNamespace(is_dev=name == "preview"))
    calls = []
    monkeypatch.setattr(runtime_selection.NativeTrialRuntime, "resolve", lambda *args: calls.append(args) or "beta")
    assert runtime_selection.selected_trial(get_ctx(), "unplaced", "scenario", "screen") == "beta"
    assert len(calls) == 1
    assert runtime_selection.selected_trial(get_ctx(), "preview", "scenario", "screen") is None
    assert len(calls) == 1
