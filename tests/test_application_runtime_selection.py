from types import SimpleNamespace
import pytest

from adaos.services.agent_context import get_ctx
from adaos.services.applications import runtime_selection


def test_explicit_ui_selection_does_not_invent_legacy_installed_dependency_owners(tmp_path, monkeypatch):
    import pytest
    from adaos.services.applications.trial_runtime import TrialRuntimeUnavailable
    installations = [SimpleNamespace(status="active", application_id=name,
        installed_release_digest="sha256:" + "a" * 64,
        component_refs=[{"component_ref": "skill:shared_dependency"}]) for name in ("one", "two")]
    store = SimpleNamespace(list_runtime_selections=lambda: [], list_installations=lambda: installations)
    ctx = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path))
    monkeypatch.setattr(runtime_selection, "ApplicationStore", lambda _: store)
    assert runtime_selection.selected_application(ctx, "local", "skill", "shared_dependency",
        installed_compatibility=False) is None
    with pytest.raises(TrialRuntimeUnavailable, match="Ambiguous installed"):
        runtime_selection.selected_application(ctx, "local", "skill", "shared_dependency")


def test_trial_lookup_visits_each_release_once_without_cloning_unrelated_packages(tmp_path, monkeypatch):
    rows = [SimpleNamespace(application_id='unrelated', release_digest='other', runtime_root_ref='trial:other')
            for _ in range(40)]
    rows += [SimpleNamespace(application_id='provider', release_digest='exact', runtime_root_ref='trial:candidate')
             for _ in range(10)]
    memberships, copies = [], []

    def membership(app, digest, **_kwargs):
        memberships.append((app, digest))
        return app == 'provider'

    def release(app, _digest):
        copies.append(app)
        assert app == 'provider', 'Unrelated immutable releases must not be cloned'
        return SimpleNamespace(accepted_candidate_id='candidate', project_release=SimpleNamespace(
            components=[SimpleNamespace(kind='skill', artifact_id='provider_skill')]))

    store = SimpleNamespace(list_runtime_selections=lambda: rows, release_has_component=membership, get_release=release)
    monkeypatch.setattr(runtime_selection, 'ApplicationStore', lambda _path: store)
    monkeypatch.setattr(runtime_selection.NativeTrialRuntime, 'resolve', lambda _ctx, *identity: identity)
    ctx = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path))
    assert runtime_selection._selected_trial_uncached(ctx, '', 'skill', 'provider_skill') == ('candidate', 'exact')
    assert memberships == [('unrelated', 'other'), ('provider', 'exact')]
    assert copies == ['provider']


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
        lifecycle="active",
        display={"title": "Reading List"},
        entrypoints=(
            {"presentation_ref": "subscription_status_app"},
            {"presentation_ref": "skill:subscription_status_skill"},
            {"presentation_ref": "scenario:reading_list"},
        ),
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
        release_has_component=lambda *args, **kwargs: True,
    )
    prepared = []
    monkeypatch.setattr(runtime_selection, "ApplicationStore", lambda _state_dir: store)
    from adaos.services.applications import lifecycle
    monkeypatch.setattr(lifecycle, 'observe_runtime_admission', lambda *args, **kwargs:
        {'admission':'admitted', 'reason':'InstalledLocalBeta'})
    monkeypatch.setattr(runtime_selection, "selected_trial_exact", lambda *args, **kwargs:
        pytest.fail('Launcher metadata must not enter the execution resolver'))
    monkeypatch.setattr(runtime_selection.NativeTrialRuntime, "resolve", lambda *args, **kwargs:
        pytest.fail('Launcher metadata must not construct a Trial runtime'))

    entries = runtime_selection.trial_launcher_entries(get_ctx(), "desktop")

    assert len(entries) == 1
    assert entries[0]["icon"] == "book-outline"
    assert entries[0]["scenario_id"] == "reading_list"
    assert entries[0]["availability"] == {"status": "ready", "source_verification": "on_open"}
    assert prepared == [], "Reading launcher metadata must not prepare executors"


@pytest.mark.parametrize('admission,ready', [('admitted',True), ('expiring',True),
                                           ('expired',False), ('unavailable',False)])
def test_launcher_metadata_keeps_live_admission_without_an_execution_runtime(tmp_path, monkeypatch, admission, ready):
    from adaos.services.applications import lifecycle
    ctx = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path))
    selected = SimpleNamespace(application_id='sample', release_digest='exact')
    store = SimpleNamespace(release_has_component=lambda *args, **kwargs: True)
    monkeypatch.setattr(lifecycle, 'observe_runtime_admission', lambda *args, **kwargs:
        {'admission':admission, 'reason':'ObservedReason'})
    value = runtime_selection._trial_launcher_availability(ctx, store, selected, 'scenario', 'screen')
    assert (value['status']=='ready') is ready
    if not ready:
        assert value['reason']=='ObservedReason'


