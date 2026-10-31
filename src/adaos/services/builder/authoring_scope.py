"""Resolve logical task ownership to an existing component workflow.

This is a Core-owned manifest lookup, not task/meaning admission. It never
changes the selected primary, creates a Project head, or authorizes a model.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, ValidationError

from adaos.services.builder_context import digest


class AuthoringScopeRejected(ValueError):
    pass


def _resolve_authoring_scope(workflow, project_ref):
    if not isinstance(project_ref, str):
        raise AuthoringScopeRejected("Invalid authoring scope")
    kind, separator, identity = project_ref.partition(":")
    if (
        not separator
        or kind not in {"project", "scenario", "skill"}
        or not identity.strip()
        or identity.strip() in {".", ".."}
        or any(char in identity for char in ("/", "\\", "\0"))
    ):
        raise AuthoringScopeRejected("Invalid authoring scope")
    manifest_digest = None
    if kind == "project":
        path = workflow.project_root(kind, identity) / "project.yaml"
        with path.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise AuthoringScopeRejected("Project manifest exceeds bounded size")
        manifest = yaml.safe_load(raw.decode("utf-8-sig"))
        schema = json.loads(
            (
                Path(__file__).resolve().parents[2] / "abi/project.v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema).validate(manifest)
        if manifest["id"] != identity:
            raise AuthoringScopeRejected("Project manifest identity mismatch")
        primary = [
            row
            for row in manifest["components"]["owned"]
            if row.get("role") == "primary"
        ]
        if len(primary) != 1:
            raise AuthoringScopeRejected("Project requires one primary owned component")
        manifest_digest = digest(manifest)
        kind, separator, identity = primary[0]["ref"].partition(":")
        if not separator or kind not in {"scenario", "skill"}:
            raise AuthoringScopeRejected("Unsupported primary authoring component")
        from .prototype_data_owners import PrototypeDataOwners

        retained_owner = PrototypeDataOwners(workflow.state_dir).resolve(project_ref)
        if retained_owner is not None and retained_owner != f"{kind}:{identity}":
            raise AuthoringScopeRejected(
                "Primary authoring owner requires explicit Prototype data migration"
            )
    return {
        "project_ref": project_ref,
        "object_type": kind,
        "object_id": identity,
        "manifest_digest": manifest_digest,
    }


def resolve_authoring_scope(workflow, project_ref):
    try:
        return _resolve_authoring_scope(workflow, project_ref)
    except AuthoringScopeRejected:
        raise
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        yaml.YAMLError,
        ValidationError,
    ) as error:
        raise AuthoringScopeRejected(
            "Authoring scope is unavailable or invalid"
        ) from error


def verify_frozen_authoring_scope(workflow, origin):
    """Authoring-only recheck; late paid observations must not rebind ownership."""
    current = resolve_authoring_scope(workflow, origin["project_ref"])
    if any(current[key] != origin.get(key) for key in current):
        raise AuthoringScopeRejected(
            "Frozen authoring scope or Project manifest changed"
        )
    return current
