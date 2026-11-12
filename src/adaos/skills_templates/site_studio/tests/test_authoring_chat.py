"""The model facade is doubled; retained proposals, compilation, commit and undo are real."""
from contextlib import closing
import importlib
import json
import sqlite3
import sys
import types

import pytest


@pytest.fixture
def authoring(provider, monkeypatch):
    provider.query_records()
    main = provider.test_main
    monkeypatch.setitem(sys.modules, main.__name__, main)
    module = importlib.import_module("site_studio_test_handlers.authoring_chat")
    facade = types.ModuleType("adaos.sdk.llm.content")
    facade.SubmissionNotAdmitted = type("SubmissionNotAdmitted", (ValueError,), {})
    llm = types.ModuleType("adaos.sdk.llm")
    llm.content = facade
    monkeypatch.setitem(sys.modules, "adaos.sdk.llm", llm)
    monkeypatch.setitem(sys.modules, "adaos.sdk.llm.content", facade)
    calls = []
    responses = []
    def generate(**request):
        with sqlite3.connect(provider.test_root / provider.DATABASE, timeout=0) as db:
            db.execute("BEGIN IMMEDIATE")
        calls.append(request)
        return responses[min(len(calls) - 1, len(responses) - 1)]
    facade.generate = generate
    facade.get = lambda _: responses[-1]
    return provider, main, module, calls, responses


def proposal(main, field="body", value="Изменено через чат"):
    source = main.query_preview()["source"]
    section = next(section for route in source["routes"] for section in route["sections"] if section["id"] == "hero")
    return {"status": "completed", "data": {"action": "propose", "reply": "Предложение готово к просмотру.",
        "reads": [], "operations": [{"kind": "set_resource_field", "target": f"resources.{section['resource_ref']}.data.{field}",
            "value_json": json.dumps(value), "before": None, "after": None}],
        "proposed_description": None, "proposal_reason": "Изменить только выбранный текст"}}


def test_customer_chat_candidate_apply_reopen_and_undo(authoring):
    provider, main, module, calls, responses = authoring
    before = main.query_preview()
    responses.append(proposal(main))
    result = main.discuss("Замени текст главного экрана", subject="block", block_id="hero")
    assert result["ok"] and result["change_set_id"]
    assert main.query_preview()["source_digest"] == before["source_digest"]
    assert calls[0]["data"]["selection"]["resource"]
    assert "Each response has EXACTLY ONE action" in calls[0]["purpose"]
    assert calls[0]["prompt"] == "Замени текст главного экрана"
    assert calls[0]["model"] == module.AUTHORING_MODEL
    assert calls[0]["reasoning"] == {"effort": "low"}
    candidate = main.query_preview(candidate_change_set_id=result["change_set_id"])
    assert "Изменено через чат" in json.dumps(candidate, ensure_ascii=False)
    assert candidate["source"]["revision"]["id"].startswith("proposal-")
    committed = main.commit_site_change(change_set_id=result["change_set_id"], expected_source_digest=before["source_digest"])
    assert committed["ok"]
    after = main.query_preview()
    assert after["source_digest"] != before["source_digest"]
    messages = main.query_discussion()["messages"]
    response = next(message for message in messages if message["id"].endswith("-response"))
    assert "Применено в черновик" in response["text"]
    assert response["actions"][0]["label"] == "Отменить правку"
    assert main.undo_site_change(change_set_id=result["change_set_id"], expected_source_digest=after["source_digest"])["ok"]
    assert main.query_preview()["source"]["resources"] == before["source"]["resources"]
    assert provider.test_reopen().query_records("sections")["items"]


def test_bad_operation_returns_compiler_feedback_to_a_bounded_repair(authoring):
    _, main, _, calls, responses = authoring
    responses.extend([proposal(main, "script", "alert(1)"), proposal(main)])
    result = main.discuss("Обнови текст", subject="block", block_id="hero")
    assert result["ok"]
    assert len(calls) == 2 and "forbidden" in calls[1]["data"]["validation_feedback"]
    assert calls[0]["request_id"] != calls[1]["request_id"]


def test_mixed_reply_and_operations_gets_actionable_repair_feedback(authoring):
    _, main, _, calls, responses = authoring
    invalid = proposal(main)
    invalid["data"]["action"] = "reply"
    responses.extend([invalid, proposal(main)])
    assert main.discuss("Обнови текст")["ok"]
    assert "action=propose" in calls[1]["data"]["validation_feedback"]


