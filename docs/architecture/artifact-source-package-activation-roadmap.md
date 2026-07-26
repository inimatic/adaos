# Artifact Source, Package, and Activation Roadmap

Status: implementation roadmap for
[Artifact Source, Package, and Activation Architecture](artifact-source-package-activation.md).

Last reviewed: 2026-07-26.

## Outcome

AdaOS moves from path-copy publication and sparse Workspace installation to a
traceable pipeline:

```text
SourceRef
  -> immutable dependency-locked ProjectRelease
  -> candidate trial
  -> stable freshness gate
  -> channel promotion
  -> transactional Workspace activation
```

The active delivery goal is the single-user path. Collaborative extraction,
public feature proposals, editions, and multi-version runtime resolution are
recorded but explicitly deferred.

## Priority Vocabulary

- `[must]`: required for the single-user package pipeline acceptance proof.
- `[should]`: required before broad or unattended usage, but not required for
  the first bounded local proof.
- `[could]`: useful improvement that must not delay the proof gate.
- `[deferred]`: deliberately excluded from this refactoring and assigned to a
  later collaboration, distribution, or runtime milestone.

## Maturity Vocabulary

Tasks move through:

```text
hypothesis -> specified -> implemented -> integrated
  -> validated-local -> validated-stand -> production-accepted
```

The checklist reports delivery state, not only code presence. A checked task
is implemented and has at least `validated-local` evidence; it does not imply
stand or production acceptance. An unchecked task is still open, including
`should`, `could`, and deliberately deferred work. A task is not
`validated-local` without a reproducible test or operation record tied to an
exact revision.

## Guardrails

1. Do not edit installed Workspace content as development source.
2. Do not activate a package that is not identified by digest.
3. Do not resolve runtime dependencies through an implicit `latest` rule.
4. Do not partially activate an incompatible project release.
5. Do not move a stable channel before source, package, and validation records
   are durable.
6. Do not automatically repeat an unknown state-changing phase.
7. Do not report deferred collaboration or edition support as implemented.
8. Preserve registry v1 read compatibility until representative package-backed
   installs pass local and stand validation.

## Current Baseline

Current useful seams:

- Git skill and scenario repository adapters already isolate most sparse
  workspace access.
- Builder realization requests already carry `base_branch` and `sparse_paths`.
- Registry helpers already produce deterministic local catalog records.
- Durable operations already model accepted, running, failed, recoverable, and
  terminal work without blindly retrying unknown side effects.
- Scenario and skill publish flows already have DEV, validation, push, and
  Workspace projection concepts.

Validated-local implementation now provides:

- explicit source, package, release, candidate, subscription, and WorkspaceLock
  identities;
- deterministic, content-addressed scenario and skill packages;
- exact dependency locking for a scenario and companion skills;
- package-only trial, stable, rollback, and subscription activation;
- accepted-trial, exact-base freshness, public-source-tree, and stable-channel
  promotion gates;
- a bounded stale-candidate reapplication plan that requires new validation and
  trial;
- a durable Forge checkpoint intent/receipt journal that never automatically
  repeats an unknown state-changing write;
- a unique Builder change identity per Automation iteration and an explicit,
  checkpoint-only recovery path that refuses partially committed artifact sets;
- content-addressed Builder task inputs with compare-and-switch DEV result
  activation, transactional backup, and rollback;
- explicit permission, migration, checkpoint, health-verify, and commit phases,
  with fail-closed permission admission and one-shot migration reconciliation.
- fail-closed v1 contract readers, portable package-path and secret admission,
  immutable project-version identities, and local/backend channel
  compare-and-swap with idempotent same-target retry.

The reproducible result and exact digests are recorded in
[Artifact Pipeline Local Evidence — 2026-07-24](artifact-pipeline-local-evidence-2026-07-24.md).

The subsequent
[Artifact Pipeline Critical Audit — 2026-07-26](artifact-pipeline-critical-audit-2026-07-26.md)
confirmed that the bounded proof did not cover version collisions, future
schema rejection, portable archive aliases, complete-set dependency
re-resolution, orphan removal, concurrent Workspace/channel writers, or
mandatory live health evidence. Affected tasks below were reopened; the proof
record remains valid for its narrower stated cases.

