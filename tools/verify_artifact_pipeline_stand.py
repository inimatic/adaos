from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from adaos.services.artifact_pipeline.activation import WorkspaceActivationManager
from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.artifact_pipeline.remote import RemoteReleaseRepository
from adaos.services.artifact_pipeline.storage import atomic_write_json
from adaos.services.root.client import RootHttpClient


STAND_EVIDENCE_SCHEMA = "adaos.artifact.pipeline_stand_proof.v1"
RELEASE_PLAN_SCHEMA = "adaos.artifact.release_plan.v1"


class StandRemote(Protocol):
    def put_release(self, plan: ReleasePlan, archives: Mapping[str, bytes]) -> None: ...

    def get_release(self, project_id: str, release_digest: str) -> ReleasePlan: ...

    def set_channel(
        self,
        plan: ReleasePlan,
        channel: str = "stable",
        *,
        expected_release_digest: str | None,
    ) -> Any: ...

    def get_channel(self, project_id: str, channel: str = "stable") -> Any: ...

    def fetch_package(self, package: Any) -> bytes: ...


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def backend_commit_matches(expected: str, observed: str) -> bool:
    expected_token = str(expected or "").strip().lower()
    observed_token = str(observed or "").strip().lower()
    if not expected_token:
        return True
    if not observed_token:
        return False
    return expected_token.startswith(observed_token) or observed_token.startswith(
        expected_token
    )


def release_plan_from_evidence(payload: Mapping[str, Any]) -> ReleasePlan:
    release = payload.get("release")
    packages = payload.get("packages")
    bindings = payload.get("bindings")
    if not isinstance(release, Mapping):
        raise ValueError("pipeline evidence has no release object")
    if not isinstance(packages, list) or not isinstance(bindings, list):
        raise ValueError("pipeline evidence has no package/binding lists")
    reverse: dict[str, set[str]] = defaultdict(set)
    for binding in bindings:
        if not isinstance(binding, Mapping):
            raise ValueError("pipeline evidence contains a malformed binding")
        consumer = str(binding.get("consumer") or "").strip()
        dependency = str(binding.get("dependency") or "").strip()
        if not consumer or not dependency:
            raise ValueError("pipeline evidence contains an incomplete binding")
        reverse[dependency].add(consumer)
    return ReleasePlan.from_mapping(
        {
            "schema": RELEASE_PLAN_SCHEMA,
            "release": dict(release),
            "packages": packages,
            "bindings": bindings,
            "reverse_consumers": {
                key: sorted(consumers) for key, consumers in sorted(reverse.items())
            },
        }
    )


def _materialization_health(workspace_root: Path, plan: ReleasePlan):
    expected = {
        package.key: workspace_root
        / (
            package.materialization_path
            or (
                f"skills/{package.artifact_id}"
                if package.kind == "skill"
                else f"scenarios/{package.artifact_id}"
            )
        )
        for package in plan.packages
    }

    def check(lock: Any) -> dict[str, Any]:
        missing = sorted(key for key, target in expected.items() if not target.is_dir())
        lock_digests = {item.key: item.digest for item in lock.components}
        mismatched = sorted(
            package.key
            for package in plan.packages
            if lock_digests.get(package.key) != package.digest
        )
        return {
            "status": "passed" if not missing and not mismatched else "failed",
            "check": "external_package_materialization",
            "missing": missing,
            "mismatched": mismatched,
        }

    return check


