"""Regenerate the packaged Android web_desktop Yjs update.

Run this with the repository's Python 3.11 environment after changing the
Android bundle descriptors. The generated base64 file is committed so the APK
does not need a native y-py wheel merely to load its immutable initial state.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import importlib.util
import sys
from pathlib import Path

import y_py as Y


ROOT = Path(__file__).resolve().parent
BOOTSTRAP = ROOT / "app/src/main/python/adaos/android/bootstrap.py"
PYTHON_ROOT = BOOTSTRAP.parents[2]
PORTABLE_RASA = ROOT.parents[1] / "services/nlu/portable_rasa.py"
OUTPUT = ROOT / "app/src/main/python/adaos/android/bundle/web_desktop.seed.yjs.b64"
SEED_CLIENT_ID = 0xADA05


def load_bootstrap():
    root = str(PYTHON_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    # Gradle copies this shared module into its generated Python source tree.
    # The host-side seed generator runs before that task, so load the exact
    # canonical source under the packaged module name instead of maintaining a
    # second Android copy.
    if "adaos.services.nlu.portable_rasa" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "adaos.services.nlu.portable_rasa", PORTABLE_RASA
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("portable_rasa_import_failed")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return importlib.import_module("adaos.android.bootstrap")


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
