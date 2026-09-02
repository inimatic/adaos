# Artifact Source, Package, and Activation Roadmap

Status: implementation roadmap for
[Artifact Source, Package, and Activation Architecture](artifact-source-package-activation.md).

Last reviewed: 2026-08-22.

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
9. Do not fork or materialize a lock-backed development source until the exact
   package, installed Workspace projection, DEV projection, Project ownership,
   and dependency role have been compared in a digest-bound recovery plan.

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
  compare-and-swap with idempotent same-target retry;
- a source-faithful local verifier that compares mutable DEV against the exact
  pushed package file inventory before rebuilding under a newer package policy,
  and exercises the current channel-CAS and explicit runtime-reload contracts.
- exact-build core-slot admission with durable active/previous markers, one-shot
  restart semantics, and server-authoritative initial Yjs reconnect protection;
- a read-only subscribed-project update plan that reports an explicit
  `up_to_date` no-op instead of converting current state into an HTTP conflict.
- strict manifest-bound workflow definition, validation, adapter-binding, and
  role-policy locks; one shared pre-channel publication/activation admission;
  and complete-generation in-flight migration/rollback evidence.

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

- make an explicit rollout decision before package-only activation becomes the
  default or legacy sparse Workspace compatibility is retired;
- replace the current bounded in-memory binary transfer and single-zone host
  store with streamed/object-store transport and multi-zone durability before
  larger artifacts or broad usage;
- keep frontend replacement, long-lived WebSocket continuity, and proxy
  control-plane evolution separate from the now-proven bounded backend HTTP
  handoff before claiming broad zero-downtime operation;
- make and record the bounded package-only rollout decision. Builder's missing
  remote packages, release, and `stable` channel were restored through an
  explicit attested recovery and then projected through ordinary
  reconciliation; all postconditions are now `noop` with no identity warnings.
  Broad production and marketplace acceptance still require the explicit
  rollout boundary plus the open transport/durability gates below.

