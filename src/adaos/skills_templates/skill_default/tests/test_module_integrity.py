from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml


def test_skill_entrypoint_imports() -> None:
    skill_root = Path(__file__).resolve().parents[1]
    manifest = yaml.safe_load((skill_root / "skill.yaml").read_text(encoding="utf-8")) or {}
    entry = manifest.get("entry") or manifest.get("entrypoint") or "handlers/main.py"
    entry_path = skill_root / str(entry)

    spec = importlib.util.spec_from_file_location("skill_under_test.handlers.main", entry_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert entry_path.is_file()


def test_lang_resources_are_mapping() -> None:
    skill_root = Path(__file__).resolve().parents[1]
    entry_path = skill_root / "handlers" / "main.py"
    spec = importlib.util.spec_from_file_location("skill_under_test.handlers.main", entry_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if hasattr(module, "lang_res"):
        assert isinstance(module.lang_res(), dict)
