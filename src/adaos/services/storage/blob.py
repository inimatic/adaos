"""Owner-isolated blob binding broker with process-only service locations."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit

from adaos.domain.blob_storage import BlobStorageBinding, BlobStorageRequirements
from adaos.domain.ownership import validate_owner_ref


def _binding_id(provider_id: str, owner_ref: str, logical_name: str) -> str:
    payload = json.dumps(
        {"provider_id": provider_id, "owner_ref": owner_ref, "logical_name": logical_name},
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
        binding_id = _binding_id(self.provider_id, owner, logical)
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
    "LocalBlobStorageProvider",
    "ProvisionedBlobStorageProvider",
    "build_default_blob_storage_broker",
    "get_blob_storage_broker",
]