The original backend route slice is no longer a blocker: PR
[inimatic/adaos-backend#1](https://github.com/inimatic/adaos-backend/pull/1)
was merged at `1329ecb` and deployed as `0.1.137`; live Forge tree lookups match
the locally persisted checkpoint trees. Builder itself subsequently completed
DEV `0.2.20` → isolated trial → accepted Workspace publication with companion
skill `0.1.28`.

The follow-up backend admission/CAS slice passed its local artifact smoke test,
the same locked test in GitHub Actions, and merged through PR `#2`. The
infrastructure deployment completed successfully, and live `0.1.142` reported
commit `5570f33`. Hub-mTLS probes confirmed missing-channel `404`, mandatory
channel-CAS `400`, and partial-release rejection `400` without creating state.
The follow-up clean stand then uploaded and read back both representative
packages and their exact release through hub mTLS, advanced only the dedicated
`stand-afb87148014b` channel with CAS, and activated a fresh empty package cache
and Workspace. This raises the bounded path to `validated-stand`; it does not
by itself authorize a default-route cutover or broad production acceptance.

The bounded binary transport then merged through backend PR `#3` at `0bc1f826`
and deployed as backend `0.1.144`. Infrastructure PR
[inimatic/infra-inimatic#1](https://github.com/inimatic/infra-inimatic/pull/1)
mounted one durable host package root into both blue/green backend slots. The
exact package, release, and dedicated channel survived a second deployment run
`30227206352`; binary read-back matched the expected digest and bytes, and an
idempotent repeat PUT returned `created=false`. This proves single-zone
deployment durability, not object-store streaming, replication, or continuous
route availability. Infrastructure PRs
[inimatic/infra-inimatic#2](https://github.com/inimatic/infra-inimatic/pull/2)
through [#5](https://github.com/inimatic/infra-inimatic/pull/5) subsequently
made slot state durable, serialized deployments, removed the retiring endpoint
before process stop with config rollback, pinned the proxy control plane, and
removed duplicate reloads. Bootstrap run `30229453608` and clean reverse
control runs `30229653248` and `30229788369` passed strict 100 ms server-side
health sampling in both deployment zones. The two clean runs recorded
`322/298` and `321/297` successful samples respectively, no failures, and no
proxy-container recreation.

## Delivery Snapshot

This table records the highest maturity reached by the current implementation.
Detailed task checkboxes are closed independently when their stated scope has
local evidence. Milestone exit gates and maturity remain separate, so a local
proof is not silently promoted to stand or production acceptance.

| Milestone | Closed | Maturity | Validated task slices | Remaining broader gates |
| --- | ---: | --- | --- | --- |
| AP0 | 8/9 | validated-local (bounded) | identities, fail-closed schemas, canonical digests, immutable version identity, SourceProvider, registry v2 compatibility, deterministic historical registry/manifest migration fixtures, and read-only identity diagnostics | publisher namespaces and ownership transfer remain deferred |
| AP1 | 11/14 | validated-stand plus local workflow gate (bounded, single-zone) | deterministic package build/store/verify, source and builder-policy identity, exact materialization target, evidence references, secret and authoring-state exclusion, portable path admission, single-pass verified extraction, deployed binary transport, detached Ed25519 trust/admission, deterministic no-replay publication journal, immutable release binding, strict workflow/validation/adapter/role locks, separately provisioned signer/trust, and clean required-mode activation | streamed/object-store transport, multi-zone durability, package lifecycle diagnostics, publisher namespaces, and commercial entitlements remain open/deferred |
| AP2 | 8/11 | validated-local (bounded) | exact component/dependency, permission, schema, migration, validation, and workflow adapter-binding locks; complete-set fixed-point selection; consistent bindings and reverse consumers | lock explain UI, plan cache, and stand validation |
| AP3 | 13/14 | validated-stand plus local workflow generation proof (bounded, isolated same-host) | Workspace writer lease/CAS, reachable-set materialization and orphan rollback, mandatory reload/health receipts, phase journal, permission admission, reversible migration/reconciliation, interruption recovery, digest-bound operator diff, exact-lock delayed verification, fail-closed retention, durable rename metadata, terminal lock-history states, complete workflow/code generation admission, and clean package-only activation | unattended irreversible migrations remain deferred |
| AP4 | 8/10 | validated-local (bounded) | exact candidate identity, explicit trial data modes, health/duration/rollback evidence, isolated package materialization, immutable Builder task snapshot, concurrent-DEV compare-and-switch | policy-proven evidence reuse and stand validation |
| AP5 | 7/10 | validated-stand + production-route-verified (bounded) | freshness/stale/rebase flow, renewed trial, Forge tree lookup, deployed backend admission and atomic channel CAS, durable post-CAS continuation, and successful external package/release/channel round-trip across a backend redeploy | metadata rebase policy and later merge-queue support |
| AP6 | 12/14 | validated-local + recovered-live (bounded) | stable subscription discovery, notify/pinned policy, reviewed package update, runtime-aware rollback, post-success observation, primary update-entrypoint cutover, Builder review/apply UI, digest-reviewed remote-to-local reconciliation, attested recovery of missing remote immutable state, one fail-closed package/legacy route contract, and explicit no-op planning for an up-to-date subscription | production deployment/observation of the route contract and later evidence-based retirement of the compatibility route |
| AP7 | 15/17 | validated-stand + second-machine-core-recovered + local workflow proof (bounded), route-fix pending | source-faithful representative LLM/Codex scenario+skill proof, bounded resilience regressions, live Builder publication, external-backend clean required-mode activation, package/release/channel survival across redeploy, exact-build local A/B recovery, generation-bound second-machine core convergence, and manifest-bound workflow authoring/package/role/migration/rollback proof | candidate-before-health proxy admission, frontend/WebSocket continuity, offline browser-draft merge, plus broad production and marketplace acceptance remain open/deferred |
| AP8 | 11/13 | validated-local plus bounded one-node stand | fail-closed deployment schemas, planner/executor, SDK, exact activation, staged reconciliation, drain/remove, projections and one-node Media Center policy | two-node TV/controller proof and recommendation admission remain open |

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
- [x] `[should]` `AP0-07` Add schema migration fixtures for historical registry
  entries and manifests with incomplete version metadata.
- [x] `[could]` `AP0-08` Add a diagnostic command that explains resolved source,
  package, release, and activation identities.
- [ ] `[deferred]` `AP0-09` Add publisher namespaces and ownership-transfer
  records after the single-user identity contract is stable.

Checked scope evidence: [local pipeline proof](artifact-pipeline-local-evidence-2026-07-24.md),
release contracts in `tests/test_artifact_release_contracts.py`, and registry
v1/v2 compatibility in `tests/test_workspace_registry.py`.
`AP0-02` was reclosed after local and backend regressions proved that an
idempotent version repeat is accepted and the same project/version with a
different release digest is rejected before visibility.
`AP0-07` is closed by checked-in v1 Workspace fixtures containing path aliases,
missing canonical YAML versions, and a conflicting derived `scenario.json`.
Migration keeps the install alias separate from canonical identity, derives a
stable non-publishable compatibility version from the canonical YAML digest,
and ignores the derived JSON version. Registry reads now reject corrupt,
unknown-version, unsafe-path, and ambiguous-alias inputs; writes replace the
registry atomically, read-modify-write mutations share a cross-process lease,
and v2 catalog reads do not rescan manifests.
`AP0-08` adds the read-only `adaos maintenance artifact-identity` command. It
keeps registry/channel identity separate from active WorkspaceLock identity and
reports drift instead of synthesizing missing pointers. On this machine the
Builder scenario's installed subscription and active WorkspaceLock agree, while
its local registry still has no stable channel/source pointer. That cache can be
reconciled only from a newly validated remote ChannelPointer, not inferred from
the active package; this is now an explicit AP6 rollout gate rather than hidden
compatibility state.

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
- [x] `[must]` `AP1-06` Persist source revision, builder identity, package
  manifest digest, and validation evidence references.
- [x] `[should]` `AP1-07` Add signed attestations and external immutable release
  asset support behind a package-store adapter.
  - [x] Persist one exact package-then-release signature set before external
    mutation, reuse it across idempotent resumes, and gate stable-channel
    movement on its completed receipt when the publisher policy is configured.
  - [x] Treat an interrupted or failed dispatch as uncertain, prohibit
    automatic replay, and require read-only exact-digest reconciliation before
    a separate explicit continuation. The same rule covers both attestation
    assets and their immutable release-binding PUT.
  - [x] Define one immutable exact attestation set per release, validate complete
    package/release provenance coverage, expose remote asset/set adapters, and
    gate configured promotion on binding that set before channel movement.
  - [x] Wire explicit `off`/`publish`/`required` runtime composition with a
    persistent externally supplied signing key and a separately provisioned
    fail-closed subscriber trust store; never generate or auto-trust a key.
  - [x] Merge/deploy the backend binding routes and provision the actual
    publisher secret/trusted public keys on the stand.
  - [x] Prove required-mode activation from those remote assets on a clean
    stand, including timeout/reconciliation recovery.
- [ ] `[could]` `AP1-08` Add package deduplication and bounded garbage collection
  diagnostics.
- [ ] `[deferred]` `AP1-09` Add commercial license and entitlement payloads to
  package admission policy.
- [x] `[should]` `AP1-10` Verify and extract each cached activation package in
  one archive/file-hash traversal into rollback-owned private staging.
- [x] `[should]` `AP1-11` Prefer bounded binary package upload/download over
  base64 JSON, preserve structured errors, verify digest before visibility,
  and fall back only when an older backend explicitly lacks the route.
- [ ] `[should]` `AP1-12` Replace whole-body binary buffering and the
  single-zone host mount with streamed/object-store transport, lifecycle
  controls, and multi-zone durability evidence.
- [x] `[should]` `AP1-13` Establish the detached attestation trust boundary:
  versioned Ed25519 records, purpose-scoped key rotation/revocation, local and
  external immutable stores, and fail-closed activation admission before fetch
  and again under the Workspace writer lease.
- [x] `[must]` `AP1-14` When `workflow.manifest: workflow.json` is declared,
  validate the strict single-file contract and add a canonical `workflow_lock`
  to the package manifest with definition, validation-report, and required
  adapter-contract digests. Prove that code or workflow changes produce a new
  immutable package and cannot be published separately.

`AP1-14` is closed at `validated-local`. A declared `workflow.json` is strictly
loaded, included in the package file inventory, and represented by recomputed
definition, validation-report, registered-adapter, aggregate binding, and role-
policy locks carried through PackageRef, ProjectRelease, and WorkspaceLock.
Package verification recomputes those locks, and publication tests prove that
code/definition/role mismatches fail before any stable-channel write.

Checked scope evidence: [local pipeline proof](artifact-pipeline-local-evidence-2026-07-24.md)
and package store regressions in `tests/test_artifact_package_store.py`.
`AP1-04` and `AP1-05` were reclosed after local and backend regressions covered
credential/private-key scrub, Windows reserved names and alternate data
streams, trailing-dot/space aliases, Unicode normalization, and case-fold
collisions before package visibility.
`AP1-06` was closed after package manifests and refs began persisting the
deterministic builder id and build-policy digest, exact materialization target,
packaged schema digests, source revision, and manifest digest. ProjectRelease
now stores canonical validation-evidence digest references. Historical records
without the complete additive field group retain their original digest; partial
groups fail closed, and every new package/release write emits the group.
`AP1-10` is closed by package-store read-count instrumentation and an activation
regression that records one `verify_and_extract_once` receipt per cached
component. Extraction I/O failures clean staging without quarantining a valid
immutable package.
`AP1-11` is closed by client and adapter regressions plus deployed backend PR
`#3`: the representative 8,130-byte package traversed the binary route without
the 10,840-byte base64 payload, survived a subsequent blue/green deployment,
and rejected a mismatched digest before visibility. Unknown upload outcomes do
not fall back or replay the mutation. `AP1-12` remains open because the current
route deliberately buffers a bounded body and the durable filesystem is local
to one deployment zone.
`AP1-13` is closed locally by `tests/test_artifact_attestations.py` and the full
`test_artifact*.py` regression. Detached signatures bind
the exact subject and provenance predicate digests without changing existing
PackageRef/ProjectRelease identities. Trust-store readers reject unknown
schemas/fields, key ids are derived from raw Ed25519 public keys, key purpose,
signing windows, issuer allowlists, rotation, and revocation are enforced, and
activation records the exact accepted signatures and immutable release-set
binding before materialization. The
external immutable-asset adapter has an explicit no-retry regression for an
unknown write outcome. The local portion of `AP1-07` now journals one exact
signature set before any external mutation. A dispatch with an unknown outcome
is never replayed: an explicit read-only reconciliation must find the exact
attestation digest before a later explicit publish call may continue pending
items. The idempotency key cannot be rebound to a different plan, and package
and release predicates are recomputed from the exact reviewed refs rather than
trusted as signer-supplied labels. The completed remote slice adds
`adaos.artifact.release_attestation_set.v1`, remote clients/stores, and promotion
ordering `assets -> immutable release binding -> channel`. Backend PR `#4`
merged commits `cd5da95` and `4f5cb80` as `8f4f2c1`; live health and mTLS route
probes confirmed that deployment. The clean stand
`20260727t070101z-required` provisioned a separate signer and trust store,
published and bound all three release subjects, moved only the dedicated
`stand-required-20260727t070101z` channel, fetched both packages into an empty
cache, and activated an empty Workspace with exact binding admission. A wrong
trust store failed before package fetch, operation creation, or Workspace
mutation. The 166-test artifact/attestation gate covers journal interruption,
read-only reconciliation, explicit continuation, and no automatic replay.

## Milestone AP2: Dependency-Locked Project Releases

**Outcome:** activation uses one exact, compatible component set and never
selects a dependency implicitly.

**Admission gate:** AP1 component packages are reproducible.

**Exit proof:** a Project definition plus its component roles, exposure,
lifecycle, entry points, profiles, compatibility policy, packages, and resolved
Project/component dependencies form one locked ProjectRelease; compatible
activation succeeds and an incompatible shared update is rejected without
changing active state. The existing component-package lock is only a partial
proof until the open composition items below close.

- [x] `[must]` `AP2-01` Add declared dependency ranges to canonical scenario and
  skill manifest handling.
- [x] `[must]` `AP2-02` Resolve declared dependencies into exact versions and
  package digests during ProjectRelease build.
- [x] `[must]` `AP2-03` Store component, permission, schema, migration, and
  validation locks in ProjectRelease.
- [x] `[must]` `AP2-04` Implement the MVP rule of one active package per canonical
  skill id in one node activation context.
- [x] `[must]` `AP2-05` Compute reverse consumers before changing a shared skill
  binding.
- [x] `[must]` `AP2-06` Reject missing, ambiguous, incompatible, cyclic, or
  internally inconsistent dependency results with an explainable plan and no
  partial mutation.
- [x] `[must]` `AP2-07` Treat every dependency-lock change as a new release
  digest even when component source files are unchanged.
- [ ] `[should]` `AP2-08` Add lock explain and dependency graph diagnostics for
  Builder and operator UI.
- [ ] `[could]` `AP2-09` Cache compatible dependency plans by release digest.
- [ ] `[deferred]` `AP2-10` Add context-aware simultaneous active versions and a
  general-purpose dependency solver.
- [x] `[must]` `AP2-11` Resolve every workflow activity through a platform or
  exact dependency package registry contract, persist the resulting
  `workflow_binding_digest` in ProjectRelease, and reject missing, mutable,
  permission-broadening, or package-inconsistent bindings.
- [ ] `[must]` `AP2-12` Bind the canonical `adaos.project.v1` definition and a
  normalized composition digest into ProjectRelease. Lock member role,
  exposure, bound/shared lifecycle, relations, entry points, profiles, and
  compatibility rules; reject a release that contains only package digests.
- [ ] `[must]` `AP2-13` Resolve Project-to-Project dependencies to exact
  ProjectRelease identities or an equivalent complete closure, preserve the
  dependency edge in the release, and reject cycles/ambiguity without partial
  publication.
- [ ] `[must]` `AP2-14` Make Project activation/removal reference-count bound,
  shared, and `project_only` members. A project-only package remains verifiable
  and diagnostic-addressable but is not independently installed or removed.
- [ ] `[should]` `AP2-15` Treat existing `skill push` and `scenario push` as
  backward-compatible one-component Project publication projections once the
  Project-level API/CLI is available; do not create a research-specific CLI.
- [ ] `[should]` `AP2-16` Add composition-lock explain/diff output for Builder,
  Catalog, activation plans, and release provenance.

`AP2-11` is closed at `validated-local` by the immutable adapter registry,
package adapter locks, aggregate binding digest, role-policy digest, package
verifier, and shared publication/activation admission. The release candidate
cannot resolve the same adapter id through a different mutable contract.

Checked scope evidence: [local pipeline proof](artifact-pipeline-local-evidence-2026-07-24.md)
and dependency resolver regressions in
`tests/test_artifact_release_resolver.py`.
`AP2-03` was closed after ProjectRelease began carrying exact schema locks from
every selected package, canonical migration locks, and validation-evidence
digest references in addition to component, dependency, and permission locks.
Local activation and backend admission recompute and compare these locks;
partial or stale lock sets are rejected. `AP2-06` was reclosed after the
resolver began rebuilding the reachable complete constraint set to a bounded
fixed point and stored plans began rejecting any binding that differs from the
final selected dependency digest.

## Milestone AP3: Transactional Workspace Activation

**Outcome:** Workspace is materialized from packages through one durable lock
transition with health verification and rollback.

**Admission gate:** AP2 produces a complete ProjectRelease plan.

**Exit proof:** an empty Workspace installs from packages only, an update
switches the lock atomically, and injected failures before and after lock switch
leave either the old or the new complete release active.

- [x] `[must]` `AP3-01` Implement durable WorkspaceLock storage with a
  workspace-wide writer lease, compare-and-switch revision, and previous-lock
  linkage.
- [x] `[must]` `AP3-02` Implement activation phases: resolve, fetch, verify,
  dependency plan, permission plan, migration plan, stage, checkpoint,
  switch-lock, reload, health verify, and commit; reload and health must record
  either a durable receipt or an explicit policy-approved skip.
- [x] `[must]` `AP3-03` Make filesystem materialization and runtime bindings
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
- [x] `[should]` `AP3-08` Add delayed post-activation verification linked to the
  exact WorkspaceLock revision.
- [x] `[should]` `AP3-09` Add orphan staging cleanup and package retention policy.
- [x] `[could]` `AP3-10` Add operator diff output between active and proposed
  locks.
- [ ] `[deferred]` `AP3-11` Add unattended activation of irreversible migrations.
- [x] `[should]` `AP3-12` Persist atomic rename metadata with Windows
  write-through or POSIX directory sync without retrying the enclosing action.
- [x] `[should]` `AP3-13` Distinguish pending, active, and rolled-back
  WorkspaceLock history and keep incomplete history fail-closed.
- [x] `[must]` `AP3-14` Stage package code, adapter registry, workflow compile,
  instance migration, and health as one candidate runtime generation; project
  exact definition/binding digests into WorkspaceLock and switch or roll back
  only the complete generation under the existing writer lease and CAS.

`AP3-14` is closed at `validated-local`. Release candidate admission verifies
the complete package/workflow/role set and desired WorkspaceLock before switch;
runtime instances pin definition/package/binding identities. Explicit in-flight
migration checkpoints survive restart and restore the exact prior instance on
rollback without repeating an external effect.

Checked scope evidence: [local pipeline proof](artifact-pipeline-local-evidence-2026-07-24.md)
and Workspace activation regressions in
`tests/test_artifact_workspace_activation.py`.
`AP3-01` and `AP3-03` were reclosed after activation and explicit recovery
began sharing a cross-process Workspace writer lease, recording and
rechecking the expected lock digest before filesystem mutation, rebuilding
components from all active slot roots, and transactionally removing or
restoring unreachable packages. Immutable remote package fetch is performed
before the Workspace writer lease when a fetch adapter is supplied. `AP3-02`
was reclosed after reload and health became fail-closed phases: every success
has a callback receipt or a named policy skip with `approved_by` and `reason`,
and legacy completed operations without both receipts cannot replay as current.
`AP3-10` is closed by a read-only activation plan bound to the observed
WorkspaceLock digest and target release digest. It reports exact component and
dependency changes, permission additions/removals, schema and migration locks,
rollback availability, required runtime checks, and legacy-target warnings.
The plan has its own canonical digest; activation rejects an obsolete reviewed
plan and still performs WorkspaceLock CAS under the writer lease.
`AP3-08` is closed locally by a durable post-commit observation attached to
every new activation operation. It records the exact WorkspaceLock digest and
revision, then a bounded background worker rechecks package-store integrity and
the hashes of materialized package files after the configured delay. A newer
WorkspaceLock marks the old observation `superseded`; corruption records a
terminal `failed` receipt and event without silently rolling back or replaying
the activation. Pending work is held in a separate bounded marker directory so
the worker does not rescan terminal operation history on every poll. Interrupted
read-only checks may be repeated safely; state-changing activation and migration
rules are unchanged.
`AP3-09` is closed locally by an explicit retention planner whose CLI defaults
to dry-run. It protects the active WorkspaceLock, retained lock histories,
recent or nonterminal durable records, pending observations, and every
`dispatching`/`running`/`uncertain` recovery record. Only unreferenced packages,
expired terminal records, and staging/backup trees proven orphaned past their
grace period become candidates. Apply requires `--apply`, rechecks target root
and modification identity under the retention and Workspace writer leases, and
permits recursive removal only for immediate children of the staging/backup
roots. Invalid journals fail closed. The local machine dry-run reported no
cleanup candidates.
`AP3-12` is closed locally by a shared durable replace primitive used by JSON,
package, trial, and Workspace switches. Windows uses write-through rename;
POSIX syncs affected directory entries best-effort. Cross-directory sync and
atomic JSON replacement regressions cover the boundary.
`AP3-13` is closed by an operation-bound history sidecar. A lock becomes active
history only after the terminal operation receipt is durable; rollback and
explicit recovery mark it rolled back. Retention does not let rolled-back
history pin packages and preserves pending or malformed status fail-closed.

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
- [x] `[must]` `AP4-05` Add a trial WorkspaceLock slot with explicit audience and
  data mode.
- [x] `[must]` `AP4-06` Protect primary activation and real data from an
  incompatible candidate.
- [x] `[must]` `AP4-07` Record deterministic validation, trial duration,
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
`AP4-05` and `AP4-07` were reclosed after TrialEvidence and WorkspaceLock slots
began carrying the same explicit data mode and immutable data identity. Mock
and snapshot modes require isolation evidence, read-only/real modes require
access safety proof, accepted trials require a successful health receipt, and
every decision records duration and rollback disposition. Rejected isolated
trial Workspaces are atomically detached into rollback history.

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
- [x] `[must]` `AP5-07` Make publication idempotent and recover partial completion
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
channel and returns a conflict. `AP5-07` was reclosed after promotion gained a
candidate-scoped writer lease and durable receipts for admission, channel CAS,
Workspace activation, registry projection, and subscription observation.
Regressions cover both a lost channel response and a failure after successful
activation; retry continues without a second channel write or activation.
ChannelPointer and subscription-set readers now reject missing or unknown
fields, malformed digests/revisions/timestamps, inconsistent index keys, and
duplicate subscriptions before their identity can enter reconciliation.

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
- [x] `[must]` `AP6-04` Route current scenario and skill update entrypoints
  through ProjectRelease planning and transactional activation.
- [x] `[must]` `AP6-05` Preserve active release after fetch, dependency,
  permission, migration, reload, or health-check failure.
- [x] `[must]` `AP6-06` Update subscription observation only after successful
  activation.
- [x] `[must]` `AP6-07` Keep registry v1 source materialization as a bounded
  compatibility fallback during migration.
- [x] `[should]` `AP6-08` Add explicit update-plan UI with dependencies,
  permissions, migrations, and rollback availability.
- [ ] `[could]` `AP6-09` Add `auto-compatible` for stateless, policy-approved
  releases after stand evidence.
- [ ] `[deferred]` `AP6-10` Add public candidate subscriptions and recommendation
  ranking.
- [x] `[must]` `AP6-11` Reconcile a freshly fetched remote ChannelPointer and
  immutable release into registry discovery through an explicit
  `plan -> reviewed digest -> apply` operation, with registry and WorkspaceLock
  compare-and-switch checks and no package activation.
- [x] `[must]` `AP6-12` Recover a locally installed exact release only when
  subscription, WorkspaceLock, release plan/receipt, package bytes, accepted
  candidate decision, isolated trial, and immutable Forge source refs agree;
  require current-contract isolated revalidation for legacy trial records and
  journal package, release, and absent-channel-CAS phases without automatic
  mutation replay.
- [x] `[must]` `AP6-13` Make the bounded rollout route explicit and shared:
  subscribed projects require package activation, only subscription absence
  admits labelled legacy source pull, corrupt subscription state fails closed,
  and package-path failures never trigger compatibility fallback.
- [x] `[must]` `AP6-14` Make read-only update inspection total for a valid
  subscription: an already-current project returns a typed `up_to_date` no-op
  plan, while real activation remains fail-closed and separately reviewed.

Checked scope evidence: [local pipeline proof](artifact-pipeline-local-evidence-2026-07-24.md)
and channel/subscription regressions in
`tests/test_artifact_channels_subscriptions.py`.
Primary scenario and skill REST/WebSocket update entrypoints now select the
package-backed path for subscribed projects, require the digest of the reviewed
plan, and execute runtime reload/projection plus health evidence inside the
activation transaction. Both transports use one runtime coordinator; deferred
projection is rejected instead of being reported as healthy, and success events
are emitted only after activation commits. DEV draft update and LLM-facing
scenario pull are retired; the bounded non-subscribed source-pull bridge is
explicit in responses as `legacy_source_pull`. Direct subscription activation
fails closed when a runtime adapter or an explicit policy is absent. AP6-08 is
closed locally: the DEV Builder now loads one read-only subscription inspection,
renders component, dependency,
permission, schema, migration, runtime-check, warning, and rollback fields,
and enables apply only for an allowed available plan. Confirmation submits the
exact reviewed plan digest through the public SDK and shared runtime
coordinator. Its idempotency key is deterministic for the project and reviewed
plan, so a lost response does not turn a repeated click into a second logical
operation. DEV skill `0.1.29` and scenario `0.2.21` passed 32 focused tests,
both validators, and a live API inspection after explicit runtime activation;
Workspace remains on Builder `0.2.20` and companion skill `0.1.28`. Full
removal of the non-subscribed compatibility bridge remains part of the
legacy-retirement gate.
AP6-11 is closed locally by focused reconciliation and SDK/CLI regressions.
The operation verifies the remote channel/release relationship, installed
subscription, active WorkspaceLock, trusted local release receipt, and current
registry entry before producing a digest. Apply takes the Workspace writer
lease, performs a registry-entry CAS, changes neither installed package bytes
nor WorkspaceLock, and recovers a lost completion receipt only on an explicit
repeat of the same reviewed operation. A live read-only plan for Builder then
failed closed because Forge returned `404 release_not_found` for
`sha256:feee37b221a12c6d6ba4e12c1cdd00fdd8320df4b5d4ca9a9ee13747f01a450b`
and `404 channel_not_found` for `builder/stable`. This is now an explicit remote
recovery blocker; local activation evidence is not promoted into central truth.
AP6-12 closes that bounded blocker. The first recovery plan refused Builder's
historical candidate because its legacy `snapshot` trial lacked the now-required
`data_ref`. Rather than weakening the reader, an explicit empty-data isolated
revalidation produced activation receipt
`sha256:4b8d35827ff4f0b1dd04b2c42f10c8e8f1e1abb4271f897a4bd15da56bec19da`.
Reviewed recovery plan
`sha256:4e2cdbbfcd22e4ebda0cd4cd444283769807a58553de6655f86a96f9e921e06c`
then uploaded the exact scenario and companion-skill packages, restored release
`sha256:feee37b221a12c6d6ba4e12c1cdd00fdd8320df4b5d4ca9a9ee13747f01a450b`,
and created `builder/stable` with absent-channel CAS. Ordinary reconciliation
plan `sha256:c275ccd1b8c1a1d61a99f312c73e1d2f6ca330ad456847faaf8f8ee49cf24dd3`
projected the pointer into `registry.json`. Subsequent identity diagnostics have
no warnings; recovery and reconciliation both report `noop`, and WorkspaceLock
remains revision 1 at digest
`sha256:62ac1e57aea1c93a911fdaa663c1dcb8fdfdc993ccabbe10024695c276b1d0b8`.
AP6-13 records the bounded rollout decision in the architecture and implements
one `adaos.artifact.update_route.v1` selector used by scenario REST, skill REST,
skill WebSocket, and the shared package coordinator. Focused API, WebSocket, and
coordinator regressions prove the package route for subscribed projects and
fail-closed handling of a malformed subscription store. The legacy bridge
remains only for projects with no subscription and stays explicitly labelled;
its eventual retirement is not required for this bounded rollout.
AP6-14 is closed by the `adaos.artifact.subscription_update_noop.v1` response
and live Builder inspection on core commit `bc603cb8`. `POST
/api/scenarios/update` with `dry_run=true` returned HTTP 200,
`mode=package_plan`, `updated=false`, `package_required=true`,
`legacy_allowed=false`, and `status=up_to_date`; it did not mutate Workspace or
turn an already-current subscription into `409 Conflict`.

Builder source recovery now treats the immutable package and WorkspaceLock as
the base of a three-way review, not as an editable source checkout. A
digest-bound plan/apply contract preserves divergent Workspace and DEV trees,
keeps consumed dependencies read-only, and materializes only the owning
Project into DEV. Apply cannot promote or move WorkspaceLock: it opens a normal
Builder Change whose later Candidate, Trial, acceptance, and Publication
provide the only path back to an active workspace release. REST, SDK, and Root
MCP expose the same contract; MCP mutation requires the dedicated
`development.write.source_recovery` capability.

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
- [x] `[should]` `AP7-11` Repeat the proof on a clean stand or second machine
  before defaulting new installs to package-only mode.
- [x] `[could]` `AP7-12` Compare local package storage with an external immutable
  release backend.
- [ ] `[deferred]` `AP7-13` Claim production acceptance or marketplace readiness
  from the single-machine proof.
- [ ] `[should]` `AP7-14` Eliminate the public-route gap during blue/green
  upstream handoff and prove continuous health while replacing a backend that
  serves persisted artifact state.
- [x] `[must]` `AP7-15` Recover the local runtime through an exact-build A/B
  slot, one bounded restart, and real browser Yjs reconnects without accepting
  listener-only readiness or automatically repeating a state-changing phase.
- [x] `[must]` `AP7-16` Prove second-machine core-release convergence after a
  partial root promotion: verified-slot supervisor fallback, transactional root
  repair, truthful restart authority, and one subsequent clean update cycle.
- [x] `[must]` `AP7-17` Extend the representative pipeline with one
  manifest-bound `workflow.json`: LLM authoring diagnostics, package and
  ProjectRelease workflow locks, guest/registered projection, trial, stable
  activation, update with an in-flight instance, and complete rollback without
  a mixed code/definition generation.

`AP7-17` is closed at `validated-local` by the workflow authoring, package,
release, activation, role-policy, second-domain, and in-flight migration suites.
Stable live-channel publication is not inferred from this local proof; the
broader route and production gates remain independently open.

Checked scope evidence: [local pipeline proof](artifact-pipeline-local-evidence-2026-07-24.md),
including its reproducible verifier command, immutable digests, operation
records, regression counts, backend deployment, and live Builder publication.
The verifier was rerun on 2026-07-26 after the critical audit. Its contract
regression now fails if DEV publishable content differs from the recorded
checkpoint package, while allowing the same exact files to be rebuilt under an
explicitly identified newer package policy. `AP7-11` is closed by the fresh
same-host stand rooted at `20260727T061000Z`: it started with an empty package
cache and Workspace, used deployed backend `0.1.144` over hub mTLS for binary
package, release, and channel reads/writes, and passed immediate exact-lock
delayed verification. In the same final slice, core commit `5dd1492f` passed 304
focused artifact/Builder transport regressions; the external verifier used only
the dedicated `stand-route-5dd1492f` channel. `AP7-12` is additionally supported
by the same package, release, and channel surviving control deployment run `30227206352`;
byte/digest read-back matched and repeat upload was idempotent. It remains
deliberately narrower than second-machine, multi-zone, and broad production
acceptance.
`AP7-14` was added after a transient public `502` was observed during both
deployment transitions. The initial stop-then-regenerate sequence, a deleted
slot-state file, and later reload-aligned transport resets were retained as
failed evidence in runs `30228183747`, `30228459894`, `30228943924`, and
`30229165792`. Infrastructure PRs `#2` through `#5` corrected those boundaries:
the candidate is admitted before cutover, the old endpoint leaves the validated
config before process stop, the slot pointer commits atomically, deployments
are serialized, proxy config failure rolls back, released proxy images are
pinned, and observer helpers do not issue duplicate reloads. Bootstrap run
`30229453608` passed both zones with `325` and `295` strict samples. Clean
opposite-direction controls `30229653248` and `30229788369` passed with
`322/298` and `321/297` samples, no server-side failures, and zero proxy
recreates. A later PR `#4` deployment exposed a remaining race: nginx-proxy
observed the candidate Docker start before its healthcheck passed, and the
strict public probe recorded `curl rc=55`. The backend did commit successfully
as `8f4f2c1`, so repeating deployment would have been unsafe. `AP7-14` is
reopened until infra commit `5f9a5b0` is merged and a clean control proves that
the candidate warms on a private network and joins `inimatic_proxy` only after
`healthy`. Frontend replacement and long-lived WebSocket continuity remain
separate acceptance scopes.
`AP7-15` is closed for the bounded local topology by commit `bc603cb8`. Slot A
was built from that exact local revision, passed structural/import validation
and all 35 installed handler imports, then became active through durable
markers. One API restart reached the same full Git commit and remained ready
under one PID for seven samples over 36 seconds. Real `dev1`, `dev1-dev`, and
`desktop-dev` browser handshakes exercised server-authoritative initial Yjs
admission without reproducing the native Yrs panic. This does not prove
long-lived WebSocket continuity across cutover, and initial offline-only browser
drafts are deliberately not merged in this MVP safety mode.
`AP7-16` is closed for the affected Linux node `192.168.0.30` by the bounded
release sequence ending at `0.1.614` (`f9faba41`, CI run `30260047119`). The
earlier partial promotion was recovered from the verified active slot; root
copy now uses candidate-owned commit receipts and backup/rollback metadata,
wrapper/CLI replacement is atomic, and the monitor resumes from durable state.
Automatic releases `0.1.611` and `0.1.612` completed slot/root convergence with
one self-restart each; `0.1.613` activated the generation gate. The strict
`0.1.614` control was observed without any manual reconcile or complete call:
the durable `root_promoted` state named old supervisor PID `824452` and instance
`0a6507...` while `root_restart_completed_at` was absent; that PID exited,
systemd started PID `827238`, and only its distinct instance `9834e0...` wrote
completion 7.9 seconds after the restart request. Slot B changed to A, root
version became `0.1.614`, direct runtime ping identified active slot A on 8777,
root parity had no mismatched paths, restart count advanced exactly once, and
both status and attempt reported no subsequent transition. This is bounded
second-machine core convergence, not broad production, multi-zone, or
long-lived browser WebSocket acceptance.

## Milestone AP8: Distributed Project Deployment

**Outcome:** one immutable ProjectRelease can express and reconcile exact
component activation across selected trusted nodes without conflating software
deployment, presentation placement, or domain data sharding.

**Admission gate:** AP2 ProjectRelease compatibility locks and AP3
transactional activation are integrated for the target node runtimes.

**Exit proof:** one reviewed deployment installs a representative coordinator
and two worker components on selected nodes, exposes independent webspace
placements, survives partial failure and staged update, and drains/removes one
worker without deleting retained domain data.

- [x] `[must]` `AP8-01` Define `ProjectDeployment`, `DeploymentRevision`,
  immutable `DeploymentPlan`, per-node `ComponentActivation`, and journaled
  `DeploymentOperation` boundaries without changing `ProjectPlacement` into a
  node deployment record.
- [x] `[must]` `AP8-02` Add fail-closed versioned schemas and typed models for
  desired component placement, exact release/package refs, node targets,
  compatibility, rollout, retention, operation phases, and observed evidence.
- [x] `[must]` `AP8-03` Resolve `singleton`, `selected_nodes`, `all_matching`,
  `per_endpoint`, and `co_located_with` policies against trusted node
  inventory, capabilities, labels, architecture, capacity and current
  activations; keep webspace exposure in `ProjectPlacement`.
- [x] `[must]` `AP8-04` Produce an immutable compare-and-switch deployment plan
  that explains installs, updates, no-ops, drains, removals, compatibility
  blocks, approvals, expected availability impact, and rollback limits before
  mutation.
- [x] `[must]` `AP8-05` Execute package fetch, verification, staging,
  activation, health evidence and commit through one idempotent operation per
  node; record partial subnet success instead of claiming cross-node atomicity.
- [x] `[must]` `AP8-06` Reconcile desired and observed generations with bounded
  retries, explicit uncertain outcomes, compatible version-skew policy, staged
  batches, stop conditions and per-node rollback.
- [x] `[must]` `AP8-07` Implement generic cordon/drain/deactivate/remove phases
  and keep package removal, runtime-data retention, derived-data retention and
  external-data ownership as separate reviewed decisions.
- [x] `[must]` `AP8-08` Publish cursor-backed deployment inventory and bounded
  desired/observed/operation projections suitable for skills, Builder and
  operator UI. The subscribed `adaos.project.deployment_projection.v1` shape
  is fail-closed by an ABI JSON Schema and excludes package bytes and operation
  receipts; full inventory remains cursor-backed.
- [x] `[must]` `AP8-09` Expose planning, apply, inspect, drain, remove and
  reconcile through a public SDK/control-plane boundary; skills must not import
  package store, Workspace, supervisor or node-inventory internals. Every SDK
  call now crosses the active runtime's loopback-only deployment authority;
  CLI, skill and candidate-runtime processes cannot authorize a plan from
  process-local link state, and authority loss fails explicitly.
- [x] `[must]` `AP8-10` Enforce trusted node identity, exact release admission,
  remote-install permission, retention confirmation, audit and secret-safe
  diagnostics.
- [x] `[must]` `AP8-11` Preserve a one-node deployment as an ordinary policy and
  migrate current scenario-driven companion-skill installation without a
  second compatibility installer.
- [ ] `[should]` `AP8-12` Validate the representative Media Center Project on
  two physical nodes plus separate TV/controller placements, including partial
  failure, staged update, drain, retained data and exact revision evidence.
- [ ] `[could]` `AP8-13` Add planner recommendations and unattended
  capability-based placement only after manual selected-node plans are
  validated on stand.

Checked local implementation evidence: [Distributed Deployment And Topology
Conformance - 2026-08-20](distributed-runtime-conformance-2026-08-20.md).
Media Center `0.6.45` at registry revision
`7119aabac4b74e055755ddff4b6f19175a6efb16` was built twice with independent
package/release caches; both builds produced release digest
`sha256:4bd827fd9f819107c1d20d85dc13d31b4ce5f0f75f18f57a5238a596ec8ddfe0`
and identical verified package archives. The reproducibility receipt is
`.adaos/state/codex/evidence/media-center-project-release-0.6.45-reproducibility.json`.
`AP8-12` remains open until the exact release also has separate TV/controller
placement evidence; the implemented recommendation API does not waive that
admission gate for `AP8-13`.

The exact two-physical-node deployment half of `AP8-12` is now validated on
the `.30` stand. Project deployment revision `48`, plan
`sha256:f244d00fa1c39a726e34c515b89d615968ddbe0765153ec3e67e8e94a15329ad`
and operation `deploymentop.01M0MCVXNM45RR6Q5Q8MCCFSHJ` retained exact hub
activations and upgraded the selected member from agent `0.6.18` to exact
`0.6.20` through fetch, verify, stage, activate, health and commit. Earlier
failed attempts rolled the member back and retained external data. The
reviewed drain/remove/restore slice then succeeded through operations
`deploymentop.01M0ME88HHV3VA46VA97F2AGXA`,
`deploymentop.01M0ME8TR124KFGA7DJAWBPFWA` and
`deploymentop.01M0MEAH08T72VGSX8174PK1Q9`. Deployment revision `49` restored
member activation `activation.c93de6188890381ddaf47a7251993c9e`; definition
v21/generation `19` reported both exact instances ready and non-partial after
core `0.1.926` automatically readmitted the replaced instance. The removal
receipt retained external data, the original file witness was unchanged and
range playback remained available. The task remains open only for separate
TV/controller placement evidence.

The 2026-08-20 stand audit found one ready Windows hub in `sn_6acf0c01` and the
Media Center stand in `sn_92ffc943`. Reassigning the parallel-work hub would
mutate an unrelated live environment, so those machines are not claimed as a
two-node ProjectDeployment proof.

Live service membership, authority leases, partition/replica topology,
freshness and data movement begin after component activation and are owned by
the [Distributed Service And Data Topology Roadmap](distributed-service-and-data-topology-roadmap.md).
`AP8` installs exact software; it does not infer domain shards or replicate
arbitrary runtime data.

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
