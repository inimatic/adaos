"""Ports for provider-neutral relational capability resolution."""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from adaos.domain.relational_storage import (
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


__all__ = ["RelationalStorageBrokerPort", "RelationalStorageProvider"]
