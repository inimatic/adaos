from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Any

import yaml


READ_ONLY_SIDE_EFFECTS = frozenset({"safe", "none", "read", "read_only", "readonly"})
_LEGACY_SIDE_EFFECT_PERMISSIONS = {
    "safe": "workspace.read",
    "none": "workspace.read",
    "read": "workspace.read",
    "read_only": "workspace.read",
    "readonly": "workspace.read",
    "ui_navigation": "workspace.read",
    "local_write": "workspace.write",
    "runtime_write": "workspace.write",
}
_MANIFEST_CACHE_LOCK = threading.RLock()
_MANIFEST_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}
_MANIFEST_CACHE_MAX = 64
_IMMUTABLE_RUNTIME_MANIFEST_CACHE: dict[tuple[tuple[str, ...], str], tuple[Path, dict[str, Any]]] = {}
_IMMUTABLE_RUNTIME_MANIFEST_CACHE_MAX = 128


def _resolved_manifest_document(path: Path) -> dict[str, Any]:
    """Read one immutable runtime manifest once per exact file revision.

    A Management first paint invokes several tools from the same skill in
    parallel.  Re-reading and decoding that manifest in every worker used to
    serialize otherwise independent data sources.  The stat tuple is part of
    the key, so an A/B slot switch or in-place development rebuild cannot
    inherit a stale authorization contract.
    """

    resolved = path.resolve()
    stat = resolved.stat()
    key = (str(resolved), int(stat.st_mtime_ns), int(stat.st_size))
    with _MANIFEST_CACHE_LOCK:
        cached = _MANIFEST_CACHE.get(key)
        if cached is not None:
            return cached
        value = json.loads(resolved.read_text(encoding="utf-8"))
        manifest = dict(value) if isinstance(value, dict) else {}
        _MANIFEST_CACHE[key] = manifest
        while len(_MANIFEST_CACHE) > _MANIFEST_CACHE_MAX:
            _MANIFEST_CACHE.pop(next(iter(_MANIFEST_CACHE)))
        return manifest


def _resolved_runtime_manifest(
    manager: Any,
    *,
    skill_name: str,
    dev: bool,
) -> tuple[Path, dict[str, Any]]:
    """Resolve a runtime manifest without re-walking immutable Trial state.

    Native Trial managers are bound to a content-addressed Application release.
    Their authority tuple changes whenever either the release or component
    package changes, so it is a stronger cache key than repeatedly consulting
    the A/B marker.  Serialising the first lookup also collapses the page's
    concurrent data-source fan-out onto one status read.
    """

    raw_authority = getattr(manager, "_adaos_immutable_trial_authority", None)
    authority = tuple(str(item) for item in raw_authority) if isinstance(raw_authority, tuple) else ()
    cache_key = (authority, str(skill_name or "").strip())
    if authority and not dev:
        with _MANIFEST_CACHE_LOCK:
            cached = _IMMUTABLE_RUNTIME_MANIFEST_CACHE.get(cache_key)
            if cached is not None:
                return cached
            status = manager.runtime_status(skill_name)
            manifest_path = Path(str(status.get("resolved_manifest") or ""))
            manifest = _resolved_manifest_document(manifest_path)
            resolved = (manifest_path, manifest)
            _IMMUTABLE_RUNTIME_MANIFEST_CACHE[cache_key] = resolved
            while len(_IMMUTABLE_RUNTIME_MANIFEST_CACHE) > _IMMUTABLE_RUNTIME_MANIFEST_CACHE_MAX:
                _IMMUTABLE_RUNTIME_MANIFEST_CACHE.pop(
                    next(iter(_IMMUTABLE_RUNTIME_MANIFEST_CACHE))
                )
            return resolved
    status = manager.dev_runtime_status(skill_name) if dev else manager.runtime_status(skill_name)
    manifest_path = Path(str(status.get("resolved_manifest") or ""))
    return manifest_path, _resolved_manifest_document(manifest_path)


