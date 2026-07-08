from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import psutil  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - diagnostic dependency
    psutil = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request(
    base_url: str,
    token: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float,
) -> dict[str, Any]:
    headers = {
        "X-AdaOS-Token": token,
        "Accept": "application/json",
    }
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base_url.rstrip("/") + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8")) if raw else {}


def _find_listening_pid(port: int) -> int | None:
    if psutil is None:
        return None
    try:
        connections = psutil.net_connections(kind="tcp")
    except Exception:
        return None
    for connection in connections:
        try:
            if int(getattr(connection.laddr, "port", 0) or 0) != int(port):
                continue
            if str(getattr(connection, "status", "") or "").upper() != "LISTEN":
                continue
            if connection.pid:
                return int(connection.pid)
        except Exception:
            continue
    return None


def _memory_snapshot(pid: int | None) -> dict[str, Any]:
    if not pid:
        return {"available": False, "reason": "missing_pid"}
    if psutil is None:
        return {"available": False, "reason": "psutil_unavailable", "pid": pid}
    try:
        process = psutil.Process(pid)
        info = process.memory_info()._asdict()
        try:
            full_info = process.memory_full_info()._asdict()
        except Exception:
            full_info = {}
        rss = int(info.get("rss") or info.get("wset") or 0)
        vms = int(info.get("vms") or info.get("pagefile") or 0)
        private = int(full_info.get("private") or full_info.get("uss") or info.get("private") or 0)
        return {
            "available": True,
            "pid": pid,
            "rss_mb": round(rss / 1048576.0, 3),
            "private_mb": round(private / 1048576.0, 3),
            "vms_mb": round(vms / 1048576.0, 3),
            "threads": process.num_threads(),
        }
    except Exception as exc:
        return {"available": False, "pid": pid, "error": f"{type(exc).__name__}: {exc}"}


def _memory_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    if not before.get("available") or not after.get("available"):
        return {"available": False}
    out: dict[str, Any] = {"available": True}
    for key in ("rss_mb", "private_mb", "vms_mb"):
        out[key] = round(float(after.get(key) or 0.0) - float(before.get(key) or 0.0), 3)
    out["threads"] = int(after.get("threads") or 0) - int(before.get("threads") or 0)
    return out


def _materialization(base_url: str, token: str, webspace: str, timeout: float) -> dict[str, Any]:
    quoted = urllib.parse.quote(webspace)
    return _request(
        base_url,
        token,
        "GET",
        f"/api/node/yjs/webspaces/{quoted}/materialization?include_runtime=0",
        timeout=timeout,
    )


def _current_scenario_from_materialization(payload: dict[str, Any]) -> str | None:
    materialization = payload.get("materialization") if isinstance(payload.get("materialization"), dict) else {}
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    snapshot_ui = snapshot.get("ui") if isinstance(snapshot.get("ui"), dict) else {}
    value = (
        materialization.get("current_scenario")
        or payload.get("current_scenario")
        or snapshot_ui.get("current_scenario")
    )
    return str(value or "").strip() or None


