import base64
from dataclasses import replace
import io
import json
import subprocess
from types import SimpleNamespace
import zipfile

import pytest

from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.services.artifact_pipeline import ArtifactPublicationService, PublicationError
from adaos.services.artifact_pipeline.source_checkpoints import (
    RootSourceCheckpointPublisher, SourceCheckpointError, source_tree,
)
from adaos.services.root.service import RootDeveloperService
from test_artifact_publication_service import _Remote, _scenario, _promote


@pytest.fixture
def reviewed_beta(tmp_path):
    dev = _scenario(tmp_path / "dev")
    for path in dev.iterdir():
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    (dev / "tests").mkdir()
    (dev / "tests/smoke.py").write_bytes(b"assert True\n")
    tree = source_tree({p.relative_to(dev).as_posix(): p.read_bytes() for p in dev.rglob("*") if p.is_file()}, "0" * 40)
    remote = _Remote(tmp_path / "remote")
    remote.tree = tree
    service = ArtifactPublicationService(state_root=tmp_path / "state", workspace_root=tmp_path / "workspace", remote=remote)
    source = ArtifactSourceRef(forge="content-addressed-dev", repository="adaos-dev:test", revision="sha256:" + "a" * 64,
        path_scope=("scenarios/recipes/",))
    service.record_push(kind="scenario", artifact_id="recipes", artifact_dir=dev, source_ref=source, source_tree=tree)
    prepared = service.prepare_candidate(kind="scenario", artifact_id="recipes", artifact_dir=dev,
        change_ids=("reviewed-beta",), validation_evidence={"status": "passed"})
    candidate = service.decide_candidate(prepared.candidate.candidate_id, accepted=True)
    return service, remote, dev, candidate, prepared.plan


class Root:
    def __init__(self, tree):
        self.tree = tree
        self.uploads = []
        self.receipt = None
        self.lose_ack = False
        self.fault = None
        self.hidden = False
        self.fail_before_write = False
        self.read_error = None
        self.client = self

    def _transport(self):
        return {}

    def get_draft_info(self, **_kwargs):
        if self.read_error:
            raise self.read_error
        if self.receipt is None or self.hidden:
            raise FileNotFoundError
        return self.receipt

    def push_scenario_draft(self, **kwargs):
        self.uploads.append(kwargs)
        if self.fail_before_write:
            self.fail_before_write = False
            raise TimeoutError("connection lost before checkpoint write")
        self.receipt = {"stored_path": f"subnets/home/nodes/node/scenarios/{kwargs['name']}",
            "commit": "1" * 40, "sha256": kwargs["sha256"], "metadata": kwargs["metadata"]}
        if self.fault == "path": self.receipt["stored_path"] = "subnets/other/nodes/node/scenarios/other"
        if self.fault == "digest": self.receipt["sha256"] = "0" * 64
        if self.fault == "identity": self.receipt["metadata"] = {"change_id": "other-candidate"}
        if self.lose_ack:
            self.lose_ack = False
            raise TimeoutError("ack lost after source checkpoint commit")
        return self.receipt

    def tree_revision(self, _source_ref):
        return "0" * 40 if self.fault == "tree" else self.tree


def configure_publisher(service, remote):
    service.source_checkpoints.publisher = RootSourceCheckpointPublisher(remote,
        repository="registry", node_id="node", subnet_id="home")


def test_root_finalize_automatically_prepares_source_before_data_cutover(reviewed_beta, monkeypatch):
    service, _remote, _dev, candidate, _plan = reviewed_beta
    root = Root(candidate.source_tree)
    configure_publisher(service, root)
    owner = object.__new__(RootDeveloperService)
    owner.ctx = SimpleNamespace()
    owner._load_config = lambda: SimpleNamespace()
    owner._artifact_publication_service = lambda _cfg: service
    owner._promote_artifact_candidate_source = lambda candidate_id, **_kwargs: _promote(service, candidate_id)

    def adopt(_ctx, candidate_id, publish):
        assert candidate_id == candidate.candidate_id
        assert service.source_checkpoints.receipt(candidate)["status"] == "completed"
        return publish()

    monkeypatch.setattr("adaos.services.applications.local_release_transition.promote_with_local_data", adopt)
    result = owner.promote_artifact_candidate(candidate.candidate_id, permission_decision={"approved": True})
    assert result.candidate == candidate
    assert len(root.uploads) == 1
    assert service.load_promotion(candidate.candidate_id)["status"] == "completed"


