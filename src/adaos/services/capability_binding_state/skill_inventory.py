"""Read-only inventory of installed skills for CBS migration planning.

The legacy ``skill.yaml.capabilities`` field is intentionally reported as
requested runtime capabilities.  It is *not* evidence that a skill provides a
portable :class:`CapabilityContract`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml
from packaging.version import InvalidVersion, Version

from adaos.domain.artifact_release import canonical_payload_digest


_PORTABLE_SCHEMAS = {
    "adaos.capability.contract.v1": "capability_contract",
    "adaos.state.contract.v1": "state_contract",
    "adaos.binding.definition.v1": "binding_definition",
    "adaos.binding.delivery.v1": "binding_delivery",
    "adaos.evidence.claim.v1": "evidence_claim",
}
_HANDOFF_MARKERS = (
    "prototype_acceptance",
    "cbs_compilation",
    "cbs_compilation_digest",
    "application.requirement.v1",
)


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(value, Mapping):
        raise ValueError(f"skill manifest must be an object: {path}")
    return dict(value)


def _skill_dirs(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return ()
    return (
        item
        for item in sorted(root.iterdir(), key=lambda value: value.name.casefold())
        if item.is_dir()
        and not item.name.startswith(".")
        and ((item / "skill.yaml").is_file() or (item / "skill.yml").is_file())
    )


def discover_dev_skills_root(dev_root: Path | None) -> Path | None:
    """Resolve either ``.adaos/dev/<node>/skills`` or an exact skills root."""

    if dev_root is None or not dev_root.is_dir():
        return None
    if any(True for _ in _skill_dirs(dev_root)):
        return dev_root
    candidates = sorted(dev_root.glob("*/skills"))
    return candidates[0] if len(candidates) == 1 else None


def _portable_artifacts(root: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {kind: [] for kind in _PORTABLE_SCHEMAS.values()}
    for path in sorted(root.rglob("*.json")):
        relative = path.relative_to(root)
        if any(part.startswith(".") or part in {"tests", "__pycache__"} for part in relative.parts):
            continue
        lowered = path.name.casefold()
        if not any(token in lowered for token in ("capability", "binding", "state", "evidence")):
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if not isinstance(item, Mapping):
                continue
            kind = _PORTABLE_SCHEMAS.get(str(item.get("schema") or ""))
            if kind and relative.as_posix() not in found[kind]:
                found[kind].append(relative.as_posix())
    return {key: value for key, value in found.items() if value}


def _handoff_markers(root: Path, manifest: Mapping[str, Any]) -> list[str]:
    haystacks = [json.dumps(manifest, sort_keys=True, ensure_ascii=False)]
    for relative in ("handlers/main.py", "workflow.json", "scenario.yaml"):
        path = root / relative
        try:
            if path.is_file() and path.stat().st_size <= 2_000_000:
                haystacks.append(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError):
            pass
    content = "\n".join(haystacks).casefold()
    return [marker for marker in _HANDOFF_MARKERS if marker in content]


def _version_relation(installed: str, development: str | None) -> str:
    if not development:
        return "no_development_overlay"
    try:
        current, candidate = Version(installed), Version(development)
    except InvalidVersion:
        return "uncomparable"
    if candidate > current:
        return "upgrade_available"
    if candidate < current:
        return "development_behind"
    return "same_version"


def _classify(
    *,
    name: str,
    tool_count: int,
    stateful: bool,
    artifacts: Mapping[str, list[str]],
    handoff_markers: list[str],
) -> tuple[str, str, str]:
    if artifacts.get("capability_contract") and artifacts.get("binding_definition"):
        return "cbs_native_provider", "none", "native"
    if handoff_markers:
        return (
            "builder_cbs_aware",
            "package portable contracts and conformance evidence",
            "high",
        )
    if tool_count and stateful:
        return (
            "stateful_legacy_provider_candidate",
            "extract StateContract/StateSpace and package-neutral BindingDefinition",
            "high",
        )
    if tool_count:
        return (
            "stateless_legacy_provider_candidate",
            "define a portable contract and binding only when the operation is reusable",
            "medium",
        )
    if any(token in name.casefold() for token in ("manager", "orchestrator", "dashboard")):
        return "consumer_or_orchestrator", "declare semantic requirements when adopted", "low"
    return "legacy_leaf_or_ui", "retain compatibility; migrate on semantic reuse", "low"


def inventory_installed_skills(
    installed_root: Path,
    *,
    dev_root: Path | None = None,
) -> dict[str, Any]:
    """Return a deterministic, read-only CBS migration inventory."""

    installed_root = Path(installed_root)
    development_root = discover_dev_skills_root(Path(dev_root) if dev_root else None)
    development: dict[str, Mapping[str, Any]] = {}
    if development_root:
        for root in _skill_dirs(development_root):
            manifest_path = root / ("skill.yaml" if (root / "skill.yaml").is_file() else "skill.yml")
            development[root.name] = _load_yaml(manifest_path)

    items: list[dict[str, Any]] = []
    for root in _skill_dirs(installed_root):
        manifest_path = root / ("skill.yaml" if (root / "skill.yaml").is_file() else "skill.yml")
        manifest = _load_yaml(manifest_path)
        name = str(manifest.get("name") or root.name)
        tools = manifest.get("tools") if isinstance(manifest.get("tools"), list) else []
        requested = manifest.get("capabilities")
        requested_capabilities = sorted(
            {str(value) for value in requested if str(value).strip()}
        ) if isinstance(requested, list) else []
        permissions = manifest.get("permissions")
        permissions_count = len(permissions) if isinstance(permissions, (list, Mapping)) else 0
        artifacts = _portable_artifacts(root)
        handoff_markers = _handoff_markers(root, manifest)
        state_signals = sorted(
            key
            for key in ("data_routes", "lifecycle", "memory_budget", "resources", "state")
            if manifest.get(key)
        )
        stateful = bool(state_signals) or any(
            token in path.name.casefold()
            for token in ("resource", "migration", "repository", "store")
            for path in root.glob(f"**/*{token}*")
            if path.is_file()
        )
        dev_manifest = development.get(root.name)
        dev_version = str(dev_manifest.get("version") or "") if dev_manifest else None
        migration_class, migration_action, priority = _classify(
            name=name,
            tool_count=len(tools),
            stateful=stateful,
            artifacts=artifacts,
            handoff_markers=handoff_markers,
        )
        items.append(
            {
                "skill": name,
                "directory": root.name,
                "installed_version": str(manifest.get("version") or ""),
                "development_version": dev_version or None,
                "development_relation": _version_relation(
                    str(manifest.get("version") or "0"), dev_version
                ),
                "tool_count": len(tools),
                "requested_runtime_capabilities": requested_capabilities,
                "permission_entry_count": permissions_count,
                "state_signals": state_signals,
                "builder_handoff_markers": handoff_markers,
                "portable_artifacts": artifacts,
                "migration_class": migration_class,
                "migration_priority": priority,
                "migration_action": migration_action,
            }
        )

    items.sort(key=lambda item: (str(item["skill"]).casefold(), str(item["directory"])))
    classes: dict[str, int] = {}
    for item in items:
        key = str(item["migration_class"])
        classes[key] = classes.get(key, 0) + 1
    body: dict[str, Any] = {
        "schema": "adaos.skill.cbs_inventory.v1",
        "semantics": {
            "legacy_capabilities_field": "requested_runtime_capabilities",
            "portable_provider_requires": [
                "adaos.capability.contract.v1",
                "adaos.binding.definition.v1",
            ],
        },
        "summary": {
            "installed_skills": len(items),
            "classes": dict(sorted(classes.items())),
            "upgrade_available": sum(
                item["development_relation"] == "upgrade_available" for item in items
            ),
            "native_provider_count": classes.get("cbs_native_provider", 0),
        },
        "skills": items,
    }
    return {**body, "inventory_digest": canonical_payload_digest(body)}


def render_skill_inventory_markdown(inventory: Mapping[str, Any]) -> str:
    """Render a compact review document from an inventory payload."""

    summary = inventory["summary"]
    lines = [
        "# Installed skill CBS inventory",
        "",
        f"Inventory digest: `{inventory['inventory_digest']}`.",
        "",
        (
            f"Installed skills: **{summary['installed_skills']}**; native CBS providers: "
            f"**{summary['native_provider_count']}**; development upgrades available: "
            f"**{summary['upgrade_available']}**."
        ),
        "",
        "> `skill.yaml.capabilities` is classified as requested runtime access. It is not a provided semantic capability.",
        "",
        "## Classes",
        "",
    ]
    for key, count in summary["classes"].items():
        lines.append(f"- `{key}`: {count}")
    lines.extend(["", "## Migration queue", ""])
    for item in inventory["skills"]:
        if item["migration_priority"] not in {"high", "medium"}:
            continue
        upgrade = (
            f"; dev `{item['development_version']}`"
            if item["development_relation"] == "upgrade_available"
            else ""
        )
        lines.append(
            f"- **{item['skill']}** `{item['installed_version']}`{upgrade} — "
            f"`{item['migration_class']}`: {item['migration_action']}."
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The inventory is a read-only migration aid. It does not modify manifests, infer provider semantics from runtime permissions, or make derived classifications authoritative.",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "discover_dev_skills_root",
    "inventory_installed_skills",
    "render_skill_inventory_markdown",
]
