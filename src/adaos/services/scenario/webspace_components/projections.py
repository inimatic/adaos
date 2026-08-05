from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Awaitable, Callable


class WebspaceProjectionService:
    """Own projection registry operations for the webspace runtime."""

    @staticmethod
    def space_for_source(source_mode: Any) -> str:
        return "dev" if str(source_mode or "").strip().lower() == "dev" else "workspace"

    def describe(
        self,
        *,
        operational: Any,
        scenario_id: str | None,
        registry: Any,
    ) -> dict[str, Any]:
        target_scenario = (
            str(scenario_id or "").strip()
            or str(operational.current_scenario or "").strip()
            or str(operational.effective_home_scenario or "").strip()
            or None
        )
        snapshot: dict[str, Any] = {}
        try:
            raw = registry.snapshot() if hasattr(registry, "snapshot") else {}
            snapshot = dict(raw) if isinstance(raw, Mapping) else {}
        except Exception:
            snapshot = {}

        active_scenario = str(snapshot.get("active_scenario_id") or "").strip() or None
        active_space = str(snapshot.get("active_space") or "").strip() or "workspace"
        target_space = self.space_for_source(operational.source_mode)
        return {
            "webspace_id": operational.webspace_id,
            "target_scenario": target_scenario,
            "target_space": target_space,
            "active_scenario": active_scenario,
            "active_space": active_space,
            "active_matches_target": bool(target_scenario)
            and active_scenario == target_scenario
            and active_space == target_space,
            "base_rule_count": int(snapshot.get("base_rule_count") or 0),
            "scenario_rule_count": int(snapshot.get("scenario_rule_count") or 0),
            "source": "projection_registry",
        }

    def refresh_rules(
        self,
        *,
        registry: Any,
        scenario_id: str | None,
        scenario_resolution: str | None,
        space: str,
    ) -> dict[str, Any]:
        if not scenario_id:
            return {
                "attempted": False,
                "scenario_id": None,
                "scenario_resolution": scenario_resolution,
                "space": space,
                "rules_loaded": 0,
                "source": "none",
            }
        try:
            rules_loaded = int(registry.load_from_scenario(scenario_id, space=space) or 0)
            return {
                "attempted": True,
                "scenario_id": scenario_id,
                "scenario_resolution": scenario_resolution,
                "space": space,
                "rules_loaded": rules_loaded,
                "source": "scenario_manifest",
            }
        except Exception as exc:
            try:
                replace_entries = getattr(registry, "replace_scenario_entries", None)
                if callable(replace_entries):
                    replace_entries([], scenario_id=scenario_id, space=space)
            except Exception:
                pass
            return {
                "attempted": True,
                "scenario_id": scenario_id,
                "scenario_resolution": scenario_resolution,
                "space": space,
                "rules_loaded": 0,
                "source": "scenario_manifest",
                "error": f"{exc.__class__.__name__}: {exc}",
            }

    async def project(
        self,
        *,
        operation: Callable[[], Awaitable[Any]],
        timeout_s: float,
    ) -> dict[str, Any]:
        try:
            projection = operation()
            if timeout_s > 0.0:
                await asyncio.wait_for(projection, timeout=timeout_s)
            else:
                await projection
            return {"status": "completed"}
        except asyncio.TimeoutError:
            return {"status": "timed_out"}
        except Exception as exc:
            return {"status": "failed", "error": exc}
