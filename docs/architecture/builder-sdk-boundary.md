# Builder SDK Boundary And Migration Roadmap

Status: SDK boundary implemented; autonomous from-zero proof remains in progress.

This document defines the public SDK boundary required by Prompt IDE, Builder,
and autonomous Builder development. It complements `builder.md` and
`builder-roadmap.md`; it does not replace their product and runtime plans.

## Why This Boundary Exists

Runtime skills are replaceable application code. `adaos.services` is core
implementation code and may change as storage, orchestration, or deployment
details evolve. A skill therefore imports capabilities from `adaos.sdk` and
must not construct or call core services directly.

The dependency direction is:

```text
scenario UI -> skill tools -> adaos.sdk -> adaos.services -> ports/adapters
```

The SDK owns stable operation names, bounded inputs, render-safe results,
runtime-context lookup, and public errors. Services own orchestration and
internal persistence. Skills own presentation, localization, and composition
of SDK operations into tool responses.

## Target Public Surfaces

### Developer projects

`adaos.sdk.developer.projects` owns DEV artifact discovery and lifecycle:

- list and describe skill/scenario projects;
- resolve a project without exposing runtime path-provider internals;
- list, read, and write allowlisted project files with bounded payloads;
- list templates and create projects;
- push, update, publish, and delete projects;
- return plain JSON-compatible results and public SDK errors.

Direct recursive deletion and construction of `.adaos/dev` paths do not belong
in skills.

### Builder preview

`adaos.sdk.builder.preview` owns workbench selection and preview lifecycle:

- select the active scenario/project;
- ensure or open its dev webspace;
- read the current source-to-dev-webspace binding;
- reload or materialize a validated Builder revision.

Skills do not persist workbench bindings or call webspace runtime services.

### Builder automation

`adaos.sdk.builder.automation` owns the implementation loop:

- start from an approved implementation brief;
- submit one bounded follow-up turn;
- read the compact automation projection.

The SDK returns the stable automation projection, not a
`BuilderAutomationService` instance.

### Builder artifacts and conversation evidence

`adaos.sdk.builder.artifacts` owns artifact checkpoints. Conversation SDK
operations own Builder topic creation and Builder Change lookup/upsert. Pending
Actions and event publication use their existing SDK surfaces.

## Dependency Contract

Scenario manifests must list every required skill. An `active_agent_id`, skill
data source, callSkill action, or skill-owned stream receiver is a runtime
dependency even when no tool is invoked during first paint.

The Builder prototype is the migration control fixture. Its control skill must:

- import only `adaos.sdk` and standard/declared third-party packages;
- exercise developer project read/write, preview selection, and automation
  state without depending on Prompt IDE handlers;
- expose a single diagnostic tool that reports capability results separately;
- remain safe to run repeatedly in the DEV snapshot.

## Code Granularity

Module decomposition is not the primary goal of this migration. New SDK code is
split by capability so the migration does not make existing large modules
larger. Decomposing `prompt_engineer_skill`, `builder_skill`, Root developer,
scenario runtime, and conversation storage requires its own characterization
tests and compatibility plan.

No large-module split should be mixed into an SDK-boundary commit unless it is
required to establish the public contract.

## Roadmap And Checklist

Tags indicate priority, not implementation order:

- `must`: required before replacing Prompt IDE with Builder;
- `should`: required for a maintainable supported boundary;
- `could`: useful follow-up that does not block the migration;
- `deferred`: intentionally retained for a dedicated later refactor.

### Phase 0: contract and observability

- [x] `[must]` Record the target dependency direction and SDK ownership.
- [x] `[must]` Identify direct service imports in Prompt IDE/Builder skills.
- [x] `[must]` Keep this checklist synchronized with landed implementation.
- [ ] `[should]` Publish an SDK compatibility/version marker consumable by
  skill manifests.
- [ ] `[could]` Expose the boundary audit in operator diagnostics.

### Phase 1: minimal public SDK

- [x] `[must]` Add `adaos.sdk.builder.preview` with selection, binding, ensure,
  open, reload, and revision materialization operations.
