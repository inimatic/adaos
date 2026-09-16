from contextlib import asynccontextmanager

import pytest
from y_py import YDoc

from adaos.services.io_web import desktop
from adaos.services.workspaces import index


MEDIA = {"path": "/media/files/content/approved.png", "mime": "image/png", "sha256": "a" * 64}


@pytest.mark.asyncio
async def test_icon_override_survives_reopen_and_other_preferences_without_changing_page(monkeypatch):
    index.ensure_workspace("icon-test")
    index.ensure_workspace("other-test")
    doc = YDoc()
    with doc.begin_transaction() as txn:
        desktop._set_json_map_value(doc.get_map("data"), txn, "desktop", {"pageSchema": {"id": "active-app"}})

    @asynccontextmanager
    async def get_doc(_):
        yield doc

    monkeypatch.setattr(desktop, "async_get_ydoc", get_doc)
    monkeypatch.setattr(desktop, "mutate_live_room", lambda *a, **kw: False)
    service = desktop.WebDesktopService()
    await service.set_icon_media("scenario:example", MEDIA, "icon-test")
    assert desktop.WebDesktopService().get_icon_media("icon-test") == {"scenario:example": MEDIA}
    index.set_workspace_icon_order_overlay("icon-test", ["scenario:example"])
    row = index.get_workspace("icon-test")
    assert row.desktop_overlay["iconMediaOverrides"] == {"scenario:example": MEDIA}
    assert service.get_icon_media("other-test") == {}
    state = desktop._coerce_dict(doc.get_map("data").get("desktop"))
    assert state["pageSchema"] == {"id": "active-app"}
    assert state["iconMediaOverrides"] == {"scenario:example": MEDIA}
    await service.set_icon_media("scenario:other", MEDIA, "icon-test")
    await service.set_icon_media("scenario:example", None, "icon-test")
    assert service.get_icon_media("icon-test") == {"scenario:other": MEDIA}


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [
    {"path": "https://example.org/image.png"}, {"path": "/media/files/content/../secret.png"},
    {"path": "/media/files/content/example.svg"}, {"mime": "text/html"}, {"sha256": "missing"},
    {"prompt": "private prompt"},
])
async def test_icon_override_rejects_nonlocal_or_unbounded_descriptor(patch):
    index.ensure_workspace("icon-test")
    service = desktop.WebDesktopService()
    with pytest.raises(ValueError, match="descriptor"):
        await service.set_icon_media("scenario:example", {**MEDIA, **patch}, "icon-test")
    assert service.get_icon_media("icon-test") == {}


@pytest.mark.asyncio
async def test_icon_customization_cannot_create_a_webspace():
    with pytest.raises(ValueError, match="does not exist"):
        await desktop.WebDesktopService().set_icon_media("scenario:example", MEDIA, "missing-dev")
    assert index.get_workspace("missing-dev") is None


def test_raster_override_survives_shared_core_cache_without_leaking_to_other_desktops():
    from adaos.services.agent_context import get_ctx
    from adaos.services.scenario import webspace_runtime as runtime_module

    runtime_module._RUNTIME.cache.clear_resolved_webspaces()
    runtime = runtime_module.WebspaceScenarioRuntime(get_ctx())
    common = dict(scenario_id="home", source_mode="workspace",
                  scenario_application={"desktop": {"pageSchema": {"id": "home"}}},
                  scenario_catalog={"apps": [], "widgets": []}, scenario_registry={"modals": [], "widgets": []},
                  live_state={}, skill_decls=[], skill_decls_fingerprint="none", desktop_scenarios=[],
                  scenario_source="test:home")
    outputs = []
    for name, overrides in [("one", {"scenario:example": MEDIA}), ("two", {})]:
        outputs.append(runtime.resolve_webspace(runtime_module.WebspaceResolverInputs(
            webspace_id=name, overlay_snapshot={"iconMediaOverrides": overrides}, **common)))
    assert runtime._last_resolver_debug["core_cache_hit"] is True
    assert outputs[0].desktop["iconMediaOverrides"] == {"scenario:example": MEDIA}
    assert outputs[1].desktop["iconMediaOverrides"] == {}
    assert outputs[0].application["desktop"]["pageSchema"]["id"] == "home"
    direct = runtime._resolve_webspace_uncached(runtime_module.WebspaceResolverInputs(
        webspace_id="one", overlay_snapshot={"iconMediaOverrides": {"scenario:example": MEDIA}}, **common))
    assert direct.desktop["iconMediaOverrides"] == outputs[0].desktop["iconMediaOverrides"]
