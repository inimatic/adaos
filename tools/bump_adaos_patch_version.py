from __future__ import annotations

import argparse
import re
from pathlib import Path
import sys
import tomllib


_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_PROJECT_TABLE_RE = re.compile(r"^\s*\[project\]\s*(?:#.*)?$")
_TABLE_RE = re.compile(r"^\s*\[[^\]]+\]\s*(?:#.*)?$")
_VERSION_LINE_RE = re.compile(r"^(\s*version\s*=\s*)(['\"])([^'\"]+)(\2)(.*)$")


def read_project_version(pyproject_path: Path) -> str:
    try:
        payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"pyproject.toml not found: {pyproject_path}") from exc
    except Exception as exc:
        raise RuntimeError(f"failed to parse {pyproject_path}: {exc}") from exc
    project = payload.get("project") if isinstance(payload, dict) else None
    version = project.get("version") if isinstance(project, dict) else None
    text = str(version or "").strip()
    if not text:
        raise RuntimeError(f"{pyproject_path} does not define [project].version")
    return text


def bump_patch(version: str) -> str:
    match = _SEMVER_RE.fullmatch(str(version or "").strip())
    if match is None:
        raise RuntimeError(f"expected plain MAJOR.MINOR.PATCH version, got {version!r}")
    major, minor, patch = match.groups()
    return f"{major}.{minor}.{int(patch) + 1}"


def write_project_version(pyproject_path: Path, version: str) -> None:
    text = pyproject_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    in_project = False
    replaced = False
    for index, line in enumerate(lines):
        if _PROJECT_TABLE_RE.match(line):
            in_project = True
            continue
        if in_project and _TABLE_RE.match(line):
            break
        if not in_project:
            continue
        match = _VERSION_LINE_RE.match(line.rstrip("\r\n"))
        if match is None:
            continue
        newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        prefix, quote, _old, _closing, suffix = match.groups()
        lines[index] = f"{prefix}{quote}{version}{quote}{suffix}{newline}"
        replaced = True
        break
    if not replaced:
        raise RuntimeError(f"could not find [project].version line in {pyproject_path}")
    pyproject_path.write_text("".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Increment AdaOS [project].version patch number.")
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "pyproject.toml",
        help="Path to pyproject.toml",
    )
    parser.add_argument("--current", action="store_true", help="Print the current version without changing files.")
    parser.add_argument("--dry-run", action="store_true", help="Print the next patch version without changing files.")
    args = parser.parse_args(argv)

    try:
        pyproject_path = args.pyproject.expanduser().resolve()
        current = read_project_version(pyproject_path)
        if args.current:
            print(current)
            return 0
        next_version = bump_patch(current)
        if not args.dry_run:
            write_project_version(pyproject_path, next_version)
        print(next_version)
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
