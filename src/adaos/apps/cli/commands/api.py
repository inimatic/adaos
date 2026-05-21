# src\adaos\apps\cli\commands\api.py
import atexit
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import psutil
import requests
import typer
import uvicorn
from click.core import ParameterSource

from adaos.services.agent_context import get_ctx
from adaos.services.node_config import load_config, save_config
from adaos.services.runtime_dotenv import apply_runtime_dotenv_overrides, merged_runtime_dotenv_env
from adaos.apps.cli.active_control import resolve_control_token

apply_runtime_dotenv_overrides()

app = typer.Typer(help="HTTP API for AdaOS")


def _uvicorn_loop_mode() -> str:
    if os.name != "nt":
        return "auto"
    raw = os.getenv("ADAOS_WIN_SELECTOR_LOOP")
    enabled = False
    if raw is not None:
        val = str(raw).strip().lower()
        if val in {"1", "true", "on", "yes"}:
            enabled = True
        elif val in {"0", "false", "off", "no"}:
            enabled = False
    # Selector loop is now an explicit diagnostic mode only. The stable default
    # on Windows is Uvicorn/asyncio auto, which uses Proactor for socket IO.
    if enabled:
        # Uvicorn's Windows "asyncio" path hardcodes ProactorEventLoop.
        # `loop="none"` falls back to asyncio.new_event_loop(), which respects
        # the process-wide event loop policy we set in the CLI / API server.
        return "none"
    return "auto"


def _is_local_url(url: str | None) -> bool:
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host in {"127.0.0.1", "localhost", "::1"}


def _advertise_base(host: str, port: int) -> str:
    advertised_host = (host or "").strip() or "127.0.0.1"
    if advertised_host in {"0.0.0.0", "::", "[::]"}:
        advertised_host = "127.0.0.1"
    return f"http://{advertised_host}:{int(port)}"


def _configured_local_api_url(conf) -> str | None:
    if conf is None:
        return None
    local_api_url = str(getattr(conf, "local_api_url", "") or "").strip()
    if _is_local_url(local_api_url):
        return local_api_url
    return None


def _resolve_bind(
    conf,
    host: str,
    port: int,
    *,
    explicit_host: bool = False,
    explicit_port: bool = False,
) -> tuple[str, int]:
    role = str(getattr(conf, "role", "") or "").strip().lower() if conf is not None else ""
    if role != "hub":
        return host, int(port)
    if str(os.getenv("ADAOS_SUPERVISOR_ENABLED") or "").strip().lower() in {"1", "true", "yes", "on"}:
        # Supervisor-managed runtimes already pass the slot-specific port explicitly.
        # Do not override it from persisted local_api_url, or slot A can get pulled onto slot B's port.
        return host, int(port)
    if explicit_host or explicit_port:
        return host, int(port)
    if host != "127.0.0.1" or int(port) != 8777:
        return host, int(port)
    local_api_url = _configured_local_api_url(conf)
    if not local_api_url:
        return host, int(port)
    try:
        parsed = urlparse(local_api_url)
        if parsed.hostname and parsed.port:
            return parsed.hostname, int(parsed.port)
    except Exception:
        pass
    return host, int(port)


def _resolve_stop_bind(conf) -> tuple[str, int] | None:
    if conf is None:
        return None
    local_api_url = _configured_local_api_url(conf)
    if not local_api_url:
        return None
    try:
        parsed = urlparse(local_api_url)
    except Exception:
        return None
    hostname = str(parsed.hostname or "").strip()
    if not hostname or not parsed.port or not _is_local_url(local_api_url):
        return None
    return hostname, int(parsed.port)


def _state_dir() -> Path:
    raw = get_ctx().paths.state_dir()
    path = raw() if callable(raw) else raw
    out = Path(path)
    out.mkdir(parents=True, exist_ok=True)
    return out


def _pidfile_path(host: str, port: int) -> Path:
    safe_host = str(host or "127.0.0.1").replace(":", "_").replace("/", "_").replace("\\", "_")
    root = _state_dir() / "api"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"serve-{safe_host}-{int(port)}.json"


