from __future__ import annotations

import json
import os
from importlib import resources
from typing import Any

from adaos.services.agent_context import get_ctx

DEFAULT_CLI_LANG = "en"


def _locales_root():
    return resources.files("adaos").joinpath("locales")


def supported_language_codes() -> list[str]:
    try:
        codes = sorted(
            item.name.rsplit(".", 1)[0].lower()
            for item in _locales_root().iterdir()
            if item.name.endswith(".json")
        )
        return codes or [DEFAULT_CLI_LANG]
    except Exception:
        return [DEFAULT_CLI_LANG]


def normalize_language_code(raw: str | None, supported: list[str] | None = None) -> str:
    supported_codes = supported or supported_language_codes()
    token = str(raw or "").strip().lower().replace("_", "-")
    candidates = [token]
    if "-" in token:
        candidates.append(token.split("-", 1)[0])
    for candidate in candidates:
        if candidate in supported_codes:
            return candidate
    return DEFAULT_CLI_LANG if DEFAULT_CLI_LANG in supported_codes else supported_codes[0]


def _load_messages(lang: str) -> dict[str, str]:
    messages: dict[str, str] = {}
    for candidate in (DEFAULT_CLI_LANG, normalize_language_code(lang)):
        try:
            path = _locales_root().joinpath(f"{candidate}.json")
            if path.is_file():
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    messages.update(data)
        except Exception:
            continue
    return messages


def _preboot_translate(key: str, **kw: Any) -> str:
    messages = _load_messages(os.getenv("ADAOS_LANG", DEFAULT_CLI_LANG))
    text = messages.get(key, key)
    try:
        return text.format(**kw)
    except Exception:
        return text


def _(key: str, **kw: Any) -> str:
    try:
        return get_ctx().i18n.translate(key, params=kw)
    except Exception:
        return _preboot_translate(key, **kw)