def run_external_stand(
    *,
    plan: ReleasePlan,
    source_store: ContentAddressedPackageStore,
    remote: StandRemote,
    stand_root: Path,
    channel: str,
    source_evidence: Path,
    backend_health: Mapping[str, Any],
) -> dict[str, Any]:
    stand_root = Path(stand_root).expanduser().resolve()
    if stand_root.exists():
        raise FileExistsError(f"clean stand root already exists: {stand_root}")
    stand_root.mkdir(parents=True, exist_ok=False)
    evidence_path = stand_root / "evidence.json"
    release_digest = plan.release.release_digest or plan.release.computed_digest()
    evidence: dict[str, Any] = {
        "schema": STAND_EVIDENCE_SCHEMA,
        "status": "running",
        "started_at": _now_iso(),
        "source_evidence": str(Path(source_evidence).expanduser().resolve()),
        "backend": {
            "version": str(backend_health.get("version") or ""),
            "commit": str(backend_health.get("commit") or ""),
            "ready": backend_health.get("ready"),
        },
        "project_id": plan.release.project_id,
        "release_digest": release_digest,
        "channel": channel,
        "phases": [],
    }

    def record(phase: str, **details: Any) -> None:
        evidence["phases"].append({"phase": phase, "at": _now_iso(), **details})
        atomic_write_json(evidence_path, evidence)

    atomic_write_json(evidence_path, evidence)
    started = time.perf_counter()
    try:
        archives = {
            package.digest: source_store.read(package.digest) for package in plan.packages
        }
        record(
            "source-packages-verified",
            package_count=len(archives),
            bytes=sum(len(data) for data in archives.values()),
        )

        remote.put_release(plan, archives)
        record("remote-release-admitted", package_count=len(plan.packages))

        pointer = remote.set_channel(
            plan,
            channel,
            expected_release_digest=None,
        )
        observed_pointer = remote.get_channel(plan.release.project_id, channel)
        if observed_pointer.release_digest != release_digest:
            raise RuntimeError("remote channel read-back differs from admitted release")
        record(
            "remote-channel-cas",
            release_digest=observed_pointer.release_digest,
            read_back_matches=pointer.release_digest == observed_pointer.release_digest,
        )

        fetched_plan = remote.get_release(plan.release.project_id, release_digest)
        if fetched_plan != plan:
            raise RuntimeError("remote release read-back differs from source release plan")
        record("remote-release-read-back")

        package_store = ContentAddressedPackageStore(stand_root / "package-cache")
        workspace_root = stand_root / "workspace"
        manager = WorkspaceActivationManager(
            workspace_root=workspace_root,
            package_store=package_store,
            state_root=stand_root / "state",
            delayed_verification_seconds=0,
        )
        result = manager.activate(
            fetched_plan,
            idempotency_key=f"clean-stand:{channel}:{release_digest}",
            fetch_package=remote.fetch_package,
            reload_policy={
                "mode": "skip",
                "approved_by": "artifact_pipeline.clean_stand",
                "reason": "isolated clean stand has no attached runtime process",
            },
            health_check=_materialization_health(workspace_root, fetched_plan),
            expected_lock_digest=None,
        )
        delayed = manager.run_due_delayed_verifications()
        if len(delayed) != 1 or delayed[0].get("status") != "passed":
            raise RuntimeError("clean stand delayed verification did not pass exactly once")
        if not all(package_store.has(package.digest) for package in plan.packages):
            raise RuntimeError("clean stand did not fetch every package into its empty cache")
        record(
            "workspace-activated",
            operation_id=result.operation_id,
            lock_digest=result.workspace_lock.to_dict()["lock_digest"],
            delayed_verification_id=result.delayed_verification_id,
        )

        evidence.update(
            {
                "status": "passed",
                "completed_at": _now_iso(),
                "duration_seconds": round(time.perf_counter() - started, 3),
                "workspace": {
                    "root": str(workspace_root),
                    "package_count": len(plan.packages),
                    "lock": result.workspace_lock.to_dict(),
                },
                "remote_channel": observed_pointer.to_dict(),
                "delayed_verification": delayed[0],
            }
        )
        atomic_write_json(evidence_path, evidence)
        return {**evidence, "evidence_path": str(evidence_path)}
    except Exception as exc:
        evidence.update(
            {
                "status": "failed",
                "failed_at": _now_iso(),
                "duration_seconds": round(time.perf_counter() - started, 3),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        atomic_write_json(evidence_path, evidence)
        raise


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify external package transport and clean Workspace activation."
    )
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--source-package-root", type=Path)
    parser.add_argument("--stand-root", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--ca", type=Path, required=True)
    parser.add_argument("--cert", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--channel")
    parser.add_argument("--expected-backend-commit")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Acknowledge idempotent package/release upload and stand-channel CAS.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.publish:
        raise SystemExit("external stand verification requires explicit --publish")
    source_evidence = args.evidence.expanduser().resolve()
    payload = json.loads(source_evidence.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("status") != "passed":
        raise SystemExit("source evidence must be a passed pipeline proof")
    plan = release_plan_from_evidence(payload)
    release_digest = plan.release.release_digest or plan.release.computed_digest()
    channel = str(args.channel or f"stand-{release_digest.split(':', 1)[1][:12]}")
    source_package_root = (
        args.source_package_root.expanduser().resolve()
        if args.source_package_root
        else source_evidence.parent / "remote" / "packages"
    )
    for path in (args.ca, args.cert, args.key):
        if not path.expanduser().resolve().is_file():
            raise SystemExit(f"transport credential file does not exist: {path}")
    client = RootHttpClient(
        base_url=str(args.base_url).rstrip("/"),
        verify=str(args.ca.expanduser().resolve()),
        cert=(
            str(args.cert.expanduser().resolve()),
            str(args.key.expanduser().resolve()),
        ),
    )
    health = client.request("GET", "/healthz")
    if not isinstance(health, Mapping) or health.get("ready") is not True:
        raise SystemExit("backend is not ready")
    expected_commit = str(args.expected_backend_commit or "").strip().lower()
    observed_commit = str(health.get("commit") or "").strip().lower()
    if not backend_commit_matches(expected_commit, observed_commit):
        raise SystemExit(
            f"backend commit mismatch: expected {expected_commit}, observed {observed_commit}"
        )
    result = run_external_stand(
        plan=plan,
        source_store=ContentAddressedPackageStore(source_package_root),
        remote=RemoteReleaseRepository(
            client,
            verify=str(args.ca.expanduser().resolve()),
            cert=(
                str(args.cert.expanduser().resolve()),
                str(args.key.expanduser().resolve()),
            ),
        ),
        stand_root=args.stand_root,
        channel=channel,
        source_evidence=source_evidence,
        backend_health=health,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
