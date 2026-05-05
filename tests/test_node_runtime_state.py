from __future__ import annotations

import threading
from pathlib import Path

from adaos.services import node_runtime_state as mod


def test_save_node_runtime_state_preserves_fields_across_concurrent_writers(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(mod, "current_state_dir", lambda: tmp_path)

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def _writer_one() -> None:
        try:
            barrier.wait(timeout=5.0)
            for _ in range(25):
                mod.save_node_runtime_state(
                    role="member",
                    hub_url="https://ru.api.inimatic.com/hubs/sn_demo",
                    token="dev-local-token",
                )
        except BaseException as exc:  # pragma: no cover - test helper
            errors.append(exc)

    def _writer_two() -> None:
        try:
            barrier.wait(timeout=5.0)
            for _ in range(25):
                mod.save_node_runtime_state(member_hub_token="join-session-token")
        except BaseException as exc:  # pragma: no cover - test helper
            errors.append(exc)

    t1 = threading.Thread(target=_writer_one)
    t2 = threading.Thread(target=_writer_two)
    t1.start()
    t2.start()
    t1.join(timeout=10.0)
    t2.join(timeout=10.0)

    assert not errors
    payload = mod.load_node_runtime_state()
    assert payload["role"] == "member"
    assert payload["hub_url"] == "https://ru.api.inimatic.com/hubs/sn_demo"
    assert payload["token"] == "dev-local-token"
    assert payload["member_hub_token"] == "join-session-token"

