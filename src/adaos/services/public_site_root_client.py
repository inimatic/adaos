"""Node-side transport. It never imports a provider adapter or operator secret."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import time
from typing import Any, Mapping
import uuid

from adaos.services.artifact_pipeline.storage import atomic_write_json
from adaos.services.public_site_delivery import build_delivery_bundle
from adaos.services.release_secret_scan import assert_release_tree_secret_free

# Base64 plus the authenticated forwarding envelope stays below 32 KiB.
CHUNK_BYTES = 16 * 1024
_AVAILABILITY: dict[str, tuple[float, dict[str, Any]]] = {}


class RootPublicationTransportError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class RootPublicationDelivery:
    def __init__(self, http: Any):
        self.http = http
        self.authoring: dict[str, Any] = {}

    def bind_authoring(self, active: Mapping[str, Any], **identity: str) -> None:
        self.authoring = {**identity, **{k: active[k] for k in ("site_id", "project_id", "revision_id", "source_digest") if k in active}}

    def _call(self, action: str, arguments: Mapping[str, Any], *, timeout: float = 120) -> dict[str, Any]:
        try:
            payload = self.http.request("POST", "/v1/public-sites/rpc",
                json={"action": action, "arguments": dict(arguments)}, timeout=timeout)
        except RuntimeError as exc:
            status = getattr(exc, "status_code", 0)
            if status == 0 or status >= 500:
                _AVAILABILITY[str(self.http.base_url)] = (time.monotonic()+20,
                    {"status": "root_publication_delivery_unavailable", "message": "Infrastructure Root publication service could not be reached."})
            raise
        if not isinstance(payload, dict) or payload.get("ok") is not True or not isinstance(payload.get("result"), dict):
            raise RootPublicationTransportError(str((payload or {}).get("error") or "invalid_root_response"), "Root publication response is invalid.")
        return payload["result"]

    def availability(self) -> dict[str, Any]:
        key = str(self.http.base_url)
        cached = _AVAILABILITY.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        try:
            health = self._call("health", {}, timeout=10)
            value = {"status": "available" if health.get("ready") else "root_publication_delivery_unavailable",
                     "message": "" if health.get("ready") else "Infrastructure Root publication service is not configured."}
        except Exception:
            value = {"status": "root_publication_delivery_unavailable", "message": "Infrastructure Root publication service could not be reached."}
        _AVAILABILITY[key] = (time.monotonic()+20, value)
        return value

    def site_status(self, site_id: str) -> dict[str, Any]:
        return self._call("site_status", {"site_id": site_id})

    def operation_status(self, workspace: Path) -> dict[str, Any] | None:
        path = workspace/"root-operation.json"
        if not path.is_file():
            return None
        identity = json.loads(path.read_text("utf-8"))
        return self._call("status", {"operation_id": identity["operation_id"]})

    def prepare(self, *, site_dir: Path, workspace: Path, platform_path: str, environment: str = "prod") -> dict[str, Any]:
        assert_release_tree_secret_free(site_dir)
        bundle = build_delivery_bundle(site_dir)
        workspace.mkdir(parents=True, exist_ok=True)
        binding = workspace / "root-operation.json"
        if binding.is_file():
            identity = json.loads(binding.read_text("utf-8"))
            if identity["bundle_digest"] != bundle.manifest["bundle_digest"]:
                raise ValueError("Root publication upload is bound to another bundle")
        else:
            identity = {"operation_id": "psroot_"+uuid.uuid4().hex, "bundle_digest": bundle.manifest["bundle_digest"]}
            atomic_write_json(binding, identity)
        operation_id = identity["operation_id"]
        begun = self._call("begin", {"operation_id": operation_id, "manifest": dict(bundle.manifest),
            "platform_path": platform_path, "environment": environment, "authoring": self.authoring})
        if begun["status"] == "uploading":
            for object_digest, content in bundle.payloads.items():
                for index in range(max(1, (len(content)+CHUNK_BYTES-1)//CHUNK_BYTES)):
                    raw = content[index*CHUNK_BYTES:(index+1)*CHUNK_BYTES]
                    self._call("chunk", {"operation_id": operation_id, "digest": object_digest,
                        "index": index, "content": base64.b64encode(raw).decode("ascii")})
        prepared = self._call("prepare", {"operation_id": operation_id}, timeout=550)
        atomic_write_json(workspace / "publication-plan.json", prepared["plan"])
        identity["plan_digest"] = prepared["plan_digest"]
        atomic_write_json(binding, identity)
        return {"ok": True, "plan_sha256": prepared["plan_digest"],
                "deployment_digest": prepared["plan"]["pointer"]["deployment_digest"],
                "pointer_digest": prepared["plan"]["pointer"]["pointer_digest"]}

    def publish(self, *, site_dir: Path, workspace: Path, platform_path: str, plan_digest: str, environment: str = "prod") -> dict[str, Any]:
        identity = json.loads((workspace/"root-operation.json").read_text("utf-8"))
        plan = json.loads((workspace/"publication-plan.json").read_text("utf-8"))
        if identity["plan_digest"] != plan_digest or plan["platform_path"] != platform_path or plan["environment"] != environment:
            raise ValueError("reviewed Root publication candidate changed")
        bundle = build_delivery_bundle(site_dir)
        if bundle.manifest["bundle_digest"] != identity["bundle_digest"]:
            raise ValueError("reviewed Root publication bytes changed")
        status = self._call("status", {"operation_id": identity["operation_id"]})
        if status["status"] == "published":
            return status["receipt"]
        if status["status"] != "prepared":
            raise RootPublicationTransportError("publication_reconciliation_required", "Root publication is pending reconciliation; it will not be repeated.")
        result = self._call("publish", {"operation_id": identity["operation_id"], "plan_digest": plan_digest, "confirmed": True}, timeout=550)
        return result["receipt"]

    def _domain(self, command: str, hostname: str, *, confirmed: bool = False) -> dict[str, Any]:
        return self._call("domain", {"command": command, "hostname": hostname,
            "site_id": self.authoring.get("site_id"), "confirmed": confirmed}, timeout=550)

    def request_domain(self, hostname: str) -> dict[str, Any]:
        return self._domain("request", hostname)

    def status_domain(self, hostname: str) -> dict[str, Any]:
        return self._domain("status", hostname)

    def activate_domain(self, hostname: str, site_id: str) -> dict[str, Any]:
        if site_id != self.authoring.get("site_id"):
            raise ValueError("domain site identity changed")
        return self._domain("activate", hostname, confirmed=True)
