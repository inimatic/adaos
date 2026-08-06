from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator
import pytest
import yaml

from adaos.services.builder.automation import BuilderAutomationService
from adaos.services.builder.workspace import BuilderWorkspaceService
from adaos.services.root.service import _rewrite_skill_template_identity
from adaos.services.skill_factory_worker import CodexRunResult, LocalSkillFactoryWorker


def _service(tmp_path: Path) -> BuilderAutomationService:
    repo_root = Path(__file__).resolve().parents[1]
    dev_skills = tmp_path / "dev" / "skills"
    dev_scenarios = tmp_path / "dev" / "scenarios"
    scenario = dev_scenarios / "recipes"
    scenario.mkdir(parents=True)
    dev_skills.mkdir(parents=True)
    (scenario / "scenario.yaml").write_text(
        yaml.safe_dump({"id": "recipes", "version": "0.1.0", "depends": []}, sort_keys=False),
        encoding="utf-8",
    )
    (scenario / "webui.json").write_text(json.dumps({"schema": "adaos.webui.v1"}), encoding="utf-8")

    class _DeveloperService:
        def create_skill(self, name: str, template: str | None = None):
            source = repo_root / "src" / "adaos" / "skills_templates" / str(template or "skill_default")
            target = dev_skills / name
            shutil.copytree(source, target)
            _rewrite_skill_template_identity(target, name)
            return SimpleNamespace(path=target, name=name)

        def create_scenario(self, name: str, template: str | None = None):
            source = repo_root / "src" / "adaos" / "scenario_templates" / str(template or "scenario_default")
            target = dev_scenarios / name
            shutil.copytree(source, target)
            return SimpleNamespace(path=target, name=name)

    workspace_service = BuilderWorkspaceService(
        state_dir=tmp_path / "state",
        repo_root=tmp_path,
        workspace_root=tmp_path / "workspace",
        skills_root=tmp_path / "workspace" / "skills",
        scenarios_root=tmp_path / "workspace" / "scenarios",
        dev_skills_root=dev_skills,
        dev_scenarios_root=dev_scenarios,
        developer_service=_DeveloperService(),
    )

    def fake_codex(*, workspace: Path, prompt: str, output_dir: Path) -> CodexRunResult:  # noqa: ARG001
        handler = workspace / "skills" / "recipes_skill" / "handlers" / "main.py"
        handler.write_text(handler.read_text(encoding="utf-8") + "\n# automation iteration\n", encoding="utf-8")
        return CodexRunResult(returncode=0, final_message="Automation iteration completed.")

    def worker_factory() -> LocalSkillFactoryWorker:
        return LocalSkillFactoryWorker(
            state_dir=tmp_path / "state",
            repo_root=repo_root,
            dev_skills_root=dev_skills,
            dev_scenarios_root=dev_scenarios,
            runs_root=tmp_path / "runs",
            executor=fake_codex,
        )

    return BuilderAutomationService(
        state_dir=tmp_path / "state",
        repo_root=repo_root,
        dev_skills_root=dev_skills,
        dev_scenarios_root=dev_scenarios,
        runs_root=tmp_path / "runs",
        worker_factory=worker_factory,
        workspace_service=workspace_service,
        background=False,
        materialize_on_completion=False,
    )


def test_execute_starts_local_automation_and_persists_session(tmp_path: Path) -> None:
    service = _service(tmp_path)

    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search and detail actions.",
        webspace_id="prompt-dev",
        conversation_id="conv.builder.recipes",
    )

    assert started["ok"] is True
    status = service.status(object_type="scenario", object_id="recipes")
    assert status["session"]["status"] == "completed"
    assert status["session"]["source_prototype_version"] == "0.1.0"
    assert status["automation"]["source_prototype_version"] == "0.1.0"
    assert status["session"]["standard_prompt_version"].startswith("adaos-skill-realization/")
    assert status["session"]["created_artifacts"][0]["kind"] == "skill"
    assert status["session"]["created_artifacts"][0]["name"] == "recipes_skill"
    task = next(
        item
        for item in service.factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == status["session"]["current_task_id"]
    )
    assert task["forge"]["base_revision"].startswith("sha256:")
    assert task["forge"]["base_revision"] == task["forge"]["source_snapshot"]["digest"]
    assert (service.dev_skills_root / "recipes_skill" / "skill.yaml").exists()
    assert "new_skill" not in (service.dev_skills_root / "recipes_skill" / "handlers" / "main.py").read_text(
        encoding="utf-8"
    )
    assert status["session"]["local_run"]["events_path"].endswith("codex-live.jsonl")


def test_automation_worker_executes_its_submitted_task_not_an_older_queue_item(tmp_path: Path) -> None:
    service = _service(tmp_path)
    older = service.factory.submit_realize_request(
        {
            "request_id": "realize.test.older-builder-task",
            "target": {"type": "scenario", "id": "older_scenario"},
        }
    )["task"]

    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement the approved recipe prototype.",
        webspace_id="prompt-dev",
        conversation_id="conv.builder.recipes",
    )

    status = service.status(object_type="scenario", object_id="recipes")
    assert status["session"]["status"] == "completed"
    submitted_task_id = status["session"]["current_task_id"]
    tasks = {
        item["task_id"]: item
        for item in service.factory.snapshot(include_tasks=True)["tasks"]
    }
    assert tasks[older["task_id"]]["status"] == "queued"
    assert tasks[submitted_task_id]["status"] == "completed"


def test_automation_carries_active_change_set_into_isolated_codex_request(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._workflow().transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CS-recipes-store-sync",
            "request": "Synchronize shopping items with the store API.",
            "issues": [
                {
                    "issue_id": "store-sync",
                    "title": "Implement transactional store synchronization",
                    "lane": "automation",
                    "acceptance_criteria": [
                        "A failed remote request leaves the local shopping list unchanged."
                    ],
                }
            ],
        },
    )

    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement the approved store synchronization change set.",
        webspace_id="prompt-dev",
        conversation_id="conv.builder.recipes",
    )

    assert started["session"]["change_set_id"] == "CS-recipes-store-sync"
    assert started["automation"]["change_set_id"] == "CS-recipes-store-sync"
    task = next(
        item
        for item in service.factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == started["session"]["current_task_id"]
    )
    request = task["realize_request"]
    assert request["links"]["change_set_id"] == "CS-recipes-store-sync"
    assert request["links"]["canonical_change_id"] == "CS-recipes-store-sync"
    assert request["links"]["context_packet_digest"].startswith("sha256:")
    assert request["artifacts"]["change_set"]["issues"][0]["issue_id"] == "store-sync"
    packet = request["artifacts"]["context_packet"]
    assert packet["schema"] == "adaos.builder.context_packet.v1"
    assert packet["digest"] == request["links"]["context_packet_digest"]
    assert packet["change"]["change_id"] == "CS-recipes-store-sync"
    assert started["session"]["canonical_change_id"] == "CS-recipes-store-sync"
    assert started["session"]["context_packet_digest"] == packet["digest"]
    serialized_packet = json.dumps(packet, ensure_ascii=False).lower()
    assert "raw_transcript" not in serialized_packet
    assert "secret" not in serialized_packet
    assert (
        "A failed remote request leaves the local shopping list unchanged."
        in request["acceptance"]["checks"]
    )
    workflow = service._workflow().describe("scenario", "recipes")
    assert started["session"]["change_id"] in workflow["change_set"]["member_change_ids"]
    automation_run = next(
        item
        for item in workflow["change"]["runs"]
        if item["run_id"] == started["session"]["current_task_id"]
    )
    assert automation_run["context_packet_digest"] == packet["digest"]
    assert automation_run["status"] == "running"
    assert automation_run["activity"] == "automation_started"


