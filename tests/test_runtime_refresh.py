from __future__ import annotations

import asyncio
import sys
import threading
import types
from typing import Any

import pytest

from adaos.services.runtime_refresh import (
    RuntimeRefreshError,
    rebuild_webspace_projection_sync,
    refresh_skill_runtime,
)


def test_rebuild_webspace_projection_sync_works_inside_running_loop(monkeypatch) -> None:
    calls: list[tuple[str, str, str, str]] = []

    async def _fake_rebuild(webspace_id: str, *, action: str, source_of_truth: str) -> None:
        calls.append((webspace_id, action, source_of_truth, threading.current_thread().name))

    module = types.ModuleType("adaos.services.scenario.webspace_runtime")
    module.rebuild_webspace_from_sources = _fake_rebuild
    monkeypatch.setitem(
        sys.modules,
        "adaos.services.scenario.webspace_runtime",
        module,
    )

    async def _call_from_loop() -> dict[str, Any]:
        return rebuild_webspace_projection_sync(
            webspace_id="desktop",
            action="infrastate_adaos_update_sync",
            source_of_truth="scenario_projection",
        )

    result = asyncio.run(_call_from_loop())

    assert result == {
        "ok": True,
        "accepted": True,
        "webspace_id": "desktop",
        "action": "infrastate_adaos_update_sync",
        "source_of_truth": "scenario_projection",
    }
    assert calls == [
        (
            "desktop",
            "infrastate_adaos_update_sync",
            "scenario_projection",
            "adaos-webspace-rebuild-sync",
        )
    ]


def test_refresh_skill_runtime_skips_deactivated_skill() -> None:
    calls: list[str] = []

    class _Manager:
        def runtime_status(self, name: str) -> dict[str, Any]:
            calls.append(f"runtime_status:{name}")
            return {
                "version": "0.2.0",
                "active_slot": "B",
                "deactivated": True,
                "deactivation": {"reason": "runtime_migration_failed"},
            }

        def runtime_update(self, name: str, space: str = "workspace") -> dict[str, Any]:
            calls.append(f"runtime_update:{name}:{space}")
            raise AssertionError("deactivated skill must not be runtime-updated")

        def install(self, name: str, validate: bool = False) -> None:
            calls.append(f"install:{name}:{int(validate)}")
            raise AssertionError("deactivated skill must not be installed")

        def prepare_runtime(self, name: str, run_tests: bool = False):
            calls.append(f"prepare_runtime:{name}:{int(run_tests)}")
            raise AssertionError("deactivated skill must not be prepared")

    payload = refresh_skill_runtime(
        _Manager(),
        "new_face_vision_skill",
        webspace_id="desktop",
        source_version="0.3.0",
        migrate_runtime=True,
        ensure_installed=True,
    )

    assert payload["ok"] is True
    assert payload["skipped"] is True
    assert payload["deactivated"] is True
    assert payload["active_version_after"] == "0.2.0"
    assert payload["active_slot_after"] == "B"
    assert [stage["stage"] for stage in payload["lifecycle_stages"]] == [
        "runtime_update",
        "prepare",
        "activate",
        "converge",
    ]
    assert calls == ["runtime_status:new_face_vision_skill"]


