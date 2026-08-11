from __future__ import annotations

import base64
import heapq
import json
from pathlib import Path
from typing import Any, Iterator

from adaos.services.media_core import (
    MEDIA_RUNTIME_SCOPE,
    MEDIA_STORAGE_SUBPATH,
    MEDIA_STORE_SKILL_NAME,
    ROOT_MEDIA_RELAY_CHUNK_BYTES,
    ROOT_MEDIA_RELAY_MAX_UPLOAD_BYTES,
    ROOT_ROUTED_MEDIA_BODY_LIMIT_BYTES,
    SUPPORTED_MEDIA_EXTENSIONS,
    guess_media_type,
    media_resource_from_path,
    media_store_dir,
    media_store_file_path,
    media_store_runtime_env,
    sanitize_media_filename,
)
from adaos.services.media_capability import member_browser_direct_foundation
from adaos.services.router.media_routes import resolve_media_route_intent


MEDIA_SKILL_NAME = MEDIA_STORE_SKILL_NAME
MEDIA_LIBRARY_DEFAULT_PAGE_SIZE = 50
MEDIA_LIBRARY_MAX_PAGE_SIZE = 100
MEDIA_LIBRARY_MAX_OFFSET = 10_000
SUPPORTED_VIDEO_EXTENSIONS = SUPPORTED_MEDIA_EXTENSIONS


def media_runtime_env():
    return media_store_runtime_env()


def media_video_dir() -> Path:
    return media_store_dir()


def media_file_path(filename: str) -> Path:
    return media_store_file_path(filename)


def _media_item_from_path(path: Path, stat: Any) -> dict[str, Any]:
    return media_resource_from_path(
        path,
        source="media_server",
        resource_id=path.name,
    ).to_public_dict(include_internal=True)


def iter_media_files() -> Iterator[dict[str, Any]]:
    root = media_video_dir()
    for path in root.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_MEDIA_EXTENSIONS:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        yield _media_item_from_path(path, stat)


def _public_media_item(item: dict[str, Any]) -> dict[str, Any]:
    public = dict(item)
    public.pop("_modified_ts", None)
    return public


def _bounded_page_limit(value: Any) -> int:
    try:
        limit = int(value or MEDIA_LIBRARY_DEFAULT_PAGE_SIZE)
    except Exception:
        limit = MEDIA_LIBRARY_DEFAULT_PAGE_SIZE
    return min(max(1, limit), MEDIA_LIBRARY_MAX_PAGE_SIZE)


def _bounded_page_offset(value: Any) -> int:
    try:
        offset = int(value or 0)
    except Exception:
        offset = 0
    return min(max(0, offset), MEDIA_LIBRARY_MAX_OFFSET)


