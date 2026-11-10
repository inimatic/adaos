from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Callable, Mapping, Protocol
import uuid

from adaos.services.agent_context import get_ctx
from adaos.services.applications.runtime_selection import selected_application, selected_trial
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.release_secret_scan import (
    assert_release_surfaces_secret_free,
    assert_release_tree_secret_free,
)
from adaos.services.public_site_components import PublicSiteComponentRegistry
from adaos.services.public_site_delivery_telemetry import (
    PublicSiteDeliveryTelemetryService,
)
from adaos.services.site_studio.publication_export import (
    export_publication,
    read_active_revision,
)
from adaos.services.site_studio.contracts import validate_contract


_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,179}$")
_PUBLIC_ORIGIN = "https://p.adaos.io"
_ENVIRONMENT = "prod"
_DELIVERY_ORIGINS = {
    "dev": "https://dev.p.adaos.io",
    "prod": _PUBLIC_ORIGIN,
}
_PLATFORM_PATH = re.compile(r"^st_[a-z0-9]{6,32}$")
_HOSTNAME = re.compile(
    r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"invalid JSON object: {path.name}")
    return dict(value)


def _canonical_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _normalize_digest(value: Any) -> str:
    result = str(value or "")
    if re.fullmatch(r"[a-f0-9]{64}", result):
        result = "sha256:" + result
    if not _DIGEST.fullmatch(result):
        raise RuntimeError("publication adapter returned an invalid plan digest")
    return result


def _public_url(platform_path: str, environment: str = _ENVIRONMENT) -> str:
    if not _PLATFORM_PATH.fullmatch(platform_path):
        raise ValueError("public site platform path is invalid")
    origin = _DELIVERY_ORIGINS.get(environment)
    if origin is None:
        raise ValueError("public site delivery environment is invalid")
    return f"{origin}/{platform_path}/"


class PublicationDelivery(Protocol):
    def prepare(
        self,
        *,
        site_dir: Path,
        workspace: Path,
        platform_path: str,
        environment: str = _ENVIRONMENT,
    ) -> dict[str, Any]: ...

    def publish(
        self,
        *,
        site_dir: Path,
        workspace: Path,
        platform_path: str,
        plan_digest: str,
        environment: str = _ENVIRONMENT,
    ) -> dict[str, Any]: ...


