from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Mapping, Protocol
import uuid

from adaos.services.agent_context import get_ctx
from adaos.services.applications.runtime_selection import selected_trial
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.site_studio.publication_export import (
    export_publication,
    read_active_revision,
)


_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,179}$")
_PUBLIC_ORIGIN = "https://p.adaos.io"
_ENVIRONMENT = "prod"
_PLATFORM_PATH = re.compile(r"^st_[a-z0-9]{6,32}$")


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


def _public_url(platform_path: str) -> str:
    if not _PLATFORM_PATH.fullmatch(platform_path):
        raise ValueError("public site platform path is invalid")
    return f"{_PUBLIC_ORIGIN}/{platform_path}/"


class PublicationDelivery(Protocol):
    def prepare(
        self, *, site_dir: Path, workspace: Path, platform_path: str
    ) -> dict[str, Any]: ...

    def publish(
        self, *, site_dir: Path, workspace: Path, platform_path: str, plan_digest: str
    ) -> dict[str, Any]: ...


class CloudflarePublicationDelivery:
    """Coordinate reviewed Cloudflare activation and REG.RU mirroring."""

    def __init__(self, adaos_root: Path, *, timeout_seconds: float = 240.0):
        self.adaos_root = adaos_root.resolve()
        self.timeout_seconds = timeout_seconds
        self.script = Path(
            os.getenv("ADAOS_PUBLIC_SITE_PUBLISH_SCRIPT")
            or self.adaos_root
            / "src/adaos/integrations/infra-inimatic/scripts/public-site-publish.py"
        ).resolve()
        self.regru_script = Path(
            os.getenv("ADAOS_PUBLIC_SITE_REGRU_SCRIPT")
            or self.adaos_root
            / "src/adaos/integrations/infra-inimatic/scripts/public-site-regru.py"
        ).resolve()
        self.secrets = Path(
            os.getenv("ADAOS_PUBLIC_SITE_SECRETS_FILE")
            or self.adaos_root
            / ".adaos/state/private_recovery/secrets/.env"
        ).resolve()
        self.client_release = Path(
            os.getenv("ADAOS_PUBLIC_SITE_CLIENT_RELEASE")
            or self.adaos_root
            / "src/adaos/integrations/adaos-client/www/public-site-runtime/client-release.json"
        ).resolve()

    def prepare(
        self, *, site_dir: Path, workspace: Path, platform_path: str
    ) -> dict[str, Any]:
        if not _PLATFORM_PATH.fullmatch(platform_path):
            raise ValueError("public site platform path is invalid")
        cloudflare_workspace = workspace / "cloudflare"
        regru_workspace = workspace / "regru"
        cloudflare = self._run_cloudflare(
            "plan",
            site_dir=site_dir,
            workspace=cloudflare_workspace,
            platform_path=platform_path,
        )
        regru = self._run_regru(
            "mirror-plan", site_dir=site_dir, workspace=regru_workspace
        )
        cloudflare_plan = _read_json(cloudflare_workspace / "publication-plan.json")
        if (
            cloudflare.get("deployment_digest") != regru.get("deployment_digest")
            or cloudflare_plan.get("platform_path") != platform_path
            or cloudflare_plan.get("public_url") != _public_url(platform_path)
        ):
            raise RuntimeError("delivery zone plans disagree")
        plan = {
            "schema": "adaos.public_site_zoned_publication_plan.v1",
            "environment": _ENVIRONMENT,
            "host": "p.adaos.io",
            "platform_path": platform_path,
            "public_url": _public_url(platform_path),
            "site_dir": str(site_dir.resolve()),
            "pointer": cloudflare_plan["pointer"],
            "routing_descriptor": cloudflare_plan["routing_descriptor"],
            "provider_plans": {
                "cloudflare": _normalize_digest(cloudflare.get("plan_sha256")),
                "regru": _normalize_digest(regru.get("plan_sha256")),
            },
        }
        workspace.mkdir(parents=True, exist_ok=True)
        atomic_write_json(workspace / "publication-plan.json", plan)
        return {
            "ok": True,
            "plan_sha256": _canonical_digest(plan),
            "deployment_digest": cloudflare["deployment_digest"],
            "pointer_digest": cloudflare["pointer_digest"],
            "descriptor_digest": cloudflare_plan["routing_descriptor"]["descriptor_digest"],
        }

    def publish(
        self, *, site_dir: Path, workspace: Path, platform_path: str, plan_digest: str
    ) -> dict[str, Any]:
        if not _DIGEST.fullmatch(plan_digest):
            raise ValueError("publication plan digest is invalid")
        plan = _read_json(workspace / "publication-plan.json")
        if (
            _canonical_digest(plan) != plan_digest
            or plan.get("site_dir") != str(site_dir.resolve())
            or plan.get("platform_path") != platform_path
        ):
            raise ValueError("reviewed zoned publication plan is stale")
        provider_plans = dict(plan.get("provider_plans") or {})
        regru = self._run_regru(
            "mirror-apply",
            site_dir=site_dir,
            workspace=workspace / "regru",
            plan_digest=_normalize_digest(provider_plans.get("regru")),
        )
        if regru.get("deployment_digest") != plan["pointer"]["deployment_digest"]:
            raise RuntimeError("REG.RU mirror receipt does not match publication")
        result = self._run_cloudflare(
            "apply",
            site_dir=site_dir,
            workspace=workspace / "cloudflare",
            platform_path=platform_path,
            plan_digest=_normalize_digest(provider_plans.get("cloudflare"))[7:],
        )
        if result.get("url") != plan.get("public_url"):
            raise RuntimeError("Cloudflare activation returned another public address")
        result["mirror_receipt"] = regru.get("receipt")
        return result

    def _run_cloudflare(
        self,
        command: str,
        *,
        site_dir: Path,
        workspace: Path,
        platform_path: str,
        plan_digest: str | None = None,
    ) -> dict[str, Any]:
        for path, label in (
            (self.script, "Cloudflare publication adapter"),
            (self.secrets, "Cloudflare publication credentials"),
            (self.client_release, "public Client release"),
        ):
            if not path.is_file():
                raise RuntimeError(f"{label} is unavailable")
        arguments = [
            sys.executable,
            str(self.script),
            "--secrets-file",
            str(self.secrets),
            "--workspace",
            str(workspace.resolve()),
            "--adaos-root",
            str(self.adaos_root),
            "--environment",
            _ENVIRONMENT,
            "--site-dir",
            str(site_dir.resolve()),
            "--client-release",
            str(self.client_release),
            "--platform-path",
            platform_path,
        ]
        if plan_digest:
            arguments.extend(("--approve-plan-sha256", plan_digest))
        arguments.append(command)
        return self._execute(arguments, "Cloudflare publication adapter")

    def _run_regru(
        self,
        command: str,
        *,
        site_dir: Path,
        workspace: Path,
        plan_digest: str | None = None,
    ) -> dict[str, Any]:
        for path, label in (
            (self.regru_script, "REG.RU publication adapter"),
            (self.secrets, "REG.RU publication credentials"),
        ):
            if not path.is_file():
                raise RuntimeError(f"{label} is unavailable")
        arguments = [
            sys.executable,
            str(self.regru_script),
            "--secrets-file",
            str(self.secrets),
            "--workspace",
            str(workspace.resolve()),
            "--adaos-root",
            str(self.adaos_root),
            "--site-dir",
            str(site_dir.resolve()),
        ]
        if plan_digest:
            arguments.extend(("--approve-plan-sha256", plan_digest))
        arguments.append(command)
        return self._execute(arguments, "REG.RU publication adapter")

    def _execute(self, arguments: list[str], label: str) -> dict[str, Any]:
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            arguments,
            cwd=self.adaos_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_seconds,
            check=False,
            creationflags=creation_flags,
        )
        if result.returncode:
            message = (result.stderr or result.stdout or f"{label} failed").strip()
            raise RuntimeError(message[-2000:])
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"{label} returned invalid output") from error
        if not isinstance(payload, Mapping) or payload.get("ok") is not True:
            raise RuntimeError(f"{label} did not confirm the operation")
        return dict(payload)


