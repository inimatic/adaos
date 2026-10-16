"""Read-only B/C/N reuse planning; existing admissions still own every write."""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping

_MISSING = object()


def _digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _equal(left, right):
    if left is _MISSING or right is _MISSING:
        return left is right
    return _digest(left) == _digest(right)


def _changes(base, candidate, path=()):
    if _equal(base, candidate):
        return []
    if isinstance(base, Mapping) and isinstance(candidate, Mapping):
        return [change for key in sorted(set(base) | set(candidate)) for change in
                _changes(base.get(key, _MISSING), candidate.get(key, _MISSING), (*path, key))]
    if isinstance(base, list) and isinstance(candidate, list):
        def ids(rows):
            result = [row.get("id") for row in rows if isinstance(row, Mapping) and isinstance(row.get("id"), str)]
            return result if len(result) == len(rows) and len(set(result)) == len(rows) else None
        identities = ids(base)
        if identities is not None and identities == ids(candidate):
            return [change for before, after in zip(base, candidate) for change in
                    _changes(before, after, (*path, "@" + before["id"]))]
    return [(path, base, candidate)]


def _lookup(value, path):
    for token in path:
        if isinstance(value, Mapping):
            value = value.get(token, _MISSING)
        elif isinstance(value, list) and token.startswith("@"):
            matches = [row for row in value if isinstance(row, Mapping) and row.get("id") == token[1:]]
            value = matches[0] if len(matches) == 1 else _MISSING
        else:
            return _MISSING
    return value


def plan_increment(*, base, candidate, current, old_obligations=None, new_obligations=None,
                   old_contract=None, new_contract=None, scope=None):
    """Classify retained declarative work without restoring or accepting it.

    Lists with unstable identity or changed order are atomic conflict domains.
    A compatible delta is a plan for the existing semantic/legacy admission,
    not authorization to copy an old tree or bypass accepted Prototype pins.
    """
    if not all(isinstance(value, Mapping) for value in (base, candidate, current)):
        return {"schema": "adaos.builder.increment_plan.v1", "decision": "missing_evidence",
                "apply_allowed": False, "model_started": False, "validation_required": True}
    delta = _changes(base, candidate)
    changes, conflicts = [], []
    for path, before, after in delta:
        now = _lookup(current, path)
        disposition = "absorbed" if _equal(now, after) else "replay" if _equal(now, before) else "conflict"
        pointer = "/" + "/".join(token.replace("~", "~0").replace("/", "~1") for token in path)
        changes.append({"path": pointer, "disposition": disposition,
                        "before_present": before is not _MISSING, "after_present": after is not _MISSING,
                        **({"before": copy.deepcopy(before)} if before is not _MISSING else {}),
                        **({"after": copy.deepcopy(after)} if after is not _MISSING else {})})
        if disposition == "conflict":
            conflicts.append(pointer)
    decision = ("unchanged" if not delta else "already_absorbed" if all(row["disposition"] == "absorbed" for row in changes)
                else "conflict" if conflicts else "compatible_delta")
    contract_checked = old_contract is not None and new_contract is not None
    contract_matches = contract_checked and _equal(old_contract, new_contract)
    if decision == "compatible_delta" and contract_checked and not contract_matches:
        decision = "contract_conflict"
    obligations_checked = old_obligations is not None and new_obligations is not None
    result = {"schema": "adaos.builder.increment_plan.v1", "decision": decision,
              "base_digest": _digest(base), "candidate_digest": _digest(candidate), "current_digest": _digest(current),
              "old_contract_digest": _digest(old_contract) if old_contract is not None else None,
              "new_contract_digest": _digest(new_contract) if new_contract is not None else None,
              "old_obligations_digest": _digest(old_obligations) if old_obligations is not None else None,
              "new_obligations_digest": _digest(new_obligations) if new_obligations is not None else None,
              "scope": scope,
              "changes": changes, "conflict_paths": conflicts, "contract_checked": contract_checked,
              "contract_matches": contract_matches, "obligations_checked": obligations_checked,
              "obligations_changed": not _equal(old_obligations, new_obligations) if obligations_checked else None,
              "apply_allowed": False, "model_started": False, "validation_required": True,
              "next_action": "validate_current_source" if decision in {"unchanged", "already_absorbed"}
                  else "admit_bounded_replay" if decision == "compatible_delta" and contract_matches and obligations_checked
                      and _equal(old_obligations, new_obligations)
                  else "review_conflicts_and_obligations",
              "authority": "Planning never changes cancellation, acceptance, data, or source; existing admission must recheck all pins."}
    result["digest"] = _digest(result)
    return result
