import json
import os
import platform
import struct
import sys


def _probe_y_py():
    try:
        import y_py as Y

        source = Y.YDoc()
        with source.begin_transaction() as txn:
            runtime = source.get_map("runtime")
            runtime.set(
                txn,
                "probe",
                {
                    "ok": True,
                    "abi": platform.machine(),
                    "pointer_bits": struct.calcsize("P") * 8,
                },
            )
        update = bytes(Y.encode_state_as_update(source))
        state_vector = bytes(Y.encode_state_vector(source))
        target = Y.YDoc()
        Y.apply_update(target, update)
        return {
            "ok": True,
            "module": getattr(Y, "__file__", ""),
            "update_bytes": len(update),
            "state_vector_bytes": len(state_vector),
            "runtime": json.loads(target.get_map("runtime").to_json()),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def probe(java_facts=""):
    return json.dumps(
        {
            "ok": True,
            "python": sys.version,
            "implementation": platform.python_implementation(),
            "machine": platform.machine(),
            "platform": platform.platform(),
            "maxsize": sys.maxsize,
            "pointer_bits": struct.calcsize("P") * 8,
            "executable": sys.executable,
            "prefix": sys.prefix,
            "cwd": os.getcwd(),
            "y_py": _probe_y_py(),
            "java_facts": str(java_facts),
        },
        sort_keys=True,
        indent=2,
    )
