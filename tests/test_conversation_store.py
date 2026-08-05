from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from adaos.services.agent_context import get_ctx
from adaos.services import conversation_context, conversation_store


def test_conversation_store_claims_transport_ingress_without_automatic_replay() -> None:
    key = f"transport:test:{uuid4().hex}"
    first = conversation_store.claim_transport_ingress(
        idempotency_key=key,
        transport="telegram",
        event_id="event-1",
        payload={"text": "Добавь раздел избранного"},
        meta={"policy": "no_automatic_retry"},
    )
    duplicate = conversation_store.claim_transport_ingress(
        idempotency_key=key,
        transport="telegram",
        event_id="event-1",
        payload={"text": "Добавь раздел избранного"},
    )
    conflict = conversation_store.claim_transport_ingress(
        idempotency_key=key,
        transport="telegram",
        event_id="event-2",
        payload={"text": "Удалить проект"},
    )
    dispatched = conversation_store.mark_transport_ingress_dispatched(key)

    assert first["claimed"] is True
    assert first["durable"] is True
    assert duplicate["claimed"] is False
    assert duplicate["duplicate"] is True
    assert conflict["claimed"] is False
    assert conflict["conflict"] is True
    assert dispatched and dispatched["status"] == "dispatched"


def test_conversation_store_appends_messages_with_monotonic_seq() -> None:
    conversation_store.ensure_schema()
    conversation_store.upsert_conversation(
        conversation_id="conv.test",
        webspace_id="desktop",
        owner="core:test",
    )

    first = conversation_store.append_message(
        conversation_id="conv.test",
        webspace_id="desktop",
        channel_id="general",
        owner="core:test",
        role="user",
        text="hello",
        payload={"id": "msg.1", "from": "user", "text": "hello"},
    )
    second = conversation_store.append_message(
        conversation_id="conv.test",
        webspace_id="desktop",
        channel_id="general",
        owner="core:test",
        role="hub",
        text="hi",
        payload={"id": "msg.2", "from": "hub", "text": "hi"},
        actor_id="agent:core:general",
        actor_label="Ада",
    )

    assert first and first["seq"] == 1
    assert first["retention_class"] == "normal"
    assert first["redaction_state"] == "active"
    assert second and second["seq"] == 2
    projection = conversation_store.list_projection("conv.test", limit=1)
    assert projection["messages"][0]["id"] == "msg.2"
    assert projection["messages"][0]["retention_class"] == "normal"
    assert projection["messages"][0]["redaction_state"] == "active"
    assert projection["has_more_before"] is True
    older = conversation_store.list_projection("conv.test", before_cursor=projection["before_cursor"], limit=1)
    assert [item["id"] for item in older["messages"]] == ["msg.1", "msg.2"]


def test_job_progress_updates_one_durable_message_without_reexecuting_the_job() -> None:
    suffix = uuid4().hex[:10]
    conversation_id = f"conv.job.{suffix}"
    job_id = f"builder-job-{suffix}"
    conversation_store.upsert_conversation(
        conversation_id=conversation_id,
        webspace_id="desktop",
        owner="skill:builder_skill",
    )
    accepted = conversation_store.upsert_job_message(
        job_id=job_id,
        phase="accepted",
        terminal=False,
        conversation_id=conversation_id,
        webspace_id="desktop",
        channel_id="builder",
        owner="skill:builder_skill",
        role="builder",
        text="Change accepted",
    )
    progress = conversation_store.upsert_job_message(
        job_id=job_id,
        phase="tests_running",
        terminal=False,
        conversation_id=conversation_id,
        webspace_id="desktop",
        channel_id="builder",
        owner="skill:builder_skill",
        role="builder",
        text="Tests are running",
    )
    terminal = conversation_store.upsert_job_message(
        job_id=job_id,
        phase="completed",
        terminal=True,
        conversation_id=conversation_id,
        webspace_id="desktop",
        channel_id="builder",
        owner="skill:builder_skill",
        role="builder",
        text="Change completed",
        payload={"result_ref": "builder-run:1"},
    )

    assert accepted["id"] == progress["id"] == terminal["id"]
    assert terminal["seq"] == accepted["seq"]
    assert terminal["job_phase"] == "completed"
    assert terminal["job_terminal"] is True
    assert terminal["result_ref"] == "builder-run:1"
    messages = conversation_store.list_messages(conversation_id, limit=20)
    assert len([item for item in messages if item.get("job_id") == job_id]) == 1


