"""Shared admission for source writers in one local development context."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import re
from typing import Iterator, Mapping, Any

import yaml

from adaos.services.mutation_lock import mutation_lock


class SourceAdmissionError(ValueError):
    code = "development_source_admission_failed"


_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")


@contextmanager
def source_write_admission(projects_root: Path) -> Iterator[None]:
    """One lock for composition, scaffold, materialization and recovery writers."""
    with mutation_lock(Path(projects_root).parent / ".source-admission.lock", timeout_s=60):
        yield


def project_owned_refs(payload: Mapping[str, Any]) -> tuple[str, ...]:
    refs = []
    components = payload.get("components")
    if not isinstance(components, Mapping) or not isinstance(components.get("owned"), list):
        raise SourceAdmissionError("Project must declare its owned component inventory")
    for member in components["owned"]:
        if not isinstance(member, Mapping):
            raise SourceAdmissionError("Invalid Project owned component")
        ref = str(member.get("ref") or "")
        kind, separator, name = ref.partition(":")
        if separator != ":" or kind not in {"skill", "scenario"} or not _ID.fullmatch(name):
            raise SourceAdmissionError("Project contains an invalid owned component reference")
        if ref in refs:
            raise SourceAdmissionError("Project declares an owned component more than once")
        refs.append(ref)
    return tuple(refs)


def ownership_inventory(projects_root: Path) -> dict[str, list[str]]:
    """Read only named Project manifests, never a runtime/DEV recursive scan."""
    root = Path(projects_root)
    result: dict[str, list[str]] = {}
    if not root.exists():
        return result
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        manifest = folder / "project.yaml"
        if not manifest.is_file():
            continue
        if any(path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400
               for path in (folder, manifest)):
            raise SourceAdmissionError("Project ownership source must not be a linked directory")
        if manifest.stat().st_size > 2_000_000:
            raise SourceAdmissionError("Project ownership manifest exceeds size limit")
        try:
            payload = yaml.safe_load(manifest.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict) or payload.get("id") != folder.name:
                raise SourceAdmissionError("Project folder and manifest identity disagree")
            for ref in project_owned_refs(payload):
                result.setdefault(ref, []).append(folder.name)
        except (OSError, UnicodeError, yaml.YAMLError, AttributeError, TypeError) as exc:
            raise SourceAdmissionError(f"Cannot establish Project ownership: {folder.name}") from exc
    return result


def assert_project_ownership(projects_root: Path, payload: Mapping[str, Any]) -> None:
    """Caller holds source_write_admission until authoritative mutation ends."""
    token = str(payload.get("id") or "")
    if not _ID.fullmatch(token):
        raise SourceAdmissionError("Invalid Project identity for source admission")
    owners = ownership_inventory(projects_root)
    for ref in project_owned_refs(payload):
        conflicting = [owner for owner in owners.get(ref, ()) if owner != token]
        if conflicting:
            raise SourceAdmissionError(f"{ref} is already owned by project:{','.join(conflicting)}")


def assert_component_unambiguous(projects_root: Path, ref: str) -> None:
    owners = ownership_inventory(projects_root).get(ref, [])
    if len(owners) > 1:
        raise SourceAdmissionError(f"{ref} has conflicting Project owners: {','.join(owners)}")
