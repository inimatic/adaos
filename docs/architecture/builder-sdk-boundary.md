# Builder SDK Boundary And Migration Roadmap

Status: functional SDK-backed Builder proof and live-binding remediation are
complete locally in revision `032`. Chat-driven Automation and real
Publication have been exercised with an isolated control project. Durable
Forge acknowledgement for scenario drafts remains an explicit blocker;
autonomous from-zero development is intentionally deferred to the next phase.
The current one-component developer-project APIs remain compatible, while the
composite `adaos.project.v1`, scoped Development Session, presentation, and
local artifact-context extensions are specified for Builder Phase 12 and are
not claimed complete by revision `032`.

This document defines the public SDK boundary required by Prompt IDE, Builder,
and autonomous Builder development. It complements `builder.md` and
`builder-roadmap.md`; it does not replace their product and runtime plans.
The general Project/session contract is defined by
[Project Composition, Presentation, and Development Context](project-composition-and-development-context.md).

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

`adaos.sdk.developer.projects` currently owns one-component DEV artifact
discovery and lifecycle. Its target contract additionally owns declarative
Project composition without exposing runtime path-provider internals:

- list and describe Project definitions and legacy skill/scenario project
  projections;
- resolve a project without exposing runtime path-provider internals;
- list, read, and write allowlisted project files with bounded payloads;
- update bounded project metadata without losing scenario UI payloads;
- expose the initialized project type and reject attempts to change it after
  creation;
- list templates and atomically create Projects plus template-declared owned
  components;
- add/remove Project components and dependencies without silently changing
  component ownership;
- checkpoint, publish, and delete projects;
- return plain JSON-compatible results and public SDK errors.

Direct recursive deletion and construction of `.adaos/dev` paths do not belong
in skills.

### Builder Development Session context

`adaos.sdk.developer.prompt_context` is the compatibility name for context
that targets `adaos.builder.development_session.v1`. It owns the development
context formerly stored directly by Prompt IDE handlers:

- read and atomically replace the base technical specification;
- append bounded, immutable specification addenda;
- persist the selected development LLM, provider, workflow state, and archive
  marker;
- keep the managed state file and base specification synchronized without
  exposing DEV paths to skills.
- keep UI focus independent from primary/secondary write targets;
- expose dependencies as `contract`, `docs`, bounded paths, or no source;
- admit local artifact groups read-only and return explicit scope-expansion
  requests when a run needs to mutate another component.

### Builder preview

`adaos.sdk.builder.preview` owns workbench selection and preview lifecycle:

- select the active Project/component and resolve its explicit presentation or
  generic system skill-preview fallback;
- ensure or open its dev webspace;
- return one canonical navigation destination shared by Open Preview and QR;
- read the current source-to-dev-webspace binding;
- reload or materialize a validated Builder revision.

Source identity is resolved through the persisted Builder preview relation.
Preview IDs are opaque and SDK consumers never append or remove `-dev`.
The one nested exception is self-hosted Builder development: a DEV preview
running Builder may own one terminal project preview. See
[Builder Preview Runtime](builder-preview-runtime.md).

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

This checkpoint/evidence API is distinct from the target Skill SDK
model-facing source context. `ctx.artifacts` enumerates and resolves manifested
`artifacts/partN` groups owned by a Project target skill, returns bounded
metadata/text plus a native path for admitted Codex sessions, and preserves a
provider-neutral ArtifactRef for future external/MCP adapters. Builder mounts
those inputs read-only; it does not copy them into conversation state or
runtime experiment data.

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
Builder scenario UI (prototype 029 geometry)
  |-- project/files/TZ/preview/automation/release -> builder_sdk_control_skill
  |                                                  -> adaos.sdk.*
  `-- dialog stream + revision restore             -> builder_skill
```

Revision `030` first derived the functional surface from approved prototype
`029`. Revision `031` records the user-requested immutable project-type
requirement. Revision `032` keeps the approved three-pane geometry and modal
contracts while correcting runtime bindings for preview, conversation,
metadata, and Automation diagnostics. The scenario contains no static project,
file, preview, or lifecycle mocks. Project discovery, project composition,
nested files,
technical specification and addenda, metadata, LLM selection, workflow state,
preview, Builder Change evidence, automation, Forge operations, publication,
archive/restore, and revision rollback are skill-backed. Real publication and
deletion remain behind explicit confirmation and runtime Action policy.
Starting autonomous implementation is available in the UI, but is not part of
the current functional smoke because it would introduce the second variable
that this phase is intended to exclude.

Prompt IDE compatibility is semantic rather than handler-for-handler. The
Builder Change timeline replaces Prompt IDE's private `git/log.json`; all other
Prompt IDE project operations map directly to public control tools:

