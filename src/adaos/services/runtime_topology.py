from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Final, Literal
from urllib.parse import urlparse

from adaos.services.env_policy import env_bool, env_int, env_text

DEFAULT_LOOPBACK_HOST: Final[str] = "127.0.0.1"
LOCALHOST_HOST: Final[str] = "localhost"
IPV6_LOOPBACK_HOST: Final[str] = "::1"

DEFAULT_SUPERVISOR_PORT: Final[int] = 8776
DEFAULT_RUNTIME_PORT: Final[int] = 8777
DEFAULT_CANDIDATE_RUNTIME_PORT: Final[int] = 8778
DEFAULT_DEV_RUNTIME_PORT: Final[int] = 8779

LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset(
    {DEFAULT_LOOPBACK_HOST, LOCALHOST_HOST, IPV6_LOOPBACK_HOST, "[::1]"}
)
LOCAL_RUNTIME_PORTS: Final[tuple[int, ...]] = (
    DEFAULT_RUNTIME_PORT,
    DEFAULT_CANDIDATE_RUNTIME_PORT,
    DEFAULT_DEV_RUNTIME_PORT,
)
LOCAL_RUNTIME_PREFERRED_PORTS: Final[tuple[int, ...]] = (
    DEFAULT_RUNTIME_PORT,
    DEFAULT_CANDIDATE_RUNTIME_PORT,
    DEFAULT_DEV_RUNTIME_PORT,
)
LOCAL_MEMBER_PREFERRED_PORTS: Final[tuple[int, ...]] = (
    DEFAULT_CANDIDATE_RUNTIME_PORT,
    DEFAULT_RUNTIME_PORT,
    DEFAULT_DEV_RUNTIME_PORT,
)


