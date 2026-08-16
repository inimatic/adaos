# src/adaos/apps/cli/commands/tests.py
from __future__ import annotations
import os, sys
import json
from pathlib import Path
from typing import List, Optional
import typer
import subprocess
import inspect
import tempfile
import xml.etree.ElementTree as ET
from adaos.apps.cli.i18n import _
from adaos.services.sandbox.bootstrap import ensure_dev_venv
from adaos.services.agent_context import get_ctx
from adaos.services.skill.manager import SkillManager
from adaos.adapters.db import SqliteSkillRegistry
from adaos.ports.sandbox import ExecLimits

app = typer.Typer(help="Run AdaOS test suites")

_PYTEST_COMMAND_LINE_SAFE_CHARS = 24_000
_TEST_DISCOVERY_EXCLUDED_DIRS = {
    ".git",
    ".runtime",
    ".venv",
    "__pycache__",
    "node_modules",
    "runtime",
    "site-packages",
    "venv",
    "vendor",
}


def _ensure_tmp_base_dir() -> Path:
    import tempfile

    p = Path(tempfile.mkdtemp(prefix="adaos_test_base_"))
    (p / "skills").mkdir(parents=True, exist_ok=True)
    os.environ["ADAOS_BASE_DIR"] = str(p)
    os.environ.setdefault("HOME", str(p))
    os.environ.setdefault("USERPROFILE", str(p))
    # git identity + флаг тестов
    os.environ.setdefault("GIT_AUTHOR_NAME", "AdaOS Test Bot")
    os.environ.setdefault("GIT_AUTHOR_EMAIL", "testbot@example.com")
    os.environ.setdefault("GIT_COMMITTER_NAME", "AdaOS Test Bot")
    os.environ.setdefault("GIT_COMMITTER_EMAIL", "testbot@example.com")
    os.environ["ADAOS_TESTING"] = "1"
    return p


def _prepare_skills_repo(no_clone: bool) -> None:
    """
    Гарантируем, что {BASE}/skills — корректный git-репозиторий монорепо.
    Без лишних pull/checkout: только clone при отсутствии .git.
    """
    if no_clone:
        return
    ctx = get_ctx()
    root = ctx.paths.skills_dir()
    if not (root / ".git").exists():
        # ровно один раз: clone (ветка/URL из settings, allow-list в SecureGit)
        ctx.git.ensure_repo(str(root), ctx.settings.skills_monorepo_url, branch=ctx.settings.skills_monorepo_branch)
        # лёгкий exclude, чтобы не шумели временные файлы
        try:
            (root / ".git" / "info").mkdir(parents=True, exist_ok=True)
            ex = root / ".git" / "info" / "exclude"
            existing = ex.read_text(encoding="utf-8").splitlines() if ex.exists() else []
            wanted = {"*.pyc", "__pycache__/", ".venv/", "state/", "cache/", "logs/"}
            merged = sorted(set(existing).union(wanted))
            ex.write_text("\n".join(merged) + "\n", encoding="utf-8")
        except Exception:
            pass


def _skill_manager() -> SkillManager:
    ctx = get_ctx()
    repo = ctx.skills_repo
    registry = SqliteSkillRegistry(ctx.sql)
    return SkillManager(repo=repo, registry=registry, git=ctx.git, paths=ctx.paths, bus=getattr(ctx, "bus", None), caps=ctx.caps)


def _install_all_skills(limit: int | None = None) -> list[str]:
    ctx = get_ctx()
    repo = ctx.skills_repo
    try:
        repo.ensure()
    except Exception:
        pass

    names: list[str] = []
    try:
        for item in repo.list() or []:
            name = getattr(item, "name", None)
            if name:
                names.append(name)
    except Exception:
        pass

    if not names:
        names = ["example_skill"]

    if limit:
        names = names[:limit]

    mgr = _skill_manager()
    installed: list[str] = []
    for name in names:
        try:
            mgr.install(name, validate=False)
            installed.append(name)
        except Exception:
            continue
    return installed


def _collect_test_dirs(root: Path) -> List[str]:
    if not root.is_dir():
        return []

    test_dirs: dict[Path, None] = {}
    for current_dir, dir_names, file_names in os.walk(root):
        dir_names[:] = [
            name for name in dir_names if name not in _TEST_DISCOVERY_EXCLUDED_DIRS
        ]
        if not any(name.startswith("test_") and name.endswith(".py") for name in file_names):
            continue

        current = Path(current_dir)
        for candidate in (current, *current.parents):
            if candidate == root.parent:
                break
            if candidate.name == "tests":
                test_dirs.setdefault(candidate, None)
                break
    return [str(path.resolve()) for path in sorted(test_dirs)]


