"""CLI helpers for working with scenarios via the SDK runtime."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

import typer

from adaos.sdk.scenarios.runtime import ScenarioRuntime, ensure_runtime_context, load_scenario

scenario_app = typer.Typer(help="Выполнение и тестирование сценариев")


def _scenario_root() -> Path:
    ctx_base = Path.cwd()
    candidate = ctx_base / ".adaos" / "scenarios"
    if candidate.exists():
        return candidate
    return ctx_base


def _scenario_path(scenario_id: str, override: Optional[str]) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    root = _scenario_root()
    if (root / "scenario.yaml").exists():
        return (root / "scenario.yaml").resolve()
    candidate = root / scenario_id / "scenario.yaml"
    if candidate.exists():
        return candidate.resolve()
    raise FileNotFoundError(f"cannot locate scenario file for '{scenario_id}'")


def _base_dir_for(path: Path) -> Path:
    for parent in path.parents:
        if parent.name == ".adaos":
            return parent
    return path.parent


@scenario_app.command("run")
def run(
    scenario_id: str = typer.Argument(..., help="Идентификатор сценария"),
    path: Optional[str] = typer.Option(None, "--path", help="Путь к scenario.yaml"),
) -> None:
    scenario_path = _scenario_path(scenario_id, path)
    ensure_runtime_context(_base_dir_for(scenario_path))
    runtime = ScenarioRuntime()
    result = runtime.run_from_file(str(scenario_path))
    meta = result.get("meta") or {}
    log_file = meta.get("log_file")
    typer.secho(f"Scenario '{scenario_id}' executed", fg=typer.colors.GREEN)
    if log_file:
        typer.echo(f"Log: {log_file}")
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@scenario_app.command("validate")
def validate(
    scenario_id: str = typer.Argument(..., help="Идентификатор сценария"),
    path: Optional[str] = typer.Option(None, "--path", help="Путь к scenario.yaml"),
) -> None:
    scenario_path = _scenario_path(scenario_id, path)
    model = load_scenario(scenario_path)
    runtime = ScenarioRuntime()
    errors = runtime.validate(model)
    if errors:
        typer.secho("Validation errors:", fg=typer.colors.RED)
        for err in errors:
            typer.echo(f" - {err}")
        raise typer.Exit(code=1)
    typer.secho(f"Scenario '{scenario_id}' is valid", fg=typer.colors.GREEN)


def _collect_scenario_tests(scenario_id: Optional[str]) -> list[Path]:
    root = _scenario_root()
    tests: list[Path] = []
    if not root.exists():
        return tests
    if scenario_id:
        candidates = [root / scenario_id / "tests"]
    else:
        candidates = [p / "tests" for p in root.iterdir() if p.is_dir()]
    for tests_dir in candidates:
        if tests_dir.is_dir():
            if any(tests_dir.glob("test_*.py")):
                tests.append(tests_dir)
    return tests


@scenario_app.command("test")
def test(
    scenario_id: Optional[str] = typer.Argument(None, help="Идентификатор сценария или все"),
    extra: Optional[str] = typer.Option(None, "--pytest-args", help="Дополнительные аргументы pytest"),
) -> None:
    tests = _collect_scenario_tests(scenario_id)
    if not tests:
        typer.secho("No scenario tests found", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)

    args = ["pytest", "-q", *[str(p) for p in tests]]
    if extra:
        args.extend(extra.split())

    typer.echo(f"Running scenario tests: {' '.join(args)}")
    result = subprocess.run(args, text=True)
    raise typer.Exit(code=result.returncode)


__all__ = ["scenario_app"]
