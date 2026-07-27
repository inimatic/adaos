# Artifact Source, Package, and Activation Architecture

Status: target architecture for the single-user artifact development,
publication, installation, update, and rollback pipeline.

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
| Project | Independently understandable development and release subject, such as a scenario and its dedicated companion skills. |
| Component | A scenario, skill, schema, migration, UI descriptor, or other versioned item included in a project release. |
| SourceRef | Exact forge-independent reference to source content. |
| PackageRef | Content-addressed reference to one immutable package. |
| ProjectRelease | Immutable, dependency-locked set of component packages. |
| Channel | Mutable discovery pointer to an immutable ProjectRelease. |
| Candidate | Pre-release ProjectRelease linked to one bounded change and base release. |
| Activation | Transactional selection of ProjectRelease packages for a workspace slot. |
| WorkspaceLock | Authoritative record of the packages and dependency bindings currently selected for a workspace. |
| DEV context | Mutable, isolated worktree or checkout created from a SourceRef. |
| Trial | Reversible candidate activation for an explicitly bounded audience and data policy. |

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
- atomic write and verification before visibility.

Packages can initially be stored locally and in the existing registry backend.
The contract allows later use of GitHub immutable release assets, OCI, or an
object store without changing Builder or Workspace semantics.

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
    skill:shopping_list:
      version: 1.6.2
      package_digest: sha256:...

  bindings:
    scenario:recipes:
      skill:shopping_list: sha256:...

  previous_lock_revision: 16
```

Filesystem materialization, runtime reload, and browser projection are derived
from this record. They are not independent sources of activation truth.

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

## Project Release Contract

A ProjectRelease is the atomic compatibility and promotion unit:

```yaml
project_release:
  schema: adaos.artifact.project_release.v1
  project_id: recipes
  version: 2.4.1
  release_digest: sha256:...
  source_ref: { ... }

  components:
    - kind: scenario
      id: recipes
      version: 2.4.1
      package_digest: sha256:...
    - kind: skill
      id: recipe_planner
      version: 1.3.0
      package_digest: sha256:...

  resolved_dependencies: [ ... ]
  permissions: [ ... ]
  migrations: [ ... ]
  validation_evidence: [ ... ]
  schema_locks: [ ... ]
  migration_locks: [ ... ]
  validation_evidence_refs: [ sha256:... ]
```

A simple standalone skill is a one-component ProjectRelease. A scenario with
dedicated companion skills is released as one locked set. Shared skills remain
separate packages and are pinned by digest.

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
4. record publication evidence;
5. move the stable channel pointer last.

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
- GitHub identities and signatures may add attestations but never replace
  AdaOS approval and activation records.

## Deferred Extension Seams

The architecture reserves, but does not implement:

- multiple active versions through context-aware dependency bindings;
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
