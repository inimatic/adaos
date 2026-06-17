from __future__ import annotations

from adaos.services.supervisor_memory import MemoryTelemetrySample


def test_memory_telemetry_preserves_process_family_and_cgroup_breakdown() -> None:
    payload = {
        "sampled_at": 100.0,
        "managed_pid": 123,
        "process_rss_bytes": 10,
        "family_rss_bytes": 20,
        "cgroup_memory_current_bytes": 30,
        "cgroup_anon_bytes": 11,
        "cgroup_file_bytes": 12,
        "cgroup_kernel_bytes": 13,
        "cgroup_slab_bytes": 14,
        "cgroup_memory_stat": {"anon": 11, "file": 12, "kernel": 13, "slab": 14},
        "available_memory_bytes": 40,
        "available_memory_percent": 50.5,
    }

    roundtrip = MemoryTelemetrySample.from_dict(payload).to_dict()

    assert roundtrip["process_rss_bytes"] == 10
    assert roundtrip["family_rss_bytes"] == 20
    assert roundtrip["cgroup_memory_current_bytes"] == 30
    assert roundtrip["cgroup_anon_bytes"] == 11
    assert roundtrip["cgroup_file_bytes"] == 12
    assert roundtrip["cgroup_kernel_bytes"] == 13
    assert roundtrip["cgroup_slab_bytes"] == 14
    assert roundtrip["cgroup_memory_stat"] == {"anon": 11, "file": 12, "kernel": 13, "slab": 14}
    assert roundtrip["available_memory_percent"] == 50.5
