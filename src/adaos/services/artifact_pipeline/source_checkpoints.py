"""Publish the exact retained Beta source without rebuilding or replacing it."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
from typing import Any, Callable, Mapping
import zipfile

from adaos.domain.artifact_release import ArtifactSourceRef, canonical_payload_digest
from .candidates import CandidateRecord, CandidateStore
from .packages import (ContentAddressedPackageStore, _assert_publishable_file,
                       _canonical_package_bytes, _build_excluded)
from .cbs_authoring import compile_cbs_provider_files
from .releases import ReleasePlan
from .storage import atomic_write_json, mutation_lock


SOURCE_CHECKPOINT_SCHEMA = "adaos.artifact.candidate_source_checkpoint.v1"


class SourceCheckpointError(RuntimeError):
    pass


class SourceCheckpointWriteRejected(SourceCheckpointError):
    pass


class RetainedGitSourceReader:
    """Read exact immutable trees from the existing private recovery checkpoints."""
    def __init__(self, recovery_root: Path):
        self.root = Path(recovery_root).resolve()

    def __call__(self, candidate: CandidateRecord) -> Mapping[str, bytes] | None:
        tree = candidate.source_tree or ""
        if len(tree) not in {40, 64} or any(c not in "0123456789abcdef" for c in tree):
            raise SourceCheckpointError("Invalid retained source tree identity")
        env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
        # One level of registered operational checkpoints, never a recursive
        # search of user projects or a checkout of mutable DEV.
        for repo in sorted(self.root.glob("*/source-checkpoint"))[:64]:
            if repo.is_symlink() or not repo.resolve().is_relative_to(self.root) or not (repo / ".git").exists():
                continue
            command = ["git", "-C", str(repo)]
            listing = subprocess.run([*command, "ls-tree", "-rzl", tree], capture_output=True, env=env, timeout=10)
            if listing.returncode:
                continue
            entries = [entry.split(b"\t", 1) for entry in listing.stdout.split(b"\0") if entry]
            if len(entries) > 20000:
                raise SourceCheckpointError("Retained source exceeds file limit")
            blobs = []; declared_size = 0
            for metadata, name in entries:
                mode, kind, digest, size = metadata.split()
                if mode != b"100644" or kind != b"blob":
                    raise SourceCheckpointError("Retained source must contain regular files only")
                declared_size += int(size)
                if declared_size > 50_000_000:
                    raise SourceCheckpointError("Retained source exceeds size limit")
                blobs.append((name.decode("utf8"), digest))
            result = subprocess.run([*command, "cat-file", "--batch"],
                input=b"".join(digest + b"\n" for _, digest in blobs), capture_output=True, env=env, timeout=20, check=True)
            stream = io.BytesIO(result.stdout); files = {}; total = 0
            for name, digest in blobs:
                actual, kind, size = stream.readline().split()
                total += int(size)
                if actual != digest or kind != b"blob" or total > 50_000_000:
                    raise SourceCheckpointError("Invalid or oversized retained source")
                raw = stream.read(int(size))
                if len(raw) != int(size) or stream.read(1) != b"\n":
                    raise SourceCheckpointError("Incomplete retained source blob")
                _assert_publishable_file(name, raw, error_type=SourceCheckpointError)
                files[name] = raw
            if source_tree(files, tree) != tree:
                raise SourceCheckpointError("Retained checkpoint tree changed")
            return files
        return None


def source_tree(files: Mapping[str, bytes], expected_tree: str) -> str:
    """Git's immutable tree identity for regular source files and directories."""
    algorithm = "sha256" if len(expected_tree) == 64 else "sha1"
    def object_digest(kind: str, data: bytes) -> bytes:
        return hashlib.new(algorithm, kind.encode() + b" " + str(len(data)).encode() + b"\0" + data).digest()
    root: dict[str, Any] = {}
    for name, data in files.items():
        path = PurePosixPath(name)
        if path.is_absolute() or not path.parts or any(part in {".", "..", ".git"} for part in path.parts):
            raise SourceCheckpointError("Unsafe retained source path")
        node = root
        for part in path.parts[:-1]:
            node = node.setdefault(part, {})
        node[path.name] = data
    def tree(node: Mapping[str, Any]) -> bytes:
        records = []
        for name, value in sorted(node.items(), key=lambda item: (item[0] + ("/" if isinstance(item[1], dict) else "")).encode("utf-8")):
            directory = isinstance(value, dict)
            digest = tree(value) if directory else object_digest("blob", value)
            records.append((b"40000 " if directory else b"100644 ") + name.encode("utf-8") + b"\0" + digest)
        return object_digest("tree", b"".join(records))
    return tree(root).hex()