def unique_texts(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = str(value or "").strip().rstrip("/")
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result


def normalize_port(value: Any, *, default: int) -> int:
    try:
        port = int(str(value or "").strip() or str(default))
    except Exception:
        port = int(default)
    return max(1, min(65535, int(port)))


def is_loopback_host(host: Any) -> bool:
    token = str(host or "").strip().lower()
    return token in LOOPBACK_HOSTS


def is_loopback_http_url(url: Any) -> bool:
    try:
        parsed = urlparse(str(url or "").strip())
    except Exception:
        return False
    if str(parsed.scheme or "").strip().lower() not in {"http", "https", "ws", "wss"}:
        return False
    return is_loopback_host(parsed.hostname)


def http_base(*, host: str = DEFAULT_LOOPBACK_HOST, port: int = DEFAULT_RUNTIME_PORT) -> str:
    return f"http://{str(host or DEFAULT_LOOPBACK_HOST).strip() or DEFAULT_LOOPBACK_HOST}:{normalize_port(port, default=DEFAULT_RUNTIME_PORT)}"


def ws_base(*, host: str = DEFAULT_LOOPBACK_HOST, port: int = DEFAULT_RUNTIME_PORT) -> str:
    return f"ws://{str(host or DEFAULT_LOOPBACK_HOST).strip() or DEFAULT_LOOPBACK_HOST}:{normalize_port(port, default=DEFAULT_RUNTIME_PORT)}"


def local_http_bases(
    ports: Iterable[Any],
    *,
    hosts: Iterable[str] = (DEFAULT_LOOPBACK_HOST, LOCALHOST_HOST),
    order: Literal["port", "host"] = "port",
) -> list[str]:
    ports_tuple = tuple(ports)
    hosts_tuple = tuple(hosts)
    if order == "host":
        values = (
            http_base(host=host, port=normalize_port(port, default=DEFAULT_RUNTIME_PORT))
            for host in hosts_tuple
            for port in ports_tuple
        )
    else:
        values = (
            http_base(host=host, port=normalize_port(port, default=DEFAULT_RUNTIME_PORT))
            for port in ports_tuple
            for host in hosts_tuple
        )
    return unique_texts(values)


def local_ws_bases(
    ports: Iterable[Any],
    *,
    hosts: Iterable[str] = (DEFAULT_LOOPBACK_HOST,),
) -> list[str]:
    return unique_texts(
        ws_base(host=host, port=normalize_port(port, default=DEFAULT_RUNTIME_PORT))
        for port in ports
        for host in hosts
    )


def runtime_port_from_env(*, default: int = DEFAULT_RUNTIME_PORT) -> int | None:
    raw = env_text("ADAOS_RUNTIME_PORT")
    if not raw.isdigit():
        return None
    return normalize_port(raw, default=default)


def runtime_port_http_base_from_env() -> str | None:
    port = runtime_port_from_env()
    if port is None:
        return None
    return http_base(port=port)


def runtime_probe_http_bases(
    *,
    include_runtime_env: bool = True,
    include_localhost: bool = True,
    ports: Iterable[int] = (DEFAULT_CANDIDATE_RUNTIME_PORT, DEFAULT_RUNTIME_PORT),
) -> list[str]:
    candidates: list[str] = []
    if include_runtime_env:
        runtime_env_base = runtime_port_http_base_from_env()
        if runtime_env_base:
            candidates.append(runtime_env_base)
    hosts = (DEFAULT_LOOPBACK_HOST, LOCALHOST_HOST) if include_localhost else (DEFAULT_LOOPBACK_HOST,)
    candidates.extend(local_http_bases(ports, hosts=hosts))
    return unique_texts(candidates)


def runtime_fallback_http_bases(
    *,
    prefer_member: bool = False,
    include_localhost: bool = True,
    order: Literal["port", "host"] = "port",
) -> list[str]:
    ports = LOCAL_MEMBER_PREFERRED_PORTS if prefer_member else LOCAL_RUNTIME_PREFERRED_PORTS
    hosts = (DEFAULT_LOOPBACK_HOST, LOCALHOST_HOST) if include_localhost else (DEFAULT_LOOPBACK_HOST,)
    return local_http_bases(ports, hosts=hosts, order=order)


def runtime_fallback_ws_bases(
    *,
    prefer_member: bool = False,
    include_localhost: bool = False,
    include_dev: bool = False,
) -> list[str]:
    ports: tuple[int, ...]
    if prefer_member:
        ports = LOCAL_MEMBER_PREFERRED_PORTS if include_dev else (
            DEFAULT_CANDIDATE_RUNTIME_PORT,
            DEFAULT_RUNTIME_PORT,
        )
    else:
        ports = LOCAL_RUNTIME_PREFERRED_PORTS if include_dev else (
            DEFAULT_CANDIDATE_RUNTIME_PORT,
            DEFAULT_RUNTIME_PORT,
        )
    hosts = (DEFAULT_LOOPBACK_HOST, LOCALHOST_HOST) if include_localhost else (DEFAULT_LOOPBACK_HOST,)
    return local_ws_bases(ports, hosts=hosts)


def supervisor_host_from_env(*, default: str = DEFAULT_LOOPBACK_HOST) -> str:
    return env_text("ADAOS_SUPERVISOR_HOST", default).strip() or default


def supervisor_port_from_env(*, default: int = DEFAULT_SUPERVISOR_PORT) -> int:
    return env_int("ADAOS_SUPERVISOR_PORT", default)


def supervisor_base_from_env() -> str:
    return http_base(host=supervisor_host_from_env(), port=supervisor_port_from_env())


def supervisor_base_candidates_from_env(
    *,
    require_signal: bool = False,
    include_localhost: bool = False,
    include_default_loopback: bool = True,
) -> list[str]:
    explicit_url = env_text("ADAOS_SUPERVISOR_URL").rstrip("/")
    explicit_base = env_text("ADAOS_SUPERVISOR_BASE").rstrip("/")
    explicit_host = env_text("ADAOS_SUPERVISOR_HOST")
    explicit_port = env_text("ADAOS_SUPERVISOR_PORT")
    enabled = env_bool("ADAOS_SUPERVISOR_ENABLED") or env_bool("ADAOS_AUTOSTART_MANAGED")
    if require_signal and not (enabled or explicit_url or explicit_base or explicit_host or explicit_port):
        return []

    candidates: list[str] = []
    candidates.extend([explicit_url, explicit_base])
    host = explicit_host or DEFAULT_LOOPBACK_HOST
    port = normalize_port(explicit_port, default=DEFAULT_SUPERVISOR_PORT)
    candidates.append(http_base(host=host, port=port))
    if include_default_loopback:
        candidates.append(http_base(port=DEFAULT_SUPERVISOR_PORT))
    elif not is_loopback_host(host):
        candidates.append(http_base(port=port))
    if include_localhost:
        candidates.append(http_base(host=LOCALHOST_HOST, port=port))
    return unique_texts(candidates)
