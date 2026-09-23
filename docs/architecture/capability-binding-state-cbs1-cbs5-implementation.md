# Capability, Binding, and State: CBS1-CBS5 Implementation

Status: executable CRUD-first implementation and compatibility guide.

Last verified: 2026-09-23.

This document records the implemented CRUD-first scope of `CBS1` through
`CBS5`. All non-deferred items through `CBS4`, and all `CBS5` items except the
compact Applications UI view (`CBS5-16`), have validated-local evidence. The
implemented boundary remains deliberately bounded to the Flowboard typed-CRUD
proof; deferred generalizations are not implied.

## Result

The implementation now exercises this authority path:

```text
ApplicationRequirement
  -> SemanticCandidate
  -> exact PackageRelease closure
  -> evidence admission
  -> immutable ApplicationResolution
  -> immutable ResolutionPlan
  -> WorkspaceLock v2 authority commit
```

Portable contracts, local identities, resolutions, plans, locks, telemetry,
and the proof bundle are independently canonical and digest-addressed. Derived
graph data remains outside this authority path.

The executable proof activates one unchanged semantic Application revision in
three materializations:

```text
prototype SQLite StateSpace
  -> local-production package A + production StateSpace
  -> relocated local-production package B + the same production StateSpace
```

Simulation and production intentionally have different `state_space_ref`
values. Production A and B intentionally retain the same `state_space_ref` and
record digest.

## Implemented Surface

### CBS1: portable contract spine

The following closed JSON Schemas and immutable typed records are implemented:

- `adaos.capability.contract.v1`;
- `adaos.state.contract.v1`;
- `adaos.binding.definition.v1`;
- `adaos.binding.delivery.v1`;
- `adaos.environment.profile.v1`;
- `adaos.evidence.claim.v1` and local evidence assessment;
- `adaos.application.requirement.v1`.

Canonical records reject unknown fields, non-finite JSON numbers, unsupported
schema versions, and portable secret/path leakage. A package-independent
`BindingDefinition` uses a logical implementation entry point. Delivery
metadata owns the physical package member, so moving an implementation changes
the package and delivery digests without changing binding semantics.

The first evidence vocabulary is intentionally limited to capability
conformance and state compatibility. Portable bundles reuse the existing
Ed25519 package-attestation trust store and issuer allow-list: the package is
the signed subject and the exact sorted CBS bundle is the predicate digest.
This is local trust-domain admission, not a federated registry.

An explicit-compatibility edge helper covers semantic cases that cannot be
inferred from SemVer. Deterministic reference and field-diff projections are
generated directly from canonical records. The persistent-document terminology
review is available as `scripts/check_cbs_terminology.py` and rejects ambiguous
new `capabilities` declarations and persistent WebUI-style `state_ref` keys.

### CBS2: local binding and state identity

`BindingInstance` and `StateSpace` have stable refs plus append-only immutable
revisions. A revision pins its predecessor, binding definition, delivery,
environment, generation, and authority epoch. Separate access-relation,
lifecycle-operation, and observation records keep mutable health and lifecycle
facts out of identity records.

The local identity store enforces contiguous revisions and non-decreasing
generation and epoch. Effective state guarantees are the intersection of the
state contract, binding support, and environment profile; every state-port
requirement must be included in that result.

Two compatibility projectors exist:

- the current local CRUD registry is projected read-only into a stable
  production identity;
- Builder Preview's SQLite-backed prototype storage is projected into a
  distinct disposable simulation identity.

Projection adopts existing records in place. It does not copy data, transfer
ownership, or grant destruction authority.

The read-only state inspector exposes identity, owner, contract, custodian,
generation, portability, locator, and latest revision-bound operational
observations through `GET /api/v1/cbs/state-spaces/inspect`. Backup/restore and
capacity observations can extend the effective operational guarantee
projection without mutating the `StateSpace`. The local JSON CRUD provider and
the SQLite-backed Prototype provider are both projected, providing a second
legacy shape for compatibility measurement.

### CBS3: two-stage resolution

