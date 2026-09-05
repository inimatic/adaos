from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

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
from adaos.services.artifact_pipeline.storage import (
    atomic_write_bytes,
    atomic_write_json,
    mutation_lock,
    replace_with_retry,
)
from adaos.services.builder.sources import BuilderProjectSourceService

SOURCE_RECOVERY_PLAN_SCHEMA = "adaos.builder.source_recovery_plan.v1"
SOURCE_RECOVERY_RECEIPT_SCHEMA = "adaos.builder.source_recovery_receipt.v1"
SOURCE_RECOVERY_OPERATION_SCHEMA = "adaos.builder.source_recovery_operation.v1"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


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
    """Plan and apply reviewed stable/workspace/dev source recovery."""

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

    @property
    def recovery_root(self) -> Path:
        return Path(self.state_dir).expanduser().resolve() / "builder" / "source_recovery"

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

    @staticmethod
    def _admissible_decisions(
        classification: str,
        *,
        editable: bool,
        workspace_present: bool,
        dev_present: bool,
    ) -> list[str]:
        if not editable:
            return ["read_only"]
        if classification in {"package_missing", "workspace_source_invalid", "dev_source_invalid"}:
            return []
        if classification in {"unmaterialized", "needs_dev_materialization"}:
            return ["reset_to_locked"]
        if classification in {"workspace_missing", "dev_ahead_workspace_missing", "dev_ahead"}:
            return ["keep_dev"]
        if classification == "clean":
            return ["keep_dev"] if dev_present else ["reset_to_locked"]
        if classification == "converged_unpublished":
            return ["keep_dev"]
        if classification == "workspace_drift":
            decisions = ["reset_to_locked"]
            if workspace_present:
                decisions.insert(0, "adopt_workspace")
            if dev_present:
                decisions.insert(0, "keep_dev")
            return decisions
        if classification == "three_way_conflict":
            decisions = ["reset_to_locked"]
            if workspace_present:
                decisions.insert(0, "adopt_workspace")
            if dev_present:
                decisions.insert(0, "keep_dev")
            return decisions
        return []

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
            project_bindings = sorted(
                binding["projects"],
                key=lambda item: (item["project_id"], item["role"]),
            )
            editable = any(item["role"] == "owned" for item in project_bindings)
            admissible_decisions = self._admissible_decisions(
                classification,
                editable=editable,
                workspace_present=workspace.get("present") is True,
                dev_present=dev.get("present") is True,
            )
            component_rows.append(
                {
                    "component_ref": package.key,
                    "kind": package.kind,
                    "artifact_id": package.artifact_id,
                    "locked_version": package.version,
                    "locked_package_digest": package.digest,
                    "package_available": package_available,
                    "project_bindings": project_bindings,
                    "editable": editable,
                    "workspace": workspace,
                    "dev": dev,
                    "classification": classification,
                    "recommended_action": recommended_action,
                    "requires_review": requires_review,
                    "admissible_decisions": admissible_decisions,
                }
            )

        blocked_classes = {
            "package_missing",
            "workspace_source_invalid",
            "dev_source_invalid",
            "three_way_conflict",
        }
        editable_rows = [item for item in component_rows if item["editable"]]
        dependency_rows = [item for item in component_rows if not item["editable"]]
        dependency_blocked = any(
            item["classification"] == "package_missing" for item in dependency_rows
        )
        blocked = not editable_rows or dependency_blocked or any(
            item["classification"] in blocked_classes for item in editable_rows
        )
        requires_review = bool(errors) or any(item["requires_review"] for item in editable_rows)
        dependency_review_required = any(item["requires_review"] for item in dependency_rows)
        if blocked:
            status = "blocked"
        elif requires_review:
            status = "review_required"
        elif any(
            item["classification"] in {"unmaterialized", "needs_dev_materialization"}
            for item in editable_rows
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
            "dependency_review_required": dependency_review_required,
            "applicable_with_decisions": bool(editable_rows)
            and not dependency_blocked
            and all(item["admissible_decisions"] for item in editable_rows),
            "workspace_lock_digest": lock.to_dict()["lock_digest"],
            "workspace_lock_revision": lock.lock_revision,
            "projects": project_rows,
            "components": component_rows,
            "errors": errors,
        }
        payload["plan_digest"] = canonical_payload_digest(payload)
        payload["generated_at"] = _now_iso()
        return payload

    def _owner_project(self, plan: Mapping[str, Any]) -> tuple[str, ReleasePlan]:
        owners = sorted(
            {
                str(item.get("project_id") or "")
                for item in plan.get("projects") or []
                if isinstance(item, Mapping) and item.get("role") == "owner"
            }
        )
        if len(owners) != 1:
            raise ValueError("source recovery requires exactly one owning Project")
        project_id = owners[0]
        project_row = next(
            item
            for item in plan.get("projects") or []
            if isinstance(item, Mapping) and item.get("project_id") == project_id
        )
        release_plan, error = self._release_plan(
            project_id,
            str(project_row.get("release_digest") or ""),
        )
        if release_plan is None:
            raise ValueError(f"owning Project release is unavailable: {error}")
        return project_id, release_plan

    def _synthesized_project_definition(
        self,
        release_plan: ReleasePlan,
        *,
        actor: str,
    ) -> dict[str, Any]:
        release = release_plan.release
        composition = release.composition_lock
        if composition is not None:
            owned = [
                {
                    "ref": member.ref,
                    "role": member.role,
                    "exposure": member.exposure,
                    "lifecycle": member.lifecycle,
                    "relations": list(member.relations),
                }
                for member in composition.members
            ]
            profiles = list(composition.profiles)
            entrypoints = [dict(item) for item in composition.entrypoints]
            compatibility = dict(composition.compatibility)
            lifecycle = dict(composition.lifecycle)
            project_dependencies = [
                {
                    "ref": item.project_ref,
                    "version": item.version_spec or None,
                    "lifecycle": "shared",
                    "relations": ["uses"],
                }
                for item in composition.project_dependencies
            ]
        else:
            owned = [
                {
                    "ref": package.key,
                    "role": "primary" if index == 0 else "implementation",
                    "exposure": "application",
                    "lifecycle": "bound",
                    "relations": ["uses"],
                }
                for index, package in enumerate(release.components)
            ]
            profiles = []
            primary_scenario = next(
                (package for package in release.components if package.kind == "scenario"),
                None,
            )
            entrypoints = (
                [
                    {
                        "id": "default",
                        "presentation": primary_scenario.key,
                        "default": True,
                        "bindings": {},
                    }
                ]
                if primary_scenario is not None
                else []
            )
            compatibility = {}
            lifecycle = {}
            project_dependencies = []
        if not isinstance(lifecycle.get("uninstall"), Mapping):
            lifecycle = {
                "uninstall": {
                    "components": "remove_if_unreferenced",
                    "runtime_data": "retain",
                    "source_artifacts": "retain",
                }
            }
        component_dependencies = [
            {
                "ref": dependency.key,
                "version": dependency.version_spec or dependency.version,
                "lifecycle": "shared",
                "relations": ["uses"],
            }
            for dependency in release.resolved_dependencies
        ]
        payload: dict[str, Any] = {
            "schema": "adaos.project.v1",
            "kind": "project",
            "id": release.project_id,
            "version": release.version,
            "profiles": profiles,
            "components": {
                "owned": owned,
                "dependencies": [*project_dependencies, *component_dependencies],
            },
            "entrypoints": entrypoints,
            "catalog": {
                "title": release.project_id.replace("_", " ").replace("-", " ").title(),
                "description": (
                    "Recovered development declaration from immutable ProjectRelease "
                    f"{release.release_digest}; review before publication."
                ),
                "categories": ["development"],
                "tags": ["recovered"],
            },
            "publication": {
                "stage": "alpha",
                "visibility": "unlisted",
                "channel": "stable",
            },
            "compatibility": compatibility,
            "lifecycle": lifecycle,
            "created_at": _now_iso(),
            "created_by": actor,
        }
        from adaos.sdk.developer.compositions import validate

        return validate(payload)

    def _snapshot_source(
        self,
        *,
        project_id: str,
        package: ArtifactPackageRef,
        source: str,
        actor: str,
        plan_digest: str,
    ) -> tuple[dict[str, Any], bytes]:
        root = self._artifact_root(source, package)
        built = build_artifact_package(root, kind=package.kind, source_ref=package.source_ref)
        self.package_store.put(built.archive_bytes, expected_digest=built.ref.digest)
        source_result = BuilderProjectSourceService(state_dir=self.state_dir).add_bytes(
            kind="project",
            project_id=project_id,
            name=(
                f"{package.artifact_id}-{source}-"
                f"{built.ref.digest.removeprefix('sha256:')[:12]}.zip"
            ),
            payload=built.archive_bytes,
            media_type="application/zip",
            role="source_recovery_snapshot",
            origin={
                "kind": "builder_source_recovery",
                "source": source,
                "component_ref": package.key,
                "plan_digest": plan_digest,
                "actor": actor,
            },
        )
        return {
            "component_ref": package.key,
            "source": source,
            "package_digest": built.ref.digest,
            "source_ref": source_result["source"]["object_ref"],
            "bundle_digest": source_result["bundle"]["digest"],
        }, built.archive_bytes

    def _stage_package(self, package_digest: str, target: Path) -> Path:
        staged = target.parent / f".{target.name}.source-recovery-stage-{uuid4().hex}"
        self.package_store.extract_to_directory(package_digest, staged)
        return staged

    @staticmethod
    def _switch_tree(staged: Path, target: Path) -> Path | None:
        backup = target.parent / f".{target.name}.source-recovery-backup-{uuid4().hex}"
        moved = False
        try:
            if target.exists():
                replace_with_retry(target, backup)
                moved = True
            replace_with_retry(staged, target)
        except Exception:
            if moved and backup.exists() and not target.exists():
                replace_with_retry(backup, target)
            raise
        return backup if moved else None

    def apply(
        self,
        *,
        kind: str,
        artifact_id: str,
        expected_plan_digest: str,
        decisions: Mapping[str, str] | None = None,
        project_id: str | None = None,
        actor: str = "builder",
    ) -> dict[str, Any]:
        expected = _token(expected_plan_digest)
        if not expected:
            raise ValueError("expected_plan_digest is required")
        selected = {
            _token(key): _token(value).lower()
            for key, value in dict(decisions or {}).items()
            if _token(key)
        }
        actor_token = _token(actor) or "builder"
        receipt_path = self.recovery_root / "receipts" / f"{_digest_token(expected)}.json"
        operation_path = self.recovery_root / "operations" / f"{_digest_token(expected)}.json"
        with mutation_lock(self.recovery_root / ".mutation.lock"):
            if receipt_path.is_file():
                receipt = _read_mapping(receipt_path)
                reviewed = receipt.get("reviewed_decisions", receipt.get("decisions"))
                if receipt.get("plan_digest") != expected or reviewed != selected:
                    raise ValueError("source recovery receipt exists with different reviewed decisions")
                return {**receipt, "idempotent": True}
            if operation_path.is_file():
                operation = _read_mapping(operation_path)
                if operation.get("status") == "rolled_back" and not operation.get("change_id"):
                    operation_path.unlink()
                else:
                    raise RuntimeError(
                        "incomplete source recovery operation requires inspection before retry: "
                        f"status={operation.get('status') or 'unknown'}"
                    )
            plan = self.plan(
                kind=kind,
                artifact_id=artifact_id,
                project_id=project_id,
            )
            if plan.get("plan_digest") != expected:
                raise ValueError("source recovery plan changed; refresh and review the new digest")
            if not plan.get("applicable_with_decisions"):
                raise ValueError("source recovery plan cannot be applied safely")
            owner_project_id, release_plan = self._owner_project(plan)
            lock_before = str(plan.get("workspace_lock_digest") or "")

            lock, lock_error = self._workspace_lock()
            if lock is None:
                raise ValueError(f"WorkspaceLock is unavailable: {lock_error}")
            locked_packages = {item.key: item for item in lock.components}
            component_refs = {
                str(item.get("component_ref") or "")
                for item in plan.get("components") or []
                if isinstance(item, Mapping)
            }
            unknown_decisions = sorted(set(selected).difference(component_refs))
            if unknown_decisions:
                raise ValueError(
                    "source recovery decisions reference unknown components: "
                    f"{','.join(unknown_decisions)}"
                )
            effective_decisions: dict[str, str] = {}
            for component in plan.get("components") or []:
                if not isinstance(component, Mapping):
                    continue
                component_ref = str(component.get("component_ref") or "")
                admissible = list(component.get("admissible_decisions") or [])
                if component.get("editable") is not True:
                    if selected.get(component_ref) not in {None, "read_only"}:
                        raise ValueError(f"dependency {component_ref} is read-only")
                    effective_decisions[component_ref] = "read_only"
                    continue
                decision = selected.get(component_ref)
                if decision is None and len(admissible) == 1:
                    decision = admissible[0]
                if decision not in admissible:
                    raise ValueError(
                        f"reviewed decision is required for {component_ref}; "
                        f"allowed={','.join(admissible) or 'none'}"
                    )
                if component_ref not in locked_packages:
                    raise ValueError(f"locked package is unavailable for {component_ref}")
                effective_decisions[component_ref] = decision

            operation: dict[str, Any] = {
                "schema": SOURCE_RECOVERY_OPERATION_SCHEMA,
                "plan_digest": expected,
                "workspace_lock_digest": lock_before,
                "project_id": owner_project_id,
                "reviewed_decisions": selected,
                "decisions": effective_decisions,
                "status": "prepared",
                "actor": actor_token,
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }
            atomic_write_json(operation_path, operation)

            component_results: list[dict[str, Any]] = []
            evidence: list[dict[str, Any]] = []
            staged_by_ref: dict[str, tuple[Path, Path]] = {}
            backups: list[tuple[Path, Path | None]] = []
            created_project_root: Path | None = None
            created_project_manifest: Path | None = None
            project_manifest: dict[str, Any] | None = None
            project_root = Path(self.dev_projects_root).expanduser().resolve() / owner_project_id
            project_manifest_path = project_root / "project.yaml"
            try:
                if project_manifest_path.is_file():
                    from adaos.sdk.developer.compositions import validate

                    current_project = validate(
                        yaml.safe_load(project_manifest_path.read_text(encoding="utf-8")) or {}
                    )
                    if current_project.get("id") != owner_project_id:
                        raise ValueError(
                            "DEV Project manifest identity does not match source recovery owner"
                        )
                    declared_owned = {
                        str(item.get("ref") or "")
                        for item in current_project["components"]["owned"]
                    }
                    recovered_owned = {
                        ref
                        for ref, decision in effective_decisions.items()
                        if decision != "read_only"
                    }
                    missing_owned = sorted(recovered_owned.difference(declared_owned))
                    if missing_owned:
                        raise ValueError(
                            "DEV Project manifest does not own recovered components: "
                            f"{','.join(missing_owned)}"
                        )
                else:
                    if project_root.exists() and any(project_root.iterdir()):
                        raise ValueError(
                            "DEV Project directory exists without project.yaml; preserve and review it first"
                        )
                    project_manifest = self._synthesized_project_definition(
                        release_plan,
                        actor=actor_token,
                    )

                for component in plan.get("components") or []:
                    if not isinstance(component, Mapping):
                        continue
                    component_ref = str(component.get("component_ref") or "")
                    editable = component.get("editable") is True
                    decision = effective_decisions[component_ref]
                    if not editable:
                        component_results.append(
                            {
                                "component_ref": component_ref,
                                "role": "dependency",
                                "decision": "read_only",
                                "status": "unchanged",
                            }
                        )
                        continue
                    package = locked_packages[component_ref]
                    for source in ("workspace", "dev"):
                        source_state = component.get(source)
                        if (
                            isinstance(source_state, Mapping)
                            and source_state.get("present") is True
                            and source_state.get("valid") is True
                            and source_state.get("matches_locked_package") is not True
                        ):
                            snapshot, _ = self._snapshot_source(
                                project_id=owner_project_id,
                                package=package,
                                source=source,
                                actor=actor_token,
                                plan_digest=expected,
                            )
                            evidence.append(snapshot)
                    target = self._artifact_root("dev", package)
                    if decision == "keep_dev":
                        component_results.append(
                            {
                                "component_ref": component_ref,
                                "role": "owned",
                                "decision": decision,
                                "status": "preserved",
                                "target": str(target),
                            }
                        )
                        continue
                    source_digest = package.digest
                    if decision == "adopt_workspace":
                        workspace_root = self._artifact_root("workspace", package)
                        built = build_artifact_package(
                            workspace_root,
                            kind=package.kind,
                            source_ref=package.source_ref,
                        )
                        self.package_store.put(
                            built.archive_bytes,
                            expected_digest=built.ref.digest,
                        )
                        source_digest = built.ref.digest
                    staged_by_ref[component_ref] = (
                        self._stage_package(source_digest, target),
                        target,
                    )
                    component_results.append(
                        {
                            "component_ref": component_ref,
                            "role": "owned",
                            "decision": decision,
                            "status": "staged",
                            "source_package_digest": source_digest,
                            "target": str(target),
                        }
                    )

                for component_ref in sorted(staged_by_ref):
                    staged, target = staged_by_ref[component_ref]
                    backup = self._switch_tree(staged, target)
                    backups.append((target, backup))
                    next(
                        item for item in component_results if item["component_ref"] == component_ref
                    )["status"] = "materialized"

                if project_manifest is not None:
                    project_root_preexisting = project_root.exists()
                    project_root.mkdir(parents=True, exist_ok=True)
                    if not project_root_preexisting:
                        created_project_root = project_root
                    atomic_write_bytes(
                        project_manifest_path,
                        yaml.safe_dump(
                            project_manifest,
                            sort_keys=False,
                            allow_unicode=True,
                        ).encode("utf-8"),
                    )
                    created_project_manifest = project_manifest_path

                lock_after, lock_error = self._workspace_lock()
                if lock_after is None or lock_after.to_dict()["lock_digest"] != lock_before:
                    raise ValueError(
                        "WorkspaceLock changed during source recovery: "
                        f"{lock_error or lock_after.to_dict()['lock_digest']}"
                    )

                operation.update(
                    {
                        "status": "source_applied",
                        "components": component_results,
                        "evidence_refs": evidence,
                        "updated_at": _now_iso(),
                    }
                )
                atomic_write_json(operation_path, operation)

                from adaos.services.builder.workflow import BuilderWorkflowService

                change_id = f"chg_recovery_{expected.removeprefix('sha256:')[:20]}"
                issue_rows = [
                    {
                        "issue_id": f"RECOVERY_{index:03d}",
                        "title": f"Validate recovered source for {item['component_ref']}",
                        "lane": "automation",
                        "status": "open",
                        "acceptance_criteria": [
                            "Recovered source validates against the current SDK and ABI contracts.",
                            "A new Candidate and isolated Trial are linked to this recovery.",
                            "WorkspaceLock changes only after explicit user acceptance and Publication.",
                        ],
                        "semantic_refs": [item["component_ref"], f"source-recovery:{expected}"],
                    }
                    for index, item in enumerate(
                        (row for row in component_results if row["role"] == "owned"),
                        start=1,
                    )
                ]
                workflow = BuilderWorkflowService(
                    dev_skills_root=self.dev_skills_root,
                    dev_scenarios_root=self.dev_scenarios_root,
                    dev_projects_root=self.dev_projects_root,
                    workspace_root=self.workspace_root,
                    state_dir=self.state_dir,
                    require_active_builder_package=False,
                ).transition(
                    "project",
                    owner_project_id,
                    "plan_change_set",
                    actor=actor_token,
                    metadata={
                        "change_set_id": change_id,
                        "request": (
                            "Validate and publish development source recovered from "
                            f"WorkspaceLock using plan {expected}."
                        ),
                        "issues": issue_rows,
                        "parallel": True,
                        "source_message_ids": [],
                        "source_recovery_plan_digest": expected,
                    },
                )
                operation.update(
                    {
                        "status": "change_planned",
                        "change_id": change_id,
                        "change_status": workflow.get("workflow", {}).get("change", {}).get(
                            "status"
                        ),
                        "updated_at": _now_iso(),
                    }
                )
                atomic_write_json(operation_path, operation)
                receipt = {
                    "schema": SOURCE_RECOVERY_RECEIPT_SCHEMA,
                    "receipt_id": f"source-recovery:{expected}",
                    "plan_digest": expected,
                    "workspace_lock_digest": lock_before,
                    "project_id": owner_project_id,
                    "release_digest": release_plan.release.release_digest,
                    "reviewed_decisions": selected,
                    "decisions": effective_decisions,
                    "components": component_results,
                    "evidence_refs": evidence,
                    "project_manifest": {
                        "path": str(project_manifest_path),
                        "synthesized": project_manifest is not None,
                        "base_project_definition_digest": (
                            release_plan.release.composition_lock.project_definition_digest
                            if release_plan.release.composition_lock is not None
                            else None
                        ),
                    },
                    "change_id": change_id,
                    "change_status": (
                        workflow.get("workflow", {}).get("change", {}).get("status")
                    ),
                    "status": "applied_to_dev",
                    "next_required": [
                        "validate_change",
                        "build_candidate",
                        "activate_trial",
                        "accept_or_reject_trial",
                        "publish_project_release",
                    ],
                    "actor": actor_token,
                    "created_at": _now_iso(),
                    "idempotent": False,
                }
                receipt["receipt_digest"] = canonical_payload_digest(receipt)
                atomic_write_json(receipt_path, receipt)
                operation.update(
                    {
                        "status": "completed",
                        "receipt_digest": receipt["receipt_digest"],
                        "updated_at": _now_iso(),
                    }
                )
                try:
                    atomic_write_json(operation_path, operation)
                except Exception:
                    pass
                for _, backup in backups:
                    if backup is not None and backup.is_dir():
                        shutil.rmtree(backup, ignore_errors=True)
                return receipt
            except Exception as exc:
                for staged, _ in staged_by_ref.values():
                    if staged.is_dir():
                        shutil.rmtree(staged, ignore_errors=True)
                if created_project_root is not None and created_project_root.is_dir():
                    shutil.rmtree(created_project_root, ignore_errors=True)
                elif created_project_manifest is not None and created_project_manifest.is_file():
                    created_project_manifest.unlink(missing_ok=True)
                for target, backup in reversed(backups):
                    if backup is None:
                        if target.is_dir():
                            shutil.rmtree(target, ignore_errors=True)
                        continue
                    if target.is_dir():
                        shutil.rmtree(target, ignore_errors=True)
                    if backup.is_dir():
                        backup.replace(target)
                operation.update(
                    {
                        "status": "rolled_back",
                        "error": f"{type(exc).__name__}:{exc}",
                        "updated_at": _now_iso(),
                    }
                )
                try:
                    atomic_write_json(operation_path, operation)
                except Exception:
                    pass
                raise


__all__ = [
    "SOURCE_RECOVERY_OPERATION_SCHEMA",
    "SOURCE_RECOVERY_PLAN_SCHEMA",
    "SOURCE_RECOVERY_RECEIPT_SCHEMA",
    "BuilderSourceRecoveryService",
]
