from __future__ import annotations

import asyncio
import inspect
import json
import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

import yaml

from adaos.adapters.db import SqliteSkillRegistry
from adaos.build_info import BUILD_INFO
from adaos.domain.project_deployment import NodeEndpointRecord, NodeInventoryRecord
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.artifact_pipeline.channels import ReleaseRepository
from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.distributed_runtime.bootstrap import configure_distributed_runtime
from adaos.services.distributed_runtime import (
    HttpServiceInvocationTransport,
    HttpTopologyPhaseTransport,
    RoutingServiceInvocationAdapter,
    SkillToolTopologyAdapter,
    TopologyExecutionError,
    UncertainTopologyPhaseError,
    register_topology_phase_receiver,
    register_service_invocation_receiver,
)
from adaos.services.project_deployment.adapters import (
    LocalComponentDeploymentAdapter,
    RoutingComponentDeploymentAdapter,
)
from adaos.services.project_deployment.bootstrap import configure_project_deployment_runtime
from adaos.services.project_deployment.inventory import SnapshotNodeInventoryProvider
from adaos.services.project_deployment.transport import (
    HttpNodeDeploymentTransport,
    register_local_deployment_receiver,
)
from adaos.services.skill.manager import SkillManager
from adaos.services.skill.runtime import resolve_active_version


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(sorted({item.strip() for item in value.split(",") if item.strip()}))


