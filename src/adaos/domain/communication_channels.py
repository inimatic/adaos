"""Core communication contract descriptors; availability comes from owned discovery."""
from __future__ import annotations

import copy

_CHANNELS = [
    {"id": "web", "surface": "browser dialog / YJS", "entrypoint": "adaos.sdk.chat.send",
     "semantics": "canonical transcript projection; browser first-sync is independent of external delivery"},
    {"id": "telegram", "surface": "Core-bound bot/chat or hub pairing ReplyRoute",
     "entrypoint": "adaos.sdk.conversation.publish",
     "semantics": "durable per-target outbox; Root acceptance precedes provider receipt"},
    {"id": "max", "surface": "Core-admitted MAX bot/chat/subnet ReplyRoute",
     "entrypoint": "adaos.sdk.conversation.publish",
     "semantics": "durable per-target outbox and exact binding; declared transport is not automatic subscription consent"},
    {"id": "voice", "surface": "Core Voice input and speech output",
     "entrypoint": "adaos.sdk.chat.send(render_targets=('text_tail','speech_text'))",
     "semantics": "speech projection and channel capability negotiation; playback is device-owned"},
    {"id": "stdout", "surface": "Core system notification sink", "entrypoint": "Core routing policy",
     "semantics": "diagnostic sink, not human delivery proof"},
]


def communication_channels() -> list[dict]:
    return copy.deepcopy(_CHANNELS)
