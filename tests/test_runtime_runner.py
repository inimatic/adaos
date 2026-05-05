from __future__ import annotations

import sys
from pathlib import Path

from adaos.skills import runtime_runner as runtime_runner_module


def _write_skill(root: Path, name: str, marker: str) -> Path:
    skill_dir = root / name
    (skill_dir / "handlers").mkdir(parents=True, exist_ok=True)
    (skill_dir / "handlers" / "__init__.py").write_text("", encoding="utf-8")
    (skill_dir / "handlers" / "main.py").write_text(
        "def get_snapshot(**kwargs):\n"
        f"    return {{'skill': '{name}', 'marker': '{marker}', 'kwargs': dict(kwargs)}}\n",
        encoding="utf-8",
    )
    return skill_dir


def test_execute_tool_isolates_generic_handlers_main_between_skills(tmp_path: Path) -> None:
    alpha = _write_skill(tmp_path, "alpha_skill", "alpha")
    beta = _write_skill(tmp_path, "beta_skill", "beta")

    before = {key: sys.modules[key] for key in list(sys.modules.keys()) if key == "handlers" or key.startswith("handlers.")}
    try:
        first = runtime_runner_module.execute_tool(alpha, module="handlers.main", attr="get_snapshot", payload={"city": "Berlin"})
        second = runtime_runner_module.execute_tool(beta, module="handlers.main", attr="get_snapshot", payload={"city": "Moscow"})
    finally:
        for key in list(sys.modules.keys()):
            if key == "handlers" or key.startswith("handlers."):
                sys.modules.pop(key, None)
        sys.modules.update(before)

    assert first["skill"] == "alpha_skill"
    assert second["skill"] == "beta_skill"
    assert second["marker"] == "beta"
