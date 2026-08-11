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

from adaos.sdk.core.errors import SdkError
from adaos.sdk.developer import projects
from adaos.services.artifact_pipeline.storage import atomic_write_bytes, mutation_lock
from adaos.services.builder.sources import _source_analysis


_GROUP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_ARTIFACT_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_MAX_BYTES = 128 * 1024 * 1024


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
) -> dict[str, Any]:
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
    media = str(media_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream")
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
    root = _root(skill_id, token)
    lock_path = root / ".mutation.lock"
    with mutation_lock(lock_path):
        manifest = _read(skill_id, token)
        existing_digest = next((entry for entry in manifest["items"] if entry["digest"] == digest), None)
        if existing_digest:
            return {"ok": True, "idempotent": True, "artifact": dict(existing_digest), "group": get_group(skill_id, token)}
        if any(entry["path"] == safe_name for entry in manifest["items"]):
            raise ArtifactContextError(f"artifact path {safe_name!r} already exists with different content")
        root.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(root / safe_name, payload)
        updated = {
            **manifest,
            "generation": int(manifest["generation"]) + 1,
            "items": [*manifest["items"], item],
            "updated_at": _now(),
        }
        updated.pop("digest", None)
        updated["digest"] = _digest_bytes(_canonical(updated))
        try:
            _write(root / "manifest.yaml", updated)
        except Exception:
            copied = root / safe_name
            if copied.is_file() and _digest_bytes(copied.read_bytes()) == digest:
                copied.unlink()
            raise
    return {"ok": True, "idempotent": False, "artifact": item, "group": get_group(skill_id, token)}


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


def source_bundle(skill_id: str) -> dict[str, Any]:
    """Compatibility projection for formulation code; files remain skill-owned."""

    selected_groups = groups(skill_id)
    sources = []
    for group in selected_groups:
        for item in group["items"]:
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
                }
            )
    identity = {
        "schema": "adaos.research.artifact_set.v1",
        "skill_ref": f"skill:{skill_id}",
        "groups": [{"group_id": group["group_id"], "digest": group["digest"], "generation": group["generation"]} for group in selected_groups],
        "sources": sources,
    }
    return {
        **identity,
        "generation": sum(int(group["generation"]) for group in selected_groups),
        "digest": _digest_bytes(_canonical(identity)),
    }


__all__ = ["ArtifactContextError", "add_path", "get_group", "groups", "read_text", "resolve", "source_bundle"]
