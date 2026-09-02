# src\adaos\services\root\service.py
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import shutil
import ssl
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping
from uuid import uuid4
from fastapi import APIRouter, Depends
import yaml
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.eventbus import emit

from adaos.services.crypto.pki import generate_rsa_key, make_csr, write_pem, write_private_key
from adaos.services.node_config import (
    NodeConfig,
    RootOwnerProfile,
    displayable_path,
    ensure_hub,
    load_node,
    save_node,
)
from adaos.services.skill.scaffold import create as scaffold_skill_create
from adaos.services.scenario.scaffold import create as scaffold_scenario_create

from .client import RootHttpClient, RootHttpError
from .keyring import KeyringUnavailableError, delete_refresh, load_refresh, save_refresh
from adaos.adapters.db import sqlite as sqlite_db
from adaos.apps.api.auth import require_owner_token
from adaos.services.id_gen import new_id
from adaos.services.root_mcp.targets import upsert_managed_target
from adaos.services.zone_hosts import DEFAULT_PUBLIC_ROOT_BASE_URL, canonical_zone_id, zone_public_base_url
from adaos.adapters.scenarios.git_repo import GitScenarioRepository
from adaos.services.scenario.manager import ScenarioManager
from adaos.services.skill.manager import SkillManager
from adaos.services.skill.validation import SkillValidationService
from adaos.services.scenario.validation import validate_scenario_path
from adaos.adapters.db import SqliteScenarioRegistry
from adaos.adapters.db import SqliteSkillRegistry
from adaos.services.workspace_registry import upsert_workspace_registry_entry
from adaos.services.skill.version_policy import RESERVED_DATA_MIGRATION_FILE, bump_index, effective_skill_bump
from adaos.services.runtime_refresh import refresh_skill_runtime
from adaos.services.workspace_release_guard import assert_workspace_component_mutable
from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.services.artifact_pipeline import (
    ArtifactPublicationService,
    PublicationStaleError,
    RemoteReleaseRepository,
    build_artifact_package,
    compose_artifact_trust_runtime,
)
from adaos.services.artifact_pipeline.storage import atomic_write_bytes, atomic_write_json

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from adaos.services.semver import bump_version


logger = logging.getLogger(__name__)
_log = logger


class RootAuthError(RuntimeError):
    pass


def _get_scenario_manager(ctx: AgentContext = Depends(get_ctx)) -> ScenarioManager:
    repo = ctx.scenarios_repo
    reg = SqliteScenarioRegistry(ctx.sql)
    return ScenarioManager(repo=repo, registry=reg, git=ctx.git, paths=ctx.paths, bus=ctx.bus, caps=ctx.caps)