def source_archive(files: Mapping[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files.items()):
            _assert_publishable_file(name, data, error_type=SourceCheckpointError)
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return output.getvalue()


class CandidateSourceCheckpoints:
    def __init__(self, state_root: Path, packages: ContentAddressedPackageStore,
                 publisher: Callable[..., Mapping[str, Any]] | None = None,
                 source_reader: Callable[[CandidateRecord], Mapping[str, bytes] | None] | None = None):
        self.state_root, self.packages, self.publisher = Path(state_root), packages, publisher
        self.source_reader = source_reader

    def path(self, candidate_id: str) -> Path:
        filename = CandidateStore(self.state_root / "candidates").path(candidate_id).name
        return self.state_root / "candidate-source-checkpoints" / filename

    @staticmethod
    def identity(candidate: CandidateRecord) -> dict[str, Any]:
        return {"candidate_id": candidate.candidate_id, "candidate_digest": candidate.digest,
            "project_id": candidate.project_id, "release_digest": candidate.release_digest,
            "package_digest": candidate.package_digest, "source_tree": candidate.source_tree}

    def receipt(self, candidate: CandidateRecord) -> dict[str, Any] | None:
        path = self.path(candidate.candidate_id)
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(payload, dict) or payload.get("schema") != SOURCE_CHECKPOINT_SCHEMA
                or any(payload.get(key) != value for key, value in self.identity(candidate).items())):
            raise SourceCheckpointError("Source checkpoint belongs to another reviewed Candidate")
        if payload.get("status") != "completed":
            return None
        unsigned = {key: value for key, value in payload.items() if key != "receipt_digest"}
        if payload.get("receipt_digest") != canonical_payload_digest(unsigned):
            raise SourceCheckpointError("Source checkpoint receipt changed")
        ref = ArtifactSourceRef.from_mapping(payload["source_ref"])
        if ref.forge != "adaos-root":
            raise SourceCheckpointError("Source checkpoint was not published to Root")
        return payload

    def prepare_archive(self, candidate: CandidateRecord, plan: ReleasePlan) -> tuple[str, bytes]:
        reference = candidate.verification_source_ref or candidate.source_ref
        if len(reference.path_scope) != 1:
            raise SourceCheckpointError("Exact Beta component source is unavailable")
        parts = reference.path_scope[0].strip("/").split("/")
        if len(parts) != 2 or parts[0] not in {"skills", "scenarios"}:
            raise SourceCheckpointError("Exact Beta component source is unavailable")
        kind, artifact_id = parts[0][:-1], parts[1]
        package = next((p for p in plan.release.components if (p.kind, p.artifact_id) == (kind, artifact_id)), None)
        if package is None or package.digest != candidate.package_digest:
            raise SourceCheckpointError("Beta source component differs from the reviewed release")
        data, verified = self.packages.read_verified(package.digest)
        if verified.ref != package:
            raise SourceCheckpointError("Retained Beta package differs from the reviewed release")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            files = {name: archive.read(name) for name in verified.file_names}
        runtime_files = dict(files)
        retained = self.state_root / "candidate-development-sources" / self.path(candidate.candidate_id).stem
        project_tests = retained / kind / artifact_id / "tests"
        tests = project_tests if project_tests.is_dir() else retained / "tests"
        if tests.is_symlink():
            raise SourceCheckpointError("Retained Beta source must not contain links")
        if tests.is_dir():
            for path in sorted(tests.rglob("*")):
                if path.is_symlink():
                    raise SourceCheckpointError("Retained Beta source must not contain links")
                if path.is_file():
                    files["tests/" + path.relative_to(tests).as_posix()] = path.read_bytes()
        if candidate.source_tree and source_tree(files, candidate.source_tree) != candidate.source_tree and self.source_reader:
            recovered = self.source_reader(candidate)
            if recovered is not None:
                # Source and runtime have different representations. Recover
                # original bytes, then prove the compiler/canonicalizer still
                # produces this exact immutable package; never rebuild a Beta.
                if source_tree(recovered, candidate.source_tree) != candidate.source_tree:
                    raise SourceCheckpointError("Recovered source differs from the reviewed Beta")
                compilation = compile_cbs_provider_files(recovered, kind=kind, allow_generated_outputs=True)
                compiled = {**recovered, **(compilation.generated_files if compilation else {})}
                expected_runtime = {name: _canonical_package_bytes(raw) for name, raw in compiled.items()
                                    if not _build_excluded(PurePosixPath(name))}
                if expected_runtime != runtime_files:
                    raise SourceCheckpointError("Recovered source does not produce the reviewed Beta package")
                files = dict(recovered)
        if not candidate.source_tree or source_tree(files, candidate.source_tree) != candidate.source_tree:
            raise SourceCheckpointError(
                "Retained source does not match the reviewed Beta. Restore its exact source snapshot before Finalize."
            )
        return kind, source_archive(files)

    def ensure(self, candidate: CandidateRecord, plan: ReleasePlan) -> dict[str, Any]:
        path = self.path(candidate.candidate_id)
        with mutation_lock(path.with_suffix(".lock"), timeout_s=30):
            if candidate.status != "accepted":
                raise SourceCheckpointError("Source publication requires the exact accepted Beta")
            existing = self.receipt(candidate)
            if existing:
                return existing
            kind, archive = self.prepare_archive(candidate, plan)
            if self.publisher is None:
                raise SourceCheckpointError("Candidate source publisher is unavailable")
            previous = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            resume = previous.get("status") == "publishing"
            intent = {"schema": SOURCE_CHECKPOINT_SCHEMA, **self.identity(candidate), "status": "publishing" if resume else "prepared",
                "checkpoint_name": "checkpoint-" + candidate.digest.removeprefix("sha256:"),
                "archive_sha256": hashlib.sha256(archive).hexdigest()}
            if previous and previous.get("archive_sha256") != intent["archive_sha256"]:
                raise SourceCheckpointError("Prepared source checkpoint archive changed")
            atomic_write_json(path, intent)
            def before_publish() -> None:
                intent["status"] = "publishing"
                atomic_write_json(path, intent)
            try:
                published = dict(self.publisher(kind=kind, name=intent["checkpoint_name"], archive=archive,
                    expected_tree=candidate.source_tree, change_id="finalize-source:" + candidate.digest,
                    before_publish=before_publish))
            except SourceCheckpointWriteRejected:
                intent["status"] = "rejected"
                atomic_write_json(path, intent)
                raise
            ref = ArtifactSourceRef.from_mapping(published["source_ref"])
            if ref.forge != "adaos-root" or published.get("source_tree") != candidate.source_tree:
                raise SourceCheckpointError("Published source differs from the reviewed Beta")
            receipt = {**intent, "status": "completed", "source_ref": ref.to_dict()}
            receipt["receipt_digest"] = canonical_payload_digest(receipt)
            atomic_write_json(path, receipt)
            return receipt


