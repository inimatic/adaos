"""Validation for Builder's compact, package-neutral CBS authoring layer."""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, ValidationError

from .workflow import BuilderWorkflowError


BUILDER_CBS_INTENT_SCHEMA = "adaos.builder.cbs_intent.v1"


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    path = Path(__file__).resolve().parents[2] / "abi" / "builder.cbs_intent.v1.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def validate_cbs_intent(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a canonical copy of one compact CBS intent or fail closed."""

    result = copy.deepcopy(dict(value))
    try:
        _validator().validate(result)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        if isinstance(exc, ValidationError):
            location = ".".join(str(item) for item in exc.absolute_path)
            suffix = f" at {location}" if location else ""
            message = f"{exc.message}{suffix}"
        else:
            message = str(exc)
        raise BuilderWorkflowError(f"invalid Builder CBS intent: {message}") from exc

    ids = [str(item["id"]) for item in result["requirements"]]
    if len(ids) != len(set(ids)):
        raise BuilderWorkflowError("Builder CBS intent requirement ids must be unique")
    return result


__all__ = ["BUILDER_CBS_INTENT_SCHEMA", "validate_cbs_intent"]
