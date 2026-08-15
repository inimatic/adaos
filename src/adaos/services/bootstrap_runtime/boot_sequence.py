from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class BootstrapBootOperations:
    bus: Any
    chat_output_event_type: Any
    chat_output_message_type: Any
    core_update_waits_for_supervisor_convergence: Any
    ensure_managed_nlu_service_skills: Any
    get_service_supervisor: Any
    json_module: Any
    load_config: Any
    local_event_bus_type: Any
    local_io_bus_type: Any
    loop_hang_watchdog_enabled_from_env: Any
    register_chat_nlu_bridge: Any
    register_subscriptions: Any
    report_hub_control_lifecycle_state: Any
    runtime_transition_role: Any
    should_emit_node_status: Any
    should_forward_node_status_to_members: Any
    should_forward_webio_control_to_members: Any
    start_nats_root_transport: Any
    start_scheduler: Any
    status_watchdog_service: Any
    telegram_sender_type: Any
    telemetry: Any
    watch_supervisor_core_update_convergence: Any


class BootstrapBootCoordinator:
    @staticmethod
    def _member_runtime_owns_upstream(operations: BootstrapBootOperations) -> bool:
        resolver = getattr(operations, "runtime_transition_role", None)
        try:
            role = str(resolver() if callable(resolver) else "active").strip().lower()
        except Exception:
            role = "active"
        return role != "candidate"

    async def run(
        self,
        service: Any,
        operations: BootstrapBootOperations,
        app: Any,
    ) -> None:
        if service._booted:
            return
        service._lifecycle.bind_app(app)
        # Unified deep-trace switch for WS/NATS/route debugging.
        try:
            if os.getenv("HUB_TRACE", "0") == "1":
                for k in (
                    "HUB_NATS_TRACE",
                    "HUB_NATS_VERBOSE",
                    "HUB_NATS_WS_TRACE",
                    "HUB_NATS_WIRETAP",
                    "HUB_NATS_WS_PATCH_AIOHTTP",
                    "HUB_ROUTE_TRACE",
                    "HUB_ROUTE_FRAME_VERBOSE",
                    "HUB_ROUTE_TX_VERBOSE",
                    "HUB_ROUTE_DIAG",
                    "HUB_WS_TRACE",
                    "HUB_ROOT_LOG_SNAPSHOT",
                    "HUB_ROOT_LOG_SNAPSHOT_EXTRACT_PRINT",
                ):
                    os.environ.setdefault(k, "1")
                os.environ.setdefault("HUB_NATS_WIRETAP_MAX_BYTES", "200")
                os.environ.setdefault("HUB_ROOT_LOG_SNAPSHOT_LINES", "2000")
                os.environ.setdefault("ADAOS_LOOP_LAG_MONITOR", "1")
                try:
                    print("[hub-io] HUB_TRACE=1 -> enabling deep WS/NATS/route tracing")
                except Exception:
                    pass
        except Exception:
            pass
        conf = getattr(service.ctx, "config", None) or operations.load_config(ctx=service.ctx)
        candidate_runtime_mode = bool(service._nats_policy.runtime_candidate_mode())
        async def _run_release_validation_autorun(trigger: str) -> None:
            try:
                from adaos.services.release_validation_autorun import (
                    autonomous_release_validation_delay_s,
                    run_autonomous_release_validation,
                )

                await asyncio.sleep(autonomous_release_validation_delay_s())
                report = await asyncio.to_thread(
                    run_autonomous_release_validation,
                    conf,
                    trigger=trigger,
                )
                if not isinstance(report, dict):
                    return
                await operations.bus.emit(
                    "release_validation.autonomous.finished",
                    report,
                    source="release_validation.autorun",
                    actor="system",
                )
                state = str(report.get("state") or "unknown").upper()
                await operations.bus.emit(
                    "ui.notify",
                    {
                        "text": (
                            f"AdaOS autonomous validation {state}: "
                            f"{report.get('build_identity') or 'unknown build'}\n"
                            f"{report.get('reason') or 'no result reason'}"
                        ),
                        "_meta": {
                            "source": "release_validation.autorun",
                            "report_id": report.get("report_id"),
                            "severity": "info" if report.get("state") == "passed" else "critical",
                        },
                    },
                    source="release_validation.autorun",
                    actor="system",
                )
                if str(getattr(conf, "role", "") or "").strip().lower() == "hub":
                    from adaos.services.root.core_update_sync import report_hub_core_update_state

                    await asyncio.to_thread(report_hub_core_update_state, conf)
            except Exception:
                service._log.warning("autonomous release validation failed trigger=%s", trigger, exc_info=True)

        def _schedule_release_validation_autorun(trigger: str) -> None:
            try:
                from adaos.services.release_validation_autorun import autonomous_release_validation_enabled

                if autonomous_release_validation_enabled():
                    service._start_boot_task_once(
                        "adaos-release-validation-autorun",
                        lambda: _run_release_validation_autorun(trigger),
                    )
            except Exception:
                service._log.debug("failed to schedule autonomous release validation", exc_info=True)

        try:
            from adaos.services.system_model.service import (
                current_node_status_push_payload as _current_node_status_push_payload,
                node_status_push_heartbeat_s as _node_status_push_heartbeat_s,
            )
        except Exception:
            _current_node_status_push_payload = None
            _node_status_push_heartbeat_s = None

        service._status_watchdog = operations.status_watchdog_service.from_environment(
            config=conf,
            logger=service._log,
            report_control=lambda config: operations.report_hub_control_lifecycle_state(config),
            node_status_payload=_current_node_status_push_payload,
            node_status_heartbeat_s=(
                _node_status_push_heartbeat_s() if callable(_node_status_push_heartbeat_s) else None
            ),
            should_emit_node_status=lambda **kwargs: operations.should_emit_node_status(**kwargs),
            emit_event=lambda *args, **kwargs: operations.bus.emit(*args, **kwargs),
        )
        _report_control_lifecycle = service._status_watchdog.report_control_lifecycle
        _emit_node_status = service._status_watchdog.emit_node_status

        service._prepare_environment()
        # local adapter over LocalEventBus
        core_bus = service.ctx.bus if isinstance(service.ctx.bus, operations.local_event_bus_type) else operations.local_event_bus_type()
        io_bus: Any = operations.local_io_bus_type(core=core_bus)
        await io_bus.connect()
        print("[bootstrap] IO bus: LocalEventBus")
        service._io_bus = io_bus
        # Attach chat IO -> NLU bridge (e.g. Telegram text -> nlp.intent.detect.request)
        try:
            operations.register_chat_nlu_bridge(core_bus)
        except Exception:
            service._log.warning("failed to register chat_io NLU bridge", exc_info=True)
        # expose in app.state
        try:
            setattr(app.state, "bus", io_bus)
        except Exception:
            pass
        await operations.bus.emit("sys.boot.start", {"role": conf.role, "node_id": conf.node_id, "subnet_id": conf.subnet_id}, source="lifecycle", actor="system")
        if not candidate_runtime_mode:
            await asyncio.to_thread(operations.ensure_managed_nlu_service_skills, service._log)
        await service.skills_loader.import_all_handlers(service.ctx.paths.skills_dir())
        # Start service-type skills (external processes).
        if candidate_runtime_mode:
            service._log.info("skipping service skill startup for candidate runtime prewarm")
        else:
            try:
                await operations.get_service_supervisor().start_all()
            except Exception:
                service._log.warning("failed to start service skills", exc_info=True)
        await operations.register_subscriptions()
        if str(getattr(conf, "role", "") or "").strip().lower() == "hub":
            try:
                from adaos.services.subnet.link_manager import get_hub_link_manager as _get_hub_link_manager

                def _forward_core_update_status_to_members(ev: Event) -> None:
                    payload = ev.payload if isinstance(ev.payload, dict) else {}
                    try:
                        asyncio.get_running_loop().create_task(
                            _get_hub_link_manager().broadcast_event(
                                event_type="core.update.status",
                                payload=payload,
                                source=str(ev.source or "hub"),
                            )
                        )
                    except Exception:
                        service._log.debug("failed to mirror core.update.status to members", exc_info=True)

                def _forward_supervisor_update_status_raw_to_members(ev: Event) -> None:
                    payload = ev.payload if isinstance(ev.payload, dict) else {}
                    try:
                        asyncio.get_running_loop().create_task(
                            _get_hub_link_manager().broadcast_event(
                                event_type="supervisor.update.status.raw",
                                payload=payload,
                                source=str(ev.source or "hub"),
                            )
                        )
                    except Exception:
                        service._log.debug("failed to mirror supervisor.update.status.raw to members", exc_info=True)

                def _forward_node_status_to_members(ev: Event) -> None:
                    payload = ev.payload if isinstance(ev.payload, dict) else {}
                    if not operations.should_forward_node_status_to_members(payload):
                        return
                    try:
                        asyncio.get_running_loop().create_task(
                            _get_hub_link_manager().broadcast_event(
                                event_type="node.status",
                                payload=payload,
                                source=str(ev.source or "hub"),
                            )
                        )
                    except Exception:
                        service._log.debug("failed to mirror node.status to members", exc_info=True)

                def _forward_desktop_reload_to_members(ev: Event) -> None:
                    payload = ev.payload if isinstance(ev.payload, dict) else {}
                    try:
                        asyncio.get_running_loop().create_task(
                            _get_hub_link_manager().broadcast_event(
                                event_type=str(ev.type or "desktop.webspace.reload"),
                                payload=payload,
                                source=str(ev.source or "hub"),
                            )
                        )
                    except Exception:
                        service._log.debug("failed to mirror desktop reload event=%s to members", str(ev.type or ""), exc_info=True)

                def _forward_webio_stream_control_to_members(ev: Event) -> None:
                    payload = ev.payload if isinstance(ev.payload, dict) else {}
                    if not operations.should_forward_webio_control_to_members(payload):
                        return
                    try:
                        asyncio.get_running_loop().create_task(
                            _get_hub_link_manager().broadcast_event(
                                event_type=str(ev.type or ""),
                                payload=payload,
                                source=str(ev.source or "hub"),
                            )
                        )
                    except Exception:
                        service._log.debug("failed to mirror webio stream control event=%s to members", str(ev.type or ""), exc_info=True)

                def _forward_targeted_event_to_members(ev: Event) -> None:
                    event_type = str(ev.type or "").strip()
                    if not event_type or event_type.startswith("desktop."):
                        return
                    if event_type in {
                        "webio.stream.snapshot.requested",
                        "webio.stream.subscription.changed",
                        "webio.yjs.snapshot.requested",
                        "webio.yjs.subscription.changed",
                    }:
                        return
                    payload = ev.payload if isinstance(ev.payload, dict) else {}
                    meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
                    if bool(meta.get("subnet_origin_node_id")) or bool(meta.get("subnet_hub_mirrored")):
                        return
                    target_node_id = str(
                        payload.get("target_node_id")
                        or payload.get("node_target_id")
                        or meta.get("target_node_id")
                        or meta.get("node_target_id")
                        or ""
                    ).strip()
                    if not target_node_id or target_node_id == str(getattr(conf, "node_id", "") or "").strip():
                        return
                    try:
                        asyncio.get_running_loop().create_task(
                            _get_hub_link_manager().broadcast_event(
                                event_type=event_type,
                                payload=payload,
                                source=str(ev.source or "hub"),
                            )
                        )
                    except Exception:
                        service._log.debug("failed to mirror node-targeted event=%s to members", event_type, exc_info=True)

                core_bus.subscribe("core.update.status", _forward_core_update_status_to_members)
                core_bus.subscribe("supervisor.update.status.raw", _forward_supervisor_update_status_raw_to_members)
                core_bus.subscribe("node.status", _forward_node_status_to_members)
                core_bus.subscribe("desktop.webspace.reload", _forward_desktop_reload_to_members)
                core_bus.subscribe("desktop.webspace.reloaded", _forward_desktop_reload_to_members)
                core_bus.subscribe("desktop.webspace.reset", _forward_desktop_reload_to_members)
                core_bus.subscribe("webio.stream.snapshot.requested", _forward_webio_stream_control_to_members)
                core_bus.subscribe("webio.stream.subscription.changed", _forward_webio_stream_control_to_members)
                core_bus.subscribe("webio.yjs.snapshot.requested", _forward_webio_stream_control_to_members)
                core_bus.subscribe("webio.yjs.subscription.changed", _forward_webio_stream_control_to_members)
                core_bus.subscribe("*", _forward_targeted_event_to_members)
            except Exception:
                service._log.debug(
                    "failed to install member status forwarders",
                    exc_info=True,
                )
        try:
            from adaos.services.core_update import (
                finalize_runtime_boot_status_async as _finalize_runtime_boot_status,
                read_public_update_status as _read_public_update_status,
                read_status as _read_core_update_status,
            )

            initial_core_update_status, initial_public_update_status = await asyncio.gather(
                asyncio.to_thread(_read_core_update_status),
                asyncio.to_thread(_read_public_update_status),
            )
            await operations.bus.emit(
                "core.update.status",
                initial_core_update_status,
                source="lifecycle",
                actor="system",
            )
            await operations.bus.emit(
                "supervisor.update.status.raw",
                initial_public_update_status,
                source="lifecycle",
                actor="system",
            )
            if operations.core_update_waits_for_supervisor_convergence(initial_core_update_status):
                service._start_boot_task_once(
                    "adaos-core-update-supervisor-convergence",
                    lambda: operations.watch_supervisor_core_update_convergence(
                        operations.bus,
                        read_status=_read_core_update_status,
                        initial_status=initial_core_update_status,
                    ),
                )
        except Exception:
            _finalize_runtime_boot_status = None
            service._log.debug("failed to emit initial core.update.status", exc_info=True)
        await _emit_node_status("boot")
        await operations.bus.emit("sys.bus.ready", {}, source="lifecycle", actor="system")
        # Start in-process scheduler after the bus is ready.
        try:
            await operations.start_scheduler()
        except Exception:
            service._log.warning("failed to start scheduler", exc_info=True)

        # Optional: monitor asyncio event loop lag to catch blocking handlers (which can manifest as
        # WebSocket stalls/timeouts and cascading disconnects).
        try:
            if os.getenv("ADAOS_LOOP_LAG_MONITOR", "0") == "1":
                try:
                    interval_s = float(os.getenv("ADAOS_LOOP_LAG_INTERVAL_S", "0.5") or "0.5")
                except Exception:
                    interval_s = 0.5
                if interval_s < 0.05:
                    interval_s = 0.05
                try:
                    # Keep normal runs readable: sub-second drift is useful for
                    # targeted diagnostics, but too noisy under browser attach.
                    warn_ms = float(os.getenv("ADAOS_LOOP_LAG_WARN_MS", "1000") or "1000")
                except Exception:
                    warn_ms = 1000.0
                try:
                    dump_ms = float(os.getenv("ADAOS_LOOP_LAG_DUMP_MS", "2000") or "2000")
                except Exception:
                    dump_ms = 2000.0
                try:
                    dump_top = int(os.getenv("ADAOS_LOOP_LAG_DUMP_TOP", "10") or "10")
                except Exception:
                    dump_top = 10
                if dump_top < 1:
                    dump_top = 1
                if dump_top > 50:
                    dump_top = 50

                async def _loop_lag_monitor() -> None:
                    # Measure *per-interval* overshoot (do not accumulate drift), so we can distinguish
                    # a single stall from a slow-but-steady loop.
                    last_tick = time.monotonic()
                    last_log = 0.0
                    last_dump = 0.0
                    while True:
                        await asyncio.sleep(interval_s)
                        now = time.monotonic()
                        drift_s = (now - last_tick) - interval_s
                        last_tick = now
                        if drift_s < 0:
                            drift_s = 0.0
                        drift_ms = drift_s * 1000.0
                        if drift_ms >= warn_ms:
                            try:
                                # Local rate-limit (do not depend on hub-io _rl_log).
                                if now - last_log >= 1.0:
                                    last_log = now
                                    msg = (
                                        f"[diag] event loop lag {drift_ms:.0f}ms (interval={interval_s:.2f}s warn={warn_ms:.0f}ms dump={dump_ms:.0f}ms)"
                                    )
                                    print(msg)
                                    diag_log.warning(
                                        "event loop lag drift_ms=%.0f interval_s=%.2f warn_ms=%.0f dump_ms=%.0f",
                                        drift_ms,
                                        interval_s,
                                        warn_ms,
                                        dump_ms,
                                    )
                            except Exception:
                                pass
                        if drift_ms >= dump_ms and (now - last_dump) >= max(5.0, interval_s):
                            last_dump = now
                            try:
                                tasks = list(asyncio.all_tasks())
                                # Keep deterministic ordering for repeated dumps.
                                tasks.sort(key=lambda t: (0 if t is asyncio.current_task() else 1, t.get_name()))
                                lines: list[str] = []
                                for t in tasks[:dump_top]:
                                    frames = None
                                    top = None
                                    try:
                                        frames = t.get_stack(limit=1)
                                        top = frames[-1] if frames else None
                                        loc = None
                                        if top is not None:
                                            try:
                                                loc = f"{top.f_code.co_filename}:{top.f_lineno}"
                                            except Exception:
                                                loc = None
                                        lines.append(f"- task={t.get_name()} done={t.done()} cancelled={t.cancelled()} at={loc}")
                                    except Exception:
                                        continue
                                    finally:
                                        # Do not keep frame objects in the lag
                                        # monitor coroutine. Frames can retain
                                        # y_py locals and later release them from
                                        # an unrelated thread during GC.
                                        del top
                                        del frames
                                del tasks
                                try:
                                    backlog_fn = getattr(core_bus, "backlog_snapshot", None)
                                    backlog = backlog_fn() if callable(backlog_fn) else {}
                                    active_bounded = (
                                        backlog.get("top_active_bounded_handlers")
                                        if isinstance(backlog, dict)
                                        else None
                                    )
                                    if isinstance(active_bounded, list):
                                        for item in active_bounded[:dump_top]:
                                            if not isinstance(item, dict):
                                                continue
                                            lines.append(
                                                "- eventbus.active_bounded "
                                                f"type={item.get('event_type')} "
                                                f"handler={item.get('handler')} "
                                                f"receiver={item.get('receiver')} "
                                                f"webspace={item.get('webspace_id')} "
                                                f"age={item.get('age_s')}s"
                                            )
                                    active_tasks = backlog.get("top_active_tasks") if isinstance(backlog, dict) else None
                                    if isinstance(active_tasks, list):
                                        for item in active_tasks[:dump_top]:
                                            if not isinstance(item, dict):
                                                continue
                                            lines.append(
                                                "- eventbus.active_task "
                                                f"type={item.get('event_type')} "
                                                f"handler={item.get('handler')} "
                                                f"age={item.get('age_s')}s"
                                            )
                                except Exception:
                                    pass
                                try:
                                    from adaos.services.yjs.doc import (
                                        live_room_command_diagnostics_snapshot,
                                    )

                                    command_diag = live_room_command_diagnostics_snapshot()
                                    last_command = command_diag.get("last_result")
                                    if isinstance(last_command, dict):
                                        lines.append(
                                            "- yjs.live_room_command "
                                            f"source={last_command.get('source')} "
                                            f"webspace={last_command.get('webspace_id')} "
                                            f"reason={last_command.get('reason')} "
                                            f"handoff={last_command.get('handoff')} "
                                            f"queue={last_command.get('queue_ms')}ms "
                                            f"apply={last_command.get('apply_ms')}ms "
                                            f"bytes={last_command.get('update_bytes')}"
                                        )
                                except Exception:
                                    pass
                                try:
                                    from adaos.services.named_entity_projection import (
                                        named_entity_projection_diagnostics_snapshot,
                                    )

                                    projection_diag = named_entity_projection_diagnostics_snapshot()
                                    last_timings = projection_diag.get("last_timings_ms")
                                    lines.append(
                                        "- named_entities.projection "
                                        f"webspace={projection_diag.get('last_webspace_id')} "
                                        f"outcome={projection_diag.get('last_outcome')} "
                                        f"payload={projection_diag.get('last_payload_bytes')}B "
                                        f"timings={last_timings}"
                                    )
                                except Exception:
                                    pass
                                if lines:
                                    dump = "\n".join(lines)
                                    print("[diag] loop lag dump:\n" + dump)
                                    diag_log.warning("event loop lag dump\n%s", dump)
                            except Exception:
                                pass

                service._start_boot_task_once("adaos-loop-lag-monitor", _loop_lag_monitor)
        except Exception:
            pass

        # Optional: hang watchdog (thread-based) to capture the main thread stack during prolonged
        # event loop stalls. This catches cases where asyncio tasks show "await" positions only.
        try:
            # Keep thread-based frame capture behind an explicit unsafe opt-in.
            if operations.loop_hang_watchdog_enabled_from_env():
                try:
                    import threading as _threading
                    import sys as _sys
                    import traceback as _traceback
                except Exception:
                    _threading = None  # type: ignore[assignment]
                    _sys = None  # type: ignore[assignment]
                    _traceback = None  # type: ignore[assignment]
                if _threading and _sys and _traceback:
                    try:
                        hang_ms = float(
                            os.getenv("ADAOS_LOOP_HANG_MS")
                            or os.getenv("ADAOS_LOOP_LAG_DUMP_MS")
                            or "3000"
                        )
                    except Exception:
                        hang_ms = 3000.0
                    try:
                        every_s = float(os.getenv("ADAOS_LOOP_HANG_EVERY_S", "10") or "10")
                    except Exception:
                        every_s = 10.0
                    try:
                        stack_limit = int(os.getenv("ADAOS_LOOP_HANG_STACK", "40") or "40")
                    except Exception:
                        stack_limit = 40
                    if stack_limit < 5:
                        stack_limit = 5
                    if stack_limit > 200:
                        stack_limit = 200
                    if hang_ms < 200:
                        hang_ms = 200.0
                    if every_s < 1:
                        every_s = 1.0

                    main_tid = _threading.get_ident()
                    last_tick_box = {"t": time.monotonic()}

                    async def _tick() -> None:
                        while True:
                            last_tick_box["t"] = time.monotonic()
                            await asyncio.sleep(0.2)

                    service._start_boot_task_once("adaos-loop-tick", _tick)

                    def _is_idle_event_loop_wait(stack_text: str) -> bool:
                        try:
                            st = stack_text.replace("\\", "/")
                            if "asyncio/base_events.py" not in st or "in _run_once" not in st:
                                return False
                            if "selectors.py" in st and "select.select(" in st:
                                return True
                            if "asyncio/windows_events.py" in st and "_overlapped.GetQueuedCompletionStatus" in st:
                                return True
                        except Exception:
                            return False
                        return False

                    def _safe_thread_stack(frame: Any, *, limit: int) -> tuple[str | None, str | None]:
                        try:
                            frames: list[str] = []
                            cur = frame
                            remaining = max(1, int(limit))
                            while cur is not None and remaining > 0:
                                code = getattr(cur, "f_code", None)
                                filename = str(getattr(code, "co_filename", "") or "")
                                func = str(getattr(code, "co_name", "") or "")
                                lineno = int(getattr(cur, "f_lineno", 0) or 0)
                                norm = filename.replace("\\", "/")
                                if "y_py" in norm or "site-packages/y_py" in norm:
                                    return None, "y_py_frame"
                                frames.append(f'  File "{filename}", line {lineno}, in {func}')
                                cur = getattr(cur, "f_back", None)
                                remaining -= 1
                            return "\n".join(reversed(frames)), None
                        except Exception as exc:
                            return None, f"{type(exc).__name__}: {exc}"

                    def _watch() -> None:
                        last_dump = 0.0
                        while True:
                            time.sleep(0.25)
                            now = time.monotonic()
                            dt_ms = (now - float(last_tick_box.get("t", now))) * 1000.0
                            if dt_ms < hang_ms:
                                continue
                            if now - last_dump < every_s:
                                continue
                            last_dump = now
                            try:
                                fr = _sys._current_frames().get(main_tid)  # type: ignore[attr-defined]
                                if fr is None:
                                    print(f"[diag] event loop hang {dt_ms:.0f}ms (no frame)")
                                    diag_log.warning("event loop hang dt_ms=%.0f frame=none", dt_ms)
                                    continue
                                st, stack_error = _safe_thread_stack(fr, limit=stack_limit)
                                if stack_error:
                                    print(f"[diag] event loop hang {dt_ms:.0f}ms stack unavailable: {stack_error}")
                                    diag_log.warning(
                                        "event loop hang dt_ms=%.0f stack_unavailable=%s",
                                        dt_ms,
                                        stack_error,
                                    )
                                    continue
                                st = st or ""
                                if _is_idle_event_loop_wait(st):
                                    diag_log.debug("event loop hang suppressed idle wait dt_ms=%.0f", dt_ms)
                                    continue
                                print(f"[diag] event loop hang {dt_ms:.0f}ms stack:\n{st.rstrip()}")
                                diag_log.warning("event loop hang dt_ms=%.0f stack:\n%s", dt_ms, st.rstrip())
                            except Exception:
                                continue

                    t = _threading.Thread(target=_watch, name="adaos-loop-hang-watchdog", daemon=True)
                    t.start()
        except Exception:
            pass
        diag_log = logging.getLogger("adaos.diagnostics")
        startup_log = logging.getLogger("adaos.startup")
        startup_stage_logs_enabled = str(os.getenv("ADAOS_STARTUP_STAGE_LOGS") or "").strip().lower() in {"1", "true", "yes", "on"}

        def _startup_stage_mark(stage: str, *, started: float | None = None, failed: Exception | None = None) -> float:
            now = time.perf_counter()
            if started is None:
                if startup_stage_logs_enabled:
                    startup_log.info("startup stage start stage=%s", stage)
                return now
            duration = now - started
            if failed is None:
                if startup_stage_logs_enabled:
                    startup_log.info("startup stage done stage=%s duration_s=%.3f", stage, duration)
            else:
                startup_log.warning(
                    "startup stage failed stage=%s duration_s=%.3f error=%s",
                    stage,
                    duration,
                    type(failed).__name__,
                )
            return now

        try:
            from adaos.services.agent_context import get_ctx as _get_ctx
            from adaos.services.workspace_sync import reconcile_workspace_db_to_materialized as _reconcile_workspace_db_to_materialized

            _reconcile_started = _startup_stage_mark("bootstrap_reconcile_workspace_registry")
            await asyncio.to_thread(_reconcile_workspace_db_to_materialized, _get_ctx())
            _startup_stage_mark("bootstrap_reconcile_workspace_registry", started=_reconcile_started)
        except Exception:
            service._log.debug("failed to reconcile workspace sqlite registry on boot", exc_info=True)
        if conf.role == "hub":
            _hub_ready_started = _startup_stage_mark("bootstrap_emit_net_subnet_hub_ready")
            await operations.bus.emit("net.subnet.hub.ready", {"subnet_id": conf.subnet_id}, source="lifecycle", actor="system")
            _startup_stage_mark("bootstrap_emit_net_subnet_hub_ready", started=_hub_ready_started)

            async def lease_monitor() -> None:
                while True:
                    for info in service.subnet_registry.mark_down_if_expired():
                        await operations.bus.emit("net.subnet.node.down", {"node_id": getattr(info, "node_id", None)}, source="lifecycle", actor="system")
                    await asyncio.sleep(5)

            service._start_boot_task_once("adaos-lease-monitor", lease_monitor)
            service._lifecycle.mark_ready()
            if candidate_runtime_mode:
                service._log.info(
                    "deferring sys.ready handlers until candidate runtime promotion"
                )
            else:
                _sys_ready_started = _startup_stage_mark("bootstrap_emit_sys_ready")
                await operations.bus.emit("sys.ready", {"ts": time.time()}, source="lifecycle", actor="system")
                _startup_stage_mark("bootstrap_emit_sys_ready", started=_sys_ready_started)
            _node_status_started = _startup_stage_mark("bootstrap_emit_node_status")
            await _emit_node_status("candidate.ready" if candidate_runtime_mode else "sys.ready")
            _startup_stage_mark("bootstrap_emit_node_status", started=_node_status_started)
            try:
                if callable(_finalize_runtime_boot_status):
                    await _finalize_runtime_boot_status()
            except Exception:
                service._log.debug("failed to finalize core.update.status after runtime readiness", exc_info=True)
            if not candidate_runtime_mode:
                _schedule_release_validation_autorun("sys.ready")
            _control_started = _startup_stage_mark("bootstrap_report_control_lifecycle")
            await _report_control_lifecycle("candidate.ready" if candidate_runtime_mode else "sys.ready")
            _startup_stage_mark("bootstrap_report_control_lifecycle", started=_control_started)
            service._status_watchdog.start_heartbeats(service._lifecycle)
        else:
            member_ready_announced = False
            member_runtime_owns_upstream = self._member_runtime_owns_upstream(operations)

            async def _announce_member_ready() -> None:
                nonlocal member_ready_announced
                if member_ready_announced:
                    return
                member_ready_announced = True
                if member_runtime_owns_upstream:
                    try:
                        from adaos.services.subnet.link_client import get_member_link_client

                        await get_member_link_client().start()
                    except Exception:
                        service._log.warning("failed to start member hub websocket link after registration", exc_info=True)
                else:
                    service._log.info(
                        "candidate member runtime keeps registration heartbeat and hub websocket passive until promotion"
                    )
                service._lifecycle.signal_ready()
                if candidate_runtime_mode:
                    service._log.info(
                        "deferring sys.ready handlers until candidate runtime promotion"
                    )
                else:
                    _sys_ready_started = _startup_stage_mark("bootstrap_emit_sys_ready")
                    await operations.bus.emit("sys.ready", {"ts": time.time()}, source="lifecycle", actor="system")
                    _startup_stage_mark("bootstrap_emit_sys_ready", started=_sys_ready_started)
                _node_status_started = _startup_stage_mark("bootstrap_emit_node_status")
                await _emit_node_status("candidate.ready" if candidate_runtime_mode else "sys.ready")
                _startup_stage_mark("bootstrap_emit_node_status", started=_node_status_started)
                try:
                    if callable(_finalize_runtime_boot_status):
                        await _finalize_runtime_boot_status()
                except Exception:
                    service._log.debug("failed to finalize core.update.status after runtime readiness", exc_info=True)
                if not candidate_runtime_mode:
                    _schedule_release_validation_autorun("sys.ready")

            # Keep the original boot-generation callback available to an
            # explicit member reconnect. If startup registration failed (for
            # example because a legacy routed token expired), a later
            # successful rejoin must complete the same readiness/sys.ready
            # transition instead of leaving the otherwise connected node
            # permanently at ready=false.
            service._lifecycle.set_member_ready_callback(_announce_member_ready)
            if member_runtime_owns_upstream:
                task = await service._member_register_and_heartbeat(conf, on_registered=_announce_member_ready)
                if task:
                    service._lifecycle.track_task(task)
                    service._lifecycle.mark_booted()
            else:
                # A passive candidate must become probe-ready without claiming
                # the singleton member identity at Root or /ws/subnet.  If two
                # runtimes connect with the same node_id, candidate cleanup can
                # otherwise orphan the active runtime's hub-side link.
                await _announce_member_ready()
                service._lifecycle.mark_booted()

        # After IO bus is ready, wire outbound subscriber for Telegram if NATS/local
        _post_ready_started = _startup_stage_mark("bootstrap_post_ready_tail")
        try:
            if hasattr(service._io_bus, "subscribe_output"):
                _subscribe_output_started = _startup_stage_mark("bootstrap_subscribe_output")

                # Subscribe to all bot ids ("tg.output.*") and use the single configured TG_BOT_TOKEN.
                sender = operations.telegram_sender_type("any-bot")

                async def _handler(subject: str, data: bytes) -> None:
                    try:
                        payload = operations.json_module.loads(data.decode("utf-8"))
                        # payload may already match ChatOutputEvent schema
                        messages = [operations.chat_output_message_type(**m) for m in payload.get("messages", [])]
                        out = operations.chat_output_event_type(target=payload.get("target", {}), messages=messages, options=payload.get("options"))
                        await sender.send(out)
                        for m in messages:
                            operations.telemetry.record_event("outbound_total", {"type": m.type})
                    except Exception as e:
                        # On error, emit DLQ if possible
                        try:
                            dlq_env = {"error": str(e), "subject": subject, "data": payload if "payload" in locals() else None}
                            if hasattr(service._io_bus, "publish_dlq"):
                                await service._io_bus.publish_dlq("output", dlq_env)
                        except Exception:
                            pass

                await service._io_bus.subscribe_output("*", _handler)
                _startup_stage_mark("bootstrap_subscribe_output", started=_subscribe_output_started)
        except Exception:
            pass

        # Inbound bridge from root NATS -> local event bus (tg.input.<hub_id>)
        await operations.start_nats_root_transport(
            service,
            core_bus=core_bus,
            startup_stage_mark=_startup_stage_mark,
            report_control_lifecycle=_report_control_lifecycle,
        )

