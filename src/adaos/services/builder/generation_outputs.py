"""Private exact provider text, distinct from metrics and candidate acceptance.

Only the Core provider boundary writes this store. A retained output does not
admit its meaning, compile a candidate, move a head or complete a Run. Recovery
uses the old exact wire and never rebinds to today's selection or invocation.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.builder_context import digest

from .generation_observations import _verify_row
from .generation_origin import verify_wire_origin
from .generation_runs import verify_run_binding, wire_run_binding
from .interpretation_runs import InterpretationRunRejected
from .task_spec import seal, verify_identity

_MAX_BYTES = 32 * 1024 * 1024


class GenerationOutputs:
    def __init__(self, state_dir):
        self.root = Path(os.path.abspath(state_dir)) / "builder" / "generation_outputs"

    @staticmethod
    def _safe(path):
        for component in (path, *path.parents):
            try:
                metadata = component.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode) or (
                getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            ):
                raise InterpretationRunRejected()
        return path

    @classmethod
    def _read(cls, path):
        cls._safe(path)
        with path.open("rb") as stream:
            payload = stream.read(_MAX_BYTES + 1)
        if len(payload) > _MAX_BYTES:
            raise InterpretationRunRejected()
        value = json.loads(payload.decode("utf-8"))
        if not isinstance(value, dict):
            raise InterpretationRunRejected()
        return value

    def _context(self, journal_dir, binding, origin, plan, wire):
        verify_run_binding(binding, origin, plan)
        directory = Path(journal_dir)
        retained = self._read(
            directory / ("wire-" + digest(str(wire["request_id"]))[7:39] + ".json")
        )
        if (
            retained != wire
            or verify_wire_origin(directory, wire) != origin
            or wire_run_binding(directory, wire, origin) != binding
        ):
            raise InterpretationRunRejected()
        name = binding["run_id"].partition(":")[2] + "-" + digest(wire["job_id"])[7:31]
        observation = self._read(
            directory / ("generation-observation-" + name + ".json")
        )
        _verify_row(observation, binding, origin)
        if (
            observation["status"] != "succeeded"
            or observation["binding"] != binding
            or observation["generation_plan_ref"] != plan["generation_plan_id"]
            or observation["job_id"] != wire["job_id"]
            or observation["wire_digest"] != digest(wire)
        ):
            raise InterpretationRunRejected()
        pins = {
            "schema": "adaos.builder.generation_output.v1",
            "run_id": binding["run_id"],
            "origin_ref": origin["origin_id"],
            "binding_ref": binding["binding_id"],
            "generation_plan_ref": plan["generation_plan_id"],
            "wire_digest": digest(wire),
            "job_id": wire["job_id"],
            "observation_ref": observation["observation_id"],
        }
        return self._safe(self.root / (name + ".json")), pins

    @staticmethod
    def _text_pins(text):
        if not isinstance(text, str):
            raise InterpretationRunRejected()
        payload = text.encode("utf-8")
        if len(payload) > _MAX_BYTES:
            raise InterpretationRunRejected()
        return {
            "text_digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "text_bytes": len(payload),
        }

    def _verify(self, row, pins):
        verify_identity(row, id_key="output_id", prefix="generation-output:")
        if (
            set(row)
            != set(pins)
            | {"output_text", "text_digest", "text_bytes", "output_id", "digest"}
            or any(row.get(key) != value for key, value in pins.items())
            or any(
                row.get(key) != value
                for key, value in self._text_pins(row["output_text"]).items()
            )
        ):
            raise InterpretationRunRejected()

    def retain(self, *, journal_dir, binding, origin, plan, wire, response):
        """After durable usage observation, pin only the facade's exact text."""
        try:
            path, pins = self._context(journal_dir, binding, origin, plan, wire)
            if (
                response.get("status") not in {"succeeded", "completed"}
                or response.get("job_id") != wire["job_id"]
                or response.get("request_id") not in (None, wire["request_id"])
            ):
                raise InterpretationRunRejected()
            text = response.get("output_text")
            row = seal(
                {**pins, **self._text_pins(text), "output_text": text},
                id_key="output_id",
                prefix="generation-output:",
            )
            if (
                len(
                    (json.dumps(row, ensure_ascii=False, indent=2) + "\n").encode(
                        "utf-8"
                    )
                )
                > _MAX_BYTES
            ):
                raise InterpretationRunRejected()
            with mutation_lock(self._safe(path.with_suffix(".lock")), timeout_s=30):
                self._safe(path)
                if path.exists():
                    before = self._read(path)
                    self._verify(before, pins)
                    if before != row:
                        raise InterpretationRunRejected()
                else:
                    atomic_write_json(path, row)
            return {"output_ref": row["output_id"], **self._text_pins(text)}
        except InterpretationRunRejected:
            raise
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise InterpretationRunRejected() from error

    def load(self, output_ref, *, journal_dir, binding, origin, plan, wire):
        """Resolve exact retained text for a later Core admission step only."""
        try:
            path, pins = self._context(journal_dir, binding, origin, plan, wire)
            row = self._read(path)
            self._verify(row, pins)
            if output_ref != row["output_id"]:
                raise InterpretationRunRejected()
            return row["output_text"]
        except InterpretationRunRejected:
            raise
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise InterpretationRunRejected() from error
