from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

from adaos.services.skill_factory import SkillFactoryService


RUNNER_VERSION = "adaos-local-codex-worker/0.1.0"
PACKET_SCHEMA = "adaos.skill_factory.codex_packet.v1"
LOCAL_SESSION_SCHEMA = "adaos.skill_factory.local_run.v1"
_log = logging.getLogger("adaos.skill_factory.local_worker")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_token(value: Any, *, fallback: str = "task") -> str:
    token = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value or "").strip())
    return token.strip("._") or fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return dict(raw) if isinstance(raw, Mapping) else {}


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    input_text: str | None = None,
    timeout: float = 120.0,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(item) for item in command],
        cwd=str(cwd),
        input=input_text,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        env=dict(env) if env is not None else None,
    )


def _git(command: Sequence[str], *, cwd: Path, timeout: float = 120.0) -> str:
    result = _run(["git", *command], cwd=cwd, timeout=timeout)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"git {' '.join(command)} failed: {detail}")
    return result.stdout.strip()


@dataclass(slots=True)
class CodexRunResult:
    returncode: int
    events: str = ""
    stderr: str = ""
    final_message: str = ""
    command: tuple[str, ...] = ()


class SubprocessCodexExecutor:
    """Run the installed Codex CLI without exposing AdaOS credentials in the prompt."""

    def __init__(
        self,
        *,
        executable: str = "codex",
        model: str | None = None,
        timeout_seconds: int = 4 * 60 * 60,
        sandbox_mode: str | None = None,
    ) -> None:
        self.executable = executable
        self.model = str(model or "").strip() or None
        self.timeout_seconds = max(60, int(timeout_seconds))
        configured_sandbox = str(sandbox_mode or os.getenv("ADAOS_LOCAL_CODEX_SANDBOX") or "").strip()
        # Native Codex workspace sandboxing is not currently writable in our
        # Windows host profile.  Local-process is an explicitly trusted debug
        # backend with a bounded environment and disposable task checkout;
        # Docker workers should override this back to workspace-write.
        self.sandbox_mode = configured_sandbox or ("danger-full-access" if os.name == "nt" else "workspace-write")

    def __call__(self, *, workspace: Path, prompt: str, output_dir: Path) -> CodexRunResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        final_path = output_dir / "last_message.md"
        live_events_path = output_dir / "codex-live.jsonl"
        live_stderr_path = output_dir / "codex-live.stderr.log"
        command = [
            self.executable,
            "exec",
            "--json",
            "--ephemeral",
            "--ignore-user-config",
            "--sandbox",
            self.sandbox_mode,
            "-c",
            'approval_policy="never"',
            "-C",
            str(workspace),
            "-o",
            str(final_path),
        ]
        if self.model:
            command.extend(["--model", self.model])
        command.append("-")
        with live_events_path.open("w", encoding="utf-8", newline="\n") as events_file, live_stderr_path.open(
            "w", encoding="utf-8", newline="\n"
        ) as stderr_file:
            process = subprocess.Popen(
                command,
                cwd=str(workspace),
                stdin=subprocess.PIPE,
                stdout=events_file,
                stderr=stderr_file,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self._bounded_environment(),
            )
            try:
                process.communicate(input=prompt, timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise
        events = live_events_path.read_text(encoding="utf-8", errors="replace")
        stderr = live_stderr_path.read_text(encoding="utf-8", errors="replace")
        final_message = final_path.read_text(encoding="utf-8", errors="replace") if final_path.exists() else ""
        return CodexRunResult(
            returncode=int(process.returncode or 0),
            events=events,
            stderr=stderr,
            final_message=final_message,
            command=tuple(command),
        )

    @staticmethod
    def _bounded_environment() -> dict[str, str]:
        # Codex authentication remains in its local home, while API keys and
        # arbitrary AdaOS/runtime secrets are deliberately not inherited.
        allowed = {
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "TEMP",
            "TMP",
            "HOME",
            "USERPROFILE",
            "LOCALAPPDATA",
            "APPDATA",
            "CODEX_HOME",
            "LANG",
            "LC_ALL",
        }
        return {key: value for key, value in os.environ.items() if key.upper() in allowed and value}


class LocalSkillFactoryWorker:
    """One-task local Skill Factory worker used by Prompt IDE automation."""

    def __init__(
        self,
        *,
        state_dir: Path,
        repo_root: Path,
        dev_skills_root: Path,
        dev_scenarios_root: Path,
        runs_root: Path | None = None,
        node_id: str = "devnode.local-codex",
        executor: Callable[..., CodexRunResult] | None = None,
        progress_callback: Callable[[str, str, str], None] | None = None,
        max_repair_attempts: int = 1,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.repo_root = Path(repo_root)
        self.dev_skills_root = Path(dev_skills_root)
        self.dev_scenarios_root = Path(dev_scenarios_root)
        self.runs_root = Path(runs_root or (self.state_dir / "skill_factory" / "local_runs"))
        self.node_id = node_id
        self.executor = executor or SubprocessCodexExecutor()
        self.progress_callback = progress_callback
        self.max_repair_attempts = max(0, int(max_repair_attempts))
        self.factory = SkillFactoryService(state_dir=self.state_dir)

    def ensure_registered(self) -> dict[str, Any]:
        return self.factory.register_dev_node(
            {
                "node_id": self.node_id,
                "node_type": "local_dev_node_simulator",
                "status": "registered_waiting",
                "trust_level": "trusted_local_debug",
                "capabilities": ["codex", "git", "local_tests", "webui", "skill_scaffold"],
                "max_parallel_tasks": 1,
                "metadata": {
                    "runner_version": RUNNER_VERSION,
                    "python_version": sys.version.split()[0],
                    "platform": sys.platform,
                },
            }
        )

    def run_once(self) -> dict[str, Any]:
        self.ensure_registered()
        polled = self.factory.poll_assignment(self.node_id)
        if not polled.get("assigned"):
            return polled
        assignment = dict(polled["assignment"])
        return self.run_assignment(assignment)

    def run_assignment(self, assignment: Mapping[str, Any]) -> dict[str, Any]:
        task_id = str(assignment.get("task_id") or "").strip()
        if not task_id:
            raise ValueError("assignment.task_id is required")
        run_root = self.runs_root / _safe_token(task_id)
        input_dir = run_root / "input"
        workspace = run_root / "workspace"
        output_dir = run_root / "output"
        runtime_dir = run_root / "runtime"
        for path in (input_dir, output_dir, runtime_dir):
            path.mkdir(parents=True, exist_ok=True)

        try:
            self._progress(task_id, "workspace_preparing", "Preparing isolated local workspace")
            if workspace.exists():
                shutil.rmtree(workspace)
            workspace.mkdir(parents=True)
            self._materialize_sources(assignment, workspace)
            _write_json(input_dir / "assignment.json", dict(assignment))
            packet = self._build_packet(assignment, workspace, input_dir)
            prompt = (input_dir / "task.md").read_text(encoding="utf-8")
            packet_hash = "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()

            self._init_git_workspace(workspace, str((assignment.get("forge") or {}).get("branch") or f"realize/{task_id}"))
            self._progress(task_id, "in_progress", "Codex is implementing the requested skill changes")
            codex_result = self.executor(workspace=workspace, prompt=prompt, output_dir=output_dir)
            self._record_codex_attempt(runtime_dir, codex_result, attempt=0)
            if codex_result.returncode:
                raise RuntimeError(f"Codex exited with code {codex_result.returncode}: {codex_result.stderr[-1000:]}")

            test_report: dict[str, Any] = {}
            for repair_attempt in range(self.max_repair_attempts + 1):
                self._progress(task_id, "tests_running", "Validating generated manifests, Python and Web UI")
                self._cleanup_generated_files(workspace)
                changed_paths = self._changed_paths(workspace)
                self._validate_changed_paths(assignment, changed_paths)
                test_report = self._validate_workspace(assignment, workspace)
                if test_report["ok"]:
                    break
                if repair_attempt >= self.max_repair_attempts:
                    break
                self._progress(task_id, "in_progress", "Codex is repairing deterministic validation failures")
                repair_prompt = (
                    prompt
                    + "\n\n# Deterministic validation repair\n\n"
                    + "The previous implementation did not pass the worker checks below. Continue in the existing workspace, "
                    + "fix every reported issue, rerun relevant checks, and leave the workspace in a valid state.\n\n"
                    + "\n".join(f"- {item}" for item in test_report["errors"][:40])
                )
                codex_result = self.executor(workspace=workspace, prompt=repair_prompt, output_dir=output_dir)
                self._record_codex_attempt(runtime_dir, codex_result, attempt=repair_attempt + 1)
                if codex_result.returncode:
                    raise RuntimeError(
                        f"Codex repair exited with code {codex_result.returncode}: {codex_result.stderr[-1000:]}"
                    )
            self._cleanup_generated_files(workspace)
            _write_json(output_dir / "test_report.json", test_report)
            if not test_report["ok"]:
                raise RuntimeError("Generated project validation failed: " + "; ".join(test_report["errors"]))

            evidence_paths = dict((assignment.get("evidence") or {}).get("expected_paths") or {})
            evidence_root = workspace / str(evidence_paths.get("result") or f".adaos/tasks/{_safe_token(task_id)}/result.json").replace("result.json", "")
            evidence_root.mkdir(parents=True, exist_ok=True)
            (evidence_root / "changed_files.txt").write_text("\n".join(changed_paths) + "\n", encoding="utf-8")
            shutil.copy2(output_dir / "test_report.json", evidence_root / "test_report.json")
            provenance = {
                "schema": "adaos.skill_factory.task_provenance.v1",
                "runner_version": RUNNER_VERSION,
                "image_digest": "local-process",
                "instruction_packet_hash": packet_hash,
                "dependency_changes": self._dependency_changes(workspace),
                "source_refs": dict(assignment.get("source_refs") or {}),
                "tool_versions": {"python": sys.version.split()[0]},
                "created_at": _now_iso(),
            }
            _write_json(evidence_root / "provenance.json", provenance)
            result_manifest = {
                "schema": "adaos.skill_factory.dev_result.v1",
                "task_id": task_id,
                "node_id": self.node_id,
                "status": "completed",
                "summary": codex_result.final_message.strip(),
                "tests": test_report,
                "packet": packet,
            }
            _write_json(evidence_root / "result.json", result_manifest)
            all_changed_paths = self._changed_paths(workspace)
            (evidence_root / "changed_files.txt").write_text("\n".join(all_changed_paths) + "\n", encoding="utf-8")

            self._progress(task_id, "commit_ready", "Committing validated local result")
            _git(["add", "-A"], cwd=workspace)
            _git(["commit", "-m", f"realize: {task_id}"], cwd=workspace)
            commit_hash = _git(["rev-parse", "HEAD"], cwd=workspace)
            final_changed_paths = self._changed_from_baseline(workspace)
            self._sync_artifacts(assignment, workspace)
            result = {
                "task_id": task_id,
                "node_id": self.node_id,
                "status": "completed",
                "commit_hash": commit_hash,
                "branch": str((assignment.get("forge") or {}).get("branch") or ""),
                "changed_paths": final_changed_paths,
                "tests": {"status": "passed", "report": str(output_dir / "test_report.json")},
                "provenance": provenance,
                "summary": codex_result.final_message.strip(),
                "local_run_dir": str(run_root),
            }
            _write_json(output_dir / "result.json", result)
            completed = self.factory.complete_task(result)
            _write_json(runtime_dir / "state.json", {"schema": LOCAL_SESSION_SCHEMA, "status": "completed", "completed_at": _now_iso()})
            return {"ok": True, "assignment": dict(assignment), "result": result, "completed": completed}
        except Exception as exc:
            failure = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "failed_at": _now_iso()}
            _write_json(runtime_dir / "state.json", {"schema": LOCAL_SESSION_SCHEMA, **failure})
            try:
                self.factory.fail_task(
                    {
                        "task_id": task_id,
                        "node_id": self.node_id,
                        "message": failure["error"],
                        "retryable": True,
                    }
                )
            except Exception:
                pass
            return {"ok": False, "assignment": dict(assignment), **failure, "run_dir": str(run_root)}

    @staticmethod
    def _record_codex_attempt(runtime_dir: Path, result: CodexRunResult, *, attempt: int) -> None:
        suffix = "" if attempt == 0 else f"-repair-{attempt}"
        (runtime_dir / f"codex-events{suffix}.jsonl").write_text(result.events, encoding="utf-8")
        (runtime_dir / f"codex-stderr{suffix}.log").write_text(result.stderr, encoding="utf-8")
        if result.final_message:
            (runtime_dir / f"codex-final{suffix}.md").write_text(result.final_message, encoding="utf-8")

    def _progress(self, task_id: str, status: str, message: str) -> None:
        self.factory.report_progress(
            task_id,
            {"node_id": self.node_id, "status": status, "stage": status, "message": message},
        )
        if self.progress_callback is not None:
            try:
                self.progress_callback(task_id, status, message)
            except Exception:
                _log.warning("local worker progress callback failed task=%s status=%s", task_id, status, exc_info=True)

    def _materialize_sources(self, assignment: Mapping[str, Any], workspace: Path) -> None:
        target = dict(assignment.get("target") or {})
        target_type = str(target.get("type") or "skill").strip().lower()
        target_id = _safe_token(target.get("id"), fallback="generated_skill")
        if target_type == "scenario":
            source = self.dev_scenarios_root / target_id
            destination = workspace / "scenarios" / target_id
            if not source.exists():
                raise FileNotFoundError(f"DEV scenario not found: {source}")
            shutil.copytree(source, destination)
            skill_id = self._companion_skill_id(assignment)
            skill_source = self.dev_skills_root / skill_id
            skill_destination = workspace / "skills" / skill_id
            if not skill_source.exists():
                raise FileNotFoundError(
                    f"DEV companion skill not found: {skill_source}; create it through the core developer lifecycle first"
                )
            shutil.copytree(skill_source, skill_destination)
        elif target_type == "skill":
            source = self.dev_skills_root / target_id
            destination = workspace / "skills" / target_id
            if not source.exists():
                raise FileNotFoundError(
                    f"DEV skill not found: {source}; create it through the core developer lifecycle first"
                )
            shutil.copytree(source, destination)
        else:
            raise ValueError(f"local worker supports skill or scenario targets, got {target_type!r}")

    def _companion_skill_id(self, assignment: Mapping[str, Any]) -> str:
        request = dict(assignment.get("realize_request") or {})
        artifacts = dict(request.get("artifacts") or {})
        target = dict(assignment.get("target") or {})
        return _safe_token(artifacts.get("companion_skill_id") or f"{target.get('id')}_skill", fallback="generated_skill")

    def _build_packet(self, assignment: Mapping[str, Any], workspace: Path, input_dir: Path) -> dict[str, Any]:
        request = dict(assignment.get("realize_request") or {})
        target = dict(assignment.get("target") or {})
        target_type = str(target.get("type") or "skill")
        target_id = _safe_token(target.get("id"), fallback="generated_skill")
        companion = self._companion_skill_id(assignment) if target_type == "scenario" else target_id
        source = dict(request.get("source") or {})
        artifacts = dict(request.get("artifacts") or {})
        brief = str(artifacts.get("implementation_brief") or source.get("text") or "").strip()
        iteration = str(artifacts.get("iteration_instruction") or "").strip()
        allowed = [str(item) for item in (assignment.get("forge") or {}).get("sparse_paths") or []]
        packet = {
            "schema": PACKET_SCHEMA,
            "task_id": assignment.get("task_id"),
            "target": target,
            "companion_skill_id": companion,
            "allowed_paths": allowed,
            "acceptance": dict(assignment.get("acceptance") or {}),
            "constraints": dict(assignment.get("constraints") or {}),
            "brief": brief,
            "iteration_instruction": iteration,
        }
        _write_json(input_dir / "packet.json", packet)
        (input_dir / "allowed_files.txt").write_text("\n".join(allowed) + "\n", encoding="utf-8")
        prompt = f"""# AdaOS local realization task

You are implementing a real AdaOS project from an approved interface prototype. Work autonomously in the current repository and finish the implementation; do not merely describe code.

## Target

- Type: {target_type}
- ID: {target_id}
- Companion skill: {companion}

## Approved implementation brief

{brief or 'Use the existing prototype and project files as the complete source of requirements.'}

## Current chat iteration

{iteration or 'This is the initial realization. Implement the complete first working version.'}

## Required result

1. Inspect all existing files under the target paths before editing.
2. Implement or correct the AdaOS skill, including `skill.yaml`, handler tools, input/output schemas and useful tests or fixtures.
3. For a scenario prototype, connect `scenarios/{target_id}` to `skills/{companion}` through `depends`, declarative actions and data routes as appropriate.
4. Create or correct `webui.json` when the project has a UI. Preserve useful prototype behavior and make actions use real skill tools instead of mocks where possible. Scenario runtime UI must remain renderable: either keep `ui.application` in `scenario.json`, or reference the adjacent complete descriptor as `ui.manifest: webui.json`.
5. Keep the result compatible with the repository's existing AdaOS schemas and conventions. Do not add dependencies unless essential.
6. Run relevant bounded checks. Fix failures caused by your changes.
7. Do not edit anything outside these task paths: {', '.join(allowed)}.
8. Do not access secrets, production data, other AdaOS runtime state, or external APIs.

Conclude with a concise summary of implemented behavior and checks. The worker, not you, creates result/provenance files and the git commit.
"""
        (input_dir / "task.md").write_text(prompt, encoding="utf-8")
        return packet

    def _init_git_workspace(self, workspace: Path, branch: str) -> None:
        _git(["init"], cwd=workspace)
        _git(["config", "user.name", "AdaOS Local Skill Factory"], cwd=workspace)
        _git(["config", "user.email", "skill-factory@localhost"], cwd=workspace)
        _git(["add", "-A"], cwd=workspace)
        _git(["commit", "-m", "chore: materialize realization workspace"], cwd=workspace)
        _git(["checkout", "-b", branch], cwd=workspace)

    def _changed_paths(self, workspace: Path) -> list[str]:
        output = _git(["status", "--porcelain", "--untracked-files=all"], cwd=workspace)
        paths: list[str] = []
        for line in output.splitlines():
            # ``_git`` trims the full output, so the leading index-space of
            # the first porcelain row may be gone.  Split at the first status
            # separator instead of relying on a fixed column offset.
            parts = line.strip().split(maxsplit=1)
            path = (parts[1] if len(parts) == 2 else "").strip().replace("\\", "/")
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            if path and path not in paths:
                paths.append(path)
        return paths

    def _changed_from_baseline(self, workspace: Path) -> list[str]:
        output = _git(["diff", "--name-only", "HEAD~1", "HEAD"], cwd=workspace)
        return [line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()]

    def _validate_changed_paths(self, assignment: Mapping[str, Any], changed_paths: list[str]) -> None:
        allowed = [str(item).replace("\\", "/").strip("/") + "/" for item in (assignment.get("forge") or {}).get("sparse_paths") or []]
        invalid = [path for path in changed_paths if not any(path == item.rstrip("/") or path.startswith(item) for item in allowed)]
        if invalid:
            raise ValueError(f"Codex changed paths outside the task scope: {invalid}")

    def _validate_workspace(self, assignment: Mapping[str, Any], workspace: Path) -> dict[str, Any]:
        errors: list[str] = []
        checks: list[dict[str, Any]] = []
        for path in sorted(workspace.rglob("*.json")):
            if ".git" in path.parts:
                continue
            try:
                json.loads(path.read_text(encoding="utf-8"))
                checks.append({"kind": "json", "path": path.relative_to(workspace).as_posix(), "ok": True})
            except Exception as exc:
                errors.append(f"{path.relative_to(workspace)}: {type(exc).__name__}: {exc}")
        for path in sorted([*workspace.rglob("*.yaml"), *workspace.rglob("*.yml")]):
            if ".git" in path.parts:
                continue
            try:
                yaml.safe_load(path.read_text(encoding="utf-8"))
                checks.append({"kind": "yaml", "path": path.relative_to(workspace).as_posix(), "ok": True})
            except Exception as exc:
                errors.append(f"{path.relative_to(workspace)}: {type(exc).__name__}: {exc}")
        python_files = [path for path in workspace.rglob("*.py") if ".git" not in path.parts]
        for path in python_files:
            try:
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
                checks.append({"kind": "python", "path": path.relative_to(workspace).as_posix(), "ok": True})
            except Exception as exc:
                errors.append(f"{path.relative_to(workspace)}: {type(exc).__name__}: {exc}")

        webui_schema_path = self.repo_root / "src" / "adaos" / "abi" / "webui.v1.schema.json"
        if webui_schema_path.exists():
            try:
                from jsonschema import Draft202012Validator

                validator = Draft202012Validator(_read_json(webui_schema_path))
                for path in sorted(workspace.rglob("webui.json")):
                    payload = _read_json(path)
                    validation_errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.path))
                    if validation_errors:
                        errors.extend(
                            f"{path.relative_to(workspace)}: webui schema: {item.message}" for item in validation_errors[:20]
                        )
                    else:
                        checks.append({"kind": "webui.v1", "path": path.relative_to(workspace).as_posix(), "ok": True})
            except Exception as exc:
                errors.append(f"webui schema validation setup failed: {type(exc).__name__}: {exc}")

        scenario_schema_path = self.repo_root / "src" / "adaos" / "abi" / "scenario.schema.json"
        if scenario_schema_path.exists():
            try:
                from jsonschema import Draft202012Validator

                validator = Draft202012Validator(_read_json(scenario_schema_path))
                for path in sorted(workspace.glob("scenarios/*/scenario.json")):
                    payload = _read_json(path)
                    validation_errors = sorted(validator.iter_errors(payload), key=lambda item: list(item.path))
                    if validation_errors:
                        errors.extend(
                            f"{path.relative_to(workspace)}: scenario schema: {item.message}"
                            for item in validation_errors[:20]
                        )
                    else:
                        checks.append({"kind": "scenario.v1", "path": path.relative_to(workspace).as_posix(), "ok": True})
                    ui = payload.get("ui") if isinstance(payload.get("ui"), Mapping) else {}
                    application = ui.get("application") if isinstance(ui.get("application"), Mapping) else {}
                    manifest_name = str(ui.get("manifest") or "").strip()
                    if application:
                        continue
                    adjacent_webui_path = path.parent / "webui.json"
                    try:
                        adjacent_webui = _read_json(adjacent_webui_path) if adjacent_webui_path.is_file() else {}
                    except Exception:
                        adjacent_webui = {}
                    adjacent_ui = adjacent_webui.get("ui") if isinstance(adjacent_webui.get("ui"), Mapping) else {}
                    adjacent_application = (
                        adjacent_ui.get("application") if isinstance(adjacent_ui.get("application"), Mapping) else {}
                    )
                    if not adjacent_application:
                        continue
                    manifest_path = path.parent / manifest_name if manifest_name else None
                    try:
                        manifest = _read_json(manifest_path) if manifest_path and manifest_path.is_file() else {}
                    except Exception:
                        manifest = {}
                    manifest_ui = manifest.get("ui") if isinstance(manifest.get("ui"), Mapping) else {}
                    if not isinstance(manifest_ui.get("application"), Mapping) or not manifest_ui.get("application"):
                        errors.append(
                            f"{path.relative_to(workspace)}: scenario UI is not renderable; "
                            "provide ui.application or ui.manifest pointing to a complete adjacent webui.json"
                        )
            except Exception as exc:
                errors.append(f"scenario schema validation setup failed: {type(exc).__name__}: {exc}")

        target = dict(assignment.get("target") or {})
        target_id = _safe_token(target.get("id"), fallback="generated_skill")
        skill_id = self._companion_skill_id(assignment) if target.get("type") == "scenario" else target_id
        required = [workspace / "skills" / skill_id / "skill.yaml", workspace / "skills" / skill_id / "handlers" / "main.py"]
        if target.get("type") == "scenario":
            required.append(workspace / "scenarios" / target_id / "scenario.json")
        for path in required:
            if not path.exists():
                errors.append(f"required file missing: {path.relative_to(workspace)}")
        self._run_generated_tests(workspace, checks, errors)
        return {"ok": not errors, "status": "passed" if not errors else "failed", "checks": checks, "errors": errors}

    def _run_generated_tests(self, workspace: Path, checks: list[dict[str, Any]], errors: list[str]) -> None:
        for tests_dir in sorted(path for path in workspace.glob("skills/*/tests") if path.is_dir()):
            test_files = list(tests_dir.glob("test_*.py"))
            if not test_files:
                continue
            environment = SubprocessCodexExecutor._bounded_environment()
            environment["PYTHONPATH"] = str(self.repo_root / "src")
            result = _run(
                [sys.executable, "-m", "pytest", "-q", str(tests_dir), "-p", "no:cacheprovider"],
                cwd=workspace,
                timeout=180.0,
                env=environment,
            )
            relative = tests_dir.relative_to(workspace).as_posix()
            checks.append(
                {
                    "kind": "pytest",
                    "path": relative,
                    "ok": result.returncode == 0,
                    "output": (result.stdout + result.stderr)[-4000:],
                }
            )
            if result.returncode:
                errors.append(f"{relative}: pytest failed: {(result.stdout + result.stderr)[-2000:]}")

    @staticmethod
    def _cleanup_generated_files(root: Path) -> None:
        for cache_dir in sorted(root.rglob("__pycache__"), reverse=True):
            if cache_dir.is_dir():
                shutil.rmtree(cache_dir)
        for cache_dir in sorted(root.rglob(".pytest_cache"), reverse=True):
            if cache_dir.is_dir():
                shutil.rmtree(cache_dir)
        for path in root.rglob("*.pyc"):
            if path.is_file():
                path.unlink()

    def _dependency_changes(self, workspace: Path) -> list[dict[str, Any]]:
        names = {"requirements.txt", "pyproject.toml", "uv.lock", "package.json", "package-lock.json"}
        return [{"path": path, "action": "changed"} for path in self._changed_paths(workspace) if Path(path).name in names]

    def _sync_artifacts(self, assignment: Mapping[str, Any], workspace: Path) -> None:
        target = dict(assignment.get("target") or {})
        target_id = _safe_token(target.get("id"), fallback="generated_skill")
        sources: list[tuple[Path, Path]] = []
        if target.get("type") == "scenario":
            sources.append((workspace / "scenarios" / target_id, self.dev_scenarios_root / target_id))
            skill_id = self._companion_skill_id(assignment)
            sources.append((workspace / "skills" / skill_id, self.dev_skills_root / skill_id))
        else:
            sources.append((workspace / "skills" / target_id, self.dev_skills_root / target_id))
        for source, destination in sources:
            if not source.exists():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                shutil.copytree(source, destination, dirs_exist_ok=True)
            else:
                shutil.copytree(source, destination)
            self._cleanup_generated_files(destination)


__all__ = ["CodexRunResult", "LocalSkillFactoryWorker", "SubprocessCodexExecutor"]
