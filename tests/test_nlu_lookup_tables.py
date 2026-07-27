from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest


def test_lookup_source_yaml_cache_is_stamp_aware_and_single_flight(tmp_path: Path, monkeypatch) -> None:
    import adaos.services.nlu_lookup_tables as lookups

    path = tmp_path / "skill.yaml"
    path.write_text("name: first\n", encoding="utf-8")
    lookups._SOURCE_DOCUMENT_CACHE.clear()
    calls = 0
    original_safe_load = lookups.yaml.safe_load

    def _slow_safe_load(value):
        nonlocal calls
        calls += 1
        time.sleep(0.01)
        return original_safe_load(value)

    monkeypatch.setattr(lookups.yaml, "safe_load", _slow_safe_load)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _index: lookups._read_yaml(path), range(8)))

    assert results == [{"name": "first"}] * 8
    assert calls == 1

    path.write_text("name: second\n", encoding="utf-8")

    assert lookups._read_yaml(path) == {"name": "second"}
    assert calls == 2


def test_desktop_lookup_tables_collect_workspace_ids() -> None:
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu_lookup_tables import collect_desktop_lookup_tables, lookup_values, rasa_lookup_entries

    payload = collect_desktop_lookup_tables(get_ctx(), webspace_id="desktop")

    assert payload["ok"] is True
    assert payload["webspace_id"] == "desktop"
    assert "apps_catalog" in lookup_values(payload, "modal_id")
    assert "nlu_teacher_modal" in lookup_values(payload, "modal_id")
    assert "nlu_teacher_app" in lookup_values(payload, "app_id")
    assert "web_desktop" in lookup_values(payload, "scenario_id")
    assert lookup_values(payload, "webspace_id") == ["desktop"]
    modal_rows = {row["value"]: row for row in payload["lookups"]["modal_id"]}
    assert "media_indexer_modal" in modal_rows
    assert "subnet_env_modal" in modal_rows
    assert "media indexer" in modal_rows["media_indexer_modal"]["labels"]
    assert "\u043c\u0435\u0434\u0438\u0430 \u0438\u043d\u0434\u0435\u043a\u0441\u0435\u0440" in modal_rows["media_indexer_modal"]["labels"]
    assert "\u0438\u043d\u0434\u0435\u043a\u0441\u0430" in modal_rows["media_indexer_modal"]["labels"]
    assert "\u043f\u0435\u0440\u0435\u043c\u0435\u043d\u043d\u044b\u0435 \u043e\u043a\u0440\u0443\u0436\u0435\u043d\u0438\u044f \u043f\u043e\u0434\u0441\u0435\u0442\u0438" in modal_rows["subnet_env_modal"]["labels"]
    assert "subnet environment variables" in modal_rows["subnet_env_modal"]["labels"]
    assert "браузеры" in modal_rows["browsers_modal"]["labels"]
    assert "Infra State" in modal_rows["infrastate_modal"]["labels"]
    assert "инфрастейт" in modal_rows["infrastate_modal"]["labels"]

    rasa_entries = rasa_lookup_entries(payload)
    assert any(entry.get("lookup") == "modal_id" for entry in rasa_entries)
    assert any(entry.get("lookup") == "scenario_id" for entry in rasa_entries)


def test_desktop_lookup_tables_reuses_baseline_cache_across_webspaces(monkeypatch) -> None:
    from adaos.services.agent_context import get_ctx
    import adaos.services.nlu_lookup_tables as lookups

    lookups._BASELINE_BUCKET_CACHE.clear()
    calls = {"json": 0, "yaml": 0}
    original_read_json = lookups._read_json
    original_read_yaml = lookups._read_yaml

    def _count_json(path):
        calls["json"] += 1
        return original_read_json(path)

    def _count_yaml(path):
        calls["yaml"] += 1
        return original_read_yaml(path)

    monkeypatch.setattr(lookups, "_read_json", _count_json)
    monkeypatch.setattr(lookups, "_read_yaml", _count_yaml)

    first = lookups.collect_desktop_lookup_tables(get_ctx(), webspace_id="desktop")
    first_calls = dict(calls)
    second = lookups.collect_desktop_lookup_tables(get_ctx(), webspace_id="dev1")

    assert first["webspace_id"] == "desktop"
    assert second["webspace_id"] == "dev1"
    assert lookups.lookup_values(first, "webspace_id") == ["desktop"]
    assert lookups.lookup_values(second, "webspace_id") == ["dev1"]
    assert calls == first_calls
    assert first_calls["json"] > 0


