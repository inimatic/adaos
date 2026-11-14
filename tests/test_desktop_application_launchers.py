from types import SimpleNamespace

import pytest
import yaml

from adaos.services.agent_context import get_ctx
from adaos.services.applications import lifecycle, runtime_selection
from adaos.services.scenario import webspace_runtime


@pytest.fixture
def desktop_catalog(tmp_path, monkeypatch):
    from adaos.services import application_registry_projection
    from adaos.services.applications import store as application_store

    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    installations, selections, projects, developments = [], [], [], []
    releases, applications, reads = {}, {}, []
    admission = {"admission": "admitted", "admission_kind": "temporary_trial"}

    def release(app, digest):
        reads.append((app, digest))
        return releases[digest]

    store = SimpleNamespace(
        list_installations=lambda: installations,
        list_runtime_selections=lambda: selections,
        get_application=lambda app: applications[app],
        get_verified_release_projection=release,
        release_has_component=lambda app, digest, **component:
            component["artifact_id"] in releases[digest]["scenarios"],
    )
    projection = SimpleNamespace(
        list_workspace_projects=lambda **_: projects,
        list_development_projects=lambda **_: developments,
    )
    monkeypatch.setattr(application_store, "ApplicationStore", lambda _: store)
    monkeypatch.setattr(application_registry_projection, "ApplicationRegistryProjection", lambda _: projection)
    monkeypatch.setattr(lifecycle, "observe_runtime_admission", lambda *_, **__: dict(admission))
    monkeypatch.setattr(runtime_selection.NativeTrialRuntime, "resolve",
                        lambda *_: pytest.fail("Catalog discovery must not start a Trial"))
    monkeypatch.setattr(webspace_runtime.scenarios_loader, "read_manifest",
                        lambda name, **_: yaml.safe_load((scenarios / name / "scenario.yaml").read_text()))
    webspace_runtime._RUNTIME.cache.clear_desktop_scenarios()
    runtime = webspace_runtime.WebspaceScenarioRuntime(SimpleNamespace(paths=SimpleNamespace(
        scenarios_dir=lambda: scenarios, state_dir=lambda: tmp_path / "state")))

    def scenario(name):
        path = scenarios / name
        path.mkdir()
        (path / "scenario.yaml").write_text(yaml.safe_dump({"id": name, "type": "desktop", "title": name}))

    def install():
        applications["builder"] = SimpleNamespace(
            lifecycle="active", display={"title": "Builder"}, entrypoints=(
                {"entrypoint_id": "main", "presentation_ref": "scenario:builder"},
                {"entrypoint_id": "prompt-ide", "presentation_ref": "scenario:prompt_ide"},
            ))
        releases["installed"] = {"catalog": {"title": "Builder", "icon": "construct-outline"},
                                 "scenarios": {"builder", "prompt_ide"}}
        installations.append(SimpleNamespace(application_id="builder", status="active", revision=1,
                                             installed_release_digest="installed"))

    def trial():
        releases["trial"] = {"catalog": {"title": "Builder Beta", "icon": "construct-outline"},
                             "scenarios": {"builder"}}
        selections.append(SimpleNamespace(application_id="builder", webspace_id="trial-desktop",
            source="local_trial", release_digest="trial", revision=1, runtime_root_ref="trial:candidate"))

    return SimpleNamespace(runtime=runtime, scenario=scenario, install=install, trial=trial,
        projects=projects, developments=developments, releases=releases, applications=applications,
        installations=installations, selections=selections, admission=admission, reads=reads,
        list=lambda host="desktop", space="workspace": runtime._list_desktop_scenarios(space, webspace_id=host))


def project(visibility="listed", primary="scenario:builder"):
    return {"id": "builder", "primary_ref": primary, "title": "Builder",
            "icon": "construct-outline", "visibility": visibility,
            "entrypoints": [{"id": "main", "presentation": "scenario:builder"}]}


def test_desktop_type_alone_does_not_publish_a_scenario(desktop_catalog):
    catalog = desktop_catalog
    catalog.scenario("orphan")
    assert catalog.list() == []
    assert catalog.list(space="dev") == []


def test_project_publishes_only_its_declared_primary_entrypoint(desktop_catalog):
    catalog = desktop_catalog
    for name in ("builder", "prompt_ide", "orphan"):
        catalog.scenario(name)
    catalog.projects.append(project())
    assert [entry[0] for entry in catalog.list()] == ["builder"]
    assert catalog.list()[0][4] == "builder"


def test_hidden_project_and_non_entrypoint_primary_cannot_publish(desktop_catalog):
    catalog = desktop_catalog
    catalog.scenario("builder")
    catalog.projects.append(project(visibility="hidden"))
    assert catalog.list() == []
    catalog.projects[0] = project(primary="scenario:internal")
    catalog.scenario("internal")
    assert catalog.list() == []


def test_development_application_is_visible_only_in_dev(desktop_catalog):
    catalog = desktop_catalog
    catalog.scenario("builder")
    catalog.developments.append(project())
    assert catalog.list() == []
    assert [entry[0] for entry in catalog.list(space="dev")] == ["builder"]


def test_installation_publishes_one_application_without_workspace_copies(desktop_catalog):
    catalog = desktop_catalog
    catalog.install()
    assert [entry[0] for entry in catalog.list()] == ["builder"]
    catalog.list()
    assert catalog.reads == [("builder", "installed")]


def test_missing_primary_does_not_fall_back_to_supporting_scenario(desktop_catalog):
    catalog = desktop_catalog
    catalog.install()
    catalog.releases["installed"]["scenarios"] = {"prompt_ide"}
    catalog.scenario("builder")
    catalog.projects.append(project())
    assert catalog.list() == []


