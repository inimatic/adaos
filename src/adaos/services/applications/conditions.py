"""User-facing Application conditions derived from authoritative lifecycle state."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any


_ATTENTION_PRESENTATION: dict[str, tuple[str, str, int]] = {
    "degraded": ("alert-circle-outline", "danger", 70),
    "action_required": ("warning-outline", "warning", 60),
    "progressing": ("sync-circle-outline", "primary", 50),
    "suspended": ("pause-circle-outline", "medium", 40),
    "update_available": ("cloud-download-outline", "primary", 30),
    "current": ("checkmark-circle-outline", "success", 20),
    "available": ("cloud-download-outline", "medium", 10),
    "unknown": ("help-circle-outline", "medium", 0),
}


def _condition(
    condition_type: str,
    status: str,
    reason: str,
    message: str,
    *,
    observed_generation: int | None = None,
    last_transition_time: str | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "type": condition_type,
        "status": status,
        "reason": reason,
        "message": message,
    }
    if observed_generation is not None:
        value["observed_generation"] = observed_generation
    if last_transition_time:
        value["last_transition_time"] = last_transition_time
    return value


def _attention_status(
    model: Mapping[str, Any], conditions: list[dict[str, Any]]
) -> tuple[str, str, str]:
    installed = bool(model.get("installed"))
    available = bool(model.get("available"))
    by_type = {item["type"]: item for item in conditions}
    ready = by_type.get("Ready", {})
    progressing = by_type.get("Progressing", {})
    suspended = by_type.get("Suspended", {})

    if ready.get("status") == "False" and ready.get("reason") in {
        "OperationFailed",
        "InstallationDegraded",
        "PlacementUnavailable",
    }:
        return "degraded", str(ready.get("reason")), str(ready.get("message"))
    if ready.get("status") == "False":
        return "action_required", str(ready.get("reason")), str(ready.get("message"))
    if progressing.get("status") == "True":
        return (
            "progressing",
            str(progressing.get("reason")),
            str(progressing.get("message")),
        )
    if suspended.get("status") == "True":
        return "suspended", str(suspended.get("reason")), str(suspended.get("message"))
    if bool(model.get("update_available")):
        return (
            "update_available",
            "NewReleaseAvailable",
            "A newer compatible release is available.",
        )
    if installed and ready.get("status") == "True":
        return "current", "Ready", "The installed Application is ready and current."
    if not installed and available:
        return (
            "available",
            "AvailableForInstall",
            "The Application is available to install.",
        )
    if ready.get("status") == "Unknown":
        return "unknown", str(ready.get("reason")), str(ready.get("message"))
    return "unknown", "StateIncomplete", "The Application state is incomplete."


def application_condition_projection(model: Mapping[str, Any]) -> dict[str, Any]:
    """Build a compact SOTA-style condition and attention projection.

    Conditions preserve machine-readable status/reason/message details.  The
    attention record is the single user-facing state used by inventory lists.
    """

    application = model.get("application") or {}
    installation = model.get("installation") or {}
    subscription = model.get("subscription") or {}
    operation = model.get("operation") or {}
    placement = model.get("execution_placement") or {}
    installed = bool(model.get("installed"))
    available = bool(model.get("available"))
    aggregate_backed = bool(application.get("aggregate_backed", True))
    operation_status = str(operation.get("status") or "").strip().lower()
    placement_status = str(placement.get("status") or "").strip().lower()
    installation_status = str(installation.get("status") or "").strip().lower()
    managed_placement = bool(placement.get("managed"))
    revision = int(installation.get("revision") or 0) or None
    installation_updated_at = str(installation.get("updated_at") or "").strip() or None

    conditions: list[dict[str, Any]] = [
        _condition(
            "Installed",
            "True" if installed else "False",
            "Installed" if installed else "NotInstalled",
            "The Application is installed in this subnet."
            if installed
            else "The Application is not installed in this subnet.",
            observed_generation=revision,
            last_transition_time=installation_updated_at,
        ),
        _condition(
            "Available",
            "True" if available else "False",
            "Available" if available else "Unavailable",
            "A release is available to this subnet."
            if available
            else "No installable release is available to this subnet.",
        ),
        _condition(
            "Current",
            "False"
            if model.get("update_available")
            else ("True" if installed else "Unknown"),
            "NewReleaseAvailable"
            if model.get("update_available")
            else ("InstalledReleaseCurrent" if installed else "NotInstalled"),
            "A newer compatible release is available."
            if model.get("update_available")
            else (
                "The installed release matches the effective release."
                if installed
                else "Install the Application before checking release currency."
            ),
            observed_generation=revision,
        ),
    ]

    progressing_status = operation_status or installation_status
    if operation_status in {"planned", "applying", "reconciling"} or installation_status in {
        "planned",
        "installing",
        "updating",
        "removing",
    }:
        conditions.append(
            _condition(
                "Progressing",
                "True",
                f"Operation{progressing_status.title()}",
                f"The {operation.get('kind') or 'lifecycle'} operation is {progressing_status}.",
                observed_generation=int(operation.get("revision") or 0) or None,
                last_transition_time=str(
                    operation.get("updated_at") or installation_updated_at or ""
                ).strip()
                or None,
            )
        )
    else:
        conditions.append(
            _condition(
                "Progressing",
                "False",
                "NoActiveOperation",
                "No lifecycle operation is currently in progress.",
                observed_generation=int(operation.get("revision") or 0) or None,
            )
        )

    paused = str(subscription.get("status") or "").lower() in {"paused", "suspended"}
    conditions.append(
        _condition(
            "Suspended",
            "True" if paused else "False",
            "SubscriptionPaused" if paused else "NotSuspended",
            "Automatic lifecycle changes are paused."
            if paused
            else "The Application lifecycle is not suspended.",
        )
    )

    if bool(model.get("retired")):
        ready = _condition(
            "Ready",
            "False",
            "ApplicationRetired",
            "The Application is retired or archived.",
        )
    elif operation_status == "failed" or installation_status in {"failed", "degraded"}:
        ready = _condition(
            "Ready",
            "False",
            "OperationFailed" if operation_status == "failed" else "InstallationDegraded",
            str(
                operation.get("recovery_reason")
                or (
                    "The Application installation is degraded."
                    if installation_status == "degraded"
                    else "The latest lifecycle operation failed."
                )
            ),
            observed_generation=int(operation.get("revision") or 0) or revision,
            last_transition_time=str(
                operation.get("updated_at") or installation_updated_at or ""
            ).strip()
            or None,
        )
    elif managed_placement and placement_status == "unavailable":
        ready = _condition(
            "Ready",
            "False",
            "PlacementUnavailable",
            "Execution placement cannot be observed.",
            observed_generation=int(placement.get("revision") or 0) or None,
            last_transition_time=str(placement.get("updated_at") or "").strip()
            or None,
        )
    elif managed_placement and (
        bool(placement.get("partial"))
        or (
            int(placement.get("desired_component_count") or 0) > 0
            and int(placement.get("observed_active_count") or 0)
            < int(placement.get("desired_component_count") or 0)
        )
    ):
        ready = _condition(
            "Ready",
            "False",
            "PlacementIncomplete",
            "Not all desired Application components are active.",
            observed_generation=int(placement.get("revision") or 0) or None,
            last_transition_time=str(placement.get("updated_at") or "").strip()
            or None,
        )
    elif installed:
        ready = _condition(
            "Ready",
            "True",
            "RuntimeReady" if aggregate_backed else "LegacyProjectionReady",
            "The Application is installed and no blocking condition is known.",
            observed_generation=revision,
            last_transition_time=installation_updated_at,
        )
    else:
        ready = _condition(
            "Ready",
            "Unknown",
            "NotInstalled",
            "Runtime readiness is unavailable before installation.",
        )
    conditions.append(ready)

    status, reason, message = _attention_status(model, conditions)
    icon, color, priority = _ATTENTION_PRESENTATION[status]
    if bool(model.get("local_beta_active")) or bool(model.get("use_prerelease")):
        release_cycle = {
            "source": "beta",
            "label": "Beta",
            "accent": "warning",
        }
    elif installed and bool(model.get("update_available")):
        release_cycle = {
            "source": "update_available",
            "label": "Installed, update available",
            "accent": "tertiary",
        }
    elif installed:
        release_cycle = {
            "source": "installed",
            "label": "Installed",
            "accent": "success",
        }
    else:
        release_cycle = {
            "source": "marketplace",
            "label": "Marketplace",
            "accent": None,
        }
    return {
        "schema": "adaos.application.condition_projection.v1",
        "conditions": conditions,
        "attention": {
            "status": status,
            "reason": reason,
            "message": message,
            "icon": icon,
            "color": color,
            "priority": priority,
            "requires_action": status
            in {"degraded", "action_required", "update_available"},
        },
        "release_cycle": release_cycle,
    }


def enrich_application_conditions(model: Mapping[str, Any]) -> dict[str, Any]:
    enriched = deepcopy(dict(model))
    projection = application_condition_projection(enriched)
    enriched["conditions"] = projection["conditions"]
    enriched["attention"] = projection["attention"]
    enriched["release_cycle"] = projection["release_cycle"]
    return enriched


__all__ = ["application_condition_projection", "enrich_application_conditions"]
