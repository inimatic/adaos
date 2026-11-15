"""Classify conclusive native admission failures without inventing runtime effects."""

from collections.abc import Mapping
from typing import Any


def rejected_before_activation(result: Mapping[str, Any]) -> bool:
    """Recognize the native executor's rejection before snapshot/desired-state writes.

    The bounded legacy shape also repairs operations that older Core versions
    incorrectly marked unknown while checking an absent snapshot. Generic
    failures, exceptions and partial deployment results remain inconclusive.
    """
    admission = result.get("cbs_admission")
    return (
        result.get("ok") is False
        and result.get("status") == "failed"
        and result.get("reason") == "native_cbs_admission_required"
        and isinstance(admission, Mapping)
        and admission.get("schema") == "adaos.application.cbs_admission_failure.v1"
        and admission.get("status") == "failed"
        and result.get("snapshot_receipt") is None
        and not any(result.get(key) for key in (
            "deployment", "deployment_operation", "workspace_activation", "restore_receipt"
        ))
    )


def execution_error(result: Mapping[str, Any]) -> dict[str, Any] | None:
    admission = result.get("cbs_admission")
    if not isinstance(admission, Mapping) or admission.get("status") != "failed":
        return None
    return {
        "code": admission.get("error_type") or admission.get("reason"),
        "message": admission.get("message") or "Release admission failed before activation.",
        "phase": "admission",
        "runtime_changed": False if rejected_before_activation(result) else None,
        **({"conflict": dict(admission["conflict"])}
           if isinstance(admission.get("conflict"), Mapping) else {}),
    }