class PublicSitePublicationService:
    def __init__(
        self,
        *,
        state_dir: Path | None = None,
        adaos_root: Path | None = None,
        delivery: PublicationDelivery | None = None,
        trial_resolver: Callable[..., Any] | None = None,
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
        self.delivery = delivery or CloudflarePublicationDelivery(self.adaos_root)
        self.trial_resolver = trial_resolver or selected_trial

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
            published = dict((registry.get("sites") or {}).get(active["site_id"]) or {})
            candidate = self._latest_candidate(active["site_id"])
        return self._status_view(active, published, candidate, platform_path)

    def prepare(
        self,
        *,
        scenario_id: str,
        skill_id: str,
        webspace_id: str,
        project_id: str,
        expected_revision_id: str,
        expected_source_digest: str,
    ) -> dict[str, Any]:
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
            receipt = export_publication(database, files_root, site_dir)
            if (
                receipt["revision_id"] != active["revision_id"]
                or receipt["source_digest"] != active["source_digest"]
            ):
                raise ValueError("Site Studio revision changed during export")
            provider = self.delivery.prepare(
                site_dir=site_dir,
                workspace=provider_workspace,
                platform_path=platform_path,
            )
            plan_path = provider_workspace / "publication-plan.json"
            plan = _read_json(plan_path)
            plan_digest = str(provider.get("plan_sha256") or "")
            pointer = dict(plan.get("pointer") or {})
            if (
                not _DIGEST.fullmatch(plan_digest)
                or provider.get("deployment_digest") != receipt["deployment_digest"]
                or pointer.get("deployment_digest") != receipt["deployment_digest"]
                or plan.get("environment") != _ENVIRONMENT
                or plan.get("host") != "p.adaos.io"
                or plan.get("platform_path") != platform_path
                or plan.get("public_url") != _public_url(platform_path)
            ):
                raise RuntimeError("publication provider plan failed identity verification")
            created_at = _iso_now()
            private_record = {
                "schema": "adaos.public_site_publication_operation.private.v1",
                "operation_id": operation_id,
                "status": "prepared",
                "created_at": created_at,
                "scenario_id": scenario_id,
                "skill_id": skill_id,
                "webspace_id": webspace_id,
                "active": active,
                "export": receipt,
                "plan_digest": plan_digest,
                "platform_path": platform_path,
                "public_url": _public_url(platform_path),
                "pointer": pointer,
                "site_dir": str(site_dir),
                "provider_workspace": str(provider_workspace),
            }
            atomic_write_json(private / "operation.json", private_record)
            public_record = self._public_operation(private_record)
            atomic_write_json(public, public_record)
        published = dict((registry.get("sites") or {}).get(active["site_id"]) or {})
        return self._status_view(active, published, public_record, platform_path)

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
            )
            pointer = dict(operation.get("pointer") or {})
            public_url = _public_url(str(operation.get("platform_path") or ""))
            if (
                result.get("deployment_digest") != pointer.get("deployment_digest")
                or result.get("pointer_digest") != pointer.get("pointer_digest")
                or result.get("url") != public_url
            ):
                raise RuntimeError("publication receipt failed identity verification")
            published_at = str(pointer.get("published_at") or _iso_now())
            operation.update(
                {
                    "status": "published",
                    "published_at": published_at,
                    "provider_receipt": str(result.get("receipt") or ""),
                    "mirror_receipt": str(result.get("mirror_receipt") or ""),
                }
            )
            atomic_write_json(private_path, operation)
            public_operation = self._public_operation(operation)
            atomic_write_json(public_path, public_operation)
            registry = self._registry_with_legacy_bootstrap(active["site_id"])
            sites = dict(registry.get("sites") or {})
            sites[active["site_id"]] = {
                "schema": "adaos.public_site_publication.v1",
                "site_id": active["site_id"],
                "project_id": active["project_id"],
                "environment": _ENVIRONMENT,
                "platform_path": operation["platform_path"],
                "public_url": public_url,
                "revision_id": active["revision_id"],
                "source_digest": active["source_digest"],
                "deployment_digest": pointer["deployment_digest"],
                "pointer_digest": pointer["pointer_digest"],
                "generation": pointer.get("generation"),
                "published_at": published_at,
                "operation_id": operation_id,
            }
            registry.update(
                {
                    "schema": "adaos.public_site_publication_registry.v1",
                    "updated_at": _iso_now(),
                    "sites": sites,
                }
            )
            atomic_write_json(self.registry_path, registry)
        return self._status_view(
            active, sites[active["site_id"]], public_operation, operation["platform_path"]
        )

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
        runtime = self.trial_resolver(
            get_ctx(), webspace_id, "scenario", scenario_id
        )
        if runtime is None:
            raise ValueError("selected Site Studio Trial is unavailable")
        runtime.component("skill", skill_id)
        runtime_root = Path(runtime.root).resolve(strict=True)
        skill_root = (
            runtime_root / "skills/.runtime" / skill_id
        ).resolve(strict=True)
        if not skill_root.is_relative_to(runtime_root):
            raise ValueError("Site Studio runtime escaped the selected Trial")
        databases = [
            path
            for path in skill_root.glob("v*/data/authoring.sqlite3")
            if path.is_file() and not path.is_symlink()
        ]
        if len(databases) != 1:
            raise ValueError("selected Site Studio authoring database is ambiguous")
        database = databases[0].resolve(strict=True)
        files_root = (database.parent / "files").resolve(strict=True)
        if not database.is_relative_to(skill_root) or not files_root.is_relative_to(skill_root):
            raise ValueError("Site Studio data escaped the selected Trial")
        active = read_active_revision(database)
        if project_id and active["project_id"] != project_id:
            raise ValueError("Site Studio project does not match the selected project")
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
            return _read_json(self.registry_path)
        registry: dict[str, Any] = {
            "schema": "adaos.public_site_publication_registry.v1",
            "updated_at": _iso_now(),
            "sites": {},
        }
        legacy = self._legacy_publication(site_id)
        if legacy:
            registry["sites"] = {site_id: legacy}
        atomic_write_json(self.registry_path, registry)
        return registry

    def _ensure_platform_path(
        self, registry: dict[str, Any], site_id: str
    ) -> str:
        addresses = dict(registry.get("addresses") or {})
        current = dict(addresses.get(site_id) or {})
        platform_path = str(current.get("platform_path") or "")
        published = dict((registry.get("sites") or {}).get(site_id) or {})
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

    def _latest_candidate(self, site_id: str) -> dict[str, Any]:
        root = self.public_root / "operations"
        candidates: list[dict[str, Any]] = []
        if root.is_dir():
            for path in root.glob("pspub_*.json"):
                try:
                    item = _read_json(path)
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if item.get("site_id") == site_id:
                    candidates.append(item)
        return max(candidates, key=lambda item: str(item.get("created_at") or ""), default={})

    @staticmethod
    def _public_operation(operation: Mapping[str, Any]) -> dict[str, Any]:
        active = dict(operation.get("active") or {})
        pointer = dict(operation.get("pointer") or {})
        return {
            "schema": "adaos.public_site_publication_operation.v1",
            "operation_id": operation.get("operation_id"),
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
        }

    @staticmethod
    def _status_view(
        active: Mapping[str, Any],
        published: Mapping[str, Any],
        candidate: Mapping[str, Any],
        platform_path: str,
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
        return {
            "schema": "adaos.public_site_publication_status.v1",
            "site_id": active.get("site_id"),
            "project_id": active.get("project_id"),
            "environment": _ENVIRONMENT,
            "platform_path": platform_path,
            "public_url": _public_url(platform_path),
            "status": status,
            "changes_pending": not same_publication,
            "can_prepare": not same_publication,
            "can_publish": ready,
            "active": {
                "revision_id": active.get("revision_id"),
                "source_digest": active.get("source_digest"),
                "deployment_digest": active.get("preview_deployment_digest"),
            },
            "published": dict(published) if published else None,
            "candidate": dict(candidate) if ready else None,
        }


def public_site_publication_service() -> PublicSitePublicationService:
    return PublicSitePublicationService()


__all__ = [
    "CloudflarePublicationDelivery",
    "PublicSitePublicationService",
    "public_site_publication_service",
]