def test_finalize_checkpoints_reviewed_source_without_rebuilding_beta_or_reading_current_dev(reviewed_beta, monkeypatch):
    service, _remote, dev, candidate, plan = reviewed_beta
    root = Root(candidate.source_tree)
    configure_publisher(service, root)
    before_candidate = service.candidate_store.path(candidate.candidate_id).read_bytes()
    before_package = service.package_store.read(candidate.package_digest)
    original_ui = (dev / "webui.json").read_bytes()
    (dev / "webui.json").write_bytes(b'{"changed_after_review": true}')
    (dev / "tests/smoke.py").write_bytes(b"raise RuntimeError('changed DEV')\n")
    monkeypatch.setattr("adaos.services.artifact_pipeline.publication.build_artifact_package",
        lambda *_args, **_kwargs: pytest.fail("Finalize must reuse the reviewed package"))

    service.prepare_candidate_public_source(candidate.candidate_id)
    receipt = service.source_checkpoints.receipt(candidate)
    assert receipt["source_tree"] == candidate.source_tree
    assert service.candidate_store.path(candidate.candidate_id).read_bytes() == before_candidate
    assert service.package_store.read(candidate.package_digest) == before_package
    assert service.get_candidate_release(candidate.candidate_id) == plan
    assert len(root.uploads) == 1
    upload = root.uploads[0]
    assert upload["name"].startswith("checkpoint-") and upload["name"] != "recipes"
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(upload["archive_b64"]))) as archive:
        assert archive.read("webui.json") == original_ui
        assert archive.read("tests/smoke.py") == b"assert True\n"
        assert ".adaos/package-manifest.json" not in archive.namelist()
    service.prepare_candidate_public_source(candidate.candidate_id)
    assert len(root.uploads) == 1

    promoted = _promote(service, candidate.candidate_id)
    assert promoted.candidate == candidate
    assert promoted.plan.release.release_digest == candidate.release_digest
    admitted = service.load_promotion(candidate.candidate_id)["receipts"]["admitted"]
    assert admitted["source_checkpoint"]["receipt_digest"] == receipt["receipt_digest"]
    assert admitted["verification_source_ref"]["forge"] == "adaos-root"
    assert service.package_store.read(candidate.package_digest) == before_package


def test_checkpoint_lost_ack_is_observed_and_resumed_without_second_upload(reviewed_beta):
    service, _remote, _dev, candidate, _plan = reviewed_beta
    root = Root(candidate.source_tree)
    root.lose_ack = True
    configure_publisher(service, root)
    before = service.candidate_store.path(candidate.candidate_id).read_bytes()
    with pytest.raises(TimeoutError, match="ack lost"):
        service.prepare_candidate_public_source(candidate.candidate_id)
    journal = json.loads(service.source_checkpoints.path(candidate.candidate_id).read_text(encoding="utf-8"))
    assert journal["status"] == "publishing"
    assert service.load_promotion(candidate.candidate_id) is None
    service.prepare_candidate_public_source(candidate.candidate_id)
    assert len(root.uploads) == 1
    assert service.source_checkpoints.receipt(candidate)["status"] == "completed"
    assert service.candidate_store.path(candidate.candidate_id).read_bytes() == before


def test_checkpoint_request_lost_before_write_resumes_the_exact_same_immutable_upload(reviewed_beta):
    service, _remote, _dev, candidate, _plan = reviewed_beta
    root = Root(candidate.source_tree)
    root.fail_before_write = True
    configure_publisher(service, root)
    with pytest.raises(TimeoutError):
        service.prepare_candidate_public_source(candidate.candidate_id)
    service.prepare_candidate_public_source(candidate.candidate_id)
    assert len(root.uploads) == 2 and root.uploads[0] == root.uploads[1]
    assert service.source_checkpoints.receipt(candidate)["status"] == "completed"
    assert service.candidate_store.load(candidate.candidate_id) == candidate


