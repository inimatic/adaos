from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from adaos.domain.project_events import BUILDER_CONTEXT_SELECTED, BUILDER_PREVIEW_DESIRED, BUILDER_PREVIEW_OBSERVED
from adaos.sdk.core.decorators import subscribe
from adaos.services.builder.preview_reconciler import BuilderPreviewReconciler
from adaos.services.runtime_paths import current_state_dir
from adaos.services.webspace_id import coerce_webspace_id
from adaos.services.workspaces.relations import (
    BUILDER_PROJECT_PREVIEW,
    BUILDER_SELF_HOST,
    WebspaceRelationshipRegistry,
    relation_purpose_for_scenario,
)


BUILDER_WORKBENCH_SCENARIO_ID = "prompt_engineer_scenario"
BUILDER_HOST_SCENARIO_ID = "builder"
BUILDER_RUNTIME_FALLBACK_SCENARIO_ID = "web_desktop"
BUILDER_DIALOG_CHANNEL_ID = "builder"
BUILDER_SKILL_ID = "builder_skill"
BUILDER_OWNER = f"skill:{BUILDER_SKILL_ID}"
_log = logging.getLogger("adaos.builder.workbench")
_PROJECTION_TASKS: dict[str, asyncio.Task[Any]] = {}
_PROJECTION_PENDING: dict[str, dict[str, Any]] = {}


def safe_source_webspace_id(value: Any) -> str:
    fallback = os.getenv("ADAOS_WEBSPACE_ID") or "desktop"
    token = coerce_webspace_id(value, fallback=fallback)
    token = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(token or "").strip()).strip(".:-_")
    return token or fallback


def source_webspace_id_for(value: Any) -> str:
    """Resolve a Builder host from explicit topology, without parsing its id."""
    token = safe_source_webspace_id(value)
    try:
        return WebspaceRelationshipRegistry.from_context().resolve_builder_host(token)
    except Exception:
        return token


def dev_webspace_id_for_source(source_webspace_id: Any) -> str:
    """Compatibility lookup for the explicitly paired preview webspace."""

    source = source_webspace_id_for(source_webspace_id)
    registry = WebspaceRelationshipRegistry.from_context()
    relation = registry.get_outgoing(source)
    if relation is None:
        relation, _created = registry.ensure(source, purpose=BUILDER_PROJECT_PREVIEW)
    return relation.target_webspace_id


def _now() -> float:
    return time.time()


def _read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    except FileNotFoundError:
        return dict(default or {})
    if not isinstance(data, dict):
        return dict(default or {})
    return data


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_path_token(value: Any) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    return token or "draft"


def _binding_semantic_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    keys = (
        "source_webspace_id",
        "dev_webspace_id",
        "preview_webspace_id",
        "relationship",
        "scenario_id",
        "runtime_scenario_id",
        "purpose",
        "active_draft_id",
        "selection",
        "preview_target",
        "dialog",
    )
    return all(left.get(key) == right.get(key) for key in keys)