def test_conversation_store_merges_legacy_builder_conversation_and_tracks_change() -> None:
    suffix = uuid4().hex[:10]
    canonical_id = f"conv.builder.canonical.{suffix}"
    legacy_id = f"conv.builder.legacy.{suffix}"
    topic_id = f"prompt-project:scenario:{suffix}"
    conversation_store.ensure_schema()
    conversation_store.upsert_conversation(
        conversation_id=canonical_id,
        webspace_id="global",
        owner="skill:builder_skill",
    )
    conversation_store.upsert_conversation(
        conversation_id=legacy_id,
        webspace_id="desktop",
        owner="skill:builder_skill",
    )
    message = conversation_store.append_message(
        conversation_id=legacy_id,
        thread_id=topic_id,
        webspace_id="desktop",
        channel_id="builder",
        owner="skill:builder_skill",
        role="user",
        text="Preserve this Builder request",
        payload={"id": f"m.builder.legacy.{suffix}", "from": "user"},
    )

    merged = conversation_store.merge_conversations(
        source_conversation_id=legacy_id,
        target_conversation_id=canonical_id,
    )
    change = conversation_store.upsert_development_change(
        change_id=f"builder_change_{suffix}",
        conversation_id=canonical_id,
        thread_id=topic_id,
        topic_id=topic_id,
        status="pushed",
        source_message_ids=[str(message["id"])],
        artifact_refs=[{"kind": "scenario", "id": suffix}],
        revision_refs=[{"revision": "003"}],
        commit_refs=[{"commit": "abc123"}],
        summary="Preserve this Builder request",
    )

    projection = conversation_store.list_projection(canonical_id, thread_id=topic_id, limit=10)
    assert merged["messages_moved"] == 1
    assert [item["text"] for item in projection["messages"]] == ["Preserve this Builder request"]
    assert change and change["source_message_ids"] == [message["id"]]
    assert conversation_store.list_development_changes(
        conversation_id=canonical_id,
        artifact_kind="scenario",
        artifact_id=suffix,
    )[0]["commit_refs"] == [{"commit": "abc123"}]


def test_conversation_store_links_runs_to_one_canonical_change() -> None:
    suffix = uuid4().hex[:10]
    conversation_id = f"conv.builder.runs.{suffix}"
    change_id = f"CH-{suffix}"
    run_id = f"RUN-{suffix}"
    topic_id = f"prompt-project:scenario:{suffix}"
    conversation_store.upsert_development_change(
        change_id=change_id,
        conversation_id=conversation_id,
        thread_id=topic_id,
        topic_id=topic_id,
        status="active",
        artifact_refs=[{"kind": "scenario", "id": suffix}],
        summary="Add recipe search",
    )

    queued = conversation_store.upsert_development_run(
        run_id=run_id,
        change_id=change_id,
        conversation_id=conversation_id,
        thread_id=topic_id,
        topic_id=topic_id,
        activity="prototype.generate",
        executor="builder.llm",
        status="queued",
        context_packet_digest=f"sha256:{'a' * 64}",
        input_refs=["message:1"],
        started_at="2026-07-29T12:00:00+00:00",
    )
    completed = conversation_store.upsert_development_run(
        run_id=run_id,
        change_id=change_id,
        conversation_id=conversation_id,
        activity="prototype.generate",
        executor="builder.llm",
        status="succeeded",
        output_refs=["prototype:001"],
        evidence_refs=["evaluation:layout"],
        completed_at="2026-07-29T12:01:00+00:00",
    )

    assert queued and queued["schema"] == "adaos.builder.run.v1"
    assert completed and completed["change_id"] == change_id
    assert completed["run_id"] == run_id
    assert completed["status"] == "succeeded"
    assert completed["input_refs"] == ["message:1"]
    assert completed["output_refs"] == ["prototype:001"]
    assert completed["evidence_refs"] == ["evaluation:layout"]
    assert conversation_store.get_development_run(run_id) == completed
    assert conversation_store.list_development_runs(change_id=change_id) == [completed]
    assert len(conversation_store.list_development_changes(topic_id=topic_id)) == 1
    run_schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "src"
            / "adaos"
            / "abi"
            / "builder.run.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator(run_schema).validate(completed)

    with pytest.raises(ValueError, match="terminal Builder Run"):
        conversation_store.upsert_development_run(
            run_id=run_id,
            change_id=change_id,
            conversation_id=conversation_id,
            activity="prototype.generate",
            executor="builder.llm",
            status="running",
        )


def test_conversation_store_keeps_agent_registry_and_memory() -> None:
    conversation_store.ensure_schema()
    conversation_store.seed_agents(
        [
            {
                "id": "agent:test:nika",
                "label": "Ника",
                "owner": "skill:test",
                "channel_id": "conversational",
                "kind": "skill_agent",
                "aliases": ["Ника", "Nika"],
                "gender": "female",
                "voice": "ru-female",
                "icon": "female-outline",
            }
        ],
        source="test",
    )

    agents = conversation_store.list_agents(channel_id="conversational")
    assert agents[0]["id"] == "agent:test:nika"
    assert "Nika" in agents[0]["aliases"]

    memory_id = conversation_store.remember(
        scope="agent_user",
        owner="skill:test",
        subject_id="agent:test:nika",
        key="style",
        text="prefers concise critique",
        consent_state="granted",
        policy={"visibility": "owner"},
    )
    items = conversation_store.list_memory(scope="agent_user", owner="skill:test", subject_id="agent:test:nika")
    assert memory_id
    assert items[0]["text"] == "prefers concise critique"
    assert items[0]["consent_state"] == "granted"
    assert items[0]["retention_class"] == "normal"
    assert items[0]["redaction_state"] == "active"


