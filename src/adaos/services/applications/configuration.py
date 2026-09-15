"""Local typed configuration, independent of release packages and business data.

Callers admit the application/component owner and credential references before
using this store. Secrets themselves belong to the credential service, never here.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


class ConfigurationConflict(ValueError):
    pass


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _validate(schema: Mapping[str, Any], values: Mapping[str, Any]) -> None:
    # Configuration validation never performs network retrieval or reports values.
    def check_refs(node):
        if isinstance(node, dict):
            for key, item in node.items():
                if key in {"$id", "$dynamicRef", "$dynamicAnchor"}:
                    raise ValueError("Configuration schema requires local JSON Pointer references")
                if key == "$ref" and not (isinstance(item, str) and (item == "#" or item.startswith("#/"))):
                    raise ValueError("Configuration schema cannot load external references")
                if key == "writeOnly" and item is True:
                    raise ValueError("Secret fields require credential bindings, not configuration values")
                check_refs(item)
        elif isinstance(node, list):
            for item in node:
                check_refs(item)
    check_refs(dict(schema))
    if schema.get("type") != "object":
        raise ValueError("Configuration schema must describe an object")
    Draft202012Validator.check_schema(schema)
    if next(Draft202012Validator(schema).iter_errors(dict(values)), None) is not None:
        raise ConfigurationConflict("Configuration is incompatible with the target schema; explicit migration or review required")
    _digest(values)


def _bindings(value: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) or not key or not isinstance(ref, str)
        or not ref.startswith("credential:") or len(ref) <= len("credential:")
        for key, ref in value.items()
    ):
        raise ValueError("Secrets must be admitted opaque credential: references")
    return dict(value)


def _overlay(base: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    # Top-level settings are atomic typed fields; null differs from deletion.
    return {"set": {key: deepcopy(value) for key, value in current.items()
                    if key not in base or value != base[key]},
            "remove": sorted(set(base) - set(current))}


def _apply(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    value = deepcopy(dict(base))
    value.update(deepcopy(overlay["set"]))
    for key in overlay["remove"]:
        value.pop(key, None)
    return value


class ApplicationConfigurationStore:
    """One local installation/component, serialized with revision preconditions."""

    def __init__(self, state_root: Path, application_id: str, component_ref: str):
        if not application_id or not component_ref.startswith(("skill:", "scenario:")):
            raise ValueError("Application and owned component identity are required")
        self.identity = {"application_id": application_id, "component_ref": component_ref}
        key = _digest(self.identity).split(":", 1)[1]
        self.path = Path(state_root) / "applications/configuration" / f"{key}.json"

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema": "adaos.application.configuration.v1", **self.identity,
                    "revision": 0, "stable": None, "beta": None, "adoption": None}
        record = json.loads(self.path.read_text(encoding="utf-8"))
        if (record.get("schema") != "adaos.application.configuration.v1"
                or any(record.get(key) != value for key, value in self.identity.items())):
            raise ValueError("Configuration store identity mismatch")
        return record

    def _expected(self, expected_revision: int) -> dict[str, Any]:
        record = self._read()
        if isinstance(expected_revision, bool) or record["revision"] != expected_revision:
            raise ConfigurationConflict("Configuration changed; reopen settings before applying")
        return record

    def _save(self, record: dict[str, Any]) -> dict[str, Any]:
        record["revision"] += 1
        atomic_write_json(self.path, record)
        return deepcopy(record)

    def read(self) -> dict[str, Any]:
        """Local owner view, not an LLM/publication context or diagnostic payload."""
        return self._read()

    def set_stable(self, *, release_digest: str, schema: Mapping[str, Any],
                   values: Mapping[str, Any], credentials: Mapping[str, str],
                   expected_revision: int) -> dict[str, Any]:
        _validate(schema, values)
        refs = _bindings(credentials)
        if not release_digest:
            raise ValueError("Stable release identity is required")
        with mutation_lock(self.path.with_suffix(".lock")):
            record = self._expected(expected_revision)
            if (record.get("beta") or {}).get("active"):
                raise ConfigurationConflict("Stable configuration is inactive while Beta is selected")
            record["stable"] = {"release_digest": release_digest, "schema_digest": _digest(schema),
                                "values": deepcopy(dict(values)), "credentials": refs}
            return self._save(record)

    def prepare_beta(self, *, candidate_id: str, release_digest: str,
                     source_release_digest: str | None, schema: Mapping[str, Any],
                     defaults: Mapping[str, Any], expected_revision: int) -> dict[str, Any]:
        if not candidate_id or not release_digest:
            raise ValueError("Exact Beta Candidate and release identities are required")
        with mutation_lock(self.path.with_suffix(".lock")):
            record = self._expected(expected_revision)
            stable = record.get("stable")
            if (stable or {}).get("release_digest") != source_release_digest:
                raise ConfigurationConflict("Installed Stable configuration does not match the Beta base")
            previous = record.get("beta")
            base_values = {**deepcopy(dict(defaults)), **deepcopy((stable or {}).get("values") or {})}
            base_credentials = deepcopy((stable or {}).get("credentials") or {})
            fingerprint = _digest({"source": stable, "defaults": defaults})
            if previous and previous["candidate_id"] == candidate_id:
                if (previous["release_digest"] != release_digest or previous["schema_digest"] != _digest(schema)
                        or previous["base_fingerprint"] != fingerprint):
                    raise ConfigurationConflict("Candidate configuration contract changed")
                if previous["active"]:
                    return deepcopy(record)
                previous["active"] = True
                return self._save(record)
            values, credentials = base_values, base_credentials
            if previous:
                if previous["stable_fingerprint"] != _digest(stable):
                    raise ConfigurationConflict("Stable configuration changed underneath Beta overrides; review required")
                values = _apply(base_values, previous["value_overrides"])
                credentials = _apply(base_credentials, previous["credential_overrides"])
            _validate(schema, values)
            record["beta"] = {"active": True, "candidate_id": candidate_id,
                "release_digest": release_digest, "source_release_digest": source_release_digest,
                "schema_digest": _digest(schema), "base_fingerprint": fingerprint,
                "stable_fingerprint": _digest(stable), "base_values": base_values,
                "base_credentials": base_credentials, "values": values, "credentials": credentials,
                "value_overrides": _overlay(base_values, values),
                "credential_overrides": _overlay(base_credentials, credentials)}
            return self._save(record)

    def update_beta(self, *, candidate_id: str, schema: Mapping[str, Any],
                    values: Mapping[str, Any], credentials: Mapping[str, str],
                    expected_revision: int) -> dict[str, Any]:
        _validate(schema, values)
        refs = _bindings(credentials)
        with mutation_lock(self.path.with_suffix(".lock")):
            record = self._expected(expected_revision)
            beta = self._beta(record, candidate_id)
            if beta["schema_digest"] != _digest(schema):
                raise ConfigurationConflict("Beta schema changed; configuration migration required")
            beta.update(values=deepcopy(dict(values)), credentials=refs,
                        value_overrides=_overlay(beta["base_values"], values),
                        credential_overrides=_overlay(beta["base_credentials"], refs))
            return self._save(record)

    @staticmethod
    def _beta(record: Mapping[str, Any], candidate_id: str) -> dict[str, Any]:
        beta = record.get("beta")
        if not beta or beta["candidate_id"] != candidate_id or not beta["active"]:
            raise ConfigurationConflict("This Beta configuration is not active")
        return beta

    def adopt_beta(self, *, candidate_id: str, expected_revision: int) -> dict[str, Any]:
        """Called inside accepted data/runtime cutover, not from a settings form."""
        with mutation_lock(self.path.with_suffix(".lock")):
            record = self._expected(expected_revision)
            if not record.get("beta") and (record.get("adoption") or {}).get("candidate_id") == candidate_id:
                return record
            beta = self._beta(record, candidate_id)
            if _digest(record.get("stable")) != beta["stable_fingerprint"]:
                raise ConfigurationConflict("Stable configuration changed before acceptance")
            record["stable"] = {key: deepcopy(beta[key]) for key in
                                ("release_digest", "schema_digest", "values", "credentials")}
            record["adoption"] = {"candidate_id": candidate_id, "release_digest": beta["release_digest"]}
            record["beta"] = None
            return self._save(record)

    def deactivate_beta(self, *, candidate_id: str, expected_revision: int) -> dict[str, Any]:
        """Retain overrides after an explicitly admitted return to Stable."""
        with mutation_lock(self.path.with_suffix(".lock")):
            record = self._expected(expected_revision)
            self._beta(record, candidate_id)["active"] = False
            return self._save(record)

    def credential_reference(self, name: str, *, candidate_id: str | None = None) -> str | None:
        record = self._read()
        if candidate_id:
            config = self._beta(record, candidate_id)
        else:
            if (record.get("beta") or {}).get("active"):
                raise ConfigurationConflict("Stable configuration is inactive while Beta is selected")
            config = record.get("stable") or {}
        return (config.get("credentials") or {}).get(name)
