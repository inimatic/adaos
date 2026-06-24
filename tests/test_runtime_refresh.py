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
