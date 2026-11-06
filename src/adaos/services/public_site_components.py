from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from uuid import uuid4

from adaos.services.agent_context import get_ctx
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.public_site_delivery import build_delivery_bundle


REGISTRY_SCHEMA = "adaos.public_site_component_registry.v1"
RECEIPT_SCHEMA = "adaos.public_site_component_registry_receipt.v1"
_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_SUPPORT_STATES = {
    "candidate",
    "recommended",
    "supported",
    "deprecated",
    "migration_required",
    "eol",
    "revoked",
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"invalid JSON object: {path.name}")
    return dict(value)


def _empty() -> dict[str, Any]:
    return {
        "schema": REGISTRY_SCHEMA,
        "revision": 0,
        "updated_at": _iso_now(),
        "packages": {},
        "release_sets": {},
        "recommended": {},
        "deployments": {},
        "client_qualifications": {},
        "receipts": [],
    }


class PublicSiteComponentRegistry:
    """Canonical Root authority for public component sets and their consumers."""

    def __init__(self, *, state_dir: Path | None = None) -> None:
        if state_dir is None:
            raw = get_ctx().paths.root_mcp_state_dir()
            state_dir = Path(raw() if callable(raw) else raw)
        self.root = Path(state_dir).resolve() / "public-site-components"
        self.path = self.root / "registry.json"
        self.lock_path = self.root / ".mutation.lock"

    def admit_bundle(
        self,
        site_dir: Path,
        *,
        actor: str,
        operation_id: str,
    ) -> dict[str, Any]:
        site_dir = Path(site_dir).resolve(strict=True)
        bundle = build_delivery_bundle(site_dir)
        release_set = _read_json(site_dir / "components/release-set.json")
        package_artifacts = {
            str(entry["digest"]): _read_json(site_dir / str(entry["artifact_ref"]))
            for entry in release_set["packages"]
        }
        deployment = _read_json(site_dir / "site.deployment.json")
        set_digest = str(release_set["set_digest"])
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._read()
            packages = dict(registry["packages"])
            for digest, artifact in package_artifacts.items():
                current = packages.get(digest)
                record = {
                    "digest": digest,
                    "package_id": artifact["package_id"],
                    "family": artifact["family"],
                    "version": artifact["version"],
                    "renderer_abi": deepcopy(artifact["renderer_abi"]),
                    "module_ref": artifact["module_ref"],
                    "style_refs": list(artifact["style_refs"]),
                    "components": list(artifact["components"]),
                    "migration_digest": artifact["migration_digest"],
                    "artifact": artifact,
                    "admitted_at": _iso_now(),
                }
                if current is not None and current.get("artifact") != artifact:
                    raise ValueError("component package digest collision")
                if current is not None:
                    record["admitted_at"] = current.get("admitted_at")
                packages[digest] = record
            release_sets = dict(registry["release_sets"])
            current_set = release_sets.get(set_digest)
            set_record = {
                "set_digest": set_digest,
                "set_id": release_set["set_id"],
                "channel": release_set["channel"],
                "renderer_abi": release_set["renderer_abi"],
                "support_state": (
                    current_set.get("support_state") if current_set else "candidate"
                ),
                "packages": deepcopy(release_set["packages"]),
                "qualification_digest": release_set["qualification_digest"],
                "qualified_at": release_set["qualified_at"],
                "artifact": release_set,
                "admitted_at": (
                    current_set.get("admitted_at") if current_set else _iso_now()
                ),
                "deployment_count": int(
                    sum(
                        1
                        for item in registry["deployments"].values()
                        if item.get("component_set_digest") == set_digest
                        and item.get("status") == "current"
                    )
                ),
                "migration_blockers": list(
                    (current_set or {}).get("migration_blockers") or []
                ),
                "security_notices": list(
                    (current_set or {}).get("security_notices") or []
                ),
            }
            if current_set is not None and current_set.get("artifact") != release_set:
                raise ValueError("component release-set digest collision")
            release_sets[set_digest] = set_record
            receipt = self._receipt(
                "admit_bundle",
                actor=actor,
                operation_id=operation_id,
                details={
                    "site_id": bundle.site_id,
                    "deployment_digest": bundle.deployment_digest,
                    "component_set_digest": set_digest,
                    "package_digests": sorted(package_artifacts),
                },
            )
            registry.update(packages=packages, release_sets=release_sets)
            self._commit(registry, receipt)
        status = self.release_set_status(set_digest)
        status["deployment"] = {
            "deployment_digest": deployment["deployment_digest"],
            "projection_digest": deployment["projection_digest"],
            "renderer_abi": deployment["renderer_abi"],
        }
        return status

    def record_publication(
        self,
        *,
        site_id: str,
        project_id: str,
        deployment_digest: str,
        component_set_digest: str,
        projection_digest: str,
        renderer_abi: str,
        actor: str,
        operation_id: str,
        published_at: str,
        predecessor_deployment_digest: str | None = None,
        acceptance_receipt: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        for value in (deployment_digest, component_set_digest, projection_digest):
            if not _DIGEST.fullmatch(str(value or "")):
                raise ValueError("component publication digest is invalid")
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._read()
            release_set = dict(registry["release_sets"].get(component_set_digest) or {})
            if not release_set:
                raise ValueError("component release set was not admitted")
            if release_set.get("renderer_abi") != renderer_abi:
                raise ValueError("component set renderer ABI mismatch")
            deployments = dict(registry["deployments"])
            for digest, item in list(deployments.items()):
                if item.get("site_id") == site_id and item.get("status") == "current":
                    previous = dict(item)
                    previous["status"] = "predecessor"
                    deployments[digest] = previous
            deployment = {
                "site_id": site_id,
                "project_id": project_id,
                "deployment_digest": deployment_digest,
                "component_set_digest": component_set_digest,
                "projection_digest": projection_digest,
                "renderer_abi": renderer_abi,
                "status": "current",
                "published_at": published_at,
                "predecessor_deployment_digest": predecessor_deployment_digest,
                "acceptance_receipt": deepcopy(dict(acceptance_receipt or {})),
                "operation_id": operation_id,
            }
            deployments[deployment_digest] = deployment
            release_sets = dict(registry["release_sets"])
            release_set["support_state"] = (
                "recommended"
                if not registry["recommended"].get(release_set["channel"])
                else "supported"
            )
            recommended = dict(registry["recommended"])
            recommended.setdefault(release_set["channel"], component_set_digest)
            release_set["deployment_count"] = sum(
                1
                for item in deployments.values()
                if item.get("component_set_digest") == component_set_digest
                and item.get("status") == "current"
            )
            release_sets[component_set_digest] = release_set
            receipt = self._receipt(
                "record_publication",
                actor=actor,
                operation_id=operation_id,
                details=deployment,
            )
            registry.update(
                deployments=deployments,
                release_sets=release_sets,
                recommended=recommended,
            )
            self._commit(registry, receipt)
        return self.site_status(site_id)

    def set_support_state(
        self,
        set_digest: str,
        *,
        support_state: str,
        actor: str,
        reason: str,
        recommended: bool = False,
        migration_blockers: Sequence[str] = (),
        security_notices: Sequence[str] = (),
    ) -> dict[str, Any]:
        support_state = str(support_state or "").strip()
        if support_state not in _SUPPORT_STATES:
            raise ValueError("component release-set support state is invalid")
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._read()
            current = dict(registry["release_sets"].get(set_digest) or {})
            if not current:
                raise KeyError(set_digest)
            recommended_map = dict(registry["recommended"])
            if recommended:
                previous_digest = recommended_map.get(current["channel"])
                if previous_digest and previous_digest != set_digest:
                    previous = dict(registry["release_sets"].get(previous_digest) or {})
                    if previous and previous.get("support_state") == "recommended":
                        previous["support_state"] = "supported"
                        registry["release_sets"][previous_digest] = previous
                recommended_map[current["channel"]] = set_digest
                support_state = "recommended"
            current.update(
                support_state=support_state,
                migration_blockers=[str(item)[:240] for item in migration_blockers],
                security_notices=[str(item)[:500] for item in security_notices],
            )
            registry["release_sets"][set_digest] = current
            registry["recommended"] = recommended_map
            receipt = self._receipt(
                "set_support_state",
                actor=actor,
                operation_id="component-policy-" + uuid4().hex,
                details={
                    "component_set_digest": set_digest,
                    "support_state": support_state,
                    "reason": str(reason)[:500],
                },
            )
            self._commit(registry, receipt)
        return self.release_set_status(set_digest)

    def record_client_qualification(
        self,
        *,
        client_release_id: str,
        client_release_digest: str,
        set_digest: str,
        renderer_abi: str,
        evidence_digest: str,
        checks: Sequence[str],
        actor: str,
    ) -> dict[str, Any]:
        for value in (client_release_digest, set_digest, evidence_digest):
            if not _DIGEST.fullmatch(str(value or "")):
                raise ValueError("client qualification digest is invalid")
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._read()
            release_set = dict(registry["release_sets"].get(set_digest) or {})
            if not release_set or release_set.get("renderer_abi") != renderer_abi:
                raise ValueError("client qualification targets an unknown component set")
            identity = f"{client_release_digest}:{set_digest}"
            record = {
                "client_release_id": str(client_release_id)[:160],
                "client_release_digest": client_release_digest,
                "component_set_digest": set_digest,
                "renderer_abi": renderer_abi,
                "evidence_digest": evidence_digest,
                "checks": sorted(set(str(item)[:80] for item in checks)),
                "qualified_at": _iso_now(),
                "actor": str(actor)[:240],
            }
            registry["client_qualifications"][identity] = record
            receipt = self._receipt(
                "record_client_qualification",
                actor=actor,
                operation_id="client-qualification-" + uuid4().hex,
                details=record,
            )
            self._commit(registry, receipt)
        return record

    def release_set_status(self, set_digest: str) -> dict[str, Any]:
        registry = self._read()
        current = dict(registry["release_sets"].get(set_digest) or {})
        if not current:
            raise KeyError(set_digest)
        current["recommended"] = (
            registry["recommended"].get(current["channel"]) == set_digest
        )
        current["client_qualifications"] = [
            deepcopy(item)
            for item in registry["client_qualifications"].values()
            if item.get("component_set_digest") == set_digest
        ]
        return current

    def site_status(self, site_id: str) -> dict[str, Any]:
        registry = self._read()
        deployments = [
            deepcopy(item)
            for item in registry["deployments"].values()
            if item.get("site_id") == site_id
        ]
        deployments.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
        current = next(
            (item for item in deployments if item.get("status") == "current"), None
        )
        release_set = (
            self.release_set_status(str(current["component_set_digest"]))
            if current
            else None
        )
        recommended_digest = (
            registry["recommended"].get(release_set["channel"])
            if release_set
            else None
        )
        return {
            "schema": "adaos.public_site_component_status.v1",
            "site_id": site_id,
            "current_deployment": current,
            "current_set": release_set,
            "recommended_set_digest": recommended_digest,
            "upgrade_available": bool(
                current and recommended_digest != current["component_set_digest"]
            ),
            "rollback_target": next(
                (
                    item
                    for item in deployments
                    if item.get("status") == "predecessor"
                ),
                None,
            ),
            "acceptance_history": [
                item.get("acceptance_receipt")
                for item in deployments
                if item.get("acceptance_receipt")
            ],
        }

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return _empty()
        value = _read_json(self.path)
        if value.get("schema") != REGISTRY_SCHEMA:
            raise RuntimeError("public component registry schema is invalid")
        for key in (
            "packages",
            "release_sets",
            "recommended",
            "deployments",
            "client_qualifications",
        ):
            value.setdefault(key, {})
        value.setdefault("receipts", [])
        return value

    def _commit(self, registry: dict[str, Any], receipt: Mapping[str, Any]) -> None:
        registry["receipts"] = [*registry["receipts"], deepcopy(dict(receipt))]
        registry["revision"] = int(registry.get("revision") or 0) + 1
        registry["updated_at"] = _iso_now()
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, registry)

    @staticmethod
    def _receipt(
        action: str,
        *,
        actor: str,
        operation_id: str,
        details: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "schema": RECEIPT_SCHEMA,
            "receipt_id": "sitecomponents_" + uuid4().hex,
            "action": action,
            "actor": str(actor)[:240],
            "operation_id": str(operation_id)[:240],
            "details": deepcopy(dict(details)),
            "created_at": _iso_now(),
        }


__all__ = ["PublicSiteComponentRegistry"]
