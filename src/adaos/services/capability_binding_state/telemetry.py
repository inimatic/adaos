"""Versioned raw telemetry emitted by CBS executable proofs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from adaos.domain.capability_binding_state import CBSBenchmarkTelemetry, CBSCrudProofBundle
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


class CBSBenchmarkTelemetryConflict(ValueError):
    pass


@dataclass(slots=True)
class CBSBenchmarkTelemetryStore:
    root: Path

    @property
    def lock_path(self) -> Path:
        return Path(self.root) / ".telemetry.lock"

    def put(self, telemetry: CBSBenchmarkTelemetry) -> Path:
        payload = telemetry.to_dict()
        path = Path(self.root) / f"{telemetry.digest.removeprefix('sha256:')}.json"
        with mutation_lock(self.lock_path):
            if path.is_file():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing != payload:
                    raise CBSBenchmarkTelemetryConflict(
                        f"telemetry digest collision: {telemetry.digest}"
                    )
            else:
                atomic_write_json(path, payload)
        return path

    def load(self, digest: str) -> CBSBenchmarkTelemetry:
        path = Path(self.root) / f"{str(digest).removeprefix('sha256:')}.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise CBSBenchmarkTelemetryConflict("telemetry record must be an object")
        record = CBSBenchmarkTelemetry.from_mapping(value)
        if record.digest != digest:
            raise CBSBenchmarkTelemetryConflict("telemetry digest does not match path")
        return record


@dataclass(slots=True)
class CBSCrudProofStore:
    root: Path

    @property
    def lock_path(self) -> Path:
        return Path(self.root) / ".proof-writer.lock"

    def put(self, proof: CBSCrudProofBundle) -> Path:
        payload = proof.to_dict()
        path = Path(self.root) / f"{proof.digest.removeprefix('sha256:')}.json"
        with mutation_lock(self.lock_path):
            if path.is_file():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if existing != payload:
                    raise CBSBenchmarkTelemetryConflict(
                        f"proof digest collision: {proof.digest}"
                    )
            else:
                atomic_write_json(path, payload)
        return path

    def load(self, digest: str) -> CBSCrudProofBundle:
        path = Path(self.root) / f"{str(digest).removeprefix('sha256:')}.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, Mapping):
            raise CBSBenchmarkTelemetryConflict("proof bundle must be an object")
        record = CBSCrudProofBundle.from_mapping(value)
        if record.digest != digest:
            raise CBSBenchmarkTelemetryConflict("proof digest does not match path")
        return record


__all__ = [
    "CBSBenchmarkTelemetryConflict",
    "CBSBenchmarkTelemetryStore",
    "CBSCrudProofStore",
]
