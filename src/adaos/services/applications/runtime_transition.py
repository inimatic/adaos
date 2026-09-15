"""Durable fail-closed cutover journal for admitted Application lifecycle effects.

Effects must be idempotent by their supplied operation key. Their receipts contain
identities/checksums only, never business records, settings values or credentials.
The journal does not claim to drain workers that bypass native execution leases.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import json
from typing import Any

from adaos.domain.application import RuntimeSelection
from adaos.services.artifact_pipeline.storage import mutation_lock
from .runtime_channel import ApplicationRuntimeChannel, RuntimeChannelConflict


@dataclass(frozen=True)
class TransitionStep:
    step_id: str
    apply: Callable[[str], Mapping[str, Any]]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class ApplicationRuntimeTransition:
    def __init__(self, channel: ApplicationRuntimeChannel):
        self.channel = channel

    def run(self, operation_id: str, *, contract_digest: str,
            expected: Sequence[RuntimeSelection], target: RuntimeSelection,
            steps: Sequence[TransitionStep]) -> dict[str, Any]:
        """Resume exact immutable intent; commit channel and completion together.

        Caller supplies already admitted/verified effects. A failed/unknown effect
        leaves a persistent fence. Recovery replays the same operation, not a new
        candidate, and must reconcile an effect whose receipt was not committed.
        """
        if (not operation_id or not contract_digest.startswith("sha256:")
                or len(contract_digest) != 71 or any(c not in "0123456789abcdef" for c in contract_digest[7:]) or not steps
                or any(not step.step_id for step in steps)
                or len({step.step_id for step in steps}) != len(steps)):
            raise ValueError("Exact transition identity, contract digest and unique steps are required")
        values = tuple(expected)
        self.channel._validate(values)
        current = next((item for item in values if item.webspace_id == target.webspace_id), None)
        if target.application_id != self.channel.application_id or target.revision != (current.revision if current else 0) + 1:
            raise ValueError("Target identity/revision does not match the reviewed selection")
        intent = {"contract_digest": contract_digest,
                  "expected": sorted([item.to_dict() for item in values], key=lambda item: item["webspace_id"]),
                  "target": target.to_dict(), "steps": [step.step_id for step in steps]}
        fingerprint = hashlib.sha256(_json(intent).encode("utf-8")).hexdigest()
        with mutation_lock(self.channel.path.with_suffix(".transition.lock")):
            record = self._begin(operation_id, fingerprint, intent, values)
            if record["completed"]:
                return record
            for step in steps:
                if step.step_id in record["receipts"]:
                    continue
                # Persist the attempted step before invoking code: lost acknowledgements
                # must be reconciled by the same effect key after process recovery.
                record["running_step"] = step.step_id
                self._write(operation_id, record)
                key = hashlib.sha256(_json([self.channel.application_id, operation_id, fingerprint, step.step_id]).encode("utf-8")).hexdigest()
                receipt = dict(step.apply(key))
                if receipt.get("ok") is not True:
                    raise RuntimeChannelConflict("Transition effect is not verified; runtime remains fenced")
                if len(_json(receipt).encode("utf-8")) > 65536:
                    raise ValueError("Transition receipt exceeds the metadata bound")
                record["receipts"][step.step_id] = receipt
                record["running_step"] = None
                self._write(operation_id, record)
            projected = [replace(item, source=target.source, release_digest=target.release_digest,
                         runtime_root_ref=target.runtime_root_ref, revision=item.revision + 1,
                         updated_at=target.updated_at) for item in values if item.webspace_id != target.webspace_id]
            projected.append(target)
            with self.channel._connection() as connection:
                connection.execute("BEGIN EXCLUSIVE")
                actual = self.channel._decode(connection.execute("SELECT document FROM channel WHERE id=1").fetchone()[0])
                if actual != values:
                    raise RuntimeChannelConflict("Channel changed during fenced transition")
                record["completed"] = True
                connection.execute("UPDATE channel SET document=? WHERE id=1", (self.channel._encode(projected),))
                connection.execute("UPDATE transitions SET completed=1, document=? WHERE operation_id=?", (_json(record), operation_id))
                connection.commit()
            return record

    def _begin(self, operation_id, fingerprint, intent, expected):
        with self.channel._connection() as connection:
            self.channel._initialize(connection, expected)
            connection.execute("BEGIN EXCLUSIVE")
            connection.execute("CREATE TABLE IF NOT EXISTS transitions (operation_id TEXT PRIMARY KEY, completed INTEGER NOT NULL, document TEXT NOT NULL)")
            row = connection.execute("SELECT document FROM transitions WHERE operation_id=?", (operation_id,)).fetchone()
            if row:
                record = json.loads(row[0])
                if record["fingerprint"] != fingerprint:
                    raise RuntimeChannelConflict("Transition operation was reused with different intent")
                connection.commit()
                return record
            self.channel._assert_available(connection)
            actual = self.channel._decode(connection.execute("SELECT document FROM channel WHERE id=1").fetchone()[0])
            if actual != expected:
                raise RuntimeChannelConflict("Channel changed before transition; review the current release")
            record = {"schema": "adaos.application.runtime_transition.v1", "operation_id": operation_id,
                      "fingerprint": fingerprint, "intent": intent, "completed": False,
                      "receipts": {}, "running_step": None}
            connection.execute("INSERT INTO transitions VALUES (?, 0, ?)", (operation_id, _json(record)))
            connection.commit()
            return record

    def _write(self, operation_id, record):
        with self.channel._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute("UPDATE transitions SET document=? WHERE operation_id=? AND completed=0", (_json(record), operation_id))
            if cursor.rowcount != 1:
                raise RuntimeChannelConflict("Transition journal changed")
            connection.commit()
