"""Declarative end-to-end evaluation runner for the Builder product path."""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Protocol, Sequence

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from adaos.e2e.stand import redact_value


SUITE_SCHEMA = "adaos.builder.e2e_suite.v1"
CASE_SCHEMA = "adaos.builder.e2e_case.v1"
RUN_SCHEMA = "adaos.builder.e2e_run.v1"
CASE_RESULT_SCHEMA = "adaos.builder.e2e_case_result.v1"
REPORT_SCHEMA = "adaos.builder.e2e_report.v1"
BASELINE_SCHEMA = "adaos.builder.e2e_baseline.v1"
RUNNER_VERSION = "0.1.0"
_INLINE_STEP_OUTPUT_BYTES = 16_384
_RESULTS = {"passed", "failed", "inconclusive", "skipped"}
_LOWER_IS_BETTER = {
    "duration_ms_p50",
    "duration_ms_p90",
    "duration_ms_p95",
    "fresh_input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "model_calls",
    "failed",
    "inconclusive",
}


class _Yaml12SafeLoader(yaml.SafeLoader):
    pass


_Yaml12SafeLoader.yaml_implicit_resolvers = {
    key: [
        resolver
        for resolver in resolvers
        if resolver[0] != "tag:yaml.org,2002:bool"
    ]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Yaml12SafeLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$", re.IGNORECASE),
    list("tTfF"),
)


class BuilderE2EError(ValueError):
    """Raised when an evaluation definition or result is unsafe or invalid."""


