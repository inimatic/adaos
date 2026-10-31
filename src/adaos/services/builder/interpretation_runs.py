"""Core-owned observations of interpretation, not another workflow authority.

Bind the originating Change before submission. Observe only new bound journals;
legacy attempts cannot be retroactively attributed to today's active Change.
"""

from __future__ import annotations

import copy

import yaml
from jsonschema import ValidationError

from adaos.services.builder_context import digest

from .run_observability import execution_metrics_from_llm
from .task_interpretation import TaskInterpretationRejected
from .task_spec import seal, verify_identity


class InterpretationRunRejected(TaskInterpretationRejected):
    def __init__(self):
        super().__init__(
            [
                {
                    "code": "task.execution_run_unavailable",
                    "gate": "G5",
                    "owner": "context_compilation",
                    "detail": "The originating execution Run cannot be safely recorded. "
                    "Retained inference must not be repeated or attributed to another Change.",
                }
            ]
        )


class InterpretationRuns:
    def __init__(self, workflow=None):
        self._workflow = workflow

    @property
    def workflow(self):
        if self._workflow is None:
            from .workflow import BuilderWorkflowService

            self._workflow = BuilderWorkflowService.from_context()
        return self._workflow

    def bind(self, task, request_id):
        try:
            return self._bind(task, request_id)
        except InterpretationRunRejected:
            raise
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            yaml.YAMLError,
            ValidationError,
        ) as error:
            raise InterpretationRunRejected() from error

    def _bind(self, task, request_id):
        verify_identity(task, id_key="task_spec_id", prefix="task:")
        from .authoring_scope import resolve_authoring_scope

        scope = resolve_authoring_scope(self.workflow, task["scope"]["project_ref"] or "")
        kind, identity = scope["object_type"], scope["object_id"]
        project_ref, manifest_digest = scope["project_ref"], scope["manifest_digest"]
        context = self.workflow.prototype_edit_context(kind, identity)
        change_id = context["change"].get("change_id")
        if (
            not change_id
            or context["archived"]
            or context["active_phase"] != "prototype"
            or not context["capabilities"]["can_edit_prototype"]
        ):
            raise InterpretationRunRejected()
        if task["scope"].get("change_ref") not in {None, change_id}:
            raise InterpretationRunRejected()
        source = task["source"]
        return seal(
            {
                "schema": "adaos.builder.interpretation_run_binding.v1",
                "object_type": kind,
                "object_id": identity,
                "project_ref": project_ref,
                "change_id": change_id,
                "origin_generation": context["generation"],
                "manifest_digest": manifest_digest,
                "source_digest": source["digest"],
                "source_message_ref": source["provenance"].get("ref"),
                "task_spec_ref": task["task_spec_id"],
                "request_id": request_id,
                "run_id": "interpretation-run:"
                + digest({"project": project_ref, "request": request_id})[7:31],
                "execution_group_ref": "task-execution:"
                + digest({"project": project_ref, "source": source["digest"]})[7:31],
            },
            id_key="binding_id",
            prefix="interpretation-binding:",
        )

    def observe(self, binding, record, phase):
        try:
            return self._observe(binding, record, phase)
        except InterpretationRunRejected:
            raise
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            yaml.YAMLError,
            ValidationError,
        ) as error:
            raise InterpretationRunRejected() from error

    def bind_generation(self, task, plan):
        """Freeze the source owner before submission; do not create a new Run."""
        from .generation_origin import from_run_binding

        binding = self.bind(task, plan["generation_plan_id"])
        return from_run_binding(task, plan, binding)

    def _observe(self, binding, record, phase):
        verify_identity(binding, id_key="binding_id", prefix="interpretation-binding:")
        verify_identity(record["task_spec"], id_key="task_spec_id", prefix="task:")
        if (
            binding["request_id"] != record["request_id"]
            or binding["task_spec_ref"] != record["task_spec"]["task_spec_id"]
            or binding["source_digest"] != record["task_spec"]["source"]["digest"]
            or binding["project_ref"] != record["task_spec"]["scope"]["project_ref"]
        ):
            raise InterpretationRunRejected()
        # Observe the frozen origin, not a fresh selection of today's primary.
        # This is metering, not authority to apply a candidate. Unrelated Project
        # metadata edits must not prevent recording an already paid attempt.
        result = record.get("result") or {}
        if result.get("job_id") and result["job_id"] != record.get("job_id"):
            raise InterpretationRunRejected()
        telemetry = copy.deepcopy(result.get("telemetry") or {})
        if record.get("job_id"):
            telemetry["root_job_id"] = record["job_id"]
        metrics = execution_metrics_from_llm(
            {
                "model": record["options"]["model"],
                "usage": result.get("usage") or {},
                "telemetry": telemetry,
            },
            reasoning_effort=(record["options"].get("reasoning") or {}).get("effort"),
        )
        metrics.update(
            pipeline_stage="interpretation",
            execution_group_ref=binding["execution_group_ref"],
        )
        refs = [
            record["task_spec"]["task_spec_id"],
            "interpretation_attempt:" + record["request_id"],
        ]
        refs += list(metrics.get("source_refs") or [])
        response = self.workflow.record_interpretation_observation(
            binding, phase=phase, execution_metrics=metrics, evidence_refs=refs
        )
        if not response.get("ok"):
            raise InterpretationRunRejected()
        return response
