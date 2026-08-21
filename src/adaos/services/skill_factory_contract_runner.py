"""Trusted interpreter for consumer-owned Builder conformance sequences.

The candidate supplies production tool implementations.  The admitted
consumer contract supplies data and an ordered sequence.  This module owns the
interpretation, schema validation, process bounds, and result report so a
candidate cannot satisfy the gate merely by authoring a permissive test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

import yaml
from jsonschema import Draft202012Validator

from adaos.skills.runtime_runner import execute_tool


_PROTECTED_ENV = {
    "ADAOS_CURRENT_SKILL",
    "ADAOS_SKILL_ENV_PATH",
    "ADAOS_SKILL_INTERNAL_DATA_ROOT",
    "ADAOS_SKILL_NAME",
    "ADAOS_SKILL_ROOT",
    "ADAOS_TASK_RUNTIME_DIR",
    "PYTHONHOME",
    "PYTHONPATH",
}


class ContractSequenceError(RuntimeError):
    """Raised when a trusted conformance sequence cannot be admitted."""


def _token(value: Any, fallback: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return result[:120] or fallback


def _json_pointer(value: Any, pointer: str) -> Any:
    token = str(pointer or "")
    if token in {"", "/"}:
        return value
    if not token.startswith("/"):
        raise ContractSequenceError(f"JSON pointer must start with '/': {token}")
    current = value
    for raw in token[1:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if part not in current:
                raise ContractSequenceError(f"JSON pointer {token} has no member {part!r}")
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise ContractSequenceError(f"JSON pointer {token} has invalid index {part!r}") from exc
        else:
            raise ContractSequenceError(f"JSON pointer {token} crosses a scalar at {part!r}")
    return current


def _resolve(
    value: Any,
    outputs: Mapping[str, Any],
    *,
    item: Any = None,
    candidate: Mapping[str, Any] | None = None,
) -> Any:
    if isinstance(value, Mapping):
        if set(value) == {"$bind"}:
            binding = value["$bind"]
            if not isinstance(binding, Mapping):
                raise ContractSequenceError("$bind must be an object")
            step = str(binding.get("step") or "")
            if step not in outputs:
                raise ContractSequenceError(f"$bind refers to unavailable step {step!r}")
            return _json_pointer(outputs[step], str(binding.get("pointer") or ""))
        if set(value) == {"$item"}:
            if item is None:
                raise ContractSequenceError("$item is only valid inside for_each")
            return _json_pointer(item, str(value["$item"] or ""))
        if set(value) == {"$candidate"}:
            if candidate is None:
                raise ContractSequenceError("$candidate is unavailable")
            return _json_pointer(candidate, str(value["$candidate"] or ""))
        return {
            str(key): _resolve(child, outputs, item=item, candidate=candidate)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [
            _resolve(child, outputs, item=item, candidate=candidate)
            for child in value
        ]
    return value


def _schema_errors(schema: Mapping[str, Any], value: Any) -> list[str]:
    validator = Draft202012Validator(dict(schema))
    return [
        f"/{'/'.join(str(part) for part in error.absolute_path)}: {error.message}"
        for error in sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path))
    ]


def _tool_entries(manifest: Mapping[str, Any]) -> dict[str, tuple[str, str]]:
    entries: dict[str, tuple[str, str]] = {}
    for raw in manifest.get("tools") or []:
        if not isinstance(raw, Mapping):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        entry = str(raw.get("entry") or f"handlers.main:{name}").strip()
        if ":" in entry:
            module, attr = entry.rsplit(":", 1)
        else:
            module, attr = "handlers.main", entry
        if not module or not attr:
            raise ContractSequenceError(f"tool {name} has invalid entry {entry!r}")
        entries[name] = (module, attr)
    return entries


def _assertions(step_id: str, value: Any, assertions: Any) -> None:
    for raw in assertions or []:
        if not isinstance(raw, Mapping) or "equals" not in raw:
            raise ContractSequenceError(f"step {step_id} has an invalid assertion")
        pointer = str(raw.get("pointer") or "")
        actual = _json_pointer(value, pointer)
        if actual != raw["equals"]:
            raise ContractSequenceError(
                f"step {step_id} assertion {pointer or '/'} expected "
                f"{raw['equals']!r}, got {actual!r}"
            )


def _safe_relative_outputs(cwd: Path, values: Any) -> list[Path]:
    if not isinstance(values, list) or not values:
        raise ContractSequenceError("execution_spec expected_outputs must be a non-empty array")
    paths: list[Path] = []
    for value in values:
        relative = Path(str(value or ""))
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ContractSequenceError(f"unsafe execution_spec expected output {value!r}")
        resolved = (cwd / relative).resolve()
        try:
            resolved.relative_to(cwd)
        except ValueError as exc:
            raise ContractSequenceError(f"execution_spec output escapes working directory: {value!r}") from exc
        paths.append(resolved)
    return paths


def _run_execution_spec(
    step: Mapping[str, Any],
    outputs: Mapping[str, Any],
    *,
    skill_dir: Path,
    data_root: Path,
    runtime_root: Path,
) -> dict[str, Any]:
    source_step = str(step.get("source_step") or "")
    if source_step not in outputs:
        raise ContractSequenceError(f"execution_spec refers to unavailable step {source_step!r}")
    spec = outputs[source_step]
    command = _json_pointer(spec, str(step.get("command_pointer") or "/command"))
    working_directory = _json_pointer(
        spec, str(step.get("working_directory_pointer") or "/working_directory")
    )
    expected = _json_pointer(
        spec, str(step.get("expected_outputs_pointer") or "/expected_outputs")
    )
    returned_env = _json_pointer(spec, str(step.get("environment_pointer") or "/environment")) \
        if isinstance(spec, Mapping) and "environment" in spec else {}
    if (
        not isinstance(command, list)
        or not 2 <= len(command) <= 64
        or any(not isinstance(part, str) or not part for part in command)
    ):
        raise ContractSequenceError("execution_spec command must contain 2..64 non-empty strings")
    executable = Path(command[0]).resolve()
    if executable != Path(sys.executable).resolve():
        raise ContractSequenceError("execution_spec command[0] must be the trusted active Python interpreter")
    script = Path(command[1]).resolve()
    try:
        script.relative_to(skill_dir)
    except ValueError as exc:
        raise ContractSequenceError("execution_spec command[1] must be a source file below the candidate skill") from exc
    cwd = Path(str(working_directory)).resolve()
    try:
        cwd.relative_to(data_root)
    except ValueError as exc:
        raise ContractSequenceError("execution_spec working_directory must be below trusted skill data root") from exc
    if not cwd.is_dir():
        raise ContractSequenceError("execution_spec working_directory does not exist")
    expected_paths = _safe_relative_outputs(cwd, expected)
    timeout_seconds = min(300, max(1, int(step.get("timeout_seconds") or 60)))
    environment = dict(os.environ)
    for key in _PROTECTED_ENV:
        environment.pop(key, None)
    if returned_env:
        if not isinstance(returned_env, Mapping):
            raise ContractSequenceError("execution_spec environment must be an object")
        for key, value in returned_env.items():
            name = str(key)
            if name in _PROTECTED_ENV or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ContractSequenceError(f"execution_spec may not override protected environment {name!r}")
            if not isinstance(value, str):
                raise ContractSequenceError(f"execution_spec environment {name!r} must be a string")
            environment[name] = value
    environment.update(
        {
            "ADAOS_SKILL_NAME": skill_dir.name,
            "ADAOS_CURRENT_SKILL": skill_dir.name,
            "ADAOS_SKILL_ROOT": str(skill_dir),
            "ADAOS_SKILL_INTERNAL_DATA_ROOT": str(data_root),
            "ADAOS_SKILL_ENV_PATH": str(data_root / "db" / "skill_env.json"),
            "ADAOS_TASK_RUNTIME_DIR": str(runtime_root),
        }
    )
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        raise ContractSequenceError(
            f"execution_spec exited {completed.returncode}: "
            f"{(completed.stdout + completed.stderr)[-2000:]}"
        )
    missing = [str(path.relative_to(cwd)) for path in expected_paths if not path.is_file()]
    if missing:
        raise ContractSequenceError(
            "execution_spec omitted exact expected outputs: " + ", ".join(missing)
        )
    return {
        "exit_code": completed.returncode,
        "working_directory": str(cwd),
        "expected_outputs": [str(path.relative_to(cwd)).replace("\\", "/") for path in expected_paths],
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    }


def run_sequence(request: Mapping[str, Any]) -> dict[str, Any]:
    skill_dir = Path(str(request["skill_dir"])).resolve()
    runtime_root = Path(str(request["runtime_root"])).resolve()
    contract = dict(request["contract"])
    fixture = dict(request["fixture"])
    manifest = yaml.safe_load((skill_dir / "skill.yaml").read_text(encoding="utf-8")) or {}
    if not isinstance(manifest, Mapping):
        raise ContractSequenceError("candidate skill manifest is not an object")
    fixture_id = _token(fixture.get("id"), "operation_sequence")
    contract_id = _token(contract.get("contract"), "contract")
    invocation_id = _token(request.get("invocation_id"), "invocation")
    # Windows still encounters legacy MAX_PATH boundaries in child-process
    # creation. Keep the physical envelope short while retaining all readable
    # identities in the trusted report and request document.
    physical_id = hashlib.sha256(
        f"{contract_id}\0{fixture_id}\0{invocation_id}\0{skill_dir.name}".encode("utf-8")
    ).hexdigest()[:16]
    data_root = (runtime_root / "contract-fixtures" / physical_id / "data").resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    # Tool calls and the command they return must resolve the same owner-scoped
    # storage.  This process is dedicated to one fixture, so setting its
    # environment once also avoids leaking candidate context into the Builder
    # process that launched us.
    for key in _PROTECTED_ENV:
        os.environ.pop(key, None)
    os.environ.update(
        {
            "ADAOS_SKILL_NAME": skill_dir.name,
            "ADAOS_CURRENT_SKILL": skill_dir.name,
            "ADAOS_SKILL_ROOT": str(skill_dir),
            "ADAOS_SKILL_INTERNAL_DATA_ROOT": str(data_root),
            "ADAOS_SKILL_ENV_PATH": str(data_root / "db" / "skill_env.json"),
            "ADAOS_TASK_RUNTIME_DIR": str(runtime_root),
        }
    )
    entries = _tool_entries(manifest)
    candidate = {"skill_id": skill_dir.name}
    operations = dict(contract.get("operations") or {})
    steps = fixture.get("steps")
    if not isinstance(steps, list) or not steps or len(steps) > 50:
        raise ContractSequenceError("operation_sequence steps must contain 1..50 entries")
    outputs: dict[str, Any] = {}
    report_steps: list[dict[str, Any]] = []
    for position, raw_step in enumerate(steps, start=1):
        if not isinstance(raw_step, Mapping):
            raise ContractSequenceError(f"step {position} is not an object")
        step = dict(raw_step)
        step_id = _token(step.get("id"), f"step_{position}")
        if step_id in outputs:
            raise ContractSequenceError(f"duplicate operation_sequence step id {step_id!r}")
        kind = str(step.get("kind") or "operation")
        if kind == "execution_spec":
            result = _run_execution_spec(
                step,
                outputs,
                skill_dir=skill_dir,
                data_root=data_root,
                runtime_root=runtime_root,
            )
            _assertions(step_id, result, step.get("assert"))
            outputs[step_id] = result
            report_steps.append({"id": step_id, "kind": kind, "ok": True})
            continue
        if kind != "operation":
            raise ContractSequenceError(f"step {step_id} has unsupported kind {kind!r}")
        operation = str(step.get("operation") or "")
        operation_contract = operations.get(operation)
        if not isinstance(operation_contract, Mapping):
            raise ContractSequenceError(f"step {step_id} names undeclared operation {operation!r}")
        if operation not in entries:
            raise ContractSequenceError(f"candidate exports no tool for operation {operation!r}")
        repetitions: list[Any] = [None]
        if "for_each" in step:
            source = _resolve(step["for_each"], outputs, candidate=candidate)
            if not isinstance(source, list) or not source:
                raise ContractSequenceError(f"step {step_id} for_each must resolve to a non-empty array")
            if len(source) > 100:
                raise ContractSequenceError(f"step {step_id} for_each exceeds 100 items")
            repetitions = source
        results: list[Any] = []
        for item in repetitions:
            payload = _resolve(
                step.get("input") or {},
                outputs,
                item=item,
                candidate=candidate,
            )
            input_schema = operation_contract.get("input_schema")
            if isinstance(input_schema, Mapping):
                failures = _schema_errors(input_schema, payload)
                if failures:
                    raise ContractSequenceError(
                        f"step {step_id} input violates {operation}: " + "; ".join(failures[:20])
                    )
            module, attr = entries[operation]
            result = execute_tool(
                skill_dir,
                module=module,
                attr=attr,
                payload=payload,
                extra_paths=[skill_dir.parent],
            )
            output_schema = operation_contract.get("output_schema")
            if isinstance(output_schema, Mapping):
                failures = _schema_errors(output_schema, result)
                if failures:
                    raise ContractSequenceError(
                        f"step {step_id} output violates {operation}: " + "; ".join(failures[:20])
                    )
            _assertions(step_id, result, step.get("assert"))
            results.append(result)
        output = results if "for_each" in step else results[0]
        outputs[step_id] = output
        report_steps.append(
            {
                "id": step_id,
                "kind": kind,
                "operation": operation,
                "calls": len(results),
                "ok": True,
            }
        )
    return {
        "ok": True,
        "contract": str(contract.get("contract") or ""),
        "fixture_id": str(fixture.get("id") or "operation_sequence"),
        "skill_id": skill_dir.name,
        "runtime_path": str(data_root.relative_to(runtime_root)).replace("\\", "/"),
        "steps": report_steps,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    result_path = Path(args.result).resolve()
    try:
        request = json.loads(Path(args.request).read_text(encoding="utf-8-sig"))
        result = run_sequence(request)
    except BaseException as exc:  # isolated worker must always emit a diagnostic
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover - subprocess entrypoint
    raise SystemExit(main())