def test_conversation_store_search_indexes_messages_and_memory() -> None:
    conversation_store.ensure_schema()
    conversation_store.upsert_conversation(
        conversation_id="conv.search",
        webspace_id="desktop",
        owner="skill:test",
    )
    conversation_store.append_message(
        conversation_id="conv.search",
        webspace_id="desktop",
        channel_id="builder",
        owner="skill:test",
        role="user",
        text="alpha vector marker for Builder retrieval",
        payload={"id": "search.msg.1", "from": "user", "text": "alpha vector marker for Builder retrieval"},
    )
    memory_id = conversation_store.remember(
        scope="skill_user",
        owner="skill:test",
        subject_id="skill:test",
        key="retrieval_style",
        text="prefers compact retrieval evidence",
        consent_state="skill_scoped",
    )

    health = conversation_store.search_index_health()
    assert health["schema"] == "adaos.conversation.search_index_health.v1"
    assert "fts_available" in health

    messages = conversation_store.search_messages("vector marker", conversation_id="conv.search")
    assert [item["id"] for item in messages] == ["search.msg.1"]
    assert messages[0]["search"]["backend"] in {"fts", "like"}

    memory = conversation_store.search_memory("compact retrieval", scope="skill_user", owner="skill:test")
    assert memory_id in {item["id"] for item in memory}
    assert {item["search"]["backend"] for item in memory}.issubset({"fts", "like"})

    rebuilt = conversation_store.rebuild_search_indexes()
    assert rebuilt["schema"] == "adaos.conversation.search_index_rebuild.v1"
    assert rebuilt["status"] in {"rebuilt", "fts_unavailable"}


def test_conversation_store_recovers_empty_or_stale_projection_from_ledger() -> None:
    suffix = uuid4().hex[:8]
    conversation_id = f"conv.projection.recovery.{suffix}"
    conversation_store.ensure_schema()
    conversation_store.upsert_conversation(
        conversation_id=conversation_id,
        webspace_id="desktop",
        owner="skill:test",
    )
    for index in range(1, 4):
        conversation_store.append_message(
            conversation_id=conversation_id,
            webspace_id="desktop",
            channel_id="general",
            owner="skill:test",
            role="user",
            text=f"recoverable turn {index}",
            payload={"id": f"projection.recovery.{suffix}.{index}", "from": "user", "text": f"recoverable turn {index}"},
        )

    empty = conversation_store.recover_projection_from_store(
        {"messages": []},
        conversation_id=conversation_id,
        limit=2,
    )
    stale = conversation_store.recover_projection_from_store(
        {
            "conversation_id": conversation_id,
            "messages": [{"id": "old", "text": "old", "seq": 1}],
            "total_message_count": 1,
        },
        conversation_id=conversation_id,
        limit=2,
    )
    current_store_projection = conversation_store.list_projection(conversation_id, limit=2)
    fresh = conversation_store.recover_projection_from_store(
        current_store_projection,
        conversation_id=conversation_id,
        limit=2,
    )

    assert empty["recovery"]["recovered"] is True
    assert empty["recovery"]["reason"] == "empty_projection"
    assert [item["text"] for item in empty["messages"]] == ["recoverable turn 2", "recoverable turn 3"]
    assert stale["recovery"]["recovered"] is True
    assert stale["recovery"]["reason"] == "stale_total"
    assert stale["total_message_count"] == 3
    assert fresh["recovery"]["recovered"] is False
    assert fresh["recovery"]["source"] == "current_projection"
    assert fresh["messages"][0]["text"] == "recoverable turn 2"


