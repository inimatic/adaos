"""Fenced data transition for a DEV skill runtime-bucket upgrade.

Builder Automation may prepare a new DEV runtime directly, without creating an
Application Candidate.  That changes the code lifecycle, not the ownership of
the skill's data.  Cross-bucket preparation therefore still has to use the
same Core-owned data adapters and native-adoption evidence as Trial/Stable.

This coordinator is deliberately internal.  Callers provide identities, not
filesystem paths; paths are derived from Core's private DEV root.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.skill.runtime_env import SkillRuntimeEnvironment

from .blob_data_transition import BlobDataTransition
from .data_lifecycle import (
    _SKILL_MEMORY_PATH,
    _SkillMemoryTransition,
    _StateFileTransition,
    _allows_legacy_adoption,
    _bounded_json_bytes,
    _bounded_state_file_bytes,
    _uses_skill_memory,
    declared_coordination_files,
    declared_databases,
    declared_operational_evidence_files,
    declared_reconstructible_directories,
    declared_state_files,
    inventory,
    require_native_tools,
)
from .native_data_adoption import adopted_native_data
from .runtime_channel import RuntimeChannelConflict
from .sqlite_data_transition import SQLiteDataTransition, verify_sqlite_schema


_SAFE_SKILL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeChannelConflict(f"Invalid retained DEV data-lifecycle evidence: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeChannelConflict(f"Invalid retained DEV data-lifecycle evidence: {path}")
    return value


@dataclass(frozen=True, slots=True)
class DevDataLifecycleResult:
    """Evidence returned to the Builder finalizer with a prepared runtime."""

    mode: str
    operation_id: str | None
    contract_digest: str | None
    source_version: str | None
    target_version: str
    source_runtime_bucket: str | None
    target_runtime_bucket: str
    transfer_receipt: Mapping[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "operation_id": self.operation_id,
            "contract_digest": self.contract_digest,
            "source_version": self.source_version,
            "target_version": self.target_version,
            "source_runtime_bucket": self.source_runtime_bucket,
            "target_runtime_bucket": self.target_runtime_bucket,
            "transfer_receipt": (
                dict(self.transfer_receipt) if self.transfer_receipt is not None else None
            ),
        }


class LocalDevSkillDataLifecycle:
    """Prepare and attest one exact DEV runtime-bucket data transition."""

    def __init__(
        self,
        *,
        private_root: Path,
        skills_root: Path,
        skill_id: str,
        target_version: str,
        target_manifest: Mapping[str, Any],
        request_id: str | None = None,
    ) -> None:
        self.private = Path(private_root).resolve()
        self.skills_root = Path(skills_root).resolve()
        self.skill_id = str(skill_id or "").strip()
        self.target_version = str(target_version or "").strip()
        self.target_manifest = json.loads(json.dumps(dict(target_manifest)))
        self.request_id = str(request_id or "").strip() or None
        if not _SAFE_SKILL_ID.fullmatch(self.skill_id):
            raise ValueError("DEV data lifecycle requires a bounded skill identity")
        if not self.target_version or len(self.target_version) > 128:
            raise ValueError("DEV data lifecycle requires a bounded target version")
        if str(self.target_manifest.get("version") or "").strip() != self.target_version:
            raise ValueError(
                "DEV data lifecycle target version must match the source manifest"
            )
        if not self.skills_root.is_relative_to(self.private):
            raise ValueError("DEV skills root escaped Core's private owner root")

        self.env = SkillRuntimeEnvironment(
            skills_root=self.skills_root,
            skill_name=self.skill_id,
        )
        self.source_version = self.env.resolve_active_version()
        self.source_bucket = (
            self.env.runtime_bucket(self.source_version)
            if self.source_version
            else None
        )
        self.target_bucket = self.env.runtime_bucket(self.target_version)
        self.source_root = (
            self.env.data_root(self.source_version) if self.source_version else None
        )
        self.target_root = self.env.data_root(self.target_version)

        declaration = self.target_manifest.get("data_lifecycle")
        self.native_transition = bool(
            self.source_version
            and self.source_bucket != self.target_bucket
            and isinstance(declaration, Mapping)
            and declaration.get("execution") == "native_tools"
        )
        self.source_manifest = self._active_manifest() if self.native_transition else {}
        self.contract_payload: dict[str, Any] | None = None
        self.contract_digest: str | None = None
        self.operation_id: str | None = None
        self.recovery: Path | None = None
        self.transfer_receipt: dict[str, Any] | None = None

        if self.native_transition:
            self._validate_contracts()
            self.contract_payload = {
                "schema": "adaos.dev_skill_data_transition.v1",
                "skill_id": self.skill_id,
                "source_version": self.source_version,
                "target_version": self.target_version,
                "source_runtime_bucket": self.source_bucket,
                "target_runtime_bucket": self.target_bucket,
                "source_root": str(self.source_root),
                "target_root": str(self.target_root),
                "source_manifest": self.source_manifest,
                "target_manifest": self.target_manifest,
            }
            self.contract_digest = _canonical_digest(self.contract_payload)
            token = self.contract_digest.split(":", 1)[1]
            self.operation_id = f"dev-skill-data:{self.skill_id}:{token}"
            self.recovery = self.private / "recovery" / "dev_skill_data" / token

        self.sqlite = SQLiteDataTransition(self.private)
        self.state_files = _StateFileTransition(self.private)
        self.skill_memory = _SkillMemoryTransition(self.private)
        self.blobs = BlobDataTransition(self.private)

    def _active_manifest(self) -> dict[str, Any]:
        if not self.source_version:
            return {}
        slot = self.env.read_active_slot(self.source_version)
        path = self.env.build_slot_paths(self.source_version, slot).resolved_manifest
        if not path.is_file():
            raise RuntimeChannelConflict(
                "Active DEV runtime has no immutable resolved manifest for data cutover"
            )
        value = _read_object(path)
        if str(value.get("version") or "").strip() != self.source_version:
            raise RuntimeChannelConflict(
                "Active DEV runtime manifest does not match its selected version"
            )
        return value

    def _validate_contracts(self) -> None:
        require_native_tools(self.target_manifest)
        target_databases = declared_databases(self.target_manifest)
        target_state = declared_state_files(self.target_manifest)
        target_coordination = declared_coordination_files(self.target_manifest)
        target_evidence = declared_operational_evidence_files(self.target_manifest)
        target_reconstructible = declared_reconstructible_directories(
            self.target_manifest
        )
        if set(target_databases) & set(target_state):
            raise ValueError("DEV data lifecycle declarations overlap")
        if self.source_root is None:
            return

        legacy = _allows_legacy_adoption(self.source_root, self.target_manifest)
        if not legacy:
            require_native_tools(self.source_manifest)
            source_databases = declared_databases(self.source_manifest)
            source_state = declared_state_files(self.source_manifest)
            if set(source_databases) - set(target_databases):
                raise ValueError(
                    "Removing a DEV SQLite store requires an explicit migration contract"
                )
            if set(source_state) - set(target_state):
                raise ValueError(
                    "Removing a DEV state file requires an explicit migration contract"
                )
            source_blobs = "storage.blob" in set(
                self.source_manifest.get("capabilities") or []
            )
            target_blobs = "storage.blob" in set(
                self.target_manifest.get("capabilities") or []
            )
            if source_blobs and not target_blobs:
                raise ValueError(
                    "Removing DEV blob storage requires an explicit migration contract"
                )
            if _uses_skill_memory(self.source_manifest) and not _uses_skill_memory(
                self.target_manifest
            ):
                raise ValueError(
                    "Removing DEV Core skill memory requires an explicit migration contract"
                )

        inventory(
            self.source_root,
            target_databases,
            state_files=target_state,
            blobs=self.blobs
            if "storage.blob" in set(self.target_manifest.get("capabilities") or [])
            else None,
            skill_memory=_uses_skill_memory(self.target_manifest),
            coordination_files=target_coordination,
            operational_evidence_files=target_evidence,
            reconstructible_directories=target_reconstructible,
        )

    def _paths(self) -> tuple[Path, Path, Path, Path]:
        if self.recovery is None:
            raise RuntimeError("DEV data transition does not require a recovery journal")
        return (
            self.recovery / "intent.json",
            self.recovery / "transfer.json",
            self.recovery / "prepared.json",
            self.recovery / "completed.json",
        )

    @staticmethod
    def _write_once(path: Path, payload: Mapping[str, Any], *, conflict: str) -> None:
        expected = dict(payload)
        if path.is_file():
            if _read_object(path) != expected:
                raise RuntimeChannelConflict(conflict)
            return
        atomic_write_json(path, expected)

    def _component_key(self, suffix: str) -> str:
        token = str(self.contract_digest or "").split(":", 1)[-1]
        return f"dev-skill-data:{token}:{suffix}"

    def _marker(self, kind: str, relative: str) -> Path:
        if self.recovery is None:
            raise RuntimeError("DEV data transition recovery is unavailable")
        token = hashlib.sha256(relative.encode("utf-8")).hexdigest()
        return self.recovery / "install" / kind / f"{token}.json"

    def _install_intent(
        self,
        *,
        kind: str,
        relative: str,
        digest: str,
        target: Path,
    ) -> None:
        marker = self._marker(kind, relative)
        intent = {
            "kind": kind,
            "relative": relative,
            "digest": digest,
            "target": str(target),
        }
        if marker.is_file():
            if _read_object(marker) != intent:
                raise RuntimeChannelConflict(
                    "Retained DEV data installation intent changed"
                )
            return
        if target.exists() and (
            target.is_file()
            or kind != "blob"
            or self.blobs.inventory(target).get("objects")
        ):
            raise RuntimeChannelConflict(
                "Target DEV data exists without recovery provenance; refusing to overwrite"
            )
        atomic_write_json(marker, intent)

    def _transfer(self) -> dict[str, Any]:
        if self.source_root is None:
            raise RuntimeChannelConflict("DEV data transfer has no selected source runtime")
        databases = declared_databases(self.target_manifest)
        state_files = declared_state_files(self.target_manifest)
        coordination = declared_coordination_files(self.target_manifest)
        evidence = declared_operational_evidence_files(self.target_manifest)
        reconstructible = declared_reconstructible_directories(self.target_manifest)
        blob_enabled = "storage.blob" in set(
            self.target_manifest.get("capabilities") or []
        )
        memory_enabled = _uses_skill_memory(self.target_manifest)
        blob_adapter = self.blobs if blob_enabled else None
        inventory(
            self.source_root,
            databases,
            state_files=state_files,
            blobs=blob_adapter,
            skill_memory=memory_enabled,
            coordination_files=coordination,
            operational_evidence_files=evidence,
            reconstructible_directories=reconstructible,
        )
        inventory(
            self.target_root,
            databases,
            state_files=state_files,
            blobs=blob_adapter,
            skill_memory=memory_enabled,
            coordination_files=coordination,
            operational_evidence_files=evidence,
            reconstructible_directories=reconstructible,
        )

        receipts: dict[str, Any] = {
            "schema": "adaos.dev_skill_data_transfer_receipt.v1",
            "databases": {},
            "state_files": {},
            "blob": None,
            "skill_memory": None,
        }
        for name, migrations in databases.items():
            source = self.source_root / name
            if not source.is_file():
                source = self.recovery / "empty" / name  # type: ignore[operator]
                source.parent.mkdir(parents=True, exist_ok=True)
                if not source.exists():
                    with closing(sqlite3.connect(source)) as connection:
                        connection.execute("PRAGMA user_version=0")
                        connection.commit()
            base = self.recovery / "snapshot" / name  # type: ignore[operator]
            snapshot = self.sqlite.snapshot(
                source,
                base,
                operation_key=self._component_key(f"snapshot:{name}"),
            )
            migrated_path = self.recovery / "migrated" / name  # type: ignore[operator]
            migrated = self.sqlite.migrate(
                base,
                migrated_path,
                snapshot_digest=str(snapshot["digest"]),
                migrations=migrations,
                operation_key=self._component_key(f"migrate:{name}"),
            )
            target = self.target_root / name
            self._install_intent(
                kind="database",
                relative=name,
                digest=str(migrated["digest"]),
                target=target,
            )
            self.sqlite.install(
                migrated_path,
                target,
                staged_digest=str(migrated["digest"]),
            )
            schema = verify_sqlite_schema(target, migrations)
            receipts["databases"][name] = {
                "digest": _file_digest(target),
                "schema_version": schema["schema_version"],
            }

        for name, contract in state_files.items():
            source = self.source_root / name
            target = self.target_root / name
            if not source.is_file():
                if target.exists():
                    raise RuntimeChannelConflict(
                        "Target DEV state file exists although the selected source has none"
                    )
                receipts["state_files"][name] = {"present": False}
                continue
            staged = self.recovery / "state_files" / name  # type: ignore[operator]
            snapshot = self.state_files.snapshot(
                source,
                staged,
                contract=contract,
                operation_key=self._component_key(f"state-file:{name}"),
            )
            self._install_intent(
                kind="state_file",
                relative=name,
                digest=str(snapshot["digest"]),
                target=target,
            )
            self.state_files.install(
                staged,
                target,
                contract=contract,
                staged_digest=str(snapshot["digest"]),
            )
            _payload, digest = _bounded_state_file_bytes(target, contract)
            receipts["state_files"][name] = {"present": True, "digest": digest}

        if blob_enabled:
            staged = self.recovery / "blobs" / "files"  # type: ignore[operator]
            snapshot = self.blobs.snapshot(
                self.source_root / "files",
                staged,
                operation_key=self._component_key("blob:files"),
            )
            target = self.target_root / "files"
            self._install_intent(
                kind="blob",
                relative="files",
                digest=str(snapshot["digest"]),
                target=target,
            )
            self.blobs.install(
                staged,
                target,
                staged_digest=str(snapshot["digest"]),
            )
            receipts["blob"] = {
                "digest": self.blobs.inventory(target)["digest"],
                "objects": self.blobs.inventory(target)["objects"],
            }

        if memory_enabled:
            source = self.source_root / _SKILL_MEMORY_PATH
            target = self.target_root / _SKILL_MEMORY_PATH
            if source.is_file():
                staged = self.recovery / "skill_memory" / "skill_env.json"  # type: ignore[operator]
                snapshot = self.skill_memory.snapshot(
                    source,
                    staged,
                    operation_key=self._component_key("skill-memory"),
                )
                self._install_intent(
                    kind="skill_memory",
                    relative=_SKILL_MEMORY_PATH,
                    digest=str(snapshot["digest"]),
                    target=target,
                )
                self.skill_memory.install(
                    staged,
                    target,
                    staged_digest=str(snapshot["digest"]),
                )
                _payload, digest = _bounded_json_bytes(target)
                receipts["skill_memory"] = {"present": True, "digest": digest}
            else:
                if target.exists():
                    raise RuntimeChannelConflict(
                        "Target DEV skill memory exists although the selected source has none"
                    )
                receipts["skill_memory"] = {"present": False}

        receipts["receipt_digest"] = _canonical_digest(receipts)
        self._verify_transfer(receipts)
        return receipts

    def _verify_transfer(self, receipt: Mapping[str, Any]) -> None:
        databases = declared_databases(self.target_manifest)
        state_files = declared_state_files(self.target_manifest)
        blob_enabled = "storage.blob" in set(
            self.target_manifest.get("capabilities") or []
        )
        memory_enabled = _uses_skill_memory(self.target_manifest)
        inventory(
            self.target_root,
            databases,
            state_files=state_files,
            blobs=self.blobs if blob_enabled else None,
            skill_memory=memory_enabled,
            coordination_files=declared_coordination_files(self.target_manifest),
            operational_evidence_files=declared_operational_evidence_files(
                self.target_manifest
            ),
            reconstructible_directories=declared_reconstructible_directories(
                self.target_manifest
            ),
        )
        database_receipts = receipt.get("databases")
        if not isinstance(database_receipts, Mapping):
            raise RuntimeChannelConflict("DEV database transfer receipt is invalid")
        for name, migrations in databases.items():
            expected = database_receipts.get(name)
            target = self.target_root / name
            if (
                not isinstance(expected, Mapping)
                or not target.is_file()
                or _file_digest(target) != expected.get("digest")
            ):
                raise RuntimeChannelConflict(
                    "Prepared DEV database changed after fenced migration"
                )
            verify_sqlite_schema(target, migrations)

        state_receipts = receipt.get("state_files")
        if not isinstance(state_receipts, Mapping):
            raise RuntimeChannelConflict("DEV state-file transfer receipt is invalid")
        for name, contract in state_files.items():
            expected = state_receipts.get(name)
            if not isinstance(expected, Mapping):
                raise RuntimeChannelConflict("DEV state-file transfer receipt is incomplete")
            target = self.target_root / name
            if expected.get("present") is False:
                if target.exists():
                    raise RuntimeChannelConflict(
                        "Prepared DEV state file appeared after fenced migration"
                    )
                continue
            if not target.is_file():
                raise RuntimeChannelConflict("Prepared DEV state file is missing")
            _payload, digest = _bounded_state_file_bytes(target, contract)
            if digest != expected.get("digest"):
                raise RuntimeChannelConflict(
                    "Prepared DEV state file changed after fenced migration"
                )

        if blob_enabled:
            expected = receipt.get("blob")
            current = self.blobs.inventory(self.target_root / "files")
            if not isinstance(expected, Mapping) or current["digest"] != expected.get(
                "digest"
            ):
                raise RuntimeChannelConflict(
                    "Prepared DEV blob data changed after fenced migration"
                )

        if memory_enabled:
            expected = receipt.get("skill_memory")
            if not isinstance(expected, Mapping):
                raise RuntimeChannelConflict("DEV skill-memory receipt is invalid")
            target = self.target_root / _SKILL_MEMORY_PATH
            if expected.get("present") is False:
                if target.exists():
                    raise RuntimeChannelConflict(
                        "Prepared DEV skill memory appeared after fenced migration"
                    )
            else:
                if not target.is_file():
                    raise RuntimeChannelConflict("Prepared DEV skill memory is missing")
                _payload, digest = _bounded_json_bytes(target)
                if digest != expected.get("digest"):
                    raise RuntimeChannelConflict(
                        "Prepared DEV skill memory changed after fenced migration"
                    )

    def prepare(self, callback: Callable[[], Any]) -> tuple[Any, DevDataLifecycleResult]:
        """Transfer data, prove adoption, and invoke the exact runtime prepare."""

        if not self.native_transition:
            prepared = callback()
            result = DevDataLifecycleResult(
                mode="same_bucket_or_manager_owned",
                operation_id=None,
                contract_digest=None,
                source_version=self.source_version,
                target_version=self.target_version,
                source_runtime_bucket=self.source_bucket,
                target_runtime_bucket=self.target_bucket,
                transfer_receipt=None,
            )
            return prepared, result

        assert self.recovery is not None
        assert self.contract_payload is not None
        assert self.contract_digest is not None
        assert self.operation_id is not None
        self.recovery.mkdir(parents=True, exist_ok=True)
        intent_path, transfer_path, prepared_path, _completed_path = self._paths()
        with mutation_lock(self.recovery / ".mutation.lock", timeout_s=300.0):
            intent = {
                "contract_digest": self.contract_digest,
                "contract": self.contract_payload,
                "request_id": self.request_id,
            }
            self._write_once(
                intent_path,
                intent,
                conflict="Retained DEV data migration intent differs from this runtime transition",
            )
            if transfer_path.is_file():
                transfer = _read_object(transfer_path)
                if transfer.get("contract_digest") != self.contract_digest:
                    raise RuntimeChannelConflict(
                        "Retained DEV data transfer belongs to another contract"
                    )
                receipt = transfer.get("receipt")
                if not isinstance(receipt, Mapping):
                    raise RuntimeChannelConflict("Retained DEV data transfer is incomplete")
                self._verify_transfer(receipt)
                self.transfer_receipt = dict(receipt)
            else:
                self.transfer_receipt = self._transfer()
                atomic_write_json(
                    transfer_path,
                    {
                        "contract_digest": self.contract_digest,
                        "receipt": self.transfer_receipt,
                    },
                )

            component = SimpleNamespace(
                target_root=self.target_root,
                target_manifest=self.target_manifest,
            )
            with adopted_native_data((component,)):
                prepared = callback()
            if str(getattr(prepared, "version", "") or "").strip() != self.target_version:
                raise RuntimeChannelConflict(
                    "Prepared DEV runtime version differs from the fenced data target"
                )
            self._verify_transfer(self.transfer_receipt)
            atomic_write_json(
                prepared_path,
                {
                    "contract_digest": self.contract_digest,
                    "version": self.target_version,
                    "slot": str(getattr(prepared, "slot", "") or "").strip(),
                    "resolved_manifest": str(
                        getattr(prepared, "resolved_manifest", "") or ""
                    ),
                },
            )

        return prepared, DevDataLifecycleResult(
            mode="fenced_native_dev_transition",
            operation_id=self.operation_id,
            contract_digest=self.contract_digest,
            source_version=self.source_version,
            target_version=self.target_version,
            source_runtime_bucket=self.source_bucket,
            target_runtime_bucket=self.target_bucket,
            transfer_receipt=self.transfer_receipt,
        )

    def complete(
        self,
        *,
        prepared: Any,
        active_slot: str,
        runtime_status: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Commit completion only after SkillManager proves exact activation."""

        if not self.native_transition:
            return {
                "ok": True,
                "mode": "same_bucket_or_manager_owned",
                "version": self.target_version,
                "slot": active_slot,
            }
        assert self.recovery is not None
        assert self.contract_digest is not None
        assert self.operation_id is not None
        _intent_path, _transfer_path, prepared_path, completed_path = self._paths()
        prepared_evidence = _read_object(prepared_path)
        expected_slot = str(getattr(prepared, "slot", "") or "").strip().upper()
        selected_slot = str(active_slot or "").strip().upper()
        selected_version = str(runtime_status.get("version") or "").strip()
        if (
            prepared_evidence.get("contract_digest") != self.contract_digest
            or prepared_evidence.get("version") != self.target_version
            or str(prepared_evidence.get("slot") or "").strip().upper()
            != expected_slot
            or selected_slot != expected_slot
            or selected_version != self.target_version
            or not bool(runtime_status.get("ready"))
            or not bool(runtime_status.get("active"))
        ):
            raise RuntimeChannelConflict(
                "DEV data lifecycle cannot complete without the exact prepared runtime activation"
            )
        receipt = {
            "ok": True,
            "schema": "adaos.dev_skill_data_activation_receipt.v1",
            "operation_id": self.operation_id,
            "contract_digest": self.contract_digest,
            "skill_id": self.skill_id,
            "source_version": self.source_version,
            "target_version": self.target_version,
            "slot": selected_slot,
        }
        with mutation_lock(self.recovery / ".mutation.lock", timeout_s=30.0):
            self._write_once(
                completed_path,
                receipt,
                conflict="Retained DEV data activation receipt changed",
            )
        return receipt


__all__ = [
    "DevDataLifecycleResult",
    "LocalDevSkillDataLifecycle",
]
