from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from adaos.services.i18n.service import I18nService


class _Paths:
    def __init__(self, root: Path) -> None:
        self.root = root

    def skills_locales_dir(self) -> Path:
        return self.root / "centralized"


def _write_messages(path: Path, messages: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(messages), encoding="utf-8")


def test_skill_i18n_uses_browser_publishable_assets_with_language_fallback(tmp_path: Path) -> None:
    skill_path = tmp_path / "skills" / "media_center_skill"
    _write_messages(
        skill_path / "assets" / "i18n" / "en.json",
        {"prep.ready": "Ready", "prep.shared": "Asset EN"},
    )
    _write_messages(
        skill_path / "assets" / "i18n" / "ru.json",
        {"prep.shared": "Asset RU"},
    )
    service = I18nService(
        SimpleNamespace(paths=_Paths(tmp_path), settings=SimpleNamespace(lang="ru"))
    )

    assert service.translate("prep.ready", scope="skill", skill_path=skill_path) == "Ready"
    assert service.translate("prep.shared", scope="skill", skill_path=skill_path) == "Asset RU"


def test_skill_i18n_prefers_canonical_assets_over_legacy_and_centralized_files(tmp_path: Path) -> None:
    skill_path = tmp_path / "skills" / "media_center_skill"
    _write_messages(
        tmp_path / "centralized" / "media_center_skill" / "en.json",
        {"prep.source": "Centralized"},
    )
    _write_messages(skill_path / "i18n" / "en.json", {"prep.source": "Legacy"})
    _write_messages(
        skill_path / "assets" / "i18n" / "en.json",
        {"prep.source": "Canonical"},
    )
    service = I18nService(
        SimpleNamespace(paths=_Paths(tmp_path), settings=SimpleNamespace(lang="en"))
    )

    assert (
        service.translate("prep.source", scope="skill", skill_path=skill_path)
        == "Canonical"
    )