class BuilderE2EUnavailable(RuntimeError):
    """Raised when evaluation infrastructure is unavailable, not when a case fails."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _safe_token(value: str, *, fallback: str) -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-.")
    return token[:120] or fallback


def _load_document(path: Path) -> dict[str, Any]:
    try:
        if path.suffix.lower() in {".yaml", ".yml"}:
            payload = yaml.load(
                path.read_text(encoding="utf-8"), Loader=_Yaml12SafeLoader
            )
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise BuilderE2EError(f"cannot read evaluation document {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise BuilderE2EError(f"evaluation document must be an object: {path}")
    return copy.deepcopy(dict(payload))


def _schema(name: str) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1] / "abi"
    path = root / f"{name.removeprefix('adaos.')}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def validate_builder_e2e_record(name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    candidate = copy.deepcopy(dict(value))
    errors = sorted(
        Draft202012Validator(_schema(name), format_checker=FormatChecker()).iter_errors(
            candidate
        ),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        first = errors[0]
        location = "$" + "".join(
            f"[{item}]" if isinstance(item, int) else f".{item}"
            for item in first.absolute_path
        )
        raise BuilderE2EError(
            f"{name} validation failed at {location}: {first.message}"
        )
    return candidate


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _compact_step_output(
    output: Mapping[str, Any],
    *,
    bundle_dir: Path,
    case_id: str,
    repetition: int,
    step_id: str,
) -> tuple[dict[str, Any], str | None]:
    redacted = redact_value(dict(output))
    raw = json.dumps(
        redacted, ensure_ascii=True, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    if len(raw) <= _INLINE_STEP_OUTPUT_BYTES:
        return dict(redacted), None

    relative = Path("evidence") / "steps" / (
        f"{_safe_token(case_id, fallback='case')}-attempt-{repetition:02d}-"
        f"{_safe_token(step_id, fallback='step')}.json.gz"
    )
    target = bundle_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    target.write_bytes(compressed)

    summary_keys = {
        "ok",
        "status",
        "error",
        "detail",
        "message",
        "draft_id",
        "scenario_id",
        "project_id",
        "project_ref",
        "project_status",
        "session_id",
        "artifact_root",
        "usage",
        "telemetry",
        "input_attribution",
    }
    summary: dict[str, Any] = {}
    for key in summary_keys:
        if key not in redacted:
            continue
        value = redacted[key]
        encoded = json.dumps(value, ensure_ascii=True, sort_keys=True).encode("utf-8")
        if len(encoded) <= 4_096:
            summary[key] = value
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    return (
        {
            "evidence_ref": relative.as_posix(),
            "content_digest": digest,
            "content_bytes": len(raw),
            "compressed_bytes": len(compressed),
            "summary": summary,
        },
        relative.as_posix(),
    )


def _run_command(args: Sequence[str], cwd: Path, *, timeout: float = 5.0) -> str | None:
    try:
        completed = subprocess.run(
            list(args),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return str(completed.stdout or "").strip() or None


def _repository_environment(repo_root: Path) -> dict[str, Any]:
    client_root = repo_root / "src" / "adaos" / "integrations" / "adaos-client"
    status = _run_command(["git", "status", "--porcelain"], repo_root)
    return {
        "runner_version": RUNNER_VERSION,
        "repository_commit": _run_command(["git", "rev-parse", "HEAD"], repo_root),
        "repository_dirty": bool(status),
        "client_commit": (
            _run_command(["git", "rev-parse", "HEAD"], client_root)
            if client_root.is_dir()
            else None
        ),
        "python": platform.python_version(),
        "node": _run_command(["node", "--version"], repo_root),
        "platform": platform.platform(),
    }


def _path_get(value: Any, path: str) -> tuple[bool, Any]:
    current = value
    for token in str(path or "").split("."):
        if not token:
            continue
        if isinstance(current, Mapping) and token in current:
            current = current[token]
            continue
        if isinstance(current, Sequence) and not isinstance(
            current, (str, bytes, bytearray)
        ):
            try:
                current = current[int(token)]
                continue
            except (ValueError, IndexError):
                pass
        return False, None
    return True, current


def _resolve_value(value: Any, context: Mapping[str, Any]) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _resolve_value(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_value(item, context) for item in value]
    if not isinstance(value, str):
        return value
    if value.startswith("$steps."):
        found, resolved = _path_get(
            context.get("outputs") or {}, value.removeprefix("$steps.")
        )
        if not found:
            raise BuilderE2EError(f"step input reference is unavailable: {value}")
        return copy.deepcopy(resolved)
    replacements = {
        "${run_id}": str(context["run_id"]),
        "${case_id}": str(context["case_id"]),
        "${repetition}": str(context["repetition"]),
        "${locale}": str(context["locale"]),
    }
    resolved = value
    for source, target in replacements.items():
        resolved = resolved.replace(source, target)
    return resolved


def _expectation_findings(
    output: Mapping[str, Any], expect: Mapping[str, Any]
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in expect.get("paths_present") or []:
        found, _ = _path_get(output, str(path))
        if not found:
            findings.append({"code": "path_missing", "path": str(path)})
    for path in expect.get("paths_absent") or []:
        found, _ = _path_get(output, str(path))
        if found:
            findings.append({"code": "path_present", "path": str(path)})
    for path, expected in dict(expect.get("values") or {}).items():
        found, actual = _path_get(output, str(path))
        if not found or actual != expected:
            findings.append(
                {
                    "code": "value_mismatch",
                    "path": str(path),
                    "expected": expected,
                    "actual": actual,
                }
            )
    for path, choices in dict(expect.get("values_in") or {}).items():
        found, actual = _path_get(output, str(path))
        if not found or actual not in choices:
            findings.append(
                {
                    "code": "value_not_admitted",
                    "path": str(path),
                    "expected": choices,
                    "actual": actual,
                }
            )
    for path, fragments in dict(expect.get("text_contains") or {}).items():
        found, actual = _path_get(output, str(path))
        text = str(actual or "") if found else ""
        for fragment in fragments:
            if str(fragment) not in text:
                findings.append(
                    {
                        "code": "text_missing",
                        "path": str(path),
                        "expected": str(fragment),
                    }
                )
    return findings


def _collect_usage(value: Any) -> dict[str, int]:
    totals = {
        "fresh_input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "model_calls": 0,
    }

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            keys = {str(key): child for key, child in item.items()}
            usage_like = any(
                key in keys
                for key in (
                    "input_tokens",
                    "prompt_tokens",
                    "output_tokens",
                    "completion_tokens",
                    "cached_tokens",
                    "cached_input_tokens",
                )
            )
            if usage_like:
                input_tokens = int(
                    keys.get("input_tokens") or keys.get("prompt_tokens") or 0
                )
                cached_tokens = int(
                    keys.get("cached_input_tokens") or keys.get("cached_tokens") or 0
                )
                details = keys.get("input_tokens_details")
                if isinstance(details, Mapping):
                    cached_tokens = int(details.get("cached_tokens") or cached_tokens)
                totals["cached_input_tokens"] += max(0, cached_tokens)
                totals["fresh_input_tokens"] += max(0, input_tokens - cached_tokens)
                totals["output_tokens"] += max(
                    0,
                    int(
                        keys.get("output_tokens") or keys.get("completion_tokens") or 0
                    ),
                )
                output_details = keys.get("output_tokens_details")
                if isinstance(output_details, Mapping):
                    totals["reasoning_tokens"] += max(
                        0, int(output_details.get("reasoning_tokens") or 0)
                    )
                totals["model_calls"] += 1
                return
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return totals


class BuilderE2EStepExecutor(Protocol):
    adapter_id: str

    def execute(
        self, step_type: str, inputs: Mapping[str, Any], context: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...

    def cleanup(self, context: Mapping[str, Any]) -> Mapping[str, Any]: ...


class CompatibilityBuilderExecutor:
    """Bootstrap adapter for the current DEV Builder chat tool."""

    adapter_id = "legacy_dev_chat.v1"

    def __init__(self, *, repo_root: Path, browser_mode: str = "auto") -> None:
        self.repo_root = repo_root
        self.browser_mode = browser_mode
        self._skill_manager: Any = None

    def _manager(self) -> Any:
        if self._skill_manager is None:
            from adaos.adapters.db import SqliteSkillRegistry
            from adaos.services.agent_context import get_ctx
            from adaos.services.skill.manager import SkillManager

            ctx = get_ctx()
            self._skill_manager = SkillManager(
                repo=ctx.skills_repo,
                registry=SqliteSkillRegistry(ctx.sql),
                git=ctx.git,
                paths=ctx.paths,
                bus=getattr(ctx, "bus", None),
                caps=ctx.caps,
                settings=ctx.settings,
            )
        return self._skill_manager

    def _observe_path(self, inputs: Mapping[str, Any]) -> Mapping[str, Any]:
        raw = str(inputs.get("path") or "").strip()
        if not raw:
            raise BuilderE2EError("observe.path requires input.path")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (self.repo_root / path).resolve()
        result: dict[str, Any] = {
            "ok": path.exists(),
            "path": str(path),
            "exists": path.exists(),
            "kind": "directory"
            if path.is_dir()
            else "file"
            if path.is_file()
            else "missing",
        }
        if path.is_file():
            raw_bytes = path.read_bytes()
            result.update(
                {
                    "size": len(raw_bytes),
                    "digest": "sha256:" + hashlib.sha256(raw_bytes).hexdigest(),
                }
            )
        elif path.is_dir():
            files = sorted(item for item in path.rglob("*") if item.is_file())
            result["file_count"] = len(files)
        return result

    def _chat(
        self, inputs: Mapping[str, Any], context: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        text = str(inputs.get("text") or "").strip()
        if not text:
            raise BuilderE2EError("builder.chat requires input.text")
        webspace_id = str(
            inputs.get("webspace_id") or f"e2e-{context['run_id']}"
        ).strip()
        conversation_id = str(
            inputs.get("conversation_id")
            or f"conversation:e2e:{context['run_id']}:{context['case_id']}:{context['repetition']}"
        )
        timeout = float(
            inputs.get("timeout_seconds") or context.get("timeout_seconds") or 300
        )
        payload = {
            "text": text,
            "webspace_id": webspace_id,
            "auto_apply": bool(inputs.get("auto_apply", True)),
            "conversation_context": {
                "schema": "adaos.context.packet.v1",
                "conversation_id": conversation_id,
                "thread_id": conversation_id,
                "topic_id": conversation_id,
                "locale": context["locale"],
                "messages": [],
                "segments": [],
                "memory": [],
                "diagnostics": {"fallbacks": [f"builder_e2e:{context['run_id']}"]},
            },
            "_meta": {
                "action_source": "builder_e2e",
                "request_origin_id": "builder_e2e",
                "message_id": f"m.e2e.{context['run_id']}.{context['case_id']}.{context['repetition']}",
                "conversation_id": conversation_id,
                "thread_id": conversation_id,
                "topic_id": conversation_id,
                "webspace_id": webspace_id,
                "source_webspace_id": webspace_id,
                "locale": context["locale"],
                "builder_e2e_run_id": context["run_id"],
                "builder_e2e_case_id": context["case_id"],
            },
        }
        return self._manager().run_dev_tool(
            "builder_skill", "chat", payload, timeout=timeout
        )

    def _session(
        self, inputs: Mapping[str, Any], context: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        session_id = str(inputs.get("session_id") or "").strip()
        if not session_id:
            raise BuilderE2EError("builder.session requires input.session_id")
        webspace_id = str(
            inputs.get("webspace_id") or f"e2e-{context['run_id']}"
        ).strip()
        return self._manager().run_dev_tool(
            "builder_skill",
            "get_session",
            {"session_id": session_id, "webspace_id": webspace_id},
            timeout=float(inputs.get("timeout_seconds") or 30),
        )

    def _scenario_validate(self, inputs: Mapping[str, Any]) -> Mapping[str, Any]:
        from adaos.apps.cli.commands.dev import _scenario_validation_roots
        from adaos.services.agent_context import get_ctx
        from adaos.services.scenario.validation import validate_scenario_path

        path_value = str(inputs.get("path") or "").strip()
        scenario_id = str(inputs.get("scenario_id") or "").strip()
        ctx = get_ctx()
        if path_value:
            scenario_path = Path(path_value).expanduser().resolve()
        elif scenario_id:
            scenario_path = Path(ctx.paths.dev_scenarios_dir()) / scenario_id
        else:
            raise BuilderE2EError(
                "scenario.validate requires input.path or input.scenario_id"
            )
        report = validate_scenario_path(
            scenario_path,
            dependency_roots=_scenario_validation_roots(ctx),
        )
        return {
            "ok": report.ok,
            "scenario_id": report.scenario_id,
            "errors": list(report.errors),
            "issues": [
                {
                    "level": issue.level,
                    "code": issue.code,
                    "message": issue.message,
                    "where": issue.where,
                }
                for issue in report.issues
            ],
        }

    def _browser_probe(
        self, inputs: Mapping[str, Any], context: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if self.browser_mode == "off":
            return {"ok": True, "skipped": True, "reason": "browser mode is off"}
        project_dir = self.repo_root / "e2e" / "stand" / "browser"
        package = project_dir / "package.json"
        if not package.is_file() or shutil.which("npm") is None:
            raise BuilderE2EUnavailable("Playwright runner is not installed")
        env = dict(os.environ)
        env.update(
            {
                "ADAOS_E2E_RUN_ID": str(context["run_id"]),
                "ADAOS_E2E_OUTPUT": str(context["bundle_dir"]),
                "ADAOS_E2E_CLIENT_URL": str(
                    inputs.get("client_url") or "http://127.0.0.1:8100/"
                ),
                "ADAOS_E2E_HUB_URL": str(
                    inputs.get("hub_url") or "http://127.0.0.1:8777"
                ),
                "ADAOS_E2E_SUBNET_ID": str(inputs.get("subnet_id") or ""),
                "ADAOS_E2E_WEBSPACE_ID": str(inputs.get("webspace_id") or "desktop"),
                "ADAOS_E2E_BROWSER_DEVICE_ID": str(
                    inputs.get("browser_device_id") or "builder-e2e"
                ),
            }
        )
        completed = subprocess.run(
            ["npm", "run", "smoke"],
            cwd=project_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=float(inputs.get("timeout_seconds") or 180),
            check=False,
        )
        log_path = Path(context["bundle_dir"]) / "browser" / f"{context['case_id']}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            (completed.stdout or "") + (completed.stderr or ""), encoding="utf-8"
        )
        return {
            "ok": completed.returncode == 0,
            "exit_code": completed.returncode,
            "evidence_ref": log_path.relative_to(
                Path(context["bundle_dir"])
            ).as_posix(),
        }

    def execute(
        self, step_type: str, inputs: Mapping[str, Any], context: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        if step_type == "observe.path":
            return self._observe_path(inputs)
        if step_type == "builder.chat":
            return self._chat(inputs, context)
        if step_type == "builder.session":
            return self._session(inputs, context)
        if step_type == "scenario.validate":
            return self._scenario_validate(inputs)
        if step_type == "browser.probe":
            return self._browser_probe(inputs, context)
        raise BuilderE2EError(f"unsupported Builder E2E step type: {step_type}")

    def cleanup(self, context: Mapping[str, Any]) -> Mapping[str, Any]:
        owned = [
            dict(item)
            for item in context.get("owned_artifacts") or []
            if isinstance(item, Mapping)
        ]
        if not owned:
            return {"status": "skipped", "reason": "case declared no owned drafts"}
        from adaos.sdk.developer import compositions
        from adaos.services.builder import BuilderWorkbenchService

        service = BuilderWorkbenchService.from_context()
        results: list[dict[str, Any]] = []
        for ownership in owned:
            draft_result = service.delete_development_skill(
                str(ownership.get("draft_id") or ""),
                str(context.get("webspace_id") or ""),
            )
            project_result: dict[str, Any] | None = None
            if draft_result.get("ok") is True:
                try:
                    project_result = compositions.delete(
                        str(ownership.get("project_id") or ""),
                        expected_manifest_digest=str(
                            ownership.get("project_manifest_digest") or ""
                        ),
                        expected_primary_ref=str(
                            ownership.get("primary_ref") or ""
                        ),
                    )
                except Exception as exc:
                    project_result = {
                        "ok": False,
                        "error": type(exc).__name__,
                        "detail": str(exc),
                    }
            results.append(
                {
                    "ownership": ownership,
                    "draft": draft_result,
                    "project": project_result,
                    "ok": draft_result.get("ok") is True
                    and isinstance(project_result, Mapping)
                    and project_result.get("ok") is True,
                }
            )
        return {
            "status": "passed" if all(item["ok"] for item in results) else "failed",
            "results": results,
        }


@dataclass(frozen=True)
class LoadedBuilderE2ESuite:
    path: Path
    suite: dict[str, Any]
    cases: tuple[dict[str, Any], ...]
    suite_digest: str
    case_digests: Mapping[str, str]


def load_builder_e2e_suite(path: Path) -> LoadedBuilderE2ESuite:
    suite_path = Path(path).expanduser().resolve()
    suite = validate_builder_e2e_record(SUITE_SCHEMA, _load_document(suite_path))
    cases: list[dict[str, Any]] = []
    digests: dict[str, str] = {}
    root = suite_path.parent.resolve()
    seen: set[str] = set()
    for raw_ref in suite["case_refs"]:
        case_path = (root / str(raw_ref)).resolve()
        try:
            case_path.relative_to(root)
        except ValueError as exc:
            raise BuilderE2EError(
                f"case reference escapes suite directory: {raw_ref}"
            ) from exc
        case = validate_builder_e2e_record(CASE_SCHEMA, _load_document(case_path))
        case_id = str(case["case_id"])
        if case_id in seen:
            raise BuilderE2EError(f"duplicate Builder E2E case id: {case_id}")
        seen.add(case_id)
        digest = _digest(case)
        case["_source_ref"] = case_path.relative_to(root).as_posix()
        cases.append(case)
        digests[case_id] = digest
    return LoadedBuilderE2ESuite(
        path=suite_path,
        suite=suite,
        cases=tuple(cases),
        suite_digest=_digest(suite),
        case_digests=digests,
    )


def _percentile(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(float(item) for item in values)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile + 0.5)))
    return round(ordered[index], 3)


def _aggregate_metrics(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    durations = [float(item.get("duration_ms") or 0) for item in results]
    counts = {
        status: sum(1 for item in results if item.get("status") == status)
        for status in ("passed", "failed", "inconclusive")
    }
    usage = {
        key: 0
        for key in (
            "fresh_input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "model_calls",
        )
    }
    required_total = 0
    required_passed = 0
    for result in results:
        metrics = (
            result.get("metrics") if isinstance(result.get("metrics"), Mapping) else {}
        )
        for key in usage:
            usage[key] += int(metrics.get(key) or 0)
        for step in result.get("steps") or []:
            if isinstance(step, Mapping) and step.get("required") is True:
                required_total += 1
                required_passed += int(step.get("status") == "passed")
    total = len(results)
    return {
        "case_attempts": total,
        **counts,
        "pass_rate": round(counts["passed"] / total, 6) if total else 0.0,
        "required_step_pass_rate": round(required_passed / required_total, 6)
        if required_total
        else 0.0,
        "duration_ms_p50": round(median(durations), 3) if durations else 0.0,
        "duration_ms_p90": _percentile(durations, 0.90),
        "duration_ms_p95": _percentile(durations, 0.95),
        **usage,
    }


def compare_builder_e2e_baseline(
    *,
    run_manifest: Mapping[str, Any],
    metrics: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    checked = validate_builder_e2e_record(BASELINE_SCHEMA, baseline)
    reasons: list[str] = []
    current_suite = dict(run_manifest.get("suite") or {})
    baseline_suite = dict(checked.get("suite") or {})
    for key in ("suite_id", "version", "digest"):
        if current_suite.get(key) != baseline_suite.get(key):
            reasons.append(f"suite.{key}")
    current_cohort = {
        "profile": run_manifest.get("profile"),
        "browser": run_manifest.get("browser"),
        "repetitions": run_manifest.get("repetitions"),
    }
    for key, expected in dict(checked.get("cohort") or {}).items():
        if key in current_cohort and current_cohort[key] != expected:
            reasons.append(f"cohort.{key}")
    for key, expected in dict(checked.get("environment_match") or {}).items():
        if dict(run_manifest.get("environment") or {}).get(key) != expected:
            reasons.append(f"environment.{key}")
    if reasons:
        return {
            "status": "uncomparable",
            "reasons": sorted(set(reasons)),
            "metrics": {},
        }
    comparisons: dict[str, Any] = {}
    statuses: list[str] = []
    for key, baseline_value in dict(checked.get("metrics") or {}).items():
        candidate_value = metrics.get(key)
        if not isinstance(baseline_value, (int, float)) or not isinstance(
            candidate_value, (int, float)
        ):
            continue
        delta = round(float(candidate_value) - float(baseline_value), 6)
        if delta == 0:
            status = "unchanged"
        elif (key in _LOWER_IS_BETTER and delta < 0) or (
            key not in _LOWER_IS_BETTER and delta > 0
        ):
            status = "improved"
        else:
            status = "regressed"
        statuses.append(status)
        comparisons[key] = {
            "candidate": candidate_value,
            "baseline": baseline_value,
            "delta": delta,
            "status": status,
        }
    overall = (
        "regressed"
        if "regressed" in statuses
        else "improved"
        if "improved" in statuses
        else "unchanged"
    )
    return {"status": overall, "reasons": [], "metrics": comparisons}


class BuilderE2ERunner:
    def __init__(
        self,
        suite_path: Path,
        *,
        output_root: Path,
        repo_root: Path | None = None,
        case_ids: Sequence[str] = (),
        tags: Sequence[str] = (),
        profile: str | None = None,
        repetitions: int | None = None,
        browser: str | None = None,
        baseline_path: Path | None = None,
        run_id: str | None = None,
        executor: BuilderE2EStepExecutor | None = None,
    ) -> None:
        self.loaded = load_builder_e2e_suite(suite_path)
        self.repo_root = (repo_root or Path.cwd()).resolve()
        defaults = dict(self.loaded.suite.get("defaults") or {})
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self.run_id = _safe_token(
            run_id or f"builder-e2e-{stamp}", fallback=f"builder-e2e-{stamp}"
        )
        self.output_root = Path(output_root).expanduser().resolve()
        self.bundle_dir = self.output_root / self.run_id
        self.case_ids = tuple(str(item) for item in case_ids if str(item))
        self.tags = tuple(str(item) for item in tags if str(item))
        self.profile = str(profile or defaults.get("profile") or "generic").strip()
        self.repetitions = int(repetitions or defaults.get("repetitions") or 1)
        self.browser = str(browser or defaults.get("browser") or "auto").strip()
        if self.repetitions < 1 or self.repetitions > 20:
            raise BuilderE2EError("repetitions must be between 1 and 20")
        if self.browser not in {"auto", "on", "off"}:
            raise BuilderE2EError("browser must be auto, on, or off")
        self.baseline_path = (
            Path(baseline_path).expanduser().resolve() if baseline_path else None
        )
        self.baseline = (
            _load_document(self.baseline_path) if self.baseline_path else None
        )
        if self.baseline is not None:
            validate_builder_e2e_record(BASELINE_SCHEMA, self.baseline)
        configured_adapter = str(defaults.get("adapter") or "legacy_dev_chat.v1")
        if executor is None:
            if configured_adapter != "legacy_dev_chat.v1":
                raise BuilderE2EError(
                    f"Builder E2E adapter is not available: {configured_adapter}"
                )
            executor = CompatibilityBuilderExecutor(
                repo_root=self.repo_root, browser_mode=self.browser
            )
        elif configured_adapter != executor.adapter_id:
            raise BuilderE2EError(
                "suite adapter does not match the injected executor: "
                f"{configured_adapter} != {executor.adapter_id}"
            )
        self.executor = executor

    def _selected_cases(self) -> list[dict[str, Any]]:
        known = {str(case["case_id"]): case for case in self.loaded.cases}
        unknown = sorted(set(self.case_ids) - set(known))
        if unknown:
            raise BuilderE2EError(f"unknown Builder E2E case: {unknown[0]}")
        selected = list(self.loaded.cases)
        if self.case_ids:
            selected = [case for case in selected if case["case_id"] in self.case_ids]
        if self.tags:
            required = set(self.tags)
            selected = [
                case for case in selected if required <= set(case.get("tags") or [])
            ]
        if not selected:
            raise BuilderE2EError("Builder E2E selection is empty")
        return selected

    def _run_case(self, case: Mapping[str, Any], repetition: int) -> dict[str, Any]:
        started_at = _utc_now()
        started = time.perf_counter()
        context: dict[str, Any] = {
            "run_id": self.run_id,
            "case_id": case["case_id"],
            "repetition": repetition,
            "locale": case["locale"],
            "profile": self.profile,
            "browser": self.browser,
            "bundle_dir": self.bundle_dir,
            "outputs": {},
            "owned_artifacts": [],
            "timeout_seconds": float(
                dict(self.loaded.suite.get("defaults") or {}).get("timeout_seconds")
                or 300
            ),
        }
        steps: list[dict[str, Any]] = []
        full_outputs: list[Mapping[str, Any]] = []
        evidence_refs: list[str] = []
        failure: dict[str, Any] | None = None
        for declaration in case["steps"]:
            step_started = time.perf_counter()
            required = bool(declaration.get("required", True))
            resolved_input: dict[str, Any] = {}
            try:
                resolved_input = _resolve_value(declaration.get("input") or {}, context)
                if declaration.get("timeout_seconds") is not None:
                    resolved_input["timeout_seconds"] = declaration["timeout_seconds"]
                if declaration["type"] in {"builder.chat", "builder.session"}:
                    resolved_input.setdefault("webspace_id", f"e2e-{self.run_id}")
                    context["webspace_id"] = str(resolved_input["webspace_id"])
                output = dict(
                    self.executor.execute(
                        str(declaration["type"]), resolved_input, context
                    )
                )
                findings = _expectation_findings(
                    output, declaration.get("expect") or {}
                )
                if output.get("skipped") is True:
                    status = "skipped"
                elif findings or output.get("ok") is False:
                    status = "failed"
                else:
                    status = "passed"
                context["outputs"][str(declaration["id"])] = copy.deepcopy(output)
                if resolved_input.get("owns_created_draft") is True:
                    draft_id = str(output.get("draft_id") or "").strip()
                    if draft_id:
                        project = (
                            dict(output.get("project"))
                            if isinstance(output.get("project"), Mapping)
                            else {}
                        )
                        primary = next(
                            (
                                item
                                for item in dict(project.get("components") or {}).get(
                                    "owned", []
                                )
                                if isinstance(item, Mapping)
                                and item.get("role") == "primary"
                            ),
                            {},
                        )
                        context["owned_artifacts"].append(
                            {
                                "draft_id": draft_id,
                                "project_id": str(
                                    output.get("project_id")
                                    or project.get("id")
                                    or ""
                                ),
                                "project_manifest_digest": str(
                                    project.get("manifest_digest") or ""
                                ),
                                "primary_ref": str(primary.get("ref") or ""),
                            }
                        )
                evidence = str(output.get("evidence_ref") or "").strip()
                if evidence:
                    evidence_refs.append(evidence)
            except BuilderE2EUnavailable as exc:
                output = {"error": type(exc).__name__, "detail": str(exc)}
                findings = [{"code": "runner_unavailable", "detail": str(exc)}]
                status = "inconclusive"
            except Exception as exc:
                output = {"error": type(exc).__name__, "detail": str(exc)}
                findings = [{"code": "step_exception", "detail": str(exc)}]
                status = "failed"
            full_outputs.append(copy.deepcopy(output))
            persisted_output, persisted_evidence = _compact_step_output(
                output,
                bundle_dir=Path(context["bundle_dir"]),
                case_id=str(case["case_id"]),
                repetition=repetition,
                step_id=str(declaration["id"]),
            )
            if persisted_evidence:
                evidence_refs.append(persisted_evidence)
            step_result = {
                "id": declaration["id"],
                "type": declaration["type"],
                "required": required,
                "status": status,
                "duration_ms": round((time.perf_counter() - step_started) * 1000.0, 3),
                "input": redact_value(resolved_input),
                "output": persisted_output,
                "findings": redact_value(findings),
            }
            steps.append(step_result)
            if required and status in {"failed", "inconclusive"}:
                failure = {
                    "step_id": declaration["id"],
                    "status": status,
                    "findings": redact_value(findings),
                }
                break
        required_statuses = [step["status"] for step in steps if step["required"]]
        if "failed" in required_statuses:
            status = "failed"
        elif "inconclusive" in required_statuses:
            status = "inconclusive"
        else:
            status = "passed"
        cleanup: Mapping[str, Any] | None = None
        cleanup_policy = str(dict(case.get("cleanup") or {}).get("policy") or "never")
        should_cleanup = cleanup_policy == "always" or (
            cleanup_policy == "on-success" and status == "passed"
        )
        if should_cleanup:
            try:
                cleanup = self.executor.cleanup(context)
            except Exception as exc:
                cleanup = {
                    "status": "failed",
                    "error": type(exc).__name__,
                    "detail": str(exc),
                }
                if status == "passed":
                    status = "inconclusive"
                    failure = {
                        "step_id": "cleanup",
                        "status": "inconclusive",
                        "findings": [cleanup],
                    }
            if cleanup is not None and cleanup.get("status") == "failed":
                if status == "passed":
                    status = "inconclusive"
                    failure = {
                        "step_id": "cleanup",
                        "status": "inconclusive",
                        "findings": [cleanup],
                    }
        usage = _collect_usage(full_outputs)
        result = validate_builder_e2e_record(
            CASE_RESULT_SCHEMA,
            {
                "schema": CASE_RESULT_SCHEMA,
                "run_id": self.run_id,
                "case_id": case["case_id"],
                "case_digest": self.loaded.case_digests[str(case["case_id"])],
                "repetition": repetition,
                "status": status,
                "started_at": started_at,
                "ended_at": _utc_now(),
                "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "steps": steps,
                "metrics": {
                    **usage,
                    "step_count": len(steps),
                    "required_step_count": len(required_statuses),
                    "required_step_passed": sum(
                        item == "passed" for item in required_statuses
                    ),
                },
                "evidence_refs": sorted(set(evidence_refs)),
                "failure": failure,
                "cleanup": redact_value(cleanup) if cleanup is not None else None,
            },
        )
        result_path = (
            self.bundle_dir
            / "cases"
            / str(case["case_id"])
            / f"attempt-{repetition:02d}.json"
        )
        _write_json(result_path, result)
        return result

    def run(self) -> dict[str, Any]:
        selected = self._selected_cases()
        try:
            self.bundle_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise BuilderE2EError(
                f"Builder E2E run bundle already exists: {self.bundle_dir}"
            ) from exc
        environment = _repository_environment(self.repo_root)
        run_manifest = validate_builder_e2e_record(
            RUN_SCHEMA,
            {
                "schema": RUN_SCHEMA,
                "run_id": self.run_id,
                "suite": {
                    "suite_id": self.loaded.suite["suite_id"],
                    "version": self.loaded.suite["version"],
                    "digest": self.loaded.suite_digest,
                    "cohort": self.loaded.suite["cohort"],
                },
                "adapter": self.executor.adapter_id,
                "profile": self.profile,
                "repetitions": self.repetitions,
                "browser": self.browser,
                "cases": [
                    {
                        "case_id": case["case_id"],
                        "digest": self.loaded.case_digests[str(case["case_id"])],
                        "locale": case["locale"],
                        "source_ref": case["_source_ref"],
                    }
                    for case in selected
                ],
                "selection": {"case_ids": list(self.case_ids), "tags": list(self.tags)},
                "environment": environment,
                "input_attribution": {
                    "domain_packs": list(
                        dict(self.loaded.suite.get("defaults") or {}).get(
                            "domain_packs"
                        )
                        or []
                    ),
                    "case_digests": {
                        case["case_id"]: self.loaded.case_digests[str(case["case_id"])]
                        for case in selected
                    },
                    "adapter": self.executor.adapter_id,
                },
                "baseline": (
                    {
                        "baseline_id": self.baseline.get("baseline_id"),
                        "digest": _digest(self.baseline),
                    }
                    if self.baseline
                    else None
                ),
                "started_at": _utc_now(),
            },
        )
        _write_json(self.bundle_dir / "run.json", run_manifest)
        results = [
            self._run_case(case, repetition)
            for case in selected
            for repetition in range(1, self.repetitions + 1)
        ]
        metrics = _aggregate_metrics(results)
        comparison = (
            compare_builder_e2e_baseline(
                run_manifest=run_manifest, metrics=metrics, baseline=self.baseline
            )
            if self.baseline
            else None
        )
        status = (
            "failed"
            if metrics["failed"]
            else "inconclusive"
            if metrics["inconclusive"]
            else "passed"
        )
        report = validate_builder_e2e_record(
            REPORT_SCHEMA,
            {
                "schema": REPORT_SCHEMA,
                "run_id": self.run_id,
                "status": status,
                "run_manifest_ref": "run.json",
                "case_results": [
                    f"cases/{result['case_id']}/attempt-{int(result['repetition']):02d}.json"
                    for result in results
                ],
                "summary": {
                    "passed": metrics["passed"],
                    "failed": metrics["failed"],
                    "inconclusive": metrics["inconclusive"],
                    "total": metrics["case_attempts"],
                },
                "metrics": metrics,
                "comparison": comparison,
                "ended_at": _utc_now(),
            },
        )
        _write_json(self.bundle_dir / "report.json", report)
        return {**report, "bundle_dir": str(self.bundle_dir)}


def create_builder_e2e_baseline(
    *,
    baseline_id: str,
    accepted_by: str,
    run_manifest: Mapping[str, Any],
    report: Mapping[str, Any],
    source_report_ref: str,
    notes: str = "",
) -> dict[str, Any]:
    persisted_report = {
        key: value for key, value in report.items() if key != "bundle_dir"
    }
    report_checked = validate_builder_e2e_record(REPORT_SCHEMA, persisted_report)
    run_checked = validate_builder_e2e_record(RUN_SCHEMA, run_manifest)
    return validate_builder_e2e_record(
        BASELINE_SCHEMA,
        {
            "schema": BASELINE_SCHEMA,
            "baseline_id": _safe_token(baseline_id, fallback="builder-baseline"),
            "accepted_at": _utc_now(),
            "accepted_by": str(accepted_by or "").strip(),
            "suite": {
                key: run_checked["suite"][key]
                for key in ("suite_id", "version", "digest")
            },
            "cohort": {
                "profile": run_checked["profile"],
                "browser": run_checked["browser"],
                "repetitions": run_checked["repetitions"],
            },
            "reference": {
                "adapter": run_checked["adapter"],
                "repository_commit": run_checked["environment"].get(
                    "repository_commit"
                ),
                "client_commit": run_checked["environment"].get("client_commit"),
            },
            "environment_match": {
                key: run_checked["environment"].get(key)
                for key in ("python", "platform")
                if run_checked["environment"].get(key)
            },
            "metrics": copy.deepcopy(dict(report_checked["metrics"])),
            "source_report_ref": str(source_report_ref),
            "source_report_digest": _digest(report_checked),
            "notes": str(notes),
        },
    )


__all__ = [
    "BASELINE_SCHEMA",
    "BuilderE2EError",
    "BuilderE2ERunner",
    "BuilderE2EStepExecutor",
    "CompatibilityBuilderExecutor",
    "compare_builder_e2e_baseline",
    "create_builder_e2e_baseline",
    "load_builder_e2e_suite",
    "validate_builder_e2e_record",
]