def _encode_media_cursor(key: tuple[float, str]) -> str:
    payload = {"modified_ts": float(key[0]), "name": str(key[1])}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_media_cursor(value: Any) -> tuple[float, str] | None:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        padded = token + ("=" * (-len(token) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        modified_ts = float(payload.get("modified_ts"))
    except Exception:
        return None
    name = str(payload.get("name") or "").strip()
    if not name:
        return None
    return (modified_ts, name)


def _matches_media_page_filter(
    item: dict[str, Any],
    *,
    query: str,
    mime_type: str,
) -> bool:
    if query and query not in str(item.get("name") or "").lower():
        return False
    if mime_type:
        observed = str(item.get("mime_type") or "").lower()
        if mime_type.endswith("/"):
            if not observed.startswith(mime_type):
                return False
        elif observed != mime_type:
            return False
    return True


def media_library_summary() -> dict[str, Any]:
    count = 0
    total_bytes = 0
    latest_modified_at = ""
    latest_key: tuple[float, str] | None = None
    for item in iter_media_files():
        count += 1
        total_bytes += int(item.get("size_bytes") or 0)
        key = (float(item.get("_modified_ts") or 0.0), str(item.get("name") or ""))
        if latest_key is None or key > latest_key:
            latest_key = key
            latest_modified_at = str(item.get("modified_at") or "")
    return {
        "count": count,
        "total_bytes": total_bytes,
        "latest_modified_at": latest_modified_at,
    }


def list_media_files_page(
    *,
    limit: int | None = None,
    offset: int = 0,
    cursor: str | None = None,
    query: str | None = None,
    mime_type: str | None = None,
) -> dict[str, Any]:
    page_limit = _bounded_page_limit(limit)
    page_offset = _bounded_page_offset(offset)
    cursor_key = _decode_media_cursor(cursor)
    query_norm = str(query or "").strip().lower()[:160]
    mime_norm = str(mime_type or "").strip().lower()[:128]
    heap_size = page_offset + page_limit + 1
    heap: list[tuple[float, str, dict[str, Any]]] = []
    total_count = 0
    total_bytes = 0
    scanned_count = 0

    for item in iter_media_files():
        scanned_count += 1
        if not _matches_media_page_filter(item, query=query_norm, mime_type=mime_norm):
            continue
        key = (float(item.get("_modified_ts") or 0.0), str(item.get("name") or ""))
        if cursor_key is not None and key >= cursor_key:
            continue
        total_count += 1
        total_bytes += int(item.get("size_bytes") or 0)
        entry = (key[0], key[1], item)
        if len(heap) < heap_size:
            heapq.heappush(heap, entry)
        elif key > (heap[0][0], heap[0][1]):
            heapq.heapreplace(heap, entry)

    ordered = sorted(heap, key=lambda entry: (entry[0], entry[1]), reverse=True)
    page_entries = ordered[page_offset : page_offset + page_limit]
    has_more = len(ordered) > page_offset + page_limit
    items = [_public_media_item(entry[2]) for entry in page_entries]
    next_cursor = ""
    if has_more and page_entries:
        last = page_entries[-1]
        next_cursor = _encode_media_cursor((last[0], last[1]))
    return {
        "ok": True,
        "schema": "adaos.media_library.page.v1",
        "items": items,
        "pagination": {
            "limit": page_limit,
            "offset": page_offset,
            "cursor": str(cursor or ""),
            "next_cursor": next_cursor,
            "has_more": has_more,
            "total_count": total_count,
            "scanned_count": scanned_count,
        },
        "summary": {
            "count": total_count,
            "total_bytes": total_bytes,
            "query": query_norm,
            "mime_type": mime_norm,
        },
    }


def _active_browser_session_totals() -> tuple[int, int]:
    try:
        from adaos.services.yjs.gateway_ws import active_browser_session_snapshot

        snapshot = active_browser_session_snapshot()
    except Exception:
        return (0, 0)
    peers = snapshot.get("peers") if isinstance(snapshot.get("peers"), list) else []
    total = 0
    connected = 0
    for item in peers:
        if not isinstance(item, dict):
            continue
        total += 1
        if str(item.get("connection_state") or "").strip().lower() == "connected":
            connected += 1
    return (total, connected)


def _media_route_profiles(*, webrtc_supported: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    browser_session_total, connected_browser_total = _active_browser_session_totals()
    foundation = member_browser_direct_foundation(
        browser_session_total=browser_session_total,
        connected_browser_session_total=connected_browser_total,
        admitted=False,
    )
    candidate_member_total = int(foundation.get("candidate_member_total") or 0)
    preferred_member_id = str(foundation.get("preferred_member_id") or "").strip() or None
    candidate_member_ids = list(foundation.get("candidate_members") or [])
    member_browser_direct_possible = bool(webrtc_supported) and bool(foundation.get("possible"))
    member_browser_direct_reason = (
        str(foundation.get("reason") or "").strip()
        or "member_browser_direct_missing_browser_or_member_candidate"
    )
    route_profiles = {
        "upload": resolve_media_route_intent(
            need="upload",
            direct_local_ready=True,
            root_routed_ready=True,
            hub_webrtc_ready=False,
            producer_preference="hub",
            preferred_member_id=preferred_member_id,
            candidate_member_ids=candidate_member_ids,
            member_browser_direct_possible=member_browser_direct_possible,
            member_browser_direct_admitted=False,
            member_browser_direct_reason=member_browser_direct_reason,
            candidate_member_total=candidate_member_total,
            browser_session_total=browser_session_total,
        ),
        "playback": resolve_media_route_intent(
            need="playback",
            direct_local_ready=True,
            root_routed_ready=True,
            hub_webrtc_ready=False,
            producer_preference="hub",
            preferred_member_id=preferred_member_id,
            candidate_member_ids=candidate_member_ids,
            member_browser_direct_possible=member_browser_direct_possible,
            member_browser_direct_admitted=False,
            member_browser_direct_reason=member_browser_direct_reason,
            candidate_member_total=candidate_member_total,
            browser_session_total=browser_session_total,
        ),
        "live_stream": resolve_media_route_intent(
            need="live_stream",
            direct_local_ready=False,
            root_routed_ready=True,
            hub_webrtc_ready=bool(webrtc_supported),
            producer_preference="member",
            preferred_member_id=preferred_member_id,
            candidate_member_ids=candidate_member_ids,
            member_browser_direct_possible=member_browser_direct_possible,
            member_browser_direct_admitted=False,
            member_browser_direct_reason=member_browser_direct_reason,
            candidate_member_total=candidate_member_total,
            browser_session_total=connected_browser_total,
        ),
        "scenario_response_media": resolve_media_route_intent(
            need="scenario_response_media",
            direct_local_ready=True,
            root_routed_ready=True,
            hub_webrtc_ready=bool(webrtc_supported),
            producer_preference="member",
            preferred_member_id=preferred_member_id,
            candidate_member_ids=candidate_member_ids,
            member_browser_direct_possible=member_browser_direct_possible,
            member_browser_direct_admitted=False,
            member_browser_direct_reason=member_browser_direct_reason,
            candidate_member_total=candidate_member_total,
            browser_session_total=browser_session_total,
        ),
    }
    foundation = dict(foundation)
    foundation["possible"] = member_browser_direct_possible
    foundation["reason"] = member_browser_direct_reason
    return route_profiles, foundation


def media_capabilities() -> dict[str, Any]:
    try:
        from adaos.services.webrtc.peer import webrtc_peer_snapshot

        live_webrtc = webrtc_peer_snapshot()
        webrtc_supported = True
    except Exception:
        live_webrtc = {}
        webrtc_supported = False
    route_profiles, member_browser_direct = _media_route_profiles(
        webrtc_supported=webrtc_supported,
    )
    return {
        "storage": {
            "dir": str(media_video_dir()),
            "subpath": MEDIA_STORAGE_SUBPATH,
        },
        "upload": {
            "direct_local": {
                "ready": True,
                "mode": "http_raw_put",
                "note": "Raw PUT upload is available when the browser talks to the local hub API directly.",
            },
            "webrtc_datachannel": {
                "ready": bool(webrtc_supported),
                "mode": "webrtc_media_datachannel",
                "note": "Browser-hub WebRTC media DataChannel supports direct bounded upload when the peer is connected.",
                "max_upload_bytes_hint": ROOT_MEDIA_RELAY_MAX_UPLOAD_BYTES,
            },
            "root_routed": {
                "ready": True,
                "mode": "bounded_media_relay",
                "note": "Dedicated /hubs/<id>/media/* relay path supports bounded upload streaming via root.",
                "max_upload_bytes_hint": ROOT_MEDIA_RELAY_MAX_UPLOAD_BYTES,
            },
        },
        "playback": {
            "direct_local": {
                "ready": True,
                "mode": "http_file_response",
                "note": "Progressive file playback is available on the direct local hub API path.",
            },
            "webrtc_datachannel": {
                "ready": bool(webrtc_supported),
                "mode": "webrtc_media_datachannel",
                "note": "Browser-hub WebRTC media DataChannel supports direct bounded file playback as a Blob source.",
                "range_requests": False,
                "chunk_bytes_hint": 64 * 1024,
            },
            "root_routed": {
                "ready": True,
                "mode": "bounded_media_relay",
                "note": "Dedicated /hubs/<id>/media/* relay path supports ranged playback via root.",
                "range_requests": True,
                "chunk_bytes_hint": ROOT_MEDIA_RELAY_CHUNK_BYTES,
            },
        },
        "broadcast": {
            "ready": bool(webrtc_supported),
            "mode": "webrtc_av_loopback" if webrtc_supported else "unavailable",
            "reason": "hub_webrtc_peer_loopback" if webrtc_supported else "webrtc_runtime_unavailable",
            "details": (
                "Browser camera/microphone tracks can be published to the hub and looped back for end-to-end media validation."
                if webrtc_supported
                else "Current runtime cannot load aiortc media support."
            ),
            "peer_total": int(live_webrtc.get("peer_total") or 0),
            "connected_peers": int(live_webrtc.get("connected_peers") or 0),
            "incoming_audio_tracks": int(live_webrtc.get("incoming_audio_tracks") or 0),
            "incoming_video_tracks": int(live_webrtc.get("incoming_video_tracks") or 0),
            "loopback_audio_tracks": int(live_webrtc.get("loopback_audio_tracks") or 0),
            "loopback_video_tracks": int(live_webrtc.get("loopback_video_tracks") or 0),
            "member_browser_direct": member_browser_direct,
        },
        "route_profiles": route_profiles,
        "notes": [
            "Direct local hub API remains the preferred path for operator-grade upload and playback validation.",
            "WebRTC media DataChannel is available for direct browser-hub upload and bounded Blob playback when the peer is connected.",
            "Root-routed media now uses a dedicated bounded relay path instead of the generic buffered JSON /api proxy.",
            "WebRTC audio/video loopback is available for live end-to-end media channel validation.",
            "Member-browser direct media is now represented as an explicit route contract foundation, even before admission policy is enabled.",
        ],
    }


def media_runtime_snapshot(items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    items = list(items) if isinstance(items, list) else list_media_files()
    total_bytes = sum(int(item.get("size_bytes") or 0) for item in items)
    try:
        from adaos.services.webrtc.peer import webrtc_peer_snapshot

        live_webrtc = webrtc_peer_snapshot()
        webrtc_supported = True
    except Exception:
        live_webrtc = {}
        webrtc_supported = False
    route_profiles, member_browser_direct = _media_route_profiles(
        webrtc_supported=webrtc_supported,
    )
    default_route = (
        route_profiles.get("scenario_response_media")
        if isinstance(route_profiles.get("scenario_response_media"), dict)
        else {}
    )
    return {
        "available": True,
        "scope": MEDIA_RUNTIME_SCOPE,
        "authority": {
            "route_administrator": "router",
            "storage": "local_hub_api",
            "playback": "local_hub_api",
            "relay": "root_media_relay",
            "broadcast": "hub_webrtc_peer_loopback" if webrtc_supported else "unavailable",
        },
        "assessment": {
            "state": "relay_and_webrtc_media_available" if webrtc_supported else "bounded_relay_available",
            "reason": (
                "media plane supports direct-local authority, bounded root relay authority, WebRTC media DataChannel upload/playback, and live WebRTC audio/video loopback"
                if webrtc_supported
                else "media plane supports direct-local authority and bounded root relay authority on a dedicated path"
            ),
        },
        "paths": {
            "direct_local_http": {
                "ready": True,
                "upload": True,
                "playback": "full",
                "authority": "local_hub_api",
                "mode": "http_raw_put + http_file_response",
            },
            "root_routed_http": {
                "ready": True,
                "upload": True,
                "playback": "full",
                "authority": "root_media_relay",
                "mode": "bounded_media_relay",
                "reason": "root_media_relay_streams_upload_and_playback_on_a_dedicated_path",
                "max_upload_bytes_hint": ROOT_MEDIA_RELAY_MAX_UPLOAD_BYTES,
                "chunk_bytes_hint": ROOT_MEDIA_RELAY_CHUNK_BYTES,
            },
            "webrtc_datachannel": {
                "ready": bool(webrtc_supported),
                "upload": True,
                "playback": "full_blob",
                "authority": "hub_webrtc_peer_datachannel" if webrtc_supported else "none",
                "mode": "webrtc_media_datachannel" if webrtc_supported else "not_implemented",
                "reason": "hub_webrtc_media_datachannel" if webrtc_supported else "webrtc_media_datachannel_unavailable",
                "range_requests": False,
                "chunk_bytes_hint": 64 * 1024,
                "peer_total": int(live_webrtc.get("peer_total") or 0),
                "connected_peers": int(live_webrtc.get("connected_peers") or 0),
            },
            "webrtc_tracks": {
                "ready": bool(webrtc_supported),
                "upload": False,
                "playback": "live_loopback" if webrtc_supported else "not_supported",
                "authority": "hub_webrtc_peer_loopback" if webrtc_supported else "none",
                "mode": "webrtc_audio_video_tracks" if webrtc_supported else "not_implemented",
                "reason": "hub_webrtc_peer_loopback" if webrtc_supported else "webrtc_media_tracks_not_implemented",
                "peer_total": int(live_webrtc.get("peer_total") or 0),
                "connected_peers": int(live_webrtc.get("connected_peers") or 0),
                "incoming_audio_tracks": int(live_webrtc.get("incoming_audio_tracks") or 0),
                "incoming_video_tracks": int(live_webrtc.get("incoming_video_tracks") or 0),
                "loopback_audio_tracks": int(live_webrtc.get("loopback_audio_tracks") or 0),
                "loopback_video_tracks": int(live_webrtc.get("loopback_video_tracks") or 0),
            },
            "member_browser_webrtc": {
                "ready": bool(member_browser_direct.get("ready")),
                "upload": False,
                "playback": "live_direct" if bool(member_browser_direct.get("ready")) else "not_admitted",
                "authority": "router_selected_member" if bool(member_browser_direct.get("admitted")) else "none",
                "mode": "webrtc_browser_member_direct",
                "reason": str(member_browser_direct.get("reason") or "member_browser_direct_not_admitted"),
                "candidate_member_total": int(member_browser_direct.get("candidate_member_total") or 0),
                "candidate_members": list(member_browser_direct.get("candidate_members") or []),
                "preferred_member_id": member_browser_direct.get("preferred_member_id"),
                "browser_session_total": int(member_browser_direct.get("browser_session_total") or 0),
            },
        },
        "recommended_path": "direct_local_http",
        "route_intent": default_route,
        "route_profiles": route_profiles,
        "producer_authority": default_route.get("producer_authority"),
        "producer_target": default_route.get("producer_target"),
        "preferred_member_id": default_route.get("preferred_member_id"),
        "delivery_topology": default_route.get("delivery_topology"),
        "selection_reason": default_route.get("selection_reason"),
        "degradation_reason": default_route.get("degradation_reason"),
        "attempt": default_route.get("attempt"),
        "monitoring": default_route.get("monitoring"),
        "member_browser_direct": member_browser_direct,
        "counts": {
            "file_total": len(items),
            "total_bytes": total_bytes,
            "live_peer_total": int(live_webrtc.get("peer_total") or 0),
            "live_connected_peers": int(live_webrtc.get("connected_peers") or 0),
            "incoming_audio_tracks": int(live_webrtc.get("incoming_audio_tracks") or 0),
            "incoming_video_tracks": int(live_webrtc.get("incoming_video_tracks") or 0),
            "loopback_audio_tracks": int(live_webrtc.get("loopback_audio_tracks") or 0),
            "loopback_video_tracks": int(live_webrtc.get("loopback_video_tracks") or 0),
        },
        "live_webrtc": live_webrtc if isinstance(live_webrtc, dict) else {},
        "storage": {
            "dir": str(media_video_dir()),
            "subpath": MEDIA_STORAGE_SUBPATH,
        },
        "notes": [
            "Direct local hub API remains the preferred path for real upload and playback validation.",
            "WebRTC media DataChannel provides direct browser-hub upload and bounded Blob playback when the peer is connected.",
            "Root-routed media now uses a dedicated bounded relay path instead of the generic buffered /api proxy.",
            "WebRTC audio/video loopback is available for live end-to-end media validation against the hub.",
            "Member-browser direct media is represented as an explicit route contract foundation and can be admitted later without changing the media runtime shape.",
            "Candidate member selection now comes from explicit member WebRTC media capability advertised via subnet capacity, not only from raw connected-member counts.",
        ],
    }


def list_media_files() -> list[dict[str, Any]]:
    items = [_public_media_item(item) for item in iter_media_files()]
    items.sort(key=lambda item: (str(item.get("modified_at") or ""), str(item.get("name") or "")), reverse=True)
    return items


def media_snapshot() -> dict[str, Any]:
    items = list_media_files()
    total_bytes = sum(int(item.get("size_bytes") or 0) for item in items)
    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "total_bytes": total_bytes,
        "capabilities": media_capabilities(),
        "runtime": media_runtime_snapshot(items),
    }
