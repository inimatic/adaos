from __future__ import annotations

import copy
import hashlib
import json

import pytest

from adaos.services.builder.workflow_authority import (
    WorkflowAuthority,
    WorkflowAuthorityRejected,
)


PROJECT = "scenario:authority-test"


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "dev" / "prompt_state.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "workflow": {"generation": 3, "prototype": {"head_revision": "001"}},
                "private_context": "Retain without exposing in the receipt",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_unadopted_resolution_is_read_only_and_does_not_create_core_state(tmp_path):
    authority = WorkflowAuthority(tmp_path / "state")
    assert authority.resolve(PROJECT) is None
    assert not authority.state_dir.exists()


def test_initial_adoption_preserves_logical_state_and_exact_source_digest(
    tmp_path, source
):
    authority = WorkflowAuthority(tmp_path / "state")
    original = source.read_bytes()
    result = authority.adopt(PROJECT, source)
    state = authority._read(authority.resolve(PROJECT))
    receipt = state.pop("workflow_authority")
    assert state == json.loads(original)
    assert receipt["source_digest"] == "sha256:" + hashlib.sha256(original).hexdigest()
    assert receipt["project_ref"] == PROJECT
    assert "private_context" not in receipt
    assert source.read_bytes() == original
    assert result["copied"] is True


def test_adoption_is_sticky_after_source_loss_or_mutation(tmp_path, source):
    authority = WorkflowAuthority(tmp_path / "state")
    authority.adopt(PROJECT, source)
    head = authority.resolve(PROJECT)
    before = head.read_bytes()
    source.write_text('{"workflow":{"generation":999}}', encoding="utf-8")
    assert authority.adopt(PROJECT, source)["copied"] is False
    assert head.read_bytes() == before
    source.unlink()
    assert authority.adopt(PROJECT, source)["copied"] is False
    assert head.read_bytes() == before


def test_missing_core_head_with_receipt_never_falls_back_to_dev(tmp_path, source):
    authority = WorkflowAuthority(tmp_path / "state")
    authority.adopt(PROJECT, source)
    authority.head_path(PROJECT).unlink()
    with pytest.raises(WorkflowAuthorityRejected, match="explicit recovery required"):
        authority.resolve(PROJECT)
    with pytest.raises(WorkflowAuthorityRejected, match="explicit recovery required"):
        authority.adopt(PROJECT, source)
    assert source.exists()


def test_corrupt_core_head_never_reboots_authority_from_dev(tmp_path, source):
    authority = WorkflowAuthority(tmp_path / "state")
    authority.adopt(PROJECT, source)
    authority.head_path(PROJECT).write_text("{", encoding="utf-8")
    with pytest.raises(WorkflowAuthorityRejected, match="corrupt"):
        authority.adopt(PROJECT, source)
    assert authority.head_path(PROJECT).read_text(encoding="utf-8") == "{"


def test_failed_initial_head_write_leaves_legacy_unadopted(
    tmp_path, source, monkeypatch
):
    import adaos.services.builder.workflow_authority as module

    authority = WorkflowAuthority(tmp_path / "state")
    original = source.read_bytes()

    def fail_copy(*_):
        raise OSError("controlled initial-copy failure")

    monkeypatch.setattr(module, "atomic_write_json", fail_copy)
    with pytest.raises(OSError, match="initial-copy failure"):
        authority.adopt(PROJECT, source)
    assert authority.resolve(PROJECT) is None
    assert source.read_bytes() == original
    assert not authority.receipt_path(PROJECT).exists()


def test_failed_receipt_recovers_without_overwriting_late_paid_observation(
    tmp_path, source, monkeypatch
):
    import adaos.services.builder.workflow_authority as module

    authority = WorkflowAuthority(tmp_path / "state")
    write = module.atomic_write_json

    def fail_receipt(path, value):
        if path == authority.receipt_path(PROJECT):
            raise OSError("controlled adoption receipt failure")
        return write(path, value)

    with monkeypatch.context() as scoped:
        scoped.setattr(module, "atomic_write_json", fail_receipt)
        with pytest.raises(OSError, match="adoption receipt failure"):
            authority.adopt(PROJECT, source)
    assert authority.resolve(PROJECT) == authority.head_path(PROJECT)
    late = authority._read(authority.resolve(PROJECT))
    late["paid_observation"] = {"calls": 1, "run_id": "original-run"}
    authority.write(PROJECT, late)
    before = authority.head_path(PROJECT).read_bytes()
    source.write_text('{"stale":"source"}', encoding="utf-8")
    restarted = WorkflowAuthority(authority.state_dir)
    assert restarted.adopt(PROJECT, source)["copied"] is False
    assert restarted.head_path(PROJECT).read_bytes() == before
    assert (
        restarted.validate(PROJECT, late)["adoption_id"]
        == late["workflow_authority"]["adoption_id"]
    )


def test_wrong_owner_and_changed_receipt_are_not_authority(tmp_path, source):
    authority = WorkflowAuthority(tmp_path / "state")
    authority.adopt(PROJECT, source)
    original = authority._read(authority.resolve(PROJECT))
    with pytest.raises(WorkflowAuthorityRejected):
        authority.validate("scenario:other", original)
    mutated = copy.deepcopy(original)
    mutated["workflow_authority"]["project_ref"] = "scenario:other"
    with pytest.raises(WorkflowAuthorityRejected, match="cannot replace"):
        authority.write(PROJECT, mutated)
    assert authority._read(authority.resolve(PROJECT)) == original


@pytest.mark.parametrize("content", ["[]", "{", '{"workflow_authority":{}}'])
def test_invalid_source_cannot_bootstrap_core_authority(tmp_path, source, content):
    authority = WorkflowAuthority(tmp_path / "state")
    source.write_text(content, encoding="utf-8")
    with pytest.raises(WorkflowAuthorityRejected):
        authority.adopt(PROJECT, source)
    assert authority.resolve(PROJECT) is None