def test_transport_failures_stop_after_bounded_checks_without_draft_writes(authoring):
    _, main, module, _, _ = authoring
    from adaos.sdk.llm import content
    before = main.query_preview()["source_digest"]
    def unavailable(**_):
        raise TimeoutError("private transport data must not appear in user messages")
    content.generate = unavailable
    first = main.discuss("Обнови текст")
    assert first["status"] == "pending"
    assert main.resume_discussion(first["job_id"])["status"] == "pending"
    assert main.resume_discussion(first["job_id"])["status"] == "failed"
    assert main.query_preview()["source_digest"] == before
    assert "private transport" not in json.dumps(main.query_discussion())


def test_content_edit_cannot_silently_rewrite_project_brief(authoring):
    _, main, _, calls, responses = authoring
    invalid = proposal(main)
    invalid["data"]["proposed_description"] = {key: "unrequested change" for key in ["goal", "audience", "description", "constraints", "open_questions"]}
    responses.extend([invalid, proposal(main)])
    assert main.discuss("Измени только текст")["ok"]
    assert "proposed_description must be null" in calls[1]["data"]["validation_feedback"]


def test_retry_of_a_completed_user_request_does_not_generate_or_commit_again(authoring):
    _, main, _, calls, responses = authoring
    responses.append(proposal(main))
    first = main.discuss("Измени текст")
    second = main.discuss("Измени текст")
    assert first["job_id"] == second["job_id"] and len(calls) == 1
    assert main.query_preview()["source_digest"] == calls[0]["data"]["source_digest"]


def test_changed_source_rejects_model_output_and_does_not_overwrite_the_user(authoring):
    provider, main, module, calls, responses = authoring
    responses.append({"status": "queued"})
    pending = main.discuss("Измени текст")
    record = next(item for item in main.query_sections()["items"] if item["id"] == "hero")
    assert main.mutate_records("sections", "update", id="hero", expected_revision=record["revision"], payload={"body": "Ручная правка"})["ok"]
    responses[:] = [proposal(main)]
    finished = main.resume_discussion(pending["job_id"])
    assert finished["status"] == "failed"
    assert "Ручная правка" in json.dumps(main.query_preview(), ensure_ascii=False)


def test_source_reference_is_read_through_project_authority_and_stop_prevents_effects(authoring):
    provider, main, module, calls, responses = authoring
    from adaos.sdk.site_studio import save_source_file
    with closing(provider._database()) as db, db:
        file = save_source_file(db, project_id="adaos-site", title="facts.md", content="Факт из исходника".encode())
    refs = main.query_chat_references()["items"]
    selected = next(item for item in refs if item["id"] == file["id"])
    responses.append({"status": "queued"})
    pending = main.discuss("Используй @[facts.md]", references=[selected])
    assert calls[0]["data"]["materials"][0]["digest"] == file["digest"]
    stopped = main.cancel_authoring(pending["job_id"])
    assert stopped["status"] == "cancelled" and stopped["ok"] is True
    assert main.resume_discussion(pending["job_id"])["status"] == "cancelled"
    assert len(calls) == 1
    provider.test_policy["request_id"] = "second-request"
    with pytest.raises(ValueError, match="material_reference_conflict"):
        main.discuss("Read another site", references=[{**selected, "uri": selected["uri"].replace("adaos-site", "foreign-site")}])


def test_source_upload_accepts_core_attachment_format_and_rejects_foreign_links(provider, monkeypatch):
    monkeypatch.setattr(sys.modules["adaos.sdk.data"], "blob", types.SimpleNamespace(), raising=False)
    sources = importlib.import_module("site_studio_test_handlers.source_files")
    sha = "a" * 64
    ref = f"/api/tools/{sources.SKILL}/read_source_attachment/attachments/source-files/{sha}/prototype.html?webspace_id=dev2"
    with closing(provider._database()) as db, db:
        sources._ensure(db)
        metadata = {"digest": "sha256:" + sha, "filename": "prototype.html", "media_type": "text/html", "size_bytes": 12}
        db.execute("INSERT INTO source_uploads VALUES(?,?)", (metadata["digest"], json.dumps(metadata)))
        assert sources._uploaded(db, ref) == metadata
        for forbidden in ["https://foreign.example" + ref, ref.replace(sources.SKILL, "another_skill"),
                          ref.replace("prototype.html", "%2e%2e%2fprivate"), ref.replace("source-files", "attachments")]:
            with pytest.raises(ValueError):
                sources._uploaded(db, forbidden)
