# src\adaos\adapters\sdk\inproc_skill_context.py
from __future__ import annotations
from contextvars import ContextVar
from pathlib import Path
from typing import Optional

from adaos.ports.skill_context import SkillContextPort, CurrentSkill

_current_skill: ContextVar[Optional[CurrentSkill]] = ContextVar("adaos_current_skill", default=None)


class InprocSkillContext(SkillContextPort):
    def set(
        self,
        name: str,
        path: Path,
        *,
        logs_dir: Path | None = None,
        service_log_path: Path | None = None,
        runtime_log_path: Path | None = None,
        ui_diagnostics_log_path: Path | None = None,
    ) -> bool:
        if not path.exists():
            return False
        if logs_dir is None or service_log_path is None or runtime_log_path is None or ui_diagnostics_log_path is None:
            try:
                from adaos.services.agent_context import get_ctx  # pylint: disable=import-outside-toplevel

                paths = get_ctx().paths
                logs_dir = logs_dir or Path(paths.logs_dir())
                service_log_path = service_log_path or _optional_skill_log_path(paths, "skill_service_log_path", name)
                runtime_log_path = runtime_log_path or _optional_skill_log_path(paths, "skill_runtime_log_path", name)
                ui_diagnostics_log_path = ui_diagnostics_log_path or _optional_skill_log_path(
                    paths,
                    "skill_ui_diagnostics_log_path",
                    name,
                )
            except Exception:
                pass
        _current_skill.set(
            CurrentSkill(
                name=name,
                path=path,
                logs_dir=logs_dir,
                service_log_path=service_log_path,
                runtime_log_path=runtime_log_path,
                ui_diagnostics_log_path=ui_diagnostics_log_path,
            )
        )
        return True

    def clear(self) -> None:
        _current_skill.set(None)

    def get(self) -> Optional[CurrentSkill]:
        return _current_skill.get()


def _optional_skill_log_path(paths: object, attr: str, skill_name: str) -> Path | None:
    fn = getattr(paths, attr, None)
    if not callable(fn):
        return None
    try:
        return Path(fn(skill_name))
    except Exception:
        return None