The bounded resolver first enumerates semantic candidates, then delegates exact
package closure to the existing artifact resolver, and only then admits
evidence. `ApplicationResolution` is created after both stages succeed. Failed
package or evidence admission leaves no resolution or authority mutation.

The immutable result pins:

- the semantic revision and requirement;
- environment and policy digests;
- selected capability and state contracts;
- binding definition and physical delivery;
- exact package closure;
- binding-instance and state-space revisions;
- admitted evidence and prior rejection explanations.

Before exact package/evidence admission, the resolver can emit a deterministic
read-only candidate report. It contains the ranking policy, selected-candidate
reason, typed rejections, effective guarantees, and a bounded set of
non-duplicate Pareto alternatives. Builder/Application semantic viability now
reports both capability gaps and still-unassessed evidence obligations.

### CBS4: plan and authority commit

`ResolutionPlan` separates selection from mutation. It pins the base
`WorkspaceLock`, desired local revisions, generations, epochs, evidence,
package release, classified steps, compensation, recovery, and expiry.

The existing activation journal now carries the application-resolution and
resolution-plan digests through staging, lock switching, recovery, and
diagnosis. The sole authority commit remains the atomic replacement of the
existing `WorkspaceLock`; staged immutable identity revisions are not active
until that lock pins them.

`WorkspaceLock` v2 adds the CBS authority section while the reader remains able
to load v1 locks. Once a workspace has a CBS-managed v2 lock, a legacy
activation cannot silently downgrade it. Single-writer local CRUD operations
validate the active authority epoch, so an old writer is rejected immediately
after a successful rebinding.

Read-only planning tooling adds a structured/human-readable diff, bounded
fresh/expiring/expired re-planning suggestions, and a content-addressed cache
keyed by every planning input. Cached plans remain dry-run records: activation
still rechecks the exact immutable plan and never silently rebases it.

### CBS5: executable CRUD proof

The Flowboard fixture defines one `resource.records.manage` capability, one
record-state contract, simulation and restricted sandbox SQLite bindings, and
a local-production binding.
The same create/update/delete contract scenario runs against the existing
prototype and local CRUD providers.

One E2E test proves all five invariants:

| Invariant | Executable assertion |
| --- | --- |
| materialization independence | simulation and production resolutions pin the same semantic revision and different state spaces |
| package-topology independence | packages A and B deliver the same binding-definition ref and digest from different physical members |
| state continuity | production state ref, record count, record digest, schema lineage, and access relation survive A to B |
| Application independence | canonical semantic bytes stay unchanged and contain no package, binding-instance, provider member, endpoint, or credential identity |
| atomic rebinding | an injected pre-commit failure preserves the old lock, records, generation, and writer; successful commit advances the epoch and fences the old writer |

The proof emits `adaos.cbs.benchmark_telemetry.v1` and
`adaos.cbs.crud_proof.v1`. The proof bundle includes source, semantic,
contract, package, delivery, resolution, plan, lock, local-revision, state,
claim, assessment, telemetry, trace, and test-result facts. Both stores are
content addressed and reject digest collisions.

The same CRUD scenario is exercised against materially different
SQLite-backed Prototype and local JSON providers. Sandbox viability is tested
as a third materialization with its own `BindingInstance` and `StateSpace`,
separate from both Preview simulation and production.

The 2026-09-23 regression gate covered CBS contracts/resolution/activation,
Resource Workbench and both CRUD providers, package storage and workspace
activation, Application runtime/access/data lifecycle, relational storage,
and migration behavior. It exposed and fixed a first-open SQLite race:
`ResourceStorage` schema/WAL initialization is now serialized across threads
and processes through the dependency-neutral mutation lock. The original race
test then passed twenty consecutive runs before the complete gate passed.

## Compatibility And Migration Policy

`CBS1-CBS5` is additive for existing installations:

- existing skill manifests, resource declarations, provider contracts, and
  package releases remain authoritative in their current domains;
- legacy CRUD state is adopted by projection without changing its path or
  records;
