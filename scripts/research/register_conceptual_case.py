"""Reconcile a tracked conceptual case package into Research Workbench."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


ToolCall = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]
_VISIBILITY_PROFILES = {
    "shared",
    "evaluation_only",
    "formulation_only",
    "implementation_input",
}


def _read_spec(package_dir: Path) -> dict[str, Any]:
    path = package_dir / "workbench.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != "adaos.research.workbench_case.v1":
        raise ValueError("workbench.json must use adaos.research.workbench_case.v1")
    for key in ("direction_id", "title", "description", "task", "artifacts", "lifecycle"):
        if key not in value:
            raise ValueError(f"workbench.json is missing {key}")
    task = value["task"]
    if not isinstance(task, dict) or not task.get("task_id") or not task.get("title"):
        raise ValueError("workbench.json task requires task_id and title")
    artifacts = value["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("workbench.json requires at least one artifact")
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise ValueError("workbench artifact entries must be objects")
        if artifact.get("visibility_profile") not in _VISIBILITY_PROFILES:
            raise ValueError("workbench artifact has an invalid visibility_profile")
    return value


def _package_path(package_dir: Path, relative: str) -> Path:
    root = package_dir.resolve()
    path = (root / relative).resolve()
    if path == root or root not in path.parents or not path.is_file():
        raise ValueError(f"workbench artifact is missing or escapes the case package: {relative}")
    return path


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def register_case(package_dir: Path, invoke: ToolCall, *, actor: str) -> dict[str, Any]:
    package = package_dir.resolve()
    spec = _read_spec(package)
    direction_id = str(spec["direction_id"])

    listed = invoke("list_directions", {"limit": 5000})
    directions = list(listed.get("items") or [])
    existing = next(
        (item for item in directions if str(item.get("direction_id") or item.get("id")) == direction_id),
        None,
    )
    created_direction = existing is None
    if created_direction:
        invoke(
            "create_direction",
            {
                "project_id": direction_id,
                "title": str(spec["title"]),
                "description": str(spec["description"]),
                "tags": list(spec.get("tags") or []),
                "actor": actor,
            },
        )
    elif str(existing.get("title") or "") != str(spec["title"]):
        raise ValueError(f"research-direction:{direction_id} exists with a different title")

    state = invoke("get_direction", {"direction_id": direction_id})
    task_spec = dict(spec["task"])
    agenda = state.get("agenda") if isinstance(state.get("agenda"), Mapping) else {}
    tasks = list(agenda.get("tasks") or [])
    task_id = str(task_spec["task_id"])
    created_task = not any(str(item.get("task_id")) == task_id for item in tasks)
    if created_task:
        invoke(
            "create_task",
            {
                "direction_id": direction_id,
                "task_id": task_id,
                "title": str(task_spec["title"]),
                "research_question": str(task_spec.get("research_question") or ""),
                "activate": True,
                "actor": actor,
            },
        )
    elif str(agenda.get("active_task_id") or "") != task_id:
        invoke(
            "select_active_task",
            {"direction_id": direction_id, "task_id": task_id, "actor": actor},
        )

    artifact_listing = invoke("list_artifacts", {"direction_id": direction_id})
    existing_artifacts = list(artifact_listing.get("items") or [])
    artifacts: list[dict[str, Any]] = []
    for artifact_spec in spec["artifacts"]:
        source = _package_path(package, str(artifact_spec["path"]))
        name = str(artifact_spec.get("name") or source.name)
        expected_digest = _digest(source)
        existing_artifact = next(
            (
                item
                for item in existing_artifacts
                if str(item.get("group_id")) == str(artifact_spec["group_id"])
                and str(item.get("path")) == name
            ),
            None,
        )
        expected_role = str(artifact_spec.get("role") or "source")
        expected_visibility = str(artifact_spec["visibility_profile"])
        if existing_artifact is not None:
            actual = (
                str(existing_artifact.get("digest") or ""),
                str(existing_artifact.get("role") or ""),
                str(existing_artifact.get("visibility_profile") or ""),
            )
            expected = (expected_digest, expected_role, expected_visibility)
            if actual != expected:
                raise ValueError(
                    f"workbench artifact {artifact_spec['group_id']}/{name} differs from the tracked case package"
                )
            item = existing_artifact
            reused = True
        else:
            result = invoke(
                "attach_source",
                {
                    "direction_id": direction_id,
                    "path": str(source),
                    "group_id": str(artifact_spec["group_id"]),
                    "name": name,
                    "role": expected_role,
                    "visibility_profile": expected_visibility,
                    "actor": actor,
                },
            )
            item = result.get("artifact") if isinstance(result.get("artifact"), Mapping) else {}
            reused = False
        artifacts.append(
            {
                "path": str(artifact_spec["path"]),
                "group_id": str(artifact_spec["group_id"]),
                "digest": item.get("digest"),
                "visibility_profile": expected_visibility,
                "reused": reused,
            }
        )

    synchronized = invoke("sync_source_bundle", {"direction_id": direction_id, "actor": actor})
    projected = invoke("list_directions", {"limit": 5000})
    projection = next(
        (
            item
            for item in list(projected.get("items") or [])
            if str(item.get("direction_id") or item.get("id")) == direction_id
        ),
        None,
    )
    if projection is None:
        raise RuntimeError(f"research-direction:{direction_id} was not projected by list_directions")
    return {
        "schema": "adaos.research.workbench_registration_receipt.v1",
        "ok": True,
        "direction_ref": f"research-direction:{direction_id}",
        "task_ref": f"research-task:{task_id}",
        "created_direction": created_direction,
        "created_task": created_task,
        "artifacts": artifacts,
        "source_bundle_digest": (synchronized.get("source_bundle") or {}).get("digest"),
        "workbench_projection": dict(projection),
        "conceptual_lifecycle": dict(spec["lifecycle"]),
        "builder_invoked": False,
    }


def _runtime_invoker() -> ToolCall:
    from adaos.apps.bootstrap import init_ctx
    from adaos.apps.cli.commands.skill import _configure_skill_run_sdk_runtimes, _mgr

    init_ctx()
    _configure_skill_run_sdk_runtimes()
    manager = _mgr()

    def call(tool: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        result = manager.run_tool(
            "research_orchestrator_skill",
            tool,
            dict(payload),
        )
        if not isinstance(result, Mapping):
            raise RuntimeError(f"research_orchestrator_skill:{tool} returned a non-object")
        return result

    return call


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package_dir", type=Path)
    parser.add_argument("--actor", default="user:local")
    args = parser.parse_args()
    receipt = register_case(args.package_dir, _runtime_invoker(), actor=args.actor)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
