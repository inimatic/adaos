from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    WorkspaceLock,
    canonical_payload_digest,
)
from adaos.services.artifact_pipeline.packages import (
    ContentAddressedPackageStore,
    build_artifact_package,
)
from adaos.services.artifact_pipeline.releases import ReleasePlan


SOURCE_RECOVERY_PLAN_SCHEMA = "adaos.builder.source_recovery_plan.v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _token(value: object) -> str:
    return str(value or "").strip()


def _digest_token(value: str) -> str:
    digest = _token(value).lower()
    if not digest.startswith("sha256:") or len(digest) != 71:
        raise ValueError("release digest must be sha256:<64 lowercase hex characters>")
    token = digest.split(":", 1)[1]
    if any(char not in "0123456789abcdef" for char in token):
        raise ValueError("release digest must be sha256:<64 lowercase hex characters>")
    return token


def _read_mapping(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{path.name} must contain an object")
    return dict(value)


@dataclass(slots=True)
class BuilderSourceRecoveryService:
    """Explain stable/workspace/dev source drift without mutating any source."""

    state_dir: Path
    workspace_root: Path
    dev_skills_root: Path
    dev_scenarios_root: Path
    dev_projects_root: Path

    @property
    def artifact_state_root(self) -> Path:
        return Path(self.state_dir).expanduser().resolve() / "artifact_pipeline"

    @property
    def package_store(self) -> ContentAddressedPackageStore:
        return ContentAddressedPackageStore(self.artifact_state_root / "packages")

    def _workspace_lock(self) -> tuple[WorkspaceLock | None, str | None]:
        path = Path(self.workspace_root).expanduser().resolve() / ".adaos" / "workspace.lock.json"
        if not path.is_file():
            return None, "workspace_lock_missing"
        try:
            return WorkspaceLock.from_mapping(_read_mapping(path)), None
        except Exception as exc:
            return None, f"workspace_lock_invalid:{type(exc).__name__}:{exc}"

    def _release_plan(self, project_id: str, release_digest: str) -> tuple[ReleasePlan | None, str | None]:
        try:
            digest_token = _digest_token(release_digest)
        except ValueError as exc:
            return None, f"release_digest_invalid:{exc}"
        path = (
            self.artifact_state_root
            / "release-cache"
            / "projects"
            / project_id
            / "releases"
            / f"{digest_token}.json"
        )
        if not path.is_file():
            return None, "release_plan_missing"
        try:
            plan = ReleasePlan.from_mapping(_read_mapping(path))
        except Exception as exc:
            return None, f"release_plan_invalid:{type(exc).__name__}:{exc}"
        actual = plan.release.release_digest or plan.release.computed_digest()
        if plan.release.project_id != project_id or actual != release_digest:
            return None, "release_plan_identity_mismatch"
        return plan, None

    def _artifact_root(self, source: str, package: ArtifactPackageRef) -> Path:
        if source == "workspace":
            return (
                Path(self.workspace_root).expanduser().resolve()
                / ("skills" if package.kind == "skill" else "scenarios")
                / package.artifact_id
            )
        root = self.dev_skills_root if package.kind == "skill" else self.dev_scenarios_root
        return Path(root).expanduser().resolve() / package.artifact_id

    def _source_state(self, source: str, package: ArtifactPackageRef) -> dict[str, Any]:
        root = self._artifact_root(source, package)
        result: dict[str, Any] = {
            "source": source,
            "path": str(root),
            "present": root.is_dir(),
            "valid": None,
            "digest": None,
            "matches_locked_package": None,
            "version": None,
            "error": None,
        }
        if not root.is_dir():
            return result
        try:
            built = build_artifact_package(
                root,
                kind=package.kind,
                source_ref=package.source_ref,
            )
        except Exception as exc:
            result.update(
                {
                    "valid": False,
                    "matches_locked_package": False,
                    "error": f"{type(exc).__name__}:{exc}",
                }
            )
            return result
        result.update(
            {
                "valid": True,
                "digest": built.ref.digest,
                "matches_locked_package": built.ref.digest == package.digest,
                "version": built.ref.version,
            }
        )
        return result

    @staticmethod
    def _classification(
        *,
        package_available: bool,
        workspace: Mapping[str, Any],
        dev: Mapping[str, Any],
    ) -> tuple[str, str, bool]:
        if not package_available:
            return "package_missing", "restore_locked_package", True
        workspace_present = workspace.get("present") is True
        dev_present = dev.get("present") is True
        workspace_valid = workspace.get("valid") is not False
        dev_valid = dev.get("valid") is not False
        workspace_base = workspace.get("matches_locked_package") is True
        dev_base = dev.get("matches_locked_package") is True

        if workspace_present and not workspace_valid:
            return "workspace_source_invalid", "review_workspace_source", True
        if dev_present and not dev_valid:
            return "dev_source_invalid", "review_dev_source", True
        if not workspace_present and not dev_present:
            return "unmaterialized", "materialize_locked_package", False
        if workspace_present and workspace_base and not dev_present:
            return "needs_dev_materialization", "materialize_locked_package", False
        if not workspace_present and dev_present and dev_base:
            return "workspace_missing", "use_existing_dev_source", False
        if not workspace_present and dev_present:
            return "dev_ahead_workspace_missing", "use_existing_dev_source", False
        if workspace_present and workspace_base and dev_present and dev_base:
            return "clean", "use_existing_dev_source", False
        if workspace_present and workspace_base and dev_present:
            return "dev_ahead", "use_existing_dev_source", False
        if workspace_present and not workspace_base and not dev_present:
            return "workspace_drift", "import_workspace_delta", True
        if workspace_present and not workspace_base and dev_present and dev_base:
            return "workspace_drift", "review_workspace_delta", True
        if workspace.get("digest") == dev.get("digest"):
            return "converged_unpublished", "use_existing_dev_source", False
        return "three_way_conflict", "reconcile_three_way", True

    def plan(
        self,
        *,
        kind: str,
        artifact_id: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        target_kind = _token(kind).lower().rstrip("s")
        target_id = _token(artifact_id)
        requested_project = _token(project_id)
        if target_kind not in {"project", "scenario", "skill"}:
            raise ValueError("kind must be project, scenario, or skill")
        if not target_id:
            raise ValueError("artifact_id is required")

        lock, lock_error = self._workspace_lock()
        if lock is None:
            payload = {
                "schema": SOURCE_RECOVERY_PLAN_SCHEMA,
                "target": {"kind": target_kind, "id": target_id},
                "status": "blocked",
                "safe_to_apply": False,
                "requires_review": True,
                "workspace_lock_digest": None,
                "projects": [],
                "components": [],
                "errors": [lock_error or "workspace_lock_unavailable"],
            }
            payload["plan_digest"] = canonical_payload_digest(payload)
            payload["generated_at"] = _now_iso()
            return payload

        project_rows: list[dict[str, Any]] = []
        package_bindings: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        target_ref = f"{target_kind}:{target_id}" if target_kind != "project" else ""
        matching_slot_count = 0

        for slot in sorted(lock.slots, key=lambda item: (item.project_id, item.slot_id)):
            if requested_project and slot.project_id != requested_project:
                continue
            if target_kind == "project" and slot.project_id != target_id:
                continue
            plan, release_error = self._release_plan(slot.project_id, slot.release_digest)
            if plan is None:
                if target_kind == "project" or requested_project == slot.project_id:
                    matching_slot_count += 1
                    errors.append(f"project:{slot.project_id}:{release_error}")
                continue
            owned = {item.key for item in plan.release.components}
            package_keys = {item.key for item in plan.packages}
            if target_ref and target_ref not in package_keys:
                continue
            matching_slot_count += 1
            project_rows.append(
                {
                    "project_id": slot.project_id,
                    "slot_id": slot.slot_id,
                    "release": slot.release,
                    "release_digest": slot.release_digest,
                    "role": "owner" if not target_ref or target_ref in owned else "consumer",
                    "workspace_project_path": str(
                        Path(self.workspace_root).expanduser().resolve()
                        / "projects"
                        / slot.project_id
                    ),
                    "workspace_project_present": (
                        Path(self.workspace_root).expanduser().resolve()
                        / "projects"
                        / slot.project_id
                    ).is_dir(),
                    "dev_project_path": str(
                        Path(self.dev_projects_root).expanduser().resolve() / slot.project_id
                    ),
                    "dev_project_present": (
                        Path(self.dev_projects_root).expanduser().resolve() / slot.project_id
                    ).is_dir(),
                }
            )
            for package in plan.packages:
                if target_ref and package.key != target_ref:
                    continue
                binding = package_bindings.setdefault(
                    package.key,
                    {"package": package, "projects": []},
                )
                binding["projects"].append(
                    {
                        "project_id": slot.project_id,
                        "role": "owned" if package.key in owned else "dependency",
                        "release_digest": slot.release_digest,
                    }
                )

        if matching_slot_count == 0 and target_ref:
            package = next((item for item in lock.components if item.key == target_ref), None)
            if package is not None:
                package_bindings[target_ref] = {"package": package, "projects": []}
                errors.append(f"{target_ref}:project_ownership_unresolved")

        component_rows: list[dict[str, Any]] = []
        for key in sorted(package_bindings):
            binding = package_bindings[key]
            package = binding["package"]
            workspace = self._source_state("workspace", package)
            dev = self._source_state("dev", package)
            package_available = self.package_store.has(package.digest)
            classification, recommended_action, requires_review = self._classification(
                package_available=package_available,
                workspace=workspace,
                dev=dev,
            )
            component_rows.append(
                {
                    "component_ref": package.key,
                    "kind": package.kind,
                    "artifact_id": package.artifact_id,
                    "locked_version": package.version,
                    "locked_package_digest": package.digest,
                    "package_available": package_available,
                    "project_bindings": sorted(
                        binding["projects"],
                        key=lambda item: (item["project_id"], item["role"]),
                    ),
                    "workspace": workspace,
                    "dev": dev,
                    "classification": classification,
                    "recommended_action": recommended_action,
                    "requires_review": requires_review,
                }
            )

        blocked_classes = {
            "package_missing",
            "workspace_source_invalid",
            "dev_source_invalid",
            "three_way_conflict",
        }
        blocked = not component_rows or any(
            item["classification"] in blocked_classes for item in component_rows
        )
        requires_review = bool(errors) or any(item["requires_review"] for item in component_rows)
        if blocked:
            status = "blocked"
        elif requires_review:
            status = "review_required"
        elif any(
            item["classification"] in {"unmaterialized", "needs_dev_materialization"}
            for item in component_rows
        ):
            status = "ready_to_materialize"
        else:
            status = "source_available"

        payload = {
            "schema": SOURCE_RECOVERY_PLAN_SCHEMA,
            "target": {
                "kind": target_kind,
                "id": target_id,
                "project_id": requested_project or None,
            },
            "status": status,
            "safe_to_apply": status == "ready_to_materialize",
            "requires_review": requires_review,
            "workspace_lock_digest": lock.to_dict()["lock_digest"],
            "workspace_lock_revision": lock.lock_revision,
            "projects": project_rows,
            "components": component_rows,
            "errors": errors,
        }
        payload["plan_digest"] = canonical_payload_digest(payload)
        payload["generated_at"] = _now_iso()
        return payload


__all__ = ["BuilderSourceRecoveryService", "SOURCE_RECOVERY_PLAN_SCHEMA"]
