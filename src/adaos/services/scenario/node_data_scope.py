from __future__ import annotations


_RESERVED_DATA_ROOTS = {
    "adaos_connect",
    "catalog",
    "desktop",
    "installed",
    "nlu",
    "nlu_teacher",
    "routing",
    "scenarios",
    "tts",
    "webio",
    "webspaces",
}


def node_scope_data_path(path: str | None, node_id: str | None) -> str:
    """
    Return a browser/Yjs data path isolated under ``data/nodes/<node_id>``.

    The desktop keeps runtime-owned branches such as ``data.catalog`` and
    ``data.desktop`` shared. Skill-owned state can use the same local path on
    each node while the shared webspace sees a node-scoped envelope.
    """

    raw = str(path or "").strip()
    node = str(node_id or "").strip()
    if not raw or not node:
        return raw

    prefix = ""
    body = raw
    if body.startswith("y:"):
        prefix = "y:"
        body = body[2:]

    parts = [part for part in body.split("/") if part]
    if len(parts) < 2 or parts[0] != "data":
        return raw
    if parts[1] == "nodes" or parts[1] in _RESERVED_DATA_ROOTS:
        return raw

    return f"{prefix}data/nodes/{node}/{'/'.join(parts[1:])}"


def is_node_scoped_data_path(path: str | None) -> bool:
    raw = str(path or "").strip()
    if raw.startswith("y:"):
        raw = raw[2:]
    parts = [part for part in raw.split("/") if part]
    return len(parts) >= 3 and parts[0] == "data" and parts[1] == "nodes"


def local_unscoped_data_path(path: str | None, node_id: str | None) -> str:
    """
    Return the local-node view of a shared node-scoped ``data`` path.

    Member nodes often project skill state into their local Yjs doc under
    ``data/<skill>/...`` while the shared desktop exposes the same state as
    ``data/nodes/<node_id>/<skill>/...``. This helper converts the shared path
    back to the local path for the matching node id so snapshot builders can
    read current local values before publishing them to the hub.
    """

    raw = str(path or "").strip()
    node = str(node_id or "").strip()
    if not raw or not node:
        return raw

    prefix = ""
    body = raw
    if body.startswith("y:"):
        prefix = "y:"
        body = body[2:]

    parts = [part for part in body.split("/") if part]
    if len(parts) < 4 or parts[0] != "data" or parts[1] != "nodes" or parts[2] != node:
        return raw

    return f"{prefix}data/{'/'.join(parts[3:])}"


__all__ = ["is_node_scoped_data_path", "local_unscoped_data_path", "node_scope_data_path"]
