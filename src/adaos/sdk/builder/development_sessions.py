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
from adaos.domain.development_validation import derive_validation_budget
from adaos.services.artifact_pipeline.storage import (
    atomic_write_bytes,
    atomic_write_json,
    mutation_lock,
)


_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
_COMPONENT_REF_RE = re.compile(r"^(skill|scenario):([A-Za-z0-9][A-Za-z0-9_.-]{0,127})$")
_INSTRUCTION_KIND_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class DevelopmentSessionError(SdkError):
    """Raised when a development session violates its exact scope contract."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _state_root() -> Path:
    ctx = require_ctx("sdk.builder.development_sessions")
    return (Path(ctx.paths.state_dir()).resolve() / "builder" / "development_sessions").resolve()


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / "abi" / "builder.development_session.v1.schema.json"


def _feedback_schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / "abi" / "builder.development_feedback.v1.schema.json"


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


def validate_feedback(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(value)
    schema = json.loads(_feedback_schema_path().read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda item: list(item.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        raise DevelopmentSessionError(f"development feedback invalid at {location}: {error.message}")
    expected = "sha256:" + hashlib.sha256(
        _canonical_bytes({key: item for key, item in payload.items() if key != "digest"})
    ).hexdigest()
    if payload["digest"] != expected:
        raise DevelopmentSessionError("development feedback digest does not match its content")
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


def _instruction_kind(value: str) -> str:
    token = str(value or "").strip().lower()
    if not _INSTRUCTION_KIND_RE.fullmatch(token):
        raise DevelopmentSessionError("instruction kind contains unsupported characters")
    return token


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def attach_instruction(
    session_id: str,
    kind: str,
    value: Mapping[str, Any],
    *,
    expected_digest: str | None = None,
    media_type: str = "application/json",
) -> dict[str, Any]:
    """Attach one immutable, digest-verified instruction to a session.

    Development Sessions are generic Builder policy objects.  The instruction
    kind identifies the producer contract (for example ``automation_brief``)
    without teaching Builder about that producer's domain schema.
    """

    token = _session_id(session_id)
    instruction_kind = _instruction_kind(kind)
    payload = dict(value)
    declared_digest = str(payload.get("digest") or "").strip()
    required_digest = str(expected_digest or "").strip()
    if required_digest and declared_digest != required_digest:
        raise DevelopmentSessionError(
            "instruction declared digest does not match the Development Session handoff"
        )
    content_digest = "sha256:" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    session_path = _path(token)
    instruction_path = (session_path.parent / "instructions" / f"{instruction_kind}.json").resolve()
    if not _within(session_path.parent, instruction_path):
        raise DevelopmentSessionError("instruction path escapes Development Session root")
    ref = f"instruction://builder/{token}/{instruction_kind}"
    descriptor = {
        "ref": ref,
        "kind": instruction_kind,
        "access": "read-only",
        "media_type": str(media_type or "application/json").strip(),
        "digest_mode": "canonical-json",
        "content_digest": content_digest,
        "path": str(instruction_path),
    }
    with mutation_lock(session_path.parent / ".mutation.lock"):
        session = get(token)
        existing = next(
            (
                dict(item)
                for item in session.get("instruction_inputs") or []
                if str(item.get("kind") or "") == instruction_kind
            ),
            None,
        )
        if existing and existing != descriptor:
            raise DevelopmentSessionError(
                f"development session {token!r} already has another {instruction_kind!r} instruction"
            )
        if instruction_path.is_file():
            current = json.loads(instruction_path.read_text(encoding="utf-8-sig"))
            if not isinstance(current, Mapping) or _canonical_bytes(current) != _canonical_bytes(payload):
                raise DevelopmentSessionError("persisted instruction content does not match its descriptor")
        else:
            atomic_write_json(instruction_path, payload)
        if not existing:
            persisted = {key: item for key, item in session.items() if key != "state_path"}
            persisted["instruction_inputs"] = [
                *list(persisted.get("instruction_inputs") or []),
                descriptor,
            ]
            validate(persisted)
            atomic_write_json(session_path, persisted)
    return {
        "ok": True,
        "idempotent": existing is not None,
        "instruction": descriptor,
        "session": get(token),
    }


def attach_instruction_file(
    session_id: str,
    kind: str,
    path: str | Path,
    *,
    expected_digest: str,
    media_type: str = "text/plain",
) -> dict[str, Any]:
    """Copy one arbitrary immutable instruction file into a session.

    JSON producer contracts should normally use :func:`attach_instruction` so
    their declared object digest is checked separately.  This operation exists
    for reviewed prose, prescribed scaffolds, and other typed text inputs whose
    identity is the digest of the exact source bytes.
    """

    token = _session_id(session_id)
    instruction_kind = _instruction_kind(kind)
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise DevelopmentSessionError("instruction source file is unavailable")
    payload = source.read_bytes()
    content_digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    required_digest = str(expected_digest or "").strip().lower()
    if required_digest != content_digest:
        raise DevelopmentSessionError("instruction file digest does not match expected_digest")
    suffix = source.suffix.lower()
    if not suffix or len(suffix) > 12 or not re.fullmatch(r"[.][a-z0-9]+", suffix):
        suffix = ".bin"
    session_path = _path(token)
    instruction_path = (
        session_path.parent / "instructions" / f"{instruction_kind}{suffix}"
    ).resolve()
    if not _within(session_path.parent, instruction_path):
        raise DevelopmentSessionError("instruction path escapes Development Session root")
    descriptor = {
        "ref": f"instruction://builder/{token}/{instruction_kind}",
        "kind": instruction_kind,
        "access": "read-only",
        "media_type": str(media_type or "application/octet-stream").strip(),
        "digest_mode": "bytes",
        "content_digest": content_digest,
        "path": str(instruction_path),
    }
    with mutation_lock(session_path.parent / ".mutation.lock"):
        session = get(token)
        existing = next(
            (
                dict(item)
                for item in session.get("instruction_inputs") or []
                if str(item.get("kind") or "") == instruction_kind
            ),
            None,
        )
        if existing and existing != descriptor:
            raise DevelopmentSessionError(
                f"development session {token!r} already has another {instruction_kind!r} instruction"
            )
        if instruction_path.is_file():
            if instruction_path.read_bytes() != payload:
                raise DevelopmentSessionError("persisted instruction content does not match its descriptor")
        else:
            atomic_write_bytes(instruction_path, payload)
        if not existing:
            persisted = {key: item for key, item in session.items() if key != "state_path"}
            persisted["instruction_inputs"] = [
                *list(persisted.get("instruction_inputs") or []),
                descriptor,
            ]
            validate(persisted)
            atomic_write_json(session_path, persisted)
    return {
        "ok": True,
        "idempotent": existing is not None,
        "instruction": descriptor,
        "session": get(token),
    }


def get_instruction(session_id: str, kind: str) -> dict[str, Any]:
    """Read an instruction only after verifying its descriptor and content."""

    token = _session_id(session_id)
    instruction_kind = _instruction_kind(kind)
    session = get(token)
    descriptor = next(
        (
            dict(item)
            for item in session.get("instruction_inputs") or []
            if str(item.get("kind") or "") == instruction_kind
        ),
        None,
    )
    if not descriptor:
        raise DevelopmentSessionError(
            f"development session {token!r} has no {instruction_kind!r} instruction"
        )
    session_root = _path(token).parent
    path = Path(str(descriptor["path"])).resolve()
    if not _within(session_root, path) or not path.is_file():
        raise DevelopmentSessionError("instruction path is unavailable or escapes its session root")
    payload = path.read_bytes()
    media_type = str(descriptor.get("media_type") or "").lower()
    digest_mode = str(descriptor.get("digest_mode") or "").strip() or (
        "canonical-json" if media_type == "application/json" else "bytes"
    )
    if digest_mode == "canonical-json":
        value = json.loads(payload.decode("utf-8-sig"))
        if not isinstance(value, Mapping):
            raise DevelopmentSessionError("JSON instruction content must be an object")
        actual_digest = "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()
    else:
        value = None
        actual_digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    if actual_digest != str(descriptor["content_digest"]):
        raise DevelopmentSessionError("instruction content digest does not match its descriptor")
    result: dict[str, Any] = {"ok": True, "instruction": descriptor}
    if value is not None:
        result["value"] = dict(value)
    elif media_type.startswith("text/") or "markdown" in media_type:
        try:
            result["content"] = payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise DevelopmentSessionError("text instruction is not valid UTF-8") from exc
    return result


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


def record_feedback(
    session_id: str,
    kind: str,
    summary: str,
    *,
    severity: str = "warning",
    blocking: bool = True,
    affected_refs: Sequence[str] = (),
    constraints: Sequence[str] = (),
    evidence: Sequence[Mapping[str, Any]] = (),
    proposed_action: str = "clarify_contract",
    protocol_digest: str | None = None,
    actor: str = "codex",
) -> dict[str, Any]:
    """Persist immutable, typed Builder feedback without altering accepted science.

    Codex and other implementation agents use this channel when the accepted
    contract is ambiguous, infeasible, missing a capability, or blocked by the
    runtime.  Recording feedback cannot expand scope or mutate the protocol.
    """

    session = get(session_id)
    normalized_refs = sorted({str(item).strip() for item in affected_refs if str(item).strip()})
    admitted_refs = {
        str(item["ref"])
        for group in session["targets"].values()
        for item in group
    } | {str(item["ref"]) for item in session["context_members"]}
    outside = sorted(set(normalized_refs) - admitted_refs)
    if outside:
        raise DevelopmentSessionError(f"feedback affected_refs are outside session context: {outside}")
    normalized_summary = " ".join(str(summary or "").split()).strip()
    identity = {
        "session_id": session["session_id"],
        "kind": str(kind or "").strip(),
        "severity": str(severity or "").strip(),
        "blocking": bool(blocking),
        "summary": normalized_summary,
        "affected_refs": normalized_refs,
        "constraints": [" ".join(str(item).split()).strip() for item in constraints if str(item).strip()],
        "evidence": [dict(item) for item in evidence],
        "proposed_action": str(proposed_action or "").strip(),
        "protocol_digest": str(protocol_digest).strip() if protocol_digest else None,
        "created_by": str(actor or "codex").strip(),
    }
    fingerprint = hashlib.sha256(_canonical_bytes(identity)).hexdigest()
    feedback_id = f"feedback_{fingerprint[:20]}"
    payload = {
        "schema": "adaos.builder.development_feedback.v1",
        "feedback_id": feedback_id,
        **identity,
        "status": "open",
        "created_at": _now(),
    }
    payload["digest"] = "sha256:" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    validate_feedback(payload)
    path = (_path(session["session_id"]).parent / "feedback" / f"{feedback_id}.json").resolve()
    with mutation_lock(path.parent / ".mutation.lock"):
        if path.is_file():
            restored = json.loads(path.read_text(encoding="utf-8-sig"))
            return {"ok": True, "idempotent": True, "feedback": validate_feedback(restored), "state_path": str(path)}
        atomic_write_json(path, payload)
    return {"ok": True, "idempotent": False, "feedback": payload, "state_path": str(path)}


def list_feedback(
    session_id: str,
    *,
    kind: str | None = None,
    blocking: bool | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Read validated feedback for orchestration, review, and audit surfaces."""

    session = get(session_id)
    root = (_path(session["session_id"]).parent / "feedback").resolve()
    if not root.is_dir():
        return []
    values = []
    for path in root.glob("feedback_*.json"):
        value = validate_feedback(json.loads(path.read_text(encoding="utf-8-sig")))
        if kind and value["kind"] != kind:
            continue
        if blocking is not None and value["blocking"] is not bool(blocking):
            continue
        values.append(value)
    values.sort(key=lambda item: (str(item["created_at"]), str(item["feedback_id"])))
    return values[-max(1, min(int(limit), 5000)):]


