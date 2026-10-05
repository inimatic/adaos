from __future__ import annotations

import base64
import binascii
import hmac
import json
from pathlib import Path
from typing import Any, Mapping

from adaos.services.agent_context import get_ctx
from adaos.services.applications.trusted_metadata import MetadataSigner


_CURSOR_SIGNERS: dict[str, MetadataSigner] = {}
_FALLBACK_SIGNER: MetadataSigner | None = None


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    import hashlib

    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _signer() -> MetadataSigner:
    global _FALLBACK_SIGNER
    try:
        key_root = Path(get_ctx().paths.root_mcp_state_dir()) / "cursor-integrity"
        cache_key = str(key_root.resolve())
        signer = _CURSOR_SIGNERS.get(cache_key)
        if signer is None:
            signer = MetadataSigner.load_or_create(key_root, "root")
            _CURSOR_SIGNERS[cache_key] = signer
        return signer
    except Exception:
        # Isolated schema/unit-test contexts may not have an AgentContext.  A
        # process-local signing key still prevents cursor tampering; cursors
        # intentionally become invalid when that isolated process exits.
        if _FALLBACK_SIGNER is None:
            _FALLBACK_SIGNER = MetadataSigner.generate("root")
        return _FALLBACK_SIGNER


def encode_opaque_cursor(
    *,
    namespace: str,
    offset: int,
    query: Any,
    field_mask: Any,
    content_digest: str,
) -> str:
    """Issue a signed cursor bound to selection, projection, and source data."""

    statement = {
        "v": 1,
        "namespace": str(namespace or "").strip(),
        "offset": max(0, int(offset)),
        "query_digest": _canonical_digest(query),
        "field_mask_digest": _canonical_digest(field_mask),
        "content_digest": str(content_digest or "").strip(),
    }
    signature = _signer().signature(statement)["signature_b64"]
    envelope = json.dumps(
        {"statement": statement, "signature": signature},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(envelope).decode("ascii").rstrip("=")


def decode_opaque_cursor(
    cursor: str | None,
    *,
    namespace: str,
    query: Any,
    field_mask: Any,
    content_digest: str | None,
) -> int:
    """Validate an opaque cursor and return its offset.

    Reusing a cursor with another query, consumer field mask, or descriptor
    generation is rejected before pagination can expose a mixed snapshot.
    A None digest verifies signature/query/mask before the source read; that
    caller MUST repeat verification with the actual digest before returning data.
    """

    token = str(cursor or "").strip()
    if not token:
        return 0
    try:
        padded = token + "=" * (-len(token) % 4)
        envelope = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        statement = envelope["statement"]
        if not isinstance(statement, Mapping):
            raise TypeError("cursor statement must be an object")
        offset = int(statement["offset"])
        signature = str(envelope["signature"])
    except (
        binascii.Error,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("invalid opaque cursor") from exc

    expected = {
        "v": 1,
        "namespace": str(namespace or "").strip(),
        "offset": offset,
        "query_digest": _canonical_digest(query),
        "field_mask_digest": _canonical_digest(field_mask),
        "content_digest": (
            str(statement.get("content_digest") or "")
            if content_digest is None else str(content_digest or "").strip()
        ),
    }
    expected_signature = _signer().signature(expected)["signature_b64"]
    if (
        offset < 0
        or dict(statement) != expected
        or not hmac.compare_digest(signature, expected_signature)
    ):
        raise ValueError("opaque cursor does not match this query, field mask, or digest")
    return offset


__all__ = ["decode_opaque_cursor", "encode_opaque_cursor"]
