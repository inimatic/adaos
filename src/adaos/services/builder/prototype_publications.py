"""Core-owned immutable preparation for failure-safe Prototype publication.

Preparation and resource materialization grant no authoring/visibility authority.
The committed workflow head, native readers, state forks and stale-write fence
must be connected before this protocol can replace compatibility publication.
Provider journals and mutable source files are intentionally outside this store.
"""

from __future__ import annotations

import copy
import json
import os
import re
import stat
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
        previous_publication_ref=None,
        brief=None,
    ):
        """Compile and retain a prepared view, without advancing any head.

        This internal preparation is not TaskSpec admission or Run completion.
        A deterministic namespace includes the semantic source, Brief and base
        publication so independently prepared successors cannot replace a base.
        """
        from adaos.sdk.developer.prototypes import derive_resource_specs

        if (
            not isinstance(project_ref, str)
            or not _PROJECT.fullmatch(project_ref)
            or not isinstance(change_id, str)
            or not change_id.strip()
            or len(change_id) > 256
            or not isinstance(revision, str)
            or not re.fullmatch(r"[0-9]{3,6}", revision)
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
        source_digest = digest({"document": source, "brief": source_brief})
        seed = {
            "schema": "adaos.builder.prototype_publication_seed.v1",
            "project_ref": project_ref,
            "change_id": change_id,
            "revision": revision,
            "previous_publication_ref": previous_publication_ref,
            "source_digest": source_digest,
        }
        publication_ref = "prototype-publication:" + digest(seed)[7:31]
        compiled = compile_semantic_prototype(
            source,
            brief=source_brief,
            project_ref=project_ref,
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
        value = seal(
            {
                "schema": "adaos.builder.prototype_publication_preparation.v1",
                "publication_ref": publication_ref,
                "seed": seed,
                "document": source,
                "brief": source_brief,
                "compiled": compiled,
                "webui_digest": prototype_webui_digest(webui),
                "resource_specs": derive_resource_specs(webui, resources=resources),
                "authoring_admitted": False,
                "head_advanced": False,
            },
            id_key="preparation_id",
            prefix="publication-preparation:",
        )
        self._bounded(value)
        path = self._path(publication_ref)
        with mutation_lock(self._path(publication_ref, ".lock"), timeout_s=30):
            self._write_once(path, value)
        return copy.deepcopy(value)

    def load(self, publication_ref):
        """Resolve only an exact immutable prepared snapshot, never a source mirror."""
        try:
            value = self._read(self._path(publication_ref))
            verify_identity(
                value, id_key="preparation_id", prefix="publication-preparation:"
            )
            seed = value["seed"]
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
        existing identical view. This is not a compatible-state fork operation.
        """
        with mutation_lock(self._path(publication_ref, ".lock"), timeout_s=30):
            value = self.load(publication_ref)
            seed = value["seed"]
            service = PrototypeResourceService(state_dir=self.state_dir)
            rows = []
            for spec in value["resource_specs"]:
                state = service.materialize(
                    {
                        "schema": "adaos.builder.prototype_resource.v1",
                        "project_ref": seed["project_ref"],
                        "change_id": seed["change_id"],
                        "revision": seed["revision"],
                        "webui_digest": value["webui_digest"],
                        **spec,
                    }
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
