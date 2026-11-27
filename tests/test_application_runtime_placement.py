from dataclasses import replace
from types import SimpleNamespace

import pytest

from adaos.domain.application import RuntimeSelection
from adaos.services.applications import runtime_placement
from adaos.services.applications.runtime_channel import ApplicationRuntimeChannel, RuntimeChannelConflict
from adaos.services.applications.trial_runtime import TrialRuntimeUnavailable
from adaos.services.agent_context import get_ctx


@pytest.fixture
def placement(tmp_path, monkeypatch):
    selected = RuntimeSelection(webspace_id="desktop", application_id="sample", source="local_trial",
        release_digest="sha256:" + "a" * 64, runtime_root_ref="trial:sample-beta", revision=4)
    channel = ApplicationRuntimeChannel(tmp_path, "sample")
    channel.select(replace(selected, revision=1), expected_revision=0)
    calls = []
    admission = {"admission": "admitted", "admission_kind": "installed_local_beta"}
    before_save = []

    class Store:
        def list_runtime_selections(self):
            return channel.read()

        def get_application(self, application_id):
            return SimpleNamespace(entrypoints=({"presentation_ref": "scenario:editor"},))

        def release_has_component(self, application_id, digest, **kwargs):
            return kwargs == {"kind": "scenario", "artifact_id": "editor"}

        def save_runtime_selection(self, value, **kwargs):
            calls.append((value, kwargs))
            for effect in before_save:
                effect(value)
            return channel.select(value, **kwargs)

        def get_runtime_selection(self, room, app):
            return next(item for item in channel.read() if item.webspace_id == room)

    monkeypatch.setattr(runtime_placement, "ApplicationStore", lambda _: Store())
    monkeypatch.setattr(runtime_placement, "get_workspace", lambda _: SimpleNamespace(is_dev=False))
    monkeypatch.setattr(runtime_placement, "observe_runtime_admission", lambda *_, **__: admission)
    return SimpleNamespace(ctx=get_ctx(), channel=channel, calls=calls, admission=admission, before_save=before_save)


def test_open_new_desktop_joins_exact_installed_beta_and_keeps_existing_placements(placement):
    before = placement.channel.read()
    assert runtime_placement.attach_scenario_runtime(placement.ctx, "second", "editor")
    after = placement.channel.read()
    assert tuple(item for item in after if item.webspace_id != "second") == before
    target = next(item for item in after if item.webspace_id == "second")
    assert (target.source, target.release_digest, target.runtime_root_ref) == (
        before[0].source, before[0].release_digest, before[0].runtime_root_ref)
    assert target.revision == 1
    assert not runtime_placement.attach_scenario_runtime(placement.ctx, "second", "editor")
    assert len(placement.calls) == 1


@pytest.mark.parametrize("unavailable", ["temporary", "expired", "fenced"])
def test_open_cannot_extend_temporary_or_unavailable_trial(placement, unavailable):
    if unavailable == "temporary":
        placement.admission["admission_kind"] = "temporary_trial"
    else:
        placement.admission.update(admission=unavailable, reason="NotAdmitted")
    with pytest.raises(TrialRuntimeUnavailable):
        runtime_placement.attach_scenario_runtime(placement.ctx, "second", "editor")
    assert placement.calls == []


def test_open_dev_or_unrelated_scenario_does_not_attach_beta(placement, monkeypatch):
    assert not runtime_placement.attach_scenario_runtime(placement.ctx, "second", "other")
    monkeypatch.setattr(runtime_placement, "get_workspace", lambda _: SimpleNamespace(is_dev=True))
    assert not runtime_placement.attach_scenario_runtime(placement.ctx, "dev", "editor")
    assert placement.calls == []


@pytest.mark.parametrize("change", ["cutover", "retirement"])
def test_concurrent_channel_change_cannot_be_reversed_by_opening_desktop(placement, change):
    def change_channel(_):
        if change == "retirement":
            placement.channel.retire()
        else:
            current = placement.channel.read()[0]
            placement.channel.select(replace(current, revision=current.revision + 1,
                release_digest="sha256:" + "b" * 64, runtime_root_ref="trial:other-beta"),
                expected_revision=current.revision)
    placement.before_save.append(change_channel)
    with pytest.raises(RuntimeChannelConflict):
        runtime_placement.attach_scenario_runtime(placement.ctx, "second", "editor")
    assert all(item.webspace_id != "second" for item in placement.channel.read())


def test_concurrent_same_channel_open_is_idempotent(placement):
    placement.before_save.append(lambda value: placement.channel.select(value, expected_revision=0))
    assert runtime_placement.attach_scenario_runtime(placement.ctx, "second", "editor")
    assert len(placement.channel.read()) == 2


def test_stable_channel_does_not_need_a_beta_placement(placement):
    current = placement.channel.read()[0]
    placement.channel.select(replace(current, source="stable_installation", runtime_root_ref="workspace", revision=2),
        expected_revision=1)
    assert not runtime_placement.attach_scenario_runtime(placement.ctx, "second", "editor")
    assert placement.calls == []


