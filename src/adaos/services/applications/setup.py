"""Release-owned setup contracts and secret-redacted setup state projection."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from adaos.domain.application_setup import ApplicationSetupContract
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


_READY = {"ready", "not_applicable"}
_ACTION_REQUIRED = {"missing", "denied", "invalid", "expired", "revoked", "failed", "unknown"}


class ApplicationSetupConflict(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _state_schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "abi" / "application.setup_state.v1.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _title(identifier: str) -> str:
    return identifier.replace("_", " ").replace("-", " ").strip().title()


def _status(value: Any, *, default: str = "unknown") -> str:
    token = str(value or default).strip().lower()
    allowed = _READY | _ACTION_REQUIRED | {"pending"}
    return token if token in allowed else default


def compile_setup_contract(
    *,
    application_id: str,
    release_digest: str,
    component_manifests: Mapping[str, Mapping[str, Any]],
    permission_profile: Mapping[str, Any] | None = None,
    connected_accounts: Sequence[Mapping[str, Any]] = (),
    placement_required: bool = True,
    verification: Sequence[Mapping[str, Any]] = (),
) -> ApplicationSetupContract:
    """Compile trusted release declarations; values and credential refs are excluded."""

    components: list[dict[str, Any]] = []
    for component_ref, manifest in sorted(component_manifests.items()):
        declaration = manifest.get("configuration") if isinstance(manifest, Mapping) else None
        settings = None
        credentials: list[dict[str, Any]] = []
        if isinstance(declaration, Mapping):
            schema = declaration.get("schema")
            defaults = declaration.get("defaults")
            if isinstance(schema, Mapping) and isinstance(defaults, Mapping):
                settings = {"schema": deepcopy(dict(schema)), "defaults": deepcopy(dict(defaults))}
            slots = declaration.get("credentials")
            if isinstance(slots, Mapping):
                for slot, raw in sorted(slots.items()):
                    item = dict(raw) if isinstance(raw, Mapping) else {}
                    credentials.append(
                        {
                            "slot": str(slot),
                            "title": str(item.get("title") or _title(str(slot))),
                            "purpose": str(item.get("purpose") or "").strip(),
                            "required": bool(item.get("required", False)),
                        }
                    )
        if settings is not None or credentials:
            components.append(
                {
                    "component_ref": str(component_ref),
                    "settings": settings,
                    "credentials": credentials,
                }
            )

    profile = dict(permission_profile or {})
    permissions: list[dict[str, Any]] = []
    for required, key in ((True, "required"), (False, "optional")):
        for raw in profile.get(key) or ():
            if not isinstance(raw, Mapping):
                continue
            permission_id = str(raw.get("id") or "").strip()
            permissions.append(
                {
                    "id": permission_id,
                    "title": str(raw.get("title") or _title(permission_id)),
                    "purpose": str(raw.get("purpose") or "Permission requested by the Application"),
                    "required": required,
                }
            )

    accounts: list[dict[str, Any]] = []
    for raw in connected_accounts:
        item = dict(raw)
        account_id = str(item.get("id") or item.get("provider") or "").strip()
        accounts.append(
            {
                "id": account_id,
                "title": str(item.get("title") or _title(account_id)),
                "purpose": str(item.get("purpose") or "Connect an external account"),
                "required": bool(item.get("required", False)),
                "scopes": sorted({str(scope) for scope in item.get("scopes") or () if str(scope).strip()}),
            }
        )

    checks = [
        {
            "id": str(item.get("id") or "").strip(),
            "title": str(item.get("title") or _title(str(item.get("id") or "verification"))),
            "required": bool(item.get("required", True)),
        }
        for item in verification
    ]
    payload = {
        "schema": "adaos.application.setup_contract.v1",
        "application_id": application_id,
        "release_digest": release_digest,
        "version": 1,
        "components": components,
        "connected_accounts": accounts,
        "permissions": permissions,
        "placement": {"required": bool(placement_required), "title": "Runtime placement"},
        "verification": checks,
    }
    return ApplicationSetupContract(payload)


def project_setup_state(
    contract: ApplicationSetupContract,
    *,
    channel: str,
    configuration: Mapping[str, Mapping[str, Any]] | None = None,
    credential_presence: Mapping[str, Sequence[str]] | None = None,
    provider_configuration_status: Mapping[str, str] | None = None,
    connected_account_status: Mapping[str, str] | None = None,
    permission_status: Mapping[str, str] | None = None,
    placement_status: str = "unknown",
    verification_status: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    configuration = configuration or {}
    credential_presence = credential_presence or {}
    provider_configuration_status = provider_configuration_status or {}
    connected_account_status = connected_account_status or {}
    permission_status = permission_status or {}
    verification_status = verification_status or {}
    requirements: list[dict[str, Any]] = []

    for component in contract.payload["components"]:
        component_ref = component["component_ref"]
        settings = component.get("settings")
        if settings:
            values = configuration.get(component_ref) or {}
            defaults = settings["defaults"]
            for field in settings["schema"].get("required") or ():
                ready = field in values or field in defaults
                requirements.append(
                    {
                        "requirement_id": f"setting:{component_ref}:{field}",
                        "kind": "setting",
                        "component_ref": component_ref,
                        "title": _title(str(field)),
                        "detail": "Required non-secret Application setting.",
                        "required": True,
                        "status": "ready" if ready else "missing",
                        "action": "none" if ready else "configure",
                    }
                )
        present = set(credential_presence.get(component_ref) or ())
        for credential in component["credentials"]:
            ready = credential["slot"] in present
            requirements.append(
                {
                    "requirement_id": f"credential:{component_ref}:{credential['slot']}",
                    "kind": "credential",
                    "component_ref": component_ref,
                    "title": credential["title"],
                    "detail": credential["purpose"],
                    "required": credential["required"],
                    "status": "ready" if ready else "missing",
                    "action": "none" if ready else "provide_secret",
                }
            )

    for account in contract.payload["connected_accounts"]:
        provider_status = _status(
            provider_configuration_status.get(account["id"]),
            default="not_applicable",
        )
        if provider_status != "not_applicable":
            requirements.append(
                {
                    "requirement_id": f"provider_configuration:{account['id']}",
                    "kind": "provider_configuration",
                    "title": f"{account['title']} provider",
                    "detail": "The node provider must be configured before an account can be connected.",
                    "required": account["required"],
                    "status": provider_status,
                    "action": (
                        "none" if provider_status in _READY else "configure_provider"
                    ),
                }
            )
        status = _status(connected_account_status.get(account["id"]), default="missing")
        requirements.append(
            {
                "requirement_id": f"connected_account:{account['id']}",
                "kind": "connected_account",
                "title": account["title"],
                "detail": account["purpose"],
                "required": account["required"],
                "status": status,
                "action": "none" if status in _READY else "connect_account",
            }
        )

    for permission in contract.payload["permissions"]:
        status = _status(permission_status.get(permission["id"]), default="pending")
        requirements.append(
            {
                "requirement_id": f"permission:{permission['id']}",
                "kind": "permission",
                "title": permission["title"],
                "detail": permission["purpose"],
                "required": permission["required"],
                "status": status,
                "action": "none" if status in _READY else "review_access",
            }
        )

    placement = contract.payload["placement"]
    placement_value = _status(placement_status)
    requirements.append(
        {
            "requirement_id": "placement:runtime",
            "kind": "placement",
            "title": placement["title"],
            "detail": "Desired runtime placement must match an observed compatible activation.",
            "required": placement["required"],
            "status": placement_value,
            "action": "none" if placement_value in _READY else "manage_placement",
        }
    )

    for check in contract.payload["verification"]:
        status = _status(verification_status.get(check["id"]), default="pending")
        requirements.append(
            {
                "requirement_id": f"verification:{check['id']}",
                "kind": "verification",
                "title": check["title"],
                "detail": "Release-owned setup verification.",
                "required": check["required"],
                "status": status,
                "action": "none" if status in _READY else "retry_validation",
            }
        )

    required = [item for item in requirements if item["required"]]
    required_action = [item for item in required if item["status"] in _ACTION_REQUIRED]
    required_pending = [item for item in required if item["status"] == "pending"]
    if required_action:
        status = "action_required"
    elif required_pending:
        status = "validating" if all(item["kind"] == "verification" for item in required_pending) else "configuring"
    else:
        status = "ready"
    optional_missing = [item for item in requirements if not item["required"] and item["status"] not in _READY]
    return {
        "schema": "adaos.application.setup_state.v1",
        "application_id": contract.application_id,
        "release_digest": contract.release_digest,
        "channel": channel,
        "contract_digest": contract.digest,
        "status": status,
        "requirements": requirements,
        "summary": {
            "required_total": len(required),
            "ready_total": sum(item["status"] in _READY for item in required),
            "action_required_total": len(required_action),
            "optional_missing_total": len(optional_missing),
        },
    }


class ApplicationSetupStateStore:
    """CAS store for the latest secret-redacted setup projection per channel."""

    def __init__(self, state_dir: Path) -> None:
        self.root = Path(state_dir) / "applications" / "setup"

    @staticmethod
    def _identity(application_id: str, channel: str) -> str:
        return hashlib.sha256(f"{application_id}|{channel}".encode("utf-8")).hexdigest()

    def _path(self, application_id: str, channel: str) -> Path:
        return self.root / self._identity(application_id, channel) / "current.json"

    def read(self, application_id: str, channel: str) -> dict[str, Any] | None:
        path = self._path(application_id, channel)
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        self._validate(value)
        return value

    def reconcile(self, projection: Mapping[str, Any], *, expected_revision: int) -> dict[str, Any]:
        value = deepcopy(dict(projection))
        application_id = str(value.get("application_id") or "")
        channel = str(value.get("channel") or "")
        path = self._path(application_id, channel)
        with mutation_lock(path.with_suffix(".lock"), timeout_s=30.0):
            current = self.read(application_id, channel)
            observed = int((current or {}).get("revision") or 0)
            if observed != expected_revision:
                raise ApplicationSetupConflict(
                    f"Application setup changed; expected revision {expected_revision}, observed {observed}"
                )
            comparable = dict(current or {})
            comparable.pop("revision", None)
            comparable.pop("updated_at", None)
            if current is not None and comparable == value:
                return current
            value["revision"] = observed + 1
            value["updated_at"] = _now()
            self._validate(value)
            atomic_write_json(path, value)
            return deepcopy(value)

    @staticmethod
    def _validate(value: Mapping[str, Any]) -> None:
        errors = sorted(
            Draft202012Validator(_state_schema()).iter_errors(value),
            key=lambda item: list(item.path),
        )
        if errors:
            raise ApplicationSetupConflict(f"invalid Application setup state: {errors[0].message}")


__all__ = [
    "ApplicationSetupConflict",
    "ApplicationSetupStateStore",
    "compile_setup_contract",
    "project_setup_state",
]
