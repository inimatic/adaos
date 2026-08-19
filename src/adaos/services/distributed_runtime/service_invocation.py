from __future__ import annotations

import json
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, Mapping, Protocol

import httpx

from adaos.domain.distributed_runtime import ServiceInstance

from .operations import (
    RetryableTopologyPhaseError,
    TopologyExecutionError,
    UncertainTopologyPhaseError,
)


SERVICE_INVOCATION_SCHEMA = "adaos.distributed.service_invocation.v1"
SERVICE_INVOCATION_RESULT_SCHEMA = "adaos.distributed.service_invocation_result.v1"
MAX_SERVICE_INVOCATION_BYTES = 512 * 1024


class ServiceInvocationAdapter(Protocol):
    def invoke(
        self,
        *,
        instance: ServiceInstance,
        operation_id: str,
        arguments: Mapping[str, Any],
        request_id: str,
        timeout_seconds: float,
        actor_ref: str,
    ) -> Any: ...


def _bounded_json(value: Any, *, reason: str) -> Any:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TopologyExecutionError(f"{reason}_not_json") from exc
    if len(encoded) > MAX_SERVICE_INVOCATION_BYTES:
        raise TopologyExecutionError(f"{reason}_too_large")
    return value


@dataclass(slots=True)
class RoutingServiceInvocationAdapter:
    local_node_id: str
    local_executor: Callable[
        [ServiceInstance, str, Mapping[str, Any], float], Any
    ]
    remote: "HttpServiceInvocationTransport"

    def invoke(
        self,
        *,
        instance: ServiceInstance,
        operation_id: str,
        arguments: Mapping[str, Any],
        request_id: str,
        timeout_seconds: float,
        actor_ref: str,
    ) -> Any:
        _bounded_json(dict(arguments), reason="service_invocation_arguments")
        if instance.node_id == self.local_node_id:
            return _bounded_json(
                self.local_executor(
                    instance,
                    operation_id,
                    arguments,
                    timeout_seconds,
                ),
                reason="service_invocation_result",
            )
        return self.remote.invoke(
            instance=instance,
            operation_id=operation_id,
            arguments=arguments,
            request_id=request_id,
            timeout_seconds=timeout_seconds,
            actor_ref=actor_ref,
        )


@dataclass(slots=True)
class HttpServiceInvocationTransport:
    endpoint_resolver: Callable[[str], str]
    token_provider: Callable[[], str]
    source_node_id: str
    connect_timeout_seconds: float = 10.0

    def invoke(
        self,
        *,
        instance: ServiceInstance,
        operation_id: str,
        arguments: Mapping[str, Any],
        request_id: str,
        timeout_seconds: float,
        actor_ref: str,
    ) -> Any:
        endpoint = str(self.endpoint_resolver(instance.node_id) or "").strip().rstrip("/")
        if not endpoint.startswith(("http://", "https://")):
            raise RetryableTopologyPhaseError("remote_service_endpoint_unavailable")
        payload = {
            "schema": SERVICE_INVOCATION_SCHEMA,
            "requesting_node_id": self.source_node_id,
            "target_node_id": instance.node_id,
            "actor_ref": actor_ref,
            "request_id": request_id,
            "instance": instance.to_dict(),
            "operation_id": operation_id,
            "arguments": dict(arguments),
            "timeout_seconds": timeout_seconds,
        }
        _bounded_json(payload, reason="service_invocation_request")
        headers = {
            "X-AdaOS-Token": str(self.token_provider() or ""),
            "X-AdaOS-Source-Node": self.source_node_id,
            "X-AdaOS-Operation-Id": request_id,
        }
        timeout = httpx.Timeout(
            timeout=max(5.0, min(float(timeout_seconds) + 5.0, 605.0)),
            connect=max(1.0, float(self.connect_timeout_seconds)),
        )
        try:
            response = httpx.post(
                f"{endpoint}/api/node/distributed-service/invoke",
                json=payload,
                headers=headers,
                timeout=timeout,
            )
        except httpx.ConnectError as exc:
            raise RetryableTopologyPhaseError("remote_service_connect_failed") from exc
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
            raise UncertainTopologyPhaseError("remote_service_ack_timeout") from exc
        except httpx.RequestError as exc:
            raise RetryableTopologyPhaseError("remote_service_transport_failed") from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise TopologyExecutionError("remote_service_response_invalid") from exc
        if response.status_code in {429, 502, 503, 504}:
            raise RetryableTopologyPhaseError(
                str(body.get("detail") or "remote_service_busy")
            )
        if response.status_code >= 400:
            raise TopologyExecutionError(
                str(body.get("detail") or f"remote_service_http_{response.status_code}")
            )
        if not isinstance(body, Mapping) or body.get("schema") != SERVICE_INVOCATION_RESULT_SCHEMA:
            raise TopologyExecutionError("remote_service_response_schema_invalid")
        return _bounded_json(body.get("result"), reason="service_invocation_result")


