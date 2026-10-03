from __future__ import annotations

import hashlib
import json
import re
import base64
import binascii
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from adaos.build_info import BUILD_INFO
from adaos.sdk.core.exporter import export as sdk_export
from adaos.services.agent_context import get_ctx
from adaos.services.system_model import (
    CANONICAL_KIND_REGISTRY,
    CANONICAL_RELATION_REGISTRY,
)
from adaos.services.system_model.model import (
    CanonicalStatus,
    ConnectivityStatus,
    InstallationStatus,
    ResourcePressureStatus,
    SyncStatus,
    TrustStatus,
)
from adaos.services.ui_capabilities import ui_capability_catalog

from .policy import capability_registry_payload, capability_registry_summary
from .reports import control_report_registry_summary
from .sessions import DEFAULT_CAPABILITY_PROFILES, mcp_session_registry_summary
from .targets import managed_target_registry_summary
from .tokens import DEFAULT_ACCESS_TOKEN_CAPABILITIES, access_token_registry_summary


DESCRIPTOR_CACHE_CLASS_DEFAULTS: dict[str, dict[str, Any]] = {
    "sdk": {"ttl_seconds": 900, "stability": "experimental", "freshness": "fresh"},
    "vocabulary": {"ttl_seconds": 3600, "stability": "stable", "freshness": "fresh"},
    "schema": {"ttl_seconds": 3600, "stability": "stable", "freshness": "fresh"},
    "templates": {
        "ttl_seconds": 900,
        "stability": "experimental",
        "freshness": "fresh",
    },
    "architecture": {
        "ttl_seconds": 1800,
        "stability": "experimental",
        "freshness": "fresh",
    },
    "policy": {"ttl_seconds": 600, "stability": "experimental", "freshness": "fresh"},
    "client": {"ttl_seconds": 600, "stability": "experimental", "freshness": "fresh"},
    "auth": {"ttl_seconds": 300, "stability": "experimental", "freshness": "fresh"},
    "registry": {"ttl_seconds": 300, "stability": "experimental", "freshness": "fresh"},
    "build": {"ttl_seconds": 600, "stability": "experimental", "freshness": "fresh"},
    "bundle": {"ttl_seconds": 600, "stability": "experimental", "freshness": "fresh"},
}


def _plane_registry_payload() -> dict[str, Any]:
    return {
        "available": True,
        "kind": "mcp_plane_registry",
        "planes": [
            {
                "plane_id": "applications",
                "title": "ApplicationsPlane",
                "enabled": True,
                "surface": "operations",
                "mode": "typed_application_lifecycle_adapter",
                "published_by": "root",
                "preferred_for": ["applications", "builder", "application_operations"],
                "descriptor_ids": ["sdk_metadata", "application_contracts"],
                "tool_prefixes": ["applications."],
                "capability_profiles": ["ApplicationsOperator"],
                "backing_store": "Application Core + Artifact Pipeline + ProjectDeployment",
            },
            {
                "plane_id": "adaos_dev",
                "title": "AdaOSDevPlane",
                "enabled": True,
                "surface": "development",
                "mode": "typed_descriptive_plane",
                "published_by": "root",
                "preferred_for": ["builder", "authoring", "architecture_assistance"],
                "descriptor_ids": [
                    "architecture_catalog",
                    "sdk_metadata",
                    "template_catalog",
                    "public_skill_registry_summary",
                    "public_scenario_registry_summary",
                    "named_entity_registry",
                ],
                "tool_prefixes": ["adaos_dev.", "builder.", "dev_ticket."],
                "capability_profiles": ["BuilderDeveloper"],
                "backing_store": "root_descriptor_cache",
            },
            {
                "plane_id": "profile_ops",
                "title": "ProfileOpsPlane",
                "enabled": True,
                "surface": "operations",
                "mode": "typed_operational_plane",
                "published_by": "root",
                "preferred_for": [
                    "profiler_inspection",
                    "profiler_control",
                    "operator_workflows",
                ],
                "descriptor_ids": ["capability_profiles", "mcp_session_profile"],
                "tool_prefixes": ["hub.memory."],
                "capability_profiles": ["ProfileOpsRead", "ProfileOpsControl"],
                "backing_store": "root_descriptor_cache + supervisor_authority",
            },
            {
                "plane_id": "context_control",
                "title": "ContextControlPlane",
                "enabled": True,
                "surface": "development",
                "mode": "typed_context_graph_adapter",
                "published_by": "root",
                "preferred_for": [
                    "builder",
                    "codex",
                    "evaluator",
                    "context_inspection",
                ],
                "descriptor_ids": [
                    "context_capsule_schema",
                    "context_relationship_schema",
                    "context_subject_binding_schema",
                    "context_plan_schema",
                    "context_delta_schema",
                    "context_receipt_schema",
                    "context_memory_candidate_schema",
                    "descriptor_overview_row_schema",
                ],
                "tool_prefixes": ["context."],
                "capability_profiles": ["ContextAgent"],
                "backing_store": "context_control relational registry + content-addressed artifacts",
            },
            {
                "plane_id": "skill_factory_task",
                "title": "SkillFactoryTaskPlane",
                "enabled": True,
                "surface": "development",
                "mode": "typed_development_task_plane",
                "published_by": "root",
                "preferred_for": [
                    "builder_realization",
                    "isolated_dev_nodes",
                    "task_queue_diagnostics",
                ],
                "descriptor_ids": [
                    "builder_realize_request_schema",
                    "skill_factory_dev_node_registration_schema",
                    "skill_factory_dev_task_assignment_schema",
                    "skill_factory_dev_result_schema",
                    "skill_factory_dev_ready_event_schema",
                    "skill_factory_dev_task_failure_schema",
                    "skill_factory_status",
                ],
                "tool_prefixes": ["skill_factory."],
                "capability_profiles": [
                    "SkillFactoryTaskRead",
                    "SkillFactoryTaskSubmit",
                    "SkillFactoryDevNode",
                ],
                "backing_store": "root_descriptor_cache + skill_factory_state",
            },
            {
                "plane_id": "nlu_authoring",
                "title": "NLUAuthoringPlane",
                "enabled": True,
                "surface": "development",
                "mode": "typed_authoring_context",
                "published_by": "root",
                "preferred_for": [
                    "nlu_teacher",
                    "entity_canonicalization",
                    "llm_authoring",
                ],
                "descriptor_ids": ["named_entity_registry", "nlu_teacher_schema"],
                "tool_prefixes": ["nlu_authoring."],
                "capability_profiles": [
                    "NLUTeacherRead",
                    "NLUTeacherDryRun",
                    "NLUTeacherAuthor",
                ],
                "backing_store": "root_descriptor_cache + named_entity_read_model + governed_access_links",
            },
        ],
    }


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _with_ttl(issued_at: str, ttl_seconds: int) -> str:
    base = datetime.fromisoformat(issued_at)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return (
        (base + timedelta(seconds=max(1, int(ttl_seconds))))
        .replace(microsecond=0)
        .isoformat()
    )


def _json_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _stable_content(value: Any) -> Any:
    """Remove delivery timestamps before deriving a reusable content identity."""

    if isinstance(value, dict):
        return {
            str(key): _stable_content(item)
            for key, item in value.items()
            if str(key) not in {"generated_at", "fresh_until", "issued_at"}
        }
    if isinstance(value, list):
        return [_stable_content(item) for item in value]
    return value


def _content_digest(value: Any) -> str:
    return f"sha256:{_json_hash(_stable_content(value))}"


