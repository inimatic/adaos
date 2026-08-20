from __future__ import annotations

import asyncio
import threading
from typing import Any


class RuntimeRefreshError(RuntimeError):
    """Runtime refresh failed after producing a diagnostic operation payload."""

    def __init__(self, message: str, payload: dict[str, Any]) -> None:
        super().__init__(message)
        self.payload = dict(payload)


def _default_webspace_id() -> str:
    from adaos.services.yjs.webspace import default_webspace_id

    return default_webspace_id()


async def rebuild_webspace_projection(
    *,
    webspace_id: str | None = None,
    action: str,
    source_of_truth: str,
) -> dict[str, Any]:
    from adaos.services.scenario.webspace_runtime import rebuild_webspace_from_sources

    target_webspace = str(webspace_id or "").strip() or _default_webspace_id()
    await rebuild_webspace_from_sources(
        target_webspace,
        action=str(action or "").strip() or "runtime_refresh",
        source_of_truth=str(source_of_truth or "").strip() or "skill_runtime",
    )
    return {
        "ok": True,
        "accepted": True,
        "webspace_id": target_webspace,
        "action": str(action or "").strip() or "runtime_refresh",
        "source_of_truth": str(source_of_truth or "").strip() or "skill_runtime",
    }


def rebuild_webspace_projection_sync(
    *,
    webspace_id: str | None = None,
    action: str,
    source_of_truth: str,
) -> dict[str, Any]:
    async def _runner() -> dict[str, Any]:
        return await rebuild_webspace_projection(
            webspace_id=webspace_id,
            action=action,
            source_of_truth=source_of_truth,
        )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_runner())

    result: dict[str, Any] | None = None
    error: BaseException | None = None

    def _thread_main() -> None:
        nonlocal result
        nonlocal error
        try:
            result = asyncio.run(_runner())
        except BaseException as exc:
            error = exc

    thread = threading.Thread(
        target=_thread_main,
        name="adaos-webspace-rebuild-sync",
        daemon=True,
    )
    thread.start()
    thread.join()
    if error is not None:
        raise error
    return result if isinstance(result, dict) else {}


def _record_stage(payload: dict[str, Any], stage: str, *, ok: bool, **fields: Any) -> None:
    entry: dict[str, Any] = {"stage": stage, "ok": bool(ok)}
    for key, value in fields.items():
        if value is None:
            continue
        if value == "" or value == {} or value == []:
            continue
        entry[key] = value
    payload.setdefault("lifecycle_stages", []).append(entry)


def _is_runtime_migration_transient(deactivation: dict[str, Any]) -> bool:
    reason = str(deactivation.get("reason") or "").strip()
    return bool(deactivation.get("deactivated")) and bool(deactivation.get("transient")) and reason == "runtime_migration_in_progress"


def _restore_rejected_candidate_fallback(
    mgr: Any,
    skill_name: str,
    *,
    version: str,
    slot: str,
    previous_deactivation: dict[str, Any],
    webspace_id: str,
) -> dict[str, Any]:
    """Restore the exact pre-migration runtime after candidate rejection."""

    if not version or slot not in {"A", "B"}:
        return {
            "ok": False,
            "skipped": True,
            "reason": "missing_previous_runtime_selection",
            "version": version,
            "slot": slot,
        }
    restore = getattr(mgr, "restore_runtime_selection_exact", None)
    if not callable(restore):
        return {
            "ok": False,
            "skipped": True,
            "reason": "runtime_manager_restore_unsupported",
            "version": version,
            "slot": slot,
        }
    try:
        result = restore(
            skill_name,
            version=version,
            slot=slot,
            previous_deactivation=previous_deactivation or None,
            webspace_id=webspace_id,
            emit_activation=not bool(previous_deactivation.get("deactivated")),
        )
    except Exception as exc:
        return {
            "ok": False,
            "skipped": False,
            "reason": "fallback_restore_failed",
            "version": version,
            "slot": slot,
            "error": str(exc),
        }
    payload = dict(result) if isinstance(result, dict) else {"result": result}
    payload.setdefault("ok", True)
    payload.setdefault("version", version)
    payload.setdefault("slot", slot)
    return payload


