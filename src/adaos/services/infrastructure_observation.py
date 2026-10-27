"""Read bounded host observations through operator-admitted, read-only identities.

The application cannot select a host, remote path, SSH command or private key.
The private provider configuration is node-owned, outside UI and skill storage.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from typing import Any

SCHEMA = "adaos.infrastructure.observation.v1"
MAX_BYTES = 65536
TARGETS = ("eu", "ru", "infra-01")
_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, dict]] = {}


class InfrastructureObservationError(RuntimeError):
    pass


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _token(value, maximum=128):
    return isinstance(value, str) and 0 < len(value) <= maximum and re.fullmatch(r"[a-zA-Z0-9_.:@/-]+", value)


def project_observation(raw: bytes, target: str, *, now: float | None = None) -> dict:
    """Reject non-finite/malformed data; copy only allowlisted non-secret fields."""
    now = time.time() if now is None else now
    if target not in TARGETS or len(raw) > MAX_BYTES:
        raise InfrastructureObservationError("invalid_observation")
    try:
        value = json.loads(raw)
        observed = value["observed_at"]
        if value["schema"] != SCHEMA or value["target_id"] != target or not finite(observed):
            raise ValueError()
        age = now - observed
        if age < -30 or age > 300:
            raise InfrastructureObservationError("stale_observation")
        checks = []
        for item in value["checks"][:24]:
            if not _token(item["id"]) or item["state"] not in ("healthy", "degraded", "unknown") or not _token(item["reason"]):
                raise ValueError()
            checks.append({key: item[key] for key in ("id", "state", "reason")})
        if not checks:
            raise ValueError()
        resources = {key: value.get("resources", {}).get(key) for key in (
            "disk_free_bytes", "disk_total_bytes", "memory_total_bytes", "memory_available_bytes", "redis_used_bytes", "redis_max_bytes")}
        resources = {k: v for k, v in resources.items() if finite(v) and v >= 0}
        services = [{"id": row["id"], "state": row["state"]} for row in value.get("services", [])[:12]
                    if row.get("id") in ("postgres", "redis", "nats", "reverse-proxy") and row.get("state") in ("running", "not_running")]
        backups = []
        for row in value.get("backups", [])[:4]:
            if row.get("scope") not in ("infrastructure.state", "root.eu", "root.ru") or row.get("state") not in ("healthy", "degraded", "unknown", "not_configured"):
                raise ValueError()
            copies = []
            for copy in row.get("copies", [])[:3]:
                if copy.get("supplier") not in ("capture", "hetzner", "regru"):
                    raise ValueError()
                copy_age = copy.get("age_seconds")
                copies.append({"supplier": copy["supplier"], "verified": copy.get("verified") is True,
                               "age_seconds": copy_age if finite(copy_age) and copy_age >= 0 else None})
            backups.append({"scope": row["scope"], "state": row["state"], "copies": copies, "restore_state": "not_observed"})
        delivery = {k: v for k, v in value.get("delivery", {}).items()
                    if k in ("source_revision", "operation", "image_id", "contract_id") and _token(v, 192)}
        return {"schema": SCHEMA, "target_id": target, "observed_at": observed, "received_at": now,
                "age_seconds": max(0, round(age, 1)), "fresh": True, "checks": checks,
                "services": services, "resources": resources, "backups": backups, "delivery": delivery,
                "state": "healthy" if all(c["state"] == "healthy" for c in checks) else "degraded"}
    except InfrastructureObservationError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        raise InfrastructureObservationError("invalid_observation") from None


def _read_remote(config: dict, temporary_root: Path) -> bytes:
    # Values come exclusively from private node admission, never tool arguments.
    if set(config) != {"host", "port", "username", "identity_file", "known_hosts_file"}:
        raise InfrastructureObservationError("invalid_provider_admission")
    if not re.fullmatch(r"[a-zA-Z0-9.-]{1,253}", str(config["host"])) or config["username"] != "inimatic_infra_observer" or type(config["port"]) is not int or not 1 <= config["port"] <= 65535:
        raise InfrastructureObservationError("invalid_provider_admission")
    key, known = Path(config["identity_file"]), Path(config["known_hosts_file"])
    if not key.is_file() or key.is_symlink() or not known.is_file() or known.is_symlink():
        raise InfrastructureObservationError("missing_provider_admission")
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="infrastructure-observation-", dir=temporary_root) as folder:
        target = Path(folder) / "snapshot.json"
        args = ["sftp", "-q", "-b", "-", "-F", os.devnull,
                "-oBatchMode=yes", "-oStrictHostKeyChecking=yes", "-oIdentitiesOnly=yes",
                "-oConnectTimeout=5", "-oConnectionAttempts=1", "-oServerAliveInterval=3", "-oServerAliveCountMax=1",
                "-oUserKnownHostsFile=" + str(known), "-i", str(key), "-P", str(config["port"]),
                config["username"] + "@" + config["host"]]
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            process.stdin.write(('get /snapshot.json "' + target.as_posix() + '"\nbye\n').encode())
            process.stdin.close()
            deadline = time.monotonic() + 12
            while process.poll() is None:
                if time.monotonic() > deadline or (target.exists() and target.stat().st_size > MAX_BYTES):
                    raise InfrastructureObservationError("observation_timeout_or_budget")
                time.sleep(.03)
            if process.returncode != 0 or not target.is_file() or target.stat().st_size > MAX_BYTES:
                raise InfrastructureObservationError("observer_unreachable")
            return target.read_bytes()
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)


def read_inventory(ctx: Any, *, use_cache=True) -> dict:
    state_root = Path(ctx.paths.state_dir())
    path = state_root / "private_recovery/infrastructure-observer/observer.json"
    skill = getattr(ctx.skill_ctx.get(), "name", "")
    key = str(state_root)
    with _LOCK:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 32768:
            raise InfrastructureObservationError("provider_not_configured")
        config = json.loads(path.read_bytes())
        if config.get("schema") != "adaos.infrastructure.observer-admission.v1" or skill not in config.get("allowed_skills", []):
            raise PermissionError("infrastructure observer admission denied")
        # Recheck admission before every cache read; revocation must be immediate.
        now = time.time()
        cached = _CACHE.get(key)
        if use_cache and cached and now - cached[0] < 30 and all(
            not row.get('fresh') or now - row['observed_at'] <= 300 for row in cached[1]['targets']):
            return deepcopy(cached[1])
        admitted = config.get("targets", {})
        if set(admitted) != set(TARGETS):
            raise InfrastructureObservationError("incomplete_provider_admission")

        def one(target):
            try:
                return project_observation(_read_remote(admitted[target], state_root / "provider-tmp"), target)
            except Exception as error:
                reason = str(error) if isinstance(error, InfrastructureObservationError) else "observer_unavailable"
                return {"target_id": target, "state": "unknown", "fresh": False, "observed_at": None,
                        "received_at": time.time(), "checks": [{"id": "observer", "state": "unknown", "reason": reason}],
                        "resources": {}, "services": [], "backups": [], "delivery": {}}

        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="infra-observation") as pool:
            rows = list(pool.map(one, TARGETS))
        result = {"schema": "adaos.infrastructure.inventory.v1", "checked_at": time.time(), "targets": rows}
        _CACHE[key] = (time.time(), result)
        return deepcopy(result)


def observer_owner_is_current(ctx: Any) -> bool:
    """Stop retained background handlers after deactivation or A/B replacement."""
    from adaos.services.skill.runtime_env import SkillRuntimeEnvironment
    current = ctx.skill_ctx.get()
    if current is None:
        return False
    environment = SkillRuntimeEnvironment(skills_root=Path(ctx.paths.skills_dir()), skill_name=current.name)
    selection = environment.read_runtime_selection()
    version, slot = selection.get('version'), selection.get('slot')
    if not version or slot not in ('A', 'B') or environment.read_deactivation().get('deactivated'):
        return False
    source = environment.build_slot_paths(version, slot).src_dir / 'skills' / current.name
    return source.resolve() == Path(current.path).resolve()
