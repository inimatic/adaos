from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from dotenv import dotenv_values

from adaos.services.node_config import load_config
from adaos.services.realtime_sidecar import (
    realtime_sidecar_enabled,
    resolve_realtime_remote_candidates,
)


SERVICE_SCHEMA = "adaosd.service_install.v1"
SUPERVISOR_SCHEMA = "adaosd.supervisor_config.v1"
WORKER_SCHEMA = "adaosd.worker_profile.v1"
YJS_SHADOW_ENVIRONMENT = (
    "ADAOS_YJS_SIDECAR_SHADOW_ENABLE",
    "ADAOS_YJS_SIDECAR_SHADOW_WEBSPACES",
    "ADAOS_YJS_SIDECAR_SHADOW_MAX_UPDATE_BYTES",
    "ADAOS_YJS_SIDECAR_SHADOW_MAX_ROOMS",
    "ADAOS_YJS_SIDECAR_SHADOW_MAX_SNAPSHOT_BYTES",
    "ADAOS_YJS_SIDECAR_SHADOW_PERSIST_ENABLE",
    "ADAOS_YJS_SIDECAR_SHADOW_STORAGE_DIR",
    "ADAOS_YJS_SIDECAR_SHADOW_CHECKPOINT_EVERY",
    "ADAOS_YJS_SIDECAR_SHADOW_QUEUE_LIMIT",
    "ADAOS_YJS_SIDECAR_SHADOW_TIMEOUT_S",
    "ADAOS_YJS_SIDECAR_SHADOW_CONTROL_URL",
    "ADAOS_YJS_SIDECAR_AUTHORITY_WEBSPACES",
    "ADAOS_YJS_SIDECAR_AUTHORITY_MAX_MESSAGE_BYTES",
    "ADAOS_YJS_SIDECAR_AUTHORITY_CONTROL_URL",
    "ADAOS_YJS_SIDECAR_AUTHORITY_TIMEOUT_S",
)


def _absolute_file(value: str | Path, *, name: str, executable: bool = False) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{name} must be absolute")
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"{name} must be a regular file: {path}")
    if executable and not os.access(path, os.X_OK):
        raise ValueError(f"{name} must be executable: {path}")
    return path