def _prune_duplicate_skill_tests(paths: List[str]) -> List[str]:
    """Prefer runtime/tests over tests for the same skill tree.

    Skill layout inside slot src is typically:
      src/skills/<name>/tests
      src/skills/<name>/runtime/tests

    Collecting both causes pytest import collisions when filenames match.
    This function keeps runtime/tests if present and removes the sibling tests/.
    """
    from pathlib import Path as _P

    # Map skill base -> set of candidate test dirs
    grouped: dict[str, set[str]] = {}
    for p in paths:
        try:
            pp = _P(p).resolve()
        except Exception:
            pp = _P(p)
        parts = pp.parts
        matched_skill_path = False
        try:
            # find pattern .../src/skills/<name>/(runtime/)?tests
            if "src" in parts and "skills" in parts:
                si = parts.index("src")
                sk = parts.index("skills", si + 1)
                name = parts[sk + 1] if len(parts) > sk + 1 else None
                if name:
                    base = _P(*parts[: sk + 2])  # up to src/skills/<name>
                    grouped.setdefault(str(base), set()).add(str(pp))
                    matched_skill_path = True
        except ValueError:
            pass
        if not matched_skill_path:
            grouped.setdefault("__other__", set()).add(str(pp))

    keep: set[str] = set()
    for base, items in grouped.items():
        if base == "__other__":
            keep.update(items)
            continue
        # Identify runtime/tests path(s) robustly via pathlib
        runtime_items = [
            x
            for x in items
            if _P(x).name == "tests" and _P(x).parent.name == "runtime"
        ]
        if runtime_items:
            keep.update(runtime_items)
        else:
            keep.update(items)
    # Preserve original order
    result: List[str] = []
    for p in paths:
        if p in keep and p not in result:
            result.append(p)
    return result


def _drive_key(path_str: str) -> str:
    # На *nix вернётся пусто, на Windows — 'C:' / 'D:' и т.п.
    import os as _os

    d, _ = _os.path.splitdrive(path_str)
    return (d or "NO_DRIVE").upper()


def _build_pytest_command(
    *,
    executable: str,
    prefix: list[str],
    pytest_args: list[str],
    paths: list[str],
    base_dir: Path,
) -> tuple[list[str], Path | None]:
    command_prefix = [executable, *prefix, "-m", "pytest", *pytest_args]
    direct_command = [*command_prefix, *paths]
    if len(subprocess.list2cmdline(direct_command)) < _PYTEST_COMMAND_LINE_SAFE_CHARS:
        return direct_command, None

    base_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        prefix="adaos-pytest-paths-",
        suffix=".txt",
        dir=base_dir,
        delete=False,
    ) as handle:
        for path in paths:
            if "\n" in path or "\r" in path:
                raise ValueError("pytest path contains a newline")
            handle.write(f"{path}\n")
        args_file = Path(handle.name)
    return [*command_prefix, f"@{args_file}"], args_file


def _find_skill_root(path: Path) -> Path | None:
    current = path.resolve()
    for candidate in (current, *current.parents):
        if (candidate / "skill.yaml").is_file():
            return candidate
    return None


def _skill_test_environment(paths: list[str]) -> tuple[list[str], set[str], Path | None]:
    python_paths: list[str] = []
    skill_names: set[str] = set()
    skill_roots: list[Path] = []

    def _add(path: Path) -> None:
        value = str(path)
        if value not in python_paths:
            python_paths.append(value)

    for path in paths:
        test_path = Path(path)
        skill_root = _find_skill_root(test_path)
        if skill_root is None:
            continue
        if skill_root not in skill_roots:
            skill_roots.append(skill_root)
        skill_names.add(skill_root.name)
        _add(test_path if test_path.is_dir() else test_path.parent)
        _add(skill_root)

        skills_dir = skill_root.parent
        if skills_dir.name == "skills":
            namespace_root = skills_dir.parent
            _add(namespace_root)

            runtime_descriptor = skills_dir / ".runtime" / skill_root.name / "current_runtime.json"
            try:
                runtime = json.loads(runtime_descriptor.read_text(encoding="utf-8"))
                runtime_bucket = str(runtime.get("runtime_bucket") or "").strip()
                vendor_root = runtime_descriptor.parent / runtime_bucket / "vendor"
                if runtime_bucket and vendor_root.is_dir():
                    _add(vendor_root)
            except (OSError, ValueError, TypeError):
                pass

        for parent in skill_root.parents:
            vendor_root = parent / "vendor"
            if vendor_root.is_dir():
                _add(vendor_root)
                break

    single_root = skill_roots[0] if len(skill_roots) == 1 else None
    return python_paths, skill_names, single_root


