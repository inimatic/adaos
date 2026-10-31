"""A serialized Run submission set, not an enclosing-turn billing certificate.

Reserve before the provider boundary. A crash leaves explicit uncertainty rather
than permission to spend again. Closure shares the same lock as submission and
requires terminal exact-wire measurement evidence for every registered job.
"""

from __future__ import annotations

import copy
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.builder_context import digest
from .generation_runs import verify_run_binding, wire_run_binding
from .interpretation_runs import InterpretationRunRejected
from .task_spec import seal, verify_identity


class GenerationSubmissions:
    def __init__(self, journal_dir, *, binding, origin, plan):
        verify_run_binding(binding, origin, plan)
        self.directory = Path(journal_dir)
        self.binding, self.origin, self.plan = copy.deepcopy((binding, origin, plan))
        self.path = self.directory / (
            "generation-submissions-" + binding["run_id"].partition(":")[2] + ".json"
        )
        self.prefix = (
            "generation-observation-" + binding["run_id"].partition(":")[2] + "-"
        )

    def _read(self, *, create=False):
        try:
            return self._read_unchecked(create=create)
        except InterpretationRunRejected:
            raise
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise InterpretationRunRejected() from error

    def _read_unchecked(self, *, create=False):
        if not self.path.exists():
            if not create:
                raise InterpretationRunRejected()
            return {
                "schema": "adaos.builder.generation_submission_scope.v1",
                "run_id": self.binding["run_id"],
                "origin_ref": self.origin["origin_id"],
                "invocation_scope": self.binding["invocation_scope"],
                "phase": "open",
                "submissions": [],
            }
        value = json.loads(self.path.read_text(encoding="utf-8"))
        verify_identity(value, id_key="scope_id", prefix="generation-submissions:")
        if (
            value.get("schema") != "adaos.builder.generation_submission_scope.v1"
            or value.get("run_id") != self.binding["run_id"]
            or value.get("origin_ref") != self.origin["origin_id"]
            or value.get("invocation_scope") != self.binding["invocation_scope"]
            or value.get("phase") not in {"open", "closed"}
            or not isinstance(value.get("submissions"), list)
            or not 1 <= len(value["submissions"]) <= 16
        ):
            raise InterpretationRunRejected()
        for row in value["submissions"]:
            if (
                not isinstance(row, dict)
                or not re.fullmatch(
                    r"sha256:[a-f0-9]{64}", str(row.get("request_digest", ""))
                )
                or not re.fullmatch(
                    r"generation-plan:[a-f0-9]{24}",
                    str(row.get("generation_plan_ref", "")),
                )
                or not re.fullmatch(
                    r"generation-binding:[a-f0-9]{24}", str(row.get("binding_ref", ""))
                )
                or row.get("status")
                not in {"reserved", "registered", "outcome_unknown"}
                or (
                    row.get("status") == "registered"
                    and (
                        not isinstance(row.get("job_id"), str)
                        or not 0 < len(row["job_id"]) <= 200
                    )
                )
            ):
                raise InterpretationRunRejected()
        requests = [row["request_digest"] for row in value["submissions"]]
        if len(set(requests)) != len(requests):
            raise InterpretationRunRejected()
        return value

    def _write(self, value):
        atomic_write_json(
            self.path, seal(value, id_key="scope_id", prefix="generation-submissions:")
        )

    @contextmanager
    def submitting(self, request_id):
        """Yield registration, holding the close/submit fence across the boundary."""
        if not isinstance(request_id, str) or not request_id:
            raise InterpretationRunRejected()
        self.directory.mkdir(parents=True, exist_ok=True)
        with mutation_lock(self.path.with_suffix(".lock"), timeout_s=30):
            value = self._read(create=True)
            identity = digest(request_id)
            if (
                value["phase"] != "open"
                or len(value["submissions"]) >= 16
                or any(row["status"] != "registered" for row in value["submissions"])
                or any(
                    row["request_digest"] == identity for row in value["submissions"]
                )
            ):
                raise InterpretationRunRejected()
            entry = {
                "request_digest": identity,
                "generation_plan_ref": self.plan["generation_plan_id"],
                "binding_ref": self.binding["binding_id"],
                "status": "reserved",
            }
            value["submissions"].append(entry)
            self._write(value)
            active = True

            def register(response):
                with mutation_lock(self.path.with_suffix(".lock"), timeout_s=30):
                    if not active or entry["status"] != "reserved":
                        raise InterpretationRunRejected()
                    job_id = response.get("job_id")
                    if not isinstance(job_id, str) or not 0 < len(job_id) <= 200:
                        raise InterpretationRunRejected()
                    entry.update(status="registered", job_id=job_id)
                    self._write(value)

            try:
                yield register
            finally:
                active = False
                if entry["status"] == "reserved":
                    entry["status"] = "outcome_unknown"
                    self._write(value)

    def close(self):
        """Close only this Run's submission set; no candidate/turn success claim."""
        try:
            with mutation_lock(self.path.with_suffix(".lock"), timeout_s=30):
                value = self._read()
                if value["phase"] == "closed":
                    closure = value["closure"]
                    verify_identity(
                        closure, id_key="closure_id", prefix="generation-closure:"
                    )
                    if (
                        closure["run_id"] != self.binding["run_id"]
                        or closure["origin_ref"] != self.origin["origin_id"]
                        or closure["invocation_scope"]
                        != self.binding["invocation_scope"]
                    ):
                        raise InterpretationRunRejected()
                    return copy.deepcopy(closure)
                if any(row["status"] != "registered" for row in value["submissions"]):
                    raise InterpretationRunRejected()
                jobs = [row["job_id"] for row in value["submissions"]]
                if len(set(jobs)) != len(jobs):
                    raise InterpretationRunRejected()
                lock = self.directory / (self.prefix + "run.lock")
                with mutation_lock(lock, timeout_s=30):
                    paths = sorted(self.directory.glob(self.prefix + "*.json"))
                    if len(paths) != len(jobs):
                        raise InterpretationRunRejected()
                    from .generation_observations import _verify_row, _TERMINAL

                    observations = [
                        json.loads(path.read_text(encoding="utf-8")) for path in paths
                    ]
                    for row in observations:
                        _verify_row(row, self.binding, self.origin)
                    if {row["job_id"] for row in observations} != set(jobs) or any(
                        row["status"] not in _TERMINAL for row in observations
                    ):
                        raise InterpretationRunRejected()
                    by_job = {row["job_id"]: row for row in observations}
                    for entry in value["submissions"]:
                        wire_path = self.directory / (
                            "wire-" + entry["request_digest"][7:39] + ".json"
                        )
                        wire = json.loads(wire_path.read_text(encoding="utf-8"))
                        row = by_job[entry["job_id"]]
                        if (
                            digest(wire["request_id"]) != entry["request_digest"]
                            or wire["job_id"] != entry["job_id"]
                            or digest(wire) != row["wire_digest"]
                            or wire["context_admission"]["generation_plan_ref"]
                            != entry["generation_plan_ref"]
                            or wire["execution_run_binding"]["binding_id"]
                            != entry["binding_ref"]
                            or wire_run_binding(self.directory, wire, self.origin)
                            != row["binding"]
                        ):
                            raise InterpretationRunRejected()
                    closure = seal(
                        {
                            "schema": "adaos.builder.generation_run_submission_closure.v1",
                            "run_id": self.binding["run_id"],
                            "origin_ref": self.origin["origin_id"],
                            "invocation_scope": self.binding["invocation_scope"],
                            "closed_at": datetime.now(timezone.utc).isoformat(),
                            "submission_refs": copy.deepcopy(value["submissions"]),
                            "observation_refs": sorted(
                                row["observation_id"] for row in observations
                            ),
                            "submission_closed_for_generation_run": True,
                            "whole_turn_accounting_complete": False,
                            "product_acceptance": "not_implied",
                        },
                        id_key="closure_id",
                        prefix="generation-closure:",
                    )
                    value.update(phase="closed", closure=closure)
                    self._write(value)
                    return closure
        except InterpretationRunRejected:
            raise
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise InterpretationRunRejected() from error
