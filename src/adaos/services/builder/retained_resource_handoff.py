"""Reuse Core-captured Prototype handoff, never a later mutable Preview store."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Mapping

from adaos.services.builder.prototype_stage import prototype_record_evidence
from adaos.services.resources.local import validate_local_resource_bundle


def _read(path: Path, limit: int = 4 * 1024 * 1024) -> bytes:
    if path.is_symlink() or path.resolve() != path.absolute() or path.stat().st_size > limit:
        raise ValueError("Invalid retained input path or size")
    raw = path.read_bytes()
    if len(raw) > limit:
        raise ValueError("Retained input exceeds its bound")
    return raw


def read_retained_handoff(runs_root: Path, reference: Mapping, *, acceptance: Mapping,
                         target: Mapping, companion_skill_ids: list[str], session_id: str,
                         iteration: int) -> dict:
    task_id = str(reference.get("source_task_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,180}", task_id):
        raise ValueError("Invalid retained handoff task identity")
    root = Path(runs_root).resolve() / task_id / "input"
    assignment = json.loads(_read(root / "assignment.json"))
    request = assignment.get("realize_request") or {}
    links, artifacts = request.get("links") or {}, request.get("artifacts") or {}
    if (assignment.get("task_id") != task_id or assignment.get("target") != dict(target)
            or not session_id or links.get("automation_session_id") != session_id
            or type(links.get("iteration")) is not int or links["iteration"] >= iteration
            or artifacts.get("prototype_acceptance") != dict(acceptance)
            or sorted(artifacts.get("companion_skill_ids") or []) != sorted(companion_skill_ids)):
        raise ValueError("Retained handoff does not belong to this accepted Automation lineage")
    raw = _read(root / "prototype-resource-handoff.json")
    digest = hashlib.sha256(raw).hexdigest()
    if reference.get("handoff_sha256") != digest:
        raise ValueError("Retained handoff digest mismatch")
    packet_raw = _read(root / "packet.json")
    packet = json.loads(packet_raw)
    handoff = json.loads(raw)
    if packet.get("task_id") != task_id or packet.get("prototype_resource_handoff") != handoff:
        raise ValueError("Retained handoff differs from its Core packet")
    # First model input records both Core file digests. Candidate source and
    # model-written output are deliberately not recovery authorities.
    prompt_raw = _read(root / "model-attempts/001.prompt.md")
    receipt = json.loads(_read(root / "model-attempts/001.prompt.json"))
    if (receipt.get("task_id") != task_id or receipt.get("prompt_bytes") != len(prompt_raw)
            or receipt.get("prompt_sha256") != hashlib.sha256(prompt_raw).hexdigest()):
        raise ValueError("Retained model input receipt mismatch")
    prompt = prompt_raw.decode("utf-8")
    heading = "## Read-only task inputs\n"
    if prompt.count(heading) != 1:
        raise ValueError("Retained handoff lacks exact Core input provenance")
    listed = json.JSONDecoder().raw_decode(prompt.split(heading)[1].split("```json\n", 1)[1])[0]
    for name, content in (("prototype-resource-handoff.json", raw), ("packet.json", packet_raw)):
        matches = [item for item in listed if isinstance(item, dict) and item.get("name") == name]
        if (len(matches) != 1 or matches[0].get("sha256") != hashlib.sha256(content).hexdigest()
                or matches[0].get("bytes") != len(content)):
            raise ValueError("Retained Core file does not match the admitted model input")
    expected = {item["resource_type"]: item for item in prototype_record_evidence(acceptance)}
    resources = handoff.get("resources") or []
    if (handoff.get("project_ref") != f"{target['type']}:{target['id']}"
            or handoff.get("acceptance_id") != acceptance.get("acceptance_id")
            or handoff.get("change_id") != acceptance.get("change_id")
            or handoff.get("revision") != acceptance.get("revision")
            or handoff.get("companion_skill_id") != companion_skill_ids[0]
            or len(resources) != len(expected)
            or {item.get("source_resource_type") for item in resources} != set(expected)):
        raise ValueError("Retained handoff resource identity mismatch")
    for resource in resources:
        bundle = resource["bundle"]
        validate_local_resource_bundle(bundle, expected_owner_ref=f"skill:{companion_skill_ids[0]}")
        metadata = bundle["resource_definition"].get("metadata") or {}
        if (bundle.get("seed") != [] or metadata.get("prototype_records_digest")
                != expected[resource["source_resource_type"]]["records_digest"]):
            raise ValueError("Retained handoff changed accepted evidence or seeded installation data")
    return handoff


def select_retained_handoff(runs_root: Path, session: Mapping, *, target: Mapping,
                           companion_skill_ids: list[str]) -> dict | None:
    acceptance = session.get("prototype_acceptance") or {}
    iteration = int(session.get("iteration") or 0)
    if not iteration or not prototype_record_evidence(acceptance) or not companion_skill_ids:
        return None
    for task_id in reversed(list(session.get("task_history") or [])[-100:]):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,180}", str(task_id)):
            continue
        path = Path(runs_root).resolve() / task_id / "input/prototype-resource-handoff.json"
        try:
            reference = {"source_task_id": task_id, "handoff_sha256": hashlib.sha256(_read(path)).hexdigest()}
            read_retained_handoff(runs_root, reference, acceptance=acceptance, target=target,
                companion_skill_ids=companion_skill_ids, session_id=str(session.get("session_id") or ""), iteration=iteration)
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            continue
        return reference
    return None
