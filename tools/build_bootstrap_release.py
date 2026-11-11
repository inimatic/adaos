#!/usr/bin/env python3
"""Build a Linux bootstrap kit in a glibc 2.31 container. No provider credentials.

The source snapshot comes from Git, never from an unrestricted workspace copy.
--candidate overlays only the bootstrap implementation under development.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
OVERLAY = ("tools/build_bootstrap_release.py", "tools/bootstrap-runtime.lock.json",
           "tools/bootstrap-distribution.sh", "tools/bootstrap_distribution.py",
           "tools/bootstrap.sh", "tools/bootstrap_uv.sh")
OVERLAY += ('src/adaos/services/bootstrap_reporting.py', 'src/adaos/apps/api/server.py')


def digest(filename: Path) -> str:
    with filename.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(spec: dict, destination: Path) -> None:
    if destination.exists() and digest(destination) == spec["sha256"]:
        return
    for attempt in range(3):
        try:
            with urllib.request.urlopen(spec["url"], timeout=60) as response, destination.open("wb") as output:
                shutil.copyfileobj(response, output)
            if digest(destination) != spec["sha256"]:
                raise ValueError("runtime archive SHA256 mismatch")
            return
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)


def run(*args: str, cwd: Path | None = None, quiet: bool = False) -> None:
    subprocess.run(args, cwd=cwd, check=True, stdout=subprocess.DEVNULL if quiet else None)


def inside(source: Path, output: Path, release: str) -> None:
    lock = json.loads((source / "tools/bootstrap-runtime.lock.json").read_text())
    kit = output / "kit"
    kit.mkdir()
    runtime = kit / "runtime"
    runtime.mkdir()
    for name in ("python", "uv"):
        archive = output / (name + ".tar.gz")
        download(lock[name], archive)
        with tarfile.open(archive) as package:
            package.extractall(runtime, filter="data")
    uv = next(runtime.glob("uv-*/uv"))
    shutil.copy2(uv, runtime / "uv")
    shutil.rmtree(uv.parent)
    python = runtime / "python/bin/python3"
    run(str(python), "-c", "import sys,ssl,sqlite3,venv,ensurepip; assert sys.version_info[:2] == (3,11)")
    work = output / "source"
    shutil.copytree(source, work)
    requirements = kit / "requirements.txt"
    run(str(runtime / "uv"), "export", "--frozen", "--no-dev", "--no-emit-project", "--no-hashes",
        "--format", "requirements-txt", "--output-file", str(requirements), cwd=work, quiet=True)
    wheels = kit / "wheels"
    wheels.mkdir()
    # nats-py 2.12.0 has no published wheel. Build that pure Python package here,
    # never on the consumer; refuse every other implicit source dependency.
    run(str(python), "-m", "pip", "wheel", "--quiet", "--only-binary=:all:", "--no-binary=nats-py",
        "--find-links", str(work / "vendor/y-py/wheels"), "--wheel-dir", str(wheels), "-r", str(requirements), cwd=work)
    run(str(python), "-m", "pip", "wheel", "--quiet", "--no-deps", "--wheel-dir", str(wheels), str(work), cwd=work)
    # Locked hashes for every admitted wheel, including the project itself.
    wheel_lock = []
    for wheel in sorted(wheels.glob("*.whl")):
        wheel_lock.append(f"./wheels/{wheel.name} --hash=sha256:{digest(wheel)}")
    (kit / "install-requirements.txt").write_text("\n".join(wheel_lock) + "\n")
    check = output / 'check-venv'
    run(str(python), '-m', 'venv', str(check))
    run(str(runtime / 'uv'), 'pip', 'install', '--quiet', '--offline', '--no-index', '--python', str(check / 'bin/python'),
        '--require-hashes', '-r', str(kit / 'install-requirements.txt'), cwd=kit)
    environment = dict(os.environ, GIT_PYTHON_REFRESH='quiet')
    subprocess.run([str(check / 'bin/adaos'), '--help'], env=environment, check=True, stdout=subprocess.DEVNULL)
    shutil.copytree(source, kit / "source")
    manifest = {"schema": "adaos.bootstrap_bundle.v1", "release_id": release,
                "os": "linux", "arch": "x86_64", "libc": "glibc", "min_libc": "2.31", "python": "3.11",
                "features": ["core"], "runtime_lock": lock,
                "source": json.loads((source / 'bootstrap-source.json').read_text()),
                "files": {p.relative_to(kit).as_posix(): digest(p) for p in sorted(kit.rglob("*")) if p.is_file()}}
    (kit / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    bundle = output / (release + "-linux-x86_64-gnu.tar.gz")
    with tarfile.open(bundle, "w:gz") as archive:
        archive.add(kit, arcname=".")
    record = {key: manifest[key] for key in ("release_id", "os", "arch", "libc", "min_libc", "python", "features")}
    record.update({"filename": bundle.name, "sha256": digest(bundle), "bytes": bundle.stat().st_size})
    record.update(source=manifest['source'], python_version=lock['python']['version'],
                  uv_version=lock['uv']['version'], built_at=datetime.now(timezone.utc).isoformat(),
                  adaos_version=subprocess.check_output([str(check / 'bin/python'), '-c',
                      "import importlib.metadata; print(importlib.metadata.version('adaos'))"], text=True).strip())
    (output / "release.json").write_text(json.dumps(record, indent=2) + "\n")
    shutil.copy2(source / 'tools/bootstrap-distribution.sh', output / 'bootstrap-distribution.sh')
    print(json.dumps(record), flush=True)


def snapshot(destination: Path, candidate: bool) -> str:
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    data = subprocess.check_output(["git", "archive", "--format=tar", revision], cwd=ROOT)
    destination.mkdir()
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive:
            name = member.name
            # Git archive contains gitlinks without their private submodule files.
            # Keep owned Python integrations (rhasspy/telegram); omit Android's
            # separate nested Python distribution from this Linux runtime.
            allowed = (name.startswith("src/adaos/") and not name.startswith("src/adaos/integrations/android-node/")) or \
                name.startswith("tools/") or name.startswith("vendor/y-py/wheels/") or \
                name in ("pyproject.toml", "uv.lock", "README.md", "LICENSE", ".env.example", ".env.prod.sample")
            if allowed and member.isfile():
                archive.extract(member, destination, filter="data")
    if candidate:
        for relative in OVERLAY:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)
    # Git on Windows can have CRLF in shell files; Linux must receive LF.
    for script in (destination / "tools").rglob("*.sh"):
        script.write_bytes(script.read_bytes().replace(b"\r\n", b"\n"))
    content_hash = hashlib.sha256()
    for item in sorted(destination.rglob('*')):
        if item.is_file():
            content_hash.update((item.relative_to(destination).as_posix() + '\0' + digest(item) + '\n').encode())
    (destination / "bootstrap-source.json").write_text(json.dumps({"revision": revision, "candidate": candidate,
        "sha256": content_hash.hexdigest()}) + "\n")
    return revision


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--candidate", action="store_true")
    parser.add_argument("--inside", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    import re
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,95}", args.release):
        parser.error("invalid release ID")
    if args.inside:
        # Docker Desktop bind mounts are slow for thousands of interpreter files.
        # Build on the container filesystem and copy only final artifacts back.
        work = Path('/work')
        work.mkdir()
        source = work / 'snapshot'
        source.mkdir()
        with tarfile.open(args.inside) as archive:
            archive.extractall(source, filter='data')
        output = work / 'output'
        output.mkdir()
        for name in ('python.tar.gz', 'uv.tar.gz'):
            if (args.output / name).exists():
                shutil.copy2(args.output / name, output / name)
        inside(source, output, args.release)
        for item in output.iterdir():
            if item.is_file():
                shutil.copy2(item, args.output / item.name)
        return
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("output must be empty")
    output.mkdir(parents=True, exist_ok=True)
    source = output / "snapshot"
    snapshot(source, args.candidate)
    archive_path = output / 'source.tar'
    with tarfile.open(archive_path, 'w') as archive:
        archive.add(source, arcname='.')
    run("docker", "run", "--rm", "--platform", "linux/amd64", "--mount", f"type=bind,source={archive_path},target=/source.tar,readonly",
        "--mount", f"type=bind,source={Path(__file__).resolve()},target=/builder.py,readonly",
        "--mount", f"type=bind,source={output},target=/output", "python:3.11-slim-bullseye", "python",
        "/builder.py", "--inside", "/source.tar", "--output", "/output", "--release", args.release)


if __name__ == "__main__":
    main()
