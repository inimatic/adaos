from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from adaos.sdk.core import decorators
from adaos.services.status.hot_events import HotEventBudget
from adaos.services.workspace_registry import write_workspace_registry


def test_subscription_log_suffix_includes_activation_strategy(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "infrascope_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text(
        "\n".join(
            [
                "name: Infrascope",
                "version: '0.9.0'",
                "runtime:",
                "  activation:",
                "    mode: lazy",
                "    startup_allowed: false",
                "    background_refresh: false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    write_workspace_registry(
        workspace,
        {
            "version": 1,
            "updated_at": "2026-04-19T00:00:00+00:00",
            "skills": [
                {
                    "kind": "skill",
                    "id": "infrascope_skill",
                    "name": "infrascope_skill",
                    "path": "skills/infrascope_skill",
                    "source": {"path": "skills/infrascope_skill", "manifest": "skills/infrascope_skill/skill.yaml"},
                    "activation": {
                        "mode": "lazy",
                        "startup_allowed": False,
                        "background_refresh": False,
                    },
                }
            ],
            "scenarios": [],
        },
    )

    monkeypatch.setattr(
        decorators,
        "require_ctx",
        lambda _reason: SimpleNamespace(paths=SimpleNamespace(workspace_dir=lambda: workspace)),
    )

    suffix = decorators._subscription_log_suffix("infrascope_skill")

    assert suffix == " activation=lazy subscription_strategy=early_cheap_handlers"


def test_subscription_log_suffix_is_empty_for_unknown_skill() -> None:
    assert decorators._subscription_log_suffix("<unknown>") == ""


def test_register_subscriptions_replaces_skill_generation(monkeypatch) -> None:
    calls: list[str] = []
    registered: list[object] = []

    def old_handler(_evt):
        calls.append("old")

    def new_handler(_evt):
        calls.append("new")

    monkeypatch.setattr(decorators, "subscriptions", [("topic.demo", old_handler)])
    monkeypatch.setattr(decorators, "_registered", False)
    monkeypatch.setattr(decorators, "_SKILL_SUBSCRIPTION_GENERATIONS", {})
    monkeypatch.setattr(decorators, "_infer_skill_name", lambda _fn: "demo_skill")
    monkeypatch.setattr(decorators, "_skill_event_targets_this_node", lambda _evt: True)
    monkeypatch.setattr(decorators, "_admit_skill_subscription_yjs_work", lambda *_args: {"allowed": True})
    monkeypatch.setattr(decorators, "_maybe_push_skill", lambda *_args: False)
    monkeypatch.setattr(decorators, "_subscription_log_suffix", lambda _skill: "")

    async def fake_on(_topic, handler):
        registered.append(handler)

    async def fake_emit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(decorators, "on", fake_on)
    monkeypatch.setattr(decorators, "emit", fake_emit)

    asyncio.run(decorators.register_subscriptions())
    assert len(registered) == 1
    asyncio.run(registered[0](SimpleNamespace(payload={})))  # type: ignore[misc]

    decorators.subscriptions.append(("topic.demo", new_handler))
    asyncio.run(decorators.register_subscriptions(skill_names={"demo_skill"}, force=True))
    assert len(registered) == 2
    asyncio.run(registered[0](SimpleNamespace(payload={})))  # old generation is stale
    asyncio.run(registered[1](SimpleNamespace(payload={})))

    assert calls == ["old", "new"]


def test_stream_control_subscriptions_bypass_yjs_owner_guard(monkeypatch) -> None:
    fake_guard = ModuleType("adaos.services.yjs.owner_guard")

    def fail_admission(**_kwargs):
        raise AssertionError("YJS owner guard should not govern stream-control subscriptions")

    fake_guard.admit_owner_work = fail_admission  # type: ignore[attr-defined]
    fake_guard.skill_owner = lambda skill: f"skill:{skill}"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "adaos.services.yjs.owner_guard", fake_guard)

    for topic in ("webio.stream.snapshot.requested", "webio.stream.subscription.changed"):
        admission = decorators._admit_skill_subscription_yjs_work(
            "demo_skill",
            topic,
            SimpleNamespace(payload={"webspace_id": "desktop"}),
        )

        assert admission == {
            "allowed": True,
            "governed": False,
            "reason": "stream_control_uses_stream_guard",
        }


def test_non_stream_subscription_still_uses_yjs_owner_guard(monkeypatch) -> None:
    calls: list[dict] = []
    fake_guard = ModuleType("adaos.services.yjs.owner_guard")

    def admit_owner_work(**kwargs):
        calls.append(kwargs)
        return {"allowed": False, "reason": "owner_quarantined"}

    fake_guard.admit_owner_work = admit_owner_work  # type: ignore[attr-defined]
    fake_guard.skill_owner = lambda skill: f"skill:{skill}"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "adaos.services.yjs.owner_guard", fake_guard)

    admission = decorators._admit_skill_subscription_yjs_work(
        "demo_skill",
        "infrastate.refresh",
        SimpleNamespace(payload={"webspace_id": "desktop"}),
    )

    assert admission == {"allowed": False, "reason": "owner_quarantined"}
    assert calls
    assert calls[0]["owner"] == "skill:demo_skill"
    assert calls[0]["root_names"] == ["data"]
    assert calls[0]["path"] == "event/infrastate.refresh"


