from __future__ import annotations


import base64
import io
import os
import zipfile

import pytest

from adaos.services.artifact_pipeline.publication import PublicationError
from adaos.services.artifact_pipeline.source_snapshots import SourceSnapshot, SourceSnapshotError
from adaos.services.artifact_pipeline import source_snapshots
from adaos.services.root.service import create_zip_bytes
from test_root_draft_metadata import _checkpoint_service


def test_source_archive_is_deterministic_and_keeps_development_tests(tmp_path):
    source = tmp_path / "source"; source.mkdir()
    (source / "skill.yaml").write_bytes(b"name: fixture\nversion: 1.0.0\n")
    (source / "tests").mkdir()
    (source / "tests/check.py").write_bytes(b"assert True\n")
    (source / "node_modules").mkdir()
    (source / "node_modules/generated.js").write_bytes(b"ignored")
    first = create_zip_bytes(source)
    os.utime(source / "skill.yaml", (1700000000, 1700000000))
    assert create_zip_bytes(source) == first
    snapshot = SourceSnapshot.from_archive(first)
    assert set(dict(snapshot.entries)) == {"skill.yaml", "tests/check.py"}


@pytest.mark.parametrize("names", [
    ["../outside"], ["a\\b"], ["a", "a"], ["A.py", "a.py"],
    ["file", "file/child"], [".git/config"], ["NUL"], [".env"],
])
def test_source_archive_rejects_unsafe_or_ambiguous_inventory(names):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        for name in names:
            entry = zipfile.ZipInfo()
            entry.filename = name
            archive.writestr(entry, b"source")
    with pytest.raises(SourceSnapshotError):
        SourceSnapshot.from_archive(data.getvalue())


def test_capture_releases_all_handles_and_detects_path_changes(tmp_path, monkeypatch):
    source = tmp_path / "source"; source.mkdir()
    (source / "a").write_bytes(b"v1")
    original = source_snapshots._inventory_paths
    count = 0
    def inventory(*args):
        nonlocal count
        count += 1
        if count == 2:
            (source / "late").write_bytes(b"new")
        return original(*args)
    monkeypatch.setattr(source_snapshots, "_inventory_paths", inventory)
    with pytest.raises(SourceSnapshotError, match="paths changed"):
        SourceSnapshot.capture(source)
    (source / "a").write_bytes(b"handles released")


@pytest.mark.skipif(os.name != "nt", reason="Windows mandatory source sharing admission")
def test_windows_capture_holds_earlier_files_against_later_writes(tmp_path, monkeypatch):
    source = tmp_path / "source"; source.mkdir()
    (source / "a").write_bytes(b"v1")
    (source / "b").write_bytes(b"v1")
    original = source_snapshots._open_source
    denied = []
    def open_source(path):
        handle = original(path)
        if path.name == "b":
            with pytest.raises(OSError):
                (source / "a").write_bytes(b"v2")
            denied.append(True)
        return handle
    monkeypatch.setattr(source_snapshots, "_open_source", open_source)
    snapshot = SourceSnapshot.capture(source)
    assert denied and dict(snapshot.entries) == {"a": b"v1", "b": b"v1"}


@pytest.mark.parametrize("lost_reply", [False, True])
def test_checkpoint_binds_sent_source_and_preserves_later_edits(tmp_path, lost_reply):
    service, publication, source, _ = _checkpoint_service(tmp_path)
    (source / "payload.txt").write_bytes(b"v1\n")
    state = {"pushes": 0}
    class Client:
        def get_draft_info(self, **kwargs):
            if "receipt" in state:
                return state["receipt"]
            raise FileNotFoundError("no previous checkpoint")
        def push_skill_draft(self, **kwargs):
            state["pushes"] += 1
            snapshot = SourceSnapshot.from_archive(base64.b64decode(kwargs["archive_b64"]))
            state["receipt"] = {"stored_path": "subnets/dev/nodes/node/skills/recipe_skill",
                "commit": "a" * 40, "tree_sha": snapshot.tree(), "sha256": kwargs["sha256"],
                "metadata": kwargs["metadata"]}
            (source / "payload.txt").write_bytes(b"v2\n")
            (source / "later.txt").write_bytes(b"preserve me\n")
            if lost_reply:
                raise TimeoutError("reply lost after commit")
            return state["receipt"]
    service._client = lambda _: Client()
    metadata = {"change_id": "checkpoint-frozen-source"}
    if lost_reply:
        with pytest.raises(TimeoutError):
            service._push_artifact("skills", "recipe_skill", metadata=metadata)
    result = service._push_artifact("skills", "recipe_skill", metadata=metadata)
    record = publication.load_pushed_source("skill", "recipe_skill")
    data, verified = publication.package_store.read_verified(record.package.digest)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.read("payload.txt") == b"v1\n"
        assert "later.txt" not in archive.namelist()
    assert result.version == "1.0.1"
    assert (source / "payload.txt").read_bytes() == b"v2\n"
    assert (source / "later.txt").is_file()
    assert state["pushes"] == 1
    with pytest.raises(PublicationError, match="changed"):
        publication.verify_pushed_source_content(record, source)


def test_checkpoint_rollback_never_reverts_a_late_manifest_edit(tmp_path):
    service, _, source, _ = _checkpoint_service(tmp_path)
    edited = b"name: recipe_skill\nversion: 9.0.0\ndependencies: []\n"
    class Client:
        def get_draft_info(self, **kwargs):
            raise FileNotFoundError("no checkpoint")
        def push_skill_draft(self, **kwargs):
            (source / "skill.yaml").write_bytes(edited)
            raise TimeoutError("unknown remote outcome")
    service._client = lambda _: Client()
    with pytest.raises(TimeoutError):
        service._push_artifact("skills", "recipe_skill", metadata={"change_id": "late-manifest"})
    assert (source / "skill.yaml").read_bytes() == edited


def test_checkpoint_rejects_remote_tree_that_does_not_match_the_sent_snapshot(tmp_path):
    from adaos.services.root.service import RootServiceError
    service, publication, source, _ = _checkpoint_service(tmp_path)
    class Client:
        def get_draft_info(self, **kwargs):
            raise FileNotFoundError("no checkpoint")
        def push_skill_draft(self, **kwargs):
            return {"stored_path": "subnets/dev/nodes/node/skills/recipe_skill",
                "commit": "a" * 40, "tree_sha": "b" * 40, "sha256": kwargs["sha256"],
                "metadata": kwargs["metadata"]}
    service._client = lambda _: Client()
    with pytest.raises(RootServiceError, match="differs from the frozen"):
        service._push_artifact("skills", "recipe_skill", metadata={"change_id": "wrong-tree"})
    assert not publication.pushed_source_path("skill", "recipe_skill").exists()

def test_frozen_snapshot_does_not_retain_mutable_input_aliases():
    raw = bytearray(b'original')
    entries = [('file.txt', raw)]
    snapshot = SourceSnapshot(entries)
    digest = snapshot.digest
    raw[:] = b'changed'
    entries.clear()
    assert snapshot.digest == digest
    assert snapshot.entries == (('file.txt', b'original'),)