def test_conversation_store_rebuilds_redaction_aware_segments() -> None:
    conversation_store.ensure_schema()
    conversation_store.upsert_conversation(
        conversation_id="conv.segments",
        webspace_id="desktop",
        owner="skill:test",
    )
    for index in range(1, 6):
        conversation_store.append_message(
            conversation_id="conv.segments",
            thread_id="thread.alpha",
            webspace_id="desktop",
            channel_id="builder",
            owner="skill:test",
            role="user",
            text=f"topic alpha detail {index}",
            payload={"id": f"segments.msg.{index}", "from": "user", "text": f"topic alpha detail {index}"},
            redaction_state="redacted" if index == 3 else "active",
        )

    result = conversation_store.rebuild_conversation_segments(
        "conv.segments",
        thread_id="thread.alpha",
        segment_size=2,
    )

    assert result["ok"] is True
    assert result["message_count"] == 4
    assert result["segment_count"] == 2
    health = conversation_store.segment_summary_health("conv.segments", thread_id="thread.alpha")
    assert health["status"] == "ok"
    assert health["summarized_message_count"] == 4
    segments = conversation_store.list_conversation_segments("conv.segments", thread_id="thread.alpha")
    assert [item["message_count"] for item in segments] == [2, 2]
    assert all(ref["message_id"] != "segments.msg.3" for item in segments for ref in item["source_refs"])

    found = conversation_store.search_conversation_segments("detail 5", conversation_id="conv.segments", thread_id="thread.alpha")
    assert found and found[0]["search"]["backend"] in {"fts", "like"}

    conversation_store.append_message(
        conversation_id="conv.segments",
        thread_id="thread.alpha",
        webspace_id="desktop",
        channel_id="builder",
        owner="skill:test",
        role="user",
        text="new unsummarized topic",
        payload={"id": "segments.msg.6", "from": "user", "text": "new unsummarized topic"},
    )
    assert conversation_store.segment_summary_health("conv.segments", thread_id="thread.alpha")["status"] == "stale"


def test_conversation_store_reports_node_local_retrieval_health() -> None:
    suffix = uuid4().hex[:8]
    conversation_id = f"conv.retrieval.health.{suffix}"
    thread_id = f"thread.health.{suffix}"
    conversation_store.ensure_schema()
    conversation_store.upsert_conversation(
        conversation_id=conversation_id,
        webspace_id="desktop",
        owner="skill:test",
    )
    for index in range(1, 4):
        conversation_store.append_message(
            conversation_id=conversation_id,
            thread_id=thread_id,
            webspace_id="desktop",
            channel_id="builder",
            owner="skill:test",
            role="user",
            text=f"retrieval health detail {index}",
            payload={"id": f"retrieval.health.{suffix}.msg.{index}", "from": "user", "text": f"retrieval health detail {index}"},
        )
    conversation_store.remember(
        scope="skill_user",
        owner="skill:test",
        subject_id="skill:test",
        key="health_style",
        text="show retrieval diagnostics compactly",
        consent_state="skill_scoped",
    )
    conversation_store.rebuild_conversation_segments(
        conversation_id,
        thread_id=thread_id,
        segment_size=2,
    )
    conversation_store.rebuild_search_indexes()

    report = conversation_store.retrieval_health_report(conversation_id, thread_id=thread_id)

    assert report["schema"] == "adaos.conversation.retrieval_health.v1"
    assert report["conversation_id"] == conversation_id
    assert report["thread_id"] == thread_id
    assert report["counts"]["messages"] == 3
    assert report["counts"]["segments"] == 2
    assert report["counts"]["memory"] >= 1
    assert report["segment_summary"]["status"] == "ok"
    assert report["search_index"]["schema"] == "adaos.conversation.search_index_health.v1"
    assert "search_index_stale" not in report["degraded_reasons"]

    conversation_store.append_message(
        conversation_id=conversation_id,
        thread_id=thread_id,
        webspace_id="desktop",
        channel_id="builder",
        owner="skill:test",
        role="user",
        text="retrieval health unsummarized detail",
        payload={"id": f"retrieval.health.{suffix}.msg.4", "from": "user", "text": "retrieval health unsummarized detail"},
    )
    stale = conversation_store.retrieval_health_report(conversation_id, thread_id=thread_id)
    assert stale["status"] == "degraded"
    assert "segment_summary_stale" in stale["degraded_reasons"]


def test_conversation_store_processes_segment_summary_jobs() -> None:
    suffix = uuid4().hex[:8]
    conversation_id = f"conv.segment.jobs.{suffix}"
    thread_id = f"thread.jobs.{suffix}"
    conversation_store.ensure_schema()
    conversation_store.upsert_conversation(
        conversation_id=conversation_id,
        webspace_id="desktop",
        owner="skill:test",
    )
    for index in range(1, 6):
        conversation_store.append_message(
            conversation_id=conversation_id,
            thread_id=thread_id,
            webspace_id="desktop",
            channel_id="builder",
            owner="skill:test",
            role="user",
            text=f"queued summary detail {index}",
            payload={"id": f"segment.jobs.{suffix}.{index}", "from": "user", "text": f"queued summary detail {index}"},
        )

    queued = conversation_store.enqueue_segment_summary_job(
        conversation_id,
        thread_id=thread_id,
        segment_size=2,
    )
    duplicate = conversation_store.enqueue_segment_summary_job(
        conversation_id,
        thread_id=thread_id,
        segment_size=2,
    )
    processed = conversation_store.process_segment_summary_jobs(limit=2)

    assert queued["status"] == "queued"
    assert duplicate["status"] == "existing"
    assert duplicate["job"]["job_id"] == queued["job"]["job_id"]
    assert processed["status"] == "processed"
    assert processed["completed"] == 1
    assert processed["jobs"][0]["status"] == "completed"
    assert processed["jobs"][0]["result"]["segment_count"] == 3
    assert conversation_store.segment_summary_health(conversation_id, thread_id=thread_id)["status"] == "ok"
    job_health = conversation_store.segment_summary_job_health(conversation_id=conversation_id, thread_id=thread_id)
    assert job_health["status"] == "ok"
    assert job_health["counts"]["completed"] == 1
    retrieval_health = conversation_store.retrieval_health_report(conversation_id, thread_id=thread_id)
    assert retrieval_health["segment_summary_jobs"]["status"] == "ok"


