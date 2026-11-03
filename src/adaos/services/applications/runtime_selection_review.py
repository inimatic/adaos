"""Review and select retained runtimes without accepting or removing a Beta."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from adaos.domain.application import ApplicationOperation, RuntimeSelection, utc_now
from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.artifact_pipeline.storage import mutation_lock
from .local_beta_admission import read_local_beta_admission
from .runtime_channel import ApplicationRuntimeChannel, RuntimeChannelConflict
from .runtime_transition import ApplicationRuntimeTransition, TransitionStep


def assert_selection_idle(ops, application_id, selections, target):
    from .lifecycle_operations import assert_idle_jobs
    from adaos.services.artifact_pipeline.trial_activation import trial_workspace_root
    values = [*selections, target]
    refs = {package.key for digest in {item["release_digest"] for item in values}
            for package in ops.store.get_release(application_id, digest).project_release.components
            if package.kind == "skill"}
    roots = {ops.workspace if item["runtime_root_ref"] == "workspace" else
             trial_workspace_root(ops.workspace, item["runtime_root_ref"].removeprefix("trial:"))
             for item in values}
    for root in roots:
        assert_idle_jobs(ops.state, application_id, root, component_refs=refs)


def configuration_targets(ops, application_id, target_digest, candidate_id, selected_digest):
    from .configuration import ApplicationConfigurationStore, _digest
    refs = {package.key for digest in {target_digest, selected_digest} if digest
            for package in ops.store.get_release(application_id, digest).project_release.components}
    records = []
    for ref in sorted(refs):
        store = ApplicationConfigurationStore(ops.state, application_id, ref)
        if not store.path.is_file():
            continue
        record = store.read()
        beta = record.get("beta")
        if not beta:
            continue
        if candidate_id and (beta["candidate_id"] != candidate_id or beta["release_digest"] != target_digest):
            raise RuntimeChannelConflict("RetainedBetaConfigurationUnavailable")
        records.append({"component_ref": ref, "expected_digest": _digest(record)})
    return records


def verify_target(ops, application_id, position, release_digest, candidate_id):
    """Verify exact retained code and its admission; never copy or migrate data."""
    if position == "private_beta":
        from .trial_runtime import NativeTrialRuntime
        candidate, activation = ops._trial(application_id, candidate_id)
        if candidate.release_digest != release_digest:
            raise RuntimeChannelConflict("ReviewedBetaReleaseChanged")
        try:
            installed = ops.store.get_installation(application_id)
        except FileNotFoundError:
            installed = None
        from .lifecycle import workspace_release_kind
        if (installed and installed.status == "active" and installed.installed_release_digest == release_digest
                and workspace_release_kind(ops.store, application_id, release_digest) == "stable"):
            raise RuntimeChannelConflict("BetaAlreadyAcceptedAsStable")
        admission = read_local_beta_admission(ops.state, candidate_id, release_digest, activation)
        if not admission or (activation.get("health_evidence") or {}).get("status") != "passed":
            raise RuntimeChannelConflict("InstalledHealthyBetaRequired")
        runtime = NativeTrialRuntime.resolve(ops.owner, candidate_id, release_digest)
        for package in runtime.packages:
            runtime.verified_source(package)
        return {"ok": True, "release_digest": release_digest, "candidate_digest": candidate.digest,
                "admission_receipt_digest": admission["receipt_digest"],
                "runtime_binding_digest": admission["runtime_binding_digest"]}
    if position == "private_stable":
        if candidate_id is not None:
            raise ValueError("Stable runtime selection does not accept a Candidate target")
        from .lifecycle import workspace_release_kind
        from .runtime_integrity import _release_observation
        installation = ops.store.get_installation(application_id)
        if installation.status != "active" or installation.installed_release_digest != release_digest:
            raise RuntimeChannelConflict("RetainedStableChanged")
        release = ops.store.get_release(application_id, release_digest)
        if workspace_release_kind(ops.store, application_id, release_digest) != "stable":
            raise RuntimeChannelConflict("RetainedPrivateStableRequired")
        observed = _release_observation(ops.owner, release, root=ops.workspace,
                                        runtime_root_ref="workspace", deep=True)
        if observed["status"] != "aligned":
            raise RuntimeChannelConflict("RetainedStableSourceUnverified")
        return {"ok": True, "release_digest": release_digest,
                "installation_revision": installation.revision,
                "source_verified": True}
    raise ValueError("Select an installed local Beta or retained private Stable")


def plan_selection(ops, snapshot, *, application_id, position, release_digest,
                   candidate_id, webspace_id, actor_ref, subnet_ref, idempotency_key, operation_id):
    from .lifecycle_operations import PLAN_SCHEMA
    if ops.store.get_application(application_id).publisher_ref != subnet_ref:
        raise PermissionError("OnlyPublisherMaySelectPrivateRuntime")
    if position == "private_stable":
        if candidate_id is not None:
            raise ValueError("Stable runtime selection does not accept a Candidate target")
        target_digest = ops.store.get_installation(application_id).installed_release_digest
        candidate_id = None
        source, root_ref = "stable_installation", "workspace"
    elif position == "private_beta" and candidate_id:
        candidate, _activation = ops._trial(application_id, candidate_id)
        target_digest = candidate.release_digest
        source, root_ref = "local_trial", f"trial:{candidate_id}"
    else:
        raise ValueError("Exact retained runtime target is required")
    if release_digest and release_digest != target_digest:
        raise RuntimeChannelConflict("ReviewedRuntimeTargetChanged")
    if snapshot.get("transition") or (snapshot.get("operation") or {}).get("status") in {"unknown", "applying", "reconciling"}:
        raise RuntimeChannelConflict("RecoverPendingTransitionBeforeSelection")
    evidence = verify_target(ops, application_id, position, target_digest, candidate_id)
    selected = snapshot["runtime"]
    if (selected.get("source"), selected.get("release_digest"), selected.get("runtime_root_ref")) == (source, target_digest, root_ref):
        raise RuntimeChannelConflict("RuntimeAlreadySelected")
    values = tuple(RuntimeSelection.from_mapping(item) for item in selected["selections"])
    current = next((item for item in values if item.webspace_id == webspace_id), None)
    target = RuntimeSelection(webspace_id=webspace_id, application_id=application_id, source=source,
        release_digest=target_digest, runtime_root_ref=root_ref, revision=(current.revision if current else 0) + 1,
        updated_at=utc_now())
    assert_selection_idle(ops, application_id, selected["selections"], target.to_dict())
    plan = {"schema": PLAN_SCHEMA, "action": "select_runtime", "application_id": application_id,
        "target_position_id": position, "candidate_id": candidate_id, "release_digest": target_digest,
        "target_selection": target.to_dict(), "target_evidence": evidence,
        "expected_lifecycle_revision": snapshot["revision"], "expected_selections": selected["selections"],
        "webspace_id": webspace_id, "affected_webspace_ids": sorted(set(selected["webspace_ids"]) | {webspace_id}),
        "actor_ref": actor_ref, "subnet_ref": subnet_ref, "required_apply_capability": "applications.apply",
        "data_policy": "retain", "data_disposition": "Use the target edition's retained data; other editions and Beta admission are retained.",
        "migration_required": False, "acknowledge_write_loss": False,
        "configuration_selections": configuration_targets(ops, application_id, target_digest,
            candidate_id, selected.get("release_digest")),
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
        "summary": f"Select {position}: {application_id}; version {ops.store.get_release(application_id, target_digest).project_release.version}; retain each edition's existing data"}
    operation = ApplicationOperation(operation_id=operation_id, application_id=application_id, kind="reconcile",
        status="planned", actor_ref=actor_ref, subnet_ref=subnet_ref, plan_digest=canonical_payload_digest(plan),
        idempotency_key=idempotency_key, expected_revision=0, revision=1, plan=plan)
    return ops.store.put_operation(operation).to_dict()


def select_reviewed_runtime(ops, operation):
    from .materialization import request_materialization
    plan = operation.plan
    runner = ApplicationRuntimeTransition(ApplicationRuntimeChannel(ops.state, operation.application_id))
    transition_id = f"application-select:{operation.operation_id}"
    previous = runner.get(transition_id)
    proof = {}
    from .configuration import ApplicationConfigurationStore, ConfigurationConflict
    configurations = [(ApplicationConfigurationStore(ops.state, operation.application_id, item["component_ref"]), item)
                      for item in plan["configuration_selections"]]

    def select_configuration(_key):
        receipts = []
        for store, item in configurations:
            store.verify_retained_selection(operation_id=operation.operation_id, expected_digest=item["expected_digest"])
        for store, item in configurations:
            result = store.select_retained_runtime(operation_id=operation.operation_id,
                expected_digest=item["expected_digest"], candidate_id=plan.get("candidate_id"),
                release_digest=plan["release_digest"])
            receipts.append({"component_ref": item["component_ref"], "revision": result["revision"]})
        return {"ok": True, "selections": receipts}

    @contextmanager
    def source_guard():
        with mutation_lock(ops.workspace / ".adaos/.workspace-writer.lock", timeout_s=30):
            actual = verify_target(ops, operation.application_id, plan["target_position_id"],
                                   plan["release_digest"], plan.get("candidate_id"))
            if actual != plan["target_evidence"]:
                raise RuntimeChannelConflict("ReviewedRuntimeAdmissionChanged")
            assert_selection_idle(ops, operation.application_id, plan["expected_selections"], plan["target_selection"])
            for store, item in configurations:
                store.verify_retained_selection(operation_id=operation.operation_id, expected_digest=item["expected_digest"])
            proof.update(actual)
            yield

    if previous and previous.get("completed"):
        transition = previous
    else:
        try:
            transition = runner.run(transition_id, contract_digest=operation.plan_digest,
                expected=tuple(RuntimeSelection.from_mapping(item) for item in plan["expected_selections"]),
                target=RuntimeSelection.from_mapping(plan["target_selection"]),
                steps=[TransitionStep("select_retained_configuration", select_configuration),
                       TransitionStep("verify_retained_runtime", lambda _key: proof)], source_guard=source_guard)
        except ConfigurationConflict:
            # A settings write may finish while the cutover is draining old
            # read leases. Retire this review only when no configuration effect
            # belonging to it was applied. Partial writes retain the fence.
            def unchanged(_key):
                if any((store.read().get("runtime_selection") or {}).get("operation_id") == operation.operation_id
                       for store, _item in configurations):
                    raise RuntimeChannelConflict("PartialRuntimeConfigurationSelectionRequiresRecovery")
                return {"ok": True, "selection_not_applied": True}
            retained = runner.get(transition_id)
            if retained and not retained.get("completed"):
                runner.abort(transition_id, contract_digest=operation.plan_digest,
                    steps=[TransitionStep("verify_no_selection_effect", unchanged)],
                    source_guard=lambda: mutation_lock(ops.workspace / ".adaos/.workspace-writer.lock", timeout_s=30))
            raise
    refresh = request_materialization(ops.owner, operation.application_id, webspace_ids=plan["affected_webspace_ids"])
    return {"ok": True, "status": "runtime_selected", "data_policy": "retain",
            "runtime_transition_operation_id": transition["operation_id"], "runtime_refresh": refresh}