class RootSourceCheckpointPublisher:
    """Publish an immutable component copy without replacing its mutable DEV draft."""
    def __init__(self, remote, *, repository: str, node_id: str, subnet_id: str):
        self.remote, self.repository, self.node_id, self.subnet_id = remote, repository, node_id, subnet_id

    def __call__(self, *, kind: str, name: str, archive: bytes, expected_tree: str, change_id: str,
                 before_publish: Callable[[], None] | None = None) -> dict[str, Any]:
        if not self.node_id or not self.subnet_id:
            raise SourceCheckpointError("Root source publisher identity is unavailable")
        client, transport = self.remote.client, self.remote._transport()
        plural = kind + "s"
        digest = hashlib.sha256(archive).hexdigest()
        try:
            response = client.get_draft_info(kind=plural, name=name, node_id=self.node_id, **transport)
        except Exception as exc:
            if not isinstance(exc, FileNotFoundError) and getattr(exc, "status_code", None) != 404:
                raise
            response = None
        if response is None:
            # Reconcile before writing. Only an authoritative 404 permits the
            # exact same content at this Candidate-specific immutable name.
            # Forge serializes owned-path writes and does not commit unchanged
            # files, so a lost request can be retried without another version.
            push = client.push_skill_draft if kind == "skill" else client.push_scenario_draft
            if before_publish:
                before_publish()
            try:
                response = push(name=name, node_id=self.node_id, archive_b64=base64.b64encode(archive).decode("ascii"),
                    sha256=digest, metadata={"change_id": change_id}, message="Checkpoint reviewed Beta source for Finalize", **transport)
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if isinstance(status, int) and 400 <= status < 500 and status != 408:
                    raise SourceCheckpointWriteRejected(f"Root rejected the source checkpoint: {exc}") from exc
                raise
        expected_path = f"subnets/{self.subnet_id}/nodes/{self.node_id}/{plural}/{name}"
        if (response.get("stored_path", "").replace("\\", "/").rstrip("/") != expected_path
                or response.get("sha256") not in {None, "", digest}
                or (response.get("metadata") or {}).get("change_id") != change_id):
            raise SourceCheckpointError("Root returned another source checkpoint")
        ref = ArtifactSourceRef(forge="adaos-root", repository=self.repository,
            revision=str(response.get("commit") or ""), path_scope=(expected_path + "/",))
        if self.remote.tree_revision(ref) != expected_tree:
            raise SourceCheckpointError("Root source tree differs from the reviewed Beta")
        return {"source_ref": ref.to_dict(), "source_tree": expected_tree}
