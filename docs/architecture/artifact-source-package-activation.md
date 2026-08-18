# Artifact Source, Package, and Activation Architecture

Status: target architecture for the single-user artifact development,
publication, installation, update, and rollback pipeline.

Last reviewed: 2026-08-18.

This document defines the target boundaries and contracts. The bounded package
path is now `validated-stand` on an isolated same-host stand using the deployed
external backend; it is not yet broadly production accepted. Delivery maturity is owned by
[Artifact Source, Package, and Activation Roadmap](artifact-source-package-activation-roadmap.md),
and the exact local/stand result is recorded in
[Artifact Pipeline Local Evidence — 2026-07-24](artifact-pipeline-local-evidence-2026-07-24.md).

## Purpose

AdaOS currently uses a shared Git workspace, sparse checkout, DEV copies, and
path-scoped publication to develop and distribute skills and scenarios. That
model is useful for the current local workflow, but it mixes four different
concerns:

- public source history;
- mutable development state;
- published installable content;
- activated runtime state.

The target architecture separates them:

```text
identified source revision
  -> deterministic immutable package
  -> registry channel
  -> transactional workspace activation
```

A user-facing Builder change follows the reverse trace without editing an
installed workspace:

```text
activated release
  -> exact source revision
  -> isolated DEV context
  -> candidate package
  -> trial
  -> freshness gate
  -> stable promotion
```

The first implementation slice intentionally proves this loop for one user and
one canonical public source. Collaborative extraction, public candidate
exchange, multiple editions, and concurrent active dependency versions remain
deferred until the single-user loop is stable.

## Goals

- Make source, package, channel, and activation identities explicit.
- Build the same immutable content that is tested, promoted, installed, and
  rolled back.
- Stop treating a mutable Git checkout as an installed runtime artifact.
- Start every Builder task from an exact source and package base.
- Detect a moved stable base before publication.
- Make dependency resolution deterministic and visible.
- Update Workspace from packages through a transactional operation.
- Preserve the current registry monorepo and sparse checkout behind adapters
  while the new pipeline is introduced.
- Leave extension points for isolated project repositories, collaboration,
  editions, and multi-version runtime resolution.

## Non-goals For The First Slice

- Multi-user Issue extraction or proposal federation.
- Automatic semantic merging of unrelated changes.
- Running multiple active versions of the same canonical skill identity.
- Paid/free or organization-specific editions.
- A general-purpose dependency solver.
- Automatic unattended migration of stateful scenarios.
- Moving every public project to an independent repository.
- Making GitHub pull requests or releases AdaOS domain records.
- Treating usage count alone as stable-release approval.

## Core Vocabulary

| Term | Meaning |
| --- | --- |
| Project | Versioned declarative distribution definition containing a non-empty set of owned components, dependencies, entry points, catalog metadata, and lifecycle policy. |
| Component | A scenario, skill, schema, migration, UI descriptor, governed `workflow.json`, or other versioned item included in a project release. |
| SourceRef | Exact forge-independent reference to source content. |
| PackageRef | Content-addressed reference to one immutable package. |
| ProjectRelease | Immutable, dependency-locked Project definition and component-package set. |
| Channel | Mutable discovery pointer to an immutable ProjectRelease. |
| Candidate | Pre-release ProjectRelease linked to one bounded change and base release. |
| Activation | Transactional selection of ProjectRelease packages for a workspace slot. |
| WorkspaceLock | Authoritative record of the packages and dependency bindings currently selected for a workspace. |
| DEV context | Mutable, isolated worktree or checkout created from a SourceRef. |
| Builder Development Session | Mutable policy overlay that selects development targets and read-only context for one Project iteration; it is not distributed. |
| Trial | Reversible candidate activation for an explicitly bounded audience and data policy. |

Project composition, presentation resolution, local model-facing artifact
groups, and Builder context policy are defined by
[Project Composition, Presentation, and Development Context](project-composition-and-development-context.md).
This page owns how that definition becomes immutable packages, ProjectRelease,
WorkspaceLock, Trial, activation, and rollback.

## Architectural Planes

### 1. Public Source

Public source contains canonical history, change branches, validation inputs,
and release tags. The current `adaos-registry` monorepo remains a valid source
provider during the migration.

A source repository is not an installation store. A branch is mutable and is
never sufficient to identify installed content.

The minimum source reference is:

