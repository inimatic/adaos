from __future__ import annotations

import asyncio
from dataclasses import dataclass

from adaos.sdk.data.projections import (
    DirtyRouter,
    ProjectionRuntime,
    ProjectionSlot,
    StreamReceiver,
    stable_payload_fingerprint,
)


class _FakeSubnet:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object, str | None]] = []

    async def set_async(self, slot: str, value: object, *, webspace_id: str | None = None) -> None:
        self.calls.append((slot, value, webspace_id))


@dataclass
class _Payload:
    name: str
    values: list[int]


def test_stable_payload_fingerprint_is_order_independent_for_mappings() -> None:
    left = {"b": 2, "a": {"z": 1, "y": [3, 2, 1]}}
    right = {"a": {"y": [3, 2, 1], "z": 1}, "b": 2}

    assert stable_payload_fingerprint(left) == stable_payload_fingerprint(right)
    assert stable_payload_fingerprint(_Payload("demo", [1, 2])) == stable_payload_fingerprint(
        {"name": "demo", "values": [1, 2]}
    )


def test_projection_runtime_writes_once_and_skips_identical_even_when_forced() -> None:
    subnet = _FakeSubnet()
    runtime = ProjectionRuntime("browsers_skill", ctx_subnet=subnet)
    slot = ProjectionSlot("browsers.summary", "data/browsers/summary")

    first = asyncio.run(runtime.set_if_changed(slot, {"count": 1}, webspace_id="desktop"))
    second = asyncio.run(runtime.set_if_changed(slot, {"count": 1}, webspace_id="desktop"))
    forced = asyncio.run(
        runtime.set_if_changed(slot, {"count": 1}, webspace_id="desktop", force=True, reason="reload")
    )

    assert first.written is True
    assert second.skipped is True
    assert forced.skipped is True
    assert forced.force is True
    assert subnet.calls == [("browsers.summary", {"count": 1}, "desktop")]

    diagnostics = runtime.diagnostics_snapshot()
    assert diagnostics["applied_total"] == 1
    assert diagnostics["skipped_unchanged_total"] == 2
    assert diagnostics["by_slot"]["browsers.summary"]["applied_total"] == 1


def test_projection_runtime_tracks_webspace_state_independently() -> None:
    subnet = _FakeSubnet()
    runtime = ProjectionRuntime("browsers_skill", ctx_subnet=subnet)

    asyncio.run(runtime.set_if_changed("browsers.summary", {"count": 1}, webspace_id="desktop"))
    asyncio.run(runtime.set_if_changed("browsers.summary", {"count": 1}, webspace_id="ops"))
    asyncio.run(runtime.set_if_changed("browsers.summary", {"count": 1}, webspace_id="desktop"))

    assert subnet.calls == [
        ("browsers.summary", {"count": 1}, "desktop"),
        ("browsers.summary", {"count": 1}, "ops"),
    ]
    assert runtime.diagnostics_snapshot()["fingerprint_entries"] == 2


def test_projection_runtime_writes_when_payload_changes() -> None:
    subnet = _FakeSubnet()
    runtime = ProjectionRuntime("infrastate_skill", ctx_subnet=subnet)

    first = asyncio.run(runtime.set_if_changed("infrastate.summary", {"state": "ok"}, webspace_id="desktop"))
    second = asyncio.run(runtime.set_if_changed("infrastate.summary", {"state": "warn"}, webspace_id="desktop"))

    assert first.written is True
    assert second.written is True
    assert [call[1] for call in subnet.calls] == [{"state": "ok"}, {"state": "warn"}]


def test_dirty_router_matches_exact_and_prefix_patterns() -> None:
    router = (
        DirtyRouter()
        .on("operations.*")
        .dirty("operations.active")
        .on("browser.session.changed", "device.registered")
        .dirty("runtime.status", "summary")
    )

    assert router.dirty_for("operations.started") == {"operations.active"}
    assert router.dirty_for("browser.session.changed") == {"runtime.status", "summary"}
    assert router.dirty_for("skills.registry.changed") == set()


def test_stream_receiver_is_declarative_type() -> None:
    receiver = StreamReceiver("infrastate.logs.recent", min_interval_s=0.5)

    assert receiver.name == "infrastate.logs.recent"
    assert receiver.min_interval_s == 0.5
