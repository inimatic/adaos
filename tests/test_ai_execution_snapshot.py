import json

import pytest
from jsonschema import Draft202012Validator

from test_ai import service as _service, request, observe, native_ctx
from adaos.services.ai.contracts import (
    operation_schemas,
    inference_contract,
    chatgpt_binding,
)

service = _service


def test_snapshot_observes_the_complete_scoped_cohort_without_another_call(
    service, monkeypatch
):
    from adaos.services.policy import invocation

    monkeypatch.setattr(
        invocation, "current_invocation", lambda: {"request_id": "trusted-turn"}
    )
    ids = []
    for index in range(3):
        job = service.submit(
            "user:owner", "skill:builder_skill", f"stage-{index}", request()
        )
        observe(service, job["job_id"], consumer="skill:builder_skill")
        ids.append(job["job_id"])
    report = service.execution_snapshot("user:owner", "skill:builder_skill", ids[0])
    assert [row["job_id"] for row in report["observations"]] == ids
    assert report["scope"] == "trusted_invocation_snapshot"
    assert report["job_set_complete_as_of_read"] is True
    assert (
        report["submission_closed"] is report["whole_turn_accounting_complete"] is False
    )
    assert len(service.provider.calls) == 3
    assert (
        sum(row["telemetry"]["provider_calls"] for row in report["observations"]) == 3
    )
    assert not any("output_text" in row for row in report["observations"])
    Draft202012Validator(operation_schemas()["execution_snapshot"][1]).validate(report)
    # A snapshot cannot close the scope: a later admitted call remains possible.
    fourth = service.submit("user:owner", "skill:builder_skill", "stage-3", request())
    observe(service, fourth["job_id"], consumer="skill:builder_skill")
    assert (
        len(
            service.execution_snapshot("user:owner", "skill:builder_skill", ids[0])[
                "observations"
            ]
        )
        == 4
    )
    assert len(report["observations"]) == 3


@pytest.mark.parametrize("scope", [None, "", "invalid", True, {}, []])
def test_unqualified_anchor_does_not_expand_to_all_consumer_history(service, scope):
    jobs = [
        service.submit("user:owner", "skill:chat", f"job-{i}", request())
        for i in range(2)
    ]
    for job in jobs:
        observe(service, job["job_id"])
    with service.store.db() as db:
        db.execute(
            "UPDATE attempts SET telemetry=? WHERE id=?",
            (json.dumps({"turn_scope": scope}), jobs[0]["job_id"]),
        )
    result = service.execution_snapshot("user:owner", "skill:chat", jobs[0]["job_id"])
    assert (
        result["scope"] == "single_unqualified_attempt" and result["turn_scope"] is None
    )
    assert [row["job_id"] for row in result["observations"]] == [jobs[0]["job_id"]]
    assert "provider_calls" not in result["observations"][0]["telemetry"]


def test_same_scope_cannot_cross_owner_consumer_or_another_turn(service):
    with service.store.db() as db:
        for identity, principal, consumer, scope in (
            ("anchor", "user:owner", "skill:builder_skill", "a" * 64),
            ("owned", "user:owner", "skill:builder_skill", "a" * 64),
            ("foreign-user", "user:other", "skill:builder_skill", "a" * 64),
            ("foreign-consumer", "user:owner", "skill:chat", "a" * 64),
            ("foreign-turn", "user:owner", "skill:builder_skill", "b" * 64),
        ):
            db.execute(
                "INSERT INTO attempts(id,principal,consumer,request_id,digest,binding,status,created,telemetry) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    principal,
                    consumer,
                    identity,
                    "0" * 64,
                    "binding",
                    "failed",
                    0,
                    json.dumps({"turn_scope": scope}),
                ),
            )
    report = service.execution_snapshot("user:owner", "skill:builder_skill", "anchor")
    assert {row["job_id"] for row in report["observations"]} == {"anchor", "owned"}
    with pytest.raises(PermissionError):
        service.execution_snapshot("user:other", "skill:builder_skill", "anchor")
    with pytest.raises(KeyError):
        service.execution_snapshot("user:owner", "skill:chat", "anchor")


