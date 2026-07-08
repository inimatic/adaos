from __future__ import annotations

import asyncio
from typing import Any

import y_py as Y

from adaos.services.io_web import desktop as desktop_module
from adaos.services.io_web import toast as toast_module
from adaos.services.yjs.json_merge import set_map_value_if_changed


class _AsyncDoc:
    def __init__(self, ydoc: Y.YDoc) -> None:
        self._ydoc = ydoc

    async def __aenter__(self) -> Y.YDoc:
        return self._ydoc

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


class _AsyncNullContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


def test_web_desktop_writers_preserve_attached_y_map_branches() -> None:
    ydoc = Y.YDoc()

    with ydoc.begin_transaction() as txn:
        desktop_module.WebDesktopService._apply_page_schema_state(
            ydoc,
            txn,
            {"id": "desktop", "layout": {"type": "single"}, "widgets": []},
        )
        desktop_module.WebDesktopService._apply_installed_state(
            ydoc,
            txn,
            desktop_module.WebDesktopInstalled(apps=["scenario:web_desktop"], widgets=["weather"]),
        )

    data_desktop = ydoc.get_map("data").get("desktop")
    assert isinstance(data_desktop, Y.YMap)
    assert isinstance(data_desktop.get("pageSchema"), Y.YMap)
    assert isinstance(data_desktop.get("installed"), Y.YMap)
    assert isinstance(ydoc.get_map("data").get("installed"), Y.YMap)

    ui_application = ydoc.get_map("ui").get("application")
    assert isinstance(ui_application, Y.YMap)
    assert isinstance(ui_application.get("desktop"), Y.YMap)

    with ydoc.begin_transaction() as txn:
        desktop_module.WebDesktopService._apply_topbar_state(ydoc, txn, [{"id": "home"}])
        desktop_module.WebDesktopService._apply_hidden_sections_state(ydoc, txn, ["node:member-01"])

    data_desktop = ydoc.get_map("data").get("desktop")
    assert isinstance(data_desktop, Y.YMap)
    assert isinstance(data_desktop.get("pageSchema"), Y.YMap)
    assert data_desktop.get("topbar") == [{"id": "home"}]
    assert data_desktop.get("hiddenSections") == ["node:member-01"]


def test_toast_writer_preserves_attached_desktop_y_map(monkeypatch) -> None:
    ydoc = Y.YDoc()
    with ydoc.begin_transaction() as txn:
        set_map_value_if_changed(
            ydoc.get_map("data"),
            txn,
            "desktop",
            {"pageSchema": {"id": "desktop"}, "toasts": [{"message": "old"}]},
        )

    monkeypatch.setattr(toast_module, "async_get_ydoc", lambda _webspace_id: _AsyncDoc(ydoc))
    monkeypatch.setattr(toast_module, "ystore_write_metadata", lambda **_kwargs: _AsyncNullContext())

    asyncio.run(
        toast_module.WebToastService().push(
            "hello",
            level="info",
            webspace_id="desktop-dev",
            max_items=2,
        )
    )

    desktop = ydoc.get_map("data").get("desktop")
    assert isinstance(desktop, Y.YMap)
    assert isinstance(desktop.get("pageSchema"), Y.YMap)
    assert [item["message"] for item in desktop.get("toasts")] == ["old", "hello"]
