from __future__ import annotations

import os
from pathlib import Path

import pytest
import typer

from adaos.apps.cli.commands import dev


def test_dev_skill_run_marks_and_restores_one_shot_execution(monkeypatch) -> None:
    observed: list[str | None] = []

    class _Manager:
        def run_dev_tool(self, name, tool, payload, *, timeout=None, slot=None):  # noqa: ANN001
            observed.append(os.getenv("ADAOS_DEV_TOOL_EXECUTION_MODE"))
            return {"ok": True, "name": name, "tool": tool, "payload": payload}

    monkeypatch.setattr(dev, "_mgr", lambda: _Manager())
    monkeypatch.setenv("ADAOS_DEV_TOOL_EXECUTION_MODE", "persistent-test")

    dev.dev_skill_run("demo", "inspect", '{"value":"тест"}', None, None, None)

    assert observed == ["oneshot"]
    assert os.environ["ADAOS_DEV_TOOL_EXECUTION_MODE"] == "persistent-test"


def test_dev_skill_run_reads_unicode_payload_from_utf8_file(monkeypatch, tmp_path: Path) -> None:
    observed: list[dict[str, object]] = []

    class _Manager:
        def run_dev_tool(self, name, tool, payload, *, timeout=None, slot=None):  # noqa: ANN001
            observed.append(payload)
            return {"ok": True}

    payload_file = tmp_path / "payload.json"
    payload_file.write_text('{"request":"Добавить раздел избранного"}', encoding="utf-8")
    monkeypatch.setattr(dev, "_mgr", lambda: _Manager())

    dev.dev_skill_run("demo", "inspect", None, None, None, payload_file)

    assert observed == [{"request": "Добавить раздел избранного"}]


def test_dev_skill_run_rejects_ambiguous_inline_and_file_payloads(tmp_path: Path) -> None:
    payload_file = tmp_path / "payload.json"
    payload_file.write_text("{}", encoding="utf-8")

    with pytest.raises(typer.Exit):
        dev.dev_skill_run("demo", "inspect", "{}", None, None, payload_file)