def test_conversation_store_reports_failed_segment_summary_jobs() -> None:
    suffix = uuid4().hex[:8]
    conversation_id = f"conv.segment.jobs.fail.{suffix}"
    thread_id = f"thread.jobs.fail.{suffix}"
    conversation_store.ensure_schema()
    conversation_store.upsert_conversation(
        conversation_id=conversation_id,
        webspace_id="desktop",
        owner="skill:test",
    )
    queued = conversation_store.enqueue_segment_summary_job(
        conversation_id,
        thread_id=thread_id,
        max_attempts=1,
    )

    processed = conversation_store.process_segment_summary_jobs(
        limit=1,
        processor=lambda _job: {"ok": False, "status": "model_unavailable", "error": "summarizer offline"},
    )

    assert queued["status"] == "queued"
    assert processed["failed"] == 1
    assert processed["jobs"][0]["status"] == "failed"
    assert processed["jobs"][0]["last_error"] == "summarizer offline"
    job_health = conversation_store.segment_summary_job_health(conversation_id=conversation_id, thread_id=thread_id)
    assert job_health["status"] == "failed"
    assert job_health["latest_error"] == "summarizer offline"
    retrieval_health = conversation_store.retrieval_health_report(conversation_id, thread_id=thread_id)
    assert "segment_summary_job_failed" in retrieval_health["degraded_reasons"]


def test_conversation_store_long_conversation_soak_keeps_retrieval_and_projection_bounded() -> None:
    suffix = uuid4().hex[:8]
    conversation_id = f"conv.soak.{suffix}"
    thread_id = f"thread.soak.{suffix}"
    started = time.perf_counter()
    conversation_store.ensure_schema()
    conversation_store.upsert_conversation(
        conversation_id=conversation_id,
        webspace_id="desktop",
        owner="skill:test",
    )
    for index in range(1, 241):
        marker = f"soakmarker{index}"
        conversation_store.append_message(
            conversation_id=conversation_id,
            thread_id=thread_id,
            webspace_id="desktop",
            channel_id="builder",
            owner="skill:test",
            role="user" if index % 2 else "hub",
            text=(
                f"{marker} long builder dialog turn {index}. "
                "The user iterates on a draft, preview evidence, memory, and diagnostics."
            ),
            payload={
                "id": f"soak.{suffix}.msg.{index}",
                "from": "user" if index % 2 else "hub",
                "text": f"{marker} long builder dialog turn {index}",
            },
        )

    rebuilt = conversation_store.rebuild_search_indexes()
    queued = conversation_store.enqueue_segment_summary_job(
        conversation_id,
        thread_id=thread_id,
        segment_size=30,
    )
    processed = conversation_store.process_segment_summary_jobs(limit=4)
    projection = conversation_store.list_projection(
        conversation_id,
        thread_id=thread_id,
        limit=24,
        max_items=48,
    )
    recovered = conversation_store.recover_projection_from_store(
        {
            "conversation_id": conversation_id,
            "thread_id": thread_id,
            "messages": [{"id": "stale", "text": "stale", "seq": 10}],
            "total_message_count": 10,
        },
        conversation_id=conversation_id,
        thread_id=thread_id,
        limit=24,
        max_items=48,
    )
    packet = conversation_context.build_context_packet(
        conversation_id=conversation_id,
        requester_owner="skill:test",
        channel_id="builder",
        thread_id=thread_id,
        budgets={"max_messages": 24, "max_segments": 4, "max_memory_items": 0, "max_tokens": 4000},
    )
    found_messages = conversation_store.search_messages(
        "soakmarker173",
        conversation_id=conversation_id,
        thread_id=thread_id,
        limit=5,
    )
    found_segments = conversation_store.search_conversation_segments(
        "soakmarker240",
        conversation_id=conversation_id,
        thread_id=thread_id,
        limit=5,
    )
    health = conversation_store.retrieval_health_report(conversation_id, thread_id=thread_id)
    elapsed = time.perf_counter() - started

    assert rebuilt["status"] in {"rebuilt", "fts_unavailable"}
    assert queued["status"] == "queued"
    assert processed["status"] == "processed"
    assert processed["completed"] == 1
    assert processed["jobs"][0]["result"]["segment_count"] == 8
    assert projection["total_message_count"] == 240
    assert len(projection["messages"]) == 24
    assert projection["has_more_before"] is True
    assert recovered["recovery"]["recovered"] is True
    assert recovered["recovery"]["reason"] == "stale_total"
    assert recovered["total_message_count"] == 240
    assert found_messages and found_messages[0]["id"] == f"soak.{suffix}.msg.173"
    assert found_segments and found_segments[0]["search"]["backend"] in {"fts", "like"}
    assert health["status"] == "ok"
    assert health["counts"]["messages"] == 240
    assert health["segment_summary"]["segment_count"] == 8
    assert packet["token_estimate"] <= packet["budgets"]["max_tokens"]
    assert packet["diagnostics"]["selected_message_count"] <= 24
    assert packet["diagnostics"]["selected_segment_count"] <= 4
    assert elapsed < 20.0


