"""Ports for provider-neutral relational capability resolution."""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from adaos.domain.relational_storage import (
    RelationalBackup,
    RelationalProviderCapabilities,
    RelationalStorageBinding,
    RelationalStorageRequirements,
)


class RelationalStorageProvider(Protocol):
    @property
    def capabilities(self) -> RelationalProviderCapabilities: ...

    def bind(
        self,
        *,
        owner_ref: str,
        logical_name: str,
        requirements: RelationalStorageRequirements,
        scope_root: Path,
    ) -> RelationalStorageBinding: ...

    def transaction(
        self,
        binding: RelationalStorageBinding,
        *,
        owner_ref: str,
    ) -> AbstractContextManager[Any]: ...

    def health(
        self,
        binding: RelationalStorageBinding,
        *,
        owner_ref: str,
    ) -> Mapping[str, Any]: ...

    def backup(
        self,
        binding: RelationalStorageBinding,
        *,
        owner_ref: str,
    ) -> RelationalBackup: ...

    def restore(
        self,
        binding: RelationalStorageBinding,
        backup: RelationalBackup,
        *,
        owner_ref: str,
    ) -> None: ...

    def delete(
        self,
        binding: RelationalStorageBinding,
        *,
        owner_ref: str,
        reason: str,
    ) -> None: ...

    def service_uri(
        self,
        binding: RelationalStorageBinding,
        *,
        owner_ref: str,
    ) -> str: ...


class RelationalStorageBrokerPort(Protocol):
    def bind(
        self,
        *,
        owner_ref: str,
        logical_name: str,
        requirements: RelationalStorageRequirements,
        scope_root: Path,
    ) -> RelationalStorageBinding: ...

    def provider_for(self, binding: RelationalStorageBinding) -> RelationalStorageProvider: ...

    def provider_profiles(self) -> Sequence[RelationalProviderCapabilities]: ...

    def service_uri(self, binding: RelationalStorageBinding, *, owner_ref: str) -> str: ...


__all__ = ["RelationalStorageBrokerPort", "RelationalStorageProvider"]
