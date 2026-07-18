# Builder SDK Boundary And Migration Roadmap

Status: functional SDK-backed Builder proof completed locally; autonomous
from-zero development is intentionally deferred to the next phase.

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
- read the compact automation projection, treating a project without an
  Automation session as the valid `idle` state.

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

## Functional Builder Control Architecture

The current control slice separates presentation from platform capabilities:

```text
Builder scenario UI
  |-- project/files/preview/automation/release -> builder_sdk_control_skill
  |                                               -> adaos.sdk.*
  `-- dialog stream                          -> builder_skill
```

The scenario contains no static project, file, preview, or lifecycle mocks.
Project discovery, bounded file editing, preview selection, Builder Change
evidence, automation projection, Forge checkpointing, and publication dry-run
are skill-backed. Real publication remains behind the runtime Action approval
policy. Starting autonomous implementation is available in the UI, but is not
part of the current functional smoke because it would introduce the second
variable that this phase is intended to exclude.

The control skill is deliberately an application adapter rather than a second
core API. It shapes SDK results for browser widgets and declares trusted
`local_write`/`ui_navigation` effects for bounded interactive operations.
External VCS and publication operations keep mandatory review semantics.

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
- [x] `[should]` Hide DEV runtime and generated cache artifacts from project
  discovery and file listings.
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
- [x] `[must]` Replace static Builder mock data with live project, file,
  preview, change, automation, and release skill bindings.
- [x] `[must]` Keep YAML and JSON scenario descriptors version-aligned during
  DEV push without replacing their UI content.
- [x] `[must]` Make DEV activation select the source manifest version by
  default and verify its patch version instead of reusing a stale slot from
  the same runtime bucket.
- [x] `[must]` Run control-skill import, validation, tool, and focused runtime
  tests on the development machine.
- [x] `[must]` Prove live HTTP tool execution, synchronous Yjs
  materialization, bounded file save, Builder Change evidence, and Builder
  dialog against a dedicated webspace.
- [x] `[must]` Render a project with no Automation session as a valid `idle`
  projection without starting autonomous work.
- [ ] `[deferred]` Recreate the control skill from zero by AdaOS autonomous
  programming without importing Prompt IDE implementation code.
- [x] `[must]` Keep legacy Prompt IDE active until the recreated skill passes
  the same checks.
- [x] `[should]` Add the control fixture to a repeatable local smoke command.
- [ ] `[could]` Retain a compact golden fixture after Prompt IDE removal.

### Phase 4: enforcement and cleanup

- [x] `[must]` Add an SDK-only import guard for the migrated skills.
- [x] `[should]` Support `runtime.sdk_only: true` as an opt-in manifest marker.
- [x] `[should]` Declare trusted interactive side effects in the control-skill
  manifest so bounded save/create actions do not inherit false filesystem risk.
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

The functional-prototype gate for this phase is complete: the Builder scenario
loads from DEV, materializes into a paired webspace, resolves its declared
skills, reads and saves bounded files, records evidence, exposes idle
automation state, performs publication dry-run, and answers a deterministic
Builder dialog turn through the live HTTP runtime.

Prompt IDE can be retired only when:

1. the replacement Builder scenario declares all runtime skill dependencies;
2. its control/recreated skill has no `adaos.services` imports;
3. SDK contract tests and control-skill tests pass locally;
4. project selection, bounded file editing, preview, automation state, and
   checkpoint evidence work through SDK operations; and
5. legacy Prompt IDE remains a tested rollback path until the replacement has
   completed an autonomous from-zero implementation run.

Criteria 1-4 are now satisfied by the control slice. Criterion 5 remains
intentionally deferred; no legacy removal is authorized by this phase.

## Local Verification Record

The functional Builder pass was verified on the development machine on
2026-07-18 with:

- SDK contract tests for Builder preview, automation, artifacts, developer
  projects, conversation/change evidence, and workbench integration;
- the complete local `builder_skill` test module and focused Prompt Engineer
  and Builder Automation tests;
- strict DEV validation with tool probing for `builder_sdk_control_skill`;
- source and activated-runtime tests for the control skill;
- live `get_state` and `get_automation` tool calls against the current Builder
  scenario and the completed Builder conversation/change services;
- live `/api/tools/call` execution for all first-paint Builder data sources,
  same-content bounded file save, publication dry-run, and deterministic
  `builder_skill.chat` project lookup;
- synchronous materialization of `builder-http-smoke-dev`, reporting
  `ready=true` and preserving all seven skill data sources and six
  `callSkill` actions in the effective desktop projection.

The final smoke uses a dedicated source webspace so it does not change the
operator's Prompt IDE selection. On PowerShell:

```powershell
$payload = '{"webspace_id":"builder-sdk-control"}'.Replace('"', '\"')
$env:PYTHONPATH = 'src'
.venv\Scripts\python.exe -c "from adaos.apps.cli.app import app; app()" dev skill validate builder_sdk_control_skill --strict --probe-tools --json
.venv\Scripts\python.exe -c "from adaos.apps.cli.app import app; app()" dev skill test builder_sdk_control_skill --runtime --json
.venv\Scripts\python.exe -c "from adaos.apps.cli.app import app; app()" dev skill activate builder_sdk_control_skill
.venv\Scripts\python.exe -c "from adaos.apps.cli.app import app; app()" dev skill run builder_sdk_control_skill select_preview --json $payload --timeout 120
.venv\Scripts\python.exe -c "from adaos.apps.cli.app import app; app()" dev skill run builder_sdk_control_skill get_state --json $payload --timeout 30
.venv\Scripts\python.exe -c "from adaos.apps.cli.app import app; app()" node yjs materialization --webspace builder-sdk-control-dev --json
```

This also covers the later Builder-service change that classifies paired
`*-dev` workspaces as DEV sources and exposes local DEV scenarios there. The
verified pair is `builder-sdk-control` / `builder-sdk-control-dev`, with the
Builder scenario selected as its DEV runtime home.

Activated migration versions for this pass are `builder_skill 0.2.113`,
`builder_automation_skill 0.1.1`, `prompt_engineer_skill 0.6.8`, and
`builder_sdk_control_skill 0.1.7`. The functional Builder scenario is on the
`0.2.2` version. Runtime self-tests and live materialization use dedicated
webspaces and do not change the operator's Prompt IDE selection.

The next proof is intentionally outside this pass: recreate the control skill
from an empty DEV project through AdaOS autonomous programming, run the same
checks, and only then plan Prompt IDE removal.
