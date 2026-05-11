from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

from adaos.services.agent_context import get_ctx


_SKILL_NAME = "rasa_nlu_service_skill"
_PACKAGE = "adaos.interpreter_data"
_RESOURCE_DIR = "rasa_nlu_service_skill"


def _copy_template_tree(src: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        dst = target / rel
        if item.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dst)


def ensure_rasa_service_skill_installed() -> Path | None:
    """
    Ensure default Rasa NLU service-skill exists in workspace skills directory.

    Returns target path when created (or already present), otherwise None.
    """
    ctx = get_ctx()
    skills_root = Path(ctx.paths.skills_dir())
    target = skills_root / _SKILL_NAME

    try:
        src_dir = resources.files(_PACKAGE) / _RESOURCE_DIR
    except Exception:
        return None

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with resources.as_file(src_dir) as src:
            _copy_template_tree(Path(src), target)
    except Exception:
        return None
    return target
