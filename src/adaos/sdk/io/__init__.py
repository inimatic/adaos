"""SDK facades for IO helpers (voice/console/etc).

This module intentionally avoids importing submodules eagerly to prevent
import cycles (e.g. decorators -> io.context -> io package).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = [
    "chat_append",
    "say",
    "media_route",
    "telegram_photo",
    "stream_publish",
    "stream_variable_publish",
    "browser_media_descriptor",
    "cached_image_variant",
    "publish_media_file",
    "stt_listen",
    "tts_speak",
    "build_capture_command",
    "compact_audio_endpoint",
    "endpoint_audio_diagnostics",
    "endpoint_audio_policy",
    "endpoint_audio_readiness",
    "endpoint_audio_stt_status",
    "process_endpoint_audio_event",
    "verify_audio_input_content",
]

if TYPE_CHECKING:
    from .out import chat_append, say, media_route, telegram_photo, stream_publish, stream_variable_publish
    from .media import browser_media_descriptor, cached_image_variant, publish_media_file
    from .voice import stt_listen, tts_speak
    from .endpoint_audio import (
        build_capture_command,
        compact_audio_endpoint,
        endpoint_audio_diagnostics,
        endpoint_audio_policy,
        endpoint_audio_readiness,
        endpoint_audio_stt_status,
        process_endpoint_audio_event,
        verify_audio_input_content,
    )


def __getattr__(name: str) -> Any:  # pragma: no cover
    if name in ("chat_append", "say", "media_route", "telegram_photo", "stream_publish", "stream_variable_publish"):
        from .out import chat_append, say, media_route, telegram_photo, stream_publish, stream_variable_publish

        return {
            "chat_append": chat_append,
            "say": say,
            "media_route": media_route,
            "telegram_photo": telegram_photo,
            "stream_publish": stream_publish,
            "stream_variable_publish": stream_variable_publish,
        }[name]
    if name in ("browser_media_descriptor", "cached_image_variant", "publish_media_file"):
        from .media import browser_media_descriptor, cached_image_variant, publish_media_file

        return {
            "browser_media_descriptor": browser_media_descriptor,
            "cached_image_variant": cached_image_variant,
            "publish_media_file": publish_media_file,
        }[name]
    if name in ("stt_listen", "tts_speak"):
        from .voice import stt_listen, tts_speak

        return {"stt_listen": stt_listen, "tts_speak": tts_speak}[name]
    if name in (
        "build_capture_command",
        "compact_audio_endpoint",
        "endpoint_audio_diagnostics",
        "endpoint_audio_policy",
        "endpoint_audio_readiness",
        "endpoint_audio_stt_status",
        "process_endpoint_audio_event",
        "verify_audio_input_content",
    ):
        from .endpoint_audio import (
            build_capture_command,
            compact_audio_endpoint,
            endpoint_audio_diagnostics,
            endpoint_audio_policy,
            endpoint_audio_readiness,
            endpoint_audio_stt_status,
            process_endpoint_audio_event,
            verify_audio_input_content,
        )

        return {
            "build_capture_command": build_capture_command,
            "compact_audio_endpoint": compact_audio_endpoint,
            "endpoint_audio_diagnostics": endpoint_audio_diagnostics,
            "endpoint_audio_policy": endpoint_audio_policy,
            "endpoint_audio_readiness": endpoint_audio_readiness,
            "endpoint_audio_stt_status": endpoint_audio_stt_status,
            "process_endpoint_audio_event": process_endpoint_audio_event,
            "verify_audio_input_content": verify_audio_input_content,
        }[name]
    raise AttributeError(name)


def __dir__() -> list[str]:  # pragma: no cover
    return sorted(list(globals().keys()) + __all__)