def normalize_side_effects(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def side_effects_are_read_only(value: Any) -> bool:
    return normalize_side_effects(value) in READ_ONLY_SIDE_EFFECTS


def _resolved_tool_spec(
    manager: Any,
    *,
    skill_name: str,
    public_tool: str,
    dev: bool,
) -> dict[str, Any]:
    try:
        _manifest_path, manifest = _resolved_runtime_manifest(
            manager,
            skill_name=skill_name,
            dev=dev,
        )
        tools = manifest.get("tools") if isinstance(manifest, dict) else {}
        spec = tools.get(public_tool) if isinstance(tools, dict) else {}
        if not isinstance(spec, dict):
            return {}
        result = dict(spec)
        permissions = result.get("permissions")
        has_permissions = bool(
            permissions
            and (
                not isinstance(permissions, dict)
                or permissions.get("required")
                or permissions.get("optional")
            )
        )
        if has_permissions:
            result["_permissions_source"] = "resolved_manifest"
            return result
        if isinstance(manifest, dict) and "capabilities" not in manifest:
            # Before capability declarations became part of the compiled
            # runtime contract, standardized workspace effects were the only
            # package-level authority available to the executor. Preserve that
            # narrow meaning for already installed exact slots. A manifest
            # produced by the current compiler always has ``capabilities``;
            # an explicit empty declaration therefore remains fail-closed.
            permission = _LEGACY_SIDE_EFFECT_PERMISSIONS.get(
                normalize_side_effects(
                    _governance_side_effects(result)
                )
            )
            if permission:
                result["permissions"] = [permission]
                result["_permissions_source"] = "legacy_side_effects"
        return result
    except Exception:
        return {}


def _governance_side_effects(spec: dict[str, Any]) -> Any:
    governance = (
        spec.get("yjs_governance")
        if isinstance(spec.get("yjs_governance"), dict)
        else {}
    )
    return (
        governance.get("side_effects")
        or spec.get("side_effects")
        or spec.get("sideEffects")
        or spec.get("effects")
        or ""
    )


def declared_skill_webui_owner(
    manager: Any,
    *,
    skill_name: str,
    dev: bool,
) -> str:
    """Return the trusted UI ownership mode from the active runtime manifest."""

    try:
        manifest_path, manifest = _resolved_runtime_manifest(
            manager,
            skill_name=skill_name,
            dev=dev,
        )
        if not isinstance(manifest, dict):
            return ""
        owner = str(manifest.get("webui_owner") or "").strip().lower()
        if owner:
            return owner

        # Resolved manifests created before ``webui_owner`` became part of the
        # runtime contract remain valid exact package slots after a core
        # update.  Recover only from the immutable source copied into that
        # same slot; never consult the mutable workspace source.  The
        # containment and skill-identity checks keep this compatibility read
        # inside the already selected runtime authority.
        slot_root = manifest_path.parent.resolve()
        source = Path(str(manifest.get("source") or "")).resolve()
        if not source.is_relative_to(slot_root):
            return ""
        source_manifest_path = source / "skill.yaml"
        source_manifest = yaml.safe_load(
            source_manifest_path.read_text(encoding="utf-8")
        )
        if not isinstance(source_manifest, dict):
            return ""
        if str(source_manifest.get("name") or "").strip() != skill_name:
            return ""
        return str(source_manifest.get("webui_owner") or "").strip().lower()
    except Exception:
        return ""


def _application_access(spec: dict[str, Any]) -> dict[str, str]:
    value = spec.get("application_access")
    if not isinstance(value, dict):
        return {}
    allowed = {"permission", "capability", "resource_argument", "provider_argument"}
    if set(value) - allowed:
        return {}
    return {
        key: str(value.get(key) or "").strip().lower()
        for key in sorted(allowed)
        if str(value.get(key) or "").strip()
    }


def declared_tool_contract(
    manager: Any,
    *,
    skill_name: str,
    public_tool: str,
    dev: bool,
) -> dict[str, Any]:
    """Read all trusted execution fields from one resolved-manifest snapshot.

    Authorization must not assemble one decision from several independently
    selected runtime revisions.  Reading the manifest once is both cheaper and
    gives the caller a coherent contract when an A/B slot changes concurrently.
    """

    spec = _resolved_tool_spec(
        manager,
        skill_name=skill_name,
        public_tool=public_tool,
        dev=dev,
    )
    raw_permissions = spec.get("permissions")
    permission_values: list[Any] = []
    if isinstance(raw_permissions, dict):
        for key in ("required", "optional"):
            candidate = raw_permissions.get(key)
            if isinstance(candidate, list):
                permission_values.extend(candidate)
    elif isinstance(raw_permissions, list):
        permission_values.extend(raw_permissions)
    approval_scope = spec.get("approval_scope")
    return {
        "side_effects": str(
            _governance_side_effects(spec)
        ).strip(),
        "approval_scope": dict(approval_scope) if isinstance(approval_scope, dict) else {},
        "permissions": tuple(
            sorted(
                {
                    str(item).strip().lower()
                    for item in permission_values
                    if str(item).strip()
                }
            )
        ),
        "application_access": _application_access(spec),
        "permissions_source": str(
            spec.get("_permissions_source") or "undeclared"
        ),
    }


def declared_tool_side_effects(
    manager: Any,
    *,
    skill_name: str,
    public_tool: str,
    dev: bool,
) -> str:
    """Read a tool's effects from the resolved manifest selected for execution.

    Callers must treat an empty result as undeclared, never as read-only.  The
    resolved manifest is the runtime authority; request payload metadata and
    method-name heuristics are deliberately excluded from this contract.
    """

    contract = declared_tool_contract(
        manager,
        skill_name=skill_name,
        public_tool=public_tool,
        dev=dev,
    )
    return str(contract.get("side_effects") or "")


def declared_tool_approval_scope(
    manager: Any,
    *,
    skill_name: str,
    public_tool: str,
    dev: bool,
) -> dict[str, Any]:
    """Return a trusted, manifest-declared reusable approval scope."""

    contract = declared_tool_contract(
        manager,
        skill_name=skill_name,
        public_tool=public_tool,
        dev=dev,
    )
    scope = contract.get("approval_scope")
    return dict(scope) if isinstance(scope, dict) else {}


def declared_tool_permissions(
    manager: Any,
    *,
    skill_name: str,
    public_tool: str,
    dev: bool,
) -> tuple[str, ...]:
    """Return normalized capability ids admitted by the resolved tool manifest."""

    contract = declared_tool_contract(
        manager,
        skill_name=skill_name,
        public_tool=public_tool,
        dev=dev,
    )
    raw = contract.get("permissions")
    return tuple(raw) if isinstance(raw, tuple) else ()


def declared_tool_application_access(
    manager: Any,
    *,
    skill_name: str,
    public_tool: str,
    dev: bool,
) -> dict[str, Any]:
    """Return a bounded Application permission/capability mapping from the manifest."""

    contract = declared_tool_contract(
        manager,
        skill_name=skill_name,
        public_tool=public_tool,
        dev=dev,
    )
    value = contract.get("application_access")
    return dict(value) if isinstance(value, dict) else {}


__all__ = [
    "READ_ONLY_SIDE_EFFECTS",
    "declared_tool_contract",
    "declared_tool_application_access",
    "declared_tool_approval_scope",
    "declared_tool_permissions",
    "declared_tool_side_effects",
    "declared_skill_webui_owner",
    "normalize_side_effects",
    "side_effects_are_read_only",
]
