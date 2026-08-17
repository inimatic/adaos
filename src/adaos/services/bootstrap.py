# src\adaos\services\bootstrap.py
from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import socket
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from adaos.adapters.db.sqlite_schema import ensure_schema
from adaos.adapters.scenarios.git_repo import GitScenarioRepository
from adaos.adapters.skills.git_repo import GitSkillRepository
from adaos.ports.heartbeat import HeartbeatPort
from adaos.ports.skills_loader import SkillsLoaderPort
from adaos.ports.subnet_registry import SubnetRegistryPort
from adaos.sdk.core.decorators import register_subscriptions
from adaos.sdk.data import bus
from adaos.services import yjs as _y_store  # ensure YStore subscriptions are registered
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.chat_io import telemetry as tm
from adaos.services.chat_io.interfaces import ChatOutputEvent, ChatOutputMessage
from adaos.services.chat_io.nlu_bridge import register_chat_nlu_bridge  # chat->NLU bridge
from adaos.services.eventbus import LocalEventBus
from adaos.services.io_bus.local_bus import LocalIoBus
from adaos.services.reliability import (
    configure_hub_root_transport_strategy,
    hub_root_transport_strategy_snapshot,
    record_hub_root_transport_event,
)
from adaos.services.node_config import NodeConfig, generate_provisional_subnet_id, load_config, set_role as cfg_set_role
from adaos.services.node_runtime_state import (
    load_member_hub_token,
)
from adaos.services.root.control_lifecycle_sync import report_hub_control_lifecycle_state
from adaos.services.runtime_identity import (
    runtime_transition_role,
)
from adaos.services.scheduler import start_scheduler, stop_scheduler
from adaos.services.scenario import (
    webspace_runtime as _scenario_ws_runtime,  # ensure core scenario subscriptions
)
from adaos.services.scenario import workflow_runtime as _scenario_workflow_runtime  # ensure scenario workflow subscriptions
from adaos.services import nlu as _nlu_services  # ensure NLU dispatcher subscriptions
from adaos.services import named_entity_projection as _named_entity_projection  # ensure named-entity projection subscriptions
from adaos.services import pending_actions as _pending_actions  # ensure Pending Actions subscriptions
from adaos.services.bootstrap_runtime import (
    BootstrapBootCoordinator,
    BootstrapBootOperations,
    BootstrapLifecycleCoordinator,
    BootstrapStatusWatchdogService,
    HubRouteProxyPolicy,
    NatsBridgePolicy,
    RootTransportReconnectOperations,
    RootTransportService,
)
from adaos.services.bootstrap_runtime import core_update_convergence as _core_update_convergence
from adaos.services.bootstrap_runtime import status_policy as _status_policy
from adaos.services.bootstrap_runtime.nats_root_runtime import start_nats_root_transport
from adaos.services.skill import runtime_shutdown_runtime as _runtime_shutdown_runtime  # ensure skill shutdown subscriptions
from adaos.services.skill import service_supervisor_runtime as _service_supervisor_runtime  # ensure service supervisor subscriptions
from adaos.services.skill.service_supervisor import get_service_supervisor
from adaos.integrations.telegram.sender import TelegramSender










def _ensure_managed_nlu_service_skills(log: logging.Logger) -> dict[str, Any]:
    try:
        from adaos.services.nlu.rasa_skill_installer import ensure_rasa_service_skill_installed, is_rasa_nlu_enabled

        if not is_rasa_nlu_enabled():
            return {"ok": True, "enabled": False, "installed": False}
        installed_path = ensure_rasa_service_skill_installed()
        return {
            "ok": installed_path is not None,
            "enabled": True,
            "installed": installed_path is not None,
        }
    except Exception as exc:
        log.warning("failed to ensure managed NLU service skills", exc_info=True)
        return {
            "ok": False,
            "enabled": True,
            "installed": False,
            "error_type": type(exc).__name__,
        }














































































































































