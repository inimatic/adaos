"""Local-first source artifacts owned by a DEV skill checkout."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from adaos.sdk.core._ctx import require_ctx
from adaos.sdk.core.errors import SdkError
from adaos.sdk.developer import projects
from adaos.sdk.developer.source_preprocessing import prepare_notebook_units, query_digest, select_units
from adaos.services.artifact_pipeline.storage import atomic_write_bytes, mutation_lock


_GROUP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_ARTIFACT_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_AUDIENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MAX_BYTES = 128 * 1024 * 1024
_DETERMINISTIC_MEDIA_TYPES = {
    ".ipynb": "application/x-ipynb+json",
    ".json": "application/json",
    ".markdown": "text/markdown",
    ".md": "text/markdown",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}


class ArtifactContextError(SdkError):
    """Raised when a skill artifact-group operation is not admitted."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _digest_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _safe_group(value: str) -> str:
    token = str(value or "").strip()
    if not _GROUP_RE.fullmatch(token):
        raise ArtifactContextError("group_id contains unsupported characters")
    return token


def _safe_name(value: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    name = Path(raw).name.strip()
    if not name or name in {".", "..", "manifest.yaml"} or name != raw:
        raise ArtifactContextError("artifact name must be one plain file name")
    if any(ord(char) < 32 for char in name):
        raise ArtifactContextError("artifact name contains control characters")
    return name[:240]


def _safe_audience(value: str) -> str:
    token = str(value or "").strip()
    if not _AUDIENCE_RE.fullmatch(token):
        raise ArtifactContextError("audience contains unsupported characters")
    return token


def _context_policy(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ArtifactContextError("context_policy must be an object")
    default = str(value.get("default") or "allow").strip().lower()
    if default not in {"allow", "deny"}:
        raise ArtifactContextError("context_policy.default must be allow or deny")
    allow = sorted({_safe_audience(item) for item in value.get("allow") or []})
    deny = sorted({_safe_audience(item) for item in value.get("deny") or []})
    overlap = sorted(set(allow) & set(deny))
    if overlap:
        raise ArtifactContextError(f"context_policy allow and deny overlap: {overlap}")
    reason = " ".join(str(value.get("reason") or "").split()).strip() or None
    return {"default": default, "allow": allow, "deny": deny, "reason": reason}


def _admitted(item: Mapping[str, Any], audience: str) -> tuple[bool, str]:
    policy = item.get("context_policy")
    if not isinstance(policy, Mapping):
        return True, "legacy_default_allow"
    if audience in set(policy.get("deny") or []):
        return False, str(policy.get("reason") or "audience_denied")
    if audience in set(policy.get("allow") or []):
        return True, "audience_allowed"
    admitted = str(policy.get("default") or "allow") == "allow"
    return admitted, "default_allow" if admitted else str(policy.get("reason") or "default_deny")


def media_type_for_name(name: str, declared: str | None = None) -> str:
    """Resolve stable artifact media types independently of the host registry."""

    explicit = str(declared or "").strip().lower()
    if explicit and explicit != "application/octet-stream":
        return explicit
    suffix = Path(str(name or "")).suffix.lower()
    return str(
        _DETERMINISTIC_MEDIA_TYPES.get(suffix)
        or mimetypes.guess_type(str(name or ""))[0]
        or explicit
        or "application/octet-stream"
    )


def _root(skill_id: str, group_id: str) -> Path:
    skill_root = projects.resolve_root("skill", skill_id)
    artifacts_root = (skill_root / "artifacts").resolve()
    group_root = (artifacts_root / _safe_group(group_id)).resolve()
    if group_root.parent != artifacts_root:
        raise ArtifactContextError("artifact group escapes skill source root")
    return group_root


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / "abi" / "skill.artifact_group.v1.schema.json"


def _validate(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(value)
    schema = json.loads(_schema_path().read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda item: list(item.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        raise ArtifactContextError(f"artifact manifest invalid at {location}: {error.message}")
    paths = [str(item["path"]) for item in payload["items"]]
    ids = [str(item["artifact_id"]) for item in payload["items"]]
    if len(paths) != len(set(paths)) or len(ids) != len(set(ids)):
        raise ArtifactContextError("artifact ids and paths must be unique inside a group")
    expected = _digest_bytes(_canonical({key: value for key, value in payload.items() if key != "digest"}))
    if payload["digest"] != expected:
        raise ArtifactContextError("artifact manifest digest does not match its content")
    return payload


def _empty(skill_id: str, group_id: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "adaos.skill.artifact_group.v1",
        "schema_version": "1.0.0",
        "skill_ref": f"skill:{skill_id}",
        "group_id": group_id,
        "generation": 0,
        "items": [],
        "updated_at": _now(),
    }
    payload["digest"] = _digest_bytes(_canonical(payload))
    return payload


def _read(skill_id: str, group_id: str, *, required: bool = False) -> dict[str, Any]:
    token = _safe_group(group_id)
    path = _root(skill_id, token) / "manifest.yaml"
    if not path.is_file():
        if required:
            raise ArtifactContextError(f"artifact group {token} does not exist for skill:{skill_id}")
        return _empty(skill_id, token)
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ArtifactContextError(f"failed to read artifact manifest: {exc}") from exc
    if not isinstance(value, Mapping):
        raise ArtifactContextError("artifact manifest must be an object")
    return _validate(value)


def _write(path: Path, value: Mapping[str, Any]) -> None:
    payload = _validate(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".manifest.yaml.tmp")
    temporary.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")
    temporary.replace(path)


def groups(skill_id: str) -> list[dict[str, Any]]:
    artifacts_root = projects.resolve_root("skill", skill_id) / "artifacts"
    if not artifacts_root.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(artifacts_root.glob("*/manifest.yaml"), key=lambda item: item.parent.name.lower()):
        result.append(_read(skill_id, path.parent.name, required=True))
    return result


def get_group(skill_id: str, group_id: str) -> dict[str, Any]:
    value = _read(skill_id, group_id, required=True)
    root = _root(skill_id, group_id)
    return {
        **value,
        "ref": f"artifact://skill/{skill_id}/{group_id}",
        "root_path": str(root),
        "manifest_path": str((root / "manifest.yaml").resolve()),
    }


def add_path(
    skill_id: str,
    group_id: str,
    source_path: str | Path,
    *,
    name: str | None = None,
    role: str = "source",
    media_type: str | None = None,
    origin: Mapping[str, Any] | None = None,
    trust: str = "untrusted",
    sensitivity: str = "unknown",
    license_id: str | None = None,
    publication: str = "private",
    context_policy: Mapping[str, Any] | None = None,
    replace_existing: bool = False,
) -> dict[str, Any]:
    # Delay Builder package initialization so worker-first imports remain acyclic.
    from adaos.services.builder.sources import _source_analysis

    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise ArtifactContextError("source_path must reference an existing regular file")
    payload = source.read_bytes()
    if not payload:
        raise ArtifactContextError("artifact must not be empty")
    if len(payload) > _MAX_BYTES:
        raise ArtifactContextError(f"artifact exceeds maximum size of {_MAX_BYTES} bytes")
    token = _safe_group(group_id)
    safe_name = _safe_name(name or source.name)
    digest = _digest_bytes(payload)
    artifact_id = f"artifact-{digest.removeprefix('sha256:')[:20]}"
    if not _ARTIFACT_RE.fullmatch(artifact_id):
        raise ArtifactContextError("generated artifact id is invalid")
    media = media_type_for_name(safe_name, media_type)
    normalized_policy = _context_policy(context_policy)
    item = {
        "artifact_id": artifact_id,
        "path": safe_name,
        "media_type": media,
        "role": str(role or "source").strip() or "source",
        "digest": digest,
        "size_bytes": len(payload),
        "origin": dict(origin or {"kind": "local_file", "source_name": source.name}),
        "trust": str(trust),
        "sensitivity": str(sensitivity),
        "license": str(license_id).strip() if license_id else None,
        "publication": str(publication),
        "analysis": _source_analysis(safe_name, media, payload),
    }
    if normalized_policy is not None:
        item["context_policy"] = normalized_policy
    root = _root(skill_id, token)
    lock_path = root / ".mutation.lock"
    with mutation_lock(lock_path):
        manifest = _read(skill_id, token)
        existing_digest = next((entry for entry in manifest["items"] if entry["digest"] == digest), None)
        if existing_digest and (
            normalized_policy is None or existing_digest.get("context_policy") == normalized_policy
        ):
            return {"ok": True, "idempotent": True, "artifact": dict(existing_digest), "group": get_group(skill_id, token)}
        existing_path = next((entry for entry in manifest["items"] if entry["path"] == safe_name), None)
        if existing_digest:
            existing_path = existing_digest
        if existing_path and not replace_existing:
            raise ArtifactContextError(f"artifact path {safe_name!r} already exists with different content")
        root.mkdir(parents=True, exist_ok=True)
        previous_payload = (root / safe_name).read_bytes() if existing_path and (root / safe_name).is_file() else None
        atomic_write_bytes(root / safe_name, payload)
        updated = {
            **manifest,
            "schema_version": "1.1.0" if normalized_policy is not None else manifest["schema_version"],
            "generation": int(manifest["generation"]) + 1,
            "items": [
                item if existing_path and entry["path"] == safe_name else entry
                for entry in manifest["items"]
            ] if existing_path else [*manifest["items"], item],
            "updated_at": _now(),
        }
        updated.pop("digest", None)
        updated["digest"] = _digest_bytes(_canonical(updated))
        try:
            _write(root / "manifest.yaml", updated)
        except Exception:
            copied = root / safe_name
            if previous_payload is not None:
                atomic_write_bytes(copied, previous_payload)
            elif copied.is_file() and _digest_bytes(copied.read_bytes()) == digest:
                copied.unlink()
            raise
    return {
        "ok": True,
        "idempotent": False,
        "replaced": bool(existing_path),
        "previous_artifact": dict(existing_path) if existing_path else None,
        "artifact": item,
        "group": get_group(skill_id, token),
    }


def set_context_policy(
    skill_id: str,
    group_id: str,
    artifact_id: str,
    policy: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Set a generic consumer policy without assigning domain meaning to audiences."""

    token = _safe_group(group_id)
    normalized = _context_policy(policy)
    root = _root(skill_id, token)
    with mutation_lock(root / ".mutation.lock"):
        manifest = _read(skill_id, token, required=True)
        previous = next(
            (dict(item) for item in manifest["items"] if item["artifact_id"] == artifact_id),
            None,
        )
        if not previous:
            raise ArtifactContextError(f"artifact {artifact_id!r} was not found")
        current = previous.get("context_policy")
        if current == normalized or (normalized is None and current is None):
            return {"ok": True, "idempotent": True, "artifact": previous, "group": get_group(skill_id, token)}
        replacement = dict(previous)
        if normalized is None:
            replacement.pop("context_policy", None)
        else:
            replacement["context_policy"] = normalized
        updated = {
            **manifest,
            "schema_version": "1.1.0",
            "generation": int(manifest["generation"]) + 1,
            "items": [
                replacement if item["artifact_id"] == artifact_id else item
                for item in manifest["items"]
            ],
            "updated_at": _now(),
        }
        updated.pop("digest", None)
        updated["digest"] = _digest_bytes(_canonical(updated))
        _write(root / "manifest.yaml", updated)
    return {"ok": True, "idempotent": False, "artifact": replacement, "group": get_group(skill_id, token)}


def _context_view_root() -> Path:
    ctx = require_ctx("sdk.developer.artifact_context")
    return (Path(ctx.paths.state_dir()).resolve() / "artifact_context" / "views").resolve()


def _context_view_schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / "abi" / "artifact.context_view.v1.schema.json"


def _validate_context_view(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(value)
    schema = json.loads(_context_view_schema_path().read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda item: list(item.absolute_path))
    if errors:
        error = errors[0]
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        raise ArtifactContextError(f"context view invalid at {location}: {error.message}")
    expected = _digest_bytes(_canonical({key: item for key, item in payload.items() if key != "digest"}))
    if payload["digest"] != expected:
        raise ArtifactContextError("context view digest does not match its content")
    return payload


def materialize_context(skill_id: str, group_id: str, audience: str) -> dict[str, Any]:
    """Create an immutable filesystem view containing only audience-admitted files.

    Filtering a prompt is not an isolation boundary when an agent can read the
    source tree.  A materialized view makes the declared policy match the files
    that are physically visible to that consumer and binds the result by digest.
    """

    audience_token = _safe_audience(audience)
    group = get_group(skill_id, group_id)
    source_root = Path(str(group["root_path"])).resolve()
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    contents: dict[str, bytes] = {}
    for item in sorted(group["items"], key=lambda entry: str(entry["path"]).casefold()):
        allowed, reason = _admitted(item, audience_token)
        if not allowed:
            excluded.append({"artifact_id": str(item["artifact_id"]), "reason": reason})
            continue
        path = (source_root / str(item["path"])).resolve()
        if path.parent != source_root or not path.is_file():
            raise ArtifactContextError("artifact file is missing or outside its group")
        content = path.read_bytes()
        if _digest_bytes(content) != item["digest"]:
            raise ArtifactContextError("artifact file digest no longer matches manifest")
        contents[str(item["path"])] = content
        included.append(
            {
                "artifact_id": str(item["artifact_id"]),
                "path": str(item["path"]),
                "digest": str(item["digest"]),
                "size_bytes": int(item["size_bytes"]),
            }
        )
    identity: dict[str, Any] = {
        "schema": "adaos.artifact.context_view.v1",
        "audience": audience_token,
        "source_ref": str(group["ref"]),
        "source_manifest_digest": str(group["digest"]),
        "items": included,
        "excluded": excluded,
    }
    identity["digest"] = _digest_bytes(_canonical(identity))
    view = _validate_context_view(identity)
    view_token = str(view["digest"]).removeprefix("sha256:")
    root = (_context_view_root() / view_token).resolve()
    if root.parent != _context_view_root():
        raise ArtifactContextError("context view path escapes state root")
    files_root = (root / "files").resolve()
    manifest_path = (root / "context-view.json").resolve()
    with mutation_lock(root / ".mutation.lock"):
        files_root.mkdir(parents=True, exist_ok=True)
        expected_names = set(contents)
        unexpected = [path.name for path in files_root.iterdir() if path.name not in expected_names]
        if unexpected:
            raise ArtifactContextError(f"context view contains unexpected files: {sorted(unexpected)}")
        for name, content in contents.items():
            destination = (files_root / name).resolve()
            if destination.parent != files_root:
                raise ArtifactContextError("context view artifact path escapes files root")
            if not destination.is_file() or _digest_bytes(destination.read_bytes()) != _digest_bytes(content):
                atomic_write_bytes(destination, content)
        if manifest_path.is_file():
            persisted = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            if not isinstance(persisted, Mapping) or _validate_context_view(persisted) != view:
                raise ArtifactContextError("persisted context view does not match its immutable identity")
        else:
            atomic_write_bytes(manifest_path, json.dumps(view, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))
    return {
        **view,
        "root_path": str(files_root),
        "manifest_path": str(manifest_path),
    }


def resolve(skill_id: str, group_id: str, artifact_id: str) -> dict[str, Any]:
    group = get_group(skill_id, group_id)
    item = next((entry for entry in group["items"] if entry["artifact_id"] == artifact_id), None)
    if not item:
        raise ArtifactContextError(f"artifact {artifact_id!r} was not found")
    path = (_root(skill_id, group_id) / str(item["path"])).resolve()
    if path.parent != _root(skill_id, group_id) or not path.is_file():
        raise ArtifactContextError("artifact file is missing or outside its group")
    actual = _digest_bytes(path.read_bytes())
    if actual != item["digest"]:
        raise ArtifactContextError("artifact file digest no longer matches manifest")
    return {**dict(item), "ref": f"artifact://skill/{skill_id}/{group_id}/{artifact_id}", "native_path": str(path)}


def read_text(skill_id: str, group_id: str, artifact_id: str, *, max_characters: int = 120_000) -> str:
    resolved = resolve(skill_id, group_id, artifact_id)
    maximum = max(1, min(int(max_characters), 1_000_000))
    raw = Path(resolved["native_path"]).read_bytes()
    try:
        return raw.decode("utf-8-sig")[:maximum]
    except UnicodeDecodeError as exc:
        raise ArtifactContextError("artifact is not UTF-8 text") from exc


def _text_units(text: str, artifact_ref: str, *, chunk_characters: int = 4_000) -> list[dict[str, Any]]:
    lines = text.splitlines(keepends=True)
    units: list[dict[str, Any]] = []
    start = 1
    body: list[str] = []
    size = 0
    for line_number, line in enumerate(lines, start=1):
        if body and size + len(line) > chunk_characters:
            units.append(
                {
                    "id": f"lines-{start}-{line_number - 1}",
                    "ref": f"{artifact_ref}#lines={start}-{line_number - 1}",
                    "content": "".join(body),
                }
            )
            body = []
            size = 0
            start = line_number
        body.append(line)
        size += len(line)
    if body or not units:
        end = max(start, len(lines))
        units.append(
            {
                "id": f"lines-{start}-{end}",
                "ref": f"{artifact_ref}#lines={start}-{end}",
                "content": "".join(body),
            }
        )
    return units


def extract_text(
    skill_id: str,
    group_id: str,
    artifact_id: str,
    *,
    max_characters: int = 40_000,
    query: str = "",
) -> dict[str, Any]:
    """Extract bounded, provenance-addressable LLM context from a text artifact.

    Notebook structure is parsed and compacted before query-aware selection.
    Bounded historical output summaries may be included, explicitly labelled
    as exploratory and untrusted. Plain text uses the same relevance selector
    over stable line chunks. The coverage envelope discloses exactly what the
    model did and did not receive.
    """

    resolved = resolve(skill_id, group_id, artifact_id)
    maximum = max(1, min(int(max_characters), 1_000_000))
    raw = Path(resolved["native_path"]).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ArtifactContextError("artifact is not UTF-8 text") from exc
    artifact_ref = str(resolved["ref"])
    notebook_meta: dict[str, Any] = {}
    if str(resolved.get("media_type") or "") == "application/x-ipynb+json" or str(resolved.get("path") or "").lower().endswith(".ipynb"):
        try:
            units, notebook_meta = prepare_notebook_units(text, artifact_ref, query=query)
        except ValueError as exc:
            raise ArtifactContextError(str(exc)) from exc
        strategy = "notebook_semantic_digest_v1"
    else:
        units = _text_units(text, artifact_ref)
        query_tokens = {item.casefold() for item in re.findall(r"[^\W_]{2,}", str(query or ""), re.UNICODE)}
        for index, unit in enumerate(units):
            content_tokens = {item.casefold() for item in re.findall(r"[^\W_]{2,}", str(unit.get("content") or ""), re.UNICODE)}
            unit["relevance"] = len(query_tokens & content_tokens) * 10 + (2 if index == 0 else 0)
            unit["order"] = index
            unit["kind"] = "text"
        strategy = "utf8_line_chunks"

    selected, selection_meta = select_units(
        units,
        max_characters=maximum,
        relevance_first=bool(str(query or "").strip()),
    )
    extracted_characters = sum(int(item.get("selected_characters") or 0) for item in selected)

    content = "\n\n".join(
        f"--- {item['label']} [{item['ref']}] ---\n{item['content'].rstrip()}" for item in selected
    )
    selected_units = len(selected)
    total_units = len(units)
    return {
        "artifact_ref": artifact_ref,
        "name": resolved.get("path"),
        "digest": resolved.get("digest"),
        "media_type": resolved.get("media_type"),
        "content": content,
        "provenance": [
            {
                key: item[key]
                for key in ("id", "ref", "selected_characters", "source_characters", "truncated")
            }
            for item in selected
        ],
        "coverage": {
            "strategy": strategy,
            "raw_bytes": len(raw),
            "source_characters": sum(len(str(item.get("content") or "")) for item in units),
            "selected_characters": extracted_characters,
            "total_units": total_units,
            "selected_units": selected_units,
            "omitted_units": max(0, total_units - selected_units),
            "truncated": selected_units < total_units or any(bool(item["truncated"]) for item in selected),
            "query_digest": query_digest(query),
            **selection_meta,
            **notebook_meta,
        },
    }


def source_bundle(skill_id: str, *, audience: str | None = None) -> dict[str, Any]:
    """Project manifested sources, optionally restricted to an exact audience."""

    selected_groups = groups(skill_id)
    sources = []
    excluded = []
    audience_token = _safe_audience(audience) if audience else None
    for group in selected_groups:
        for item in group["items"]:
            if audience_token:
                admitted, reason = _admitted(item, audience_token)
                if not admitted:
                    excluded.append(
                        {
                            "artifact_id": item["artifact_id"],
                            "group_id": group["group_id"],
                            "reason": reason,
                        }
                    )
                    continue
            sources.append(
                {
                    "source_id": item["artifact_id"],
                    "name": item["path"],
                    "digest": item["digest"],
                    "media_type": item["media_type"],
                    "role": item["role"],
                    "analysis": item["analysis"],
                    "origin": item["origin"],
                    "artifact_ref": f"artifact://skill/{skill_id}/{group['group_id']}/{item['artifact_id']}",
                    "group_id": group["group_id"],
                    "context_policy": item.get("context_policy"),
                }
            )
    if audience_token:
        group_identities = []
        for group in selected_groups:
            admitted_items = [
                {
                    "artifact_id": item["artifact_id"],
                    "digest": item["digest"],
                    "context_policy": item.get("context_policy"),
                }
                for item in group["items"]
                if _admitted(item, audience_token)[0]
            ]
            filtered_identity = {
                "group_id": group["group_id"],
                "audience": audience_token,
                "items": admitted_items,
            }
            group_identities.append(
                {
                    "group_id": group["group_id"],
                    "digest": _digest_bytes(_canonical(filtered_identity)),
                }
            )
    else:
        group_identities = [
            {
                "group_id": group["group_id"],
                "digest": group["digest"],
                "generation": group["generation"],
            }
            for group in selected_groups
        ]
    identity = {
        "schema": "adaos.research.artifact_set.v1",
        "skill_ref": f"skill:{skill_id}",
        "groups": group_identities,
        "sources": sources,
        "audience": audience_token,
    }
    return {
        **identity,
        "generation": sum(int(group["generation"]) for group in selected_groups),
        "source_manifests": [
            {
                "group_id": group["group_id"],
                "digest": group["digest"],
                "generation": group["generation"],
            }
            for group in selected_groups
        ],
        "excluded": excluded,
        "digest": _digest_bytes(_canonical(identity)),
    }


__all__ = [
    "ArtifactContextError",
    "add_path",
    "extract_text",
    "get_group",
    "groups",
    "materialize_context",
    "read_text",
    "resolve",
    "set_context_policy",
    "source_bundle",
]
