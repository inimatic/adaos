import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from adaos.services.builder.interpretation_ingress import admit_ingress
from adaos.services.builder.task_interpretation import TaskInterpretationRejected
from adaos.services.builder.task_spec import compile_task_spec
from adaos.services.builder_intent import capture_intent, compile_prototype_brief
from test_builder_task_interpretation import prepared
from test_builder_task_pipeline import Provider, invocation


def reserve(
    directory,
    *,
    task=None,
    intent=None,
    request_id="builder-interpretation-original",
    context=None,
):
    source, seed, _ = prepared()
    return admit_ingress(
        directory,
        task=task or seed,
        intent=intent or source,
        request_id=request_id,
        context=context or {"profile": "original"},
        prior_brief=None,
    )


def journal(directory, *, request_id="builder-interpretation-original", context=None):
    record = {
        "task_spec": prepared()[1],
        "request_id": request_id,
        "context": context or {"profile": "original"},
    }
    path = directory / (request_id + ".json")
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_exact_reservation_recovers_only_with_its_durable_original_journal(tmp_path):
    first = reserve(tmp_path)
    with pytest.raises(TaskInterpretationRejected) as uncertain:
        reserve(tmp_path)
    assert (
        uncertain.value.findings[0]["code"] == "task.interpretation_ingress_uncertain"
    )
    journal(tmp_path)
    assert reserve(tmp_path) == first


@pytest.mark.parametrize("changed", ["request", "context", "task", "history"])
def test_same_acknowledged_turn_cannot_gain_another_submission_identity(
    tmp_path, changed
):
    first = reserve(tmp_path)
    journal(tmp_path)
    source, task, _ = prepared()
    from adaos.services.builder.task_spec import seal

    params = dict(
        task=task,
        intent=source,
        request_id="builder-interpretation-original",
        context={"profile": "original"},
        prior_brief=None,
    )
    if changed == "request":
        params["request_id"] = "builder-interpretation-new"
    elif changed == "context":
        params["context"] = {"profile": "changed"}
    elif changed == "task":
        params["task"] = seal({**task, "design_freedom": ["new interpretation"]})
    else:
        params["prior_brief"] = {"digest": "different-history"}
    with pytest.raises(TaskInterpretationRejected) as caught:
        admit_ingress(tmp_path, **params)
    assert caught.value.findings[0]["code"] == "task.interpretation_ingress_changed"
    assert len(list(tmp_path.glob("interpretation-ingress-*.json"))) == 1
    assert (
        json.loads(
            next(tmp_path.glob("interpretation-ingress-*.json")).read_text(
                encoding="utf-8"
            )
        )
        == first
    )


def test_legacy_same_turn_request_is_adopted_without_overwriting_it(tmp_path):
    path = journal(tmp_path)
    before = path.read_bytes()
    reserve(tmp_path)
    assert path.read_bytes() == before


def test_legacy_same_turn_packet_change_is_blocked_before_a_new_fence(tmp_path):
    journal(tmp_path)
    with pytest.raises(TaskInterpretationRejected) as caught:
        reserve(tmp_path, request_id="builder-interpretation-new")
    assert caught.value.findings[0]["code"] == "task.interpretation_ingress_changed"
    assert not list(tmp_path.glob("interpretation-ingress-*.json"))


def test_multiple_legacy_same_turn_packets_are_not_selected_by_filename(tmp_path):
    journal(tmp_path)
    journal(tmp_path, request_id="builder-interpretation-another")
    with pytest.raises(TaskInterpretationRejected) as caught:
        reserve(tmp_path)
    assert caught.value.findings[0]["code"] == "task.interpretation_ingress_ambiguous"


@pytest.mark.parametrize("corruption", ["invalid_json", "wrong_digest", "wrong_shape"])
def test_corrupt_fence_fails_closed_without_private_exception_prose(
    tmp_path, corruption
):
    reserve(tmp_path)
    fence = next(tmp_path.glob("interpretation-ingress-*.json"))
    if corruption == "invalid_json":
        fence.write_text("PRIVATE", encoding="utf-8")
    elif corruption == "wrong_shape":
        fence.write_text("[]", encoding="utf-8")
    else:
        content = json.loads(fence.read_text(encoding="utf-8"))
        content["source_digest"] = "PRIVATE"
        fence.write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(TaskInterpretationRejected) as caught:
        reserve(tmp_path)
    assert caught.value.findings[0]["gate"] == "G5"
    assert "PRIVATE" not in str(caught.value)


def test_concurrent_changed_profiles_admit_at_most_one_reserved_submission(tmp_path):
    def attempt(index):
        try:
            reserve(
                tmp_path,
                request_id=f"builder-interpretation-{index}",
                context={"profile": index},
            )
            return "admitted"
        except TaskInterpretationRejected:
            return "blocked"

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(attempt, [0, 1]))
    assert sorted(results) == ["admitted", "blocked"]
    assert len(list(tmp_path.glob("interpretation-ingress-*.json"))) == 1


def test_a_new_trusted_source_turn_has_its_own_ingress_identity(tmp_path):
    source, _, _ = prepared()
    reserve(tmp_path)
    next_source = capture_intent(
        source["statement"],
        project_ref="project:qualification",
        source={"kind": "chat", "ref": "a-new-trusted-message"},
    )
    task = compile_task_spec(next_source, compile_prototype_brief(next_source))
    second = reserve(
        tmp_path,
        task=task,
        intent=next_source,
        request_id="builder-interpretation-new-turn",
    )
    assert second["source_digest"] == next_source["digest"] != source["digest"]
    assert len(list(tmp_path.glob("interpretation-ingress-*.json"))) == 2


def test_profile_change_after_paid_attempt_does_not_submit_or_poll_again(
    tmp_path, monkeypatch
):
    provider = Provider(copy.deepcopy(prepared()[2]))
    invocation(tmp_path, provider)
    monkeypatch.setenv("ADAOS_BUILDER_INTERPRETATION_OUTPUT_MODE", "json_text_envelope")
    with pytest.raises(TaskInterpretationRejected) as caught:
        invocation(tmp_path, provider)
    assert caught.value.findings[0]["code"] == "task.interpretation_ingress_changed"
    assert len(provider.submissions) == 1 and len(provider.observations) == 1


def test_validator_only_change_cannot_overwrite_a_journal_or_buy_another_call(tmp_path, monkeypatch):
    from adaos.services.builder import interpretation_revalidation

    provider = Provider(copy.deepcopy(prepared()[2]))
    invocation(tmp_path, provider)
    path, = tmp_path.glob('builder-interpretation-*.json')
    before = path.read_bytes()
    retained = json.loads(before)
    assert retained['admission_policy_digest'] == interpretation_revalidation.validator_digest()
    assert 'admission_policy_digest' not in retained['context']
    monkeypatch.setattr(interpretation_revalidation, 'validator_digest', lambda: 'changed-validator')
    with pytest.raises(TaskInterpretationRejected) as caught:
        invocation(tmp_path, provider)
    assert caught.value.findings[0]['code'] == 'task.interpretation_ingress_changed'
    assert path.read_bytes() == before
    assert len(list(tmp_path.glob('builder-interpretation-*.json'))) == 1
    assert len(provider.submissions) == 1 and len(provider.observations) == 1
