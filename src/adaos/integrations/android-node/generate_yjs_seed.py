"""Regenerate the packaged Android web_desktop Yjs update.

Run this with the repository's Python 3.11 environment after changing the
Android bundle descriptors. The generated base64 file is committed so the APK
does not need a native y-py wheel merely to load its immutable initial state.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
from pathlib import Path

import y_py as Y


ROOT = Path(__file__).resolve().parent
BOOTSTRAP = ROOT / "app/src/main/python/adaos/android/bootstrap.py"
OUTPUT = ROOT / "app/src/main/python/adaos/android/bundle/web_desktop.seed.yjs.b64"
SEED_CLIENT_ID = 0xADA05


def load_bootstrap():
    spec = importlib.util.spec_from_file_location("adaos_android_seed_builder", BOOTSTRAP)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {BOOTSTRAP}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def set_map_value(target, transaction, key: str, value) -> None:
    if isinstance(value, dict):
        child = Y.YMap({})
        target.set(transaction, key, child)
        for child_key in sorted(value):
            set_map_value(child, transaction, str(child_key), value[child_key])
        return
    if isinstance(value, list):
        child = Y.YArray()
        target.set(transaction, key, child)
        for item in value:
            append_array_value(child, transaction, item)
        return
    target.set(transaction, key, value)


def append_array_value(target, transaction, value) -> None:
    if isinstance(value, dict):
        child = Y.YMap({})
        target.append(transaction, child)
        for child_key in sorted(value):
            set_map_value(child, transaction, str(child_key), value[child_key])
        return
    if isinstance(value, list):
        child = Y.YArray()
        target.append(transaction, child)
        for item in value:
            append_array_value(child, transaction, item)
        return
    target.append(transaction, value)


def main() -> None:
    snapshot = load_bootstrap()._build_desktop_snapshot()
    # A fixed client id and explicit shared types keep the committed seed
    # byte-for-byte reproducible. JSON maps encoded as scalar values are backed
    # by hash maps in yrs and therefore do not have deterministic byte order.
    document = Y.YDoc(client_id=SEED_CLIENT_ID)
    with document.begin_transaction() as transaction:
        for root_name in ("ui", "data", "registry", "runtime"):
            root = document.get_map(root_name)
            values = snapshot.get(root_name, {})
            for key in sorted(values):
                set_map_value(root, transaction, key, values[key])
    update = bytes(Y.encode_state_as_update(document))
    OUTPUT.write_text(base64.b64encode(update).decode("ascii") + "\n", encoding="ascii")
    print(
        f"wrote {OUTPUT.relative_to(ROOT)}: {len(update)} bytes, "
        f"sha256={hashlib.sha256(update).hexdigest()}"
    )


if __name__ == "__main__":
    main()