def _absolute_dir(value: str | Path, *, name: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{name} must be absolute")
    path = path.resolve()
    if not path.is_dir():
        raise ValueError(f"{name} must be a directory: {path}")
    return path


def _output_path(value: str | Path, *, name: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{name} must be absolute")
    return path.resolve()


def _atomic_write(path: Path, content: str, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _atomic_write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n", mode=0o644)


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for name, value in dotenv_values(path).items():
        if name and value is not None:
            os.environ.setdefault(str(name), str(value))


def _selected_environment(names: tuple[str, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip():
            result[name] = str(value).strip()
    return result


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _runtime_token(base_dir: Path, config: Any) -> str:
    runtime_path = base_dir / "state" / "node_runtime.json"
    if runtime_path.is_file():
        try:
            payload = json.loads(runtime_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                token = str(payload.get("token") or "").strip()
                if token:
                    return token
        except (OSError, ValueError, TypeError):
            pass
    return str(getattr(config, "token", None) or "dev-local-token").strip() or "dev-local-token"


def _private_environment(token: str) -> str:
    if "\n" in token or "\r" in token or "\x00" in token:
        raise ValueError("AdaOS token cannot contain control characters")
    escaped = token.replace("\\", "\\\\").replace('"', '\\"')
    return f'ADAOS_TOKEN="{escaped}"\n'


def _preserved_approval(path: Path, *, scope: str) -> dict[str, str] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        approval = payload.get("approval") if isinstance(payload, dict) else None
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(approval, dict) or str(approval.get("scope") or "") != scope:
        return None
    required = ("id", "approved_at", "stand", "scope")
    if not all(str(approval.get(name) or "").strip() for name in required):
        return None
    return {name: str(approval[name]) for name in required}


def _restart_policy(*, limit: int) -> dict[str, Any]:
    return {
        "mode": "always",
        "limit": limit,
        "window_s": 120,
        "base_backoff_s": 1,
        "max_backoff_s": 30,
        "circuit_open_s": 90,
    }


def _shutdown_policy(*, drain: int, term: int, kill: int) -> dict[str, Any]:
    return {
        "drain_timeout_s": drain,
        "term_timeout_s": term,
        "kill_timeout_s": kill,
    }


def _lifecycle_environment(config: Any) -> dict[str, str]:
    if str(getattr(config, "role", "") or "").strip().lower() != "hub":
        return {}
    root_settings = getattr(config, "root_settings", None)
    base_url = str(getattr(root_settings, "base_url", "") or "").strip().rstrip("/")
    result: dict[str, str] = {}
    if base_url:
        result["ADAOS_SIDECAR_LIFECYCLE_URL"] = base_url + "/v1/hub/lifecycle/report"
    for name, value in (
        ("ADAOS_SIDECAR_LIFECYCLE_CA", config.ca_cert_path()),
        ("ADAOS_SIDECAR_LIFECYCLE_CERT", config.hub_cert_path()),
        ("ADAOS_SIDECAR_LIFECYCLE_KEY", config.hub_key_path()),
    ):
        path = Path(value).expanduser().resolve()
        if path.is_file():
            result[name] = str(path)
    return result


def generate_configuration(
    *,
    base_dir: str | Path,
    repo_root: str | Path,
    python_executable: str | Path,
    binary: str | Path,
    service_config_path: str | Path,
    dotenv_path: str | Path,
    serve_host: str,
    serve_port: int,
    control_port: int,
    sidecar_implementation: str,
    approval_scope: str,
    update_implementation: str = "python",
    approval_id: str | None = None,
    approval_stand: str | None = None,
) -> dict[str, Any]:
    if sidecar_implementation not in {"python", "go"}:
        raise ValueError("sidecar implementation must be python or go")
    if update_implementation not in {"python", "go"}:
        raise ValueError("core update implementation must be python or go")
    if approval_scope not in {"m4c_canary", "production"}:
        raise ValueError("approval scope must be m4c_canary or production")
    if not 1 <= int(serve_port) <= 65535 or not 1 <= int(control_port) <= 65535:
        raise ValueError("runtime ports must be between 1 and 65535")

    base = _output_path(base_dir, name="base directory")
    base.mkdir(parents=True, exist_ok=True)
    repo = _absolute_dir(repo_root, name="repository root")
    python = _absolute_file(python_executable, name="Python executable", executable=True)
    adaosd = _absolute_file(binary, name="adaosd binary", executable=True)
    service_path = _output_path(service_config_path, name="service config")
    dotenv = _output_path(dotenv_path, name="dotenv path")
    _load_dotenv(dotenv)
    os.environ["ADAOS_BASE_DIR"] = str(base)
    os.environ["ADAOS_REALTIME_IMPLEMENTATION"] = sidecar_implementation
    os.environ["ADAOS_REALTIME_GO_BINARY"] = str(adaosd)

    node_config = load_config()
    role = str(getattr(node_config, "role", "") or "").strip().lower()
    node_id = str(getattr(node_config, "node_id", "") or "").strip() or "unknown"
    token = _runtime_token(base, node_config)
    sidecar_managed = bool(realtime_sidecar_enabled(role=role))
    yjs_shadow_environment = _selected_environment(YJS_SHADOW_ENVIRONMENT)
    yjs_shadow_enabled = _enabled(yjs_shadow_environment.get("ADAOS_YJS_SIDECAR_SHADOW_ENABLE"))
    yjs_authority_webspaces = str(
        yjs_shadow_environment.get("ADAOS_YJS_SIDECAR_AUTHORITY_WEBSPACES") or ""
    ).strip()
    if yjs_shadow_enabled and sidecar_implementation != "go":
        raise ValueError("Yjs sidecar shadow requires the Go sidecar implementation")
    if yjs_shadow_enabled and not sidecar_managed:
        raise ValueError("Yjs sidecar shadow requires a managed realtime sidecar")
    if yjs_shadow_enabled and not yjs_shadow_environment.get("ADAOS_YJS_SIDECAR_SHADOW_WEBSPACES"):
        raise ValueError("Yjs sidecar shadow requires an explicit webspace allowlist")
    if yjs_authority_webspaces and not yjs_shadow_enabled:
        raise ValueError("Yjs sidecar authority requires the durable Yjs shadow")
    if yjs_authority_webspaces and sidecar_implementation != "go":
        raise ValueError("Yjs sidecar authority requires the Go sidecar implementation")
    if yjs_authority_webspaces:
        shadow_webspaces = {
            value.strip()
            for value in str(
                yjs_shadow_environment.get("ADAOS_YJS_SIDECAR_SHADOW_WEBSPACES") or ""
            ).replace(";", ",").split(",")
            if value.strip()
        }
        authority_webspaces = {
            value.strip()
            for value in yjs_authority_webspaces.replace(";", ",").split(",")
            if value.strip()
        }
        if "*" in authority_webspaces:
            raise ValueError("Yjs sidecar authority requires explicit webspace ids")
        if "*" not in shadow_webspaces and not authority_webspaces.issubset(shadow_webspaces):
            raise ValueError("Yjs sidecar authority webspaces must be admitted by the shadow")
    if yjs_shadow_enabled:
        yjs_shadow_environment.setdefault("ADAOS_YJS_SIDECAR_SHADOW_PERSIST_ENABLE", "1")
        yjs_shadow_environment.setdefault(
            "ADAOS_YJS_SIDECAR_SHADOW_STORAGE_DIR",
            str(base / "state" / "yjs-shadow"),
        )
        yjs_shadow_environment.setdefault("ADAOS_YJS_SIDECAR_SHADOW_CHECKPOINT_EVERY", "64")

    config_dir = service_path.parent
    workers_dir = config_dir / "workers"
    supervisor_path = config_dir / "supervisor.json"
    runtime_profile_path = workers_dir / "runtime.json"
    sidecar_profile_path = workers_dir / "sidecar.json"
    private_env_path = base / "private" / "adaosd-supervisor.env"

    runtime_environment = {
        "ADAOS_AUTOSTART_MANAGED": "1",
        "ADAOS_BASE_DIR": str(base),
        "ADAOS_ROOT_REPO_ROOT": "{slot_repo}",
        "ADAOS_SHARED_DOTENV_PATH": str(dotenv),
        "PYTHONPATH": "{slot_repo}/src",
    }
    runtime_environment.update(yjs_shadow_environment)
    runtime_profile = {
        "schema": WORKER_SCHEMA,
        "id": "adaos-runtime",
        "role": "runtime",
        "command": [
            "{slot_python}",
            "-m",
            "adaos.apps.autostart_runner",
            "--host",
            str(serve_host),
            "--port",
            str(int(serve_port)),
        ],
        "working_directory": str(base / "state" / "core_slots" / "slots" / "{active_slot}" / "repo"),
        "environment": {
            "clear": True,
            "inherit": ["HOME", "LANG", "LC_ALL", "PATH", "TZ"],
            "set": runtime_environment,
            "unset": ["PYTHONINSPECT"],
        },
        "restart": _restart_policy(limit=6),
        "shutdown": _shutdown_policy(drain=20, term=20, kill=30),
    }
    _write_json(runtime_profile_path, runtime_profile)

    workers: list[dict[str, Any]] = []
    remote_candidates: list[str] = []
    if sidecar_managed:
        remote_candidates = resolve_realtime_remote_candidates()
        if not remote_candidates:
            raise RuntimeError("realtime sidecar has no remote WebSocket candidate")
        sidecar_host = str(os.getenv("ADAOS_REALTIME_HOST") or "127.0.0.1").strip() or "127.0.0.1"
        sidecar_port = int(str(os.getenv("ADAOS_REALTIME_PORT") or "7422"))
        sidecar_control_port = int(str(os.getenv("ADAOS_REALTIME_CONTROL_PORT") or str(sidecar_port + 4)))
        route_ws_port = int(str(os.getenv("ADAOS_REALTIME_ROUTE_WS_PORT") or str(sidecar_port + 1)))
        route_yws_port = int(str(os.getenv("ADAOS_REALTIME_ROUTE_YWS_PORT") or str(sidecar_port + 2)))
        command = (
            [str(adaosd), "realtime-sidecar", "--host", sidecar_host, "--port", str(sidecar_port)]
            if sidecar_implementation == "go"
            else [str(python), "-m", "adaos.services.realtime_sidecar", "--host", sidecar_host, "--port", str(sidecar_port)]
        )
        sidecar_environment = {
            "ADAOS_BASE_DIR": str(base),
            "ADAOS_REALTIME_ALLOW_API_FALLBACK": "0",
            "ADAOS_REALTIME_CHILD": "1",
            "ADAOS_REALTIME_CONTROL_PORT": str(sidecar_control_port),
            "ADAOS_REALTIME_ENABLE": "1",
            "ADAOS_REALTIME_GO_BINARY": str(adaosd),
            "ADAOS_REALTIME_HOST": sidecar_host,
            "ADAOS_REALTIME_IMPLEMENTATION": sidecar_implementation,
            "ADAOS_REALTIME_PORT": str(sidecar_port),
            "ADAOS_REALTIME_PREFER_DEDICATED": "0",
            "ADAOS_REALTIME_REMOTE_WS_URL": remote_candidates[0],
            "ADAOS_REALTIME_ROUTE_PROXY_ENABLE": "1",
            "ADAOS_REALTIME_ROUTE_WS_PORT": str(route_ws_port),
            "ADAOS_REALTIME_ROUTE_YWS_PORT": str(route_yws_port),
            "ADAOS_ROOT_REPO_ROOT": str(repo),
            "ADAOS_RUNTIME_HOST": "127.0.0.1",
            "ADAOS_RUNTIME_PORT": str(int(serve_port)),
            "ADAOS_SHARED_DOTENV_PATH": str(dotenv),
            "ADAOS_SIDECAR_LIFECYCLE_REPORT_ENABLE": "1",
            "ADAOS_SUPERVISOR_ENABLED": "1",
            "ADAOS_SUPERVISOR_URL": "{supervisor_url}",
        }
        if len(remote_candidates) > 1:
            sidecar_environment["ADAOS_REALTIME_REMOTE_WS_ALT"] = ",".join(remote_candidates[1:])
        sidecar_environment.update(yjs_shadow_environment)
        if sidecar_implementation == "python":
            sidecar_environment["PYTHONPATH"] = str(repo / "src")
        sidecar_environment.update(_lifecycle_environment(node_config))
        sidecar_profile = {
            "schema": WORKER_SCHEMA,
            "id": "adaos-realtime-sidecar",
            "role": "sidecar",
            "command": command,
            "working_directory": str(repo),
            "environment": {
                "clear": True,
                "inherit": ["HOME", "LANG", "LC_ALL", "PATH", "TZ"],
                "set": sidecar_environment,
                "unset": ["PYTHONINSPECT"],
            },
            "restart": _restart_policy(limit=8),
            "shutdown": _shutdown_policy(drain=10, term=10, kill=20),
        }
        _write_json(sidecar_profile_path, sidecar_profile)
        workers.append(
            {
                "profile_path": str(sidecar_profile_path),
                "health": {
                    "url": f"http://127.0.0.1:{sidecar_control_port}/ready",
                    "interval_s": 1,
                    "timeout_s": 1,
                    "startup_timeout_s": 60,
                    "success_json_field": "ready",
                },
                "startup_barrier": True,
                "shutdown_url": f"http://127.0.0.1:{sidecar_control_port}/shutdown",
                "compatibility": {
                    "host": sidecar_host,
                    "port": sidecar_port,
                    "control_port": sidecar_control_port,
                    "ws_port": route_ws_port,
                    "yws_port": route_yws_port,
                },
            }
        )

    workers.append(
        {
            "profile_path": str(runtime_profile_path),
            "health": {
                "url": f"http://127.0.0.1:{int(control_port)}/api/ping",
                "interval_s": 2,
                "timeout_s": 1.5,
                "startup_timeout_s": 120,
                "success_json_field": "ok",
            },
            "shutdown_url": f"http://127.0.0.1:{int(control_port)}/api/admin/shutdown",
            "compatibility": {"host": "127.0.0.1", "port": int(serve_port)},
        }
    )

    approval = _preserved_approval(supervisor_path, scope=approval_scope)
    if approval is None:
        approved_at = datetime.now().astimezone().replace(microsecond=0).isoformat()
        approval = {
            "id": str(approval_id or f"bootstrap-go-supervisor-{node_id}").strip(),
            "approved_at": approved_at,
            "stand": str(approval_stand or socket.gethostname()).strip(),
            "scope": approval_scope,
        }
    if not approval["id"] or not approval["stand"]:
        raise ValueError("approval id and stand cannot be empty")

    supervisor_config = {
        "schema": SUPERVISOR_SCHEMA,
        "base_dir": str(base),
        "listen": {"host": "127.0.0.1", "port": 8776},
        "token_env": "ADAOS_TOKEN",
        "approval": approval,
        "update": {
            "implementation": "go" if update_implementation == "go" else "python_compat",
            "source_mode": (
                str(os.getenv("ADAOS_CORE_UPDATE_SOURCE_MODE") or "auto").strip().lower()
                or "auto"
            ),
            "repo_url": str(
                os.getenv("ADAOS_CORE_UPDATE_REPO_URL") or "https://github.com/inimatic/adaos.git"
            ).strip(),
            "source_archive_url": str(
                os.getenv("ADAOS_CORE_UPDATE_SOURCE_ARCHIVE_URL")
                or os.getenv("ADAOS_CORE_UPDATE_ARCHIVE_URL")
                or ""
            ).strip(),
            "source_archive_url_template": str(
                os.getenv("ADAOS_CORE_UPDATE_SOURCE_ARCHIVE_URL_TEMPLATE")
                or os.getenv("ADAOS_CORE_UPDATE_ARCHIVE_URL_TEMPLATE")
                or ""
            ).strip(),
            "source_archive_sha256": str(
                os.getenv("ADAOS_CORE_UPDATE_SOURCE_ARCHIVE_SHA256")
                or os.getenv("ADAOS_CORE_UPDATE_ARCHIVE_SHA256")
                or ""
            ).strip(),
            "shared_dotenv_path": str(dotenv),
            "prepare_timeout_s": float(
                str(os.getenv("ADAOS_CORE_UPDATE_PREPARE_TIMEOUT_S") or "1800").strip()
            ),
        },
        "workers": workers,
    }
    _write_json(supervisor_path, supervisor_config)
    _atomic_write(private_env_path, _private_environment(token), mode=0o600)

    service_config = {
        "schema": SERVICE_SCHEMA,
        "service_name": "adaos.service",
        "description": "AdaOS (Go supervisor)",
        "base_dir": str(base),
        "binary_path": str(adaosd),
        "supervisor_config_path": str(supervisor_path),
        "environment_files": [{"path": str(private_env_path)}],
        "environment": {"ADAOS_BASE_DIR": str(base)},
        "systemd": {
            "restart": "always",
            "restart_s": 3,
            "timeout_stop_s": 75,
            "kill_mode": "mixed",
            "start_limit_interval_s": 300,
            "start_limit_burst": 10,
            "limit_nofile": 524288,
            "memory_high": "2200M",
            "memory_max": "3000M",
            "memory_swap_max": "1024M",
            "oom_policy": "stop",
        },
    }
    _write_json(service_path, service_config)
    return {
        "schema": "adaosd.bootstrap_configuration.v1",
        "service_config_path": str(service_path),
        "supervisor_config_path": str(supervisor_path),
        "runtime_profile_path": str(runtime_profile_path),
        "sidecar_profile_path": str(sidecar_profile_path) if sidecar_managed else None,
        "sidecar_implementation": sidecar_implementation,
        "update_implementation": update_implementation,
        "sidecar_managed": sidecar_managed,
        "role": role,
        "approval": approval,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate AdaOS adaosd service, supervisor, and worker contracts.")
    parser.add_argument("--base-dir", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--service-config", required=True)
    parser.add_argument("--dotenv", required=True)
    parser.add_argument("--serve-host", default="127.0.0.1")
    parser.add_argument("--serve-port", type=int, default=8777)
    parser.add_argument("--control-port", type=int, default=8777)
    parser.add_argument("--sidecar-implementation", choices=("python", "go"), default="python")
    parser.add_argument("--update-implementation", choices=("python", "go"), default="python")
    parser.add_argument("--approval-scope", choices=("m4c_canary", "production"), default="m4c_canary")
    parser.add_argument("--approval-id")
    parser.add_argument("--approval-stand")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = generate_configuration(
        base_dir=args.base_dir,
        repo_root=args.repo_root,
        python_executable=args.python,
        binary=args.binary,
        service_config_path=args.service_config,
        dotenv_path=args.dotenv,
        serve_host=args.serve_host,
        serve_port=args.serve_port,
        control_port=args.control_port,
        sidecar_implementation=args.sidecar_implementation,
        update_implementation=args.update_implementation,
        approval_scope=args.approval_scope,
        approval_id=args.approval_id,
        approval_stand=args.approval_stand,
    )
    json.dump(result, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
