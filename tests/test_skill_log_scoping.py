from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.adapters.fs.path_provider import PathProvider
from adaos.adapters.sdk.inproc_skill_context import InprocSkillContext
from adaos.services.agent_context import clear_ctx, set_ctx
from adaos.services.logging import configure_skill_module_logging, logging_queue_snapshot, setup_logging
from adaos.services.root_mcp.logs import list_local_logs
from adaos.services.ui_runtime_diagnostics import ingest_ui_runtime_diagnostics
import adaos.services.ui_runtime_diagnostics as ui_runtime_diagnostics


@pytest.mark.asyncio
async def test_ui_runtime_diagnostics_write_skill_scoped_log_and_mcp_can_read_it(tmp_path: Path) -> None:
    paths = PathProvider(tmp_path)
    paths.ensure_tree()
    set_ctx(SimpleNamespace(paths=paths, skill_ctx=InprocSkillContext()))
    try:
        result = await ingest_ui_runtime_diagnostics(
            {
                "webspace_id": "desktop",
                "events": [
                    {
                        "level": "warning",
                        "source": "ui.modal",
                        "code": "modal.not_found",
                        "message": "Modal missing.",
                        "skillId": "browsers_skill",
                        "details": {
                            "requestedId": "browser_link_settings_modal",
                            "browser_identity": {
                                "device_id": "dev-browser-1",
                                "browser_family": "Chrome",
                                "os_name": "Windows",
                                "form_factor": "Desktop",
                            },
                            "runtime_debug": {
                                "session_id": "brs-1",
                                "tab_id": "tab-1",
                                "details": {"client_attempt_id": "cyws-1"},
                            },
                        },
                    }
                ],
            }
        )
        assert result["accepted"] == 1

        log_path = paths.skill_ui_diagnostics_log_path("browsers_skill")
        assert log_path.exists()
        line = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
        assert line["skill_id"] == "browsers_skill"
        assert line["code"] == "modal.not_found"
        assert line["webspace_id"] == "desktop"
        assert line["browser_device_id"] == "dev-browser-1"
        assert line["browser_family"] == "Chrome"
        assert line["browser_os_name"] == "Windows"
        assert line["browser_form_factor"] == "Desktop"
        assert line["browser_session_id"] == "brs-1"
        assert line["browser_tab_id"] == "tab-1"
        assert line["client_yws_attempt_id"] == "cyws-1"

        payload = list_local_logs(
            category="skills",
            skill="browsers_skill",
            logs_dir=paths.logs_dir(),
            lines=5,
        )
        assert [item["name"] for item in payload["items"]] == ["service.browsers_skill.ui_runtime.log"]
    finally:
        clear_ctx()


@pytest.mark.asyncio
async def test_ui_runtime_diagnostics_preserve_unattributed_fallback_log_name(tmp_path: Path) -> None:
    paths = PathProvider(tmp_path)
    paths.ensure_tree()
    set_ctx(SimpleNamespace(paths=paths, skill_ctx=InprocSkillContext()))
    try:
        result = await ingest_ui_runtime_diagnostics(
            {
                "webspace_id": "desktop",
                "events": [
                    {
                        "level": "warning",
                        "source": "ui.modal",
                        "code": "modal.missing_id",
                        "message": "Cannot open modal: modal id is missing.",
                        "details": {"options": {"nodeId": "node-1"}},
                    }
                ],
            }
        )
        assert result["accepted"] == 1

        log_path = paths.skill_ui_diagnostics_log_path("__ui_runtime__")
        assert log_path.name == "service.__ui_runtime__.ui_runtime.log"
        assert log_path.exists()
        line = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
        assert line["skill_id"] == "__ui_runtime__"
        assert line["code"] == "modal.missing_id"
    finally:
        clear_ctx()


@pytest.mark.asyncio
async def test_ui_runtime_diagnostics_drop_noisy_runtime_debug_events(tmp_path: Path) -> None:
    paths = PathProvider(tmp_path)
    paths.ensure_tree()
    set_ctx(SimpleNamespace(paths=paths, skill_ctx=InprocSkillContext()))
    try:
        result = await ingest_ui_runtime_diagnostics(
            {
                "webspace_id": "desktop",
                "events": [
                    {
                        "level": "debug",
                        "source": "ui.runtime_debug",
                        "code": "webio.event",
                        "message": "webio.event",
                        "details": {
                            "runtime_debug": {
                                "kind": "webio.event",
                                "session_id": "brs-1",
                                "tab_id": "tab-1",
                                "details": {
                                    "receiver": "slideshow_skill.session",
                                    "topic": "webio.stream.desktop.slideshow_skill.session",
                                },
                            }
                        },
                    },
                    {
                        "level": "info",
                        "source": "ui.runtime_debug",
                        "code": "webio.subscribe",
                        "message": "webio.subscribe",
                        "details": {
                            "runtime_debug": {
                                "kind": "webio.subscribe",
                                "session_id": "brs-1",
                                "tab_id": "tab-1",
                                "details": {"receiver": "voice_chat.messages"},
                            }
                        },
                    },
                    {
                        "level": "info",
                        "source": "ui.runtime_debug",
                        "code": "yjs.provider.status",
                        "message": "yjs.provider.status",
                        "details": {
                            "runtime_debug": {
                                "kind": "yjs.provider.status",
                                "session_id": "brs-1",
                                "tab_id": "tab-1",
                                "details": {"path": "yws", "status": "connected"},
                            }
                        },
                    },
                    {
                        "level": "debug",
                        "source": "ui.runtime_debug",
                        "code": "runtime_debug.cursor",
                        "message": "runtime_debug.cursor",
                        "details": {"runtime_debug_cursor": {"latest_seq": 42}},
                    },
                ],
            }
        )
        assert result["accepted"] == 1

        log_path = paths.skill_ui_diagnostics_log_path("__ui_runtime__")
        lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        assert [line["code"] for line in lines] == ["runtime_debug.cursor"]
    finally:
        clear_ctx()