def test_automation_rejects_change_set_before_prototype_approval(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._workflow().transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CS-recipes-layout",
            "request": "Add a favorites section.",
            "issues": [
                {
                    "issue_id": "favorites-layout",
                    "title": "Add a favorites section",
                    "lane": "prototype",
                    "acceptance_criteria": ["Favorites is visible in the navigation."],
                }
            ],
        },
    )

    with pytest.raises(ValueError, match="Prototype approval gate"):
        service.start_from_execute(
            object_type="scenario",
            object_id="recipes",
            implementation_brief="Implement favorites.",
            webspace_id="prompt-dev",
        )


def test_scenario_automation_uses_declared_runtime_skill_as_companion(tmp_path: Path) -> None:
    service = _service(tmp_path)
    scenario = service.dev_scenarios_root / "recipes" / "scenario.yaml"
    scenario.write_text(
        yaml.safe_dump(
            {
                "id": "recipes",
                "version": "0.1.0",
                "depends": ["recipes_control_skill"],
                "runtime": {"skills": {"required": ["recipes_control_skill"]}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    companion = service._resolve_companion_skill_id("scenario", "recipes")

    assert companion == "recipes_control_skill"


def test_scenario_automation_retains_all_previous_automation_companions(tmp_path: Path) -> None:
    service = _service(tmp_path)
    snapshot = (
        service.state_dir
        / "builder"
        / "workflow_snapshots"
        / "scenario"
        / "recipes"
        / "automation"
    )
    snapshot.mkdir(parents=True)
    (snapshot / "scenario.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "recipes",
                "version": "0.2.0",
                "depends": ["recipes_skill", "recipes_control_skill"],
                "runtime": {
                    "skills": {
                        "required": ["recipes_skill", "recipes_control_skill"],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    companions = service._resolve_companion_skill_ids("scenario", "recipes")

    assert companions == ["recipes_skill", "recipes_control_skill"]


def test_scenario_automation_retains_published_companions_as_immutable_baseline(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    assert service.workspace_service is not None
    assert service.workspace_service.scenarios_root is not None
    publication = Path(service.workspace_service.scenarios_root) / "recipes"
    publication.mkdir(parents=True)
    (publication / "scenario.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "recipes",
                "version": "0.4.0",
                "depends": ["recipes_skill", "recipes_control_skill"],
                "runtime": {
                    "skills": {
                        "required": ["recipes_skill", "recipes_control_skill"],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (publication / "webui.json").write_text(
        json.dumps({"schema": "adaos.webui.v1", "ui": {"application": {}}}),
        encoding="utf-8",
    )

    companions = service._resolve_companion_skill_ids("scenario", "recipes")
    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Preserve the installed behavior while applying the approved prototype.",
        webspace_id="prompt-dev",
    )
    task = next(
        item
        for item in service.factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == started["session"]["current_task_id"]
    )

    assert companions == ["recipes_skill", "recipes_control_skill"]
    assert task["realize_request"]["artifacts"]["companion_skill_ids"] == companions
    attachment = next(
        item
        for item in task["forge"]["source_snapshot"]["attachments"]
        if item["name"] == "current_publication"
    )
    assert attachment["target_path"] == "scenarios/recipes/.builder_current_publication"
    task_prompt = (
        tmp_path
        / "runs"
        / started["session"]["current_task_id"]
        / "input"
        / "task.md"
    ).read_text(encoding="utf-8")
    assert "immutable currently installed functional edition" in task_prompt
    assert not (
        service.dev_scenarios_root / "recipes" / ".builder_current_publication"
    ).exists()


def test_scenario_automation_keeps_installed_only_skill_outside_mutable_envelope(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    assert service.workspace_service is not None
    assert service.workspace_service.skills_root is not None
    workspace_skill = Path(service.workspace_service.skills_root) / "voice_chat_skill"
    workspace_skill.mkdir(parents=True)
    (workspace_skill / "skill.yaml").write_text(
        yaml.safe_dump({"name": "voice_chat_skill", "version": "1.0.0"}, sort_keys=False),
        encoding="utf-8",
    )
    scenario = service.dev_scenarios_root / "recipes" / "scenario.yaml"
    scenario.write_text(
        yaml.safe_dump(
            {
                "id": "recipes",
                "version": "0.1.0",
                "depends": ["recipes_skill", "voice_chat_skill"],
                "runtime": {"skills": {"required": ["recipes_skill", "voice_chat_skill"]}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement the owned recipe behavior against installed chat APIs.",
        webspace_id="prompt-dev",
    )
    task = next(
        item
        for item in service.factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == started["session"]["current_task_id"]
    )

    assert started["session"]["companion_skill_ids"] == ["recipes_skill"]
    assert task["realize_request"]["artifacts"]["companion_skill_ids"] == ["recipes_skill"]
    assert "skills/voice_chat_skill/" not in task["forge"]["sparse_paths"]
    assert not (service.dev_skills_root / "voice_chat_skill").exists()


def test_followup_refreshes_companions_from_current_publication(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement the first functional recipe edition.",
        webspace_id="prompt-dev",
    )
    assert service.workspace_service is not None
    assert service.workspace_service.scenarios_root is not None
    publication = Path(service.workspace_service.scenarios_root) / "recipes"
    publication.mkdir(parents=True)
    (publication / "scenario.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "recipes",
                "version": "0.4.0",
                "depends": ["recipes_skill", "recipes_control_skill"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    assert service.workspace_service is not None
    service.workspace_service.create_draft(
        kind="skill",
        artifact_id="recipes_control_skill",
        source_idea="Existing published control dependency.",
        template_id="skill_default",
    )

    followed = service.submit_turn(
        text="Apply the next approved prototype without dropping published behavior.",
        object_type="scenario",
        object_id="recipes",
        webspace_id="prompt-dev",
    )
    task = next(
        item
        for item in service.factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == followed["session"]["current_task_id"]
    )

    assert followed["session"]["companion_skill_ids"] == [
        "recipes_skill",
        "recipes_control_skill",
    ]
    assert task["realize_request"]["artifacts"]["companion_skill_ids"] == [
        "recipes_skill",
        "recipes_control_skill",
    ]


@pytest.mark.parametrize("corrupted", ["???????? ??????", "Damaged \ufffd text"])
def test_automation_start_rejects_transport_corrupted_brief_before_writes(
    tmp_path: Path,
    corrupted: str,
) -> None:
    service = _service(tmp_path)

    with pytest.raises(ValueError, match="transport-corrupted"):
        service.start_from_execute(
            object_type="scenario",
            object_id="recipes",
            implementation_brief=corrupted,
            webspace_id="prompt-dev",
        )

    assert service.get_session("scenario", "recipes") is None


def test_automation_followup_rejects_transport_corrupted_text_before_iteration(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
    )

    with pytest.raises(ValueError, match="transport-corrupted"):
        service.submit_turn(
            text="???? broken follow-up",
            object_type="scenario",
            object_id="recipes",
            webspace_id="prompt-dev",
        )

    current = service.get_session("scenario", "recipes")
    assert current is not None
    assert current["iteration"] == 0
    assert current["change_id"] == started["session"]["change_id"]


def test_completed_automation_routes_chat_to_next_codex_iteration(tmp_path: Path) -> None:
    service = _service(tmp_path)
    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
    )

    turn = service.submit_turn(text="Add filtering by cooking time.", webspace_id="prompt-dev")

    assert turn["handled"] is True
    assert turn["status"] == "automation_queued"
    status = service.status(object_type="scenario", object_id="recipes")
    assert status["session"]["status"] == "completed"
    assert status["session"]["iteration"] == 1
    assert status["session"]["turns"][0]["text"] == "Add filtering by cooking time."
    assert len(status["session"]["task_history"]) == 2
    assert status["session"]["change_id"] != started["session"]["change_id"]
    assert status["session"]["change_history"] == [started["session"]["change_id"]]
    assert status["session"]["change_id"] in status["session"]["task"]["request_id"]
    workflow = service._workflow().describe("scenario", "recipes")
    assert workflow["automation"]["iteration"] == 2
    assert workflow["automation"]["status"] == "working"
    assert workflow["history"][-1]["action"] == "automation_iteration_started"


def test_duplicate_queued_start_relaunches_orphaned_worker(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    session = {
        "schema": "adaos.builder.automation_session.v1",
        "session_id": "automation.scenario.recipes",
        "object_type": "scenario",
        "object_id": "recipes",
        "companion_skill_id": "recipes_skill",
        "webspace_id": "prompt-dev",
        "status": "queued",
        "current_task_id": "task.queued",
    }
    service._save_session(session)
    service.factory = SimpleNamespace(snapshot=lambda **_kwargs: {"tasks": []})
    launched: list[str] = []
    monkeypatch.setattr(
        BuilderAutomationService,
        "_launch_worker",
        lambda self, session_id: launched.append(session_id),
    )

    result = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
        conversation_id="conv.builder.recipes",
    )

    assert result["duplicate"] is True
    assert result["worker_relaunched"] is True
    assert launched == ["automation.scenario.recipes"]
    assert result["session"]["conversation_id"] == "conv.builder.recipes"


def test_followup_backfills_conversation_before_terminal_notification(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
    )
    notified: list[str] = []

    def notify(self, session):
        notified.append(str(session.get("conversation_id") or ""))
        return dict(session)

    monkeypatch.setattr(BuilderAutomationService, "_notify_completed_session", notify)

    service.submit_turn(
        text="Add filtering by cooking time.",
        object_type="scenario",
        object_id="recipes",
        webspace_id="prompt-dev",
        conversation_id="conv.builder.recipes",
    )

    assert notified == ["conv.builder.recipes"]
    current = service.get_session("scenario", "recipes")
    assert current is not None
    assert current["conversation_id"] == "conv.builder.recipes"


def test_followup_turn_clears_stale_terminal_projection(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
    )
    previous = service.get_session("scenario", "recipes")
    assert previous is not None
    previous["completion_readiness"] = {"ok": True, "completed_at": "before"}
    previous["completion_notified_task_id"] = previous["current_task_id"]
    service._save_session(previous)

    turn = service.submit_turn(
        text="Add filtering by cooking time.",
        object_type="scenario",
        object_id="recipes",
        webspace_id="prompt-dev",
    )

    assert turn["automation"]["summary"] is None
    assert "completion_readiness" not in turn["session"]
    assert turn["session"]["completion_history"][0]["completed_at"] == "before"


def test_automation_projection_is_render_safe_and_abi_valid(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
    )

    result = service.projection(webspace_id="prompt-dev")

    assert result["ok"] is True
    projection = result["automation"]
    assert projection["status"] == "completed"
    assert projection["phase"] == "completed"
    assert projection["can_submit"] is True
    assert projection["project"] == {
        "type": "scenario",
        "id": "recipes",
        "companion_skill_id": "recipes_skill",
        "companion_skill_ids": ["recipes_skill"],
    }
    assert projection["result_branch"] == result["session"]["last_result"]["branch"]
    assert projection["steps"][-1]["state"] == "completed"

    schema_path = Path(__file__).resolve().parents[1] / "src" / "adaos" / "abi" / "builder.automation_projection.v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(projection)


def test_empty_projection_disables_automation_input() -> None:
    projection = BuilderAutomationService.empty_projection(webspace_id="prompt-dev")

    assert projection["status"] == "idle"
    assert projection["can_submit"] is False
    assert projection["project"] is None


def test_failed_projection_exposes_actionable_diagnostics_and_retry(tmp_path: Path) -> None:
    service = _service(tmp_path)
    session = {
        "session_id": "automation.scenario.builder",
        "object_type": "scenario",
        "object_id": "builder",
        "webspace_id": "dev1-dev",
        "status": "failed",
        "current_task_id": "task.1",
        "last_failure": {
            "message": "codex_executable_not_found",
            "failure_id": "failure.task.1.cli",
            "retryable": True,
            "stage": "in_progress",
        },
        "local_run": {
            "events_path": "run/codex-live.jsonl",
            "stderr_path": "run/codex-live.stderr.log",
            "result_path": "run/result.json",
        },
    }

    projection = service.project_session(session)

    assert projection["can_submit"] is True
    assert projection["error"] == "codex_executable_not_found"
    assert projection["failure_id"] == "failure.task.1.cli"
    assert projection["retryable"] is True
    assert projection["evidence"]["stderr_path"] == "run/codex-live.stderr.log"


def test_projection_event_is_not_reemitted_for_unchanged_status_reads(tmp_path: Path) -> None:
    service = _service(tmp_path)
    events: list[dict] = []
    service.event_sink = lambda payload: events.append(dict(payload))
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
    )
    event_count = len(events)
    statuses = [event["status"] for event in events]
    assert "workspace_preparing" in statuses
    assert "in_progress" in statuses
    assert "tests_running" in statuses
    assert statuses[-1] == "completed"

    service.status(object_type="scenario", object_id="recipes")
    service.status(object_type="scenario", object_id="recipes")

    assert len(events) == event_count


def test_completed_iteration_clears_stale_failure_from_session(tmp_path: Path) -> None:
    service = _service(tmp_path)
    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
    )
    session = dict(started["session"])
    session["last_failure"] = {"message": "previous attempt failed"}

    refreshed = service.refresh_session(session)

    assert refreshed["status"] == "completed"
    assert "last_failure" not in refreshed


def test_refresh_recovers_terminal_orphan_once_and_finalizes_without_rerunning_codex(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    service.materialize_on_completion = True
    task_id = "task.orphan"
    session = {
        "schema": "adaos.builder.automation_session.v1",
        "session_id": "automation.scenario.recipes",
        "object_type": "scenario",
        "object_id": "recipes",
        "companion_skill_id": "recipes_skill",
        "webspace_id": "prompt-dev",
        "current_task_id": task_id,
        "status": "in_progress",
    }
    service._save_session(session)
    output_dir = Path(service.runs_root) / task_id / "output"
    output_dir.mkdir(parents=True)
    (output_dir / "codex-live.jsonl").write_text(
        '{"type":"turn.completed"}\n',
        encoding="utf-8",
    )
    recovered: list[str] = []

    class _Worker:
        def recover_orphaned_codex_run(self, value: str) -> dict:
            recovered.append(value)
            return {"ok": True}

    service.worker_factory = _Worker

    def snapshot(**_kwargs):
        completed = bool(recovered)
        return {
            "tasks": [
                {
                    "task_id": task_id,
                    "status": "completed" if completed else "in_progress",
                    "updated_at": "2026-07-28T15:13:00+00:00",
                    "result": {"summary": "Recovered result."} if completed else None,
                    "progress": [],
                }
            ]
        }

    service.factory = SimpleNamespace(snapshot=snapshot)
    finalized: list[dict] = []

    def finalize(_service, value):
        finalized.append(dict(value))
        completed = dict(value)
        completed["status"] = "completed"
        completed["completion_readiness"] = {
            "ok": True,
            "task_id": task_id,
            "completed_at": "2026-07-28T15:14:00+00:00",
        }
        completed.pop("finalizing_task_id", None)
        _service._save_session(completed)

    monkeypatch.setattr(BuilderAutomationService, "_finalize_completed_session", finalize)

    refreshed = service.refresh_session(session)

    assert recovered == [task_id]
    assert finalized[0]["status"] == "commit_ready"
    assert finalized[0]["last_result"]["summary"] == "Recovered result."
    assert refreshed["status"] == "completed"
    assert refreshed["completion_readiness"]["ok"] is True


def test_projection_backfills_missing_conversation_before_notification(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
    )
    notified: list[str] = []

    def notify(self, session):
        notified.append(str(session.get("conversation_id") or ""))
        return dict(session)

    monkeypatch.setattr(BuilderAutomationService, "_notify_completed_session", notify)

    result = service.projection(
        object_type="scenario",
        object_id="recipes",
        webspace_id="prompt-dev",
        conversation_id="conv.builder.recipes",
    )

    assert result["ok"] is True
    assert notified == ["conv.builder.recipes"]
    assert service.get_session("scenario", "recipes")["conversation_id"] == "conv.builder.recipes"


def test_refresh_preserves_finalization_progress_after_worker_completion(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.factory = SimpleNamespace(
        snapshot=lambda **_kwargs: {
            "tasks": [
                {
                    "task_id": "task.1",
                    "status": "completed",
                    "updated_at": "2026-07-18T00:00:00+00:00",
                    "progress": [{"status": "commit_ready", "message": "worker commit"}],
                }
            ]
        }
    )

    refreshed = service.refresh_session(
        {
            "object_type": "scenario",
            "object_id": "recipes",
            "current_task_id": "task.1",
            "finalizing_task_id": "task.1",
            "status": "commit_ready",
            "progress": {"status": "commit_ready", "message": "Forge finalization"},
        }
    )

    assert refreshed["status"] == "commit_ready"
    assert refreshed["progress"]["message"] == "Forge finalization"


def test_refresh_preserves_terminal_orchestration_progress_after_worker_completion(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.factory = SimpleNamespace(
        snapshot=lambda **_kwargs: {
            "tasks": [
                {
                    "task_id": "task.1",
                    "status": "completed",
                    "updated_at": "2026-07-18T00:00:00+00:00",
                    "progress": [
                        {"status": "commit_ready", "message": "worker commit"}
                    ],
                }
            ]
        }
    )

    refreshed = service.refresh_session(
        {
            "object_type": "scenario",
            "object_id": "recipes",
            "current_task_id": "task.1",
            "status": "completed",
            "progress": {
                "task_id": "task.1",
                "status": "completed",
                "message": "Automation result activated and checkpointed",
                "updated_at": "2026-07-18T00:01:00+00:00",
            },
            "completion_readiness": {
                "ok": True,
                "task_id": "task.1",
                "completed_at": "2026-07-18T00:01:00+00:00",
                "vcs_checkpoints": [
                    {"ok": True, "kind": "scenario", "name": "recipes"}
                ],
            },
        }
    )

    assert refreshed["status"] == "completed"
    assert refreshed["progress"]["status"] == "completed"
    assert refreshed["progress"]["message"] == "Automation result activated and checkpointed"
    assert refreshed["updated_at"] == "2026-07-18T00:01:00+00:00"


def test_refresh_reconciles_legacy_false_positive_checkpoint_completion(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.factory = SimpleNamespace(
        snapshot=lambda **_kwargs: {
            "tasks": [
                {
                    "task_id": "task.1",
                    "status": "completed",
                    "updated_at": "2026-07-18T00:00:00+00:00",
                    "result": {"summary": "code complete"},
                    "progress": [],
                }
            ]
        }
    )

    refreshed = service.refresh_session(
        {
            "object_type": "scenario",
            "object_id": "recipes",
            "current_task_id": "task.1",
            "status": "completed",
            "completion_readiness": {
                "ok": True,
                "task_id": "task.1",
                "vcs_checkpoints": [
                    {"ok": False, "kind": "scenario", "name": "recipes", "error": "504"}
                ],
            },
        }
    )

    assert refreshed["status"] == "failed"
    assert refreshed["completion_readiness"]["ok"] is False
    assert refreshed["last_failure"]["stage"] == "forge_checkpoint"


def test_refresh_reconciles_completed_task_with_failed_live_readiness(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.factory = SimpleNamespace(
        snapshot=lambda **_kwargs: {
            "tasks": [
                {
                    "task_id": "task.1",
                    "status": "completed",
                    "updated_at": "2026-07-18T00:00:00+00:00",
                    "result": {"summary": "code complete"},
                    "progress": [],
                }
            ]
        }
    )

    refreshed = service.refresh_session(
        {
            "object_type": "scenario",
            "object_id": "recipes",
            "current_task_id": "task.1",
            "status": "completed",
            "completion_readiness": {
                "ok": False,
                "task_id": "task.1",
                "error": "ValueError: automation Preview is not available",
                "vcs_checkpoints": [{"ok": True, "kind": "scenario", "name": "recipes"}],
            },
        }
    )

    assert refreshed["status"] == "failed"
    assert refreshed["completion_readiness"]["ok"] is False
    assert refreshed["last_failure"]["stage"] == "live_readiness"
    assert "Preview is not available" in refreshed["last_failure"]["message"]


def test_completed_session_publishes_one_terminal_chat_message(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    published: list[dict] = []
    monkeypatch.setattr(
        "adaos.services.agent_context.get_ctx",
        lambda: SimpleNamespace(bus=object()),
    )
    monkeypatch.setattr(
        "adaos.services.conversation_response.materialize_response",
        lambda response, **kwargs: published.append({"response": response, **kwargs}) or {"ok": True},
    )
    session = {
        "schema": "adaos.builder.automation_session.v1",
        "session_id": "automation.scenario.recipes",
        "object_type": "scenario",
        "object_id": "recipes",
        "webspace_id": "desktop",
        "conversation_id": "conv.builder.recipes",
        "current_task_id": "task.1",
        "last_result": {"summary": "Implemented filters."},
    }

    first = service._notify_completed_session(session)
    second = service._notify_completed_session(first)

    assert len(published) == 1
    assert "Локальный Codex завершил работу" in published[0]["response"]["message"]
    assert published[0]["thread_id"] == "prompt-project:scenario:recipes"
    assert second["completion_notified_task_id"] == "task.1"


def test_finalize_prepares_materialized_runtime_then_notifies(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    service.materialize_on_completion = True
    calls: list[str] = []
    saved: list[dict] = []

    monkeypatch.setattr(
        BuilderAutomationService,
        "_checkpoint_completed_artifacts",
        lambda self, session: calls.append("checkpoint")
        or [
            {
                "ok": True,
                "kind": "scenario",
                "name": "recipes",
                "commit": "forge-1",
                "package_digest": "sha256:" + "1" * 64,
                "source_revision": "forge-1",
            }
        ],
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_prepare_and_activate_dev_skill",
        lambda self, skill_id, **kwargs: calls.append(f"activate:{skill_id}")
        or {"ok": True, "slot": "B"},
    )

    class FakeWorkbench:
        def __init__(self, **kwargs):  # noqa: ARG002
            pass

        async def ensure_dev_webspace(self, source_webspace_id, **kwargs):  # noqa: ARG002
            calls.append("ensure")
            return {
                "dev_webspace_id": "desktop-dev",
                "runtime": {"ok": True, "webspace_id": "desktop-dev"},
            }

    monkeypatch.setattr("adaos.services.builder.workbench.BuilderWorkbenchService", FakeWorkbench)
    monkeypatch.setattr(BuilderAutomationService, "_save_session", lambda self, value: saved.append(dict(value)))
    monkeypatch.setattr(
        BuilderAutomationService,
        "_notify_completed_session",
        lambda self, value: calls.append("notify") or dict(value),
    )

    service._finalize_completed_session(
        {
            "session_id": "automation.scenario.recipes",
            "object_type": "scenario",
            "object_id": "recipes",
            "companion_skill_id": "recipes_skill",
            "webspace_id": "desktop",
            "current_task_id": "task.1",
            "change_id": "change-1",
            "status": "completed",
        }
    )

    assert calls == ["checkpoint", "activate:recipes_skill", "ensure", "notify"]
    assert saved[-1]["completion_readiness"]["ok"] is True
    assert saved[-1]["completion_readiness"]["materialization"]["preview_webspace_id"] == "desktop-dev"
    assert saved[-1]["completion_readiness"]["task_id"] == "task.1"
    assert saved[-1]["completion_readiness"]["vcs_checkpoints"][0]["commit"] == "forge-1"
    assert (
        saved[-1]["completion_readiness"]["workflow_checkpoint"]["workflow"]["delivery"]["status"]
        == "checkpoint"
    )
    assert saved[-1]["status"] == "completed"
    assert saved[-1]["progress"]["status"] == "completed"
    assert saved[-1]["progress"]["task_id"] == "task.1"


@pytest.mark.parametrize(
    ("binding_updated_at", "expected_preview_calls", "expected_transition"),
    [
        ("2026-07-29T03:45:00+00:00", 1, "followed_completed_work"),
        ("2026-07-29T03:50:00+00:00", 0, "preserved_user_selection"),
    ],
)
def test_finalize_follows_completed_automation_only_when_preview_choice_is_unchanged(
    tmp_path: Path,
    monkeypatch,
    binding_updated_at: str,
    expected_preview_calls: int,
    expected_transition: str,
) -> None:
    service = _service(tmp_path)
    saved: list[dict] = []
    preview_calls: list[dict] = []
    public_target = {
        "schema": "adaos.builder.preview_target.v1",
        "object_type": "scenario",
        "object_id": "recipes",
        "stage": "publication",
        "revision": "0.1.0",
        "follow_active": False,
    }

    monkeypatch.setattr(
        BuilderAutomationService,
        "_checkpoint_completed_artifacts",
        lambda self, session: [
            {
                "ok": True,
                "kind": "scenario",
                "name": "recipes",
                "commit": "forge-1",
                "package_digest": "sha256:" + "1" * 64,
                "source_revision": "forge-1",
            }
        ],
    )

    class FakeWorkbench:
        def __init__(self, **kwargs):  # noqa: ARG002
            pass

        def get_workspace_binding(self, source_webspace_id):  # noqa: ARG002
            return {
                "preview_webspace_id": "desktop-dev",
                "updated_at": binding_updated_at,
                "preview_target": public_target,
            }

    class FakeWorkflow:
        def snapshot_current_automation(self, *args, **kwargs):  # noqa: ARG002
            return {"path": "automation/0.1.1"}

        def describe(self, *args, **kwargs):  # noqa: ARG002
            return {"active_phase": "automation"}

        def transition(self, *args, **kwargs):  # noqa: ARG002
            return {"delivery": {"status": "checkpoint"}}

    monkeypatch.setattr("adaos.services.builder.workbench.BuilderWorkbenchService", FakeWorkbench)
    monkeypatch.setattr(
        "adaos.sdk.builder.preview.select_target",
        lambda *args, **kwargs: preview_calls.append(dict(kwargs))
        or {"ok": True, "preview_webspace_id": "desktop-dev"},
    )
    monkeypatch.setattr(BuilderAutomationService, "_workflow", lambda self: FakeWorkflow())
    monkeypatch.setattr(BuilderAutomationService, "_save_session", lambda self, value: saved.append(dict(value)))
    monkeypatch.setattr(BuilderAutomationService, "_notify_completed_session", lambda self, value: dict(value))

    service._finalize_completed_session(
        {
            "session_id": "automation.scenario.recipes",
            "object_type": "scenario",
            "object_id": "recipes",
            "webspace_id": "desktop",
            "current_task_id": "task.1",
            "change_id": "change-1",
            "preview_binding_at_submit": {
                "captured": True,
                "updated_at": "2026-07-29T03:45:00+00:00",
                "target": public_target,
            },
        }
    )

    assert len(preview_calls) == expected_preview_calls
    if preview_calls:
        assert preview_calls[0]["stage"] == "automation"
        assert preview_calls[0]["follow_active"] is True
    assert saved[-1]["completion_readiness"]["preview_transition"]["status"] == expected_transition
    assert saved[-1]["status"] == "completed"


def test_finalize_records_live_readiness_failure_without_success_chat(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    saved: list[dict] = []
    notified: list[dict] = []
    monkeypatch.setattr(
        BuilderAutomationService,
        "_checkpoint_completed_artifacts",
        lambda self, session: [{"ok": True, "commit": "forge-1"}],
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_prepare_and_activate_dev_skill",
        lambda self, *args, **kwargs: (_ for _ in ()).throw(RuntimeError("activation failed")),
    )
    monkeypatch.setattr(BuilderAutomationService, "_save_session", lambda self, value: saved.append(dict(value)))
    monkeypatch.setattr(
        BuilderAutomationService,
        "_notify_completed_session",
        lambda self, value: notified.append(dict(value)) or dict(value),
    )

    service._finalize_completed_session(
        {
            "session_id": "automation.scenario.recipes",
            "object_type": "scenario",
            "object_id": "recipes",
            "companion_skill_id": "recipes_skill",
            "status": "completed",
        }
    )

    assert saved[-1]["status"] == "failed"
    assert saved[-1]["last_failure"]["stage"] == "live_readiness"
    assert saved[-1]["progress"]["status"] == "failed"
    assert notified == []


def test_finalize_compensates_failed_follow_active_preview_after_workflow_completion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    saved: list[dict] = []
    notified: list[dict] = []
    transitions: list[str] = []

    monkeypatch.setattr(
        BuilderAutomationService,
        "_checkpoint_completed_artifacts",
        lambda self, session: [{"ok": True, "commit": "forge-1"}],
    )

    class FakeWorkbench:
        def __init__(self, **kwargs):  # noqa: ARG002
            pass

        def get_workspace_binding(self, source_webspace_id):  # noqa: ARG002
            return {
                "preview_target": {
                    "stage": "prototype",
                    "revision": "UI 005",
                    "follow_active": True,
                }
            }

    class FakeWorkflow:
        def snapshot_current_automation(self, *args, **kwargs):  # noqa: ARG002
            return {"path": "automation/0.2.11"}

        def describe(self, object_type, object_id):  # noqa: ARG002
            return {"active_phase": "automation"}

        def transition(self, object_type, object_id, event, **kwargs):  # noqa: ARG002
            transitions.append(event)
            return {"active_phase": "automation"}

    monkeypatch.setattr("adaos.services.builder.workbench.BuilderWorkbenchService", FakeWorkbench)
    monkeypatch.setattr(
        "adaos.sdk.builder.preview.select_target",
        lambda *args, **kwargs: {  # noqa: ARG005
            "ok": False,
            "error": "webspace_rebuild_failed",
            "error_detail": "ValueError: invalid runtime projection",
        },
    )
    monkeypatch.setattr(BuilderAutomationService, "_workflow", lambda self: FakeWorkflow())
    monkeypatch.setattr(BuilderAutomationService, "_save_session", lambda self, value: saved.append(dict(value)))
    monkeypatch.setattr(
        BuilderAutomationService,
        "_notify_completed_session",
        lambda self, value: notified.append(dict(value)) or dict(value),
    )

    service._finalize_completed_session(
        {
            "session_id": "automation.scenario.recipes",
            "object_type": "scenario",
            "object_id": "recipes",
            "webspace_id": "desktop",
            "current_task_id": "task.1",
            "status": "completed",
        }
    )

    assert saved[-1]["status"] == "failed"
    assert saved[-1]["completion_readiness"]["ok"] is False
    assert saved[-1]["completion_readiness"]["materialization"]["error"] == "webspace_rebuild_failed"
    assert "invalid runtime projection" in saved[-1]["last_failure"]["message"]
    assert transitions == ["automation_completed", "automation_failed"]
    assert notified == []


def test_finalize_fails_when_forge_checkpoint_is_not_confirmed(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    saved: list[dict] = []
    activations: list[str] = []
    monkeypatch.setattr(
        BuilderAutomationService,
        "_checkpoint_completed_artifacts",
        lambda self, session: [
            {"ok": False, "kind": "scenario", "name": "recipes", "error": "504"}
        ],
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_prepare_and_activate_dev_skill",
        lambda self, skill_id, **kwargs: activations.append(skill_id) or {"ok": True},
    )
    monkeypatch.setattr(BuilderAutomationService, "_save_session", lambda self, value: saved.append(dict(value)))

    service._finalize_completed_session(
        {
            "object_type": "scenario",
            "object_id": "recipes",
            "companion_skill_id": "recipes_skill",
            "webspace_id": "prompt-dev",
            "current_task_id": "task.1",
            "iteration": 1,
        }
    )

    assert saved[-1]["status"] == "failed"
    assert saved[-1]["completion_readiness"]["ok"] is False
    assert "Forge checkpoint failed" in saved[-1]["completion_readiness"]["error"]
    assert saved[-1]["last_failure"]["stage"] == "forge_checkpoint"
    assert activations == []


def test_explicit_checkpoint_reconciliation_does_not_rerun_codex(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
    )
    failed = service.get_session("scenario", "recipes")
    assert failed is not None
    previous_change_id = failed["change_id"]
    failed["status"] = "failed"
    failed["completion_readiness"] = {
        "ok": False,
        "task_id": failed["current_task_id"],
        "vcs_checkpoints": [
            {"ok": False, "kind": "skill", "name": "recipes_skill", "error": "preflight"},
            {"ok": False, "kind": "scenario", "name": "recipes", "error": "preflight"},
        ],
    }
    failed["last_failure"] = {"stage": "forge_checkpoint", "message": "preflight"}
    service._save_session(failed)
    finalized: list[dict] = []

    def finalize(_service, session):
        finalized.append(dict(session))
        completed = dict(session)
        completed["status"] = "completed"
        _service._save_session(completed)

    monkeypatch.setattr(BuilderAutomationService, "_finalize_completed_session", finalize)
    monkeypatch.setattr(
        BuilderAutomationService,
        "_submit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Codex must not be submitted")),
    )

    result = service.reconcile_checkpoint(object_type="scenario", object_id="recipes")

    assert result["ok"] is True
    assert result["change_id"] != previous_change_id
    assert finalized[0]["status"] == "commit_ready"
    assert finalized[0]["current_task_id"] == failed["current_task_id"]
    assert finalized[0]["reconciliation_history"][-1]["previous_change_id"] == previous_change_id


def test_checkpoint_reconciliation_reuses_change_id_for_partially_committed_pair(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
    )
    failed = service.get_session("scenario", "recipes")
    assert failed is not None
    previous_change_id = failed["change_id"]
    failed["status"] = "failed"
    failed["completion_readiness"] = {
        "ok": False,
        "task_id": failed["current_task_id"],
        "vcs_checkpoints": [
            {"ok": True, "kind": "skill", "name": "recipes_skill", "commit": "abc"},
            {"ok": False, "kind": "scenario", "name": "recipes", "error": "timeout"},
        ],
    }
    failed["last_failure"] = {"stage": "forge_checkpoint", "message": "timeout"}
    service._save_session(failed)
    finalized: list[dict] = []

    def finalize(_service, session):
        finalized.append(dict(session))
        completed = dict(session)
        completed["status"] = "completed"
        _service._save_session(completed)

    monkeypatch.setattr(BuilderAutomationService, "_finalize_completed_session", finalize)
    monkeypatch.setattr(
        BuilderAutomationService,
        "_submit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Codex must not be submitted")),
    )

    result = service.reconcile_checkpoint(object_type="scenario", object_id="recipes")

    assert result["ok"] is True
    assert result["change_id"] == previous_change_id
    assert finalized[0]["reconciliation_history"][-1]["mode"] == "resume_partial"


def test_validated_result_recovery_reuses_completed_task_after_live_readiness_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    session = {
        "object_type": "scenario",
        "object_id": "recipes",
        "current_task_id": "task.1",
        "status": "failed",
        "task": {"task_id": "task.1", "status": "completed", "result": {"summary": "ready"}},
        "last_result": {"summary": "ready"},
        "last_failure": {"stage": "live_readiness", "message": "preview failed"},
        "completion_readiness": {
            "ok": False,
            "task_id": "task.1",
            "vcs_checkpoints": [{"ok": True, "kind": "scenario", "commit": "forge-1"}],
        },
    }
    service._save_session(session)
    finalized: list[dict] = []

    monkeypatch.setattr(BuilderAutomationService, "refresh_session", lambda self, value: dict(value))

    def finalize(_service, value):
        finalized.append(dict(value))
        completed = dict(value)
        completed["status"] = "completed"
        completed.pop("reuse_confirmed_checkpoints", None)
        _service._save_session(completed)

    monkeypatch.setattr(BuilderAutomationService, "_finalize_completed_session", finalize)
    service.worker_factory = lambda: (_ for _ in ()).throw(AssertionError("worker must not run"))

    result = service.recover_validated_result(object_type="scenario", object_id="recipes")

    assert result["ok"] is True
    assert result["worker"]["reused_validated_result"] is True
    assert finalized[0]["status"] == "commit_ready"
    assert finalized[0]["reuse_confirmed_checkpoints"] is True


def test_validated_result_recovery_records_missing_workflow_checkpoint_without_rerunning_codex(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    session = {
        "object_type": "scenario",
        "object_id": "recipes",
        "change_id": "change-1",
        "current_task_id": "task.1",
        "status": "completed",
        "task": {"task_id": "task.1", "status": "completed"},
        "last_result": {"summary": "ready"},
        "completion_readiness": {
            "ok": True,
            "task_id": "task.1",
            "vcs_checkpoints": [
                {
                    "ok": True,
                    "kind": "scenario",
                    "name": "recipes",
                    "commit": "forge-1",
                    "package_digest": "sha256:" + "1" * 64,
                    "source_revision": "forge-1",
                }
            ],
        },
    }
    service._save_session(session)
    finalized: list[dict] = []

    monkeypatch.setattr(BuilderAutomationService, "refresh_session", lambda self, value: dict(value))

    def finalize(_service, value):
        finalized.append(dict(value))
        completed = dict(value)
        completed["status"] = "completed"
        completed.pop("reuse_confirmed_checkpoints", None)
        _service._save_session(completed)

    monkeypatch.setattr(BuilderAutomationService, "_finalize_completed_session", finalize)
    service.worker_factory = lambda: (_ for _ in ()).throw(AssertionError("worker must not run"))

    result = service.recover_validated_result(object_type="scenario", object_id="recipes")

    assert result["ok"] is True
    assert result["worker"]["reused_validated_result"] is True
    assert result["worker"]["recovery_stage"] == "workflow_checkpoint"
    assert finalized[0]["status"] == "commit_ready"
    assert finalized[0]["reuse_confirmed_checkpoints"] is True


def test_refresh_restores_recovered_return_to_prototype_transition(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    session = {
        "object_type": "scenario",
        "object_id": "recipes",
        "current_task_id": "task.prototype",
        "status": "failed",
        "last_failure": {"message": "worker failed before finalization"},
    }
    task = {
        "task_id": "task.prototype",
        "status": "completed",
        "updated_at": "2026-07-28T12:00:00+00:00",
        "realize_request": {
            "artifacts": {"workflow_transition": "return_to_prototype"},
        },
        "result": {"summary": "Safe prototype recovered."},
    }
    monkeypatch.setattr(
        type(service.factory),
        "snapshot",
        lambda _self, **_kwargs: {"tasks": [task]},
    )

    refreshed = service.refresh_session(session)

    assert refreshed["status"] == "completed"
    assert refreshed["pending_workflow_transition"] == "return_to_prototype"
    assert refreshed["last_result"]["summary"] == "Safe prototype recovered."


def test_validated_result_recovery_finalizes_recovered_workflow_transition(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    session = {
        "object_type": "scenario",
        "object_id": "recipes",
        "current_task_id": "task.prototype",
        "status": "completed",
        "task": {"task_id": "task.prototype", "status": "completed"},
        "last_result": {"summary": "Safe prototype recovered."},
        "pending_workflow_transition": "return_to_prototype",
    }
    service._save_session(session)
    finalized: list[dict] = []
    monkeypatch.setattr(BuilderAutomationService, "refresh_session", lambda self, value: dict(value))

    def finalize(_service, value):
        finalized.append(dict(value))
        completed = dict(value)
        completed["status"] = "completed"
        completed.pop("pending_workflow_transition", None)
        _service._save_session(completed)

    monkeypatch.setattr(BuilderAutomationService, "_finalize_completed_session", finalize)
    service.worker_factory = lambda: (_ for _ in ()).throw(AssertionError("worker must not run"))

    result = service.recover_validated_result(object_type="scenario", object_id="recipes")

    assert result["ok"] is True
    assert result["worker"]["recovery_stage"] == "workflow_transition"
    assert finalized[0]["status"] == "commit_ready"
    assert finalized[0]["pending_workflow_transition"] == "return_to_prototype"


def test_validated_result_recovery_finalizes_externally_repaired_task(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    session = {
        "object_type": "skill",
        "object_id": "builder_sdk_control_skill",
        "current_task_id": "task.repaired",
        "status": "completed",
        "task": {"task_id": "task.repaired", "status": "completed"},
        "last_result": {"summary": "Preserved worktree repaired and validated."},
    }
    service._save_session(session)
    finalized: list[dict] = []
    monkeypatch.setattr(BuilderAutomationService, "refresh_session", lambda self, value: dict(value))

    def finalize(_service, value):
        finalized.append(dict(value))
        completed = dict(value)
        completed["status"] = "completed"
        _service._save_session(completed)

    monkeypatch.setattr(BuilderAutomationService, "_finalize_completed_session", finalize)
    service.worker_factory = lambda: (_ for _ in ()).throw(AssertionError("worker must not rerun"))

    result = service.recover_validated_result(
        object_type="skill",
        object_id="builder_sdk_control_skill",
    )

    assert result["ok"] is True
    assert result["worker"]["reused_validated_result"] is True
    assert result["worker"]["recovery_stage"] == "validated_activation"
    assert finalized[0]["status"] == "commit_ready"
    assert "reuse_confirmed_checkpoints" not in finalized[0]


def test_automation_checkpoints_scenario_and_companion_skill_with_result_summary(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    calls: list[dict] = []

    class _Workspace:
        @classmethod
        def from_context(cls):
            return cls()

        def checkpoint_artifact(self, **kwargs):
            calls.append(dict(kwargs))
            return {"ok": True, "kind": kwargs["kind"], "name": kwargs["artifact_id"], "commit": f"{kwargs['kind']}-sha"}

    import adaos.services.builder.workspace as workspace

    monkeypatch.setattr(workspace, "BuilderWorkspaceService", _Workspace)

    checkpoints = service._checkpoint_completed_artifacts(
        {
            "object_type": "scenario",
            "object_id": "recipes",
            "companion_skill_id": "recipes_skill",
            "last_result": {
                "summary": "Implemented recipe filters and details.",
                "changed_paths": [
                    "skills/recipes_skill/handlers/main.py",
                    "scenarios/recipes/webui.json",
                ],
            },
        }
    )

    assert calls == [
        {
            "kind": "skill",
            "artifact_id": "recipes_skill",
            "message": "Implemented recipe filters and details.",
        },
        {
            "kind": "scenario",
            "artifact_id": "recipes",
            "message": "Implemented recipe filters and details.",
        },
    ]
    assert [item["commit"] for item in checkpoints] == ["skill-sha", "scenario-sha"]


def test_automation_does_not_checkpoint_unchanged_companion_skill(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    calls: list[dict] = []

    class _Workspace:
        @classmethod
        def from_context(cls):
            return cls()

        def checkpoint_artifact(self, **kwargs):
            calls.append(dict(kwargs))
            return {"ok": True, "kind": kwargs["kind"], "name": kwargs["artifact_id"]}

    import adaos.services.builder.workspace as workspace

    monkeypatch.setattr(workspace, "BuilderWorkspaceService", _Workspace)

    checkpoints = service._checkpoint_completed_artifacts(
        {
            "object_type": "scenario",
            "object_id": "recipes",
            "companion_skill_id": "recipes_skill",
            "last_result": {
                "summary": "Aligned derived scenario projections.",
                "changed_paths": [
                    ".adaos/tasks/task.1/result.json",
                    "scenarios/recipes/scenario.json",
                    "scenarios/recipes/webui.json",
                ],
            },
        }
    )

    assert calls == [
        {
            "kind": "scenario",
            "artifact_id": "recipes",
            "message": "Aligned derived scenario projections.",
        }
    ]
    assert checkpoints == [{"ok": True, "kind": "scenario", "name": "recipes"}]


def test_automation_checkpoints_primary_scenario_when_only_companion_skill_changed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    calls: list[dict] = []

    class _Workspace:
        @classmethod
        def from_context(cls):
            return cls()

        def checkpoint_artifact(self, **kwargs):
            calls.append(dict(kwargs))
            return {"ok": True, "kind": kwargs["kind"], "name": kwargs["artifact_id"]}

    import adaos.services.builder.workspace as workspace

    monkeypatch.setattr(workspace, "BuilderWorkspaceService", _Workspace)

    checkpoints = service._checkpoint_completed_artifacts(
        {
            "object_type": "scenario",
            "object_id": "recipes",
            "companion_skill_id": "recipes_skill",
            "last_result": {
                "summary": "Implemented the scenario dependency in its companion skill.",
                "changed_paths": ["skills/recipes_skill/handlers/main.py"],
            },
        }
    )

    assert [(item["kind"], item["artifact_id"]) for item in calls] == [
        ("skill", "recipes_skill"),
        ("scenario", "recipes"),
    ]
    assert [(item["kind"], item["name"]) for item in checkpoints] == [
        ("skill", "recipes_skill"),
        ("scenario", "recipes"),
    ]
