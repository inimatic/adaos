#!/usr/bin/env python3
"""Second bootstrap stage, executed exclusively by our hash-pinned CPython."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import stat

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bootstrap_runtime import host_profile, python_path, runtime_lock

# Available before dependency installation: this module uses only the stdlib.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from adaos.services.bootstrap_reporting import durable_json, flush_outbox

class Reporter:
    def __init__(self, attempt: Path):
        self.attempt = attempt
        self.attempt_id = json.loads((attempt / "request.json").read_text())["attempt_id"]
        self.route = "standard"
        self.stage = "python"

    def event(self, kind: str, reason: str, status: int = 0) -> None:
        identifier = str(uuid.uuid4())
        durable_json(self.attempt / ("event-" + identifier + ".json"), {
            "attempt_id": self.attempt_id, "event_id": identifier, "type": kind,
            "stage": self.stage, "reason": reason, "route": self.route, "status": str(status)})


def run(*arguments: str, cwd: Path | None = None, env: dict | None = None) -> None:
    subprocess.run(arguments, cwd=cwd, env=env, check=True)


def is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return os.name == 'nt' and bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except FileNotFoundError:
        return False


def make_link(path: Path, target: Path) -> None:
    if os.name == 'nt':
        # Directory junctions need neither elevation nor Developer Mode.
        import _winapi
        _winapi.CreateJunction(str(target.resolve()), str(path))
    else:
        path.symlink_to(target, target_is_directory=True)


def recover_activation(dest: Path) -> None:
    record = dest / '.adaos/venv-activation.json'
    if os.name != 'nt' or not record.exists():
        return
    saved = json.loads(record.read_text(encoding='utf-8'))
    backup = dest / saved['backup']
    if not backup.resolve(strict=False).is_relative_to(dest / '.adaos/environments'):
        raise ValueError('Invalid retained environment activation')
    active = dest / '.venv'
    if not active.exists() and is_link(backup):
        backup.rename(active)


def activate(dest: Path, venv: Path, identity: str) -> str | None:
    active = dest / '.venv'
    candidate = dest / ('.venv.candidate-' + identity)
    previous = str(active.resolve()) if is_link(active) else None
    make_link(candidate, venv)
    if os.name != 'nt':
        os.replace(candidate, active)
        return previous
    backup = dest / '.adaos/environments' / ('active-before-' + identity)
    if previous:
        durable_json(dest / '.adaos/venv-activation.json', {'backup': backup.relative_to(dest).as_posix()})
        active.rename(backup)
    try:
        candidate.rename(active)
    except OSError:
        if previous:
            backup.rename(active)
        raise
    if previous:
        # Remove only the junction itself, never its target's files.
        assert is_link(backup) and backup.parent.resolve() == (dest / '.adaos/environments').resolve()
        os.rmdir(backup)
    return previous


def install(args: argparse.Namespace, reporter: Reporter) -> None:
    dest = args.dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    if (dest / '.git').exists():
        raise ValueError('Prepared releases cannot replace a Git checkout; use --dev or a fresh --dest')
    marker = dest / ".adaos/bootstrap-managed.json"
    recover_activation(dest)
    if (dest / ".venv").exists() and not marker.exists():
        raise ValueError("Refusing to replace an unmanaged .venv; choose a fresh --dest")
    active = dest / '.venv'
    if active.exists() and not is_link(active):
        raise ValueError('Existing .venv uses a different bootstrap layout; choose a fresh --dest')
    kit = args.kit.resolve() if args.kit else None
    source = args.source.resolve()
    profile = host_profile()
    lock = runtime_lock(source, profile)
    reporter.route = "bundle" if kit else "standard"
    if kit:
        reporter.stage = "verify"
        manifest = json.loads((kit / "manifest.json").read_text())
        if manifest["schema"] != "adaos.bootstrap_bundle.v1" or manifest["runtime_lock"] != lock:
            raise ValueError("Invalid bundle manifest")
        if manifest.get('os', profile.split('-')[0]) != profile.split('-')[0] or manifest.get('arch', profile.split('-')[1]) != profile.split('-')[1]:
            raise ValueError('Bundle platform differs from this machine')
        for relative, expected in manifest["files"].items():
            path = (kit / relative).resolve()
            if not path.is_relative_to(kit):
                raise ValueError("Bundle path escapes extraction directory")
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    raise ValueError("Bundle file SHA256 mismatch: " + relative)
    reporter.stage = "python"
    runtime = dest / ".adaos/runtime" / lock["python"]["sha256"][:16]
    origin = kit / "runtime/python" if kit else args.python_home.resolve()
    if not python_path(runtime).exists():
        shutil.copytree(origin, runtime, symlinks=True)
    python = python_path(runtime)
    run(str(python), "-c", "import sys,ssl,venv,ensurepip; assert (3,11,9) <= sys.version_info[:3] < (3,12,0)")
    if source != dest:
        shutil.copytree(source, dest, dirs_exist_ok=True, symlinks=True)
    if kit:
        shutil.copy2(kit / 'manifest.json', dest / '.adaos/bundle-manifest.json')
    durable_json(marker, {"schema": "adaos.bootstrap_managed.v1", "python_sha256": lock["python"]["sha256"]})
    reporter.stage = "venv"
    venv = dest / '.adaos/environments' / reporter.attempt_id
    run(str(python), "-m", "venv", str(venv))
    vpython = python_path(venv, venv=True)
    reporter.stage = "dependencies"
    uv = args.uv or (kit / ('runtime/uv.exe' if os.name == 'nt' else 'runtime/uv') if kit else None)
    uv_ok = False
    if kit:
        requirements = str(kit / "install-requirements.txt")
        options = ("--offline", "--no-index", "--find-links", str(kit / "wheels"), "--require-hashes", "-r", requirements)
        if not args.without_uv and uv and uv.exists():
            try:
                run(str(uv), "pip", "install", "--quiet", "--python", str(vpython), *options, cwd=kit)
                uv_ok = True
            except (OSError, subprocess.CalledProcessError) as cause:
                reporter.event("stage_failed", "uv_install_failed", cause.returncode if isinstance(cause, subprocess.CalledProcessError) else 126)
                print("uv unavailable; continuing with managed CPython + pip", flush=True)
        if not uv_ok:
            run(str(vpython), "-m", "pip", "install", "--quiet", "--no-index", "--find-links", str(kit / "wheels"),
                "--require-hashes", "-r", requirements, cwd=kit)
    else:
        # Standard route deliberately permits upstream access, but never system Python.
        wheelhouse = str(dest / "vendor/y-py/wheels")
        if not args.without_uv and uv and uv.exists():
            try:
                run(str(uv), "export", "--frozen", "--no-dev", "--no-emit-project", "--no-hashes",
                    "--output-file", str(args.attempt / "requirements.txt"), cwd=dest)
                run(str(uv), "pip", "install", "--python", str(vpython), "--only-binary", ":all:",
                    "--find-links", wheelhouse, "-r", str(args.attempt / "requirements.txt"), cwd=dest)
                run(str(uv), "pip", "install", "--python", str(vpython), "--no-deps", "-e", str(dest), cwd=dest)
                uv_ok = True
            except (OSError, subprocess.CalledProcessError) as cause:
                reporter.event("stage_failed", "uv_install_failed", cause.returncode if isinstance(cause, subprocess.CalledProcessError) else 126)
        if not uv_ok:
            run(str(vpython), "-m", "pip", "install", "--find-links", wheelhouse, "--only-binary", ":all:", "-e", str(dest), cwd=dest)
    run(str(vpython), "-m", "pip", "check")
    run(str(vpython), "-c", 'import y_py,fastapi,adaos; import importlib.metadata as m; assert m.version("y-py")=="0.6.2+adaos.1"')
    run(str(vpython), '-m', 'adaos', '--help', env=dict(os.environ, GIT_PYTHON_REFRESH='quiet',
        ADAOS_DISABLE_PREFERRED_PYTHON_REEXEC='1', ADAOS_DISABLE_ACTIVE_SLOT_PYTHON_REEXEC='1'))
    previous = activate(dest, venv, reporter.attempt_id)
    if not args.runtime_only:
        reporter.stage = "configure"
        environment = dict(os.environ, ADAOS_BOOTSTRAP_RUNTIME_READY="1")
        try:
            command = ('powershell.exe', '-NoProfile', '-File', str(dest / 'tools/bootstrap.ps1')) if os.name == 'nt' else ('bash', str(dest / 'tools/bootstrap.sh'))
            run(*command, *args.bootstrap_args, cwd=dest, env=environment)
        except (OSError, subprocess.CalledProcessError):
            if previous:
                activate(dest, Path(previous), reporter.attempt_id + '-rollback')
            raise
    reporter.stage = "complete"
    reporter.event("installation_succeeded", "runtime_ready" if args.runtime_only else "bootstrap_completed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attempt", type=Path, required=True)
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--kit", type=Path)
    parser.add_argument("--python-home", type=Path)
    parser.add_argument("--uv", type=Path)
    parser.add_argument("--without-uv", action="store_true")
    parser.add_argument("--runtime-only", action="store_true")
    parser.add_argument("bootstrap_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.bootstrap_args[:1] == ["--"]:
        args.bootstrap_args.pop(0)
    reporter = Reporter(args.attempt)
    flush_outbox(args.attempt.parent)
    try:
        install(args, reporter)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as cause:
        code = cause.returncode if isinstance(cause, subprocess.CalledProcessError) else 1
        reporter.event("installation_failed", "stage_execution_failed", max(1, min(255, code)))
        print(f"Bootstrap failed at {reporter.stage}: {cause}", file=sys.stderr)
        return 1
    finally:
        pending = flush_outbox(args.attempt.parent)
        if pending:
            print(f"Root reporting pending for {pending} attempt(s); durable local outbox: {args.attempt.parent}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