def _project_selection(
    object_type: Any,
    object_id: Any,
    *,
    title: Any = None,
    description: Any = None,
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    kind = str(object_type or "scenario").strip().lower().rstrip("s")
    project_id = str(object_id or "builder").strip() or "builder"
    old = dict(previous) if isinstance(previous, Mapping) else {}
    same_project = old.get("object_type") == kind and old.get("object_id") == project_id
    resolved_title = str(title or (old.get("title") if same_project else "") or project_id).strip() or project_id
    resolved_description = str(
        description if description is not None else (old.get("description") if same_project else "")
    ).strip()
    topic_id = f"prompt-project:{kind}:{project_id}"
    return {
        "object_type": kind,
        "object_id": project_id,
        "ref": f"{kind}:{project_id}",
        "title": resolved_title,
        "description": resolved_description,
        "topic_id": topic_id,
        "thread_id": topic_id,
    }


def _preview_runtime_projection(value: Any) -> dict[str, Any]:
    runtime = dict(value) if isinstance(value, Mapping) else {}
    return {
        key: runtime.get(key)
        for key in (
            "schema",
            "source_webspace_id",
            "preview_webspace_id",
            "selected_project",
            "desired_scenario",
            "observed_scenario",
            "generation",
            "operation_id",
            "status",
            "requested_at",
            "started_at",
            "completed_at",
            "updated_at",
            "error",
        )
    }


def _preview_state_projection(value: Any) -> dict[str, Any]:
    state = dict(value) if isinstance(value, Mapping) else {}
    return {
        key: state.get(key)
        for key in (
            "scenario_id",
            "draft_id",
            "version",
            "revision",
            "title",
            "status",
            "phase",
            "updated_at",
        )
        if key in state
    }


def _info_to_dict(info: Any) -> dict[str, Any]:
    if info is None:
        return {}
    to_dict = getattr(info, "to_dict", None)
    if callable(to_dict):
        try:
            data = to_dict()
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    out: dict[str, Any] = {}
    for key in (
        "id",
        "webspace_id",
        "title",
        "kind",
        "source_mode",
        "home_scenario",
        "current_scenario",
        "current_scenario_exists",
        "degraded",
        "validation_reason",
        "recommended_action",
    ):
        value = getattr(info, key, None)
        if value is not None:
            out[key] = value
    return out


def _draft_runtime_scenario_id(state_dir: Path | None, draft_id: str | None) -> str | None:
    token = str(draft_id or "").strip()
    if not token:
        return None
    draft_path = Path(state_dir or current_state_dir()) / "builder" / "drafts" / token / "builder.draft.json"
    draft = _read_json(draft_path)
    artifact = draft.get("artifact") if isinstance(draft.get("artifact"), dict) else {}
    if str(artifact.get("kind") or "").strip() != "scenario":
        return None
    scenario_id = str(artifact.get("id") or "").strip()
    return scenario_id or None


@dataclass(slots=True)
class BuilderWorkbenchService:
    state_dir: Path | None = None
    webspace_service: Any | None = None
    relationship_registry: WebspaceRelationshipRegistry | None = None
    preview_reconciler: BuilderPreviewReconciler | None = None

    @classmethod
    def from_context(cls) -> "BuilderWorkbenchService":
        return cls(state_dir=current_state_dir())

    @property
    def root(self) -> Path:
        path = Path(self.state_dir or current_state_dir()) / "builder" / "workbench"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def binding_path(self, source_webspace_id: str) -> Path:
        return self.root / "bindings" / f"{safe_source_webspace_id(source_webspace_id)}.json"

    def snapshot_path(self, source_webspace_id: str) -> Path:
        return self.root / "snapshots" / f"{safe_source_webspace_id(source_webspace_id)}.json"

    @property
    def relationships(self) -> WebspaceRelationshipRegistry:
        if self.relationship_registry is None:
            self.relationship_registry = WebspaceRelationshipRegistry.from_context()
        return self.relationship_registry

    @property
    def reconciler(self) -> BuilderPreviewReconciler:
        if self.preview_reconciler is None:
            self.preview_reconciler = BuilderPreviewReconciler(state_dir=self.state_dir)
        return self.preview_reconciler

    def resolve_source_webspace_id(self, value: Any) -> str:
        token = safe_source_webspace_id(value)
        incoming = self.relationships.get_incoming(token)
        if incoming is not None:
            return token if incoming.purpose == BUILDER_SELF_HOST else incoming.source_webspace_id

        bindings_root = self.root / "bindings"
        for path in bindings_root.glob("*.json") if bindings_root.is_dir() else ():
            legacy = _read_json(path)
            if str(legacy.get("dev_webspace_id") or legacy.get("preview_webspace_id") or "").strip() != token:
                continue
            source = safe_source_webspace_id(legacy.get("source_webspace_id") or path.stem)
            scenario_id = str(legacy.get("runtime_scenario_id") or "").strip() or None
            relation, _created = self.relationships.ensure(
                source,
                purpose=relation_purpose_for_scenario(scenario_id),
                scenario_id=scenario_id,
                legacy_target_webspace_id=token,
                metadata={"migrated_from": "builder_workbench_binding"},
            )
            return token if relation.purpose == BUILDER_SELF_HOST else source
        return self.relationships.resolve_builder_host(token)

    def resolve_action_source_webspace_id(
        self,
        value: Any,
        *,
        current_scenario_id: Any = None,
    ) -> str:
        """Resolve an action host, claiming the bounded self-host level for Builder."""

        token = safe_source_webspace_id(value)
        if relation_purpose_for_scenario(current_scenario_id) == BUILDER_SELF_HOST:
            return self.relationships.claim_builder_self_host(
                token,
                scenario_id=current_scenario_id,
            )
        return self.resolve_source_webspace_id(token)

    def _ensure_preview_relation(
        self,
        source_webspace_id: str,
        *,
        scenario_id: str | None,
        legacy_target_webspace_id: str | None = None,
    ):
        return self.relationships.ensure(
            source_webspace_id,
            purpose=relation_purpose_for_scenario(scenario_id),
            scenario_id=scenario_id,
            legacy_target_webspace_id=legacy_target_webspace_id,
            metadata={"owner": "builder.workbench"},
        )

    def list_workspace_bindings(self) -> list[dict[str, Any]]:
        bindings: list[dict[str, Any]] = []
        sources: set[str] = set()
        root = self.root / "bindings"
        if root.is_dir():
            for path in root.glob("*.json"):
                raw = _read_json(path)
                source = safe_source_webspace_id(raw.get("source_webspace_id") or path.stem)
                if source in sources:
                    continue
                sources.add(source)
                bindings.append(self.get_workspace_binding(source))
        for relation in self.relationships.list():
            if relation.source_webspace_id in sources:
                continue
            sources.add(relation.source_webspace_id)
            bindings.append(self.get_workspace_binding(relation.source_webspace_id))
        return bindings

    def _webspace_inventory(self) -> dict[str, dict[str, Any]]:
        """Return the operational Webspace inventory without changing topology."""

        svc = self.webspace_service
        if svc is None:
            from adaos.services.scenario.webspace_runtime import WebspaceService

            svc = WebspaceService()
        inventory: dict[str, dict[str, Any]] = {}
        for item in svc.list(mode="mixed"):
            payload = _info_to_dict(item)
            webspace_id = str(payload.get("id") or payload.get("webspace_id") or "").strip()
            if webspace_id:
                inventory[webspace_id] = payload
        return inventory

    def list_builder_hosts(self) -> list[dict[str, Any]]:
        """Discover active Builder surfaces and their explicit Preview targets.

        Discovery is intentionally read-only: it never creates a workbench
        binding, Webspace relation, or Preview Webspace. A Builder host is a
        Webspace whose effective current scenario is a configured Builder
        scenario. Merely having Builder installed or a stale binding is not
        sufficient.
        """

        inventory = self._webspace_inventory()
        configured_builder_scenarios = os.getenv("ADAOS_BUILDER_SCENARIO_IDS") or (
            f"{BUILDER_HOST_SCENARIO_ID},{BUILDER_WORKBENCH_SCENARIO_ID}"
        )
        builder_scenarios = {
            item.strip()
            for item in str(configured_builder_scenarios).split(",")
            if item.strip()
        }
        contexts: list[dict[str, Any]] = []
        for webspace_id, info in inventory.items():
            current_scenario = str(info.get("current_scenario") or "").strip()
            home_scenario = str(info.get("home_scenario") or "").strip()
            effective_scenario = current_scenario or home_scenario
            if effective_scenario not in builder_scenarios:
                continue

            relation = self.relationships.get_outgoing(webspace_id)
            preview_id = relation.target_webspace_id if relation is not None else ""
            preview = inventory.get(preview_id) if preview_id else None
            status = "ready"
            reason: str | None = None
            if info.get("current_scenario_exists") is False or bool(info.get("degraded")):
                status = "builder_degraded"
                reason = str(info.get("validation_reason") or "builder_webspace_degraded")
            elif relation is None:
                status = "preview_relation_missing"
                reason = "builder_preview_relation_missing"
            elif preview is None:
                status = "preview_webspace_missing"
                reason = "builder_preview_webspace_missing"
            elif str(preview.get("kind") or "").strip() != "dev":
                status = "preview_webspace_invalid"
                reason = "builder_preview_must_be_dev_webspace"

            contexts.append(
                {
                    "schema": "adaos.builder.context_ref.v1",
                    "builder_webspace_id": webspace_id,
                    "builder_title": str(info.get("title") or webspace_id).strip() or webspace_id,
                    "builder_space_kind": str(info.get("kind") or "workspace").strip() or "workspace",
                    "builder_source_mode": str(info.get("source_mode") or "workspace").strip() or "workspace",
                    "builder_scenario_id": effective_scenario,
                    "preview_webspace_id": preview_id or None,
                    "preview_relation_id": relation.relation_id if relation is not None else None,
                    "preview_relation_generation": relation.generation if relation is not None else None,
                    "status": status,
                    "selectable": status == "ready",
                    "reason": reason,
                }
            )
        return sorted(
            contexts,
            key=lambda item: (
                not bool(item.get("selectable")),
                str(item.get("builder_title") or "").casefold(),
                str(item.get("builder_webspace_id") or "").casefold(),
            ),
        )

    def resolve_builder_context(
        self,
        builder_webspace_id: Any,
        *,
        require_ready: bool = True,
    ) -> dict[str, Any]:
        """Resolve one explicit Builder host; never infer it from an id suffix."""

        requested = safe_source_webspace_id(builder_webspace_id)
        context = next(
            (
                item
                for item in self.list_builder_hosts()
                if str(item.get("builder_webspace_id") or "") == requested
            ),
            None,
        )
        if context is None:
            raise ValueError(f"Builder is not active in Webspace {requested!r}")
        if require_ready and not bool(context.get("selectable")):
            status = str(context.get("status") or "unavailable")
            raise ValueError(f"Builder Webspace {requested!r} is not ready: {status}")
        return dict(context)

    async def ensure_dev_webspace(
        self,
        source_webspace_id: str | None = None,
        *,
        active_draft_id: str | None = None,
        scenario_id: str | None = None,
        runtime_scenario_id: str | None = None,
        preview_state: Mapping[str, Any] | None = None,
        wait_for_rebuild: bool = True,
    ) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        workbench_scenario = str(scenario_id or "").strip() or BUILDER_WORKBENCH_SCENARIO_ID
        runtime_scenario = (
            str(runtime_scenario_id or "").strip()
            or _draft_runtime_scenario_id(self.state_dir, active_draft_id)
            or BUILDER_RUNTIME_FALLBACK_SCENARIO_ID
        )
        legacy_binding = _read_json(self.binding_path(source_id))
        legacy_target = str(
            legacy_binding.get("preview_webspace_id") or legacy_binding.get("dev_webspace_id") or ""
        ).strip() or None
        relation, relation_created = self._ensure_preview_relation(
            source_id,
            scenario_id=runtime_scenario,
            legacy_target_webspace_id=legacy_target,
        )
        dev_id = relation.target_webspace_id
        created = False
        info_payload: dict[str, Any] = {}
        runtime_payload: dict[str, Any] = {}
        try:
            svc = self.webspace_service
            if svc is None:
                from adaos.services.scenario.webspace_runtime import WebspaceService

                svc = WebspaceService()
            existing = None
            for item in svc.list(mode="mixed"):
                if str(getattr(item, "id", "") or getattr(item, "webspace_id", "") or "").strip() == dev_id:
                    existing = item
                    break
            if existing is None:
                existing = await svc.create(
                    dev_id,
                    f"DEV: {source_id}",
                    scenario_id=runtime_scenario,
                    dev=True,
                )
                created = True
            else:
                kind = str(getattr(existing, "kind", "") or "").strip()
                if kind and kind != "dev":
                    raise ValueError(f"paired webspace {dev_id!r} exists but is not a dev webspace")
            info_payload = _info_to_dict(existing)
            if runtime_scenario and self.webspace_service is None:
                try:
                    from adaos.services.scenario.webspace_runtime import reload_webspace_from_scenario, switch_webspace_scenario

                    _requested, coalesced = self.reconciler.request(
                        source_webspace_id=source_id,
                        preview_webspace_id=dev_id,
                        project_kind="scenario",
                        project_id=runtime_scenario,
                        desired_scenario=runtime_scenario,
                    )

                    async def _apply_preview(record: Mapping[str, Any]) -> Mapping[str, Any]:
                        desired = str(record.get("desired_scenario") or "").strip()
                        preview_id = str(record.get("preview_webspace_id") or "").strip()
                        switch_payload = await switch_webspace_scenario(
                            preview_id,
                            desired,
                            set_home=True,
                            # Reconcile owns the background boundary. Waiting here keeps
                            # one materialization in flight while newer desired states
                            # coalesce behind it.
                            wait_for_rebuild=True,
                            request_id=str(record.get("operation_id") or "").strip() or None,
                            request_source="builder.workbench.reconciler",
                        )
                        skip_reason = (
                            str(switch_payload.get("skip_reason") or "").strip()
                            if isinstance(switch_payload, Mapping)
                            else ""
                        )
                        if skip_reason and skip_reason not in {
                            "already_current",
                            "already_current_ready",
                            "already_pending_rebuild",
                        }:
                            return await reload_webspace_from_scenario(
                                preview_id,
                                scenario_id=desired,
                                action="reload",
                                event_payload={
                                    "source": "builder.workbench.reconciler",
                                    "source_webspace_id": source_id,
                                    "operation_id": record.get("operation_id"),
                                },
                            )
                        return switch_payload

                    reconciled = await self.reconciler.reconcile(
                        source_id,
                        _apply_preview,
                        wait=wait_for_rebuild,
                    )
                    runtime_payload = {
                        "ok": str(reconciled.get("status") or "") != "failed",
                        "webspace_id": dev_id,
                        "scenario_id": runtime_scenario,
                        "coalesced": coalesced,
                        "switch": reconciled.get("result"),
                        "error": reconciled.get("error"),
                        "preview_runtime": reconciled,
                    }
                except BaseException as exc:
                    if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                        raise
                    runtime_payload = {
                        "ok": False,
                        "error": "dev_runtime_reload_failed",
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
            elif runtime_scenario:
                _requested, coalesced = self.reconciler.request(
                    source_webspace_id=source_id,
                    preview_webspace_id=dev_id,
                    project_kind="scenario",
                    project_id=runtime_scenario,
                    desired_scenario=runtime_scenario,
                )

                async def _accept_injected(_record: Mapping[str, Any]) -> Mapping[str, Any]:
                    return {"ok": True, "accepted": True, "skipped": "injected_webspace_service"}

                reconciled = await self.reconciler.reconcile(source_id, _accept_injected, wait=True)
                runtime_payload = {
                    "ok": True,
                    "skipped": "injected_webspace_service",
                    "webspace_id": dev_id,
                    "scenario_id": runtime_scenario,
                    "coalesced": coalesced,
                    "preview_runtime": reconciled,
                }
        except Exception as exc:
            info_payload = {"ok": False, "error": "dev_webspace_unavailable", "detail": f"{type(exc).__name__}: {exc}"}

        binding = self.set_active_draft(
            source_webspace_id=source_id,
            active_draft_id=active_draft_id,
            scenario_id=workbench_scenario,
            dev_webspace_id=dev_id,
            runtime_scenario_id=runtime_scenario,
            persist_projection=False,
        )
        binding["created"] = created
        binding["relationship_created"] = relation_created
        binding["dev_webspace"] = info_payload
        binding["runtime"] = runtime_payload
        binding["preview_runtime"] = self.reconciler.describe(source_id)
        _schedule_projection_publish(self, source_id, preview_state=preview_state)
        return binding

    def get_workspace_binding(self, source_webspace_id: str | None = None) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        existing = _read_json(self.binding_path(source_id))
        if existing:
            normalized = dict(existing)
            active_draft_id = str(normalized.get("active_draft_id") or "").strip() or None
            runtime_scenario_id = str(normalized.get("runtime_scenario_id") or "").strip() or None
            selection = (
                dict(normalized.get("selection"))
                if isinstance(normalized.get("selection"), Mapping)
                else _project_selection(
                    "scenario",
                    runtime_scenario_id or "builder",
                    title="Builder" if not runtime_scenario_id else None,
                )
            )
            relation, _created = self._ensure_preview_relation(
                source_id,
                scenario_id=runtime_scenario_id,
                legacy_target_webspace_id=str(
                    normalized.get("preview_webspace_id") or normalized.get("dev_webspace_id") or ""
                ).strip() or None,
            )
            dev_id = relation.target_webspace_id
            refreshed_dialog = self.dialog_widget_config(
                source_id,
                active_draft_id=active_draft_id,
                runtime_scenario_id=runtime_scenario_id,
                dev_webspace_id=dev_id,
            )
            relation_payload = relation.to_dict()
            if (
                normalized.get("dialog") != refreshed_dialog
                or normalized.get("preview_webspace_id") != dev_id
                or normalized.get("relationship") != relation_payload
                or normalized.get("selection") != selection
            ):
                normalized["source_webspace_id"] = source_id
                normalized["dev_webspace_id"] = dev_id
                normalized["preview_webspace_id"] = dev_id
                normalized["relationship"] = relation_payload
                normalized["selection"] = selection
                normalized["dialog"] = refreshed_dialog
                normalized["updated_at"] = _now()
                _write_json(self.binding_path(source_id), normalized)
            return normalized
        relation, _created = self._ensure_preview_relation(source_id, scenario_id=None)
        return {
            "source_webspace_id": source_id,
            "dev_webspace_id": relation.target_webspace_id,
            "preview_webspace_id": relation.target_webspace_id,
            "relationship": relation.to_dict(),
            "scenario_id": BUILDER_WORKBENCH_SCENARIO_ID,
            "runtime_scenario_id": None,
            "purpose": "builder_prompt_ide",
            "active_draft_id": None,
            "selection": _project_selection("scenario", "builder", title="Builder"),
            "preview_target": None,
            "dialog": self.dialog_widget_config(
                source_id,
                dev_webspace_id=relation.target_webspace_id,
            ),
            "created_at": None,
            "updated_at": None,
        }

    def set_active_draft(
        self,
        *,
        source_webspace_id: str | None = None,
        active_draft_id: str | None,
        scenario_id: str = BUILDER_WORKBENCH_SCENARIO_ID,
        dev_webspace_id: str | None = None,
        runtime_scenario_id: str | None = None,
        persist_projection: bool = True,
    ) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        now = _now()
        existing = _read_json(self.binding_path(source_id))
        runtime_id = (
            str(runtime_scenario_id or "").strip()
            or _draft_runtime_scenario_id(self.state_dir, active_draft_id)
            or existing.get("runtime_scenario_id")
            or None
        )
        relation, _created = self._ensure_preview_relation(
            source_id,
            scenario_id=str(runtime_id or "").strip() or None,
            legacy_target_webspace_id=str(
                dev_webspace_id
                or existing.get("preview_webspace_id")
                or existing.get("dev_webspace_id")
                or ""
            ).strip() or None,
        )
        dev_id = relation.target_webspace_id
        scenario_token = str(scenario_id or existing.get("scenario_id") or BUILDER_WORKBENCH_SCENARIO_ID).strip()
        active_draft_token = str(active_draft_id or "").strip() or None
        previous_selection = existing.get("selection") if isinstance(existing.get("selection"), Mapping) else None
        explicit_runtime_id = str(runtime_scenario_id or "").strip()
        if explicit_runtime_id and (
            not previous_selection
            or str(previous_selection.get("object_type") or "") == "scenario"
            and str(previous_selection.get("object_id") or "") != explicit_runtime_id
        ):
            selection = _project_selection("scenario", explicit_runtime_id, previous=previous_selection)
        else:
            selection = dict(previous_selection) if previous_selection else _project_selection("scenario", "builder", title="Builder")
        preview_target = (
            dict(existing.get("preview_target"))
            if isinstance(existing.get("preview_target"), Mapping)
            else None
        )
        if not previous_selection or (
            str(previous_selection.get("object_type") or "") != str(selection.get("object_type") or "")
            or str(previous_selection.get("object_id") or "") != str(selection.get("object_id") or "")
        ):
            preview_target = None
        binding = {
            "source_webspace_id": source_id,
            "dev_webspace_id": dev_id,
            "preview_webspace_id": dev_id,
            "relationship": relation.to_dict(),
            "scenario_id": scenario_token,
            "runtime_scenario_id": runtime_id,
            "purpose": "builder_prompt_ide",
            "active_draft_id": active_draft_token,
            "selection": selection,
            "preview_target": preview_target,
            "dialog": self.dialog_widget_config(
                source_id,
                active_draft_id=active_draft_token,
                runtime_scenario_id=runtime_id,
                dev_webspace_id=dev_id,
            ),
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
        }
        if existing and not persist_projection and _binding_semantic_equal(existing, binding):
            return existing
        _write_json(self.binding_path(source_id), binding)
        if persist_projection:
            self.publish_projection_sync(source_id)
        return binding

    def set_selected_project(
        self,
        *,
        source_webspace_id: str | None = None,
        object_type: str,
        object_id: str,
        title: str | None = None,
        description: str | None = None,
        persist_projection: bool = False,
    ) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        binding = self.get_workspace_binding(source_id)
        previous = binding.get("selection") if isinstance(binding.get("selection"), Mapping) else None
        selection = _project_selection(
            object_type,
            object_id,
            title=title,
            description=description,
            previous=previous,
        )
        if selection == previous:
            return binding
        updated = {**binding, "selection": selection, "updated_at": _now()}
        if not previous or (
            str(previous.get("object_type") or "") != str(selection.get("object_type") or "")
            or str(previous.get("object_id") or "") != str(selection.get("object_id") or "")
        ):
            updated["preview_target"] = None
        _write_json(self.binding_path(source_id), updated)
        if persist_projection:
            self.publish_projection_sync(source_id)
        return updated

    def set_preview_target(
        self,
        *,
        source_webspace_id: str | None = None,
        target: Mapping[str, Any] | None,
        persist_projection: bool = False,
    ) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        binding = self.get_workspace_binding(source_id)
        normalized = dict(target) if isinstance(target, Mapping) else None
        updated = {**binding, "preview_target": normalized, "updated_at": _now()}
        _write_json(self.binding_path(source_id), updated)
        if persist_projection:
            self.publish_projection_sync(source_id)
        return updated

    def open_dev_webspace(self, source_webspace_id: str | None = None, *, base_url: str | None = None) -> dict[str, Any]:
        binding = self.get_workspace_binding(source_webspace_id)
        dev_id = str(binding.get("dev_webspace_id") or "").strip()
        base = str(base_url or "").strip().rstrip("/")
        url = f"{base}/?webspace={dev_id}" if base else f"/?webspace={dev_id}"
        return {"ok": True, "url": url, "webspace_id": dev_id, "binding": binding}

    async def open_dev_webspace_ready(
        self,
        source_webspace_id: str | None = None,
        *,
        base_url: str | None = None,
        active_draft_id: str | None = None,
        runtime_scenario_id: str | None = None,
    ) -> dict[str, Any]:
        existing = self.get_workspace_binding(source_webspace_id)
        binding = await self.ensure_dev_webspace(
            source_webspace_id,
            active_draft_id=active_draft_id if active_draft_id is not None else existing.get("active_draft_id"),
            runtime_scenario_id=runtime_scenario_id or existing.get("runtime_scenario_id"),
        )
        return {
            **self.open_dev_webspace(source_webspace_id, base_url=base_url),
            "binding": binding,
        }

    def dialog_widget_config(
        self,
        source_webspace_id: str | None = None,
        *,
        active_draft_id: str | None = None,
        runtime_scenario_id: str | None = None,
        dev_webspace_id: str | None = None,
    ) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        relation = self.relationships.get_outgoing(source_id)
        if relation is None:
            relation, _created = self._ensure_preview_relation(
                source_id,
                scenario_id=str(runtime_scenario_id or "").strip() or None,
            )
        dev_id = str(dev_webspace_id or relation.target_webspace_id).strip()
        try:
            from adaos.services.conversation_links import ensure_builder_topic

            topic = ensure_builder_topic(
                source_id,
                active_draft_id=active_draft_id,
                scenario_id=runtime_scenario_id,
                dev_webspace_id=dev_id,
            )
        except Exception:
            runtime_token = str(runtime_scenario_id or "").strip()
            project_id = str(active_draft_id or runtime_token or dev_id or "default").strip() or "default"
            if runtime_token and not str(active_draft_id or "").strip():
                fallback_topic_id = f"prompt-project:scenario:{runtime_token}"
                fallback_thread_id = fallback_topic_id
                fallback_kind = "builder_scenario"
            else:
                token = project_id
                fallback_topic_id = f"builder:{source_id}:{token}"
                fallback_thread_id = f"thread.builder.{source_id}.{token}"
                fallback_kind = "builder_default"
            topic = {
                "schema": "adaos.conversation.topic_ref.v1",
                "topic_id": fallback_topic_id,
                "thread_id": fallback_thread_id,
                "topic_kind": fallback_kind,
                "webspace_id": source_id,
                "source_webspace_id": source_id,
                "active_draft_id": str(active_draft_id or "").strip() or None,
                "scenario_id": str(runtime_scenario_id or "").strip() or None,
                "dev_webspace_id": dev_id,
                "project_id": project_id,
                "conversation_id": f"conv.skill.{BUILDER_SKILL_ID}.default.{source_id}",
                "channel_id": BUILDER_DIALOG_CHANNEL_ID,
                "owner": BUILDER_OWNER,
                "stored": False,
            }
        runtime_token = str(runtime_scenario_id or "").strip()
        if runtime_token and runtime_token != BUILDER_RUNTIME_FALLBACK_SCENARIO_ID:
            scenario_topic_id = f"prompt-project:scenario:{runtime_token}"
            topic = {k: v for k, v in dict(topic).items() if v is not None}
            topic["topic_id"] = scenario_topic_id
            topic["thread_id"] = scenario_topic_id
            topic["topic_kind"] = "builder_scenario"
            topic["scenario_id"] = runtime_token
            topic["project_id"] = runtime_token
            topic.setdefault("schema", "adaos.conversation.topic_ref.v1")
            topic.setdefault("webspace_id", source_id)
            topic.setdefault("source_webspace_id", source_id)
            topic.setdefault("dev_webspace_id", dev_id)
            topic.setdefault("conversation_id", f"conv.skill.{BUILDER_SKILL_ID}.default.{source_id}")
            topic.setdefault("channel_id", BUILDER_DIALOG_CHANNEL_ID)
            topic.setdefault("owner", BUILDER_OWNER)
        conversation_id = str(topic.get("conversation_id") or f"conv.skill.{BUILDER_SKILL_ID}.default.{source_id}").strip()
        thread_id = str(topic.get("thread_id") or "").strip()
        topic_id = str(topic.get("topic_id") or "").strip()
        send_meta = {
            "source_webspace_id": source_id,
            "builder_source_webspace_id": source_id,
            "dev_webspace_id": dev_id,
            "dialog_channel_id": BUILDER_DIALOG_CHANNEL_ID,
            "conversation_id": conversation_id,
            "conversation_owner": BUILDER_OWNER,
            "thread_id": thread_id,
            "topic_id": topic_id,
            "conversation_thread_id": thread_id,
            "conversation_topic_id": thread_id or topic_id,
            "builder_topic": {k: v for k, v in topic.items() if k != "stored"},
            "route_id": "voice_chat",
        }
        return {
            "widget": "voice_chat",
            "mode": "embedded",
            "source_webspace_id": source_id,
            "dev_webspace_id": dev_id,
            "dialog_channel_id": BUILDER_DIALOG_CHANNEL_ID,
            "conversation_id": conversation_id,
            "thread_id": thread_id,
            "topic_id": topic_id,
            "topic": {k: v for k, v in topic.items() if k != "stored"},
            "owner": BUILDER_OWNER,
            "default_tool": f"{BUILDER_SKILL_ID}.chat",
            "meta": send_meta,
            "allow_voice": True,
            "allow_text": True,
        }

    def list_development_skills(self, source_webspace_id: str | None = None) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        binding = self.get_workspace_binding(source_id)
        drafts: list[dict[str, Any]] = []
        drafts_root = Path(self.state_dir or current_state_dir()) / "builder" / "drafts"
        if drafts_root.exists():
            for path in sorted(drafts_root.glob("*/builder.draft.json")):
                draft = _read_json(path)
                artifact = draft.get("artifact") if isinstance(draft.get("artifact"), dict) else {}
                metadata = draft.get("metadata") if isinstance(draft.get("metadata"), dict) else {}
                links = draft.get("links") if isinstance(draft.get("links"), dict) else {}
                conversation = links.get("conversation") if isinstance(links.get("conversation"), dict) else {}
                draft_webspace = str(metadata.get("webspace_id") or conversation.get("webspace_id") or source_id).strip()
                if draft_webspace and draft_webspace != source_id:
                    continue
                drafts.append(
                    {
                        "draft_id": draft.get("draft_id"),
                        "status": draft.get("status"),
                        "kind": artifact.get("kind"),
                        "id": artifact.get("id"),
                        "root": artifact.get("draft_root") or artifact.get("root"),
                        "source_idea": metadata.get("source_idea"),
                        "active": draft.get("draft_id") == binding.get("active_draft_id"),
                        "updated_at": draft.get("updated_at") or draft.get("created_at"),
                    }
                )
        return {"ok": True, "source_webspace_id": source_id, "active_draft_id": binding.get("active_draft_id"), "items": drafts}

    def delete_development_skill(self, draft_id: str, source_webspace_id: str | None = None) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        token = str(draft_id or "").strip()
        if not token:
            return {"ok": False, "error": "draft_id_required"}
        drafts_root = Path(self.state_dir or current_state_dir()) / "builder" / "drafts"
        draft_dir = (drafts_root / token).resolve()
        root = drafts_root.resolve()
        if root not in draft_dir.parents or not draft_dir.exists():
            return {"ok": False, "error": "draft_not_found", "draft_id": token}
        draft = _read_json(draft_dir / "builder.draft.json")
        artifact = draft.get("artifact") if isinstance(draft.get("artifact"), dict) else {}
        raw_artifact_root = str(artifact.get("draft_root") or artifact.get("root") or "").strip()
        if not raw_artifact_root:
            return {"ok": False, "error": "artifact_root_missing", "draft_id": token}

        candidate = Path(raw_artifact_root).expanduser()
        artifact_root = (candidate if candidate.is_absolute() else draft_dir / candidate).resolve()
        artifact_id = str(artifact.get("id") or "").strip()
        artifact_kind = str(artifact.get("kind") or "").strip()
        expected_parent = {"scenario": "scenarios", "skill": "skills"}.get(artifact_kind)
        if (
            not artifact_id
            or expected_parent is None
            or artifact_root.name != artifact_id
            or artifact_root.parent.name != expected_parent
            or artifact_root == root
            or root in artifact_root.parents
            or artifact_root in root.parents
        ):
            return {
                "ok": False,
                "error": "unsafe_artifact_root",
                "draft_id": token,
                "artifact_root": str(artifact_root),
            }

        archive_root = Path(self.state_dir or current_state_dir()) / "builder" / "archive"
        archive_dir = archive_root / f"{int(_now() * 1000)}-{_safe_path_token(token)}"
        archive_dir.mkdir(parents=True, exist_ok=False)
        try:
            shutil.copytree(draft_dir, archive_dir / "draft")
            artifact_exists = artifact_root.exists()
            if artifact_exists:
                shutil.copytree(artifact_root, archive_dir / "artifact")
            _write_json(
                archive_dir / "archive.json",
                {
                    "schema": "adaos.builder.archive.v1",
                    "draft_id": token,
                    "source_webspace_id": source_id,
                    "artifact": dict(artifact),
                    "original_draft_root": str(draft_dir),
                    "original_artifact_root": str(artifact_root),
                    "artifact_existed": artifact_exists,
                    "archived_at": _now(),
                },
            )
        except Exception as exc:
            return {
                "ok": False,
                "error": "draft_archive_failed",
                "detail": f"{type(exc).__name__}: {exc}",
                "draft_id": token,
                "archive_root": str(archive_dir),
            }

        removed_artifact = False
        try:
            if artifact_root.exists():
                shutil.rmtree(artifact_root)
                removed_artifact = True
            shutil.rmtree(draft_dir)
        except Exception as exc:
            return {
                "ok": False,
                "error": "draft_delete_failed",
                "detail": f"{type(exc).__name__}: {exc}",
                "draft_id": token,
                "archive_root": str(archive_dir),
                "removed_artifact": removed_artifact,
            }
        binding = self.get_workspace_binding(source_id)
        if binding.get("active_draft_id") == token:
            self.set_active_draft(source_webspace_id=source_id, active_draft_id=None, persist_projection=False)
        return {
            "ok": True,
            "draft_id": token,
            "removed_artifact": removed_artifact,
            "archive_root": str(archive_dir),
        }

    def snapshot(self, source_webspace_id: str | None = None, *, preview_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        binding = self.get_workspace_binding(source_id)
        snapshot = {
            "schema": "adaos.builder.workbench.v1",
            "source_webspace_id": source_id,
            "binding": binding,
            "selection": dict(binding.get("selection") or {}),
            "dialog": binding.get("dialog") if isinstance(binding.get("dialog"), dict) else self.dialog_widget_config(source_id),
            "development_skills": self.list_development_skills(source_id).get("items", []),
            "preview_runtime": self.reconciler.describe(source_id),
            "preview_state": dict(preview_state or {}),
            "updated_at": _now(),
        }
        _write_json(self.snapshot_path(source_id), snapshot)
        return snapshot

    def runtime_projection(
        self,
        source_webspace_id: str | None = None,
        *,
        preview_state: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        binding = self.get_workspace_binding(source_id)
        return {
            "schema": "adaos.builder.runtime_projection.v1",
            "source_webspace_id": source_id,
            "selection": dict(binding.get("selection") or {}),
            "binding": {
                key: binding.get(key)
                for key in (
                    "source_webspace_id",
                    "preview_webspace_id",
                    "runtime_scenario_id",
                    "active_draft_id",
                    "purpose",
                )
            },
            "preview_runtime": _preview_runtime_projection(self.reconciler.describe(source_id)),
            "preview_state": _preview_state_projection(preview_state),
            "updated_at": _now(),
        }

    async def publish_projection(self, source_webspace_id: str | None = None, *, preview_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
        source_id = self.resolve_source_webspace_id(source_webspace_id)
        snapshot = self.runtime_projection(source_id, preview_state=preview_state)
        published: list[str] = []
        try:
            from adaos.services.yjs.doc import async_get_ydoc

            async with async_get_ydoc(source_id, prefer_live_room=True, load_mark_roots=["data"]) as ydoc:
                data = ydoc.get_map("data")
                with ydoc.begin_transaction() as txn:
                    data.set(txn, "builder", snapshot)
            published.append(source_id)
        except Exception:
            pass
        return {"ok": True, "snapshot": snapshot, "published_webspaces": published}

    def publish_projection_sync(self, source_webspace_id: str | None = None, *, preview_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.publish_projection(source_webspace_id, preview_state=preview_state))
        snapshot = self.runtime_projection(source_webspace_id, preview_state=preview_state)
        return {"ok": True, "snapshot": snapshot, "published_webspaces": [], "deferred": True}


def _payload_from_event(evt: Any) -> dict[str, Any]:
    if isinstance(evt, Mapping):
        payload = evt.get("payload") if isinstance(evt.get("payload"), Mapping) else evt
        return dict(payload)
    payload = getattr(evt, "payload", None)
    if isinstance(payload, Mapping):
        return dict(payload)
    return {}


def _schedule_projection_publish(
    service: BuilderWorkbenchService,
    source_webspace_id: str,
    *,
    preview_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source_id = service.resolve_source_webspace_id(source_webspace_id)
    _PROJECTION_PENDING[source_id] = {
        "preview_state": dict(preview_state or {}),
    }
    current = _PROJECTION_TASKS.get(source_id)
    if current is not None and not current.done():
        return {"scheduled": True, "coalesced": True, "source_webspace_id": source_id}

    async def _runner() -> None:
        try:
            while True:
                request = _PROJECTION_PENDING.pop(source_id, None)
                if request is None:
                    break
                try:
                    await service.publish_projection(
                        source_id,
                        preview_state=request.get("preview_state"),
                    )
                except Exception:
                    _log.warning(
                        "failed to publish deferred Builder projection source_webspace=%s",
                        source_id,
                        exc_info=True,
                    )
                if source_id not in _PROJECTION_PENDING:
                    break
        finally:
            if _PROJECTION_TASKS.get(source_id) is asyncio.current_task():
                _PROJECTION_TASKS.pop(source_id, None)

    task = asyncio.create_task(
        _runner(),
        name=f"builder-projection:{source_id}"[:120],
    )
    _PROJECTION_TASKS[source_id] = task
    return {
        "scheduled": True,
        "coalesced": False,
        "source_webspace_id": source_id,
        "task": task.get_name(),
    }


@subscribe(BUILDER_CONTEXT_SELECTED)
async def _on_builder_context_selected(evt: Any) -> None:
    payload = _payload_from_event(evt)
    source_webspace_id = str(payload.get("source_webspace_id") or payload.get("webspace_id") or "").strip()
    object_type = str(payload.get("object_type") or payload.get("project_kind") or "").strip()
    object_id = str(payload.get("object_id") or payload.get("project_id") or "").strip()
    if not source_webspace_id or not object_type or not object_id:
        return
    service = BuilderWorkbenchService()
    service.set_selected_project(
        source_webspace_id=source_webspace_id,
        object_type=object_type,
        object_id=object_id,
        title=str(payload.get("title") or "").strip() or None,
        description=str(payload.get("description") or "").strip() or None,
        persist_projection=False,
    )
    _schedule_projection_publish(service, source_webspace_id)


@subscribe("desktop.webspace.reloaded")
async def _on_builder_source_webspace_reloaded(evt: Any) -> None:
    """Restore dynamic Builder context after scenario materialization.

    A governed webspace reset rebuilds scenario-owned branches before emitting
    ``desktop.webspace.reloaded``.  Builder selection is runtime-owned state,
    so the durable workbench binding must be projected once the rebuild has
    completed instead of allowing the scenario seed to become authoritative.
    """

    payload = _payload_from_event(evt)
    source_webspace_id = str(payload.get("webspace_id") or "").strip()
    scenario_id = str(
        payload.get("scenario_id")
        or payload.get("current_scenario")
        or payload.get("materialized_scenario")
        or ""
    ).strip()
    if not source_webspace_id or scenario_id not in {
        BUILDER_HOST_SCENARIO_ID,
        BUILDER_WORKBENCH_SCENARIO_ID,
    }:
        return
    service = BuilderWorkbenchService()
    # A Builder opened inside another Builder's preview is a bounded self-host.
    # Claim that topology as soon as scenario materialization tells us the
    # authoritative scenario id. UI actions do not always carry scenario_id in
    # their tool metadata, so deferring the claim until the first action can
    # incorrectly resolve ``dev1-dev`` back to ``dev1`` and overwrite the
    # Builder itself instead of allocating ``dev1-dev-dev`` for its project.
    source_webspace_id = service.resolve_action_source_webspace_id(
        source_webspace_id,
        current_scenario_id=scenario_id,
    )
    if not service.binding_path(source_webspace_id).is_file():
        return
    _schedule_projection_publish(service, source_webspace_id)


@subscribe("builder.workbench.ensure_requested")
async def _on_builder_workbench_ensure_requested(evt: Any) -> None:
    payload = _payload_from_event(evt)
    source_webspace_id = str(payload.get("source_webspace_id") or payload.get("webspace_id") or "").strip() or None
    if not source_webspace_id:
        return
    await BuilderWorkbenchService().ensure_dev_webspace(
        source_webspace_id,
        active_draft_id=str(payload.get("active_draft_id") or "").strip() or None,
        runtime_scenario_id=str(payload.get("runtime_scenario_id") or "").strip() or None,
        preview_state=payload.get("preview_state") if isinstance(payload.get("preview_state"), Mapping) else None,
    )


@subscribe(BUILDER_PREVIEW_DESIRED)
@subscribe("builder.preview.selected")
async def _on_builder_preview_selected(evt: Any) -> None:
    payload = _payload_from_event(evt)
    if bool(payload.get("reconciled")):
        return
    scenario_id = str(payload.get("scenario_id") or payload.get("object_id") or "").strip()
    object_type = str(payload.get("object_type") or "scenario").strip().lower()
    if object_type != "scenario" or not scenario_id:
        return
    source_webspace_id = str(payload.get("source_webspace_id") or payload.get("webspace_id") or "").strip() or None
    service = BuilderWorkbenchService()
    reason = str(payload.get("reason") or "").strip()
    if reason in {"builder_project_created", "builder_project_switched"}:
        binding = service.get_workspace_binding(source_webspace_id)
        desired_scenario = str(binding.get("runtime_scenario_id") or "").strip()
        if desired_scenario and desired_scenario != scenario_id:
            _log.info(
                "builder preview selection superseded source_webspace=%s scenario=%s desired_scenario=%s reason=%s",
                source_webspace_id,
                scenario_id,
                desired_scenario,
                reason,
            )
            return
    await service.ensure_dev_webspace(
        source_webspace_id,
        active_draft_id=str(payload.get("draft_id") or "").strip() or None,
        runtime_scenario_id=scenario_id,
        preview_state=payload.get("preview_state") if isinstance(payload.get("preview_state"), Mapping) else None,
        wait_for_rebuild=bool(payload.get("wait_for_rebuild", False)),
    )


@subscribe(BUILDER_PREVIEW_OBSERVED)
async def _on_builder_preview_observed(evt: Any) -> None:
    payload = _payload_from_event(evt)
    source_webspace_id = str(payload.get("source_webspace_id") or "").strip()
    operation_id = str(payload.get("operation_id") or "").strip()
    if not source_webspace_id or not operation_id:
        return
    service = BuilderWorkbenchService()
    current = service.reconciler.describe(source_webspace_id)
    if (
        str(current.get("operation_id") or "").strip() != operation_id
        or str(current.get("status") or "").strip() != "ready"
    ):
        return
    _schedule_projection_publish(service, source_webspace_id)