def _labels() -> dict[str, str]:
    raw = str(os.getenv("ADAOS_DEPLOYMENT_LABELS_JSON") or "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return {str(key): str(item) for key, item in value.items()} if isinstance(value, Mapping) else {}


def deployment_runtime_inventory_payload(ctx: AgentContext | None = None) -> dict[str, Any]:
    current = ctx or get_ctx()
    conf = current.config
    base_dir = Path(current.paths.base_dir()).resolve()
    try:
        usage = shutil.disk_usage(base_dir)
        storage_bytes = int(usage.free)
    except OSError:
        storage_bytes = 0
    cpu_count = max(1, int(os.cpu_count() or 1))
    memory_mb = 0
    try:
        import psutil

        memory_mb = int(psutil.virtual_memory().available // (1024 * 1024))
    except Exception:
        pass
    capabilities = _tokens(
        str(
            os.getenv("ADAOS_DEPLOYMENT_CAPABILITIES")
            or "project.activate,distributed.runtime,media.catalog,media.playback"
        )
    )
    base_url = str(os.getenv("ADAOS_NODE_DEPLOYMENT_URL") or "").strip().rstrip("/")
    endpoints = []
    if base_url.startswith(("http://", "https://")):
        endpoints.append(
            {
                "endpoint_id": base_url,
                "role": "deployment",
                "available": True,
                "capabilities": ["project.component.phase.v1"],
                "labels": {"base_url": base_url},
                "capacity": {"max_parallel": 1},
            }
        )
    labels = _labels()
    labels.setdefault("node.role", str(getattr(conf, "role", "") or "node"))
    return {
        "architecture": platform.machine().lower() or "unknown",
        "runtime_version": BUILD_INFO.version,
        "capabilities": list(capabilities),
        "protocols": {
            "project_activation": "1",
            "distributed_runtime": "1",
            "distributed_topology": "1",
        },
        "labels": labels,
        "capacity": {
            "cpu_millicores": cpu_count * 1000,
            "memory_mb": memory_mb,
            "storage_bytes": storage_bytes,
        },
        "endpoints": endpoints,
    }


def local_node_inventory_record(ctx: AgentContext | None = None) -> NodeInventoryRecord:
    current = ctx or get_ctx()
    conf = current.config
    payload = deployment_runtime_inventory_payload(current)
    endpoints = tuple(
        NodeEndpointRecord.from_mapping(item) for item in payload["endpoints"]
    )
    return NodeInventoryRecord(
        node_id=str(conf.node_id),
        subnet_id=str(conf.subnet_id),
        trust_state="trusted",
        online=True,
        architecture=str(payload["architecture"]),
        runtime_version=str(payload["runtime_version"]),
        capabilities=tuple(payload["capabilities"]),
        protocols=dict(payload["protocols"]),
        labels=dict(payload["labels"]),
        capacity=dict(payload["capacity"]),
        endpoints=endpoints,
        revision=1,
    )


@dataclass(slots=True)
class CachedReleaseProvider:
    repository: ReleaseRepository
    fallback: Any = None

    def get_release(self, project_id: str, release_digest: str) -> ReleasePlan:
        try:
            return self.repository.get_release(project_id, release_digest)
        except FileNotFoundError:
            if self.fallback is None:
                raise
            plan = self.fallback.get_release(project_id, release_digest)
            self.repository.put_release(plan)
            return plan


@dataclass(slots=True)
class AdaOSComponentLifecycleHooks:
    ctx: AgentContext

    def _skill_manager(self) -> SkillManager:
        return SkillManager(
            repo=self.ctx.skills_repo,
            registry=SqliteSkillRegistry(self.ctx.sql),
            git=self.ctx.git,
            paths=self.ctx.paths,
            bus=getattr(self.ctx, "bus", None),
            caps=self.ctx.caps,
            settings=self.ctx.settings,
        )

    def activate(self, *, kind: str, component_id: str, version: str) -> Mapping[str, Any]:
        if kind == "skill":
            slot = self._skill_manager().activate_runtime(component_id, version=version)
            return {"activated": True, "version": version, "slot": slot}
        if kind == "scenario":
            return {"activated": True, "version": version, "mode": "source_available"}
        raise RuntimeError(f"unsupported component kind: {kind}")

    def health(self, *, kind: str, component_id: str, version: str) -> Mapping[str, Any]:
        if kind == "skill":
            observed = str(resolve_active_version(component_id, ctx=self.ctx) or "")
            return {"ready": observed == version, "version": observed}
        path = Path(self.ctx.paths.scenarios_dir()) / component_id / "scenario.yaml"
        try:
            manifest = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            manifest = {}
        observed = str(manifest.get("version") or "") if isinstance(manifest, Mapping) else ""
        return {"ready": observed == version, "version": observed}

    def cordon(self, *, kind: str, component_id: str) -> Mapping[str, Any]:
        return {"cordoned": True, "kind": kind, "component_id": component_id}

    def drain(self, *, kind: str, component_id: str) -> Mapping[str, Any]:
        return {"drained": True, "kind": kind, "component_id": component_id}

    def deactivate(self, *, kind: str, component_id: str) -> Mapping[str, Any]:
        if kind != "skill":
            return {"deactivated": True, "kind": kind, "component_id": component_id}
        try:
            return self._skill_manager().deactivate_runtime(
                component_id,
                reason="project_deployment_removed",
                source="project_deployment",
                status="removed",
                transient=False,
            )
        except RuntimeError as exc:
            if "no active version" not in str(exc):
                raise
            return {"deactivated": False, "already_inactive": True}


def _snapshot() -> Mapping[str, Any]:
    try:
        from adaos.services.subnet.link_manager import hub_link_manager_snapshot

        return hub_link_manager_snapshot()
    except Exception:
        return {}


def _publisher(ctx: AgentContext, topic: str):
    def publish(payload: Mapping[str, Any]) -> None:
        try:
            result = ctx.bus.emit(topic, dict(payload), source="distributed.runtime", actor="system")
            if not inspect.isawaitable(result):
                return
            try:
                asyncio.get_running_loop().create_task(result)
            except RuntimeError:
                asyncio.run(result)
        except Exception:
            return

    return publish


_configure_lock = RLock()
_configured_key = ""


def configure_default_distributed_runtimes(
    ctx: AgentContext | None = None,
    *,
    release_fallback: Any = None,
) -> dict[str, Any]:
    global _configured_key
    current = ctx or get_ctx()
    conf = current.config
    state_dir = Path(current.paths.state_dir()).resolve()
    key = f"{state_dir}:{conf.node_id}:{conf.subnet_id}"
    with _configure_lock:
        if _configured_key == key:
            return {"ok": True, "configured": False, "node_id": str(conf.node_id)}
        artifact_root = state_dir / "artifact_pipeline"
        package_store = ContentAddressedPackageStore(artifact_root / "packages")
        releases = CachedReleaseProvider(
            ReleaseRepository(artifact_root / "release-cache"),
            fallback=release_fallback,
        )
        inventory = SnapshotNodeInventoryProvider(
            _snapshot,
            local_records=lambda: (local_node_inventory_record(current),),
        )
        local_adapter = LocalComponentDeploymentAdapter(
            local_node_id=str(conf.node_id),
            workspace_root=Path(current.paths.workspace_dir()),
            state_root=state_dir,
            package_store=package_store,
            fetch_package=lambda package: package_store.read(package.digest),
            hooks=AdaOSComponentLifecycleHooks(current),
        )

        def endpoint(node_id: str) -> str:
            for node in inventory.list_nodes(str(conf.subnet_id)):
                if node.node_id != node_id:
                    continue
                for item in node.endpoints:
                    if item.role != "deployment" or not item.available:
                        continue
                    value = str(item.labels.get("base_url") or item.endpoint_id).strip()
                    if value.startswith(("http://", "https://")):
                        return value
            return ""

        remote = HttpNodeDeploymentTransport(
            endpoint_resolver=endpoint,
            token_provider=lambda: str(conf.token or ""),
            package_reader=package_store.read,
            source_node_id=str(conf.node_id),
        )
        deployment = configure_project_deployment_runtime(
            releases=releases,
            inventory=inventory,
            adapter=RoutingComponentDeploymentAdapter(
                local_node_id=str(conf.node_id),
                local=local_adapter,
                remote=remote,
            ),
            state_dir=state_dir,
            local_node_id=str(conf.node_id),
            projection_publisher=_publisher(ctx=current, topic="project.deployment.projection"),
        )
        distributed = configure_distributed_runtime(
            releases=releases,
            inventory=inventory,
            state_dir=state_dir,
            deployment_store=deployment.store,
            projection_publisher=_publisher(ctx=current, topic="distributed.topology.projection"),
        )

        def execute_topology_tool(
            skill_id: str,
            tool: str,
            payload: Mapping[str, Any],
        ) -> Mapping[str, Any]:
            selected_instance_id = str(payload.get("selected_instance_id") or "")
            selected = next(
                (
                    item
                    for item in (
                        payload.get("source_instance"),
                        payload.get("target_instance"),
                    )
                    if isinstance(item, Mapping)
                    and str(item.get("instance_id") or "") == selected_instance_id
                ),
                None,
            )
            if selected is None:
                raise TopologyExecutionError("topology_skill_instance_missing")
            try:
                activation = deployment.store.get_activation(
                    str(selected.get("activation_id") or "")
                )
            except FileNotFoundError as exc:
                raise TopologyExecutionError(
                    "topology_skill_activation_missing"
                ) from exc
            if (
                activation.status != "active"
                or activation.node_id != str(conf.node_id)
                or activation.component_ref != f"skill:{skill_id}"
                or activation.release_digest
                != str(selected.get("release_digest") or "")
                or activation.generation
                != int(selected.get("runtime_generation") or 0)
            ):
                raise TopologyExecutionError(
                    "topology_skill_activation_identity_mismatch"
                )
            try:
                result = AdaOSComponentLifecycleHooks(current)._skill_manager().run_tool(
                    skill_id,
                    tool,
                    payload,
                    timeout=600.0,
                    bypass_yjs_guard=True,
                )
            except TimeoutError as exc:
                raise UncertainTopologyPhaseError(
                    "topology_skill_adapter_timeout"
                ) from exc
            except (KeyError, FileNotFoundError) as exc:
                raise TopologyExecutionError(
                    "topology_skill_adapter_unavailable"
                ) from exc
            if not isinstance(result, Mapping):
                raise TopologyExecutionError("topology_skill_adapter_result_invalid")
            return result

        distributed.topology_adapter = SkillToolTopologyAdapter(
            store=distributed.store,
            local_node_id=str(conf.node_id),
            local_executor=execute_topology_tool,
            remote=HttpTopologyPhaseTransport(
                endpoint_resolver=endpoint,
                token_provider=lambda: str(conf.token or ""),
                source_node_id=str(conf.node_id),
            ),
        )

        def execute_service_tool(
            instance: Any,
            operation_id: str,
            arguments: Mapping[str, Any],
            timeout_seconds: float,
        ) -> Any:
            try:
                activation = deployment.store.get_activation(instance.activation_id)
            except FileNotFoundError as exc:
                raise TopologyExecutionError(
                    "service_invocation_activation_missing"
                ) from exc
            if (
                activation.status != "active"
                or activation.node_id != str(conf.node_id)
                or activation.component_ref != instance.component_ref
                or activation.release_digest != instance.release_digest
                or activation.generation != instance.runtime_generation
            ):
                raise TopologyExecutionError(
                    "service_invocation_activation_identity_mismatch"
                )
            kind, separator, skill_id = instance.component_ref.partition(":")
            if kind != "skill" or separator != ":" or not skill_id:
                raise TopologyExecutionError(
                    "service_invocation_component_not_skill"
                )
            try:
                return AdaOSComponentLifecycleHooks(current)._skill_manager().run_tool(
                    skill_id,
                    operation_id,
                    arguments,
                    timeout=timeout_seconds,
                )
            except TimeoutError as exc:
                raise UncertainTopologyPhaseError(
                    "service_invocation_skill_timeout"
                ) from exc

        distributed.service_invoker = RoutingServiceInvocationAdapter(
            local_node_id=str(conf.node_id),
            local_executor=execute_service_tool,
            remote=HttpServiceInvocationTransport(
                endpoint_resolver=endpoint,
                token_provider=lambda: str(conf.token or ""),
                source_node_id=str(conf.node_id),
            ),
        )
        register_local_deployment_receiver(local_adapter, node_id=str(conf.node_id))
        register_topology_phase_receiver(
            execute_topology_tool,
            node_id=str(conf.node_id),
        )
        register_service_invocation_receiver(
            execute_service_tool,
            node_id=str(conf.node_id),
        )
        _configured_key = key
        return {
            "ok": True,
            "configured": True,
            "node_id": str(conf.node_id),
            "subnet_id": str(conf.subnet_id),
            "state_dir": str(state_dir),
            "package_store": str(package_store.root),
            "deployment_runtime": type(deployment).__name__,
            "distributed_runtime": type(distributed).__name__,
        }


__all__ = [
    "AdaOSComponentLifecycleHooks",
    "CachedReleaseProvider",
    "configure_default_distributed_runtimes",
    "deployment_runtime_inventory_payload",
    "local_node_inventory_record",
]
