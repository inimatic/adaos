import json
import os
import platform
import struct
import sys


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
            "java_facts": str(java_facts),
        },
        sort_keys=True,
        indent=2,
    )
