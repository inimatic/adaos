# src/adaos/apps/bootstrap.py
from __future__ import annotations
import os
import sys
from typing import Optional
from threading import RLock

from adaos.services.settings import Settings
from adaos.services.agent_context import AgentContext
from adaos.adapters.fs.path_provider import PathProvider
from adaos.services.eventbus import LocalEventBus
from adaos.services.execution.local import LocalProcessExecutor
from adaos.services.logging import setup_logging, attach_event_logger
from adaos.adapters.git.cli_git import CliGitClient
from adaos.adapters.db import SQLite, SQLiteKV
from adaos.services.runtime import AsyncProcessManager
from adaos.services.policy.capabilities import InMemoryCapabilities
from adaos.services.policy.net import NetPolicy
from adaos.adapters.git.secure_git import SecureGitClient
from adaos.services.policy.fs import SimpleFSPolicy
from adaos.adapters.secrets.keyring_vault import KeyringVault
from adaos.adapters.secrets.file_vault import FileVault
from adaos.services.crypto.secrets_service import SecretsService
from adaos.services.crypto.vault import (
    load_or_create_local_master,
    local_master_key_path,
)
from adaos.services.sandbox.runner import ProcSandbox
from adaos.services.sandbox.service import SandboxService
from adaos.services.storage import build_default_relational_storage_broker
from adaos.services.provider_status import build_provider_status_registry

from adaos.services.agent_context import set_ctx
from adaos.services.node_config import load_config


def _credential_vault_mode() -> str:
    mode = str(os.getenv("ADAOS_CREDENTIAL_VAULT_BACKEND") or "auto").strip().lower()
    if mode not in {"auto", "keyring", "file"}:
        raise ValueError("ADAOS_CREDENTIAL_VAULT_BACKEND must be auto, keyring, or file")
    return mode


def _usable_os_keyring(*, mode: str) -> bool:
    if mode == "file":
        return False
    # Importing/probing backend plugins takes several seconds on a headless
    # Linux host and resolves to keyring.backends.fail.Keyring. In auto mode
    # avoid that critical-path work unless a desktop Secret Service session is
    # actually present. Operators may still request an explicit probe.
    if (
        mode == "auto"
        and sys.platform.startswith("linux")
        and not os.getenv("DBUS_SESSION_BUS_ADDRESS")
    ):
        return False
    try:
        import keyring

        backend = keyring.get_keyring()
        return float(getattr(backend, "priority", 0) or 0) > 0
    except Exception:
        return False


def _build_credential_vault(*, paths: PathProvider, profile: str, kv: SQLiteKV):
    mode = _credential_vault_mode()
    if _usable_os_keyring(mode=mode):
        return KeyringVault(profile=profile, kv=kv)
    if mode == "keyring":
        raise RuntimeError("requested OS credential keyring is unavailable")
    master = load_or_create_local_master(local_master_key_path(paths.base_dir(), profile))
    return FileVault(
        base_dir=paths.base,
        fs=None,
        key_get=lambda: master,
        key_set=lambda _value: None,
    )


