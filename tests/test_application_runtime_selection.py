from types import SimpleNamespace

from adaos.services.agent_context import get_ctx
from adaos.services.applications import runtime_selection


def test_exact_selection_fingerprint_does_not_treat_stable_workspace_as_trial(
    tmp_path, monkeypatch
) -> None:
    from adaos.services.artifact_pipeline import trial_activation

    release_path = tmp_path / "release.json"
    release_path.write_text("{}", encoding="utf-8")
    selection = SimpleNamespace(
        runtime_root_ref="workspace",
        revision=17,
        to_dict=lambda: {
            "webspace_id": "desktop",
            "application_id": "adaos_builder",
            "release_digest": "sha256:stable",
            "runtime_root_ref": "workspace",
            "revision": 17,
        },
    )
    store = SimpleNamespace(
        get_runtime_selection=lambda *_args: selection,
        _release_path=lambda *_args: release_path,
    )
    monkeypatch.setattr(runtime_selection, "ApplicationStore", lambda _state_dir: store)

    class _UnexpectedTrialStore:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("stable workspace must not resolve a Trial activation")

    monkeypatch.setattr(trial_activation, "TrialActivationStore", _UnexpectedTrialStore)

    fingerprint = runtime_selection._exact_trial_selection_fingerprint(
        get_ctx(),
        tmp_path,
        "desktop",
        "adaos_builder",
        "sha256:stable",
    )

    assert any(row[0] == "release" for row in fingerprint)
    assert all(not row[0].startswith("activation") for row in fingerprint)


def test_trial_launcher_uses_signed_release_icon(monkeypatch) -> None:
    selection = SimpleNamespace(
        webspace_id="desktop",
        runtime_root_ref="trial:candidate-1",
        application_id="reading-list",
        release_digest="sha256:" + "a" * 64,
        to_dict=lambda: {"application_id": "reading-list"},
    )
    application = SimpleNamespace(
        application_id="reading-list",
        display={"title": "Reading List"},
        entrypoints=({"presentation_ref": "scenario:reading_list"},),
    )
    release = SimpleNamespace(
        accepted_candidate_id="candidate-1",
        project_release=SimpleNamespace(
            version="1.2.3",
            catalog={"icon": "book-outline"},
        ),
    )
    store = SimpleNamespace(
        list_runtime_selections=lambda: [selection],
        get_application=lambda _application_id: application,
        get_release=lambda _application_id, _release_digest: release,
    )
    trial_runtime = SimpleNamespace(
        packages=(),
        component=lambda kind, component: (kind, component),
        verified_source=lambda component: component,
    )

    monkeypatch.setattr(runtime_selection, "ApplicationStore", lambda _state_dir: store)
    monkeypatch.setattr(runtime_selection, "selected_trial", lambda *_args: trial_runtime)

    entries = runtime_selection.trial_launcher_entries(get_ctx(), "desktop")

    assert entries[0]["icon"] == "book-outline"
    assert entries[0]["scenario_id"] == "reading_list"


def test_selected_application_cache_tracks_selection_and_installation_generation(
    monkeypatch,
) -> None:
    installation = SimpleNamespace(
        status="active",
        application_id="media_center",
        installed_release_digest="sha256:media",
        component_refs=({"component_ref": "scenario:media_center"},),
    )
    calls = {"installations": 0}

    class Store:
        def __init__(self, _state_dir):
            pass

        def list_runtime_selections(self):
            return ()

        def list_installations(self):
            calls["installations"] += 1
            return (installation,)

    generation = {"value": (("installations/media/current.json", 1, 10),)}
    monkeypatch.setattr(runtime_selection, "ApplicationStore", Store)
    monkeypatch.setattr(
        runtime_selection,
        "_trial_selection_fingerprint",
        lambda _state_dir: generation["value"],
    )
    runtime_selection._SELECTED_APPLICATION_CACHE.clear()
    runtime_selection._APPLICATION_AUTHORITY_INDEX_CACHE.clear()
    runtime_selection._TRIAL_SELECTION_FINGERPRINT_CACHE.clear()

    first = runtime_selection.selected_application(
        get_ctx(), "", "scenario", "media_center"
    )
    second = runtime_selection.selected_application(
        get_ctx(), "", "scenario", "media_center"
    )
    generation["value"] = (("installations/media/current.json", 2, 11),)
    runtime_selection._TRIAL_SELECTION_FINGERPRINT_CACHE.clear()
    third = runtime_selection.selected_application(
        get_ctx(), "", "scenario", "media_center"
    )

    assert first == second == third
    assert calls["installations"] == 2


def test_selected_application_reuses_one_authority_index_for_different_components(
    monkeypatch,
) -> None:
    installations = (
        SimpleNamespace(
            status="active",
            application_id="media_center",
            installed_release_digest="sha256:media",
            component_refs=({"component_ref": "scenario:media_center"},),
        ),
        SimpleNamespace(
            status="active",
            application_id="semantic_ui",
            installed_release_digest="sha256:semantic",
            component_refs=({"component_ref": "scenario:semantic_ui_demo"},),
        ),
    )
    calls = {"installations": 0}

    class Store:
        def __init__(self, _state_dir):
            pass

        def list_runtime_selections(self):
            return ()

        def list_installations(self):
            calls["installations"] += 1
            return installations

    monkeypatch.setattr(runtime_selection, "ApplicationStore", Store)
    monkeypatch.setattr(
        runtime_selection,
        "_trial_selection_fingerprint",
        lambda _state_dir: (("installations", 1, 1),),
    )
    runtime_selection._SELECTED_APPLICATION_CACHE.clear()
    runtime_selection._APPLICATION_AUTHORITY_INDEX_CACHE.clear()
    runtime_selection._TRIAL_SELECTION_FINGERPRINT_CACHE.clear()

    media = runtime_selection.selected_application(
        get_ctx(), "", "scenario", "media_center"
    )
    semantic = runtime_selection.selected_application(
        get_ctx(), "", "scenario", "semantic_ui_demo"
    )

    assert media.application_id == "media_center"
    assert semantic.application_id == "semantic_ui"
    assert calls["installations"] == 1