def test_conversation_store_compacts_long_history_with_range_refs() -> None:
    suffix = uuid4().hex[:8]
    conversation_id = f"conv.compaction.{suffix}"
    thread_id = f"thread.compaction.{suffix}"
    conversation_store.ensure_schema()
    conversation_store.upsert_conversation(
        conversation_id=conversation_id,
        webspace_id="desktop",
        owner="skill:test",
    )
    for index in range(1, 11):
        conversation_store.append_message(
            conversation_id=conversation_id,
            thread_id=thread_id,
            webspace_id="desktop",
            channel_id="conversational",
            owner="skill:test",
            role="user" if index % 2 else "hub",
            text=f"compaction detail {index}",
            payload={"id": f"compaction.{suffix}.msg.{index}", "from": "user", "text": f"compaction detail {index}"},
        )

    result = conversation_store.compact_conversation_history(
        conversation_id,
        thread_id=thread_id,
        keep_last_messages=4,
        segment_size=3,
    )

    assert result["schema"] == "adaos.conversation.summary_compaction.v1"
    assert result["status"] == "compacted"
    assert result["message_count"] == 10
    assert result["compacted_message_count"] == 6
    assert result["raw_tail_count"] == 4
    assert result["tail_start_seq"] == 7
    assert [item["seq"] for item in result["raw_tail_refs"]] == [7, 8, 9, 10]
    assert [(item["start_seq"], item["end_seq"]) for item in result["summary_refs"]] == [(1, 3), (4, 6)]
    covered = {
        ref["seq"]
        for segment in result["summary_refs"]
        for ref in segment["source_refs"]
    } | {ref["seq"] for ref in result["raw_tail_refs"]}
    assert covered == set(range(1, 11))
    segments = conversation_store.list_conversation_segments(conversation_id, thread_id=thread_id)
    assert segments
    assert conversation_store.search_conversation_segments("detail 6", conversation_id=conversation_id, thread_id=thread_id)


def test_conversation_store_records_retention_and_redaction_metadata() -> None:
    conversation_store.ensure_schema()
    conversation_store.upsert_conversation(
        conversation_id="conv.retention",
        webspace_id="desktop",
        owner="core:test",
    )

    stored = conversation_store.append_message(
        conversation_id="conv.retention",
        webspace_id="desktop",
        channel_id="general",
        owner="core:test",
        role="user",
        text="temporary detail",
        payload={"id": "retention.msg.1", "from": "user", "text": "temporary detail"},
        retention_class="ephemeral",
        retention_until=1234.5,
        redaction_state="redacted",
        redacted_at=1235.0,
        redaction_reason="user_request",
    )

    assert stored is not None
    assert stored["retention_class"] == "ephemeral"
    assert stored["retention_until"] == 1234.5
    assert stored["redaction_state"] == "redacted"
    assert stored["redacted_at"] == 1235.0
    assert stored["redaction_reason"] == "user_request"
    projected = conversation_store.list_projection("conv.retention")["messages"][0]
    assert projected["retention_class"] == "ephemeral"
    assert projected["redaction_state"] == "redacted"

    memory_id = conversation_store.remember(
        scope="global_user",
        owner="core",
        key="preference",
        text="prefers short answers",
        retention_class="profile",
        retention_until=9999.0,
        redaction_state="active",
    )
    assert memory_id
    memory = conversation_store.list_memory(scope="global_user", owner="core")[0]
    assert memory["retention_class"] == "profile"
    assert memory["retention_until"] == 9999.0
    assert memory["redaction_state"] == "active"


