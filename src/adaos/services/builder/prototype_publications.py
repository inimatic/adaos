"""Core-owned immutable preparation for failure-safe Prototype publication.

Preparation and resource materialization grant no authoring/visibility authority.
The internal workflow commit does not migrate native Builder/readers. Compatible
data forks and mutation fences do not by themselves qualify native activation.
Provider journals and mutable source files are intentionally outside this store.
"""

from __future__ import annotations

import copy
import json
import os
import re
import stat
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.builder_context import digest
from adaos.services.resources.prototype import (
    PrototypeResourceService,
    prototype_webui_digest,
)

from .semantic_prototype import compile_semantic_prototype
from .task_spec import seal, verify_identity

_PUBLICATION = re.compile(r"prototype-publication:[a-f0-9]{24}")
_PROJECT = re.compile(r"(?:project|scenario):[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_MAX_BYTES = 32 * 1024 * 1024
_COMMIT_SCOPE = ContextVar("prototype_publication_commit_scope", default=None)


class PrototypePublicationRejected(ValueError):
    """A preparation cannot prove the requested immutable publication view."""


class PrototypePublications:
    def __init__(self, state_dir: Path):
        self.state_dir = Path(os.path.abspath(state_dir))

    @property
    def root(self):
        return self.state_dir / "builder" / "prototype_publications"

    def _safe(self, path):
        # Check every existing ancestor, not just the final file. The store is
        # Core-owned and no caller-supplied pathname is accepted.
        # Resolving a nonexistent leaf while another writer creates its
        # ancestors has platform-dependent results on Windows. Inspect each
        # existing component directly, including junction/reparse points.
        for component in (path, *path.parents):
            try:
                metadata = component.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode) or (
                getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            ):
                raise PrototypePublicationRejected(
                    "Publication store must not traverse links"
                )
        return path

    @staticmethod
    def _bounded(value):
        # Match atomic_write_json's actual UTF-8 representation, including
        # indentation and the final newline, rather than its compact digest.
        if (
            len(
                (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            )
            > _MAX_BYTES
        ):
            raise PrototypePublicationRejected(
                "Publication exceeds bounded storage size"
            )

    def _path(self, publication_ref, suffix=".json"):
        if not isinstance(publication_ref, str) or not _PUBLICATION.fullmatch(
            publication_ref
        ):
            raise PrototypePublicationRejected("Invalid publication reference")
        return self._safe(self.root / (publication_ref.partition(":")[2] + suffix))

    def _write_once(self, path, value):
        self._safe(path)
        self._bounded(value)
        if path.exists():
            if self._read(path) != value:
                raise PrototypePublicationRejected(
                    "Publication identity has conflicting content"
                )
            return
        atomic_write_json(path, value)

    def _read(self, path):
        self._safe(path)
        if path.stat().st_size > _MAX_BYTES:
            raise PrototypePublicationRejected(
                "Publication exceeds bounded storage size"
            )
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise PrototypePublicationRejected("Invalid publication content")
        return value

    def prepare(
        self,
        document,
        *,
        project_ref,
        change_id,
        revision,
        base_revision=None,
        previous_publication_ref=None,
        brief=None,
        source_scope=None,
        compiler_digest=None,
    ):
        """Compile and retain a prepared view, without advancing any head.

        This internal preparation is not TaskSpec admission or Run completion.
        A deterministic namespace includes the semantic source, Brief and base
        publication so independently prepared successors cannot replace a base.
        """
        from adaos.sdk.developer.prototypes import derive_resource_specs

        if compiler_digest is not None:
            from .generation_candidates import _compiler_identity

            if compiler_digest != _compiler_identity():
                raise PrototypePublicationRejected(
                    "Publication compiler differs from the admitted candidate"
                )

        if (
            not isinstance(project_ref, str)
            or not _PROJECT.fullmatch(project_ref)
            or not isinstance(change_id, str)
            or not change_id.strip()
            or len(change_id) > 256
            or not isinstance(revision, str)
            or not re.fullmatch(r"[0-9]{3,6}", revision)
            or (
                base_revision is not None
                and (
                    not isinstance(base_revision, str)
                    or not re.fullmatch(r"[0-9]{3,6}", base_revision)
                )
            )
            or (
                previous_publication_ref is not None
                and (
                    not isinstance(previous_publication_ref, str)
                    or not _PUBLICATION.fullmatch(previous_publication_ref)
                )
            )
        ):
            raise PrototypePublicationRejected("Invalid publication identity")
        source, source_brief = copy.deepcopy(document), copy.deepcopy(brief)
        self._bounded({"document": source, "brief": source_brief})
        runtime_fork = None
        if previous_publication_ref is not None:
            from .prototype_runtime_forks import capture

            runtime = PrototypeResourceService(state_dir=self.state_dir)
            with mutation_lock(runtime.lock_path, timeout_s=30):
                runtime_fork = capture(
                    self,
                    previous_publication_ref,
                    project_ref=project_ref,
                    base_revision=base_revision,
                )
        source_digest = digest({"document": source, "brief": source_brief})
        seed = {
            "schema": "adaos.builder.prototype_publication_seed.v1",
            "project_ref": project_ref,
            "change_id": change_id,
            "revision": revision,
            "base_revision": base_revision,
            "previous_publication_ref": previous_publication_ref,
            "source_digest": source_digest,
        }
        if source_scope is not None:
            seed["source_scope"] = copy.deepcopy(source_scope)
        if compiler_digest is not None:
            seed["compiler_digest"] = compiler_digest
        runtime_project_ref = self._runtime_project_ref(seed)
        if runtime_fork is not None:
            seed["runtime_fork_ref"] = runtime_fork["fork_id"]
        publication_ref = "prototype-publication:" + digest(seed)[7:31]
        compiled = compile_semantic_prototype(
            source,
            brief=source_brief,
            project_ref=runtime_project_ref,
            runtime_scope_ref=publication_ref,
        )
        webui = compiled["webui"]
        resources = compiled.get("prototype_resources")
        if resources is None:
            schemas = webui["ui"]["application"]["desktop"]["pageSchema"]["meta"][
                "builder"
            ]["prototype_record_schemas"]
            if len(schemas) != 1:
                raise PrototypePublicationRejected(
                    "Legacy publication requires exactly one resource"
                )
            resources = [
                {
                    "resource_type": next(iter(schemas)),
                    "records": compiled["prototype_records"],
                }
            ]
        resource_specs = derive_resource_specs(webui, resources=resources)
        for spec in resource_specs:
            definition = spec["resource_definition"]
            definition["metadata"] = {
                **definition.get("metadata", {}),
                "prototype_publication_ref": publication_ref,
            }
            if runtime_project_ref != project_ref:
                definition["metadata"]["prototype_workflow_ref"] = project_ref
        value = {
            "schema": "adaos.builder.prototype_publication_preparation.v1",
            "publication_ref": publication_ref,
            "seed": seed,
            "document": source,
            "brief": source_brief,
            "compiled": compiled,
            "webui_digest": prototype_webui_digest(webui),
            "resource_specs": resource_specs,
            "authoring_admitted": False,
            "head_advanced": False,
        }
        if runtime_fork is not None:
            from .prototype_runtime_forks import apply

            value["runtime_fork"] = runtime_fork
            apply(self, value, runtime_fork)
        value = seal(
            value,
            id_key="preparation_id",
            prefix="publication-preparation:",
        )
        if compiler_digest is not None and compiler_digest != _compiler_identity():
            raise PrototypePublicationRejected(
                "Publication compiler changed during preparation"
            )
        self._bounded(value)
        path = self._path(publication_ref)
        with mutation_lock(self._path(publication_ref, ".lock"), timeout_s=30):
            self._write_once(path, value)
            self._write_once(
                self._path(publication_ref, ".index.json"), self._index(value)
            )
        return copy.deepcopy(value)

    @staticmethod
    def _runtime_project_ref(seed):
        """Keep logical task/data ownership distinct from its workflow head."""
        scope = seed.get("source_scope")
        if scope is None:
            return seed["project_ref"]
        if (
            not isinstance(scope, dict)
            or set(scope)
            != {"project_ref", "object_type", "object_id", "manifest_digest"}
            or not isinstance(scope["project_ref"], str)
            or not _PROJECT.fullmatch(scope["project_ref"])
            or scope["object_type"] != "scenario"
            or f"{scope['object_type']}:{scope['object_id']}" != seed["project_ref"]
            or (
                scope["project_ref"].startswith("project:")
                and (
                    not isinstance(scope["manifest_digest"], str)
                    or not re.fullmatch(
                        r"sha256:[a-f0-9]{64}", scope["manifest_digest"]
                    )
                )
            )
            or (
                not scope["project_ref"].startswith("project:")
                and (
                    scope["project_ref"] != seed["project_ref"]
                    or scope["manifest_digest"] is not None
                )
            )
        ):
            raise PrototypePublicationRejected(
                "Invalid publication source-scope binding"
            )
        return scope["project_ref"]

    @staticmethod
    def _index(value):
        # Bounded metadata allows history traversal without reading every
        # large semantic/fixture/UI payload. It is not proof of commitment.
        return seal(
            {
                "schema": "adaos.builder.prototype_publication_index.v1",
                "publication_ref": value["publication_ref"],
                "seed": value["seed"],
                "preparation_ref": value["preparation_id"],
                "webui_digest": value["webui_digest"],
            },
            id_key="index_id",
            prefix="publication-index:",
        )

    def index(self, publication_ref):
        try:
            path = self._path(publication_ref, ".index.json")
            if path.stat().st_size > 16 * 1024:
                raise PrototypePublicationRejected(
                    "Publication index exceeds bounded size"
                )
            value = self._read(path)
            verify_identity(value, id_key="index_id", prefix="publication-index:")
            seed = value["seed"]
            if (
                value["schema"] != "adaos.builder.prototype_publication_index.v1"
                or value["publication_ref"] != publication_ref
                or publication_ref != "prototype-publication:" + digest(seed)[7:31]
                or not _PROJECT.fullmatch(seed["project_ref"])
                or not re.fullmatch(r"[0-9]{3,6}", seed["revision"])
                or not re.fullmatch(
                    r"publication-preparation:[a-f0-9]{24}", value["preparation_ref"]
                )
                or not re.fullmatch(r"sha256:[a-f0-9]{64}", value["webui_digest"])
            ):
                raise PrototypePublicationRejected(
                    "Publication index identity mismatch"
                )
            return value
        except PrototypePublicationRejected:
            raise
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise PrototypePublicationRejected(
                "Publication index unavailable or invalid"
            ) from error

    def lineage(self, publication_ref, *, project_ref):
        """Resolve a bounded reference chain, not independently assert admission.

        The caller must anchor its first reference in the committed workflow
        head. Orphan preparations/index files are never scanned or promoted.
        """
        rows, visited = [], set()
        current = publication_ref
        while current is not None:
            if len(rows) >= 512 or current in visited:
                raise PrototypePublicationRejected(
                    "Publication lineage exceeds its bound or contains a cycle"
                )
            row = self.index(current)
            seed = row["seed"]
            if seed["project_ref"] != project_ref or (
                rows
                and (
                    rows[-1]["seed"]["base_revision"] != seed["revision"]
                    or int(rows[-1]["seed"]["revision"]) <= int(seed["revision"])
                )
            ):
                raise PrototypePublicationRejected(
                    "Publication lineage target or revision mismatch"
                )
            visited.add(current)
            rows.append(row)
            current = seed["previous_publication_ref"]
        return rows

    def has_snapshot(self, publication_ref):
        path = self._path(publication_ref)
        return path.is_file() and path.stat().st_size <= _MAX_BYTES

    def load(self, publication_ref):
        """Resolve only an exact immutable prepared snapshot, never a source mirror."""
        try:
            value = self._read(self._path(publication_ref))
            verify_identity(
                value, id_key="preparation_id", prefix="publication-preparation:"
            )
            seed = value["seed"]
            fork = value.get("runtime_fork")
            self._runtime_project_ref(seed)
            if fork is not None:
                verify_identity(fork, id_key="fork_id", prefix="runtime-fork:")
                if (
                    fork["fork_id"] != seed.get("runtime_fork_ref")
                    or fork["project_ref"] != seed["project_ref"]
                    or fork["base_publication_ref"] != seed["previous_publication_ref"]
                    or fork["base_revision"] != seed["base_revision"]
                ):
                    raise PrototypePublicationRejected("Runtime fork identity mismatch")
            if (
                value["schema"] != "adaos.builder.prototype_publication_preparation.v1"
                or value["publication_ref"] != publication_ref
                or publication_ref != "prototype-publication:" + digest(seed)[7:31]
                or seed["source_digest"]
                != digest({"document": value["document"], "brief": value["brief"]})
                or value["webui_digest"]
                != prototype_webui_digest(value["compiled"]["webui"])
                or value["authoring_admitted"] is not False
                or value["head_advanced"] is not False
                or (seed.get("runtime_fork_ref") is not None and fork is None)
                or self.index(publication_ref) != self._index(value)
            ):
                raise PrototypePublicationRejected(
                    "Publication preparation identity mismatch"
                )
            return value
        except PrototypePublicationRejected:
            raise
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise PrototypePublicationRejected(
                "Publication preparation unavailable or invalid"
            ) from error

    def materialize(self, publication_ref):
        """Prepare isolated runtime bundles; partial failure exposes no new head.

        Immutable preparation is retained before resource writes. Retrying a
        partial write reuses the same bundle identities and never reseeds an
        existing identical view. Compatible forks have separately pinned initial
        records; source fixtures remain the explicit reset baseline.
        """
        with mutation_lock(self._path(publication_ref, ".lock"), timeout_s=30):
            value = self.load(publication_ref)
            seed = value["seed"]
            service = PrototypeResourceService(state_dir=self.state_dir)
            rows = []
            for spec in value["resource_specs"]:
                from .prototype_runtime_forks import initial_records

                initial = initial_records(value, spec)
                state = service.materialize(
                    {
                        "schema": "adaos.builder.prototype_resource.v1",
                        "project_ref": self._runtime_project_ref(seed),
                        "change_id": seed["change_id"],
                        "revision": seed["revision"],
                        "webui_digest": value["webui_digest"],
                        **spec,
                    },
                    **({"_initial_records": initial} if initial is not None else {}),
                )["state"]
                rows.append(
                    {
                        key: state[key]
                        for key in (
                            "resource_type",
                            "bundle_digest",
                            "definition_digest",
                        )
                    }
                )
            receipt = seal(
                {
                    "schema": "adaos.builder.prototype_publication_materialization.v1",
                    "publication_ref": publication_ref,
                    "preparation_ref": value["preparation_id"],
                    "resources": rows,
                    "head_advanced": False,
                },
                id_key="materialization_id",
                prefix="publication-materialization:",
            )
            self._write_once(self._path(publication_ref, ".materialized.json"), receipt)
            return receipt

    @contextmanager
    def commit_guard(self, project_ref):
        """Serialize managed publication and resource changes through commit."""
        if not isinstance(project_ref, str) or not _PROJECT.fullmatch(project_ref):
            raise PrototypePublicationRejected("Invalid publication target")
        lock = self._safe(self.root / ".commits" / (digest(project_ref)[7:] + ".lock"))
        runtime = PrototypeResourceService(state_dir=self.state_dir)
        with (
            mutation_lock(lock, timeout_s=30),
            mutation_lock(runtime.lock_path, timeout_s=30),
        ):
            scope = {
                "target": (str(self.state_dir), project_ref),
                "thread": threading.get_ident(),
                "active": True,
            }
            token = _COMMIT_SCOPE.set(scope)
            try:
                yield
            finally:
                # A captured/copied context must not keep a capability after
                # the locks have been released, including a delayed task.
                scope["active"] = False
                _COMMIT_SCOPE.reset(token)

    def assert_commit_guard(self, project_ref):
        scope = _COMMIT_SCOPE.get()
        if (
            not scope
            or scope["active"] is not True
            or scope["thread"] != threading.get_ident()
            or scope["target"] != (str(self.state_dir), project_ref)
        ):
            raise PrototypePublicationRejected(
                "Publication commit requires its managed guard"
            )

    def ready(self, publication_ref):
        """Check the exact completion receipt and all retained runtime bundles.

        Mutable records are deliberately not equated to their fixture seeds.
        Definitions and ownership, however, must still match preparation. This
        method performs no repair or materialization on a reader's behalf.
        """
        try:
            value = self.load(publication_ref)
            receipt = self._read(self._path(publication_ref, ".materialized.json"))
            verify_identity(
                receipt,
                id_key="materialization_id",
                prefix="publication-materialization:",
            )
            if (
                receipt["schema"]
                != "adaos.builder.prototype_publication_materialization.v1"
                or receipt["publication_ref"] != publication_ref
                or receipt["preparation_ref"] != value["preparation_id"]
                or receipt["head_advanced"] is not False
                or len(receipt["resources"]) != len(value["resource_specs"])
            ):
                raise PrototypePublicationRejected(
                    "Publication materialization identity mismatch"
                )
            seed = value["seed"]
            identity = {
                key: seed[key] for key in ("project_ref", "change_id", "revision")
            }
            identity["project_ref"] = self._runtime_project_ref(seed)
            identity["webui_digest"] = value["webui_digest"]
            runtime = PrototypeResourceService(state_dir=self.state_dir)
            with mutation_lock(runtime.lock_path, timeout_s=30):
                for spec, row in zip(
                    value["resource_specs"], receipt["resources"], strict=True
                ):
                    definition = copy.deepcopy(spec["resource_definition"])
                    definition["metadata"] = {
                        **definition.get("metadata", {}),
                        "prototype": True,
                        **identity,
                    }
                    resource_type = definition["resource_type"]
                    definition_digest = digest(definition)
                    bundle_digest = digest(
                        {
                            **identity,
                            "definition": definition,
                            "data_definition": spec["data_definition"],
                        }
                    )
                    expected = {
                        "resource_type": resource_type,
                        "definition_digest": definition_digest,
                        "bundle_digest": bundle_digest,
                    }
                    state = runtime.snapshot(resource_type)
                    has_pins = isinstance(state, dict) and (
                        "publication_ref" in state or "workflow_ref" in state
                    )
                    scoped_owner = (
                        self._runtime_project_ref(seed) != seed["project_ref"]
                    )
                    if (
                        row != expected
                        or not isinstance(state, dict)
                        or any(
                            state.get(key) != item
                            for key, item in {**identity, **expected}.items()
                        )
                        or state.get("definition") != definition
                        or state.get("data_definition") != spec["data_definition"]
                        or (
                            (has_pins or scoped_owner)
                            and (
                                state.get("publication_ref") != publication_ref
                                or state.get("workflow_ref") != seed["project_ref"]
                            )
                        )
                    ):
                        raise PrototypePublicationRejected(
                            "Prepared publication runtime bundle changed or missing"
                        )
            return {"preparation": value, "materialization": receipt}
        except PrototypePublicationRejected:
            raise
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise PrototypePublicationRejected(
                "Publication materialization unavailable or invalid"
            ) from error

    def admit(
        self,
        publication_ref,
        *,
        project_ref,
        revision,
        base_revision,
        previous_publication_ref,
        change_id,
        workflow=None,
    ):
        """Validate pins for a caller holding commit_guard, not a standalone commit."""
        self.assert_commit_guard(project_ref)
        result = self.ready(publication_ref)
        seed = result["preparation"]["seed"]
        expected = {
            "project_ref": project_ref,
            "revision": revision,
            "base_revision": base_revision,
            "previous_publication_ref": previous_publication_ref,
            "change_id": change_id,
        }
        if any(seed.get(key) != item for key, item in expected.items()) or (
            base_revision is not None and int(revision) <= int(base_revision)
        ):
            raise PrototypePublicationRejected(
                "Publication target or base no longer matches workflow"
            )
        if seed.get("source_scope") is not None:
            from .authoring_scope import verify_frozen_authoring_scope

            if workflow is None:
                raise PrototypePublicationRejected(
                    "Source-scoped publication requires its Core workflow resolver"
                )
            verify_frozen_authoring_scope(workflow, seed["source_scope"])
        from .prototype_runtime_forks import assert_current

        assert_current(self, result["preparation"])
        return result
