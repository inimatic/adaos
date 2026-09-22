from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator


APPLICATION_SETUP_CONTRACT_SCHEMA = "adaos.application.setup_contract.v1"


def canonical_setup_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "abi" / "application.setup_contract.v1.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True, slots=True)
class ApplicationSetupContract:
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        value = json.loads(json.dumps(dict(self.payload), ensure_ascii=False))
        errors = sorted(
            Draft202012Validator(_schema()).iter_errors(value),
            key=lambda item: list(item.path),
        )
        if errors:
            path = ".".join(str(item) for item in errors[0].path) or "contract"
            raise ValueError(f"invalid Application setup contract at {path}: {errors[0].message}")
        component_refs = [item["component_ref"] for item in value["components"]]
        if len(component_refs) != len(set(component_refs)):
            raise ValueError("Application setup component refs must be unique")
        requirement_ids = [item["id"] for item in value["connected_accounts"]]
        requirement_ids.extend(item["id"] for item in value["permissions"])
        requirement_ids.extend(item["id"] for item in value["verification"])
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("Application setup requirement ids must be unique")
        object.__setattr__(self, "payload", value)

    @property
    def application_id(self) -> str:
        return str(self.payload["application_id"])

    @property
    def release_digest(self) -> str:
        return str(self.payload["release_digest"])

    @property
    def digest(self) -> str:
        return canonical_setup_digest(self.payload)

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.payload, ensure_ascii=False))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ApplicationSetupContract":
        return cls(payload=value)


__all__ = [
    "APPLICATION_SETUP_CONTRACT_SCHEMA",
    "ApplicationSetupContract",
    "canonical_setup_digest",
]