```yaml
source_ref:
  schema: adaos.artifact.source_ref.v1
  forge: github
  repository: inimatic/adaos-registry
  revision: 0123456789abcdef
  path_scope:
    - scenarios/recipes/
    - skills/recipe_planner/
```

Rules:

- `revision` is always immutable for a release or candidate record.
- `path_scope` limits build and change envelopes but does not pretend that a
  subdirectory has an independent Git branch history.
- Project identity is stable across a later repository or path move.
- A GitHub pull request, branch, or release may be linked as an external
  integration reference, but it is not the AdaOS source of truth.

### 2. DEV Context

DEV is the only routine mutable source space.

```yaml
dev_context:
  project_id: recipes
  change_id: CS-142
  base_source_ref: { ... }
  base_release_ref: recipes@2.4.1
  branch: change/CS-142-favorites
  path_scope:
    - scenarios/recipes/
    - skills/recipe_planner/
```

Required invariants:

- A DEV context has one exact base release and source revision.
- A task cannot implicitly mutate another task's DEV context.
- Sparse checkout may reduce materialized files, but it is not the isolation
  boundary.
- Materialization from a public `SourceRef` uses a task-scoped Git worktree or
  isolated clone at the exact revision.
- Handoff of an approved but not yet published DEV revision to Codex captures a
  content-addressed task snapshot before queueing. Applying the result requires
  the DEV source digest to remain unchanged; otherwise the result is retained
  for explicit reapplication and current DEV is not overwritten.
- Runtime Workspace files are never used as an untracked source of changes.
- A DEV context may be deleted after its source commits, package references,
  and evidence are durable.

### 3. Immutable Package Store

Every executable component is packaged before installation or trial.

```yaml
package_ref:
  schema: adaos.artifact.package_ref.v1
  kind: scenario
  artifact_id: recipes
  version: 2.5.0-alpha.142.1
  digest: sha256:...
  source_ref: { ... }
  manifest_digest: sha256:...
  builder_id: adaos.package_builder.v1
  build_policy_digest: sha256:...
  materialization_path: scenarios/recipes
  schema_locks:
    - lock_id: scenario:recipes:recipes.schema.json
      digest: sha256:...
```

Package requirements:

- deterministic normalized content;
- canonical manifest included in the digest;
- exact source revision and path scope;
- deterministic builder and build-policy identity;
- one exact portable Workspace materialization target;
- explicit component kind and canonical identity;
- dependency declaration and resolved dependency lock;
- validation result references;
- no credentials, user runtime data, or unrelated DEV files;
- safe extraction rules and bounded size;
- one verification/file-hash traversal when a cached package enters
  operation-private activation staging;
- content-addressed local storage;
- bounded binary upload/download as the preferred remote transport, with
  structured errors and compatibility fallback only for an explicitly absent
  route;
- atomic write and verification before visibility.

When a canonical `skill.yaml` or `scenario.yaml` declares
`workflow.manifest: workflow.json`, the workflow is part of the same component
package as its code and manifest. It is not a separately installable component.
The package manifest records a `workflow_lock` with the exact path, workflow
schema/type/version, canonical definition digest, validation report digest,
required registered-adapter contract locks, and normalized platform plus
transition-authority role-policy digest. The ordinary package file list also
records the raw file digest, and the package digest covers both code and
workflow bytes. A mismatch between the manifest reference, file inventory,
semantic digest, adapter contract, validation evidence, or role policy rejects
the package before visibility.

`workflow.json` is the only governed workflow source file for v1. A component
may omit it or contain exactly one. Role-specific variants and independent
workflow updates are not package inputs; role-dependent behavior is resolved
from the one definition plus authority policy.

Packages can initially be stored locally and in the existing registry backend.
The contract allows later use of GitHub immutable release assets, OCI, or an
object store without changing Builder or Workspace semantics.

The first deployed remote store is intentionally bounded: both blue/green
backend slots mount one persistent package directory in one zone, while
release and channel metadata remain in the backend's durable repository. This
is sufficient to survive a container replacement and was verified across a
second deployment. It is not a multi-zone storage contract. Broad rollout
requires streamed/object-store transfer, lifecycle and backup policy,
replication or a documented regional recovery boundary, and continuous
candidate-ready/switch/drain routing during backend replacement.

### 4. Registry And Channels

The registry is a catalog and channel index. It is not runtime state and must
not become a second mutable copy of source truth.