def _junit_target(args: list[str]) -> str | None:
    for index, arg in enumerate(args):
        for option in ("--junitxml", "--junit-xml"):
            if arg == option and index + 1 < len(args):
                return args[index + 1]
            prefix = f"{option}="
            if arg.startswith(prefix):
                return arg[len(prefix) :]
    return None


def _replace_junit_target(args: list[str], target: Path) -> list[str]:
    updated = list(args)
    target_value = target.as_posix()
    for index, arg in enumerate(updated):
        for option in ("--junitxml", "--junit-xml"):
            if arg == option and index + 1 < len(updated):
                updated[index + 1] = target_value
                return updated
            prefix = f"{option}="
            if arg.startswith(prefix):
                updated[index] = f"{prefix}{target_value}"
                return updated
    return updated


def _merge_junit_reports(shards: list[Path], target: Path) -> int:
    suites: list[ET.Element] = []
    for shard in shards:
        if not shard.is_file():
            continue
        document_root = ET.parse(shard).getroot()
        if document_root.tag == "testsuite":
            suites.append(document_root)
        else:
            suites.extend(document_root.findall("testsuite"))

    if not suites:
        return 0

    merged = ET.Element("testsuites", {"name": "pytest tests"})
    integer_totals = {name: 0 for name in ("tests", "errors", "failures", "skipped")}
    elapsed = 0.0
    for suite in suites:
        merged.append(suite)
        for name in integer_totals:
            integer_totals[name] += int(suite.get(name, "0"))
        elapsed += float(suite.get("time", "0"))
    for name, value in integer_totals.items():
        merged.set(name, str(value))
    merged.set("time", f"{elapsed:.3f}")

    target.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(merged).write(target, encoding="utf-8", xml_declaration=True)
    for shard in shards:
        shard.unlink(missing_ok=True)
    return len(suites)


def _run_one_group(
    ctx=None, *, base_dir: Path, venv_python: str, paths: list[str], addopts: str = "", py_exec: str, py_prefix: list[str], use_sandbox: bool  # ← опционально: AgentContext
) -> tuple[int, str, str]:
    """
    Запускает pytest внутри песочницы.
    - регистрирует маркер asyncio через `-o markers=...`
    - пользовательские фильтры передаются через PYTEST_ADDOPTS (addopts)
    - если передан ctx — добавляем полезные переменные окружения
    """
    if not paths:
        return 5, "no tests ran (empty path group)", ""

    pytest_args = [
        "-q",
        "--strict-markers",
        "--import-mode=importlib",
    ]

    extra_env = {"PYTHONUNBUFFERED": "1"}
    # доп. окружение из контекста — безопасно и опционально
    try:
        if ctx is not None:
            extra_env.update(
                {
                    "ADAOS_LANG": getattr(ctx.settings, "lang", None) or os.environ.get("ADAOS_LANG", "en"),
                    "ADAOS_PROFILE": getattr(ctx.settings, "profile", None) or os.environ.get("ADAOS_PROFILE", "default"),
                }
            )
    except Exception:
        pass

    # Give each isolated skill process both its top-level imports (handlers.*)
    # and its namespace imports (skills.<name>.*).
    try:
        pp_entries, skill_names, skill_root = _skill_test_environment(paths)
        if pp_entries:
            extra_env["PYTHONNOUSERSITE"] = "1"
            extra_env["PYTHONPATH"] = os.pathsep.join(pp_entries)
        if len(skill_names) == 1:
            skill_name = next(iter(skill_names))
            extra_env.setdefault("ADAOS_SKILL_NAME", skill_name)
            extra_env.setdefault("ADAOS_SKILL_PACKAGE", f"skills.{skill_name}")
            extra_env.setdefault("ADAOS_SKILL_MODE", "runtime")
            if skill_root is not None:
                extra_env.setdefault("ADAOS_SKILL_ROOT", str(skill_root))
                extra_env.setdefault("ADAOS_DEV_SKILL_DIR", str(skill_root))
    except Exception:
        pass

    if addopts:
        # preserve user's opts and ensure our importmode survives
        extra_env["PYTEST_ADDOPTS"] = addopts
    cmd, args_file = _build_pytest_command(
        executable=(venv_python or py_exec),
        prefix=([] if venv_python else py_prefix),
        pytest_args=pytest_args,
        paths=paths,
        base_dir=base_dir,
    )
    test_cwd = Path(ctx.paths.repo_root()).resolve() if ctx is not None else base_dir
    try:
        return _sandbox_run(cmd, cwd=test_cwd, extra_env=extra_env, use_sandbox=use_sandbox)
    finally:
        if args_file is not None:
            args_file.unlink(missing_ok=True)