Remaining acceptance blockers:

- close the correctness blockers recorded by the critical audit before routing
  legacy update entrypoints through the package pipeline;
- a live stand/second-machine run is required before package-only activation is
  the default or legacy sparse Workspace compatibility is retired;
- delayed post-activation observation remains a `[should]` operational gate.

The backend route slice is no longer a blocker: PR
[inimatic/adaos-backend#1](https://github.com/inimatic/adaos-backend/pull/1)
was merged at `1329ecb` and deployed as `0.1.137`; live Forge tree lookups match
the locally persisted checkpoint trees. Builder itself subsequently completed
DEV `0.2.20` → isolated trial → accepted Workspace publication with companion
skill `0.1.28`.

## Delivery Snapshot

This table records the highest maturity reached by the current implementation.
Detailed task checkboxes are closed independently when their stated scope has
local evidence. Milestone exit gates and maturity remain separate, so a local
proof is not silently promoted to stand or production acceptance.

| Milestone | Closed | Maturity | Validated task slices | Remaining broader gates |
| --- | ---: | --- | --- | --- |
| AP0 | 6/9 | validated-local (bounded) | identities, fail-closed schemas, canonical digests, immutable version identity, SourceProvider, registry v2 compatibility | historical migration fixtures and identity diagnostics |
| AP1 | 5/9 | validated-local (bounded) | deterministic package build/store/verify, secret and authoring-state exclusion, portable path admission, corruption and zero-byte coverage | builder attestation identity and external signing |
| AP2 | 5/10 | validated-local (bounded) | exact dependency ranges/digests, reverse consumers, conflict/cycle rejection | complete-set re-resolution plus broader schema-component and migration-lock inputs |
| AP3 | 4/11 | validated-local (bounded) | phase journal, permission admission, reversible migration/reconciliation, interruption recovery, and rollback injection | writer lease/CAS, orphan removal, mandatory reload/health policy, delayed observation, and stand validation |
| AP4 | 6/10 | validated-local (bounded) | exact candidate identity, isolated package materialization, acceptance record, immutable Builder task snapshot, concurrent-DEV compare-and-switch | enforced data isolation and health/rollback trial evidence; stand validation |
| AP5 | 6/10 | validated-local + production-route-verified (bounded) | freshness/stale/rebase flow, renewed trial, Forge tree lookup, and local/backend atomic channel CAS | merge/deploy backend hardening, durable promotion continuation, and clean stand promotion |
| AP6 | 6/10 | validated-local | stable subscription discovery, notify/pinned policy, package update, rollback, post-success observation | legacy update-entrypoint cutover and Builder/operator update-plan UI |
| AP7 | 11/13 | validated-local | representative LLM/Codex scenario+skill, 21 bounded resilience tests, 161 focused regressions, and live Builder `0.2.20` publication with explicit checkpoint recovery | clean stand run; production and marketplace acceptance remain open |

## Milestone AP0: Contracts And Compatibility Boundary

**Outcome:** new identities and service boundaries exist without changing
current user-visible behavior.

**Exit proof:** legacy artifacts can be described by SourceRef, PackageRef,
ProjectRelease, and WorkspaceLock-compatible records while existing tests
remain green.

- [x] `[must]` `AP0-01` Add versioned schemas and typed models for `ProjectRef`,
  `SourceRef`, `PackageRef`, `ProjectRelease`, `WorkspaceLock`, and stable
  `Subscription`.
- [x] `[must]` `AP0-02` Define canonical digest serialization and reject one
  version identity mapping to different content.
- [x] `[must]` `AP0-03` Make canonical manifest `id` the dependency identity;
  keep display names and registry aliases outside dependency locks.
- [x] `[must]` `AP0-04` Add forge-independent `SourceProvider` and
  `RevisionSelector` ports.
- [x] `[must]` `AP0-05` Add additive registry v2 fields for source, channels,
  release digest, and package references while preserving registry v1 reads.
- [x] `[must]` `AP0-06` Add legacy adapters that synthesize the new identities
  for current path-backed artifacts.
- [ ] `[should]` `AP0-07` Add schema migration fixtures for historical registry
  entries and manifests with incomplete version metadata.
- [ ] `[could]` `AP0-08` Add a diagnostic command that explains resolved source,
  package, release, and activation identities.
- [ ] `[deferred]` `AP0-09` Add publisher namespaces and ownership-transfer
  records after the single-user identity contract is stable.

Checked scope evidence: [local pipeline proof](artifact-pipeline-local-evidence-2026-07-24.md),
release contracts in `tests/test_artifact_release_contracts.py`, and registry
v1/v2 compatibility in `tests/test_workspace_registry.py`.
`AP0-02` was reclosed after local and backend regressions proved that an
idempotent version repeat is accepted and the same project/version with a
different release digest is rejected before visibility.

## Milestone AP1: Deterministic Immutable Packages

**Outcome:** scenario and skill content can be built once and addressed by
digest independently of Workspace and Git checkout layout.

**Admission gate:** AP0 contracts are implemented.

**Exit proof:** two builds from the same source produce the same digest, changed
content produces a different digest, and package extraction passes traversal,
symlink, size, and corruption tests.

- [x] `[must]` `AP1-01` Implement `PackageBuilder` for scenario and skill
  component packages.
- [x] `[must]` `AP1-02` Normalize package paths, timestamps, permissions, and
  manifest serialization for deterministic output.
- [x] `[must]` `AP1-03` Implement a content-addressed local package store with
  atomic put, verify, get, and quarantine operations.
- [x] `[must]` `AP1-04` Exclude DEV metadata, secrets, runtime data, caches, and
  unrelated sparse paths from package inputs.
- [x] `[must]` `AP1-05` Validate archive path traversal, links, portable path
  aliases and case collisions, file count, decompressed size, and manifest
  digest before visibility.
- [ ] `[must]` `AP1-06` Persist source revision, builder identity, package
  manifest digest, and validation evidence references.
- [ ] `[should]` `AP1-07` Add signed attestations and external immutable release
  asset support behind a package-store adapter.
- [ ] `[could]` `AP1-08` Add package deduplication and bounded garbage collection
  diagnostics.
- [ ] `[deferred]` `AP1-09` Add commercial license and entitlement payloads to
  package admission policy.

Checked scope evidence: [local pipeline proof](artifact-pipeline-local-evidence-2026-07-24.md)
and package store regressions in `tests/test_artifact_package_store.py`.
`AP1-04` and `AP1-05` were reclosed after local and backend regressions covered
credential/private-key scrub, Windows reserved names and alternate data
streams, trailing-dot/space aliases, Unicode normalization, and case-fold
collisions before package visibility.
`AP1-06` remains open because a durable builder/attestation identity is not yet
part of the package contract.

## Milestone AP2: Dependency-Locked Project Releases

**Outcome:** activation uses one exact, compatible component set and never
selects a dependency implicitly.

**Admission gate:** AP1 component packages are reproducible.

**Exit proof:** a scenario and shared skill form a locked ProjectRelease;
compatible activation succeeds and an incompatible shared-skill update is
rejected without changing active state.

- [x] `[must]` `AP2-01` Add declared dependency ranges to canonical scenario and
  skill manifest handling.
- [x] `[must]` `AP2-02` Resolve declared dependencies into exact versions and
  package digests during ProjectRelease build.
- [ ] `[must]` `AP2-03` Store component, permission, schema, migration, and
  validation locks in ProjectRelease.
- [x] `[must]` `AP2-04` Implement the MVP rule of one active package per canonical
  skill id in one node activation context.
- [x] `[must]` `AP2-05` Compute reverse consumers before changing a shared skill
  binding.
- [ ] `[must]` `AP2-06` Reject missing, ambiguous, incompatible, cyclic, or
  internally inconsistent dependency results with an explainable plan and no
  partial mutation.
- [x] `[must]` `AP2-07` Treat every dependency-lock change as a new release
  digest even when component source files are unchanged.
- [ ] `[should]` `AP2-08` Add lock explain and dependency graph diagnostics for
  Builder and operator UI.
- [ ] `[could]` `AP2-09` Cache compatible dependency plans by release digest.
- [ ] `[deferred]` `AP2-10` Add context-aware simultaneous active versions and a
  general-purpose dependency solver.

Checked scope evidence: [local pipeline proof](artifact-pipeline-local-evidence-2026-07-24.md)
and dependency resolver regressions in
`tests/test_artifact_release_resolver.py`.
`AP2-03` remains open for the broader explicit schema and migration-lock
contract, beyond the component, permission, migration, and validation fields
already present in the bounded release model. `AP2-06` was reopened because a
compatible multi-consumer constraint set can currently produce inconsistent
binding digests.

## Milestone AP3: Transactional Workspace Activation

**Outcome:** Workspace is materialized from packages through one durable lock
transition with health verification and rollback.

**Admission gate:** AP2 produces a complete ProjectRelease plan.

**Exit proof:** an empty Workspace installs from packages only, an update
switches the lock atomically, and injected failures before and after lock switch
leave either the old or the new complete release active.

- [ ] `[must]` `AP3-01` Implement durable WorkspaceLock storage with a
  workspace-wide writer lease, compare-and-switch revision, and previous-lock
  linkage.
- [ ] `[must]` `AP3-02` Implement activation phases: resolve, fetch, verify,
  dependency plan, permission plan, migration plan, stage, checkpoint,
  switch-lock, reload, health verify, and commit; reload and health must record
  either a durable receipt or an explicit policy-approved skip.
- [ ] `[must]` `AP3-03` Make filesystem materialization and runtime bindings
  projections of WorkspaceLock, including transactional removal of components
  no longer reachable from any active slot.
- [x] `[must]` `AP3-04` Add idempotency keys and durable phase evidence to the
  existing operation manager.
- [x] `[must]` `AP3-05` Roll back lock, materialized content, and runtime binding
  after post-switch failure when the migration contract permits rollback.
- [x] `[must]` `AP3-06` Never automatically replay an interrupted phase with an
  unknown side effect.
- [x] `[must]` `AP3-07` Add transactional backup and restore coverage for current
  workspace update paths.
- [ ] `[should]` `AP3-08` Add delayed post-activation verification linked to the
  exact WorkspaceLock revision.
- [ ] `[should]` `AP3-09` Add orphan staging cleanup and package retention policy.
- [ ] `[could]` `AP3-10` Add operator diff output between active and proposed
  locks.
- [ ] `[deferred]` `AP3-11` Add unattended activation of irreversible migrations.

Checked scope evidence: [local pipeline proof](artifact-pipeline-local-evidence-2026-07-24.md)
and Workspace activation regressions in
`tests/test_artifact_workspace_activation.py`.
`AP3-01` through `AP3-03` were reopened for writer serialization, explicit
reload/health policy, and transactional removal of unreachable components.

## Milestone AP4: Exact-Base DEV Candidate And Trial

**Outcome:** Builder develops from the installed stable identity, produces an
immutable candidate, and trials it without modifying primary stable activation.

**Admission gate:** AP3 can activate and roll back packages.

**Exit proof:** Builder creates a candidate from an exact stable release,
activates it in a trial slot, preserves primary stable activation, and records
acceptance or rollback evidence.

- [x] `[must]` `AP4-01` Resolve installed stable release to exact SourceRef and
  ProjectRelease before DEV creation.
- [x] `[must]` `AP4-02` Replace the shared mutable DEV branch context with a
  task/change-scoped worktree or isolated clone.
- [x] `[must]` `AP4-03` Keep sparse paths as an optional task envelope inside the
  isolated DEV context.
- [x] `[must]` `AP4-04` Build candidate version, source revision, base release,
  and package digest from the DEV result.
- [ ] `[must]` `AP4-05` Add a trial WorkspaceLock slot with explicit audience and
  data mode.
- [x] `[must]` `AP4-06` Protect primary activation and real data from an
  incompatible candidate.
- [ ] `[must]` `AP4-07` Record deterministic validation, trial duration,
  acceptance, health, and rollback evidence against candidate digest.
- [x] `[should]` `AP4-08` Add clear Builder and runtime labels for stable versus
  trial activation.
- [ ] `[could]` `AP4-09` Reuse unchanged low-risk evidence after a policy-proven
  no-op rebuild.
- [ ] `[deferred]` `AP4-10` Extract multiple-user WorkLogs into federated
  ChangeSets and public alpha proposals.

Checked scope evidence: [local pipeline proof](artifact-pipeline-local-evidence-2026-07-24.md),
candidate publication regressions in
`tests/test_artifact_publication_service.py`, and preview identity regressions
in `tests/test_webspace_phase2.py`.
`AP4-05` and `AP4-07` were reopened because data mode is not enforced by the
trial lock and live runtime health evidence is optional.

## Milestone AP5: Freshness Gate And Stable Promotion

**Outcome:** an accepted candidate becomes stable only when its exact base is
current; otherwise it returns through DEV migration and trial.

**Admission gate:** AP4 candidate trial is validated locally.

**Exit proof:** unchanged-base promotion advances stable to the accepted digest;
a moved-base candidate is rejected as stale, rebuilt on the new base, retrialed,
and then promoted.

- [x] `[must]` `AP5-01` Atomically compare candidate base version, source
  revision, and release digest with current stable when moving the channel.
- [x] `[must]` `AP5-02` Mark moved-base candidates stale without changing source,
  package, or channel state.
- [x] `[must]` `AP5-03` Recreate DEV on the new base and reapply the bounded
  project change inside its path and dependency envelope.
- [x] `[must]` `AP5-04` Require a new digest, deterministic validation, and trial
  after any material rebase result.
- [x] `[must]` `AP5-05` Persist public source identity and verify its tree before
  stable promotion.
- [x] `[must]` `AP5-06` Persist source, package, release, and evidence before
  moving the stable channel pointer last.
- [ ] `[must]` `AP5-07` Make publication idempotent and recover partial completion
  without duplicate commits, releases, or registry entries.
- [ ] `[should]` `AP5-08` Add policy classes for documentation-only and
  deterministic metadata rebases.
- [ ] `[could]` `AP5-09` Add a merge-queue adapter after the single-writer flow is
  proven.
- [ ] `[deferred]` `AP5-10` Add zone-specific multi-user approvals and semantic
  merge arbitration.

Checked scope evidence: [local pipeline proof](artifact-pipeline-local-evidence-2026-07-24.md)
and candidate publication regressions in
`tests/test_artifact_publication_service.py`, including the deployed Forge tree
verification recorded in the proof.
`AP5-01` was reclosed after the promotion path began passing the exact observed
base digest into an authoritative local/backend channel compare-and-swap. The
same-target retry is idempotent; a stale expected digest preserves the observed
channel and returns a conflict. `AP5-07` remains open for durable continuation
after channel movement and before publisher activation/projection completes.

## Milestone AP6: Stable Subscription And Package Update

**Outcome:** registry stable-version changes are discovered as subscriptions
and applied through package-backed transactional activation.

**Admission gate:** AP5 creates valid stable channel records.

**Exit proof:** an installed stable subscriber detects a new stable release,
shows the plan, updates from packages, verifies health, and rolls back after an
injected failure.

- [x] `[must]` `AP6-01` Create a stable subscription record when a public project
  is installed.
- [x] `[must]` `AP6-02` Support `notify` and `pinned` policies without enabling
  unattended stateful updates.
- [x] `[must]` `AP6-03` Detect channel movement by release identity and digest,
  not version string alone.
- [ ] `[must]` `AP6-04` Route current scenario and skill update entrypoints
  through ProjectRelease planning and transactional activation.
- [x] `[must]` `AP6-05` Preserve active release after fetch, dependency,
  permission, migration, reload, or health-check failure.
- [x] `[must]` `AP6-06` Update subscription observation only after successful
  activation.
- [x] `[must]` `AP6-07` Keep registry v1 source materialization as a bounded
  compatibility fallback during migration.
- [ ] `[should]` `AP6-08` Add explicit update-plan UI with dependencies,
  permissions, migrations, and rollback availability.
- [ ] `[could]` `AP6-09` Add `auto-compatible` for stateless, policy-approved
  releases after stand evidence.
- [ ] `[deferred]` `AP6-10` Add public candidate subscriptions and recommendation
  ranking.

Checked scope evidence: [local pipeline proof](artifact-pipeline-local-evidence-2026-07-24.md)
and channel/subscription regressions in
`tests/test_artifact_channels_subscriptions.py`.
`AP6-04` remains open: the package-backed subscription path is validated, but
all legacy scenario/skill update entrypoints have not yet been cut over to it.

## Milestone AP7: End-To-End Proof And Legacy Retirement Decision

**Outcome:** the representative single-user pipeline is reproducible on this
machine and produces durable proof for broader documentation updates.

**Admission gate:** AP0 through AP6 must items are integrated.

**Exit proof:** one representative scenario and shared skill pass the complete
source-to-trial-to-stable-to-subscriber update pipeline, including stale-base,
dependency-conflict, interruption, and rollback cases.

- [x] `[must]` `AP7-01` Select and record the representative scenario, shared
  skill, initial stable release, and source revision.
- [x] `[must]` `AP7-02` Install an empty Workspace from packages only.
- [x] `[must]` `AP7-03` Create, validate, and trial a user-requested candidate
  through Builder and the built-in implementation model.
- [x] `[must]` `AP7-04` Exercise unchanged-base stable promotion of the accepted
  digest.
- [x] `[must]` `AP7-05` Exercise moved-base migration, rebuilt candidate, renewed
  trial, and promotion.
- [x] `[must]` `AP7-06` Exercise compatible shared-skill update and incompatible
  dependency rejection.
- [x] `[must]` `AP7-07` Exercise interruption at every activation phase and prove
  no unknown side effect is replayed.
- [x] `[must]` `AP7-08` Exercise stable subscription discovery, update, health
  verification, and rollback.
- [x] `[must]` `AP7-09` Record commands, commits, package digests, WorkspaceLock
  revisions, operation ids, validation results, and acceptance decision.
- [x] `[must]` `AP7-10` Update Builder, registry, operations, runtime, and governed
  evolution documentation only from the recorded implementation evidence.
- [ ] `[should]` `AP7-11` Repeat the proof on a clean stand or second machine
  before defaulting new installs to package-only mode.
- [x] `[could]` `AP7-12` Compare local package storage with an external immutable
  release backend.
- [ ] `[deferred]` `AP7-13` Claim production acceptance or marketplace readiness
  from the single-machine proof.

Checked scope evidence: [local pipeline proof](artifact-pipeline-local-evidence-2026-07-24.md),
including its reproducible verifier command, immutable digests, operation
records, regression counts, backend deployment, and live Builder publication.

## Explicit Deferred Backlog

These items are architectural directions, not hidden requirements of the
active refactoring:

- [ ] `[deferred]` `APD-01` Multi-user WorkLog extraction and issue grouping.
- [ ] `[deferred]` `APD-02` Trusted development groups, delegated capability
  ownership, and proposal feeds.
- [ ] `[deferred]` `APD-03` Public alpha candidate discovery and cross-user trial
  evidence aggregation.
- [ ] `[deferred]` `APD-04` Multiple simultaneously active versions of one skill
  id.
- [ ] `[deferred]` `APD-05` Paid, free, organization, and personalized editions.
- [ ] `[deferred]` `APD-06` Commercial entitlement, licensing, and billing.
- [ ] `[deferred]` `APD-07` General dependency solving across independently
  released project graphs.
- [ ] `[deferred]` `APD-08` Automatic semantic merge and risk-weighted evidence
  transfer after rebase.
- [ ] `[deferred]` `APD-09` Repository-per-public-project migration; SourceRef
  must make it configuration-only when admitted.
- [ ] `[deferred]` `APD-10` Fully unattended stateful updates and irreversible
  migrations.

## Commit And Evidence Policy

- Commit each completed coherent milestone slice independently.
- A documentation-only architecture baseline is committed and tagged before
  implementation begins.
- Backend repositories may be published when a completed slice requires them.
- Client and main AdaOS publication occurs only after the local pipeline proof.
- Never include unrelated pre-existing worktree or submodule changes.
- Every roadmap checkbox changed to complete must link to a test, operation
  record, commit, or reproducible command.
- Failed experiments remain visible in evidence when they change an
  architectural decision.

## Documentation Synchronization Gate

Until AP7 evidence exists, this roadmap and its target architecture are the
only documents updated for the new pipeline. After the proof:

1. update Builder architecture and roadmap;
2. update registry/marketplace and operation roadmaps;
3. update runtime activation and dependency documentation;
4. update Governed Evolution milestone maturity;
5. update roadmap inventory and translated summaries;
6. record legacy sparse Workspace retirement or the remaining compatibility
   window.
