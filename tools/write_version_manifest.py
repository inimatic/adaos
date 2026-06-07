from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "adaos.version-manifest.v1"
COMPONENTS = ("adaos_core", "root_backend", "hosted_client", "redevice_agent")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _git(args: list[str], cwd: Path) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=str(cwd), text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_pyproject_version(path: Path) -> str:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover
        import tomli as tomllib  # type: ignore
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    project = payload.get("project") if isinstance(payload, dict) else {}
    return str((project or {}).get("version") or "").strip()


def _read_gradle_properties(path: Path) -> tuple[str, str]:
    version = ""
    version_code = ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "", ""
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "redeviceAgentVersionName":
            version = value.strip()
        elif key.strip() == "redeviceAgentVersionCode":
            version_code = value.strip()
    return version, version_code


def _component_source(root: Path, component: str) -> tuple[str, str, dict[str, Any]]:
    if component == "adaos_core":
        path = root / "pyproject.toml"
        return _read_pyproject_version(path), path.relative_to(root).as_posix(), {}
    if component == "root_backend":
        path = root / "src" / "adaos" / "integrations" / "adaos-backend" / "package.json"
        return str(_read_json(path).get("version") or "").strip(), path.relative_to(root).as_posix(), {}
    if component == "hosted_client":
        path = root / "src" / "adaos" / "integrations" / "adaos-client" / "package.json"
        return str(_read_json(path).get("version") or "").strip(), path.relative_to(root).as_posix(), {}
    if component == "redevice_agent":
        path = root / "src" / "adaos" / "integrations" / "redevice-agent" / "android" / "gradle.properties"
        version, version_code = _read_gradle_properties(path)
        extra = {"version_code": version_code} if version_code else {}
        return version, path.relative_to(root).as_posix(), extra
    raise ValueError(f"unknown component: {component}")


def _entry(
    *,
    component: str,
    version: str,
    build_version: str,
    commit: str,
    source: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    served: dict[str, Any] = {
        "version": version,
        "build_version": build_version or version,
        "commit": commit,
        "source": source,
        "updated_at": datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat(),
    }
    if extra:
        served.update({key: value for key, value in extra.items() if value not in ("", None)})
    return {"component": component, "served": served, "used": None}


def _load_existing(path: Path) -> dict[str, Any]:
    manifest = _read_json(path)
    components = manifest.get("components")
    return {
        "schema_version": str(manifest.get("schema_version") or SCHEMA_VERSION),
        "generated_at": datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat(),
        "components": dict(components) if isinstance(components, dict) else {},
    }


def write_manifest(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.repo_root).resolve() if args.repo_root else _repo_root()
    out = Path(args.out).resolve()
    manifest = _load_existing(out)
    commit = args.commit or _git(["rev-parse", "HEAD"], root)
    short = commit[:7] if commit else ""
    selected = COMPONENTS if args.component == "all" else (args.component,)
    for component in selected:
        version, source_path, extra = _component_source(root, component)
        if component == args.component and args.version:
            version = args.version
        if not version:
            continue
        build_version = args.build_version if component == args.component and args.build_version else (
            f"{version}+{short}" if short else version
        )
        source = args.source if component == args.component and args.source else source_path
        manifest["components"][component] = _entry(
            component=component,
            version=version,
            build_version=build_version,
            commit=commit,
            source=source,
            extra=extra,
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write or update AdaOS aggregate version manifest.")
    parser.add_argument("--out", default="adaos-versions.json", help="Manifest path to write.")
    parser.add_argument("--repo-root", default="", help="Repository root for source version discovery.")
    parser.add_argument("--component", choices=("all", *COMPONENTS), default="all")
    parser.add_argument("--version", default="", help="Override version when updating a single component.")
    parser.add_argument("--build-version", default="", help="Override build_version when updating a single component.")
    parser.add_argument("--commit", default="", help="Override commit SHA.")
    parser.add_argument("--source", default="", help="Override source label/path when updating a single component.")
    args = parser.parse_args(argv)
    write_manifest(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
