from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema

from adaos.sdk.web import implementation_binding_contract


def test_implementation_binding_guide_uses_current_abi_and_valid_examples():
    guide = implementation_binding_contract()
    root = Path(__file__).resolve().parents[1] / "src/adaos/abi"
    schema = json.loads((root / "webui.v1.schema.json").read_text(encoding="utf-8"))
    for name, receipt in guide["sources"].items():
        raw = (root / name).read_bytes()
        assert receipt == {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    for name in ("read_collection", "record_editor"):
        ref = guide["schema_refs"][name].split("#", 1)[1]
        jsonschema.Draft202012Validator({**schema, "$ref": f"#{ref}"}).validate(guide["examples"][name])
    assert len(json.dumps(guide, ensure_ascii=False).encode("utf-8")) < 12_000
    assert "Prototype" in guide["binding_rules"]["attachments"] or "Preview" in guide["binding_rules"]["attachments"]
    assert "metadata only" in guide["binding_rules"]["attachments"]
    assert "workspace.write" in guide["binding_rules"]["authorization"]
    guide["examples"].clear()
    assert implementation_binding_contract()["examples"]


def test_editor_binding_retains_loaded_revision_and_does_not_fake_upload():
    guide = implementation_binding_contract()
    editor = guide["examples"]["record_editor"]
    command = editor["actions"][0]
    assert command["params"]["revision"] == "$event.record.revision"
    assert editor["inputs"]["selectedStateKey"] == "selectedId"
    assert editor["dataSource"]["params"]["id"] == "$state.selectedId"
    assert command["invalidates"] == editor["dataSource"]["invalidationTags"]
    assert command["resultPath"] == "item"
    assert not editor["inputs"]["resetOnSuccess"]
    assert guide["examples"]["rejected_write"]["ok"] is False
