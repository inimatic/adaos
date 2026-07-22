from __future__ import annotations

import importlib.metadata
import json
import subprocess
import sys
import textwrap


EXPECTED_Y_PY_VERSION = "0.6.2+adaos.1"


def test_adaos_y_py_fork_is_installed() -> None:
    assert importlib.metadata.version("y-py") == EXPECTED_Y_PY_VERSION


def test_dropped_ydocs_release_native_store_memory() -> None:
    probe = textwrap.dedent(
        """
        import gc
        import json

        import psutil
        import y_py as Y


        def private_bytes():
            info = psutil.Process().memory_full_info()
            for field in ("uss", "private", "rss"):
                value = getattr(info, field, None)
                if value is not None:
                    return int(value)
            raise RuntimeError("psutil returned no usable process memory field")


        source = Y.YDoc()
        root = source.get_map("root")
        with source.begin_transaction() as txn:
            root.set(txn, "payload", "x" * 2_000_000)
        update = Y.encode_state_as_update(source)
        del root, source


        def materialize_and_drop():
            target = Y.YDoc()
            Y.apply_update(target, update)
            target.get_map("root")
            del target


        for _ in range(6):
            materialize_and_drop()
        gc.collect()
        baseline = private_bytes()

        samples = []
        for index in range(24):
            materialize_and_drop()
            if index % 4 == 3:
                gc.collect()
                samples.append(private_bytes())

        print(json.dumps({
            "baseline": baseline,
            "final": samples[-1],
            "growth": samples[-1] - baseline,
            "samples": samples,
            "version": Y.__version__,
        }))
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", probe],
        check=True,
        capture_output=True,
        text=True,
        timeout=90,
    )
    result = json.loads(completed.stdout.strip().splitlines()[-1])

    assert result["version"] == EXPECTED_Y_PY_VERSION
    assert result["growth"] < 16 * 1024 * 1024, result