| Prompt IDE capability | Builder SDK control surface |
| --- | --- |
| project list/select/create and templates | `list_projects`, `select_preview`, `create_project`, `list_templates` |
| project metadata, composition, files | `get_project`, `update_project_metadata`, `list_project_objects`, `list_project_file_tree`, `read_project_file`, `save_project_file` |
| base TZ and addenda | `get_prompt_context`, `save_prompt_context`, `append_prompt_addendum` |
| LLM and workflow state | `get_llm_options`, `set_llm_profile`, `set_workflow_state` |
| VCS log/checkpoint/publish/delete | `list_changes`, `push_project`, `publish_project`, `delete_project` |
| lifecycle, automation, archive, preview | `get_lifecycle`, `start_automation`, `submit_automation`, `get_automation`, `archive_project`, `select_preview`, `get_preview` |

Central-to-DEV update is intentionally absent from Builder. It needs an explicit divergence signal and a dedicated conflict-aware workflow before it can be exposed safely. The CLI/SDK update primitive remains available for controlled maintenance and replaces a DEV artifact transactionally with backup/rollback.

The three preview affordances intentionally use different browser/runtime
contracts over one binding: Compare calls `select_preview`, Open uses native
`openWorkspace` in a new window, and QR renders the `get_preview.qr_text` value
locally. Builder chat uses `conv.skill.builder_skill.default` plus the stable
`prompt-project:<kind>:<id>` thread; the transcript is ledger-backed and must
not live only in page state.

Localization is also a boundary contract. Scenario-owned static copy uses a
plain fallback field plus its semantic `*_i18n` sibling; `assets/i18n/en.json`
and `assets/i18n/ru.json` are published through `ui.application.resources`
with `role=i18n`. SDK presentation adapters return the same form for dynamic
project type, stage, synchronization, and lifecycle values. The browser
localizes both declarative widget configuration and data-source payloads, so a
skill does not branch its tool response by the current browser locale.

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
- [x] `[must]` Add bounded project metadata updates and
  `adaos.sdk.developer.prompt_context` for TZ, addenda, LLM, workflow, and
  archive state.
- [x] `[must]` Expose initialized project type and reject post-creation type
  changes in `adaos.sdk.developer.projects`.
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
- [x] `[must]` Restore the approved `029` three-pane prototype as the visual
  baseline and store the functional result as revisions `030` and `032`, while
  preserving user-generated revision `031` as immutable input evidence.
- [x] `[must]` Map every Prompt IDE project/TZ/LLM/VCS/workflow capability to a
  Builder control data source or action without importing Prompt IDE code.
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
- [x] `[must]` Resolve source/preview identity through explicit relations, use native new-window
  navigation, and render QR locally without a third-party QR endpoint.
- [x] `[must]` Restore chat from the canonical conversation/project thread and
  surface Automation terminal diagnostics and evidence paths without requiring
  raw projection inspection.
- [x] `[must]` Dispatch ordinary Builder chat to `builder_skill`, then let the
  selected project and workflow state route Automation through
  `adaos.sdk.builder.automation`; the generic HTTP transport no longer owns a
  Builder-specific service fallback.
- [x] `[must]` Keep Automation non-terminal while Forge checkpoints, DEV
  activation, and scenario materialization are finalizing. Clear stale
  readiness on a follow-up turn and fail the session before activation when
  any required checkpoint is unconfirmed.
- [x] `[must]` Run dependency-aware scenario validation and skill validation
  with handler probing before `dev skill|scenario push` and `publish`. Scenario
  validation resolves routes through declared DEV skill dependencies and uses
  `scenario.json` as the canonical Builder manifest when legacy YAML is also
  present.
- [x] `[must]` Exercise real Publication with an isolated companion skill and
  scenario. The workspace registry commits were pushed to `origin/main`; the
  scenario scaffold now reuses the shared workspace repository instead of
  creating `scenarios/scenarios` state.
- [x] `[must]` Resolve an installed Codex CLI for the server worker from
  explicit configuration, PATH, or the current VS Code extension bundle.
- [x] `[must]` Publish complete `ru`/`en` Builder dictionaries, add WebUI ABI
  support for localized action-button labels, and localize dynamic list/tree
  data returned by SDK adapters.
- [ ] `[deferred]` Recreate the control skill from zero by AdaOS autonomous
  programming without importing Prompt IDE implementation code.
- [x] `[must]` Keep legacy Prompt IDE active until the recreated skill passes
  the same checks.
