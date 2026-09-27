from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import threading
import time

from cryptography.fernet import Fernet

ENV_MASTER = "ADAOS_VAULT_MASTER_KEY"


def generate_key() -> bytes:
    return Fernet.generate_key()


def _environment_master_key() -> bytes | None:
    value = os.getenv(ENV_MASTER, "").strip()
    if not value:
        return None
    candidates = [value.encode("utf-8")]
    try:
        candidates.append(base64.urlsafe_b64decode(value.encode("utf-8")))
    except Exception:
        pass
    for candidate in candidates:
        try:
            Fernet(candidate)
        except (TypeError, ValueError):
            continue
        return candidate
    raise ValueError(f"{ENV_MASTER} is not a valid Fernet key")


def local_master_key_path(base_dir: str | Path, profile: str) -> Path:
    """Return the node-private master-key path for a headless FileVault."""

    profile_digest = hashlib.sha256(str(profile or "default").encode("utf-8")).hexdigest()[:16]
    return Path(base_dir) / "private" / "credentials" / f"vault-master-{profile_digest}.key"


def load_or_create_local_master(path: str | Path) -> bytes:
    """Load or atomically provision a persistent node-local Fernet key.

    Headless Linux nodes commonly have the Python ``keyring`` package but no
    usable OS backend. A generated key must therefore survive process and A/B
    slot restarts. ``O_EXCL`` makes concurrent candidate boot deterministic:
    every contender either creates the one key or reloads the winner.
    """

    target = Path(path)

    def read_existing() -> bytes | None:
        if target.is_symlink():
            raise PermissionError("vault master key path must not be a symbolic link")
        try:
            value = target.read_bytes().strip()
        except FileNotFoundError:
            return None
        Fernet(value)
        try:
            target.chmod(0o600)
        except OSError:
            pass
        return value

    existing = read_existing()
    if existing is not None:
        return existing
    candidate = _environment_master_key() or generate_key()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.parent.chmod(0o700)
    except OSError:
        pass
    temporary = target.with_name(
        f".{target.name}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(candidate)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            winner = read_existing()
            if winner is None:
                raise PermissionError("vault master key provisioning race did not produce a key")
            return winner
        try:
            directory = os.open(target.parent, os.O_RDONLY)
        except OSError:
            directory = None
        if directory is not None:
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except Exception:
        raise
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return candidate


def load_or_create_master(getter, setter) -> bytes:
    """
    getter() -> bytes|None, setter(bytes) -> None
    1) читаем ключ из getter (напр., OS keyring)
    2) если нет — берём из ENV или генерируем и сохраняем через setter
    """
    k = getter()
    if k:
        return k
    env = _environment_master_key()
    if env:
        return env
    k = generate_key()
    setter(k)
    return k


def fernet_from_key(k: bytes) -> Fernet:
    return Fernet(k)
