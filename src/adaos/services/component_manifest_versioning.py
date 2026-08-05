"""Atomic component and bound conversational manifest version updates."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Mapping

import yaml


class ComponentManifestVersionError(ValueError):
    pass


def _payload(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ComponentManifestVersionError(f"cannot read bound manifest: {path}") from exc
    if not isinstance(value, Mapping):
        raise ComponentManifestVersionError(f"bound manifest must contain an object: {path}")
    return dict(value)


def _bound_conversational_manifest(
    component_path: Path,
    component: Mapping[str, Any],
) -> tuple[Path, dict[str, Any]] | None:
    binding = component.get("conversational")
    if not isinstance(binding, Mapping):
        return None
    relative = str(binding.get("manifest") or "").strip()
    if not relative:
        raise ComponentManifestVersionError("conversational binding requires manifest")
    root = component_path.parent.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ComponentManifestVersionError("conversational manifest escapes component root") from exc
    if not path.is_file():
        raise ComponentManifestVersionError(f"bound conversational manifest is missing: {relative}")
    return path, _payload(path)


def write_component_version_atomically(
    component_path: Path | str,
    component: Mapping[str, Any],
    *,
    previous_version: str | None,
    next_version: str,
) -> None:
    """Write one release version to component and conversational manifests.

    Existing bound versions are a compare-and-swap precondition. Temporary
    files are prepared before either source is replaced, and any replacement
    failure restores the exact original bytes.
    """

    path = Path(component_path).resolve()
    value = dict(component)
    selected = str(next_version or "").strip()
    if not selected:
        raise ComponentManifestVersionError("next component version is required")
    value["version"] = selected
    bound = _bound_conversational_manifest(path, value)
    writes: list[tuple[Path, bytes]] = [
        (path, (yaml.safe_dump(value, allow_unicode=True, sort_keys=False) + "\n").encode("utf-8"))
    ]
    if bound is not None:
        package_path, package = bound
        current_package_version = str(package.get("version") or "").strip() or None
        expected = str(previous_version or "").strip() or None
        if current_package_version != expected:
            raise ComponentManifestVersionError(
                "conversational manifest version drift: "
                f"component={expected!r} conversational={current_package_version!r}"
            )
        package["version"] = selected
        writes.append(
            (
                package_path,
                (yaml.safe_dump(package, allow_unicode=True, sort_keys=False) + "\n").encode("utf-8"),
            )
        )
    originals = {target: target.read_bytes() for target, _ in writes}
    temporary: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for target, content in writes:
            tmp = target.with_name(f".{target.name}.version-{uuid.uuid4().hex}.tmp")
            tmp.write_bytes(content)
            temporary[target] = tmp
        for target, _ in writes:
            temporary[target].replace(target)
            replaced.append(target)
    except Exception:
        for target in reversed(replaced):
            target.write_bytes(originals[target])
        raise
    finally:
        for tmp in temporary.values():
            tmp.unlink(missing_ok=True)


__all__ = ["ComponentManifestVersionError", "write_component_version_atomically"]
