from __future__ import annotations

import sys
import types
import importlib.util
from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace

import pytest

if "y_py" not in sys.modules and importlib.util.find_spec("y_py") is None:
    sys.modules["y_py"] = types.SimpleNamespace(
        YDoc=type("YDoc", (), {}),
        encode_state_vector=lambda *args, **kwargs: b"",
        encode_state_as_update=lambda *args, **kwargs: b"",
        apply_update=lambda *args, **kwargs: None,
    )
if "ypy_websocket.ystore" not in sys.modules and importlib.util.find_spec("ypy_websocket.ystore") is None:
    ystore_module = types.ModuleType("ypy_websocket.ystore")
    ystore_module.BaseYStore = type("BaseYStore", (), {})
    ystore_module.YDocNotFound = type("YDocNotFound", (Exception,), {})
    sys.modules["ypy_websocket.ystore"] = ystore_module
if "ypy_websocket" not in sys.modules and importlib.util.find_spec("ypy_websocket") is None:
    pkg = types.ModuleType("ypy_websocket")
    pkg.ystore = sys.modules["ypy_websocket.ystore"]
    sys.modules["ypy_websocket"] = pkg

import adaos.services.pending_actions as pending_actions


class _FakeMap(dict):
    def get(self, key, default=None):  # type: ignore[override]
        return super().get(key, default)

    def set(self, txn, key, value):
        self[key] = value


class _FakeTxn:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeYDoc:
    def __init__(self):
        self._maps = {"data": _FakeMap()}

    def get_map(self, name: str):
        return self._maps.setdefault(name, _FakeMap())

    def begin_transaction(self):
        return _FakeTxn()


class _FakeBus:
    def __init__(self) -> None:
        self.events: list[object] = []

    def publish(self, event) -> None:
        self.events.append(event)


def _make_ctx() -> SimpleNamespace:
    return SimpleNamespace(
        bus=_FakeBus(),
        config=SimpleNamespace(
            node_id="node-test",
            node_id_value="node-test",
            node_settings=SimpleNamespace(id="node-test"),
        ),
    )


@pytest.fixture
def pending_action_docs(monkeypatch):
    docs: dict[str, _FakeYDoc] = {}

    @contextmanager
    def _get_ydoc(webspace_id: str, **kwargs):
        yield docs.setdefault(webspace_id, _FakeYDoc())

    @asynccontextmanager
    async def _async_get_ydoc(webspace_id: str, **kwargs):
        yield docs.setdefault(webspace_id, _FakeYDoc())

    monkeypatch.setattr(pending_actions, "get_ydoc", _get_ydoc)
    monkeypatch.setattr(pending_actions, "async_get_ydoc", _async_get_ydoc)
    monkeypatch.setattr(pending_actions, "default_webspace_id", lambda: "default")
    return docs


def _publish(ctx: SimpleNamespace, **overrides):
    payload = {
        "ctx": ctx,
        "webspace_id": "default",
        "kind": "nlu.teacher.candidate_confirmation",
        "title": "Confirm command understanding",
        "summary": "Open Face Vision?",
        "producer": {"type": "skill", "skill_id": "nlu_teacher"},
        "actions": ["test", "approve", "refuse", "postpone"],
        "ttl_s": 60,
        "response_topic": "nlp.teacher.candidate.confirmation.response",
        "domain_ref": {"candidate_id": "cand.123"},
    }
    payload.update(overrides)
    return pending_actions.publish_pending_action(**payload)


def test_publish_pending_action_projects_node_aware_action_to_yjs(pending_action_docs) -> None:
    ctx = _make_ctx()

    action = _publish(ctx)

    assert action["producer"]["node_id"] == "node-test"
    assert action["producer"]["instance_id"] == "nlu_teacher@node-test"
    assert action["expires_at"] > action["created_at"]
    allowed = {item["id"]: item for item in action["allowed_actions"]}
    assert allowed["test"]["terminal"] is False
    assert allowed["approve"]["terminal"] is True
    assert allowed["approve"]["label_i18n"]["key"] == "pending_actions.action.approve"

    data = pending_action_docs["default"].get_map("data")
    projection = data.get("pending_actions")
    assert projection["schema_version"] == 1
    assert projection["active"] == [action["id"]]
    assert projection["by_id"][action["id"]]["producer"]["node_id"] == "node-test"

    topics = [event.type for event in ctx.bus.events]
    assert topics == ["pending_actions.created", "pending_actions.changed"]


def test_publish_pending_action_rejects_zero_ttl(pending_action_docs) -> None:
    ctx = _make_ctx()

    with pytest.raises(ValueError, match="ttl_s"):
        _publish(ctx, ttl_s=0)

    assert pending_action_docs == {}


def test_response_marks_action_terminal_and_routes_once(pending_action_docs) -> None:
    ctx = _make_ctx()
    action = _publish(ctx)

    result = pending_actions.respond_pending_action(
        action["id"],
        "approve",
        ctx=ctx,
        webspace_id="default",
        responder={"type": "user", "user_id": "owner"},
        response_payload={"source": "test"},
    )

    assert result["terminal"] is True
    assert result["duplicate"] is False
    assert result["response"]["response_action_id"] == "approve"

    projection = pending_action_docs["default"].get_map("data").get("pending_actions")
    stored = projection["by_id"][action["id"]]
    assert stored["status"] == "responded"
    assert projection["active"] == []

    topics = [event.type for event in ctx.bus.events]
    assert topics.count("pending_actions.responded") == 1
    assert topics.count("nlp.teacher.candidate.confirmation.response") == 1

    duplicate = pending_actions.respond_pending_action(
        action["id"],
        "approve",
        ctx=ctx,
        webspace_id="default",
    )

    assert duplicate["duplicate"] is True
    topics = [event.type for event in ctx.bus.events]
    assert topics.count("nlp.teacher.candidate.confirmation.response") == 1


def test_response_to_stale_missing_action_is_idempotent(pending_action_docs) -> None:
    ctx = _make_ctx()

    result = pending_actions.respond_pending_action(
        "pa.missing",
        "approve",
        ctx=ctx,
        webspace_id="default",
    )

    assert result["duplicate"] is True
    assert result["terminal"] is True
    assert result["action"]["stale"] is True
    assert result["response"]["stale"] is True
    assert [event.type for event in ctx.bus.events] == []


def test_non_terminal_test_action_keeps_pending_action_active(pending_action_docs) -> None:
    ctx = _make_ctx()
    action = _publish(ctx)

    result = pending_actions.respond_pending_action(
        action["id"],
        "test",
        ctx=ctx,
        webspace_id="default",
    )

    assert result["terminal"] is False
    projection = pending_action_docs["default"].get_map("data").get("pending_actions")
    stored = projection["by_id"][action["id"]]
    assert stored["status"] == "pending"
    assert projection["active"] == [action["id"]]
    assert stored["last_response"]["response_action_id"] == "test"


def test_expire_pending_actions_marks_stale_items(pending_action_docs) -> None:
    ctx = _make_ctx()
    action = _publish(ctx, ttl_s=None, expires_at=1)

    result = pending_actions.expire_pending_actions(ctx=ctx, webspace_id="default")

    assert [item["id"] for item in result["expired"]] == [action["id"]]
    projection = pending_action_docs["default"].get_map("data").get("pending_actions")
    assert projection["by_id"][action["id"]]["status"] == "expired"
    assert projection["active"] == []
    assert any(event.type == "pending_actions.expired" for event in ctx.bus.events)