def test_refresh_skill_runtime_retries_deactivated_skill_when_requested() -> None:
    calls: list[str] = []

    class _Runtime:
        version = "0.2.1"
        slot = "B"
        data_migration = {}

    class _Manager:
        def __init__(self) -> None:
            self._status_calls = 0

        def runtime_status(self, name: str) -> dict[str, Any]:
            self._status_calls += 1
            calls.append(f"runtime_status:{name}:{self._status_calls}")
            if self._status_calls == 1:
                return {
                    "version": "0.2.0",
                    "active_slot": "A",
                    "deactivated": True,
                    "deactivation": {
                        "deactivated": True,
                        "transient": False,
                        "reason": "runtime_migration_failed",
                    },
                }
            return {"version": "0.2.1", "active_slot": "B", "deactivated": False}

        def runtime_update(self, name: str, space: str = "workspace") -> dict[str, Any]:
            calls.append(f"runtime_update:{name}:{space}")
            raise AssertionError("versioned candidate must not mutate the active runtime")

        def install(self, name: str, validate: bool = False) -> None:
            calls.append(f"install:{name}:{int(validate)}")

        def prepare_runtime(self, name: str, *, run_tests: bool = False, allow_deactivated: bool = False):
            calls.append(f"prepare_runtime:{name}:{int(run_tests)}:{int(allow_deactivated)}")
            assert allow_deactivated is True
            return _Runtime()

        def activate_for_space(self, name: str, *, version=None, slot=None, space="default", webspace_id=None):
            calls.append(f"activate_for_space:{name}:{version}:{slot}:{space}:{webspace_id}")
            return slot

    payload = refresh_skill_runtime(
        _Manager(),
        "weather_skill",
        webspace_id="desktop",
        source_version="0.2.1",
        migrate_runtime=True,
        ensure_installed=True,
        require_active_version=True,
        retry_deactivated=True,
    )

    assert payload["ok"] is True
    assert payload["deactivation_retry"] is True
    assert payload["runtime_migrated"] is True
    assert payload["isolated_candidate"] is True
    assert payload["runtime_updated"] is False
    assert payload["active_converged"] is True
    assert payload["active_version_after"] == "0.2.1"
    assert payload["active_slot_after"] == "B"
    assert calls == [
        "runtime_status:weather_skill:1",
        "install:weather_skill:0",
        "prepare_runtime:weather_skill:0:1",
        "activate_for_space:weather_skill:0.2.1:B:default:desktop",
        "runtime_status:weather_skill:2",
    ]


def test_refresh_skill_runtime_recovers_transient_migration_deactivation() -> None:
    calls: list[str] = []

    class _Runtime:
        version = "0.8.3"
        slot = "B"
        data_migration = {}

    class _Manager:
        def __init__(self) -> None:
            self._status_calls = 0

        def runtime_status(self, name: str) -> dict[str, Any]:
            self._status_calls += 1
            calls.append(f"runtime_status:{name}:{self._status_calls}")
            if self._status_calls == 1:
                return {
                    "version": "0.8.2",
                    "active_slot": "A",
                    "deactivated": True,
                    "deactivation": {
                        "deactivated": True,
                        "transient": True,
                        "reason": "runtime_migration_in_progress",
                    },
                }
            return {"version": "0.8.3", "active_slot": "B", "deactivated": False}

        def runtime_update(self, name: str, space: str = "workspace") -> dict[str, Any]:
            calls.append(f"runtime_update:{name}:{space}")
            raise AssertionError("versioned candidate must not mutate the active runtime")

        def install(self, name: str, validate: bool = False) -> None:
            calls.append(f"install:{name}:{int(validate)}")

        def prepare_runtime(self, name: str, *, run_tests: bool = False, allow_deactivated: bool = False):
            calls.append(f"prepare_runtime:{name}:{int(run_tests)}:{int(allow_deactivated)}")
            assert allow_deactivated is True
            return _Runtime()

        def activate_for_space(self, name: str, *, version=None, slot=None, space="default", webspace_id=None):
            calls.append(f"activate_for_space:{name}:{version}:{slot}:{space}:{webspace_id}")
            return slot

    payload = refresh_skill_runtime(
        _Manager(),
        "mediaserver",
        webspace_id="desktop",
        source_version="0.8.3",
        migrate_runtime=True,
        ensure_installed=True,
        require_active_version=True,
    )

    assert payload["ok"] is True
    assert payload["deactivation_recovery"] is True
    assert payload["runtime_migrated"] is True
    assert payload["isolated_candidate"] is True
    assert payload["runtime_updated"] is False
    assert payload["active_converged"] is True
    assert payload["active_version_after"] == "0.8.3"
    assert payload["active_slot_after"] == "B"
    assert payload.get("skipped") is not True
    assert calls == [
        "runtime_status:mediaserver:1",
        "install:mediaserver:0",
        "prepare_runtime:mediaserver:0:1",
        "activate_for_space:mediaserver:0.8.3:B:default:desktop",
        "runtime_status:mediaserver:2",
    ]


