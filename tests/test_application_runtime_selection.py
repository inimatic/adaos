from types import SimpleNamespace

from adaos.services.agent_context import get_ctx
from adaos.services.applications import runtime_selection


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
