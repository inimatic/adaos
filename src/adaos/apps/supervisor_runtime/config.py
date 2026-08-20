from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Any


FALSE_VALUES = frozenset({"0", "false", "no", "off"})
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
MIB = 1024 * 1024


class SupervisorRuntimeConfig:
    """Single owner for supervisor environment policy and numeric defaults."""

    @staticmethod
    def _float(name: str, default: float, *, minimum: float) -> float:
        try:
            return max(
                minimum,
                float(str(os.getenv(name) or str(default)).strip()),
            )
        except Exception:
            return max(minimum, default)

    @staticmethod
    def _int(name: str, default: int, *, minimum: int) -> int:
        try:
            return max(minimum, int(str(os.getenv(name) or str(default)).strip()))
        except Exception:
            return max(minimum, default)

    @staticmethod
    def _enabled(name: str, *, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return str(raw).strip().lower() not in FALSE_VALUES

    @staticmethod
    def _opt_in(name: str, *, default: bool = False) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return str(raw).strip().lower() in TRUE_VALUES

    def update_attempt_timeout_sec(self) -> float:
        return self._float("ADAOS_SUPERVISOR_UPDATE_TIMEOUT_SEC", 180.0, minimum=10.0)

    def update_prepare_timeout_sec(self) -> float:
        return max(
            self.update_attempt_timeout_sec(),
            self._float("ADAOS_SUPERVISOR_PREPARE_TIMEOUT_SEC", 900.0, minimum=0.0),
        )

    def min_update_period_sec(self) -> float:
        return self._float("ADAOS_SUPERVISOR_MIN_UPDATE_PERIOD_SEC", 300.0, minimum=0.0)

    def live_media_guard_defer_sec(self) -> float:
        return self._float("ADAOS_SUPERVISOR_LIVE_MEDIA_DEFER_SEC", 300.0, minimum=30.0)

    def auto_update_complete_enabled(self) -> bool:
        return self._enabled("ADAOS_SUPERVISOR_AUTO_UPDATE_COMPLETE", default=True)

    def root_restart_delay_sec(self) -> float:
        return self._float("ADAOS_SUPERVISOR_ROOT_RESTART_DELAY_SEC", 0.25, minimum=0.1)

    @staticmethod
    def autostart_self_restart_supported() -> bool:
        if os.name == "nt":
            managed = str(os.getenv("ADAOS_AUTOSTART_MANAGED") or "").strip().lower()
            restart_loop = str(os.getenv("ADAOS_AUTOSTART_SELF_RESTART") or "").strip().lower()
            return managed in TRUE_VALUES and restart_loop in TRUE_VALUES
        if not sys.platform.startswith("linux"):
            return False
        raw = os.getenv("ADAOS_AUTOSTART_MANAGED")
        if raw is not None and str(raw).strip():
            return str(raw).strip().lower() in TRUE_VALUES
        return bool(str(os.getenv("INVOCATION_ID") or "").strip())

    @staticmethod
    def slot_runtime_ports(primary_port: int) -> dict[str, int]:
        fallback_a = int(primary_port)
        fallback_b = fallback_a + 1
        try:
            slot_a = int(
                str(os.getenv("ADAOS_SUPERVISOR_SLOT_A_PORT") or fallback_a).strip()
                or fallback_a
            )
        except Exception:
            slot_a = fallback_a
        try:
            slot_b = int(
                str(os.getenv("ADAOS_SUPERVISOR_SLOT_B_PORT") or fallback_b).strip()
                or fallback_b
            )
        except Exception:
            slot_b = fallback_b
        return {
            "A": slot_a if slot_a > 0 else fallback_a,
            "B": slot_b if slot_b > 0 else fallback_b,
        }

    def warm_switch_enabled(self) -> bool:
        return self._enabled("ADAOS_SUPERVISOR_WARM_SWITCH_ENABLED", default=True)

    def warm_switch_min_available_bytes(self) -> int:
        return int(
            self._float(
                "ADAOS_SUPERVISOR_WARM_SWITCH_MIN_AVAILABLE_MB",
                256.0,
                minimum=0.0,
            )
            * MIB
        )

    def warm_switch_min_candidate_bytes(self) -> int:
        return int(
            self._float(
                "ADAOS_SUPERVISOR_WARM_SWITCH_MIN_CANDIDATE_MB",
                192.0,
                minimum=0.0,
            )
            * MIB
        )

    def warm_switch_max_candidate_rss_bytes(
        self,
        *,
        total_memory_bytes: int | None,
    ) -> int:
        raw = os.getenv("ADAOS_SUPERVISOR_WARM_SWITCH_MAX_CANDIDATE_RSS_MB")
        if raw is not None:
            try:
                return max(0, int(float(str(raw).strip()) * MIB))
            except Exception:
                return 1536 * MIB
        if total_memory_bytes and total_memory_bytes > 0:
            return max(512 * MIB, min(1536 * MIB, int(float(total_memory_bytes) * 0.40)))
        return 1536 * MIB

    def warm_switch_rss_multiplier(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_WARM_SWITCH_RSS_MULTIPLIER", 1.15, minimum=1.0
        )

    def warm_switch_candidate_ready_timeout_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_CANDIDATE_READY_TIMEOUT_SEC", 300.0, minimum=0.0
        )

    def warm_switch_strict_cutover_enabled(self) -> bool:
        return self._enabled(
            "ADAOS_SUPERVISOR_STRICT_WARM_SWITCH_CUTOVER", default=True
        )

    def warm_switch_cold_fallback_enabled(self) -> bool:
        return self._opt_in("ADAOS_SUPERVISOR_COLD_CUTOVER_FALLBACK")

    def warm_switch_defer_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_WARM_SWITCH_DEFER_SEC", 60.0, minimum=5.0
        )

    def cutover_recovery_stable_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_CUTOVER_RECOVERY_STABLE_SEC", 30.0, minimum=5.0
        )

    def warm_switch_max_deferrals(self) -> int:
        return self._int(
            "ADAOS_SUPERVISOR_WARM_SWITCH_MAX_DEFERRALS", 1, minimum=0
        )

    def sidecar_code_change_debounce_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_SIDECAR_CODE_DEBOUNCE_SEC", 3.0, minimum=0.5
        )

    def sidecar_recovery_settle_timeout_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_SIDECAR_RECOVERY_SETTLE_TIMEOUT_SEC",
            3.0,
            minimum=0.1,
        )

    def sidecar_restart_window_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_SIDECAR_RESTART_WINDOW_SEC", 60.0, minimum=5.0
        )

    def sidecar_restart_limit(self) -> int:
        return self._int("ADAOS_SUPERVISOR_SIDECAR_RESTART_LIMIT", 4, minimum=2)

    def sidecar_restart_base_backoff_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_SIDECAR_RESTART_BASE_BACKOFF_SEC", 2.0, minimum=1.0
        )

    def sidecar_restart_max_backoff_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_SIDECAR_RESTART_MAX_BACKOFF_SEC", 30.0, minimum=2.0
        )

    def sidecar_restart_circuit_open_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_SIDECAR_RESTART_CIRCUIT_OPEN_SEC", 90.0, minimum=5.0
        )

    def hub_root_watchdog_enabled(self) -> bool:
        return self._enabled("ADAOS_SUPERVISOR_HUB_ROOT_WATCHDOG", default=True)

    def required_upstream_watchdog_poll_interval_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_UPSTREAM_WATCHDOG_POLL_INTERVAL_SEC",
            10.0,
            minimum=2.0,
        )

    def runtime_reliability_probe_timeout_sec(self) -> float:
        """Deadline for the advisory runtime reliability snapshot.

        The snapshot is richer than a listener readiness probe and can be
        delayed by a busy runtime event loop.  Keep it bounded, but do not use
        the old 1.5 second transport deadline as a process-health verdict.
        """

        return self._float(
            "ADAOS_SUPERVISOR_RELIABILITY_PROBE_TIMEOUT_SEC",
            4.0,
            minimum=0.5,
        )

    def hub_root_watchdog_cooldown_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_HUB_ROOT_RECONNECT_COOLDOWN_SEC", 30.0, minimum=5.0
        )

    def hub_root_watchdog_reset_degraded_route_enabled(self) -> bool:
        return self._opt_in("ADAOS_SUPERVISOR_HUB_ROOT_ROUTE_DEGRADED_RESET")

    def hub_root_watchdog_verify_timeout_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_HUB_ROOT_VERIFY_TIMEOUT_SEC", 15.0, minimum=0.0
        )

    def hub_root_watchdog_verify_interval_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_HUB_ROOT_VERIFY_INTERVAL_SEC", 1.0, minimum=0.25
        )

    def hub_root_root_probe_enabled(self) -> bool:
        return self._enabled("ADAOS_SUPERVISOR_HUB_ROOT_ROOT_PROBE", default=True)

    def hub_root_root_probe_interval_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_HUB_ROOT_ROOT_PROBE_INTERVAL_SEC", 30.0, minimum=5.0
        )

    def hub_root_root_probe_timeout_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_HUB_ROOT_ROOT_PROBE_TIMEOUT_SEC", 1.5, minimum=0.1
        )

    def hub_root_root_probe_ttl_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_HUB_ROOT_ROOT_PROBE_TTL_SEC", 120.0, minimum=5.0
        )

    @staticmethod
    def parse_root_probe_time(value: Any) -> float | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            return float(raw)
        except Exception:
            pass
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return float(parsed.timestamp())
        except Exception:
            return None

    def member_hub_watchdog_enabled(self) -> bool:
        return self._enabled("ADAOS_SUPERVISOR_MEMBER_HUB_WATCHDOG", default=True)

    def member_hub_watchdog_cooldown_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_MEMBER_HUB_RECONNECT_COOLDOWN_SEC", 20.0, minimum=5.0
        )

    def member_hub_watchdog_verify_timeout_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_MEMBER_HUB_VERIFY_TIMEOUT_SEC", 10.0, minimum=0.0
        )

    def member_hub_watchdog_verify_interval_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_MEMBER_HUB_VERIFY_INTERVAL_SEC", 1.0, minimum=0.25
        )

    def post_recovery_core_update_reconcile_enabled(self) -> bool:
        return self._enabled(
            "ADAOS_SUPERVISOR_POST_RECOVERY_CORE_UPDATE_RECONCILE", default=True
        )

    def post_recovery_core_update_reconcile_cooldown_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_POST_RECOVERY_CORE_UPDATE_RECONCILE_COOLDOWN_SEC",
            120.0,
            minimum=10.0,
        )

    def post_recovery_core_update_reconcile_countdown_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_POST_RECOVERY_CORE_UPDATE_RECONCILE_COUNTDOWN_SEC",
            60.0,
            minimum=0.0,
        )

    def periodic_core_update_reconcile_enabled(self) -> bool:
        raw = os.getenv("ADAOS_SUPERVISOR_PERIODIC_CORE_UPDATE_RECONCILE")
        if raw is None:
            raw = os.getenv("ADAOS_SUPERVISOR_CORE_UPDATE_RECONCILE")
        if raw is None:
            return True
        return str(raw).strip().lower() not in FALSE_VALUES

    def periodic_core_update_reconcile_interval_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_CORE_UPDATE_RECONCILE_INTERVAL_SEC",
            120.0,
            minimum=30.0,
        )

    def post_recovery_member_hub_refresh_enabled(self) -> bool:
        return self._enabled(
            "ADAOS_SUPERVISOR_POST_RECOVERY_MEMBER_HUB_REFRESH", default=True
        )

    def post_recovery_member_hub_refresh_cooldown_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_POST_RECOVERY_MEMBER_HUB_REFRESH_COOLDOWN_SEC",
            60.0,
            minimum=10.0,
        )

    def runtime_listener_restart_timeout_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_RUNTIME_LISTENER_TIMEOUT_SEC", 45.0, minimum=5.0
        )

    def runtime_listener_startup_grace_sec(
        self, *, listener_timeout_sec: float
    ) -> float:
        return max(
            listener_timeout_sec,
            self._float(
                "ADAOS_SUPERVISOR_RUNTIME_STARTUP_GRACE_SEC", 90.0, minimum=0.0
            ),
        )

    def runtime_api_restart_timeout_sec(self) -> float:
        return self._float(
            "ADAOS_SUPERVISOR_RUNTIME_API_TIMEOUT_SEC", 60.0, minimum=5.0
        )