def test_refresh_skill_runtime_isolates_same_version_source_revision() -> None:
    calls: list[str] = []

    class _Runtime:
        version = "1.0.0"
        slot = "B"
        data_migration = {}

    class _Manager:
        def __init__(self) -> None:
            self._status_calls = 0

        def runtime_status(self, name: str) -> dict[str, Any]:
            self._status_calls += 1
            calls.append(f"runtime_status:{name}:{self._status_calls}")
            return {
                "version": "1.0.0",
                "active_slot": "A" if self._status_calls == 1 else "B",
                "deactivated": False,
            }

        def runtime_update(self, name: str, space: str = "workspace") -> dict[str, Any]:
            calls.append(f"runtime_update:{name}:{space}")
            raise AssertionError("same-version refresh must not mutate the active slot")

        def prepare_runtime(self, name: str, *, run_tests: bool = False):
            calls.append(f"prepare_runtime:{name}:{int(run_tests)}")
            return _Runtime()

        def activate_for_space(self, name: str, *, version=None, slot=None, space="default", webspace_id=None):
            calls.append(f"activate_for_space:{name}:{version}:{slot}:{space}:{webspace_id}")
            return slot

    payload = refresh_skill_runtime(
        _Manager(),
        "demo_skill",
        webspace_id="desktop",
        source_version="1.0.0",
        migrate_runtime=True,
    )

    assert payload["ok"] is True
    assert payload["isolated_candidate"] is True
    assert payload["runtime_updated"] is False
    assert payload["runtime_migrated"] is True
    assert payload["prepared_slot"] == "B"
    assert payload["activated_slot"] == "B"
    assert payload["lifecycle_stages"][0]["reason"] == "slot_candidate_isolated"
    assert calls == [
        "runtime_status:demo_skill:1",
        "prepare_runtime:demo_skill:0",
        "activate_for_space:demo_skill:1.0.0:B:default:desktop",
        "runtime_status:demo_skill:2",
    ]


def test_refresh_skill_runtime_restores_active_fallback_after_activation_failure() -> None:
    calls: list[str] = []

    class _Runtime:
        version = "1.1.0"
        slot = "B"
        data_migration = {}

    class _Manager:
        def runtime_status(self, name: str) -> dict[str, Any]:
            calls.append(f"runtime_status:{name}")
            return {"version": "1.0.0", "active_slot": "A", "deactivated": False}

        def deactivate_runtime(self, name: str, **_kwargs: Any) -> dict[str, Any]:
            calls.append(f"deactivate_runtime:{name}")
            return {
                "deactivated": True,
                "transient": True,
                "reason": "runtime_migration_in_progress",
            }

        def prepare_runtime(self, name: str, **_kwargs: Any) -> _Runtime:
            calls.append(f"prepare_runtime:{name}")
            return _Runtime()

        def activate_for_space(self, name: str, **_kwargs: Any) -> str:
            calls.append(f"activate_for_space:{name}")
            raise RuntimeError("candidate rehydrate failed")

        def restore_runtime_selection_exact(self, name: str, **kwargs: Any) -> dict[str, Any]:
            calls.append(f"restore_runtime_selection_exact:{name}")
            assert kwargs == {
                "version": "1.0.0",
                "slot": "A",
                "previous_deactivation": None,
                "webspace_id": "desktop",
                "emit_activation": True,
            }
            return {
                "ok": True,
                "restored_active_version": "1.0.0",
                "restored_active_slot": "A",
                "restored_deactivated": False,
                "activation_emitted": True,
            }

    with pytest.raises(RuntimeRefreshError) as exc_info:
        refresh_skill_runtime(
            _Manager(),
            "demo_skill",
            webspace_id="desktop",
            source_version="1.1.0",
            migrate_runtime=True,
            disable_during_migration=True,
        )

    payload = exc_info.value.payload
    assert payload["failed_stage"] == "activate"
    assert payload["active_version_after"] == "1.0.0"
    assert payload["active_slot_after"] == "A"
    assert payload["fallback_restore"] == {
        "ok": True,
        "restored_active_version": "1.0.0",
        "restored_active_slot": "A",
        "restored_deactivated": False,
        "activation_emitted": True,
        "version": "1.0.0",
        "slot": "A",
    }
    assert payload["lifecycle_stages"][-1]["stage"] == "fallback_restore"
    assert payload["lifecycle_stages"][-1]["ok"] is True
    assert calls == [
        "runtime_status:demo_skill",
        "deactivate_runtime:demo_skill",
        "prepare_runtime:demo_skill",
        "activate_for_space:demo_skill",
        "restore_runtime_selection_exact:demo_skill",
        "runtime_status:demo_skill",
    ]


