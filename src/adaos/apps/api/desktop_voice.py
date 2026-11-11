"""Bounded recognition for a verified desktop participant, without dialog dispatch."""
from __future__ import annotations

import asyncio
import base64
import binascii
import contextvars
import hashlib
import importlib.util
import io
import json
import math
import threading
import time
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from adaos.apps.api.desktop_access import connection_principal
from adaos.services.desktop_access import DesktopAccessService, DesktopPrincipal
from adaos.services.policy.caller import CallerAccessDenied, verified_caller

router = APIRouter(prefix="/api/node/yjs/webspaces/{webspace_id}/voice", tags=["desktop-voice"])
MAX_BODY = 180 * 1024
MAX_WAV = 128 * 1024
MAX_TEXT = 4096
TIMEOUT = 12.0
_lock = threading.Lock()
_active: set[str] = set()
_recent: dict[str, tuple[float, str, dict | None]] = {}
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="desktop-stt")


def _response(value: dict) -> JSONResponse:
    return JSONResponse(value, headers={"Cache-Control": "no-store"})


async def _participant(request: Request):
    # The general desktop dependency decodes JSON for administrative fields.
    # Authenticate first here, but leave bounded body parsing to this route.
    try:
        principal = await asyncio.to_thread(connection_principal, request)
        if principal is None:
            raise HTTPException(401, "desktop_authentication_required")
        await asyncio.to_thread(DesktopAccessService().require_attachment, principal, request.path_params["webspace_id"])
    except (CallerAccessDenied, PermissionError, ValueError, RuntimeError):
        raise HTTPException(403, "desktop_access_denied") from None
    with verified_caller(principal.actor):
        yield principal


def _available() -> bool:
    from adaos.adapters.audio.stt.model_manager import resolve_vosk_model
    from adaos.apps.api.stt_api import _models_root
    return importlib.util.find_spec("vosk") is not None and resolve_vosk_model("ru-RU", _models_root()) is not None


@router.get("/capabilities")
async def capabilities(webspace_id: str, principal: DesktopPrincipal = Depends(_participant)):
    available = await asyncio.to_thread(_available)
    try:
        await asyncio.to_thread(DesktopAccessService().require_attachment, principal, webspace_id)
    except CallerAccessDenied as exc:
        raise HTTPException(403, str(exc)) from None
    return _response({"ok": True, "schema": "adaos.desktop_voice.v1", "webspace_id": webspace_id,
                      "available": available, "provider": "vosk", "language": "ru-RU",
                      "max_seconds": 3.5, "max_wav_bytes": MAX_WAV,
                      "sample_rate": 16000, "channels": 1, "bits_per_sample": 16,
                      "recognition_only": True})