def execute_service_invocation_request(
    payload: Mapping[str, Any],
    *,
    local_node_id: str,
    executor: Callable[[ServiceInstance, str, Mapping[str, Any], float], Any],
) -> dict[str, Any]:
    fields = {
        "schema",
        "requesting_node_id",
        "target_node_id",
        "actor_ref",
        "request_id",
        "instance",
        "operation_id",
        "arguments",
        "timeout_seconds",
    }
    if set(payload) != fields or payload.get("schema") != SERVICE_INVOCATION_SCHEMA:
        raise TopologyExecutionError("service_invocation_schema_invalid")
    if str(payload.get("target_node_id") or "") != local_node_id:
        raise TopologyExecutionError("service_invocation_target_node_mismatch")
    if not isinstance(payload.get("instance"), Mapping) or not isinstance(
        payload.get("arguments"), Mapping
    ):
        raise TopologyExecutionError("service_invocation_contract_invalid")
    instance = ServiceInstance.from_mapping(payload["instance"])
    if instance.node_id != local_node_id:
        raise TopologyExecutionError("service_invocation_instance_node_mismatch")
    operation_id = str(payload.get("operation_id") or "").strip()
    if not operation_id or len(operation_id) > 200:
        raise TopologyExecutionError("service_invocation_operation_invalid")
    timeout_seconds = max(1.0, min(float(payload.get("timeout_seconds") or 30), 600.0))
    result = executor(
        instance,
        operation_id,
        dict(payload["arguments"]),
        timeout_seconds,
    )
    return {
        "schema": SERVICE_INVOCATION_RESULT_SCHEMA,
        "request_id": str(payload.get("request_id") or ""),
        "result": _bounded_json(result, reason="service_invocation_result"),
    }


_receiver_lock = RLock()
_receiver_node_id = ""
_receiver_executor: Callable[
    [ServiceInstance, str, Mapping[str, Any], float], Any
] | None = None


def register_service_invocation_receiver(
    executor: Callable[[ServiceInstance, str, Mapping[str, Any], float], Any] | None,
    *,
    node_id: str = "",
) -> None:
    global _receiver_executor, _receiver_node_id
    with _receiver_lock:
        _receiver_executor = executor
        _receiver_node_id = str(node_id or "").strip()


def execute_registered_service_invocation(payload: Mapping[str, Any]) -> dict[str, Any]:
    with _receiver_lock:
        executor = _receiver_executor
        node_id = _receiver_node_id
    if executor is None or not node_id:
        raise TopologyExecutionError("service_invocation_receiver_not_configured")
    return execute_service_invocation_request(
        payload,
        local_node_id=node_id,
        executor=executor,
    )


__all__ = [
    "HttpServiceInvocationTransport",
    "MAX_SERVICE_INVOCATION_BYTES",
    "RoutingServiceInvocationAdapter",
    "SERVICE_INVOCATION_RESULT_SCHEMA",
    "SERVICE_INVOCATION_SCHEMA",
    "ServiceInvocationAdapter",
    "execute_registered_service_invocation",
    "execute_service_invocation_request",
    "register_service_invocation_receiver",
]