def _get_skill_manager(ctx: AgentContext = Depends(get_ctx)) -> SkillManager:
    # используем skills_repo, а не scenarios_repo
    repo = ctx.skills_repo
    registry = SqliteSkillRegistry(ctx.sql)
    return SkillManager(
        repo=repo,
        registry=registry,
        git=ctx.git,
        paths=ctx.paths,
        bus=getattr(ctx, "bus", None),
        caps=ctx.caps,
        settings=ctx.settings,
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _current_timestamp() -> str:
    return _now().replace(microsecond=0).isoformat()


def _normalize_draft_commit_message(value: str | None) -> str | None:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return None
    return text[:240]


_DRAFT_METADATA_TRAILERS = {
    "AdaOS-Change-Id": "change_id",
    "AdaOS-Conversation-Id": "conversation_id",
    "AdaOS-Topic-Id": "topic_id",
    "AdaOS-Thread-Id": "thread_id",
    "AdaOS-Revision": "revision",
    "AdaOS-Model": "model",
    "AdaOS-Request-Id": "request_id",
    "AdaOS-Result-Message-Id": "result_message_id",
    "AdaOS-Source-Messages": "source_message_ids",
}


def _normalize_draft_metadata(value: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(value or {})
    allowed = set(_DRAFT_METADATA_TRAILERS.values())
    out: dict[str, Any] = {}
    for key in allowed:
        raw = source.get(key)
        if key == "source_message_ids":
            values = raw if isinstance(raw, (list, tuple)) else str(raw or "").split(",")
            normalized = [" ".join(str(item or "").split())[:160] for item in values]
            normalized = [item for item in normalized if item][:32]
            if normalized:
                out[key] = normalized
            continue
        text = " ".join(str(raw or "").split()).strip()[:512]
        if text:
            out[key] = text
    return out


def _parse_draft_commit_metadata(message: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for line in str(message or "").splitlines():
        key, separator, value = line.partition(":")
        field = _DRAFT_METADATA_TRAILERS.get(key.strip()) if separator else None
        if not field:
            continue
        text = value.strip()
        if field == "source_message_ids":
            result[field] = [item.strip() for item in text.split(",") if item.strip()]
        elif text:
            result[field] = text
    return _normalize_draft_metadata(result)


def _definitive_remote_rejection_status(intent: Mapping[str, Any]) -> int | None:
    """Recover known 4xx rejections, including journals written by older cores."""

    raw_status = intent.get("remote_status")
    status = raw_status if isinstance(raw_status, int) else None
    if status is None:
        error = str(intent.get("error") or "")
        if error.startswith("RootHttpError:"):
            status = next(
                (code for code in range(400, 500) if f"{code} " in error),
                None,
            )
    if status is None or status == 408:
        return None
    return status


def _parse_timestamp(value: str | None) -> datetime:
    if not value:
        return _EPOCH
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _EPOCH


def _parse_expiry(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:  # pragma: no cover - defensive fallback
        raise RootAuthError(f"invalid expiry timestamp: {value}") from exc


def _format_expiry(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def _extract_zone_id_from_url(url: str | None) -> str | None:
    text = str(url or "").strip()
    if not text or "?" not in text:
        return None
    try:
        query = text.split("?", 1)[1]
    except Exception:
        return None
    for item in query.split("&"):
        if not item.startswith("zone="):
            continue
        zone_id = item.split("=", 1)[1].strip().lower()
        return canonical_zone_id(zone_id)
    return None


def _root_state(cfg: NodeConfig) -> dict:
    if cfg.root is None:
        cfg.root = {}
    return cfg.root


@dataclass
class RootAuthService:
    http: RootHttpClient
    clock_skew: timedelta = timedelta(seconds=30)

    @staticmethod
    def resolve_owner(token: str) -> str:
        require_owner_token(token)
        owner_id = os.getenv("ADAOS_ROOT_OWNER_ID") or "local-owner"
        return owner_id

    def login_owner(
        self,
        cfg: NodeConfig,
        *,
        on_start: Callable[[dict], None] | None = None,
    ) -> RootOwnerProfile:
        ensure_hub(cfg)
        owner_id = cfg.subnet_id
        start = self.http.owner_start(owner_id)
        if on_start:
            on_start(start)
        device_code = start.get("device_code")
        interval = max(int(start.get("interval", 5)), 1)
        expires_in = int(start.get("expires_in", 600))
        if not isinstance(device_code, str) or not device_code:
            raise RootAuthError("root did not return device_code")
        deadline = _now() + timedelta(seconds=expires_in)
        while _now() < deadline:
            try:
                result = self.http.owner_poll(device_code)
            except RootHttpError as exc:
                if exc.error_code == "authorization_pending":
                    time.sleep(interval)
                    continue
                if exc.error_code == "slow_down":
                    interval += 5
                    time.sleep(interval)
                    continue
                if exc.error_code in {"expired_token", "expired_device_code"}:
                    raise RootAuthError("device code expired before authorization completed") from exc
                raise
            break
        else:  # pragma: no cover - timing dependent
            raise RootAuthError("device authorization expired")

        if not isinstance(result, dict):
            raise RootAuthError("unexpected response from root")
        access_token = result.get("access_token")
        refresh_token = result.get("refresh_token")
        expires_at_str = result.get("expires_at")
        subject = result.get("subject")
        scopes = result.get("scopes") or []
        owner_returned = result.get("owner_id")
        hub_ids = result.get("hub_ids") or []
        if owner_returned and owner_returned != owner_id:
            raise RootAuthError("root returned mismatched owner_id")
        if not isinstance(access_token, str) or not isinstance(refresh_token, str):
            raise RootAuthError("root response is missing tokens")
        if not isinstance(expires_at_str, str):
            raise RootAuthError("root response is missing expires_at")

        if not isinstance(scopes, Iterable):
            scopes = []
        scopes_list = [str(scope) for scope in scopes]
        if not isinstance(hub_ids, Iterable):
            hub_ids = []
        hub_list = [str(h) for h in hub_ids]

        expiry = _parse_expiry(expires_at_str)

        profile: RootOwnerProfile = {
            "owner_id": owner_id,
            "subject": subject if isinstance(subject, str) else None,
            "scopes": scopes_list,
            "access_expires_at": _format_expiry(expiry),
            "hub_ids": hub_list,
        }

        state = _root_state(cfg)
        state["profile"] = profile
        state["access_token_cached"] = access_token
        try:
            save_refresh(owner_id, refresh_token)
            state.pop("refresh_token_fallback", None)
        except KeyringUnavailableError:
            state["refresh_token_fallback"] = refresh_token
        save_node(cfg)
        return profile

    def _refresh_token(self, cfg: NodeConfig) -> tuple[str, datetime]:
        state = _root_state(cfg)
        profile = state.get("profile")
        if not profile:
            raise RootAuthError("root owner profile is not configured; run 'adaos dev root login'")
        owner_id = profile["owner_id"]
        refresh_token: str | None = None
        try:
            refresh_token = load_refresh(owner_id)
        except KeyringUnavailableError:
            refresh_token = state.get("refresh_token_fallback")
        if not refresh_token:
            raise RootAuthError("refresh token is missing; login required")
        result = self.http.token_refresh(refresh_token)
        access_token = result.get("access_token")
        expires_at = result.get("expires_at")
        if not isinstance(access_token, str) or not isinstance(expires_at, str):
            raise RootAuthError("invalid refresh response from root")
        expiry = _parse_expiry(expires_at)
        state["access_token_cached"] = access_token
        profile["access_expires_at"] = _format_expiry(expiry)
        save_node(cfg)
        return access_token, expiry

    def get_access_token(self, cfg: NodeConfig) -> str:
        ensure_hub(cfg)
        state = _root_state(cfg)
        profile = state.get("profile")
        if not profile:
            raise RootAuthError("root owner profile is not configured; run 'adaos dev root login'")
        cached = state.get("access_token_cached")
        if isinstance(cached, str) and cached:
            expiry = _parse_expiry(profile["access_expires_at"])
            if expiry - self.clock_skew > _now():
                return cached
        token, expiry = self._refresh_token(cfg)
        profile["access_expires_at"] = _format_expiry(expiry)
        save_node(cfg)
        return token

    def whoami(self, cfg: NodeConfig) -> dict:
        token = self.get_access_token(cfg)
        return self.http.whoami(token)

    def logout(self, cfg: NodeConfig) -> None:
        state = _root_state(cfg)
        profile = state.get("profile")
        if profile:
            owner_id = profile["owner_id"]
            try:
                delete_refresh(owner_id)
            except KeyringUnavailableError:
                pass
        cfg.root = None
        save_node(cfg)

    @staticmethod
    def register_subnet(owner_token: str, csr_pem: str, fingerprint: str, hints: Any | None = None) -> dict:
        owner_id = RootAuthService.resolve_owner(owner_token)
        ca_state = sqlite_db.ca_load()
        ca_key = serialization.load_pem_private_key(ca_state["ca_key_pem"].encode("utf-8"), password=None)
        ca_cert = x509.load_pem_x509_certificate(ca_state["ca_cert_pem"].encode("utf-8"))

        subnet = sqlite_db.subnet_get_or_create(owner_id)
        existing_device = sqlite_db.device_get_by_fingerprint(subnet["subnet_id"], fingerprint)
        now = datetime.now(timezone.utc)

        if existing_device:
            cert_pem = existing_device["cert_pem"]
            issued_at = existing_device["issued_at"]
            expires_at = existing_device["expires_at"]
            device_id = existing_device["device_id"]
        else:
            try:
                csr = x509.load_pem_x509_csr(csr_pem.encode("utf-8"))
            except ValueError as exc:  # pragma: no cover - invalid CSR handling
                raise RootAuthError("invalid CSR") from exc
            if not csr.is_signature_valid:
                raise RootAuthError("CSR signature invalid")

            serial = int(ca_state["next_serial"])
            not_before = now - timedelta(minutes=1)
            not_after = now + timedelta(days=365)
            builder = (
                x509.CertificateBuilder()
                .subject_name(csr.subject)
                .issuer_name(ca_cert.subject)
                .public_key(csr.public_key())
                .serial_number(serial)
                .not_valid_before(not_before)
                .not_valid_after(not_after)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            )
            for extension in csr.extensions:
                builder = builder.add_extension(extension.value, extension.critical)
            certificate = builder.sign(private_key=ca_key, algorithm=hashes.SHA256())
            cert_pem = certificate.public_bytes(serialization.Encoding.PEM).decode("utf-8")
            issued_at = int(now.timestamp())
            expires_at = int(not_after.timestamp())
            device = sqlite_db.device_upsert_hub(subnet["subnet_id"], fingerprint, cert_pem, issued_at, expires_at)
            device_id = device["device_id"]
            sqlite_db.ca_update_serial(serial + 1)
        envelope_time = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        event_id = new_id()
        data = {
            "subnet_id": subnet["subnet_id"],
            "hub_device_id": device_id,
            "cert_pem": cert_pem,
        }
        try:
            upsert_managed_target(
                {
                    "target_id": f"hub:{subnet['subnet_id']}",
                    "title": f"Hub {subnet['subnet_id']}",
                    "kind": "hub",
                    "environment": str(((hints or {}) if isinstance(hints, dict) else {}).get("environment") or "test").strip().lower() or "test",
                    "status": "registered",
                    "zone": str(((hints or {}) if isinstance(hints, dict) else {}).get("zone") or "").strip() or None,
                    "subnet_id": subnet["subnet_id"],
                    "transport": {"channel": "hub_root_protocol", "mode": "registered"},
                    "operational_surface": {
                        "published_by": "skill:infra_access_skill",
                        "enabled": False,
                        "availability": "planned",
                    },
                    "meta": {
                        "registry_source": "subnet_registration",
                        "hub_device_id": device_id,
                    },
                }
            )
        except Exception:
            pass
        return {"data": data, "event_id": event_id, "server_time_utc": envelope_time}


@dataclass
class OwnerHubsService:
    http: RootHttpClient
    auth: RootAuthService

    def sync(self, cfg: NodeConfig) -> list[str]:
        ensure_hub(cfg)
        token = self.auth.get_access_token(cfg)
        hubs = self.http.owner_hubs_list(token)
        hub_ids = [str(item.get("hub_id")) for item in hubs if isinstance(item, dict) and item.get("hub_id")]
        state = _root_state(cfg)
        profile = state.get("profile")
        if profile:
            profile["hub_ids"] = hub_ids
            save_node(cfg)
        return hub_ids

    def add_current_hub(self, cfg: NodeConfig, hub_id: str | None = None) -> None:
        ensure_hub(cfg)
        token = self.auth.get_access_token(cfg)
        effective_hub_id = hub_id or cfg.node_id
        if not effective_hub_id:
            raise RootAuthError("hub identifier is not configured")
        self.http.owner_hubs_add(token, effective_hub_id)
        state = _root_state(cfg)
        profile = state.get("profile")
        if profile:
            hubs = set(profile.get("hub_ids", []))
            hubs.add(effective_hub_id)
            profile["hub_ids"] = sorted(hubs)
            save_node(cfg)


@dataclass
class PkiService:
    http: RootHttpClient
    auth: RootAuthService

    def enroll(self, cfg: NodeConfig, hub_id: str, csr_pem: str, ttl: str | None = None) -> dict:
        ensure_hub(cfg)
        token = self.auth.get_access_token(cfg)
        return self.http.pki_enroll(token, hub_id, csr_pem, ttl)


# ---------------------------------------------------------------------------
# Developer workflow helpers
# ---------------------------------------------------------------------------


class RootServiceError(RuntimeError):
    """Raised when developer workflow operations fail."""


class TemplateResolutionError(RootServiceError):
    """Raised when a requested scaffold template cannot be resolved."""

    exit_code: int = 2

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        if exit_code is not None:
            self.exit_code = exit_code


class ArtifactNotFoundError(RootServiceError):
    """Raised when a requested artifact is missing from the dev workspace."""

    exit_code: int = 3

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        if exit_code is not None:
            self.exit_code = exit_code


@dataclass(slots=True)
class DeviceAuthorization:
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None
    zone_id: str | None
    interval: int
    expires_in: int


@dataclass(slots=True)
class RootInitResult:
    subnet_id: str
    reused: bool
    hub_key_path: Path
    hub_cert_path: Path
    ca_cert_path: Path | None
    workspace_path: Path


@dataclass(slots=True)
class RootLoginResult:
    owner_id: str
    workspace_path: Path
    subnet_id: str | None = None


@dataclass(slots=True)
class ArtifactCreateResult:
    kind: str
    name: str
    owner_id: str
    path: Path
    version: str | None = None
    updated_at: str | None = None


@dataclass(slots=True)
class ArtifactPushResult:
    kind: str
    name: str
    stored_path: str
    sha256: str
    bytes_uploaded: int
    version: str | None = None
    updated_at: str | None = None
    commit: str | None = None
    message: str | None = None
    metadata: dict[str, Any] | None = None
    package_digest: str | None = None
    source_revision: str | None = None
    source_tree: str | None = None


@dataclass(slots=True)
class ArtifactDeleteResult:
    kind: str
    name: str
    owner_id: str
    path: Path
    version: str | None = None
    updated_at: str | None = None


@dataclass(slots=True)
class ArtifactUpdateResult:
    kind: str
    name: str
    source_path: Path
    target_path: Path
    version: str | None = None
    updated_at: str | None = None
    commit: str | None = None
    message: str | None = None
    metadata: dict[str, Any] | None = None
    recovery: dict[str, Any] | None = None


@dataclass(slots=True)
class ArtifactListItem:
    name: str
    path: Path
    version: str | None
    updated_at: str | None


@dataclass(slots=True)
class ArtifactPublishResult:
    kind: str
    name: str
    source_path: Path
    target_path: Path
    version: str
    previous_version: str | None
    updated_at: str
    dry_run: bool = False
    warnings: tuple[str, ...] = ()


_SKIP_DIRS = {
    ".git",
    # Project-owned source artifacts are immutable Builder/LLM inputs, not
    # component source. They are bound separately by artifact-group digest
    # and may be private or non-redistributable.
    "artifacts",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".venv",
    "node_modules",
    "dist",
    "build",
    ".idea",
}
_SKIP_FILES = {".DS_Store"}


def assert_safe_name(name: str) -> None:
    if not name or not name.strip():
        raise RootServiceError("Name must not be empty")


def _should_skip(path: Path) -> bool:
    for part in path.parts[:-1]:
        if part in _SKIP_DIRS:
            return True
    if path.name in _SKIP_FILES:
        return True
    if path.suffix in {".pyc", ".pyo"}:
        return True
    return False


def create_zip_bytes(root: Path) -> bytes:
    if not root.exists():
        raise RootServiceError(f"Path not found: {root}")
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise RootServiceError(f"Expected directory at {root}")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if _should_skip(relative):
                continue
            if path.is_dir():
                continue
            zf.write(path, arcname=str(relative))
    return buffer.getvalue()


def archive_bytes_to_b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _replace_directory_transactionally(staged: Path, target: Path) -> None:
    """Activate a staged artifact without deleting the current copy first."""

    staged = staged.expanduser().resolve()
    target = target.expanduser().resolve()
    if staged.parent != target.parent:
        raise RootServiceError("Staged artifact must be on the same filesystem as its target")

    backup = target.parent / f".{target.name}.backup-{os.getpid()}-{uuid4().hex}"
    target_moved = False
    activated = False
    try:
        if target.exists():
            try:
                target.replace(backup)
                target_moved = True
            except PermissionError as exc:
                # Windows refuses to rename a directory while an IDE, shell,
                # or read-only observer retains a handle below it. Individual
                # files are normally still replaceable. Keep publication
                # transactional by synchronizing staged files atomically with
                # a complete rollback copy instead of asking callers to copy
                # a live artifact by hand.
                _log.warning(
                    "artifact directory swap is locked; using file-atomic activation target=%s: %s",
                    target,
                    exc,
                )
                _replace_directory_contents_transactionally(staged, target)
                activated = True
                return
        try:
            staged.replace(target)
            activated = True
        except PermissionError as activation_error:
            # A directory handle can also prevent Windows from installing the
            # staged directory after the old copy was moved successfully. Put
            # the old copy back before using the same file-atomic fallback;
            # otherwise the fallback would have no rollback baseline.
            if target_moved:
                try:
                    _restore_directory_backup(backup, target)
                    target_moved = False
                except Exception as rollback_error:
                    raise RootServiceError(
                        "Artifact activation failed and rollback could not restore the previous copy; "
                        f"backup retained at {backup}: {type(rollback_error).__name__}: {rollback_error}"
                    ) from activation_error
            _log.warning(
                "staged artifact directory activation is locked; using file-atomic activation target=%s: %s",
                target,
                activation_error,
            )
            _replace_directory_contents_transactionally(staged, target)
            activated = True
            return
        except Exception as activation_error:
            if target_moved:
                try:
                    _restore_directory_backup(backup, target)
                    target_moved = False
                except Exception as rollback_error:
                    raise RootServiceError(
                        "Artifact activation failed and rollback could not restore the previous copy; "
                        f"backup retained at {backup}: {type(rollback_error).__name__}: {rollback_error}"
                    ) from activation_error
            raise

        if target_moved:
            try:
                shutil.rmtree(backup)
                target_moved = False
            except OSError as cleanup_error:
                _log.warning(
                    "Artifact update succeeded but previous-copy cleanup failed; backup retained at %s: %s",
                    backup,
                    cleanup_error,
                )
    finally:
        if not activated and staged.exists():
            shutil.rmtree(staged, ignore_errors=True)


def _restore_directory_backup(
    backup: Path,
    target: Path,
    *,
    attempts: int = 5,
    retry_delay_s: float = 0.05,
) -> None:
    """Restore a just-renamed directory across transient Windows handle races."""

    last_error: PermissionError | None = None
    for attempt in range(max(1, int(attempts))):
        try:
            backup.replace(target)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt + 1 >= max(1, int(attempts)):
                break
            time.sleep(max(0.0, float(retry_delay_s)) * (attempt + 1))
    if last_error is not None:
        raise last_error


def _replace_directory_contents_transactionally(staged: Path, target: Path) -> None:
    """Activate staged contents when a live directory cannot be renamed.

    Every regular file is installed with ``os.replace``. A sibling rollback
    tree is created before the first write and retained if rollback itself
    cannot complete. Runtime/cache directories excluded from artifact packages
    are left untouched.
    """

    staged = staged.expanduser().resolve()
    target = target.expanduser().resolve()
    if staged.parent != target.parent:
        raise RootServiceError("Staged artifact must be on the same filesystem as its target")
    if not target.exists() or not target.is_dir():
        raise RootServiceError("File-atomic activation requires an existing target directory")

    rollback = target.parent / f".{target.name}.rollback-{os.getpid()}-{uuid4().hex}"
    try:
        shutil.copytree(
            target,
            rollback,
            ignore=shutil.ignore_patterns(*_SKIP_DIRS, *_SKIP_FILES, "*.pyc", "*.pyo"),
        )
        try:
            _synchronize_directory_files(staged, target)
        except Exception as activation_error:
            try:
                _synchronize_directory_files(rollback, target)
            except Exception as rollback_error:
                raise RootServiceError(
                    "Artifact activation failed and rollback could not restore the previous files; "
                    f"backup retained at {rollback}: {type(rollback_error).__name__}: {rollback_error}"
                ) from activation_error
            raise
    finally:
        shutil.rmtree(staged, ignore_errors=True)
        if rollback.exists():
            try:
                shutil.rmtree(rollback)
            except OSError as cleanup_error:
                _log.warning(
                    "Artifact file-atomic activation finished but rollback cleanup failed; "
                    "backup retained at %s: %s",
                    rollback,
                    cleanup_error,
                )


def _synchronize_directory_files(source: Path, target: Path) -> None:
    """Synchronize one artifact tree using atomic replacement per file."""

    source = source.expanduser().resolve()
    target = target.expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    source_files = {
        path.relative_to(source)
        for path in source.rglob("*")
        if path.is_file() and not _should_skip(path.relative_to(source))
    }

    for relative in sorted(source_files, key=lambda item: item.as_posix()):
        src = source / relative
        dst = target / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        temporary = dst.with_name(f".{dst.name}.publish-{os.getpid()}-{uuid4().hex}")
        try:
            shutil.copy2(src, temporary)
            os.replace(temporary, dst)
        finally:
            temporary.unlink(missing_ok=True)

    existing = sorted(
        target.rglob("*"),
        key=lambda item: (len(item.relative_to(target).parts), item.as_posix()),
        reverse=True,
    )
    for path in existing:
        relative = path.relative_to(target)
        if any(part in _SKIP_DIRS for part in relative.parts):
            continue
        if path.is_file() and relative not in source_files:
            path.unlink()
        elif path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass


def _extract_zip_bytes(data: bytes, target: Path) -> None:
    target = target.expanduser().resolve()
    temporary = target.parent / f".{target.name}.update-{os.getpid()}-{uuid4().hex}"
    temporary.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for entry in archive.infolist():
                relative = Path(entry.filename.replace("\\", "/"))
                if relative.is_absolute() or ".." in relative.parts:
                    raise RootServiceError(f"Archive entry escapes artifact root: {entry.filename}")
                destination = (temporary / relative).resolve()
                if temporary not in destination.parents and destination != temporary:
                    raise RootServiceError(f"Archive entry escapes artifact root: {entry.filename}")
            archive.extractall(temporary)
        _replace_directory_transactionally(temporary, target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def fingerprint_for_key(key: rsa.RSAPrivateKey) -> str:
    public_bytes = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return "sha256:" + hashlib.sha256(public_bytes).hexdigest()


def _config_path_value(path: Path) -> str:
    rendered = displayable_path(path)
    return rendered if rendered is not None else str(path)


def _templates_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "templates"


def _template_path(name: str) -> Path:
    candidate = _templates_dir() / name
    if not candidate.exists():
        raise RootServiceError(f"Template '{name}' not found at {candidate}")
    return candidate


def _copy_template(src: Path, dst: Path) -> None:
    if dst.exists():
        raise RootServiceError(f"Target already exists: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)


def _rewrite_skill_template_identity(root: Path, name: str) -> None:
    label = name.replace("_", " ").replace("-", " ").title()
    text_suffixes = {".py", ".yaml", ".yml", ".json", ".md", ".intent", ".txt"}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in text_suffixes:
            continue
        text = path.read_text(encoding="utf-8")
        rewritten = text.replace("new_skill", name).replace("New Skill", label)
        if rewritten != text:
            path.write_text(rewritten, encoding="utf-8")


def _rewrite_scenario_template_identity(root: Path, name: str) -> None:
    """Replace identity placeholders in a freshly copied scenario template."""

    label = name.replace("_", " ").replace("-", " ").title()
    text_suffixes = {".yaml", ".yml", ".json", ".md", ".txt"}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in text_suffixes:
            continue
        text = path.read_text(encoding="utf-8")
        rewritten = text.replace("template-id", name).replace("New Scenario", label)
        if rewritten != text:
            path.write_text(rewritten, encoding="utf-8")


def _sync_scenario_content_metadata(
    root: Path,
    name: str,
    manifest_meta: Mapping[str, Any] | None,
) -> None:
    """Materialize the derived runtime descriptor from canonical YAML and WebUI sources."""

    content_path = root / "scenario.json"
    if not content_path.is_file():
        return
    payload = _load_manifest(content_path)
    yaml_path = root / "scenario.yaml"
    canonical = _load_manifest(yaml_path) if yaml_path.is_file() else {}
    canonical.update(
        {
            key: value
            for key, value in dict(manifest_meta or {}).items()
            if key in {"version", "updated_at"} and value is not None
        }
    )
    for key, value in canonical.items():
        payload[key] = value
    payload["id"] = name
    payload["name"] = name

    webui_path = root / "webui.json"
    if webui_path.is_file():
        webui = _load_manifest(webui_path)
        ui = webui.get("ui")
        if not isinstance(ui, Mapping):
            raise RootServiceError(f"Scenario WebUI at {webui_path} must contain an object ui")
        projected_ui = dict(ui)
        canonical_version = canonical.get("version")
        if canonical_version is not None:
            projected_ui["version"] = canonical_version
        canonical_ui = canonical.get("ui")
        if isinstance(canonical_ui, Mapping) and canonical_ui.get("manifest") is not None:
            projected_ui["manifest"] = canonical_ui["manifest"]
        if projected_ui != ui:
            webui["ui"] = projected_ui
            _write_manifest(webui_path, webui)
        payload["ui"] = projected_ui
    _write_manifest(content_path, payload)


def _ensure_keep_file(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    keep = directory / ".keep"
    if not keep.exists():
        keep.write_text("", encoding="utf-8")


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RootServiceError(f"Failed to read manifest at {manifest_path}: {exc}") from exc

    if manifest_path.suffix == ".json":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RootServiceError(f"Invalid JSON manifest at {manifest_path}: {exc}") from exc
    else:
        try:
            data = yaml.safe_load(raw) or {}
        except yaml.YAMLError as exc:
            raise RootServiceError(f"Invalid YAML manifest at {manifest_path}: {exc}") from exc

    if not isinstance(data, dict):
        return {}
    return data


def _write_manifest(manifest_path: Path, data: Mapping[str, Any]) -> None:
    try:
        if manifest_path.suffix == ".json":
            manifest_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=4) + "\n",
                encoding="utf-8",
            )
        else:
            manifest_path.write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise RootServiceError(f"Failed to write manifest at {manifest_path}: {exc}") from exc


def _insecure_tls_enabled() -> bool:
    value = os.getenv("ADAOS_INSECURE_TLS", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


class RootDeveloperService:
    """High-level orchestration for Root developer workflows."""

    def __init__(
        self,
        *,
        config_loader: Callable[[], NodeConfig] | None = None,
        config_saver: Callable[[NodeConfig], None] | None = None,
        client_factory: Callable[[NodeConfig], RootHttpClient] | None = None,
        ctx: AgentContext | None = None,
    ) -> None:
        self._load_config = config_loader or load_node
        self._save_config = config_saver or save_node
        self._client_factory = client_factory
        self.ctx: AgentContext = ctx or get_ctx()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def init(
        self,
        *,
        root_token: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        preferred_subnet_id: str | None = None,
    ) -> RootInitResult:
        cfg = self._load_config()
        node_id = cfg.node_settings.id or cfg.node_id
        emit(self.ctx.bus, "root.dev.init.start", {"node_id": node_id}, "root.dev")

        try:
            token = root_token or os.getenv("ROOT_TOKEN") or "dev-root-token"
            if not token:
                raise RootServiceError("ROOT_TOKEN is not configured; set ROOT_TOKEN or pass --token")

            preferred_subnet = str(preferred_subnet_id or "").strip() or None
            existing_subnet = preferred_subnet or cfg.subnet_settings.id or cfg.subnet_id
            key_path = cfg.hub_key_path()
            cert_path = cfg.hub_cert_path()
            ca_path = cfg.ca_cert_path()
            force_new_keypair = bool(preferred_subnet and key_path.exists())

            if existing_subnet and key_path.exists() and cert_path.exists() and ca_path.exists():
                try:
                    cert_pem = cert_path.read_text(encoding="utf-8")
                except OSError:
                    cert_pem = ""
                if cert_pem and self._hub_certificate_is_acceptable(cert_pem, subnet_id=existing_subnet, owner_id=None):
                    workspace = self._prepare_workspace(cfg, owner="pending_owner")
                    cfg.subnet_settings.id = existing_subnet
                    cfg.subnet_id = existing_subnet
                    cfg.subnet_settings.hub.key = _config_path_value(key_path)
                    cfg.subnet_settings.hub.cert = _config_path_value(cert_path)
                    cfg.root_settings.ca_cert = _config_path_value(ca_path)
                    self._save_config(cfg)
                    emit(
                        self.ctx.bus,
                        "root.dev.init.done",
                        {
                            "node_id": node_id,
                            "subnet_id": existing_subnet,
                            "reused": True,
                            "workspace": displayable_path(workspace) or str(workspace),
                        },
                        "root.dev",
                    )
                    return RootInitResult(
                        subnet_id=existing_subnet,
                        reused=True,
                        hub_key_path=key_path,
                        hub_cert_path=cert_path,
                        ca_cert_path=ca_path,
                        workspace_path=workspace,
                    )
            key_path, private_key = self._ensure_hub_keypair(cfg, force_new=force_new_keypair)

            verify = self._plain_verify(cfg)
            client = self._client(cfg)

            previous_subnet = cfg.subnet_id
            registration = self._register_hub(
                client,
                token,
                verify=verify,
                private_key=private_key,
                metadata=metadata,
                subnet_id=preferred_subnet or cfg.subnet_id,
            )
            reused_flag = bool(registration.get("reused"))
            forge_info = registration.get("forge") if isinstance(registration, Mapping) else None
            forge_repo = forge_info.get("repo") if isinstance(forge_info, Mapping) else None
            forge_path = forge_info.get("path") if isinstance(forge_info, Mapping) else None
            if isinstance(forge_repo, str) and forge_repo:
                cfg.dev_settings.forge_repo = forge_repo
            if isinstance(forge_path, str) and forge_path:
                cfg.dev_settings.forge_path = forge_path

            subnet_id = registration.get("subnet_id")
            cert_pem = registration.get("cert_pem")
            ca_pem = registration.get("ca_pem")
            if not isinstance(subnet_id, str) or not subnet_id:
                raise RootServiceError("Root response missing subnet_id")
            if preferred_subnet and subnet_id != preferred_subnet:
                raise RootServiceError(
                    f"Root returned subnet_id {subnet_id} instead of requested {preferred_subnet}; "
                    "the requested subnet may not be claimable with the current Root registration flow"
                )
            if not isinstance(cert_pem, str) or not cert_pem.strip():
                raise RootServiceError("Root response missing hub certificate")

            cert_path = cfg.hub_cert_path()
            write_pem(cert_path, cert_pem)
            owner_id = cfg.root_settings.owner.owner_id if cfg.root_settings and cfg.root_settings.owner else None
            if not self._hub_certificate_is_acceptable(cert_pem, subnet_id=subnet_id, owner_id=owner_id):
                logger.warning(
                    "Root issued hub certificate without subnet binding; rotating hub credentials",
                )
                key_path, private_key = self._ensure_hub_keypair(cfg, force_new=True)
                rotation = self._register_hub(
                    client,
                    token,
                    verify=verify,
                    private_key=private_key,
                    metadata=metadata,
                    subnet_id=subnet_id,
                )
                rotated_cert = rotation.get("cert_pem")
                if not isinstance(rotated_cert, str) or not rotated_cert.strip():
                    raise RootServiceError("Root response missing hub certificate after rotation")
                write_pem(cert_path, rotated_cert)
                cert_pem = rotated_cert
                ca_candidate = rotation.get("ca_pem")
                if isinstance(ca_candidate, str) and ca_candidate.strip():
                    ca_pem = ca_candidate
                reused_flag = reused_flag or bool(rotation.get("reused"))
                if not self._hub_certificate_is_acceptable(cert_pem, subnet_id=subnet_id, owner_id=None):
                    raise RootServiceError("Root issued hub certificate without subnet binding; contact support")

            ca_path: Path | None = None
            if isinstance(ca_pem, str) and ca_pem.strip():
                ca_path = cfg.ca_cert_path()
                write_pem(ca_path, ca_pem)
                cfg.root_settings.ca_cert = _config_path_value(ca_path)

            cfg.subnet_settings.id = subnet_id
            cfg.subnet_id = subnet_id
            cfg.subnet_settings.hub.key = _config_path_value(key_path)
            cfg.subnet_settings.hub.cert = _config_path_value(cert_path)

            workspace = self._prepare_workspace(cfg, owner="pending_owner")

            self._save_config(cfg)

            reused = reused_flag or previous_subnet == subnet_id
            emit(
                self.ctx.bus,
                "root.dev.init.done",
                {
                    "node_id": node_id,
                    "subnet_id": subnet_id,
                    "reused": reused,
                    "workspace": displayable_path(workspace) or str(workspace),
                },
                "root.dev",
            )
            return RootInitResult(
                subnet_id=subnet_id,
                reused=reused,
                hub_key_path=key_path,
                hub_cert_path=cert_path,
                ca_cert_path=ca_path,
                workspace_path=workspace,
            )
        except RootHttpError as exc:
            if exc.status_code == 0 and "handshake operation timed out" in str(exc).lower():
                zone_id = canonical_zone_id((os.getenv("ADAOS_ZONE_ID") or cfg.zone_id or "").strip().lower())
                current_base = self._client(cfg).base_url
                zone_hint = f" Try ADAOS_ZONE_ID={zone_id}." if zone_id else " Try setting ADAOS_ZONE_ID=ru if this machine should use the RU Root."
                raise RootServiceError(f"TLS handshake with Root timed out at {current_base}.{zone_hint}") from exc
            emit(self.ctx.bus, "root.dev.init.error", {"node_id": node_id}, "root.dev")
            raise
        except Exception:
            emit(self.ctx.bus, "root.dev.init.error", {"node_id": node_id}, "root.dev")
            raise

    def dev_logs(
        self,
        *,
        minutes: int = 30,
        limit: int = 2000,
        hub_id: str | None = None,
        contains: str | None = None,
        root_token: str | None = None,
    ) -> dict[str, Any]:
        cfg = self._load_config()
        token = root_token or os.getenv("ROOT_TOKEN") or "dev-root-token"
        if not token:
            raise RootServiceError("ROOT_TOKEN is not configured; set ROOT_TOKEN or pass --token")
        client = self._client(cfg)
        verify = self._plain_verify(cfg)
        payload = client.dev_logs(
            root_token=token,
            minutes=minutes,
            limit=limit,
            hub_id=hub_id,
            contains=contains,
            verify=verify,
        )
        return payload if isinstance(payload, dict) else {"ok": False, "payload": payload}

    def dev_log_files(
        self,
        *,
        contains: str | None = None,
        limit: int = 500,
        root_token: str | None = None,
    ) -> dict[str, Any]:
        cfg = self._load_config()
        token = root_token or os.getenv("ROOT_TOKEN") or "dev-root-token"
        if not token:
            raise RootServiceError("ROOT_TOKEN is not configured; set ROOT_TOKEN or pass --token")
        client = self._client(cfg)
        verify = self._plain_verify(cfg)
        payload = client.dev_log_files(
            root_token=token,
            contains=contains,
            limit=limit,
            verify=verify,
        )
        return payload if isinstance(payload, dict) else {"ok": False, "payload": payload}

    def dev_log_tail(
        self,
        *,
        file: str,
        lines: int = 200,
        max_bytes: int = 2_000_000,
        root_token: str | None = None,
    ) -> dict[str, Any]:
        cfg = self._load_config()
        token = root_token or os.getenv("ROOT_TOKEN") or "dev-root-token"
        if not token:
            raise RootServiceError("ROOT_TOKEN is not configured; set ROOT_TOKEN or pass --token")
        client = self._client(cfg)
        verify = self._plain_verify(cfg)
        payload = client.dev_log_tail(
            root_token=token,
            file=file,
            lines=lines,
            max_bytes=max_bytes,
            verify=verify,
        )
        return payload if isinstance(payload, dict) else {"ok": False, "payload": payload}

    def _start_device_authorization(
        self,
        cfg: NodeConfig,
        *,
        client: RootHttpClient,
        verify_plain: ssl.SSLContext | bool,
        owner_id_hint: str,
    ) -> tuple[DeviceAuthorization, ssl.SSLContext | bool, tuple[str, str] | None]:
        authorize_cert: tuple[str, str] | None = None
        authorize_verify: ssl.SSLContext | bool = verify_plain
        requested_zone_id = canonical_zone_id(
            (os.getenv("ADAOS_ZONE_ID") or cfg.zone_id or "").strip().lower()
        )
        authorize_payload: dict[str, Any] = {"owner_id": owner_id_hint}
        if requested_zone_id:
            authorize_payload["zone_id"] = requested_zone_id

        mtls_material = self._mtls_material_optional(cfg, verify_plain)
        if mtls_material:
            cert_path, key_path, mtls_verify = mtls_material
            authorize_cert = (cert_path, key_path)
            authorize_verify = mtls_verify
            try:
                start = client.device_authorize(
                    verify=authorize_verify,
                    cert=authorize_cert,
                    payload=authorize_payload,
                )
            except RootHttpError as exc:
                if self._is_certificate_error(exc):
                    raise RootServiceError(
                        "Root rejected the hub client certificate; run 'adaos dev root init' to rotate credentials",
                    ) from exc
                authorize_cert = None
                authorize_verify = verify_plain
                try:
                    start = client.device_authorize(verify=authorize_verify, payload=authorize_payload)
                except RootHttpError as retry_exc:
                    try:
                        fallback = self._maybe_retry_with_mtls(cfg, retry_exc)
                    except RootServiceError as mtls_exc:
                        raise mtls_exc from retry_exc
                    if not fallback:
                        raise RootServiceError(str(retry_exc)) from retry_exc
                    authorize_verify, authorize_cert = fallback
                    try:
                        start = client.device_authorize(
                            verify=authorize_verify,
                            cert=authorize_cert,
                            payload=authorize_payload,
                        )
                    except RootHttpError as second_exc:
                        raise RootServiceError(str(second_exc)) from second_exc
        else:
            try:
                start = client.device_authorize(verify=authorize_verify, payload=authorize_payload)
            except RootHttpError as exc:
                try:
                    fallback = self._maybe_retry_with_mtls(cfg, exc)
                except RootServiceError as mtls_exc:
                    raise mtls_exc from exc
                if not fallback:
                    raise RootServiceError(str(exc)) from exc
                authorize_verify, authorize_cert = fallback
                try:
                    start = client.device_authorize(
                        verify=authorize_verify,
                        cert=authorize_cert,
                        payload=authorize_payload,
                    )
                except RootHttpError as retry_exc:
                    raise RootServiceError(str(retry_exc)) from retry_exc

        device_code = start.get("device_code")
        user_code = start.get("user_code") or start.get("user_code_short")
        verification_uri = start.get("verify_uri")
        verification_complete = start.get("verification_uri_complete")
        interval = max(int(start.get("interval", 5)), 1)
        expires_in = int(start.get("expires_in", 600))
        if not isinstance(device_code, str) or not isinstance(user_code, str) or not isinstance(verification_uri, str):
            raise RootServiceError("Root did not return device authorization data")
        zone_id = _extract_zone_id_from_url(verification_complete) or _extract_zone_id_from_url(verification_uri)
        if not zone_id:
            zone_id = canonical_zone_id((os.getenv("ADAOS_ZONE_ID") or "").strip().lower())

        auth = DeviceAuthorization(
            device_code=device_code,
            user_code=user_code,
            verification_uri=verification_uri,
            verification_uri_complete=verification_complete if isinstance(verification_complete, str) else None,
            zone_id=zone_id,
            interval=interval,
            expires_in=expires_in,
        )
        return auth, authorize_verify, authorize_cert

    def device_authorize(self, *, owner_id_hint: str | None = None) -> DeviceAuthorization:
        """
        Start a browser/device authorization flow and return the code+URL without polling.

        Intended for bootstrap scripts that want to print a pairing URL / QR and let the user
        complete the flow later.
        """
        cfg = self._load_config()
        verify_plain = self._plain_verify(cfg)
        client = self._owner_auth_client(cfg)
        hint = owner_id_hint or cfg.subnet_id or cfg.subnet_settings.id or "local-owner"
        auth, _verify, _cert = self._start_device_authorization(
            cfg,
            client=client,
            verify_plain=verify_plain,
            owner_id_hint=hint,
        )
        return auth

    def login(
        self,
        *,
        on_authorize: Callable[[DeviceAuthorization], None] | None = None,
    ) -> RootLoginResult:
        cfg = self._load_config()
        node_id = cfg.node_settings.id or cfg.node_id
        emit(self.ctx.bus, "root.dev.login.start", {"node_id": node_id}, "root.dev")

        try:
            verify_plain = self._plain_verify(cfg)
            client = self._owner_auth_client(cfg)
            owner_id_hint = cfg.subnet_id or cfg.subnet_settings.id or "local-owner"
            auth, authorize_verify, authorize_cert = self._start_device_authorization(
                cfg,
                client=client,
                verify_plain=verify_plain,
                owner_id_hint=owner_id_hint,
            )
            if on_authorize:
                on_authorize(auth)

            deadline = time.monotonic() + auth.expires_in
            delay = 0
            while time.monotonic() < deadline:
                if delay:
                    time.sleep(delay)
                try:
                    result = client.device_poll(auth.device_code, verify=authorize_verify, cert=authorize_cert)
                except RootHttpError as exc:
                    code = exc.error_code or ""
                    if code == "authorization_pending":
                        delay = auth.interval
                        continue
                    if code == "slow_down":
                        auth.interval += 5
                        delay = auth.interval
                        continue
                    if code in {"expired_token", "expired_device_code"}:
                        raise RootServiceError("Device authorization expired before completion") from exc
                    raise RootServiceError(str(exc)) from exc
                else:
                    token = result
                    break
            else:
                raise RootServiceError("Device authorization expired before completion")

            owner_id = token.get("owner_id")
            subnet_id = token.get("subnet_id") if isinstance(token, Mapping) else None
            if not isinstance(owner_id, str) or not owner_id:
                raise RootServiceError("Root did not return owner_id")

            cfg.root_settings.owner.owner_id = owner_id
            if isinstance(subnet_id, str) and subnet_id:
                cfg.subnet_settings.id = subnet_id
                cfg.subnet_id = subnet_id

            workspace = self._activate_workspace(cfg, owner_id)
            self._save_config(cfg)
            result = RootLoginResult(owner_id=owner_id, workspace_path=workspace, subnet_id=subnet_id if isinstance(subnet_id, str) else None)
            emit(
                self.ctx.bus,
                "root.dev.login.done",
                {
                    "node_id": node_id,
                    "owner_id": owner_id,
                    "subnet_id": subnet_id,
                    "workspace": displayable_path(workspace) or str(workspace),
                },
                "root.dev",
            )
            return result
        except Exception:
            emit(self.ctx.bus, "root.dev.login.error", {"node_id": node_id}, "root.dev")
            raise

    def create_skill(self, name: str, template: str | None = None) -> ArtifactCreateResult:
        cfg = self._load_config()
        node_id = cfg.node_settings.id or cfg.node_id
        emit(self.ctx.bus, "root.dev.skill.create.start", {"name": name, "node_id": node_id}, "root.dev")
        try:
            result = self._create_artifact("skills", name, template=template)
        except Exception:
            emit(
                self.ctx.bus,
                "root.dev.skill.create.error",
                {"name": name, "node_id": node_id},
                "root.dev",
            )
            raise
        emit(
            self.ctx.bus,
            "root.dev.skill.create.done",
            {
                "name": result.name,
                "node_id": node_id,
                "version": result.version,
                "updated_at": result.updated_at,
            },
            "root.dev",
        )
        return result

    def create_scenario(self, name: str, template: str | None = None) -> ArtifactCreateResult:
        cfg = self._load_config()
        node_id = cfg.node_settings.id or cfg.node_id
        emit(self.ctx.bus, "root.dev.scenario.create.start", {"name": name, "node_id": node_id}, "root.dev")
        try:
            result = self._create_artifact("scenarios", name, template=template)
        except Exception:
            emit(
                self.ctx.bus,
                "root.dev.scenario.create.error",
                {"name": name, "node_id": node_id},
                "root.dev",
            )
            raise
        emit(
            self.ctx.bus,
            "root.dev.scenario.create.done",
            {
                "name": result.name,
                "node_id": node_id,
                "version": result.version,
                "updated_at": result.updated_at,
            },
            "root.dev",
        )
        return result

    def push_skill(
        self,
        name: str,
        *,
        message: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactPushResult:
        cfg = self._load_config()
        node_id = cfg.node_settings.id or cfg.node_id
        emit(self.ctx.bus, "root.dev.skill.push.start", {"name": name, "node_id": node_id}, "root.dev")
        try:
            result = self._push_artifact("skills", name, message=message, metadata=metadata)
        except Exception:
            emit(
                self.ctx.bus,
                "root.dev.skill.push.error",
                {"name": name, "node_id": node_id},
                "root.dev",
            )
            raise
        emit(
            self.ctx.bus,
            "root.dev.skill.push.done",
            {
                "name": result.name,
                "node_id": node_id,
                "version": result.version,
                "updated_at": result.updated_at,
                "bytes": result.bytes_uploaded,
                "commit": result.commit,
                "message": result.message,
                "metadata": result.metadata,
            },
            "root.dev",
        )
        return result

    def push_scenario(
        self,
        name: str,
        *,
        message: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactPushResult:
        cfg = self._load_config()
        node_id = cfg.node_settings.id or cfg.node_id
        emit(self.ctx.bus, "root.dev.scenario.push.start", {"name": name, "node_id": node_id}, "root.dev")
        try:
            result = self._push_artifact("scenarios", name, message=message, metadata=metadata)
        except Exception:
            emit(
                self.ctx.bus,
                "root.dev.scenario.push.error",
                {"name": name, "node_id": node_id},
                "root.dev",
            )
            raise
        emit(
            self.ctx.bus,
            "root.dev.scenario.push.done",
            {
                "name": result.name,
                "node_id": node_id,
                "version": result.version,
                "updated_at": result.updated_at,
                "bytes": result.bytes_uploaded,
                "commit": result.commit,
                "message": result.message,
                "metadata": result.metadata,
            },
            "root.dev",
        )
        return result

    def update_skill(self, name: str) -> ArtifactUpdateResult:
        cfg = self._load_config()
        node_id = cfg.node_settings.id or cfg.node_id
        emit(
            self.ctx.bus,
            "root.dev.skill.update.retired",
            {"name": name, "node_id": node_id, "replacement": "exact_base_rebase"},
            "root.dev",
        )
        raise RootServiceError(
            "DEV draft update is retired because it can overwrite local changes. "
            "Create or migrate an exact-base DEV context, then reapply the bounded ChangeSet."
        )

    def update_scenario(self, name: str) -> ArtifactUpdateResult:
        cfg = self._load_config()
        node_id = cfg.node_settings.id or cfg.node_id
        emit(
            self.ctx.bus,
            "root.dev.scenario.update.retired",
            {"name": name, "node_id": node_id, "replacement": "exact_base_rebase"},
            "root.dev",
        )
        raise RootServiceError(
            "DEV draft update is retired because it can overwrite local changes. "
            "Create or migrate an exact-base DEV context, then reapply the bounded ChangeSet."
        )

    def publish_skill(
        self,
        name: str,
        *,
        bump: Literal["major", "minor", "patch"] = "patch",
        force: bool = False,
        dry_run: bool = False,
        signoff: bool = False,
    ) -> ArtifactPublishResult:
        # TODO self.caps.require("core", "skills.manage", "git.write", "net.git")
        # Проверяем именно skills workspace-репо
        root = self.ctx.paths.workspace_dir()
        if not (Path(root) / ".git").exists():
            raise RuntimeError("Skills repo is not initialized. Run `adaos skill sync` once.")
        cfg = self._load_config()
        node_id = cfg.node_settings.id or cfg.node_id
        emit(
            self.ctx.bus,
            "root.dev.skill.publish.start",
            {"name": name, "node_id": node_id, "bump": bump, "dry_run": dry_run},
            "root.dev",
        )
        try:
            result = self._publish_artifact(
                cfg,
                "skills",
                name,
                bump=bump,
                force=force,
                dry_run=dry_run,
            )
        except ArtifactNotFoundError:
            emit(
                self.ctx.bus,
                "root.dev.skill.publish.missing",
                {"name": name, "node_id": node_id},
                "root.dev",
            )
            raise
        except Exception:
            emit(
                self.ctx.bus,
                "root.dev.skill.publish.error",
                {"name": name, "node_id": node_id},
                "root.dev",
            )
            raise
        emit(
            self.ctx.bus,
            "root.dev.skill.publish.done",
            {
                "name": result.name,
                "node_id": node_id,
                "version": result.version,
                "previous_version": result.previous_version,
                "updated_at": result.updated_at,
                "dry_run": result.dry_run,
            },
            "root.dev",
        )
        # гарантируем, что подпуть есть в sparse-checkout (на случай узкой sparse-конфигурации)
        if result.dry_run:
            return result
        try:
            self.ctx.git.sparse_add(str(self.ctx.paths.workspace_dir()), f"skills/{name}")
        except Exception:
            pass
        # Создаём менеджер навыков вручную и пушим подпуть
        mgr = SkillManager(
            repo=self.ctx.skills_repo,
            registry=SqliteSkillRegistry(self.ctx.sql),
            git=self.ctx.git,
            paths=self.ctx.paths,
            bus=getattr(self.ctx, "bus", None),
            caps=self.ctx.caps,
            settings=self.ctx.settings,
        )
        msg = f"publish(skill): {result.name} v{result.version}"
        sha = mgr.push(result.name, msg, signoff=signoff, bump=False)
        emit(
            self.ctx.bus,
            "registry.skills.published",
            {
                "name": result.name,
                "version": result.version,
                "previous_version": result.previous_version,
                "updated_at": result.updated_at,
            },
            "root.dev",
        )
        # ничего не мешает вернуть sha в result через setattr/обновлённый датакласс — но это опционально
        return result

    def publish_scenario(
        self,
        name: str,
        *,
        bump: Literal["major", "minor", "patch"] = "patch",
        force: bool = False,
        dry_run: bool = False,
        signoff: bool = False,
    ) -> ArtifactPublishResult:
        root = self.ctx.paths.workspace_dir()
        if not (Path(root) / ".git").exists():
            raise RuntimeError("Skills repo is not initialized. Run `adaos skill sync` once.")
        cfg = self._load_config()
        node_id = cfg.node_settings.id or cfg.node_id
        emit(
            self.ctx.bus,
            "root.dev.scenario.publish.start",
            {"name": name, "node_id": node_id, "bump": bump, "dry_run": dry_run},
            "root.dev",
        )
        try:
            result = self._publish_artifact(
                cfg,
                "scenarios",
                name,
                bump=bump,
                force=force,
                dry_run=dry_run,
            )
        except ArtifactNotFoundError:
            emit(
                self.ctx.bus,
                "root.dev.scenario.publish.missing",
                {"name": name, "node_id": node_id},
                "root.dev",
            )
            raise
        except Exception:
            emit(
                self.ctx.bus,
                "root.dev.scenario.publish.error",
                {"name": name, "node_id": node_id},
                "root.dev",
            )
            raise
        emit(
            self.ctx.bus,
            "root.dev.scenario.publish.done",
            {
                "name": result.name,
                "node_id": node_id,
                "version": result.version,
                "previous_version": result.previous_version,
                "updated_at": result.updated_at,
                "dry_run": result.dry_run,
            },
            "root.dev",
        )
        if result.dry_run:
            return result
        try:
            self.ctx.git.sparse_add(str(self.ctx.paths.workspace_dir()), f"scenarios/{name}")
        except Exception:
            pass
        # Создаём менеджер навыков вручную и пушим подпуть
        mgr = ScenarioManager(
            repo=self.ctx.scenarios_repo,
            registry=SqliteSkillRegistry(self.ctx.sql),
            git=self.ctx.git,
            paths=self.ctx.paths,
            bus=getattr(self.ctx, "bus", None),
            caps=self.ctx.caps,
        )
        msg = f"publish(scenario): {result.name} v{result.version}"
        sha = mgr.push(result.name, msg, signoff=signoff, bump=False)
        emit(
            self.ctx.bus,
            "registry.scenarios.published",
            {
                "name": result.name,
                "version": result.version,
                "previous_version": result.previous_version,
                "updated_at": result.updated_at,
            },
            "root.dev",
        )
        # ничего не мешает вернуть sha в result через setattr/обновлённый датакласс — но это опционально
        return result

    def delete_skill(self, name: str) -> ArtifactDeleteResult:
        cfg = self._load_config()
        node_id = cfg.node_settings.id or cfg.node_id
        emit(self.ctx.bus, "root.dev.skill.delete.start", {"name": name, "node_id": node_id}, "root.dev")
        try:
            result = self._delete_artifact(cfg, "skills", name)
        except ArtifactNotFoundError:
            emit(
                self.ctx.bus,
                "root.dev.skill.delete.missing",
                {"name": name, "node_id": node_id},
                "root.dev",
            )
            raise
        except Exception:
            emit(
                self.ctx.bus,
                "root.dev.skill.delete.error",
                {"name": name, "node_id": node_id},
                "root.dev",
            )
            raise
        # 1) удаляем драфт на root (он и был у тебя в репозитории)
        draft_audit = self._delete_draft_remote("skills", result.name, node_id=node_id, all_nodes=False)
        # 2) best-effort: удаляем из registry (может вернуться 404 — это ок)
        audit = self._delete_registry_remote("skills", result.name, version=None, all_versions=True, force=False)
        emit(
            self.ctx.bus,
            "dev.skills.deleted",
            {
                "name": result.name,
                "node_id": node_id,
                "version": result.version,
                "updated_at": result.updated_at,
                "draft_deleted": (draft_audit or {}).get("deleted", []),
                "registry_deleted_versions": (audit or {}).get("deleted", []),
                "registry_audit_id": (audit or {}).get("audit_id"),
            },
            "root.dev",
        )
        return result

    def delete_scenario(self, name: str) -> ArtifactDeleteResult:
        cfg = self._load_config()
        node_id = cfg.node_settings.id or cfg.node_id
        emit(self.ctx.bus, "root.dev.scenario.delete.start", {"name": name, "node_id": node_id}, "root.dev")
        try:
            result = self._delete_artifact(cfg, "scenarios", name)
        except ArtifactNotFoundError:
            emit(
                self.ctx.bus,
                "root.dev.scenario.delete.missing",
                {"name": name, "node_id": node_id},
                "root.dev",
            )
            raise
        except Exception:
            emit(
                self.ctx.bus,
                "root.dev.scenario.delete.error",
                {"name": name, "node_id": node_id},
                "root.dev",
            )
            raise
        draft_audit = self._delete_draft_remote("scenarios", result.name, node_id=node_id, all_nodes=False)
        audit = self._delete_registry_remote("scenarios", result.name, version=None, all_versions=True, force=False)
        emit(
            self.ctx.bus,
            "dev.scenarios.deleted",
            {
                "name": result.name,
                "node_id": node_id,
                "version": result.version,
                "updated_at": result.updated_at,
                "draft_deleted": (draft_audit or {}).get("deleted", []),
                "registry_deleted_versions": (audit or {}).get("deleted", []),
                "registry_audit_id": (audit or {}).get("audit_id"),
            },
            "root.dev",
        )
        return result

    def list_skills(self) -> list[ArtifactListItem]:
        cfg = self._load_config()
        node_id = cfg.node_settings.id or cfg.node_id
        items = self._list_artifacts(cfg, "skills")
        self._save_config(cfg)
        emit(
            self.ctx.bus,
            "root.dev.skill.list.done",
            {"node_id": node_id, "count": len(items)},
            "root.dev",
        )
        return items

    def list_scenarios(self) -> list[ArtifactListItem]:
        cfg = self._load_config()
        node_id = cfg.node_settings.id or cfg.node_id
        items = self._list_artifacts(cfg, "scenarios")
        self._save_config(cfg)
        emit(
            self.ctx.bus,
            "root.dev.scenario.list.done",
            {"node_id": node_id, "count": len(items)},
            "root.dev",
        )
        return items

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _client(self, cfg: NodeConfig) -> RootHttpClient:
        if self._client_factory:
            return self._client_factory(cfg)
        base_url = cfg.root_settings.base_url or DEFAULT_PUBLIC_ROOT_BASE_URL
        return RootHttpClient(base_url=base_url)

    def _owner_auth_client(self, cfg: NodeConfig) -> RootHttpClient:
        if self._client_factory:
            return self._client_factory(cfg)
        zone_id = canonical_zone_id((os.getenv("ADAOS_ZONE_ID") or cfg.zone_id or "").strip().lower())
        if zone_id:
            return RootHttpClient(base_url=zone_public_base_url(zone_id))
        return self._client(cfg)

    def _plain_verify(self, cfg: NodeConfig) -> ssl.SSLContext | bool:
        ca_setting = cfg.root_settings.ca_cert
        ca_path = cfg.ca_cert_path()
        if ca_setting:
            if ca_path.exists():
                return self._load_verify_context(ca_path)
            default_indicator = self.ctx.paths.base_dir() / "keys" / "ca.cert"
            try:
                configured = Path(str(ca_setting)).expanduser()
            except Exception:  # pragma: no cover - defensive
                configured = ca_path
            # TODO Привести к единому источнику домашнего каталога self.ctx.paths.base_dir()
            """ if configured != default_indicator:
                display = displayable_path(ca_path) or str(ca_path)
                raise RootServiceError(f"CA certificate not found at {display}") """
        if _insecure_tls_enabled():
            return False
        return True

    def _register_hub(
        self,
        client: RootHttpClient,
        token: str,
        *,
        verify: ssl.SSLContext | bool,
        private_key: rsa.RSAPrivateKey,
        metadata: Mapping[str, Any] | None = None,
        subnet_id: str | None = None,
    ) -> Mapping[str, Any]:
        fingerprint = fingerprint_for_key(private_key)
        meta_payload: dict[str, Any] = {"fingerprint": fingerprint}
        zone_id = canonical_zone_id((os.getenv("ADAOS_ZONE_ID") or "").strip().lower())
        if zone_id:
            meta_payload["zone_id"] = zone_id
        if metadata:
            meta_payload.update(metadata)
        bootstrap = client.request_bootstrap_token(token, meta=meta_payload, verify=verify)
        bootstrap_token = bootstrap.get("one_time_token") or bootstrap.get("token")
        if not isinstance(bootstrap_token, str) or not bootstrap_token:
            raise RootServiceError("Root did not return bootstrap token")
        csr_common_name = self._hub_common_name(subnet_id)
        csr_pem = make_csr(csr_common_name, None, private_key).replace("\r\n", "\n").strip() + "\n"
        return client.register_subnet(
            csr_pem,
            bootstrap_token=bootstrap_token,
            verify=verify,
        )

    def _maybe_retry_with_mtls(
        self,
        cfg: NodeConfig,
        exc: RootHttpError,
    ) -> tuple[ssl.SSLContext, tuple[str, str]] | None:
        if not self._should_retry_with_mtls(exc):
            return None
        try:
            cert_path, key_path, verify = self._mtls_material(cfg)
        except RootServiceError as exc:
            raise RootServiceError(f"{exc} (required for client certificate authentication)") from exc
        return verify, (cert_path, key_path)

    def _mtls_material_optional(
        self,
        cfg: NodeConfig,
        verify_hint: ssl.SSLContext | bool,
    ) -> tuple[str, str, ssl.SSLContext | bool] | None:
        cert_path = cfg.hub_cert_path()
        key_path = cfg.hub_key_path()
        if not cert_path.exists() or not key_path.exists():
            return None
        subnet_id = cfg.subnet_id or cfg.subnet_settings.id
        if subnet_id:
            try:
                cert_pem = cert_path.read_text(encoding="utf-8")
            except OSError:
                return None

            # Если уже известен owner_id — проверим и O, но мягко (только если O присутствует)
            owner_id = cfg.root_settings.owner.owner_id if cfg.root_settings and cfg.root_settings.owner else None
            if not self._hub_certificate_is_acceptable(cert_pem, subnet_id=subnet_id, owner_id=owner_id):
                logger.debug("Skipping hub mTLS credentials due to subject mismatch (CN/O) on certificate")
                return None

        ca_path = cfg.ca_cert_path()
        if ca_path.exists():
            verify = self._load_verify_context(ca_path)
        else:
            verify = verify_hint
        return str(cert_path), str(key_path), verify

    @staticmethod
    def _should_retry_with_mtls(exc: RootHttpError) -> bool:
        if exc.error_code in {
            "invalid_client_certificate",
            "client_certificate_required",
            "client_certificate_missing",
        }:
            return True
        if exc.status_code in {400, 401, 403} and "certificate" in str(exc).lower():
            return True
        return False

    @staticmethod
    def _is_certificate_error(exc: RootHttpError) -> bool:
        if exc.error_code in {
            "invalid_client_certificate",
            "client_certificate_required",
            "client_certificate_missing",
        }:
            return True
        return "certificate" in str(exc).lower()

    def _mtls_material(self, cfg: NodeConfig) -> tuple[str, str, ssl.SSLContext]:
        ca_path = cfg.ca_cert_path()
        cert_path = cfg.hub_cert_path()
        key_path = cfg.hub_key_path()
        for label, path in ("CA certificate", ca_path), ("hub certificate", cert_path), ("hub private key", key_path):
            if not path.exists():
                raise RootServiceError(f"{label} not found at {path}; run 'adaos dev root init' first")
        verify = self._load_verify_context(ca_path)
        return str(cert_path), str(key_path), verify

    @staticmethod
    def _load_verify_context(ca_path: Path) -> ssl.SSLContext:
        try:
            context = ssl.create_default_context()
        except ssl.SSLError as exc:  # pragma: no cover - unexpected SSL configuration issues
            raise RootServiceError(f"Failed to create TLS context: {exc}") from exc
        try:
            context.load_verify_locations(cafile=str(ca_path))
        except (FileNotFoundError, ssl.SSLError) as exc:
            raise RootServiceError(f"Failed to load CA certificate from {ca_path}: {exc}") from exc
        return context

    def _ensure_hub_keypair(
        self,
        cfg: NodeConfig,
        *,
        force_new: bool = False,
    ) -> tuple[Path, rsa.RSAPrivateKey]:
        key_path = cfg.hub_key_path()
        cert_path = cfg.hub_cert_path()

        if force_new:
            try:
                key_path.unlink(missing_ok=True)
            except OSError:
                pass
            try:
                cert_path.unlink(missing_ok=True)
            except OSError:
                pass
        elif self._hub_certificate_requires_rotation(cert_path):
            try:
                key_path.unlink(missing_ok=True)
            except OSError:
                pass
            try:
                cert_path.unlink(missing_ok=True)
            except OSError:
                pass

        key_path.parent.mkdir(parents=True, exist_ok=True)
        if key_path.exists():
            try:
                private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
            except ValueError as exc:  # pragma: no cover - corrupted key
                raise RootServiceError(f"Invalid hub private key at {key_path}") from exc
        else:
            private_key = generate_rsa_key()
            write_private_key(key_path, private_key)
        cfg.subnet_settings.hub.key = _config_path_value(key_path)
        return key_path, private_key

    @staticmethod
    def _hub_certificate_requires_rotation(cert_path: Path) -> bool:
        if not cert_path.exists():
            return False
        try:
            x509.load_pem_x509_certificate(cert_path.read_bytes())
        except ValueError:
            return True
        return False

    @staticmethod
    def _hub_common_name(subnet_id: str | None) -> str:
        if subnet_id:
            return f"subnet:{subnet_id}"
        return "adaos-hub"

    @staticmethod
    def _hub_certificate_org(cert_pem: str) -> str | None:
        try:
            pem_clean = cert_pem.replace("\r\n", "\n").encode("utf-8")
            cert = x509.load_pem_x509_certificate(pem_clean)
        except Exception:
            return None
        try:
            attrs = cert.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)
            return attrs[0].value if attrs else None
        except Exception:
            return None

    @staticmethod
    def _hub_certificate_is_acceptable(
        cert_pem: str,
        *,
        subnet_id: str,
        owner_id: str | None = None,
    ) -> bool:
        """
        Допустимые варианты:
        - legacy: CN='subnet:<subnet_id>'
        - новый:  CN='hub:<hub_id>' (дополнительно, если указан owner_id, и присутствует O, то O должно быть 'owner:<owner_id>')
        """
        cn = RootDeveloperService._hub_certificate_common_name(cert_pem)
        if not cn:
            return False

        # Legacy-режим: CN=subnet:<subnet_id>
        if cn == f"subnet:{subnet_id}":
            return True

        # Новый формат: CN=hub:<hub_id>
        if cn.startswith("hub:"):
            if owner_id:
                org = RootDeveloperService._hub_certificate_org(cert_pem) or ""
                # Если поле O присутствует — оно должно совпадать. Если поля O нет, не заваливаем проверку.
                if org and org != f"owner:{owner_id}":
                    return False
            return True

        return False

    @staticmethod
    def _hub_certificate_common_name(cert_pem: str) -> str | None:
        try:
            # normalize line endings just in case
            pem_clean = cert_pem.replace("\r\n", "\n").encode("utf-8")
            cert = x509.load_pem_x509_certificate(pem_clean)
        except Exception as e:
            return None

        try:
            cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
            if not cn_attrs:
                return None
            return cn_attrs[0].value
        except Exception as e:
            return None

    def _workspace_root(self, cfg: NodeConfig) -> Path:
        hub_id = cfg.subnet_id or "pending_hub"
        return (self.ctx.paths.base_dir() / "dev" / hub_id).resolve()

    def artifact_release_repository(
        self,
        *,
        role: Literal["hub", "node"] = "hub",
        config: NodeConfig | None = None,
    ) -> RemoteReleaseRepository:
        """Return the authenticated immutable-artifact registry port."""

        cfg = config or self._load_config()
        cert_path, key_path, verify = self._mtls_material_for_role(cfg, role)
        return RemoteReleaseRepository(
            self._client(cfg),
            verify=verify,
            cert=(cert_path, key_path),
        )

    def _artifact_publication_service(self, cfg: NodeConfig) -> ArtifactPublicationService:
        remote = self.artifact_release_repository(role="hub", config=cfg)
        state_root = Path(self.ctx.paths.state_dir()) / "artifact_pipeline"
        trust = compose_artifact_trust_runtime(
            state_root=state_root,
            client=remote.client,
            verify=remote.verify,
            cert=remote.cert,
        )
        return ArtifactPublicationService(
            state_root=state_root,
            workspace_root=Path(self.ctx.paths.workspace_dir()),
            remote=remote,
            attestation_publisher=trust.publisher,
            attestation_admission=trust.admission,
        )

    @staticmethod
    def _workspace_lock_components(
        lock: Any,
        *,
        kind: str,
        component_keys: frozenset[str] | None = None,
    ) -> list[Any]:
        expected = str(kind or "").strip().lower().rstrip("s")
        return [
            component
            for component in getattr(lock, "components", ()) or ()
            if str(getattr(component, "kind", "") or "").strip().lower().rstrip("s") == expected
            and (
                component_keys is None
                or str(getattr(component, "key", "") or "") in component_keys
            )
        ]

    def _reload_published_workspace_runtime(
        self,
        lock: Any,
        *,
        component_keys: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        """Converge skill runtimes in the activated project dependency closure."""

        manager = _get_skill_manager(self.ctx)
        refreshed: list[dict[str, Any]] = []
        for component in self._workspace_lock_components(
            lock,
            kind="skill",
            component_keys=component_keys,
        ):
            skill_id = str(getattr(component, "artifact_id", "") or "").strip()
            version = str(getattr(component, "version", "") or "").strip()
            if not skill_id or not version:
                raise RootServiceError("WorkspaceLock contains an incomplete skill component")
            result = refresh_skill_runtime(
                manager,
                skill_id,
                source_version=version,
                migrate_runtime=True,
                ensure_installed=False,
                require_active_version=True,
                operation_id=f"workspace-lock:{getattr(lock, 'lock_revision', '')}",
            )
            refreshed.append(
                {
                    "skill_id": skill_id,
                    "version": version,
                    "active_version": result.get("active_version_after"),
                    "active_slot": result.get("active_slot_after"),
                    "runtime_migrated": bool(result.get("runtime_migrated")),
                }
            )
        return {
            "status": "completed",
            "lock_revision": getattr(lock, "lock_revision", None),
            "component_keys": sorted(component_keys or ()),
            "skills": refreshed,
        }

    def _health_published_workspace_runtime(
        self,
        lock: Any,
        *,
        component_keys: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        manager = _get_skill_manager(self.ctx)
        checks: list[dict[str, Any]] = []
        failures: list[str] = []
        for component in self._workspace_lock_components(
            lock,
            kind="skill",
            component_keys=component_keys,
        ):
            skill_id = str(getattr(component, "artifact_id", "") or "").strip()
            expected = str(getattr(component, "version", "") or "").strip()
            status = manager.runtime_status(skill_id)
            active = str(status.get("version") or "").strip()
            ready = bool(status.get("ready", True)) and bool(status.get("active_slot")) and active == expected
            checks.append(
                {
                    "kind": "skill_runtime",
                    "artifact_id": skill_id,
                    "expected_version": expected,
                    "active_version": active,
                    "active_slot": status.get("active_slot"),
                    "ok": ready,
                }
            )
            if not ready:
                failures.append(f"skill:{skill_id} expected={expected} active={active or 'none'}")
        for component in self._workspace_lock_components(
            lock,
            kind="scenario",
            component_keys=component_keys,
        ):
            scenario_id = str(getattr(component, "artifact_id", "") or "").strip()
            expected = str(getattr(component, "version", "") or "").strip()
            manifest_path = Path(self.ctx.paths.scenarios_dir()) / scenario_id / "scenario.yaml"
            manifest = _load_manifest(manifest_path)
            installed = str(manifest.get("version") or "").strip()
            ready = bool(scenario_id) and installed == expected
            checks.append(
                {
                    "kind": "scenario_source",
                    "artifact_id": scenario_id,
                    "expected_version": expected,
                    "installed_version": installed,
                    "ok": ready,
                }
            )
            if not ready:
                failures.append(f"scenario:{scenario_id} expected={expected} installed={installed or 'none'}")
        if failures:
            raise RootServiceError("Published Workspace runtime did not converge: " + "; ".join(failures))
        return {
            "status": "healthy",
            "lock_revision": getattr(lock, "lock_revision", None),
            "component_keys": sorted(component_keys or ()),
            "checks": checks,
        }

    def prepare_artifact_candidate(
        self,
        kind: Literal["skill", "scenario"],
        name: str,
        *,
        change_ids: tuple[str, ...],
        validation_evidence: Mapping[str, Any] | None = None,
        target_webspace_id: str = "desktop",
        target_space_kind: str = "development",
        target_zone: str | None = None,
        target_subnet_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        cfg = self._load_config()
        plural: Literal["skills", "scenarios"] = "skills" if kind == "skill" else "scenarios"
        source = self._workspace_root(cfg) / plural / name
        if not source.is_dir():
            raise ArtifactNotFoundError(f"{kind.capitalize()} '{name}' not found at {source}")
        self._validate_artifact_preflight(plural, name, source)
        evidence = dict(validation_evidence or {})
        evidence.setdefault("status", "passed")
        evidence.setdefault("validator", f"adaos.{kind}.preflight")
        prepared = self._artifact_publication_service(cfg).prepare_candidate(
            kind=kind,
            artifact_id=name,
            artifact_dir=source,
            change_ids=change_ids,
            validation_evidence=evidence,
            target_webspace_id=target_webspace_id,
            target_space_kind=target_space_kind,
            target_zone=target_zone,
            target_subnet_id=target_subnet_id,
            idempotency_key=idempotency_key,
        )
        return {
            "ok": True,
            "candidate": prepared.candidate.to_dict(),
            "release": prepared.plan.release.to_dict(),
            "trial_workspace": str(prepared.trial_workspace),
            "trial_activation": dict(prepared.trial_activation),
        }

    def prepare_project_candidate(
        self,
        project_id: str,
        *,
        source_kind: Literal["skill", "scenario"],
        source_name: str,
        source_revision: str,
        change_ids: tuple[str, ...],
        validation_evidence: Mapping[str, Any] | None = None,
        target_webspace_id: str = "desktop",
        target_space_kind: str = "development",
        target_zone: str | None = None,
        target_subnet_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        cfg = self._load_config()
        source_workspace = self._workspace_root(cfg)
        project_dir = source_workspace / "projects" / project_id
        if not (project_dir / "project.yaml").is_file():
            raise ArtifactNotFoundError(
                f"Project '{project_id}' not found at {project_dir}"
            )
        publication = self._artifact_publication_service(cfg)
        pushed = publication.load_pushed_source(source_kind, source_name)
        checkpoint_revision = str(source_revision or "").strip()
        if not checkpoint_revision:
            raise RootServiceError("Project candidate requires a checkpoint source revision")
        if pushed.source_ref.revision != checkpoint_revision:
            raise RootServiceError(
                "Project candidate source revision differs from the confirmed component checkpoint"
            )
        source_ref = ArtifactSourceRef(
            forge=pushed.source_ref.forge,
            repository=pushed.source_ref.repository,
            revision=checkpoint_revision,
            path_scope=(f"projects/{project_id}/",),
        )
        evidence = dict(validation_evidence or {})
        evidence.setdefault("status", "passed")
        evidence.setdefault("validator", "adaos.project.preflight")
        evidence.setdefault("checkpoint_source_revision", checkpoint_revision)
        evidence.setdefault("checkpoint_component_ref", f"{source_kind}:{source_name}")
        prepared = publication.prepare_project_candidate(
            project_id=project_id,
            project_dir=project_dir,
            source_workspace_root=source_workspace,
            source_ref=source_ref,
            change_ids=change_ids,
            validation_evidence=evidence,
            target_webspace_id=target_webspace_id,
            target_space_kind=target_space_kind,
            target_zone=target_zone,
            target_subnet_id=target_subnet_id,
            idempotency_key=idempotency_key,
        )
        return {
            "ok": True,
            "candidate": prepared.candidate.to_dict(),
            "release": prepared.plan.release.to_dict(),
            "trial_workspace": str(prepared.trial_workspace),
            "trial_activation": dict(prepared.trial_activation),
        }

    def decide_artifact_candidate(
        self,
        candidate_id: str,
        *,
        accepted: bool,
        observations: tuple[Mapping[str, Any], ...] = (),
    ) -> dict[str, Any]:
        cfg = self._load_config()
        candidate = self._artifact_publication_service(cfg).decide_candidate(
            candidate_id,
            accepted=accepted,
            observations=observations,
        )
        return {"ok": True, "candidate": candidate.to_dict()}

    def get_artifact_candidate(self, candidate_id: str) -> dict[str, Any]:
        cfg = self._load_config()
        token = str(candidate_id or "").strip()
        candidate = self._artifact_publication_service(cfg).get_candidate(token)
        publication = self._artifact_publication_service(cfg)
        trial_workspace = (
            Path(self.ctx.paths.workspace_dir())
            / ".runtime"
            / "trials"
            / token
            / "workspace"
        )
        return {
            "ok": True,
            "candidate": candidate.to_dict(),
            "trial_workspace": str(trial_workspace),
            "trial_activation": publication.get_trial_activation(token),
        }

    def reconcile_artifact_trial_activation(self, candidate_id: str) -> dict[str, Any]:
        cfg = self._load_config()
        activation = self._artifact_publication_service(cfg).reconcile_trial_activation(
            str(candidate_id or "").strip()
        )
        return {"ok": True, "trial_activation": activation}

    def recover_artifact_candidate_activation(
        self,
        candidate_id: str,
        operation_id: str,
    ) -> dict[str, Any]:
        cfg = self._load_config()
        try:
            result = self._artifact_publication_service(cfg).recover_promotion_activation(
                str(candidate_id or "").strip(),
                str(operation_id or "").strip(),
            )
        except Exception as exc:
            if isinstance(exc, RootServiceError):
                raise
            raise RootServiceError(str(exc)) from exc
        return {"ok": True, "recovery": result}

    def prepare_rebased_artifact_candidate(
        self,
        stale_candidate_id: str,
        kind: Literal["skill", "scenario"],
        name: str,
        *,
        validation_evidence: Mapping[str, Any] | None = None,
        target_webspace_id: str = "desktop",
        target_space_kind: str = "development",
        target_zone: str | None = None,
        target_subnet_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        cfg = self._load_config()
        plural: Literal["skills", "scenarios"] = "skills" if kind == "skill" else "scenarios"
        source = self._workspace_root(cfg) / plural / name
        if not source.is_dir():
            raise ArtifactNotFoundError(f"{kind.capitalize()} '{name}' not found at {source}")
        self._validate_artifact_preflight(plural, name, source)
        evidence = dict(validation_evidence or {})
        evidence.setdefault("status", "passed")
        evidence.setdefault("validator", f"adaos.{kind}.rebase-preflight")
        prepared = self._artifact_publication_service(cfg).prepare_rebased_candidate(
            stale_candidate_id,
            kind=kind,
            artifact_id=name,
            artifact_dir=source,
            validation_evidence=evidence,
            target_webspace_id=target_webspace_id,
            target_space_kind=target_space_kind,
            target_zone=target_zone,
            target_subnet_id=target_subnet_id,
            idempotency_key=idempotency_key,
        )
        return {
            "ok": True,
            "candidate": prepared.candidate.to_dict(),
            "release": prepared.plan.release.to_dict(),
            "trial_workspace": str(prepared.trial_workspace),
            "trial_activation": dict(prepared.trial_activation),
            "replaces_candidate_id": stale_candidate_id,
        }

    def promote_artifact_candidate(
        self,
        candidate_id: str,
        *,
        permission_decision: bool | Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        cfg = self._load_config()
        publication = self._artifact_publication_service(cfg)
        candidate_release = publication.get_candidate_release(candidate_id)
        affected_component_keys = frozenset(
            package.key for package in candidate_release.packages
        )

        def reload_candidate_runtime(lock: Any) -> dict[str, Any]:
            return self._reload_published_workspace_runtime(
                lock,
                component_keys=affected_component_keys,
            )

        def health_candidate_runtime(lock: Any) -> dict[str, Any]:
            return self._health_published_workspace_runtime(
                lock,
                component_keys=affected_component_keys,
            )

        try:
            promoted = publication.promote(
                candidate_id,
                permission_decision=permission_decision,
                reload_runtime=reload_candidate_runtime,
                health_check=health_candidate_runtime,
            )
        except PublicationStaleError as exc:
            return {
                "ok": False,
                "status": "stale",
                "candidate_id": candidate_id,
                "error": "candidate_base_moved",
                "rebase_plan": exc.plan.to_dict(),
            }
        component = next(
            (
                item
                for item in promoted.plan.release.components
                if item.artifact_id == promoted.candidate.project_id
            ),
            None,
        )
        if component is None:
            raise RootServiceError(
                "Promoted release does not contain its project component; workspace was not reconciled"
            )
        promotion_operation = publication.load_promotion(candidate_id) or {}
        promotion_receipts = (
            promotion_operation.get("receipts")
            if isinstance(promotion_operation.get("receipts"), Mapping)
            else {}
        )
        activation_receipt = (
            promotion_receipts.get("workspace_activated")
            if isinstance(promotion_receipts.get("workspace_activated"), Mapping)
            else {}
        )
        permission = (
            dict(permission_decision)
            if isinstance(permission_decision, Mapping)
            else {"approved": bool(permission_decision)}
        )
        accepted_trial = next(
            (
                item
                for item in reversed(promoted.candidate.trials)
                if item.status == "accepted"
            ),
            None,
        )
        trial_observations = (
            [dict(item) for item in accepted_trial.observations]
            if accepted_trial is not None
            else []
        )
        actor_id = str(permission.get("actor") or "").strip()
        if not actor_id:
            actor_id = next(
                (
                    str(item.get("actor") or item.get("actor_id") or "").strip()
                    for item in trial_observations
                    if str(item.get("actor") or item.get("actor_id") or "").strip()
                ),
                "builder.user",
            )
        runtime_slot = next(
            (
                item.slot_id
                for item in promoted.activation.workspace_lock.slots
                if item.project_id == promoted.candidate.project_id
            ),
            f"workspace-lock:{promoted.activation.workspace_lock.lock_revision}",
        )
        policy_evidence = [
            {
                "kind": "publication_permission",
                "approved": permission.get("approved") is True,
                "actor": actor_id,
            },
            *trial_observations,
        ]
        apply_evidence = {
            "draft_ref": {
                "draft_id": f"candidate:{promoted.candidate.candidate_id}",
                "revision": promoted.candidate.source_ref.revision,
            },
            "validation_evidence": [
                dict(item) for item in promoted.candidate.validation_evidence
            ],
            "approval": {
                "approval_id": str(
                    permission.get("approval_id")
                    or f"candidate:{promoted.candidate.candidate_id}:approval"
                ),
                "actor_id": actor_id,
                "actor_type": str(permission.get("actor_type") or "user"),
                "approved_at": promoted.candidate.updated_at,
                "policy_evidence": policy_evidence,
            },
            "activation": {
                "operation_id": promoted.activation.operation_id,
                "runtime_slot": runtime_slot,
                "health_receipt": dict(activation_receipt.get("health_receipt") or {}),
                "reload_receipt": dict(activation_receipt.get("reload_receipt") or {}),
                "workspace_lock_digest": promoted.activation.workspace_lock.to_dict()[
                    "lock_digest"
                ],
            },
            "rollback": {
                "mode": "workspace_lock_restore",
                "operation_ref": promoted.activation.operation_id,
            },
        }
        return {
            "ok": True,
            "candidate_id": candidate_id,
            "kind": component.kind,
            "name": component.artifact_id,
            "version": component.version,
            "release": promoted.pointer.release,
            "release_digest": promoted.pointer.release_digest,
            "package_digest": component.digest,
            "source_revision": component.source_ref.revision,
            "workspace_lock": promoted.activation.workspace_lock.to_dict(),
            "subscription": promoted.subscription.to_dict(),
            "commit": None,
            "activation_mode": "package_lock",
            "apply_evidence": apply_evidence,
        }

    def check_artifact_subscription(self, project_id: str) -> dict[str, Any]:
        cfg = self._load_config()
        notice = self._artifact_publication_service(cfg).check_subscription(project_id)
        return {"ok": True, **notice.to_dict()}

    def plan_artifact_registry_reconciliation(
        self,
        kind: Literal["skill", "scenario"],
        project_id: str,
        *,
        channel: str = "stable",
    ) -> dict[str, Any]:
        cfg = self._load_config()
        plan = self._artifact_publication_service(cfg).plan_registry_reconciliation(
            project_id,
            kind=kind,
            channel=channel,
        )
        return {"ok": True, **plan.to_dict()}

    def apply_artifact_registry_reconciliation(
        self,
        kind: Literal["skill", "scenario"],
        project_id: str,
        *,
        reviewed_plan_digest: str,
        channel: str = "stable",
    ) -> dict[str, Any]:
        cfg = self._load_config()
        result = self._artifact_publication_service(cfg).apply_registry_reconciliation(
            project_id,
            kind=kind,
            channel=channel,
            reviewed_plan_digest=reviewed_plan_digest,
        )
        return {"ok": True, **result}

    def plan_artifact_remote_registry_recovery(
        self,
        kind: Literal["skill", "scenario"],
        project_id: str,
        *,
        channel: str = "stable",
    ) -> dict[str, Any]:
        cfg = self._load_config()
        plan = self._artifact_publication_service(cfg).plan_remote_registry_recovery(
            project_id,
            kind=kind,
            channel=channel,
        )
        return {"ok": True, **plan.to_dict()}

    def revalidate_artifact_remote_registry_recovery(
        self,
        kind: Literal["skill", "scenario"],
        project_id: str,
        *,
        channel: str = "stable",
    ) -> dict[str, Any]:
        cfg = self._load_config()
        result = self._artifact_publication_service(
            cfg
        ).revalidate_remote_registry_recovery(
            project_id,
            kind=kind,
            channel=channel,
        )
        return {"ok": True, **result}

    def apply_artifact_remote_registry_recovery(
        self,
        kind: Literal["skill", "scenario"],
        project_id: str,
        *,
        reviewed_plan_digest: str,
        channel: str = "stable",
    ) -> dict[str, Any]:
        cfg = self._load_config()
        result = self._artifact_publication_service(cfg).apply_remote_registry_recovery(
            project_id,
            kind=kind,
            channel=channel,
            reviewed_plan_digest=reviewed_plan_digest,
        )
        return {"ok": True, **result}

    def plan_artifact_subscription_update(self, project_id: str) -> dict[str, Any]:
        cfg = self._load_config()
        plan = self._artifact_publication_service(cfg).plan_subscription_update(project_id)
        return {"ok": True, **plan.to_dict()}

    def inspect_artifact_subscription_update(self, project_id: str) -> dict[str, Any]:
        cfg = self._load_config()
        publication = self._artifact_publication_service(cfg)
        notice = publication.check_subscription(project_id)
        payload: dict[str, Any] = {"ok": True, **notice.to_dict(), "update_plan": None}
        if notice.available:
            payload["update_plan"] = publication.plan_subscription_update(
                project_id,
                notice=notice,
            ).to_dict()
        return payload

    def activate_artifact_subscription(
        self,
        project_id: str,
        *,
        idempotency_key: str | None = None,
        expected_plan_digest: str | None = None,
        permission_decision: bool | Mapping[str, Any] | None = None,
        reload_runtime=None,
        health_check=None,
        reload_policy: Mapping[str, Any] | None = None,
        health_policy: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        cfg = self._load_config()
        updated = self._artifact_publication_service(cfg).activate_subscription_update(
            project_id,
            idempotency_key=idempotency_key,
            expected_plan_digest=expected_plan_digest,
            permission_decision=permission_decision,
            reload_runtime=reload_runtime,
            health_check=health_check,
            reload_policy=reload_policy,
            health_policy=health_policy,
        )
        return {
            "ok": True,
            "project_id": project_id,
            "release": updated.pointer.release,
            "release_digest": updated.pointer.release_digest,
            "workspace_lock": updated.activation.workspace_lock.to_dict(),
            "subscription": updated.subscription.to_dict(),
            "activation_mode": "package_lock",
            "activation_operation_id": updated.activation.operation_id,
            "activation_status": updated.activation.status,
            "idempotent_replay": updated.activation.idempotent_replay,
        }

    def _prepare_workspace(self, cfg: NodeConfig, *, owner: str) -> Path:
        workspace_root = self._workspace_root(cfg)
        workspace_root.mkdir(parents=True, exist_ok=True)
        for sub in ("skills", "scenarios", "uploads"):
            _ensure_keep_file(workspace_root / sub)
        cfg.dev_settings.workspace = _config_path_value(workspace_root)
        return workspace_root

    def _activate_workspace(self, cfg: NodeConfig, owner_id: str) -> Path:
        return self._prepare_workspace(cfg, owner=owner_id)

    def _owner_workspace(self, cfg: NodeConfig) -> tuple[str, Path]:
        owner = cfg.owner_id or "pending_owner"
        path = self._prepare_workspace(cfg, owner=owner)
        self._save_config(cfg)
        return owner, path

    def _create_artifact(
        self,
        kind: Literal["skills", "scenarios"],
        name: str,
        *,
        template: str | None,
    ) -> ArtifactCreateResult:
        assert_safe_name(name)
        cfg = self._load_config()
        owner, workspace = self._owner_workspace(cfg)
        target = workspace / kind / name

        template_path, prototype_value = self._resolve_template(kind, template)
        _copy_template(template_path, target)
        if kind == "skills":
            _rewrite_skill_template_identity(target, name)
        else:
            _rewrite_scenario_template_identity(target, name)
        manifest_meta = self._update_manifest(
            kind,
            target,
            name,
            prototype_value,
            # A template version is the initial version of a newly-created
            # artifact. Creation is not a new revision of the template and
            # must not consume a semantic-version bump before the first
            # checkpoint or publication.
            version_bump_index=None,
            set_prototype=True,
        )
        if kind == "scenarios":
            _sync_scenario_content_metadata(target, name, manifest_meta)

        return ArtifactCreateResult(
            kind=kind.rstrip("s"),
            name=name,
            owner_id=owner,
            path=target,
            version=(manifest_meta or {}).get("version"),
            updated_at=(manifest_meta or {}).get("updated_at"),
        )

    def _list_artifacts(self, cfg: NodeConfig, kind: Literal["skills", "scenarios"]) -> list[ArtifactListItem]:
        owner = cfg.owner_id or "pending_owner"
        workspace = self._prepare_workspace(cfg, owner=owner)
        artifacts_dir = workspace / kind
        items: list[ArtifactListItem] = []
        if artifacts_dir.exists():
            for entry in artifacts_dir.iterdir():
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                name, version, updated_at = self._artifact_manifest_info(entry, kind)
                items.append(
                    ArtifactListItem(
                        name=name,
                        path=entry,
                        version=version,
                        updated_at=updated_at,
                    )
                )
        items.sort(key=lambda item: (_parse_timestamp(item.updated_at), item.name.lower()), reverse=True)
        return items

    def _delete_artifact(
        self,
        cfg: NodeConfig,
        kind: Literal["skills", "scenarios"],
        name: str,
    ) -> ArtifactDeleteResult:
        owner = cfg.owner_id or "pending_owner"
        workspace = self._prepare_workspace(cfg, owner=owner)
        target = workspace / kind / name
        if not target.exists() or not target.is_dir():
            raise ArtifactNotFoundError(f"{kind[:-1].capitalize()} '{name}' not found at {target}")

        manifest_name, version, updated_at = self._artifact_manifest_info(target, kind)
        shutil.rmtree(target)

        result = ArtifactDeleteResult(
            kind=kind.rstrip("s"),
            name=manifest_name,
            owner_id=owner,
            path=target,
            version=version,
            updated_at=updated_at,
        )
        self._save_config(cfg)
        return result

    def _delete_registry_remote(
        self,
        kind: Literal["skills", "scenarios"],
        name: str,
        *,
        version: str | None,
        all_versions: bool,
        force: bool,
    ) -> dict | None:
        cfg = self._load_config()
        owner_id = cfg.owner_id
        if not owner_id:
            raise RootServiceError("Owner is not configured; run 'adaos dev root login' first")
        cert_path, key_path, verify = self._mtls_material_for_role(cfg, "hub")
        client = self._client(cfg)
        try:
            if kind == "skills":
                resp = client.delete_skill_registry(
                    name=name,
                    version=version,
                    all_versions=all_versions,
                    force=force,
                    verify=verify,
                    cert=(cert_path, key_path),
                )
            else:
                resp = client.delete_scenario_registry(
                    name=name,
                    version=version,
                    all_versions=all_versions,
                    force=force,
                    verify=verify,
                    cert=(cert_path, key_path),
                )
        except RootHttpError as exc:
            # 404 — артефакт не публиковался в registry: это не ошибка для delete
            if getattr(exc, "status_code", None) == 404:
                return None
            # 409 — используется: подскажи юзеру про --force (пока не реализуем)
            if getattr(exc, "status_code", None) == 409:
                raise RootServiceError(f"Cannot delete {kind[:-1]} '{name}' from registry: artifact is in use") from exc
            raise
        except Exception as exc:
            raise RootServiceError(f"Failed to delete {kind[:-1]} '{name}' from registry on root") from exc
        return resp or {}

    def _delete_draft_remote(
        self,
        kind: Literal["skills", "scenarios"],
        name: str,
        *,
        node_id: str | None,
        all_nodes: bool,
    ) -> dict | None:
        cfg = self._load_config()
        if not cfg.owner_id:
            raise RootServiceError("Owner is not configured; run 'adaos dev root login' first")
        cert_path, key_path, verify = self._mtls_material_for_role(cfg, "hub")
        client = self._client(cfg)
        try:
            if kind == "skills":
                resp = client.delete_skill_draft(
                    name=name,
                    node_id=node_id,
                    all_nodes=all_nodes,
                    verify=verify,
                    cert=(cert_path, key_path),
                )
            else:
                resp = client.delete_scenario_draft(
                    name=name,
                    node_id=node_id,
                    all_nodes=all_nodes,
                    verify=verify,
                    cert=(cert_path, key_path),
                )
        except RootHttpError as exc:
            if getattr(exc, "status_code", None) == 404:
                return None
            raise
        except Exception as exc:
            raise RootServiceError(f"Failed to delete {kind[:-1]} draft '{name}' on root") from exc
        return resp or {}

    def _manifest_payload(
        self,
        directory: Path,
        kind: Literal["skills", "scenarios"],
    ) -> tuple[Path, dict[str, Any]] | None:
        for candidate in self._manifest_candidates(kind):
            manifest_path = directory / candidate
            if not manifest_path.exists():
                continue
            data = _load_manifest(manifest_path)
            return manifest_path, data
        return None

    def _manifest_warnings(
        self,
        source: dict[str, Any] | None,
        target: dict[str, Any] | None,
    ) -> list[str]:
        if not source or not target:
            return []
        ignore = {"version", "updated_at"}
        keys = sorted(set(source.keys()) | set(target.keys()))
        warnings: list[str] = []
        for key in keys:
            if key in ignore:
                continue
            if key not in source:
                warnings.append(f"Registry metadata contains '{key}' absent in dev copy")
                continue
            if key not in target:
                warnings.append(f"Dev metadata contains new field '{key}' not present in registry")
                continue
            if source[key] != target[key]:
                warnings.append(f"Field '{key}' differs between dev and registry metadata")
        return warnings

    def _workspace_templates_dir(self, kind: Literal["skills", "scenarios"]) -> Path:
        if kind == "scenarios":
            return self.ctx.paths.scenarios_workspace_dir()
        return self.ctx.paths.skills_workspace_dir()

    def _builtin_templates_dir(self, kind: Literal["skills", "scenarios"]) -> Path:
        if kind == "scenarios":
            return self.ctx.paths.scenario_templates_dir()
        return self.ctx.paths.skill_templates_dir()

    def _default_template_name(self, kind: Literal["skills", "scenarios"]) -> str:
        return "scenario_default" if kind == "scenarios" else "skill_default"

    def _collect_templates(self, directory: Path) -> list[str]:
        if not directory.exists():
            return []
        return sorted(entry.name for entry in directory.iterdir() if entry.is_dir())

    def _resolve_template(
        self,
        kind: Literal["skills", "scenarios"],
        template: str | None,
    ) -> tuple[Path, str]:
        workspace_dir = self._workspace_templates_dir(kind)
        builtin_dir = self._builtin_templates_dir(kind)
        default_name = self._default_template_name(kind)

        if isinstance(template, str):
            template = template.strip() or None
        if isinstance(template, str) and template.lower() == "default":
            template = None

        template_name = template or default_name

        search_candidates: list[Path] = []
        if template:
            search_candidates.extend(
                [
                    workspace_dir / template_name,
                    builtin_dir / template_name,
                ]
            )
        else:
            search_candidates.append(builtin_dir / template_name)

        for candidate in search_candidates:
            if candidate.exists():
                prototype_value = template if template else "default"
                return candidate, prototype_value

        available_user = self._collect_templates(workspace_dir)
        available_builtin = self._collect_templates(builtin_dir)

        limit = 20
        lines = [f"Template '{template_name}' not found for {kind[:-1]}."]
        lines.append("Available templates (use --template <name>):")

        remaining = limit
        if available_user:
            lines.append("  Workspace templates:")
            for name in available_user[:remaining]:
                lines.append(f"    - {name}")
            remaining -= min(len(available_user), remaining)
        if available_builtin and remaining > 0:
            lines.append("  Built-in templates:")
            for name in available_builtin[:remaining]:
                lines.append(f"    - {name}")
            remaining -= min(len(available_builtin), remaining)
        if not available_user and not available_builtin:
            lines.append("  (no templates available)")
        elif remaining == 0 and (len(available_user) + len(available_builtin)) > limit:
            lines.append("  …")

        lines.append("Specify a template explicitly with --template <name>.")

        raise TemplateResolutionError("\n".join(lines))

    def _update_manifest(
        self,
        kind: Literal["skills", "scenarios"],
        target: Path,
        name: str,
        prototype: str | None,
        *,
        version_bump_index: int | None,
        set_prototype: bool,
        explicit_version: str | None = None,
    ) -> dict[str, str] | None:
        manifest_meta: dict[str, str] | None = None
        for candidate in self._manifest_candidates(kind):
            manifest_path = target / candidate
            if not manifest_path.exists():
                continue
            data = _load_manifest(manifest_path)
            data["name"] = name
            if kind == "scenarios":
                data["id"] = name
            if set_prototype and prototype is not None:
                data["prototype"] = prototype

            if manifest_meta is None:
                existing_version = data.get("version") if isinstance(data.get("version"), str) else None
                if explicit_version is not None:
                    data["version"] = explicit_version
                elif version_bump_index is not None:
                    data["version"] = bump_version(existing_version, version_bump_index)
                timestamp = _current_timestamp()
                manifest_meta = {
                    "version": data.get("version") if isinstance(data.get("version"), str) else None,
                    "updated_at": timestamp,
                }
            else:
                data["version"] = manifest_meta.get("version")
                timestamp = str(manifest_meta.get("updated_at") or _current_timestamp())
            data["updated_at"] = timestamp

            _write_manifest(manifest_path, data)
            if kind != "scenarios":
                break
        if manifest_meta is not None:
            conversational_manifest = target / "conversational" / "manifest.yaml"
            if conversational_manifest.is_file():
                conversational_data = _load_manifest(conversational_manifest)
                conversational_data["version"] = manifest_meta.get("version")
                _write_manifest(conversational_manifest, conversational_data)
        return manifest_meta

    def _manifest_candidates(self, kind: Literal["skills", "scenarios"]) -> list[str]:
        if kind == "skills":
            return ["skill.yaml"]
        return ["scenario.yaml"]

    def _artifact_manifest_info(
        self,
        entry: Path,
        kind: Literal["skills", "scenarios"],
    ) -> tuple[str, str | None, str | None]:
        for candidate in self._manifest_candidates(kind):
            manifest_path = entry / candidate
            if not manifest_path.exists():
                continue
            data = _load_manifest(manifest_path)
            name_raw = data.get("name")
            name = name_raw.strip() if isinstance(name_raw, str) and name_raw.strip() else entry.name
            version_raw = data.get("version")
            version = version_raw if isinstance(version_raw, str) and version_raw else None
            updated_raw = data.get("updated_at")
            updated_at = updated_raw if isinstance(updated_raw, str) and updated_raw else None
            return name, version, updated_at
        return entry.name, None, None

    def _mtls_material_for_role(self, cfg: NodeConfig, role: Literal["hub", "node"]) -> tuple[str, str, ssl.SSLContext]:
        ca_path = cfg.ca_cert_path()
        if role == "hub":
            cert_path = cfg.hub_cert_path()
            key_path = cfg.hub_key_path()
            role_name = "hub"
        else:
            cert_path = cfg.node_cert_path()
            key_path = cfg.node_key_path()
            role_name = "node"

        for label, path in (f"{role_name} certificate", cert_path), (f"{role_name} private key", key_path), ("CA certificate", ca_path):
            if not path.exists():
                raise RootServiceError(f"{label} not found at {path}; run 'adaos dev node register' (for node) or '... root init' (for hub) first")

        verify = self._load_verify_context(ca_path)
        return str(cert_path), str(key_path), verify

    def _push_artifact(
        self,
        kind: Literal["skills", "scenarios"],
        name: str,
        *,
        message: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactPushResult:
        cfg = self._load_config()

        owner_id = cfg.owner_id
        if not owner_id:
            raise RootServiceError("Owner is not configured; run 'adaos dev root login' first")
        _, workspace = self._owner_workspace(cfg)
        source = workspace / kind / name
        if not source.exists():
            raise RootServiceError(f"{kind[:-1].capitalize()} '{name}' not found at {source}")
        self._validate_artifact_preflight(kind, name, source)
        commit_message = _normalize_draft_commit_message(message)
        commit_metadata = _normalize_draft_metadata(metadata)
        change_id = str(commit_metadata.get("change_id") or "").strip()
        publication = self._artifact_publication_service(cfg)
        cert_path, key_path, verify = self._mtls_material_for_role(cfg, "hub")
        client = self._client(cfg)
        node_id = cfg.node_settings.id or cfg.node_id
        intent_path: Path | None = None
        intent_archive_path: Path | None = None
        intent: dict[str, Any] = {}
        if change_id:
            intent_key = hashlib.sha256(change_id.encode("utf-8")).hexdigest()
            intent_root = publication.state_root / "checkpoint-intents" / kind / name
            intent_path = intent_root / f"{intent_key}.json"
            intent_archive_path = intent_root / f"{intent_key}.zip"
            if intent_path.is_file():
                try:
                    loaded_intent = json.loads(intent_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise RootServiceError("Checkpoint intent journal is unreadable") from exc
                if not isinstance(loaded_intent, dict) or loaded_intent.get("change_id") != change_id:
                    raise RootServiceError("Checkpoint intent journal identity mismatch")
                intent = loaded_intent

        def write_intent(status: str, **extra: Any) -> None:
            nonlocal intent
            if not change_id or intent_path is None:
                return
            intent = {
                **intent,
                "schema": "adaos.artifact.checkpoint_intent.v1",
                "kind": kind.rstrip("s"),
                "artifact_id": name,
                "change_id": change_id,
                "status": status,
                "updated_at": _current_timestamp(),
                **extra,
            }
            atomic_write_json(intent_path, intent)

        # Older cores classified every exception after dispatch as an unknown
        # outcome. A concrete non-timeout 4xx response proves that Forge
        # rejected the request, so retry may safely rebuild the archive under
        # the current packaging policy without risking a duplicate commit.
        if intent.get("status") == "uncertain":
            rejected_status = _definitive_remote_rejection_status(intent)
            if rejected_status is not None:
                write_intent("rejected", remote_status=rejected_status)

        def result_from_checkpoint(
            *,
            source_ref: ArtifactSourceRef,
            stored_path: str,
            pushed_source: Any,
            response_metadata: Mapping[str, Any],
            archive_bytes: bytes,
        ) -> ArtifactPushResult:
            _, version, updated_at = self._artifact_manifest_info(source, kind)
            return ArtifactPushResult(
                kind=kind.rstrip("s"),
                name=name,
                stored_path=stored_path,
                sha256=hashlib.sha256(archive_bytes).hexdigest(),
                bytes_uploaded=len(archive_bytes),
                version=version,
                updated_at=updated_at,
                commit=source_ref.revision,
                message=commit_message,
                metadata=_normalize_draft_metadata(response_metadata),
                package_digest=pushed_source.package.digest,
                source_revision=source_ref.revision,
                source_tree=pushed_source.source_tree,
            )

        def verified_source_tree(
            response: Mapping[str, Any],
            *,
            revision: str,
        ) -> str:
            tree = str(response.get("tree_sha") or "").strip().lower()
            if not tree:
                verified = client.get_draft_source_tree(
                    kind=kind,
                    name=name,
                    revision=revision,
                    node_id=node_id,
                    verify=verify,
                    cert=(cert_path, key_path),
                )
                tree = str(verified.get("tree_sha") or "").strip().lower()
            if len(tree) not in {40, 64} or any(
                char not in "0123456789abcdef" for char in tree
            ):
                raise RootServiceError("Root did not return a verifiable Forge source tree")
            return tree

        def draft_receipt_identity(response: Mapping[str, Any]) -> dict[str, Any]:
            metadata = _normalize_draft_metadata(
                response.get("metadata") if isinstance(response.get("metadata"), Mapping) else {}
            )
            return {
                key: value
                for key, value in {
                    "stored_path": str(response.get("stored_path") or "").strip().rstrip("/"),
                    "commit": str(response.get("commit") or "").strip(),
                    "tree_sha": str(response.get("tree_sha") or "").strip().lower(),
                    "sha256": str(response.get("sha256") or "").strip().lower(),
                    "change_id": str(metadata.get("change_id") or "").strip(),
                }.items()
                if value
            }

        def same_draft_receipt(
            current: Mapping[str, Any],
            expected: Mapping[str, Any],
        ) -> bool:
            current_identity = draft_receipt_identity(current)
            expected_identity = {
                str(key): str(value).strip().rstrip("/") if key == "stored_path" else str(value).strip()
                for key, value in expected.items()
                if str(value or "").strip()
            }
            required = {"stored_path", "commit", "sha256"}
            return required.issubset(expected_identity) and all(
                current_identity.get(key) == value
                for key, value in expected_identity.items()
            )

        recorded = None
        if change_id and publication.pushed_source_path(kind.rstrip("s"), name).is_file():
            recorded = publication.load_pushed_source(kind.rstrip("s"), name)
            if change_id in recorded.change_ids:
                try:
                    publication.verify_pushed_source(recorded, source)
                except Exception as exc:
                    raise RootServiceError(
                        f"checkpoint id {change_id} was already used for different content"
                    ) from exc
                archive_bytes = create_zip_bytes(source)
                stored_path = str(recorded.source_ref.path_scope[0]).rstrip("/")
                return result_from_checkpoint(
                    source_ref=recorded.source_ref,
                    stored_path=stored_path,
                    pushed_source=recorded,
                    response_metadata=commit_metadata,
                    archive_bytes=archive_bytes,
                )

        if change_id:
            draft_info: Mapping[str, Any] = {}
            if intent.get("status") == "remote_confirmed" and isinstance(intent.get("receipt"), Mapping):
                draft_info = dict(intent["receipt"])
            try:
                if not draft_info:
                    draft_info = client.get_draft_info(
                        kind=kind,
                        name=name,
                        node_id=node_id,
                        verify=verify,
                        cert=(cert_path, key_path),
                    )
            except Exception as exc:
                missing = isinstance(exc, FileNotFoundError) or getattr(exc, "status_code", None) == 404
                if not missing:
                    if intent:
                        raise RootServiceError(
                            "Checkpoint outcome is unresolved; Forge reconciliation must succeed before another write"
                        ) from exc
                    raise RootServiceError(
                        "Forge checkpoint preflight is unavailable; state-changing write was not started"
                    ) from exc
                draft_info = {}
            draft_metadata = _normalize_draft_metadata(
                draft_info.get("metadata") if isinstance(draft_info.get("metadata"), Mapping) else {}
            )
            if str(draft_metadata.get("change_id") or "").strip() == change_id:
                if intent_archive_path is not None and intent_archive_path.is_file():
                    archive_bytes = intent_archive_path.read_bytes()
                else:
                    archive_bytes = create_zip_bytes(source)
                digest = hashlib.sha256(archive_bytes).hexdigest()
                expected_digest = str(draft_info.get("sha256") or "").strip().lower()
                if expected_digest and expected_digest != digest:
                    raise RootServiceError(
                        f"checkpoint id {change_id} was already used for different content"
                    )
                if intent_archive_path is not None and intent_archive_path.is_file():
                    _extract_zip_bytes(archive_bytes, source)
                stored = str(draft_info.get("stored_path") or "").strip()
                commit = str(draft_info.get("commit") or "").strip()
                if not stored or not commit:
                    raise RootServiceError("Root checkpoint receipt is incomplete")
                source_ref = ArtifactSourceRef(
                    forge="adaos-root",
                    repository=str(
                        getattr(cfg.dev_settings, "forge_repo", None)
                        or "inimatic/adaos-registry"
                    ),
                    revision=commit,
                    path_scope=(stored.rstrip("/") + "/",),
                )
                pushed_source = publication.record_push(
                    kind=kind.rstrip("s"),
                    artifact_id=name,
                    artifact_dir=source,
                    source_ref=source_ref,
                    change_ids=(change_id,),
                    source_tree=verified_source_tree(draft_info, revision=commit),
                )
                write_intent(
                    "completed",
                    archive_sha256=digest,
                    receipt=dict(draft_info),
                    package_digest=pushed_source.package.digest,
                    source_tree=pushed_source.source_tree,
                )
                return result_from_checkpoint(
                    source_ref=source_ref,
                    stored_path=stored,
                    pushed_source=pushed_source,
                    response_metadata=draft_metadata,
                    archive_bytes=archive_bytes,
                )
            if intent.get("status") in {"dispatching", "uncertain", "remote_confirmed"}:
                previous_receipt = (
                    dict(intent.get("previous_receipt"))
                    if isinstance(intent.get("previous_receipt"), Mapping)
                    else {}
                )
                if not previous_receipt and recorded is not None:
                    recorded_scope = (
                        str(recorded.source_ref.path_scope[0]).strip().rstrip("/")
                        if recorded.source_ref.path_scope
                        else ""
                    )
                    remote_identity = draft_receipt_identity(draft_info)
                    remote_change_id = str(remote_identity.get("change_id") or "").strip()
                    recorded_change_ids = {str(item).strip() for item in recorded.change_ids}
                    if (
                        remote_identity.get("commit") == recorded.source_ref.revision
                        and remote_identity.get("stored_path") == recorded_scope
                        and (
                            not recorded.source_tree
                            or remote_identity.get("tree_sha") == recorded.source_tree
                        )
                        and remote_change_id
                        and remote_change_id in recorded_change_ids
                    ):
                        previous_receipt = remote_identity
                archive_digest = str(intent.get("archive_sha256") or "").strip().lower()
                prepared_archive_matches = bool(
                    intent_archive_path is not None
                    and intent_archive_path.is_file()
                    and archive_digest
                    and hashlib.sha256(intent_archive_path.read_bytes()).hexdigest() == archive_digest
                )
                if (
                    prepared_archive_matches
                    and previous_receipt
                    and same_draft_receipt(draft_info, previous_receipt)
                ):
                    # The remote still exposes the exact receipt observed before
                    # dispatch, so the uncertain write provably did not become
                    # authoritative. Resume the immutable prepared archive; do
                    # not rebuild it or bump the manifest a second time.
                    write_intent(
                        "prepared",
                        archive_sha256=archive_digest,
                        previous_receipt=previous_receipt,
                        resolution="remote_receipt_unchanged",
                    )
                else:
                    raise RootServiceError(
                        "Checkpoint outcome is unresolved and does not match the current Forge receipt; refusing a duplicate write"
                    )

            previous_receipt = draft_receipt_identity(draft_info) if draft_info else {}

        rollback_paths = [
            source / ("skill.yaml" if kind == "skills" else "scenario.yaml"),
            workspace / "registry.json",
        ]
        conversational_manifest = source / "conversational" / "manifest.yaml"
        if conversational_manifest.is_file():
            rollback_paths.append(conversational_manifest)
        if kind == "scenarios":
            rollback_paths.append(source / "scenario.json")
            rollback_paths.append(source / "webui.json")
        snapshots = {
            path: path.read_bytes() if path.is_file() else None
            for path in rollback_paths
        }
        remote_committed = False
        dispatch_started = False
        try:
            resume_archive = (
                intent.get("status") == "prepared"
                and intent_archive_path is not None
                and intent_archive_path.is_file()
            )
            if resume_archive:
                archive_bytes = intent_archive_path.read_bytes()
                expected_archive_sha = str(intent.get("archive_sha256") or "").strip().lower()
                if hashlib.sha256(archive_bytes).hexdigest() != expected_archive_sha:
                    raise RootServiceError("Prepared checkpoint archive does not match its journal")
                _extract_zip_bytes(archive_bytes, source)
                _, resumed_version, resumed_updated_at = self._artifact_manifest_info(source, kind)
                manifest_meta = {
                    "version": resumed_version,
                    "updated_at": resumed_updated_at,
                }
            else:
                source_payload = self._manifest_payload(source, kind)
                source_data = source_payload[1] if source_payload else {}
                publish_bump_index = (
                    bump_index(
                        effective_skill_bump(
                            source_data,
                            "patch",
                            has_data_migration_file=(source / RESERVED_DATA_MIGRATION_FILE).is_file(),
                        )
                    )
                    if kind == "skills"
                    else bump_index("patch")
                )
                manifest_meta = self._update_manifest(
                    kind,
                    source,
                    name,
                    None,
                    version_bump_index=publish_bump_index,
                    set_prototype=False,
                )
                if kind == "scenarios":
                    _sync_scenario_content_metadata(source, name, manifest_meta)

            build_artifact_package(
                source,
                kind=kind.rstrip("s"),  # type: ignore[arg-type]
                source_ref=ArtifactSourceRef(
                    forge="checkpoint-preflight",
                    repository="local-dev",
                    revision="0" * 40,
                    path_scope=(f"{kind}/{name}/",),
                ),
            )
            try:
                upsert_workspace_registry_entry(
                    workspace,
                    kind,
                    source,
                    version=(manifest_meta or {}).get("version"),
                    updated_at=(manifest_meta or {}).get("updated_at"),
                    extra={
                        "publisher": {
                            "owner_id": owner_id,
                            "node_id": node_id,
                        }
                    },
                )
            except Exception:
                _log.debug(
                    "failed to update local workspace registry after push kind=%s name=%s",
                    kind,
                    name,
                    exc_info=True,
                )
            if not resume_archive:
                archive_bytes = create_zip_bytes(source)
            archive_b64 = archive_bytes_to_b64(archive_bytes)
            digest = hashlib.sha256(archive_bytes).hexdigest()
            if change_id and intent_archive_path is not None:
                atomic_write_bytes(intent_archive_path, archive_bytes)
                write_intent(
                    "prepared",
                    archive_sha256=digest,
                    previous_receipt=previous_receipt,
                )
            push_method = (
                client.push_skill_draft if kind == "skills" else client.push_scenario_draft
            )
            write_intent("dispatching", archive_sha256=digest)
            dispatch_started = True
            response = push_method(
                name=name,
                archive_b64=archive_b64,
                node_id=node_id,
                verify=verify,
                cert=(cert_path, key_path),
                sha256=digest,
                message=commit_message,
                metadata=commit_metadata,
            )
            remote_committed = True
            write_intent(
                "remote_confirmed",
                archive_sha256=digest,
                receipt=dict(response),
            )
            stored = str(response.get("stored_path") or "").strip()
            commit = str(response.get("commit") or "").strip()
            if not stored:
                raise RootServiceError("Root did not return stored_path")
            if not commit:
                raise RootServiceError(
                    "Root stored the draft archive but did not confirm a Forge commit"
                )
            response_metadata = _normalize_draft_metadata(
                response.get("metadata") if isinstance(response.get("metadata"), Mapping) else {}
            )
            if commit_metadata and response_metadata != commit_metadata:
                raise RootServiceError("Root returned stale Forge commit metadata for the draft archive")
            source_ref = ArtifactSourceRef(
                forge="adaos-root",
                repository=str(
                    getattr(cfg.dev_settings, "forge_repo", None)
                    or "inimatic/adaos-registry"
                ),
                revision=commit,
                path_scope=(stored.rstrip("/") + "/",),
            )
            source_tree = verified_source_tree(response, revision=commit)
            pushed_source = publication.record_push(
                kind=kind.rstrip("s"),
                artifact_id=name,
                artifact_dir=source,
                source_ref=source_ref,
                change_ids=(change_id,) if change_id else (),
                source_tree=source_tree,
            )
            write_intent(
                "completed",
                archive_sha256=digest,
                receipt=dict(response),
                package_digest=pushed_source.package.digest,
                source_tree=source_tree,
            )
            return result_from_checkpoint(
                source_ref=source_ref,
                stored_path=stored,
                pushed_source=pushed_source,
                response_metadata=response_metadata or commit_metadata,
                archive_bytes=archive_bytes,
            )
        except Exception as exc:
            if dispatch_started and not remote_committed:
                status_code = getattr(exc, "status_code", None)
                definitive_rejection = (
                    isinstance(status_code, int)
                    and 400 <= status_code < 500
                    and status_code != 408
                )
                write_intent(
                    "rejected" if definitive_rejection else "uncertain",
                    archive_sha256=(
                        hashlib.sha256(archive_bytes).hexdigest()
                        if "archive_bytes" in locals()
                        else None
                    ),
                    remote_status=status_code,
                    error=f"{type(exc).__name__}: {exc}",
                )
            if not remote_committed:
                for path, content in snapshots.items():
                    if content is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        temporary = path.with_name(f".{path.name}.rollback-{uuid4().hex}")
                        temporary.write_bytes(content)
                        os.replace(temporary, path)
            raise

    def _update_artifact(self, cfg: NodeConfig, kind: Literal["skills", "scenarios"], name: str) -> ArtifactUpdateResult:
        assert_safe_name(name)
        node_id = cfg.node_settings.id or cfg.node_id
        owner = cfg.owner_id or "pending_owner"
        workspace = self._prepare_workspace(cfg, owner=owner)
        target = workspace / kind / name
        source = Path(kind) / name
        commit_info: dict[str, Any] = {}
        archive_error: Exception | None = None
        downloaded = False

        try:
            cert_path, key_path, verify = self._mtls_material_for_role(cfg, "hub")
            response = self._client(cfg).get_draft_archive(
                kind=kind,
                name=name,
                node_id=node_id,
                verify=verify,
                cert=(cert_path, key_path),
            )
            archive_b64 = str(response.get("archive_b64") or "").strip()
            if not archive_b64:
                raise RootServiceError("Root draft archive response is missing archive_b64")
            archive_bytes = base64.b64decode(archive_b64, validate=True)
            expected_digest = str(response.get("sha256") or "").strip().lower()
            actual_digest = hashlib.sha256(archive_bytes).hexdigest()
            if expected_digest and expected_digest != actual_digest:
                raise RootServiceError("Root draft archive SHA256 mismatch")
            _extract_zip_bytes(archive_bytes, target)
            source = Path(str(response.get("stored_path") or f"{kind}/{name}"))
            response_metadata = response.get("metadata") if isinstance(response.get("metadata"), Mapping) else {}
            commit_info = {
                "commit": str(response.get("commit") or "").strip(),
                "message": str(response.get("message") or "").strip(),
                "metadata": _normalize_draft_metadata(response_metadata),
            }
            downloaded = True
        except Exception as exc:
            archive_error = exc
            _log.info("Root draft archive update unavailable kind=%s name=%s; trying Forge checkout: %s", kind, name, exc)

        forge_repo = getattr(cfg.dev_settings, "forge_repo", None)
        forge_path = getattr(cfg.dev_settings, "forge_path", None)
        if not downloaded:
            if not forge_repo or not forge_path:
                raise RootServiceError(
                    "Root draft archive update failed and Forge checkout is not configured: "
                    f"{type(archive_error).__name__}: {archive_error}"
                ) from archive_error
            repo_dir = Path(self.ctx.paths.dev_dir()) / ".forge"
            repo_dir.mkdir(parents=True, exist_ok=True)
            try:
                self.ctx.git.ensure_repo(str(repo_dir), forge_repo)
                self.ctx.git.pull(str(repo_dir))
            except Exception as exc:  # pragma: no cover - git failures depend on environment
                raise RootServiceError(f"Failed to update forge repository at {repo_dir}: {exc}") from exc
            relative = Path(forge_path) / "nodes" / node_id / kind / name
            checkout_source = repo_dir / relative
            if not checkout_source.exists():
                raise ArtifactNotFoundError(f"{kind[:-1].capitalize()} '{name}' not found in forge repository (expected at {relative})")
            temporary = target.parent / f".{target.name}.update-{os.getpid()}-{uuid4().hex}"
            try:
                shutil.copytree(checkout_source, temporary)
                _replace_directory_transactionally(temporary, target)
            except Exception:
                shutil.rmtree(temporary, ignore_errors=True)
                raise
            source = checkout_source
            try:
                commit_info = dict(self.ctx.git.latest_commit_for_path(str(repo_dir), relative.as_posix()) or {})
            except Exception:
                _log.debug("failed to read Forge commit metadata for %s", relative, exc_info=True)
        commit_message = str(commit_info.get("message") or "").strip()
        commit_metadata = _normalize_draft_metadata(
            commit_info.get("metadata") if isinstance(commit_info.get("metadata"), Mapping) else None
        ) or _parse_draft_commit_metadata(commit_message)
        recovery = self._reconcile_builder_change_from_forge(
            kind=kind,
            name=name,
            target=target,
            commit=str(commit_info.get("commit") or "").strip() or None,
            message=commit_message or None,
            metadata=commit_metadata,
        )
        manifest_name, version, updated_at = self._artifact_manifest_info(target, kind)
        return ArtifactUpdateResult(
            kind=kind.rstrip("s"),
            name=manifest_name,
            source_path=source,
            target_path=target,
            version=version,
            updated_at=updated_at,
            commit=str(commit_info.get("commit") or "").strip() or None,
            message=commit_message or None,
            metadata=commit_metadata or None,
            recovery=recovery,
        )

    def _reconcile_builder_change_from_forge(
        self,
        *,
        kind: Literal["skills", "scenarios"],
        name: str,
        target: Path,
        commit: str | None,
        message: str | None,
        metadata: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        change_id = str(metadata.get("change_id") or "").strip()
        conversation_id = str(metadata.get("conversation_id") or "").strip()
        topic_id = str(metadata.get("topic_id") or metadata.get("thread_id") or "").strip()
        thread_id = str(metadata.get("thread_id") or topic_id).strip()
        if not change_id or not conversation_id:
            return None
        from adaos.services import conversation_store

        existing_conversation = conversation_store.get_conversation(conversation_id)
        if not existing_conversation:
            conversation_store.upsert_conversation(
                conversation_id=conversation_id,
                webspace_id="global",
                owner="skill:builder_skill",
                kind="builder",
                title="Builder",
                meta={"surface": "builder", "scope": "global", "recovered_from_forge": True},
            )
        if thread_id:
            conversation_store.start_thread(
                conversation_id=conversation_id,
                thread_id=thread_id,
                title=f"Builder: {name}",
                created_by={"type": "core", "id": "forge_recovery"},
                meta={"topic_id": topic_id or thread_id, "artifact_kind": kind.rstrip("s"), "artifact_id": name},
            )
        revision = str(metadata.get("revision") or "").strip()
        source_message_ids = [
            str(item).strip()
            for item in metadata.get("source_message_ids", [])
            if str(item).strip()
        ] if isinstance(metadata.get("source_message_ids"), list) else []
        artifact_ref = {"kind": kind.rstrip("s"), "id": name, "path": str(target)}
        revision_refs = [{"revision": revision, "path": f"ui_revisions/{revision}.json"}] if revision else []
        commit_refs = [{"commit": commit, "message": str(message or "").splitlines()[0]}] if commit else []
        existing_change = conversation_store.get_development_change(change_id) or {}
        existing_change_meta = existing_change.get("meta") if isinstance(existing_change.get("meta"), Mapping) else {}
        reconciled_status = "recovered" if existing_change_meta.get("synthetic_chat") else "pushed"
        change = conversation_store.upsert_development_change(
            change_id=change_id,
            conversation_id=conversation_id,
            thread_id=thread_id or None,
            topic_id=topic_id or None,
            status=reconciled_status,
            source_message_ids=source_message_ids,
            source_refs={"request_id": metadata.get("request_id")},
            artifact_refs=[artifact_ref],
            revision_refs=revision_refs,
            commit_refs=commit_refs,
            result_message_id=str(metadata.get("result_message_id") or "").strip() or None,
            request_id=str(metadata.get("request_id") or "").strip() or None,
            model=str(metadata.get("model") or "").strip() or None,
            summary=str(message or "").splitlines()[0][:240],
            meta={"reconciled_from_forge": True},
        )
        projection = conversation_store.list_projection(
            conversation_id,
            thread_id=thread_id or None,
            limit=1,
            max_items=1,
        )
        if int(projection.get("total_message_count") or 0) > 0:
            return {"registered": True, "messages_recovered": 0, "change": change}

        revision_payload: dict[str, Any] = {}
        revision_path = target / "ui_revisions" / f"{revision}.json" if revision else None
        if revision_path is not None and revision_path.is_file():
            try:
                raw = json.loads(revision_path.read_text(encoding="utf-8-sig") or "{}")
                revision_payload = dict(raw) if isinstance(raw, Mapping) else {}
            except Exception:
                _log.debug("failed to read recovered Builder revision %s", revision_path, exc_info=True)
        request_payload = revision_payload.get("request") if isinstance(revision_payload.get("request"), Mapping) else {}
        llm_payload = revision_payload.get("llm") if isinstance(revision_payload.get("llm"), Mapping) else {}
        request_text = str(request_payload.get("text") or "").strip()
        result_text = str(llm_payload.get("comment") or "").strip() or str(message or "").splitlines()[0].strip()
        recovered_ids: list[str] = []
        recovered_request_id: str | None = None
        base_id = hashlib.sha256(f"{change_id}:{commit or ''}".encode("utf-8")).hexdigest()[:20]
        common_meta = {
            "source": "forge_recovery",
            "recovered": True,
            "change_id": change_id,
            "topic_id": topic_id or thread_id,
            "artifact_kind": kind.rstrip("s"),
            "artifact_id": name,
            "revision": revision or None,
            "commit": commit,
        }
        if request_text:
            recovered = conversation_store.append_message(
                conversation_id=conversation_id,
                thread_id=thread_id or None,
                webspace_id="recovery",
                channel_id="builder",
                owner="skill:builder_skill",
                role="user",
                text=request_text,
                payload={"id": f"m.recovery.{base_id}.request", "from": "recovery"},
                meta=common_meta,
                actor_id="forge_recovery",
                actor_label="Recovered request",
                idempotency_key=f"forge-recovery:{change_id}:request",
            )
            if recovered:
                recovered_request_id = str(recovered.get("id") or "") or None
                if recovered_request_id:
                    recovered_ids.append(recovered_request_id)
        if result_text:
            recovered = conversation_store.append_message(
                conversation_id=conversation_id,
                thread_id=thread_id or None,
                webspace_id="recovery",
                channel_id="builder",
                owner="skill:builder_skill",
                role="assistant",
                text=result_text,
                payload={"id": f"m.recovery.{base_id}.result", "from": "hub"},
                meta=common_meta,
                actor_id="agent:builder_skill:builder",
                actor_label="Builder (recovered)",
                idempotency_key=f"forge-recovery:{change_id}:result",
            )
            if recovered:
                recovered_ids.append(str(recovered.get("id") or ""))
        if recovered_ids:
            final_change = conversation_store.upsert_development_change(
                change_id=change_id,
                conversation_id=conversation_id,
                thread_id=thread_id or None,
                topic_id=topic_id or None,
                status="recovered",
                source_message_ids=list(
                    dict.fromkeys([*source_message_ids, *([recovered_request_id] if recovered_request_id else [])])
                ),
                artifact_refs=[artifact_ref],
                revision_refs=revision_refs,
                commit_refs=commit_refs,
                result_message_id=recovered_ids[-1],
                request_id=str(metadata.get("request_id") or "").strip() or None,
                model=str(metadata.get("model") or "").strip() or None,
                summary=str(message or "").splitlines()[0][:240],
                meta={"reconciled_from_forge": True, "synthetic_chat": True},
            )
            change = final_change or change
        return {"registered": True, "messages_recovered": len(recovered_ids), "change": change}

    def _publish_artifact(
        self,
        cfg: NodeConfig,
        kind: Literal["skills", "scenarios"],
        name: str,
        *,
        bump: Literal["major", "minor", "patch"],
        force: bool,
        dry_run: bool,
    ) -> ArtifactPublishResult:
        if bump not in {"major", "minor", "patch"}:
            raise RootServiceError(f"Unsupported version bump '{bump}'")

        workspace_root = self._workspace_root(cfg)
        source = workspace_root / kind / name
        if not source.exists() or not source.is_dir():
            raise ArtifactNotFoundError(f"{kind[:-1].capitalize()} '{name}' not found at {source}")
        self._validate_artifact_preflight(kind, name, source)

        target = (self.ctx.paths.scenarios_dir() if kind == "scenarios" else self.ctx.paths.skills_dir()) / name
        if not dry_run:
            assert_workspace_component_mutable(
                self.ctx.paths.workspace_dir(),
                kind=kind,
                artifact_id=name,
            )

        source_payload = self._manifest_payload(source, kind)
        source_data = source_payload[1] if source_payload else {}
        effective_bump = (
            effective_skill_bump(
                source_data,
                bump,
                has_data_migration_file=(source / RESERVED_DATA_MIGRATION_FILE).is_file(),
            )
            if kind == "skills"
            else bump
        )
        resolved_bump_index = bump_index(effective_bump)
        target_payload = self._manifest_payload(target, kind) if target.exists() else None
        target_data = target_payload[1] if target_payload else {}
        warnings = self._manifest_warnings(source_data, target_data)

        previous_version: str | None = None
        if target.exists():
            _, previous_version, _ = self._artifact_manifest_info(target, kind)

        new_version = bump_version(previous_version, resolved_bump_index)

        if warnings and not force and not dry_run:
            warning_text = "; ".join(warnings)
            raise RootServiceError(
                "; ".join(
                    [
                        "Manifest metadata differences detected",
                        warning_text,
                        "Re-run with --force to publish anyway.",
                    ]
                )
            )

        if dry_run:
            timestamp = _current_timestamp()
            return ArtifactPublishResult(
                kind=kind.rstrip("s"),
                name=name,
                source_path=source,
                target_path=target,
                version=new_version,
                previous_version=previous_version,
                updated_at=timestamp,
                dry_run=True,
                warnings=tuple(warnings),
            )

        scaffold = scaffold_skill_create if kind == "skills" else scaffold_scenario_create
        created = False
        manifest_meta: dict[str, str] | None = None
        staged: Path | None = None
        try:
            if target.exists():
                staged = target.parent / f".{target.name}.publish-{os.getpid()}-{uuid4().hex}"
                shutil.copytree(
                    source,
                    staged,
                    ignore=shutil.ignore_patterns(*_SKIP_DIRS, *_SKIP_FILES, "*.pyc", "*.pyo"),
                )
                manifest_meta = self._update_manifest(
                    kind,
                    staged,
                    name,
                    None,
                    version_bump_index=None,
                    set_prototype=False,
                    explicit_version=new_version,
                )
                self._validate_artifact_preflight(kind, name, staged)
                _replace_directory_transactionally(staged, target)
                staged = None
            else:
                scaffold(name, template=str(source), version=new_version, register=True, push=False)
                created = True
                manifest_meta = self._update_manifest(
                    kind,
                    target,
                    name,
                    None,
                    version_bump_index=None,
                    set_prototype=False,
                    explicit_version=new_version,
                )
        except Exception:
            if staged and staged.exists():
                shutil.rmtree(staged, ignore_errors=True)
            if created and target.exists():
                try:
                    shutil.rmtree(target)
                except OSError:
                    pass
            raise

        updated_at = (manifest_meta or {}).get("updated_at") or _current_timestamp()
        try:
            upsert_workspace_registry_entry(
                self.ctx.paths.workspace_dir(),
                kind,
                target,
                version=new_version,
                updated_at=updated_at,
            )
        except Exception as exc:
            raise RootServiceError(f"Failed to update workspace registry metadata for {kind[:-1]} '{name}'") from exc

        try:
            from adaos.services.root_mcp.registry import record_descriptor_refresh

            descriptor_ids = [
                "descriptor_build_profile",
                "descriptor_bundle",
                "architecture_catalog",
                "public_skill_registry_summary" if kind == "skills" else "public_scenario_registry_summary",
            ]
            record_descriptor_refresh(
                reason=f"publish_{kind[:-1]}",
                descriptor_ids=descriptor_ids,
                source_kind="workspace_registry_publish",
                artifact_kind=kind.rstrip("s"),
                artifact_name=name,
            )
        except Exception:
            pass

        return ArtifactPublishResult(
            kind=kind.rstrip("s"),
            name=name,
            source_path=source,
            target_path=target,
            version=new_version,
            previous_version=previous_version,
            updated_at=updated_at,
            dry_run=False,
            warnings=tuple(warnings),
        )

    def _validate_artifact_preflight(
        self,
        kind: Literal["skills", "scenarios"],
        name: str,
        source: Path,
    ) -> None:
        if kind == "skills":
            report = SkillValidationService(self.ctx).validate_path(
                source,
                name=name,
                strict=False,
                probe_tools=True,
            )
            issues = list(report.issues)
            ok = report.ok
        else:
            report = validate_scenario_path(
                source,
                dependency_roots=(source.parent.parent / "skills", self.ctx.paths.skills_dir()),
            )
            issues = list(report.issues)
            ok = report.ok

        warnings = [issue for issue in issues if getattr(issue, "level", "") == "warning"]
        for issue in warnings:
            logger.warning(
                "%s preflight warning for %s '%s': %s",
                kind[:-1],
                kind[:-1],
                name,
                getattr(issue, "message", issue),
            )
        if ok:
            return
        errors = [
            f"{getattr(issue, 'code', 'validation.error')}: {getattr(issue, 'message', issue)}"
            for issue in issues
            if getattr(issue, "level", "error") == "error"
        ]
        detail = "; ".join(errors[:8]) or "unknown validation error"
        raise RootServiceError(f"Validation failed for {kind[:-1]} '{name}': {detail}")


__all__ = [
    "RootAuthService",
    "OwnerHubsService",
    "PkiService",
    "RootAuthError",
    "RootDeveloperService",
    "RootServiceError",
    "TemplateResolutionError",
    "ArtifactNotFoundError",
    "DeviceAuthorization",
    "RootInitResult",
    "RootLoginResult",
    "ArtifactCreateResult",
    "ArtifactPushResult",
    "ArtifactDeleteResult",
    "ArtifactListItem",
    "ArtifactPublishResult",
    "ArtifactUpdateResult",
    "assert_safe_name",
    "create_zip_bytes",
    "archive_bytes_to_b64",
    "fingerprint_for_key",
]
