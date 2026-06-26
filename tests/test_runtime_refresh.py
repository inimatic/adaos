from __future__ import annotations

from typing import Any

from adaos.services.runtime_refresh import refresh_skill_runtime


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
            return {"ok": True}

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
    assert payload["active_converged"] is True
    assert payload["active_version_after"] == "0.8.3"
    assert payload["active_slot_after"] == "B"
    assert payload.get("skipped") is not True
    assert calls == [
        "runtime_status:mediaserver:1",
        "runtime_update:mediaserver:workspace",
        "install:mediaserver:0",
        "prepare_runtime:mediaserver:0:1",
        "activate_for_space:mediaserver:0.8.3:B:default:desktop",
        "runtime_status:mediaserver:2",
    ]
