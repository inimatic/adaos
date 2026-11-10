"""Infrastructure-only provider adapter; importing it needs no node runtime."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping
from adaos.services.artifact_pipeline.storage import atomic_write_json

_ENVIRONMENT = "prod"
_DELIVERY_ORIGINS = {"dev": "https://dev.p.adaos.io", "prod": "https://p.adaos.io"}
_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,179}$")
_PLATFORM_PATH = re.compile(r"^st_[a-z0-9]{6,32}$")

def _read_json(path):
    value=json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value,dict): raise ValueError("invalid publication object")
    return value

def _canonical_digest(value):
    return "sha256:"+hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()

def _normalize_digest(value):
    value=str(value or "")
    if re.fullmatch(r"[a-f0-9]{64}",value): value="sha256:"+value
    if not _DIGEST.fullmatch(value): raise ValueError("invalid publication digest")
    return value

def _public_url(platform_path,environment="prod"):
    if not _PLATFORM_PATH.fullmatch(platform_path) or environment not in _DELIVERY_ORIGINS:
        raise ValueError("invalid publication address")
    return _DELIVERY_ORIGINS[environment]+"/"+platform_path+"/"

class CloudflarePublicationDelivery:
    """Coordinate reviewed Firebase activation and immutable delivery zones."""

    def __init__(
        self,
        adaos_root: Path,
        *,
        state_dir: Path | None = None,
        timeout_seconds: float = 300.0,
    ):
        self.adaos_root = adaos_root.resolve()
        self.state_dir = Path(
            state_dir or self.adaos_root / ".adaos/state"
        ).resolve()
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
        self.firebase_script = Path(
            os.getenv("ADAOS_PUBLIC_SITE_FIREBASE_SCRIPT")
            or self.adaos_root
            / "src/adaos/integrations/infra-inimatic/scripts/public-site-firebase.py"
        ).resolve()
        self.firebase_domain_tool = Path(
            os.getenv("ADAOS_PUBLIC_SITE_FIREBASE_DOMAIN_TOOL")
            or self.adaos_root
            / "src/adaos/integrations/infra-inimatic/scripts/public-site-firebase-domain.mjs"
        ).resolve()
        self.gc_script = Path(
            os.getenv("ADAOS_PUBLIC_SITE_GC_SCRIPT")
            or self.adaos_root
            / "src/adaos/integrations/infra-inimatic/scripts/public-site-gc.py"
        ).resolve()
        self.firebase_cli = Path(
            os.getenv("ADAOS_PUBLIC_SITE_FIREBASE_CLI")
            or self.adaos_root
            / "src/adaos/integrations/adaos-client/node_modules/firebase-tools/lib/bin/firebase.js"
        ).resolve()
        self.firebase_state = Path(
            os.getenv("ADAOS_PUBLIC_SITE_FIREBASE_STATE")
            or self.state_dir / "private_recovery/public-sites/firebase-bootstrap"
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
        self,
        *,
        site_dir: Path,
        workspace: Path,
        platform_path: str,
        environment: str = _ENVIRONMENT,
    ) -> dict[str, Any]:
        if not _PLATFORM_PATH.fullmatch(platform_path):
            raise ValueError("public site platform path is invalid")
        if environment not in _DELIVERY_ORIGINS:
            raise ValueError("public site delivery environment is invalid")
        cloudflare_workspace = workspace / "cloudflare"
        cloudflare = self._run_cloudflare(
            "plan",
            site_dir=site_dir,
            workspace=cloudflare_workspace,
            platform_path=platform_path,
            environment=environment,
        )
        cloudflare_plan = _read_json(cloudflare_workspace / "publication-plan.json")
        provider_plans = {
            "cloudflare": _normalize_digest(cloudflare.get("plan_sha256"))
        }
        if (
            cloudflare_plan.get("environment") != environment
            or cloudflare_plan.get("platform_path") != platform_path
            or cloudflare_plan.get("public_url")
            != _public_url(platform_path, environment)
        ):
            raise RuntimeError("delivery zone plans disagree")
        if environment == "prod":
            regru_workspace = workspace / "regru"
            firebase_workspace = workspace / "firebase"
            regru = self._run_regru(
                "mirror-plan", site_dir=site_dir, workspace=regru_workspace
            )
            firebase = self._run_firebase(
                "routing-plan",
                workspace=firebase_workspace,
                provider_plan=cloudflare_workspace / "publication-plan.json",
            )
            if (
                cloudflare.get("deployment_digest")
                != regru.get("deployment_digest")
                or firebase.get("descriptor_digest")
                != cloudflare_plan["routing_descriptor"]["descriptor_digest"]
            ):
                raise RuntimeError("delivery zone plans disagree")
            provider_plans.update(
                regru=_normalize_digest(regru.get("plan_sha256")),
                firebase=_normalize_digest(firebase.get("plan_sha256")),
            )
        plan = {
            "schema": "adaos.public_site_zoned_publication_plan.v1",
            "environment": environment,
            "host": _DELIVERY_ORIGINS[environment].removeprefix("https://"),
            "platform_path": platform_path,
            "public_url": _public_url(platform_path, environment),
            "site_dir": str(site_dir.resolve()),
            "pointer": cloudflare_plan["pointer"],
            "routing_descriptor": cloudflare_plan["routing_descriptor"],
            "provider_plans": provider_plans,
        }
        workspace.mkdir(parents=True, exist_ok=True)
        atomic_write_json(workspace / "publication-plan.json", plan)
        record = {
            "ok": True,
            "plan_sha256": _canonical_digest(plan),
            "deployment_digest": cloudflare["deployment_digest"],
            "pointer_digest": cloudflare["pointer_digest"],
            "descriptor_digest": cloudflare_plan["routing_descriptor"]["descriptor_digest"],
        }
        return record

    def publish(
        self,
        *,
        site_dir: Path,
        workspace: Path,
        platform_path: str,
        plan_digest: str,
        environment: str = _ENVIRONMENT,
    ) -> dict[str, Any]:
        if not _DIGEST.fullmatch(plan_digest):
            raise ValueError("publication plan digest is invalid")
        plan = _read_json(workspace / "publication-plan.json")
        if (
            _canonical_digest(plan) != plan_digest
            or plan.get("site_dir") != str(site_dir.resolve())
            or plan.get("platform_path") != platform_path
            or plan.get("environment") != environment
        ):
            raise ValueError("reviewed zoned publication plan is stale")
        provider_plans = dict(plan.get("provider_plans") or {})
        regru = None
        if environment == "prod":
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
            environment=environment,
        )
        if result.get("url") != plan.get("public_url"):
            raise RuntimeError("Cloudflare activation returned another public address")
        if environment == "prod":
            firebase = self._run_firebase(
                "routing-apply",
                workspace=workspace / "firebase",
                provider_plan=workspace / "cloudflare/publication-plan.json",
                plan_digest=_normalize_digest(provider_plans.get("firebase")),
            )
            if (
                firebase.get("descriptor_digest")
                != plan["routing_descriptor"]["descriptor_digest"]
            ):
                raise RuntimeError(
                    "Firebase activation returned another routing descriptor"
                )
            result["mirror_receipt"] = regru.get("receipt") if regru else ""
            result["firebase_receipt"] = firebase.get("url")
            try:
                gc = self._run_gc(
                    site_id=str(plan["pointer"]["site_id"]),
                    workspace=workspace / "gc",
                )
                result["gc_status"] = "confirmed"
                result["gc_receipt"] = gc.get("receipt")
            except (OSError, RuntimeError, ValueError):
                # Artifact activation is already committed. Cleanup is retried by a
                # later publication and must not misreport this release as failed.
                result["gc_status"] = "deferred"
        else:
            result["gc_status"] = "not_applicable"
        return result

    def _run_gc(self, *, site_id: str, workspace: Path) -> dict[str, Any]:
        if not self.gc_script.is_file():
            raise RuntimeError("public-site GC adapter is unavailable")
        base = [
            sys.executable,
            str(self.gc_script),
            "--secrets-file",
            str(self.secrets),
            "--workspace",
            str(workspace.resolve()),
            "--site-id",
            site_id,
            "--retain-generations",
            "2",
            "--grace-days",
            "7",
        ]
        planned = self._execute([*base, "plan"], "public-site GC adapter")
        digest = str(planned.get("plan_sha256") or "")
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise RuntimeError("public-site GC adapter returned an invalid plan digest")
        return self._execute(
            [*base, "--approve-plan-sha256", digest, "apply"],
            "public-site GC adapter",
        )

    def _run_cloudflare(
        self,
        command: str,
        *,
        site_dir: Path,
        workspace: Path,
        platform_path: str,
        plan_digest: str | None = None,
        environment: str = _ENVIRONMENT,
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
            environment,
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

    def _run_firebase(
        self,
        command: str,
        *,
        workspace: Path,
        provider_plan: Path,
        plan_digest: str | None = None,
        domain_bindings: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        for path, label in (
            (self.firebase_script, "Firebase bootstrap adapter"),
            (self.firebase_cli, "Firebase CLI"),
            (provider_plan, "Cloudflare routing plan"),
        ):
            if not path.is_file():
                raise RuntimeError(f"{label} is unavailable")
        arguments = [
            sys.executable,
            str(self.firebase_script),
            "--workspace",
            str(workspace.resolve()),
            "--state-dir",
            str(self.firebase_state),
            "--provider-plan",
            str(provider_plan.resolve()),
            "--firebase-cli",
            str(self.firebase_cli),
        ]
        if plan_digest:
            arguments.extend(("--approve-plan-sha256", plan_digest))
        for binding in domain_bindings:
            arguments.extend(("--domain-binding", binding))
        arguments.append(command)
        return self._execute(arguments, "Firebase bootstrap adapter")

    def request_domain(self, hostname: str) -> dict[str, Any]:
        return self._firebase_domain("ensure", hostname)

    def status_domain(self, hostname: str) -> dict[str, Any]:
        return self._firebase_domain("status", hostname)

    def activate_domain(self, hostname: str, site_id: str) -> dict[str, Any]:
        status = self._firebase_domain("status", hostname)
        if status.get("status") != "active" or status.get("ssl_status") != "active":
            raise ValueError("Firebase custom domain and certificate must be active before binding")
        registry = _read_json(self.state_dir / "public-sites/registry.json")
        published = dict((registry.get("sites") or {}).get(site_id) or {})
        operation_id = str(published.get("operation_id") or "")
        platform_path = str(published.get("platform_path") or "")
        if not _IDENTIFIER.fullmatch(operation_id) or not _PLATFORM_PATH.fullmatch(platform_path):
            raise ValueError("custom domain requires an active published site")
        operation = _read_json(
            self.state_dir
            / "private_recovery/public-sites/operations"
            / operation_id
            / "operation.json"
        )
        workspace = Path(str(operation.get("provider_workspace") or "")).resolve(strict=True)
        provider_plan = (workspace / "cloudflare/publication-plan.json").resolve(strict=True)
        return self.activate_domain_for_plan(hostname, platform_path, provider_plan)

    def activate_domain_for_plan(self, hostname: str, platform_path: str, provider_plan: Path) -> dict[str, Any]:
        status = self._firebase_domain("status", hostname)
        if status.get("status") != "active" or status.get("ssl_status") != "active":
            raise ValueError("Firebase custom domain and certificate must be active before binding")
        domain_workspace = (
            self.state_dir
            / "private_recovery/public-sites/domains"
            / hashlib.sha256(hostname.encode()).hexdigest()
        )
        binding = f"{hostname}={platform_path}"
        planned = self._run_firebase(
            "routing-plan",
            workspace=domain_workspace,
            provider_plan=provider_plan,
            domain_bindings=(binding,),
        )
        plan_digest = _normalize_digest(planned.get("plan_sha256"))
        applied = self._run_firebase(
            "routing-apply",
            workspace=domain_workspace,
            provider_plan=provider_plan,
            plan_digest=plan_digest,
            domain_bindings=(binding,),
        )
        if applied.get("descriptor_digest") != planned.get("descriptor_digest"):
            raise RuntimeError("Firebase custom-domain binding changed the site descriptor")
        return {"status": "active", "provider": "firebase", "hostname": hostname}

    @staticmethod
    def _firebase_records(value: Any) -> list[dict[str, str]]:
        records: list[dict[str, str]] = []
        if not isinstance(value, Mapping):
            return records
        for domain in value.get("desired") or []:
            if not isinstance(domain, Mapping):
                continue
            for record in domain.get("records") or []:
                if not isinstance(record, Mapping):
                    continue
                name = str(record.get("domainName") or "").lower().rstrip(".")
                record_type = str(record.get("type") or "").upper()
                content = str(record.get("rdata") or "")
                if name and record_type and content:
                    records.append(
                        {"name": name, "type": record_type, "value": content}
                    )
        return records

    def _firebase_domain(self, command: str, hostname: str) -> dict[str, Any]:
        if command not in {"ensure", "status"}:
            raise ValueError("unsupported Firebase domain operation")
        tools_root = self.firebase_cli.parents[2]
        for path, label in (
            (self.firebase_domain_tool, "Firebase domain adapter"),
            (tools_root / "lib/apiv2.js", "Firebase tools API"),
        ):
            if not path.is_file():
                raise RuntimeError(f"{label} is unavailable")
        arguments = [
            "node",
            str(self.firebase_domain_tool),
            command,
            "--project",
            "myinimatic",
            "--site",
            "adaos-public-bootstrap",
            "--domain",
            hostname,
            "--firebase-tools-root",
            str(tools_root),
        ]
        result = subprocess.run(
            arguments,
            cwd=self.adaos_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_seconds,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            raise RuntimeError(
                "Firebase domain adapter failed: " + (result.stderr or result.stdout)[-1200:]
            )
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("Firebase domain adapter returned invalid output") from error
        if not isinstance(value, Mapping) or value.get("exists") is not True:
            raise RuntimeError("Firebase did not retain the custom domain")
        certificate = dict(value.get("cert") or {})
        verification = dict(certificate.get("verification") or {})
        routing = self._firebase_records(value.get("requiredDnsUpdates"))
        certificate_records = self._firebase_records(verification.get("dns"))
        ownership = next(
            (record for record in routing if record["type"] == "TXT"),
            routing[0] if routing else {},
        )
        active = (
            value.get("hostState") == "HOST_ACTIVE"
            and value.get("ownershipState") == "OWNERSHIP_ACTIVE"
            and certificate.get("state") == "CERT_ACTIVE"
        )
        return {
            "status": "active" if active else "pending",
            "ssl_status": "active" if certificate.get("state") == "CERT_ACTIVE" else "pending_validation",
            "provider": "firebase",
            "ownership_verification": ownership,
            "certificate_validation": [
                {
                    "txt_name": record["name"],
                    "txt_value": record["value"],
                    "type": record["type"],
                }
                for record in certificate_records
            ],
            "routing_records": routing,
        }

    def _run_domain(
        self, command: str, *, hostname: str, site_id: str | None = None
    ) -> dict[str, Any]:
        workspace = self.state_dir / "private_recovery/public-sites/domains"
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
            "--hostname",
            hostname,
            "--approve-hostname",
            hostname,
        ]
        if site_id:
            arguments.extend(("--site-id", site_id))
        arguments.append(command)
        return self._execute(arguments, "Cloudflare domain adapter")

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

