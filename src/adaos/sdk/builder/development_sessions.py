"""Policy-scoped Builder Development Sessions.

A session is mutable workflow state, deliberately separate from the
distributable Project declaration.  This first implementation materializes the
pre-Codex session and its exact filesystem scope; execution is a later gate.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from adaos.sdk.core._ctx import require_ctx
from adaos.sdk.core.errors import SdkError
from adaos.sdk.developer import artifact_context, compositions, projects
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
_COMPONENT_REF_RE = re.compile(r"^(skill|scenario):([A-Za-z0-9][A-Za-z0-9_.-]{0,127})$")


class DevelopmentSessionError(SdkError):
    """Raised when a development session violates its exact scope contract."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _state_root() -> Path:
    ctx = require_ctx("sdk.builder.development_sessions")
    return (Path(ctx.paths.state_dir()).resolve() / "builder" / "development_sessions").resolve()


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / "abi" / "builder.development_session.v1.schema.json"


def validate(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(value)
    schema = json.loads(_schema_path().read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda item: list(item.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        raise DevelopmentSessionError(f"development session invalid at {location}: {error.message}")
    target_refs = [str(item["ref"]) for group in payload["targets"].values() for item in group]
    if len(target_refs) != len(set(target_refs)):
        raise DevelopmentSessionError("development target refs must be unique")
    if payload["focus"]["ref"] not in set(target_refs) | {str(item["ref"]) for item in payload["context_members"]}:
        raise DevelopmentSessionError("focus must reference an admitted target or context member")
    return payload


def _session_id(value: str) -> str:
    token = str(value or "").strip()
    if not _SESSION_RE.fullmatch(token):
        raise DevelopmentSessionError("session_id contains unsupported characters")
    return token


def _path(session_id: str) -> Path:
    root = _state_root()
    token = _session_id(session_id)
    path = (root / token / "session.json").resolve()
    if path.parent.parent != root:
        raise DevelopmentSessionError("session path escapes Builder state root")
    return path


def get(session_id: str) -> dict[str, Any]:
    path = _path(session_id)
    if not path.is_file():
        raise DevelopmentSessionError(f"development session {session_id!r} was not found")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, Mapping):
        raise DevelopmentSessionError("development session state must be an object")
    return {**validate(value), "state_path": str(path)}


def list_sessions(*, project_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    root = _state_root()
    if not root.is_dir():
        return []
    result: list[tuple[str, int, str, dict[str, Any]]] = []
    for path in root.glob("*/session.json"):
        value = get(path.parent.name)
        if project_id and value["project_ref"] != f"project:{project_id}":
            continue
        result.append(
            (
                str(value.get("created_at") or ""),
                int(path.stat().st_mtime_ns),
                str(value["session_id"]),
                value,
            )
        )
    result.sort(key=lambda item: item[:3])
    bounded = max(1, min(int(limit), 5000))
    return [item[3] for item in result[-bounded:]]


def bind(session_id: str, builder_webspace_id: str) -> dict[str, Any]:
    """Select a session for one Builder host without mutating Project scope."""

    session = get(session_id)
    webspace_id = str(builder_webspace_id or "").strip()
    if not webspace_id or any(char in webspace_id for char in ("/", "\\", "\0")):
        raise DevelopmentSessionError("builder_webspace_id is invalid")
    bindings_root = (_state_root() / "bindings").resolve()
    binding_path = (bindings_root / f"{webspace_id}.json").resolve()
    if binding_path.parent != bindings_root:
        raise DevelopmentSessionError("Builder binding path escapes state root")
    payload = {
        "schema": "adaos.builder.development_session_binding.v1",
        "builder_webspace_id": webspace_id,
        "session_id": session["session_id"],
        "project_ref": session["project_ref"],
        "focus_ref": session["focus"]["ref"],
        "bound_at": _now(),
    }
    atomic_write_json(binding_path, payload)
    return {"ok": True, "binding": payload, "session": session}


def binding_for(builder_webspace_id: str) -> dict[str, Any] | None:
    webspace_id = str(builder_webspace_id or "").strip()
    if not webspace_id or any(char in webspace_id for char in ("/", "\\", "\0")):
        raise DevelopmentSessionError("builder_webspace_id is invalid")
    path = (_state_root() / "bindings" / f"{webspace_id}.json").resolve()
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    return dict(value) if isinstance(value, Mapping) else None


def _within(root: Path, candidate: Path) -> bool:
    return candidate == root or root in candidate.parents


def review_changes(session_id: str, paths: Sequence[str]) -> dict[str, Any]:
    """Validate proposed changed paths against the effective write scope.

    Read-only artifact roots take precedence over the enclosing target skill,
    so a patch cannot rewrite intake material merely because the skill itself
    is an admitted source target.
    """

    session = get(session_id)
    values = [str(item or "").strip() for item in paths]
    if not values or len(values) > 5000:
        raise DevelopmentSessionError("changed paths must contain between 1 and 5000 items")
    target_roots = [
        Path(str(item["source_path"])).resolve()
        for group in session["targets"].values()
        for item in group
    ]
    scratch_root = Path(str(session["scratch"]["path"])).resolve()
    artifact_roots = [Path(str(item["root_path"])).resolve() for item in session["artifact_inputs"]]
    admitted: list[str] = []
    violations: list[dict[str, str]] = []
    for raw in values:
        candidate = Path(raw)
        if not candidate.is_absolute():
            violations.append({"path": raw, "reason": "path_must_be_absolute"})
            continue
        resolved = candidate.resolve()
        if any(_within(root, resolved) for root in artifact_roots):
            violations.append({"path": str(resolved), "reason": "read_only_artifact_input"})
        elif _within(scratch_root, resolved) or any(_within(root, resolved) for root in target_roots):
            admitted.append(str(resolved))
        else:
            violations.append({"path": str(resolved), "reason": "outside_development_session_scope"})
    return {
        "ok": not violations,
        "session_id": session["session_id"],
        "admitted": admitted,
        "violations": violations,
    }


def request_scope_expansion(
    session_id: str,
    target_ref: str,
    reason: str,
    *,
    actor: str = "codex",
) -> dict[str, Any]:
    """Record, but never auto-approve, a request for one additional target."""

    session = get(session_id)
    normalized_ref = str(target_ref or "").strip()
    match = _COMPONENT_REF_RE.fullmatch(normalized_ref)
    explanation = " ".join(str(reason or "").split()).strip()
    if not match:
        raise DevelopmentSessionError("target_ref must be a skill: or scenario: component ref")
    if not explanation:
        raise DevelopmentSessionError("scope-expansion reason is required")
    existing = {
        str(item["ref"])
        for group in session["targets"].values()
        for item in group
    }
    if normalized_ref in existing:
        raise DevelopmentSessionError(f"{normalized_ref} is already an admitted write target")
    kind, component_id = match.groups()
    source_path = str(projects.resolve_root(kind, component_id))
    identity = json.dumps(
        {"session_id": session["session_id"], "target_ref": normalized_ref, "reason": explanation},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    request_id = f"scope_{hashlib.sha256(identity).hexdigest()[:20]}"
    path = (_path(session["session_id"]).parent / "scope_requests" / f"{request_id}.json").resolve()
    payload = {
        "schema": "adaos.builder.scope_expansion_request.v1",
        "request_id": request_id,
        "session_id": session["session_id"],
        "target_ref": normalized_ref,
        "source_path": source_path,
        "reason": explanation,
        "status": "requested",
        "requested_by": str(actor or "codex"),
        "requested_at": _now(),
    }
    with mutation_lock(path.parent / ".mutation.lock"):
        if not path.is_file():
            atomic_write_json(path, payload)
        else:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return {"ok": True, "approved": False, "request": payload, "state_path": str(path)}


def create(
    project_id: str,
    *,
    automation_brief_digest: str,
    research_prototype_digest: str,
    artifact_groups: Sequence[str],
    primary_targets: Sequence[str] | None = None,
    secondary_targets: Sequence[str] = (),
    context_members: Sequence[Mapping[str, Any]] = (),
    prohibited_actions: Sequence[str],
    base_release: Mapping[str, Any] | None = None,
    focus_ref: str | None = None,
    session_id: str | None = None,
    actor: str = "user:local",
) -> dict[str, Any]:
    project = compositions.get(project_id)
    owned = {str(item["ref"]) for item in project["components"]["owned"]}
    default_primary = next(str(item["ref"]) for item in project["components"]["owned"] if item["role"] == "primary")
    primary = list(primary_targets or [default_primary])
    secondary = list(secondary_targets)
    requested = primary + secondary
    outside = sorted(set(requested) - owned)
    if outside:
        raise DevelopmentSessionError(f"development targets are not owned by project:{project_id}: {outside}")
    if not requested:
        raise DevelopmentSessionError("at least one development target is required")

    def target(ref: str) -> dict[str, Any]:
        kind, _, component_id = ref.partition(":")
        return {
            "ref": ref,
            "access": "read-write",
            "context": "full",
            "source_path": str(projects.resolve_root(kind, component_id)),
        }

    primary_skill = default_primary.partition(":")[2]
    artifact_inputs = []
    for group_id in artifact_groups:
        group = artifact_context.get_group(primary_skill, group_id)
        artifact_inputs.append(
            {
                "ref": f"artifact://skill/{primary_skill}/{group_id}",
                "access": "read-only",
                "manifest_digest": group["digest"],
                "root_path": group["root_path"],
            }
        )
    if not artifact_inputs:
        raise DevelopmentSessionError("at least one exact artifact group is required")

    token = _session_id(
        session_id
        or f"dev_{project['id']}_{str(automation_brief_digest).removeprefix('sha256:')[:16]}"
    )
    session_path = _path(token)
    scratch = (session_path.parent / "scratch").resolve()
    normalized_context = []
    for item in context_members:
        value = dict(item)
        value.setdefault("access", "read-only")
        value.setdefault("context", "contract")
        normalized_context.append(value)
    payload = {
        "schema": "adaos.builder.development_session.v1",
        "session_id": token,
        "project_ref": project["ref"],
        "base_release": dict(base_release) if isinstance(base_release, Mapping) else None,
        "focus": {"ref": str(focus_ref or primary[0])},
        "targets": {
            "primary": [target(ref) for ref in primary],
            "secondary": [target(ref) for ref in secondary],
        },
        "context_members": normalized_context,
        "artifact_inputs": artifact_inputs,
        "scratch": {"owner": "session", "access": "read-write", "path": str(scratch)},
        "handoff": {
            "automation_brief_digest": str(automation_brief_digest),
            "research_prototype_digest": str(research_prototype_digest),
            "artifact_manifest_digests": [item["manifest_digest"] for item in artifact_inputs],
            "prohibited_actions": [str(item) for item in prohibited_actions if str(item).strip()],
        },
        "status": "ready",
        "created_at": _now(),
        "created_by": str(actor or "user:local"),
    }
    validate(payload)
    with mutation_lock(session_path.parent / ".mutation.lock"):
        if session_path.is_file():
            previous = get(token)
            if (
                previous["project_ref"] == payload["project_ref"]
                and previous["handoff"] == payload["handoff"]
                and previous["targets"] == payload["targets"]
                and previous["base_release"] == payload["base_release"]
            ):
                return {"ok": True, "idempotent": True, "session": previous}
            raise DevelopmentSessionError(f"development session {token!r} already exists with another scope")
        scratch.mkdir(parents=True, exist_ok=True)
        atomic_write_json(session_path, payload)
    return {"ok": True, "idempotent": False, "session": get(token)}


__all__ = [
    "DevelopmentSessionError",
    "bind",
    "binding_for",
    "create",
    "get",
    "list_sessions",
    "request_scope_expansion",
    "review_changes",
    "validate",
]