def test_launcher_metadata_does_not_admit_a_pending_channel(tmp_path, monkeypatch):
    from adaos.services.applications import lifecycle, runtime_channel
    ctx = SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path))
    selected = SimpleNamespace(application_id='sample', release_digest='exact')
    store = SimpleNamespace(release_has_component=lambda *args, **kwargs: True)
    monkeypatch.setattr(lifecycle, 'observe_runtime_admission', lambda *args, **kwargs:
        {'admission':'admitted', 'reason':'InstalledLocalBeta'})
    monkeypatch.setattr(runtime_channel, 'ApplicationRuntimeChannel', lambda *args:
        SimpleNamespace(path=SimpleNamespace(is_file=lambda:True), pending_transitions=lambda:({'pending':True},)))
    assert runtime_selection._trial_launcher_availability(ctx, store, selected, 'scenario', 'screen') == {
        'status':'unavailable', 'reason':'RuntimeTransitionPending'}


@pytest.mark.parametrize(
    "feature_status,expected_status,hidden",
    [
        ("available", "ready", False),
        ("limited", "ready", False),
        ("suspended", "unavailable", False),
        ("unavailable", "unavailable", True),
    ],
)
def test_site_studio_launcher_uses_separate_root_service_entitlement(
    monkeypatch, feature_status, expected_status, hidden
):
    from adaos.services import personalization_runtime, site_studio_entitlements

    class Entitlements:
        def status(self, **request):
            assert request["scope_refs"] == ("subject:subnet:sn-owner",)
            assert request["application_id"] == "site-studio-app"
            return {
                "policy_revision": "site-studio-beta-1",
                "features": {
                    "authoring": {
                        "status": feature_status,
                        "reason_code": "test_" + feature_status,
                    }
                },
                "usage": {"projects": 1},
                "limits": {"projects": 10},
            }

    monkeypatch.setattr(
        personalization_runtime, "current_subnet_id", lambda ctx: "sn-owner"
    )
    monkeypatch.setattr(
        site_studio_entitlements,
        "site_studio_entitlement_service",
        lambda: Entitlements(),
    )
    selected = SimpleNamespace(application_id="site-studio-app")
    result = runtime_selection._service_launcher_availability(
        get_ctx(),
        selected,
        "site_studio",
        {
            "service_entitlements": [
                {
                    "service_id": "site_studio",
                    "feature": "authoring",
                    "scenario_id": "site_studio",
                    "skill_id": "site_studio_skill",
                    "environment": "prod",
                }
            ]
        },
    )

    assert result["status"] == expected_status
    assert result.get("hide_launcher", False) is hidden
    assert result["service_entitlement"]["status"] == feature_status


def test_trial_launcher_publishes_its_owned_icon_before_app_mount(tmp_path,monkeypatch):
    import json
    source=tmp_path/'owned-service'
    (source/'assets').mkdir(parents=True)
    (source/'assets/icon.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"><circle r="4"/></svg>')
    (source/'webui.json').write_text(json.dumps({'resources':{
        'owned.icon':{'kind':'image','scope':'skill','path':'assets/icon.svg','fallback':'book-outline'},
        'unrelated.resource':{'kind':'image','path':'assets/private.svg'},
    }}))
    package=SimpleNamespace(kind='skill',artifact_id='owned-service')
    runtime=SimpleNamespace(packages=[package],verified_source=lambda item:source)
    monkeypatch.setattr(runtime_selection.NativeTrialRuntime,'resolve',lambda *args:runtime)
    entries=[{'icon':'resource:owned.icon','availability':{'status':'ready'},'runtime_selection':{'runtime_root_ref':'trial:candidate','release_digest':'sha256:owned'}}]
    published=runtime_selection.trial_launcher_icon_resources(get_ctx(),entries)
    assert list(published)==['owned.icon']
    assert published['owned.icon']['owner']=='skill:owned-service'
    assert published['owned.icon']['url'].startswith('/assets/blobs/sha256/')
    (source/'assets/icon.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"><circle r="6"/></svg>')
    updated=runtime_selection.trial_launcher_icon_resources(get_ctx(),entries)
    assert updated['owned.icon']['cacheKey']!=published['owned.icon']['cacheKey']
    (source/'assets/icon.svg').unlink()
    missing=runtime_selection.trial_launcher_icon_resources(get_ctx(),entries)
    assert missing['owned.icon']['fallback']=='book-outline'
    assert not missing['owned.icon'].get('url') and not missing['owned.icon'].get('path')


