from __future__ import annotations

from typing import Any, Mapping, Sequence


PUBLIC_GRANT_SCHEMA = "adaos.public_grant.v1"

READ_ONLY_CAPABILITIES = ("read", "preview", "download")
FOLDER_READ_ONLY_CAPABILITIES = ("read", "list", "preview", "download")


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_public_capabilities(value: Any, *, resource_kind: str = "file") -> tuple[str, ...]:
    resource = _text(resource_kind).lower()
    allowed = set(FOLDER_READ_ONLY_CAPABILITIES if resource == "folder" else READ_ONLY_CAPABILITIES)
    if isinstance(value, str):
        raw_items: Sequence[Any] = [item.strip() for item in value.split(",")]
    elif isinstance(value, Sequence):
        raw_items = value
    else:
        raw_items = FOLDER_READ_ONLY_CAPABILITIES if resource == "folder" else READ_ONLY_CAPABILITIES
    out: list[str] = []
    for item in raw_items:
        token = _text(item).lower().replace("_", "-")
        if token in allowed and token not in out:
            out.append(token)
    if "read" not in out:
        out.insert(0, "read")
    if resource == "folder" and not any(cap in out for cap in ("list", "download", "preview")):
        out.extend(["list", "preview", "download"])
    return tuple(out)


def public_grant_descriptor(
    *,
    grant_kind: str,
    face_id: str,
    resource_kind: str,
    resource_name: str,
    capabilities: Any = None,
    readonly: bool = True,
    status: str = "active",
    expires_at: Any = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    kind = _text(resource_kind).lower() or "file"
    caps = normalize_public_capabilities(capabilities, resource_kind=kind)
    descriptor: dict[str, Any] = {
        "schema": PUBLIC_GRANT_SCHEMA,
        "grant_kind": _text(grant_kind) or "resource",
        "face_id": _text(face_id),
        "resource": {
            "kind": kind,
            "name": _text(resource_name),
        },
        "capabilities": list(caps),
        "readonly": bool(readonly),
        "status": _text(status).lower() or "active",
        "expires_at": expires_at,
    }
    if metadata:
        descriptor["metadata"] = dict(metadata)
    return descriptor


__all__ = [
    "FOLDER_READ_ONLY_CAPABILITIES",
    "PUBLIC_GRANT_SCHEMA",
    "READ_ONLY_CAPABILITIES",
    "normalize_public_capabilities",
    "public_grant_descriptor",
]