- current semantic Applications do not gain package or provider fields;
- v1 `WorkspaceLock` remains readable and can be upgraded on the first planned
  CBS activation;
- native CBS activation never writes a v1 lock and refuses a later downgrade;
- no current skill is required to publish native portable contracts merely to
  continue running.

Therefore an eager data or skill migration is not required now. Compatibility
projection is the safer default until Builder and installation workflows can
produce and inspect native records. Data migration is required only when a
selected transition changes schema/provider in a way that cannot preserve the
existing StateSpace attachment.

## Builder Adaptation

Builder should adopt CBS incrementally, without changing the current Web UI or
prototype client ABI:

1. compile accepted semantic activities and state needs into
   `ApplicationRequirement` and state-port references;
2. materialize Preview resources as simulation bindings with disposable
   `StateSpace` identities;
3. display semantic, simulation, and production viability plus typed rejection
   and evidence obligations;
4. allow an Application-local binding when no admitted reusable binding exists;
5. show the exact `ApplicationResolution` and `ResolutionPlan` diff before
   activation;
6. route Trial and production installation through the CBS-aware activation
   path;
7. only later offer capability extraction/curation into reusable packages.

Builder must not persist package names, skill IDs, physical entry points,
storage locators, endpoints, or credentials into the semantic Application.

## Skill Adoption

Existing skills need no bulk rewrite. Native adoption should proceed per skill:

1. inventory current semantic capabilities, provider/tool entry points,
   resource declarations, schema locks, permissions, and state ownership;
2. generate candidate contracts and binding definitions, but require explicit
   contract admission rather than treating manifest text as canonical;
3. project and adopt the existing state in place under a stable StateSpace ref;
4. publish package delivery metadata for the admitted binding definition;
5. run shared conformance scenarios and create exact claims;
6. activate a v2 lock and move writers to epoch fencing;
7. retain a reviewed fallback until recovery and rollback evidence pass.

Record migration is unnecessary for package-only relocation. It becomes an
explicit `ResolutionPlan` migration only for incompatible schema or provider
transitions.

## When Legacy Code Can Be Removed

The completion of `CBS5` is not permission to remove compatibility code.
Removal is safe only after all of these gates hold for the affected surface:

- Builder emits native requirements and can complete Preview, Trial, and
  production activation without a legacy-only path;
- every active workspace has a v2 lock and every writer validates the pinned
  epoch;
- every installed legacy store has a deterministic projection/adoption report
  and its backup/restore and recovery paths have passed;
- package installation always enters through an admitted resolution and exact
  plan;
- operational telemetry reports zero legacy write-path use for at least two
  release cycles;
- downgrade, rollback, and disaster-recovery procedures have been exercised on
  retained evidence.

Even then, remove write paths before readers. Keep v1 lock reading and legacy
state/package import for a longer compatibility window. Do not remove
`skill.yaml` permission or provider metadata that remains authoritative for
SDK/runtime policy; only stop using it as an implicit semantic capability.

## Next Delivery Sequence

1. integrate native requirement/resolution/plan inspection into Builder and the
   Application APIs;
2. run a migration inventory over installed skills and classify each as
   projection-only, native-ready, or explicit-migration-required;
3. implement `CBS6` evidence freshness and external dependency invalidation;
4. implement `CBS7` with `booking.reserve` to exercise multiple state ports,
   concurrency, idempotency, partial effects, and compensation;
5. build derived semantic/impact/viability projections in `CBS8` only from
   canonical records;
6. use the telemetry accumulated from this proof in the `CBS9` scientific
   benchmark.

## Verification

The primary proof is
`tests/test_capability_binding_state_e2e.py`. Supporting suites cover portable
contracts, local identity projection, resolver ordering and failures, exact
planning, phase-fault rollback, crash recovery, package integrity, Resource
Workbench behavior, Application compatibility, relational storage, access,
and migration.

Standard local proof artifacts belong under `e2e/artifacts/`; they are run
evidence, not source authority and are not committed as architecture records.
