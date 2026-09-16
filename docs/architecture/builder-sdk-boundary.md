# Builder SDK Boundary And Migration Roadmap

Status: public SDK contract with partially completed migration. A working
control surface and SDK-only imports do not establish complete Prototype
execution ownership. BIP-07 owns that transfer; this checklist owns the
remaining SDK compatibility and migration obligations.

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

An SDK-only import guard proves a dependency restriction, not transfer of
orchestration or persistence authority. In the audited implementation,
`adaos.sdk.builder.prototype` still delegates to a legacy skill execution port;
that temporary adapter is not the target dependency direction below.

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

`adaos.sdk.developer.projects` owns DEV discovery and lifecycle. Its public
contract includes Project composition without exposing path-provider internals:

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
- materialize digest-bound audience views for artifact groups so hidden files
  are absent from an agent's filesystem root, not merely omitted from prompt
  text;
- retain both the source-manifest digest and filtered context-view digest in
  the Development Session.

### Builder preview

`adaos.sdk.builder.preview` owns workbench selection and preview lifecycle:

- select the active Project/component and resolve its explicit presentation or
  generic system skill-preview fallback;
- ensure or open its dev webspace;
- return one canonical navigation destination shared by Open Preview and QR;
- read the current source-to-dev-webspace binding;
- reload or materialize a validated Builder revision.

Source identity is resolved through the persisted Builder preview relation.
SDK consumers obtain Preview IDs from the topology service and never append or
remove `-dev` themselves. The owner allocates `W-dev`, or `W-dev-dev` for the
single DEV Builder self-host level, only under a registered production host.
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