- [x] `[should]` Add the control fixture to a repeatable local smoke command.
- [ ] `[must]` Make the Root scenario-draft endpoint return a durable Forge
  commit acknowledgement instead of an nginx `504`. Archive read-back proves
  that files arrived but is not a Git acceptance gate: commit metadata can
  remain stale after the archive changes. Automation must stay failed until a
  current commit and task metadata are confirmed.
- [x] `[should]` Retry the exact same archive once after transient Forge
  `500/502/503/504` failures without applying a second version bump, and reject
  success responses that omit a commit or return stale task metadata.
- [ ] `[should]` Preserve widget identity on no-op semantic reloads instead of
  replacing coarse `ui.application` and desktop/catalog/webio branches. The
  available branch-diff path handled the first changed reload, but the next
  unchanged reload still fell back to replacement; add fingerprint-convergence
  coverage and a browser reconnect/reload soak fixture.
- [x] `[could]` Add a golden structural fixture that compares functional
  revisions with `029` and rejects layout, widget-type, area, and modal loss.
- [ ] `[could]` Add governed open/copy controls for Automation evidence files.

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
loads from DEV, materializes the approved `029` structure as revision `032`,
resolves its declared skills, exposes the full Prompt IDE capability surface,
reads and saves bounded files, records evidence, exposes idle automation state,
and keeps release/destructive actions governed.

Prompt IDE can be retired only when:

1. the replacement Builder scenario declares all runtime skill dependencies;
2. its control/recreated skill has no `adaos.services` imports;
3. SDK contract tests and control-skill tests pass locally;
4. project selection, bounded file editing, preview, automation state, and
   checkpoint evidence work through SDK operations; and
5. legacy Prompt IDE remains a tested rollback path until the replacement has
   completed an autonomous from-zero implementation run.

Criteria 1-3 are satisfied by the control slice. Criterion 4 is satisfied for
the skill checkpoint and workspace Publication path, but remains open for the
Root scenario-draft commit acknowledgement. Criterion 5 remains intentionally
deferred; no legacy removal is authorized by this phase.

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
- synchronous materialization of `dev1-dev`, reporting `ready=true`,
  `current_scenario=builder`, revision `032`, 19 page widgets, and the original
  `left/center/right` split;
- live HTTP execution of all 12 read routes used by the interface, including
  project composition, nested file tree, TZ state, lifecycle, LLM options,
  templates, Automation idle state, Builder Changes, and preview binding;
- focused scenario/control/SDK and browser tests, including the `029`
  structural golden fixture, complete Prompt IDE capability mapping, immutable
  project type, canonical preview identity, local QR rendering, executable
  discovery, and render-safe Automation diagnostics.

The repeatable isolated smoke uses a dedicated source webspace so it does not
change the operator's Prompt IDE selection. On PowerShell:

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

The historical control fixture was `builder_sdk_control_skill 0.1.13`; the
corresponding functional Builder scenario archive was `0.2.9`. At that point
runtime verification used only the existing `dev1` / `dev1-dev` pair. The
current self-hosting contract intentionally permits one terminal
`dev1-dev-dev` project preview when `dev1-dev` is explicitly claimed by the
Builder scenario; ordinary projects still cannot create nested previews.

The isolated pipeline fixture is
`builder_pipeline_smoke_1784408500` / `_skill`. Ordinary
`builder_skill:chat` produced successive DEV versions `0.2.1`, `0.2.2`, and
`0.2.3`; the last tool result is `{ok: true, marker: "verified"}` and its five
focused tests pass. Follow-up submission clears the previous readiness, and
the projection remains `commit_ready` during finalization instead of briefly
reporting a stale `completed` state.

Real workspace Publication was also verified. The skill published as `0.0.1`
and the scenario as `0.0.2`; workspace `origin/main` matched local commit
`b3aacca7efef7654c80016140e15969692a62601`, and `registry.json` records the
scenario's required companion skill from canonical `scenario.json`. The
legacy nested scenario checkout and lock produced by the first run were
removed after the shared-repository scaffold fix, and the repeated real
publication left the workspace clean.

Forge draft verification is deliberately stricter than archive parity. The
skill retry confirmed commit `5d4f1fc45faf864dbd00ca56a4a6652808c7df40`
for task `task.01KXVJMDV0AVAES3TAY17EC5FG`. The scenario archive contains
version `0.2.3`, but the endpoint repeatedly returned `504` and still reports
the older commit/task metadata. This is recorded as `forge_checkpoint` failure
and blocks a successful Automation terminal state until the Root service
contract is repaired.

Live materialization otherwise published both 144-entry dictionaries and
loaded them by content-addressed URL.

The next proof is intentionally outside this pass: recreate the control skill
from an empty DEV project through AdaOS autonomous programming, run the same
checks, and only then plan Prompt IDE removal.