class BootstrapService:
    def __init__(
        self,
        ctx: AgentContext,
        *,
        heartbeat: HeartbeatPort,
        skills_loader: SkillsLoaderPort,
        subnet_registry: SubnetRegistryPort,
    ) -> None:
        self.ctx = ctx
        self.heartbeat = heartbeat
        self.skills_loader = skills_loader
        self.subnet_registry = subnet_registry
        self._boot_coordinator = BootstrapBootCoordinator()
        self._lifecycle = BootstrapLifecycleCoordinator()
        self._nats_policy = NatsBridgePolicy()
        self._route_policy = HubRouteProxyPolicy()
        self._io_bus: Any = None
        self._log = logging.getLogger("adaos.hub-io")
        self._root_transport = RootTransportService(
            lifecycle=self._lifecycle,
            role=lambda: str(getattr(self.ctx.config, "role", "") or ""),
            candidate_passive=self._nats_policy.candidate_passive_mode,
            reconnect=lambda **kwargs: self.request_hub_root_reconnect(**kwargs),
            watchdog_interval=_status_policy._hub_root_bridge_watchdog_interval_s,
            record_event=lambda *args, **kwargs: record_hub_root_transport_event(*args, **kwargs),
            logger=self._log,
        )

    @staticmethod
    def _root_transport_reconnect_operations() -> RootTransportReconnectOperations:
        return RootTransportReconnectOperations(
            configure_strategy=configure_hub_root_transport_strategy,
            record_event=record_hub_root_transport_event,
            strategy_snapshot=hub_root_transport_strategy_snapshot,
        )

    @staticmethod
    def _boot_sequence_operations() -> BootstrapBootOperations:
        return BootstrapBootOperations(
            bus=bus,
            chat_output_event_type=ChatOutputEvent,
            chat_output_message_type=ChatOutputMessage,
            core_update_waits_for_supervisor_convergence=(
                _core_update_convergence._core_update_waits_for_supervisor_convergence
            ),
            ensure_managed_nlu_service_skills=_ensure_managed_nlu_service_skills,
            get_service_supervisor=get_service_supervisor,
            json_module=_json,
            load_config=load_config,
            local_event_bus_type=LocalEventBus,
            local_io_bus_type=LocalIoBus,
            loop_hang_watchdog_enabled_from_env=_status_policy._loop_hang_watchdog_enabled_from_env,
            register_chat_nlu_bridge=register_chat_nlu_bridge,
            register_subscriptions=register_subscriptions,
            report_hub_control_lifecycle_state=report_hub_control_lifecycle_state,
            runtime_transition_role=runtime_transition_role,
            should_emit_node_status=_status_policy._should_emit_node_status,
            should_forward_node_status_to_members=_status_policy._should_forward_node_status_to_members,
            should_forward_webio_control_to_members=_status_policy._should_forward_webio_control_to_members,
            start_nats_root_transport=start_nats_root_transport,
            start_scheduler=start_scheduler,
            status_watchdog_service=BootstrapStatusWatchdogService,
            telegram_sender_type=TelegramSender,
            telemetry=tm,
            watch_supervisor_core_update_convergence=(
                _core_update_convergence._watch_supervisor_core_update_convergence
            ),
        )

    # Compatibility facades for callers and tests that still inspect the
    # historical BootstrapService lifecycle attributes directly.
    @property
    def _boot_tasks(self) -> list[asyncio.Task[Any]]:
        return self._lifecycle.boot_tasks

    @_boot_tasks.setter
    def _boot_tasks(self, value: list[asyncio.Task[Any]]) -> None:
        self._lifecycle.boot_tasks = value

    @property
    def _boot_lock(self) -> asyncio.Lock:
        return self._lifecycle.boot_lock

    @property
    def _boot_done(self) -> asyncio.Event:
        return self._lifecycle.boot_done

    @property
    def _boot_in_progress(self) -> bool:
        return self._lifecycle.boot_in_progress

    @_boot_in_progress.setter
    def _boot_in_progress(self, value: bool) -> None:
        self._lifecycle.boot_in_progress = bool(value)

    @property
    def _ready(self) -> asyncio.Event:
        return self._lifecycle.ready

    @property
    def _booted(self) -> bool:
        return self._lifecycle.booted

    @_booted.setter
    def _booted(self, value: bool) -> None:
        self._lifecycle.booted = bool(value)

    @property
    def _app(self) -> Any:
        return self._lifecycle.app

    @_app.setter
    def _app(self, value: Any) -> None:
        self._lifecycle.app = value

    @property
    def _member_ready_callback(self) -> Callable[[], Awaitable[None]] | None:
        return self._lifecycle.member_ready_callback

    @_member_ready_callback.setter
    def _member_ready_callback(self, value: Callable[[], Awaitable[None]] | None) -> None:
        self._lifecycle.member_ready_callback = value

    @property
    def _hub_root_nc(self) -> Any:
        return self._root_transport.nats_client

    @_hub_root_nc.setter
    def _hub_root_nc(self, value: Any) -> None:
        self._root_transport.nats_client = value

    @property
    def _hub_root_route_reset(self) -> Any:
        return self._root_transport.route_reset

    @_hub_root_route_reset.setter
    def _hub_root_route_reset(self, value: Any) -> None:
        self._root_transport.route_reset = value

    @property
    def _hub_root_bridge_task_name(self) -> str:
        return self._root_transport.bridge_task_name

    @property
    def _hub_root_bridge_watchdog_task_name(self) -> str:
        return self._root_transport.bridge_watchdog_task_name

    @property
    def _hub_root_bridge_factory(self) -> Callable[[], Awaitable[Any]] | None:
        return self._root_transport.bridge_factory

    @_hub_root_bridge_factory.setter
    def _hub_root_bridge_factory(self, value: Callable[[], Awaitable[Any]] | None) -> None:
        self._root_transport.bridge_factory = value

    @property
    def _hub_root_bridge_watchdog_rearm_total(self) -> int:
        return self._root_transport.bridge_watchdog_rearm_total

    @_hub_root_bridge_watchdog_rearm_total.setter
    def _hub_root_bridge_watchdog_rearm_total(self, value: int) -> None:
        self._root_transport.bridge_watchdog_rearm_total = int(value)

    @property
    def _hub_root_authority_waiters(self) -> set[asyncio.Event]:
        return self._root_transport.authority_waiters

    @property
    def _hub_root_authority_ready_at(self) -> float | None:
        return self._root_transport.authority_ready_at

    @_hub_root_authority_ready_at.setter
    def _hub_root_authority_ready_at(self, value: float | None) -> None:
        self._root_transport.authority_ready_at = value

    def _mark_hub_root_authority_ready(self) -> None:
        """Release cutover waiters only after the active Root route subscription is flushed."""
        self._root_transport.mark_authority_ready()

    def _find_live_boot_task(self, task_name: str) -> asyncio.Task | None:
        return self._lifecycle.find_live_task(task_name)

    def _start_boot_task_once(self, task_name: str, coro_factory: Callable[[], Awaitable[Any]]) -> asyncio.Task:
        return self._lifecycle.start_task_once(task_name, coro_factory)

    def _start_hub_root_bridge_task(
        self,
        coro_factory: Callable[[], Awaitable[Any]],
        *,
        start_immediately: bool = True,
    ) -> asyncio.Task | None:
        return self._root_transport.start_bridge_task(
            coro_factory,
            start_immediately=start_immediately,
        )

    def _hub_root_bridge_required(self) -> bool:
        return self._root_transport.bridge_required()

    async def _repair_missing_hub_root_bridge(self, *, reason: str) -> dict[str, Any]:
        return await self._root_transport.repair_missing_bridge(reason=reason)

    async def _hub_root_bridge_watchdog(self) -> None:
        await self._root_transport.watchdog()

    def _ensure_hub_root_bridge_task(
        self,
        *,
        force_rearm: bool = False,
        reason: str | None = None,
    ) -> dict[str, Any]:
        return self._root_transport.ensure_bridge_task(
            force_rearm=force_rearm,
            reason=reason,
        )

    def is_ready(self) -> bool:
        return self._lifecycle.is_ready()

    async def _reset_hub_root_route_runtime(
        self,
        *,
        reason: str,
        notify_browser: bool,
    ) -> dict[str, Any]:
        return await self._root_transport.reset_route_runtime(
            reason=reason,
            notify_browser=notify_browser,
        )

    async def request_hub_root_reconnect(
        self,
        *,
        transport: str | None = None,
        url_override: str | None = None,
        wait_for_authority: bool = False,
        _reason: str = "manual_reconnect",
        _force_bridge_rearm: bool = False,
    ) -> dict[str, Any]:
        return await self._root_transport.request_reconnect(
            self._root_transport_reconnect_operations(),
            transport=transport,
            url_override=url_override,
            wait_for_authority=wait_for_authority,
            _reason=_reason,
            _force_bridge_rearm=_force_bridge_rearm,
        )

    def _member_hub_transition_snapshot(self) -> dict[str, Any]:
        try:
            from adaos.services.core_update import read_status as _read_core_update_status
        except Exception:
            _read_core_update_status = None
        try:
            from adaos.services.runtime_lifecycle import runtime_lifecycle_snapshot as _runtime_lifecycle_snapshot
        except Exception:
            _runtime_lifecycle_snapshot = None

        update_status = _read_core_update_status() if callable(_read_core_update_status) else {}
        lifecycle = _runtime_lifecycle_snapshot() if callable(_runtime_lifecycle_snapshot) else {}
        status = update_status if isinstance(update_status, dict) else {}
        runtime = lifecycle if isinstance(lifecycle, dict) else {}
        state = str(status.get("state") or "").strip().lower()
        phase = str(status.get("phase") or "").strip().lower()
        node_state = str(runtime.get("node_state") or "").strip().lower()
        lifecycle_reason = str(runtime.get("reason") or "").strip().lower()
        draining = bool(runtime.get("draining"))

        transition_state = "ready"
        reason = "none"
        recovery_blocked = False
        if state in {"preparing", "countdown", "draining", "stopping", "applying"}:
            transition_state = "paused_for_update"
            reason = state
            recovery_blocked = True
        elif state == "restarting" or phase in {"launch", "root_promoted"}:
            transition_state = "restarting"
            reason = state or phase or "restarting"
            recovery_blocked = True
        elif state == "validated" and phase == "root_promotion_pending":
            transition_state = "waiting_restart"
            reason = "root_promotion_pending"
            recovery_blocked = True
        elif draining or node_state in {"stopping", "stopped", "restarting"}:
            transition_state = "waiting_restart"
            reason = lifecycle_reason or node_state or "draining"
            recovery_blocked = True
        return {
            "transition_state": transition_state,
            "reason": reason,
            "recovery_blocked": recovery_blocked,
            "update_state": state or None,
            "update_phase": phase or None,
            "node_state": node_state or None,
            "draining": draining,
        }

    async def request_member_hub_reconnect(self, *, force: bool = False) -> dict[str, Any]:
        conf = load_config(ctx=self.ctx)
        transition = self._member_hub_transition_snapshot()
        if str(getattr(conf, "role", "") or "").strip().lower() != "member":
            return {
                "ok": False,
                "accepted": False,
                "error": "role_not_member",
                "role": str(getattr(conf, "role", "") or "").strip().lower() or None,
                "transition": transition,
            }
        hub_url = str(getattr(conf, "hub_url", "") or "").strip()
        if not hub_url:
            return {
                "ok": False,
                "accepted": False,
                "error": "hub_url_missing",
                "transition": transition,
            }
        member_hub_token = str(load_member_hub_token() or getattr(conf, "token", "") or "").strip()
        if not member_hub_token:
            return {
                "ok": False,
                "accepted": False,
                "error": "member_hub_token_missing",
                "transition": transition,
            }
        if transition.get("recovery_blocked") and not force:
            return {
                "ok": True,
                "accepted": False,
                "transition": transition,
                "reason": "transition_in_progress",
            }

        from adaos.services.subnet.link_client import get_member_link_client

        existing = self._find_live_boot_task("adaos-heartbeat")
        if existing is not None:
            existing.cancel()
            try:
                await existing
            except asyncio.CancelledError:
                pass
            except BaseException:
                pass
        try:
            await get_member_link_client().stop()
        except Exception:
            pass

        ready_callback = self._member_ready_callback if callable(self._member_ready_callback) else None
        heartbeat_task = await self._member_register_and_heartbeat(conf, on_registered=ready_callback)
        if heartbeat_task is not None:
            self._lifecycle.track_task(heartbeat_task)
        try:
            await get_member_link_client().start()
        except Exception as exc:
            return {
                "ok": False,
                "accepted": False,
                "error": f"{type(exc).__name__}: {exc}",
                "transition": transition,
            }
        return {
            "ok": True,
            "accepted": True,
            "transition": transition,
            "role": "member",
            "hub_url": hub_url,
            "started": {
                "heartbeat": heartbeat_task is not None,
                "link_client": True,
            },
        }

    async def request_member_hub_refresh(self, *, reason: str = "member_hub_refresh") -> dict[str, Any]:
        conf = load_config(ctx=self.ctx)
        transition = self._member_hub_transition_snapshot()
        if str(getattr(conf, "role", "") or "").strip().lower() != "member":
            return {
                "ok": False,
                "accepted": False,
                "error": "role_not_member",
                "role": str(getattr(conf, "role", "") or "").strip().lower() or None,
                "transition": transition,
            }
        if transition.get("recovery_blocked"):
            return {
                "ok": True,
                "accepted": False,
                "transition": transition,
                "reason": "transition_in_progress",
            }

        from adaos.services.subnet.link_client import get_member_link_client

        result = get_member_link_client().request_refresh(
            reason=str(reason or "member_hub_refresh"),
        )
        if isinstance(result, dict):
            result.setdefault("transition", transition)
            return result
        return {"ok": True, "accepted": False, "transition": transition}

    async def request_hub_root_route_reset(
        self,
        *,
        reason: str,
        notify_browser: bool = True,
    ) -> dict[str, Any]:
        return await self._reset_hub_root_route_runtime(
            reason=str(reason or "").strip() or "route_reset",
            notify_browser=bool(notify_browser),
        )

    def _prepare_environment(self) -> None:
        """
        Гарантированная подготовка окружения:
          - создаёт каталоги (skills, scenarios, state, cache, logs)
          - инициализирует схему БД (skills/scenarios)
          - при наличии URL монорепо — клонирует репозитории без установки
        """
        ctx = self.ctx

        # каталоги (учитываем, что в paths могут быть callables)
        def _resolve(x):
            return x() if callable(x) else x

        skills_root = Path(_resolve(getattr(ctx.paths, "skills_dir", "")))
        scenarios_root = Path(_resolve(getattr(ctx.paths, "scenarios_dir", "")))
        state_root = Path(_resolve(getattr(ctx.paths, "state_dir", "")))
        cache_root = Path(_resolve(getattr(ctx.paths, "cache_dir", "")))
        logs_root = Path(_resolve(getattr(ctx.paths, "logs_dir", "")))

        for p in (skills_root, scenarios_root, state_root, cache_root, logs_root):
            if p:
                p.mkdir(parents=True, exist_ok=True)

        # схема БД (единая функция, не через побочный эффект конкретного реестра)
        ensure_schema(ctx.sql)

        # в тестах — не трогаем удалённые репозитории/сеть
        if os.getenv("ADAOS_TESTING") == "1":
            return

        # Default routing rules for RouterService (stdout + telegram broadcast).
        # This file is a runtime config (often ignored by git) but must exist for
        # system notifications (subnet.started/stopped, greet_on_boot, etc).
        try:
            base_dir = getattr(ctx.paths, "base_dir", None)
            base_dir = base_dir() if callable(base_dir) else base_dir
            if base_dir:
                rules_path = Path(base_dir) / "route_rules.yaml"
                if not rules_path.exists():
                    rules_path.write_text(
                        "rules:\n"
                        "  - priority: 60\n"
                        "    match: {}\n"
                        "    target: {node_id: this, kind: io_type, io_type: stdout}\n"
                        "  - priority: 50\n"
                        "    match: {}\n"
                        "    target: {node_id: this, kind: io_type, io_type: telegram}\n",
                        encoding="utf-8",
                    )
        except Exception:
            pass

        # монорепо навыков
        try:
            if ctx.settings.skills_monorepo_url and not (skills_root / ".git").exists():
                GitSkillRepository(
                    paths=ctx.paths,
                    git=ctx.git,
                    monorepo_url=getattr(ctx.settings, "skills_monorepo_url", None),
                    monorepo_branch=getattr(ctx.settings, "skills_monorepo_branch", None),
                ).ensure()
        except Exception:
            # не блокируем бут при сбое ensure; логирование можно добавить позже
            pass

        # монорепо сценариев (поддержим оба возможных конструктора)
        try:
            if ctx.settings.scenarios_monorepo_url and not (scenarios_root / ".git").exists():
                GitScenarioRepository(
                    paths=ctx.paths,
                    git=ctx.git,
                    url=getattr(ctx.settings, "scenarios_monorepo_url", None),
                    branch=getattr(ctx.settings, "scenarios_monorepo_branch", None),
                ).ensure()

        except Exception:
            pass

    async def _member_register_and_heartbeat(
        self,
        conf: NodeConfig,
        *,
        on_registered: Callable[[], Awaitable[None]] | None = None,
    ) -> Optional[asyncio.Task]:
        hub_url = str(conf.hub_url or "").strip()
        if not hub_url:
            await bus.emit("net.subnet.register.error", {"status": "hub_url_missing"}, source="lifecycle", actor="system")
            return None
        member_hub_token = str(load_member_hub_token() or conf.token or "").strip()
        register_retry_s = _status_policy._bounded_interval_seconds(
            os.getenv("ADAOS_MEMBER_REGISTER_RETRY_INITIAL_S", "1"),
            default=1.0,
            minimum=0.05,
        )

        async def _try_register() -> bool:
            try:
                ok = await self.heartbeat.register(
                    hub_url,
                    member_hub_token,
                    node_id=conf.node_id,
                    subnet_id=conf.subnet_id,
                    hostname=socket.gethostname(),
                    roles=["member"],
                )
            except Exception as exc:
                await bus.emit("net.subnet.register.error", {"error": str(exc)}, source="lifecycle", actor="system")
                return False
            if not ok:
                await bus.emit("net.subnet.register.error", {"status": "non-200"}, source="lifecycle", actor="system")
                return False
            await bus.emit("net.subnet.registered", {"hub": conf.hub_url}, source="lifecycle", actor="system")
            return True

        async def loop() -> None:
            registered = False
            registered_notified = False
            backoff = register_retry_s
            while True:
                if not registered:
                    registered = await _try_register()
                    if not registered:
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 30)
                        continue
                    backoff = 1
                    if on_registered is not None and not registered_notified:
                        registered_notified = True
                        await on_registered()
                try:
                    ok_hb = await self.heartbeat.heartbeat(
                        hub_url,
                        member_hub_token,
                        node_id=conf.node_id,
                        base_url=str(os.getenv("ADAOS_SELF_BASE_URL") or "").strip() or None,
                    )
                    if ok_hb:
                        backoff = 1
                    else:
                        await bus.emit("net.subnet.heartbeat.warn", {"status": "non-200"}, source="lifecycle", actor="system")
                        backoff = min(backoff * 2, 30)
                except Exception as e:
                    await bus.emit("net.subnet.heartbeat.error", {"error": str(e)}, source="lifecycle", actor="system")
                    backoff = min(backoff * 2, 30)
                await asyncio.sleep(backoff if backoff > 1 else 5)

        return asyncio.create_task(loop(), name="adaos-heartbeat")

    async def run_boot_sequence(self, app: Any) -> None:
        await self._lifecycle.run_once(app, self._run_boot_sequence_impl)

    async def _run_boot_sequence_impl(self, app: Any) -> None:
        await self._boot_coordinator.run(
            self,
            self._boot_sequence_operations(),
            app,
        )

    async def shutdown(self) -> None:
        await bus.emit("sys.stopping", {}, source="lifecycle", actor="system")
        try:
            conf = getattr(self.ctx, "config", None) or load_config(ctx=self.ctx)
            if getattr(conf, "role", None) == "hub":
                await asyncio.to_thread(report_hub_control_lifecycle_state, conf)
        except Exception:
            self._log.debug("control lifecycle report failed trigger=sys.stopping", exc_info=True)
        try:
            await get_service_supervisor().shutdown()
        except Exception as e:
            self._log.debug("service supervisor shutdown failed", exc_info=True)
            pass
        try:
            await stop_scheduler()
        except Exception:
            pass
        await self._lifecycle.stop()
        status_watchdog = getattr(self, "_status_watchdog", None)
        if status_watchdog is not None:
            try:
                status_watchdog.close()
            except Exception:
                self._log.debug("status watchdog shutdown failed", exc_info=True)
        await bus.emit("sys.stopped", {}, source="lifecycle", actor="system")

    async def switch_role(self, app: Any, role: str, *, hub_url: str | None = None, subnet_id: str | None = None) -> NodeConfig:
        prev = getattr(self.ctx, "config", None) or load_config(ctx=self.ctx)
        await self.shutdown()
        if prev.role == "member" and role.lower().strip() == "hub" and prev.hub_url:
            try:
                await self.heartbeat.deregister(prev.hub_url, prev.token or "", node_id=prev.node_id)
            except Exception:
                pass
            subnet_id = subnet_id or generate_provisional_subnet_id()
        conf = cfg_set_role(role, hub_url=hub_url, subnet_id=subnet_id, ctx=self.ctx)
        if str(role or "").strip().lower() == "hub":
            self._last_role_switch_root_init = self._ensure_hub_root_bootstrap_for_role_switch(conf)
            if not bool(self._last_role_switch_root_init.get("ok")):
                raise RuntimeError(str(self._last_role_switch_root_init.get("error") or "hub Root bootstrap failed"))
            conf = load_config(ctx=self.ctx)
        else:
            self._last_role_switch_root_init = {"attempted": False, "reason": "role_not_hub"}
        await self.run_boot_sequence(app or self._app)
        return conf

    def _ensure_hub_root_bootstrap_for_role_switch(self, conf: NodeConfig) -> dict[str, Any]:
        """
        Ensure a switched hub has Root-issued mTLS material before booting.

        `node role switch --role hub` can be called from a member runtime. In that
        path the config role changes immediately, but the hub still needs
        Root-issued hub cert/key/CA before the hub-root NATS tunnel can obtain
        credentials and become visible to routed browsers.
        """
        if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
            return {"attempted": False, "reason": "role_not_hub"}
        if str(os.getenv("ADAOS_ROLE_SWITCH_HUB_ROOT_INIT", "1") or "1").strip().lower() in {"0", "false", "no", "off"}:
            return {"attempted": False, "ok": True, "reason": "disabled_by_env"}
        try:
            from adaos.services.root.service import RootDeveloperService

            result = RootDeveloperService().init(preferred_subnet_id=str(getattr(conf, "subnet_id", "") or "").strip() or None)
            return {
                "attempted": True,
                "ok": True,
                "subnet_id": result.subnet_id,
                "reused": bool(result.reused),
                "hub_key_path": str(result.hub_key_path),
                "hub_cert_path": str(result.hub_cert_path),
                "ca_cert_path": str(result.ca_cert_path) if result.ca_cert_path else None,
                "workspace_path": str(result.workspace_path),
            }
        except Exception as exc:
            return {
                "attempted": True,
                "ok": False,
                "subnet_id": str(getattr(conf, "subnet_id", "") or "") or None,
                "error": f"{type(exc).__name__}: {exc}",
            }


