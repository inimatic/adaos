from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from adaos.services.models.artifacts import hash_file
from adaos.services.root.client import RootHttpClient, RootHttpError


def _norm_url(value: str | None) -> str:
    return str(value or "").strip().rstrip("/")


def _load_config() -> Any:
    from adaos.services.agent_context import get_ctx
    from adaos.services.node_config import load_config

    try:
        return load_config(ctx=get_ctx())
    except Exception:
        return load_config()


def _expand_cert_path(cfg: Any, raw: Any, fallback: str) -> str:
    from adaos.services.node_config import _expand_path

    path = _expand_path(raw, fallback)
    return str(path) if path else ""


def _default_root_url(cfg: Any, *, root_url: str | None = None) -> str:
    if root_url:
        return _norm_url(root_url)
    configured = str(getattr(getattr(cfg, "root_settings", None), "base_url", None) or "").strip()
    if configured:
        return _norm_url(configured)
    return "https://api.inimatic.com"


def _root_http_client(*, root_url: str | None = None) -> tuple[RootHttpClient, Any]:
    cfg = _load_config()
    ca = _expand_cert_path(cfg, cfg.root_settings.ca_cert, "keys/ca.cert")
    cert = _expand_cert_path(cfg, cfg.subnet_settings.hub.cert, "keys/hub_cert.pem")
    key = _expand_cert_path(cfg, cfg.subnet_settings.hub.key, "keys/hub_private.pem")
    verify: str | bool = True
    if os.getenv("ADAOS_ROOT_VERIFY_CA", "0") == "1":
        verify = ca or True
    cert_tuple = (cert, key) if cert and key else None
    return (
        RootHttpClient(
            base_url=_default_root_url(cfg, root_url=root_url),
            verify=verify,
            cert=cert_tuple,
        ),
        cfg,
    )


def _infer_skill_id(skill_id: str | None) -> str:
    token = str(skill_id or "").strip()
    if token:
        return token
    for key in ("ADAOS_SKILL_NAME", "ADAOS_CURRENT_SKILL", "SKILL_NAME"):
        token = str(os.getenv(key) or "").strip()
        if token:
            return token
    try:
        from adaos.sdk.data.context import get_current_skill

        current = get_current_skill()
        token = str(getattr(current, "name", None) or getattr(current, "skill_name", None) or "").strip()
        if token:
            return token
    except Exception:
        pass
    raise ValueError("skill_id is required when no current AdaOS skill context is active")


def _safe_artifact_name(value: str | None, fallback_path: Path | None = None) -> str:
    raw = str(value or "").strip() or (fallback_path.name if fallback_path is not None else "")
    name = Path(raw).name
    if not name or name in {".", ".."}:
        raise ValueError("artifact name is required")
    return name


def _manifest_error_payload(exc: BaseException, *, label: str) -> dict[str, Any]:
    if isinstance(exc, RootHttpError) and int(getattr(exc, "status_code", 0) or 0) == 404:
        return {"ok": False, "label": label, "missing": True, "error": str(exc)}
    raise exc


def get_model_manifest(
    skill_id: str | None = None,
    *,
    label: str = "current",
    root_url: str | None = None,
) -> dict[str, Any]:
    """Return Root metadata for a skill-owned model slot."""

    resolved_skill = _infer_skill_id(skill_id)
    client, _ = _root_http_client(root_url=root_url)
    try:
        manifest = client.get_skill_model_manifest(name=resolved_skill, label=str(label or "current"))
    except RootHttpError as exc:
        return _manifest_error_payload(exc, label=str(label or "current"))
    return {"ok": True, "skill_id": resolved_skill, "label": str(label or "current"), "manifest": manifest}


def current_model_info(skill_id: str | None = None, *, root_url: str | None = None) -> dict[str, Any]:
    return get_model_manifest(skill_id, label="current", root_url=root_url)


def previous_model_info(skill_id: str | None = None, *, root_url: str | None = None) -> dict[str, Any]:
    return get_model_manifest(skill_id, label="previous", root_url=root_url)


