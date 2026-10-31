import json

import pytest
from test_builder_generation_observations import response, retained
from test_builder_generation_runs import generation_rows
from test_builder_interpretation_runs import planned
from test_builder_workflow import workflow_project as _workflow_project

from adaos.services.builder.generation_runs import GenerationRuns
from adaos.services.builder.interpretation_runs import InterpretationRunRejected
from adaos.services.builder.provider_jobs import PlannedBuilderJobs

workflow_project = _workflow_project


def recover(service, folder, result):
    class Transport:
        def wait(self, job_id, **options):
            assert job_id == result["job_id"]
            return result

        def submit(self, *args, **options):
            pytest.fail("Exact output recovery cannot spend again")

    return PlannedBuilderJobs(
        Transport(), None, folder, run_observer=GenerationRuns(service)
    ).wait(result["job_id"])


def test_completed_output_is_private_pinned_and_recovered_without_authoring(
    workflow_project, tmp_path
):
    from adaos.services.builder.generation_outputs import GenerationOutputs

    service, _ = workflow_project
    folder = tmp_path / "jobs"
    _, plan, origin, binding, wire = retained(service, folder)
    result = response()
    result["output_text"] = ' \n{"title":"Точный текст"}\r\n'
    before = service.describe("scenario", "recipes")
    delivered = recover(service, folder, result)
    reference = delivered["generation_output"]
    store = GenerationOutputs(service.state_dir)
    assert (
        store.load(
            reference["output_ref"],
            journal_dir=folder,
            binding=binding,
            origin=origin,
            plan=plan,
            wire=wire,
        )
        == result["output_text"]
    )
    assert "PRIVATE" not in json.dumps(reference) and "output_text" not in reference
    files = list(store.root.glob("*.json"))
    assert len(files) == 1 and files[0].is_relative_to(service.state_dir)
    private = files[0].read_text(encoding="utf-8")
    assert "PRIVATE EXCEPTION" not in private and "PRIVATE TOKEN" not in private
    assert all(
        "output_text" not in path.read_text(encoding="utf-8")
        for path in folder.glob("generation-observation-*.json")
    )
    after = service.describe("scenario", "recipes")
    for key in (
        "generation",
        "prototype",
        "automation",
        "active_phase",
        "governed",
        "history",
    ):
        assert after.get(key) == before.get(key)
    assert generation_rows(service)[0]["status"] == "running"
    snapshot = files[0].read_bytes()
    planned(service, "CH-later")
    assert recover(service, folder, result)["generation_output"] == reference
    assert files[0].read_bytes() == snapshot


def test_terminal_output_conflict_refuses_without_erasing_paid_measurements(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    folder = tmp_path / "jobs"
    retained(service, folder)
    recover(service, folder, response())
    before = generation_rows(service)
    conflicting = {**response(), "output_text": "DIFFERENT RESPONSE"}
    with pytest.raises(InterpretationRunRejected):
        recover(service, folder, conflicting)
    assert generation_rows(service) == before


def test_missing_completed_text_refuses_after_durable_usage_without_new_submission(
    workflow_project, tmp_path
):
    service, _ = workflow_project
    folder = tmp_path / "jobs"
    retained(service, folder)
    missing = response()
    missing.pop("output_text")
    with pytest.raises(InterpretationRunRejected):
        recover(service, folder, missing)
    row = generation_rows(service)[0]
    assert (
        row["status"] == "running" and row["execution_metrics"]["provider_calls"] == 1
    )
    assert recover(service, folder, response())["generation_output"]["output_ref"]


@pytest.mark.parametrize(
    "fault", ["bytes", "rehashed_text", "reference", "observation", "wire"]
)
def test_loading_requires_intact_content_and_exact_retained_provenance(
    workflow_project, tmp_path, fault
):
    from adaos.services.builder.generation_outputs import GenerationOutputs
    from adaos.services.builder.task_spec import seal

    service, _ = workflow_project
    folder = tmp_path / "jobs"
    _, plan, origin, binding, wire = retained(service, folder)
    reference = recover(service, folder, response())["generation_output"]["output_ref"]
    store = GenerationOutputs(service.state_dir)
    (path,) = store.root.glob("*.json")
    if fault == "bytes":
        path.write_text("{broken", encoding="utf-8")
    elif fault == "rehashed_text":
        row = json.loads(path.read_text(encoding="utf-8"))
        row["output_text"] = "changed with stale text hash"
        path.write_text(
            json.dumps(seal(row, id_key="output_id", prefix="generation-output:")),
            encoding="utf-8",
        )
    elif fault == "reference":
        reference = "generation-output:" + "a" * 24
    elif fault == "observation":
        (observation,) = folder.glob("generation-observation-*.json")
        observation.unlink()
    elif fault == "wire":
        wire = {**wire, "job_id": "another-job"}
    before = path.read_bytes()
    with pytest.raises(InterpretationRunRejected):
        store.load(
            reference,
            journal_dir=folder,
            binding=binding,
            origin=origin,
            plan=plan,
            wire=wire,
        )
    assert path.read_bytes() == before


def test_output_storage_failure_keeps_usage_and_exact_wait_repairs_without_spending(
    workflow_project, tmp_path, monkeypatch
):
    from adaos.services.builder import generation_outputs as module

    service, _ = workflow_project
    folder = tmp_path / "jobs"
    retained(service, folder)
    original = module.atomic_write_json

    def fail(*args, **kwargs):
        raise OSError("simulated output storage failure")

    monkeypatch.setattr(module, "atomic_write_json", fail)
    with pytest.raises(InterpretationRunRejected):
        recover(service, folder, response())
    row = generation_rows(service)[0]
    assert (
        row["execution_metrics"]["provider_calls"] == 1 and row["status"] == "running"
    )
    monkeypatch.setattr(module, "atomic_write_json", original)
    assert recover(service, folder, response())["generation_output"]["output_ref"]
    assert generation_rows(service) == [row]


@pytest.mark.parametrize("text", ["", "\r\n", " \nЮникод 🙂\r\n"])
def test_text_is_not_trimmed_or_reinterpreted_as_candidate_acceptance(
    workflow_project, tmp_path, text
):
    import hashlib

    service, _ = workflow_project
    folder = tmp_path / "jobs"
    retained(service, folder)
    result = recover(service, folder, {**response(), "output_text": text})
    assert result["generation_output"]["text_bytes"] == len(text.encode("utf-8"))
    assert (
        result["generation_output"]["text_digest"]
        == "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
    )
    assert generation_rows(service)[0]["status"] == "running"


def test_store_bounds_actual_reads_and_refuses_linked_ancestors(tmp_path, monkeypatch):
    import stat
    from pathlib import Path
    from types import SimpleNamespace

    from adaos.services.builder import generation_outputs as module

    path = tmp_path / "bounded.json"
    path.write_bytes(b" " * 17)
    monkeypatch.setattr(module, "_MAX_BYTES", 16)
    with pytest.raises(InterpretationRunRejected):
        module.GenerationOutputs._read(path)
    original = Path.lstat

    def linked(component):
        return (
            SimpleNamespace(st_mode=stat.S_IFLNK)
            if component == tmp_path
            else original(component)
        )

    monkeypatch.setattr(Path, "lstat", linked)
    with pytest.raises(InterpretationRunRejected):
        module.GenerationOutputs._safe(path)
