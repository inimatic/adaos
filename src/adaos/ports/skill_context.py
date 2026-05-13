from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol


@dataclass(slots=True)
class CurrentSkill:
    name: str
    path: Path
    logs_dir: Path | None = None
    service_log_path: Path | None = None
    runtime_log_path: Path | None = None
    ui_diagnostics_log_path: Path | None = None


class SkillContextPort(Protocol):
    """src/adaos/services/skill/context.py"""

    def set(
        self,
        name: str,
        path: Path,
        *,
        logs_dir: Path | None = None,
        service_log_path: Path | None = None,
        runtime_log_path: Path | None = None,
        ui_diagnostics_log_path: Path | None = None,
    ) -> bool: ...
    def clear(self) -> None: ...
    def get(self) -> Optional[CurrentSkill]: ...
