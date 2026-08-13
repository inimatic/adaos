from __future__ import annotations

import io
import os
import base64
import json
import asyncio
import threading
import time
import wave
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from adaos.apps.api.auth import require_token
from adaos.services.agent_context import get_ctx

router = APIRouter(prefix="/stt", tags=["stt"])
_MODEL_CACHE: dict[str, object] = {}
_MODEL_CACHE_LOCK = threading.RLock()


def _resolve_lang(lang: Optional[str]) -> str:
    raw = (lang or "").strip().lower()
    if raw.startswith("ru"):
        return "ru-RU"
    if raw.startswith("en"):
        return "en-US"
    return "ru-RU"


def _models_root() -> Path:
    ctx = get_ctx()
    return Path(ctx.paths.base_dir()) / "models" / "vosk"


def _ensure_model(language: str, model_id: str | None = None) -> Path:
    from adaos.adapters.audio.stt.model_manager import resolve_vosk_model

    path = resolve_vosk_model(language, _models_root(), model_id=model_id)
    if path is not None:
        return path

    if os.getenv("ADAOS_VOSK_AUTO_DOWNLOAD", "").strip() == "1":
        from adaos.adapters.audio.stt.model_manager import ensure_vosk_model

        return ensure_vosk_model(language, base_dir=_models_root(), model_id=model_id)

    raise HTTPException(
        status_code=503,
        detail=f"vosk model not found for '{language}'. Install it through /api/stt/models/install",
    )


def _load_model(vosk_module, path: Path):
    key = str(path.resolve())
    with _MODEL_CACHE_LOCK:
        model = _MODEL_CACHE.get(key)
        if model is None:
            model = vosk_module.Model(key)
            _MODEL_CACHE.clear()
            _MODEL_CACHE[key] = model
        return model


@router.get("/models", dependencies=[Depends(require_token)])
def stt_models() -> dict:
    from adaos.adapters.audio.stt.model_manager import installed_models, model_catalog

    return {
        "ok": True,
        "provider_modes": ["system", "vosk", "auto"],
        "default_provider_mode": "system",
        "auto_activation_rule": "installed_and_verified_on_device",
        "catalog": model_catalog(),
        "installed": installed_models(_models_root()),
    }


@router.post("/models/install", dependencies=[Depends(require_token)])
async def stt_model_install(request: Request) -> dict:
    from adaos.adapters.audio.stt.model_manager import install_vosk_model

    payload = await request.json()
    model_id = str(payload.get("model_id") or "").strip()
    if not model_id:
        raise HTTPException(status_code=400, detail="model_id_required")
    descriptor = payload.get("descriptor") if isinstance(payload.get("descriptor"), dict) else None
    local_zip = payload.get("local_zip")
    try:
        path = await asyncio.to_thread(
            install_vosk_model,
            model_id,
            _models_root(),
            local_zip=local_zip,
            descriptor=descriptor,
            select=payload.get("select") is not False,
        )
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "model_id": model_id, "path": str(path)}


@router.post("/models/select", dependencies=[Depends(require_token)])
async def stt_model_select(request: Request) -> dict:
    from adaos.adapters.audio.stt.model_manager import select_vosk_model

    payload = await request.json()
    try:
        selection = select_vosk_model(
            str(payload.get("language") or "ru-RU"),
            str(payload.get("model_id") or ""),
            _models_root(),
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "selection": selection}


@router.post("/models/verify", dependencies=[Depends(require_token)])
async def stt_model_verify(request: Request) -> dict:
    from adaos.adapters.audio.stt.model_manager import mark_model_verified

    payload = await request.json()
    try:
        marker = mark_model_verified(
            str(payload.get("model_id") or ""),
            _models_root(),
            device_id=str(payload.get("device_id") or "local"),
            metrics=payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {},
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "model": marker}


