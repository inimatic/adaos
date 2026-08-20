from __future__ import annotations

import base64
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, Mapping

import httpx

from adaos.domain.artifact_release import ArtifactPackageRef
from adaos.domain.project_deployment import (
    ComponentActivation,
    DeploymentPlanChange,
    NodeInventoryRecord,
    ProjectDeployment,
)
from adaos.services.artifact_pipeline.releases import ReleasePlan

from .adapters import LocalComponentDeploymentAdapter
from .execution import (
    ProjectDeploymentExecutionError,
    RetryableDeploymentPhaseError,
    UncertainDeploymentPhaseError,
)


REMOTE_PHASE_SCHEMA = "adaos.project.remote_component_phase.v1"
REMOTE_PHASE_RESULT_SCHEMA = "adaos.project.remote_component_phase_result.v1"
MAX_REMOTE_PACKAGE_BYTES = 64 * 1024 * 1024
MAX_MEMBER_LINK_PACKAGE_BYTES = 1024 * 1024


def _release_mapping(value: ReleasePlan) -> dict[str, Any]:
    return {"schema": "adaos.artifact.release_plan.v1", **value.explain()}


def _safe_receipt(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "<truncated>"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:100]:
            token = str(key)
            if any(secret in token.lower() for secret in ("token", "secret", "password", "credential")):
                result[token] = "<redacted>"
            else:
                result[token] = _safe_receipt(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe_receipt(item, depth=depth + 1) for item in list(value)[:100]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value if not isinstance(value, str) else value[:2000]
    return str(value)[:500]


def _remote_phase_payload(
    *,
    node_id: str,
    source_node_id: str,
    package_reader: Callable[[str], bytes],
    package_limit: int,
    kwargs: Mapping[str, Any],
) -> dict[str, Any]:
    package: ArtifactPackageRef | None = kwargs.get("package")
    archive_b64: str | None = None
    if kwargs.get("phase") == "fetch" and package is not None:
        archive = package_reader(package.digest)
        if len(archive) > package_limit:
            reason = (
                "remote_package_exceeds_member_link_limit"
                if package_limit == MAX_MEMBER_LINK_PACKAGE_BYTES
                else "remote_package_exceeds_transport_limit"
            )
            raise ProjectDeploymentExecutionError(reason)
        archive_b64 = base64.b64encode(archive).decode("ascii")
    return {
        "schema": REMOTE_PHASE_SCHEMA,
        "source_node_id": source_node_id,
        "target_node_id": node_id,
        "phase": kwargs["phase"],
        "node": kwargs.get("node").to_dict() if kwargs.get("node") is not None else None,
        "change": kwargs["change"].to_dict(),
        "desired": kwargs["desired"].to_dict(),
        "release_plan": _release_mapping(kwargs["release_plan"]),
        "package": package.to_dict() if package is not None else None,
        "current_activation": (
            kwargs["current_activation"].to_dict()
            if kwargs.get("current_activation") is not None
            else None
        ),
        "idempotency_key": str(kwargs["idempotency_key"]),
        "attempt": int(kwargs["attempt"]),
        "package_archive_b64": archive_b64,
    }


def _remote_phase_receipt(body: Any) -> dict[str, Any]:
    if not isinstance(body, Mapping) or body.get("schema") != REMOTE_PHASE_RESULT_SCHEMA:
        raise ProjectDeploymentExecutionError("remote_node_response_contract_invalid")
    receipt = body.get("receipt")
    if not isinstance(receipt, Mapping):
        raise ProjectDeploymentExecutionError("remote_node_receipt_missing")
    return dict(receipt)


@dataclass(slots=True)
class HttpNodeDeploymentTransport:
    endpoint_resolver: Callable[[str], str]
    token_provider: Callable[[], str]
    package_reader: Callable[[str], bytes]
    source_node_id: str
    connect_timeout_seconds: float = 10.0
    operation_timeout_seconds: float = 600.0

    def execute_component_phase(self, *, node_id: str, **kwargs: Any) -> Mapping[str, Any]:
        endpoint = str(self.endpoint_resolver(node_id) or "").strip().rstrip("/")
        if not endpoint.startswith(("http://", "https://")):
            raise RetryableDeploymentPhaseError("remote_node_endpoint_unavailable")
        payload = _remote_phase_payload(
            node_id=node_id,
            source_node_id=self.source_node_id,
            package_reader=self.package_reader,
            package_limit=MAX_REMOTE_PACKAGE_BYTES,
            kwargs=kwargs,
        )
        headers = {
            "X-AdaOS-Token": str(self.token_provider() or ""),
            "X-AdaOS-Source-Node": self.source_node_id,
            "X-AdaOS-Operation-Id": str(kwargs["idempotency_key"]),
        }
        timeout = httpx.Timeout(
            timeout=max(30.0, float(self.operation_timeout_seconds)),
            connect=max(1.0, float(self.connect_timeout_seconds)),
        )
        try:
            response = httpx.post(
                f"{endpoint}/api/node/project-deployment/phase",
                json=payload,
                headers=headers,
                timeout=timeout,
            )
        except httpx.ConnectError as exc:
            raise RetryableDeploymentPhaseError("remote_node_connect_failed") from exc
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
            raise UncertainDeploymentPhaseError(
                "remote component phase timed out after dispatch",
                details={"node_id": node_id, "phase": kwargs.get("phase")},
            ) from exc
        except httpx.RequestError as exc:
            raise RetryableDeploymentPhaseError("remote_node_transport_failed") from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise ProjectDeploymentExecutionError("remote_node_response_invalid") from exc
        if response.status_code in {429, 502, 503, 504}:
            raise RetryableDeploymentPhaseError(str(body.get("detail") or "remote_node_busy"))
        if response.status_code >= 400:
            raise ProjectDeploymentExecutionError(
                str(body.get("detail") or f"remote_node_http_{response.status_code}")
            )
        return _remote_phase_receipt(body)


@dataclass(slots=True)
class MemberLinkNodeDeploymentTransport:
    rpc_call: Callable[..., Any]
    package_reader: Callable[[str], bytes]
    source_node_id: str
    operation_timeout_seconds: float = 600.0

    def execute_component_phase(self, *, node_id: str, **kwargs: Any) -> Mapping[str, Any]:
        payload = _remote_phase_payload(
            node_id=node_id,
            source_node_id=self.source_node_id,
            package_reader=self.package_reader,
            package_limit=MAX_MEMBER_LINK_PACKAGE_BYTES,
            kwargs=kwargs,
        )
        try:
            body = self.rpc_call(
                node_id,
                method="project.deployment.phase",
                params=payload,
                timeout=max(30.0, float(self.operation_timeout_seconds)),
            )
        except TimeoutError as exc:
            raise UncertainDeploymentPhaseError(
                "remote component phase timed out after dispatch",
                details={"node_id": node_id, "phase": kwargs.get("phase")},
            ) from exc
        except ConnectionError as exc:
            raise RetryableDeploymentPhaseError("remote_member_link_unavailable") from exc
        except RuntimeError as exc:
            reason = str(exc)
            if any(token in reason for token in ("member_not_connected", "member_rpc_busy", "link_replaced")):
                raise RetryableDeploymentPhaseError(reason) from exc
            raise ProjectDeploymentExecutionError(reason) from exc
        return _remote_phase_receipt(body)


_receiver_lock = RLock()
_receiver_adapter: LocalComponentDeploymentAdapter | None = None
_receiver_node_id = ""


def register_local_deployment_receiver(
    adapter: LocalComponentDeploymentAdapter | None,
    *,
    node_id: str = "",
) -> None:
    global _receiver_adapter, _receiver_node_id
    with _receiver_lock:
        _receiver_adapter = adapter
        _receiver_node_id = str(node_id or "").strip()


def execute_remote_component_phase(payload: Mapping[str, Any]) -> dict[str, Any]:
    with _receiver_lock:
        adapter = _receiver_adapter
        local_node_id = _receiver_node_id
    if adapter is None or not local_node_id:
        raise ProjectDeploymentExecutionError("local_deployment_receiver_not_configured")
    if payload.get("schema") != REMOTE_PHASE_SCHEMA:
        raise ProjectDeploymentExecutionError("remote_phase_schema_invalid")
    target_node_id = str(payload.get("target_node_id") or "").strip()
    if target_node_id != local_node_id:
        raise ProjectDeploymentExecutionError("remote_phase_target_identity_mismatch")
    raw_node = payload.get("node")
    raw_change = payload.get("change")
    raw_desired = payload.get("desired")
    raw_release = payload.get("release_plan")
    if not all(isinstance(item, Mapping) for item in (raw_node, raw_change, raw_desired, raw_release)):
        raise ProjectDeploymentExecutionError("remote_phase_contract_missing")
    node = NodeInventoryRecord.from_mapping(raw_node)
    change = DeploymentPlanChange.from_mapping(raw_change)
    desired = ProjectDeployment.from_mapping(raw_desired)
    release_plan = ReleasePlan.from_mapping(raw_release)
    raw_package = payload.get("package")
    package = ArtifactPackageRef.from_mapping(raw_package) if isinstance(raw_package, Mapping) else None
    raw_activation = payload.get("current_activation")
    current_activation = (
        ComponentActivation.from_mapping(raw_activation)
        if isinstance(raw_activation, Mapping)
        else None
    )
    if node.node_id != local_node_id:
        raise ProjectDeploymentExecutionError("remote_phase_node_contract_mismatch")
    release_digest = release_plan.release.release_digest or release_plan.release.computed_digest()
    if release_digest != desired.release_digest:
        raise ProjectDeploymentExecutionError("remote_phase_release_digest_mismatch")
    if package is not None and package.digest != change.target_package_digest:
        raise ProjectDeploymentExecutionError("remote_phase_package_digest_mismatch")
    encoded = payload.get("package_archive_b64")
    if encoded is not None:
        if payload.get("phase") != "fetch" or package is None or not isinstance(encoded, str):
            raise ProjectDeploymentExecutionError("remote_phase_package_payload_invalid")
        try:
            archive = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ProjectDeploymentExecutionError("remote_phase_package_encoding_invalid") from exc
        if len(archive) > MAX_REMOTE_PACKAGE_BYTES:
            raise ProjectDeploymentExecutionError("remote_package_exceeds_transport_limit")
        adapter.package_store.put(archive, expected_digest=package.digest)
    receipt = adapter.execute_phase(
        phase=str(payload.get("phase") or ""),
        node=node,
        change=change,
        desired=desired,
        release_plan=release_plan,
        package=package,
        current_activation=current_activation,
        idempotency_key=str(payload.get("idempotency_key") or ""),
        attempt=int(payload.get("attempt") or 0),
    )
    return {
        "schema": REMOTE_PHASE_RESULT_SCHEMA,
        "target_node_id": local_node_id,
        "phase": str(payload.get("phase") or ""),
        "receipt": _safe_receipt(receipt),
    }


__all__ = [
    "HttpNodeDeploymentTransport",
    "MemberLinkNodeDeploymentTransport",
    "MAX_MEMBER_LINK_PACKAGE_BYTES",
    "MAX_REMOTE_PACKAGE_BYTES",
    "REMOTE_PHASE_RESULT_SCHEMA",
    "REMOTE_PHASE_SCHEMA",
    "execute_remote_component_phase",
    "register_local_deployment_receiver",
]
