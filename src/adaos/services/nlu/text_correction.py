from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class TextCorrectionResult:
    text: str
    corrections: tuple[dict[str, str], ...] = ()


_LIGHT_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bслай\s*шоу\b", re.IGNORECASE | re.UNICODE), "слайдшоу"),
    (re.compile(r"\bслайшоу\b", re.IGNORECASE | re.UNICODE), "слайдшоу"),
)


def correct_light_text(text: str) -> TextCorrectionResult:
    """Apply conservative voice/chat typo fixes before NLU routing."""

    value = str(text or "")
    if not value:
        return TextCorrectionResult(text="")

    corrections: list[dict[str, str]] = []
    current = value
    for pattern, replacement in _LIGHT_REPLACEMENTS:
        matches = list(pattern.finditer(current))
        if not matches:
            continue
        current = pattern.sub(replacement, current)
        for match in matches:
            source = match.group(0)
            if source != replacement:
                corrections.append({"from": source, "to": replacement})

    return TextCorrectionResult(text=current, corrections=tuple(corrections))
