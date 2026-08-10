from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from adaos.sdk.core.decorators import tool


def _descriptor() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "research.yaml"
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError("research.yaml must contain an object")
    return value


@tool(summary="Describe this research direction.", side_effects="none")
def describe_direction(**_: Any) -> dict[str, Any]:
    direction = _descriptor()
    implementation = dict(direction.get("implementation") or {})
    return {
        "ok": True,
        "direction": direction,
        "readiness": {
            "ready": implementation.get("state") == "ready" and not implementation.get("missing"),
            "state": implementation.get("state") or "pre_codex",
            "missing": list(implementation.get("missing") or []),
        },
    }


@tool(summary="Validate a ResearchPrototype against the direction boundary.", side_effects="none")
def validate_research_prototype(prototype: Mapping[str, Any], **_: Any) -> dict[str, Any]:
    value = dict(prototype or {})
    issues: list[dict[str, str]] = []
    required = ("title", "research_question", "hypotheses", "experimental_plan", "evaluation_plan")
    for key in required:
        if not value.get(key):
            issues.append({"code": f"prototype.missing.{key}", "message": f"{key} is required"})
    if value.get("schema") not in {None, "adaos.research.prototype.v1"}:
        issues.append({"code": "prototype.schema", "message": "unsupported ResearchPrototype schema"})
    return {"ok": not issues, "accepted": not issues, "issues": issues}


@tool(summary="Report experimental runner readiness.", side_effects="none")
def execution_readiness(**_: Any) -> dict[str, Any]:
    implementation = dict(_descriptor().get("implementation") or {})
    missing = list(implementation.get("missing") or [])
    ready = implementation.get("state") == "ready" and not missing
    return {"ok": True, "ready": ready, "state": implementation.get("state") or "pre_codex", "missing": missing}