def test_critical_control_plane_subscription_uses_bounded_bypass(monkeypatch) -> None:
    fake_guard = ModuleType("adaos.services.yjs.owner_guard")

    def admit_owner_work(**kwargs):
        return {
            "allowed": False,
            "reason": "owner_quarantined",
            "owner": kwargs["owner"],
            "webspace_id": kwargs["webspace_id"],
            "policy_state": "block",
        }

    fake_guard.admit_owner_work = admit_owner_work  # type: ignore[attr-defined]
    fake_guard.skill_owner = lambda skill: f"skill:{skill}"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "adaos.services.yjs.owner_guard", fake_guard)
    monkeypatch.setattr(
        decorators,
        "_CRITICAL_CONTROL_PLANE_BUDGET",
        HotEventBudget(debounce_ms=1000, window_ms=5000, max_events=2),
    )

    admission = decorators._admit_skill_subscription_yjs_work(
        "infrastate_skill",
        "core.update.status",
        SimpleNamespace(
            type="core.update.status",
            payload={
                "webspace_id": "desktop",
                "state": "succeeded",
                "phase": "validate",
                "target_version": "rev1",
                "active_slot": "A",
                "active_git_short_commit": "abc1234",
            },
        ),
    )

    assert admission["allowed"] is True
    assert admission["reason"] == "critical_control_plane_budget"
    assert admission["critical_control_plane"] is True
    assert admission["owner_guard_allowed"] is False
    assert admission["owner_guard_reason"] == "owner_quarantined"
    assert admission["hot_event"]["reason"] == "admitted"
    assert admission["owner"] == "skill:infrastate_skill"


def test_critical_control_plane_bypass_is_debounced(monkeypatch) -> None:
    fake_guard = ModuleType("adaos.services.yjs.owner_guard")
    fake_guard.admit_owner_work = lambda **kwargs: {  # type: ignore[attr-defined]
        "allowed": False,
        "reason": "write_amplification_blocked",
        "owner": kwargs["owner"],
        "webspace_id": kwargs["webspace_id"],
    }
    fake_guard.skill_owner = lambda skill: f"skill:{skill}"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "adaos.services.yjs.owner_guard", fake_guard)
    monkeypatch.setattr(
        decorators,
        "_CRITICAL_CONTROL_PLANE_BUDGET",
        HotEventBudget(debounce_ms=1000, window_ms=5000, max_events=2),
    )

    evt = SimpleNamespace(
        type="core.update.status",
        payload={
            "webspace_id": "desktop",
            "state": "restarting",
            "phase": "restart",
            "target_version": "rev1",
            "active_slot": "B",
        },
    )

    first = decorators._admit_skill_subscription_yjs_work("infrastate_skill", "core.update.status", evt)
    second = decorators._admit_skill_subscription_yjs_work("infrastate_skill", "core.update.status", evt)

    assert first["allowed"] is True
    assert second["allowed"] is False
    assert second["reason"] == "critical_control_plane_debounce"
    assert second["critical_control_plane"] is True
    assert second["retry_after_s"] > 0


def test_browser_session_changed_remains_governed_by_owner_guard(monkeypatch) -> None:
    fake_guard = ModuleType("adaos.services.yjs.owner_guard")
    fake_guard.admit_owner_work = lambda **kwargs: {  # type: ignore[attr-defined]
        "allowed": False,
        "reason": "owner_quarantined",
        "owner": kwargs["owner"],
        "webspace_id": kwargs["webspace_id"],
    }
    fake_guard.skill_owner = lambda skill: f"skill:{skill}"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "adaos.services.yjs.owner_guard", fake_guard)
    monkeypatch.setattr(
        decorators,
        "_CRITICAL_CONTROL_PLANE_BUDGET",
        HotEventBudget(debounce_ms=1000, window_ms=5000, max_events=2),
    )

    admission = decorators._admit_skill_subscription_yjs_work(
        "infrastate_skill",
        "browser.session.changed",
        SimpleNamespace(payload={"webspace_id": "desktop"}),
    )

    assert admission["allowed"] is False
    assert admission["reason"] == "owner_quarantined"
    assert "critical_control_plane" not in admission