@pytest.mark.asyncio
async def test_ui_runtime_diagnostics_deduplicates_stable_runtime_debug_cursors(tmp_path: Path) -> None:
    paths = PathProvider(tmp_path)
    paths.ensure_tree()
    set_ctx(SimpleNamespace(paths=paths, skill_ctx=InprocSkillContext()))
    ui_runtime_diagnostics._CURSOR_DEDUP.clear()
    try:
        cursor_event = {
            "level": "debug",
            "source": "ui.runtime_debug",
            "code": "runtime_debug.cursor",
            "message": "runtime_debug.cursor",
            "currentScenario": "prompt_engineer_scenario",
            "details": {
                "runtime_debug_cursor": {
                    "session_id": "brs-1",
                    "tab_id": "tab-1",
                    "latest_seq": 42,
                    "yjs_status": {"state": "green", "reason": "ready"},
                    "yjs_channel_guarantee": {
                        "state": "green",
                        "reason": "channel_guarantee_ready",
                        "runtime": {"connectionState": "connected", "currentPath": "webrtc_data:yjs"},
                    },
                }
            },
        }
        result = await ingest_ui_runtime_diagnostics(
            {"webspace_id": "desktop", "events": [cursor_event, cursor_event]}
        )
        assert result["accepted"] == 1

        result = await ingest_ui_runtime_diagnostics(
            {"webspace_id": "desktop", "events": [cursor_event]}
        )
        assert result["accepted"] == 0

        log_path = paths.skill_ui_diagnostics_log_path("__ui_runtime__")
        lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        assert [line["code"] for line in lines] == ["runtime_debug.cursor"]
    finally:
        ui_runtime_diagnostics._CURSOR_DEDUP.clear()
        clear_ctx()


def test_skill_context_logs_route_to_skill_runtime_log_not_platform_log(tmp_path: Path) -> None:
    paths = PathProvider(tmp_path)
    paths.ensure_tree()
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    skill_ctx = InprocSkillContext()
    set_ctx(SimpleNamespace(paths=paths, skill_ctx=skill_ctx))
    adaos_logger = logging.getLogger("adaos")
    previous_handlers = list(adaos_logger.handlers)
    previous_level = adaos_logger.level
    previous_propagate = adaos_logger.propagate
    logger = setup_logging(paths, level="DEBUG")
    try:
        assert skill_ctx.set("demo_skill", skill_dir)
        logging.getLogger("adaos.demo.skill").warning("skill-only")
        skill_ctx.clear()
        logging.getLogger("adaos.demo.platform").warning("platform")
        for handler in logger.handlers:
            handler.flush()

        platform_log = paths.logs_dir() / "adaos.log"
        skill_log = paths.skill_runtime_log_path("demo_skill")
        assert "platform" in platform_log.read_text(encoding="utf-8")
        assert "skill-only" not in platform_log.read_text(encoding="utf-8")
        assert "skill-only" in skill_log.read_text(encoding="utf-8")
    finally:
        for handler in list(logger.handlers):
            handler.close()
        logger.handlers.clear()
        logger.handlers[:] = previous_handlers
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate
        clear_ctx()


def test_skill_exception_capture_avoids_traceback_formatter_and_cross_thread_frames(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = PathProvider(tmp_path)
    paths.ensure_tree()
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    skill_ctx = InprocSkillContext()
    set_ctx(SimpleNamespace(paths=paths, skill_ctx=skill_ctx))
    logger = setup_logging(paths, level="DEBUG")
    monkeypatch.setattr(
        logging.Formatter,
        "formatException",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("queued exception must not retain or format traceback objects")
        ),
    )
    configure_skill_module_logging("adaos_skill_slow_logging_test")
    skill_logger = logging.getLogger("adaos_skill_slow_logging_test")
    try:
        assert skill_ctx.set("demo_skill", skill_dir)
        try:
            raise RuntimeError("slow traceback source")
        except RuntimeError:
            skill_logger.warning("deferred traceback", exc_info=True)
        for handler in logger.handlers:
            handler.flush()

        records = [
            json.loads(line)
            for line in paths.skill_runtime_log_path("demo_skill").read_text(encoding="utf-8").splitlines()
        ]
        captured = next(item for item in records if item.get("msg") == "deferred traceback")
        assert captured["exception"]["type"] == "RuntimeError"
        assert captured["exception"]["message"] == "slow traceback source"
        assert captured["exception"]["frames"][-1]["function"].startswith("test_skill_exception_capture")
        snapshot = logging_queue_snapshot()
        assert snapshot["configured"] is True
        assert snapshot["enqueued_total"] >= 1
        assert snapshot["dropped_total"] == 0
    finally:
        for handler in logger.handlers:
            handler.flush()
        skill_ctx.clear()
        clear_ctx()