Target verification operations expose the existing trusted runner to the admitted
candidate: inspect scoped changes, select checks, observe/cancel an owned check
and retrieve typed evidence or permitted browser observations. Use the same
validators, task identity and receipts as final verification, with separate
authority to select mandatory gates. A caller cannot nominate another project's
runtime or extend network/filesystem access. Route and ABI implementation is owned
by [BIP-13](builder-intent-to-prototype-roadmap.md#bip-13) and
[BIP-21](builder-intent-to-prototype-roadmap.md#bip-21), not a second runner in the
skill. This is a target surface, not a claim that callable tools already exist.

### Builder artifacts and conversation evidence

`adaos.sdk.builder.artifacts` owns artifact checkpoints. Conversation SDK
operations own Builder topic creation and Builder Change lookup/upsert. Pending
Actions and event publication use their existing SDK surfaces.

After each accepted Prototype and accepted Automation, the workflow must obtain
the service equivalent of `adaos dev project push`, without `--local-only`, for
that exact DEV Project composition. This private development checkpoint is not
Trial publication, Workspace installation or public registry publication. It
does not require pushing the AdaOS or Client source repositories or triggering
their CI merely to preserve an application revision.

Keep three explicit evidence classes: local revision/hash receipt, remote component
commit, and complete remote ProjectRelease. Only the last closes the accepted
project's VCS obligation. Retain Project/Change/stage/accepted revision, owned-member
source digests, composition/package digest, version and durable remote identity.
Package source, tests, schemas, locales and source-owned assets; never runtime
databases, user settings values, secrets or temporary verification files.

Checkpoint the immutable accepted snapshot, not whatever is latest when upload
finishes. Reuse a matching confirmed full-project receipt idempotently. On failure,
retain the human decision and local source but expose checkpoint pending/failed
and block dependent acceptance completion or promotion. Recovery retries the same
operation without another model call, duplicate version bump or duplicate upload;
offline/local-only work must not be labelled remotely persisted.

Current Prototype local checkpoints, Automation component pushes and composition
version reservation do not implement this complete acceptance hook. Qualification
belongs to SDK-04 below; [BIP-23](builder-intent-to-prototype-roadmap.md#bip-23)
owns related retention, not a weaker alternative durability gate.

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

## Control Surface Contract

Builder's control skill adapts SDK operations for project discovery/composition,
file/specification editing, exact preview, governed changes, Automation,
checkpoints and release decisions. Ordinary conversation remains project-scoped.
No static lifecycle mocks or private Prompt IDE calls may replace these tools.

Functional parity is expressed by
[builder-functional-parity.json](builder-functional-parity.json) and its tests.
It is a product-specific compatibility fixture, not generic Prototype context
or a universal layout prescription. Preserve the current approved artifact
rather than impose the geometry of a historic control revision.

Source-to-preview identity comes from the topology SDK. Open Preview and QR
share one canonical destination; they may not allocate case-specific hosts.
Source/metadata reads must not silently start Automation or move approval state.

Scenario-owned UI copy uses fallback text plus semantic i18n keys and
scenario-owned dictionaries. Dynamic projections use the same localizable
shape. Builder's own EN/RU support is separate from the generated application's
current-locale or explicitly multilingual contract.

## Code Granularity

Move state and authority to their owning Core services before splitting files.
An SDK-only import guard does not prove a thin skill while provider handling,
artifact writes and orchestration remain inside it. Complete that work through
BIP-07 with dependency/parity tests. Unrelated large-module refactors remain
deferred; do not make them a prerequisite for a bounded SDK correction.

## Roadmap And Checklist

Completed migration boundary: public project/context/preview/Automation/review
surfaces, bounded file operations, SDK-only checks for migrated skills,
manifest dependency validation and exact activation/source receipts exist.
This is not full consumer migration, current remote health, human acceptance,
or general from-zero Builder autonomy.

- [ ] `[should]` **SDK-01** Publish a version/compatibility marker consumable by
  skill manifests; qualify rejection of incompatible contracts.
- [ ] `[should]` **SDK-02** Replace public Root developer class re-export with
  a real facade while preserving a migration alias.
- [ ] `[should]` **SDK-03** Move remaining Prompt IDE DEV file lifecycle behind
  bounded developer-project SDK operations.
- [ ] `[must]` **SDK-04** Qualify durable scenario checkpoint acknowledgement,
  exact commit/task/source identity, bounded retry and recovery after transient
  failure. Changed archive bytes are not acknowledgement; finalization must
  fail on stale/unconfirmed metadata. Share evidence with BIP-23, not another
  checkpoint implementation.
- [ ] `[must]` **SDK-04 acceptance hook** Obtain a full remote DEV ProjectRelease
  after each Prototype and Automation acceptance, using existing project-push
  services. Verify all owned members and distinguish local/component/project
  receipts in Builder. Cover duplicate approval, stale source, disconnect after
  remote commit, restart/resume and failure without loss of the accepted snapshot.
  A pending receipt must not become a successful transition or trigger regeneration.
- [ ] `[should]` **SDK-05** Preserve widget identity on no-op semantic reloads;
  prove fingerprint convergence and browser reconnect/reload soak behavior.
- [ ] `[should]` **SDK-06** Make `runtime.sdk_only` the default in new skill
  templates after migration; keep explicit compatibility for existing consumers.
- [ ] `[should]` **SDK-07** Remove compatibility aliases only after all supported
  consumers migrate and rollback is qualified.
- [ ] `[could]` **SDK-08** Expose a bounded boundary audit in operator diagnostics.
- [ ] `[could]` **SDK-09** Migrate unrelated legacy skills after this boundary is
  stable, using explicit per-consumer compatibility evidence.
- [ ] `[could]` **SDK-10** Extend SDK-only enforcement to workspace skills after
  consumer migration, without silently breaking installed applications.

Governed evidence open/copy controls are owned once in
[Builder Phase 8](builder-roadmap.md#phase-8-product-experience). From-zero
control-skill recreation and Prompt IDE removal remain deferred there; neither
is authorized by SDK-only import success.

## Exit Criteria

For the supported migration scope:

1. All runtime skill dependencies and public errors are declared.
2. Skills use public SDK contracts; Core owns state/transaction/execution
   authority, not merely service imports hidden behind a compatibility adapter.
3. Contract/dependency tests, exact runtime identity and functional parity pass.
4. Bounded file operations, preview, conversation, Automation and durable
   checkpoints work through SDK routes with truthful failure/recovery.
5. Human/installed acceptance and rollback are complete for any claimed rollout.

Implementation and delivery status are owned by the checklists, not a dated
verification report. Measurements and fault evidence belong only in the
[engineering journal](builder-engineering-journal.md).
