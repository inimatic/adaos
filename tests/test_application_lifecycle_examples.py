from __future__ import annotations

import inspect
import json
from pathlib import Path

from adaos.sdk import applications
from adaos.sdk.builder import applications as builder_applications


def test_lifecycle_examples_cover_every_public_transition_with_real_sdk_functions() -> None:
    payload = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "src" / "adaos" / "abi" / "application.lifecycle.examples.v1.json"
        ).read_text(encoding="utf-8")
    )
    transitions = {item["id"]: item for item in payload["transitions"]}
    assert {
        "create", "materialize_dev", "preview_dev", "create_trial", "accept_trial",
        "publish_link_trial", "promote_first_stable", "publish_prerelease",
        "promote_next_stable", "publish_stable_source", "install_or_update",
        "install_trial_link", "select_update_track", "remove",
    } == set(transitions)
    for item in payload["transitions"]:
        prefix, _, function_name = item["sdk"].rpartition(".")
        module = builder_applications if prefix == "adaos.sdk.builder.applications" else applications
        function = getattr(module, function_name)
        parameters = inspect.signature(function).parameters
        assert set(item["required"]) <= set(parameters), item["id"]
