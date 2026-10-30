"""One acknowledged source turn cannot spend again under a changed packet.

The request ID still pins the exact wire contract. A separate exclusive ingress
fence prevents an environment/profile/compiler change from turning recovery of
that same source into another provider submission. A new trusted user turn has
a different Intent identity and can authorize a new experiment.
"""

from __future__ import annotations

import json
from pathlib import Path

from adaos.services.builder_context import digest
from .task_interpretation import TaskInterpretationRejected
from .task_spec import seal, verify_identity


def _reject(code, detail):
    raise TaskInterpretationRejected(
        [
            {
                "code": "task.interpretation_ingress_" + code,
                "gate": "G5",
                "owner": "context_compilation",
                "detail": detail,
            }
        ]
    )


def _read(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Invalid journal shape")
        return value
    except (OSError, ValueError, TypeError):
        _reject(
            "invalid",
            "A retained interpretation ingress record cannot be verified; no resubmission is allowed",
        )


def admit_ingress(journal_dir, *, task, intent, request_id, context, prior_brief):
    """Fence before submission; do not guess or rewrite historical input pins."""
    verify_identity(task, id_key="task_spec_id", prefix="task:")
    verify_identity(intent, id_key="intent_id", prefix="intent:")
    if task["source"]["digest"] != intent["digest"]:
        _reject("source_conflict", "Interpretation ingress and Core source disagree")
    directory = Path(journal_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("interpretation-ingress-" + intent["digest"][7:39] + ".json")
    expected = seal(
        {
            "schema": "adaos.builder.interpretation_ingress.v1",
            "source_digest": intent["digest"],
            "task_spec_ref": task["task_spec_id"],
            "history_basis_digest": (prior_brief or {}).get("digest"),
            "request_id": request_id,
            "context_digest": digest(context),
        },
        id_key="ingress_id",
        prefix="interpretation-ingress:",
    )
    # Legacy journals predate the fence. Refuse an ambiguous or changed same-
    # turn packet; never select the most convenient old result by filename.
    if not path.exists():
        matches = []
        for candidate in directory.glob("builder-interpretation-*.json"):
            record = _read(candidate)
            seed = record.get("task_spec")
            if not isinstance(seed, dict) or not isinstance(seed.get("source"), dict):
                _reject(
                    "invalid", "Retained interpretation source shape cannot be verified"
                )
            if seed["source"].get("digest") != intent["digest"]:
                continue
            try:
                verify_identity(
                    record["task_spec"], id_key="task_spec_id", prefix="task:"
                )
            except (ValueError, TypeError, AttributeError):
                _reject(
                    "invalid",
                    "Retained interpretation task identity cannot be verified",
                )
            if (
                not isinstance(record.get("request_id"), str)
                or candidate.name != record["request_id"] + ".json"
            ):
                _reject(
                    "invalid", "Retained interpretation identity cannot be verified"
                )
            matches.append(record)
            if len(matches) > 1:
                _reject(
                    "ambiguous",
                    "Several retained packets belong to the same acknowledged turn; select explicit recovery",
                )
        if matches and (
            matches[0]["request_id"] != request_id
            or matches[0].get("context") != context
        ):
            _reject(
                "changed",
                "This acknowledged turn already has a different frozen packet. "
                "Recover that exact request or authorize a new trusted turn; no resubmission occurred",
            )
    created = False
    try:
        with path.open("x", encoding="utf-8") as output:
            json.dump(expected, output, ensure_ascii=False, indent=2)
        created = True
    except FileExistsError:
        pass
    try:
        retained = _read(path)
        verify_identity(retained, id_key="ingress_id", prefix="interpretation-ingress:")
    except (OSError, ValueError, TypeError, AttributeError):
        _reject(
            "invalid",
            "The retained ingress fence cannot be verified; no resubmission is allowed",
        )
    if retained != expected:
        _reject(
            "changed",
            "The same acknowledged turn has a different frozen task/profile/context. "
            "Recover its exact request or use a new trusted turn; no resubmission occurred",
        )
    if not created and not (directory / (request_id + ".json")).exists():
        _reject(
            "uncertain",
            "Ingress was reserved without a durable job journal; explicit recovery is required",
        )
    return retained
