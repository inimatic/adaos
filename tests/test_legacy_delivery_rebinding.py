from copy import deepcopy
import hashlib
from io import BytesIO
import json
from types import SimpleNamespace
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml
import jsonschema

from adaos.domain.artifact_release import DependencyBinding, WorkspaceLock
from adaos.services.artifact_pipeline.trial_activation import unresolved_shared_skill_conflicts
from test_trial_activation import _skill


def setup_delivery(fault=None):
    active, candidate = _skill("a"), _skill("b")
    manifest = {"name": active.artifact_id, "version": "1.0.0", "tools": [{"name": "run",
        "input_schema": {"type": "object"}, "output_schema": {"type": "object"}}]}
    old_ui = {"registry": {"modals": {"panel": {"schema": {
        "layout": {"type": "single", "areas": [{"id": "main", "role": "main"}]},
        "widgets": [{"id": "list", "area": "main", "actions": [{"on": "click", "target": "sample.read"}]}],
        "interface": {"schema": "adaos.ui.modal.interface.v1", "routes": {"main": {"view": "shared.panel"}}},
    }}}}}
    new_ui = deepcopy(old_ui)
    schema = new_ui["registry"]["modals"]["panel"]["schema"]
    schema["layout"] = {"version": 2, "regions": [{"id": "main", "role": "collection"}]}
    target_manifest = {**manifest, "version": "1.0.1"}
    code = b"def run(): return {}\n"
    new_code = code
    if fault == "code": new_code = b"def run(): raise RuntimeError()\n"
    if fault == "action": schema["widgets"][0]["actions"][0]["target"] = "sample.write"
    if fault == "interface": schema["interface"]["routes"]["main"]["view"] = "other.panel"
    if fault == "data_source": schema["widgets"][0]["dataSource"] = {"kind": "y", "path": "other"}
    if fault == "region": schema["layout"]["regions"][0]["id"] = "other"
    if fault == "variants": schema["layout"]["variants"] = [{"id": "changes-widget-availability"}]
    if fault == "tool": target_manifest["tools"] = []
    if fault == "semantic": new_ui["semantic"] = {"layout": schema["layout"]}
    if fault == "state": schema["initialState"] = {"write_enabled": True}
    snapshots = {}
    for ref, declaration, ui, implementation in [(active, manifest, old_ui, code), (candidate, target_manifest, new_ui, new_code)]:
        files = {"skill.yaml": yaml.safe_dump(declaration).encode(), "webui.json": json.dumps(ui).encode(),
            "handlers/main.py": implementation}
        if fault == "partial_cbs" and ref is candidate:
            files["contracts/capability.contract.json"] = b"{}"
        stream = BytesIO()
        with ZipFile(stream, "w") as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        snapshots[ref.digest] = stream.getvalue(), SimpleNamespace(ref=ref, binding_deliveries=(),
            package_manifest={"files": [{"path": name, "digest": "sha256:" + hashlib.sha256(data).hexdigest()}
                for name, data in files.items()]})
    store = SimpleNamespace(read_verified=lambda digest: snapshots[digest])
    lock = WorkspaceLock(lock_revision=1, updated_at="2026-11-02T12:00:00+00:00", components=(active,),
        bindings=(DependencyBinding(consumer="scenario:other", dependency=active.key, package_digest=active.digest),))
    return SimpleNamespace(packages=(candidate,)), lock, store


def test_legacy_layout_migration_has_exact_execution_and_consumer_contract_proof():
    plan, lock, store = setup_delivery()
    conflicts, proofs = unresolved_shared_skill_conflicts(plan, lock, store)
    assert not conflicts
    assert len(proofs) == 1
    assert proofs[0]["active_consumers"] == ["scenario:other"]
    assert proofs[0]["active_package_digest"] == lock.components[0].digest
    assert proofs[0]["candidate_package_digest"] == plan.packages[0].digest
    assert proofs[0]["policy"] == "exact_legacy_code_and_ui_contract_with_durable_layout_migration"
    assert proofs[0]["runtime_fingerprint"].startswith("sha256:")
    root_schema = json.loads((Path(__file__).resolve().parents[1] / "src/adaos/abi/workflow.publication_admission.v1.schema.json").read_text())
    schema = {"$defs": root_schema["$defs"], **root_schema["properties"]["shared_skill_rebindings"]}
    jsonschema.validate(proofs, schema)
    missing = deepcopy(proofs)
    missing[0].pop("runtime_fingerprint")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(missing, schema)


@pytest.mark.parametrize("fault", ["code", "action", "interface", "data_source", "region", "variants", "tool", "semantic", "state", "partial_cbs"])
def test_legacy_layout_proof_never_admits_execution_interface_state_or_widget_changes(fault):
    plan, lock, store = setup_delivery(fault)
    conflicts, proofs = unresolved_shared_skill_conflicts(plan, lock, store)
    assert not proofs
    assert conflicts[0]["skill"] == lock.components[0].key