class RootPublicationDeliveryUnavailable:
    """Fail closed until a Root-hosted delivery transport is installed.

    The embedded Root MCP plane owns local authoring state, but it is not an
    infrastructure Root. Never turn its presence into permission to load
    operator recovery credentials or execute a local provider adapter.
    """

    reason = "root_publication_delivery_unavailable"
    message = (
        "Root-hosted public Site delivery is not configured. "
        "Provider credentials and publication must remain on the infrastructure Roots."
    )

    @staticmethod
    def _unavailable(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(RootPublicationDeliveryUnavailable.message)

    prepare = _unavailable
    publish = _unavailable
    request_domain = _unavailable
    status_domain = _unavailable
    activate_domain = _unavailable
    activate_managed_domain = _unavailable


from adaos.services.public_site_provider import CloudflarePublicationDelivery


class PublicSitePublicationService:
    def __init__(
        self,
        *,
        state_dir: Path | None = None,
        adaos_root: Path | None = None,
        delivery: PublicationDelivery | None = None,
        trial_resolver: Callable[..., Any] | None = None,
        component_registry: PublicSiteComponentRegistry | None = None,
        delivery_telemetry: PublicSiteDeliveryTelemetryService | None = None,
    ):
        context = None if state_dir is not None and adaos_root is not None else get_ctx()
        self.state_dir = Path(
            state_dir or context.paths.state_dir()  # type: ignore[union-attr]
        ).resolve()
        self.adaos_root = Path(
            adaos_root or Path(__file__).resolve().parents[3]
        ).resolve()
        self.public_root = self.state_dir / "public-sites"
        self.private_root = self.state_dir / "private_recovery/public-sites"
        self.registry_path = self.public_root / "registry.json"
        self.lock_path = self.public_root / ".mutation.lock"
        # A local semantic control plane is not an infrastructure execution
        # authority. A Root service must explicitly provide its delivery adapter.
        self.delivery = delivery if delivery is not None else RootPublicationDeliveryUnavailable()
        self.trial_resolver = trial_resolver or selected_trial
        self.component_registry = component_registry or PublicSiteComponentRegistry(
            state_dir=self.state_dir / "root_mcp"
        )
        self.delivery_telemetry = (
            delivery_telemetry
            or PublicSiteDeliveryTelemetryService(state_dir=self.state_dir / "root_mcp")
        )

    def status(
        self,
        *,
        scenario_id: str,
        skill_id: str,
        webspace_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        active, _, _ = self._active_authoring(
            scenario_id=scenario_id,
            skill_id=skill_id,
            webspace_id=webspace_id,
            project_id=project_id,
        )
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._registry_with_legacy_bootstrap(active["site_id"])
            platform_path = self._ensure_platform_path(registry, active["site_id"])
            published = self._published_for_environment(
                registry, active["site_id"], "prod"
            )
            candidate = self._latest_candidate(active["site_id"], "prod")
            development_published = self._published_for_environment(
                registry, active["site_id"], "dev"
            )
            development_candidate = self._latest_candidate(active["site_id"], "dev")
        root_status = getattr(self.delivery, "site_status", None)
        availability = getattr(self.delivery, "availability", None)
        if callable(root_status) and callable(availability) and availability().get("status") == "available":
            try:
                authoritative = root_status(str(active["site_id"]))
            except RuntimeError as exc:
                if getattr(exc, "status_code", 0) not in {0, 500, 502, 503, 504}:
                    raise
                authoritative = {}
            channels = authoritative.get("publications") or {}
            if channels.get("prod"):
                published = {**published, **channels["prod"]}
            if channels.get("dev"):
                development_published = {**development_published, **channels["dev"]}
            if authoritative.get("platform_path"):
                platform_path = str(authoritative["platform_path"])
        result = self._status_with_components(
            active, published, candidate, platform_path
        )
        result["delivery_telemetry"] = self.delivery_telemetry.status(
            site_id=str(active["site_id"]),
            deployment_digest=(
                str(published.get("deployment_digest"))
                if published.get("deployment_digest")
                else None
            ),
        )
        result["release_channels"] = {
            "production": deepcopy(result),
            "development": self._status_view(
                active,
                development_published,
                development_candidate,
                platform_path,
                environment="dev",
            ),
        }
        result["release_channels"]["production"].pop("release_channels", None)
        return result

    def record_delivery_observation(
        self,
        observation: Mapping[str, Any],
        *,
        source: str,
        observation_id: str | None = None,
    ) -> dict[str, Any]:
        """Retain one bounded provider or synthetic delivery observation."""

        return self.delivery_telemetry.record(
            observation, source=source, observation_id=observation_id
        )

    def domains(
        self,
        *,
        scenario_id: str,
        skill_id: str,
        webspace_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        active, _, _ = self._active_authoring(
            scenario_id=scenario_id,
            skill_id=skill_id,
            webspace_id=webspace_id,
            project_id=project_id,
        )
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._registry_with_legacy_bootstrap(active["site_id"])
            publication = self._domain_publication(registry, active)
            records = [
                dict(value)
                for value in (registry.get("domains") or {}).values()
                if value.get("site_id") == active["site_id"]
            ]
        status_domain = getattr(self.delivery, "status_domain", None)
        if callable(status_domain):
            refreshed = []
            for current in records:
                provider = status_domain(str(current.get("hostname") or ""))
                record = self._domain_record(
                    active["site_id"],
                    str(current.get("hostname") or ""),
                    provider,
                    current=current,
                    publication=publication,
                )
                for field in ("provider_binding", "public_url"):
                    if current.get(field):
                        record[field] = current[field]
                refreshed.append(record)
            records = refreshed
        records.sort(key=lambda item: str(item.get("hostname") or ""))
        return {
            "schema": "adaos.public_site_domains.v1",
            "site_id": active["site_id"],
            "items": records,
        }

    def request_domain(
        self,
        *,
        scenario_id: str,
        skill_id: str,
        webspace_id: str,
        project_id: str,
        hostname: str,
    ) -> dict[str, Any]:
        active, _, _ = self._active_authoring(
            scenario_id=scenario_id,
            skill_id=skill_id,
            webspace_id=webspace_id,
            project_id=project_id,
        )
        hostname = self._external_hostname(hostname)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._registry_with_legacy_bootstrap(active["site_id"])
            publication = self._domain_publication(registry, active)
            domains = dict(registry.get("domains") or {})
            current = dict(domains.get(hostname) or {})
            if current and current.get("site_id") != active["site_id"]:
                raise ValueError("domain is already assigned to another site")
            provider = self.delivery.request_domain(hostname)  # type: ignore[attr-defined]
            record = self._domain_record(
                active["site_id"],
                hostname,
                provider,
                current=current,
                publication=publication,
            )
            domains[hostname] = record
            registry.update(domains=domains, updated_at=_iso_now())
            atomic_write_json(self.registry_path, registry)
        return record

    def activate_domain(
        self,
        *,
        scenario_id: str,
        skill_id: str,
        webspace_id: str,
        project_id: str,
        hostname: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        if confirmed is not True:
            raise ValueError("domain activation requires explicit confirmation")
        active, _, _ = self._active_authoring(
            scenario_id=scenario_id,
            skill_id=skill_id,
            webspace_id=webspace_id,
            project_id=project_id,
        )
        hostname = self._external_hostname(hostname)
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._registry_with_legacy_bootstrap(active["site_id"])
            publication = self._domain_publication(registry, active)
            domains = dict(registry.get("domains") or {})
            current = dict(domains.get(hostname) or {})
            if current.get("site_id") != active["site_id"]:
                raise ValueError("domain request is unavailable for this site")
            provider = self.delivery.activate_domain(  # type: ignore[attr-defined]
                hostname, active["site_id"]
            )
            status = self.delivery.request_domain(hostname)  # type: ignore[attr-defined]
            record = self._domain_record(
                active["site_id"],
                hostname,
                status,
                current=current,
                publication=publication,
            )
            existing_primary = any(
                item.get("site_id") == active["site_id"]
                and item.get("primary_canonical") is True
                and item.get("hostname") != hostname
                for item in domains.values()
            )
            record.update(
                {
                    "status": "active",
                    "provider_binding": provider.get("status"),
                    "public_url": f"https://{hostname}/",
                    "activated_at": current.get("activated_at") or _iso_now(),
                    "primary_canonical": bool(
                        current.get("primary_canonical") or not existing_primary
                    ),
                }
            )
            domains[hostname] = record
            registry.update(domains=domains, updated_at=_iso_now())
            atomic_write_json(self.registry_path, registry)
        return record

    def set_primary_domain(
        self,
        *,
        scenario_id: str,
        skill_id: str,
        webspace_id: str,
        project_id: str,
        hostname: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        if confirmed is not True:
            raise ValueError("primary domain selection requires explicit confirmation")
        active, _, _ = self._active_authoring(
            scenario_id=scenario_id,
            skill_id=skill_id,
            webspace_id=webspace_id,
            project_id=project_id,
        )
        hostname = str(hostname or "").strip().lower().rstrip(".")
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._registry_with_legacy_bootstrap(active["site_id"])
            domains = dict(registry.get("domains") or {})
            selected = dict(domains.get(hostname) or {})
            if (
                selected.get("site_id") != active["site_id"]
                or selected.get("status") != "active"
                or selected.get("ssl_status") != "active"
            ):
                raise ValueError("primary domain must be an active verified binding")
            for key, value in list(domains.items()):
                item = dict(value)
                if item.get("site_id") == active["site_id"]:
                    item["primary_canonical"] = key == hostname
                    item["updated_at"] = _iso_now()
                    domains[key] = item
            registry.update(domains=domains, updated_at=_iso_now())
            atomic_write_json(self.registry_path, registry)
        return dict(domains[hostname])

    def reconcile_managed_domain(
        self,
        *,
        site_id: str,
        hostname: str,
        receipt_path: Path,
    ) -> dict[str, Any]:
        """Record a platform-owned domain after reviewed infrastructure apply."""
        hostname = self._managed_hostname(hostname)
        receipts_root = (
            self.private_root / "managed-domains"
        ).resolve()
        receipt_path = Path(receipt_path).resolve(strict=True)
        if not receipt_path.is_relative_to(receipts_root):
            raise ValueError("managed domain receipt escaped private recovery")
        receipt = _read_json(receipt_path)
        if (
            receipt.get("schema")
            != "inimatic.public_site_managed_domain_apply.v1"
            or receipt.get("status") != "active"
            or receipt.get("site_id") != site_id
            or receipt.get("hostname") != hostname
            or receipt.get("public_url") != f"https://{hostname}/"
            or not _DIGEST.fullmatch(
                "sha256:" + str(receipt.get("plan_sha256") or "")
            )
        ):
            raise ValueError("managed domain receipt is invalid")
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._registry_with_legacy_bootstrap(site_id)
            if site_id not in (registry.get("sites") or {}):
                raise ValueError("managed domain requires a published site")
            domains = dict(registry.get("domains") or {})
            current = dict(domains.get(hostname) or {})
            if current and current.get("site_id") != site_id:
                raise ValueError("domain is already assigned to another site")
            now = _iso_now()
            publication = dict((registry.get("sites") or {}).get(site_id) or {})
            record = self._domain_record(
                site_id,
                hostname,
                {"status": "active", "ssl_status": "active", "provider": "firebase"},
                current=current,
                publication=publication,
            )
            record.update(
                managed_by="platform",
                provider_binding="active",
                public_url=f"https://{hostname}/",
                requested_at=current.get("requested_at") or now,
                activated_at=current.get("activated_at") or now,
                primary_canonical=bool(
                    current.get("primary_canonical")
                    or not any(
                        item.get("site_id") == site_id
                        and item.get("primary_canonical") is True
                        and item.get("hostname") != hostname
                        for item in domains.values()
                    )
                ),
                updated_at=now,
            )
            domains[hostname] = record
            registry.update(domains=domains, updated_at=now)
            atomic_write_json(self.registry_path, registry)
        return record

    def prepare(
        self,
        *,
        scenario_id: str,
        skill_id: str,
        webspace_id: str,
        project_id: str,
        expected_revision_id: str,
        expected_source_digest: str,
        release_target: str = "production",
    ) -> dict[str, Any]:
        environment = self._release_environment(release_target)
        active, database, files_root = self._active_authoring(
            scenario_id=scenario_id,
            skill_id=skill_id,
            webspace_id=webspace_id,
            project_id=project_id,
        )
        self._assert_expected(active, expected_revision_id, expected_source_digest)
        operation_id = "pspub_" + uuid.uuid4().hex
        private = self.private_root / "operations" / operation_id
        public = self.public_root / "operations" / f"{operation_id}.json"
        site_dir = private / "site"
        provider_workspace = private / "provider"
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._registry_with_legacy_bootstrap(active["site_id"])
            platform_path = self._ensure_platform_path(registry, active["site_id"])
            private.mkdir(parents=True, exist_ok=False)
            receipt = export_publication(
                database,
                files_root,
                site_dir,
                canonical_base_url=_public_url(platform_path, environment),
            )
            assert_release_tree_secret_free(site_dir)
            component_status = self.component_registry.admit_bundle(
                site_dir,
                actor="skill:" + skill_id,
                operation_id=operation_id,
            )
            deployment = dict(component_status["deployment"])
            if (
                receipt["revision_id"] != active["revision_id"]
                or receipt["source_digest"] != active["source_digest"]
            ):
                raise ValueError("Site Studio revision changed during export")
            provider = self.delivery.prepare(
                site_dir=site_dir,
                workspace=provider_workspace,
                platform_path=platform_path,
                environment=environment,
            )
            plan_path = provider_workspace / "publication-plan.json"
            plan = _read_json(plan_path)
            plan_digest = str(provider.get("plan_sha256") or "")
            pointer = dict(plan.get("pointer") or {})
            if (
                not _DIGEST.fullmatch(plan_digest)
                or provider.get("deployment_digest") != receipt["deployment_digest"]
                or pointer.get("deployment_digest") != receipt["deployment_digest"]
                or plan.get("environment") != environment
                or plan.get("host")
                != _DELIVERY_ORIGINS[environment].removeprefix("https://")
                or plan.get("platform_path") != platform_path
                or plan.get("public_url") != _public_url(platform_path, environment)
            ):
                raise RuntimeError("publication provider plan failed identity verification")
            created_at = _iso_now()
            private_record = {
                "schema": "adaos.public_site_publication_operation.private.v1",
                "operation_id": operation_id,
                "status": "prepared",
                "environment": environment,
                "release_target": release_target,
                "created_at": created_at,
                "scenario_id": scenario_id,
                "skill_id": skill_id,
                "webspace_id": webspace_id,
                "active": active,
                "export": receipt,
                "plan_digest": plan_digest,
                "platform_path": platform_path,
                "public_url": _public_url(platform_path, environment),
                "pointer": pointer,
                "site_dir": str(site_dir),
                "provider_workspace": str(provider_workspace),
                "component_release": {
                    "component_set_digest": component_status["set_digest"],
                    "renderer_abi": deployment["renderer_abi"],
                    "projection_digest": deployment["projection_digest"],
                    "deployment_digest": deployment["deployment_digest"],
                    "channel": component_status["channel"],
                },
            }
            atomic_write_json(private / "operation.json", private_record)
            public_record = self._public_operation(private_record)
            atomic_write_json(public, public_record)
        published = self._published_for_environment(
            registry, active["site_id"], environment
        )
        return self._status_with_components(
            active, published, public_record, platform_path, environment=environment
        )

    def publish(
        self,
        *,
        scenario_id: str,
        skill_id: str,
        webspace_id: str,
        project_id: str,
        operation_id: str,
        plan_digest: str,
        expected_revision_id: str,
        expected_source_digest: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        if confirmed is not True:
            raise ValueError("publication requires explicit confirmation")
        if not _IDENTIFIER.fullmatch(operation_id) or not _DIGEST.fullmatch(plan_digest):
            raise ValueError("publication operation identity is invalid")
        active, _, _ = self._active_authoring(
            scenario_id=scenario_id,
            skill_id=skill_id,
            webspace_id=webspace_id,
            project_id=project_id,
        )
        self._assert_expected(active, expected_revision_id, expected_source_digest)
        private_dir = self.private_root / "operations" / operation_id
        private_path = private_dir / "operation.json"
        public_path = self.public_root / "operations" / f"{operation_id}.json"
        with mutation_lock(self.lock_path, timeout_s=30.0):
            operation = _read_json(private_path)
            if (
                operation.get("operation_id") != operation_id
                or operation.get("status") != "prepared"
                or operation.get("plan_digest") != plan_digest
                or operation.get("active") != active
            ):
                raise ValueError("publication candidate is stale or already consumed")
            result = self.delivery.publish(
                site_dir=Path(str(operation["site_dir"])),
                workspace=Path(str(operation["provider_workspace"])),
                platform_path=str(operation["platform_path"]),
                plan_digest=plan_digest,
                environment=str(operation.get("environment") or _ENVIRONMENT),
            )
            pointer = dict(operation.get("pointer") or {})
            environment = str(operation.get("environment") or _ENVIRONMENT)
            public_url = _public_url(
                str(operation.get("platform_path") or ""), environment
            )
            if (
                result.get("deployment_digest") != pointer.get("deployment_digest")
                or result.get("pointer_digest") != pointer.get("pointer_digest")
                or result.get("url") != public_url
            ):
                raise RuntimeError("publication receipt failed identity verification")
            published_at = str(pointer.get("published_at") or _iso_now())
            component_release = dict(operation.get("component_release") or {})
            previous_publication = dict(
                self._published_for_environment(
                    self._registry_with_legacy_bootstrap(active["site_id"]),
                    active["site_id"],
                    environment,
                )
            )
            self.component_registry.record_publication(
                site_id=active["site_id"],
                project_id=active["project_id"],
                deployment_digest=str(component_release.get("deployment_digest") or ""),
                component_set_digest=str(
                    component_release.get("component_set_digest") or ""
                ),
                projection_digest=str(component_release.get("projection_digest") or ""),
                renderer_abi=str(component_release.get("renderer_abi") or ""),
                actor="skill:" + skill_id,
                operation_id=operation_id,
                published_at=published_at,
                environment=environment,
                predecessor_deployment_digest=str(
                    previous_publication.get("deployment_digest") or ""
                )
                or None,
                acceptance_receipt={
                    "actor": "skill:" + skill_id,
                    "confirmed": True,
                    "revision_id": active["revision_id"],
                    "source_digest": active["source_digest"],
                    "plan_digest": plan_digest,
                },
            )
            operation.update(
                {
                    "status": "published",
                    "published_at": published_at,
                    "provider_receipt": str(result.get("receipt") or ""),
                    "mirror_receipt": str(result.get("mirror_receipt") or ""),
                    "firebase_receipt": str(result.get("firebase_receipt") or ""),
                    "gc_status": str(result.get("gc_status") or "deferred"),
                    "gc_receipt": str(result.get("gc_receipt") or ""),
                }
            )
            atomic_write_json(private_path, operation)
            public_operation = self._public_operation(operation)
            atomic_write_json(public_path, public_operation)
            registry = self._registry_with_legacy_bootstrap(active["site_id"])
            site_record = {
                "schema": "adaos.public_site_publication.v1",
                "site_id": active["site_id"],
                "project_id": active["project_id"],
                "environment": environment,
                "platform_path": operation["platform_path"],
                "public_url": public_url,
                "revision_id": active["revision_id"],
                "source_digest": active["source_digest"],
                "deployment_digest": pointer["deployment_digest"],
                "pointer_digest": pointer["pointer_digest"],
                "generation": pointer.get("generation"),
                "published_at": published_at,
                "operation_id": operation_id,
                "component_set_digest": component_release["component_set_digest"],
                "renderer_abi": component_release["renderer_abi"],
                "projection_digest": component_release["projection_digest"],
            }
            sites = dict(registry.get("sites") or {})
            channels = dict(registry.get("channels") or {})
            environment_sites = dict(channels.get(environment) or {})
            environment_sites[active["site_id"]] = site_record
            channels[environment] = environment_sites
            if environment == "prod":
                sites[active["site_id"]] = site_record
            registry.update(
                {
                    "schema": "adaos.public_site_publication_registry.v1",
                    "updated_at": _iso_now(),
                    "sites": sites,
                    "channels": channels,
                }
            )
            atomic_write_json(self.registry_path, registry)
        return self._status_with_components(
            active,
            site_record,
            public_operation,
            operation["platform_path"],
            environment=environment,
        )

    def component_status(
        self,
        *,
        scenario_id: str,
        skill_id: str,
        webspace_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        active, _, _ = self._active_authoring(
            scenario_id=scenario_id,
            skill_id=skill_id,
            webspace_id=webspace_id,
            project_id=project_id,
        )
        return self.component_registry.site_status(active["site_id"])

    def set_component_upgrade_policy(
        self,
        *,
        scenario_id: str,
        skill_id: str,
        webspace_id: str,
        project_id: str,
        mode: str,
    ) -> dict[str, Any]:
        active, _, _ = self._active_authoring(
            scenario_id=scenario_id,
            skill_id=skill_id,
            webspace_id=webspace_id,
            project_id=project_id,
        )
        return self.component_registry.set_upgrade_policy(
            active["site_id"], mode=mode, actor="skill:" + skill_id
        )

    def prepare_component_upgrade(
        self,
        *,
        scenario_id: str,
        skill_id: str,
        webspace_id: str,
        project_id: str,
        candidate_set_digest: str | None = None,
    ) -> dict[str, Any]:
        active, _, _ = self._active_authoring(
            scenario_id=scenario_id,
            skill_id=skill_id,
            webspace_id=webspace_id,
            project_id=project_id,
        )
        with mutation_lock(self.lock_path, timeout_s=30.0):
            publication_registry = self._registry_with_legacy_bootstrap(
                active["site_id"]
            )
            published = dict(
                (publication_registry.get("sites") or {}).get(active["site_id"]) or {}
            )
            if not published:
                raise ValueError("component upgrade requires a published site")
        review = self.component_registry.prepare_upgrade(
            active["site_id"],
            actor="skill:" + skill_id,
            candidate_set_digest=candidate_set_digest,
        )
        preview = self.component_registry.upgrade_preview(review["review_id"])
        grant = self.component_registry.issue_upgrade_preview(
            review["review_id"],
            owner_binding={
                "site_id": active["site_id"],
                "project_id": project_id,
                "scenario_id": scenario_id,
                "skill_id": skill_id,
                "webspace_id": webspace_id,
            },
        )
        fragment = ".".join(
            (grant["grant_id"], grant["review_id"], grant["token"])
        )
        return {
            "schema": "adaos.public_site_component_upgrade_candidate.v1",
            "review": review,
            "preview": preview,
            "preview_url": (
                f"/site-development/{active['site_id']}/"
                f"#release-preview={fragment}"
            ),
            "preview_grant": grant,
            "cache_policy": "private,no-store",
            "owner_only": True,
        }

    def resolve_component_upgrade_preview(
        self,
        *,
        scenario_id: str,
        skill_id: str,
        webspace_id: str,
        project_id: str,
        review_id: str,
        grant_id: str,
        token: str,
    ) -> dict[str, Any]:
        active, _, _ = self._active_authoring(
            scenario_id=scenario_id,
            skill_id=skill_id,
            webspace_id=webspace_id,
            project_id=project_id,
        )
        preview = self.component_registry.resolve_upgrade_preview(
            grant_id=grant_id,
            review_id=review_id,
            token=token,
            owner_binding={
                "site_id": active["site_id"],
                "project_id": project_id,
                "scenario_id": scenario_id,
                "skill_id": skill_id,
                "webspace_id": webspace_id,
            },
        )
        return {
            "schema": "adaos.public_site_component_upgrade_owner_preview.v1",
            "preview": preview,
            "cache_policy": "private,no-store",
            "owner_only": True,
        }

    def decide_component_upgrade(
        self,
        *,
        scenario_id: str,
        skill_id: str,
        webspace_id: str,
        project_id: str,
        review_id: str,
        plan_digest: str,
        decision: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        if confirmed is not True:
            raise ValueError("component upgrade decision requires confirmation")
        active, _, _ = self._active_authoring(
            scenario_id=scenario_id,
            skill_id=skill_id,
            webspace_id=webspace_id,
            project_id=project_id,
        )
        review = self.component_registry.upgrade_review(review_id)
        if review.get("site_id") != active["site_id"]:
            raise ValueError("component upgrade review belongs to another site")
        decided = self.component_registry.decide_upgrade(
            review_id,
            plan_digest=plan_digest,
            decision=decision,
            actor="skill:" + skill_id,
        )
        if decision == "reject":
            return {
                "schema": "adaos.public_site_component_upgrade_decision.v1",
                "review": decided,
                "publication": None,
            }

        operation_id = review_id
        private = self.private_root / "operations" / operation_id
        public = self.public_root / "operations" / f"{operation_id}.json"
        site_dir = private / "site"
        provider_workspace = private / "provider"
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._registry_with_legacy_bootstrap(active["site_id"])
            published = dict((registry.get("sites") or {}).get(active["site_id"]) or {})
            if (
                not published
                or published.get("deployment_digest")
                != decided.get("current_deployment_digest")
            ):
                raise ValueError("component upgrade base deployment changed")
            source_operation = self._private_operation(
                str(published.get("operation_id") or "")
            )
            source_site = Path(str(source_operation["site_dir"])).resolve(strict=True)
            private.mkdir(parents=True, exist_ok=False)
            shutil.copytree(source_site, site_dir)
            self._write_component_upgrade_bundle(site_dir, decided)
            assert_release_tree_secret_free(site_dir)
            admitted = self.component_registry.admit_bundle(
                site_dir,
                actor="skill:" + skill_id,
                operation_id=operation_id,
            )
            candidate_deployment = dict(decided["candidate_deployment"])
            if (
                admitted["deployment"]["deployment_digest"]
                != candidate_deployment["deployment_digest"]
            ):
                raise RuntimeError("component upgrade staging changed deployment identity")
            provider = self.delivery.prepare(
                site_dir=site_dir,
                workspace=provider_workspace,
                platform_path=str(published["platform_path"]),
            )
            provider_plan = _read_json(provider_workspace / "publication-plan.json")
            provider_plan_digest = str(provider.get("plan_sha256") or "")
            pointer = dict(provider_plan.get("pointer") or {})
            if (
                not _DIGEST.fullmatch(provider_plan_digest)
                or pointer.get("deployment_digest")
                != candidate_deployment["deployment_digest"]
                or provider_plan.get("platform_path") != published["platform_path"]
                or provider_plan.get("public_url") != published["public_url"]
            ):
                raise RuntimeError("component upgrade provider plan failed identity verification")
            operation = {
                "schema": "adaos.public_site_publication_operation.private.v1",
                "operation_id": operation_id,
                "kind": "component_upgrade",
                "status": "upgrade_prepared",
                "created_at": _iso_now(),
                "scenario_id": scenario_id,
                "skill_id": skill_id,
                "webspace_id": webspace_id,
                "active": active,
                "review_id": review_id,
                "review_plan_digest": plan_digest,
                "plan_digest": provider_plan_digest,
                "platform_path": published["platform_path"],
                "public_url": published["public_url"],
                "pointer": pointer,
                "site_dir": str(site_dir),
                "provider_workspace": str(provider_workspace),
                "previous_publication": published,
                "component_release": {
                    "component_set_digest": decided["candidate_set_digest"],
                    "renderer_abi": candidate_deployment["renderer_abi"],
                    "projection_digest": candidate_deployment["projection_digest"],
                    "deployment_digest": candidate_deployment["deployment_digest"],
                    "channel": admitted["channel"],
                },
                "acceptance_receipt": decided["decision_receipt"],
            }
            atomic_write_json(private / "operation.json", operation)
            public_operation = self._public_operation(operation)
            atomic_write_json(public, public_operation)
        return {
            "schema": "adaos.public_site_component_upgrade_decision.v1",
            "review": decided,
            "publication": public_operation,
        }

    def publish_component_upgrade(
        self,
        *,
        scenario_id: str,
        skill_id: str,
        webspace_id: str,
        project_id: str,
        operation_id: str,
        plan_digest: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        if confirmed is not True:
            raise ValueError("component upgrade publication requires confirmation")
        active, _, _ = self._active_authoring(
            scenario_id=scenario_id,
            skill_id=skill_id,
            webspace_id=webspace_id,
            project_id=project_id,
        )
        private_path = self.private_root / "operations" / operation_id / "operation.json"
        public_path = self.public_root / "operations" / f"{operation_id}.json"
        with mutation_lock(self.lock_path, timeout_s=30.0):
            operation = _read_json(private_path)
            registry = self._registry_with_legacy_bootstrap(active["site_id"])
            current = dict((registry.get("sites") or {}).get(active["site_id"]) or {})
            previous = dict(operation.get("previous_publication") or {})
            if (
                operation.get("kind") != "component_upgrade"
                or operation.get("status") != "upgrade_prepared"
                or operation.get("plan_digest") != plan_digest
                or operation.get("active") != active
                or current.get("deployment_digest")
                != previous.get("deployment_digest")
            ):
                raise ValueError("component upgrade publication is stale")
            result = self.delivery.publish(
                site_dir=Path(str(operation["site_dir"])),
                workspace=Path(str(operation["provider_workspace"])),
                platform_path=str(operation["platform_path"]),
                plan_digest=plan_digest,
            )
            published = self._commit_release_operation(
                registry=registry,
                active=active,
                operation=operation,
                result=result,
                skill_id=skill_id,
                acceptance_receipt=operation["acceptance_receipt"],
            )
            operation.update(published["operation"])
            atomic_write_json(private_path, operation)
            public_operation = self._public_operation(operation)
            atomic_write_json(public_path, public_operation)
            atomic_write_json(self.registry_path, published["registry"])
            self.component_registry.record_upgrade_publication(
                str(operation["review_id"]),
                deployment_digest=str(
                    operation["component_release"]["deployment_digest"]
                ),
                pointer_digest=str(operation["pointer"]["pointer_digest"]),
                actor="skill:" + skill_id,
            )
        return self._status_with_components(
            active,
            published["site"],
            public_operation,
            str(operation["platform_path"]),
        )

    def prepare_rollback(
        self,
        *,
        scenario_id: str,
        skill_id: str,
        webspace_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        active, _, _ = self._active_authoring(
            scenario_id=scenario_id,
            skill_id=skill_id,
            webspace_id=webspace_id,
            project_id=project_id,
        )
        component_status = self.component_registry.site_status(active["site_id"])
        target = dict(component_status.get("rollback_target") or {})
        if not target:
            raise ValueError("site has no retained rollback target")
        operation_id = "psrollback_" + uuid.uuid4().hex
        private = self.private_root / "operations" / operation_id
        public = self.public_root / "operations" / f"{operation_id}.json"
        site_dir = private / "site"
        provider_workspace = private / "provider"
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._registry_with_legacy_bootstrap(active["site_id"])
            current = dict((registry.get("sites") or {}).get(active["site_id"]) or {})
            target_operation = self._private_operation(str(target["operation_id"]))
            source_site = Path(str(target_operation["site_dir"])).resolve(strict=True)
            private.mkdir(parents=True, exist_ok=False)
            shutil.copytree(source_site, site_dir)
            assert_release_tree_secret_free(site_dir)
            provider = self.delivery.prepare(
                site_dir=site_dir,
                workspace=provider_workspace,
                platform_path=str(current["platform_path"]),
            )
            plan = _read_json(provider_workspace / "publication-plan.json")
            plan_digest = str(provider.get("plan_sha256") or "")
            pointer = dict(plan.get("pointer") or {})
            if (
                pointer.get("deployment_digest") != target["deployment_digest"]
                or not _DIGEST.fullmatch(plan_digest)
            ):
                raise RuntimeError("rollback provider plan failed identity verification")
            target_active = dict(target_operation.get("active") or active)
            operation = {
                "schema": "adaos.public_site_publication_operation.private.v1",
                "operation_id": operation_id,
                "kind": "rollback",
                "status": "rollback_prepared",
                "created_at": _iso_now(),
                "scenario_id": scenario_id,
                "skill_id": skill_id,
                "webspace_id": webspace_id,
                "active": active,
                "target_active": target_active,
                "plan_digest": plan_digest,
                "platform_path": current["platform_path"],
                "public_url": current["public_url"],
                "pointer": pointer,
                "site_dir": str(site_dir),
                "provider_workspace": str(provider_workspace),
                "previous_publication": current,
                "component_release": {
                    "component_set_digest": target["component_set_digest"],
                    "renderer_abi": target["renderer_abi"],
                    "projection_digest": target["projection_digest"],
                    "deployment_digest": target["deployment_digest"],
                },
            }
            atomic_write_json(private / "operation.json", operation)
            public_operation = self._public_operation(operation)
            atomic_write_json(public, public_operation)
        return public_operation

    def publish_rollback(
        self,
        *,
        scenario_id: str,
        skill_id: str,
        webspace_id: str,
        project_id: str,
        operation_id: str,
        plan_digest: str,
        confirmed: bool,
    ) -> dict[str, Any]:
        if confirmed is not True:
            raise ValueError("rollback requires explicit confirmation")
        active, _, _ = self._active_authoring(
            scenario_id=scenario_id,
            skill_id=skill_id,
            webspace_id=webspace_id,
            project_id=project_id,
        )
        private_path = self.private_root / "operations" / operation_id / "operation.json"
        public_path = self.public_root / "operations" / f"{operation_id}.json"
        with mutation_lock(self.lock_path, timeout_s=30.0):
            operation = _read_json(private_path)
            registry = self._registry_with_legacy_bootstrap(active["site_id"])
            current = dict((registry.get("sites") or {}).get(active["site_id"]) or {})
            previous = dict(operation.get("previous_publication") or {})
            if (
                operation.get("kind") != "rollback"
                or operation.get("status") != "rollback_prepared"
                or operation.get("plan_digest") != plan_digest
                or current.get("deployment_digest")
                != previous.get("deployment_digest")
            ):
                raise ValueError("rollback candidate is stale")
            result = self.delivery.publish(
                site_dir=Path(str(operation["site_dir"])),
                workspace=Path(str(operation["provider_workspace"])),
                platform_path=str(operation["platform_path"]),
                plan_digest=plan_digest,
            )
            target_active = dict(operation.get("target_active") or active)
            published = self._commit_release_operation(
                registry=registry,
                active=target_active,
                operation=operation,
                result=result,
                skill_id=skill_id,
                acceptance_receipt={
                    "actor": "skill:" + skill_id,
                    "confirmed": True,
                    "kind": "rollback",
                    "plan_digest": plan_digest,
                },
            )
            operation.update(published["operation"])
            atomic_write_json(private_path, operation)
            public_operation = self._public_operation(operation)
            atomic_write_json(public_path, public_operation)
            atomic_write_json(self.registry_path, published["registry"])
        return self._status_with_components(
            active,
            published["site"],
            public_operation,
            str(operation["platform_path"]),
        )

    def _write_component_upgrade_bundle(
        self, site_dir: Path, review: Mapping[str, Any]
    ) -> None:
        release = self.component_registry.release_artifacts(
            str(review["candidate_set_digest"])
        )
        packages_dir = site_dir / "components/packages"
        if packages_dir.is_dir():
            for item in packages_dir.iterdir():
                if item.is_file():
                    item.unlink()
        atomic_write_json(
            site_dir / "components/release-set.json", release["release_set"]
        )
        for relative, artifact in release["packages"].items():
            atomic_write_json(site_dir / relative, artifact)
        atomic_write_json(
            site_dir / "site.deployment.json", review["candidate_deployment"]
        )

    def _private_operation(self, operation_id: str) -> dict[str, Any]:
        if not _IDENTIFIER.fullmatch(operation_id):
            raise ValueError("publication operation identity is invalid")
        operation = _read_json(
            self.private_root / "operations" / operation_id / "operation.json"
        )
        if operation.get("operation_id") != operation_id:
            raise ValueError("publication operation identity mismatch")
        return operation

    def _commit_release_operation(
        self,
        *,
        registry: dict[str, Any],
        active: Mapping[str, Any],
        operation: Mapping[str, Any],
        result: Mapping[str, Any],
        skill_id: str,
        acceptance_receipt: Mapping[str, Any],
    ) -> dict[str, Any]:
        pointer = dict(operation.get("pointer") or {})
        component_release = dict(operation.get("component_release") or {})
        public_url = str(operation.get("public_url") or "")
        if (
            result.get("deployment_digest") != pointer.get("deployment_digest")
            or result.get("pointer_digest") != pointer.get("pointer_digest")
            or result.get("url") != public_url
        ):
            raise RuntimeError("release transition receipt failed identity verification")
        published_at = str(pointer.get("published_at") or _iso_now())
        previous = dict(operation.get("previous_publication") or {})
        environment = str(operation.get("environment") or _ENVIRONMENT)
        self.component_registry.record_publication(
            site_id=str(active["site_id"]),
            project_id=str(active["project_id"]),
            deployment_digest=str(component_release["deployment_digest"]),
            component_set_digest=str(component_release["component_set_digest"]),
            projection_digest=str(component_release["projection_digest"]),
            renderer_abi=str(component_release["renderer_abi"]),
            actor="skill:" + skill_id,
            operation_id=str(operation["operation_id"]),
            published_at=published_at,
            environment=environment,
            predecessor_deployment_digest=str(previous.get("deployment_digest") or "")
            or None,
            acceptance_receipt=acceptance_receipt,
        )
        operation_update = {
            "status": "published",
            "published_at": published_at,
            "provider_receipt": str(result.get("receipt") or ""),
            "mirror_receipt": str(result.get("mirror_receipt") or ""),
            "firebase_receipt": str(result.get("firebase_receipt") or ""),
            "gc_status": str(result.get("gc_status") or "deferred"),
            "gc_receipt": str(result.get("gc_receipt") or ""),
        }
        site = {
            "schema": "adaos.public_site_publication.v1",
            "site_id": active["site_id"],
            "project_id": active["project_id"],
            "environment": environment,
            "platform_path": operation["platform_path"],
            "public_url": public_url,
            "revision_id": active["revision_id"],
            "source_digest": active["source_digest"],
            "deployment_digest": pointer["deployment_digest"],
            "pointer_digest": pointer["pointer_digest"],
            "generation": pointer.get("generation"),
            "published_at": published_at,
            "operation_id": operation["operation_id"],
            "component_set_digest": component_release["component_set_digest"],
            "renderer_abi": component_release["renderer_abi"],
            "projection_digest": component_release["projection_digest"],
        }
        sites = dict(registry.get("sites") or {})
        channels = dict(registry.get("channels") or {})
        environment_sites = dict(channels.get(environment) or {})
        environment_sites[str(active["site_id"])] = site
        channels[environment] = environment_sites
        if environment == "prod":
            sites[str(active["site_id"])] = site
        registry.update(
            schema="adaos.public_site_publication_registry.v1",
            updated_at=_iso_now(),
            sites=sites,
            channels=channels,
        )
        return {"operation": operation_update, "site": site, "registry": registry}

    def _active_authoring(
        self,
        *,
        scenario_id: str,
        skill_id: str,
        webspace_id: str,
        project_id: str | None,
    ) -> tuple[dict[str, Any], Path, Path]:
        for value, label in (
            (scenario_id, "scenario_id"),
            (skill_id, "skill_id"),
            (webspace_id, "webspace_id"),
        ):
            if not _IDENTIFIER.fullmatch(str(value or "")):
                raise ValueError(f"{label} is invalid")
        ctx = get_ctx()
        runtime = self.trial_resolver(ctx, webspace_id, "scenario", scenario_id)
        if runtime is not None:
            runtime.component("skill", skill_id)
            runtime_root = Path(runtime.root).resolve(strict=True)
        else:
            # Finalize moves the same authoring data into the selected Workspace
            # runtime. Publication belongs to that selection, not forever to Trial.
            from adaos.services.applications.lifecycle import observe_runtime_admission
            from adaos.services.applications.store import ApplicationStore

            selection = selected_application(ctx, webspace_id, "scenario", scenario_id,
                                             installed_compatibility=False)
            skill_selection = selected_application(ctx, webspace_id, "skill", skill_id,
                                                   installed_compatibility=False)
            if (selection is None or skill_selection is None
                    or selection.runtime_root_ref != "workspace"
                    or (selection.application_id, selection.release_digest) != (
                        skill_selection.application_id, skill_selection.release_digest)):
                raise ValueError("selected Site Studio runtime is unavailable")
            store = ApplicationStore(Path(ctx.paths.state_dir()))
            admission = observe_runtime_admission(Path(ctx.paths.state_dir()), selection, store=store)
            if admission["admission"] != "admitted":
                raise ValueError("selected Site Studio runtime is unavailable")
            runtime_root = Path(ctx.paths.workspace_dir()).resolve(strict=True)
        skill_root = (
            runtime_root / "skills/.runtime" / skill_id
        ).resolve(strict=True)
        if not skill_root.is_relative_to(runtime_root):
            raise ValueError("Site Studio runtime escaped the selected Trial")
        marker = skill_root / "current_runtime.json"
        database_name = (
            project_id + ".sqlite3"
            if project_id and re.fullmatch(r"project-[0-9a-f]{32}", project_id)
            else "authoring.sqlite3"
        )
        if marker.is_file():
            bucket = str(_read_json(marker).get("runtime_bucket") or "")
            if not re.fullmatch(r"v\d+\.\d+(?:\.\d+)?", bucket):
                raise ValueError("selected Site Studio runtime bucket is invalid")
            candidates = [skill_root / bucket / "data" / database_name]
        else:
            candidates = skill_root.glob("v*/data/" + database_name)
        databases = [path for path in candidates if path.is_file() and not path.is_symlink()]
        if len(databases) != 1:
            raise ValueError("selected Site Studio authoring database is ambiguous")
        database = databases[0].resolve(strict=True)
        files_root = (database.parent / "files").resolve(strict=True)
        if not database.is_relative_to(skill_root) or not files_root.is_relative_to(skill_root):
            raise ValueError("Site Studio data escaped the selected Trial")
        active = read_active_revision(database)
        if project_id and active["project_id"] != project_id:
            raise ValueError("Site Studio project does not match the selected project")
        bind = getattr(self.delivery, "bind_authoring", None)
        if callable(bind):
            bind(active, scenario_id=scenario_id, skill_id=skill_id, webspace_id=webspace_id)
        return active, database, files_root

    @staticmethod
    def _assert_expected(
        active: Mapping[str, Any], revision_id: str, source_digest: str
    ) -> None:
        if (
            active.get("revision_id") != revision_id
            or active.get("source_digest") != source_digest
        ):
            raise ValueError("Site Studio revision changed; reload before publishing")

    def _registry_with_legacy_bootstrap(self, site_id: str) -> dict[str, Any]:
        if self.registry_path.is_file():
            registry = _read_json(self.registry_path)
            registry.setdefault("channels", {})
            return registry
        registry: dict[str, Any] = {
            "schema": "adaos.public_site_publication_registry.v1",
            "updated_at": _iso_now(),
            "sites": {},
            "channels": {},
        }
        legacy = self._legacy_publication(site_id)
        if legacy:
            registry["sites"] = {site_id: legacy}
            registry["channels"] = {"prod": {site_id: legacy}}
        atomic_write_json(self.registry_path, registry)
        return registry

    def _ensure_platform_path(
        self, registry: dict[str, Any], site_id: str
    ) -> str:
        addresses = dict(registry.get("addresses") or {})
        current = dict(addresses.get(site_id) or {})
        platform_path = str(current.get("platform_path") or "")
        published = dict((registry.get("sites") or {}).get(site_id) or {})
        observe = getattr(self.delivery, "site_status", None)
        availability = getattr(self.delivery, "availability", None)
        if callable(observe) and callable(availability) and availability().get("status") == "available":
            try:
                authoritative = observe(site_id)
            except RuntimeError as exc:
                if getattr(exc, "status_code", 0) not in {0, 500, 502, 503, 504}:
                    raise
                authoritative = {}
            if authoritative.get("platform_path"):
                platform_path = str(authoritative["platform_path"])
        if not platform_path:
            platform_path = str(published.get("platform_path") or "")
        if not _PLATFORM_PATH.fullmatch(platform_path):
            override_key = "ADAOS_PUBLIC_SITE_PLATFORM_PATH_" + re.sub(
                r"[^A-Za-z0-9]", "_", site_id
            ).upper()
            platform_path = os.getenv(override_key) or "st_" + uuid.uuid4().hex[:16]
            if not _PLATFORM_PATH.fullmatch(platform_path):
                raise ValueError("configured public site platform path is invalid")
        if current.get("platform_path") != platform_path:
            addresses[site_id] = {"platform_path": platform_path}
            registry.update(
                {
                    "updated_at": _iso_now(),
                    "addresses": addresses,
                }
            )
            atomic_write_json(self.registry_path, registry)
        return platform_path

    def _legacy_publication(self, site_id: str) -> dict[str, Any] | None:
        path = (
            self.state_dir
            / "private_recovery/root-infrastructure/cloudflare-public-site/publication-plan.json"
        )
        if not path.is_file():
            return None
        try:
            plan = _read_json(path)
            pointer = dict(plan.get("pointer") or {})
            site_dir = Path(str(plan.get("site_dir") or "")).resolve(strict=True)
            projection = _read_json(site_dir / "site.projection.json")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        if (
            plan.get("environment") != _ENVIRONMENT
            or plan.get("host") != "p.adaos.io"
            or projection.get("site_id") != site_id
            or not _DIGEST.fullmatch(str(projection.get("source_digest") or ""))
            or not _DIGEST.fullmatch(str(pointer.get("pointer_digest") or ""))
            or pointer.get("deployment_digest")
            != _read_json(site_dir / "site.deployment.json").get("deployment_digest")
        ):
            return None
        return {
            "schema": "adaos.public_site_publication.v1",
            "site_id": site_id,
            "project_id": site_id,
            "environment": _ENVIRONMENT,
            "public_url": _PUBLIC_ORIGIN + "/",
            "revision_id": str(projection.get("revision_id") or ""),
            "source_digest": projection["source_digest"],
            "deployment_digest": pointer["deployment_digest"],
            "pointer_digest": pointer["pointer_digest"],
            "generation": pointer.get("generation"),
            "published_at": pointer.get("published_at"),
            "operation_id": "legacy-ps3-bootstrap",
        }

    @staticmethod
    def _external_hostname(value: str) -> str:
        hostname = str(value or "").strip().lower().rstrip(".")
        if (
            not _HOSTNAME.fullmatch(hostname)
            or hostname == "adaos.io"
            or hostname.endswith(".adaos.io")
        ):
            raise ValueError("custom domain is invalid or reserved by AdaOS")
        return hostname

    @staticmethod
    def _managed_hostname(value: str) -> str:
        hostname = str(value or "").strip().lower().rstrip(".")
        reserved = {
            "p.adaos.io",
            "cf.p.adaos.io",
            "reg.p.adaos.io",
            "dev.p.adaos.io",
            "public-origin.adaos.io",
            "infra.adaos.io",
        }
        if (
            not _HOSTNAME.fullmatch(hostname)
            or not hostname.endswith(".adaos.io")
            or hostname in reserved
        ):
            raise ValueError("managed domain is invalid or reserved by AdaOS")
        return hostname

    def _domain_record(
        self,
        site_id: str,
        hostname: str,
        provider: Mapping[str, Any],
        *,
        current: Mapping[str, Any],
        publication: Mapping[str, Any],
    ) -> dict[str, Any]:
        platform_path = str(publication.get("platform_path") or "")
        environment = str(publication.get("environment") or _ENVIRONMENT)
        component_status = self.component_registry.site_status(
            site_id, environment=environment
        )
        rollback = dict(component_status.get("rollback_target") or {})
        provider_status = str(provider.get("status") or "pending")
        ssl_status = str(provider.get("ssl_status") or "pending")
        health_status, reason, action = self._domain_health(
            provider_status, ssl_status
        )
        record = {
            "schema": "adaos.public_site_domain.v1",
            "organization_id": current.get("organization_id")
            or publication.get("organization_id"),
            "ownership_scope": current.get("ownership_scope")
            or publication.get("ownership_scope")
            or {"kind": "subject", "id": "local-owner"},
            "project_id": publication.get("project_id"),
            "site_id": site_id,
            "hostname": hostname,
            "release": {
                "revision_id": publication.get("revision_id"),
                "source_digest": publication.get("source_digest"),
            },
            "deployment_digest": publication.get("deployment_digest"),
            "projection_digest": publication.get("projection_digest"),
            "component_set_digest": publication.get("component_set_digest"),
            "renderer_abi": publication.get("renderer_abi"),
            "status": provider_status,
            "verification_status": provider_status,
            "ssl_status": ssl_status,
            "certificate": {
                "status": ssl_status,
                "expires_at": provider.get("certificate_expires_at"),
                "renewal_status": provider.get("certificate_renewal_status")
                or ("managed" if ssl_status == "active" else "pending"),
            },
            "provider": provider.get("provider") or "",
            "provider_binding_refs": {
                "provider": provider.get("provider") or "",
                "binding_status": current.get("provider_binding")
                or provider.get("status")
                or "pending",
            },
            "ownership_verification": dict(
                provider.get("ownership_verification") or {}
            ),
            "certificate_validation": list(
                provider.get("certificate_validation") or []
            ),
            "routing_records": list(provider.get("routing_records") or []),
            "primary_canonical": bool(current.get("primary_canonical")),
            "platform_url": (
                _public_url(platform_path, environment) if platform_path else None
            ),
            "delivery_zone_policy": (
                {
                    "bootstrap": "firebase",
                    "preferred": "cloudflare",
                    "fallback": ["regru"],
                }
                if environment == "prod"
                else {
                    "bootstrap": "cloudflare",
                    "preferred": "cloudflare",
                    "fallback": [],
                }
            ),
            "routing_descriptor_generation": publication.get("generation"),
            "rollback_target": (
                {
                    "deployment_digest": rollback.get("deployment_digest"),
                    "projection_digest": rollback.get("projection_digest"),
                    "component_set_digest": rollback.get("component_set_digest"),
                    "renderer_abi": rollback.get("renderer_abi"),
                }
                if rollback
                else None
            ),
            "health": {
                "status": health_status,
                "reason_code": reason,
                "administrative_action": action,
                "checked_at": _iso_now(),
            },
            "requested_at": current.get("requested_at") or _iso_now(),
            "updated_at": _iso_now(),
            **(
                {"activated_at": current["activated_at"]}
                if current.get("activated_at")
                else {}
            ),
        }
        validate_contract("public_site_domain.v1.schema.json", record)
        return record

    @staticmethod
    def _domain_publication(
        registry: Mapping[str, Any], active: Mapping[str, Any]
    ) -> dict[str, Any]:
        site_id = str(active.get("site_id") or "")
        published = dict((registry.get("sites") or {}).get(site_id) or {})
        if published:
            return published
        address = dict((registry.get("addresses") or {}).get(site_id) or {})
        return {
            "site_id": site_id,
            "project_id": active.get("project_id"),
            "environment": _ENVIRONMENT,
            "platform_path": address.get("platform_path"),
            "revision_id": active.get("revision_id"),
            "source_digest": active.get("source_digest"),
        }

    @staticmethod
    def _domain_health(
        verification_status: str, ssl_status: str
    ) -> tuple[str, str, str | None]:
        if verification_status in {"conflict", "revoked"}:
            return "unavailable", "domain_" + verification_status, "contact_support"
        if verification_status != "active":
            return "pending", "dns_verification_pending", "update_dns_records"
        if ssl_status in {"expired", "revoked", "failed"}:
            return "unavailable", "certificate_" + ssl_status, "renew_certificate"
        if ssl_status != "active":
            return "degraded", "certificate_pending", "wait_for_certificate"
        return "healthy", "available", None

    def _latest_candidate(
        self, site_id: str, environment: str = _ENVIRONMENT
    ) -> dict[str, Any]:
        root = self.public_root / "operations"
        candidates: list[dict[str, Any]] = []
        if root.is_dir():
            for path in root.glob("pspub_*.json"):
                try:
                    item = _read_json(path)
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if (
                    item.get("site_id") == site_id
                    and item.get("environment", _ENVIRONMENT) == environment
                ):
                    candidates.append(item)
        return max(candidates, key=lambda item: str(item.get("created_at") or ""), default={})

    @staticmethod
    def _public_operation(operation: Mapping[str, Any]) -> dict[str, Any]:
        active = dict(operation.get("active") or {})
        pointer = dict(operation.get("pointer") or {})
        public_operation = {
            "schema": "adaos.public_site_publication_operation.v1",
            "operation_id": operation.get("operation_id"),
            "kind": operation.get("kind", "publication"),
            "environment": operation.get("environment", _ENVIRONMENT),
            "status": operation.get("status"),
            "created_at": operation.get("created_at"),
            "published_at": operation.get("published_at"),
            "site_id": active.get("site_id"),
            "project_id": active.get("project_id"),
            "revision_id": active.get("revision_id"),
            "source_digest": active.get("source_digest"),
            "deployment_digest": pointer.get("deployment_digest"),
            "pointer_digest": pointer.get("pointer_digest"),
            "plan_digest": operation.get("plan_digest"),
            "platform_path": operation.get("platform_path"),
            "public_url": operation.get("public_url"),
            "component_release": deepcopy(
                dict(operation.get("component_release") or {})
            ),
        }
        assert_release_surfaces_secret_free(
            {"publication-receipt.json": public_operation}
        )
        return public_operation

    def _status_with_components(
        self,
        active: Mapping[str, Any],
        published: Mapping[str, Any],
        candidate: Mapping[str, Any],
        platform_path: str,
        *,
        environment: str = _ENVIRONMENT,
    ) -> dict[str, Any]:
        result = self._status_view(
            active, published, candidate, platform_path, environment=environment
        )
        component_status = self.component_registry.site_status(
            str(active.get("site_id") or ""), environment=environment
        )
        candidate_release = dict(candidate.get("component_release") or {})
        if candidate_release:
            try:
                candidate_set = self.component_registry.release_set_status(
                    str(candidate_release.get("component_set_digest") or "")
                )
            except KeyError:
                candidate_set = None
            component_status["candidate_set"] = candidate_set
        result["components"] = component_status
        return result

    def _status_view(
        self,
        active: Mapping[str, Any],
        published: Mapping[str, Any],
        candidate: Mapping[str, Any],
        platform_path: str,
        *,
        environment: str = _ENVIRONMENT,
    ) -> dict[str, Any]:
        same_publication = bool(
            published
            and published.get("revision_id") == active.get("revision_id")
            and published.get("source_digest") == active.get("source_digest")
            and published.get("platform_path") == platform_path
        )
        ready = bool(
            candidate.get("status") == "prepared"
            and candidate.get("revision_id") == active.get("revision_id")
            and candidate.get("source_digest") == active.get("source_digest")
            and candidate.get("platform_path") == platform_path
        )
        status = (
            "published"
            if same_publication
            else "candidate_ready"
            if ready
            else "changes_pending"
            if published
            else "unpublished"
        )
        delivery_unavailable = isinstance(self.delivery, RootPublicationDeliveryUnavailable)
        delivery_status = ({"status": self.delivery.reason, "message": self.delivery.message}
                           if delivery_unavailable else {})
        observe = getattr(self.delivery, "availability", None)
        if callable(observe):
            delivery_status = observe()
            delivery_unavailable = delivery_status.get("status") != "available"
        observe_operation = getattr(self.delivery, "operation_status", None)
        if ready and not delivery_unavailable and callable(observe_operation):
            try:
                remote = observe_operation(self.private_root/"operations"/str(candidate.get("operation_id"))/"provider")
            except RuntimeError as exc:
                if getattr(exc, "status_code", 0) not in {0, 500, 502, 503, 504}:
                    raise
                remote = None
                delivery_unavailable = True
                delivery_status = {"status": "root_publication_delivery_unavailable", "message": "Infrastructure Root publication service could not be reached."}
            if remote is None:
                ready = False
                if not delivery_unavailable:
                    delivery_status = {"status": "publication_reprepare_required", "message": "Prepare this candidate again through infrastructure Root."}
            elif remote.get("status") not in {"prepared", "published"}:
                ready = False
                delivery_status = {"status": "publication_reconciliation_required", "message": "Root publication is pending status reconciliation; it will not be repeated."}
        if status == "candidate_ready" and not ready:
            status = "changes_pending" if published else "unpublished"
        return {
            "schema": "adaos.public_site_publication_status.v1",
            "site_id": active.get("site_id"),
            "project_id": active.get("project_id"),
            "environment": environment,
            "platform_path": platform_path,
            "public_url": _public_url(platform_path, environment),
            "status": status,
            "changes_pending": not same_publication,
            "can_prepare": not same_publication and not delivery_unavailable,
            "can_publish": ready and not delivery_unavailable,
            **({"delivery": delivery_status} if delivery_status else {}),
            "active": {
                "revision_id": active.get("revision_id"),
                "source_digest": active.get("source_digest"),
                "deployment_digest": active.get("preview_deployment_digest"),
            },
            "published": dict(published) if published else None,
            "candidate": dict(candidate) if ready else None,
        }

    @staticmethod
    def _release_environment(release_target: str) -> str:
        mapping = {"development": "dev", "production": "prod"}
        environment = mapping.get(str(release_target or "").strip())
        if environment is None:
            raise ValueError("public site release target is invalid")
        return environment

    @staticmethod
    def _published_for_environment(
        registry: Mapping[str, Any], site_id: str, environment: str
    ) -> dict[str, Any]:
        channels = dict(registry.get("channels") or {})
        selected = dict((channels.get(environment) or {}).get(site_id) or {})
        if selected:
            return selected
        if environment == "prod":
            return dict((registry.get("sites") or {}).get(site_id) or {})
        return {}


def public_site_publication_service() -> PublicSitePublicationService:
    from adaos.services.node_config import load_config
    from adaos.services.root.client import RootHttpClient
    from adaos.services.public_site_root_client import RootPublicationDelivery

    cfg = load_config(ctx=get_ctx())
    http = RootHttpClient.from_hub_config(cfg)
    return PublicSitePublicationService(delivery=RootPublicationDelivery(http) if http else None)


__all__ = [
    "CloudflarePublicationDelivery",
    "PublicSitePublicationService",
    "public_site_publication_service",
]