def test_snapshot_never_copies_private_prose_or_invents_unknown_usage(service):
    job = service.submit("user:owner", "skill:chat", "one", request())
    observe(service, job["job_id"])
    with service.store.db() as db:
        db.execute(
            "UPDATE attempts SET output=?,error=?,usage=?,telemetry=? WHERE id=?",
            (
                "PRIVATE output",
                "PRIVATE error",
                json.dumps(
                    {
                        "input_tokens": 4,
                        "output_tokens": True,
                        "input_tokens_details": {
                            "cached_tokens": 2,
                            "secret": "PRIVATE",
                        },
                    }
                ),
                json.dumps(
                    {
                        "turn_scope": "a" * 64,
                        "provider_calls": True,
                        "reserved_provider_calls": 4,
                        "instructions": "PRIVATE",
                        "tool_calls": [{"result": "PRIVATE"}],
                        "wire": {
                            "model": "selected",
                            "body_sha256": "b" * 64,
                            "body_digest_basis": "serialized_utf8",
                            "tool_types": ["function"],
                            "messages": "PRIVATE",
                            "credentials": "PRIVATE",
                        },
                    }
                ),
                job["job_id"],
            ),
        )
    result = service.execution_snapshot("user:owner", "skill:chat", job["job_id"])
    row = result["observations"][0]
    assert row["usage"] == {"input_tokens": 4, "cached_input_tokens": 2}
    assert "provider_calls" not in row["telemetry"]
    assert row["telemetry"]["wire"]["body_sha256"] == "b" * 64
    assert "PRIVATE" not in json.dumps(result)
    assert len(service.provider.calls) == 1


def test_oversized_cohort_refuses_instead_of_claiming_a_truncated_set(service):
    with service.store.db() as db:
        for index in range(17):
            db.execute(
                "INSERT INTO attempts(id,principal,consumer,request_id,digest,binding,status,created,telemetry) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    f"j-{index}",
                    "user:owner",
                    "skill:builder_skill",
                    f"r-{index}",
                    "0" * 64,
                    "binding",
                    "interrupted",
                    0,
                    json.dumps({"turn_scope": "a" * 64}),
                ),
            )
    with pytest.raises(ValueError, match="snapshot_capacity_exceeded"):
        service.execution_snapshot("user:owner", "skill:builder_skill", "j-0")


def test_sdk_snapshot_keeps_existing_owner_capability_and_consumer_fences(
    service, tmp_path, monkeypatch
):
    from adaos.sdk import ai
    from adaos.services.ai import bridge
    from adaos.domain.personalization_access import SubjectRef
    from adaos.services.policy.caller import verified_caller
    import adaos.sdk.core._ctx as sdk_ctx

    monkeypatch.delenv("ADAOS_AI_BRIDGE_URL", raising=False)
    ctx = native_ctx(tmp_path, "builder_skill")
    monkeypatch.setattr(sdk_ctx, "require_ctx", lambda *a: ctx)
    monkeypatch.setattr(bridge, "ai_service", lambda ctx: service)
    from adaos.services.policy import invocation

    monkeypatch.setattr(
        invocation, "current_invocation", lambda: {"request_id": "sdk-turn"}
    )
    job = service.submit("user:owner", "skill:builder_skill", "one", request())
    observe(service, job["job_id"], consumer="skill:builder_skill")
    with pytest.raises(PermissionError):
        ai.execution_snapshot(job["job_id"])
    with verified_caller(SubjectRef("user", "other")):
        with pytest.raises(PermissionError):
            ai.execution_snapshot(job["job_id"])
    with verified_caller(SubjectRef("user", "owner")):
        assert ai.execution_snapshot(job["job_id"])["anchor_job_id"] == job["job_id"]
        with pytest.raises(ValueError):
            bridge.dispatch(
                service,
                "user:owner",
                "skill:builder_skill",
                "execution_snapshot",
                {"job_id": job["job_id"], "consumer": "skill:chat"},
            )
        (tmp_path / "skill.yaml").write_text("capabilities: []\n", encoding="utf-8")
        with pytest.raises(PermissionError):
            ai.execution_snapshot(job["job_id"])