def _restart_marker_path(host: str, port: int) -> Path:
    safe_host = str(host or "127.0.0.1").replace(":", "_").replace("/", "_").replace("\\", "_")
    root = _state_dir() / "api"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"restart-{safe_host}-{int(port)}.json"


def _write_restart_marker(path: Path, *, host: str, port: int, reason: str, ttl_s: float = 180.0) -> None:
    now = time.time()
    payload = {
        "host": str(host or "127.0.0.1"),
        "port": int(port),
        "reason": str(reason or "cli.restart"),
        "created_at": now,
        "expires_at": now + max(30.0, float(ttl_s)),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _clear_restart_marker(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def _read_pidfile(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _write_pidfile(path: Path, *, host: str, port: int, advertised_base: str, owner: str | None = None) -> None:
    payload = {
        "pid": os.getpid(),
        "host": host,
        "port": int(port),
        "advertised_base": advertised_base,
        "owner": str(owner or _current_launch_owner()),
        "launch_mode": str(os.getenv("ADAOS_RUNTIME_LAUNCH_MODE") or os.getenv("ADAOS_AUTOSTART_MODE") or ""),
        "started_at": time.time(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _host_matches_listener(bind_host: str, listener_host: str | None) -> bool:
    host = str(bind_host or "").strip().lower()
    other = str(listener_host or "").strip().lower()
    if not host or host in {"0.0.0.0", "::", "[::]"}:
        return True
    if host == other:
        return True
    local_any = {"0.0.0.0", "::", "[::]"}
    loopbacks = {"127.0.0.1", "::1", "localhost"}
    if host in loopbacks and (other in loopbacks or other in local_any):
        return True
    return False


def _process_cmdline(proc: psutil.Process) -> list[str]:
    try:
        return [str(part) for part in proc.cmdline()]
    except Exception:
        return []


def _process_kind(proc: psutil.Process) -> str | None:
    cmdline = [part.lower() for part in _process_cmdline(proc)]
    joined = " ".join(cmdline)
    if "adaos" not in joined:
        return None
    if "adaos.apps.supervisor" in joined:
        return "supervisor"
    if "adaos.apps.autostart_runner" in joined:
        return "autostart_runner"
    if "serve" in joined and (("api" in joined) or "adaos.apps.cli.commands.api" in joined):
        return "api_serve"
    return None


def _process_looks_like_adaos_api(proc: psutil.Process) -> bool:
    return _process_kind(proc) is not None


def _process_base_dir(proc: psutil.Process) -> Path | None:
    raw = ""
    try:
        raw = str((proc.environ() or {}).get("ADAOS_BASE_DIR") or "").strip()
    except Exception:
        raw = ""
    if raw:
        try:
            return Path(raw).expanduser().resolve()
        except Exception:
            return None
    return None


def _current_base_dir() -> Path | None:
    try:
        raw = get_ctx().paths.base_dir()
        return Path(raw() if callable(raw) else raw).expanduser().resolve()
    except Exception:
        return None


def _same_base_dir(proc: psutil.Process, current_base_dir: Path | None) -> bool:
    if current_base_dir is None:
        return False
    proc_base = _process_base_dir(proc)
    if proc_base is None:
        return False
    try:
        return proc_base == current_base_dir
    except Exception:
        return False


def _current_launch_owner() -> str:
    if str(os.getenv("ADAOS_AUTOSTART_MODE") or "").strip() or str(os.getenv("ADAOS_AUTOSTART_MANAGED") or "").strip():
        return "autostart"
    return "api"


def _candidate_bind(proc: psutil.Process, fallback_host: str, fallback_port: int) -> tuple[str, int]:
    cmdline = _process_cmdline(proc)
    raw_port = _cmdline_option_value(cmdline, "--port")
    try:
        port = int(str(raw_port or "").strip() or str(int(fallback_port)))
    except Exception:
        port = int(fallback_port)
    host = _cmdline_option_value(cmdline, "--host") or str(fallback_host or "127.0.0.1")
    return host, port


def _cmdline_option_value(cmdline: list[str], option: str) -> str | None:
    opt = str(option or "").strip().lower()
    if not opt:
        return None
    for idx, part in enumerate(cmdline):
        item = str(part or "").strip()
        lower = item.lower()
        if lower == opt:
            if idx + 1 < len(cmdline):
                value = str(cmdline[idx + 1] or "").strip()
                return value or None
            return None
        prefix = f"{opt}="
        if lower.startswith(prefix):
            value = item[len(prefix) :].strip()
            return value or None
    return None


def _process_matches_bind(proc: psutil.Process, host: str, port: int) -> bool:
    cmdline = _process_cmdline(proc)
    if not cmdline:
        return False
    if not _process_looks_like_adaos_api(proc):
        return False
    raw_port = _cmdline_option_value(cmdline, "--port")
    try:
        cmd_port = int(str(raw_port or "").strip() or "8777")
    except Exception:
        return False
    if cmd_port != int(port):
        return False
    cmd_host = _cmdline_option_value(cmdline, "--host") or "127.0.0.1"
    return _host_matches_listener(host, cmd_host)


def _current_process_family_pids() -> set[int]:
    protected: set[int] = {os.getpid()}
    try:
        current = psutil.Process(os.getpid())
    except psutil.Error:
        return protected
    try:
        for proc in current.parents():
            pid = int(getattr(proc, "pid", 0) or 0)
            if pid > 0:
                protected.add(pid)
    except psutil.Error:
        pass
    try:
        for proc in current.children(recursive=True):
            pid = int(getattr(proc, "pid", 0) or 0)
            if pid > 0:
                protected.add(pid)
    except psutil.Error:
        pass
    return protected


def _find_matching_server_pids(host: str, port: int, *, protected_pids: set[int] | None = None) -> list[int]:
    matches: list[int] = []
    blocked = protected_pids or set()
    try:
        for proc in psutil.process_iter(["pid", "cmdline"]):
            pid = int(proc.info.get("pid") or 0)
            if pid <= 0 or pid == os.getpid() or pid in blocked:
                continue
            if _process_matches_bind(proc, host, port):
                matches.append(pid)
    except Exception:
        return matches
    return matches


def _find_listening_server_pid(host: str, port: int) -> int | None:
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.status != psutil.CONN_LISTEN:
                continue
            laddr = getattr(conn, "laddr", None)
            if not laddr or int(getattr(laddr, "port", 0) or 0) != int(port):
                continue
            listener_host = getattr(laddr, "ip", None) or getattr(laddr, "host", None)
            if not _host_matches_listener(host, listener_host):
                continue
            pid = int(conn.pid or 0)
            if pid > 0:
                return pid
    except Exception:
        return None
    return None


def _terminate_process_tree(pid: int) -> None:
    if pid <= 0 or pid == os.getpid():
        return
    try:
        proc = psutil.Process(pid)
    except psutil.Error:
        return
    try:
        children = proc.children(recursive=True)
    except psutil.Error:
        children = []
    for child in reversed(children):
        try:
            child.terminate()
        except psutil.Error:
            pass
    psutil.wait_procs(children, timeout=3.0)
    for child in children:
        try:
            if child.is_running():
                child.kill()
        except psutil.Error:
            pass
    try:
        proc.terminate()
        proc.wait(timeout=5.0)
    except psutil.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=3.0)
        except psutil.Error:
            pass
    except psutil.Error:
        pass


def _process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def _wait_for_pids_exit(pids: list[int], *, timeout: float) -> None:
    deadline = time.monotonic() + max(0.0, float(timeout))
    while time.monotonic() < deadline:
        if not any(_process_running(pid) for pid in pids):
            return
        time.sleep(0.1)


def _resolved_shutdown_token(token: str | None = None) -> str | None:
    raw = str(token or os.getenv("ADAOS_TOKEN") or "").strip()
    if raw:
        return raw
    try:
        raw = str(getattr(load_config(), "token", "") or "").strip()
    except Exception:
        raw = ""
    if raw:
        return raw
    try:
        raw = str(resolve_control_token()).strip()
    except Exception:
        raw = ""
    return raw or None


def _stop_autostart_service_for_takeover(current_base_dir: Path | None) -> None:
    try:
        from adaos.services.autostart import status as autostart_status
    except Exception:
        return
    try:
        info = autostart_status(get_ctx())
    except Exception:
        return
    if not isinstance(info, dict) or info.get("active") is not True:
        return
    try:
        service_main_pid = int(info.get("service_main_pid") or 0)
    except Exception:
        service_main_pid = 0
    if current_base_dir is not None:
        raw_base = str(info.get("base_dir") or "").strip()
        if raw_base:
            try:
                if Path(raw_base).expanduser().resolve() != current_base_dir:
                    return
            except Exception:
                return

    platform_name = str(info.get("platform") or "").strip().lower()
    cmd: list[str] | None = None
    if platform_name == "linux":
        scope = str(info.get("scope") or "").strip().lower()
        service_ref = str(info.get("service") or "adaos.service").strip() or "adaos.service"
        service_name = Path(service_ref).name or "adaos.service"
        cmd = ["systemctl"]
        if scope == "user":
            cmd.append("--user")
        cmd.extend(["stop", service_name])
    elif platform_name == "windows":
        task_name = str(info.get("task") or "AdaOS").strip() or "AdaOS"
        cmd = ["schtasks", "/End", "/TN", task_name]
    elif platform_name == "macos":
        label = "com.adaos.autostart"
        try:
            uid = str(os.getuid()) if hasattr(os, "getuid") else ""
        except Exception:
            uid = ""
        domain = f"gui/{uid}" if uid else "gui"
        cmd = ["launchctl", "bootout", domain, label]
    if not cmd:
        return
    try:
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=25,
        )
    except Exception:
        return
    if service_main_pid > 0:
        _wait_for_pids_exit([service_main_pid], timeout=20.0)


def _find_owner_conflict_pids(
    host: str,
    port: int,
    *,
    new_owner: str,
    protected_pids: set[int],
    current_base_dir: Path | None,
) -> list[int]:
    conflicts: list[int] = []
    try:
        for proc in psutil.process_iter(["pid", "cmdline"]):
            pid = int(proc.info.get("pid") or 0)
            if pid <= 0 or pid == os.getpid() or pid in protected_pids:
                continue
            kind = _process_kind(proc)
            if kind is None:
                continue
            same_bind = _process_matches_bind(proc, host, port)
            same_base = _same_base_dir(proc, current_base_dir)
            conflict = same_bind
            if new_owner == "autostart":
                conflict = conflict or (kind == "api_serve" and same_base)
            elif new_owner == "api":
                conflict = conflict or (kind in {"autostart_runner", "supervisor", "api_serve"} and same_base)
            if conflict and pid not in conflicts:
                conflicts.append(pid)
    except Exception:
        return conflicts
    return conflicts


def _stop_previous_server(host: str, port: int) -> None:
    pidfile = _pidfile_path(host, port)
    protected_pids = _current_process_family_pids()
    current_owner = _current_launch_owner()
    current_base_dir = _current_base_dir()
    if current_owner == "api":
        _stop_autostart_service_for_takeover(current_base_dir)
    candidate_pids: list[int] = []
    meta = _read_pidfile(pidfile)
    try:
        file_pid = int((meta or {}).get("pid") or 0)
    except Exception:
        file_pid = 0
    if file_pid > 0 and file_pid != os.getpid() and file_pid not in protected_pids:
        candidate_pids.append(file_pid)
    owner_pid = _find_listening_server_pid(host, port)
    if owner_pid and owner_pid != os.getpid() and owner_pid not in protected_pids and owner_pid not in candidate_pids:
        candidate_pids.append(owner_pid)
    for pid in _find_matching_server_pids(host, port, protected_pids=protected_pids):
        if pid not in candidate_pids:
            candidate_pids.append(pid)
    for pid in _find_owner_conflict_pids(
        host,
        port,
        new_owner=current_owner,
        protected_pids=protected_pids,
        current_base_dir=current_base_dir,
    ):
        if pid not in candidate_pids:
            candidate_pids.append(pid)

    token = _resolved_shutdown_token()
    for pid in candidate_pids:
        try:
            proc = psutil.Process(pid)
        except psutil.Error:
            continue
        kind = _process_kind(proc)
        if kind is None:
            continue
        stopped = False
        if kind in {"api_serve", "autostart_runner"}:
            candidate_host, candidate_port = _candidate_bind(proc, host, port)
            stopped = _request_graceful_shutdown(
                candidate_host,
                candidate_port,
                token=token,
                reason=f"{current_owner}.takeover",
            )
        if stopped:
            continue
        _terminate_process_tree(pid)
    _wait_for_pids_exit(candidate_pids, timeout=5.0)

    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        owner_pid = _find_listening_server_pid(host, port)
        if not owner_pid or owner_pid == os.getpid():
            break
        time.sleep(0.1)
    try:
        if not candidate_pids and pidfile.exists():
            pidfile.unlink()
    except Exception:
        pass


def _cleanup_pidfile(path: Path) -> None:
    try:
        data = _read_pidfile(path)
        if int((data or {}).get("pid") or 0) == os.getpid():
            path.unlink(missing_ok=True)
    except Exception:
        pass


def _wait_for_server_exit(host: str, port: int, *, timeout: float) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout))
    while time.monotonic() < deadline:
        owner_pid = _find_listening_server_pid(host, port)
        remaining = _find_matching_server_pids(host, port, protected_pids=_current_process_family_pids())
        if not owner_pid and not remaining:
            return True
        time.sleep(0.1)
    return False


def _wait_for_server_start(host: str, port: int, *, timeout: float) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout))
    while time.monotonic() < deadline:
        owner_pid = _find_listening_server_pid(host, port)
        if owner_pid and owner_pid != os.getpid():
            return True
        time.sleep(0.1)
    return False