@pytest.mark.anyio
async def test_desktop_lookup_tables_overlay_live_yjs_registry() -> None:
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu_lookup_tables import collect_desktop_lookup_tables_async, lookup_values
    from adaos.services.yjs.doc import async_get_ydoc
    from adaos.services.yjs.store import reset_ystore_for_webspace

    webspace_id = f"lookup-live-{uuid4().hex}"
    try:
        async with async_get_ydoc(webspace_id) as ydoc:
            with ydoc.begin_transaction() as txn:
                ydoc.get_map("ui").set(
                    txn,
                    "application",
                    {"modals": {"live_modal": {"title": "Live Modal", "nluAliases": ["spoken live modal"]}}},
                )
                ydoc.get_map("ui").set(txn, "current_scenario", "live_scenario")
                ydoc.get_map("registry").set(
                    txn,
                    "merged",
                    {"modals": {"merged_modal": {"title": "Merged Modal"}}},
                )
                ydoc.get_map("data").set(
                    txn,
                    "catalog",
                    {"apps": [{"id": "live_app", "title": "Live App", "launchModal": "live_modal"}]},
                )
                ydoc.get_map("data").set(txn, "installed", {"apps": ["installed_app"]})
                ydoc.get_map("data").set(
                    txn,
                    "nodes",
                    {"node-live": {"label": "Kitchen Display"}},
                )

        payload = await collect_desktop_lookup_tables_async(get_ctx(), webspace_id=webspace_id)

        assert payload["live_overlay"] == {"attempted": True, "ok": True}
        assert "live_modal" in lookup_values(payload, "modal_id")
        assert "merged_modal" in lookup_values(payload, "modal_id")
        live_modal = next(row for row in payload["lookups"]["modal_id"] if row["value"] == "live_modal")
        assert "Live App" in live_modal["labels"]
        assert "spoken live modal" in live_modal["labels"]
        assert "live_app" in lookup_values(payload, "app_id")
        assert "installed_app" in lookup_values(payload, "app_id")
        assert "live_scenario" in lookup_values(payload, "scenario_id")
        assert "node-live" in lookup_values(payload, "node_ref")
        assert "Kitchen Display" in lookup_values(payload, "node_ref")
    finally:
        reset_ystore_for_webspace(webspace_id)


@pytest.mark.anyio
async def test_desktop_lookup_tables_async_prefers_live_room(monkeypatch) -> None:
    from adaos.services.agent_context import get_ctx
    from adaos.services.nlu_lookup_tables import collect_desktop_lookup_tables_async
    import adaos.services.yjs.doc as ydoc_module

    calls: list[dict[str, object]] = []

    class _FakeYDoc:
        def get_map(self, name: str):
            if name == "ui":
                return {"application": {"modals": {}}, "current_scenario": "web_desktop"}
            if name == "registry":
                return {"merged": {"modals": {}}}
            if name == "data":
                return {"catalog": {"apps": []}, "installed": {"apps": []}, "nodes": {}}
            return {}

    class _FakeAsyncDoc:
        async def __aenter__(self):
            return _FakeYDoc()

        async def __aexit__(self, *_args):
            return None

    def _fake_async_get_ydoc(*_args, **kwargs):
        calls.append(dict(kwargs))
        return _FakeAsyncDoc()

    monkeypatch.setattr(ydoc_module, "async_get_ydoc", _fake_async_get_ydoc)

    payload = await collect_desktop_lookup_tables_async(get_ctx(), webspace_id="desktop", include_live=True)

    assert payload["live_overlay"] == {"attempted": True, "ok": True}
    assert calls
    assert calls[0]["read_only"] is True
    assert calls[0]["prefer_live_room"] is True