```yaml
project:
  id: recipes
  source:
    repository: inimatic/adaos-registry
    integration_ref: main

  channels:
    stable:
      release: recipes@2.4.1
      source_revision: 0123456789abcdef
      package_digest: sha256:...

  candidates:
    CS-142:
      release: recipes@2.5.0-alpha.142.1
      base_release: recipes@2.4.1
      source_revision: fedcba9876543210
      package_digest: sha256:...
```

The first slice supports one `stable` channel and local candidates. Channel
identifiers remain open strings so that integration, beta, edition, and group
channels can be introduced later without replacing the contract.

Promotion changes a channel pointer only after source, package, validation,
and release records are durable. It never rebuilds the already accepted
package implicitly.

### 5. Workspace Activation

Workspace is an activated package projection, not a development checkout.

The authoritative record is `WorkspaceLock`:

```yaml
workspace_lock:
  schema: adaos.workspace.lock.v1
  lock_revision: 17
  updated_at: 2026-07-24T00:00:00Z

  slots:
    primary:
      project_id: recipes
      release: recipes@2.4.1
      release_digest: sha256:...
    trial:
      project_id: recipes
      release: recipes@2.5.0-alpha.142.1
      release_digest: sha256:...
      audience: user:local

  components:
    scenario:recipes:
      version: 2.4.1
      package_digest: sha256:...
      workflow_definition_digest: sha256:...
      workflow_binding_digest: sha256:...
      workflow_role_policy_digest: sha256:...
    skill:shopping_list:
      version: 1.6.2
      package_digest: sha256:...
      workflow_definition_digest: sha256:...
      workflow_binding_digest: sha256:...
      workflow_role_policy_digest: sha256:...

  bindings:
    scenario:recipes:
      skill:shopping_list: sha256:...

  previous_lock_revision: 16
```

The three workflow digest fields are present only for components that declare
a workflow manifest. They remain absent, rather than synthesized, for a
component with no governed workflow.

A `ProjectRelease` resolves every workflow activity to
an exact platform contract or dependency `PackageRef` and includes the
resulting `workflow_binding_digest`. Activation stages and validates the full
package set, candidate adapter registry, workflow definitions, and migrations
before switching one WorkspaceLock/runtime-generation pointer. It cannot
activate new code with an old workflow, a new workflow with old code, or roll
back only one of them. The projected workflow digests in WorkspaceLock are
inspectable consistency witnesses; PackageRef and ProjectRelease digests remain
the delivery authority.

`WorkspaceActivationManager.admit_release_candidate` is shared by publication
and activation. It verifies package archives and refs, then emits one
`adaos.workflow.publication_admission.v1` record binding package/code,
manifest, definition, validation, adapter binding, role policy, desired
WorkspaceLock, and migration evidence. Stable publication persists this record
before channel CAS. Any mismatch fails with zero channel writes; activation
cannot construct a different admission for the same release candidate.

Filesystem materialization, runtime reload, workflow dispatch, and browser
projection are derived from this record. They are not independent sources of
activation truth.

## Identity And Authority

### Canonical Artifact Identity

Display names are never dependency identities. Every scenario and skill uses a
stable canonical manifest `id`. The first slice keeps current ids for backward
compatibility and rejects ambiguous registry aliases.

Future publisher namespaces may extend identity without changing package
digests or dependency bindings:

```text
current:  shopping_list
future:   inimatic/shopping_list
```

The canonical YAML manifest remains authoritative for version and artifact
declaration. Derived JSON, projections, indexes, and package metadata must be
regenerated from it or validated against it.

### Release Identity

A version string is not enough to identify a release. The release identity is
the tuple:

```text
project_id + version + source_revision + release_digest
```

Any change to packaged content, component lock, migration, or source tree
produces a new digest. Reusing a version for a different digest is rejected.

## Dependency Model

### Declared And Resolved Dependencies

Source manifests declare compatibility intent:

```yaml
requires:
  - id: shopping_list
    kind: skill
    version: ">=1.4,<2.0"
```

ProjectRelease locks the exact result:

```yaml
resolved_dependencies:
  - id: shopping_list
    kind: skill
    version: 1.6.2
    package_digest: sha256:...
```

The runtime never resolves `latest` while activating a release.

### MVP Runtime Constraint

The first slice supports one active package per canonical skill id in one node
activation context. The package store may retain multiple versions for trial,
rollback, and future use, but the active runtime binding is singular.

Before activation AdaOS computes:

- the complete candidate component set;
- exact dependency bindings;
- reverse consumers of every changed shared skill;
- compatibility against all active consumers;
- introduced and removed permissions;
- required migrations and rollback capability.

An incompatible shared dependency blocks the entire activation. AdaOS never
partially updates a release or silently selects a newer dependency.

This constraint is intentionally explicit. Context-aware multi-version
resolution can later replace:

```text
skill_id -> package_digest
```

with:

```text
workspace + slot + scenario + skill_id -> package_digest
```

without changing package identity.

## Project Definition Boundary

The source Project definition is the declarative input to release planning. It
may own one standalone skill, one scenario with companion skills, or another
non-empty combination. Shared components are declared as dependencies rather
than copied into each Project.

Transient Builder state is not serialized into the distributable Project:

- current UI focus;
- the particular Codex run or conversation;
- temporary primary/secondary development targets;
- read-only context hydration choices;
- scratch paths and uncommitted task state.

Those fields belong to a Builder Development Session. Stable owned components,
entry points/presentations, dependency requirements, catalog profile, and
install/remove data policy belong to the Project definition. Publication
resolves the latter into the exact ProjectRelease below.

## Project Release Contract

A ProjectRelease is the atomic compatibility and promotion unit:

```yaml
project_release:
  schema: adaos.artifact.project_release.v1
  project_id: recipes
  version: 2.4.1
  release_digest: sha256:...
  source_ref: { ... }
  project_definition:
    schema: adaos.project.v1
    digest: sha256:...
    composition_digest: sha256:...

  components:
    - kind: scenario
      id: recipes
      version: 2.4.1
      package_digest: sha256:...
      role: primary
      exposure: application
      lifecycle: bound
      relations: [presents]
    - kind: skill
      id: recipe_planner
      version: 1.3.0
      package_digest: sha256:...
      role: implementation
      exposure: project_only
      lifecycle: bound
      relations: [realizes]

  entrypoint_locks: [ ... ]
  profile_locks: [ ... ]
  resolved_dependencies:
    - kind: project_release
      project_id: shared_food_data
      release_digest: sha256:...
      resolved_component_closure_digest: sha256:...
  permissions: [ ... ]
  migrations: [ ... ]
  validation_evidence: [ ... ]
  schema_locks: [ ... ]
  migration_locks: [ ... ]
  validation_evidence_refs: [ sha256:... ]
```

A simple standalone skill Project is a one-component ProjectRelease. A
scenario with dedicated companion skills can be released as one locked set.
Shared skills and shared Projects remain separate packages/releases and are
pinned by digest.

The Project definition is part of the release identity, not publication-time
advice. The release locks member roles, exposure, bound/shared lifecycle,
relations, entry points, profiles, compatibility rules, and the exact resolved
Project dependency closure. A set of the same package digests with a different
entry point, ownership/lifecycle rule, or exposure policy is a different
ProjectRelease.

`project_only` controls Catalog and independent-install visibility. Such a
member is still an ordinary verifiable package and remains directly addressable
for diagnostics and provenance. Visibility does not grant or restrict runtime
authority.

Project publication never captures live domain/runtime state. A
ResearchDirection snapshot, scientific ResearchRelease, user preferences, or
skill-owned database is exported through its owning domain contract and may
reference this ProjectRelease; it is not inserted into the software package by
implication.

Schema locks are collected from every selected package. Migration locks and
validation evidence references are recomputed from canonical payload bytes at
every local and registry admission boundary. Raw evidence remains available
for explanation, while the digest references make replacement or omission
detectable without trusting labels.

## Transactional Activation

Activation is a durable operation with an idempotency key and explicit phases:

```text
resolve
  -> fetch
  -> verify
  -> dependency-plan
  -> permission-plan
  -> migration-plan
  -> stage
  -> checkpoint
  -> switch-lock
  -> reload
  -> health-verify
  -> commit
```

Failure before `switch-lock` removes staged state and leaves the active lock
unchanged. Failure after `switch-lock` restores the previous lock and reloads
the previous component set when rollback is supported.

Every durable file or directory switch uses one shared filesystem primitive.
On Windows it requests write-through rename semantics; on POSIX it fsyncs the
affected directory entries best-effort after the atomic rename. Retry is
limited to a transient sharing failure in that switch and never repeats the
enclosing activation or remote mutation.

