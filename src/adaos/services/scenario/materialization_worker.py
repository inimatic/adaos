from __future__ import annotations

"""One-shot process boundary for YDoc-heavy webspace materialization."""

import asyncio
import base64
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Mapping


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=True))


def _process_rss_bytes() -> int | None:
    try:
        import psutil

        return int(psutil.Process(os.getpid()).memory_info().rss)
    except Exception:
        return None


async def _materialize(request: Mapping[str, Any]) -> dict[str, Any]:
    from adaos.services.scenario.webspace_runtime import WebspaceScenarioRuntime

    mode = str(request.get("mode") or "").strip()
    webspace_id = str(request.get("webspace_id") or "").strip()
    request_id = str(request.get("request_id") or "").strip() or None
    scenario_id = str(request.get("scenario_id") or "").strip() or None
    identity = request.get("materialization_identity")
    materialization_identity = dict(identity) if isinstance(identity, Mapping) else None
    raw_skill_decls = request.get("skill_decls_snapshot")
    skill_decls_snapshot = (
        [dict(item) for item in raw_skill_decls if isinstance(item, Mapping)]
        if isinstance(raw_skill_decls, list)
        else None
    )
    skill_decls_fingerprint = str(request.get("skill_decls_fingerprint") or "").strip() or None
    runtime = WebspaceScenarioRuntime()
    if mode == "payload_only":
        entry = await runtime.materialize_webspace_payload_async(
            webspace_id,
            request_id=request_id,
            scenario_id=scenario_id,
            materialization_identity=materialization_identity,
            isolate_process=False,
            skill_decls_snapshot=skill_decls_snapshot,
            skill_decls_fingerprint=skill_decls_fingerprint,
        )
        return {
            "materialized_payload": _json_clone(runtime._last_materialized_payload or {}),
            "rebuild_timings_ms": runtime._last_rebuild_timings_ms,
            "resolver_debug": runtime._last_resolver_debug,
            "apply_summary": runtime._last_apply_summary,
            "apply_phase_timings_ms": runtime._last_apply_phase_timings_ms,
            "ydoc_timings_ms": runtime._last_rebuild_ydoc_timings_ms,
            "registry_summary": {
                "scenario_id": str(getattr(entry, "scenario_id", scenario_id) or ""),
                "apps": len(getattr(entry, "apps", []) or []),
                "widgets": len(getattr(entry, "widgets", []) or []),
            },
        }
    if mode == "fresh_doc":
        result = runtime._rebuild_fresh_doc_snapshot_sync(
            webspace_id,
            request_id=request_id,
            initial_scenario_id=scenario_id,
            materialization_identity=materialization_identity,
            skill_decls_snapshot=skill_decls_snapshot,
            skill_decls_fingerprint=skill_decls_fingerprint,
        )
        result.pop("entry", None)
        result["snapshot_update_b64"] = base64.b64encode(
            bytes(result.pop("snapshot_update", b"") or b"")
        ).decode("ascii")
        result["state_vector_b64"] = base64.b64encode(
            bytes(result.pop("state_vector", b"") or b"")
        ).decode("ascii")
        return result
    raise ValueError(f"unsupported materialization mode: {mode or '-'}")


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    if len(args) != 2:
        raise SystemExit("usage: materialization_worker <request.json> <result.json>")
    request_path = Path(args[0])
    result_path = Path(args[1])
    os.environ["ADAOS_MATERIALIZATION_WORKER"] = "0"
    started = time.perf_counter()
    result: dict[str, Any]
    exit_code = 0
    init_elapsed_ms: float | None = None
    materialize_elapsed_ms: float | None = None
    try:
        from adaos.apps.bootstrap import init_ctx

        phase_started = time.perf_counter()
        init_ctx()
        init_elapsed_ms = round((time.perf_counter() - phase_started) * 1000.0, 3)
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise ValueError("materialization request must be an object")
        phase_started = time.perf_counter()
        payload = asyncio.run(_materialize(request))
        materialize_elapsed_ms = round((time.perf_counter() - phase_started) * 1000.0, 3)
        result = {
            "ok": True,
            "schema": "adaos.webspace.materialization_worker_result.v1",
            **payload,
        }
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
            raise
        exit_code = 1
        result = {
            "ok": False,
            "schema": "adaos.webspace.materialization_worker_result.v1",
            "error": "materialization_worker_failed",
            "detail": f"{type(exc).__name__}: {exc}",
        }
    result["worker_elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
    result["worker_init_ms"] = init_elapsed_ms
    result["worker_materialize_ms"] = materialize_elapsed_ms
    result["worker_rss_bytes"] = _process_rss_bytes()
    temp_path = result_path.with_suffix(result_path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(result, ensure_ascii=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temp_path.replace(result_path)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