def _mk_sandbox(base_dir: Path, profile: str = "tool"):
    """
    Создаём ProcSandbox, совместимую с разными сигнатурами конструктора:
    - ProcSandbox(fs_base=...)
    - ProcSandbox(base_dir=...) / ProcSandbox(base=...)
    - ProcSandbox(<positional>)
    Дополнительно прокидываем profile, если он поддерживается.
    """
    from adaos.services.sandbox.runner import ProcSandbox
    import inspect

    bd = Path(base_dir)
    sig = inspect.signature(ProcSandbox)
    params = sig.parameters

    # Подготовим kwargs по поддерживаемым именам
    kwargs = {}
    if "fs_base" in params:
        kwargs["fs_base"] = bd
    elif "base_dir" in params:
        kwargs["base_dir"] = bd
    elif "base" in params:
        kwargs["base"] = bd

    if "profile" in params:
        kwargs.setdefault("profile", profile)

    # 1) Попытка вызвать с подобранными kwargs
    try:
        if kwargs:
            return ProcSandbox(**kwargs)
    except TypeError:
        pass

    # 2) Попытка позиционным первым аргументом (если допустимо)
    try:
        # есть ли хотя бы один позиционный параметр без значения по умолчанию?
        positional_ok = any(p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.default is p.empty for p in params.values())
        if positional_ok:
            return ProcSandbox(bd)
    except TypeError:
        pass

    # 3) Последняя попытка: без аргументов + ручная установка базы
    try:
        sb = ProcSandbox()
    except TypeError as e:
        raise TypeError(f"ProcSandbox ctor incompatible; expected one of fs_base/base_dir/base/positional. Original: {e}") from e

    # Пробуем сеттеры/атрибуты
    for attr in ("set_fs_base", "set_base"):
        if hasattr(sb, attr):
            try:
                getattr(sb, attr)(bd)
                return sb
            except Exception:
                pass
    for attr in ("fs_base", "base_dir", "base", "_base"):
        if hasattr(sb, attr):
            try:
                setattr(sb, attr, bd)
                return sb
            except Exception:
                pass

    return sb  # как есть (но это вряд ли)


def _run_direct(cmd: list[str], *, cwd: Path, extra_env: dict | None = None):
    env = dict(os.environ)
    if extra_env:
        env.update({k: v for k, v in (extra_env or {}).items() if isinstance(k, str) and isinstance(v, str)})
    p = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    return p.returncode, p.stdout, p.stderr