# --- модульные фасады (синглтон) ---
from adaos.services.heartbeat_requests import RequestsHeartbeat
from adaos.services.skills_loader_importlib import ImportlibSkillsLoader
from adaos.services.subnet_registry_mem import get_subnet_registry

_SERVICE: BootstrapService | None = None


def _svc() -> BootstrapService:
    global _SERVICE
    if _SERVICE is None:
        ctx = get_ctx()
        _SERVICE = BootstrapService(ctx, heartbeat=RequestsHeartbeat(), skills_loader=ImportlibSkillsLoader(), subnet_registry=get_subnet_registry())
    return _SERVICE


def is_ready() -> bool:
    return _svc().is_ready()


def control_report_runtime_snapshot() -> dict[str, Any]:
    service = _SERVICE
    watchdog = getattr(service, "_status_watchdog", None) if service is not None else None
    if watchdog is None or not hasattr(watchdog, "control_report_snapshot"):
        return {
            "available": False,
            "in_flight": False,
            "executor": "not_started",
            "ordered": True,
        }
    return {
        "available": True,
        **watchdog.control_report_snapshot(),
    }


async def request_hub_root_reconnect(
    *,
    transport: str | None = None,
    url_override: str | None = None,
    wait_for_authority: bool = False,
) -> dict[str, Any]:
    return await _svc().request_hub_root_reconnect(
        transport=transport,
        url_override=url_override,
        wait_for_authority=bool(wait_for_authority),
    )


async def request_member_hub_reconnect(*, force: bool = False) -> dict[str, Any]:
    return await _svc().request_member_hub_reconnect(force=bool(force))


async def request_member_hub_refresh(*, reason: str = "member_hub_refresh") -> dict[str, Any]:
    return await _svc().request_member_hub_refresh(reason=str(reason or "member_hub_refresh"))


async def request_hub_root_route_reset(*, reason: str, notify_browser: bool = True) -> dict[str, Any]:
    return await _svc().request_hub_root_route_reset(
        reason=str(reason or "").strip() or "route_reset",
        notify_browser=bool(notify_browser),
    )


async def run_boot_sequence(app: Any) -> None:
    await _svc().run_boot_sequence(app)


async def shutdown() -> None:
    await _svc().shutdown()


async def switch_role(app: Any, role: str, *, hub_url: str | None = None, subnet_id: str | None = None) -> NodeConfig:
    return await _svc().switch_role(app, role, hub_url=hub_url, subnet_id=subnet_id)