class _CtxHolder:
    _ctx: Optional[AgentContext] = None
    _lock = RLock()

    @classmethod
    def get(cls) -> AgentContext:
        with cls._lock:
            if cls._ctx is None:
                cls._ctx = cls._build(Settings.from_sources())
                # публикуем в ContextVar
                set_ctx(cls._ctx)
            return cls._ctx

    @classmethod
    def init(cls, settings: Optional[Settings] = None) -> AgentContext:
        with cls._lock:
            cls._ctx = cls._build(settings or Settings.from_sources())
            set_ctx(cls._ctx)  # публикуем
            return cls._ctx

    @classmethod
    def reload(cls, **overrides) -> AgentContext:
        """Immutable reload (singleton): create new Settings and rebuild context."""
        with cls._lock:
            old = cls._ctx or cls._build(Settings.from_sources())
            new_settings = old.settings.with_overrides(**overrides)
            cls._ctx = cls._build(new_settings)
            set_ctx(cls._ctx)  # публикуем
            return cls._ctx

    @staticmethod
    def _build(settings: Settings) -> AgentContext:
        paths = PathProvider(settings)
        paths.ensure_tree()

        fs = SimpleFSPolicy()
        for root in (
            paths.base_dir(),
            paths.skills_dir(),
            paths.scenarios_dir(),
            paths.logs_dir(),
            paths.cache_dir(),
            paths.state_dir(),
            paths.tmp_dir(),
        ):
            fs.allow_root(root)

        # Ensure the Yjs stores root exists (Stage A1).
        try:
            (paths.state_dir() / "ystores").mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        bus = LocalEventBus()
        root_logger = setup_logging(paths)
        attach_event_logger(bus, root_logger.getChild("events"))

        # policies
        caps = InMemoryCapabilities()
        net = NetPolicy()

        # allow host(s) из монореп
        def _allow_host(url: str | None):
            if not url:
                return
            from urllib.parse import urlparse

            host = urlparse(url).hostname
            if not host and "@" in url and ":" in url:
                host = url.split("@", 1)[1].split(":", 1)[0]  # ssh git
            if host:
                net.allow(host)

        _allow_host(settings.skills_monorepo_url)
        _allow_host(settings.scenarios_monorepo_url)

        # базовые capabilities
        caps.grant(
            "core",
            "proc.run",
            "net.git",
            "git.write",
            "skills.manage",
            "scenarios.manage",
            "secrets.read",
            "secrets.write",
        )

        # Git с защитой
        git_base = CliGitClient(depth=1)
        git = SecureGitClient(git_base, net)

        proc = AsyncProcessManager(bus=bus)
        sql = SQLite(paths)
        kv = SQLiteKV(sql, namespace="adaos")

        # Desktop OS keyring where one is actually available; encrypted local
        # FileVault with a persistent node-private master key on headless nodes.
        secrets_backend = _build_credential_vault(
            paths=paths,
            profile=settings.profile,
            kv=kv,
        )

        secrets = SecretsService(secrets_backend, caps)

        # если backend FileVault — подставим fs
        if isinstance(secrets_backend, FileVault):
            secrets_backend.fs = fs

        relational_storage = build_default_relational_storage_broker()
        execution_provider = LocalProcessExecutor(
            state_root=paths.state_dir(),
            allowed_roots=(paths.base_dir(), paths.skills_dir(), paths.state_dir(), paths.models_dir()),
        )
        ctx = AgentContext(
            settings=settings,
            paths=paths,
            bus=bus,
            proc=proc,
            caps=caps,
            devices=object(),
            kv=kv,
            sql=sql,
            secrets=secrets,
            credential_vault=secrets_backend,
            net=net,
            updates=object(),
            git=git,
            fs=fs,
            sandbox=SandboxService(runner=ProcSandbox(fs_base=paths.base), caps=caps, bus=bus),
            relational_storage=relational_storage,
            execution_provider=execution_provider,
            provider_status=build_provider_status_registry(
                relational_broker=relational_storage,
                executors=(execution_provider,),
            ),
        )

        # чтобы в адаптерах было paths.ctx.fs (если Paths это позволяет)
        if getattr(paths, "ctx", None) is None:
            try:
                paths.ctx = ctx
            except Exception:
                pass

        # NodeConfig path migration uses the process context. At this point the
        # context is complete, so publish it before loading node.yaml.
        set_ctx(ctx)

        # Attach NodeConfig once; consumers should use ctx.config instead of calling load_config repeatedly
        try:
            object.__setattr__(ctx, "config", load_config(ctx=ctx))
        except Exception:
            pass
        try:
            ctx.status_registry
        except Exception:
            pass
        return ctx


# ── публичные функции (удобные фасады) ─────────────────────────────────────────


def get_ctx() -> AgentContext:
    """Shim: проксируем на services.agent_context.get_ctx()."""
    from adaos.services.agent_context import get_ctx as _get

    return _get()


def init_ctx(settings: Optional[Settings] = None) -> AgentContext:
    """Явная инициализация приложения и публикация контекста."""
    return _CtxHolder.init(settings)


def reload_ctx(**overrides) -> AgentContext:
    """Пересборка с overrides и публикация контекста."""
    return _CtxHolder.reload(**overrides)