def _wait_ready(
    base_url: str,
    token: str,
    *,
    webspace: str,
    target: str,
    since_epoch: float,
    timeout_s: float,
    poll_interval_s: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    polls = 0
    first_new_seen_ms: float | None = None
    last: dict[str, Any] = {}
    while time.perf_counter() - started < timeout_s:
        polls += 1
        poll_started = time.perf_counter()
        payload = _materialization(base_url, token, webspace, timeout=15.0)
        poll_ms = (time.perf_counter() - poll_started) * 1000.0
        rebuild = payload.get("rebuild") if isinstance(payload.get("rebuild"), dict) else {}
        materialization = payload.get("materialization") if isinstance(payload.get("materialization"), dict) else {}
        last = {
            "last_poll_ms": round(poll_ms, 3),
            "rebuild": rebuild,
            "materialization": materialization,
        }
        started_at = _float_or_zero(rebuild.get("started_at"))
        finished_at = _float_or_zero(rebuild.get("finished_at"))
        if first_new_seen_ms is None and (started_at >= since_epoch - 0.05 or finished_at >= since_epoch - 0.05):
            first_new_seen_ms = (time.perf_counter() - started) * 1000.0
        if (
            str(materialization.get("current_scenario") or "") == target
            and str(rebuild.get("status") or "") == "ready"
            and not bool(rebuild.get("pending"))
            and finished_at >= since_epoch - 0.05
        ):
            return {
                "timeout": False,
                "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "polls": polls,
                "first_new_seen_ms": round(first_new_seen_ms, 3) if first_new_seen_ms is not None else None,
                "server_rebuild_ms": round((finished_at - started_at) * 1000.0, 3)
                if started_at and finished_at
                else None,
                "server_accept_to_finish_ms": round((finished_at - since_epoch) * 1000.0, 3)
                if finished_at
                else None,
                **last,
            }
        time.sleep(max(0.01, poll_interval_s))
    return {
        "timeout": True,
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "polls": polls,
        **last,
    }


def _float_or_zero(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _base_url_port(base_url: str) -> int:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.port:
        return int(parsed.port)
    return 443 if parsed.scheme == "https" else 80


def _compact_action(action: dict[str, Any]) -> dict[str, Any]:
    ready = action.get("ready_wait") if isinstance(action.get("ready_wait"), dict) else {}
    rebuild = ready.get("rebuild") if isinstance(ready.get("rebuild"), dict) else {}
    live = rebuild.get("live_room_refresh") if isinstance(rebuild.get("live_room_refresh"), dict) else {}
    materialized = live.get("materialized_payload") if isinstance(live.get("materialized_payload"), dict) else {}
    return {
        "target": action.get("target"),
        "post_ms": action.get("post_duration_ms"),
        "ready_wait_ms": ready.get("elapsed_ms"),
        "server_rebuild_ms": ready.get("server_rebuild_ms"),
        "server_accept_to_finish_ms": ready.get("server_accept_to_finish_ms"),
        "post_timings": (action.get("post") or {}).get("timings_ms") if isinstance(action.get("post"), dict) else None,
        "rebuild_timings": rebuild.get("timings_ms"),
        "materialized_phase_timings": materialized.get("phase_timings_ms"),
        "materialized_semantic_timings": materialized.get("semantic_timings_ms"),
        "memory_delta": (action.get("memory") or {}).get("operation_delta")
        if isinstance(action.get("memory"), dict)
        else None,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    base_url = args.base_url.rstrip("/")
    port = int(args.memory_port or 0) or _base_url_port(base_url)
    pid = int(args.memory_pid or 0) or _find_listening_pid(port)
    initial_memory = _memory_snapshot(pid)
    request_source = str(args.request_source or "").strip() or "tools.scenario_switch_benchmark"
    initial_materialization: dict[str, Any] = {}
    initial_materialization_error: str | None = None
    try:
        initial_materialization = _materialization(base_url, args.token, args.webspace, timeout=args.timeout)
    except Exception as exc:
        initial_materialization_error = f"{type(exc).__name__}: {exc}"
    initial_current = _current_scenario_from_materialization(initial_materialization)
    report: dict[str, Any] = {
        "label": args.label,
        "webspace": args.webspace,
        "pid": pid,
        "sequence": list(args.sequence),
        "started_at": _now_iso(),
        "request_source": request_source,
        "initial_current_scenario": initial_current,
        "initial_materialization_error": initial_materialization_error,
        "restore_current_enabled": bool(args.restore_current),
        "initial_memory": initial_memory,
        "actions": [],
    }
    quoted = urllib.parse.quote(args.webspace)
    for index, target in enumerate(args.sequence, start=1):
        before = _memory_snapshot(pid)
        started_epoch = time.time()
        started = time.perf_counter()
        post = _request(
            base_url,
            args.token,
            "POST",
            f"/api/node/yjs/webspaces/{quoted}/scenario",
            {
                "scenario_id": target,
                "include_rebuild": True,
                "include_runtime": False,
                "wait_for_rebuild": False,
                "request_source": request_source,
                "request_id": f"{request_source}:{args.label}:{index}:{int(started_epoch * 1000)}",
            },
            timeout=args.timeout,
        )
        post_ms = (time.perf_counter() - started) * 1000.0
        after_post = _memory_snapshot(pid)
        ready = _wait_ready(
            base_url,
            args.token,
            webspace=args.webspace,
            target=target,
            since_epoch=started_epoch,
            timeout_s=args.poll_timeout,
            poll_interval_s=args.poll_interval,
        )
        after_ready = _memory_snapshot(pid)
        report["actions"].append(
            {
                "target": target,
                "started_at": started_epoch,
                "post_duration_ms": round(post_ms, 3),
                "post": post,
                "ready_wait": ready,
                "memory": {
                    "before": before,
                    "after_post": after_post,
                    "after_ready": after_ready,
                    "post_delta": _memory_delta(before, after_post),
                    "operation_delta": _memory_delta(before, after_ready),
                },
            }
        )
        if args.cooldown > 0:
            time.sleep(args.cooldown)
    last_target = str(args.sequence[-1] or "").strip() if args.sequence else ""
    if bool(args.restore_current) and initial_current and initial_current != last_target:
        before = _memory_snapshot(pid)
        started_epoch = time.time()
        started = time.perf_counter()
        post = _request(
            base_url,
            args.token,
            "POST",
            f"/api/node/yjs/webspaces/{quoted}/scenario",
            {
                "scenario_id": initial_current,
                "include_rebuild": True,
                "include_runtime": False,
                "wait_for_rebuild": False,
                "request_source": request_source,
                "request_id": f"{request_source}:{args.label}:restore:{int(started_epoch * 1000)}",
            },
            timeout=args.timeout,
        )
        post_ms = (time.perf_counter() - started) * 1000.0
        after_post = _memory_snapshot(pid)
        ready = _wait_ready(
            base_url,
            args.token,
            webspace=args.webspace,
            target=initial_current,
            since_epoch=started_epoch,
            timeout_s=args.poll_timeout,
            poll_interval_s=args.poll_interval,
        )
        after_ready = _memory_snapshot(pid)
        report["restore_current"] = {
            "target": initial_current,
            "started_at": started_epoch,
            "post_duration_ms": round(post_ms, 3),
            "post": post,
            "ready_wait": ready,
            "memory": {
                "before": before,
                "after_post": after_post,
                "after_ready": after_ready,
                "post_delta": _memory_delta(before, after_post),
                "operation_delta": _memory_delta(before, after_ready),
            },
        }
    final_memory = _memory_snapshot(pid)
    report["finished_at"] = _now_iso()
    report["final_memory"] = final_memory
    report["total_memory_delta"] = _memory_delta(initial_memory, final_memory)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark webspace scenario switching through local API.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8777")
    parser.add_argument("--token", default="dev-local-token")
    parser.add_argument("--webspace", default="desktop-dev")
    parser.add_argument("--sequence", nargs="+", default=["web_desktop", "prompt_engineer_scenario"])
    parser.add_argument("--label", default="scenario-switch")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--poll-timeout", type=float, default=45.0)
    parser.add_argument("--poll-interval", type=float, default=0.05)
    parser.add_argument("--cooldown", type=float, default=0.25)
    parser.add_argument("--memory-pid", type=int, default=0)
    parser.add_argument("--memory-port", type=int, default=0)
    parser.add_argument("--request-source", default="tools.scenario_switch_benchmark")
    parser.add_argument("--restore-current", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    report = run(args)
    out = args.out
    if not out:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = f".adaos/diagnostics/scenario_switch_{args.label}_{stamp}.json"
    out_path = Path(out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"report: {out_path}")
    print("total_memory_delta:", json.dumps(report.get("total_memory_delta"), ensure_ascii=False))
    for action in report.get("actions", []):
        print(json.dumps(_compact_action(action), ensure_ascii=False))
    if isinstance(report.get("restore_current"), dict):
        print("restore_current:", json.dumps(_compact_action(report["restore_current"]), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
