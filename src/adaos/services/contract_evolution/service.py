"""Contract assessments and coordinated work over existing native authorities."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from adaos.services.artifact_pipeline.storage import mutation_lock

from .records import POLICY_DIGEST, digest, instant, seal
from .store import EvolutionQueryLimitError, EvolutionStore


CHANGE = "adaos.contract_change.v1"
USAGE = "adaos.consumer_contract_usage_lock.v1"
ASSESSMENT = "adaos.consumer_contract_assessment.v1"
WAVE = "adaos.contract_evolution_wave.v1"
VERIFICATION = "adaos.contract_verification.v1"
TERMINAL_TICKETS = {"closed", "verified", "superseded", "stale", "refused"}


class ContractEvolutionService:
    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.store = EvolutionStore(state_dir)

    def current_changes(self) -> list[dict[str, Any]]:
        # The first MVP has one supported target lineage per contract ref.
        # Historical deltas remain addressable through their wave identity.
        latest = {}
        for item in self.store.list(CHANGE):
            latest.setdefault(item["contract_ref"], item)
        return list(latest.values())

    def publish_change(self, *, contract_ref: str, contract_kind: str, owner_ref: str,
                       before: str | None, after: str | None, classification: str,
                       dimensions: Mapping[str, str] | None = None,
                       findings: list[dict[str, Any]] | None = None,
                       required_evidence: list[str] | None = None,
                       migration_route: str = "consumer_adaptation", removal_after: str | None = None,
                       replacement_refs: list[str] | None = None, migrator_refs: list[str] | None = None,
                       before_descriptor: Mapping[str, Any] | None = None,
                       after_descriptor: Mapping[str, Any] | None = None,
                       publisher_ref: str = "core:contract_publisher") -> dict[str, Any]:
        if before is None and after is None:
            raise ValueError("a contract change requires a before or after digest")
        if before == after and classification != "deprecated":
            raise ValueError("unchanged contract cannot create a ContractChange")
        from .producers import unit_digest
        for descriptor, expected in ((before_descriptor, before), (after_descriptor, after)):
            if descriptor is not None and unit_digest(descriptor) != expected:
                raise ValueError("contract descriptor does not match its published digest")
        record = seal(CHANGE, contract_ref=contract_ref, contract_kind=contract_kind,
                      owner_ref=owner_ref, before=before, after=after, classification=classification,
                      dimensions=dict(dimensions or {}), findings=findings or [],
                      required_evidence=required_evidence if required_evidence is not None else ["contract_test"],
                      migration_route=migration_route, removal_after=removal_after,
                      replacement_refs=replacement_refs or [], migrator_refs=migrator_refs or [],
                      publisher_ref=publisher_ref,
                      **({"before_descriptor": dict(before_descriptor)} if before_descriptor is not None else {}),
                      **({"after_descriptor": dict(after_descriptor)} if after_descriptor is not None else {}))
        return self.store.put(record, "contract_change.published")

    def register_usage(self, record: Mapping[str, Any]) -> dict[str, Any]:
        if record.get("schema") != USAGE:
            raise ValueError("expected ConsumerContractUsageLock")
        refs = [edge["contract_ref"] for edge in record["edges"]]
        if len(refs) != len(set(refs)):
            raise ValueError("usage lock has duplicate contract edges")
        return self.store.put(record, "consumer_usage.registered")

    def register_application_release(self, release) -> dict[str, Any] | None:
        """Index admitted build evidence; legacy releases remain explicitly unknown."""
        from .usage import release_usage_locks
        entries = release_usage_locks(release.project_release, application_ref="application:" + release.application_id)
        if not entries:
            return None
        edges, unresolved, artifacts = {}, [], {}
        indexed = {entry["consumer_ref"] for entry in entries}
        missing = {component.key for component in release.project_release.components} - indexed
        missing.update(dependency.kind + ":" + dependency.artifact_id
                       for dependency in release.project_release.resolved_dependencies)
        unresolved.extend({"code": "component_usage_missing", "component_ref": ref} for ref in sorted(missing))
        for entry in entries:
            unresolved.extend({**finding, "component_ref": entry["consumer_ref"]} for finding in entry["unresolved"])
            artifacts.update({entry["consumer_ref"] + "/" + path: value for path, value in entry.get("source_artifacts", {}).items()})
            for edge in entry["edges"]:
                previous = edges.get(edge["contract_ref"])
                if previous and previous["contract_digest"] != edge["contract_digest"]:
                    unresolved.append({"code": "component_contract_digest_conflict", "contract_ref": edge["contract_ref"]})
                else:
                    qualified = {**edge,
                        "surface": sorted(set((previous or {}).get("surface", [])) |
                                          {entry["consumer_ref"] + "/" + path for path in edge["surface"]}),
                        "component_refs": sorted(set((previous or {}).get("component_refs", [])) | {entry["consumer_ref"]})}
                    if previous and previous["exposure"] != edge["exposure"]:
                        qualified["exposure"] = "declared" if "declared" in {previous["exposure"], edge["exposure"]} else "static"
                    edges[edge["contract_ref"]] = qualified
        entry = self.register_usage(seal(USAGE, application_ref="application:" + release.application_id,
            consumer_ref="project:" + release.project_release.project_id, consumer_digest=release.release_digest,
            producer_digest=digest(sorted(item["producer_digest"] for item in entries)),
            complete=all(item["complete"] for item in entries) and not unresolved,
            unresolved=unresolved, source_artifacts=artifacts,
            edges=sorted(edges.values(), key=lambda edge: edge["contract_ref"])))
        changes = self.current_changes()
        if changes:
            wave = self.create_wave([item["change_id"] for item in changes], owner_ref="core:contract_evolution")
            self.assess(wave_id=wave["wave_id"], usage_lock_id=entry["usage_lock_id"])
        return entry

    def create_wave(self, change_ids: list[str], *, owner_ref: str) -> dict[str, Any]:
        if not change_ids:
            raise ValueError("wave requires a ContractChange")
        for identity in change_ids:
            if self.store.get(identity)["schema"] != CHANGE:
                raise ValueError("wave members must be ContractChanges")
        return self.store.put(seal(WAVE, change_ids=sorted(set(change_ids)), owner_ref=owner_ref),
                              "contract_impact.indexed")

    def reconcile(self, *, materialize: bool = False) -> dict[str, Any]:
        """Explicit operator/background work; product reads never call this."""
        changes = self.current_changes()
        if not changes:
            return {"wave": None, "assessments": [], "tickets": []}
        wave = self.create_wave([item["change_id"] for item in changes], owner_ref="core:contract_evolution")
        rows, tickets = [], []
        for entry in self.store.list(USAGE):
            assessed = self.assess(wave_id=wave["wave_id"], usage_lock_id=entry["usage_lock_id"])
            rows.extend(assessed)
            if materialize:
                for item in assessed:
                    ticket = self.materialize_ticket(item["assessment_id"])
                    if ticket:
                        tickets.append(ticket["ticket_id"])
        adopted = self.observe_adoptions()
        return {"wave": wave, "assessments": rows, "tickets": sorted(set(tickets)), "adoptions": adopted}

    def observe_adoptions(self) -> list[dict[str, Any]]:
        """Observe native installation authority without changing its lifecycle."""
        from adaos.services.applications.store import ApplicationStore
        store = ApplicationStore(self.state_dir)
        adopted = []
        for installation in store.list_installations():
            if installation.status != "active":
                continue
            posture = self.application_projection("application:" + installation.application_id,
                consumer_digest=installation.installed_release_digest)
            if posture["posture"] not in {"aligned", "compatible_legacy"}:
                continue
            refs = {"application_ref": posture["application_ref"], "consumer_digest": installation.installed_release_digest,
                    "installation_revision": installation.revision,
                    "assessment_ids": [item["assessment_id"] for item in posture["assessments"]],
                    "source_authority": "core:applications"}
            self.store.append_event("compatible_release.activated", "application:" + installation.application_id, refs)
            adopted.append(refs)
        return adopted

    def record_migration(self, receipt: Mapping[str, Any], *, usage_lock_id: str, change_id: str):
        from .records import validate
        value = validate(receipt)
        usage, change = self.store.get(usage_lock_id), self.store.get(change_id)
        if value["schema"] != "adaos.contract_migration_receipt.v1" or not value["validated"] or value["unresolved"]:
            raise ValueError("migration receipt must have passed validation with no unresolved findings")
        if change["migration_route"] != "declarative_projection":
            raise ValueError("contract change does not admit transparent migration")
        if value["source_digest"] not in usage.get("source_artifacts", {}).values():
            raise ValueError("migration source does not belong to the exact consumer snapshot")
        if not any(item.get("ref") in change["migrator_refs"] and item.get("target_contract_digest") == change["after"]
                   for item in value["migrators"]):
            raise ValueError("migration receipt does not target the admitted contract/migrator")
        value = self.store.put(value, "contract_migration.verified")
        self.store.link_migration(usage_lock_id, change_id, value["receipt_id"])
        return value

    def record_verification(self, *, application_ref: str, consumer_digest: str, change_id: str,
                            environment_ref: str, kind: str, result: str, report_digest: str,
                            valid_until: str, runner_ref: str) -> dict[str, Any]:
        change = self.store.get(change_id)
        if change["schema"] != CHANGE or instant(valid_until) <= datetime.now(timezone.utc):
            raise ValueError("verification requires a ContractChange and future expiry")
        return self.store.put(seal(VERIFICATION, application_ref=application_ref,
            consumer_digest=consumer_digest, change_id=change_id, target_contract_digest=change["after"],
            environment_ref=environment_ref, policy_digest=POLICY_DIGEST, kind=kind, result=result,
            report_digest=report_digest, valid_until=valid_until, runner_ref=runner_ref),
            "consumer_verification.completed")

    def assess(self, *, wave_id: str, usage_lock_id: str,
               environment_ref: str = "profile:local/default") -> list[dict[str, Any]]:
        wave, usage = self.store.get(wave_id), self.store.get(usage_lock_id)
        if wave["schema"] != WAVE or usage["schema"] != USAGE:
            raise ValueError("assessment requires a wave and usage lock")
        results = []
        edges = {edge["contract_ref"]: edge for edge in usage["edges"]}
        for identity in wave["change_ids"]:
            change = self.store.get(identity)
            edge = edges.get(change["contract_ref"])
            source_digest = edge["contract_digest"] if edge else None
            evidence, expiry = [], None
            status, reason, action = "unaffected", "ContractNotUsed", False
            if edge is None and not usage["complete"]:
                status, reason = "unknown", "UsageIncomplete"
            elif edge is not None:
                migrations = self.store.migration_receipts(usage_lock_id, identity)
                checks = self.store.list(VERIFICATION, application_ref=usage["application_ref"],
                    consumer_digest=usage["consumer_digest"], change_id=identity)
                latest = {}
                for check in checks:
                    if (check["environment_ref"] == environment_ref and check["policy_digest"] == POLICY_DIGEST
                            and check["target_contract_digest"] == change["after"]):
                        latest.setdefault(check["kind"], check)
                fresh = {kind: check for kind, check in latest.items()
                         if instant(check["valid_until"]) > datetime.now(timezone.utc)}
                evidence = sorted(check["verification_id"] for check in fresh.values())
                expiry = min((check["valid_until"] for check in fresh.values()), default=None)
                if any(finding.get("code") == "component_contract_digest_conflict" and
                       finding.get("contract_ref") == change["contract_ref"] for finding in usage["unresolved"]):
                    status, reason = "unknown", "ComponentContractDigestConflict"
                elif source_digest not in {change["before"], change["after"]}:
                    status, reason = "unknown", "BaselineDigestMismatch"
                elif any(check["result"] == "failed" for check in fresh.values()):
                    status, reason, action = "incompatible", "ConsumerVerificationFailed", True
                elif migrations and usage["complete"]:
                    status, reason = "compatible", "TransparentMigrationVerified"
                    evidence.extend(item["receipt_id"] for item in migrations)
                elif (change["required_evidence"] and all(
                        kind in fresh and fresh[kind]["result"] == "passed" for kind in change["required_evidence"])):
                    status, reason = "compatible", "ConsumerVerified"
                elif not usage["complete"] and change["classification"] != "breaking" and change["after"] is not None:
                    status, reason = "unknown", "UsageIncomplete"
                elif change["classification"] in {"additive", "compatible_by_policy"}:
                    status, reason = "compatible", "ContractPolicyCompatible"
                elif source_digest == change["after"] and not change["required_evidence"]:
                    status, reason = "compatible", "ConsumerTargetsCurrentContract"
                elif change["classification"] == "deprecated":
                    status, reason = "deprecated_compatible", "SupportedLegacyContract"
                elif change["migration_route"] == "declarative_projection" and change["migrator_refs"]:
                    status, reason = "migration_available", "RegisteredMigratorAvailable"
                elif change["after"] is None:
                    status, reason, action = "incompatible", "UsedContractRemoved", True
                else:
                    status, reason = "verification_due", "TargetVerificationRequired"
                    # A deterministic source or authority incompatibility is
                    # actionable immediately, without executing the consumer.
                    if change["classification"] == "breaking":
                        status, reason, action = "incompatible", "UsedContractBreaking", True
            if status not in {"unaffected", "compatible"} and change["removal_after"]:
                if instant(change["removal_after"]) <= datetime.now(timezone.utc):
                    action = True
                    reason = "ContractSupportDeadlineReached"
            result = seal(ASSESSMENT, application_ref=usage["application_ref"],
                consumer_digest=usage["consumer_digest"], consumer_ref=usage["consumer_ref"],
                usage_lock_id=usage["usage_lock_id"], change_id=identity, wave_id=wave_id,
                contract_ref=change["contract_ref"], source_contract_digest=source_digest,
                target_contract_digest=change["after"], environment_ref=environment_ref,
                compatibility=status, reason=reason, action_required=action, policy_digest=POLICY_DIGEST,
                evidence_refs=evidence, evidence_expires_at=expiry, findings=change["findings"] if edge else [])
            results.append(self.store.put(result, "consumer_exposure.confirmed"))
        return results

    def materialize_ticket(self, assessment_id: str) -> dict[str, Any] | None:
        assessment = self.store.get(assessment_id)
        if assessment["schema"] != ASSESSMENT or not assessment["action_required"]:
            return None
        current = self.store.list(ASSESSMENT, application_ref=assessment["application_ref"],
            consumer_digest=assessment["consumer_digest"], change_id=assessment["change_id"], current=True)
        if not any(item["assessment_id"] == assessment_id for item in current):
            raise ValueError("assessment has been superseded; reassess before creating work")
        change = self.store.get(assessment["change_id"])
        from adaos.services.development_tickets import DevelopmentTicketService
        self.store.path.parent.mkdir(parents=True, exist_ok=True)
        with mutation_lock(self.store.path.parent / ".ticket.lock", timeout_s=30):
            tickets = DevelopmentTicketService(state_dir=self.state_dir)
            linked = self.store.ticket_links().get(assessment_id)
            if linked:
                return tickets.get_ticket(linked)
            target_ref = assessment["consumer_ref"]
            target_kind, _, target_id = target_ref.partition(":")
            key = "contract_evolution:" + digest([change["digest"], assessment["application_ref"],
                assessment["consumer_digest"], change["migration_route"]])
            usage = self.store.get(assessment["usage_lock_id"])
            context = {"wave_id": assessment["wave_id"], "change_id": change["change_id"],
                       "assessment_id": assessment_id, "application_ref": assessment["application_ref"],
                       "usage_lock_id": usage["usage_lock_id"], "usage_producer_digest": usage["producer_digest"],
                       "usage_complete": usage["complete"],
                       "used_contract_edges": [edge for edge in usage["edges"] if edge["contract_ref"] == change["contract_ref"]],
                       "unresolved_usage": usage["unresolved"],
                       "contract_ref": change["contract_ref"], "baseline_digest": assessment["consumer_digest"],
                       "target_contract_digest": change["after"], "purpose": "migration",
                       "migration_route": change["migration_route"],
                       "acceptance": change["required_evidence"], "findings": assessment["findings"],
                       "before_contract": change.get("before_descriptor"),
                       "target_contract": change.get("after_descriptor"),
                       "contract_record_ref": change["change_id"]}
            affected = sorted({ref for edge in context["used_contract_edges"] for ref in edge.get("component_refs", [])})
            context["affected_components"] = affected
            metadata = {"contract_evolution": context}
            if len(affected) == 1:
                component_kind, _, component_id = affected[0].partition(":")
                metadata["builder_repair"] = {"target_object_type": component_kind, "target_object_id": component_id}
            signal = tickets.capture_signal(kind="compatibility_finding",
                summary=f"Update {target_ref} for {change['contract_ref']}",
                target_scope={"type": target_kind, "id": target_id, "digest": assessment["consumer_digest"], "source": "installed"},
                origin_scope={"type": "core", "id": "contract_evolution"},
                severity="high", blocking=True, source="compatibility_scan", dedup_key=key,
                evidence_refs=[{"type": "contract_assessment", "ref": assessment_id}],
                metadata=metadata)["signal"]
            ticket = tickets.ensure_ticket_for_signal(signal, kind="runtime_compatibility_debt",
                status="proposed", dedup_key=key, metadata=metadata,
                relation_refs=[{"target_ref": change["change_id"], "relation": "caused_by"}])["ticket"]
            self.store.link_ticket(assessment_id, ticket["ticket_id"])
            return ticket

    def application_projection(self, application_ref: str, *, consumer_digest: str | None = None,
                               environment_ref: str = "profile:local/default", detail: bool = True) -> dict[str, Any]:
        try:
            assessments = self.store.list(ASSESSMENT, application_ref=application_ref, consumer_digest=consumer_digest, current=True)
            target_changes = {item["change_id"] for item in self.current_changes()}
        except EvolutionQueryLimitError as exc:
            return {"schema": "adaos.application.contract_posture.v1", "application_ref": application_ref,
                    "consumer_digest": consumer_digest, "posture": "unknown", "assessments": [],
                    "assessment_count": 0, "action_required_count": 0, "stale_evidence_count": 0,
                    "tickets": [], "ticket_count": 0, "last_evaluated_at": None,
                    "coverage_complete": False, "reason": "AssessmentQueryLimitExceeded", "message": str(exc)}
        latest = {}
        for item in assessments:
            if item["environment_ref"] == environment_ref and item["change_id"] in target_changes:
                latest.setdefault((item["change_id"], item["consumer_digest"]), item)
        rows = list(latest.values())
        missing = target_changes - {item["change_id"] for item in rows}
        links = self.store.ticket_links() if rows else {}
        ticket_ids = sorted({links[item["assessment_id"]] for item in rows if item["action_required"] and item["assessment_id"] in links})
        tickets = []
        if ticket_ids:
            from adaos.services.development_tickets import DevelopmentTicketService
            service = DevelopmentTicketService(state_dir=self.state_dir)
            for identity in ticket_ids:
                ticket = service.get_ticket(identity)
                if ticket and ticket["status"] not in TERMINAL_TICKETS:
                    tickets.append({"ticket_id": identity, "status": ticket["status"], "summary": ticket["summary"]})
        stale = [item for item in rows if item["evidence_expires_at"] and
                 instant(item["evidence_expires_at"]) <= datetime.now(timezone.utc)]
        statuses = {item["compatibility"] for item in rows}
        posture = "unknown" if not rows else "aligned"
        if any(item["action_required"] for item in rows):
            posture = "action_required"
        elif rows and (missing or stale or statuses & {"unknown", "verification_due"}):
            posture = "verification_due"
        elif "migration_available" in statuses:
            posture = "migration_ready"
        elif "deprecated_compatible" in statuses:
            posture = "compatible_legacy"
        return {"schema": "adaos.application.contract_posture.v1", "application_ref": application_ref,
                "consumer_digest": consumer_digest, "posture": posture, "assessments": rows if detail else [],
                "assessment_count": len(rows), "action_required_count": sum(item["action_required"] for item in rows),
                "coverage_complete": bool(rows) and not missing,
                "unassessed_change_count": len(missing),
                "stale_evidence_count": len(stale), "tickets": tickets, "ticket_count": len(tickets),
                "last_evaluated_at": max((item["recorded_at"] for item in rows), default=None)}

    def wave_projection(self, wave_id: str) -> dict[str, Any]:
        wave = self.store.get(wave_id)
        if wave["schema"] != WAVE:
            raise ValueError("expected EvolutionWave")
        latest = {}
        for identity in wave["change_ids"]:
            for row in self.store.list(ASSESSMENT, change_id=identity, current=True):
                if row["wave_id"] == wave_id:
                    latest.setdefault((row["application_ref"], row["consumer_digest"], identity, row["environment_ref"]), row)
        rows = list(latest.values())
        links = self.store.ticket_links()
        return {"wave": wave, "coverage": dict(Counter(row["compatibility"] for row in rows)),
                "assessed_edges": len(rows), "consumers": sorted({row["application_ref"] for row in rows}),
                "actionable_without_ticket": sum(row["action_required"] and row["assessment_id"] not in links for row in rows),
                "assessments": rows}
