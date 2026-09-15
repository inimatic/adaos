import json
from pathlib import Path

import jsonschema
import pytest

from adaos.services.builder.placement import BuilderPlacementError, normalize_project_placement


ABI = Path(__file__).resolve().parents[1] / "src/adaos/abi"


def placement(mode):
    return {"kind": "trial", "result_ref": {"id": "candidate-example"},
            "target": {"webspace_id": "desktop", "space_kind": "workspace"},
            "data_mode": mode, "trial_activation_ref": "trial:candidate-example",
            "safety": {"approved": True, "reversible": True,
                       "data_transition": {"operation_id": "operation-example", "contract_digest": "sha256:example"}}}


def test_every_trial_activation_data_mode_survives_placement_projection():
    activation = json.loads((ABI / "trial.activation.v1.schema.json").read_text(encoding="utf-8"))
    schema = json.loads((ABI / "project.placement.v1.schema.json").read_text(encoding="utf-8"))
    modes = activation["properties"]["data_mode"]["enum"]
    assert "snapshot" in modes
    for mode in modes:
        source = placement(mode)
        result = normalize_project_placement(source, project_ref="scenario:example")
        jsonschema.Draft202012Validator(schema).validate(result)
        assert result["data_mode"] == mode
        assert result["safety"] == source["safety"]
        assert result["trial_activation_ref"] == source["trial_activation_ref"]


@pytest.mark.parametrize("mode", ["real", "live"])
def test_writable_external_placement_still_requires_approval(mode):
    source = placement(mode)
    source["safety"] = {}
    with pytest.raises(BuilderPlacementError, match="explicit approval"):
        normalize_project_placement(source, project_ref="scenario:example")


def test_unknown_storage_mode_is_not_admitted():
    with pytest.raises(BuilderPlacementError, match="data_mode"):
        normalize_project_placement(placement("unknown"), project_ref="scenario:example")
