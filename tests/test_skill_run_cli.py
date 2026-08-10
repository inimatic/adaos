from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner

from adaos.apps.cli.commands import skill as skill_cmd


def test_skill_run_accepts_utf8_json_file(tmp_path, monkeypatch) -> None:
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(
        json.dumps({"direction_id": "tlp_direction_skill", "text": "Уточнить постановку"}, ensure_ascii=False),
        encoding="utf-8",
    )
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        skill_cmd,
        "_mgr",
        lambda: SimpleNamespace(
            run_tool=lambda name, tool, payload, timeout=None: calls.append((name, tool, payload, timeout))
            or {"ok": True}
        ),
    )

    result = CliRunner().invoke(
        skill_cmd.app,
        ["run", "research_orchestrator_skill", "chat", "--json-file", str(payload_path), "--timeout", "12"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"ok": True}
    assert calls == [
        (
            "research_orchestrator_skill",
            "chat",
            {"direction_id": "tlp_direction_skill", "text": "Уточнить постановку"},
            12.0,
        )
    ]


def test_skill_run_rejects_inline_and_file_payload_together(tmp_path) -> None:
    payload_path = tmp_path / "payload.json"
    payload_path.write_text("{}", encoding="utf-8")

    result = CliRunner().invoke(
        skill_cmd.app,
        ["run", "demo", "tool", "--json", "{}", "--json-file", str(payload_path)],
    )

    assert result.exit_code == 1
    assert "use either --json or --json-file" in result.output
