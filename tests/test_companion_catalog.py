import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from adaos.services.companion.catalog import CapabilityIndex, ContextHandles


def rows():
    return [{"ref": "ui:slideshow", "title": "Slideshow", "kind": "ui", "owner": "test", "effect": "ui_navigation", "admitted": True},
            {"ref": "ui:notes", "title": "Заметки", "aliases": ["Notebook", "notes"], "kind": "ui", "owner": "test", "effect": "ui_navigation", "admitted": True}]


def test_failed_search_can_inspect_complete_paged_catalog():
    index = CapabilityIndex(rows)
    assert index.search("слайд-шоу", kind="ui")["outcome"] == "search_miss"
    page = index.search("", kind="ui", limit=1)
    second = index.search("", kind="ui", limit=1, offset=page["next_offset"], catalog_digest=page["catalog_digest"])
    assert page["total"] == 2
    assert second["next_offset"] is None
    assert index.search("slideshow", kind="ui")["items"][0]["ref"] == "ui:slideshow"
    assert index.search("NOTEBOOK")["items"][0]["ref"] == "ui:notes"


def test_concurrent_warm_queries_compile_once_and_return_copies():
    calls = []
    def load():
        calls.append(1)
        return rows()
    index = CapabilityIndex(load)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: index.search(""), range(20)))
    assert len(calls) == 1
    descriptor = index.describe("ui:notes")
    descriptor["title"] = "corrupted"
    assert index.describe("ui:notes")["title"] == "Заметки"
    index.invalidate()
    index.search("")
    assert len(calls) == 2


def test_handle_reuse_checks_fresh_state_and_catalog():
    index = CapabilityIndex(rows)
    state = {"current_scenario": "management"}
    handles = ContextHandles(index, lambda: dict(state))
    handle = handles.read()["context_handle"]
    assert handles.resolve(handle)["state"] == state
    state["current_scenario"] = "home"
    with pytest.raises(ValueError, match="context_stale"):
        handles.resolve(handle)
    handle = handles.read()["context_handle"]
    index.invalidate()
    with pytest.raises(ValueError, match="context_catalog_stale"):
        handles.resolve(handle)


def test_stale_paging_revision_and_expired_handles_rejected():
    index = CapabilityIndex(rows)
    handles = ContextHandles(index, lambda: {}, ttl=-1)
    handle = handles.read()["context_handle"]
    with pytest.raises(ValueError, match="expired"):
        handles.resolve(handle)
    with pytest.raises(ValueError, match="revision_changed"):
        index.search("", catalog_digest="old")
