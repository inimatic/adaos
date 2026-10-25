"""Immutable, content-addressed Contract Evolution records."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker


IDENTITIES = {
    "adaos.contract_change.v1": "change_id",
    "adaos.consumer_contract_usage_lock.v1": "usage_lock_id",
    "adaos.consumer_contract_assessment.v1": "assessment_id",
    "adaos.contract_migration_receipt.v1": "receipt_id",
    "adaos.contract_evolution_wave.v1": "wave_id",
    "adaos.contract_verification.v1": "verification_id",
}
POLICY = {
    "schema": "adaos.contract_evolution_policy.v1",
    "revision": 1,
    "unknown": "verify_before_adaptation",
    "breaking": "consumer_contract_verification",
    "transparent_migration": "registered_declarative_only",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


POLICY_DIGEST = digest(POLICY)


def instant(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Contract Evolution timestamps require a timezone")
    return result


@lru_cache(maxsize=8)
def _validator(schema: str) -> Draft202012Validator:
    if schema not in IDENTITIES:
        raise ValueError("unsupported Contract Evolution record schema")
    path = Path(__file__).resolve().parents[2] / "abi" / (schema.removeprefix("adaos.") + ".schema.json")
    return Draft202012Validator(json.loads(path.read_text(encoding="utf-8")), format_checker=FormatChecker())


def validate(record: Mapping[str, Any]) -> dict[str, Any]:
    value = deepcopy(dict(record))
    schema = str(value.get("schema", ""))
    _validator(schema).validate(value)
    identity = IDENTITIES[schema]
    content = {k: v for k, v in value.items() if k not in {identity, "digest", "recorded_at"}}
    expected = digest(content)
    if value["digest"] != expected or value[identity] != schema.removeprefix("adaos.").removesuffix(".v1") + ":" + expected:
        raise ValueError("Contract Evolution record digest/identity mismatch")
    instant(value["recorded_at"])
    for key in ("removal_after", "evidence_expires_at", "valid_until"):
        if value.get(key):
            instant(value[key])
    return value


def seal(schema: str, **fields: Any) -> dict[str, Any]:
    identity = IDENTITIES[schema]
    content = {"schema": schema, **deepcopy(fields)}
    hashed = digest(content)
    return validate({**content, identity: schema.removeprefix("adaos.").removesuffix(".v1") + ":" + hashed,
                     "digest": hashed, "recorded_at": utc_now()})