- [x] `[must]` Add `adaos.sdk.builder.automation` with start, submit, and state
  operations.
- [x] `[must]` Add `adaos.sdk.developer.projects` lifecycle operations with
  plain result contracts.
- [x] `[must]` Add contract tests that mock services below the SDK boundary.
- [x] `[should]` Add `adaos.sdk.builder.artifacts` checkpoint operations.
- [x] `[should]` Add Builder Change operations to the conversation SDK.
- [ ] `[should]` Replace the Root developer class re-export with a real facade
  while retaining the old import as a compatibility alias.

### Phase 2: skill migration

- [x] `[must]` Migrate `prompt_engineer_skill` event and preview calls to SDK.
- [x] `[must]` Migrate `builder_automation_skill` to SDK.
- [x] `[must]` Remove all `adaos.services` imports from the Builder control
  fixture.
- [x] `[should]` Migrate Builder operations used by the replacement UI away
  from direct service imports.
- [ ] `[should]` Move Prompt IDE DEV file lifecycle behind developer projects
  SDK operations.
- [ ] `[could]` Migrate unrelated legacy skills after the Builder slice is
  stable.

### Phase 3: control fixture and replacement proof

- [x] `[must]` Create a new DEV control skill for
  `.adaos/dev/sn_6acf0c01/scenarios/builder`.
- [x] `[must]` Declare actual Builder scenario dependencies in both YAML and
  JSON manifests.
- [x] `[must]` Run control-skill import, validation, tool, and focused runtime
  tests on the development machine.
- [ ] `[must]` Prove the control skill can be recreated from zero by AdaOS
  autonomous programming without importing Prompt IDE implementation code.
- [x] `[must]` Keep legacy Prompt IDE active until the recreated skill passes
  the same checks.
- [ ] `[should]` Add the control fixture to a repeatable local smoke command.
- [ ] `[could]` Retain a compact golden fixture after Prompt IDE removal.

### Phase 4: enforcement and cleanup

- [x] `[must]` Add an SDK-only import guard for the migrated skills.
- [x] `[should]` Support `runtime.sdk_only: true` as an opt-in manifest marker.
- [ ] `[should]` Make `runtime.sdk_only` the default in new skill templates
  after the remaining templates are migrated.
- [ ] `[should]` Remove compatibility aliases after all consumers migrate.
- [ ] `[could]` Extend the guard to all workspace skills.
- [ ] `[deferred]` Split the large Prompt Engineer handler by project,
  specification, LLM, metadata, and VCS capabilities.
- [ ] `[deferred]` Split `builder_skill` into dialog, transformation, revision,
  workbench, automation, and checkpoint modules.
- [ ] `[deferred]` Decompose Root developer, scenario runtime, and conversation
  storage behind characterization tests.
- [ ] `[deferred]` Remove legacy Prompt IDE only after autonomous Builder
  development and rollback procedures are proven.

## Exit Criteria

Prompt IDE can be retired only when:

1. the replacement Builder scenario declares all runtime skill dependencies;
2. its control/recreated skill has no `adaos.services` imports;
3. SDK contract tests and control-skill tests pass locally;
4. project selection, bounded file editing, preview, automation state, and
   checkpoint evidence work through SDK operations;
5. legacy Prompt IDE remains a tested rollback path until the replacement has
   completed an autonomous from-zero implementation run.

## Local Verification Record

The first migration pass was verified on the development machine on
2026-07-18 with:

- SDK contract tests for Builder preview, automation, artifacts, developer
  projects, conversation/change evidence, and workbench integration;
- the complete local `builder_skill` test module and focused Prompt Engineer
  and Builder Automation tests;
- strict DEV validation with tool probing for `builder_sdk_control_skill`;
- source and activated-runtime tests for the control skill;
- live `get_state` and `get_automation` tool calls against the current Builder
  scenario and the completed Builder conversation/change services.

The remaining blocking proof is intentionally external to this pass: recreate
the control skill from an empty DEV project through AdaOS autonomous
programming, run the same checks, and only then plan Prompt IDE removal.
