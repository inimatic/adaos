from __future__ import annotations

import os
from typing import Any, Iterable, Mapping

import requests


VOICE_CONTROL_INTENTS = frozenset(
    {
        "voice.long_form.note.start",
        "voice.long_form.dialog.start",
        "voice.long_form.stop",
        "voice.listening.stop",
    }
)


def detect_voice_control_intent(
    texts: Iterable[str],
    *,
    timeout_s: float = 0.35,
    confidence_min: float = 0.7,
) -> dict[str, Any]:
    """Parse only voice control intents through the canonical Rasa service.

    Dialog routing uses this small synchronous gate so an addressed long-form
    command cannot be consumed by the active LLM before NLU sees it. Ordinary
    intents remain on the asynchronous NLU pipeline.
    """

    base = str(os.getenv("ADAOS_RASA_NLU_URL") or "http://127.0.0.1:18092").rstrip("/")
    best: dict[str, Any] = {}
    seen: set[str] = set()
    for raw in texts:
        text = str(raw or "").strip()
        folded = text.casefold()
        if not text or folded in seen:
            continue
        seen.add(folded)
        try:
            response = requests.post(
                f"{base}/parse",
                json={"text": text},
                timeout=max(0.05, min(float(timeout_s), 2.0)),
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            continue
        result = payload.get("result") if isinstance(payload, Mapping) else {}
        intent = result.get("intent") if isinstance(result, Mapping) else {}
        name = str(intent.get("name") or "").strip() if isinstance(intent, Mapping) else ""
        try:
            confidence = float(intent.get("confidence") or 0.0) if isinstance(intent, Mapping) else 0.0
        except (TypeError, ValueError):
            confidence = 0.0
        if name not in VOICE_CONTROL_INTENTS or confidence < float(confidence_min):
            continue
        candidate = {
            "intent": name,
            "confidence": confidence,
            "text": text,
            "provider": "rasa",
            "mode": "voice_control_gate",
        }
        if confidence > float(best.get("confidence") or 0.0):
            best = candidate
    return best


__all__ = ["VOICE_CONTROL_INTENTS", "detect_voice_control_intent"]
