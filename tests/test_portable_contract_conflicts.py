from pathlib import Path

import pytest

from adaos.domain.capability_binding_state import CapabilityContract
from adaos.services.capability_binding_state.catalog import PortableContractCatalog, PortableContractConflict


def _contract(operation, ref="capability:resource.records.manage", version="1.0.0"):
    return CapabilityContract.create(capability_ref=ref, version=version, title="Records",
        operations=[{"operation_id": operation, "input_schema": {"type": "object"},
                     "output_schema": {"type": "object"}, "errors": []}])


def test_conflict_retains_exact_contract_and_reports_both_operation_sets(tmp_path: Path):
    catalog = PortableContractCatalog(tmp_path)
    installed, incoming = _contract("list"), _contract("query_records")
    catalog.put(installed)
    original = catalog.index_path.read_bytes()
    with pytest.raises(PortableContractConflict) as error:
        catalog.put_many((_contract("other", ref="capability:new.records"), incoming))
    details = error.value.to_dict()
    assert details["existing_digest"] == installed.digest
    assert details["incoming_digest"] == incoming.digest
    assert details["different_fields"] == ["operations"]
    assert details["existing_operations"] == ["list"]
    assert details["incoming_operations"] == ["query_records"]
    assert details["recovery"]["requires_source_change"] is True
    assert catalog.index_path.read_bytes() == original
    assert catalog.load(installed.digest, CapabilityContract) == installed
    assert len(list(catalog.records_root.glob("*/*.json"))) == 1


def test_conflicting_records_in_one_new_batch_do_not_write_partial_records(tmp_path: Path):
    catalog = PortableContractCatalog(tmp_path)
    with pytest.raises(PortableContractConflict) as error:
        catalog.put_many((_contract("list"), _contract("query_records")))
    assert error.value.to_dict()["existing_operations"] == ["list"]
    assert not catalog.index_path.exists()
    assert not list(catalog.records_root.glob("*/*.json"))


@pytest.mark.parametrize("ref,version", [
    ("capability:automation.records.manage", "1.0.0"),
    ("capability:resource.records.manage", "2.0.0"),
])
def test_new_identity_or_version_can_coexist_without_replacing_the_installed_contract(tmp_path, ref, version):
    catalog = PortableContractCatalog(tmp_path)
    installed, repaired = _contract("list"), _contract("query_records", ref, version)
    catalog.put(installed)
    catalog.put(repaired)
    assert catalog.load(installed.digest, CapabilityContract) == installed
    assert catalog.load(repaired.digest, CapabilityContract) == repaired