WorkspaceLock history keeps the strict WorkspaceLock payload unchanged and
adds an operation-bound status sidecar. History is `pending` while commit is
in flight, becomes `active` only after the terminal operation receipt is
durable, and becomes `rolled_back` if activation or explicit recovery restores
the prior lock. Legacy history without a sidecar is treated as active, while a
malformed sidecar is retained fail-closed. Rolled-back history remains auditable
but does not indefinitely pin package reachability.

For a cached package, `verify` performs safe extraction into the operation's
private staging tree while it validates the archive, manifest, and every file
digest. The later `stage` phase records admission of that verified tree and
does not traverse the archive again. Nothing from this private tree becomes
live before `switch-lock`; permission or migration rejection and interruption
remove it through the same rollback path. A newly fetched remote package is
still verified at the remote/store trust boundary before this activation pass.

Before a user-approved update, the same planner runs without writes and emits a
canonical plan digest. The plan is bound to both the immutable target release
and the observed WorkspaceLock digest, and includes component/dependency,
permission, schema, migration, runtime-check, and rollback sections. Activation
recomputes this view and rejects an obsolete `expected_plan_digest`; the
Workspace writer lease and lock compare-and-switch remain the final concurrency
guard.

The operation record contains:

- operation and idempotency ids;
- initiator and policy decision;
- previous and proposed WorkspaceLock digests;
- fetched package digests;
- dependency and permission decisions;
- migration checkpoints;
- reload and health evidence;
- rollback result;
- final activation status.

No unknown state-changing phase is automatically repeated after interruption.
Recovery resumes only a phase proven idempotent or performs rollback from the
durable checkpoint.

Commit also schedules a durable delayed verification bound to the exact
WorkspaceLock digest and revision. The delayed check is read-only: it confirms
that the same lock is still active, re-verifies immutable packages, and hashes
the expected files in each materialized component. If a later activation has
already moved the lock, the observation is `superseded`, not failed. A content
mismatch records a terminal failure and emits an operator event; it does not
perform an implicit rollback. Pending observations have their own marker
directory, while terminal evidence remains on the activation operation. This
keeps periodic work proportional to pending checks rather than total history.

Artifact retention is a separate explicit maintenance operation. Its default
mode is a read-only plan. The protected set is rebuilt from the active
WorkspaceLock, retained lock histories, recent and nonterminal candidate/trial/
promotion/activation records, and pending delayed observations. Records with
unknown mutation or rollback state are retained without an age limit. Packages
outside that set become eligible only after a package grace period; terminal
operation/release records and orphan staging or backup trees have separate
retention windows. Apply rechecks the exact path root and modification identity
under a retention lease plus the Workspace writer lease. Recursive deletion is
limited to one immediate staging/backup operation directory. A malformed
operation record therefore preserves recovery material rather than making it
look orphaned.

Reload and health phases never succeed by omission. Each stores either a
callback completion receipt or an explicit policy skip containing both
`approved_by` and `reason`. A completed operation without these receipts is
not eligible for idempotent replay. Trial acceptance additionally requires a
successful health receipt; a policy-skipped trial health check cannot be
promoted as healthy evidence.

For the single-user slice, introduced permissions fail closed until an
explicit serializable approval is attached to the operation. A non-empty
migration plan is admitted only when every migration declares a rollback
procedure and runtime supplies one-shot execute and rollback handlers. The
executor is called once after the durable checkpoint. A timeout or missing
receipt becomes `uncertain`; it is never retried as activation and requires a
separate one-shot reconciliation that proves `not_applied` or `rolled_back`.

## Stateful Data And Migration

Package rollback does not imply data rollback. Every stateful release declares:

```yaml
migration:
  from_schema: 2
  to_schema: 3
  backward_readable: false
  trial_data_mode: snapshot
  rollback:
    supported: true
    procedure_ref: migration/3-to-2
```

Trial data modes are explicit in both TrialEvidence and its WorkspaceLock slot:

- `empty`: an isolated Workspace with no seeded data;
- `mock`: a fixture identified by immutable `data_ref` plus isolation evidence;
- `snapshot`: an immutable snapshot identity plus verified isolation evidence;
- `read_only`: a named data source with a proven read-only adapter;
- `real`: a named source admitted only with read-only or reversible behavior.

The first slice permits real-data trial only when policy proves one of:

- read-only candidate behavior;
- backward-compatible schema access;
- isolated data snapshot or sandbox;
- separately approved and tested rollback.