def _spawn_detached_server(host: str, port: int, *, token: str | None, reload: bool = False) -> None:
    args = [
        sys.executable,
        "-m",
        "adaos.apps.cli.commands.api",
        "serve",
        "--host",
        str(host),
        "--port",
        str(int(port)),
    ]
    if reload:
        args.append("--reload")
    if token:
        args.extend(["--token", str(token)])

    env = merged_runtime_dotenv_env(os.environ.copy())
    creationflags = 0
    popen_kwargs: dict[str, object] = {
        "args": args,
        "cwd": os.getcwd(),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(
            getattr(subprocess, "DETACHED_PROCESS", 0)
        )
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        popen_kwargs["startupinfo"] = startupinfo
        popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True
    subprocess.Popen(**popen_kwargs)


def _request_graceful_shutdown(host: str, port: int, *, token: str | None, reason: str = "cli.stop") -> bool:
    url = f"http://{host}:{int(port)}/api/admin/shutdown"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-AdaOS-Token"] = str(token)
    try:
        response = requests.post(
            url,
            json={"reason": reason, "drain_timeout_sec": 5.0, "signal_delay_sec": 0.2},
            headers=headers,
            timeout=(2.0, 15.0),
        )
    except Exception:
        return False
    if response.status_code not in (200, 202):
        return False
    return _wait_for_server_exit(host, port, timeout=20.0)


@app.command("serve")
def serve(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8777, "--port"),
    reload: bool = typer.Option(False, "--reload", help="Enable uvicorn autoreload"),
    token: str | None = typer.Option(None, "--token", help="Override X-AdaOS-Token / ADAOS_TOKEN"),
):
    """Serve the AdaOS local HTTP API."""
    from adaos.apps.api.server import app as server_app

    conf = None
    try:
        conf = load_config()
    except Exception:
        conf = None

    try:
        explicit_host = ctx.get_parameter_source("host") == ParameterSource.COMMANDLINE
    except Exception:
        explicit_host = False
    try:
        explicit_port = ctx.get_parameter_source("port") == ParameterSource.COMMANDLINE
    except Exception:
        explicit_port = False

    host, port = _resolve_bind(
        conf,
        host,
        port,
        explicit_host=explicit_host,
        explicit_port=explicit_port,
    )
    advertised_base = _advertise_base(host, port)
    pidfile = _pidfile_path(host, port)

    _stop_previous_server(host, port)
    _write_pidfile(pidfile, host=host, port=port, advertised_base=advertised_base, owner="api")
    atexit.register(_cleanup_pidfile, pidfile)

    if conf is not None and str(getattr(conf, "role", "") or "").strip().lower() == "hub":
        try:
            if _is_local_url(advertised_base) and str(getattr(conf, "local_api_url", "") or "").strip() != advertised_base:
                conf.local_api_url = advertised_base
            save_config(conf)
        except Exception:
            pass

    if token:
        os.environ["ADAOS_TOKEN"] = token
    try:
        os.environ["ADAOS_SELF_BASE_URL"] = advertised_base
    except Exception:
        pass
    try:
        os.environ["ADAOS_RUNTIME_LAUNCH_MODE"] = "api_serve"
    except Exception:
        pass

    try:
        loop_mode = _uvicorn_loop_mode()
        if os.getenv("HUB_NATS_TRACE", "0") == "1" or os.getenv("ADAOS_CLI_DEBUG", "0") == "1":
            try:
                print(f"[AdaOS] uvicorn loop mode={loop_mode}")
            except Exception:
                pass
        uvicorn.run(
            server_app,
            host=host,
            port=int(port),
            loop=loop_mode,
            reload=reload,
            workers=1,
            access_log=False,
            # Remote yws clients can receive large first-sync bursts through the root route.
            # Keep WebSocket writes cheap and predictable on the event loop.
            ws_per_message_deflate=False,
        )
    finally:
        _cleanup_pidfile(pidfile)


