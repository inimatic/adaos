from __future__ import annotations

import os

from adaos.apps.cli.commands import dev


def test_dev_skill_run_marks_and_restores_one_shot_execution(monkeypatch) -> None:
    observed: list[str | None] = []

    class _Manager:
        def run_dev_tool(self, name, tool, payload, *, timeout=None, slot=None):  # noqa: ANN001
            observed.append(os.getenv("ADAOS_DEV_TOOL_EXECUTION_MODE"))
            return {"ok": True, "name": name, "tool": tool, "payload": payload}

    monkeypatch.setattr(dev, "_mgr", lambda: _Manager())
    monkeypatch.setenv("ADAOS_DEV_TOOL_EXECUTION_MODE", "persistent-test")

    dev.dev_skill_run("demo", "inspect", '{"value":"тест"}', None, None)

    assert observed == ["oneshot"]
    assert os.environ["ADAOS_DEV_TOOL_EXECUTION_MODE"] == "persistent-test"