def test_new_snapshot_contract_preserves_all_published_legacy_contract_digests():
    expected = {
        "1.0.0": (
            "8c4a088aa78c0310937179ea49a5c08d550195f80332b048bdbf3a0243000a14",
            "9c6b2933018c66bcaeb1b051f0eccdc94386a9073e4a0b41c8e7c419b3f53aa7",
        ),
        "1.1.0": (
            "e44e2ddff033efbdc5ec3ac6d2fe62d61b4b7fbf1c11f4b5c976f32e23a185f4",
            "a621c449b5732462237423e5fb1620bea122419c7ee7e0584c9b403ed4a8cb3b",
        ),
        "1.2.0": (
            "6602133729d9c0add6ca6dc1ca32fe61288281e44d1a88721d0375970ed4aeb5",
            "f016e0d596b601e05f606d988c47188db8423e71012bba2fc76b44bdbe7b029d",
        ),
    }
    for version, (contract, binding) in expected.items():
        assert inference_contract(version).digest == "sha256:" + contract
        assert chatgpt_binding(version).digest == "sha256:" + binding
        assert "execution_snapshot" not in operation_schemas(version)
    assert (
        inference_contract().version
        == chatgpt_binding().to_dict()["version"]
        == "1.3.0"
    )


def test_anchor_and_cohort_share_one_read_snapshot_while_a_writer_progresses(
    service, monkeypatch
):
    from adaos.services.ai import execution_snapshot as projection

    with service.store.db() as db:
        db.execute(
            "INSERT INTO attempts(id,principal,consumer,request_id,digest,binding,status,created,telemetry) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "anchor",
                "user:owner",
                "skill:builder_skill",
                "one",
                "0" * 64,
                "binding",
                "completed",
                0,
                json.dumps({"turn_scope": "a" * 64}),
            ),
        )
    original = projection.execution_scope_snapshot
    intercepted = []

    def concurrent_read(db, **arguments):
        class SnapshotDB:
            def execute(self, sql, parameters):
                cursor = db.execute(sql, parameters)
                if "WHERE id=?" not in sql:
                    return cursor
                row = cursor.fetchone()
                with service.store.db() as writer:
                    writer.execute(
                        "INSERT INTO attempts(id,principal,consumer,request_id,digest,binding,status,created,telemetry) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            "later",
                            "user:owner",
                            "skill:builder_skill",
                            "two",
                            "0" * 64,
                            "binding",
                            "queued",
                            1,
                            json.dumps({"turn_scope": "a" * 64}),
                        ),
                    )
                intercepted.append(True)

                class Anchor:
                    def fetchone(self):
                        return row

                return Anchor()

        result = original(SnapshotDB(), **arguments)
        assert db.total_changes == 0
        return result

    monkeypatch.setattr(projection, "execution_scope_snapshot", concurrent_read)
    first = service.execution_snapshot("user:owner", "skill:builder_skill", "anchor")
    assert intercepted == [True]
    assert [row["job_id"] for row in first["observations"]] == ["anchor"]
    monkeypatch.setattr(projection, "execution_scope_snapshot", original)
    second = service.execution_snapshot("user:owner", "skill:builder_skill", "anchor")
    assert {row["job_id"] for row in second["observations"]} == {"anchor", "later"}


def test_service_bridge_snapshot_cannot_expand_its_executor_scope(
    service, tmp_path, monkeypatch
):
    from adaos.services.ai import bridge
    from contextlib import nullcontext
    import adaos.services.applications.runtime_selection as selection

    ctx = native_ctx(tmp_path, "context_chat_service")
    monkeypatch.setattr(bridge, "ai_service", lambda ctx: service)
    monkeypatch.setattr(selection, "application_execution", lambda *a: nullcontext())
    owned = service.submit("user:owner", "skill:context_chat_service", "own", request())
    observe(service, owned["job_id"], consumer="skill:context_chat_service")
    foreign = service.submit("user:owner", "skill:chat", "foreign", request())
    observe(service, foreign["job_id"])
    env = bridge.bridge_environment(ctx, "context_chat_service", tmp_path)
    token = env["ADAOS_AI_BRIDGE_TOKEN"]
    try:
        result = bridge.invoke_service(
            token, "127.0.0.1", "execution_snapshot", {"job_id": owned["job_id"]}
        )
        assert result["consumer"] == "skill:context_chat_service"
        with pytest.raises(KeyError):
            bridge.invoke_service(
                token, "127.0.0.1", "execution_snapshot", {"job_id": foreign["job_id"]}
            )
        with pytest.raises(PermissionError):
            bridge.invoke_service(
                token, "192.0.2.1", "execution_snapshot", {"job_id": owned["job_id"]}
            )
    finally:
        bridge.revoke_bridge("context_chat_service")
