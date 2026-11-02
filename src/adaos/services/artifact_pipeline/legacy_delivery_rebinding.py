"""Compatibility proof for a legacy delivery migrating its renderer layouts."""

from copy import deepcopy
from io import BytesIO
import json
from zipfile import ZipFile

import yaml

from adaos.domain.artifact_release import canonical_payload_digest


def equivalent_legacy_delivery(active, candidate, package_store):
    """Keep code, manifest behavior and UI consumer interfaces identical.

    Historical packages may migrate areas to durable regions before they have
    portable CBS contracts. Region identities, widgets, actions, state, data
    sources and exported interfaces must remain exact. Tool ABI equality alone
    never admits replacement code or a changed consumer interface.
    """
    snapshots = []
    for package in (active, candidate):
        archive, verified = package_store.read_verified(package.digest)
        if verified.ref != package:
            return None
        files = {item["path"]: item["digest"] for item in verified.package_manifest["files"]}
        if any(name in files for name in ("contracts/capability.contract.json", "contracts/binding.definition.json")):
            return None
        with ZipFile(BytesIO(archive)) as bundle:
            manifest = yaml.safe_load(bundle.read("skill.yaml").decode("utf-8"))
            manifest.pop("version", None)
            ui = json.loads(bundle.read("webui.json")) if "webui.json" in files else None
        snapshots.append({"manifest": manifest, "ui": ui,
            "files": {name: digest for name, digest in files.items() if name not in {"skill.yaml", "webui.json"}}})
    old, new = snapshots
    if active.key != candidate.key:
        return None
    old["ui"], new["ui"] = deepcopy(old["ui"]), deepcopy(new["ui"])

    def layouts(left, right):
        if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
            for before, after in zip(left, right):
                layouts(before, after)
        elif isinstance(left, dict) and isinstance(right, dict):
            before, after = left.get("layout") or {}, right.get("layout") or {}
            areas, regions = before.get("areas"), after.get("regions")
            if (isinstance(left.get("widgets"), list) and isinstance(right.get("widgets"), list)
                    and isinstance(areas, list) and isinstance(regions, list) and after.get("version") == 2):
                old_ids = [item.get("id") for item in areas if isinstance(item, dict)]
                new_ids = [item.get("id") for item in regions if isinstance(item, dict)]
                if (len(old_ids) == len(areas) and len(new_ids) == len(regions)
                        and all(isinstance(key, str) and key for key in old_ids + new_ids)
                        and len(set(old_ids)) == len(old_ids) and len(set(new_ids)) == len(new_ids)
                        and set(old_ids) == set(new_ids) and not before.get("variants") and not after.get("variants")):
                    left["layout"] = right["layout"] = {"preserved_region_ids": sorted(old_ids)}
            for key in left.keys() & right.keys():
                if key not in {"semantic", "initialState"}:
                    layouts(left[key], right[key])

    layouts(old["ui"], new["ui"])
    old_digest = canonical_payload_digest(old)
    return old_digest if old_digest == canonical_payload_digest(new) else None
