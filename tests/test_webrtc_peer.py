from __future__ import annotations

import asyncio
import pytest
import importlib
import json
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace

from adaos.services.webio_snapshot_demand import clear_snapshot_demand_for_tests


def _load_peer_module(monkeypatch):
    fake_aiortc = ModuleType("aiortc")

    class DummyRTCPeerConnection:
        def __init__(self, configuration=None):
            self.configuration = configuration
            self.connectionState = "new"
            self.remoteDescription = None
            self.localDescription = None

        def on(self, _event):
            def decorator(fn):
                return fn

            return decorator

        async def setRemoteDescription(self, description):
            self.remoteDescription = description

        async def createAnswer(self):
            return DummyRTCSessionDescription(sdp="draft-answer-sdp", type="answer")

        async def setLocalDescription(self, description):
            self.localDescription = DummyRTCSessionDescription(
                sdp="final-local-answer-sdp",
                type=description.type,
            )

        async def addIceCandidate(self, candidate):
            self.lastIceCandidate = candidate

        async def close(self):
            self.connectionState = "closed"

    class DummyRTCSessionDescription:
        def __init__(self, sdp: str, type: str):
            self.sdp = sdp
            self.type = type

    class DummyRTCIceServer:
        def __init__(self, urls):
            self.urls = urls

    class DummyRTCConfiguration:
        def __init__(self, iceServers):
            self.iceServers = iceServers

    fake_aiortc.RTCPeerConnection = DummyRTCPeerConnection
    fake_aiortc.RTCSessionDescription = DummyRTCSessionDescription
    fake_aiortc.RTCIceCandidate = object
    fake_aiortc.RTCConfiguration = DummyRTCConfiguration
    fake_aiortc.RTCIceServer = DummyRTCIceServer

    fake_aiortc_contrib = ModuleType("aiortc.contrib")
    fake_aiortc_contrib_media = ModuleType("aiortc.contrib.media")

    class DummyMediaRelay:
        def subscribe(self, track):
            return track

    fake_aiortc_contrib_media.MediaRelay = DummyMediaRelay

    fake_aiortc_sdp = ModuleType("aiortc.sdp")
    fake_aiortc_sdp.candidate_from_sdp = (
        lambda line: SimpleNamespace(candidate=line, sdpMid=None, sdpMLineIndex=None)
    )

    fake_yjs_adapter = ModuleType("adaos.services.webrtc.yjs_adapter")

    class DummyDataChannelYjsAdapter:
        def __init__(self, dc, webspace_id: str, *, device_id: str | None = None, peer_id: str | None = None, desktop_principal=None):
            self.dc = dc
            self.webspace_id = webspace_id
            self.device_id = device_id
            self.peer_id = peer_id

        def close(self) -> None:
            return None

        async def serve(self) -> None:
            return None

    fake_yjs_adapter.DataChannelYjsAdapter = DummyDataChannelYjsAdapter

    fake_media_library = ModuleType("adaos.services.media_library")
    fake_media_library.ROOT_MEDIA_RELAY_MAX_UPLOAD_BYTES = 10_000_000
    fake_media_library.guess_media_type = lambda name: "video/mp4"
    fake_media_library.media_file_path = lambda name: Path(name or "media.bin")

    fake_agent_context = ModuleType("adaos.services.agent_context")
    fake_agent_context.get_ctx = lambda: SimpleNamespace(bus=None)

    fake_eventbus = ModuleType("adaos.services.eventbus")
    fake_eventbus.emit = lambda *args, **kwargs: None

    monkeypatch.setitem(sys.modules, "aiortc", fake_aiortc)
    monkeypatch.setitem(sys.modules, "aiortc.contrib", fake_aiortc_contrib)
    monkeypatch.setitem(sys.modules, "aiortc.contrib.media", fake_aiortc_contrib_media)
    monkeypatch.setitem(sys.modules, "aiortc.sdp", fake_aiortc_sdp)
    monkeypatch.setitem(sys.modules, "adaos.services.webrtc.yjs_adapter", fake_yjs_adapter)
    monkeypatch.setitem(sys.modules, "adaos.services.media_library", fake_media_library)
    monkeypatch.setitem(sys.modules, "adaos.services.agent_context", fake_agent_context)
    monkeypatch.setitem(sys.modules, "adaos.services.eventbus", fake_eventbus)
    monkeypatch.delitem(sys.modules, "adaos.services.webrtc.peer", raising=False)

    # Transport lifecycle tests isolate admission; the actual policy and every
    # ingress are exercised separately in test_desktop_access_ingress.py.
    from adaos.services.desktop_access import DesktopAccessService
    monkeypatch.setattr(DesktopAccessService, "__init__", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(DesktopAccessService, "require", lambda *args, **kwargs: None)
    monkeypatch.setattr(DesktopAccessService, "require_attachment", lambda *args, **kwargs: None)
    import adaos.services.desktop_access as desktop_access
    monkeypatch.setattr(desktop_access, "filter_desktop_topics", lambda _, topics: topics if isinstance(topics, list) else [])
    module = importlib.import_module("adaos.services.webrtc.peer")
    return importlib.reload(module)


def test_handle_rtc_offer_reuses_existing_clean_peer(monkeypatch) -> None:
    peer_mod = _load_peer_module(monkeypatch)

    class ExistingPeer:
        def __init__(self) -> None:
            self.pc = SimpleNamespace(connectionState="connected")
            self.webspace_id = "desk-old"
            self._send_ice = None
            self.close_called = False
            self.handled_offers: list[tuple[str, str]] = []
            self.emitted_reasons: list[str] = []

        async def handle_offer(self, sdp: str, type: str = "offer") -> dict[str, str]:
            self.handled_offers.append((sdp, type))
            return {"sdp": "answer-sdp", "type": "answer"}

        async def close(self) -> None:
            self.close_called = True

        def is_reusable_for_offer(self) -> bool:
            return True

        def _emit_state_event(self, *, reason: str) -> None:
            self.emitted_reasons.append(reason)

        def _set_webspace_id(self, webspace_id: str, *, reason: str) -> bool:
            self.webspace_id = webspace_id
            self.emitted_reasons.append(f"webspace:{reason}")
            return True

    existing = ExistingPeer()
    peer_mod._peers.clear()
    peer_mod._peers["browser-1"] = existing

    async def send_ice_cb(candidate: dict[str, object]) -> None:
        return None

    answer = asyncio.run(
        peer_mod.handle_rtc_offer(
            offer_sdp="offer-sdp",
            offer_type="offer",
            device_id="browser-1",
            webspace_id="desk-next",
            send_ice_cb=send_ice_cb,
        )
    )

    assert answer == {"sdp": "answer-sdp", "type": "answer"}
    assert peer_mod._peers["browser-1"] is existing
    assert existing.handled_offers == [("offer-sdp", "offer")]
    assert existing.close_called is False
    assert existing.webspace_id == "desk-next"
    assert existing._send_ice is send_ice_cb
    assert existing.emitted_reasons == ["webspace:offer.renegotiate"]


@pytest.mark.parametrize('old_user,new_user', [(None, 'owner'), ('owner', None), ('owner', 'other')])
def test_signaling_identity_change_cannot_take_over_existing_peer(monkeypatch, old_user, new_user):
    from adaos.domain.personalization_access import SubjectRef
    peer_mod = _load_peer_module(monkeypatch)
    old_actor = SubjectRef('user', old_user) if old_user else None
    new_actor = SubjectRef('user', new_user) if new_user else None
    closed = []
    async def close(): closed.append(True)
    from adaos.services.desktop_access import DesktopPrincipal
    existing = SimpleNamespace(pc=SimpleNamespace(connectionState='connected'), _verified_owner=old_actor,
        _desktop_principal=DesktopPrincipal(old_actor) if old_actor else None,
        is_reusable_for_offer=lambda: True, close=close)

    class Peer:
        def __init__(self, device_id, webspace_id, send, generation_id=None):
            self.device_id, self.webspace_id, self.generation_id = device_id, webspace_id, generation_id
        def _emit_state_event(self, **_): pass
        async def handle_offer(self, *_): return {'sdp': 'new-answer', 'type': 'answer'}

    async def send(_): pass
    monkeypatch.setattr(peer_mod, 'HubPeer', Peer)
    peer_mod._peers.clear()
    peer_mod._peers['browser'] = existing
    with pytest.raises(PermissionError, match='peer_principal_mismatch'):
        asyncio.run(peer_mod.handle_rtc_offer('offer', 'offer', 'browser', 'desktop', send, verified_owner=new_actor))
    assert closed == []
    assert peer_mod._peers['browser'] is existing
    assert existing._verified_owner == old_actor


def test_handle_rtc_offer_constructs_new_peer_off_event_loop_thread(monkeypatch) -> None:
    peer_mod = _load_peer_module(monkeypatch)
    owner_thread = threading.get_ident()
    construction_threads: list[int] = []

    class NewPeer:
        def __init__(self, device_id, webspace_id, send_ice_cb, generation_id=None) -> None:
            construction_threads.append(threading.get_ident())
            self.device_id = device_id
            self.webspace_id = webspace_id
            self._send_ice = send_ice_cb
            self.generation_id = generation_id
            self.pc = SimpleNamespace(connectionState="new")

        async def handle_offer(self, sdp: str, type: str = "offer") -> dict[str, str]:
            return {"sdp": f"answer:{sdp}", "type": type}

        def _emit_state_event(self, *, reason: str) -> None:  # noqa: ARG002
            return None

    monkeypatch.setattr(peer_mod, "HubPeer", NewPeer)
    peer_mod._peers.clear()

    async def send_ice_cb(candidate: dict[str, object]) -> None:  # noqa: ARG001
        return None

    answer = asyncio.run(
        peer_mod.handle_rtc_offer(
            offer_sdp="offer-sdp",
            offer_type="offer",
            device_id="browser-threaded",
            webspace_id="desktop",
            send_ice_cb=send_ice_cb,
        )
    )

    assert answer == {"sdp": "answer:offer-sdp", "type": "offer"}
    assert len(construction_threads) == 1
    assert construction_threads[0] != owner_thread


def test_fresh_generation_replaces_connecting_peer(monkeypatch) -> None:
    peer_mod = _load_peer_module(monkeypatch)

    class ExistingPeer:
        def __init__(self) -> None:
            self.pc = SimpleNamespace(connectionState="connecting")
            self.generation_id = "generation-old"
            self.closed = False

        def _connection_state(self) -> str:
            return "connecting"

        def is_reusable_for_offer(self) -> bool:
            return True

        async def close(self) -> None:
            self.closed = True

    class NewPeer:
        def __init__(self, device_id, webspace_id, send_ice_cb, generation_id=None) -> None:
            self.device_id = device_id
            self.webspace_id = webspace_id
            self._send_ice = send_ice_cb
            self.generation_id = generation_id
            self.pc = SimpleNamespace(connectionState="new")
            self.emitted_reasons: list[str] = []

        async def handle_offer(self, sdp: str, type: str = "offer") -> dict[str, str]:
            return {"sdp": "fresh-answer", "type": "answer"}

        async def add_ice_candidate(self, candidate) -> None:
            return None

        def _emit_state_event(self, *, reason: str) -> None:
            self.emitted_reasons.append(reason)

    existing = ExistingPeer()
    monkeypatch.setattr(peer_mod, "HubPeer", NewPeer)
    peer_mod._peers.clear()
    peer_mod._peers["browser-fresh"] = existing

    async def send_ice_cb(candidate: dict[str, object]) -> None:
        return None

    answer = asyncio.run(
        peer_mod.handle_rtc_offer(
            offer_sdp="offer-sdp",
            offer_type="offer",
            device_id="browser-fresh",
            webspace_id="desktop",
            send_ice_cb=send_ice_cb,
            generation_id="generation-new",
            negotiation_mode="fresh_peer",
        )
    )

    assert existing.closed is True
    assert peer_mod._peers["browser-fresh"] is not existing
    assert peer_mod._peers["browser-fresh"].generation_id == "generation-new"
    assert answer["generation_id"] == "generation-new"


def test_browser_tabs_with_one_device_keep_independent_peers(monkeypatch) -> None:
    peer_mod = _load_peer_module(monkeypatch)

    class NewPeer:
        def __init__(self, device_id, webspace_id, send_ice_cb, generation_id=None) -> None:
            self.device_id = device_id
            self.webspace_id = webspace_id
            self._send_ice = send_ice_cb
            self.generation_id = generation_id
            self.pc = SimpleNamespace(connectionState="new")
            self.closed = False

        async def handle_offer(self, sdp: str, type: str = "offer") -> dict[str, str]:
            return {"sdp": f"answer:{sdp}", "type": "answer"}

        async def add_ice_candidate(self, candidate) -> None:
            return None

        async def close(self) -> None:
            self.closed = True

        def _emit_state_event(self, *, reason: str) -> None:
            return None

    monkeypatch.setattr(peer_mod, "HubPeer", NewPeer)
    peer_mod._peers.clear()

    async def send_ice_cb(candidate: dict[str, object]) -> None:
        return None

    for peer_id in ("peer-tab-a", "peer-tab-b"):
        asyncio.run(
            peer_mod.handle_rtc_offer(
                offer_sdp=peer_id,
                offer_type="offer",
                device_id="browser-shared",
                peer_id=peer_id,
                webspace_id="dev1-dev",
                send_ice_cb=send_ice_cb,
                generation_id=f"generation-{peer_id}",
                negotiation_mode="fresh_peer",
                browser_session_id=f"session-{peer_id}",
                client_build_id="build-tabs",
                client_build_version="0.0.267",
            )
        )

    assert set(peer_mod._peers) == {"peer-tab-a", "peer-tab-b"}
    assert {peer.device_id for peer in peer_mod._peers.values()} == {"browser-shared"}
    assert all(peer.closed is False for peer in peer_mod._peers.values())
    assert {
        peer.browser_session_id for peer in peer_mod._peers.values()
    } == {"session-peer-tab-a", "session-peer-tab-b"}
    snapshot = peer_mod.webrtc_peer_snapshot(now_ts=123.0)
    assert {
        row["browser_session_id"] for row in snapshot["peers"]
    } == {"session-peer-tab-a", "session-peer-tab-b"}
    assert {row["client_build_id"] for row in snapshot["peers"]} == {"build-tabs"}


def test_ice_restart_reuses_same_generation_peer(monkeypatch) -> None:
    peer_mod = _load_peer_module(monkeypatch)

    class ExistingPeer:
        def __init__(self) -> None:
            self.device_id = "browser-restart"
            self.pc = SimpleNamespace(connectionState="disconnected")
            self.generation_id = "generation-same"
            self.webspace_id = "desktop"
            self._send_ice = None
            self.closed = False
            self.handled = 0

        def _connection_state(self) -> str:
            return "disconnected"

        def is_reusable_for_offer(self) -> bool:
            return False

        def _set_webspace_id(self, webspace_id: str, *, reason: str) -> bool:
            self.webspace_id = webspace_id
            return True

        async def handle_offer(self, sdp: str, type: str = "offer") -> dict[str, str]:
            self.handled += 1
            return {"sdp": "restart-answer", "type": "answer"}

        async def add_ice_candidate(self, candidate) -> None:
            return None

        async def close(self) -> None:
            self.closed = True

    existing = ExistingPeer()
    peer_mod._peers.clear()
    peer_mod._peers["browser-restart"] = existing

    async def send_ice_cb(candidate: dict[str, object]) -> None:
        return None

    answer = asyncio.run(
        peer_mod.handle_rtc_offer(
            offer_sdp="restart-offer",
            offer_type="offer",
            device_id="browser-restart",
            webspace_id="desktop",
            send_ice_cb=send_ice_cb,
            generation_id="generation-same",
            negotiation_mode="ice_restart",
        )
    )

    assert peer_mod._peers["browser-restart"] is existing
    assert existing.closed is False
    assert existing.handled == 1
    assert answer["generation_id"] == "generation-same"


def test_remote_ice_is_buffered_until_matching_offer(monkeypatch) -> None:
    peer_mod = _load_peer_module(monkeypatch)

    class NewPeer:
        def __init__(self, device_id, webspace_id, send_ice_cb, generation_id=None) -> None:
            self.device_id = device_id
            self.webspace_id = webspace_id
            self._send_ice = send_ice_cb
            self.generation_id = generation_id
            self.pc = SimpleNamespace(connectionState="new")
            self.candidates: list[dict[str, object]] = []

        async def handle_offer(self, sdp: str, type: str = "offer") -> dict[str, str]:
            return {"sdp": "answer", "type": "answer"}

        async def add_ice_candidate(self, candidate) -> None:
            self.candidates.append(candidate)

        def _emit_state_event(self, *, reason: str) -> None:
            return None

    monkeypatch.setattr(peer_mod, "HubPeer", NewPeer)
    peer_mod._peers.clear()
    candidate = {"candidate": "candidate:1 1 udp 1 192.0.2.1 5000 typ host"}

    asyncio.run(
        peer_mod.handle_remote_ice(
            "browser-buffered",
            candidate,
            generation_id="generation-buffered",
        )
    )
    assert peer_mod._pending_remote_ice

    async def send_ice_cb(item: dict[str, object]) -> None:
        return None

    asyncio.run(
        peer_mod.handle_rtc_offer(
            offer_sdp="offer",
            offer_type="offer",
            device_id="browser-buffered",
            webspace_id="desktop",
            send_ice_cb=send_ice_cb,
            generation_id="generation-buffered",
            negotiation_mode="fresh_peer",
        )
    )

    peer = peer_mod._peers["browser-buffered"]
    assert peer.candidates == [candidate]
    assert peer_mod._pending_remote_ice == {}


def test_hub_peer_is_not_reusable_with_live_channels(monkeypatch) -> None:
    peer_mod = _load_peer_module(monkeypatch)

    async def send_ice_cb(candidate: dict[str, object]) -> None:
        return None

    peer = peer_mod.HubPeer("browser-live", "default", send_ice_cb)
    peer._events_channel = SimpleNamespace(readyState="open")

    try:
        assert peer.is_reusable_for_offer() is False
    finally:
        asyncio.run(peer.close())


def test_connected_peer_waits_for_datachannel_grace_before_stale_prune(monkeypatch) -> None:
    peer_mod = _load_peer_module(monkeypatch)

    async def send_ice_cb(candidate: dict[str, object]) -> None:
        return None

    peer = peer_mod.HubPeer("browser-negotiating", "default", send_ice_cb)
    peer.pc.connectionState = "connected"
    peer._created_at = 100.0
    peer._last_activity_at = 100.0
    peer._last_state_change_at = 100.0

    try:
        assert peer.is_stale(now_ts=124.0) is False
        assert peer.is_stale(now_ts=131.0) is True
    finally:
        asyncio.run(peer.close())


def test_hub_peer_handle_offer_returns_final_local_description(monkeypatch) -> None:
    peer_mod = _load_peer_module(monkeypatch)

    async def send_ice_cb(candidate: dict[str, object]) -> None:
        return None

    peer = peer_mod.HubPeer("browser-answer", "desktop", send_ice_cb)

    try:
        answer = asyncio.run(peer.handle_offer("offer-sdp", "offer"))

        assert answer == {"sdp": "final-local-answer-sdp", "type": "answer"}
        assert peer.pc.remoteDescription.sdp == "offer-sdp"
        assert peer.pc.localDescription.sdp == "final-local-answer-sdp"
    finally:
        asyncio.run(peer.close())


def test_setup_yjs_channels_keep_independent_desktop_bindings(monkeypatch) -> None:
    peer_mod = _load_peer_module(monkeypatch)

    class TrackingAdapter:
        def __init__(self, dc, webspace_id: str, *, device_id: str | None = None, peer_id: str | None = None, desktop_principal=None):
            self.dc = dc
            self.webspace_id = webspace_id
            self.device_id = device_id
            self.peer_id = peer_id
            self.closed = False

        def close(self) -> None:
            self.closed = True

        async def serve(self) -> None:
            await asyncio.sleep(3600)

    class DummyChannel:
        def __init__(self, label: str = "yjs") -> None:
            self.label = label
            self.readyState = "open"
            self.close_called = 0
            self.handlers = {}

        def on(self, event):
            def decorator(fn):
                self.handlers[event] = fn
                return fn

            return decorator

        def close(self) -> None:
            self.close_called += 1
            handler = self.handlers.get("close")
            if callable(handler):
                handler()

    peer_mod.DataChannelYjsAdapter = TrackingAdapter

    async def send_ice_cb(candidate: dict[str, object]) -> None:
        return None

    async def _run() -> None:
        peer = peer_mod.HubPeer("browser-yjs", "default", send_ice_cb)
        first = DummyChannel("yjs:desktop-a:one")
        second = DummyChannel("yjs:desktop-b:two")
        peer._setup_yjs_channel(first)
        await asyncio.sleep(0.05)
        old_adapter = peer._yjs_adapter
        old_task = peer._yjs_task
        assert old_adapter is not None
        assert old_task is not None

        peer._setup_yjs_channel(second)
        await asyncio.sleep(0.05)

        assert old_adapter.closed is False
        assert old_adapter.webspace_id == "desktop-a"
        assert peer._yjs_adapter.webspace_id == "desktop-b"
        assert len(peer._desktop_channels) == 2
        assert first.close_called == 0
        assert not old_task.done()
        assert peer._yjs_channel is second
        first.close()
        assert peer._yjs_channel is second
        await peer.close()

    asyncio.run(_run())


def test_yjs_sync_arriving_during_admission_is_buffered_without_a_retry(monkeypatch):
    peer_mod = _load_peer_module(monkeypatch)
    from adaos.services.desktop_access import DesktopAccessService
    entered, release = threading.Event(), threading.Event()
    def admit(*args, **kwargs):
        entered.set()
        assert release.wait(2)
    monkeypatch.setattr(DesktopAccessService, 'require_attachment', admit)
    received = []
    class Adapter:
        def __init__(self, *args, **kwargs): pass
        def _enqueue(self, frame): received.append(frame)
        def close(self): pass
        async def serve(self): await asyncio.Future()
    class Channel:
        label = 'yjs:desktop-b:buffered-first-sync'
        readyState = 'open'
        def __init__(self): self.handlers = {}
        def on(self, name):
            def bind(handler): self.handlers[name] = handler; return handler
            return bind
        def close(self): self.readyState = 'closed'
    monkeypatch.setattr(peer_mod, 'DataChannelYjsAdapter', Adapter)
    async def run():
        async def send(_): pass
        peer = peer_mod.HubPeer('browser', 'desktop-a', send)
        channel = Channel()
        peer._setup_yjs_channel(channel)
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            channel.handlers['message'](b'initial-sync-before-policy-completed')
            assert received == []
            release.set()
            for _ in range(100):
                if received: break
                await asyncio.sleep(.005)
            assert received == [b'initial-sync-before-policy-completed']
        finally:
            release.set()
            await peer.close()
    asyncio.run(run())


def test_media_datachannel_download_tracks_delivery_activity(monkeypatch, tmp_path: Path) -> None:
    peer_mod = _load_peer_module(monkeypatch)
    media_file = tmp_path / "movie.mp4"
    media_file.write_bytes(b"x" * (64 * 1024 + 17))
    activity: list[tuple[str, str]] = []

    monkeypatch.setattr(peer_mod, "media_file_path", lambda _name: media_file)
    monkeypatch.setattr(
        peer_mod,
        "begin_media_delivery",
        lambda *, media_type: activity.append(("begin", media_type)) or "lease-1",
    )
    monkeypatch.setattr(
        peer_mod,
        "touch_media_delivery",
        lambda lease_id: activity.append(("touch", lease_id)),
    )
    monkeypatch.setattr(
        peer_mod,
        "end_media_delivery",
        lambda lease_id: activity.append(("end", lease_id)),
    )

    class DummyChannel:
        readyState = "open"

        def __init__(self) -> None:
            self.handlers = {}
            self.sent: list[str | bytes] = []

        def on(self, event):
            def decorator(fn):
                self.handlers[event] = fn
                return fn

            return decorator

        def send(self, payload: str | bytes) -> None:
            self.sent.append(payload)

    async def send_ice_cb(candidate: dict[str, object]) -> None:
        return None

    async def _run() -> None:
        peer = peer_mod.HubPeer("browser-media", "desktop", send_ice_cb)
        channel = DummyChannel()
        peer._setup_media_channel(channel)
        channel.handlers["message"](
            json.dumps(
                {
                    "ch": "media",
                    "t": "download_start",
                    "requestId": "request-1",
                    "filename": "movie.mp4",
                }
            )
        )
        for _ in range(20):
            await asyncio.sleep(0)
            messages = [json.loads(item) for item in channel.sent if isinstance(item, str)]
            if any(item.get("t") == "download_done" for item in messages):
                break

        assert activity == [
            ("begin", "video/mp4"),
            ("touch", "lease-1"),
            ("touch", "lease-1"),
            ("end", "lease-1"),
        ]
        chunks = [item for item in channel.sent if isinstance(item, bytes)]
        assert b"".join(chunks) == media_file.read_bytes()
        await peer.close()

    asyncio.run(_run())


def test_events_webspace_change_preserves_existing_yjs_binding(monkeypatch) -> None:
    peer_mod = _load_peer_module(monkeypatch)
    seen_commands: list[dict[str, object]] = []

    class TrackingAdapter:
        def __init__(self, dc, webspace_id: str, *, device_id: str | None = None, peer_id: str | None = None, desktop_principal=None):
            self.dc = dc
            self.webspace_id = webspace_id
            self.device_id = device_id
            self.peer_id = peer_id
            self.closed = False

        def close(self) -> None:
            self.closed = True

        async def serve(self) -> None:
            await asyncio.sleep(3600)

    class DummyChannel:
        def __init__(self, label: str) -> None:
            self.label = label
            self.readyState = "open"
            self.close_called = 0
            self.handlers = {}
            self.sent: list[str] = []

        def on(self, event):
            def decorator(fn):
                self.handlers[event] = fn
                return fn

            return decorator

        def send(self, payload: str) -> None:
            self.sent.append(payload)

        def close(self) -> None:
            self.close_called += 1
            handler = self.handlers.get("close")
            if callable(handler):
                handler()

    fake_gateway = ModuleType("adaos.services.yjs.gateway_ws")

    async def fake_process_events_command(
        *,
        kind: str,
        cmd_id: str,
        payload: dict[str, object],
        device_id: str,
        webspace_id: str,
        send_response,
        client_label: str | None = None,
        verified_owner=None,
        desktop_principal=None,
    ) -> str:
        seen_commands.append(
            {
                "kind": kind,
                "cmd_id": cmd_id,
                "payload": payload,
                "device_id": device_id,
                "webspace_id": webspace_id,
                "client_label": client_label,
                "verified_owner": verified_owner,
            }
        )
        await send_response({"ch": "events", "t": "ack", "id": cmd_id, "ok": True, "data": {"webspace_id": "desktop"}})
        return "desktop"

    fake_gateway.process_events_command = fake_process_events_command
    monkeypatch.setitem(sys.modules, "adaos.services.yjs.gateway_ws", fake_gateway)
    peer_mod.DataChannelYjsAdapter = TrackingAdapter

    async def send_ice_cb(candidate: dict[str, object]) -> None:
        return None

    async def _run() -> None:
        peer = peer_mod.HubPeer("browser-yjs", "desktop-dev", send_ice_cb)
        from adaos.domain.personalization_access import SubjectRef
        peer._verified_owner = SubjectRef('user', 'signaling-owner')
        yjs = DummyChannel("yjs")
        events = DummyChannel("events")
        peer._setup_yjs_channel(yjs)
        await asyncio.sleep(0.05)
        old_adapter = peer._yjs_adapter
        old_task = peer._yjs_task
        assert old_adapter is not None
        assert old_task is not None
        assert old_adapter.webspace_id == "desktop-dev"

        peer._setup_events_channel(events)
        events.handlers["message"](
            json.dumps(
                {
                    "ch": "events",
                    "t": "cmd",
                    "id": "cmd-1",
                    "kind": "device.register",
                    "payload": {"webspace_id": "desktop"},
                }
            )
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert seen_commands[-1]["webspace_id"] == "desktop"
        assert seen_commands[-1]["verified_owner"] == peer._verified_owner
        assert peer.webspace_id == "desktop"
        assert peer._yjs_channel is yjs
        assert peer._yjs_adapter is old_adapter
        assert peer._yjs_task is old_task
        assert old_adapter.closed is False
        assert yjs.close_called == 0
        assert not old_task.done()
        assert events.sent
        await peer.close()

    asyncio.run(_run())


def test_request_webio_stream_snapshots_extracts_node_qualified_receiver(monkeypatch) -> None:
    clear_snapshot_demand_for_tests()
    peer_mod = _load_peer_module(monkeypatch)
    published: list[tuple[str, dict[str, object], str]] = []

    peer_mod.get_ctx = lambda: SimpleNamespace(bus=object())
    peer_mod.bus_emit = lambda bus, topic, payload, source: published.append((topic, payload, source))

    peer_mod._request_webio_stream_snapshots(
        {"webio.stream.default.nodes.member-01.telemetry.feed"},
        transport="webrtc_data:events",
    )

    assert published == [
        (
            "webio.stream.snapshot.requested",
            {
                "topic": "webio.stream.default.nodes.member-01.telemetry.feed",
                "webspace_id": "default",
                "receiver": "telemetry.feed",
                "transport": "webrtc_data:events",
                "node_id": "member-01",
            },
            "webrtc.peer",
        )
    ]


def test_request_webio_stream_snapshots_extracts_global_node_receiver(monkeypatch) -> None:
    clear_snapshot_demand_for_tests()
    peer_mod = _load_peer_module(monkeypatch)
    published: list[tuple[str, dict[str, object], str]] = []

    peer_mod.get_ctx = lambda: SimpleNamespace(bus=object())
    peer_mod.bus_emit = lambda bus, topic, payload, source: published.append((topic, payload, source))

    peer_mod._request_webio_stream_snapshots(
        {"webio.stream.nodes.member-01.telemetry.feed"},
        transport="webrtc_data:events",
    )

    assert published == [
        (
            "webio.stream.snapshot.requested",
            {
                "topic": "webio.stream.nodes.member-01.telemetry.feed",
                "webspace_id": "default",
                "receiver": "telemetry.feed",
                "transport": "webrtc_data:events",
                "node_id": "member-01",
            },
            "webrtc.peer",
        )
    ]


def test_event_channel_topics_replace_node_slideshow_stream_with_hub_alias(monkeypatch) -> None:
    peer_mod = _load_peer_module(monkeypatch)

    topics = peer_mod._normalize_event_channel_topics(
        [
            "webio.stream.desktop.nodes.member-01.slideshow_skill.index",
            "webio.stream.nodes.member-02.slideshow_skill.folders",
            "webio.stream.desktop.nodes.member-03.weather.current",
            "webio.stream.desktop.slideshow_skill.preview",
            "core.update.status",
        ]
    )

    assert "webio.stream.desktop.nodes.member-01.slideshow_skill.index" not in topics
    assert "webio.stream.desktop.slideshow_skill.index" in topics
    assert "webio.stream.nodes.member-02.slideshow_skill.folders" not in topics
    assert "webio.stream.desktop.slideshow_skill.folders" in topics
    assert "webio.stream.desktop.nodes.member-03.weather.current" in topics
    assert "webio.stream.desktop.weather.current" not in topics
    assert "webio.stream.desktop.slideshow_skill.preview" in topics
    assert "core.update.status" in topics

    aliases = peer_mod._webio_stream_node_aliases(
        ["webio.stream.desktop.nodes.member-01.slideshow_skill.index"]
    )
    assert aliases == [
        {
            "source": "webio.stream.desktop.slideshow_skill.index",
            "target": "webio.stream.desktop.nodes.member-01.slideshow_skill.index",
            "node_id": "member-01",
        }
    ]


def test_request_webio_yjs_projection_snapshots_extracts_node_qualified_slot(monkeypatch) -> None:
    clear_snapshot_demand_for_tests()
    peer_mod = _load_peer_module(monkeypatch)
    published: list[tuple[str, dict[str, object], str]] = []

    peer_mod.get_ctx = lambda: SimpleNamespace(bus=object())
    peer_mod.bus_emit = lambda bus, topic, payload, source: published.append((topic, payload, source))

    peer_mod._request_webio_yjs_projection_snapshots(
        {"webio.yjs.default.nodes.member-01.infrastate.summary"},
        transport="webrtc_data:events",
        connection_id="peer-1",
    )

    assert published == [
        (
            "webio.yjs.snapshot.requested",
            {
                "topic": "webio.yjs.default.nodes.member-01.infrastate.summary",
                "webspace_id": "desktop",
                "slot": "infrastate.summary",
                "projection": "infrastate.summary",
                "node_id": "member-01",
                "target_node_id": "member-01",
                "_meta": {"webspace_id": "desktop", "target_node_id": "member-01"},
                "transport": "webrtc_data:events",
                "connection_id": "peer-1",
                "subscription_id": (
                    "webrtc_data:events:peer-1:"
                    "webio.yjs.default.nodes.member-01.infrastate.summary"
                ),
            },
            "webrtc.peer",
        )
    ]


def test_request_webio_yjs_projection_snapshots_dedupes_repeated_control_events(monkeypatch) -> None:
    clear_snapshot_demand_for_tests()
    peer_mod = _load_peer_module(monkeypatch)
    published: list[tuple[str, dict[str, object], str]] = []

    peer_mod._WEBIO_CONTROL_DEDUPE_RECENT.clear()
    peer_mod.get_ctx = lambda: SimpleNamespace(bus=object())
    peer_mod.bus_emit = lambda bus, topic, payload, source: published.append((topic, payload, source))

    for _ in range(2):
        peer_mod._request_webio_yjs_projection_snapshots(
            {"webio.yjs.default.nodes.member-01.infrastate.summary"},
            transport="webrtc_data:events",
        )

    assert len(published) == 1
    assert published[0][0] == "webio.yjs.snapshot.requested"
    peer_mod._WEBIO_CONTROL_DEDUPE_RECENT.clear()
    clear_snapshot_demand_for_tests()


def test_webio_yjs_projection_subscription_tracks_active_demand(monkeypatch) -> None:
    from adaos.sdk.data.projections import clear_projection_demand, has_projection_demand

    peer_mod = _load_peer_module(monkeypatch)
    published: list[tuple[str, dict[str, object], str]] = []

    clear_projection_demand()
    peer_mod.get_ctx = lambda: SimpleNamespace(bus=object())
    peer_mod.bus_emit = lambda bus, topic, payload, source: published.append((topic, payload, source))

    peer_mod._publish_webio_yjs_projection_subscription_change(
        {"webio.yjs.default.browsers.devices"},
        action="subscribed",
        transport="webrtc_data:events",
        connection_id="peer-1",
    )

    assert has_projection_demand("browsers.devices", webspace_id="desktop") is True
    assert published[0][0] == "webio.yjs.subscription.changed"
    assert published[0][1]["slot"] == "browsers.devices"

    peer_mod._publish_webio_yjs_projection_subscription_change(
        {"webio.yjs.default.browsers.devices"},
        action="unsubscribed",
        transport="webrtc_data:events",
        connection_id="peer-1",
    )

    assert has_projection_demand("browsers.devices", webspace_id="desktop") is False
    clear_projection_demand()


def test_handle_rtc_offer_replaces_failed_peer(monkeypatch) -> None:
    peer_mod = _load_peer_module(monkeypatch)

    class FailedPeer:
        def __init__(self) -> None:
            self.pc = SimpleNamespace(connectionState="failed")
            self.close_called = False

        async def close(self) -> None:
            self.close_called = True

    class NewPeer:
        def __init__(self, device_id: str, webspace_id: str, send_ice_cb, generation_id=None) -> None:
            self.device_id = device_id
            self.webspace_id = webspace_id
            self._send_ice = send_ice_cb
            self.generation_id = generation_id
            self.pc = SimpleNamespace(connectionState="new")
            self.handled_offers: list[tuple[str, str]] = []
            self.emitted_reasons: list[str] = []

        async def handle_offer(self, sdp: str, type: str = "offer") -> dict[str, str]:
            self.handled_offers.append((sdp, type))
            return {"sdp": "fresh-answer", "type": "answer"}

        def _emit_state_event(self, *, reason: str) -> None:
            self.emitted_reasons.append(reason)

    monkeypatch.setattr(peer_mod, "HubPeer", NewPeer)
    failed = FailedPeer()
    peer_mod._peers.clear()
    peer_mod._peers["browser-2"] = failed

    async def send_ice_cb(candidate: dict[str, object]) -> None:
        return None

    answer = asyncio.run(
        peer_mod.handle_rtc_offer(
            offer_sdp="offer-sdp",
            offer_type="offer",
            device_id="browser-2",
            webspace_id="desk",
            send_ice_cb=send_ice_cb,
        )
    )

    new_peer = peer_mod._peers["browser-2"]
    assert failed.close_called is True
    assert isinstance(new_peer, NewPeer)
    assert answer == {"sdp": "fresh-answer", "type": "answer"}
    assert new_peer.handled_offers == [("offer-sdp", "offer")]
    assert new_peer.webspace_id == "desk"
    assert new_peer._send_ice is send_ice_cb
    assert new_peer.emitted_reasons == ["offer.accepted"]


def test_handle_rtc_offer_replaces_disconnected_peer(monkeypatch) -> None:
    peer_mod = _load_peer_module(monkeypatch)

    class DisconnectedPeer:
        def __init__(self) -> None:
            self.pc = SimpleNamespace(connectionState="disconnected")
            self.close_called = False

        async def close(self) -> None:
            self.close_called = True

        def _connection_state(self) -> str:
            return "disconnected"

        def is_reusable_for_offer(self) -> bool:
            return False

    class NewPeer:
        def __init__(self, device_id: str, webspace_id: str, send_ice_cb, generation_id=None) -> None:
            self.device_id = device_id
            self.webspace_id = webspace_id
            self._send_ice = send_ice_cb
            self.generation_id = generation_id
            self.pc = SimpleNamespace(connectionState="new")
            self.handled_offers: list[tuple[str, str]] = []
            self.emitted_reasons: list[str] = []

        async def handle_offer(self, sdp: str, type: str = "offer") -> dict[str, str]:
            self.handled_offers.append((sdp, type))
            return {"sdp": "fresh-answer", "type": "answer"}

        def _emit_state_event(self, *, reason: str) -> None:
            self.emitted_reasons.append(reason)

    monkeypatch.setattr(peer_mod, "HubPeer", NewPeer)
    existing = DisconnectedPeer()
    peer_mod._peers.clear()
    peer_mod._peers["browser-3"] = existing

    async def send_ice_cb(candidate: dict[str, object]) -> None:
        return None

    answer = asyncio.run(
        peer_mod.handle_rtc_offer(
            offer_sdp="offer-sdp",
            offer_type="offer",
            device_id="browser-3",
            webspace_id="desk",
            send_ice_cb=send_ice_cb,
        )
    )

    new_peer = peer_mod._peers["browser-3"]
    assert existing.close_called is True
    assert isinstance(new_peer, NewPeer)
    assert answer == {"sdp": "fresh-answer", "type": "answer"}
    assert new_peer.handled_offers == [("offer-sdp", "offer")]
    assert new_peer.emitted_reasons == ["offer.accepted"]


def test_handle_rtc_offer_continues_when_existing_close_hangs(monkeypatch) -> None:
    peer_mod = _load_peer_module(monkeypatch)
    monkeypatch.setattr(peer_mod, "_REPLACE_CLOSE_TIMEOUT_SECONDS", 0.01)

    class HangingPeer:
        def __init__(self) -> None:
            self.pc = SimpleNamespace(connectionState="failed")
            self.close_called = False

        async def close(self) -> None:
            self.close_called = True
            await asyncio.sleep(3600)

        def _connection_state(self) -> str:
            return "failed"

        def is_reusable_for_offer(self) -> bool:
            return False

    class NewPeer:
        def __init__(self, device_id: str, webspace_id: str, send_ice_cb, generation_id=None) -> None:
            self.device_id = device_id
            self.webspace_id = webspace_id
            self._send_ice = send_ice_cb
            self.generation_id = generation_id
            self.pc = SimpleNamespace(connectionState="new")
            self.handled_offers: list[tuple[str, str]] = []
            self.emitted_reasons: list[str] = []

        async def handle_offer(self, sdp: str, type: str = "offer") -> dict[str, str]:
            self.handled_offers.append((sdp, type))
            return {"sdp": "fresh-answer", "type": "answer"}

        def _emit_state_event(self, *, reason: str) -> None:
            self.emitted_reasons.append(reason)

    monkeypatch.setattr(peer_mod, "HubPeer", NewPeer)
    hanging = HangingPeer()
    peer_mod._peers.clear()
    peer_mod._peers["browser-timeout"] = hanging

    async def send_ice_cb(candidate: dict[str, object]) -> None:
        return None

    answer = asyncio.run(
        peer_mod.handle_rtc_offer(
            offer_sdp="offer-sdp",
            offer_type="offer",
            device_id="browser-timeout",
            webspace_id="desk",
            send_ice_cb=send_ice_cb,
        )
    )

    new_peer = peer_mod._peers["browser-timeout"]
    assert hanging.close_called is True
    assert isinstance(new_peer, NewPeer)
    assert answer == {"sdp": "fresh-answer", "type": "answer"}
    assert new_peer.handled_offers == [("offer-sdp", "offer")]
    assert new_peer.emitted_reasons == ["offer.accepted"]


def test_close_peers_for_webspace_preserves_peer_and_other_desktop(monkeypatch):
    peer_mod = _load_peer_module(monkeypatch)
    closed = []
    class Channel:
        def __init__(self, name): self.name = name
        def close(self): closed.append(self.name)
    async def run():
        async def wait(): await asyncio.sleep(3600)
        task_a, task_b = asyncio.create_task(wait()), asyncio.create_task(wait())
        peer = SimpleNamespace(_desktop_channels={
            'a': (Channel('a'), SimpleNamespace(path='desktop-a', close=lambda: None), task_a),
            'b': (Channel('b'), SimpleNamespace(path='desktop-b', close=lambda: None), task_b)},
            _close_data_channel=peer_mod.HubPeer._close_data_channel)
        peer_mod._peers.clear()
        peer_mod._peers['browser'] = peer
        assert await peer_mod.close_peers_for_webspace('desktop-a') == 1
        assert task_a.cancelled()
        assert not task_b.done()
        assert peer_mod._peers['browser'] is peer
        task_b.cancel()
        await asyncio.gather(task_b, return_exceptions=True)
    asyncio.run(run())
    assert closed == ['a']


def test_webrtc_peer_snapshot_prunes_stale_peers(monkeypatch) -> None:
    peer_mod = _load_peer_module(monkeypatch)

    class StalePeer:
        def __init__(self, device_id: str, webspace_id: str) -> None:
            self.device_id = device_id
            self.webspace_id = webspace_id
            self.pc = SimpleNamespace(connectionState="disconnected")
            self._events_channel = None
            self._yjs_channel = None
            self._incoming_tracks = {}
            self._loopback_tracks = {}
            self.scheduled_reasons: list[str] = []

        def is_stale(self, *, now_ts=None) -> bool:
            return True

        def _schedule_close(self, *, reason: str, delay: float = 0.0) -> bool:
            self.scheduled_reasons.append(f"{reason}:{delay}")
            return True

        def _connection_state(self) -> str:
            return "disconnected"

        def _events_state(self) -> str:
            return "missing"

        def _yjs_state(self) -> str:
            return "missing"

    peer_mod._peers.clear()
    stale = StalePeer("browser-stale", "default")
    peer_mod._peers[stale.device_id] = stale

    snapshot = peer_mod.webrtc_peer_snapshot()

    assert snapshot["peer_total"] == 0
    assert snapshot["pruned_stale_peers"] == 1
    assert stale.device_id not in peer_mod._peers
    assert stale.scheduled_reasons == ["stale_peer_prune:0.0"]


def test_replaced_peer_does_not_emit_stale_state(monkeypatch) -> None:
    peer_mod = _load_peer_module(monkeypatch)
    emitted: list[dict[str, object]] = []
    old_peer = peer_mod.HubPeer.__new__(peer_mod.HubPeer)
    old_peer.device_id = "browser-1"
    old_peer.peer_id = "browser-1"
    old_peer.generation_id = "old"
    old_peer.snapshot_record = lambda: {"generation_id": "old"}
    peer_mod._peers.clear()
    peer_mod._peers[old_peer.peer_id] = object()
    monkeypatch.setattr(peer_mod, "get_ctx", lambda: SimpleNamespace(bus=object()))
    monkeypatch.setattr(peer_mod, "bus_emit", lambda _bus, _kind, payload, _source: emitted.append(payload))

    old_peer._emit_state_event(reason="connection_state:closed")

    assert emitted == []