def test_conversation_store_migrates_retention_and_redaction_columns() -> None:
    sql = get_ctx().sql
    conversation_store._ENSURED_SQL_IDS.discard(id(sql))
    with sql.connect() as con:
        con.execute(
            """
            CREATE TABLE conversation_conversations (
                conversation_id TEXT PRIMARY KEY,
                webspace_id TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'conversation',
                owner TEXT NOT NULL,
                title TEXT,
                active_agent_id TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                initiator_json TEXT NOT NULL DEFAULT '{}',
                policy_json TEXT NOT NULL DEFAULT '{}',
                meta_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        con.execute(
            """
            CREATE TABLE conversation_messages (
                message_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                webspace_id TEXT NOT NULL,
                channel_id TEXT,
                owner TEXT,
                actor_id TEXT,
                actor_label TEXT,
                actor_icon TEXT,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                route_id TEXT,
                ts REAL NOT NULL,
                request_id TEXT,
                turn_trace_id TEXT,
                idempotency_key TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}',
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                UNIQUE (conversation_id, seq),
                UNIQUE (conversation_id, idempotency_key)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE conversation_memory_items (
                memory_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                owner TEXT NOT NULL,
                subject_id TEXT,
                key TEXT,
                text TEXT,
                value_json TEXT NOT NULL DEFAULT '{}',
                confidence REAL,
                consent_state TEXT NOT NULL DEFAULT 'unknown',
                policy_json TEXT NOT NULL DEFAULT '{}',
                source_ref_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE conversation_turn_traces (
                turn_trace_id TEXT PRIMARY KEY,
                conversation_id TEXT,
                message_id TEXT,
                webspace_id TEXT NOT NULL,
                channel_id TEXT,
                agent_id TEXT,
                selected_tool TEXT,
                policy_decision_json TEXT NOT NULL DEFAULT '{}',
                renderer_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'started',
                summary TEXT,
                created_at REAL NOT NULL,
                completed_at REAL
            )
            """
        )
        con.commit()

    assert conversation_store.ensure_schema()
    with sql.connect() as con:
        for table in (
            "conversation_conversations",
            "conversation_messages",
            "conversation_memory_items",
            "conversation_turn_traces",
        ):
            columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
            assert {
                "retention_class",
                "retention_until",
                "redaction_state",
                "redacted_at",
                "redaction_reason",
            }.issubset(columns)
        audit_columns = {row[1] for row in con.execute("PRAGMA table_info(conversation_audit_events)")}
        assert {"audit_event_id", "event_type", "action", "conversation_id", "counts_json"}.issubset(
            audit_columns
        )


def test_conversation_store_exports_and_redacts_conversation_bundle() -> None:
    conversation_store.ensure_schema()
    conversation_store.upsert_conversation(
        conversation_id="conv.export",
        webspace_id="desktop",
        owner="skill:test",
    )
    conversation_store.append_message(
        conversation_id="conv.export",
        webspace_id="desktop",
        channel_id="general",
        owner="skill:test",
        role="user",
        text="private detail",
        payload={"id": "export.msg.1", "from": "user", "text": "private detail"},
    )
    memory_id = conversation_store.remember(
        scope="conversation",
        owner="skill:test",
        subject_id="conv.export",
        key="fact",
        text="private memory",
        consent_state="session",
    )
    trace_id = conversation_store.start_turn_trace(
        webspace_id="desktop",
        conversation_id="conv.export",
        channel_id="general",
        selected_tool="skill:test.tool",
        policy_decision={"reason": "test"},
    )
    assert memory_id and trace_id
    assert conversation_store.finish_turn_trace(trace_id, status="completed")

    exported = conversation_store.export_conversation("conv.export")
    assert exported["schema"] == "adaos.conversation.export.v1"
    assert exported["counts"] == {"messages": 1, "memory": 1, "turn_traces": 1}
    assert exported["conversation"]["conversation_id"] == "conv.export"
    assert exported["audit_event_id"]

    result = conversation_store.redact_conversation("conv.export", reason="test_redaction")
    assert result["ok"] is True
    assert result["counts"] == {"conversation": 1, "messages": 1, "memory": 1, "turn_traces": 1}
    assert result["audit_event_id"]
    audit = conversation_store.list_audit_events(conversation_id="conv.export", ascending=True)
    assert [item["action"] for item in audit[:2]] == ["export_conversation", "redact_conversation"]
    assert audit[1]["reason"] == "test_redaction"
    assert audit[1]["counts"] == {"conversation": 1, "messages": 1, "memory": 1, "turn_traces": 1}

    visible = conversation_store.export_conversation("conv.export")
    assert visible["conversation"] is None
    assert visible["messages"] == []
    assert visible["memory"] == []
    assert visible["turn_traces"] == []

    redacted = conversation_store.export_conversation("conv.export", include_redacted=True)
    assert redacted["conversation"]["redaction_state"] == "redacted"
    assert redacted["messages"][0]["redaction_reason"] == "test_redaction"
    assert redacted["memory"][0]["redaction_reason"] == "test_redaction"
    assert redacted["turn_traces"][0]["redaction_reason"] == "test_redaction"


def test_conversation_store_hard_deletes_conversation_bundle() -> None:
    conversation_store.ensure_schema()
    conversation_store.upsert_conversation(
        conversation_id="conv.delete",
        webspace_id="desktop",
        owner="skill:test",
    )
    conversation_store.append_message(
        conversation_id="conv.delete",
        webspace_id="desktop",
        channel_id="general",
        owner="skill:test",
        role="user",
        text="remove me",
        payload={"id": "delete.msg.1", "from": "user", "text": "remove me"},
    )
    result = conversation_store.redact_conversation("conv.delete", hard_delete=True)

    assert result["ok"] is True
    assert result["counts"]["conversation"] == 1
    assert result["audit_event_id"]
    assert conversation_store.export_conversation("conv.delete", include_redacted=True)["conversation"] is None
    assert conversation_store.export_conversation("conv.delete", include_redacted=True)["messages"] == []
    audit = conversation_store.list_audit_events(conversation_id="conv.delete", action="hard_delete_conversation")
    assert audit and audit[0]["counts"]["conversation"] == 1


def test_conversation_store_redacts_only_selected_messages() -> None:
    conversation_store.ensure_schema()
    conversation_store.upsert_conversation(
        conversation_id="conv.message-redaction",
        webspace_id="desktop",
        owner="skill:test",
    )
    for message_id, text in (("keep.msg.1", "keep me"), ("redact.msg.1", "redact me")):
        conversation_store.append_message(
            conversation_id="conv.message-redaction",
            webspace_id="desktop",
            channel_id="general",
            owner="skill:test",
            role="user",
            text=text,
            payload={"id": message_id, "from": "user", "text": text},
        )

    result = conversation_store.redact_messages(
        ["redact.msg.1", "missing.msg.1"],
        reason="test_message_redaction",
    )

    assert result["ok"] is True
    assert result["counts"] == {"messages": 1, "requested": 2, "found": 1}
    assert [
        message["id"] for message in conversation_store.export_conversation("conv.message-redaction")["messages"]
    ] == ["keep.msg.1"]
    exported = conversation_store.export_conversation("conv.message-redaction", include_redacted=True)
    assert [message["id"] for message in exported["messages"]] == ["keep.msg.1", "redact.msg.1"]
    assert exported["messages"][1]["redaction_reason"] == "test_message_redaction"
    audit = conversation_store.list_audit_events(
        conversation_id="conv.message-redaction",
        action="redact_messages",
    )
    assert audit and audit[0]["counts"] == {"messages": 1, "requested": 2, "found": 1}


def test_conversation_store_persists_active_dialog_channel() -> None:
    conversation_store.ensure_schema()
    conversation_store.upsert_dialog_channel(
        webspace_id="desktop",
        channel_id="conversational",
        label="Conversational",
        owner="skill:conversation_companions",
        conversation_id="conv.skill.conversation_companions.default.desktop",
        active_agent_id="agent:conversation_companions:arseni",
        default_skill="conversation_companions",
        default_tool="talk",
        route_id="voice_chat",
    )

    assert conversation_store.set_active_dialog_channel(
        webspace_id="desktop",
        channel_id="conversational",
        conversation_id="conv.skill.conversation_companions.default.desktop",
        active_agent_id="agent:conversation_companions:arseni",
        meta={"event": "agent_addressed"},
    )

    active = conversation_store.get_active_dialog_channel("desktop")
    assert active is not None
    assert active["channel_id"] == "conversational"
    assert active["conversation_id"] == "conv.skill.conversation_companions.default.desktop"
    assert active["active_agent_id"] == "agent:conversation_companions:arseni"
    assert active["meta"]["event"] == "agent_addressed"


def test_conversation_store_returns_latest_dialog_channel_from_messages() -> None:
    conversation_store.ensure_schema()
    conversation_store.upsert_conversation(
        conversation_id="conv.latest",
        webspace_id="desktop",
        owner="skill:test",
    )
    conversation_store.append_message(
        conversation_id="conv.latest",
        webspace_id="desktop",
        channel_id="conversational",
        owner="skill:test",
        role="hub",
        text="reply",
        payload={"id": "latest.1", "from": "hub", "text": "reply"},
        actor_id="agent:test:one",
        actor_label="One",
        actor_icon="person-circle-outline",
        route_id="voice_chat",
        ts=10.0,
    )

    latest = conversation_store.latest_dialog_channel_for_webspace("desktop")
    assert latest is not None
    assert latest["channel_id"] == "conversational"
    assert latest["conversation_id"] == "conv.latest"
    assert latest["active_agent_id"] == "agent:test:one"
    assert latest["active_agent_label"] == "One"
