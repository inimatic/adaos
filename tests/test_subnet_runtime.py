from __future__ import annotations

import asyncio
from types import SimpleNamespace


class _MemberLinkClient:
    def __init__(self) -> None:
        self.start_total = 0
        self.stop_total = 0

    async def start(self) -> None:
        self.start_total += 1

    async def stop(self) -> None:
        self.stop_total += 1


def _app() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace())


def test_candidate_member_runtime_keeps_upstream_passive(monkeypatch) -> None:
    import adaos.services.subnet.runtime as runtime

    client = _MemberLinkClient()
    app = _app()
    monkeypatch.setattr(runtime, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(role="member")))
    monkeypatch.setattr(runtime, "runtime_transition_role", lambda: "candidate")
    monkeypatch.setattr(runtime, "get_member_link_client", lambda: client)

    asyncio.run(runtime.start_subnet_p2p(app))

    assert client.start_total == 0
    assert app.state.subnet_p2p["member_link_start"] == "deferred_candidate"


def test_active_member_runtime_starts_and_stops_upstream(monkeypatch) -> None:
    import adaos.services.subnet.runtime as runtime

    client = _MemberLinkClient()
    app = _app()
    monkeypatch.setattr(runtime, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(role="member")))
    monkeypatch.setattr(runtime, "runtime_transition_role", lambda: "active")
    monkeypatch.setattr(runtime, "get_member_link_client", lambda: client)

    asyncio.run(runtime.start_subnet_p2p(app))
    asyncio.run(runtime.stop_subnet_p2p(app))

    assert client.start_total == 1
    assert client.stop_total == 1
    assert app.state.subnet_p2p is None