def test_trial_launcher_reuses_immutable_icon_projection(tmp_path, monkeypatch):
    import json

    source = tmp_path / "owned-service"
    (source / "assets").mkdir(parents=True)
    (source / "assets/icon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><circle r="4"/></svg>'
    )
    (source / "webui.json").write_text(
        json.dumps(
            {
                "resources": {
                    "owned.icon": {
                        "kind": "image",
                        "scope": "skill",
                        "path": "assets/icon.svg",
                    }
                }
            }
        )
    )
    package = SimpleNamespace(kind="skill", artifact_id="owned-service")
    runtime = SimpleNamespace(packages=[package], verified_source=lambda _item: source)
    calls = {"resolve": 0}

    def _resolve(*_args):
        calls["resolve"] += 1
        return runtime

    monkeypatch.delenv("ADAOS_TESTING", raising=False)
    monkeypatch.setattr(runtime_selection.NativeTrialRuntime, "resolve", _resolve)
    runtime_selection._TRIAL_LAUNCHER_ICON_CACHE.clear()
    entries = [
        {
            "icon": "resource:owned.icon",
            "availability": {"status": "ready"},
            "runtime_selection": {
                "runtime_root_ref": "trial:candidate",
                "release_digest": "sha256:owned-cache",
            },
        }
    ]

    first = runtime_selection.trial_launcher_icon_resources(get_ctx(), entries)
    first["owned.icon"]["owner"] = "mutated"
    second = runtime_selection.trial_launcher_icon_resources(get_ctx(), entries)

    assert calls["resolve"] == 1
    assert second["owned.icon"]["owner"] == "skill:owned-service"


def test_trial_launcher_deduplicates_trial_verification_within_cold_projection(
    tmp_path, monkeypatch
):
    import json

    source = tmp_path / "owned-service"
    (source / "assets").mkdir(parents=True)
    (source / "assets" / "icon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><circle r="4"/></svg>'
    )
    (source / "webui.json").write_text(
        json.dumps(
            {
                "resources": {
                    "owned.icon": {
                        "kind": "image",
                        "scope": "skill",
                        "path": "assets/icon.svg",
                    }
                }
            }
        )
    )
    package = SimpleNamespace(kind="skill", artifact_id="owned-service")
    calls = {"resolve": 0, "verify": 0}

    def verified_source(_package):
        calls["verify"] += 1
        return source

    runtime = SimpleNamespace(packages=[package], verified_source=verified_source)

    def resolve(*_args):
        calls["resolve"] += 1
        return runtime

    monkeypatch.setenv("ADAOS_TESTING", "1")
    monkeypatch.setattr(runtime_selection.NativeTrialRuntime, "resolve", resolve)
    entry = {
        "icon": "resource:owned.icon",
        "availability": {"status": "ready"},
        "runtime_selection": {
            "runtime_root_ref": "trial:candidate",
            "release_digest": "sha256:owned-cold",
        },
    }

    published = runtime_selection.trial_launcher_icon_resources(
        get_ctx(), [dict(entry), dict(entry)]
    )

    assert published["owned.icon"]["owner"] == "skill:owned-service"
    assert calls == {"resolve": 1, "verify": 1}


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
@pytest.mark.parametrize("declared_icon", [None, "book-outline"])
def test_trial_launcher_distinguishes_missing_artwork_without_replacing_declared_icons(tmp_path, monkeypatch, declared_icon):
    from types import SimpleNamespace
    from adaos.services.applications import runtime_selection as module
    selection = SimpleNamespace(webspace_id="desktop", application_id="sample", runtime_root_ref="trial:sample", release_digest="release", to_dict=lambda: {})
    app = SimpleNamespace(application_id="sample", lifecycle="active", display={"title": "Sample App"}, entrypoints=({"presentation_ref": "scenario:sample"},))
    store = SimpleNamespace(list_runtime_selections=lambda: (selection,), get_application=lambda _: app,
        get_verified_release_projection=lambda *a: {"catalog": {"icon": declared_icon}, "version": "1.0.0", "accepted_candidate_id": "sample"})
    monkeypatch.setattr(module, "ApplicationStore", lambda _: store)
    monkeypatch.setattr(module, "_trial_launcher_availability", lambda *a: {"status": "ready"})
    rows = module.trial_launcher_entries(SimpleNamespace(paths=SimpleNamespace(state_dir=lambda: tmp_path)), "desktop")
    assert rows[0]["icon"] == (declared_icon or "apps-outline")
    assert rows[0].get("iconText") == (None if declared_icon else "SA")