def _sandbox_run(cmd: list[str], *, cwd: Path, profile: str = "tool", extra_env: dict | None = None, use_sandbox: bool = True):
    """
    Универсальный запуск внутри песочницы:
    - авто-определяет сигнатуру ProcSandbox.run(...) и прокидывает только поддерживаемые kwargs
    - если inherit_env не поддерживается, сам мерджит окружение
    - нормализует результат к (exit_code, stdout, stderr)
    """
    if not use_sandbox or os.getenv("ADAOS_SANDBOX_DISABLED") == "1":
        return _run_direct(cmd, cwd=cwd, extra_env=extra_env)
    extra_env = dict(extra_env or {})
    # По возможности просигнализируем раннеру о «лёгком» профиле
    extra_env.setdefault("ADAOS_SANDBOX_PROFILE", "tests")
    extra_env.setdefault("ADAOS_SANDBOX_FS_SIZE_MB", "32")  # если раннер поддерживает
    extra_env.setdefault("ADAOS_SANDBOX_TMP_SIZE_MB", "32")
    extra_env.setdefault("PYTHONUNBUFFERED", "1")

    from adaos.services.sandbox.runner import ProcSandbox

    sb = _mk_sandbox(cwd, profile=profile)
    # пытаемся импортировать ExecLimits, но делаем это опционально
    ExecLimits = None
    try:
        from adaos.services.sandbox.runner import ExecLimits as _ExecLimits

        ExecLimits = _ExecLimits
    except Exception:
        pass

    run_sig = inspect.signature(sb.run)
    params = run_sig.parameters

    kwargs = {}
    # базовые
    if "cwd" in params:
        kwargs["cwd"] = str(cwd)
    if "limits" in params and ExecLimits is not None:
        kwargs["limits"] = ExecLimits(wall_time_sec=600)

    # окружение
    if "env" in params:
        if "inherit_env" in params:
            # sandbox сам унаследует env, мы дадим только дельту
            kwargs["env"] = extra_env or {}
            kwargs["inherit_env"] = True
        else:
            # нет inherit_env — склеиваем сами c системным окружением
            merged = dict(os.environ)
            if extra_env:
                merged.update({k: v for k, v in extra_env.items() if isinstance(k, str) and isinstance(v, str)})
            kwargs["env"] = merged

    # текстовый режим вывода
    if "text" in params:
        kwargs["text"] = True

    # запускаем
    res = sb.run(cmd=cmd, **kwargs)

    # нормализуем результат
    if isinstance(res, tuple) and len(res) >= 3:
        code, out, err = res[0], res[1], res[2]
    else:
        code = getattr(res, "exit", getattr(res, "returncode", 0))
        out = getattr(res, "stdout", "")
        err = getattr(res, "stderr", "")
    return code, (out or ""), (err or "")


import shutil, re


def _pick_python(spec: Optional[str]) -> tuple[str, list[str]]:
    """
    Возвращает ("python-exe", launcher_prefix_args).
    Если spec — путь к exe -> (path, []).
    Если spec — '3.11-64' -> ("py", ["-3.11-64"]).
    Если spec пуст -> авто-подбор через py -0p (Windows) или просто "python".
    """
    # явный путь
    if spec and Path(spec).exists():
        return str(Path(spec)), []
    # явный spec для py
    if spec and re.match(r"^\d+(\.\d+)?(-\d+)?$", spec):
        return "py", [f"-{spec}"]

    # авто-подбор
    if os.name == "nt":
        try:
            out = subprocess.check_output(["py", "-0p"], text=True, stderr=subprocess.STDOUT)
            # формат строк вида:  -3.12-64 * C:\...\python.exe
            min_major, min_minor = map(int, os.getenv("ADAOS_MIN_PY", "3.11").split("."))
            candidates = []
            for line in out.splitlines():
                m = re.search(r"-?(\d+)\.(\d+)(?:-\d+)?\s+\*\s+(.+python\.exe)", line, re.IGNORECASE)
                if m:
                    major, minor = int(m.group(1)), int(m.group(2))
                    path = m.group(3).strip()
                    if (major, minor) >= (min_major, min_minor):
                        candidates.append(((major, minor), path))
            if candidates:
                # берём наивысшую
                candidates.sort(reverse=True)
                return candidates[0][1], []
        except Exception:
            pass
        # fallback
        return shutil.which("python") or "python", []
    else:
        # *nix: просто python из PATH
        return shutil.which("python") or "python", []


