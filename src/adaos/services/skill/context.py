# src\adaos\services\skill\context.py
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from adaos.services.agent_context import AgentContext
from adaos.ports.skill_context import CurrentSkill
from adaos.services.workspace_registry import find_workspace_registry_entry


@dataclass(slots=True)
class SkillContextService:
    ctx: AgentContext

    def set_current_skill(self, name: str) -> bool:
        token = str(name or "").strip()
        if not token:
            return False

        entry = find_workspace_registry_entry(
            self.ctx.paths.workspace_dir(),
            kind="skills",
            name_or_id=token,
            fallback_to_scan=True,
        )
        if isinstance(entry, dict):
            rel_path = str((entry.get("source") or {}).get("path") or entry.get("path") or "").strip()
            if rel_path:
                skill_path = (self.ctx.paths.workspace_dir() / rel_path).resolve()
                if skill_path.exists():
                    return self._set_skill_ctx(token, skill_path)

        meta = self.ctx.skills_repo.get(token)
        if not meta:
            return False
        return self._set_skill_ctx(token, Path(meta.path))

    def clear_current_skill(self) -> None:
        self.ctx.skill_ctx.clear()

    def get_current_skill(self) -> Optional[CurrentSkill]:
        return self.ctx.skill_ctx.get()

    def _set_skill_ctx(self, token: str, skill_path: Path) -> bool:
        logs_dir = _optional_path(self.ctx.paths, "logs_dir")
        try:
            return self.ctx.skill_ctx.set(
                token,
                skill_path,
                logs_dir=logs_dir,
                service_log_path=_optional_skill_log_path(self.ctx.paths, "skill_service_log_path", token),
                runtime_log_path=_optional_skill_log_path(self.ctx.paths, "skill_runtime_log_path", token),
                ui_diagnostics_log_path=_optional_skill_log_path(
                    self.ctx.paths,
                    "skill_ui_diagnostics_log_path",
                    token,
                ),
            )
        except TypeError:
            # Backward-compatible for tests or external adapters that still expose
            # the historical set(name, path) signature.
            return self.ctx.skill_ctx.set(token, skill_path)


def _optional_path(paths: object, attr: str) -> Path | None:
    fn = getattr(paths, attr, None)
    if not callable(fn):
        return None
    try:
        return Path(fn())
    except Exception:
        return None


def _optional_skill_log_path(paths: object, attr: str, skill_name: str) -> Path | None:
    fn = getattr(paths, attr, None)
    if not callable(fn):
        return None
    try:
        return Path(fn(skill_name))
    except Exception:
        return None
