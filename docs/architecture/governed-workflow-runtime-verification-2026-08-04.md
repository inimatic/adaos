# Governed Workflow Runtime Verification 2026-08-04

Status: accepted `validated-local` evidence for the current data-driven
workflow increment. This record does not claim live Telegram ingress durability
or broad production rollout.

## Accepted Slices

| Slice | Revision | Reproducible evidence | Accepted result |
| --- | --- | --- | --- |
| Cross-channel ingress and trace spine | `5e0ed333` | `tests/test_workflow_execution.py`, `tests/test_workflow_trace_identity.py`, interaction/runtime/delivery tests | Web, Telegram, numbered text, and SDK preserve one invocation semantic digest, guards, generation, target, executor readiness, and result; one trace reaches the DeliveryAttempt |
| Builder package cutover | `6dfd3442` | `tests/test_builder_governed_workflow.py` | strict mode requires active WorkspaceLock `skill:builder_skill`, verifies definition/validation/binding pins, has no definition fallback, pins instances, and survives migration restart/rollback |
| Publication admission | `b0dd4fae` | package, release, Workspace activation, admission, publication, and artifact E2E tests | one gate binds package code/manifest, workflow definition/validation, adapter binding, role policy, desired lock, and migrations before channel mutation |
| Story runner and metrics evidence | `b72f2a7d` | conversational artifact/runtime, workflow metrics, Builder Run, artifact Trial, and publication tests | runner v2 covers repair, interaction/fallback, stale/concurrent/retry/executor-unavailable/negative cases; Run and Trial persist bounded workflow metrics |

## Proof Boundaries

The ingress harness is intentionally deterministic and side-effect isolated.
It proves semantic equality for exactly `web`, `telegram`, `text`, and `sdk`,
including `executor_unavailable`; it does not prove that a public Telegram
webhook has durably reached an offline target hub. That transport acceptance
boundary remains GWR6-16.

The Builder cutover is strict when
`ADAOS_BUILDER_REQUIRE_ACTIVE_PACKAGE=true`. The flag remains off by default so
deployment can be staged and isolated tests can retain a bounded compatibility
constructor. Enabling it by default in accepted environments, observing the
rollback window, and then removing the compatibility constructor and legacy
projection store are rollout work, not missing strict-path behavior.

A 2026-08-05 architecture audit tightened this evidence boundary. The accepted
tests prove the workflow/interaction/IntentProposal contracts and a deterministic
semantic Builder path. They do **not** prove that live Web, Telegram, voice, and
Builder private parsing already converge on one production intent-mediation
rail; that all English/Russian presentation keys are ABI-bound and complete;
that concrete LLM/Codex/Trial/Publication executors are registered; or that the
full path ran without compatibility promotion transitions. Those are explicit
open GWR2/GWR3/GWR4/GWR5 gates in the current roadmap.

Publication and activation share
`WorkspaceActivationManager.admit_release_candidate`. A role-policy mismatch,
code/workflow mixture, stale validation lock, or adapter-binding mismatch fails
before a stable-channel write. The durable publication operation stores the
admission evidence and resumes only subsequent idempotent phases.

## Local Commands

The implementation slices were checked on Windows with the repository virtual
environment. The focused acceptance groups include:

```powershell
.venv\Scripts\python.exe -m pytest `
  tests\test_workflow_execution.py `
  tests\test_workflow_trace_identity.py `
  tests\test_conversation_interactions.py `
  tests\test_conversational_runtime.py -q

.venv\Scripts\python.exe -m pytest `
  tests\test_builder_governed_workflow.py `
  tests\test_builder_governed_e2e.py -q

.venv\Scripts\python.exe -m pytest `
  tests\test_artifact_package_store.py `
  tests\test_artifact_workspace_activation.py `
  tests\test_artifact_publication_service.py `
  tests\test_workflow_admission.py `
  tests\test_governed_workflow_artifact_e2e.py -q

.venv\Scripts\python.exe -m pytest `
  tests\test_conversational_artifacts.py `
  tests\test_conversational_runtime.py `
  tests\test_conversation_interactions.py `
  tests\test_workflow_metrics.py `
  tests\test_builder_run_and_data_modes.py `
  tests\test_builder_governed_workflow.py `
  tests\test_builder_governed_e2e.py `
  tests\test_artifact_candidates.py `
  tests\test_artifact_publication_service.py -q
```

The story/metrics group passed `85` tests. The final combined gate selected 24
workflow, Builder, conversational, and artifact modules and passed `243` tests
in `171.6s` on this machine. The manifest ABI, artifact release contract, and
conversation contract group passed another `83` tests. `mkdocs build --strict`
completed successfully in `123s`.

## Inventory And Provider Decision

The source inventory now includes governed journals, conversation/delivery
stores, publication and activation operations, Builder migration/Automation/
Preview state, runtime operations, Skill Factory, core update, Root MCP leases,
hub-root acknowledgements, retries, and process-local task registries. Each is
classified by owner and migration disposition.

No inventoried node-local business process meets the external-provider
admission criteria. Shared SQLite remains sufficient for the bounded workflow
and reply path. GWR6-16 calls for a durable transport inbox and receipt, so the
DBOS/Temporal/Restate decision remains postponed rather than silently selected.

## Remaining Gates

1. Complete the localized semantic Interaction/Affordance ABI and migrate live
   Web/Telegram/voice/Builder text to one package-bound IntentProposal rail;
   retire private/direct mutation paths after measured compatibility coverage.
2. Register concrete Builder activities, replace compatibility Trial/
   Publication transitions with normative waiting/results, and record one real
   built-in LLM -> isolated Codex -> Trial -> Publication run on an empty
   scenario without manual repair.
3. Enable strict Builder package mode by default in an accepted deployment,
   observe restart/rollback, then retire the compatibility constructor and
   migrate the remaining `prompt_state.json` projection authority.
4. Implement and accept the GWR6-16 per-hub Telegram inbox and target-zone
   durable receipt; repeat live mutating callback coverage without weakening
   the local semantic gate.
5. Keep direct-agent comparison metrics and runtime-failure-to-story promotion
   in their owning Builder/conversational roadmaps; they do not block the
   current data-driven workflow semantic model.
6. Re-evaluate an external durable provider only when the persistence ADR's
   distributed, availability, scale, timer, or operator-cost criterion is
   measured.
