from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Mapping

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.domain.project_deployment import (
    ComponentActivation,
    ComponentPlacementPolicy,
    DeploymentPlan,
    DeploymentPlanChange,
    NodeInventoryRecord,
    ProjectDeployment,
    inventory_revision,
)
from adaos.services.artifact_pipeline.releases import ReleasePlan


class ProjectDeploymentPlanningError(RuntimeError):
    pass


_ACTIVATE_PHASES = ("fetch", "verify", "stage", "activate", "health", "commit")
_REMOVE_PHASES = ("cordon", "drain", "deactivate", "remove")


def _matches_version(value: str, requirement: str) -> bool:
    token = str(requirement or "").strip()
    try:
        observed = Version(str(value or "").strip())
    except InvalidVersion:
        return False
    if not token:
        return True
    if token[0] not in "<>=!~":
        try:
            return observed == Version(token)
        except InvalidVersion:
            return False
    try:
        return observed in SpecifierSet(token)
    except InvalidSpecifier:
        return False


def _latest_activations(
    values: Iterable[ComponentActivation], deployment_id: str
) -> dict[tuple[str, str], ComponentActivation]:
    result: dict[tuple[str, str], ComponentActivation] = {}
    for item in values:
        if item.deployment_id != deployment_id:
            continue
        key = (item.component_ref, item.node_id)
        previous = result.get(key)
        if previous is None or (
            item.generation,
            item.updated_at,
            item.activation_id,
        ) > (
            previous.generation,
            previous.updated_at,
            previous.activation_id,
        ):
            result[key] = item
    return result