def test_failed_checkpoint_read_never_triggers_a_write_and_can_be_resumed(reviewed_beta):
    service, _remote, _dev, candidate, _plan = reviewed_beta
    root = Root(candidate.source_tree)
    root.read_error = TimeoutError("Root unavailable")
    configure_publisher(service, root)
    with pytest.raises(TimeoutError):
        service.prepare_candidate_public_source(candidate.candidate_id)
    assert not root.uploads
    journal = json.loads(service.source_checkpoints.path(candidate.candidate_id).read_text(encoding="utf-8"))
    assert journal["status"] == "prepared"
    root.read_error = None
    service.prepare_candidate_public_source(candidate.candidate_id)
    assert len(root.uploads) == 1


@pytest.mark.parametrize("fault", ["path", "digest", "identity", "tree"])
def test_wrong_root_checkpoint_never_admits_publication(reviewed_beta, fault):
    service, remote, _dev, candidate, _plan = reviewed_beta
    root = Root(candidate.source_tree)
    root.fault = fault
    configure_publisher(service, root)
    with pytest.raises(SourceCheckpointError, match="another source checkpoint|source tree differs"):
        service.prepare_candidate_public_source(candidate.candidate_id)
    assert service.source_checkpoints.receipt(candidate) is None
    assert service.load_promotion(candidate.candidate_id) is None
    assert remote.channel_writes == 0


def test_changed_retained_source_requires_recovery_before_upload_not_a_silent_new_beta(reviewed_beta):
    service, remote, _dev, candidate, _plan = reviewed_beta
    root = Root(candidate.source_tree)
    configure_publisher(service, root)
    retained = service.state_root / "candidate-development-sources" / candidate.candidate_id / "tests/smoke.py"
    retained.write_bytes(b"changed retained source\n")
    with pytest.raises(SourceCheckpointError, match="does not match the reviewed Beta"):
        service.prepare_candidate_public_source(candidate.candidate_id)
    assert not root.uploads and not remote.channel_writes
    assert service.candidate_store.load(candidate.candidate_id) == candidate


def test_checkpoint_receipt_cannot_be_reused_for_another_candidate_identity(reviewed_beta):
    service, _remote, _dev, candidate, _plan = reviewed_beta
    configure_publisher(service, Root(candidate.source_tree))
    service.prepare_candidate_public_source(candidate.candidate_id)
    changed = replace(candidate, release_digest="sha256:" + "c" * 64, trials=())
    with pytest.raises(SourceCheckpointError, match="another reviewed Candidate"):
        service.source_checkpoints.receipt(changed)


def test_checkpoint_does_not_publish_an_unaccepted_beta(reviewed_beta):
    service, _remote, _dev, candidate, _plan = reviewed_beta
    configure_publisher(service, Root(candidate.source_tree))
    service.candidate_store.save(replace(candidate, status="trial"))
    with pytest.raises(PublicationError, match="exact accepted Beta"):
        service.prepare_candidate_public_source(candidate.candidate_id)


@pytest.mark.parametrize("algorithm, width", [("sha1", 40), ("sha256", 64)])
def test_source_tree_matches_git_object_identity(tmp_path, algorithm, width):
    repository = tmp_path / "git-source"
    files = {"a.txt": b"a\n", "folder/z.txt": b"z\n", "folder.txt": b"file next to directory\n",
        "folder/deep/value": b"\x00\xffbinary", "unicode.txt": "исходники\n".encode("utf-8")}
    subprocess.run(["git", "init", "--quiet", "--object-format=" + algorithm, str(repository)], check=True)
    for name, data in files.items():
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    subprocess.run(["git", "-c", "core.autocrlf=false", "add", "--all"], cwd=repository, check=True)
    expected = subprocess.check_output(["git", "write-tree"], cwd=repository, text=True).strip()
    assert source_tree(files, "0" * width) == expected
