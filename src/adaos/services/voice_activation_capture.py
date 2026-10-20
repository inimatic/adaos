"""Read-only native capture discovery; activation uses the installed agent registry."""
from __future__ import annotations

import importlib.util

from fastapi import HTTPException


def voice_activation_descriptor(language: str, *, native_dialogue: bool = False) -> dict:
    from adaos.apps.api.stt_api import _models_root, _resolve_lang
    from adaos.adapters.audio.stt.model_manager import resolve_vosk_model
    from adaos.services.router.dialog_registry import published_agent_records

    language = _resolve_lang(language)
    if importlib.util.find_spec("vosk") is None:
        raise HTTPException(status_code=501, detail="vosk is not available")
    if resolve_vosk_model(language, _models_root()) is None:
        raise HTTPException(status_code=503, detail=f"vosk model not found for '{language}'")
    targets = []
    for record in published_agent_records()[:64]:
        label = str(record.get("label") or "").strip()[:128]
        aliases = [label, *(record.get("aliases") or [])]
        aliases = list(dict.fromkeys(str(item).strip()[:80] for item in aliases if isinstance(item, str) and item.strip() and ":" not in item))[:16]
        if record.get("id") and aliases:
            targets.append({"id": str(record["id"])[:256], "label": label, "aliases": aliases})
    if not targets:
        raise HTTPException(status_code=503, detail="voice_activation_targets_unavailable")
    result = {"contract": "adaos.voice.activation.v1", "language": language, "targets": targets}
    # An older, already loaded gateway may import this newer helper on demand.
    # It must not advertise streaming until its command dispatcher supports it.
    if native_dialogue:
        result.update(dialogue_contract="adaos.voice.dialogue.v1", streaming=True)
    return result
