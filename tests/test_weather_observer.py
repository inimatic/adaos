from __future__ import annotations

import importlib.util
import sys
import types
from types import SimpleNamespace


if "y_py" not in sys.modules and importlib.util.find_spec("y_py") is None:
    sys.modules["y_py"] = types.SimpleNamespace(YMap=dict, YArray=list)

from adaos.services.weather import observer


class _Doc:
    def __init__(self, data: dict[str, object]) -> None:
        self.data = data

    def get_map(self, name: str) -> dict[str, object]:
        assert name == "data"
        return self.data

    def observe_after_transaction(self, _callback) -> int:
        return 7


def _reset_observer_state() -> None:
    observer._YDOC_OBSERVERS.clear()  # noqa: SLF001
    observer._YDOC_LOOPS.clear()  # noqa: SLF001
    observer._PENDING_DOC_CHECKS.clear()  # noqa: SLF001
    observer._LAST_CITY_IN_DOC.clear()  # noqa: SLF001
    observer._LAST_CITY_TARGET_NODE.clear()  # noqa: SLF001
    observer._OBSERVER_STATS.clear()  # noqa: SLF001


def test_weather_observer_reads_local_node_scoped_city(monkeypatch) -> None:
    monkeypatch.setattr(observer, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(node_id="node-local")))
    doc = _Doc(
        {
            "nodes": {
                "node-remote": {"weather": {"current": {"city": "Paris"}}},
                "node-local": {"weather": {"current": {"city": "Berlin"}}},
            }
        }
    )

    assert observer._current_city_from_doc(doc) == ("Berlin", "node-local")  # noqa: SLF001


def test_weather_observer_keeps_legacy_unscoped_city_first(monkeypatch) -> None:
    monkeypatch.setattr(observer, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(node_id="node-local")))
    doc = _Doc(
        {
            "weather": {"current": {"city": "Moscow"}},
            "nodes": {
                "node-local": {"weather": {"current": {"city": "Berlin"}}},
            },
        }
    )

    assert observer._current_city_from_doc(doc) == ("Moscow", None)  # noqa: SLF001


def test_weather_observer_emits_target_node_for_node_scoped_city(monkeypatch) -> None:
    _reset_observer_state()
    published: list[object] = []

    class _Bus:
        def publish(self, event: object) -> None:
            published.append(event)

    monkeypatch.setattr(
        observer,
        "get_ctx",
        lambda: SimpleNamespace(config=SimpleNamespace(node_id="node-local"), bus=_Bus()),
    )
    doc = _Doc({"nodes": {"node-local": {"weather": {"current": {"city": "Berlin"}}}}})

    observer._ensure_city_observer("desktop", doc)  # noqa: SLF001

    assert len(published) == 1
    event = published[0]
    assert getattr(event, "type") == "weather.city_changed"
    assert getattr(event, "payload")["city"] == "Berlin"
    assert getattr(event, "payload")["target_node_id"] == "node-local"
    assert getattr(event, "payload")["_meta"]["target_node_id"] == "node-local"