def create(
    project_id: str,
    *,
    automation_brief_digest: str | None = None,
    research_prototype_digest: str | None = None,
    artifact_groups: Sequence[str] = (),
    artifact_audience: str | None = None,
    artifact_sources: Sequence[Mapping[str, Any]] = (),
    subject_refs: Sequence[Mapping[str, Any]] = (),
    contract_inputs: Sequence[Mapping[str, Any]] = (),
    acceptance_profiles: Sequence[str] = (),
    acceptance_requirements: Sequence[Mapping[str, Any]] = (),
    request: str | None = None,
    execution_budget: Mapping[str, Any] | None = None,
    agent_profile: Mapping[str, Any] | None = None,
    primary_targets: Sequence[str] | None = None,
    secondary_targets: Sequence[str] = (),
    context_members: Sequence[Mapping[str, Any]] = (),
    prohibited_actions: Sequence[str] = (),
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

    primary_kind, _, primary_component_id = default_primary.partition(":")
    artifact_inputs = []
    if artifact_groups and primary_kind != "skill":
        raise DevelopmentSessionError(
            "artifact_groups shorthand requires a primary skill; use artifact_sources"
        )
    sources = [
        {
            "skill_id": primary_component_id,
            "group_id": str(group_id),
            "audience": artifact_audience,
        }
        for group_id in artifact_groups
    ]
    sources.extend(dict(item) for item in artifact_sources)
    seen_artifact_refs: set[str] = set()
    for source in sources:
        source_skill = str(source.get("skill_id") or "").strip()
        group_id = str(source.get("group_id") or "").strip()
        audience = str(source.get("audience") or "").strip() or None
        if not source_skill or not group_id:
            raise DevelopmentSessionError("artifact_sources require skill_id and group_id")
        ref = f"artifact://skill/{source_skill}/{group_id}"
        if ref in seen_artifact_refs:
            raise DevelopmentSessionError(f"duplicate development artifact input: {ref}")
        seen_artifact_refs.add(ref)
        group = artifact_context.get_group(source_skill, group_id)
        descriptor = {
            "ref": ref,
            "access": "read-only",
            "manifest_digest": group["digest"],
            "root_path": group["root_path"],
        }
        if audience:
            view = artifact_context.materialize_context(source_skill, group_id, audience)
            descriptor.update(
                {
                    "root_path": view["root_path"],
                    "audience": view["audience"],
                    "context_digest": view["digest"],
                }
            )
        artifact_inputs.append(descriptor)
    normalized_subjects = [dict(item) for item in subject_refs]
    normalized_contracts = [dict(item) for item in contract_inputs]
    normalized_acceptance = [
        str(item).strip() for item in acceptance_profiles if str(item).strip()
    ]
    normalized_acceptance_requirements = [dict(item) for item in acceptance_requirements]
    requirement_ids = [str(item.get("id") or "").strip() for item in normalized_acceptance_requirements]
    if len(requirement_ids) != len(set(requirement_ids)):
        raise DevelopmentSessionError("acceptance requirement ids must be unique")
    context_refs = {str(item.get("ref") or "").strip() for item in context_members}
    outside_consumers = sorted(
        {
            str(item.get("provider_ref") or "").strip()
            for item in normalized_acceptance_requirements
            if str(item.get("provider_ref") or "").strip() not in context_refs
        }
    )
    if outside_consumers:
        raise DevelopmentSessionError(
            f"acceptance providers must be admitted context members: {outside_consumers}"
        )

    legacy_brief_digest = str(automation_brief_digest or "").strip()
    legacy_prototype_digest = str(research_prototype_digest or "").strip()
    legacy_seed = legacy_brief_digest.removeprefix("sha256:")[:16]
    generic_identity = {
        "project_ref": project["ref"],
        "targets": requested,
        "subject_refs": normalized_subjects,
        "contract_inputs": normalized_contracts,
        "acceptance_profiles": normalized_acceptance,
        "acceptance_requirements": normalized_acceptance_requirements,
        "artifact_manifest_digests": [item["manifest_digest"] for item in artifact_inputs],
    }
    generic_seed = hashlib.sha256(_canonical_bytes(generic_identity)).hexdigest()[:16]

    token = _session_id(
        session_id
        or f"dev_{project['id']}_{legacy_seed or generic_seed}"
    )
    session_path = _path(token)
    scratch = (session_path.parent / "scratch").resolve()
    normalized_context = []
    for item in context_members:
        value = dict(item)
        value.setdefault("access", "read-only")
        value.setdefault("context", "contract")
        normalized_context.append(value)
    normalized_budget = None
    if execution_budget is not None:
        normalized_budget = {
            "budget_view": str(execution_budget.get("budget_view") or "default").strip(),
            "max_wall_seconds": int(execution_budget.get("max_wall_seconds") or 0),
            "max_model_tokens": int(execution_budget.get("max_model_tokens") or 0),
            "max_attempts": int(execution_budget.get("max_attempts") or 0),
            "max_human_interventions": int(
                execution_budget.get("max_human_interventions") or 0
            ),
        }
    normalized_agent_profile = None
    if agent_profile is not None:
        normalized_agent_profile = {
            "provider": str(agent_profile.get("provider") or "").strip(),
            "model": str(agent_profile.get("model") or "").strip(),
            "reasoning_effort": str(agent_profile.get("reasoning_effort") or "").strip(),
            "tool_profile": str(agent_profile.get("tool_profile") or "").strip(),
        }
    validation_budget = derive_validation_budget(
        normalized_budget,
        source="development_session.execution_budget",
    )
    handoff = {
        **(
            {"automation_brief_digest": legacy_brief_digest}
            if legacy_brief_digest
            else {}
        ),
        **(
            {"research_prototype_digest": legacy_prototype_digest}
            if legacy_prototype_digest
            else {}
        ),
        **(
            {
                "artifact_manifest_digests": [
                    item["manifest_digest"] for item in artifact_inputs
                ]
            }
            if artifact_inputs
            else {}
        ),
        **({"request": str(request).strip()} if str(request or "").strip() else {}),
        **({"execution_budget": normalized_budget} if normalized_budget else {}),
        "validation_budget": validation_budget,
        **({"agent_profile": normalized_agent_profile} if normalized_agent_profile else {}),
        "prohibited_actions": [
            str(item).strip() for item in prohibited_actions if str(item).strip()
        ],
    }
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
        **({"subject_refs": normalized_subjects} if normalized_subjects else {}),
        **({"contract_inputs": normalized_contracts} if normalized_contracts else {}),
        **({"acceptance_profiles": normalized_acceptance} if normalized_acceptance else {}),
        **(
            {"acceptance_requirements": normalized_acceptance_requirements}
            if normalized_acceptance_requirements
            else {}
        ),
        "scratch": {"owner": "session", "access": "read-write", "path": str(scratch)},
        "handoff": handoff,
        "status": "ready",
        "created_at": _now(),
        "created_by": str(actor or "user:local"),
    }
    validate(payload)
    with mutation_lock(session_path.parent / ".mutation.lock"):
        if session_path.is_file():
            previous = get(token)
            identity_fields = (
                "project_ref",
                "base_release",
                "focus",
                "targets",
                "context_members",
                "artifact_inputs",
                "subject_refs",
                "contract_inputs",
                "acceptance_profiles",
                "acceptance_requirements",
                "handoff",
            )
            if all(previous.get(field) == payload.get(field) for field in identity_fields):
                return {"ok": True, "idempotent": True, "session": previous}
            raise DevelopmentSessionError(f"development session {token!r} already exists with another scope")
        scratch.mkdir(parents=True, exist_ok=True)
        atomic_write_json(session_path, payload)
    return {"ok": True, "idempotent": False, "session": get(token)}


__all__ = [
    "DevelopmentSessionError",
    "attach_instruction",
    "attach_instruction_file",
    "bind",
    "binding_for",
    "create",
    "get",
    "get_instruction",
    "list_sessions",
    "list_feedback",
    "record_feedback",
    "request_scope_expansion",
    "review_changes",
    "validate",
    "validate_feedback",
]
