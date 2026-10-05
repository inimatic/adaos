from types import SimpleNamespace

import pytest

from adaos.services.applications import runtime_selection
from adaos.services.scenario import webspace_runtime
from adaos.services.scenario.webspace_runtime import canonical_materialization_identity


def test_canonical_materialization_identity_is_access_scoped() -> None:
    first = canonical_materialization_identity(
        webspace_id="desktop-dev",
        scenario_id="todo_list_5b9319fa",
        revision="021",
        source_fingerprint="abcdef1234567890",
        roles=["Editor", "admin", "editor", ""],
    )
    second = canonical_materialization_identity(
        webspace_id="desktop-dev",
        scenario_id="todo_list_5b9319fa",
        revision="021",
        source_fingerprint="abcdef1234567890",
        user_id="guest",
        roles=["admin", "editor"],
    )
    different_access = canonical_materialization_identity(
        webspace_id="desktop-dev",
        scenario_id="todo_list_5b9319fa",
        revision="021",
        source_fingerprint="abcdef1234567890",
        user_id="alice",
        roles=["admin"],
    )

    assert first["user_id"] == "guest"
    assert first["guest"] is True
    assert first["roles"] == ["admin", "editor"]
    assert first["key"] == second["key"]
    assert first["key_hash"] == second["key_hash"]
    assert different_access["key"] != first["key"]
    assert "policy-" not in first["key"]


def test_canonical_materialization_identity_uses_current_without_revision_or_source() -> None:
    identity = canonical_materialization_identity(
        webspace_id="desktop-dev",
        scenario_id="todo_list_5b9319fa",
        user_id="",
        roles=None,
    )

    assert identity["user_id"] == "guest"
    assert identity["revision"] is None
    assert identity["source_fingerprint"] is None
    assert ":current:guest:" in identity["key"]


def test_canonical_materialization_identity_pins_application_release() -> None:
    first = canonical_materialization_identity(
        webspace_id="desktop",
        scenario_id="mail_focus_reader",
        application_id="mail_focus_reader",
        application_release_digest="sha256:first",
    )
    second = canonical_materialization_identity(
        webspace_id="desktop",
        scenario_id="mail_focus_reader",
        application_id="mail_focus_reader",
        application_release_digest="sha256:second",
    )

    assert first["application_id"] == "mail_focus_reader"
    assert first["application_release_digest"] == "sha256:first"
    assert first["key"] != second["key"]
    assert first["key_hash"] != second["key_hash"]


@pytest.mark.parametrize(
    ("application_id", "application_release_digest"),
    [("mail_focus_reader", None), (None, "sha256:first")],
)
def test_canonical_materialization_identity_rejects_partial_application_authority(
    application_id: str | None,
    application_release_digest: str | None,
) -> None:
    with pytest.raises(ValueError, match="requires both"):
        canonical_materialization_identity(
            webspace_id="desktop",
            scenario_id="mail_focus_reader",
            application_id=application_id,
            application_release_digest=application_release_digest,
        )


def test_trial_materialization_uses_selected_package_as_exact_application_authority(
    monkeypatch,
) -> None:
    trial = SimpleNamespace(
        project_id="web_desktop",
        candidate_id="candidate-42",
        release_digest="sha256:release-42",
    )
    monkeypatch.setattr(
        webspace_runtime,
        "_scenario_source_fingerprint_for_materialization",
        lambda *_args, **_kwargs: "workspace:old",
    )
    monkeypatch.setattr(
        webspace_runtime,
        "_skill_sources_fingerprint_for_materialization",
        lambda *_args, **_kwargs: "skills",
    )
    monkeypatch.setattr(runtime_selection, "selection_snapshot", lambda *_args: [])
    monkeypatch.setattr(runtime_selection, "selected_trial", lambda *_args: trial)
    monkeypatch.setattr(runtime_selection, "selected_application", lambda *_args: None)

    identity = webspace_runtime._scenario_switch_materialization_identity(
        webspace_id="desktop",
        scenario_id="web_desktop",
        source_mode="workspace",
    )

    assert identity is not None
    assert identity["application_id"] == "web_desktop"
    assert identity["application_release_digest"] == "sha256:release-42"
    assert identity["source_fingerprint"] == "trial_sha256_release-42"


