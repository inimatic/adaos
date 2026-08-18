"""Owner-isolated blob binding broker with process-only service locations."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any, Mapping
from urllib.parse import urlsplit

from adaos.domain.blob_storage import BlobStorageBinding, BlobStorageRequirements
from adaos.domain.ownership import validate_owner_ref
from adaos.services.artifact_pipeline.storage import atomic_write_bytes
from adaos.services.skill.data_paths import resolve_skill_data_root


def _binding_id(
    provider_id: str,
    owner_ref: str,
    logical_name: str,
    *,
    scope_identity: str | None = None,
) -> str:
    identity = {
        "provider_id": provider_id,
        "owner_ref": owner_ref,
        "logical_name": logical_name,
    }
    if scope_identity:
        identity["scope_identity"] = scope_identity
    payload = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"blobbind.{hashlib.sha256(payload).hexdigest()}"


def _logical_name(value: str) -> str:
    result = str(value or "").strip().lower()
    if not result or not result.replace("_", "").replace("-", "").isalnum() or len(result) > 64:
        raise ValueError("blob logical name is invalid")
    return result


class LocalBlobStorageProvider:
    provider_id = "filesystem"

    def __init__(self) -> None:
        self._targets: dict[str, Path] = {}
        self._lock = RLock()

    def supports(self, requirements: BlobStorageRequirements) -> bool:
        return requirements.locality in {"node", "any"}

    def bind(
        self,
        *,
        owner_ref: str,
        logical_name: str,
        requirements: BlobStorageRequirements,
        scope_root: Path,
    ) -> BlobStorageBinding:
        owner = validate_owner_ref(owner_ref)
        logical = _logical_name(logical_name)
        if not self.supports(requirements):
            raise ValueError("filesystem blob provider cannot satisfy network locality")
        root = Path(scope_root).expanduser().resolve()
        target = (root / logical).resolve()
        target.relative_to(root)
        target.mkdir(parents=True, exist_ok=True)
        binding_id = _binding_id(
            self.provider_id,
            owner,
            logical,
            scope_identity=os.path.normcase(str(root)),
        )
        with self._lock:
            self._targets[binding_id] = target
        return BlobStorageBinding(
            binding_id=binding_id,
            provider_id=self.provider_id,
            owner_ref=owner,
            locator=f"skill-data:files/{logical}",
        )

    def service_uri(self, binding: BlobStorageBinding, *, owner_ref: str) -> str:
        if binding.owner_ref != validate_owner_ref(owner_ref):
            raise ValueError("blob binding belongs to another owner")
        with self._lock:
            target = self._targets[binding.binding_id]
        return target.as_uri()

    def put_bytes(
        self,
        binding: BlobStorageBinding,
        *,
        owner_ref: str,
        name: str,
        data: bytes,
        media_type: str,
    ) -> dict[str, Any]:
        if binding.owner_ref != validate_owner_ref(owner_ref):
            raise ValueError("blob binding belongs to another owner")
        suffix = Path(str(name or "")).suffix.lower()
        if not suffix or len(suffix) > 16 or not suffix[1:].isalnum():
            suffix = ".bin"
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        key = f"objects/{digest.removeprefix('sha256:')[:2]}/{digest.removeprefix('sha256:')}{suffix}"
        with self._lock:
            root = self._targets[binding.binding_id]
        target = (root / key).resolve()
        target.relative_to(root)
        if target.is_file():
            if target.read_bytes() != data:
                raise ValueError("content-addressed blob path contains different bytes")
        else:
            atomic_write_bytes(target, data)
        return {
            "schema": "adaos.storage.blob.object.v1",
            "binding_id": binding.binding_id,
            "provider_id": binding.provider_id,
            "owner_ref": binding.owner_ref,
            "ref": f"adaos-blob:{binding.binding_id}:{digest}",
            "key": key,
            "digest": digest,
            "size_bytes": len(data),
            "media_type": str(media_type or "application/octet-stream"),
        }

    def materialize_path(
        self,
        binding: BlobStorageBinding,
        blob: Mapping[str, Any],
        *,
        owner_ref: str,
    ) -> Path:
        if binding.owner_ref != validate_owner_ref(owner_ref):
            raise ValueError("blob binding belongs to another owner")
        if str(blob.get("binding_id") or "") != binding.binding_id:
            raise ValueError("blob object belongs to another binding")
        with self._lock:
            root = self._targets[binding.binding_id]
        target = (root / str(blob.get("key") or "")).resolve()
        target.relative_to(root)
        if not target.is_file():
            raise FileNotFoundError("blob object is unavailable")
        digest = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != str(blob.get("digest") or ""):
            raise ValueError("blob object digest verification failed")
        return target


class ProvisionedBlobStorageProvider:
    provider_id = "object"

    def __init__(self, base_uri: str, *, secret_ref: str = "core:storage/blob") -> None:
        value = str(base_uri or "").strip().rstrip("/")
        parsed = urlsplit(value)
        if parsed.scheme not in {"s3", "gs", "wasbs", "abfss"} or not parsed.netloc:
            raise ValueError("provisioned blob URI must use a supported object-storage scheme")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("provisioned blob URI must not contain credentials, query, or fragment")
        self._base_uri = value
        self._secret_ref = secret_ref
        self._targets: dict[str, str] = {}

    def supports(self, requirements: BlobStorageRequirements) -> bool:
        return requirements.locality in {"network", "any"} and requirements.durability == "durable"

    def bind(
        self,
        *,
        owner_ref: str,
        logical_name: str,
        requirements: BlobStorageRequirements,
        scope_root: Path,
    ) -> BlobStorageBinding:
        del scope_root
        owner = validate_owner_ref(owner_ref)
        logical = _logical_name(logical_name)
        if not self.supports(requirements):
            raise ValueError("object blob provider cannot satisfy the requested binding")
        binding_id = _binding_id(self.provider_id, owner, logical)
        suffix = hashlib.sha256(f"{owner}\0{logical}".encode("utf-8")).hexdigest()[:32]
        self._targets[binding_id] = f"{self._base_uri}/{suffix}"
        return BlobStorageBinding(
            binding_id=binding_id,
            provider_id=self.provider_id,
            owner_ref=owner,
            locator=f"adaos-blob:{binding_id}",
            secret_ref=self._secret_ref,
        )

    def service_uri(self, binding: BlobStorageBinding, *, owner_ref: str) -> str:
        if binding.owner_ref != validate_owner_ref(owner_ref):
            raise ValueError("blob binding belongs to another owner")
        return self._targets[binding.binding_id]


class BlobStorageBroker:
    def __init__(self, providers) -> None:  # noqa: ANN001
        self._providers = tuple(providers)
        self._by_id = {provider.provider_id: provider for provider in self._providers}

    def bind(
        self,
        *,
        owner_ref: str,
        logical_name: str,
        requirements: BlobStorageRequirements,
        scope_root: Path,
        prefer_provisioned: bool = False,
    ) -> BlobStorageBinding:
        ordered = sorted(
            self._providers,
            key=lambda provider: (provider.provider_id != "object") if prefer_provisioned else (provider.provider_id != "filesystem"),
        )
        for provider in ordered:
            if provider.supports(requirements):
                return provider.bind(
                    owner_ref=owner_ref,
                    logical_name=logical_name,
                    requirements=requirements,
                    scope_root=scope_root,
                )
        raise ValueError("no blob provider satisfies the requested capability")

    def service_uri(self, binding: BlobStorageBinding, *, owner_ref: str) -> str:
        provider = self._by_id.get(binding.provider_id)
        if provider is None:
            raise ValueError("blob binding provider is unavailable")
        return provider.service_uri(binding, owner_ref=owner_ref)

    def put_bytes(
        self,
        binding: BlobStorageBinding,
        *,
        owner_ref: str,
        name: str,
        data: bytes,
        media_type: str,
    ) -> dict[str, Any]:
        provider = self._by_id.get(binding.provider_id)
        operation = getattr(provider, "put_bytes", None)
        if not callable(operation):
            raise NotImplementedError(f"blob provider {binding.provider_id!r} has no byte-write adapter")
        return operation(binding, owner_ref=owner_ref, name=name, data=data, media_type=media_type)

    def materialize_path(
        self,
        binding: BlobStorageBinding,
        blob: Mapping[str, Any],
        *,
        owner_ref: str,
    ) -> Path:
        provider = self._by_id.get(binding.provider_id)
        operation = getattr(provider, "materialize_path", None)
        if not callable(operation):
            raise NotImplementedError(f"blob provider {binding.provider_id!r} has no local materialization adapter")
        return operation(binding, blob, owner_ref=owner_ref)


class BlobStore:
    def __init__(self, service: "BlobStorageService", binding: BlobStorageBinding) -> None:
        self._service = service
        self.binding = binding

    def put_bytes(self, name: str, data: bytes, *, media_type: str = "application/octet-stream") -> dict[str, Any]:
        return self._service.put_bytes(self.binding, name=name, data=data, media_type=media_type)

    def put_json(self, name: str, value: Mapping[str, Any]) -> dict[str, Any]:
        payload = json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return self.put_bytes(name, payload, media_type="application/json")

    def materialize_path(self, blob: Mapping[str, Any]) -> Path:
        return self._service.materialize_path(self.binding, blob)


class BlobStorageService:
    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx
        self._broker = get_blob_storage_broker(ctx)

    def _current_owner(self) -> tuple[str, Any]:
        skill_ctx = getattr(self._ctx, "skill_ctx", None)
        current = skill_ctx.get() if skill_ctx is not None else None
        skill_name = str(getattr(current, "name", "") or "").strip()
        if not skill_name:
            raise ValueError("storage.blob SDK access requires an active skill context")
        return validate_owner_ref(f"skill:{skill_name}"), current

    def acquire_for_current_skill(
        self,
        logical_name: str = "artifacts",
        *,
        requirements: BlobStorageRequirements | None = None,
    ) -> BlobStore:
        owner_ref, current = self._current_owner()
        normalized = requirements or BlobStorageRequirements()
        data_root = resolve_skill_data_root(self._ctx, current)
        binding = self._broker.bind(
            owner_ref=owner_ref,
            logical_name=logical_name,
            requirements=normalized,
            scope_root=data_root / "files",
        )
        return BlobStore(self, binding)

    def _owner(self, binding: BlobStorageBinding) -> str:
        owner_ref, _ = self._current_owner()
        if binding.owner_ref != owner_ref:
            raise ValueError("blob store belongs to another active skill")
        return owner_ref

    def put_bytes(self, binding: BlobStorageBinding, *, name: str, data: bytes, media_type: str) -> dict[str, Any]:
        return self._broker.put_bytes(
            binding,
            owner_ref=self._owner(binding),
            name=name,
            data=bytes(data),
            media_type=media_type,
        )

    def materialize_path(self, binding: BlobStorageBinding, blob: Mapping[str, Any]) -> Path:
        return self._broker.materialize_path(binding, blob, owner_ref=self._owner(binding))


def build_default_blob_storage_broker() -> BlobStorageBroker:
    providers = [LocalBlobStorageProvider()]
    object_uri = str(os.getenv("ADAOS_BLOB_OBJECT_URI") or "").strip()
    if object_uri:
        providers.append(
            ProvisionedBlobStorageProvider(
                object_uri,
                secret_ref=str(os.getenv("ADAOS_BLOB_OBJECT_SECRET_REF") or "core:storage/blob"),
            )
        )
    return BlobStorageBroker(providers)


def get_blob_storage_broker(ctx) -> BlobStorageBroker:  # noqa: ANN001
    broker = getattr(ctx, "blob_storage", None)
    if broker is None:
        broker = build_default_blob_storage_broker()
        object.__setattr__(ctx, "blob_storage", broker)
    return broker


__all__ = [
    "BlobStorageBroker",
    "BlobStorageService",
    "BlobStore",
    "LocalBlobStorageProvider",
    "ProvisionedBlobStorageProvider",
    "build_default_blob_storage_broker",
    "get_blob_storage_broker",
]
