from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Protocol

from adaos.domain.distributed_runtime import TransferRecord, utc_now

from .store import DistributedRuntimeStore


class TransferTransportError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TransferChunk:
    payload: bytes
    checkpoint: str
    eof: bool
    content_witness: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.payload, bytes):
            raise TransferTransportError("transfer chunk payload must be bytes")
        if len(self.payload) > 4 * 1024 * 1024:
            raise TransferTransportError("transfer chunk exceeds 4 MiB")
        if not str(self.checkpoint or "").strip():
            raise TransferTransportError("transfer chunk checkpoint is required")


class AuthenticatedTransferSource(Protocol):
    def authorize(self, *, auth_scope: str, operation_id: str) -> bool: ...

    def read(
        self, *, checkpoint: str | None, max_bytes: int, cancelled: Callable[[], bool]
    ) -> TransferChunk: ...


@dataclass(slots=True)
class BoundedTransferController:
    store: DistributedRuntimeStore
    max_chunk_bytes: int = 1024 * 1024
    pressure_probe: Callable[[], float] | None = None
    pressure_limit: float = 0.9

    def pump(
        self,
        transfer_id: str,
        *,
        source: AuthenticatedTransferSource,
        auth_scope: str,
        cancelled: Callable[[], bool] = lambda: False,
        max_chunks: int = 16,
    ) -> TransferRecord:
        transfer = self.store.get_transfer(transfer_id)
        if transfer.state not in {"preparing", "transferring"}:
            raise TransferTransportError("transfer is not resumable")
        if not source.authorize(
            auth_scope=auth_scope, operation_id=transfer.operation_id
        ):
            raise TransferTransportError("transfer authorization denied")
        current = replace(transfer, state="transferring", updated_at=utc_now())
        self.store.put_transfer(current)
        for _ in range(max(1, min(int(max_chunks), 100))):
            if cancelled():
                return current
            if (
                self.pressure_probe is not None
                and self.pressure_probe() > self.pressure_limit
            ):
                return current
            chunk = source.read(
                checkpoint=current.checkpoint,
                max_bytes=max(1, min(int(self.max_chunk_bytes), 4 * 1024 * 1024)),
                cancelled=cancelled,
            )
            current = replace(
                current,
                checkpoint=chunk.checkpoint,
                byte_count=current.byte_count + len(chunk.payload),
                item_count=current.item_count + 1,
                resume_token_ref=f"transfer:{current.transfer_id}:{chunk.checkpoint}",
                state="verifying" if chunk.eof else "transferring",
                updated_at=utc_now(),
            )
            self.store.put_transfer(current)
            if chunk.eof:
                if not chunk.content_witness:
                    raise TransferTransportError(
                        "completed transfer requires content witness"
                    )
                if chunk.content_witness != current.manifest_digest:
                    raise TransferTransportError("transfer content witness mismatch")
                current = replace(current, state="complete", updated_at=utc_now())
                self.store.put_transfer(current)
                return current
        return current


__all__ = [
    "AuthenticatedTransferSource",
    "BoundedTransferController",
    "TransferChunk",
    "TransferTransportError",
]
