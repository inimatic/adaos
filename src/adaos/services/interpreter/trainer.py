# src/adaos/services/interpreter/trainer.py
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional
import sys

from adaos.services.interpreter.workspace import InterpreterWorkspace


class RasaTrainer:
    """
    Legacy in-process Rasa trainer.

    Production training goes through `rasa_nlu_service_skill`; this class is
    kept only for compatibility with older scripts that explicitly call it.
    """

    def __init__(self, workspace: InterpreterWorkspace, *, rasa_version: str = "3.6.20"):
        self.ws = workspace
        self.rasa_version = rasa_version
        self.models_dir = Path(self.ws.context.paths.models_dir()) / "interpreter"
        self.models_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- helpers
    def _python(self) -> Path:
        """
        Use the current interpreter for legacy direct `python -m rasa`.
        """
        return Path(sys.executable)

    def _run(self, cmd: list[str], *, cwd: Optional[Path] = None) -> None:
        subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)

    # ---------------------------------------------------------------- training
    def train(self, *, note: Optional[str] = None) -> dict:
        project = self.ws.build_rasa_project()
        # Rasa is expected to be installed into the current interpreter env.
        python = self._python()
        cmd = [
            str(python),
            "-m",
            "rasa",
            "train",
            "nlu",
            "--fixed-model-name",
            "interpreter_latest",
            "--out",
            str(self.models_dir),
        ]
        self._run(cmd, cwd=project)
        model_path = self.models_dir / "interpreter_latest.tar.gz"
        meta = self.ws.record_training(note=note or "rasa-train", extra={"model_path": str(model_path)})
        return meta