def upload_model(
    path: str | os.PathLike[str],
    *,
    skill_id: str | None = None,
    artifact: str | None = None,
    label: str = "current",
    metadata: Mapping[str, Any] | None = None,
    skip_if_same: bool = True,
    root_url: str | None = None,
) -> dict[str, Any]:
    """Upload a model artifact to Root and rotate the target label on change."""

    resolved_skill = _infer_skill_id(skill_id)
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(str(file_path))
    artifact_name = _safe_artifact_name(artifact, file_path)
    sha256, size_bytes = hash_file(file_path)
    client, _ = _root_http_client(root_url=root_url)

    current: dict[str, Any] | None = None
    if skip_if_same:
        try:
            current = client.get_skill_model_manifest(name=resolved_skill, label=str(label or "current"))
        except RootHttpError as exc:
            if int(getattr(exc, "status_code", 0) or 0) != 404:
                raise
        if isinstance(current, Mapping):
            if str(current.get("sha256") or "") == sha256 and str(current.get("artifact") or "") == artifact_name:
                return {
                    "ok": True,
                    "skipped": True,
                    "reason": "same_hash",
                    "skill_id": resolved_skill,
                    "label": str(label or "current"),
                    "artifact": artifact_name,
                    "sha256": sha256,
                    "size_bytes": size_bytes,
                    "manifest": dict(current),
                }

    response = client.upload_skill_model_artifact(
        name=resolved_skill,
        artifact=artifact_name,
        file_path=file_path,
        sha256=sha256,
        size_bytes=size_bytes,
        label=str(label or "current"),
        metadata=dict(metadata or {}),
    )
    return {
        "ok": True,
        "skipped": False,
        "skill_id": resolved_skill,
        "label": str(label or "current"),
        "artifact": artifact_name,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "manifest": response,
    }


def update_model_if_changed(
    path: str | os.PathLike[str],
    *,
    skill_id: str | None = None,
    artifact: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    root_url: str | None = None,
) -> dict[str, Any]:
    return upload_model(
        path,
        skill_id=skill_id,
        artifact=artifact,
        metadata=metadata,
        skip_if_same=True,
        root_url=root_url,
    )


def download_model(
    dest_path: str | os.PathLike[str],
    *,
    skill_id: str | None = None,
    artifact: str | None = None,
    label: str = "current",
    root_url: str | None = None,
    verify: bool = True,
) -> dict[str, Any]:
    """Download a Root model slot without running `skill install`."""

    resolved_skill = _infer_skill_id(skill_id)
    slot = str(label or "current")
    client, _ = _root_http_client(root_url=root_url)
    manifest = client.get_skill_model_manifest(name=resolved_skill, label=slot)
    artifact_name = _safe_artifact_name(artifact or str(manifest.get("artifact") or ""))
    dest = Path(dest_path)
    if dest.exists() and dest.is_dir():
        dest = dest / artifact_name
    elif not dest.exists() and not dest.suffix:
        dest = dest / artifact_name
    meta = client.download_skill_model_artifact(
        name=resolved_skill,
        artifact=artifact_name,
        dest_path=dest,
        label=slot,
    )
    sha256, size_bytes = hash_file(dest)
    expected_sha = str(manifest.get("sha256") or meta.get("sha256") or "").strip()
    if verify and expected_sha and sha256 != expected_sha:
        try:
            dest.unlink()
        except OSError:
            pass
        raise ValueError(f"downloaded model checksum mismatch: expected {expected_sha}, got {sha256}")
    return {
        "ok": True,
        "skill_id": resolved_skill,
        "label": slot,
        "artifact": artifact_name,
        "path": str(dest),
        "sha256": sha256,
        "size_bytes": size_bytes,
        "manifest": manifest,
        "download": meta,
    }


def download_previous_model(
    dest_path: str | os.PathLike[str],
    *,
    skill_id: str | None = None,
    artifact: str | None = None,
    root_url: str | None = None,
    verify: bool = True,
) -> dict[str, Any]:
    return download_model(
        dest_path,
        skill_id=skill_id,
        artifact=artifact,
        label="previous",
        root_url=root_url,
        verify=verify,
    )
