"""Bounded explicit Codex profiles and credential-free execution evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


def normalize_codex_profile(value: Mapping[str, Any]) -> dict[str, str]:
    provider = str(value.get("provider") or "openai-codex-cli").strip()
    model = str(value.get("model") or "").strip()
    effort = str(value.get("reasoning_effort") or "").strip()
    if provider != "openai-codex-cli":
        raise ValueError("Codex requires the openai-codex-cli provider")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", model):
        raise ValueError("An explicit valid Codex model is required")
    if effort not in {"", "none", "minimal", "low", "medium", "high", "xhigh"}:
        raise ValueError("Unsupported Codex reasoning effort")
    return {"provider": provider, "model": model, **({"reasoning_effort": effort} if effort else {})}


def execution_profile_from_command(command: tuple[str, ...]) -> dict[str, Any]:
    """Keep model evidence without retaining credential-bearing CLI overrides."""
    model = None
    effort = None
    for index, token in enumerate(command[:-1]):
        if token in {"--model", "-m"}:
            model = command[index + 1]
        elif token in {"--config", "-c"}:
            match = re.fullmatch(r'model_reasoning_effort="([a-z]+)"', command[index + 1])
            if match:
                effort = match[1]
    return {
        "schema": "adaos.codex.execution_profile.v1",
        "provider": "openai-codex-cli",
        "model": model,
        "reasoning_effort": effort,
        "model_source": "explicit_cli" if model else "runner_default_unresolved",
    }