@app.command("stop")
def stop():
    """Stop the AdaOS local HTTP API resolved from persisted local_api_url."""
    try:
        conf = load_config()
    except Exception as exc:
        typer.secho(f"[AdaOS] failed to load local node runtime config: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    bind = _resolve_stop_bind(conf)
    if bind is None:
        typer.secho(
            "[AdaOS] local runtime state does not contain a local_api_url with explicit host:port",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    host, port = bind
    pidfile = _pidfile_path(host, port)
    had_pidfile = pidfile.exists()
    owner_pid = _find_listening_server_pid(host, port)
    extra_pids = _find_matching_server_pids(host, port, protected_pids=_current_process_family_pids())

    stopped_gracefully = False
    if owner_pid or extra_pids:
        stopped_gracefully = _request_graceful_shutdown(
            host,
            port,
            token=getattr(conf, "token", None) or resolve_control_token(),
        )

    if not stopped_gracefully:
        _stop_previous_server(host, port)

    remaining_owner = _find_listening_server_pid(host, port)
    remaining_pids = _find_matching_server_pids(host, port, protected_pids=_current_process_family_pids())
    if remaining_owner or remaining_pids:
        typer.secho(
            f"[AdaOS] failed to stop api server at {host}:{port}",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    if owner_pid or extra_pids or had_pidfile:
        if stopped_gracefully:
            typer.echo(f"Stopped AdaOS API gracefully at http://{host}:{port}")
        else:
            typer.echo(f"Stopped AdaOS API at http://{host}:{port}")
    else:
        typer.echo(f"No AdaOS API server running at http://{host}:{port}")


@app.command("restart")
def restart():
    """Restart the AdaOS local HTTP API with a single Telegram notification."""
    try:
        conf = load_config()
    except Exception as exc:
        typer.secho(f"[AdaOS] failed to load local node runtime config: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    bind = _resolve_stop_bind(conf)
    if bind is None:
        typer.secho(
            "[AdaOS] local runtime state does not contain a local_api_url with explicit host:port",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    host, port = bind
    token = getattr(conf, "token", None) or resolve_control_token()
    marker = _restart_marker_path(host, port)
    _write_restart_marker(marker, host=host, port=port, reason="cli.restart")

    stopped_gracefully = False
    try:
        stopped_gracefully = _request_graceful_shutdown(host, port, token=token, reason="cli.restart")
        if not stopped_gracefully:
            _stop_previous_server(host, port)
            if _find_listening_server_pid(host, port) or _find_matching_server_pids(
                host, port, protected_pids=_current_process_family_pids()
            ):
                raise RuntimeError(f"failed to stop api server at {host}:{port}")

        _spawn_detached_server(host, port, token=token, reload=False)
        if not _wait_for_server_start(host, port, timeout=20.0):
            raise RuntimeError(f"api server did not start at {host}:{port}")
    except Exception as exc:
        _clear_restart_marker(marker)
        typer.secho(f"[AdaOS] restart failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    mode = "gracefully" if stopped_gracefully else "after hard stop"
    typer.echo(f"Restarted AdaOS API {mode} at http://{host}:{port}")


if __name__ == "__main__":
    app()
