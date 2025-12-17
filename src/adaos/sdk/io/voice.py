"""SDK facade for voice IO (TTS/STT).

Currently backed by the local mock implementation from ``adaos.services``.
"""

from __future__ import annotations

from typing import Mapping

from adaos.sdk.core.decorators import tool
from adaos.services.io_voice_mock import stt_listen as _stt_listen
from adaos.services.io_voice_mock import tts_speak as _tts_speak

__all__ = ["tts_speak", "stt_listen"]


@tool(
    "io.voice.tts.speak",
    summary="Speak text via TTS (mock adapter in local runtime).",
    stability="experimental",
    examples=["io.voice.tts.speak('Hello')"],
)
def tts_speak(text: str | None) -> Mapping[str, bool]:
    return _tts_speak(text)


@tool(
    "io.voice.stt.listen",
    summary="Listen for text via STT (mock adapter in local runtime).",
    stability="experimental",
    examples=["io.voice.stt.listen('10s')"],
)
def stt_listen(timeout: str = "20s") -> Mapping[str, str]:
    return _stt_listen(timeout=timeout)