def _encode_catalog_cursor(*, offset: int, scope_digest: str) -> str:
    payload = json.dumps(
        {"offset": max(0, int(offset)), "scope_digest": scope_digest},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_catalog_cursor(cursor: str | None, *, scope_digest: str) -> int:
    token = str(cursor or "").strip()
    if not token:
        return 0
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        offset = int(payload["offset"])
    except (
        binascii.Error,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("invalid catalog cursor") from exc
    if str(payload.get("scope_digest") or "") != scope_digest or offset < 0:
        raise ValueError("catalog cursor does not match this query")
    return offset


def _overview_row(
    *,
    row_id: str,
    kind: str,
    title: str,
    summary: str | None,
    stability: str,
    descriptor_id: str,
    version: str | None = None,
    side_effects: str | None = None,
    owner: str | None = None,
    schema_id: str | None = None,
    required_args: Any = None,
    capabilities: Any = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    identity = {
        "row_id": row_id,
        "kind": kind,
        "title": title,
        "summary": summary,
        "version": version,
        "stability": stability,
        "side_effects": side_effects,
        "owner": owner,
        "schema_id": schema_id,
        "required_args_digest": f"sha256:{_json_hash(required_args)}"
        if required_args is not None
        else None,
        "capability_digest": f"sha256:{_json_hash(capabilities)}"
        if capabilities is not None
        else None,
    }
    fingerprint = f"sha256:{_json_hash(identity)}"
    return {
        "schema": "adaos.descriptor.overview_row.v1",
        **identity,
        "fingerprint": fingerprint,
        "freshness": {"state": "descriptor_bound"},
        "drill_down": {
            "descriptor_id": descriptor_id,
            "item_id": row_id,
            "content_hash": fingerprint,
        },
        "metadata": dict(metadata or {}),
    }


def _sdk_metadata(
    level: str,
    *,
    query: str | None = None,
    limit: int = 24,
    cursor: str | None = None,
    purpose: str = "authoring",
) -> dict[str, Any]:
    effective_purpose = "migration" if str(purpose or "").strip().lower() == "migration" else "authoring"
    payload = dict(
        sdk_export(
            level=level,
            query=query,
            # Selection is bounded before the requested page is projected.
            # The exporter remains the single complete build-time source.
            limit=64 if query else limit,
            include_deprecated=effective_purpose == "migration",
        )
    )
    raw_items = (
        payload.get("items")
        if isinstance(payload.get("items"), list)
        else payload.get("tools")
    )
    all_items = [item for item in raw_items or [] if isinstance(item, dict)]
    bounded_limit = max(1, min(int(limit or 24), 64))
    scope_digest = _content_digest(
        {
            "descriptor": "sdk_metadata",
            "level": level,
            "query": str(query or "").strip().casefold(),
            "purpose": effective_purpose,
        }
    )
    offset = _decode_catalog_cursor(cursor, scope_digest=scope_digest)
    selected_items = all_items[offset : offset + bounded_limit]
    has_more = offset + len(selected_items) < len(all_items)
    if isinstance(payload.get("items"), list):
        payload["items"] = selected_items
    elif isinstance(payload.get("tools"), list):
        payload["tools"] = selected_items
    rows: list[dict[str, Any]] = []
    for item in selected_items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("n") or item.get("name") or "").strip()
        if not name:
            continue
        meta = (
            dict(item.get("meta") or {}) if isinstance(item.get("meta"), dict) else {}
        )
        contract = dict(item.get("contract") or {}) if isinstance(item.get("contract"), dict) else {}
        input_schema = (
            dict(item.get("input_schema") or {})
            if isinstance(item.get("input_schema"), dict)
            else {}
        )
        row = _overview_row(
            row_id=name,
            kind=str(item.get("k") or item.get("kind") or "sdk_method"),
            title=name,
            summary=str(item.get("s") or item.get("summary") or "").strip() or None,
            stability=str(item.get("st") or meta.get("stability") or "experimental"),
            descriptor_id="sdk_metadata",
            version=str(meta.get("version") or "").strip() or None,
            side_effects=str(meta.get("side_effects") or "").strip() or None,
            owner=str(item.get("m") or item.get("module") or "adaos.sdk").strip(),
            schema_id=f"sdk:{name}:input",
            required_args=input_schema.get("required") or item.get("a") or [],
            capabilities={
                "approval_scope": meta.get("approval_scope"),
                "idempotent": meta.get("idempotent"),
                "permissions": contract.get("permissions") or [],
                "effects": contract.get("effects") or [],
                "boundedness": contract.get("boundedness") or item.get("boundedness"),
                "pagination": contract.get("pagination") or item.get("pagination"),
            },
            metadata={
                "module": item.get("m") or item.get("module"),
                "qualname": item.get("qualname"),
                "args": list(item.get("a") or []),
                "contract": contract,
            },
        )
        if level == "mini":
            row = {
                key: value
                for key, value in row.items()
                if key
                in {
                    "schema",
                    "row_id",
                    "kind",
                    "title",
                    "summary",
                    "stability",
                    "owner",
                    "fingerprint",
                    "drill_down",
                    "metadata",
                }
                and value not in (None, "", [], {})
            }
            drill_down = dict(row.get("drill_down") or {})
            drill_down.pop("content_hash", None)
            row["drill_down"] = drill_down
        rows.append(row)
    payload["overview_schema"] = "adaos.descriptor.overview_row.v1"
    payload["overview_rows"] = rows
    payload["count"] = len(selected_items)
    payload["total_count"] = len(all_items)
    payload["limit"] = bounded_limit
    payload["offset"] = offset
    payload["has_more"] = has_more
    payload["next_cursor"] = (
        _encode_catalog_cursor(
            offset=offset + len(selected_items), scope_digest=scope_digest
        )
        if has_more
        else None
    )
    payload["purpose"] = effective_purpose
    payload["deprecated_included"] = effective_purpose == "migration"
    if level == "mini":
        # Mini is the authoritative model discovery projection. Avoid sending
        # the same rows twice as exporter items and descriptor overview rows.
        payload.pop("items", None)
    return payload


def _descriptor_cache_state_path() -> Path:
    ctx = get_ctx()
    path = Path(ctx.paths.root_mcp_state_dir()) / "descriptor_cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_descriptor_cache_state() -> dict[str, Any]:
    return _load_json(_descriptor_cache_state_path())


def _write_descriptor_cache_state(payload: dict[str, Any]) -> None:
    _descriptor_cache_state_path().write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def record_descriptor_refresh(
    *,
    reason: str,
    descriptor_ids: list[str],
    source_kind: str,
    artifact_kind: str | None = None,
    artifact_name: str | None = None,
) -> dict[str, Any]:
    now = _iso_now()
    current = _read_descriptor_cache_state()
    refresh_count = max(0, int(current.get("refresh_count") or 0)) + 1
    payload = {
        "enabled": True,
        "cache_mode": "root_descriptor_cache",
        "updated_at": now,
        "refresh_count": refresh_count,
        "last_refresh": {
            "at": now,
            "reason": str(reason or "").strip() or "manual",
            "source_kind": str(source_kind or "").strip() or "unknown",
            "artifact_kind": str(artifact_kind or "").strip() or None,
            "artifact_name": str(artifact_name or "").strip() or None,
            "descriptor_ids": [
                str(item).strip() for item in descriptor_ids if str(item).strip()
            ],
        },
    }
    _write_descriptor_cache_state(payload)
    return payload


def descriptor_cache_summary() -> dict[str, Any]:
    current = _read_descriptor_cache_state()
    return {
        "enabled": True,
        "cache_mode": "root_descriptor_cache",
        "state_path": str(_descriptor_cache_state_path()),
        "refresh_count": int(current.get("refresh_count") or 0),
        "updated_at": current.get("updated_at"),
        "last_refresh": dict(current.get("last_refresh") or {})
        if isinstance(current.get("last_refresh"), dict)
        else None,
    }


def _package_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _skill_manifest_schema() -> dict[str, Any]:
    return _load_json(_package_root() / "services" / "skill" / "skill_schema.json")


def _scenario_manifest_schema() -> dict[str, Any]:
    return _load_json(_package_root() / "abi" / "scenario.schema.json")


def _builder_task_schema() -> dict[str, Any]:
    return _load_json(_package_root() / "abi" / "builder.task.v1.schema.json")


def _builder_draft_schema() -> dict[str, Any]:
    return _load_json(_package_root() / "abi" / "builder.draft.v1.schema.json")


def _builder_realize_request_schema() -> dict[str, Any]:
    return _load_json(
        _package_root() / "abi" / "builder.realize_request.v1.schema.json"
    )


def _skill_factory_schema(name: str) -> dict[str, Any]:
    return _load_json(_package_root() / "abi" / name)


def _application_contracts() -> dict[str, Any]:
    names = (
        "application.v1.schema.json",
        "application.release.v1.schema.json",
        "application.installation.v1.schema.json",
        "application.subscription.v1.schema.json",
        "application.runtime-selection.v1.schema.json",
        "application.trial-access-grant.v1.schema.json",
        "application.operation.v1.schema.json",
        "application.prerelease-rollout.v1.schema.json",
        "application.permission-profile.v1.schema.json",
        "application.access-grant.v1.schema.json",
        "application.access-decision.v1.schema.json",
        "application.access-profile-diff.v1.schema.json",
        "application.verification-report.v1.schema.json",
        "application.release-evidence-bundle.v1.schema.json",
        "application.setup_contract.v1.schema.json",
        "application.setup_state.v1.schema.json",
    )

    # Publish the executable Root MCP surface with the ABI bundle.  Schemas
    # alone prove record shape, but they do not tell an Automation worker which
    # public operation owns a read or mutation.  Keep this projection compact:
    # every operation is derived from the registered contract and deliberately
    # omits the common response envelope and internal handler metadata.
    from .applications_plane import contracts as application_tool_contracts

    tools = {
        contract.id: {
            "tool_id": contract.id,
            "title": contract.title,
            "summary": contract.summary,
            "input_schema": contract.input_schema,
            "required_capability": contract.required_capability,
            "side_effects": contract.side_effects,
            "stability": contract.stability,
        }
        for contract in application_tool_contracts()
    }
    group_specs = (
        (
            "catalog_and_detail",
            "Bounded catalog, selected Application, release, operation, report, component and placement reads.",
            {
                "applications.list",
                "applications.show",
                "applications.list_components",
                "applications.list_placements",
                "applications.list_releases",
                "applications.list_operations",
                "applications.poll_operation_events",
                "applications.get_operation",
                "applications.list_development_reports",
                "applications.get_development_report_status",
            },
        ),
        (
            "lifecycle",
            "Reviewable install, update, remove and track plans; exact digest apply; direct settings; Home pinning; update batches.",
            {
                "applications.assess_updates",
                "applications.plan_updates",
                "applications.get_update_batch",
                "applications.apply_updates",
                "applications.list_home_targets",
                "applications.set_home_pin",
                "applications.set_home_pins",
                "applications.reorder_home",
                "applications.update_settings",
                "applications.plan",
                "applications.apply",
                "applications.explain_plan",
            },
        ),
        (
            "access",
            "Release-bound permissions, roles, grants, connected accounts, privacy, readiness, simulation and audit activity.",
            {
                tool_id
                for tool_id in tools
                if tool_id.startswith("applications.access.")
            },
        ),
        (
            "setup_and_placement",
            "Release-owned setup state, typed settings, write-only credential handoff and desired/observed placement.",
            {
                tool_id
                for tool_id in tools
                if tool_id.startswith("applications.setup.")
                or tool_id
                in {
                    "applications.list_components",
                    "applications.list_placements",
                }
            },
        ),
        (
            "builder_lifecycle",
            "Publisher-local development, Prototype/Automation evidence, Trial beta and Stable publication operations.",
            {
                tool_id
                for tool_id in tools
                if tool_id.startswith("applications.development.")
            },
        ),
        (
            "trial_and_prerelease",
            "Targeted Trial access plus prerelease rollout, health and reviewed install operations.",
            {
                "applications.issue_trial_access",
                "applications.revoke_trial_access",
                "applications.resolve_trial_link",
                "applications.plan_trial_link_install",
                "applications.get_prerelease_rollout",
                "applications.set_prerelease_rollout",
                "applications.record_prerelease_health",
            },
        ),
    )
    operation_groups = []
    for group_id, summary, selected_ids in group_specs:
        selected = [
            tools[tool_id] for tool_id in sorted(selected_ids) if tool_id in tools
        ]
        operation_groups.append(
            {
                "group_id": group_id,
                "title": group_id.replace("_", " ").title(),
                "summary": summary,
                "tool_ids": [item["tool_id"] for item in selected],
                "capabilities": sorted(
                    {
                        str(item["required_capability"])
                        for item in selected
                        if item.get("required_capability")
                    }
                ),
                "tools": selected,
            }
        )
    return {
        "schema": "adaos.application.contract_bundle.v1",
        "schemas": {name: _skill_factory_schema(name) for name in names},
        "operation_groups": operation_groups,
        "error_contract": {
            "conflict": "A stale expected_revision or changed exact plan fails closed; refresh authoritative state before retry.",
            "idempotency": "Mutating tools that declare idempotency_key replay only the same admitted request.",
            "authority": "Only the public SDK and Root MCP tool contracts are runtime authority; local fixtures are never a fallback.",
        },
    }


def _skill_factory_status() -> dict[str, Any]:
    try:
        from adaos.services.skill_factory import SkillFactoryService

        return SkillFactoryService().snapshot(include_tasks=False)
    except Exception as exc:
        return {
            "ok": False,
            "available": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _nlu_teacher_schema() -> dict[str, Any]:
    return _load_json(_package_root() / "abi" / "nlu.teacher.v1.schema.json")


def _status_vocab() -> dict[str, list[str]]:
    return {
        "operational": [item.value for item in CanonicalStatus],
        "connectivity": [item.value for item in ConnectivityStatus],
        "trust": [item.value for item in TrustStatus],
        "resource_pressure": [item.value for item in ResourcePressureStatus],
        "sync": [item.value for item in SyncStatus],
        "installation": [item.value for item in InstallationStatus],
    }


def _template_names(raw: Any) -> list[str]:
    try:
        path = Path(raw() if callable(raw) else raw)
    except Exception:
        return []
    if not path.exists() or not path.is_dir():
        return []
    return sorted(
        item.name
        for item in path.iterdir()
        if item.is_dir() and not item.name.startswith(".")
    )


def _template_catalog() -> dict[str, Any]:
    ctx = get_ctx()
    return {
        "skills": _template_names(getattr(ctx.paths, "skill_templates_dir", None)),
        "scenarios": _template_names(
            getattr(ctx.paths, "scenario_templates_dir", None)
        ),
    }


def _workspace_registry_path() -> Path:
    ctx = get_ctx()
    return Path(ctx.paths.workspace_dir()) / "registry.json"


def _workspace_registry() -> dict[str, Any]:
    return _load_json(_workspace_registry_path())


def _registry_entries(kind: str) -> list[dict[str, Any]]:
    registry = _workspace_registry()
    items = registry.get(kind) if isinstance(registry.get(kind), list) else []
    return [dict(item) for item in items if isinstance(item, dict)]


def _registry_manifest(item: dict[str, Any]) -> dict[str, Any]:
    manifest_ref = str(item.get("manifest") or "").strip()
    if not manifest_ref:
        return {}
    path = Path(manifest_ref)
    if not path.is_absolute():
        path = _workspace_registry_path().parent / path
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(loaded) if isinstance(loaded, dict) else {}


def _registry_capabilities(item: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for source in (item.get("capabilities"), manifest.get("capabilities")):
        if isinstance(source, list):
            values.extend(str(value).strip() for value in source if str(value).strip())
    if item.get("tools_count") or manifest.get("tools"):
        values.append("tools")
    if manifest.get("subscriptions") or manifest.get("emits"):
        values.append("events")
    if manifest.get("nlu") or item.get("nlu") or item.get("hints"):
        values.append("nlu")
    return sorted(set(values))


def _public_registry_index_entry(kind: str, item: dict[str, Any]) -> dict[str, Any]:
    manifest = _registry_manifest(item)
    item_id = str(item.get("id") or item.get("name") or "").strip()
    version = str(item.get("version") or manifest.get("version") or "").strip() or None
    stability = str(
        item.get("stability")
        or manifest.get("stability")
        or manifest.get("stage")
        or "published"
    ).strip()
    capabilities = _registry_capabilities(item, manifest)
    digest = _content_digest(
        {
            "id": item_id,
            "version": version,
            "stability": stability,
            "capabilities": capabilities,
            "manifest": manifest,
        }
    )
    return {
        "id": item_id,
        "version": version,
        "stability": stability,
        "capabilities": capabilities,
        "digest": digest,
    }


def public_registry_item(kind: str, item_id: str) -> dict[str, Any]:
    token = str(kind or "").strip().lower()
    selected = next(
        (
            item
            for item in _registry_entries(token)
            if str(item.get("id") or item.get("name") or "").strip() == str(item_id or "").strip()
        ),
        None,
    )
    if selected is None:
        raise KeyError(item_id)
    manifest = _registry_manifest(selected)
    index = _public_registry_index_entry(token, selected)
    tools = [dict(item) for item in manifest.get("tools") or [] if isinstance(item, dict)]
    schemas = {
        str(tool.get("name") or ""): {
            "input": tool.get("input_schema") or {},
            "output": tool.get("output_schema") or {},
        }
        for tool in tools
        if str(tool.get("name") or "").strip()
    }
    examples = {
        str(tool.get("name") or ""): list(tool.get("examples") or [])
        for tool in tools
        if str(tool.get("name") or "").strip() and tool.get("examples")
    }
    manifest_ref = str(selected.get("manifest") or "").strip() or None
    source_ref = str(selected.get("path") or "").strip()
    readme_ref = f"{source_ref.rstrip('/')}/README.md" if source_ref else None
    return {
        **index,
        "schema": "adaos.public_registry.item.v1",
        "kind": token[:-1],
        "name": str(selected.get("name") or index["id"]),
        "schemas": schemas,
        "examples": examples,
        "documentation": {
            "summary": str(selected.get("description") or manifest.get("description") or "").strip() or None,
            "manifest_ref": manifest_ref,
            "readme_ref": readme_ref,
        },
    }


def _public_registry_summary(
    kind: str,
    *,
    query: str | None = None,
    limit: int = 24,
    cursor: str | None = None,
) -> dict[str, Any]:
    token = str(kind or "").strip().lower()
    source_items = _registry_entries(token)
    indexed = [_public_registry_index_entry(token, item) for item in source_items]
    terms = [part.casefold() for part in re.findall(r"\w+", str(query or "")) if len(part) >= 2]
    if terms:
        by_id = {
            str(item.get("id") or item.get("name") or "").strip(): item
            for item in source_items
        }
        scored: list[tuple[int, dict[str, Any]]] = []
        for entry in indexed:
            source = by_id.get(str(entry.get("id") or ""), {})
            haystack = " ".join(
                (
                    str(entry.get("id") or ""),
                    str(source.get("description") or ""),
                    " ".join(entry.get("capabilities") or []),
                )
            ).casefold()
            score = sum(40 if term == str(entry.get("id") or "").casefold() else 5 for term in terms if term in haystack)
            if score:
                scored.append((score, entry))
        indexed = [entry for _score, entry in sorted(scored, key=lambda row: (-row[0], str(row[1]["id"])))]
    else:
        indexed.sort(key=lambda item: str(item.get("id") or ""))
    bounded_limit = max(1, min(int(limit or 24), 64))
    scope_digest = _content_digest({"kind": token, "query": str(query or "").strip().casefold()})
    offset = _decode_catalog_cursor(cursor, scope_digest=scope_digest)
    page = indexed[offset : offset + bounded_limit]
    has_more = offset + len(page) < len(indexed)
    payload_digest = _content_digest(indexed)
    return {
        "schema": "adaos.public_registry.index.v1",
        "kind": token,
        "available": True,
        "item_count": len(indexed),
        "count": len(page),
        "limit": bounded_limit,
        "offset": offset,
        "has_more": has_more,
        "next_cursor": _encode_catalog_cursor(offset=offset + len(page), scope_digest=scope_digest) if has_more else None,
        "digest": payload_digest,
        "etag": payload_digest,
        "detail_request": {
            "tool": "development.get_descriptor_item",
            "descriptor_id": f"public_{token[:-1]}_registry_summary",
        },
        "items": page,
    }


def _architecture_catalog(
    *,
    query: str | None = None,
    limit: int = 24,
    cursor: str | None = None,
    roots: list[str] | None = None,
    depth: int = 1,
) -> dict[str, Any]:
    repository_root = Path(__file__).resolve().parents[4]
    path = repository_root / "docs" / "architecture" / "index.md"
    pages: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        text = ""
    pattern = re.compile(
        r"^- \[(?P<title>[^\]]+)\]\((?P<link>[^\)]+)\):\s*(?P<summary>.+)$",
        re.MULTILINE,
    )
    for match in pattern.finditer(text):
        pages.append(
            {
                "title": match.group("title").strip(),
                "path": match.group("link").strip(),
                "summary": match.group("summary").strip(),
            }
        )
    if not pages:
        architecture_root = next(
            (
                candidate
                for candidate in (
                    repository_root / "docs-development" / "platform" / "architecture",
                    repository_root / "docs-stable" / "platform" / "architecture",
                )
                if candidate.is_dir()
            ),
            None,
        )
        if architecture_root is not None:
            path = architecture_root
            for document in sorted(architecture_root.glob("*.md"))[:256]:
                try:
                    document_text = document.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    continue
                heading = re.search(r"^#\s+(.+)$", document_text, re.MULTILINE)
                paragraphs = [
                    " ".join(part.split())
                    for part in re.split(r"\n\s*\n", document_text)
                    if part.strip() and not part.lstrip().startswith(("#", "---"))
                ]
                pages.append(
                    {
                        "title": heading.group(1).strip() if heading else document.stem,
                        "path": document.relative_to(repository_root).as_posix(),
                        "summary": (paragraphs[0][:237] + "...")
                        if paragraphs and len(paragraphs[0]) > 240
                        else paragraphs[0]
                        if paragraphs
                        else "AdaOS architecture document.",
                    }
                )
    nodes = [
        {
            "id": str(page.get("path") or page.get("title") or "").strip(),
            "type": "architecture.document",
            "title": str(page.get("title") or "").strip(),
            "summary": str(page.get("summary") or "").strip(),
        }
        for page in pages
        if str(page.get("path") or page.get("title") or "").strip()
    ]
    by_id = {node["id"]: node for node in nodes}
    for node in nodes:
        node["digest"] = _content_digest(node)
    edges: list[dict[str, Any]] = []
    repository_root = Path(__file__).resolve().parents[4]
    for node in nodes:
        document = repository_root / node["id"]
        try:
            document_text = document.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for raw_target in re.findall(r"\[[^\]]+\]\(([^\)#]+\.md)(?:#[^\)]*)?\)", document_text):
            candidate = (document.parent / raw_target).resolve()
            try:
                target_id = candidate.relative_to(repository_root.resolve()).as_posix()
            except ValueError:
                continue
            if target_id not in by_id:
                continue
            edge = {"source": node["id"], "target": target_id, "type": "references"}
            edge["digest"] = _content_digest(edge)
            edges.append(edge)
    selected_ids = {str(value).strip() for value in roots or [] if str(value).strip() in by_id}
    terms = [part.casefold() for part in re.findall(r"\w+", str(query or "")) if len(part) >= 2]
    if terms and not selected_ids:
        ranked = sorted(
            (
                (
                    sum(
                        40 if term == node["id"].casefold() else 8 if term in node["title"].casefold() else 2
                        for term in terms
                        if term in f"{node['id']} {node['title']} {node['summary']}".casefold()
                    ),
                    node["id"],
                )
                for node in nodes
            ),
            key=lambda row: (-row[0], row[1]),
        )
        selected_ids = {node_id for score, node_id in ranked if score > 0}
    if selected_ids:
        frontier = set(selected_ids)
        allowed = set(selected_ids)
        for _ in range(max(0, min(int(depth or 0), 3))):
            neighbors = {
                endpoint
                for edge in edges
                if edge["source"] in frontier or edge["target"] in frontier
                for endpoint in (edge["source"], edge["target"])
            }
            frontier = neighbors - allowed
            allowed.update(neighbors)
        nodes = [node for node in nodes if node["id"] in allowed]
        edges = [edge for edge in edges if edge["source"] in allowed and edge["target"] in allowed]
    nodes.sort(key=lambda node: node["id"])
    bounded_limit = max(1, min(int(limit or 24), 64))
    scope_digest = _content_digest({"query": query or "", "roots": sorted(selected_ids), "depth": depth})
    offset = _decode_catalog_cursor(cursor, scope_digest=scope_digest)
    page_nodes = nodes[offset : offset + bounded_limit]
    page_ids = {node["id"] for node in page_nodes}
    # Never let a page pull the full graph back in through high-degree boundary
    # edges. A caller asks for the next node page or a rooted neighborhood.
    page_edges = [
        edge
        for edge in edges
        if edge["source"] in page_ids and edge["target"] in page_ids
    ]
    has_more = offset + len(page_nodes) < len(nodes)
    graph_digest = _content_digest({"nodes": nodes, "edges": edges})
    return {
        "schema": "adaos.architecture.graph.v1",
        "available": True,
        "index_path": str(path),
        "page_count": len(nodes),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "count": len(page_nodes),
        "limit": bounded_limit,
        "offset": offset,
        "has_more": has_more,
        "next_cursor": _encode_catalog_cursor(offset=offset + len(page_nodes), scope_digest=scope_digest) if has_more else None,
        "digest": graph_digest,
        "etag": graph_digest,
        "nodes": page_nodes,
        "edges": page_edges,
    }


def _descriptor_build_profile() -> dict[str, Any]:
    sdk_meta = dict(sdk_export(level="mini").get("meta") or {})
    return {
        "available": True,
        "build_pipeline": "prototype",
        "generator": "adaos.sdk.core.exporter.export",
        "lifecycle_hooks": {
            "publish_refresh_enabled": True,
            "cache_state": descriptor_cache_summary(),
        },
        "input_sources": [
            "adaos.sdk.manage",
            "adaos.sdk.data",
            "docs/architecture/index.md",
            ".adaos/workspace/registry.json",
        ],
        "published_descriptor_ids": [
            "sdk_metadata",
            "ui_capability_catalog",
            "system_model_vocabulary",
            "skill_manifest_schema",
            "scenario_manifest_schema",
            "builder_task_schema",
            "builder_draft_schema",
            "builder_realize_request_schema",
            "skill_factory_dev_node_registration_schema",
            "skill_factory_dev_task_assignment_schema",
            "skill_factory_dev_result_schema",
            "skill_factory_dev_ready_event_schema",
            "skill_factory_dev_task_failure_schema",
            "skill_factory_status",
            "nlu_teacher_schema",
            "descriptor_overview_row_schema",
            "context_capsule_schema",
            "context_relationship_schema",
            "context_subject_binding_schema",
            "context_plan_schema",
            "context_delta_schema",
            "context_receipt_schema",
            "context_memory_candidate_schema",
            "template_catalog",
            "architecture_catalog",
            "public_skill_registry_summary",
            "public_scenario_registry_summary",
            "named_entity_registry",
            "descriptor_bundle",
        ],
        "sdk_export_meta": sdk_meta,
    }


def _client_profile() -> dict[str, Any]:
    return {
        "recommended_client": "RootMcpClient",
        "connection": {
            "root_url": {"required": True, "type": "string"},
            "subnet_id": {"required": False, "type": "string"},
            "access_token": {"required": True, "type": "string"},
            "zone": {"required": False, "type": "string"},
            "mcp_session_lease": {
                "required": False,
                "type": "bearer",
                "summary": "When a root-issued MCP session lease is used, subnet and zone are restored server-side from the lease.",
            },
        },
        "headers": {
            "Authorization": "Bearer <access_token>",
            "X-AdaOS-Subnet-Id": "<subnet_id> (optional with session lease)",
            "X-AdaOS-Zone": "<zone> (optional with session lease)",
        },
        "access_token_defaults": {
            "capabilities": list(DEFAULT_ACCESS_TOKEN_CAPABILITIES),
        },
        "entrypoints": [
            "/v1/root/mcp/foundation",
            "/v1/root/mcp/contracts",
            "/v1/root/mcp/descriptors",
            "/v1/root/mcp/descriptors/{descriptor_id}",
            "/v1/root/mcp/targets",
            "/v1/root/mcp/call",
            "/v1/root/mcp/audit",
        ],
    }


def _system_model_vocabulary() -> dict[str, Any]:
    return {
        "kinds": sorted(CANONICAL_KIND_REGISTRY),
        "relations": sorted(CANONICAL_RELATION_REGISTRY),
        "statuses": _status_vocab(),
        "projection_classes": [
            "object",
            "reliability",
            "inventory",
            "neighborhood",
            "task_packet",
        ],
    }


def _descriptor_entry(
    descriptor_id: str,
    *,
    title: str,
    summary: str,
    source_kind: str,
    descriptor_class: str,
    stability: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    defaults = dict(DESCRIPTOR_CACHE_CLASS_DEFAULTS.get(str(descriptor_class), {}))
    ttl_seconds = int(defaults.get("ttl_seconds") or 600)
    effective_stability = str(stability or defaults.get("stability") or "experimental")
    overview = _overview_row(
        row_id=descriptor_id,
        kind=f"descriptor.{descriptor_class}",
        title=title,
        summary=summary,
        stability=effective_stability,
        descriptor_id=descriptor_id,
        owner="root",
        schema_id=descriptor_id if descriptor_class == "schema" else None,
        capabilities=tags or [],
    )
    return {
        "descriptor_id": descriptor_id,
        "title": title,
        "summary": summary,
        "descriptor_class": descriptor_class,
        "stability": effective_stability,
        "publication_mode": "root-curated",
        "source": {
            "kind": source_kind,
            "published_by": "root",
        },
        "cache": {
            "enabled": True,
            "mode": "root_descriptor_cache",
            "ttl_seconds": ttl_seconds,
            "freshness_policy": str(defaults.get("freshness") or "fresh"),
        },
        "tags": list(tags or []),
        "fingerprint": overview["fingerprint"],
        "drill_down": overview["drill_down"],
        "overview": overview,
    }


def _descriptor_bundle_metadata(
    entry: dict[str, Any], payload: Any, *, level: str = "std"
) -> dict[str, Any]:
    issued_at = _iso_now()
    cache = dict(entry.get("cache") or {})
    ttl_seconds = int(cache.get("ttl_seconds") or 600)
    content_digest = _content_digest(payload)
    return {
        "descriptor_id": entry["descriptor_id"],
        "level": level,
        "generated_at": issued_at,
        "fresh_until": _with_ttl(issued_at, ttl_seconds),
        "ttl_seconds": ttl_seconds,
        "freshness": {
            "state": "fresh",
            "cache_mode": str(cache.get("mode") or "root_descriptor_cache"),
            "served_from": "root",
        },
        "provenance": {
            "source_kind": entry["source"]["kind"],
            "published_by": entry["source"]["published_by"],
            "build_version": BUILD_INFO.version,
            "build_date": BUILD_INFO.build_date,
            "content_hash": content_digest,
        },
        "etag": content_digest,
    }


_DESCRIPTOR_SNAPSHOT_MEMORY: dict[tuple[str, str], Any] = {}


def _descriptor_snapshot_path(descriptor_id: str, digest: str) -> Path | None:
    try:
        root = Path(get_ctx().paths.root_mcp_state_dir()) / "descriptor_snapshots" / descriptor_id
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{digest.removeprefix('sha256:')}.json"
    except Exception:
        return None


def _store_descriptor_snapshot(descriptor_id: str, digest: str, payload: Any) -> None:
    _DESCRIPTOR_SNAPSHOT_MEMORY[(descriptor_id, digest)] = payload
    path = _descriptor_snapshot_path(descriptor_id, digest)
    if path is None or path.exists():
        return
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    except Exception:
        return


def _load_descriptor_snapshot(descriptor_id: str, digest: str) -> Any:
    cached = _DESCRIPTOR_SNAPSHOT_MEMORY.get((descriptor_id, digest))
    if cached is not None:
        return cached
    path = _descriptor_snapshot_path(descriptor_id, digest)
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    _DESCRIPTOR_SNAPSHOT_MEMORY[(descriptor_id, digest)] = payload
    return payload


def _delta_fragments(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"payload": payload}
    for key, id_keys in (
        ("items", ("id", "row_id", "name")),
        ("nodes", ("id",)),
        ("overview_rows", ("row_id", "id", "name")),
        ("tools", ("name", "id")),
        ("pages", ("path", "id", "title")),
    ):
        rows = payload.get(key)
        if not isinstance(rows, list):
            continue
        result: dict[str, Any] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_id = next((str(row.get(name) or "").strip() for name in id_keys if str(row.get(name) or "").strip()), "")
            if row_id:
                result[f"{key}:{row_id}"] = row
        if key == "nodes" and isinstance(payload.get("edges"), list):
            for edge in payload["edges"]:
                if not isinstance(edge, dict):
                    continue
                edge_id = f"edges:{edge.get('source')}:{edge.get('type')}:{edge.get('target')}"
                result[edge_id] = edge
        return result
    return {f"field:{key}": value for key, value in payload.items()}


def _descriptor_delta(previous: Any, current: Any, *, base_etag: str, etag: str) -> dict[str, Any]:
    before = _delta_fragments(previous)
    after = _delta_fragments(current)
    changed = [
        {"fragment_id": key, "digest": _content_digest(value), "value": value}
        for key, value in sorted(after.items())
        if key not in before or _content_digest(before[key]) != _content_digest(value)
    ]
    return {
        "schema": "adaos.descriptor.delta.v1",
        "base_etag": base_etag,
        "etag": etag,
        "changed": changed,
        "removed": sorted(set(before) - set(after)),
    }


def _descriptor_payload(
    descriptor_id: str,
    *,
    level: str = "std",
    query: str | None = None,
    limit: int = 24,
    cursor: str | None = None,
    roots: list[str] | None = None,
    depth: int = 1,
    purpose: str = "authoring",
) -> Any:
    token = str(descriptor_id or "").strip().lower()
    if token == "sdk_metadata":
        effective_level = str(level or "std").strip().lower() or "std"
        if effective_level not in {"mini", "std", "rich"}:
            effective_level = "std"
        return _sdk_metadata(
            effective_level,
            query=query,
            limit=limit,
            cursor=cursor,
            purpose=purpose,
        )
    if token == "application_contracts":
        return _application_contracts()
    if token == "ui_capability_catalog":
        return ui_capability_catalog()
    if token == "system_model_vocabulary":
        return _system_model_vocabulary()
    if token == "skill_manifest_schema":
        return _skill_manifest_schema()
    if token == "scenario_manifest_schema":
        return _scenario_manifest_schema()
    if token == "builder_task_schema":
        return _builder_task_schema()
    if token == "builder_draft_schema":
        return _builder_draft_schema()
    if token == "builder_realize_request_schema":
        return _builder_realize_request_schema()
    if token == "skill_factory_dev_node_registration_schema":
        return _skill_factory_schema(
            "skill_factory.dev_node_registration.v1.schema.json"
        )
    if token == "skill_factory_dev_task_assignment_schema":
        return _skill_factory_schema("skill_factory.dev_task_assignment.v1.schema.json")
    if token == "skill_factory_dev_result_schema":
        return _skill_factory_schema("skill_factory.dev_result.v1.schema.json")
    if token == "skill_factory_dev_ready_event_schema":
        return _skill_factory_schema("skill_factory.dev_ready_event.v1.schema.json")
    if token == "skill_factory_dev_task_failure_schema":
        return _skill_factory_schema("skill_factory.dev_task_failure.v1.schema.json")
    if token == "skill_factory_status":
        return _skill_factory_status()
    if token == "nlu_teacher_schema":
        return _nlu_teacher_schema()
    context_schemas = {
        "context_capsule_schema": "context.capsule.v2.schema.json",
        "context_relationship_schema": "context.relationship.v1.schema.json",
        "context_subject_binding_schema": "context.subject_binding.v1.schema.json",
        "context_plan_schema": "context.plan.v1.schema.json",
        "context_delta_schema": "context.delta.v1.schema.json",
        "context_receipt_schema": "agent.context_receipt.v1.schema.json",
        "context_memory_candidate_schema": "context.memory_candidate.v1.schema.json",
        "descriptor_overview_row_schema": "descriptor.overview_row.v1.schema.json",
    }
    if token in context_schemas:
        return _skill_factory_schema(context_schemas[token])
    if token == "template_catalog":
        return _template_catalog()
    if token == "capability_registry":
        return capability_registry_payload()
    if token == "mcp_plane_registry":
        return _plane_registry_payload()
    if token == "mcp_client_profile":
        return _client_profile()
    if token == "access_token_profile":
        return access_token_registry_summary()
    if token == "capability_profiles":
        return {
            "available": True,
            "kind": "named_capability_profiles",
            "profiles": [
                {
                    "profile_id": profile_id,
                    "capabilities": list(capabilities),
                }
                for profile_id, capabilities in sorted(
                    DEFAULT_CAPABILITY_PROFILES.items()
                )
            ],
        }
    if token == "mcp_session_profile":
        return {
            "session_registry": mcp_session_registry_summary(),
            "client_bootstrap": {
                "mode": "bearer_only",
                "subnet_transport_params_required": False,
                "issuer": "root",
            },
        }
    if token == "architecture_catalog":
        return _architecture_catalog(
            query=query,
            limit=limit,
            cursor=cursor,
            roots=roots,
            depth=depth,
        )
    if token == "public_skill_registry_summary":
        return _public_registry_summary("skills", query=query, limit=limit, cursor=cursor)
    if token == "public_scenario_registry_summary":
        return _public_registry_summary("scenarios", query=query, limit=limit, cursor=cursor)
    if token == "named_entity_registry":
        from adaos.services import named_entities

        return named_entities.compact_registry_payload(webspace_id="desktop")
    if token == "descriptor_build_profile":
        return _descriptor_build_profile()
    if token == "descriptor_bundle":
        descriptor_ids = [
            item["descriptor_id"]
            for item in list_descriptor_sets()
            if item["descriptor_id"] != "descriptor_bundle"
        ]
        items = [get_descriptor_set(item_id, level=level) for item_id in descriptor_ids]
        return {
            "bundle_id": "root_descriptor_bundle",
            "descriptor_count": len(items),
            "descriptors": items,
        }
    raise KeyError(token)


def list_descriptor_sets() -> list[dict[str, Any]]:
    return [
        _descriptor_entry(
            "application_contracts",
            title="Application lifecycle contracts",
            summary="Versioned Application, release, installation, subscription, runtime selection, Trial grant, and operation schemas.",
            source_kind="application_contract_bundle",
            descriptor_class="schema",
            tags=["applications", "lifecycle", "distribution", "schema"],
        ),
        _descriptor_entry(
            "sdk_metadata",
            title="SDK metadata",
            summary="Root-curated metadata view over the AdaOS SDK exporter.",
            source_kind="internal_sdk_export",
            descriptor_class="sdk",
            tags=["development", "sdk", "metadata"],
        ),
        _descriptor_entry(
            "system_model_vocabulary",
            title="System model vocabulary",
            summary="Canonical kinds, relations, statuses, and projection classes used by AdaOS.",
            source_kind="system_model_registry",
            descriptor_class="vocabulary",
            tags=["development", "system-model", "vocabulary"],
        ),
        _descriptor_entry(
            "skill_manifest_schema",
            title="Skill manifest schema",
            summary="Current JSON schema for skill manifests and related runtime metadata.",
            source_kind="skill_manifest_schema",
            descriptor_class="schema",
            tags=["development", "skill", "schema"],
        ),
        _descriptor_entry(
            "scenario_manifest_schema",
            title="Scenario manifest schema",
            summary="Current JSON schema for scenario manifests.",
            source_kind="scenario_manifest_schema",
            descriptor_class="schema",
            tags=["development", "scenario", "schema"],
        ),
        _descriptor_entry(
            "builder_task_schema",
            title="Builder task schema",
            summary="Current JSON schema for Builder task handoff packets.",
            source_kind="builder_task_schema",
            descriptor_class="schema",
            tags=["development", "builder", "task", "schema"],
        ),
        _descriptor_entry(
            "builder_draft_schema",
            title="Builder draft schema",
            summary="Current JSON schema for Builder draft workspace metadata.",
            source_kind="builder_draft_schema",
            descriptor_class="schema",
            tags=["development", "builder", "draft", "schema"],
        ),
        _descriptor_entry(
            "builder_realize_request_schema",
            title="Builder realize request schema",
            summary="JSON schema for normalized Builder realization requests sent to the Skill Factory.",
            source_kind="builder_realize_request_schema",
            descriptor_class="schema",
            tags=["development", "builder", "skill-factory", "schema"],
        ),
        _descriptor_entry(
            "skill_factory_dev_node_registration_schema",
            title="Skill Factory dev-node registration schema",
            summary="JSON schema for isolated dev-node registration records.",
            source_kind="skill_factory_dev_node_registration_schema",
            descriptor_class="schema",
            tags=["development", "skill-factory", "dev-node", "schema"],
        ),
        _descriptor_entry(
            "skill_factory_dev_task_assignment_schema",
            title="Skill Factory dev-task assignment schema",
            summary="JSON schema for task assignments issued by Root to isolated dev nodes.",
            source_kind="skill_factory_dev_task_assignment_schema",
            descriptor_class="schema",
            tags=["development", "skill-factory", "assignment", "schema"],
        ),
        _descriptor_entry(
            "skill_factory_dev_result_schema",
            title="Skill Factory dev result schema",
            summary="JSON schema for completed development task result manifests.",
            source_kind="skill_factory_dev_result_schema",
            descriptor_class="schema",
            tags=["development", "skill-factory", "result", "schema"],
        ),
        _descriptor_entry(
            "skill_factory_dev_ready_event_schema",
            title="Skill Factory ready event schema",
            summary="JSON schema for Root ready events sent after a dev result is accepted.",
            source_kind="skill_factory_dev_ready_event_schema",
            descriptor_class="schema",
            tags=["development", "skill-factory", "ready-event", "schema"],
        ),
        _descriptor_entry(
            "skill_factory_dev_task_failure_schema",
            title="Skill Factory task failure schema",
            summary="JSON schema for failed development task reports.",
            source_kind="skill_factory_dev_task_failure_schema",
            descriptor_class="schema",
            tags=["development", "skill-factory", "failure", "schema"],
        ),
        _descriptor_entry(
            "skill_factory_status",
            title="Skill Factory status",
            summary="Root-published queue and dev-node status for diagnostics and operator UI.",
            source_kind="skill_factory_state",
            descriptor_class="registry",
            tags=["development", "skill-factory", "queue", "diagnostics"],
        ),
        _descriptor_entry(
            "nlu_teacher_schema",
            title="NLU Teacher schema",
            summary="Current JSON schema for NLU Teacher request, candidate, clarification, feedback, and MCP profile contracts.",
            source_kind="nlu_teacher_schema",
            descriptor_class="schema",
            tags=["development", "nlu", "teacher", "schema"],
        ),
        _descriptor_entry(
            "descriptor_overview_row_schema",
            title="Descriptor overview row schema",
            summary="Common compact overview row with stable fingerprint and drill-down identity.",
            source_kind="descriptor_overview_row_schema",
            descriptor_class="schema",
            tags=["development", "descriptor", "overview", "schema"],
        ),
        _descriptor_entry(
            "context_capsule_schema",
            title="Context capsule schema",
            summary="Immutable governed context unit with subject, trust, authority, time, and artifact identity.",
            source_kind="context_capsule_schema",
            descriptor_class="schema",
            tags=["development", "context", "capsule", "schema"],
        ),
        _descriptor_entry(
            "context_relationship_schema",
            title="Context relationship schema",
            summary="Typed dependency and provenance edge between immutable context capsules.",
            source_kind="context_relationship_schema",
            descriptor_class="schema",
            tags=["development", "context", "relationship", "schema"],
        ),
        _descriptor_entry(
            "context_subject_binding_schema",
            title="Context subject binding schema",
            summary="Optimistic mutable binding from a typed subject and purpose to an immutable capsule.",
            source_kind="context_subject_binding_schema",
            descriptor_class="schema",
            tags=["development", "context", "binding", "schema"],
        ),
        _descriptor_entry(
            "context_plan_schema",
            title="Context plan schema",
            summary="Deterministic selected, omitted, denied, and unavailable context plan under a token budget.",
            source_kind="context_plan_schema",
            descriptor_class="schema",
            tags=["development", "context", "plan", "schema"],
        ),
        _descriptor_entry(
            "context_delta_schema",
            title="Context delta schema",
            summary="Digest-bound model projection containing only changes from an acknowledged canonical context packet.",
            source_kind="context_delta_schema",
            descriptor_class="schema",
            tags=["development", "context", "delta", "schema"],
        ),
        _descriptor_entry(
            "context_receipt_schema",
            title="Agent context receipt schema",
            summary="Immutable attribution of context selection, token use, execution route, and validation evidence.",
            source_kind="context_receipt_schema",
            descriptor_class="schema",
            tags=["development", "context", "receipt", "schema"],
        ),
        _descriptor_entry(
            "context_memory_candidate_schema",
            title="Context memory candidate schema",
            summary="Evidence-gated reusable-memory proposal and independent promotion lifecycle.",
            source_kind="context_memory_candidate_schema",
            descriptor_class="schema",
            tags=["development", "context", "memory", "schema"],
        ),
        _descriptor_entry(
            "template_catalog",
            title="Template catalog",
            summary="Built-in skill and scenario template names available for scaffolding workflows.",
            source_kind="template_catalog",
            descriptor_class="templates",
            tags=["development", "templates", "scaffold"],
        ),
        _descriptor_entry(
            "architecture_catalog",
            title="Architecture catalog",
            summary="Root-curated catalog of AdaOS architecture pages and control-plane references.",
            source_kind="docs_architecture_index",
            descriptor_class="architecture",
            tags=["development", "architecture", "docs"],
        ),
        _descriptor_entry(
            "ui_capability_catalog",
            title="UI capability catalog",
            summary="Versioned semantic layouts, components, recipes, limitations, and acceptance postconditions for Builder-authored WebUI.",
            source_kind="ui_capability_catalog",
            descriptor_class="client",
            tags=["development", "builder", "client", "ui", "components", "recipes"],
        ),
        _descriptor_entry(
            "capability_registry",
            title="Capability registry",
            summary="Root MCP capability classes, default grants, and risk hints.",
            source_kind="root_mcp_policy_registry",
            descriptor_class="policy",
            tags=["development", "policy", "capabilities"],
        ),
        _descriptor_entry(
            "mcp_plane_registry",
            title="MCP plane registry",
            summary="Published Root MCP plane registry covering descriptive and operational product surfaces over the foundation.",
            source_kind="root_mcp_plane_registry",
            descriptor_class="registry",
            tags=["development", "planes", "registry"],
        ),
        _descriptor_entry(
            "mcp_client_profile",
            title="MCP client profile",
            summary="Root MCP client configuration shape for external tools such as Codex or VS Code integrations.",
            source_kind="root_mcp_client_profile",
            descriptor_class="client",
            tags=["development", "client", "integration"],
        ),
        _descriptor_entry(
            "access_token_profile",
            title="Access token profile",
            summary="Bounded Root MCP access-token defaults and registry summary.",
            source_kind="root_mcp_access_token_registry",
            descriptor_class="auth",
            tags=["development", "auth", "tokens"],
        ),
        _descriptor_entry(
            "capability_profiles",
            title="Capability profiles",
            summary="Named capability profiles for root-issued MCP session leases and future plane-scoped bootstrap flows.",
            source_kind="root_mcp_capability_profiles",
            descriptor_class="auth",
            tags=["development", "auth", "profiles"],
        ),
        _descriptor_entry(
            "mcp_session_profile",
            title="MCP session profile",
            summary="Root-issued MCP session lease registry and bearer-only client bootstrap guidance.",
            source_kind="root_mcp_session_registry",
            descriptor_class="auth",
            tags=["development", "auth", "sessions"],
        ),
        _descriptor_entry(
            "public_skill_registry_summary",
            title="Public skill registry summary",
            summary="Root-curated summary of published workspace skill entries for descriptive MCP clients.",
            source_kind="workspace_registry",
            descriptor_class="registry",
            tags=["development", "skills", "registry"],
        ),
        _descriptor_entry(
            "public_scenario_registry_summary",
            title="Public scenario registry summary",
            summary="Root-curated summary of published workspace scenario entries for descriptive MCP clients.",
            source_kind="workspace_registry",
            descriptor_class="registry",
            tags=["development", "scenarios", "registry"],
        ),
        _descriptor_entry(
            "named_entity_registry",
            title="Named entity registry",
            summary="Compact read-only canonical named-entity registry for LLM, NLU, UI diagnostics, and operator tooling.",
            source_kind="named_entity_service",
            descriptor_class="registry",
            tags=["development", "nlu", "entities", "registry"],
        ),
        _descriptor_entry(
            "descriptor_build_profile",
            title="Descriptor build profile",
            summary="Prototype build profile that turns SDK export, docs, and workspace registry inputs into root-curated descriptive bundles.",
            source_kind="root_descriptor_build_profile",
            descriptor_class="build",
            tags=["development", "build", "pipeline"],
        ),
        _descriptor_entry(
            "descriptor_bundle",
            title="Descriptor bundle",
            summary="Root-built bundle over the current descriptive registry for LLM bootstrap and cache-backed development workflows.",
            source_kind="root_descriptor_bundle",
            descriptor_class="bundle",
            tags=["development", "bundle", "cache"],
        ),
    ]


def descriptor_registry_summary() -> dict[str, Any]:
    items = list_descriptor_sets()
    return {
        "available": True,
        "publication_mode": "root-curated",
        "cache_mode": "root_descriptor_cache",
        "descriptor_count": len(items),
        "descriptors": [item["descriptor_id"] for item in items],
        "descriptor_classes": sorted(
            {
                str(item.get("descriptor_class") or "").strip()
                for item in items
                if str(item.get("descriptor_class") or "").strip()
            }
        ),
        "cache_policies": {
            key: {
                "ttl_seconds": int(value.get("ttl_seconds") or 0),
                "stability": str(value.get("stability") or "experimental"),
            }
            for key, value in sorted(DESCRIPTOR_CACHE_CLASS_DEFAULTS.items())
        },
        "descriptor_cache": descriptor_cache_summary(),
        "capability_registry": capability_registry_summary(),
        "managed_target_registry": managed_target_registry_summary(),
        "control_report_registry": control_report_registry_summary(),
        "access_token_registry": access_token_registry_summary(),
        "mcp_session_registry": mcp_session_registry_summary(),
    }


def get_descriptor_set(
    descriptor_id: str,
    *,
    level: str = "std",
    query: str | None = None,
    limit: int = 24,
    cursor: str | None = None,
    roots: list[str] | None = None,
    depth: int = 1,
    if_none_match: str | None = None,
    since_digest: str | None = None,
    purpose: str = "authoring",
) -> dict[str, Any]:
    token = str(descriptor_id or "").strip().lower()
    effective_level = str(level or "std").strip().lower() or "std"
    if effective_level not in {"mini", "std", "rich"}:
        effective_level = "std"
    entry = next(
        (item for item in list_descriptor_sets() if item["descriptor_id"] == token),
        None,
    )
    if entry is None:
        raise KeyError(token)
    payload = _descriptor_payload(
        token,
        level=effective_level,
        query=query,
        limit=max(1, min(int(limit or 24), 64)),
        cursor=cursor,
        roots=roots,
        depth=max(0, min(int(depth or 0), 3)),
        purpose=purpose,
    )
    metadata = _descriptor_bundle_metadata(entry, payload, level=effective_level)
    etag = str(metadata.get("etag") or "")
    _store_descriptor_snapshot(token, etag, payload)
    result = {
        **entry,
        "level": effective_level,
        "metadata": metadata,
        "etag": etag,
        "payload": payload,
        "delivery": {"mode": "full", "etag": etag, "cache": "content-addressed"},
    }
    if str(if_none_match or "").strip() == etag:
        result["payload"] = None
        result["delivery"] = {"mode": "not_modified", "etag": etag, "cache": "content-addressed"}
        return result
    base = str(since_digest or "").strip()
    if base and base != etag:
        previous = _load_descriptor_snapshot(token, base)
        if previous is None:
            result["delivery"]["reset_required"] = True
        else:
            result["payload"] = _descriptor_delta(previous, payload, base_etag=base, etag=etag)
            result["delivery"] = {"mode": "delta", "etag": etag, "base_etag": base, "cache": "content-addressed"}
    return result


__all__ = [
    "descriptor_cache_summary",
    "descriptor_registry_summary",
    "get_descriptor_set",
    "list_descriptor_sets",
    "public_registry_item",
    "record_descriptor_refresh",
]
