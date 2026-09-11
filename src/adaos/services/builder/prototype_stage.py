"""Stage boundaries shared by Prototype generation and Automation handoff."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any


PROTOTYPE_STAGE_CONTRACT = {
    "stage": "prototype",
    "acceptance": "interactive_preview_not_production_readiness",
    "executable_now": [
        "collection browsing, selection and detail disclosure",
        "supported local CRUD, search, filters and field validation",
        "representative states with visible, revision-bound fixture evidence",
    ],
    "automation_requirements": {
        "scope": "business_rule or external_integration only",
        "required": [
            "exact Brief job or residual requirement_ref with a prototype binding",
            "visible state or interaction illustrating the intended outcome",
            "en/ru disclosure of what is simulated or not enforced",
            "testable acceptance condition for Automation, including failure behavior",
        ],
        "not_allowed": "deferring supported UI interactions or claiming simulated rules are enforced",
    },
    "capability_gaps": "missing platform capability preventing the required prototype; remains a blocker",
}


def prototype_builder_metadata(webui: Mapping[str, Any]) -> dict[str, Any]:
    value: Any = webui
    for key in ("ui", "application", "desktop", "pageSchema", "meta", "builder"):
        if not isinstance(value, Mapping):
            return {}
        value = value.get(key)
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def prototype_automation_requirements(webui: Mapping[str, Any]) -> list[dict[str, Any]]:
    builder = prototype_builder_metadata(webui)
    return copy.deepcopy(builder.get("automation_requirements") or [])


def automation_acceptance_checks(acceptance: Mapping[str, Any]) -> list[str]:
    return [
        f"Implement and test {item['requirement_ref']}: {item['statement']}. "
        f"Acceptance: {item['acceptance']}. Prototype fixtures or disclosure alone do not pass."
        for item in acceptance.get("automation_requirements") or []
    ]


def automation_obligations(
    document: Mapping[str, Any], brief: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Preserve accepted requirements; the model cannot declare them implemented."""
    statements = {
        str(item.get("id")): str(item.get("statement") or "")
        for group in ("principal_jobs", "residual_requirements")
        for item in (brief or {}).get(group) or []
        if isinstance(item, Mapping)
    }
    bindings = {
        str(item["requirement_ref"]): copy.deepcopy(item["semantic_refs"])
        for item in document.get("requirement_bindings") or []
    }
    return [
        {
            **copy.deepcopy(dict(item)),
            "statement": statements.get(str(item["requirement_ref"]), str(item.get("statement") or "")),
            "prototype_refs": bindings.get(str(item["requirement_ref"]), []),
            "status": "pending_automation",
            "brief_ref": document.get("brief_ref"),
            "brief_digest": document.get("brief_digest"),
        }
        for item in document.get("automation_requirements") or []
    ]