def _validate(value: object) -> tuple[str, float, bytes]:
    if not isinstance(value, dict) or set(value) != {"request_id", "created_at", "audio_b64", "lang"}:
        raise HTTPException(400, "invalid_voice_request")
    request_id = value["request_id"]
    try:
        valid_id = isinstance(request_id, str) and str(uuid.UUID(request_id)) == request_id
    except (ValueError, AttributeError):
        valid_id = False
    created = value["created_at"]
    if not valid_id or isinstance(created, bool) or not isinstance(created, (int, float)) or not math.isfinite(created):
        raise HTTPException(400, "invalid_voice_request")
    if created > time.time() + 5 or created < time.time() - 60:
        raise HTTPException(409, "voice_request_expired")
    encoded = value["audio_b64"]
    if value["lang"] != "ru-RU" or not isinstance(encoded, str) or len(encoded) > ((MAX_WAV + 2) // 3) * 4:
        raise HTTPException(400, "invalid_voice_audio")
    try:
        data = base64.b64decode(encoded, validate=True)
        if not 44 <= len(data) <= MAX_WAV:
            raise ValueError()
        with wave.open(io.BytesIO(data), "rb") as source:
            if (source.getcomptype(), source.getnchannels(), source.getsampwidth(), source.getframerate()) != ("NONE", 1, 2, 16000):
                raise ValueError()
            frames = source.getnframes()
            if not 4000 <= frames <= 56000 or len(source.readframes(frames)) != frames * 2:
                raise ValueError()
    except (ValueError, binascii.Error, wave.Error, EOFError, OverflowError):
        raise HTTPException(400, "invalid_voice_audio") from None
    return request_id, float(created), data


def _recognize(payload: dict, principal_key: str, key: str, fingerprint: str) -> dict:
    try:
        from adaos.apps.api.stt_api import transcribe_audio_payload
        if not _available():
            raise HTTPException(503, "transcription_unavailable")
        result = transcribe_audio_payload(payload)
        text = result.get("text")
        if result.get("ok") is not True or not isinstance(text, str) or len(text) > MAX_TEXT:
            raise HTTPException(502, "invalid_transcription_response")
        answer = {"text": text, "provider": "vosk"}
        with _lock:
            _recent[key] = (time.monotonic(), fingerprint, answer)
        return answer
    except HTTPException as exc:
        # Provider paths/details never cross this participant-facing boundary.
        raise HTTPException(exc.status_code if exc.status_code in {400, 413, 501, 503} else 502,
                            "transcription_unavailable") from None
    except Exception:
        raise HTTPException(502, "transcription_unavailable") from None
    finally:
        with _lock:
            _active.discard(principal_key)


@router.post("/transcribe")
async def transcribe(request: Request, webspace_id: str,
                     principal: DesktopPrincipal = Depends(_participant)):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "voice_json_required")
    body = bytearray()
    async for chunk in request.stream():
        if len(chunk) > MAX_BODY - len(body):
            raise HTTPException(413, "voice_request_too_large")
        body.extend(chunk)
    try:
        value = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise HTTPException(400, "invalid_voice_request") from None
    request_id, created, data = _validate(value)
    principal_key = hashlib.sha256(f"{principal.actor.ref()}:{principal.session_id or ''}".encode()).hexdigest()
    key = f"{principal_key}:{webspace_id}:{request_id}"
    fingerprint = hashlib.sha256(data + f"{created}:ru-RU".encode()).hexdigest()
    cached = None
    with _lock:
        now = time.monotonic()
        for old in [item for item, row in _recent.items() if now - row[0] > 90]:
            del _recent[old]
        previous = _recent.get(key)
        if previous:
            if previous[1] != fingerprint:
                raise HTTPException(409, "voice_request_conflict")
            if previous[2] is None:
                raise HTTPException(409, "voice_request_already_received")
            cached = previous[2]
        else:
            if principal_key in _active or len(_active) >= 2 or len(_recent) >= 128:
                raise HTTPException(429, "voice_busy")
            _active.add(principal_key)
            _recent[key] = (now, fingerprint, None)
    if cached is None:
        try:
            future = asyncio.get_running_loop().run_in_executor(
                _executor, contextvars.copy_context().run, _recognize,
                {"audio_b64": value["audio_b64"], "lang": "ru-RU"}, principal_key, key, fingerprint)
        except Exception:
            with _lock:
                _active.discard(principal_key)
            raise HTTPException(503, "transcription_unavailable") from None
        # Observe late exceptions after timeout/disconnect without logging audio.
        future.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        try:
            cached = await asyncio.wait_for(asyncio.shield(future), TIMEOUT)
        except asyncio.TimeoutError:
            raise HTTPException(504, "transcription_timeout_no_automatic_retry") from None
    try:
        await asyncio.to_thread(DesktopAccessService().require_attachment, principal, webspace_id)
    except CallerAccessDenied as exc:
        raise HTTPException(403, str(exc)) from None
    return _response({"ok": True, "schema": "adaos.desktop_voice.v1", "webspace_id": webspace_id,
                      "request_id": request_id, "recognition_only": True, **cached})
