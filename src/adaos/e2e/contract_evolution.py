"""Provider-free local Contract Evolution qualification using native Builder rails.

The target SDK contract is deliberately tightened inside the experiment only.
No live Core API is broken, no user Application is edited, and the forge is a
local filesystem fixture. The real worker, validators and Trial/activation
services execute the consumer repair.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import shutil
from unittest.mock import patch

from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.services.artifact_pipeline import ArtifactPublicationService, ReleaseRepository
from adaos.services.artifact_pipeline.packages import artifact_source_snapshot
from adaos.services.artifact_pipeline.storage import atomic_write_json
from adaos.services.builder.repair import BuilderRepairService
from adaos.services.builder.workflow import BuilderWorkflowService
from adaos.services.contract_evolution.producers import current_contracts, publish_sdk_changes
from adaos.services.contract_evolution.records import digest
from adaos.services.contract_evolution.service import ContractEvolutionService
from adaos.services.contract_evolution.usage import build_usage_lock
from adaos.services.development_tickets import DevelopmentTicketService, _autonomous_repair_brief
from adaos.services.root.service import _rewrite_skill_template_identity
from adaos.services.skill_factory import SkillFactoryService
from adaos.services.skill_factory_worker import LocalSkillFactoryWorker


class _LocalForge:
    """The same exact-release/bytes protocol as a remote publisher, without network."""

    def __init__(self, root):
        self.releases = ReleaseRepository(root)
        self.archives = {}

    def tree_revision(self, source):
        return source.revision

    def put_release(self, plan, archives):
        self.archives.update(archives)
        self.releases.put_release(plan)

    def get_release(self, project_id, release_digest):
        return self.releases.get_release(project_id, release_digest)

    def set_channel(self, plan, channel="stable", *, expected_release_digest):
        return self.releases.set_channel(plan.release.project_id, channel, plan.release.release_digest,
                                         expected_release_digest=expected_release_digest)

    def get_channel(self, project_id, channel="stable"):
        return self.releases.get_channel(project_id, channel)

    def fetch_package(self, package):
        return self.archives[package.digest]


def qualify_contract_evolution(root: Path, *, repo_root: Path) -> dict:
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    state = root / "state"
    skills = root / "dev" / "skills"
    skill_id = "cef_compatibility_probe"
    source = skills / skill_id
    shutil.copytree(repo_root / "src/adaos/skills_templates/skill_default", source)
    _rewrite_skill_template_identity(source, skill_id)
    old_call = "return applications.get_application(application_id)"
    new_call = 'return applications.get_application(application_id, webspace_id="desktop")'
    probe = source / "handlers" / "compatibility_probe.py"
    probe.write_text("from adaos.sdk import applications\n\ndef inspect_application(application_id):\n    " + old_call + "\n", encoding="utf-8")
    test = source / "tests" / "test_contract_upgrade.py"
    test.parent.mkdir(exist_ok=True)
    test.write_text("import ast\nfrom pathlib import Path\n\ndef test_required_webspace_is_explicit():\n"
        "    tree = ast.parse((Path(__file__).parents[1] / 'handlers/compatibility_probe.py').read_text())\n"
        "    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]\n"
        "    assert any(k.arg == 'webspace_id' for call in calls for k in call.keywords)\n", encoding="utf-8")

    service = ContractEvolutionService(state)
    units = current_contracts()
    contract_ref = "sdk:adaos.sdk.applications.get_application"
    old_contract = deepcopy(units[contract_ref])
    new_contract = deepcopy(old_contract)
    new_contract["input_schema"]["required"] = sorted(set(new_contract["input_schema"].get("required", [])) | {"webspace_id"})
    new_contract["contract"]["digest"] = digest({"target_input": new_contract["input_schema"], "experiment": "required_webspace"})
    delta = publish_sdk_changes(service, {"tools": [old_contract]}, {"tools": [new_contract]})[0]
    before = artifact_source_snapshot(source)
    files = {item["path"]: (source / item["path"]).read_bytes() for item in before["files"]}
    entry = service.register_usage(build_usage_lock(files, application_ref="application:cef_probe",
        consumer_ref="skill:" + skill_id, consumer_digest=before["digest"], contracts=units))
    unaffected = service.register_usage(build_usage_lock({}, application_ref="application:cef_unaffected",
        consumer_ref="scenario:cef_unaffected", consumer_digest=digest("unaffected"), contracts=units))
    wave = service.create_wave([delta["change_id"]], owner_ref="core:sdk")
    affected = service.assess(wave_id=wave["wave_id"], usage_lock_id=entry["usage_lock_id"])[0]
    ignored = service.assess(wave_id=wave["wave_id"], usage_lock_id=unaffected["usage_lock_id"])[0]
    assert ignored["compatibility"] == "unaffected"
    assert service.materialize_ticket(ignored["assessment_id"]) is None
    ticket = service.materialize_ticket(affected["assessment_id"])
    assert ticket and service.materialize_ticket(affected["assessment_id"])["ticket_id"] == ticket["ticket_id"]

    tickets = DevelopmentTicketService(state_dir=state)
    handoff = tickets.handoff_ticket(ticket["ticket_id"], mode="interactive", actor="e2e:operator",
                                     repair_service=BuilderRepairService(state_dir=state))
    workflow = BuilderWorkflowService(dev_skills_root=skills, dev_scenarios_root=root / "dev/scenarios", state_dir=state)
    planned = workflow.transition("skill", skill_id, "plan_change_set", actor="e2e:operator", metadata={
        "change_set_id": "CH-cef-upgrade", "request": ticket["summary"],
        "issues": [{"issue_id": ticket["ticket_id"], "title": ticket["summary"], "lane": "automation",
                    "acceptance_criteria": ["Pass the required-webspace target contract and runtime smoke."]}],
    })["workflow"]
    brief = _autonomous_repair_brief(ticket, handoff["repair"], target={"type": "skill", "id": skill_id})
    relative = f"skills/{skill_id}/handlers/compatibility_probe.py"
    factory = SkillFactoryService(state_dir=state)
    submitted = factory.submit_realize_request({
        "target": {"type": "skill", "id": skill_id},
        "artifacts": {"implementation_brief": brief,
            "execution_budget": {"source": "cef.qualified_migration", "max_tokens": 1000, "max_wall_seconds": 300},
            "repair_hints": {"profile": "surgical_data", "target_files": [relative],
                "structured_edits": {"schema": "adaos.builder.structured_edit_set.v1", "operations": [
                    {"id": "required-webspace", "op": "replace_text", "path": relative,
                     "old": old_call, "new": new_call, "expected_count": 1}]}}},
        "repo": {"sparse_paths": [f"skills/{skill_id}/"]},
        "constraints": {"mode": "dev_ticket_repair", "repair_profile": "surgical_data",
                        "exact_changed_paths": [relative], "max_changed_files": 1},
    })
    def forbidden_model(**_):
        raise AssertionError("An admitted exact migration must not invoke a model")
    worker = LocalSkillFactoryWorker(state_dir=state, repo_root=repo_root, dev_skills_root=skills,
        dev_scenarios_root=root / "dev/scenarios", runs_root=root / "runs", executor=forbidden_model)
    result = worker.run_once(task_id=submitted["task"]["task_id"])
    if not result.get("ok"):
        atomic_write_json(root / "worker-failure.json", result)
        raise AssertionError("Native worker failed; inspect the owned worker-failure.json")
    assert result["result"]["execution_strategy"] == "structured_edits"
    assert new_call in probe.read_text(encoding="utf-8")
    spec = importlib.util.spec_from_file_location("cef_consumer_probe", probe)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with patch("adaos.sdk.applications.get_application", return_value={"application_id": "fixture"}) as sdk:
        assert module.inspect_application("fixture") == {"application_id": "fixture"}
        sdk.assert_called_once_with("fixture", webspace_id="desktop")
    smoke = {"kind": "runtime_smoke", "calls": [{"application_id": "fixture", "webspace_id": "desktop"}],
             "scope": "isolated consumer import and invocation; external Application service is hermetic"}

    publication = ArtifactPublicationService(state_root=root / "publication", workspace_root=root / "workspace",
                                             remote=_LocalForge(root / "forge"))
    source_ref = ArtifactSourceRef(forge="content-addressed-dev", repository="cef-local-fixture",
        revision=digest(artifact_source_snapshot(source))[7:47], path_scope=(f"skills/{skill_id}/",))
    publication.record_push(kind="skill", artifact_id=skill_id, artifact_dir=source, source_ref=source_ref)
    candidate = publication.prepare_candidate(kind="skill", artifact_id=skill_id, artifact_dir=source,
        change_ids=(planned["change"]["change_id"],), validation_evidence={"status": "passed", "suite": "cef.consumer", "smoke": smoke})
    accepted = publication.decide_candidate(candidate.candidate.candidate_id, accepted=True,
        observations=({"actor": "e2e:operator", "scope": "synthetic compatibility probe", "decision": "accept"},))
    promoted = publication.promote(accepted.candidate_id,
        reload_policy={"mode": "skip", "approved_by": "e2e:operator", "reason": "owned fixture has no background runtime"},
        health_check=lambda lock: (root / "workspace/skills" / skill_id / "handlers/compatibility_probe.py").is_file())
    after = artifact_source_snapshot(source)
    after_files = {item["path"]: (source / item["path"]).read_bytes() for item in after["files"]}
    upgraded = service.register_usage(build_usage_lock(after_files, application_ref=entry["application_ref"],
        consumer_ref=entry["consumer_ref"], consumer_digest=promoted.pointer.release_digest,
        contracts={**units, contract_ref: new_contract}))
    for kind, report in (("contract_test", result["result"]), ("runtime_smoke", smoke)):
        service.record_verification(application_ref=entry["application_ref"], consumer_digest=upgraded["consumer_digest"],
            change_id=delta["change_id"], environment_ref="profile:local/default", kind=kind, result="passed",
            report_digest=digest(report), valid_until=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), runner_ref="e2e:cef-native")
    verified = service.assess(wave_id=wave["wave_id"], usage_lock_id=upgraded["usage_lock_id"])[0]
    assert verified["compatibility"] == "compatible"
    refs = [{"type": "contract_assessment", "ref": verified["assessment_id"]},
            {"type": "application_release", "ref": promoted.pointer.release_digest}]
    tickets.record_resolution(ticket["ticket_id"], evidence_refs=refs, actor="e2e:operator",
        resolved_by_version=candidate.plan.release.version,
        repair_service=BuilderRepairService(state_dir=state), repair_id=handoff["repair"]["repair_id"])
    tickets.verify_ticket(ticket["ticket_id"], evidence_refs=refs, actor="e2e:verifier")
    tickets.close_ticket(ticket["ticket_id"], evidence_refs=refs, reason="verified", actor="e2e:operator")
    report = {"schema": "adaos.e2e.contract_evolution.v1", "passed": True,
        "scope": "local native service integration; synthetic SDK tightening; no user source or external forge mutation",
        "change_id": delta["change_id"], "wave_id": wave["wave_id"], "ticket_id": ticket["ticket_id"],
        "repair_id": handoff["repair"]["repair_id"], "builder_change_id": planned["change"]["change_id"],
        "task_id": submitted["task"]["task_id"], "execution_strategy": result["result"]["execution_strategy"],
        "candidate_id": accepted.candidate_id, "release_digest": promoted.pointer.release_digest,
        "usage_lock_id": upgraded["usage_lock_id"], "assessment_id": verified["assessment_id"],
        "unaffected_without_ticket": True, "exact_ticket_dedup": True,
        "native_trial_and_workspace_activation": True, "wave": service.wave_projection(wave["wave_id"])}
    atomic_write_json(root / "qualification.json", report)
    return report
