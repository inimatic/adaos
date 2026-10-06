import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from adaos.services.skill.data_route_contract import automation_stream_contract
from adaos.services.skill.activation import load_skill_stream_receiver_patterns, stream_receiver_event_admission


def test_stream_contract_is_exact_bounded_abi_closure_and_digest():
    contract = automation_stream_contract()
    assert len(json.dumps(contract, ensure_ascii=False)) < 12000
    manifest = json.loads((Path(__file__).parents[1] / "src/adaos/abi/skill.schema.json").read_text())
    schema = contract["json_schema"]
    for name, definition in schema["$defs"].items():
        assert definition == manifest["$defs"][name]
    validator = Draft202012Validator(schema)
    validator.validate(contract["example"])
    with pytest.raises(ValidationError):
        validator.validate({"data_routes": [{"surface": "widget:test", "route": "stream", "invented_policy": True}]})
    digest = contract.pop("digest")
    assert digest == "sha256:" + hashlib.sha256(json.dumps(contract, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def test_example_declares_only_its_receiver_and_denies_foreign_receiver(tmp_path):
    root = tmp_path / "sample_skill"
    root.mkdir()
    (root / "skill.yaml").write_text(json.dumps(automation_stream_contract()["example"]))
    patterns = load_skill_stream_receiver_patterns(tmp_path, "sample_skill")
    assert patterns == ("sample.status",)
    for topic in ("webio.stream.snapshot.requested", "webio.stream.subscription.changed"):
        assert stream_receiver_event_admission(patterns, {"receiver": "sample.status"}, topic)["allowed"]
        assert not stream_receiver_event_admission(patterns, {"receiver": "foreign.notes"}, topic)["allowed"]


def test_worker_includes_authoritative_stream_contract():
    from adaos.services.skill_factory_worker import _implementation_sdk_contract_bundle

    bundle = _implementation_sdk_contract_bundle()
    assert bundle["manifest_contracts"]["skill_stream_data_routes"] == automation_stream_contract()
