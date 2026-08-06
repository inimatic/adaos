from __future__ import annotations

from types import SimpleNamespace

from adaos.apps.supervisor_runtime import MemoryProfilingService


class _Process:
    def __init__(self, pid: int, rss: int, children=None, cmdline=None) -> None:
        self.pid = pid
        self._rss = rss
        self._children = list(children or [])
        self._cmdline = list(cmdline or ["python"])

    def ppid(self):
        return 1

    def memory_info(self):
        return SimpleNamespace(rss=self._rss)

    def name(self):
        return "python"

    def cmdline(self):
        return self._cmdline

    def children(self, recursive=True):
        return self._children


def test_memory_profiling_service_snapshots_process_family() -> None:
    child = _Process(2, 30)
    root = _Process(1, 70, children=[child])
    fake_psutil = SimpleNamespace(Process=lambda pid: root)

    result = MemoryProfilingService().process_family_snapshot(1, psutil_module=fake_psutil)

    assert result["family_rss_bytes"] == 100
    assert result["children"][0]["pid"] == 2


def test_memory_profiling_service_identifies_skill_runtime() -> None:
    name = MemoryProfilingService.skill_runtime_name(
        ["python", "/opt/adaos/skills/.runtime/weather/venv/bin/worker"]
    )

    assert name == "weather"


def test_memory_profiling_service_uses_normal_shutdown_defaults() -> None:
    assert MemoryProfilingService.graceful_shutdown_timeouts("normal") == (5.0, 0.25, 8.0, 5.0)


def test_memory_profiling_service_preserves_policy_env_contract(monkeypatch) -> None:
    service = MemoryProfilingService()

    monkeypatch.delenv("ADAOS_SUPERVISOR_MEMORY_POLICY_PROFILE_RESTARTS", raising=False)
    assert service.policy_profile_restarts_enabled() is True

    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_POLICY_PROFILE_RESTARTS", "0")
    assert service.policy_profile_restarts_enabled() is False

    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_FAMILY_RSS_BYTES", "none")
    assert service.suspicion_family_rss_threshold_bytes() is None

    monkeypatch.setenv("ADAOS_SUPERVISOR_MEMORY_PROFILE_COOLDOWN_SEC", "30")
    assert service.auto_profile_cooldown_sec() == 60.0


def test_memory_profiling_service_owns_mutable_session_and_baseline_state() -> None:
    service = MemoryProfilingService(default_profiler_adapter="sampled")

    service.active_session_id = "mem-001"
    service.baseline_family_rss_bytes = 512
    service.suspicion_state = "suspected"

    assert service.profiler_adapter_name == "sampled"
    assert service.active_session_id == "mem-001"
    assert service.baseline_family_rss_bytes == 512
    assert service.suspicion_state == "suspected"
