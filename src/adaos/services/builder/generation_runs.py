"""Prospective Run preparation, distinct from static task ownership.

No legacy delivery is enabled here. A completed orchestrator contract must opt
in before the provider boundary. Observation uses its retained binding rather
than today's invocation; preparing a Run grants no authoring authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from adaos.services.builder_context import digest
from .generation_plan import GenerationInputRejected
from .interpretation_runs import InterpretationRunRejected
from .task_spec import seal, verify_identity


def generation_run_ref(origin, invocation_scope):
    return (
        "generation-run:"
        + digest({"origin": origin["origin_id"], "invocation": invocation_scope})[7:31]
    )


def verify_run_binding(binding, origin, plan):
    from .generation_origin import verify_origin

    verify_origin(origin, plan)
    verify_identity(binding, id_key="binding_id", prefix="generation-binding:")
    scope = binding.get("invocation_scope")
    if (
        plan.get("execution_run_required") is not True
        or plan.get("execution_origin_required") is not True
        or binding.get("schema") != "adaos.builder.generation_run_binding.v1"
        or binding.get("origin_ref") != origin["origin_id"]
        or binding.get("generation_plan_ref") != plan["generation_plan_id"]
        or not isinstance(scope, str)
        or not re.fullmatch(r"[a-f0-9]{64}", scope)
        or binding.get("run_id") != generation_run_ref(origin, scope)
    ):
        raise GenerationInputRejected(
            "context.execution_run_binding_conflict",
            "The execution binding differs from its frozen origin/plan/invocation",
        )
    return binding


class GenerationRuns:
    def __init__(self, workflow=None):
        self._workflow = workflow

    @property
    def workflow(self):
        if self._workflow is None:
            from .workflow import BuilderWorkflowService

            self._workflow = BuilderWorkflowService.from_context()
        return self._workflow

    def reserve(self, task, plan, origin):
        """Reserve under the trusted current invocation before paid submission.

        Automatic repairs in the same invocation share the generation Run.
        A new invocation has a distinct Run id without moving task ownership.
        This prerequisite is not wired into legacy Builder delivery.
        """
        from .generation_origin import verify_origin
        from adaos.services.policy.invocation import current_invocation

        try:
            verify_origin(origin, plan, task=task)
            invocation = (current_invocation() or {}).get("request_id")
            if (
                not plan.get("execution_run_required")
                or not isinstance(invocation, str)
                or not invocation
            ):
                raise InterpretationRunRejected()
            scope = hashlib.sha256(invocation.encode()).hexdigest()
            binding = seal(
                {
                    "schema": "adaos.builder.generation_run_binding.v1",
                    "origin_ref": origin["origin_id"],
                    "generation_plan_ref": plan["generation_plan_id"],
                    "invocation_scope": scope,
                    "run_id": generation_run_ref(origin, scope),
                },
                id_key="binding_id",
                prefix="generation-binding:",
            )
            verify_run_binding(binding, origin, plan)
            result = self.workflow.record_generation_preparation(
                binding, origin=origin, plan=plan
            )
            if not result.get("ok"):
                raise InterpretationRunRejected()
            return {**result, "binding": binding}
        except InterpretationRunRejected:
            raise
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise InterpretationRunRejected() from error

    def assert_submission(self, binding, origin, plan):
        """A new submission needs the already reserved current execution only."""
        from adaos.services.policy.invocation import current_invocation

        try:
            verify_run_binding(binding, origin, plan)
            invocation = (current_invocation() or {}).get("request_id")
            if (
                not isinstance(invocation, str)
                or not invocation
                or hashlib.sha256(invocation.encode()).hexdigest()
                != binding["invocation_scope"]
            ):
                raise InterpretationRunRejected()
            result = self.workflow.record_generation_preparation(
                binding, origin=origin, plan=plan, existing_only=True
            )
            if not result.get("ok"):
                raise InterpretationRunRejected()
        except InterpretationRunRejected:
            raise
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise InterpretationRunRejected() from error


def retained_run_binding(journal_dir, plan, origin):
    """Read the exact pre-submission binding; never fabricate a legacy one."""
    if not plan or not plan.get("execution_run_required"):
        return None
    try:
        verify_identity(plan, id_key="generation_plan_id", prefix="generation-plan:")
        name = (
            "repair-plan-" + plan["generation_plan_id"].partition(":")[2]
            if plan.get("context_stage") == "repair"
            else plan["generation_plan_id"].replace(":", "-")
        )
        record = json.loads(
            (Path(journal_dir) / (name + ".json")).read_text(encoding="utf-8")
        )
        if (
            record.get("generation_plan") != plan
            or record.get("execution_origin") != origin
        ):
            raise InterpretationRunRejected()
        binding = record["execution_run_binding"]
        verify_run_binding(binding, origin, plan)
        return binding
    except InterpretationRunRejected:
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise InterpretationRunRejected() from error


def wire_run_binding(journal_dir, wire, origin):
    """Observe an old paid wire without consulting today's invocation/selection."""
    admission = wire.get("context_admission") or {}
    binding = wire.get("execution_run_binding")
    if not admission.get("execution_run_required") and binding is None:
        return None
    try:
        ref = admission["generation_plan_ref"]
        if not isinstance(ref, str) or not re.fullmatch(
            r"generation-plan:[a-f0-9]{24}", ref
        ):
            raise InterpretationRunRejected()
        name = (
            "repair-plan-" + ref.partition(":")[2]
            if admission.get("parent_generation_plan_ref")
            else ref.replace(":", "-")
        )
        plan = json.loads(
            (Path(journal_dir) / (name + ".json")).read_text(encoding="utf-8")
        )["generation_plan"]
        expected = retained_run_binding(journal_dir, plan, origin)
        if expected is None or binding != expected:
            raise InterpretationRunRejected()
        return expected
    except InterpretationRunRejected:
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise InterpretationRunRejected() from error
