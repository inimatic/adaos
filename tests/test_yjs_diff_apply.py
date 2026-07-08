from __future__ import annotations

import y_py as Y

from adaos.services.scenario import webspace_runtime as webspace_runtime_module


def _collect_container_ids(value, out: set[int]) -> None:
    if isinstance(value, dict):
        out.add(id(value))
        for item in value.values():
            _collect_container_ids(item, out)
        return
    if isinstance(value, list):
        out.add(id(value))
        for item in value:
            _collect_container_ids(item, out)
        return
    if isinstance(value, tuple):
        out.add(id(value))
        for item in value:
            _collect_container_ids(item, out)


def test_set_map_value_if_changed_promotes_dict_branch_to_attached_y_map() -> None:
    ydoc = Y.YDoc()
    with ydoc.begin_transaction() as txn:
        data_map = ydoc.get_map("data")
        data_map.set(
            txn,
            "desktop",
            {
                "pageSchema": {"id": "old-page", "widgets": [{"id": "w1"}]},
                "topbar": [{"id": "t1"}],
            },
        )

    with ydoc.begin_transaction() as txn:
        changed, mode = webspace_runtime_module._set_map_value_if_changed(
            ydoc.get_map("data"),
            txn,
            "desktop",
            {
                "pageSchema": {"id": "new-page", "widgets": [{"id": "w1"}]},
                "topbar": [{"id": "t1"}],
            },
        )

    assert changed is True
    assert mode == "diff"

    desktop = ydoc.get_map("data").get("desktop")
    assert isinstance(desktop, Y.YMap)
    assert desktop.get("topbar") == [{"id": "t1"}]

    page_schema = desktop.get("pageSchema")
    assert isinstance(page_schema, Y.YMap)
    assert page_schema.get("id") == "new-page"
    assert page_schema.get("widgets") == [{"id": "w1"}]

    with ydoc.begin_transaction() as txn:
        changed, mode = webspace_runtime_module._set_map_value_if_changed(
            ydoc.get_map("data"),
            txn,
            "desktop",
            {
                "pageSchema": {"id": "new-page", "widgets": [{"id": "w1"}]},
                "topbar": [{"id": "t1"}],
            },
        )

    assert changed is False
    assert mode == "diff"


def test_set_map_value_if_changed_diff_deletes_missing_nested_keys() -> None:
    ydoc = Y.YDoc()
    with ydoc.begin_transaction() as txn:
        data_map = ydoc.get_map("data")
        changed, mode = webspace_runtime_module._set_map_value_if_changed(
            data_map,
            txn,
            "routing",
            {
                "current": {"path": "/home"},
                "history": ["/home"],
            },
        )
    assert changed is True
    assert mode == "diff"

    with ydoc.begin_transaction() as txn:
        changed, mode = webspace_runtime_module._set_map_value_if_changed(
            ydoc.get_map("data"),
            txn,
            "routing",
            {
                "current": {"path": "/settings"},
            },
        )

    assert changed is True
    assert mode == "diff"

    routing = ydoc.get_map("data").get("routing")
    assert isinstance(routing, Y.YMap)
    assert routing.get("history") is None
    current = routing.get("current")
    assert isinstance(current, Y.YMap)
    assert current.get("path") == "/settings"


def test_set_map_value_if_changed_promotes_equal_plain_mapping() -> None:
    ydoc = Y.YDoc()
    with ydoc.begin_transaction() as txn:
        ydoc.get_map("data").set(txn, "webio", {"receivers": {}})

    with ydoc.begin_transaction() as txn:
        changed, mode = webspace_runtime_module._set_map_value_if_changed(
            ydoc.get_map("data"),
            txn,
            "webio",
            {"receivers": {}},
        )

    assert changed is True
    assert mode == "diff"
    webio = ydoc.get_map("data").get("webio")
    assert isinstance(webio, Y.YMap)
    assert isinstance(webio.get("receivers"), Y.YMap)