def _read_wav_mono16k(data: bytes) -> bytes:
    if not data:
        raise HTTPException(status_code=400, detail="empty audio")
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            channels = wf.getnchannels()
            rate = wf.getframerate()
            sampwidth = wf.getsampwidth()
            if sampwidth != 2:
                raise HTTPException(
                    status_code=400,
                    detail=f"expected wav PCM16; got channels={channels} rate={rate} sampwidth={sampwidth}",
                )
            frames = wf.readframes(wf.getnframes())
            if channels == 1:
                return frames
            # Best-effort: downmix multi-channel WAV by taking channel 0.
            try:
                import array

                samples = array.array("h")
                samples.frombytes(frames)
                mono = samples[0::channels]
                return mono.tobytes()
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail=f"failed to downmix wav channels={channels}",
                )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid wav: {exc}")

def _try_parse_json(body: bytes) -> dict | None:
    if not body:
        return None
    if body[:1] not in (b"{", b"["):
        return None
    try:
        obj = json.loads(body.decode("utf-8"))
    except Exception:
        return None
    if isinstance(obj, dict):
        return obj
    return None


def _decode_audio_from_request(body: bytes, content_type: str | None) -> bytes:
    """
    Support both:
      - raw WAV body (Content-Type: audio/wav)
      - JSON wrapper (Content-Type: application/json) with fields:
          { "audio_b64": "...", "lang": "ru-RU" }
    This avoids binary-body issues through some proxies.
    """
    ct = (content_type or "").lower().strip()
    obj: dict | None = None
    if "application/json" in ct or ct.endswith("+json"):
        obj = _try_parse_json(body)
        if obj is None:
            raise HTTPException(status_code=400, detail="invalid json")
    else:
        # Some proxies drop/override the Content-Type header. If the body looks
        # like JSON, still try to decode it as a wrapper.
        obj = _try_parse_json(body)

    if obj is not None:
        b64 = obj.get("audio_b64") or obj.get("wav_b64") or obj.get("data")
        if not isinstance(b64, str) or not b64.strip():
            raise HTTPException(status_code=400, detail="invalid json: missing audio_b64")
        token = b64.strip()
        if token.startswith("data:"):
            # data:audio/wav;base64,...
            try:
                token = token.split(",", 1)[1]
            except Exception:
                raise HTTPException(status_code=400, detail="invalid data url")
        try:
            return base64.b64decode(token, validate=False)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid base64: {exc}")
    # default: treat as raw wav bytes
    return body


@router.post("/transcribe", dependencies=[Depends(require_token)])
async def stt_transcribe(
    request: Request,
    lang: Optional[str] = None,
):
    """
    Minimal hub STT API for MVP.

    Accepts a raw WAV body (mono, 16kHz, 16-bit PCM) and returns `{ ok, text }`.
    """
    try:
        import vosk  # type: ignore
    except Exception as exc:
        raise HTTPException(status_code=501, detail=f"vosk is not available: {exc}")

    language = _resolve_lang(lang)
    try:
        body = await request.body()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"failed to read body: {exc}")
    ct = request.headers.get("content-type")
    # If JSON wrapper includes its own lang override, apply it (also when
    # Content-Type is missing but body looks like JSON).
    obj = _try_parse_json(body)
    if isinstance(obj, dict) and isinstance(obj.get("lang"), str) and obj.get("lang").strip():
        language = _resolve_lang(obj.get("lang"))
    model_id = str(obj.get("model_id") or "").strip() if isinstance(obj, dict) else ""
    model_path = _ensure_model(language, model_id=model_id or None)
    body = _decode_audio_from_request(body, ct)
    pcm = _read_wav_mono16k(body)

    try:
        started = time.perf_counter()
        model = _load_model(vosk, model_path)
        # Use the model-native rate (16k) regardless of input; the frontend
        # encodes 16kHz WAV, and we also accept other rates for debugging.
        rec = vosk.KaldiRecognizer(model, 16000)
        rec.SetWords(False)
        rec.AcceptWaveform(pcm)
        import json

        res = json.loads(rec.FinalResult() or "{}")
        text = (res.get("text") or "").strip()
        return {
            "ok": True,
            "text": text,
            "provider": "vosk",
            "model_id": model_path.name,
            "language": language,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