Irreversible migrations cannot use automatic trial or unattended stable
activation.

Trial completion records start/end time, computed duration, health and reload
receipts, observations, and rollback disposition. Rejection atomically detaches
the isolated trial Workspace into bounded rollback history before the
candidate record changes; acceptance records `rollback:not_required`.

## Candidate And Trial Flow

```text
stable release S0
  -> DEV context from exact S0 SourceRef
  -> implementation and deterministic validation
  -> candidate C1 with base S0
  -> trial activation
  -> acceptance evidence
  -> stable freshness gate
```

The candidate records both base identities:

```yaml
base:
  release: recipes@2.4.1
  source_revision: 0123456789abcdef
  release_digest: sha256:...
```

Before publication, Builder compares the candidate base with the current
stable channel. Version equality alone is insufficient.

If stable is unchanged:

1. persist the exact candidate source tree in the public source;
2. verify public source tree identity;
3. persist the immutable candidate ProjectRelease;
4. run and persist the shared workflow publication admission;
5. record publication evidence;
6. move the stable channel pointer last.

The local continuation after the registry pointer moves is a separate durable
promotion operation. It records receipts for admission, channel CAS,
publisher Workspace activation, registry projection, and subscription
observation. If a response is lost after channel CAS, retry first reads the
authoritative pointer: an already-visible candidate digest is recorded as the
receipt and the mutation is not repeated. A later local failure pauses the
operation and resumes only its missing idempotent phase; it does not classify
the already-promoted candidate as stale or roll the public channel backward.

If stable moved:

1. mark the candidate `stale`;
2. create or reuse a DEV context from the new stable SourceRef;
3. reapply or rebase the bounded change;
4. rebuild a new candidate digest;
5. repeat deterministic validation and trial;
6. run the freshness gate again.

Evidence is never silently copied from an old digest to a changed candidate.

### MVP Runtime Trial And Project Placement

The first deployable Trial does not require a second full Workspace. It uses a
runtime-only activation derived from the immutable candidate:

```text
DEV source
  -> Candidate PackageRef
  -> durable TrialActivation
  -> workspace/.runtime materialization
  -> Webspace-scoped runtime binding
  -> optional launcher placement with Trial badge
```

The candidate digest, not the mutable DEV tree, is the authority. The
`workspace/.runtime` tree is replaceable derived state. The durable
`adaos.trial.activation.v1` record contains at minimum:

- `trial_id`, project/candidate/release refs and exact package digests;
- zone, subnet, target Webspace, scenario entry point, and audience;
- data mode and approval/reversibility evidence;
- resolved runtime dependency bindings and previous bindings;
- start, expiry, completion, detach, and reconciliation timestamps;
- status, health evidence, rollback/cleanup disposition, and idempotency key.

`adaos.project.placement.v1` is the durable answer to "where can this result be
opened?" It binds either a TrialActivation or stable Release to a destination
and host capability. Placement is not publication and does not mutate channel
identity. A stable result may be published and installed but not yet placed; a
Trial may be placed while stable remains unchanged.

The single-version runtime constraint remains fail-closed. Before Trial
activation AdaOS compares every candidate skill binding with active reverse
consumers. The same version or a unique skill is admitted. A different version
of a shared active skill is rejected unless a Webspace-scoped resolver proves
that the candidate cannot leak into another scenario. Context-aware
multi-version resolution is deferred, but conflict detection is mandatory.

For the MVP, `empty`, `mock`, and proven `read_only` modes are admitted by
default. `real` writes require an explicit approval plus a tested reversible
effect/rollback contract; unknown or irreversible effects are blocked. Full
data-space isolation, simultaneous shared-skill versions, public alpha/beta
channels, and audience rollout policy remain deferred extension seams.

## Stable Subscription And Update

Installation creates a minimal stable subscription record:

```yaml
subscription:
  project_id: recipes
  channel: stable
  policy: notify
  installed_release: recipes@2.4.1
  installed_digest: sha256:...
```

The first slice supports `notify` and `pinned`. `auto-compatible` remains
disabled by default until stateful activation evidence exists.

When the stable channel moves, AdaOS:

1. reads the release and package identities;
2. compares them with WorkspaceLock;
3. fetches and verifies missing packages;
4. computes dependency, permission, and migration plans;
5. asks for any required decision;
6. executes transactional activation;
7. updates the subscription observation only after success;
8. records failure without changing the stable registry pointer.

