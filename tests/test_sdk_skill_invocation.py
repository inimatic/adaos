from __future__ import annotations

from types import SimpleNamespace

import pytest

from adaos.sdk.skills import invocation


def test_skill_invocation_requires_capability_and_uses_runtime_manager(monkeypatch) -> None:
    calls: list[tuple[str, str, dict, float | None]] = []
    ctx = SimpleNamespace(
        skills_repo=object(),
        git=object(),
        paths=object(),
        bus=object(),
        caps=object(),
        settings=object(),
    )

    class Manager:
        def __init__(self, **_kwargs) -> None:
            pass

        def run_tool(self, skill, tool, payload, *, timeout=None):
            calls.append((skill, tool, dict(payload), timeout))
            return {"ok": True}

    monkeypatch.setattr(invocation, "require_ctx", lambda _name: ctx)
    admitted: list[str] = []
    monkeypatch.setattr(
        invocation,
        "require_skill_capability",
        lambda _ctx, capability: admitted.append(capability),
    )
    monkeypatch.setattr("adaos.services.skill.manager.SkillManager", Manager)

    result = invocation.invoke("runner_skill", "prepare_attempt", {"seed": 17}, timeout=2.5)

    assert result == {"ok": True}
    assert admitted == ["skills.invoke"]
    assert calls == [("runner_skill", "prepare_attempt", {"seed": 17}, 2.5)]


@pytest.mark.parametrize("value", ["", "../other", "skill/tool", "skill name"])
def test_skill_invocation_rejects_non_identifier_targets(monkeypatch, value: str) -> None:
    monkeypatch.setattr(invocation, "require_ctx", lambda _name: object())
    monkeypatch.setattr(invocation, "require_skill_capability", lambda *_args: None)
    with pytest.raises(ValueError):
        invocation.invoke(value, "tool", {})
