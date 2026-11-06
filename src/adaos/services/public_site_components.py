from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from uuid import uuid4

from adaos.services.agent_context import get_ctx
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.public_site_delivery import build_delivery_bundle
from adaos.services.site_studio.contracts import digest, validate_contract


REGISTRY_SCHEMA = "adaos.public_site_component_registry.v1"
RECEIPT_SCHEMA = "adaos.public_site_component_registry_receipt.v1"
_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_SUPPORT_STATES = {
    "candidate",
    "recommended",
    "supported",
    "deprecated",
    "migration_required",
    "eol",
    "revoked",
}
_UPGRADE_POLICIES = {"explicit_review", "compatible_auto"}
_QUALIFICATION_CHECKS = {
    "responsive",
    "accessibility",
    "overflow",
    "visual",
    "performance",
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"invalid JSON object: {path.name}")
    return dict(value)


def _empty() -> dict[str, Any]:
    return {
        "schema": REGISTRY_SCHEMA,
        "revision": 0,
        "updated_at": _iso_now(),
        "packages": {},
        "release_sets": {},
        "recommended": {},
        "deployments": {},
        "admitted_deployments": {},
        "client_qualifications": {},
        "upgrade_policies": {},
        "upgrade_reviews": {},
        "receipts": [],
    }


