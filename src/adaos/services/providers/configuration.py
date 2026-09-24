"""Node-owned configuration for reusable external providers.

OAuth client credentials configure the provider implementation, not an
Application release or a connected user account.  Values therefore live only
in the node credential vault while this service persists a secret-redacted CAS
revision for setup orchestration and UI rendering.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


PROVIDER_CONFIGURATION_SCHEMA = "adaos.provider.configuration_state.v1"
_PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")

_DECLARATIONS: dict[str, dict[str, Any]] = {
    "google.gmail": {
        "title": "Google OAuth client",
        "purpose": (
            "Configure the node OAuth client used to connect delegated Gmail accounts. "
            "The same provider configuration can serve multiple Applications."
        ),
        "fields": (
            {
                "id": "client_id",
                "type": "shortText",
                "label": "Google OAuth client ID",
                "helpText": "OAuth 2.0 Web application client ID from Google Cloud.",
                "required": True,
                "vault_key": "provider:google.oauth:client_id",
            },
            {
                "id": "client_secret",
                "type": "password",
                "label": "Google OAuth client secret",
                "helpText": "Stored only in the node credential vault.",
                "required": True,
                "vault_key": "provider:google.oauth:client_secret",
            },
        ),
    },
}


class ProviderConfigurationError(ValueError):
    pass


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _safe_id(provider_id: str) -> str:
    value = str(provider_id or "").strip().lower()
    if not _PROVIDER_ID.fullmatch(value):
        raise ProviderConfigurationError("provider_id is invalid")
    return value


@dataclass(slots=True)
class ProviderConfigurationService:
    state_dir: Path
    vault: Any

    @property
    def root(self) -> Path:
        return Path(self.state_dir) / "providers" / "configuration"

    def declaration(self, provider_id: str) -> Mapping[str, Any] | None:
        return _DECLARATIONS.get(_safe_id(provider_id))

    def inspect(self, provider_id: str) -> dict[str, Any]:
        identity = _safe_id(provider_id)
        declaration = self.declaration(identity)
        if declaration is None:
            return {
                "schema": PROVIDER_CONFIGURATION_SCHEMA,
                "provider_id": identity,
                "supported": False,
                "status": "not_applicable",
                "revision": 0,
                "fields": [],
                "present_fields": [],
                "missing_fields": [],
            }
        record = self._read(identity)
        presence: dict[str, bool] = {}
        for field in declaration["fields"]:
            try:
                presence[str(field["id"])] = bool(
                    str(self.vault.get(str(field["vault_key"]), default=None) or "").strip()
                )
            except Exception as exc:
                raise ProviderConfigurationError(
                    "provider credential vault is unavailable"
                ) from exc
        required = {
            str(field["id"])
            for field in declaration["fields"]
            if bool(field.get("required"))
        }
        present = sorted(field for field, value in presence.items() if value)
        missing = sorted(required - set(present))
        return {
            "schema": PROVIDER_CONFIGURATION_SCHEMA,
            "provider_id": identity,
            "title": str(declaration["title"]),
            "purpose": str(declaration["purpose"]),
            "supported": True,
            "status": "ready" if not missing else "missing",
            "revision": int((record or {}).get("revision") or 0),
            "fields": [
                {
                    key: value
                    for key, value in field.items()
                    if key != "vault_key"
                }
                for field in declaration["fields"]
            ],
            "present_fields": present,
            "missing_fields": missing,
            "updated_at": (record or {}).get("updated_at"),
        }

    def configure(
        self,
        provider_id: str,
        values: Mapping[str, Any],
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        identity = _safe_id(provider_id)
        declaration = self.declaration(identity)
        if declaration is None:
            raise ProviderConfigurationError(
                f"provider configuration is not supported: {identity}"
            )
        fields = {str(item["id"]): item for item in declaration["fields"]}
        supplied = dict(values)
        unknown = sorted(set(supplied) - set(fields))
        if unknown:
            raise ProviderConfigurationError(
                "provider configuration contains unknown fields: " + ", ".join(unknown)
            )
        if not supplied:
            raise ProviderConfigurationError("provider configuration values are required")
        path = self._path(identity)
        with mutation_lock(path.with_suffix(".lock"), timeout_s=30.0):
            current = self._read(identity)
            observed = int((current or {}).get("revision") or 0)
            if observed != int(expected_revision):
                raise ProviderConfigurationError(
                    f"provider configuration changed; expected revision {expected_revision}, "
                    f"observed {observed}"
                )
            for field_id, raw in supplied.items():
                field = fields[field_id]
                if raw is not None and not isinstance(raw, str):
                    raise ProviderConfigurationError(
                        f"provider configuration {field_id} must be a string or null"
                    )
                value = str(raw or "").strip()
                if len(value.encode("utf-8")) > 65_536:
                    raise ProviderConfigurationError(
                        f"provider configuration {field_id} exceeds 64 KiB"
                    )
                try:
                    if value:
                        self.vault.put(
                            str(field["vault_key"]),
                            value,
                            meta={
                                "provider_id": identity,
                                "configuration_field": field_id,
                            },
                        )
                    else:
                        self.vault.delete(str(field["vault_key"]))
                except Exception as exc:
                    raise ProviderConfigurationError(
                        "provider credential vault update failed"
                    ) from exc
            saved = {
                "schema": PROVIDER_CONFIGURATION_SCHEMA,
                "provider_id": identity,
                "revision": observed + 1,
                "updated_at": _now(),
                "configured_fields": sorted(supplied),
            }
            atomic_write_json(path, saved)
        return self.inspect(identity)

    def _path(self, provider_id: str) -> Path:
        return self.root / f"{_safe_id(provider_id)}.json"

    def _read(self, provider_id: str) -> dict[str, Any] | None:
        path = self._path(provider_id)
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(value, Mapping)
            or value.get("schema") != PROVIDER_CONFIGURATION_SCHEMA
            or value.get("provider_id") != provider_id
        ):
            raise ProviderConfigurationError("provider configuration state is invalid")
        return dict(value)


__all__ = [
    "PROVIDER_CONFIGURATION_SCHEMA",
    "ProviderConfigurationError",
    "ProviderConfigurationService",
]