def test_set_map_value_if_changed_unchanged_diff_avoids_cloning_current_containers(monkeypatch) -> None:
    ydoc = Y.YDoc()
    payload = {
        "pageSchema": {
            "id": "desktop",
            "widgets": [
                {"id": "weather"},
                {"id": "infrascope"},
            ],
        },
        "topbar": [
            {"id": "home"},
            {"id": "settings"},
        ],
    }

    with ydoc.begin_transaction() as txn:
        changed, mode = webspace_runtime_module._set_map_value_if_changed(
            ydoc.get_map("data"),
            txn,
            "desktop",
            payload,
        )

    assert changed is True
    assert mode == "diff"

    payload_container_ids: set[int] = set()
    _collect_container_ids(payload, payload_container_ids)
    original_clone = webspace_runtime_module._clone_json_like

    def _guarded_clone(value):
        if isinstance(value, (dict, list, tuple)) and id(value) not in payload_container_ids:
            raise AssertionError(f"unexpected clone of current container: {type(value).__name__}")
        return original_clone(value)

    monkeypatch.setattr(webspace_runtime_module, "_clone_json_like", _guarded_clone)

    with ydoc.begin_transaction() as txn:
        changed, mode = webspace_runtime_module._set_map_value_if_changed(
            ydoc.get_map("data"),
            txn,
            "desktop",
            payload,
        )

    assert changed is False
    assert mode == "diff"


def test_patch_map_value_from_previous_updates_only_changed_nested_keys() -> None:
    previous = {
        "pageSchema": {
            "id": "desktop",
            "title": "Before",
            "widgets": [
                {"id": "editor"},
                {"id": "cards", "inputs": {"previewKey": "status"}},
            ],
        },
        "topbar": [{"id": "home"}],
    }
    next_value = {
        "pageSchema": {
            "id": "desktop",
            "title": "After",
            "widgets": [
                {"id": "editor"},
                {"id": "cards", "inputs": {"previewKey": "card_preview"}},
            ],
        },
        "topbar": [{"id": "home"}],
    }

    ydoc = Y.YDoc()
    with ydoc.begin_transaction() as txn:
        changed, mode = webspace_runtime_module._set_map_value_if_changed(
            ydoc.get_map("data"),
            txn,
            "desktop",
            previous,
        )
    assert changed is True
    assert mode == "diff"
    before_vector = Y.encode_state_vector(ydoc)

    with ydoc.begin_transaction() as txn:
        changed, mode = webspace_runtime_module._patch_map_value_from_previous(
            ydoc.get_map("data"),
            txn,
            "desktop",
            next_value,
            previous,
        )

    assert changed is True
    assert mode == "patch"
    update = Y.encode_state_as_update(ydoc, before_vector)
    assert update
    desktop = ydoc.get_map("data").get("desktop")
    assert isinstance(desktop, Y.YMap)
    assert desktop.get("topbar") == [{"id": "home"}]
    page_schema = desktop.get("pageSchema")
    assert isinstance(page_schema, Y.YMap)
    assert page_schema.get("title") == "After"
    assert page_schema.get("widgets")[1]["inputs"]["previewKey"] == "card_preview"

    with ydoc.begin_transaction() as txn:
        changed, mode = webspace_runtime_module._patch_map_value_from_previous(
            ydoc.get_map("data"),
            txn,
            "desktop",
            next_value,
            next_value,
        )
    assert changed is False
    assert mode == "patch"


def test_ydoc_defaults_preserve_attached_y_map_branch() -> None:
    ydoc = Y.YDoc()
    runtime = webspace_runtime_module.WebspaceScenarioRuntime()

    with ydoc.begin_transaction() as txn:
        changed, mode = webspace_runtime_module._set_map_value_if_changed(
            ydoc.get_map("data"),
            txn,
            "desktop",
            {
                "pageSchema": {"id": "desktop"},
                "topbar": [{"id": "home"}],
            },
        )
    assert changed is True
    assert mode == "diff"

    with ydoc.begin_transaction() as txn:
        runtime._apply_ydoc_defaults_in_txn(
            ydoc,
            txn,
            [
                {
                    "skill": "notebook_skill",
                    "ydoc_defaults": {
                        "data/desktop/notebook": {
                            "notes": [],
                        }
                    },
                }
            ],
        )

    desktop = ydoc.get_map("data").get("desktop")
    assert isinstance(desktop, Y.YMap)
    assert isinstance(desktop.get("pageSchema"), Y.YMap)
    assert isinstance(desktop.get("notebook"), Y.YMap)
    assert desktop.get("topbar") == [{"id": "home"}]