class ProjectDeploymentPlanner:
    """Resolve component placement without importing presentation or domain sharding."""

    def recommend_nodes(
        self,
        desired: ProjectDeployment,
        placement: ComponentPlacementPolicy,
        *,
        inventory: Iterable[NodeInventoryRecord],
        activations: Iterable[ComponentActivation] = (),
        limit: int = 20,
    ) -> dict[str, object]:
        """Rank eligible nodes without changing desired placement or reservations."""

        bounded = max(1, min(int(limit), 100))
        current = _latest_activations(activations, desired.deployment_id)
        candidates: list[dict[str, object]] = []
        rejected: list[dict[str, str]] = []
        for node in sorted(inventory, key=lambda item: item.node_id):
            if node.subnet_id != desired.subnet_id:
                continue
            reason = self._node_rejection(desired, placement, node, reserved={})
            if reason is not None:
                rejected.append({"node_id": node.node_id, "reason": reason})
                continue
            headroom = {
                resource: max(0, int(node.capacity.get(resource, 0)) - required)
                for resource, required in placement.required_capacity.items()
            }
            exact = current.get((placement.component_ref, node.node_id))
            score = (
                1000 if exact is not None and exact.status == "active" else 0
            ) + sum(min(value, 1_000_000_000) for value in headroom.values())
            candidates.append(
                {
                    "node_id": node.node_id,
                    "score": score,
                    "already_active": exact is not None and exact.status == "active",
                    "architecture": node.architecture,
                    "runtime_version": node.runtime_version,
                    "labels": dict(node.labels),
                    "headroom": headroom,
                    "reasons": [
                        "exact_activation"
                        if exact is not None and exact.status == "active"
                        else "eligible",
                        "capacity_headroom",
                    ],
                }
            )
        candidates.sort(key=lambda item: (-int(item["score"]), str(item["node_id"])))
        return {
            "schema": "adaos.project.placement_recommendation.v1",
            "deployment_id": desired.deployment_id,
            "desired_revision": desired.revision,
            "component_ref": placement.component_ref,
            "mode": placement.mode,
            "dry_run": True,
            "candidates": candidates[:bounded],
            "rejected": rejected[:bounded],
            "truncated": len(candidates) > bounded or len(rejected) > bounded,
        }

    def plan(
        self,
        desired: ProjectDeployment,
        *,
        release_plan: ReleasePlan,
        inventory: Iterable[NodeInventoryRecord],
        activations: Iterable[ComponentActivation] = (),
        local_node_id: str | None = None,
    ) -> DeploymentPlan:
        release = release_plan.release
        release_digest = release.release_digest or release.computed_digest()
        if desired.project_ref != f"project:{release.project_id}":
            raise ProjectDeploymentPlanningError(
                "deployment project_ref does not match ProjectRelease"
            )
        if desired.release_digest != release_digest:
            raise ProjectDeploymentPlanningError(
                "deployment release_digest does not match ProjectRelease"
            )
        package_by_ref = {item.key: item for item in release.components}
        if len(package_by_ref) != len(release.components):
            raise ProjectDeploymentPlanningError(
                "ProjectRelease components are not unique"
            )

        nodes = {
            item.node_id: item
            for item in inventory
            if item.subnet_id == desired.subnet_id
        }
        if not nodes:
            raise ProjectDeploymentPlanningError(
                "node inventory has no records for the deployment subnet"
            )
        inventory_digest = inventory_revision(nodes.values())
        current = _latest_activations(activations, desired.deployment_id)
        warnings: list[str] = []
        approvals: set[str] = set()
        targets: dict[str, tuple[str, ...]] = {}
        reservations: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

        placements = {item.component_ref: item for item in desired.placements}
        missing = sorted(set(package_by_ref).difference(placements))
        extra = sorted(set(placements).difference(package_by_ref))
        warnings.extend(f"blocked:{item}:missing_placement" for item in missing)
        warnings.extend(f"blocked:{item}:not_in_project_release" for item in extra)

        independent = sorted(
            (item for item in desired.placements if item.mode != "co_located_with"),
            key=lambda item: item.component_ref,
        )
        pending = list(
            sorted(
                (item for item in desired.placements if item.mode == "co_located_with"),
                key=lambda item: item.component_ref,
            )
        )

        def resolve_placement(placement: ComponentPlacementPolicy) -> None:
            if placement.component_ref not in package_by_ref:
                targets[placement.component_ref] = ()
                return
            selected, rejection = self._select_nodes(
                desired,
                placement,
                nodes=nodes,
                current=current,
                targets=targets,
                reservations=reservations,
            )
            targets[placement.component_ref] = tuple(selected)
            if len(selected) < placement.min_instances:
                reason = rejection or "insufficient_eligible_nodes"
                warnings.append(f"blocked:{placement.component_ref}:{reason}")
            for node_id in selected:
                for resource, required in placement.required_capacity.items():
                    reservations[node_id][resource] += required
                if local_node_id is None or node_id != local_node_id:
                    approvals.add("remote_install")

        for placement in independent:
            resolve_placement(placement)

        while pending:
            ready = [item for item in pending if str(item.co_located_with) in targets]
            if not ready:
                for item in pending:
                    targets[item.component_ref] = ()
                    warnings.append(
                        f"blocked:{item.component_ref}:colocation_dependency_cycle_or_missing"
                    )
                break
            pending = [item for item in pending if item not in ready]
            for placement in ready:
                resolve_placement(placement)

        changes: list[DeploymentPlanChange] = []
        target_pairs: set[tuple[str, str]] = set()
        for component_ref, node_ids in sorted(targets.items()):
            package = package_by_ref[component_ref]
            placement = placements[component_ref]
            for node_id in node_ids:
                pair = (component_ref, node_id)
                target_pairs.add(pair)
                observed = current.get(pair)
                if observed is None or observed.status == "removed":
                    action = "install"
                    reason = "activation_missing"
                elif (
                    observed.package_digest != package.digest
                    or observed.status != "active"
                    or (
                        observed.release_digest != desired.release_digest
                        and not desired.compatibility.allow_release_skew
                    )
                ):
                    action = "update"
                    reason = "activation_drift"
                else:
                    action = "noop"
                    reason = "exact_activation_ready"
                impact = "none"
                if action == "update" and placement.min_instances <= 1:
                    impact = "temporary_unavailable"
                elif action == "update":
                    impact = "reduced_capacity"
                changes.append(
                    DeploymentPlanChange(
                        action=action,
                        component_ref=component_ref,
                        node_id=node_id,
                        target_package_digest=package.digest,
                        current_activation_ref=(
                            observed.activation_id if observed is not None else None
                        ),
                        reason=reason,
                        phases=("observe",) if action == "noop" else _ACTIVATE_PHASES,
                        availability_impact=impact,
                    )
                )

        for pair, observed in sorted(current.items()):
            if pair in target_pairs or observed.status == "removed":
                continue
            approvals.add("component_remove")
            changes.append(
                DeploymentPlanChange(
                    action="remove",
                    component_ref=observed.component_ref,
                    node_id=observed.node_id,
                    current_activation_ref=observed.activation_id,
                    reason="activation_not_in_desired_topology",
                    phases=_REMOVE_PHASES,
                    availability_impact="reduced_capacity",
                )
            )

        if desired.retention.runtime_data == "delete":
            approvals.add("runtime_data_delete")
        if desired.retention.derived_data == "delete":
            approvals.add("derived_data_delete")
        if desired.rollout.max_unavailable == 0 and any(
            item.availability_impact != "none" and item.action != "noop"
            for item in changes
        ):
            warnings.append("blocked:rollout:max_unavailable_zero")

        status = (
            "blocked"
            if any(item.startswith("blocked:") for item in warnings)
            else "ready"
        )
        identity = canonical_payload_digest(
            {
                "deployment_id": desired.deployment_id,
                "revision": desired.revision,
                "release_digest": desired.release_digest,
                "inventory_revision": inventory_digest,
                "changes": [item.to_dict() for item in changes],
            }
        ).split(":", 1)[1][:24]
        return DeploymentPlan(
            plan_id=f"plan.{identity}",
            deployment_id=desired.deployment_id,
            expected_revision=desired.revision,
            release_digest=desired.release_digest,
            inventory_revision=inventory_digest,
            changes=tuple(changes),
            warnings=tuple(warnings),
            required_approvals=tuple(approvals),
            status=status,
            created_at=desired.updated_at,
        )

    def _select_nodes(
        self,
        desired: ProjectDeployment,
        placement: ComponentPlacementPolicy,
        *,
        nodes: Mapping[str, NodeInventoryRecord],
        current: Mapping[tuple[str, str], ComponentActivation],
        targets: Mapping[str, tuple[str, ...]],
        reservations: Mapping[str, Mapping[str, int]],
    ) -> tuple[list[str], str | None]:
        if placement.mode == "co_located_with":
            candidates = list(targets.get(str(placement.co_located_with), ()))
            if not candidates:
                return [], "colocation_target_unresolved"
        elif placement.mode == "selected_nodes":
            candidates = list(placement.selected_node_ids)
        else:
            candidates = sorted(nodes)

        eligible: list[str] = []
        rejection_codes: set[str] = set()
        for node_id in candidates:
            node = nodes.get(node_id)
            if node is None:
                rejection_codes.add("node_missing")
                continue
            reason = self._node_rejection(
                desired,
                placement,
                node,
                reserved=reservations.get(node_id, {}),
            )
            if reason is not None:
                rejection_codes.add(reason)
                continue
            eligible.append(node_id)

        if placement.mode == "singleton" and eligible:
            active_nodes = [
                node_id
                for node_id in eligible
                if (placement.component_ref, node_id) in current
                and current[(placement.component_ref, node_id)].status == "active"
            ]
            eligible = [sorted(active_nodes or eligible)[0]]
        elif placement.max_instances is not None:
            eligible = eligible[: placement.max_instances]

        reason = "+".join(sorted(rejection_codes)) if rejection_codes else None
        return eligible, reason

    @staticmethod
    def _node_rejection(
        desired: ProjectDeployment,
        placement: ComponentPlacementPolicy,
        node: NodeInventoryRecord,
        *,
        reserved: Mapping[str, int],
    ) -> str | None:
        if node.trust_state != "trusted":
            return "node_untrusted"
        if not node.online:
            return "node_offline"
        if (
            desired.compatibility.architectures
            and node.architecture not in desired.compatibility.architectures
        ):
            return "architecture_incompatible"
        minimum = desired.compatibility.minimum_runtime_version
        if minimum and not _matches_version(node.runtime_version, f">={minimum}"):
            return "runtime_incompatible"
        for protocol, requirement in desired.compatibility.required_protocols.items():
            if not _matches_version(node.protocols.get(protocol, ""), requirement):
                return f"protocol_incompatible:{protocol}"
        if not set(placement.required_capabilities).issubset(node.capabilities):
            return "capability_missing"
        if any(
            node.labels.get(key) != value
            for key, value in placement.required_labels.items()
        ):
            return "label_mismatch"
        for resource, required in placement.required_capacity.items():
            available = int(node.capacity.get(resource, 0)) - int(
                reserved.get(resource, 0)
            )
            if available < required:
                return f"capacity_insufficient:{resource}"
        if placement.mode == "per_endpoint" and not any(
            endpoint.available and endpoint.role == placement.endpoint_role
            for endpoint in node.endpoints
        ):
            return "endpoint_missing"
        return None


__all__ = ["ProjectDeploymentPlanner", "ProjectDeploymentPlanningError"]
