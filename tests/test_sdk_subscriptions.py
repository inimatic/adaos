from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from adaos.sdk.core import decorators
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
