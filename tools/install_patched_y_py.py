#!/usr/bin/env python3
"""Install or verify AdaOS' platform-specific, hash-pinned y-py wheel."""

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


WHEELS = {
    ("linux", "x86_64"): (
        "y_py-0.6.2+adaos.2-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
        "abf027ea01e5c6125b9e8c37cf411f21fb4a252f53656c6e388bfd1f4c8caaaa",
    ),
    ("linux", "aarch64"): (
        "y_py-0.6.2+adaos.2-cp311-cp311-manylinux2014_aarch64.manylinux_2_17_aarch64.whl",
        "42031208ef9dc2524532535830d28061849c5681c5e460af7f158e9b1502f45d",
    ),
    ("win32", "amd64"): (
        "y_py-0.6.2+adaos.2-cp311-cp311-win_amd64.whl",
        "52ca9134bc6333bddb6badaa71cb8f6ed72e505d850d10619d3dae6c6e792c37",
    ),
    # This platform retains its qualified legacy binary until a native ARM
    # runner qualifies the GC fix. Match the marker in pyproject.toml/uv.lock.
    ("darwin", "arm64"): (
        "y_py-0.6.2+adaos.1-cp311-cp311-macosx_11_0_arm64.whl",
        "049abeb28943f21caea9b84b5e732e954e509feb40b2705fdd7f19c19ee85247",
    ),
    ("darwin", "x86_64"): (
        "y_py-0.6.2+adaos.2-cp311-cp311-macosx_10_15_x86_64.whl",
        "ac4a7affc8d27975502f05955f9ff454de4f7273ace985126e7e3c1ccc433057",
    ),
}
MAX_WHEEL_BYTES = 64 * 1024 * 1024


def _machine() -> str:
    value = platform.machine().strip().lower()
    aliases = (
        {"x86_64": "amd64", "x64": "amd64"}
        if sys.platform == "win32"
        else {"amd64": "x86_64", "x64": "x86_64"}
    )
    if sys.platform == "linux":
        aliases["arm64"] = "aarch64"
    return aliases.get(value, value)


def _selected_wheel() -> tuple[str, str]:
    key = (sys.platform, _machine())
    selected = WHEELS.get(key)
    if selected is None:
        supported = ", ".join(f"{system}/{machine}" for system, machine in sorted(WHEELS))
        raise RuntimeError(
            f"no qualified patched y-py wheel for {key[0]}/{key[1]}; supported: {supported}"
        )
    return selected


def _installed(version: str) -> bool:
    try:
        return importlib.metadata.version("y-py") == version
    except importlib.metadata.PackageNotFoundError:
        return False


def _verify_installation(version: str) -> None:
    if not _installed(version):
        raise RuntimeError(f"this platform requires patched y-py=={version}")
    import y_py

    if version == "0.6.2+adaos.1":
        print("macOS ARM retains the legacy y-py binary; GC fix is not qualified")
    else:
        # Exercise the loaded native binary, not only its distribution metadata.
        # A stale .1 binary grows client 1's clock to 15 instead of 10 here.
        document = y_py.YDoc()
        y_py.apply_update(document, bytes.fromhex("0201020081010401010100000a00"))
        vector = iter(y_py.encode_state_vector(document))

        def read_varuint() -> int:
            value = shift = 0
            for byte in vector:
                value |= (byte & 0x7F) << shift
                if byte < 0x80:
                    return value
                shift += 7
            raise RuntimeError("invalid native y-py state vector")

        clocks = {}
        for _ in range(read_varuint()):
            client = read_varuint()
            clocks[client] = read_varuint()
        if clocks != {1: 10, 2: 1}:
            raise RuntimeError(f"native y-py clock integrity failed: {clocks}")
    native = getattr(y_py, "y_py", y_py)
    print(f"verified patched y-py {version}; native={native.__file__}")


def _download(target: Path, *, filename: str, expected_sha256: str) -> None:
    release_tag = "y-py-v" + filename.split("-")[1].replace("+", "-")
    base = str(
        os.getenv("ADAOS_Y_PY_WHEEL_RELEASE_BASE")
        or f"https://github.com/inimatic/adaos/releases/download/{release_tag}"
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


def _prepare_wheel(directory: Path, *, filename: str, expected_sha256: str) -> Path:
    wheel = directory / filename
    if wheel.is_file() and hashlib.sha256(wheel.read_bytes()).hexdigest() == expected_sha256:
        return wheel
    bundled = Path(__file__).resolve().parents[1] / "vendor/y-py/wheels" / filename
    if bundled.is_file():
        if hashlib.sha256(bundled.read_bytes()).hexdigest() != expected_sha256:
            raise RuntimeError(f"bundled patched y-py wheel digest mismatch: {bundled}")
        shutil.copyfile(bundled, wheel)
    else:
        _download(wheel, filename=filename, expected_sha256=expected_sha256)
    return wheel


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", choices=("pip", "uv"))
    parser.add_argument("--wheelhouse", default="")
    parser.add_argument(
        "--check", action="store_true",
        help="Verify the installed platform version and native integrity without installing",
    )
    args = parser.parse_args(argv)
    if not args.check and not args.installer:
        parser.error("--installer is required unless --check is used")
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("patched y-py wheels require CPython 3.11")
    filename, digest = _selected_wheel()
    version = filename.split("-")[1]
    if args.check:
        _verify_installation(version)
        return 0
    already_installed = _installed(version)
    if already_installed and not str(args.wheelhouse).strip():
        _verify_installation(version)
        return 0
    temporary_root = (
        Path(args.wheelhouse).expanduser().resolve()
        if str(args.wheelhouse).strip()
        else Path(tempfile.mkdtemp(prefix="adaos-y-py-"))
    )
    temporary_root.mkdir(parents=True, exist_ok=True)
    ephemeral = not str(args.wheelhouse).strip()
    try:
        wheel = _prepare_wheel(temporary_root, filename=filename, expected_sha256=digest)
        if already_installed:
            _verify_installation(version)
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
    _verify_installation(version)
    print(f"installed patched y-py {version} from hash-pinned release wheel")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