@app.command("run")
def run_tests(
    only_sdk: bool = typer.Option(False, help="Run only SDK/API tests (./tests)."),
    only_skills: bool = typer.Option(False, help="Run only skills tests."),
    only_scenarios: bool = typer.Option(False, help="Run only scenario tests."),
    marker: Optional[str] = typer.Option(None, "--m", help="Pytest -m expression"),
    use_real_base: bool = typer.Option(False, help="Do NOT isolate ADAOS_BASE_DIR."),
    no_install: bool = typer.Option(False, help="Do not auto-install skills before running tests."),
    no_clone: bool = typer.Option(False, help="Do not clone/init skills monorepo (expect it to exist)."),
    bootstrap: bool = typer.Option(True, help="Bootstrap dev dependencies in sandbox venv once."),
    sandbox: bool = typer.Option(False, "--sandbox/--no-sandbox", help="Run tests inside ProcSandbox"),
    python_spec: Optional[str] = typer.Option(None, "--python", help="Python to run tests with. Path to python.exe OR py-launcher spec like '3.11-64'. Overrides --use-current."),
    use_current: bool = typer.Option(True, "--use-current/--no-use-current", help="Use the same interpreter that's running this CLI (ideal for your current venv)."),
    extra: Optional[List[str]] = typer.Argument(None),
):
    if only_scenarios and (only_sdk or only_skills):
        typer.secho("--only-scenarios cannot be combined with --only-sdk or --only-skills", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    # 0) изоляция BASE
    if not use_real_base:
        tmpdir = _ensure_tmp_base_dir()
        typer.secho(f"[AdaOS] Using isolated ADAOS_BASE_DIR at: {tmpdir}", fg=typer.colors.BLUE)
    if not sandbox:
        os.environ["ADAOS_SANDBOX_DISABLED"] = "1"
    env_spec = os.getenv("ADAOS_TEST_PY")
    py_exec, py_prefix = None, []
    if use_current:
        # всегда этот интерпретатор (текущий venv)
        py_exec, py_prefix = sys.executable, []
    elif python_spec or env_spec:
        # использовать указанный пользователем
        py_exec, py_prefix = _pick_python(python_spec or env_spec)
    else:
        # авто-подбор (как делали ранее)
        py_exec, py_prefix = _pick_python(None)
    # ctx уже читает настройки с учётом только что установленного ADAOS_BASE_DIR
    ctx = get_ctx()
    base_dir = Path(os.environ["ADAOS_BASE_DIR"])
    repo_root = Path(ctx.paths.repo_root()).resolve()
    pytest_paths: List[str] = []
    skill_test_paths: set[str] = set()
    # 1) Подготовка dev venv (однократно), затем используем его python
    if bootstrap and sandbox:
        try:
            venv_python = str(
                ensure_dev_venv(
                    base_dir=base_dir,
                    repo_root=repo_root,
                    run=lambda c, cwd, env=None: _sandbox_run(c, cwd=cwd, extra_env=env, use_sandbox=sandbox),
                    env={"PYTHONUNBUFFERED": "1"},
                )
            )
            typer.secho(f"[AdaOS] Dev venv ready: {venv_python}", fg=typer.colors.BLUE)
        except Exception as e:
            typer.secho(f"[AdaOS] Bootstrap failed: {e}", fg=typer.colors.RED)
            raise typer.Exit(code=2)
    else:
        venv_python = None  # важное изменение: используем выбранный py_exec, а не "python" из PATH
        # probe выбранного интерпретатора:
        probe = [py_exec, *py_prefix, "-c", "import pytest; print('ok')"]
        code, out, err = _sandbox_run(probe, cwd=base_dir, use_sandbox=sandbox)
        if code != 0:
            typer.secho(f"pytest is not installed for the selected interpreter: {py_exec} {' '.join(py_prefix)}", fg=typer.colors.RED)
            typer.secho("Tip: use --bootstrap or install dev deps: py -m pip install -e .[dev]", fg=typer.colors.YELLOW)
            raise typer.Exit(code=2)

    # 2) Подбор путей
    scenario_suite = repo_root / "tests" / "test_scenarios.py"

    if only_scenarios:
        if scenario_suite.exists():
            pytest_paths.append(str(scenario_suite))
        else:
            typer.secho("No scenario tests found in tests/test_scenarios.py", fg=typer.colors.YELLOW)
            raise typer.Exit(code=1)

    if not only_skills and not only_scenarios:
        sdk_tests = repo_root / "tests"
        if sdk_tests.exists():
            pytest_paths.append(str(sdk_tests))
        elif only_sdk:
            typer.secho("No SDK tests found in ./tests", fg=typer.colors.YELLOW)
            raise typer.Exit(code=0)

    if not only_sdk and not only_scenarios:
        _prepare_skills_repo(no_clone=no_clone)
        if not no_install:
            try:
                installed = _install_all_skills()
                if installed:
                    typer.secho(f"[AdaOS] Installed skills: {', '.join(installed)}", fg=typer.colors.BLUE)
            except Exception as e:
                typer.secho(f"[AdaOS] Auto-install skipped/failed: {e}", fg=typer.colors.YELLOW)

        skills_root = ctx.paths.skills_dir()
        collected = _collect_test_dirs(skills_root)
        pytest_paths.extend(collected)
        skill_test_paths.update(str(Path(path).resolve()) for path in collected)

        # при разработке — добавим тесты из исходников, если есть
        src_skills = repo_root / "src" / "adaos" / "skills"
        collected = _collect_test_dirs(src_skills)
        pytest_paths.extend(collected)
        skill_test_paths.update(str(Path(path).resolve()) for path in collected)

    # Prefer runtime/tests if both are present for a skill
    pytest_paths = _prune_duplicate_skill_tests(pytest_paths)

    if not pytest_paths:
        if only_sdk:
            typer.secho("No SDK tests found. Create ./tests with test_*.py", fg=typer.colors.YELLOW)
            raise typer.Exit(code=0)
        elif only_skills:
            typer.secho("No skill tests found. Ensure skills are installed and contain tests/ with test_*.py", fg=typer.colors.YELLOW)
            raise typer.Exit(code=0)
        else:
            typer.secho("No test paths found. Tip: add SDK tests in ./tests, or ensure skills with tests are installed.", fg=typer.colors.YELLOW)
            raise typer.Exit(code=0)

    # 3) Группировка по диску (Windows rootdir guard)
    from collections import defaultdict

    grouped: dict[str, List[str]] = defaultdict(list)
    for p in pytest_paths:
        drive = _drive_key(p)
        resolved = str(Path(p).resolve())
        if resolved in skill_test_paths:
            group_key = f"{drive} skill={Path(p).parent.name} path={resolved}"
        else:
            group_key = f"{drive} sdk"
        grouped[group_key].append(p)

    # 4) Формирование addopts (на всю группу единые)
    addopts_parts: List[str] = []
    if marker:
        addopts_parts += ["-m", marker]
    if extra:
        addopts_parts += extra
    junit_value = _junit_target(addopts_parts)
    junit_target = None
    junit_option_target = None
    junit_shards: list[Path] = []
    if junit_value and len(grouped) > 1:
        candidate = Path(junit_value)
        junit_option_target = candidate
        junit_target = candidate if candidate.is_absolute() else repo_root / candidate

    # 5) Прогон по группам (каждую — через venv_python)
    overall_code = 0
    for group_index, (group_label, paths) in enumerate(grouped.items(), start=1):
        interp = venv_python or py_exec
        prefix = [] if venv_python else py_prefix
        group_addopts = addopts_parts
        if junit_target is not None and junit_option_target is not None:
            suffix = junit_option_target.suffix or ".xml"
            option_shard = junit_option_target.with_name(
                f"{junit_option_target.stem}-{group_index:03d}{suffix}"
            )
            shard = option_shard if option_shard.is_absolute() else repo_root / option_shard
            junit_shards.append(shard)
            group_addopts = _replace_junit_target(addopts_parts, option_shard)
        addopts_str = subprocess.list2cmdline(group_addopts).strip()

        code, out, err = _run_one_group(
            ctx=ctx,
            base_dir=base_dir,
            venv_python=interp,  # всегда непустой путь/исполняемый
            paths=paths,
            addopts=addopts_str,
            py_exec=interp,  # дублируем для совместимости сигнатуры
            py_prefix=prefix,  # [] если venv_python задан, иначе py-prefix для py-launcher
            use_sandbox=sandbox,
        )
        # трактуем пустую группу как успех
        if code == 5 and "no tests ran" in (out.lower() + err.lower()):
            code = 0

        color = typer.colors.CYAN if code == 0 else typer.colors.RED
        typer.secho(f"[pytest {group_label}] exit={code} sandbox=yes\n--- stdout ---\n{out}\n--- stderr ---\n{err}", fg=color)
        overall_code = code if overall_code == 0 else overall_code

    if junit_target is not None:
        merged_count = _merge_junit_reports(junit_shards, junit_target)
        expected_count = len(junit_shards)
        if merged_count != expected_count:
            typer.secho(
                f"[AdaOS] JUnit merge incomplete: expected={expected_count} merged={merged_count}",
                fg=typer.colors.RED,
            )
            overall_code = overall_code or 2
        else:
            typer.secho(
                f"[AdaOS] Merged {merged_count} JUnit suites into {junit_target}",
                fg=typer.colors.BLUE,
            )

    raise typer.Exit(code=overall_code)
