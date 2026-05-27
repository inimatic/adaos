from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


_DEFAULT_MODELS_DIR = Path("data/files/models")


@dataclass(frozen=True, slots=True)
class ModelArtifact:
    key: str
    source_path: Path | None
    install_path: Path
    artifact_name: str
    capability: str | None = None
    dependency_profile: str | None = None
    uri: str | None = None
    expected_sha256: str | None = None
    expected_size_bytes: int | None = None
    private: bool = False


@dataclass(frozen=True, slots=True)
class LocalArtifactState:
    artifact: ModelArtifact
    path: Path
    sha256: str
    size_bytes: int


def declared_model_artifacts(manifest: Mapping[str, Any], *, skill_dir: Path) -> list[ModelArtifact]:
    models = manifest.get("models")
    if not isinstance(models, Mapping):
        return []
    models_private = _optional_bool(models.get("private"))
    artifacts = models.get("artifacts")
    if not isinstance(artifacts, Mapping):
        return []
    result: list[ModelArtifact] = []
    for key, raw in artifacts.items():
        if not isinstance(raw, Mapping):
            continue
        source_token = str(raw.get("path") or raw.get("source_path") or "").strip()
        uri_token = str(raw.get("uri") or "").strip()
        install_token = str(raw.get("install_path") or "").strip()
        artifact_name = str(raw.get("artifact") or raw.get("name") or "").strip()
        source_path = _resolve_under(skill_dir, source_token) if source_token else None
        if not artifact_name:
            if source_token:
                artifact_name = Path(source_token).name
            elif uri_token:
                artifact_name = Path(uri_token.rstrip("/")).name
            else:
                artifact_name = str(key)
        install_path = _normalize_install_path(install_token, artifact_name)
        result.append(
            ModelArtifact(
                key=str(key),
                source_path=source_path,
                install_path=install_path,
                artifact_name=_safe_artifact_name(artifact_name),
                capability=_optional_string(raw.get("capability")),
                dependency_profile=_optional_string(raw.get("dependency_profile")),
                uri=uri_token or None,
                expected_sha256=_optional_string(raw.get("sha256")),
                expected_size_bytes=_optional_int(raw.get("size_bytes")),
                private=_optional_bool(raw.get("private"), default=models_private),
            )
        )
    return result


def local_artifact_state(artifact: ModelArtifact) -> LocalArtifactState | None:
    path = artifact.source_path
    if path is None or not path.is_file():
        return None
    sha256, size_bytes = hash_file(path)
    return LocalArtifactState(artifact=artifact, path=path, sha256=sha256, size_bytes=size_bytes)


def hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def install_local_artifact(
    state: LocalArtifactState,
    *,
    data_root: Path,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    dest = _resolve_install_destination(data_root, state.artifact.install_path)
    _copy_verified(state.path, dest, expected_sha256=state.sha256, expected_size=state.size_bytes)
    payload = _manifest_entry(
        state.artifact,
        path=dest,
        sha256=state.sha256,
        size_bytes=state.size_bytes,
        source="local",
        provenance=provenance,
    )
    _write_models_manifest(data_root, [payload])
    return payload


def install_downloaded_artifact(
    artifact: ModelArtifact,
    *,
    data_root: Path,
    downloaded_path: Path,
    expected_sha256: str,
    expected_size_bytes: int | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    sha256, size_bytes = hash_file(downloaded_path)
    if sha256 != expected_sha256:
        raise ValueError(f"downloaded model checksum mismatch for {artifact.key}: expected {expected_sha256}, got {sha256}")
    if expected_size_bytes is not None and size_bytes != expected_size_bytes:
        raise ValueError(f"downloaded model size mismatch for {artifact.key}: expected {expected_size_bytes}, got {size_bytes}")
    dest = _resolve_install_destination(data_root, artifact.install_path)
    _copy_verified(downloaded_path, dest, expected_sha256=sha256, expected_size=size_bytes)
    payload = _manifest_entry(
        artifact,
        path=dest,
        sha256=sha256,
        size_bytes=size_bytes,
        source="root",
        provenance=provenance,
    )
    _write_models_manifest(data_root, [payload])
    return payload


def _write_models_manifest(data_root: Path, entries: list[dict[str, Any]]) -> None:
    manifest_path = data_root / "files" / "models" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                existing = parsed
        except Exception:
            existing = {}
    by_key = {str(item.get("key") or ""): dict(item) for item in existing.get("artifacts", []) if isinstance(item, Mapping)}
    for entry in entries:
        by_key[str(entry.get("key") or "")] = dict(entry)
    payload = {
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "artifacts": [by_key[key] for key in sorted(by_key) if key],
    }
    tmp = manifest_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, manifest_path)


def _manifest_entry(
    artifact: ModelArtifact,
    *,
    path: Path,
    sha256: str,
    size_bytes: int,
    source: str,
    provenance: Mapping[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "key": artifact.key,
        "artifact": artifact.artifact_name,
        "path": str(path),
        "install_path": str(artifact.install_path).replace("\\", "/"),
        "sha256": sha256,
        "size_bytes": size_bytes,
        "source": source,
        "installed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    if artifact.capability:
        payload["capability"] = artifact.capability
    if artifact.dependency_profile:
        payload["dependency_profile"] = artifact.dependency_profile
    if provenance:
        payload["provenance"] = dict(provenance)
    return payload


def _copy_verified(source: Path, dest: Path, *, expected_sha256: str, expected_size: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        shutil.copy2(source, tmp)
        actual_sha256, actual_size = hash_file(tmp)
        if actual_sha256 != expected_sha256 or actual_size != expected_size:
            raise ValueError(f"model copy verification failed for {dest}")
        os.replace(tmp, dest)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def _normalize_install_path(token: str, artifact_name: str) -> Path:
    raw = Path(token) if token else _DEFAULT_MODELS_DIR / artifact_name
    parts = raw.parts
    if raw.is_absolute() or ".." in parts:
        raise ValueError(f"invalid model install_path: {raw}")
    if len(parts) >= 3 and parts[0] == "data" and parts[1] == "files" and parts[2] == "models":
        return Path(*parts)
    if len(parts) >= 2 and parts[0] == "files" and parts[1] == "models":
        return Path("data") / raw
    if len(parts) >= 1 and parts[0] == "models":
        return Path("data/files") / raw
    return _DEFAULT_MODELS_DIR / raw.name


def _resolve_install_destination(data_root: Path, install_path: Path) -> Path:
    parts = install_path.parts
    relative = Path(*parts[1:]) if parts and parts[0] == "data" else install_path
    dest = (data_root / relative).resolve()
    root = data_root.resolve()
    if dest != root and root not in dest.parents:
        raise ValueError(f"model install_path escapes skill data root: {install_path}")
    return dest


def _resolve_under(root: Path, token: str) -> Path:
    raw = Path(token)
    path = raw if raw.is_absolute() else root / raw
    resolved = path.resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError(f"model source path escapes skill directory: {token}")
    return resolved


def _safe_artifact_name(value: str) -> str:
    name = Path(value).name.strip()
    if not name or name in {".", ".."}:
        raise ValueError("invalid model artifact name")
    return name


def _optional_string(value: Any) -> str | None:
    token = str(value or "").strip()
    return token or None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed >= 0 else None


def _optional_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default
