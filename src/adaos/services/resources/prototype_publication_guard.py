"""Mutation admission for publication-scoped local Prototype data.

Called under PrototypeResourceService's resource mutation lock. Reads of old
views stay supported; mutation must match the current Core authority and its
pinned materialization receipt. No DEV source/pointer or model tool is consulted.
"""

from __future__ import annotations

import re
from collections.abc import Mapping


def _scope(definition):
    from .prototype import PrototypeResourceConflict

    metadata = definition.get("metadata") or {}
    if not isinstance(metadata, Mapping):
        raise PrototypeResourceConflict("Invalid committed publication metadata")
    if "prototype_publication_ref" not in metadata:
        return None
    value = metadata["prototype_publication_ref"]
    if not isinstance(value, str) or not re.fullmatch(
        r"prototype-publication:[a-f0-9]{24}", value
    ):
        raise PrototypeResourceConflict("Invalid committed publication scope")
    return value


def assert_current_mutation(state, state_dir):
    # Keep Builder orchestration imports out of the provider startup graph and
    # avoid loading the semantic compiler for unaffected legacy resources.
    from adaos.services.builder.workflow_authority import WorkflowAuthority

    from .prototype import PrototypeResourceConflict, _digest

    try:
        scope = _scope(state["definition"])
        project_ref = state["project_ref"]
        metadata = state["definition"].get("metadata") or {}
        pinned = "publication_ref" in state or "workflow_ref" in state
        if pinned and (scope is None or state.get("publication_ref") != scope):
            raise PrototypeResourceConflict(
                "Published runtime scope pins differ from definition"
            )
        if "prototype_workflow_ref" in metadata:
            project_ref = metadata["prototype_workflow_ref"]
            if not isinstance(project_ref, str) or not re.fullmatch(
                r"scenario:[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", project_ref
            ):
                raise PrototypeResourceConflict("Invalid publication workflow owner")
            if not pinned:
                raise PrototypeResourceConflict(
                    "Published runtime workflow pins are missing"
                )
        if pinned and state.get("workflow_ref") != project_ref:
            raise PrototypeResourceConflict(
                "Published runtime workflow pins differ from definition"
            )
        authority = WorkflowAuthority(state_dir)
        path = authority.resolve(project_ref)
        if path is None:
            if scope is not None:
                raise PrototypeResourceConflict(
                    "Prototype publication is not committed"
                )
            return
        value = authority._read(path)
        authority.validate(project_ref, value)
        workflow = value.get("workflow") or {}
        if not isinstance(workflow, Mapping):
            raise PrototypeResourceConflict(
                "Current workflow publication authority is invalid"
            )
        head = workflow.get("prototype") or {}
        if not isinstance(head, Mapping):
            raise PrototypeResourceConflict(
                "Current workflow publication head is invalid"
            )
        current = head.get("publication_ref")
        if current is None and scope is None:
            return  # The unchanged legacy base during first authority adoption.
        if current is None or scope != current:
            raise PrototypeResourceConflict(
                "Prototype mutation uses a stale or uncommitted publication"
            )

        from adaos.services.builder.prototype_publications import PrototypePublications
        from adaos.services.builder.task_spec import verify_identity

        publications = PrototypePublications(state_dir)
        path = publications._path(current, ".materialized.json")
        if path.stat().st_size > 16 * 1024:
            raise PrototypeResourceConflict(
                "Current publication receipt exceeds its bounded size"
            )
        receipt = publications._read(path)
        verify_identity(
            receipt, id_key="materialization_id", prefix="publication-materialization:"
        )
        if (
            receipt.get("schema")
            != "adaos.builder.prototype_publication_materialization.v1"
            or receipt.get("publication_ref") != current
            or receipt.get("preparation_ref") != head.get("preparation_ref")
            or receipt.get("materialization_id") != head.get("materialization_ref")
        ):
            raise PrototypeResourceConflict(
                "Current publication materialization identity mismatch"
            )
        expected = {
            key: state[key]
            for key in ("resource_type", "definition_digest", "bundle_digest")
        }
        identity = {
            key: state[key]
            for key in ("project_ref", "change_id", "revision", "webui_digest")
        }
        if (
            expected not in receipt["resources"]
            or _digest(state["definition"]) != state["definition_digest"]
            or _digest(
                {
                    **identity,
                    "definition": state["definition"],
                    "data_definition": state["data_definition"],
                }
            )
            != state["bundle_digest"]
        ):
            raise PrototypeResourceConflict(
                "Current publication runtime bundle differs from its committed pin"
            )
    except PrototypeResourceConflict:
        raise
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise PrototypeResourceConflict(
            "Current publication mutation evidence unavailable or invalid"
        ) from error


def assert_materialization(previous, *, definition, project_ref, state_dir):
    from .prototype import PrototypeResourceConflict

    scope = _scope(definition)
    old_scope = _scope(previous["definition"]) if previous else None
    # Exact duplicate bundles return before this guard. A different bundle may
    # not redefine a publication view, including stripping its scope metadata.
    if previous and (
        scope is not None
        or old_scope is not None
        or "publication_ref" in previous
        or "workflow_ref" in previous
    ):
        raise PrototypeResourceConflict(
            "Publication-scoped runtime bundle is immutable"
        )
    if scope is None:
        assert_current_mutation(
            {"definition": definition, "project_ref": project_ref}, state_dir
        )
