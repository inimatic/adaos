from __future__ import annotations

import contextlib
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

import requests

from adaos.services.core_slots import active_slot, active_slot_manifest
from adaos.services.core_update import read_status
from adaos.services.runtime_paths import current_state_dir


AUTONOMOUS_SUITE_ID = "adaos-post-update-smoke"
AUTONOMOUS_SUITE_VERSION = "1.0.0"
_TERMINAL_STATES = frozenset({"passed", "failed", "inconclusive"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def autonomous_release_validation_enabled() -> bool:
    return _truthy(os.getenv("ADAOS_RELEASE_VALIDATION_AUTORUN"))


def autonomous_release_validation_delay_s() -> float:
    try:
        return max(0.0, min(120.0, float(os.getenv("ADAOS_RELEASE_VALIDATION_AUTORUN_DELAY_S") or "5")))
    except Exception:
        return 5.0


def _state_root(state_dir: Path | str | None = None) -> Path:
    return Path(state_dir or current_state_dir()) / "release_validation" / "autonomous"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return dict(value) if isinstance(value, Mapping) else None


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    try:
        temporary.replace(path)
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink()


def _installed_identity(manifest: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    observed = {
        key: manifest.get(key)
        for key in (
            "git_commit",
            "target_version",
            "resolved_target_version",
            "build_version",
            "base_version",
        )
    }
    identity = next(
        (str(value).strip() for value in observed.values() if value is not None and str(value).strip()),
        "",
    )
    return identity, observed


def _local_runtime_base_url(conf: Any) -> str:
    raw = str(os.getenv("ADAOS_SELF_BASE_URL") or getattr(conf, "local_api_url", "") or "").strip().rstrip("/")
    if not raw:
        port = int(os.getenv("ADAOS_RUNTIME_PORT") or 8777)
        return f"http://127.0.0.1:{port}"
    parsed = urlsplit(raw)
    host = parsed.hostname or "127.0.0.1"
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    netloc = host
    if parsed.port:
        netloc = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme or "http", netloc, parsed.path.rstrip("/"), "", ""))


def _supervisor_base_url() -> str:
    configured = str(os.getenv("ADAOS_SUPERVISOR_URL") or "").strip().rstrip("/")
    if configured:
        return configured
    host = str(os.getenv("ADAOS_SUPERVISOR_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.getenv("ADAOS_SUPERVISOR_PORT") or 8776)
    return f"http://{host}:{port}"


def _check(check_id: str, started: float, status: str, detail: str, evidence: Any = None) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "status": status,
        "detail": str(detail)[:500],
        "evidence": evidence,
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
    }


def _json_response_check(
    *,
    check_id: str,
    url: str,
    request_get: Callable[..., Any],
    evaluate: Callable[[Mapping[str, Any]], tuple[bool, str, Mapping[str, Any]]],
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        response = request_get(url, headers={"Accept": "application/json"}, timeout=5.0)
        if int(response.status_code) != 200:
            return _check(check_id, started, "failed", f"http_status:{response.status_code}")
        payload = response.json()
        if not isinstance(payload, Mapping):
            return _check(check_id, started, "failed", "invalid_json_payload")
        ok, detail, evidence = evaluate(payload)
        return _check(check_id, started, "passed" if ok else "failed", detail, dict(evidence))
    except Exception as exc:
        return _check(check_id, started, "error", f"request_failed:{type(exc).__name__}")


def read_autonomous_release_validation_report(*, state_dir: Path | str | None = None) -> dict[str, Any] | None:
    report = _read_json(_state_root(state_dir) / "latest.json")
    if not report or str(report.get("state") or "") not in _TERMINAL_STATES:
        return None
    return report


def run_autonomous_release_validation(
    conf: Any,
    *,
    trigger: str = "sys.ready",
    force: bool = False,
    state_dir: Path | str | None = None,
    request_get: Callable[..., Any] = requests.get,
) -> dict[str, Any] | None:
    if not autonomous_release_validation_enabled():
        return None

    started_at = _now()
    manifest = active_slot_manifest() or {}
    slot = str(active_slot() or manifest.get("slot") or "").strip().upper()
    build_identity, observed = _installed_identity(manifest)
    node_id = str(getattr(conf, "node_id", "") or "").strip()
    report_key = json.dumps(
        {
            "suite_id": AUTONOMOUS_SUITE_ID,
            "suite_version": AUTONOMOUS_SUITE_VERSION,
            "node_id": node_id,
            "active_slot": slot,
            "build_identity": build_identity,
        },
        sort_keys=True,
    )
    report_id = f"auto-{hashlib.sha256(report_key.encode('utf-8')).hexdigest()[:24]}"
    root = _state_root(state_dir)
    report_path = root / "reports" / f"{report_id}.json"
    existing = _read_json(report_path)
    if not force and existing and str(existing.get("state") or "") in _TERMINAL_STATES:
        result = dict(existing)
        result["reused"] = True
        return result

    checks: list[dict[str, Any]] = []
    check_started = time.monotonic()
    identity_ok = bool(slot and build_identity)
    checks.append(
        _check(
            "installed_build_identity",
            check_started,
            "passed" if identity_ok else "failed",
            "installed_build_observed" if identity_ok else "installed_build_identity_missing",
            {"active_slot": slot or None, **observed},
        )
    )

    update_status = read_status() or {}
    check_started = time.monotonic()
    update_state = str(update_status.get("state") or "idle").strip().lower()
    update_ok = update_state not in {"failed", "restarting", "running", "applying", "validating"}
    checks.append(
        _check(
            "core_update_terminal",
            check_started,
            "passed" if update_ok else "failed",
            "core_update_terminal" if update_ok else f"core_update_state:{update_state}",
            {
                "state": update_state,
                "phase": update_status.get("phase"),
                "target_slot": update_status.get("target_slot"),
            },
        )
    )

    runtime_url = f"{_local_runtime_base_url(conf)}/api/ping"
    checks.append(
        _json_response_check(
            check_id="runtime_ping",
            url=runtime_url,
            request_get=request_get,
            evaluate=lambda payload: (
                payload.get("ok") is True and payload.get("service") == "adaos-runtime",
                "runtime_ready" if payload.get("ok") is True and payload.get("service") == "adaos-runtime" else "runtime_not_ready",
                {"ok": payload.get("ok"), "service": payload.get("service"), "runtime": payload.get("runtime")},
            ),
        )
    )

    supervisor_url = f"{_supervisor_base_url()}/api/supervisor/public/update-status"

    def evaluate_supervisor(payload: Mapping[str, Any]) -> tuple[bool, str, Mapping[str, Any]]:
        runtime = payload.get("runtime") if isinstance(payload.get("runtime"), Mapping) else {}
        ok = (
            payload.get("ok") is True
            and runtime.get("runtime_state") == "ready"
            and runtime.get("listener_running") is True
            and runtime.get("runtime_api_ready") is True
        )
        return (
            ok,
            "supervisor_runtime_ready" if ok else "supervisor_runtime_not_ready",
            {
                "ok": payload.get("ok"),
                "active_slot": runtime.get("active_slot"),
                "runtime_state": runtime.get("runtime_state"),
                "listener_running": runtime.get("listener_running"),
                "runtime_api_ready": runtime.get("runtime_api_ready"),
            },
        )

    checks.append(
        _json_response_check(
            check_id="supervisor_status",
            url=supervisor_url,
            request_get=request_get,
            evaluate=evaluate_supervisor,
        )
    )

    if any(item["status"] == "failed" for item in checks):
        state = "failed"
        reason = next(item["detail"] for item in checks if item["status"] == "failed")
    elif any(item["status"] == "error" for item in checks):
        state = "inconclusive"
        reason = next(item["detail"] for item in checks if item["status"] == "error")
    else:
        state = "passed"
        reason = "all_post_update_checks_passed"

    report = {
        "schema_version": 1,
        "report_id": report_id,
        "suite_id": AUTONOMOUS_SUITE_ID,
        "suite_version": AUTONOMOUS_SUITE_VERSION,
        "target_policy": "latest_installed",
        "build_identity": build_identity or None,
        "active_slot": slot or None,
        "node_id": node_id,
        "subnet_id": str(getattr(conf, "subnet_id", "") or "").strip(),
        "trigger": str(trigger or "sys.ready"),
        "state": state,
        "reason": reason,
        "started_at": started_at,
        "finished_at": _now(),
        "checks": checks,
        "result": {
            "checks_total": len(checks),
            "checks_passed": sum(1 for item in checks if item["status"] == "passed"),
            "checks_failed": sum(1 for item in checks if item["status"] == "failed"),
            "checks_inconclusive": sum(1 for item in checks if item["status"] == "error"),
        },
    }
    _write_json_atomic(report_path, report)
    _write_json_atomic(root / "latest.json", report)
    return report