def test_dev_materialization_does_not_read_production_runtime_selections(
    monkeypatch,
) -> None:
    def unexpected(*_args, **_kwargs):
        raise AssertionError("DEV materialization must not inspect production selections")

    monkeypatch.setattr(
        webspace_runtime,
        "_scenario_source_fingerprint_for_materialization",
        lambda *_args, **_kwargs: "dev:source",
    )
    monkeypatch.setattr(
        webspace_runtime,
        "_skill_sources_fingerprint_for_materialization",
        lambda *_args, **_kwargs: "dev-skills",
    )
    monkeypatch.setattr(runtime_selection, "selection_snapshot", unexpected)
    monkeypatch.setattr(runtime_selection, "selected_trial", unexpected)
    monkeypatch.setattr(runtime_selection, "selected_application", unexpected)

    identity = webspace_runtime._scenario_switch_materialization_identity(
        webspace_id="desktop-dev",
        scenario_id="applications",
        source_mode="dev",
    )

    assert identity is not None
    assert identity["source_fingerprint"] == "dev_source"
    assert identity.get("application_id") is None
    assert identity.get("application_release_digest") is None


def test_stable_materialization_resolves_application_once_without_trial_scan(
    monkeypatch,
) -> None:
    stable = runtime_selection.InstalledApplicationAuthority(
        application_id="media_center",
        release_digest="sha256:media",
        webspace_id="desktop",
    )
    monkeypatch.setattr(
        webspace_runtime,
        "_scenario_source_fingerprint_for_materialization",
        lambda *_args, **_kwargs: "workspace:media",
    )
    monkeypatch.setattr(
        webspace_runtime,
        "_skill_sources_fingerprint_for_materialization",
        lambda *_args, **_kwargs: "skills",
    )
    monkeypatch.setattr(runtime_selection, "selected_application", lambda *_args: stable)

    def unexpected_trial(*_args, **_kwargs):
        raise AssertionError("stable authority must not scan Trial runtime")

    monkeypatch.setattr(runtime_selection, "selected_trial", unexpected_trial)

    identity = webspace_runtime._scenario_switch_materialization_identity(
        webspace_id="desktop",
        scenario_id="media_center",
        source_mode="workspace",
    )

    assert identity is not None
    assert identity["application_id"] == "media_center"
    assert identity["application_release_digest"] == "sha256:media"


def test_stable_scenario_existence_uses_application_index_without_trial_scan(
    monkeypatch,
) -> None:
    stable = runtime_selection.InstalledApplicationAuthority(
        application_id="media_center",
        release_digest="sha256:media",
        webspace_id="desktop",
    )
    monkeypatch.setattr(runtime_selection, "selected_application", lambda *_args: stable)

    def unexpected_trial(*_args, **_kwargs):
        raise AssertionError("stable scenario validation must not scan Trial runtime")

    monkeypatch.setattr(runtime_selection, "selected_trial", unexpected_trial)

    assert webspace_runtime._scenario_exists_in_webspace(
        "media_center", space="workspace", webspace_id="desktop"
    )


@pytest.mark.asyncio
async def test_switch_state_does_not_run_diagnostic_scenario_validation(
    monkeypatch,
) -> None:
    row = SimpleNamespace(
        workspace_id="desktop",
        title="Desktop",
        effective_kind="personal",
        effective_source_mode="workspace",
        is_dev=False,
        home_scenario="web_desktop",
        effective_home_scenario="web_desktop",
        home_scenario_ref_overlay=None,
        has_current_scenario_overlay=True,
        current_scenario_overlay="media_center",
    )
    monkeypatch.setattr(webspace_runtime.workspace_index, "get_workspace", lambda _id: row)

    def unexpected_validation(*_args, **_kwargs):
        raise AssertionError("switch admission must not run dashboard validation")

    monkeypatch.setattr(webspace_runtime, "_build_webspace_validation", unexpected_validation)

    state = await webspace_runtime._describe_webspace_switch_state("desktop")

    assert state.current_scenario == "media_center"
    assert state.effective_home_scenario == "web_desktop"


def test_resolver_cache_is_scoped_to_exact_application_release(monkeypatch) -> None:
    monkeypatch.setattr(
        webspace_runtime,
        "_resolver_cache_keys",
        lambda _inputs: {
            "scenario": "same",
            "skills": "same",
            "desktop_scenarios": "same",
        },
    )

    def inputs(release_digest: str) -> SimpleNamespace:
        return SimpleNamespace(
            scenario_id="web_desktop",
            source_mode="workspace",
            scenario_source="application_trial",
            legacy_scenario_fallback=False,
            metadata={
                "materialization": {
                    "identity": {
                        "revision": "candidate-42",
                        "application_id": "web_desktop",
                        "application_release_digest": release_digest,
                    }
                }
            },
        )

    assert webspace_runtime._resolver_core_fingerprint(
        inputs("sha256:first")
    ) != webspace_runtime._resolver_core_fingerprint(inputs("sha256:second"))
