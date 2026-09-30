#!/usr/bin/env python3
"""Install AdaOS' published, hash-pinned y-py wheel for this interpreter."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


VERSION = "0.6.2+adaos.1"
RELEASE_TAG = "y-py-v0.6.2-adaos.1"
DEFAULT_RELEASE_BASE = (
    f"https://github.com/inimatic/adaos/releases/download/{RELEASE_TAG}"
)
WHEELS = {
    ("linux", "x86_64"): (
        "y_py-0.6.2+adaos.1-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
        "62d5f04a97a35786dd8abed06e662cf8b0fc5ffc1d74f35dd195907852105a1c",
    ),
    ("linux", "aarch64"): (
        "y_py-0.6.2+adaos.1-cp311-cp311-manylinux_2_17_aarch64.manylinux2014_aarch64.whl",
        "0895cf0e958c3243843c721680bd87e6c7af07e78e0f9676289833c9a6cf2ac3",
    ),
    ("win32", "amd64"): (
        "y_py-0.6.2+adaos.1-cp311-cp311-win_amd64.whl",
        "f38974c3b8b2e6fb6a560faa48714a943d3e5ca1cad023ab76acabdf8cdcdc40",
    ),
    ("darwin", "arm64"): (
        "y_py-0.6.2+adaos.1-cp311-cp311-macosx_11_0_arm64.whl",
        "049abeb28943f21caea9b84b5e732e954e509feb40b2705fdd7f19c19ee85247",
    ),
    ("darwin", "x86_64"): (
        "y_py-0.6.2+adaos.1-cp311-cp311-macosx_10_15_x86_64.whl",
        "08dda6bae793a561456e4b6172b2a2ba461555e10ce61347a99442f7037e24d0",
    ),
}
MAX_WHEEL_BYTES = 64 * 1024 * 1024


def _machine() -> str:
    value = platform.machine().strip().lower()
    aliases = {"amd64": "amd64", "x64": "amd64", "arm64": "arm64"}
    return aliases.get(value, value)


def _installed() -> bool:
    try:
        return importlib.metadata.version("y-py") == VERSION
    except importlib.metadata.PackageNotFoundError:
        return False


def _download(target: Path, *, filename: str, expected_sha256: str) -> None:
    base = str(
        os.getenv("ADAOS_Y_PY_WHEEL_RELEASE_BASE") or DEFAULT_RELEASE_BASE
    ).rstrip("/")
    url = f"{base}/{urllib.parse.quote(filename, safe='._-')}"
    request = urllib.request.Request(url, headers={"User-Agent": "AdaOS-bootstrap/1"})
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as output:
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_WHEEL_BYTES:
            raise RuntimeError("patched y-py wheel exceeds download size limit")
        downloaded = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            downloaded += len(chunk)
            if downloaded > MAX_WHEEL_BYTES:
                raise RuntimeError("patched y-py wheel exceeds download size limit")
            digest.update(chunk)
            output.write(chunk)
    observed = digest.hexdigest()
    if observed != expected_sha256:
        target.unlink(missing_ok=True)
        raise RuntimeError(
            f"patched y-py wheel digest mismatch: expected {expected_sha256}, got {observed}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", choices=("pip", "uv"), required=True)
    parser.add_argument("--wheelhouse", default="")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("patched y-py wheels require CPython 3.11")
    already_installed = _installed()
    if already_installed and not str(args.wheelhouse).strip():
        print(f"patched y-py {VERSION} already installed")
        return 0
    key = (sys.platform, _machine())
    selected = WHEELS.get(key)
    if selected is None:
        supported = ", ".join(f"{system}/{machine}" for system, machine in sorted(WHEELS))
        raise RuntimeError(
            f"no published patched y-py wheel for {key[0]}/{key[1]}; supported: {supported}"
        )
    filename, digest = selected
    temporary_root = (
        Path(args.wheelhouse).expanduser().resolve()
        if str(args.wheelhouse).strip()
        else Path(tempfile.mkdtemp(prefix="adaos-y-py-"))
    )
    temporary_root.mkdir(parents=True, exist_ok=True)
    ephemeral = not str(args.wheelhouse).strip()
    try:
        wheel = temporary_root / filename
        if not wheel.is_file() or hashlib.sha256(wheel.read_bytes()).hexdigest() != digest:
            _download(wheel, filename=filename, expected_sha256=digest)
        if already_installed:
            print(f"patched y-py {VERSION} already installed; wheelhouse verified")
            return 0
        if args.installer == "pip":
            command = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--only-binary",
                ":all:",
                str(wheel),
            ]
        else:
            uv = shutil.which("uv")
            if not uv:
                raise RuntimeError("uv installer was requested but uv is not available")
            command = [
                uv,
                "pip",
                "install",
                "--python",
                sys.executable,
                "--no-deps",
                "--only-binary",
                ":all:",
                str(wheel),
            ]
        subprocess.run(command, check=True)
    finally:
        if ephemeral:
            shutil.rmtree(temporary_root, ignore_errors=True)
    if not _installed():
        raise RuntimeError(f"patched y-py {VERSION} installation did not converge")
    print(f"installed patched y-py {VERSION} from hash-pinned release wheel")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