For a subscribed scenario or skill, the current REST and WebSocket update
entrypoints implement this sequence directly. A write requires the digest of a
freshly reviewed plan; activation rejects a changed WorkspaceLock, missing
runtime reload adapter, or missing health evidence. Runtime reload/projection
is part of the activation transaction so a failure restores files, data, and
the previous lock before the subscription observation can advance.
REST and WebSocket delegate to the same runtime coordinator. It does not allow
deferred projection in this transaction and emits the public success event only
after activation and subscription persistence complete.

Builder exposes the same contract rather than a parallel updater. Overview
performs one read-only subscription inspection and shows the current and target
release, component and dependency changes, new and removed permissions, schema
and migration changes, required runtime checks, warnings, and rollback
availability. Apply is enabled only for an available plan allowed by policy.
The confirmation submits that exact plan digest through the public developer
SDK; activation re-plans and rejects stale review data. The default Builder
idempotency identity is derived from artifact kind, project id, and reviewed
plan digest, so a transport retry recovers the same logical operation.

The old DEV draft `update` and LLM-facing scenario `pull` commands are retired:
they cannot overwrite a mutable DEV tree from Workspace or a remote source.
Projects without a subscription may temporarily use the bounded source-pull
bridge. Such responses are marked `legacy_source_pull` and
`legacy_materialization`; they are not equivalent to reviewed package
activation and remain subject to the legacy-retirement gate.

The bounded rollout decision is therefore explicit and deterministic:

- a valid stable subscription selects `package_activation` and forbids legacy
  fallback for that request;
- an absent subscription may select only the labelled `legacy_source_pull`
  compatibility route;
- a corrupt or unreadable subscription store fails closed and does not look
  like an absent subscription;
- remote/package/activation failure after the package route was selected never
  triggers source pull;
- REST, WebSocket, and Builder consume the same versioned route decision.

This makes package activation the mandatory update authority for migrated
projects without claiming that every historical installation has already been
migrated. Retirement of the compatibility route is a later evidence-based
decision, not an error fallback or an implicit deadline.

## Forge And Repository Evolution

The current monorepo remains supported through `SourceProvider`:

```text
SourceProvider.resolve(ProjectRef, RevisionSelector) -> SourceSnapshot
```

The Builder and package pipeline depend on SourceRef, not on the physical
repository layout. A later project move therefore changes only source mapping:

```text
before: inimatic/adaos-registry @ SHA / scenarios/recipes
after:  inimatic/project-recipes @ SHA /
```

Task-scoped Git worktrees replace a shared mutable DEV checkout before
multi-user collaboration is admitted. Sparse checkout remains an optional
materialization optimization inside an isolated worktree.

## Compatibility Bridge

Migration uses adapters instead of a flag-day rewrite:

- Legacy source paths produce SourceRef records.
- Existing DEV-to-Workspace copy can temporarily implement PackageBuilder and
  Activation interfaces while marked `legacy_materialization`.
- Registry v1 entries remain readable; v2 fields are additive.
- Historical package/release records without the complete builder/lock field
  group preserve their original canonical digest. New writes always emit the
  complete group, and partial groups are rejected instead of inferred.
- Existing installed artifacts receive synthesized package and release
  identities during migration.
- New package-backed activation is mandatory for subscribed projects. A
  non-subscribed project can use only the explicitly labelled compatibility
  bridge until it receives a subscription or the bridge is retired.
- Runtime consumers continue reading current paths while those paths become a
  projection of WorkspaceLock.

## Security And Provenance

- Packages are verified by digest before extraction and activation.
- Archive paths, symlinks, size, and file count are bounded.
- Build inputs, source revision, builder identity, and validation evidence are
  retained.
- Secrets and user data are excluded before packaging.
- A publication scrub checks credentials, personal data, licensing metadata,
  and unrelated files.
- Channel mutation requires explicit policy authority.
- A package accepted in trial is promoted by digest; it is not silently
  rebuilt for stable.
- Signed provenance is detached from PackageRef and ProjectRelease so adding or
  rotating a publisher signature never changes their canonical content digest.
- `adaos.artifact.attestation.v1` binds an Ed25519 signature to subject kind,
  package/release digest, project id, publisher issuer/key id, issuance time,
  predicate type, and predicate digest. The signed record has its own immutable
  attestation digest.
