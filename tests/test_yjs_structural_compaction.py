from __future__ import annotations

import hashlib

import pytest
import y_py as Y

from adaos.services.yjs import structural_compaction as compaction


def _snapshot_with_history() -> bytes:
    doc = Y.YDoc()
    with doc.begin_transaction() as txn:
        ui = doc.get_map("ui")
        ui.set(txn, "current_scenario", "old")
        ui.set(txn, "discarded", "x" * 4096)
        nested = Y.YMap({})
        ui.set(txn, "application", nested)
        nested.set(txn, "title", "Пример")
        rows = Y.YArray([])
        nested.set(txn, "rows", rows)
        rows.extend(txn, [{"id": "one"}, {"id": "two"}])
        doc.get_map("data").set(txn, "note", Y.YText("English и русский"))
    with doc.begin_transaction() as txn:
        ui = doc.get_map("ui")
        ui.set(txn, "current_scenario", "test04_recipes")
        ui.pop(txn, "discarded")
    return bytes(Y.encode_state_as_update(doc))


def test_structural_compaction_preserves_semantics_and_shared_types(tmp_path) -> None:
    path = tmp_path / "dev1-dev.ysnap"
    path.write_bytes(_snapshot_with_history())
    before = compaction.inspect_snapshot(path)

    dry_run = compaction.compact_snapshot(
        path,
        expected_source_digest=before["source_digest"],
        offline_witness=compaction.OFFLINE_WITNESS,
    )
    assert dry_run["applied"] is False
    assert path.stat().st_size == before["source_bytes"]
    assert dry_run["compacted_bytes"] < before["source_bytes"]

    result = compaction.compact_snapshot(
        path,
        expected_source_digest=before["source_digest"],
        offline_witness=compaction.OFFLINE_WITNESS,
        apply=True,
    )
    after = compaction.inspect_snapshot(path)
    assert result["applied"] is True
    assert result["rolled_back"] is False
    assert after["semantic_digest"] == before["semantic_digest"]
    assert after["shared_type_digest"] == before["shared_type_digest"]
    assert after["source_digest"] == result["compacted_digest"]
    assert result["backup_path"]
    assert path.with_name(path.name + result["backup_path"].split(path.name, 1)[1]).is_file()


def test_structural_compaction_rejects_stale_digest_and_missing_witness(tmp_path) -> None:
    path = tmp_path / "sample.ysnap"
    path.write_bytes(_snapshot_with_history())
    digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

    with pytest.raises(compaction.StructuralCompactionError, match="offline witness"):
        compaction.compact_snapshot(
            path,
            expected_source_digest=digest,
            offline_witness="yes",
            apply=True,
        )
    with pytest.raises(compaction.StructuralCompactionError, match="source digest changed"):
        compaction.compact_snapshot(
            path,
            expected_source_digest="sha256:" + "0" * 64,
            offline_witness=compaction.OFFLINE_WITNESS,
            apply=True,
        )


def test_webspace_compaction_refuses_a_live_room(monkeypatch, tmp_path) -> None:
    path = tmp_path / "active.ysnap"
    path.write_bytes(_snapshot_with_history())
    digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(compaction, "ystore_path_for_webspace", lambda _webspace_id: path)
    import adaos.services.yjs.gateway_ws as gateway_ws

    monkeypatch.setattr(gateway_ws, "live_webspace_ids", lambda: ["active"])
    with pytest.raises(compaction.StructuralCompactionError, match="live Webspace"):
        compaction.compact_webspace_snapshot(
            "active",
            expected_source_digest=digest,
            offline_witness=compaction.OFFLINE_WITNESS,
            apply=True,
        )
