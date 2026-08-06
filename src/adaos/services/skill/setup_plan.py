from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml
from jsonschema import Draft202012Validator


SETUP_PLAN_SCHEMA = "adaos.skill.setup_plan.v1"
SETUP_PLAN_FILENAME = "setup_plan.json"


class SetupPlanError(ValueError):
    pass


def _schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "abi" / "skill.setup_plan.v1.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def validate_setup_plan(value: Mapping[str, Any], *, skill_id: str | None = None) -> dict[str, Any]:
    plan = dict(value)
    errors = sorted(Draft202012Validator(_schema()).iter_errors(plan), key=lambda item: list(item.path))
    if errors:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
            for error in errors[:20]
        )
        raise SetupPlanError(f"invalid setup plan: {details}")
    if skill_id and str(plan.get("skill_id") or "") != str(skill_id):
        raise SetupPlanError("setup plan skill_id does not match the skill manifest")
    step_ids = [str(item.get("step_id") or "") for item in plan.get("steps") or []]
    if len(step_ids) != len(set(step_ids)):
        raise SetupPlanError("setup plan step_id values must be unique")
    check_ids = [
        str(item.get("check_id") or "")
        for item in (plan.get("preconditions") or []) + (plan.get("verification", {}).get("checks") or [])
    ]
    if len(check_ids) != len(set(check_ids)):
        raise SetupPlanError("setup plan check_id values must be unique")
    return plan


def load_setup_plan(skill_dir: Path, *, skill_id: str | None = None) -> dict[str, Any]:
    path = Path(skill_dir) / SETUP_PLAN_FILENAME
    if not path.is_file():
        raise SetupPlanError(f"skill setup requires {SETUP_PLAN_FILENAME}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SetupPlanError(f"could not read setup plan: {exc}") from exc
    if not isinstance(value, Mapping):
        raise SetupPlanError("setup plan must be a JSON object")
    return validate_setup_plan(value, skill_id=skill_id)


def _load_manifest(skill_dir: Path) -> dict[str, Any]:
    path = Path(skill_dir) / "skill.yaml"
    if not path.is_file():
        raise SetupPlanError("skill.yaml is required")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise SetupPlanError(f"could not read skill.yaml: {exc}") from exc
    if not isinstance(value, Mapping):
        raise SetupPlanError("skill.yaml must contain an object")
    return dict(value)


def setup_is_required(skill_dir: Path, manifest: Mapping[str, Any] | None = None) -> bool:
    data = dict(manifest or _load_manifest(skill_dir))
    tools = data.get("tools") if isinstance(data.get("tools"), Mapping) else {}
    return bool(
        "setup" in tools
        or (Path(skill_dir) / SETUP_PLAN_FILENAME).is_file()
        or (Path(skill_dir) / "setup.py").is_file()
    )


def publication_setup_evidence(
    skill_dir: Path,
    *,
    validation_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fail-closed authoring gate for setup-bearing skills.

    The plan is release evidence rather than executable authority. Runtime
    execution remains a separate approved operation after activation.
    """

    root = Path(skill_dir)
    manifest = _load_manifest(root)
    skill_id = str(manifest.get("id") or manifest.get("name") or root.name).strip()
    if not setup_is_required(root, manifest):
        return {
            "schema": "adaos.skill.setup_publication_evidence.v1",
            "status": "not_required",
            "skill_id": skill_id,
        }
    tools = manifest.get("tools") if isinstance(manifest.get("tools"), Mapping) else {}
    if "setup" not in tools:
        raise SetupPlanError("setup plan exists but skill.yaml does not own a setup tool")
    plan = load_setup_plan(root, skill_id=skill_id)
    evidence = dict(validation_evidence or {})
    setup_tests = evidence.get("setup_tests")
    if not isinstance(setup_tests, Mapping) or str(setup_tests.get("status") or "").lower() != "passed":
        raise SetupPlanError("setup-bearing skill requires passed setup_tests validation evidence")
    return {
        "schema": "adaos.skill.setup_publication_evidence.v1",
        "status": "passed",
        "skill_id": skill_id,
        "plan_id": plan["plan_id"],
        "plan_version": plan["version"],
        "plan_digest": _canonical_digest(plan),
        "setup_tool": "setup",
        "setup_tests": dict(setup_tests),
        "execution_policy": "separate_approved_post_activation_operation",
    }


@dataclass(frozen=True, slots=True)
class SetupExecutionRequest:
    skill_id: str
    release_digest: str
    plan_digest: str
    approval_id: str
    approved_by: str
    webspace_id: str
    dry_run: bool = False

    @property
    def idempotency_key(self) -> str:
        value = f"{self.skill_id}|{self.release_digest}|{self.plan_digest}|{self.webspace_id}|{int(self.dry_run)}"
        return f"setup:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def execute_via_skill_manager(request: SetupExecutionRequest, *, manager: Any) -> Any:
    """SDK/runtime adapter shared with the existing ``adaos skill setup`` entrypoint."""

    if not request.approval_id or not request.approved_by:
        raise SetupPlanError("approved setup execution requires approval identity")
    if request.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "skill_id": request.skill_id,
            "idempotency_key": request.idempotency_key,
        }
    return manager.setup_skill(request.skill_id)


__all__ = [
    "SETUP_PLAN_FILENAME",
    "SETUP_PLAN_SCHEMA",
    "SetupExecutionRequest",
    "SetupPlanError",
    "execute_via_skill_manager",
    "load_setup_plan",
    "publication_setup_evidence",
    "setup_is_required",
    "validate_setup_plan",
]
