from __future__ import annotations

from adaos.services.supervisor_memory import MemoryTelemetrySample


def test_memory_telemetry_preserves_process_family_and_cgroup_breakdown() -> None:
    payload = {
        "sampled_at": 100.0,
        "managed_pid": 123,
        "process_rss_bytes": 10,
        "family_rss_bytes": 20,
        "process_tree": {
            "available": True,
            "children": [{"pid": 124, "rss_bytes": 9, "skill_runtime": "demo_skill"}],
            "children_total": 1,
        },
        "cgroup_memory_current_bytes": 30,
        "cgroup_anon_bytes": 11,
        "cgroup_file_bytes": 12,
        "cgroup_kernel_bytes": 13,
        "cgroup_slab_bytes": 14,
        "cgroup_memory_stat": {"anon": 11, "file": 12, "kernel": 13, "slab": 14},
        "available_memory_bytes": 40,
        "available_memory_percent": 50.5,
        "baseline_scope_key": "runtime:rt-a-1",
        "baseline_pid": 123,
        "baseline_phase": "mature",
        "baseline_age_sec": 300.0,
        "baseline_warmup_sec": 120.0,
        "baseline_maturity_slope_threshold_bytes_per_min": 1234.0,
        "baseline_last_adjustment_reason": "warmup_matured",
        "baseline_adjustment_total": 1,
    }

    roundtrip = MemoryTelemetrySample.from_dict(payload).to_dict()

    assert roundtrip["process_rss_bytes"] == 10
    assert roundtrip["family_rss_bytes"] == 20
    assert roundtrip["process_tree"]["children"][0]["skill_runtime"] == "demo_skill"
    assert roundtrip["cgroup_memory_current_bytes"] == 30
    assert roundtrip["cgroup_anon_bytes"] == 11
    assert roundtrip["cgroup_file_bytes"] == 12
    assert roundtrip["cgroup_kernel_bytes"] == 13
    assert roundtrip["cgroup_slab_bytes"] == 14
    assert roundtrip["cgroup_memory_stat"] == {"anon": 11, "file": 12, "kernel": 13, "slab": 14}
    assert roundtrip["available_memory_percent"] == 50.5
    assert roundtrip["baseline_scope_key"] == "runtime:rt-a-1"
    assert roundtrip["baseline_pid"] == 123
    assert roundtrip["baseline_phase"] == "mature"
    assert roundtrip["baseline_age_sec"] == 300.0
    assert roundtrip["baseline_warmup_sec"] == 120.0
    assert roundtrip["baseline_maturity_slope_threshold_bytes_per_min"] == 1234.0
    assert roundtrip["baseline_last_adjustment_reason"] == "warmup_matured"
    assert roundtrip["baseline_adjustment_total"] == 1
