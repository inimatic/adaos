"""Serve an isolated, no-provider Retouch fixture for the real Client browser."""
import argparse
import json
from pathlib import Path
import secrets
import sys

import uvicorn
from adaos.e2e.retouch_fixture import RetouchFixture, fixture_app

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--owned-root", type=Path, required=True)
parser.add_argument("--source", type=Path, required=True)
parser.add_argument("--port", type=int, default=18879)
args = parser.parse_args()
root = args.owned_root.resolve()
if not root.is_relative_to((Path.cwd() / ".tmp").resolve()) or root == (Path.cwd() / ".tmp").resolve():
    parser.error("An explicit owned fixture directory under repository .tmp is required")
root.mkdir(parents=True, exist_ok=True)
token_path = root / "fixture-token"
if not token_path.exists(): token_path.write_text(secrets.token_hex(24), encoding="utf-8")
def no_external_connections(event, arguments):
    if event != "socket.connect": return
    address = arguments[1]
    if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
        raise PermissionError("External network is forbidden in the Retouch browser fixture")

sys.addaudithook(no_external_connections)
fixture = RetouchFixture(root, args.source, token_path.read_text(encoding="utf-8"))
fixture.install_blob_boundary()
(root / "fixture-endpoint.json").write_text(json.dumps({"base_url": f"http://127.0.0.1:{args.port}",
    "token_file": str(token_path), "source_digest": fixture.source_digest,
    "scope": "synthetic fixture only; never a provider-quality receipt"}, indent=2), encoding="utf-8")
uvicorn.run(fixture_app(fixture), host="127.0.0.1", port=args.port, log_level="warning")