class PublicSiteComponentRegistry:
    """Canonical Root authority for public component sets and their consumers."""

    def __init__(self, *, state_dir: Path | None = None) -> None:
        if state_dir is None:
            raw = get_ctx().paths.root_mcp_state_dir()
            state_dir = Path(raw() if callable(raw) else raw)
        self.root = Path(state_dir).resolve() / "public-site-components"
        self.path = self.root / "registry.json"
        self.lock_path = self.root / ".mutation.lock"

    def admit_bundle(
        self,
        site_dir: Path,
        *,
        actor: str,
        operation_id: str,
    ) -> dict[str, Any]:
        site_dir = Path(site_dir).resolve(strict=True)
        bundle = build_delivery_bundle(site_dir)
        release_set = _read_json(site_dir / "components/release-set.json")
        package_artifacts = {
            str(entry["digest"]): _read_json(site_dir / str(entry["artifact_ref"]))
            for entry in release_set["packages"]
        }
        deployment = _read_json(site_dir / "site.deployment.json")
        projection = _read_json(site_dir / "site.projection.json")
        used_components: set[str] = set()
        affected_routes: set[str] = set()
        for route in projection.get("routes") or []:
            page = _read_json(site_dir / str(route["page_graph_ref"]))
            route_components = {
                str(widget.get("type") or "")
                for widget in page.get("widgets") or []
                if str(widget.get("type") or "").startswith("site.")
            }
            if route_components:
                affected_routes.add(str(route.get("id") or ""))
                used_components.update(route_components)
        set_digest = str(release_set["set_digest"])
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._read()
            packages = dict(registry["packages"])
            for digest, artifact in package_artifacts.items():
                current = packages.get(digest)
                record = {
                    "digest": digest,
                    "package_id": artifact["package_id"],
                    "family": artifact["family"],
                    "version": artifact["version"],
                    "renderer_abi": deepcopy(artifact["renderer_abi"]),
                    "module_ref": artifact["module_ref"],
                    "style_refs": list(artifact["style_refs"]),
                    "components": list(artifact["components"]),
                    "migration_digest": artifact["migration_digest"],
                    "artifact": artifact,
                    "admitted_at": _iso_now(),
                }
                if current is not None and current.get("artifact") != artifact:
                    raise ValueError("component package digest collision")
                if current is not None:
                    record["admitted_at"] = current.get("admitted_at")
                packages[digest] = record
            release_sets = dict(registry["release_sets"])
            current_set = release_sets.get(set_digest)
            set_record = {
                "set_digest": set_digest,
                "set_id": release_set["set_id"],
                "channel": release_set["channel"],
                "renderer_abi": release_set["renderer_abi"],
                "support_state": (
                    current_set.get("support_state") if current_set else "candidate"
                ),
                "packages": deepcopy(release_set["packages"]),
                "qualification_digest": release_set["qualification_digest"],
                "qualified_at": release_set["qualified_at"],
                "artifact": release_set,
                "admitted_at": (
                    current_set.get("admitted_at") if current_set else _iso_now()
                ),
                "deployment_count": int(
                    sum(
                        1
                        for item in registry["deployments"].values()
                        if item.get("component_set_digest") == set_digest
                        and item.get("status") == "current"
                    )
                ),
                "migration_blockers": list(
                    (current_set or {}).get("migration_blockers") or []
                ),
                "security_notices": list(
                    (current_set or {}).get("security_notices") or []
                ),
            }
            if current_set is not None and current_set.get("artifact") != release_set:
                raise ValueError("component release-set digest collision")
            release_sets[set_digest] = set_record
            admitted_deployments = dict(registry["admitted_deployments"])
            deployment_digest = str(deployment["deployment_digest"])
            admitted_record = {
                "deployment": deployment,
                "projection": projection,
                "used_components": sorted(used_components),
                "affected_routes": sorted(affected_routes),
                "admitted_at": _iso_now(),
            }
            current_deployment = admitted_deployments.get(deployment_digest)
            if current_deployment is not None:
                if {
                    key: value
                    for key, value in current_deployment.items()
                    if key != "admitted_at"
                } != {
                    key: value
                    for key, value in admitted_record.items()
                    if key != "admitted_at"
                }:
                    raise ValueError("component deployment digest collision")
                admitted_record["admitted_at"] = current_deployment.get(
                    "admitted_at"
                )
            admitted_deployments[deployment_digest] = admitted_record
            receipt = self._receipt(
                "admit_bundle",
                actor=actor,
                operation_id=operation_id,
                details={
                    "site_id": bundle.site_id,
                    "deployment_digest": bundle.deployment_digest,
                    "component_set_digest": set_digest,
                    "package_digests": sorted(package_artifacts),
                },
            )
            registry.update(
                packages=packages,
                release_sets=release_sets,
                admitted_deployments=admitted_deployments,
            )
            self._commit(registry, receipt)
        status = self.release_set_status(set_digest)
        status["deployment"] = {
            "deployment_digest": deployment["deployment_digest"],
            "projection_digest": deployment["projection_digest"],
            "renderer_abi": deployment["renderer_abi"],
        }
        return status

    def record_publication(
        self,
        *,
        site_id: str,
        project_id: str,
        deployment_digest: str,
        component_set_digest: str,
        projection_digest: str,
        renderer_abi: str,
        actor: str,
        operation_id: str,
        published_at: str,
        predecessor_deployment_digest: str | None = None,
        acceptance_receipt: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        for value in (deployment_digest, component_set_digest, projection_digest):
            if not _DIGEST.fullmatch(str(value or "")):
                raise ValueError("component publication digest is invalid")
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._read()
            release_set = dict(registry["release_sets"].get(component_set_digest) or {})
            if not release_set:
                raise ValueError("component release set was not admitted")
            if release_set.get("renderer_abi") != renderer_abi:
                raise ValueError("component set renderer ABI mismatch")
            deployments = dict(registry["deployments"])
            for digest, item in list(deployments.items()):
                if item.get("site_id") == site_id and item.get("status") == "current":
                    previous = dict(item)
                    previous["status"] = "predecessor"
                    deployments[digest] = previous
            deployment = {
                "site_id": site_id,
                "project_id": project_id,
                "deployment_digest": deployment_digest,
                "component_set_digest": component_set_digest,
                "projection_digest": projection_digest,
                "renderer_abi": renderer_abi,
                "status": "current",
                "published_at": published_at,
                "predecessor_deployment_digest": predecessor_deployment_digest,
                "acceptance_receipt": deepcopy(dict(acceptance_receipt or {})),
                "operation_id": operation_id,
            }
            deployments[deployment_digest] = deployment
            release_sets = dict(registry["release_sets"])
            release_set["support_state"] = (
                "recommended"
                if not registry["recommended"].get(release_set["channel"])
                else "supported"
            )
            recommended = dict(registry["recommended"])
            recommended.setdefault(release_set["channel"], component_set_digest)
            release_set["deployment_count"] = sum(
                1
                for item in deployments.values()
                if item.get("component_set_digest") == component_set_digest
                and item.get("status") == "current"
            )
            release_sets[component_set_digest] = release_set
            for digest_value, item in list(release_sets.items()):
                updated = dict(item)
                updated["deployment_count"] = sum(
                    1
                    for deployment_item in deployments.values()
                    if deployment_item.get("component_set_digest") == digest_value
                    and deployment_item.get("status") == "current"
                )
                release_sets[digest_value] = updated
            receipt = self._receipt(
                "record_publication",
                actor=actor,
                operation_id=operation_id,
                details=deployment,
            )
            registry.update(
                deployments=deployments,
                release_sets=release_sets,
                recommended=recommended,
            )
            self._commit(registry, receipt)
        return self.site_status(site_id)

    def set_support_state(
        self,
        set_digest: str,
        *,
        support_state: str,
        actor: str,
        reason: str,
        recommended: bool = False,
        migration_blockers: Sequence[str] = (),
        security_notices: Sequence[str] = (),
    ) -> dict[str, Any]:
        support_state = str(support_state or "").strip()
        if support_state not in _SUPPORT_STATES:
            raise ValueError("component release-set support state is invalid")
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._read()
            current = dict(registry["release_sets"].get(set_digest) or {})
            if not current:
                raise KeyError(set_digest)
            recommended_map = dict(registry["recommended"])
            if recommended:
                previous_digest = recommended_map.get(current["channel"])
                if previous_digest and previous_digest != set_digest:
                    previous = dict(registry["release_sets"].get(previous_digest) or {})
                    if previous and previous.get("support_state") == "recommended":
                        previous["support_state"] = "supported"
                        registry["release_sets"][previous_digest] = previous
                recommended_map[current["channel"]] = set_digest
                support_state = "recommended"
            current.update(
                support_state=support_state,
                migration_blockers=[str(item)[:240] for item in migration_blockers],
                security_notices=[str(item)[:500] for item in security_notices],
            )
            registry["release_sets"][set_digest] = current
            registry["recommended"] = recommended_map
            receipt = self._receipt(
                "set_support_state",
                actor=actor,
                operation_id="component-policy-" + uuid4().hex,
                details={
                    "component_set_digest": set_digest,
                    "support_state": support_state,
                    "reason": str(reason)[:500],
                },
            )
            self._commit(registry, receipt)
        return self.release_set_status(set_digest)

    def record_client_qualification(
        self,
        *,
        client_release_id: str,
        client_release_digest: str,
        set_digest: str,
        renderer_abi: str,
        evidence_digest: str,
        checks: Sequence[str],
        actor: str,
    ) -> dict[str, Any]:
        for value in (client_release_digest, set_digest, evidence_digest):
            if not _DIGEST.fullmatch(str(value or "")):
                raise ValueError("client qualification digest is invalid")
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._read()
            release_set = dict(registry["release_sets"].get(set_digest) or {})
            if not release_set or release_set.get("renderer_abi") != renderer_abi:
                raise ValueError("client qualification targets an unknown component set")
            identity = f"{client_release_digest}:{set_digest}"
            record = {
                "client_release_id": str(client_release_id)[:160],
                "client_release_digest": client_release_digest,
                "component_set_digest": set_digest,
                "renderer_abi": renderer_abi,
                "evidence_digest": evidence_digest,
                "checks": sorted(set(str(item)[:80] for item in checks)),
                "qualified_at": _iso_now(),
                "actor": str(actor)[:240],
            }
            registry["client_qualifications"][identity] = record
            receipt = self._receipt(
                "record_client_qualification",
                actor=actor,
                operation_id="client-qualification-" + uuid4().hex,
                details=record,
            )
            self._commit(registry, receipt)
        return record

    def set_upgrade_policy(
        self,
        site_id: str,
        *,
        mode: str,
        actor: str,
    ) -> dict[str, Any]:
        mode = str(mode or "").strip()
        if mode not in _UPGRADE_POLICIES:
            raise ValueError("component upgrade policy is invalid")
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._read()
            if not any(
                item.get("site_id") == site_id
                for item in registry["deployments"].values()
            ):
                raise KeyError(site_id)
            record = {
                "schema": "adaos.public_site_component_upgrade_policy.v1",
                "site_id": site_id,
                "mode": mode,
                "updated_at": _iso_now(),
                "actor": str(actor)[:240],
                "security_override": "platform_policy_only",
                "forced_migration": "platform_policy_only",
            }
            registry["upgrade_policies"][site_id] = record
            receipt = self._receipt(
                "set_upgrade_policy",
                actor=actor,
                operation_id="component-policy-" + uuid4().hex,
                details=record,
            )
            self._commit(registry, receipt)
        return record

    def prepare_upgrade(
        self,
        site_id: str,
        *,
        actor: str,
        candidate_set_digest: str | None = None,
    ) -> dict[str, Any]:
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._read()
            current = self._current_deployment(registry, site_id)
            current_set = dict(
                registry["release_sets"].get(current["component_set_digest"]) or {}
            )
            candidate_digest = str(
                candidate_set_digest
                or registry["recommended"].get(current_set.get("channel"))
                or ""
            )
            candidate = dict(registry["release_sets"].get(candidate_digest) or {})
            if not candidate:
                raise ValueError("recommended component release set is unavailable")
            if candidate_digest == current["component_set_digest"]:
                raise ValueError("site already uses the selected component release set")
            if candidate.get("support_state") not in {"recommended", "supported"}:
                raise ValueError("component release set is not supported for upgrade")
            admitted = dict(
                registry["admitted_deployments"].get(
                    current["deployment_digest"]
                )
                or {}
            )
            if not admitted:
                raise ValueError("current deployment artifacts are unavailable")
            current_artifact = dict(admitted["deployment"])
            current_packages = {
                entry["family"]: dict(registry["packages"].get(entry["digest"]) or {})
                for entry in current_set["packages"]
            }
            candidate_packages = {
                entry["family"]: dict(registry["packages"].get(entry["digest"]) or {})
                for entry in candidate["packages"]
            }
            used_families = sorted(
                str(entry["family"])
                for entry in current_artifact["required_packages"]
            )
            used_components = list(admitted.get("used_components") or [])
            changed_families: list[str] = []
            blockers: list[str] = []
            schema_review = False
            for family in used_families:
                old_package = current_packages.get(family) or {}
                new_package = candidate_packages.get(family) or {}
                if not new_package:
                    blockers.append(f"missing_family:{family}")
                    continue
                if old_package.get("digest") == new_package.get("digest"):
                    continue
                changed_families.append(family)
                component_ids = set(new_package.get("components") or [])
                missing = sorted(set(used_components) - component_ids)
                missing = [
                    component
                    for component in missing
                    if component in set(old_package.get("components") or [])
                ]
                blockers.extend(f"missing_component:{item}" for item in missing)
                old_contracts = {
                    item["id"]: item for item in old_package.get("contracts") or []
                }
                new_contracts = {
                    item["id"]: item for item in new_package.get("contracts") or []
                }
                for component in sorted(set(used_components) & component_ids):
                    old_contract = old_contracts.get(component)
                    new_contract = new_contracts.get(component)
                    if not old_contract or not new_contract:
                        schema_review = True
                        continue
                    if (
                        old_contract.get("data_schema")
                        != new_contract.get("data_schema")
                        or (old_contract.get("migration") or {}).get(
                            "contract_version"
                        )
                        != (new_contract.get("migration") or {}).get(
                            "contract_version"
                        )
                    ):
                        blockers.append(f"schema_migration_required:{component}")
            if candidate.get("renderer_abi") != current.get("renderer_abi"):
                blockers.append("renderer_abi_mismatch")
            if candidate.get("migration_blockers"):
                blockers.extend(
                    "release_blocker:" + str(item)[:200]
                    for item in candidate["migration_blockers"]
                )
            observed_checks = self._qualified_checks(registry, candidate_digest)
            findings = self._upgrade_findings(
                changed=bool(changed_families),
                schema_review=schema_review,
                qualified_checks=observed_checks,
                blocked=bool(blockers),
            )
            review_id = "psupgrade_" + uuid4().hex
            created_at = _iso_now()
            policy = dict(
                registry["upgrade_policies"].get(site_id)
                or {
                    "schema": "adaos.public_site_component_upgrade_policy.v1",
                    "site_id": site_id,
                    "mode": "explicit_review",
                    "security_override": "platform_policy_only",
                    "forced_migration": "platform_policy_only",
                }
            )
            body = {
                "schema": "adaos.public_site_component_upgrade_review.v1",
                "review_id": review_id,
                "site_id": site_id,
                "status": (
                    "blocked"
                    if blockers
                    else "qualification_required"
                    if any(item["status"] != "passed" for item in findings)
                    else "preview_ready"
                ),
                "current_deployment_digest": current["deployment_digest"],
                "projection_digest": current["projection_digest"],
                "current_set_digest": current["component_set_digest"],
                "candidate_set_digest": candidate_digest,
                "renderer_abi": current["renderer_abi"],
                "used_families": used_families,
                "changed_families": changed_families,
                "used_components": used_components,
                "affected_routes": list(admitted.get("affected_routes") or []),
                "findings": findings,
                "blockers": blockers,
                "policy": policy,
                "created_at": created_at,
                "actor": str(actor)[:240],
            }
            body["plan_digest"] = digest(body)
            registry["upgrade_reviews"][review_id] = body
            receipt = self._receipt(
                "prepare_upgrade",
                actor=actor,
                operation_id=review_id,
                details={
                    "site_id": site_id,
                    "current_set_digest": current["component_set_digest"],
                    "candidate_set_digest": candidate_digest,
                    "plan_digest": body["plan_digest"],
                    "status": body["status"],
                },
            )
            self._commit(registry, receipt)
        return deepcopy(body)

    def decide_upgrade(
        self,
        review_id: str,
        *,
        plan_digest: str,
        decision: str,
        actor: str,
    ) -> dict[str, Any]:
        if decision not in {"approve", "reject"}:
            raise ValueError("component upgrade decision is invalid")
        with mutation_lock(self.lock_path, timeout_s=30.0):
            registry = self._read()
            review = dict(registry["upgrade_reviews"].get(review_id) or {})
            if not review or review.get("plan_digest") != plan_digest:
                raise ValueError("component upgrade review is stale")
            if review.get("status") not in {
                "preview_ready",
                "qualification_required",
            }:
                raise ValueError("component upgrade review is not decidable")
            if decision == "approve" and review.get("status") != "preview_ready":
                raise ValueError("component upgrade qualification is incomplete")
            decided_at = _iso_now()
            receipt = self._receipt(
                "approve_upgrade" if decision == "approve" else "reject_upgrade",
                actor=actor,
                operation_id=review_id,
                details={
                    "site_id": review["site_id"],
                    "plan_digest": plan_digest,
                    "current_set_digest": review["current_set_digest"],
                    "candidate_set_digest": review["candidate_set_digest"],
                    "decision": decision,
                },
            )
            review.update(
                status="accepted" if decision == "approve" else "rejected",
                decided_at=decided_at,
                decision_receipt=receipt,
            )
            if decision == "approve":
                review["candidate_deployment"] = self._upgrade_deployment(
                    registry, review, receipt
                )
            registry["upgrade_reviews"][review_id] = review
            self._commit(registry, receipt)
        return deepcopy(review)

    def upgrade_preview(self, review_id: str) -> dict[str, Any]:
        registry = self._read()
        review = dict(registry["upgrade_reviews"].get(review_id) or {})
        if not review:
            raise KeyError(review_id)
        admitted = dict(
            registry["admitted_deployments"].get(
                review["current_deployment_digest"]
            )
            or {}
        )
        release_set = dict(
            registry["release_sets"].get(review["candidate_set_digest"]) or {}
        )
        required_families = set(review["used_families"])
        packages = [
            deepcopy(registry["packages"][entry["digest"]]["artifact"])
            for entry in release_set["packages"]
            if entry["family"] in required_families
        ]
        return {
            "schema": "adaos.public_site_component_upgrade_preview.v1",
            "review": deepcopy(review),
            "projection": deepcopy(admitted["projection"]),
            "component_release_set": deepcopy(release_set["artifact"]),
            "component_packages": packages,
        }

    def release_artifacts(self, set_digest: str) -> dict[str, Any]:
        registry = self._read()
        release_set = dict(registry["release_sets"].get(set_digest) or {})
        if not release_set:
            raise KeyError(set_digest)
        return {
            "release_set": deepcopy(release_set["artifact"]),
            "packages": {
                entry["artifact_ref"]: deepcopy(
                    registry["packages"][entry["digest"]]["artifact"]
                )
                for entry in release_set["packages"]
            },
        }

    def retention_plan(self) -> dict[str, Any]:
        registry = self._read()
        retained_sets = {
            digest_value
            for digest_value, item in registry["release_sets"].items()
            if item.get("support_state") not in {"eol", "revoked"}
        }
        retained_sets.update(
            str(item.get("component_set_digest") or "")
            for item in registry["deployments"].values()
            if item.get("status") in {"current", "predecessor"}
        )
        retained_sets.discard("")
        retained_packages = {
            entry["digest"]
            for set_digest in retained_sets
            for entry in registry["release_sets"].get(set_digest, {}).get(
                "packages", []
            )
        }
        return {
            "schema": "adaos.public_site_component_retention_plan.v1",
            "retain_sets": sorted(retained_sets),
            "delete_sets": sorted(set(registry["release_sets"]) - retained_sets),
            "retain_packages": sorted(retained_packages),
            "delete_packages": sorted(set(registry["packages"]) - retained_packages),
        }

    def release_set_status(self, set_digest: str) -> dict[str, Any]:
        registry = self._read()
        current = dict(registry["release_sets"].get(set_digest) or {})
        if not current:
            raise KeyError(set_digest)
        current["recommended"] = (
            registry["recommended"].get(current["channel"]) == set_digest
        )
        current["client_qualifications"] = [
            deepcopy(item)
            for item in registry["client_qualifications"].values()
            if item.get("component_set_digest") == set_digest
        ]
        return current

    def site_status(self, site_id: str) -> dict[str, Any]:
        registry = self._read()
        deployments = [
            deepcopy(item)
            for item in registry["deployments"].values()
            if item.get("site_id") == site_id
        ]
        deployments.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
        current = next(
            (item for item in deployments if item.get("status") == "current"), None
        )
        release_set = (
            self.release_set_status(str(current["component_set_digest"]))
            if current
            else None
        )
        recommended_digest = (
            registry["recommended"].get(release_set["channel"])
            if release_set
            else None
        )
        return {
            "schema": "adaos.public_site_component_status.v1",
            "site_id": site_id,
            "current_deployment": current,
            "current_set": release_set,
            "recommended_set_digest": recommended_digest,
            "upgrade_available": bool(
                current and recommended_digest != current["component_set_digest"]
            ),
            "rollback_target": next(
                (
                    item
                    for item in deployments
                    if item.get("status") == "predecessor"
                ),
                None,
            ),
            "acceptance_history": [
                item.get("acceptance_receipt")
                for item in deployments
                if item.get("acceptance_receipt")
            ],
            "upgrade_policy": deepcopy(
                registry["upgrade_policies"].get(site_id)
                or {
                    "schema": "adaos.public_site_component_upgrade_policy.v1",
                    "site_id": site_id,
                    "mode": "explicit_review",
                    "security_override": "platform_policy_only",
                    "forced_migration": "platform_policy_only",
                }
            ),
            "upgrade_reviews": [
                deepcopy(item)
                for item in registry["upgrade_reviews"].values()
                if item.get("site_id") == site_id
            ][-10:],
        }

    @staticmethod
    def _current_deployment(
        registry: Mapping[str, Any], site_id: str
    ) -> dict[str, Any]:
        current = [
            dict(item)
            for item in registry["deployments"].values()
            if item.get("site_id") == site_id and item.get("status") == "current"
        ]
        if len(current) != 1:
            raise ValueError("site does not have one current component deployment")
        return current[0]

    @staticmethod
    def _qualified_checks(
        registry: Mapping[str, Any], set_digest: str
    ) -> set[str]:
        aliases = {
            "routes": "route",
            "contract": "schema",
            "style_isolation": "overflow",
        }
        checks: set[str] = set()
        for item in registry["client_qualifications"].values():
            if item.get("component_set_digest") != set_digest:
                continue
            for value in item.get("checks") or []:
                token = str(value).strip().lower()
                checks.add(aliases.get(token, token))
        return checks

    @staticmethod
    def _upgrade_findings(
        *,
        changed: bool,
        schema_review: bool,
        qualified_checks: set[str],
        blocked: bool,
    ) -> list[dict[str, Any]]:
        findings = []
        for category in (
            "schema",
            "route",
            "responsive",
            "accessibility",
            "overflow",
            "metadata",
            "visual",
            "performance",
        ):
            if blocked and category == "schema":
                status = "blocked"
            elif not changed:
                status = "passed"
            elif category in {"route", "metadata"}:
                status = "passed"
            elif category == "schema":
                status = "review_required" if schema_review else "passed"
            else:
                status = "passed" if category in qualified_checks else "required"
            findings.append(
                {
                    "category": category,
                    "status": status,
                    "evidence": (
                        "projection_reused"
                        if category in {"route", "metadata"}
                        else "client_qualification"
                        if status == "passed" and changed
                        else "package_contract"
                    ),
                }
            )
        return findings

    @staticmethod
    def _upgrade_deployment(
        registry: Mapping[str, Any],
        review: Mapping[str, Any],
        receipt: Mapping[str, Any],
    ) -> dict[str, Any]:
        admitted = registry["admitted_deployments"][
            review["current_deployment_digest"]
        ]
        current = dict(admitted["deployment"])
        release_set = registry["release_sets"][review["candidate_set_digest"]]
        packages = {item["family"]: item for item in release_set["packages"]}
        accepted_at = str(receipt["created_at"])
        body = {
            key: deepcopy(value)
            for key, value in current.items()
            if key != "deployment_digest"
        }
        body.update(
            deployment_id=(
                f"{current['site_id']}:components:{review['candidate_set_digest'][7:23]}"
            ),
            release_id=(
                f"components-{review['candidate_set_digest'][7:23]}"
            ),
            component_release_set={
                "id": release_set["set_id"],
                "digest": review["candidate_set_digest"],
            },
            required_packages=[
                {"family": family, "digest": packages[family]["digest"]}
                for family in review["used_families"]
            ],
            acceptance={
                "kind": (
                    "policy"
                    if review["policy"].get("mode") == "compatible_auto"
                    else "owner"
                ),
                "receipt_id": receipt["receipt_id"],
                "accepted_at": accepted_at,
            },
            created_at=accepted_at,
            rollback_predecessor_digest=review["current_deployment_digest"],
        )
        deployment = {**body, "deployment_digest": digest(body)}
        validate_contract("public_site_deployment.v1.schema.json", deployment)
        return deployment

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return _empty()
        value = _read_json(self.path)
        if value.get("schema") != REGISTRY_SCHEMA:
            raise RuntimeError("public component registry schema is invalid")
        for key in (
            "packages",
            "release_sets",
            "recommended",
            "deployments",
            "admitted_deployments",
            "client_qualifications",
            "upgrade_policies",
            "upgrade_reviews",
        ):
            value.setdefault(key, {})
        value.setdefault("receipts", [])
        return value

    def _commit(self, registry: dict[str, Any], receipt: Mapping[str, Any]) -> None:
        registry["receipts"] = [*registry["receipts"], deepcopy(dict(receipt))]
        registry["revision"] = int(registry.get("revision") or 0) + 1
        registry["updated_at"] = _iso_now()
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, registry)

    @staticmethod
    def _receipt(
        action: str,
        *,
        actor: str,
        operation_id: str,
        details: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "schema": RECEIPT_SCHEMA,
            "receipt_id": "sitecomponents_" + uuid4().hex,
            "action": action,
            "actor": str(actor)[:240],
            "operation_id": str(operation_id)[:240],
            "details": deepcopy(dict(details)),
            "created_at": _iso_now(),
        }


__all__ = ["PublicSiteComponentRegistry"]
