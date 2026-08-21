from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from adaos.services.agent_context import AgentContext

DEFAULT_LANG = "en"


def normalize_language_code(raw: str | None) -> str:
    token = str(raw or "").strip().lower().replace("_", "-")
    if not token:
        return DEFAULT_LANG
    if "-" in token:
        return token.split("-", 1)[0]
    return token


@dataclass(slots=True)
class I18nService:
    ctx: AgentContext
    _cache_global: Dict[str, Dict[str, str]] = field(default_factory=dict, init=False, repr=False)
    _cache_skill: Dict[tuple[str, str], Dict[str, str]] = field(default_factory=dict, init=False, repr=False)

    def translate(
        self,
        key: str,
        *,
        lang: Optional[str] = None,
        params: Optional[dict[str, Any]] = None,
        skill_path: Optional[Path] = None,
        skill_id: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> str:
        """Resolve one global or skill-owned message without an SDK dependency."""
        lang = normalize_language_code(
            lang or getattr(self.ctx.settings, "lang", None) or os.getenv("ADAOS_LANG") or DEFAULT_LANG
        )
        params = params or {}

        if scope == "global" or (scope is None and not key.startswith("prep.")):
            messages = self._load_global(lang)
        else:
            messages = self._load_skill(lang, skill_path=skill_path, skill_id=skill_id)

        text = messages.get(key, key)
        try:
            return text.format(**params)
        except Exception:
            return text

    def _load_global(self, lang: str) -> Dict[str, str]:
        lang = normalize_language_code(lang)
        if lang in self._cache_global:
            return self._cache_global[lang]
        base = self.ctx.paths.locales_dir()
        data: Dict[str, str] = {}
        for candidate in dict.fromkeys([DEFAULT_LANG, lang]):
            data.update(self._read_messages(base / f"{candidate}.json"))
        self._cache_global[lang] = data
        return data

    def _load_skill(
        self,
        lang: str,
        *,
        skill_path: Optional[Path],
        skill_id: Optional[str],
    ) -> Dict[str, str]:
        lang = normalize_language_code(lang)
        key = ((skill_id or (skill_path.name if skill_path else "")), lang)
        if key in self._cache_skill:
            return self._cache_skill[key]

        data: Dict[str, str] = {}
        sid = skill_id or (skill_path.name if skill_path else None)
        for candidate in dict.fromkeys([DEFAULT_LANG, lang]):
            if sid:
                centralized = self.ctx.paths.skills_locales_dir() / sid / f"{candidate}.json"
                data.update(self._read_messages(centralized))
            if skill_path:
                # assets/i18n is canonical when one dictionary is shared with the browser.
                # The legacy i18n directory remains readable during rolling upgrades.
                for relative_root in (Path("i18n"), Path("assets") / "i18n"):
                    data.update(
                        self._read_messages(skill_path / relative_root / f"{candidate}.json")
                    )

        self._cache_skill[key] = data
        return data

    @staticmethod
    def _read_messages(path: Path) -> Dict[str, str]:
        if not path.exists():
            return {}
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return {}
        return {str(key): value for key, value in loaded.items() if isinstance(value, str)}