- Trust is local policy, not a property asserted by the package. Trust keys are
  purpose-scoped (`package`/`release`), may have signing windows, rotate by
  adding a new key id, and fail closed after revocation. A revoked key does not
  retain implicit historical trust.
- Required attestation admission runs before remote package fetch and again
  inside the Workspace writer lease before staging/switch. This closes the
  revocation race without putting remote I/O under the mutation lease.
- Detached attestations can live in the local content-addressed store or behind
  an external immutable-asset adapter. An unknown external write outcome is not
  automatically repeated.
- Publication persists the complete deterministic package-then-release
  signature set before its first external write. Each dispatch intent is
  journaled atomically. A timeout or interrupted dispatch enters an uncertain
  state that blocks replay; a separate reconciliation performs remote reads
  only and must find the exact attestation digest before another explicit
  publication call can continue still-pending items.
- The release-binding PUT follows the same no-replay rule. Its dispatch intent
  is persisted first; an unknown outcome blocks promotion until an explicit
  read-only lookup finds the exact set. Promotion never repeats that PUT by
  itself.
- When an attestation publisher is configured, stable promotion records its
  exact completed publication result, binds one immutable
  `adaos.artifact.release_attestation_set.v1` to that release, and only then
  performs channel compare-and-switch. Resume re-reads both journals/remote
  binding and rejects a receipt that no longer matches; compatibility mode
  remains explicit until remote trust policy is deployed.
- A release attestation set covers the release plus every selected package,
  including exact subject, issuer/key, predicate, and attestation digests. The
  registry validates canonical identity and coverage but does not grant trust;
  the installing AdaOS verifies Ed25519 signatures against its local policy.
- The MVP binding is immutable per release. If the only signing key for an old
  release is revoked, recovery uses a new patch release and a new attestation
  set rather than mutating historical authorization metadata.
- Admission recomputes package and release provenance predicates from the exact
  reviewed `PackageRef` and `ProjectRelease`. A valid publisher signature over
  a different provenance statement is rejected.
- Historical package activation remains compatible when no attestation policy
  is configured. A project/publisher policy that requires attestations never
  falls back to unsigned activation.
- Runtime composition is explicit through
  `ADAOS_ARTIFACT_ATTESTATIONS_MODE=off|publish|required`. Publishing requires a
  persistent 32-byte Ed25519 key file and issuer; required admission requires a
  non-empty, separately provisioned trust store and may restrict issuers. AdaOS
  neither generates a production publisher key nor trusts its public half on
  first use.
- GitHub identities and signatures may provide issuer evidence but never
  replace AdaOS trial approval, reviewed activation, or health records.

## Deferred Extension Seams

The architecture reserves, but does not implement:

- multiple active versions through context-aware dependency bindings;
- full Trial data sandboxing and per-Webspace state isolation;
- public alpha/beta candidate channels and audience rollout policy;
- feature and edition variants through additional channel and release metadata;
- multi-user extraction from WorkLog into ChangeSets;
- trusted development groups and zone-specific approvals;
- public candidate discovery and adoption evidence aggregation;
- independent project repositories;
- publisher namespaces and ownership transfer;
- semantic merge and risk-weighted evidence reuse;
- automatic compatible updates;
- commercial entitlement and licensing enforcement.

These extensions must reuse SourceRef, PackageRef, ProjectRelease,
WorkspaceLock, and durable activation operations rather than bypassing them.

## Required End-To-End Proof

The architecture is not accepted until one representative scenario and one
shared skill demonstrate:

1. build from an exact stable source revision;
2. deterministic package digests;
3. dependency-locked ProjectRelease creation;
4. install into an empty Workspace from packages only;
5. candidate creation and isolated trial;
6. successful activation and health verification;
7. rollback to the prior WorkspaceLock;
8. dependency conflict rejection without partial update;
9. stale-base detection and rebuilt candidate trial;
10. stable promotion of the exact accepted package digest;
11. stable subscription detection and package-backed update;
12. recovery from an interrupted activation without replaying an unknown side
    effect;
13. exact Builder task input and concurrent DEV conflict preservation;
14. permission fail-closed behavior and reversible/uncertain migration
    recovery across all activation phases;
15. proof-harness conformance to the current channel/reload contracts and an
    exact file-inventory comparison between mutable DEV and its recorded Forge
    checkpoint before a newer package policy may rebuild that source.

Only after this proof should the broader Builder, registry, marketplace, and
governed-evolution documents be updated to report the new pipeline as current.