def test_logging_queue_survives_output_handler_failure(tmp_path: Path) -> None:
    paths = PathProvider(tmp_path)
    paths.ensure_tree()
    logger = setup_logging(paths, level="DEBUG")
    queue_handler = logger.handlers[0]
    listener = queue_handler._listener
    output_handler = listener.handlers[0]
    original_handle = output_handler.handle
    failed = False

    def fail_once(record: logging.LogRecord) -> bool:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("synthetic log sink failure")
        return original_handle(record)

    output_handler.handle = fail_once
    try:
        logger.info("first record triggers sink failure")
        logger.info("second record remains observable")
        queue_handler.flush()

        snapshot = logging_queue_snapshot()
        assert snapshot["listener_alive"] is True
        assert snapshot["listener_failure_total"] == 1
        assert snapshot["last_listener_failure"]["error_type"] == "OSError"
        assert "second record remains observable" in (paths.logs_dir() / "adaos.log").read_text(encoding="utf-8")
    finally:
        output_handler.handle = original_handle


def test_setup_logging_removes_direct_output_handlers(tmp_path: Path) -> None:
    paths = PathProvider(tmp_path)
    paths.ensure_tree()
    root_logger = logging.getLogger()
    direct_root = logging.StreamHandler()
    direct_library = logging.StreamHandler()
    library_logger = logging.getLogger("test.direct.library")
    root_logger.addHandler(direct_root)
    library_logger.handlers[:] = [direct_library]
    library_logger.propagate = False

    logger = setup_logging(paths, level="DEBUG")
    queue_handler = logger.handlers[0]

    assert root_logger.handlers == [queue_handler]
    assert library_logger.handlers == [queue_handler]
    assert logging_queue_snapshot()["unsafe_direct_handlers"] == []


def test_logging_queue_redirects_handlers_added_after_setup(tmp_path: Path) -> None:
    paths = PathProvider(tmp_path)
    paths.ensure_tree()
    logger = setup_logging(paths, level="DEBUG")
    queue_handler = logger.handlers[0]
    library_logger = logging.getLogger("test.direct.late")
    direct_path = tmp_path / "late-direct.log"
    before = int(logging_queue_snapshot()["redirected_direct_handler_total"])

    library_logger.addHandler(logging.FileHandler(direct_path, encoding="utf-8"))
    library_logger.warning("record after direct handler attempt")
    queue_handler.flush()

    snapshot = logging_queue_snapshot()
    assert library_logger.handlers == [queue_handler]
    assert library_logger.propagate is False
    assert snapshot["redirected_direct_handler_total"] == before + 1
    assert snapshot["recent_direct_handler_redirects"][-1]["logger"] == "test.direct.late"
    assert snapshot["recent_direct_handler_redirects"][-1]["handler"] == "FileHandler"
    assert snapshot["unsafe_direct_handlers"] == []
    assert not direct_path.exists() or direct_path.read_text(encoding="utf-8") == ""


def test_scenario_logs_use_shared_nonblocking_listener(tmp_path: Path) -> None:
    paths = PathProvider(tmp_path)
    paths.ensure_tree()
    set_ctx(SimpleNamespace(paths=paths, settings=SimpleNamespace(scenario_log_level="INFO")))
    logger = setup_logging(paths, level="DEBUG")
    queue_handler = logger.handlers[0]
    try:
        from adaos.sdk.core.logging import setup_scenario_logger

        scenario_logger, log_path = setup_scenario_logger("demo_scenario")
        scenario_logger.info("scenario record")
        queue_handler.flush()

        assert scenario_logger.handlers == [queue_handler]
        assert json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])["msg"] == "scenario record"
        assert logging_queue_snapshot()["unsafe_direct_handlers"] == []
    finally:
        clear_ctx()


def test_logging_queue_restarts_unexpectedly_stopped_listener(tmp_path: Path) -> None:
    paths = PathProvider(tmp_path)
    paths.ensure_tree()
    logger = setup_logging(paths, level="DEBUG")
    queue_handler = logger.handlers[0]
    listener = queue_handler._listener
    listener.enqueue_sentinel()
    listener._thread.join(timeout=2.0)
    assert listener._thread.is_alive() is False

    logger.info("record after listener restart")
    queue_handler.flush()

    snapshot = logging_queue_snapshot()
    assert snapshot["listener_alive"] is True
    assert snapshot["listener_restart_total"] == 1
    assert "record after listener restart" in (paths.logs_dir() / "adaos.log").read_text(encoding="utf-8")
