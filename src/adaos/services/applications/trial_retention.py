"""Retire a replaced Trial only after a committed, data-preserving cutover.

Runtime journals and metadata survive collection of executable trees. No business
data is read here; the native data transition owns snapshot verification.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.artifact_pipeline.trial_activation import trial_workspace_root
from adaos.services.storage_hygiene import apply_storage_cleanup_plan, create_storage_cleanup_plan

from .runtime_channel import ApplicationRuntimeChannel
from .store import ApplicationStore


REASON = "superseded_trial_data_migrated"
SCHEMA = "adaos.application.trial_supersession.v1"


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Trial retention metadata must be an object")
    return value


def _node(value: dict, application_id: str) -> tuple[str, str] | None:
    ref, digest = value.get("runtime_root_ref"), value.get("release_digest")
    if (value.get("application_id") != application_id or not isinstance(ref, str)
            or not ref.startswith("trial:") or not isinstance(digest, str)
            or len(digest) != 71 or not digest.startswith("sha256:")
            or any(char not in "0123456789abcdef" for char in digest[7:])):
        return None
    candidate = ref[6:]
    if not candidate or any(not (char.isalnum() or char in "-_") for char in candidate):
        return None
    return ref, digest


class SupersededTrialRetention:
    def __init__(self, *, state_dir: Path, workspace_root: Path):
        self.state = Path(state_dir).resolve()
        self.workspace = Path(workspace_root).resolve()
        self.pipeline = self.state / "artifact_pipeline"
        self.evidence = self.pipeline / "trial-supersessions"

    def _evidence_path(self, application_id: str, operation_id: str) -> Path:
        key = hashlib.sha256(f"{application_id}:{operation_id}".encode()).hexdigest()
        return self.evidence / f"{key}.json"

    def _proof(self, application_id: str, record: dict) -> dict | None:
        intent, receipts = record.get("intent") or {}, record.get("receipts") or {}
        target = _node(intent.get("target") or {}, application_id)
        expected = intent.get("expected") or []
        sources = {_node(item, application_id) for item in expected}
        snapshot, activated = receipts.get("snapshot_migrate_data") or {}, receipts.get("activate_verify") or {}
        contract = intent.get("contract_digest")
        if (record.get("schema") != "adaos.application.runtime_transition.v1"
                or record.get("completed") is not True or record.get("aborted") or record.get("recovery")
                or not target or not expected or None in sources or len(sources) != 1
                or target in sources or not isinstance(contract, str) or not contract.startswith("sha256:")
                or snapshot.get("ok") is not True or snapshot.get("mode") != "beta_snapshot_forward"
                or snapshot.get("contract_digest") != contract or activated.get("ok") is not True
                or record.get("fingerprint") != canonical_payload_digest(intent)[7:]
                or set(intent.get("steps") or []) != set(receipts)
                or any(receipt.get("ok") is not True for receipt in receipts.values())):
            return None
        operation_id = f"application-beta:{application_id}:{target[0][6:]}"
        if record.get("operation_id") != operation_id:
            return None
        source = next(iter(sources))
        proof = {"schema": SCHEMA, "application_id": application_id,
                 "operation_id": operation_id, "contract_digest": contract,
                 "transition_fingerprint": record.get("fingerprint"),
                 "source": {"runtime_root_ref": source[0], "release_digest": source[1]},
                 "target": {"runtime_root_ref": target[0], "release_digest": target[1]}}
        root = trial_workspace_root(self.workspace, target[0][6:])
        binding_path = root / ".adaos/data-transition.json"
        if binding_path.is_file():
            if binding_path.resolve() != binding_path:
                return None
            binding = _json(binding_path)
            beta_source = binding.get("beta_source") or {}
            if (binding.get("schema") != "adaos.application.local_data_binding.v1"
                    or binding.get("application_id") != application_id
                    or binding.get("candidate_id") != target[0][6:]
                    or binding.get("release_digest") != target[1]
                    or (beta_source.get("runtime_root_ref"), beta_source.get("release_digest")) != source
                    or binding.get("retained_retired_components")):
                return None
            release_path = root / ".adaos/releases" / (target[1][7:] + ".json")
            if release_path.is_file():
                lifecycle = (_json(release_path).get("composition_lock") or {}).get("lifecycle") or {}
                if any(item.get("data") == "retain" for item in (lifecycle.get("upgrade") or {}).get("retired_components", [])):
                    return None
            proof["binding_digest"] = canonical_payload_digest(binding)
        else:
            # An intermediate successor may itself have been collected. Only
            # previously verified, Core-owned metadata can close that chain.
            path = self._evidence_path(application_id, operation_id)
            retained = _json(path) if path.is_file() else {}
            if (any(retained.get(key) != value for key, value in proof.items()) or not retained.get("binding_digest")
                    or retained.get("proof_digest") != canonical_payload_digest(
                        {key: value for key, value in retained.items() if key != "proof_digest"})):
                return None
            proof["binding_digest"] = retained["binding_digest"]
        return proof

    def _assert_source_unchanged(self, root: Path, proof: dict, connection) -> None:
        raw = connection.execute("SELECT document FROM transitions WHERE operation_id=?", (proof["operation_id"],)).fetchone()
        completed_at = (json.loads(raw[0]).get("progress") or {}).get("updated_at") if raw else None
        if not completed_at:
            raise ValueError("Trial cutover timestamp is missing")
        # Existing journals store seconds, while files have finer timestamps.
        cutoff = datetime.fromisoformat(completed_at.replace("Z", "+00:00")).timestamp() + 1
        for data in (root / "skills/.runtime").glob("*/v*/data"):
            pending = [data]
            while pending:
                with os.scandir(pending.pop()) as entries:
                    for entry in entries:
                        metadata = entry.stat(follow_symlinks=False)
                        if entry.is_symlink() or getattr(metadata, "st_file_attributes", 0) & 0x400:
                            raise ValueError("Linked predecessor data requires explicit recovery")
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False) and not entry.name.endswith(("-shm", "-journal")):
                            if metadata.st_mtime > cutoff:
                                raise ValueError("Predecessor data changed after cutover")

    def _protected(self) -> tuple[set[str], set[str]]:
        selected, pending = ApplicationStore(self.state).runtime_selection_snapshot(include_pending=True)
        refs = {item.runtime_root_ref for item in (*selected, *pending)}
        digests = set()
        # A deliberately retained inactive Beta is not disposable merely
        # because another edition currently owns the execution channel.
        for collection in ("local_beta_admissions", "trial_access_grants", "retention/holds"):
            root = self.state / "applications" / collection
            for path in (*root.glob("*.json"), *root.glob("*/current.json")):
                item = _json(path)
                if item.get("status") != "active":
                    continue
                expiry = item.get("expires_at")
                if expiry and datetime.fromisoformat(str(expiry).replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                    continue
                if item.get("candidate_id"):
                    refs.add("trial:" + str(item["candidate_id"]))
                if item.get("release_digest"):
                    digests.add(str(item["release_digest"]))
        def retain_references(value):
            if isinstance(value, dict):
                if value.get("candidate_id"):
                    refs.add("trial:" + str(value["candidate_id"]))
                for item in value.values():
                    retain_references(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    retain_references(item)
            elif isinstance(value, str) and value.startswith("trial:"):
                refs.add(value)
        for operation in ApplicationStore(self.state).list_operations(statuses={"planned", "applying", "unknown", "reconciling"}):
            expiry = operation.plan.get("expires_at")
            if (operation.status == "planned" and expiry
                    and datetime.fromisoformat(str(expiry).replace("Z", "+00:00")) <= datetime.now(timezone.utc)):
                continue
            retain_references(operation.plan)
        return refs, digests

    def _plan_application(self, application_id: str, connection, *, protected) -> dict:
        channel = ApplicationRuntimeChannel(self.state, application_id)
        channel._assert_available(connection)
        row = connection.execute("SELECT document FROM channel WHERE id=1").fetchone()
        selections = channel._decode(row[0]) if row else ()
        if not selections:
            return {"actions": [], "proofs": []}
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='transitions'").fetchone():
            return {"actions": [], "proofs": []}
        proofs = []
        for raw, in connection.execute("SELECT document FROM transitions WHERE completed=1"):
            proof = self._proof(application_id, json.loads(raw))
            if proof:
                proofs.append(proof)
        # Walk back from the live channel, including chains of several beta
        # upgrades. A successful but abandoned fork cannot authorize deletion.
        reached = {(item.runtime_root_ref, item.release_digest) for item in selections}
        reached_releases = {item.release_digest for item in selections if item.runtime_root_ref == "workspace"}
        live_refs, held_digests = protected
        live_refs = live_refs | {item.runtime_root_ref for item in selections}
        covered = {}
        remaining = list(proofs)
        while remaining:
            advanced = False
            for proof in remaining[:]:
                source, target = proof["source"], proof["target"]
                source_node = source["runtime_root_ref"], source["release_digest"]
                target_node = target["runtime_root_ref"], target["release_digest"]
                if target_node not in reached and target_node[1] not in reached_releases:
                    continue
                reached.add(source_node)
                covered.setdefault(source_node, proof)
                remaining.remove(proof)
                advanced = True
            if not advanced:
                break
        actions = []
        for (ref, digest), proof in sorted(covered.items()):
            if ref in live_refs or digest in held_digests:
                continue
            candidate_id = ref[6:]
            activation_path = self.pipeline / "trial-activations" / f"{candidate_id}.json"
            activation = _json(activation_path) if activation_path.is_file() else {}
            identity = activation.get("candidate_ref") or {}
            if (identity.get("candidate_id") != candidate_id or identity.get("release_digest") != digest
                    or activation.get("status") not in {"active", "completed"}):
                continue
            root = trial_workspace_root(self.workspace, candidate_id)
            if not root.is_dir():
                continue
            if Path(str((activation.get("runtime_binding") or {}).get("path") or "")).resolve() != root:
                continue
            from .lifecycle_operations import assert_idle_jobs
            assert_idle_jobs(self.state, application_id, root)
            self._assert_source_unchanged(root, proof, connection)
            actions.append({"action": "delete_tree", "path": str(root), "reason": REASON,
                            "application_id": application_id, "candidate_id": candidate_id,
                            "release_digest": digest, "successor": proof["target"],
                            "bytes": 0, "bytes_measured": False, "mtime_ns": root.stat().st_mtime_ns})
        return {"actions": actions, "proofs": proofs}

    def plan(self, *, application_id: str | None = None) -> dict:
        protected = self._protected()
        ids = {application_id} if application_id else ApplicationRuntimeChannel.list_application_ids(self.state)
        actions, deferred = [], []
        for identity in sorted(ids):
            channel = ApplicationRuntimeChannel(self.state, identity)
            if not channel.path.is_file():
                continue
            try:
                with closing(sqlite3.connect(channel.path.as_uri() + "?mode=ro", uri=True, timeout=.25)) as connection:
                    connection.execute("BEGIN")
                    actions.extend(self._plan_application(identity, connection, protected=protected)["actions"])
            except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                deferred.append({"application_id": identity, "reason": type(exc).__name__, "detail": str(exc)[:200]})
        return {"ok": True, "dry_run": True, "actions": actions, "deferred": deferred}

    def apply(self, *, application_id: str | None = None, candidate_ids: set[str] | None = None) -> dict:
        protected = self._protected()
        ids = {application_id} if application_id else ApplicationRuntimeChannel.list_application_ids(self.state)
        jobs, results, deferred = [], [], []
        base = self.workspace.parent
        repository = base.parent if base.name == ".adaos" else base
        for identity in sorted(ids):
            channel = ApplicationRuntimeChannel(self.state, identity)
            if not channel.path.is_file():
                continue
            token = hashlib.sha256(identity.encode()).hexdigest()
            try:
                # Match native preparation's owner -> transition -> execution
                # order; an in-flight tool holds a SQLite read lease, so the
                # exclusive lease below cannot collect its executable tree.
                with mutation_lock(self.state / "applications/operation-leases" / f"{token}.lock", timeout_s=.25):
                    with mutation_lock(channel.path.with_suffix(".transition.lock"), timeout_s=.25):
                        with channel._connection() as connection:
                            connection.execute("BEGIN EXCLUSIVE")
                            plan = self._plan_application(identity, connection, protected=protected)
                            actions = [action for action in plan["actions"]
                                       if candidate_ids is None or action["candidate_id"] in candidate_ids]
                            if not actions:
                                continue
                            for proof in plan["proofs"]:
                                atomic_write_json(self._evidence_path(identity, proof["operation_id"]),
                                                  {**proof, "proof_digest": canonical_payload_digest(proof)})
                            storage_plan = create_storage_cleanup_plan(
                                [{**action, "spec_id": "superseded_trials", "owner": "applications",
                                  "expected_mtime_ns": action["mtime_ns"]} for action in actions],
                                workspace_root=repository, base_dir=base,
                                source="application-trial-supersession", profile="superseded-trials")
                            job = apply_storage_cleanup_plan(storage_plan["plan_digest"],
                                workspace_root=repository, base_dir=base, max_seconds=2, max_entries=1000)
                            jobs.append(job["job_id"])
                            for item in job["items"]:
                                results.append({"path": item["source_path"], "candidate_id": item["candidate_id"],
                                                "status": item["status"], "cleanup_job_id": job["job_id"],
                                                "deleted_bytes": item.get("deleted_bytes", 0),
                                                **({"error": item["error"]} if item.get("error") else {})})
            except (OSError, ValueError, RuntimeError, sqlite3.Error, TimeoutError) as exc:
                deferred.append({"application_id": identity, "reason": type(exc).__name__, "detail": str(exc)[:200]})
        return {"ok": not deferred and not any(item["status"] == "error" for item in results),
                "dry_run": False, "jobs": jobs, "results": results, "deferred": deferred}


def cleanup_superseded_trials(owner, application_id: str) -> dict:
    """Best effort after a committed new Trial; never fail its publication."""
    try:
        return SupersededTrialRetention(state_dir=Path(owner.paths.state_dir()),
            workspace_root=Path(owner.paths.workspace_dir())).apply(application_id=application_id)
    except (OSError, ValueError, RuntimeError, sqlite3.Error, TimeoutError) as exc:
        return {"ok": False, "status": "deferred", "reason": type(exc).__name__}