def test_temporary_trial_cannot_leak_through_old_installation_or_project(desktop_catalog):
    catalog = desktop_catalog
    catalog.install()
    catalog.trial()
    catalog.scenario("builder")
    catalog.projects.append(project())
    assert catalog.list() == []
    assert catalog.list(host="trial-desktop")[0][1] == "Builder Beta"
    assert catalog.reads == [("builder", "trial")]


def test_application_identity_masks_its_legacy_project_alias(desktop_catalog):
    catalog = desktop_catalog
    catalog.install()
    catalog.trial()
    catalog.applications["builder"].legacy_project_id = "builder-project"
    catalog.scenario("builder")
    catalog.projects.append({**project(), "id": "builder-project"})
    assert catalog.list() == []


def test_installed_beta_can_offer_its_exact_release_on_another_desktop(desktop_catalog):
    catalog = desktop_catalog
    catalog.install()
    catalog.trial()
    catalog.admission["admission_kind"] = "installed_local_beta"
    assert catalog.list()[0][1] == "Builder Beta"
    assert catalog.reads == [("builder", "trial")]


def test_expired_trial_is_hidden_even_with_warm_launcher_cache(desktop_catalog):
    catalog = desktop_catalog
    catalog.install()
    catalog.trial()
    assert catalog.list(host="trial-desktop")
    catalog.admission["admission"] = "expired"
    assert catalog.list(host="trial-desktop") == []


def test_project_visibility_changes_invalidate_warm_launcher_cache(desktop_catalog):
    catalog = desktop_catalog
    catalog.scenario("builder")
    catalog.projects.append(project())
    assert catalog.list()
    catalog.projects[0]["visibility"] = "hidden"
    assert catalog.list() == []


def test_retired_application_invalidates_its_cached_launcher(desktop_catalog):
    catalog = desktop_catalog
    catalog.install()
    assert catalog.list()
    catalog.applications["builder"].lifecycle = "retired"
    assert catalog.list() == []


def test_broken_trial_admission_does_not_hide_unrelated_application(desktop_catalog, monkeypatch):
    catalog = desktop_catalog
    catalog.install()
    catalog.trial()
    catalog.scenario("other")
    catalog.projects.append({"id": "other", "primary_ref": "scenario:other", "title": "Other",
        "entrypoints": [{"presentation": "scenario:other"}], "visibility": "listed"})
    def unavailable(*_, **__):
        raise ValueError("damaged Trial admission")
    monkeypatch.setattr(lifecycle, "observe_runtime_admission", unavailable)
    assert [entry[0] for entry in catalog.list()] == ["other"]


def test_resolver_rejects_scenario_contributions_and_stale_pins(monkeypatch):
    monkeypatch.setattr(webspace_runtime, "_local_node_id", lambda: "local")
    rogue = {"id": "scenario:orphan", "scenario_id": "orphan"}
    runtime = webspace_runtime.WebspaceScenarioRuntime(get_ctx())
    resolved = runtime.resolve_webspace(webspace_runtime.WebspaceResolverInputs(
        webspace_id="desktop", scenario_id="web_desktop", source_mode="workspace",
        scenario_catalog={"apps": [rogue, {"id": "alias", "action": {
            "target": "desktop.scenario.set", "params": {"scenario_id": "orphan"}}}]},
        overlay_snapshot={"installed": {"apps": ["scenario:orphan"]},
                          "pinnedApplications": ["scenario:orphan", "scenario:builder"]},
        skill_decls=[{"skill": "rogue", "apps": [rogue,
            {"id": "node:local:scenario:orphan", "scenario_id": "orphan", "application_id": "pretend-app"},
            {"id": "scenario:builder", "title": "Spoofed"}, {"id": "modal-app", "launchModal": "settings"}]}],
        live_state={"catalog": {"apps": [rogue,
            {"id": "node:old:scenario:orphan", "scenario_id": "orphan"},
            {"id": "node:member:scenario:editor", "scenario_id": "editor", "application_id": "editor-app"}]}},
        desktop_scenarios=[("builder", "Builder", "construct-outline", "", "builder")],
    ))
    apps = {item["id"]: item for item in resolved.catalog["apps"]}
    assert set(apps) == {"scenario:builder", "modal-app", "node:member:scenario:editor"}
    assert apps["scenario:builder"]["title"] == "Builder"
    assert apps["scenario:builder"]["application_id"] == "builder"


def test_trial_launcher_publishes_one_primary_and_omits_missing_release_member(desktop_catalog, monkeypatch):
    catalog = desktop_catalog
    catalog.install()
    catalog.trial()
    selected = catalog.selections[0]
    selected.to_dict = lambda: {"application_id": "builder"}
    catalog.applications["builder"].application_id = "builder"
    catalog.releases["trial"].update(version="1.0.0", accepted_candidate_id="candidate")
    monkeypatch.setattr(runtime_selection, "ApplicationStore", lambda _: SimpleNamespace(
        list_runtime_selections=lambda: catalog.selections,
        get_application=lambda _: catalog.applications["builder"],
        get_verified_release_projection=lambda *_: catalog.releases["trial"],
        release_has_component=lambda *_, **component: component["artifact_id"] in catalog.releases["trial"]["scenarios"],
    ))
    ctx = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: catalog.runtime.ctx.paths.state_dir()))
    assert [item["scenario_id"] for item in runtime_selection.trial_launcher_entries(ctx, "trial-desktop")] == ["builder"]
    catalog.releases["trial"]["scenarios"] = {"prompt_ide"}
    assert runtime_selection.trial_launcher_entries(ctx, "trial-desktop") == []