def test_explicit_core_home_is_not_claimed_by_an_unrelated_temporary_trial(placement, monkeypatch):
    placement.admission["admission_kind"] = "temporary_trial"
    store = runtime_placement.ApplicationStore(None)
    store.release_has_component = lambda *_, **__: True
    store.get_application = lambda _: SimpleNamespace(entrypoints=({"presentation_ref":"scenario:web_desktop"},))
    monkeypatch.setattr(runtime_placement, "ApplicationStore", lambda _: store)
    # The same temporary scenario remains confined outside its admitted room.
    with pytest.raises(TrialRuntimeUnavailable):
        runtime_placement.attach_scenario_runtime(placement.ctx, "second", "web_desktop")
    before = placement.channel.read()
    monkeypatch.setattr(runtime_placement, "get_workspace", lambda _: SimpleNamespace(
        is_dev=False, effective_home_scenario="web_desktop",
        home_scenario_ref_overlay={"application_id":"core:desktop","scenario_id":"web_desktop"}))
    store.list_runtime_selections = lambda: pytest.fail("Core Home must not scan unrelated Application placements")
    assert not runtime_placement.attach_scenario_runtime(placement.ctx, "second", "web_desktop")
    assert placement.channel.read() == before
    assert placement.calls == []


@pytest.mark.parametrize("home_ref", [{}, {"scenario_id": "web_desktop", "application_id": "sample"}])
def test_go_home_distinguishes_a_legacy_desktop_from_an_explicit_application(placement, monkeypatch, home_ref):
    placement.admission["admission_kind"] = "temporary_trial"
    store = runtime_placement.ApplicationStore(None)
    store.release_has_component = lambda *_, **__: True
    store.get_application = lambda _: SimpleNamespace(entrypoints=({"presentation_ref": "scenario:web_desktop"},))
    monkeypatch.setattr(runtime_placement, "ApplicationStore", lambda _: store)
    monkeypatch.setattr(runtime_placement, "get_workspace", lambda _: SimpleNamespace(
        is_dev=False, effective_home_scenario="web_desktop", home_scenario_ref_overlay=home_ref))
    before = placement.channel.read()

    with pytest.raises(TrialRuntimeUnavailable):
        runtime_placement.attach_scenario_runtime(placement.ctx, "second", "web_desktop")
    if home_ref:
        with pytest.raises(TrialRuntimeUnavailable):
            runtime_placement.attach_scenario_runtime(placement.ctx, "second", "web_desktop", home_navigation=True)
    else:
        assert not runtime_placement.attach_scenario_runtime(placement.ctx, "second", "web_desktop", home_navigation=True)
    assert placement.channel.read() == before
    assert placement.calls == []


@pytest.mark.parametrize("versioned", [False, True])
def test_legacy_management_home_uses_the_builtin_outside_its_temporary_trial(placement, monkeypatch, versioned):
    selection = replace(placement.channel.read()[0], application_id="web_desktop")
    home = {"scenario_id": "web_desktop", "application_id": "web_desktop", "title": "Management"}
    if versioned:
        home["release_digest"] = selection.release_digest
    store = SimpleNamespace(list_runtime_selections=lambda: (selection,),
        release_has_component=lambda *_, **__: True,
        get_application=lambda _: SimpleNamespace(entrypoints=({"presentation_ref": "scenario:web_desktop"},)),
        save_runtime_selection=lambda *_args, **_kwargs: pytest.fail("Temporary Trial must stay in its desktop"))
    monkeypatch.setattr(runtime_placement, "ApplicationStore", lambda _: store)
    monkeypatch.setattr(runtime_placement, "get_workspace", lambda _: SimpleNamespace(
        is_dev=False, effective_home_scenario="web_desktop", home_scenario_ref_overlay=home))
    placement.admission["admission_kind"] = "temporary_trial"
    with pytest.raises(TrialRuntimeUnavailable):
        runtime_placement.attach_scenario_runtime(placement.ctx, "second", "web_desktop")
    if versioned:
        with pytest.raises(TrialRuntimeUnavailable):
            runtime_placement.attach_scenario_runtime(placement.ctx, "second", "web_desktop", home_navigation=True)
    else:
        assert not runtime_placement.attach_scenario_runtime(placement.ctx, "second", "web_desktop", home_navigation=True)


def test_core_home_reference_cannot_extend_an_unrelated_temporary_scenario(placement, monkeypatch):
    placement.admission["admission_kind"] = "temporary_trial"
    monkeypatch.setattr(runtime_placement, "get_workspace", lambda _: SimpleNamespace(
        is_dev=False, effective_home_scenario="web_desktop",
        home_scenario_ref_overlay={"application_id":"core:desktop","scenario_id":"web_desktop"}))
    with pytest.raises(TrialRuntimeUnavailable):
        runtime_placement.attach_scenario_runtime(placement.ctx, "second", "editor")
    assert placement.calls == []
