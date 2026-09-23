"""Release-pinned, private data/configuration effects for local channel cutover.

This initial adapter admits request/response skills, declared SQLite stores,
and stateless native event subscribers with an explicit runtime drain hook.
Workers with owned mutable data, external stores and undeclared mutable files
require explicit adapters; an empty drain receipt must never authorize their
migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing, nullcontext
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping
import hashlib
import json
import sqlite3

from adaos.domain.application import RuntimeSelection
from adaos.domain.relational_storage import RelationalMigration
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from .configuration import ApplicationConfigurationStore, ConfigurationConflict, _digest
from .blob_data_transition import BlobDataTransition
from .runtime_channel import ApplicationRuntimeChannel, RuntimeChannelConflict
from .runtime_transition import ApplicationRuntimeTransition, TransitionStep
from .sqlite_data_transition import SQLiteDataTransition


@dataclass(frozen=True)
class OwnedDataComponent:
    component_ref: str
    stable_root: Path | None
    beta_root: Path
    target_root: Path
    stable_manifest: Mapping[str, Any]
    target_manifest: Mapping[str, Any]


def automation_data_contract() -> dict[str, Any]:
    """Small generic authoring capsule; contains no installation values or paths."""
    return {
        "manifest_field": "skill.yaml:data_lifecycle",
        "schema_ref": "abi:skill.schema.json#/properties/data_lifecycle",
        "capability_declaration": {
            "schema_ref": "abi:skill.schema.json#/properties/capabilities",
            "shape": "skill.yaml capabilities is a flat unique string array, not an object with required/optional fields. Merge required SDK tokens without removing existing declarations.",
            "example": {"capabilities": ["storage.relational", "configuration.read", "configuration.write"]},
        },
        "declaration": {"schema": "adaos.skill.data_lifecycle.v1", "execution": "native_tools", "databases": []},
        "database_fields": {"path": "relative SQLite filename under this skill's SDK data root",
            "migrations": "full ordered list of {version: positive integer, name: string, statements: SQL string[]}"},
        "initialization_contract": {
            "call": "adaos.sdk.data.lifecycle.ensure_database(path) before opening the declared SQLite file; requires storage.relational.",
            "async": "Async handlers use a_ensure_database(path).",
            "behavior": "Reads the active skill.yaml chain and uses the same Core checksum ledger as Beta migration. Initializes an empty store or verifies an already migrated one. Existing installed data with pending migrations is rejected; DEV synthetic stores can migrate in place.",
            "boundary": "Do not implement another ledger or duplicate migrations in handlers. A mismatched ledger is a blocker, never a reason to clear history or ignore an exception.",
            "testing": "A mocked SDK can test caller ordering and consumer behavior, but does not verify Core migrations/checksums. Label such coverage as a test double. Independent acceptance executes the actual declared SQL chain with Core on synthetic fixtures, then qualifies installed migration separately.",
        },
        "configuration_contract": {
            "manifest": "configuration.schema is a JSON Schema for the non-secret values object; configuration.defaults must satisfy it.",
            "read": "adaos.sdk.data.configuration.read() -> {revision, values}; requires configuration.read.",
            "write": "adaos.sdk.data.configuration.write(values, expected_revision=revision) replaces the complete values object; requires configuration.write. Keep the revision from the displayed snapshot; do not reread it to bypass a conflict.",
            "async": "Async handlers use a_read() / a_write(values, expected_revision=revision).",
            "boundary": "Settings are not application database rows. Do not hard-code production values in manifests or copy credential references into forms/model input. SDK write preserves separate secret bindings.",
        },
        "blob_contract": {
            "capability": "storage.blob",
            "layout": "Core-owned local content-addressed objects under files/<binding>/objects/<prefix>/<sha256>.<ext>.",
            "behavior": "The local lifecycle verifies object paths and bytes, snapshots Stable into Beta, and adopts Beta objects with the same fenced data cutover. Remote blob providers require a provider-native adapter.",
        },
        "rules": [
            "Declare each owned SQLite store; use an empty databases list only when the skill has no mutable stores.",
            "Preserve applied migration versions/checksums. Append forward SQL migrations; keep fresh-install initialization compatible with the same schema.",
            "Core owns the adaos_schema_migrations checksum ledger during cutover. SQL statements may not modify it, change transaction boundaries, issue PRAGMA, attach databases or load extensions. A first chain must also handle an existing legacy schema without that ledger; test both empty and legacy databases with synthetic records.",
            "Develop and test with synthetic records only. Never inspect/copy Workspace or Trial records, configuration values or secrets into DEV, fixtures, packages or model input.",
            "Core snapshots accepted Stable and runs the pinned chain for each new Beta; Stable acceptance adopts Beta writes. Do not implement channel copying/resetting in handlers.",
            "Use adaos.sdk.data.configuration for declared non-secret settings; DEV is isolated and lifecycle inherits/adopts real settings. Secrets are separate scoped bindings.",
            "For new secret slots declare configuration.credentials.<name>.purpose and secrets.read/write, then use sdk.data.secrets. Values stay in the node vault; this adapter currently admits only the verified local owner, not delegated/background callers.",
            "Local storage.blob attachments use the verified Core content-addressed adapter. Background services, shared mutable stores, remote blob providers and credential files require qualified adapters; report a platform gap instead of claiming migration support."
        ],
    }


def declared_databases(manifest: Mapping[str, Any]) -> dict[str, tuple[RelationalMigration, ...]]:
    declaration = manifest.get("data_lifecycle")
    if not isinstance(declaration, dict) or declaration.get("schema") != "adaos.skill.data_lifecycle.v1":
        raise ValueError("Owned skill requires a pinned data_lifecycle declaration before data cutover")
    if set(declaration) != {"schema", "execution", "databases"} or declaration["execution"] != "native_tools":
        raise ValueError("Data cutover currently requires declared native_tools execution")
    databases = declaration["databases"]
    if not isinstance(databases, list) or len(databases) > 32:
        raise ValueError("Data lifecycle databases must be a bounded list")
    result = {}
    for database in databases:
        if not isinstance(database, dict) or set(database) != {"path", "migrations"}:
            raise ValueError("SQLite store must declare path and its full migration chain")
        path = database["path"]
        if (not isinstance(path, str) or not path or len(path) > 240 or "\\" in path or ":" in path
                or PurePosixPath(path).is_absolute() or any(part in {".", "..", ""} for part in path.split("/"))
                or path in result or path in {"files/secrets.json", "db/skill_env.json"}):
            raise ValueError("SQLite store requires a unique relative owner data path")
        chain = database["migrations"]
        if not isinstance(chain, list) or len(chain) > 200:
            raise ValueError("SQLite migration chain must be a bounded list")
        migrations = []
        for item in chain:
            if not isinstance(item, dict) or set(item) != {"version", "name", "statements"}:
                raise ValueError("Migration requires version, name and SQL statements")
            if (type(item["version"]) is not int or not isinstance(item["statements"], list)
                    or any(not isinstance(sql, str) or len(sql.encode("utf-8")) > 1024 * 1024 for sql in item["statements"])):
                raise ValueError("Invalid migration version or SQL statements")
            migrations.append(RelationalMigration(**item, dialects=("sqlite",)))
        if len({item.version for item in migrations}) != len(migrations):
            raise ValueError("Migration versions must be unique")
        result[path] = tuple(migrations)
    return result


def _declared_tool_names(manifest: Mapping[str, Any]) -> set[str]:
    tools = manifest.get("tools")
    if isinstance(tools, Mapping):
        return {
            str(name).strip()
            for name, value in tools.items()
            if str(name).strip() and isinstance(value, Mapping)
        }
    if isinstance(tools, list):
        return {
            str(value.get("name") or "").strip()
            for value in tools
            if isinstance(value, Mapping) and str(value.get("name") or "").strip()
        }
    return set()


def require_native_tools(manifest: Mapping[str, Any]) -> None:
    runtime = manifest.get("runtime") or {}
    lifecycle = manifest.get("lifecycle")
    lifecycle = lifecycle if isinstance(lifecycle, Mapping) else {}
    subscriptions = (manifest.get("events") or {}).get("subscribe")
    background = bool(
        manifest.get("service")
        or manifest.get("services")
        or lifecycle
        or manifest.get("workflow")
        or manifest.get("conversational")
        or subscriptions
        or any(
            runtime.get(key)
            for key in (
                "services",
                "service",
                "lifecycle",
                "after_activate",
                "rehydrate",
            )
        )
    )
    if not background:
        return

    drain_tool = str(lifecycle.get("drain") or manifest.get("drain") or "").strip()
    declaration = manifest.get("data_lifecycle")
    declared_databases_value = (
        declaration.get("databases") if isinstance(declaration, Mapping) else None
    )
    capabilities = {
        str(item).strip()
        for item in manifest.get("capabilities") or ()
        if str(item).strip()
    }
    stateless_native_subscriber = bool(
        subscriptions
        and not manifest.get("service")
        and not manifest.get("services")
        and not manifest.get("workflow")
        and not manifest.get("conversational")
        and not any(
            runtime.get(key)
            for key in ("services", "service", "lifecycle", "after_activate")
        )
        and isinstance(declaration, Mapping)
        and declaration.get("schema") == "adaos.skill.data_lifecycle.v1"
        and declaration.get("execution") == "native_tools"
        and declared_databases_value == []
        and not manifest.get("configuration")
        and "storage.blob" not in capabilities
        and drain_tool
        and drain_tool in _declared_tool_names(manifest)
    )
    if not stateless_native_subscriber:
        raise ValueError("Background/lifecycle execution requires a verified owner drain adapter before data cutover")


def inventory(root: Path | None, declared: Mapping[str, Any], *, blobs: BlobDataTransition | None = None) -> None:
    if root is None or not root.exists():
        return
    if root.absolute() != root.resolve():
        raise ValueError("Linked runtime data requires an explicit storage adapter")
    permitted = set(declared)
    for name in declared:
        permitted.update(name + suffix for suffix in ("-wal", "-shm", "-journal"))
    for path in root.rglob("*"):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)() or not path.resolve().is_relative_to(root):
            raise ValueError("Linked runtime data requires an explicit storage adapter")
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in permitted and not (blobs is not None and relative.startswith("files/")):
            raise ValueError("Undeclared runtime data requires a data/configuration/credential adapter")
    if blobs is not None:
        blobs.inventory(root / "files")


class LocalApplicationDataLifecycle:
    """Internal coordinator: caller admits publisher, release and component paths.

    Do not expose this constructor as a path-taking SDK or MCP function. Its
    callbacks must activate/verify the exact immutable release without selecting
    a channel; selection and admission are committed by this journal instead.
    """

    def __init__(self, *, state_root: Path, private_root: Path, application_id: str,
                 candidate_id: str, release_digest: str, stable_digest: str | None,
                 components: tuple[OwnedDataComponent, ...]):
        self.state = state_root.resolve()
        self.private = private_root.resolve()
        self.application_id = application_id
        self.candidate_id = candidate_id
        self.release_digest = release_digest
        self.stable_digest = stable_digest
        self.components = components
        self.channel = ApplicationRuntimeChannel(self.state, application_id)
        self.data = SQLiteDataTransition(self.private)
        self.blobs = BlobDataTransition(self.private)
        key = hashlib.sha256(json.dumps([application_id, candidate_id, release_digest]).encode()).hexdigest()
        self.recovery = self.private / "recovery/applications" / key
        self.contracts = {}
        self.blob_contracts = {}
        self.stable_blob_contracts = {}
        owned_roots = []
        for component in components:
            if not component.component_ref.startswith("skill:") or component.component_ref in self.contracts:
                raise ValueError("Data lifecycle requires unique owned skill identities")
            for root in (component.stable_root, component.beta_root, component.target_root):
                if root is not None and (root.absolute() != root.resolve() or not root.resolve().is_relative_to(self.private)):
                    raise ValueError("Data component escaped the local private root")
                if root is not None:
                    if any(root.is_relative_to(other) or other.is_relative_to(root) for other in owned_roots):
                        raise ValueError("Data component roots overlap another owner")
            if (component.beta_root == component.stable_root or component.beta_root == component.target_root
                    or component.beta_root.is_relative_to(component.target_root)
                    or component.target_root.is_relative_to(component.beta_root)):
                raise ValueError("Beta requires an isolated data root")
            owned_roots.extend(root for root in (component.stable_root, component.beta_root, component.target_root) if root is not None)
            require_native_tools(component.stable_manifest)
            require_native_tools(component.target_manifest)
            if component.stable_manifest.get("configuration") and not component.target_manifest.get("configuration"):
                raise ValueError("Removing configuration requires an explicit migration contract")
            databases = declared_databases(component.target_manifest)
            for parent in databases:
                if any(child.startswith(parent + "/") for child in databases):
                    raise ValueError("SQLite store paths overlap")
            self.contracts[component.component_ref] = databases
            stable_blobs = "storage.blob" in set(component.stable_manifest.get("capabilities") or [])
            target_blobs = "storage.blob" in set(component.target_manifest.get("capabilities") or [])
            if stable_blobs and not target_blobs:
                raise ValueError("Removing storage.blob requires an explicit blob migration contract")
            self.stable_blob_contracts[component.component_ref] = stable_blobs
            self.blob_contracts[component.component_ref] = target_blobs

    def _root(self, component, name):
        key = hashlib.sha256(component.component_ref.encode()).hexdigest()
        return self.recovery / key / name

    def _contract(self):
        return _digest({"release": self.release_digest, "stable": self.stable_digest,
            "components": [{"ref": item.component_ref, "stable_root": str(item.stable_root),
                "beta_root": str(item.beta_root), "target_root": str(item.target_root),
                "old": item.stable_manifest, "new": item.target_manifest} for item in self.components]})

    def _install_new(self, component, mode, name, staged, digest, target):
        marker = self._root(component, mode + "_install") / (name + ".json")
        intent = {"digest": digest, "target": str(target)}
        if marker.exists():
            if json.loads(marker.read_text(encoding="utf-8")) != intent:
                raise RuntimeChannelConflict("Retained data installation intent changed")
        else:
            if target.exists():
                raise RuntimeChannelConflict("Target data already exists without recovery provenance; refusing to overwrite")
            atomic_write_json(marker, intent)
        self.data.install(staged, target, staged_digest=digest)

    def _install_blobs_new(self, component, mode, staged, digest, target):
        marker = self._root(component, mode + "_blob_install") / "files.json"
        intent = {"digest": digest, "target": str(target)}
        if marker.exists():
            if json.loads(marker.read_text(encoding="utf-8")) != intent:
                raise RuntimeChannelConflict("Retained blob installation intent changed")
        else:
            if target.exists() and self.blobs.inventory(target)["objects"]:
                raise RuntimeChannelConflict("Target blob data already exists without recovery provenance; refusing to overwrite")
            atomic_write_json(marker, intent)
        self.blobs.install(staged, target, staged_digest=digest)

    def _run(self, mode: str, webspace_id: str, steps, *, allow_beta_data_reset=False):
        path = self.recovery / f"{mode}.intent.json"
        with mutation_lock(path.with_suffix(".lock")):
            contract = self._contract()
            if path.exists():
                saved = json.loads(path.read_text(encoding="utf-8"))
                if saved["contract"] != contract or saved["webspace_id"] != webspace_id:
                    raise RuntimeChannelConflict("Retained migration intent differs from requested candidate")
            else:
                expected = self.channel.read() or ()
                current = next((value for value in expected if value.webspace_id == webspace_id), None)
                if current is None and (expected or mode != "beta" or self.stable_digest):
                    raise RuntimeChannelConflict("Data cutover requires an explicitly selected Application installation")
                if mode == "beta" and current and current.runtime_root_ref == "workspace" and current.release_digest != self.stable_digest:
                    raise RuntimeChannelConflict("Selected Stable release differs from the migration base")
                if mode == "beta" and current and current.runtime_root_ref.startswith("trial:"):
                    if not allow_beta_data_reset:
                        raise RuntimeChannelConflict("Replacing Beta working data requires explicit data-loss acknowledgement and recovery protection")
                if mode == "stable" and (current.runtime_root_ref != f"trial:{self.candidate_id}" or current.release_digest != self.release_digest):
                    raise RuntimeChannelConflict("Accept only the exact selected Beta")
                source, root = ("local_trial", f"trial:{self.candidate_id}") if mode == "beta" else ("stable_installation", "workspace")
                target = (current.advance(expected_revision=current.revision, source=source,
                                          release_digest=self.release_digest, runtime_root_ref=root)
                          if current else RuntimeSelection(webspace_id=webspace_id, application_id=self.application_id,
                              source=source, release_digest=self.release_digest, runtime_root_ref=root, revision=1))
                saved = {"contract": contract, "webspace_id": webspace_id,
                         "expected": [value.to_dict() for value in expected], "target": target.to_dict()}
                atomic_write_json(path, saved)
            operation_id = f"application-{mode}:{self.application_id}:{self.candidate_id}"
            result = ApplicationRuntimeTransition(self.channel).run(operation_id, contract_digest=contract,
                expected=tuple(RuntimeSelection.from_mapping(value) for value in saved["expected"]),
                target=RuntimeSelection.from_mapping(saved["target"]), steps=steps)
            return {"ok": True, "operation_id": operation_id, "completed": result["completed"],
                    "runtime_selection": next(value for value in self.channel.read() if value.webspace_id == webspace_id).to_dict()}

    def prepare_beta(self, *, webspace_id: str, activate: Callable[[str], Mapping[str, Any]], allow_beta_data_reset=False):
        def transfer(key):
            for component in self.components:
                databases = self.contracts[component.component_ref]
                blob_adapter = self.blobs if self.blob_contracts[component.component_ref] else None
                inventory(component.stable_root, databases, blobs=blob_adapter)
                inventory(component.beta_root, databases, blobs=blob_adapter)
                for name, migrations in databases.items():
                    source = component.stable_root / name if component.stable_root else None
                    base = self._root(component, "stable") / name
                    if source is None or not source.exists():
                        source = self._root(component, "empty") / name
                        source.parent.mkdir(parents=True, exist_ok=True)
                        with closing(sqlite3.connect(source)) as connection:
                            connection.execute("PRAGMA user_version=0")
                            connection.commit()
                    snapshot = self.data.snapshot(source, base, operation_key=key + ":base:" + name)
                    staged = self._root(component, "migrated") / name
                    migrated = self.data.migrate(base, staged, snapshot_digest=snapshot["digest"],
                        migrations=migrations, operation_key=key + ":migrate:" + name)
                    self._install_new(component, "beta", name, staged, migrated["digest"], component.beta_root / name)
                if blob_adapter is not None:
                    source = component.stable_root / "files" if component.stable_root else None
                    staged = self._root(component, "stable_blobs") / "files"
                    snapshot = self.blobs.snapshot(source, staged, operation_key=key + ":base:files")
                    self._install_blobs_new(component, "beta", staged, snapshot["digest"], component.beta_root / "files")
            return {"ok": True, "mode": "stable_snapshot_forward", "contract_digest": self._contract()}

        def configure(_key):
            for component in self.components:
                target = component.target_manifest.get("configuration")
                if not target:
                    continue
                store = ApplicationConfigurationStore(self.state, self.application_id, component.component_ref)
                record = store.read()
                bound = (record.get("stable") or {}).get("credentials") or {}
                previous_slots = (component.stable_manifest.get("configuration") or {}).get("credentials") or {}
                target_slots = target.get("credentials") or {}
                if any(not previous_slots.get(name) or previous_slots.get(name) != target_slots.get(name) for name in bound):
                    raise ConfigurationConflict("Credential slot/purpose changed; explicit owner rebinding is required")
                if record["stable"] is None and self.stable_digest:
                    original = component.stable_manifest.get("configuration") or {
                        "schema": {"type": "object", "additionalProperties": False}, "defaults": {}}
                    record = store.set_stable(release_digest=self.stable_digest, schema=original["schema"],
                        values=original["defaults"], credentials={}, expected_revision=record["revision"])
                store.prepare_beta(candidate_id=self.candidate_id, release_digest=self.release_digest,
                    source_release_digest=self.stable_digest, schema=target["schema"], defaults=target["defaults"],
                    expected_revision=record["revision"])
            return {"ok": True}

        return self._run("beta", webspace_id, [TransitionStep("snapshot_migrate_data", transfer),
            TransitionStep("inherit_configuration", configure), TransitionStep("activate_verify", activate)],
            allow_beta_data_reset=allow_beta_data_reset)

    def accept_beta(self, *, webspace_id: str, publish: Callable[[str], Mapping[str, Any]]):
        def transfer(key):
            for component in self.components:
                databases = self.contracts[component.component_ref]
                blob_adapter = self.blobs if self.blob_contracts[component.component_ref] else None
                inventory(component.beta_root, databases, blobs=blob_adapter)
                inventory(component.stable_root, databases, blobs=blob_adapter)
                inventory(component.target_root, databases, blobs=blob_adapter)
                for name in databases:
                    base = self._root(component, "stable") / name
                    if component.stable_root and (component.stable_root / name).exists():
                        old = self.data.snapshot(component.stable_root / name, self._root(component, "stable_before_accept") / name,
                                                 operation_key=key + ":before:" + name)
                        receipt = json.loads(base.with_suffix(base.suffix + ".receipt.json").read_text(encoding="utf-8"))
                        if old["digest"] != receipt["digest"]:
                            raise RuntimeChannelConflict("Stable data changed while Beta was selected; explicit reconciliation required")
                    accepted = self._root(component, "accepted") / name
                    snapshot = self.data.snapshot(component.beta_root / name, accepted, operation_key=key + ":accepted:" + name)
                    target = component.target_root / name
                    if component.target_root == component.stable_root:
                        self.data.install(accepted, target, staged_digest=snapshot["digest"])
                    else:
                        self._install_new(component, "stable", name, accepted, snapshot["digest"], target)
                if blob_adapter is not None:
                    base = self._root(component, "stable_blobs") / "files"
                    base_receipt_path = base.parent / f"{base.name}.receipt.json"
                    if component.stable_root:
                        current = self.blobs.inventory(component.stable_root / "files")
                        if base_receipt_path.is_file():
                            base_digest = json.loads(base_receipt_path.read_text(encoding="utf-8"))["digest"]
                        elif not self.stable_blob_contracts[component.component_ref] and current["objects"] == 0:
                            # A Candidate prepared by Core before local blob cutover
                            # existed can add its first blob capability. Its old
                            # Stable manifest and an empty live inventory jointly
                            # prove the only admissible base state.
                            base_digest = self.blobs.inventory(None)["digest"]
                        else:
                            raise RuntimeChannelConflict("Stable blob snapshot evidence is missing")
                        if current["digest"] != base_digest:
                            raise RuntimeChannelConflict("Stable blob data changed while Beta was selected; explicit reconciliation required")
                    accepted = self._root(component, "accepted_blobs") / "files"
                    snapshot = self.blobs.snapshot(component.beta_root / "files", accepted,
                                                   operation_key=key + ":accepted:files")
                    target = component.target_root / "files"
                    if component.target_root == component.stable_root:
                        self.blobs.install(accepted, target, staged_digest=snapshot["digest"])
                    else:
                        self._install_blobs_new(component, "stable", accepted, snapshot["digest"], target)
            return {"ok": True, "keep_data": True}

        def configure(_key):
            for component in self.components:
                if component.target_manifest.get("configuration"):
                    store = ApplicationConfigurationStore(self.state, self.application_id, component.component_ref)
                    store.adopt_beta(candidate_id=self.candidate_id, expected_revision=store.read()["revision"])
            return {"ok": True}

        return self._run("stable", webspace_id, [TransitionStep("protect_and_adopt_data", transfer),
            TransitionStep("adopt_configuration", configure), TransitionStep("publish_verify", publish)])

    def abort_beta_preparation(self, *, verify_source: Callable[[str], Mapping[str, Any]], source_guard: Callable = nullcontext):
        """Cancel a failed prepare, never an accepted/published data cutover.

        Failed Beta data and snapshots remain private for diagnosis. The source
        was never overwritten by prepare; callers must verify the pinned Stable
        code/installation before making it executable again.
        """
        runner = ApplicationRuntimeTransition(self.channel)
        operation = f"application-beta:{self.application_id}:{self.candidate_id}"
        record = runner.get(operation)
        if (not record or record["intent"]["target"]["runtime_root_ref"] != f"trial:{self.candidate_id}"
                or any(item["runtime_root_ref"] != "workspace" or item["release_digest"] != self.stable_digest
                       for item in record["intent"]["expected"])):
            raise RuntimeChannelConflict("Only a failed Stable-to-Beta preparation can be cancelled")
        if runner.get(f"application-stable:{self.application_id}:{self.candidate_id}"):
            raise RuntimeChannelConflict("Stable data adoption has started; recover that exact publication instead")

        def configure(_key):
            for component in self.components:
                store = ApplicationConfigurationStore(self.state, self.application_id, component.component_ref)
                state = store.read()
                beta = state.get("beta")
                if beta and beta["active"]:
                    if beta["candidate_id"] != self.candidate_id:
                        raise RuntimeChannelConflict("Another Candidate owns the active configuration")
                    store.deactivate_beta(candidate_id=self.candidate_id, expected_revision=state["revision"])
            return {"ok": True, "overrides_retained": True}

        return runner.abort(operation, contract_digest=self._contract(), steps=[
            TransitionStep("deactivate_configuration", configure), TransitionStep("verify_source", verify_source)],
            source_guard=source_guard)

    def reject_beta(self, *, webspace_id: str,
                    verify_source: Callable[[str], Mapping[str, Any]],
                    source_guard: Callable = nullcontext):
        """Deselect a reviewed Beta while retaining its isolated data evidence."""
        runner = ApplicationRuntimeTransition(self.channel)
        operation = f"application-beta:{self.application_id}:{self.candidate_id}"
        record = runner.get(operation)
        if (not record or not record["completed"]
                or record["intent"]["target"]["webspace_id"] != webspace_id
                or record["intent"]["target"]["runtime_root_ref"] != f"trial:{self.candidate_id}"):
            raise RuntimeChannelConflict("Reject only the exact completed Beta selection")
        stable = runner.get(f"application-stable:{self.application_id}:{self.candidate_id}")
        if stable is not None:
            raise RuntimeChannelConflict("Stable adoption has started; recover that exact publication instead")

        def configure(_key):
            for component in self.components:
                store = ApplicationConfigurationStore(self.state, self.application_id, component.component_ref)
                state = store.read()
                beta = state.get("beta")
                if beta and beta["active"]:
                    if beta["candidate_id"] != self.candidate_id:
                        raise RuntimeChannelConflict("Another Candidate owns the active configuration")
                    store.deactivate_beta(candidate_id=self.candidate_id, expected_revision=state["revision"])
            return {"ok": True, "overrides_retained": True}

        return runner.reject_completed(operation, contract_digest=self._contract(), steps=[
            TransitionStep("deactivate_configuration", configure),
            TransitionStep("verify_source", verify_source),
        ], source_guard=source_guard)