def test_refresh_skill_runtime_restores_original_deactivation_after_prepare_failure() -> None:
    original = {
        "deactivated": True,
        "transient": False,
        "reason": "operator_hold",
    }

    class _Manager:
        def runtime_status(self, _name: str) -> dict[str, Any]:
            return {
                "version": "2.0.0",
                "active_slot": "B",
                "deactivated": True,
                "deactivation": original,
            }

        def deactivate_runtime(self, _name: str, **_kwargs: Any) -> dict[str, Any]:
            return {
                "deactivated": True,
                "transient": True,
                "reason": "runtime_migration_in_progress",
            }

        def prepare_runtime(self, _name: str, **_kwargs: Any) -> Any:
            raise RuntimeError("candidate dependency unavailable")

        def restore_runtime_selection_exact(self, _name: str, **kwargs: Any) -> dict[str, Any]:
            assert kwargs["previous_deactivation"] == original
            assert kwargs["emit_activation"] is False
            return {
                "ok": True,
                "restored_active_version": "2.0.0",
                "restored_active_slot": "B",
                "restored_deactivated": True,
                "activation_emitted": False,
            }

    with pytest.raises(RuntimeRefreshError) as exc_info:
        refresh_skill_runtime(
            _Manager(),
            "held_skill",
            webspace_id="desktop",
            source_version="2.1.0",
            migrate_runtime=True,
            disable_during_migration=True,
            retry_deactivated=True,
        )

    payload = exc_info.value.payload
    assert payload["failed_stage"] == "prepare"
    assert payload["fallback_restore"]["restored_deactivated"] is True
    assert payload["fallback_restore"]["activation_emitted"] is False


def test_refresh_skill_runtime_without_migration_keeps_active_slot_immutable() -> None:
    calls: list[str] = []

    class _Manager:
        def runtime_status(self, name: str) -> dict[str, Any]:
            calls.append(f"runtime_status:{name}")
            return {"version": "1.0.0", "active_slot": "A", "deactivated": False}

        def runtime_update(self, *_args, **_kwargs):
            raise AssertionError("migration-disabled refresh must not mutate the active slot")

        def prepare_runtime(self, *_args, **_kwargs):
            raise AssertionError("migration-disabled refresh must not prepare a slot")

    payload = refresh_skill_runtime(
        _Manager(),
        "demo_skill",
        webspace_id="desktop",
        source_version="1.0.0",
        migrate_runtime=False,
    )

    assert payload["ok"] is True
    assert payload["runtime_updated"] is False
    assert payload["runtime_migrated"] is False
    assert payload["runtime_refresh_skipped"] is True
    assert payload["runtime_refresh_skip_reason"] == "runtime_migration_disabled"
    assert payload["active_slot_after"] == "A"
    assert calls == ["runtime_status:demo_skill", "runtime_status:demo_skill"]
