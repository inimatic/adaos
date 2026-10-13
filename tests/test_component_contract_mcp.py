import copy
from pathlib import Path

import pytest

from adaos.services.ui_component_contracts import load_component_contract, project_component_contract


def test_actual_root_mcp_direct_read_agrees_with_inline_prefetch_and_checks_pin(monkeypatch, tmp_path):
    from test_root_mcp_foundation import _make_client
    monkeypatch.setenv("ADAOS_ROOT_OWNER_TOKEN", "contract-owner-test")
    client = _make_client()
    snapshot = project_component_contract()
    headers = {"X-Owner-Token": "contract-owner-test", "X-AdaOS-Subnet-Id": "subnet:contract-test", "X-AdaOS-Zone": "lab"}
    for unit in snapshot["retrieval_units"]:
        response = client.post("/v1/root/mcp/call", headers=headers,
            json={"tool_id": "development.get_descriptor_item", "arguments": {**unit["locator"], "consumer": "builder"}})
        assert response.status_code == 200, response.text[:500]
        result = response.json()["response"]["result"]["descriptor_item"]
        assert result["item"]["component_contract"] == unit
        assert result["receipt"]["source_digests"]["component_declaration"] == snapshot["provenance"]["source_digest"]
    bad = client.post("/v1/root/mcp/call", headers=headers,
        json={"tool_id": "development.get_descriptor_item", "arguments": {"descriptor_id": "ui_capability_catalog",
              "item_id": "ui.form", "expected_source_digest": "sha256:" + "0" * 64}})
    assert bad.status_code >= 400 or bad.json().get("ok") is False or bad.json()["response"].get("ok") is False


def test_descriptor_header_fingerprint_changes_when_owned_contract_changes():
    from adaos.services.root_mcp.descriptor_search import _payload_headers
    from adaos.services.ui_capabilities import ui_capability_catalog
    catalog = ui_capability_catalog()
    previous = {row["item_id"]: row["fingerprint"] for row in _payload_headers("ui_capability_catalog", catalog)}
    current = copy.deepcopy(catalog)
    next(item for item in current["components"] if item["id"] == "ui.form")["component_contract"]["content"]["effects"].append("new.effect")
    after = {row["item_id"]: row["fingerprint"] for row in _payload_headers("ui_capability_catalog", current)}
    assert previous["ui.form"] != after["ui.form"]
    assert previous["ui.table"] == after["ui.table"]


def test_cbs_discovery_does_not_promote_portable_candidates_to_callable_tools(tmp_path):
    from test_semantic_registry_query import _fixture_records, _query, _write_registry, REVISION
    from adaos.services.contract_context_admission import discover_semantic_candidates
    profile, capability, binding, delivery, evidence, requirement = _fixture_records()
    registry = tmp_path / "registry"
    index = _write_registry(registry, (capability, binding, delivery, evidence))
    query = _query(index, profile, requirement)
    result = discover_semantic_candidates(query, registry_root=registry, index=index, registry_revision=REVISION)
    assert result["registry_result"]["status"] == "matched"
    assert result["registry_result"]["requirements"][0]["candidates"][0]["capability_contract"]["digest"] == capability.digest
    assert result["binding_availability"] == "portable_candidate_only_local_admission_pending"
    assert result["callable_tools"] == []
    query["policy_inputs"]["allowed_publisher_refs"] = []
    query["policy_inputs"]["denied_publisher_refs"] = ["application:gmail_provider"]
    rejected = discover_semantic_candidates(query, registry_root=registry, index=index, registry_revision=REVISION)
    assert rejected["registry_result"]["status"] == "unmatched"
    assert rejected["registry_result"]["requirements"][0]["candidates"] == []