def refresh_skill_runtime(
    mgr: Any,
    skill_name: str,
    *,
    webspace_id: str | None = None,
    source_version: str | None = None,
    migrate_runtime: bool = True,
    ensure_installed: bool = False,
    require_active_version: bool = False,
    disable_during_migration: bool = False,
    operation_id: str | None = None,
    retry_deactivated: bool = False,
    defer_webspace_rebuild: bool = False,
    run_candidate_tests: bool = False,
    emit_activation: bool = True,
) -> dict[str, Any]:
    target_webspace = str(webspace_id or "").strip() or _default_webspace_id()
    expected_version = str(source_version or "").strip()
    payload: dict[str, Any] = {
        "skill": str(skill_name or "").strip(),
        "webspace_id": target_webspace,
        "source_version": expected_version,
        "ok": False,
        "runtime_updated": False,
        "runtime_migrated": False,
        "active_converged": False,
        "prepared_version": "",
        "prepared_slot": "",
        "activated_slot": "",
        "failed_stage": "",
        "failure_reason": "",
        "candidate_tests_required": bool(run_candidate_tests),
        "tests": {},
        "lifecycle_stages": [],
    }
    runtime_status_before: dict[str, Any] = {}
    try:
        runtime_status_before = mgr.runtime_status(skill_name)
    except Exception:
        runtime_status_before = {}
    runtime_version_before = str(runtime_status_before.get("version") or "").strip()
    payload["active_version_before"] = runtime_version_before
    payload["active_slot_before"] = str(runtime_status_before.get("active_slot") or "").strip()
    deactivation_before = (
        runtime_status_before.get("deactivation")
        if isinstance(runtime_status_before.get("deactivation"), dict)
        else {}
    )
    recover_transient_deactivation = _is_runtime_migration_transient(deactivation_before)
    retry_deactivated_prepare = (
        bool(runtime_status_before.get("deactivated"))
        and not recover_transient_deactivation
        and bool(retry_deactivated)
    )
    if bool(runtime_status_before.get("deactivated")) and not recover_transient_deactivation and not retry_deactivated_prepare:
        payload["ok"] = True
        payload["skipped"] = True
        payload["deactivated"] = True
        payload["deactivation"] = deactivation_before
        payload["active_version_after"] = runtime_version_before
        payload["active_slot_after"] = str(runtime_status_before.get("active_slot") or "").strip()
        payload["active_converged"] = True
        _record_stage(
            payload,
            "runtime_update",
            ok=True,
            skipped=True,
            reason=str((deactivation_before or {}).get("reason") or "deactivated"),
        )
        _record_stage(payload, "prepare", ok=True, skipped=True, reason="deactivated")
        _record_stage(payload, "activate", ok=True, skipped=True, reason="deactivated")
        _record_stage(payload, "converge", ok=True, skipped=True, active_version=runtime_version_before)
        return payload
    if recover_transient_deactivation or retry_deactivated_prepare:
        payload["deactivation_recovery"] = True
        payload["deactivation"] = deactivation_before
    if retry_deactivated_prepare:
        payload["deactivation_retry"] = True
    # Workspace source can change without a semantic-version bump during local
    # development. Treat every requested production refresh as an A/B
    # candidate so a loaded module is never changed underneath the process.
    isolated_candidate = bool(migrate_runtime and expected_version)
    payload["isolated_candidate"] = isolated_candidate
    if isolated_candidate:
        # runtime_update copies Workspace sources into the active slot. Every
        # production candidate must remain physically isolated until its
        # prepare/tests/activation sequence completes, including same-version
        # source revisions.
        isolation_reason = (
            "versioned_candidate_isolated"
            if expected_version != runtime_version_before
            else "slot_candidate_isolated"
        )
        _record_stage(payload, "runtime_update", ok=True, skipped=True, reason=isolation_reason)
    else:
        payload["runtime_refresh_skipped"] = True
        payload["runtime_refresh_skip_reason"] = (
            "source_version_missing" if migrate_runtime else "runtime_migration_disabled"
        )
        _record_stage(
            payload,
            "runtime_update",
            ok=True,
            skipped=True,
            reason=payload["runtime_refresh_skip_reason"],
        )
    should_prepare = bool(isolated_candidate)
    if recover_transient_deactivation:
        should_prepare = True
    if retry_deactivated_prepare:
        should_prepare = True
    if migrate_runtime and should_prepare:
        allow_deactivated_prepare = bool(recover_transient_deactivation or retry_deactivated_prepare)
        if disable_during_migration:
            try:
                payload["deactivation"] = mgr.deactivate_runtime(
                    skill_name,
                    reason="runtime_migration_in_progress",
                    failure_kind="migration",
                    failed_stage="prepare",
                    source="runtime_refresh",
                    committed_core_switch=False,
                    status="disabled",
                    comment="Skill runtime is disabled while AdaOS prepares and activates its updated runtime slot.",
                    operation_id=str(operation_id or ""),
                    transient=True,
                )
                allow_deactivated_prepare = True
            except Exception as exc:
                payload["deactivation_error"] = str(exc)
        if ensure_installed:
            mgr.install(skill_name, validate=False)
        try:
            prepare_kwargs: dict[str, Any] = {"run_tests": bool(run_candidate_tests)}
            if allow_deactivated_prepare:
                prepare_kwargs["allow_deactivated"] = True
            runtime = mgr.prepare_runtime(skill_name, **prepare_kwargs)
        except Exception as exc:
            message = f"runtime prepare failed after skill update: {exc}"
            failed_stage = "tests" if str(exc).strip().lower().startswith("skill tests failed") else "prepare"
            payload["failed_stage"] = failed_stage
            payload["failure_reason"] = str(exc)
            payload["error"] = message
            _record_stage(payload, failed_stage, ok=False, error=str(exc))
            payload["fallback_restore"] = _restore_rejected_candidate_fallback(
                mgr,
                skill_name,
                version=runtime_version_before,
                slot=str(payload.get("active_slot_before") or "").strip().upper(),
                previous_deactivation=dict(deactivation_before),
                webspace_id=target_webspace,
            )
            _record_stage(
                payload,
                "fallback_restore",
                ok=bool(payload["fallback_restore"].get("ok")),
                **{key: value for key, value in payload["fallback_restore"].items() if key != "ok"},
            )
            try:
                runtime_status_after = mgr.runtime_status(skill_name)
            except Exception:
                runtime_status_after = {}
            payload["active_version_after"] = str(runtime_status_after.get("version") or "").strip()
            payload["active_slot_after"] = str(runtime_status_after.get("active_slot") or "").strip()
            raise RuntimeRefreshError(message, payload) from exc
        version = getattr(runtime, "version", None)
        slot = getattr(runtime, "slot", None)
        payload["prepared_version"] = str(version or "").strip()
        payload["prepared_slot"] = str(slot or "").strip()
        payload["data_migration"] = dict(getattr(runtime, "data_migration", None) or {})
        payload["tests"] = {
            str(test_name): {
                "status": str(getattr(result, "status", result) or ""),
                "detail": str(getattr(result, "detail", "") or ""),
            }
            for test_name, result in dict(getattr(runtime, "tests", None) or {}).items()
        }
        _record_stage(payload, "prepare", ok=True, version=version, slot=slot)
        try:
            activation_kwargs: dict[str, Any] = {
                "version": version,
                "slot": slot,
                "space": "default",
                "webspace_id": target_webspace,
            }
            if defer_webspace_rebuild:
                activation_kwargs["defer_webspace_rebuild"] = True
            if not emit_activation:
                activation_kwargs["emit_activation"] = False
            active_slot = mgr.activate_for_space(skill_name, **activation_kwargs)
        except Exception as exc:
            message = f"runtime activation failed after skill update: {exc}"
            payload["failed_stage"] = "activate"
            payload["failure_reason"] = str(exc)
            payload["error"] = message
            _record_stage(payload, "activate", ok=False, version=version, slot=slot, error=str(exc))
            payload["fallback_restore"] = _restore_rejected_candidate_fallback(
                mgr,
                skill_name,
                version=runtime_version_before,
                slot=str(payload.get("active_slot_before") or "").strip().upper(),
                previous_deactivation=dict(deactivation_before),
                webspace_id=target_webspace,
            )
            _record_stage(
                payload,
                "fallback_restore",
                ok=bool(payload["fallback_restore"].get("ok")),
                **{key: value for key, value in payload["fallback_restore"].items() if key != "ok"},
            )
            try:
                runtime_status_after = mgr.runtime_status(skill_name)
            except Exception:
                runtime_status_after = {}
            payload["active_version_after"] = str(runtime_status_after.get("version") or "").strip()
            payload["active_slot_after"] = str(runtime_status_after.get("active_slot") or "").strip()
            raise RuntimeRefreshError(message, payload) from exc
        payload["runtime_migrated"] = True
        payload["migrated_version"] = version
        payload["migrated_slot"] = active_slot
        payload["activated_slot"] = str(active_slot or "").strip()
        _record_stage(payload, "activate", ok=True, version=version, slot=active_slot)
    else:
        _record_stage(payload, "prepare", ok=True, skipped=True)
        _record_stage(payload, "activate", ok=True, skipped=True)
    runtime_status_after: dict[str, Any] = {}
    try:
        runtime_status_after = mgr.runtime_status(skill_name)
    except Exception:
        runtime_status_after = {}
    runtime_version_after = str(runtime_status_after.get("version") or "").strip()
    payload["active_version_after"] = runtime_version_after
    payload["active_slot_after"] = str(runtime_status_after.get("active_slot") or "").strip()
    if expected_version:
        payload["active_converged"] = runtime_version_after == expected_version
    else:
        payload["active_converged"] = bool(runtime_version_after)
    _record_stage(
        payload,
        "converge",
        ok=bool(payload["active_converged"]),
        expected_version=expected_version,
        active_version=runtime_version_after,
    )
    if require_active_version and expected_version and runtime_version_after != expected_version:
        message = (
            "runtime active version did not converge after skill update: "
            f"skill={skill_name} expected={expected_version} active={runtime_version_after or 'none'}"
        )
        payload["failed_stage"] = "converge"
        payload["failure_reason"] = message
        payload["error"] = message
        raise RuntimeRefreshError(message, payload)
    payload["ok"] = True
    return payload
