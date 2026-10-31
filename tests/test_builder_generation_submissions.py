import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from adaos.services.builder.generation_submissions import GenerationSubmissions
from adaos.services.builder.interpretation_runs import InterpretationRunRejected
from adaos.services.builder_context import digest
from test_builder_generation_observations import retained, observe, response
from test_builder_workflow import workflow_project as _workflow_project

workflow_project = _workflow_project


def setup(service, folder):
    _, plan, origin, binding, wire = retained(service, folder)
    scope = GenerationSubmissions(folder, binding=binding, origin=origin, plan=plan)
    return scope, plan, origin, binding, wire


def completed(service, folder):
    scope, plan, origin, binding, wire = setup(service, folder)
    with scope.submitting(wire["request_id"]) as register:
        register(response())
        observe(service, folder, plan, origin, binding, wire, response())
    return scope, wire


def test_terminal_exact_wire_set_closes_idempotently_without_claiming_turn_or_product_success(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    scope, _ = completed(service, tmp_path / "jobs")
    before = service.describe("scenario", "recipes")
    closure = scope.close()
    assert closure["submission_closed_for_generation_run"] is True
    assert closure["whole_turn_accounting_complete"] is False
    assert closure["product_acceptance"] == "not_implied"
    assert [row["job_id"] for row in closure["submission_refs"]] == ["primary-job"]
    assert len(closure["observation_refs"]) == 1
    assert service.describe("scenario", "recipes") == before
    stored = scope.path.read_bytes()
    assert scope.close() == closure and scope.path.read_bytes() == stored
    with pytest.raises(InterpretationRunRejected):
        with scope.submitting("new-paid-request"):
            pytest.fail("A closed Run admitted a new provider boundary")
    assert scope.path.read_bytes() == stored


def test_unknown_submission_outcome_fences_all_retry_and_repair_without_zero_claim(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    scope, _, _, _, wire = setup(service, tmp_path / "jobs")
    with pytest.raises(RuntimeError, match="uncertain"):
        with scope.submitting(wire["request_id"]):
            raise RuntimeError("uncertain provider boundary")
    state = json.loads(scope.path.read_text(encoding="utf-8"))
    assert state["submissions"][0]["status"] == "outcome_unknown"
    assert "provider_calls" not in state and "job_id" not in state["submissions"][0]
    for request_id in (wire["request_id"], "different-repair-request"):
        with pytest.raises(InterpretationRunRejected):
            with scope.submitting(request_id):
                pytest.fail("Unknown inference authorized a fresh submission")
    with pytest.raises(InterpretationRunRejected):
        scope.close()


@pytest.mark.parametrize("status", ["queued", "running", "interrupted"])
def test_nonterminal_observation_cannot_close_a_registered_submission_set(
    workflow_project, tmp_path, status
):
    service, _ = workflow_project
    folder = tmp_path / "jobs"
    scope, plan, origin, binding, wire = setup(service, folder)
    result = {"job_id": "primary-job", "status": status}
    with scope.submitting(wire["request_id"]) as register:
        register(result)
        observe(service, folder, plan, origin, binding, wire, result)
    with pytest.raises(InterpretationRunRejected):
        scope.close()
    assert json.loads(scope.path.read_text(encoding="utf-8"))["phase"] == "open"


def test_scope_closure_checks_the_exact_registered_wire_not_just_job_names(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    folder = tmp_path / "jobs"
    scope, wire = completed(service, folder)
    wire_path = folder / ("wire-" + digest(wire["request_id"])[7:39] + ".json")
    wire["context_admission"]["unrelated"] = "mutated"
    wire_path.write_text(json.dumps(wire), encoding="utf-8")
    with pytest.raises(InterpretationRunRejected):
        scope.close()
    assert json.loads(scope.path.read_text(encoding="utf-8"))["phase"] == "open"


def test_close_waits_for_the_in_progress_submission_boundary_then_sees_its_terminal_receipt(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    folder = tmp_path / "jobs"
    scope, plan, origin, binding, wire = setup(service, folder)
    entered, release, closing = Event(), Event(), Event()

    def submit():
        with scope.submitting(wire["request_id"]) as register:
            entered.set()
            assert release.wait(5)
            register(response())
            observe(service, folder, plan, origin, binding, wire, response())

    def close():
        closing.set()
        return scope.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        sending = pool.submit(submit)
        assert entered.wait(5)
        finishing = pool.submit(close)
        assert closing.wait(5)
        try:
            assert not finishing.done()
        finally:
            release.set()
        sending.result(timeout=5)
        assert (
            finishing.result(timeout=5)["submission_closed_for_generation_run"] is True
        )


def test_corrupt_scope_is_a_typed_refusal_not_an_empty_new_execution(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    scope, _ = completed(service, tmp_path / "jobs")
    scope.path.write_text('{"broken":', encoding="utf-8")
    with pytest.raises(InterpretationRunRejected):
        with scope.submitting("new"):
            pytest.fail("Corrupt history authorized spending")


@pytest.mark.parametrize("operation", ["submit", "send"])
def test_native_provider_boundary_obeys_the_closed_run_fence(
    workflow_project, tmp_path, operation
):
    from adaos.services.builder.provider_jobs import PlannedBuilderJobs
    from adaos.services.builder.generation_runs import GenerationRuns
    from adaos.services.policy.invocation import verified_invocation

    service, _ = workflow_project
    folder = tmp_path / "jobs"
    scope, plan, _, _, wire = setup(service, folder)
    calls = []

    class Transport:
        def submit(self, *args, **options):
            calls.append(options["request_id"])
            return {
                "job_id": "primary-job",
                "status": "queued",
                "context_admission": wire["context_admission"],
            }

        def send(self, *args, **options):
            calls.append(options["request_id"])
            return {**response(), "context_admission": wire["context_admission"]}

        def wait(self, *args, **options):
            return response()

    jobs = PlannedBuilderJobs(
        Transport(), plan, folder, run_observer=GenerationRuns(service)
    )
    with verified_invocation(request_id="trusted-turn", idempotency_key=None):
        getattr(jobs, operation)([], request_id=wire["request_id"])
        if operation == "submit":
            jobs.wait("primary-job")
        assert scope.close()["submission_closed_for_generation_run"] is True
        with pytest.raises(InterpretationRunRejected):
            getattr(jobs, operation)([], request_id="must-not-spend")
    assert calls == [wire["request_id"]]


def test_capacity_refuses_without_truncating_registered_submission_identities(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    scope, _, _, _, _ = setup(service, tmp_path / "jobs")
    for index in range(16):
        with scope.submitting("request-" + str(index)) as register:
            register({"job_id": "job-" + str(index)})
    before = scope.path.read_bytes()
    with pytest.raises(InterpretationRunRejected):
        with scope.submitting("seventeenth"):
            pytest.fail("Submission scope exceeded its explicit capacity")
    assert scope.path.read_bytes() == before
    assert len(json.loads(before)["submissions"]) == 16


def test_escaped_registration_cannot_reopen_a_closed_execution(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    folder = tmp_path / "jobs"
    scope, plan, origin, binding, wire = setup(service, folder)
    with scope.submitting(wire["request_id"]) as register:
        register(response())
        observe(service, folder, plan, origin, binding, wire, response())
    scope.close()
    before = scope.path.read_bytes()
    with pytest.raises(InterpretationRunRejected):
        register(response())
    assert scope.path.read_bytes() == before
    with pytest.raises(InterpretationRunRejected):
        with scope.submitting("new-boundary"):
            pytest.fail("Escaped registration reopened a closed scope")
