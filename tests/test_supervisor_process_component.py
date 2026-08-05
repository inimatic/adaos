from __future__ import annotations

from types import SimpleNamespace

from adaos.apps.supervisor_runtime import ProcessSupervisor


def test_process_supervisor_describes_managed_process() -> None:
    proc = SimpleNamespace(pid=42, args=["python", "-m", "adaos.apps.api"], cwd="/runtime", poll=lambda: None)

    result = ProcessSupervisor(None).describe(proc)

    assert result["managed_pid"] == 42
    assert result["managed_alive"] is True
    assert result["managed_executable"] == "python"
    assert result["managed_cwd"] == "/runtime"


def test_process_supervisor_finds_listener_owner() -> None:
    connection = SimpleNamespace(
        status="LISTEN",
        laddr=SimpleNamespace(ip="0.0.0.0", port=8777),
        pid=123,
    )
    fake_psutil = SimpleNamespace(net_connections=lambda kind: [connection])

    assert ProcessSupervisor(fake_psutil).listener_owner_pid("127.0.0.1", 8777) == 123
