# Distributed Service And Data Topology Roadmap

Status: implementation roadmap for
[Distributed Service And Data Topology](distributed-service-and-data-topology.md).

Last reviewed: 2026-08-22.

## Outcome

AdaOS core provides one reusable control plane for logical distributed
services, partitions, replicas, authority leases, freshness, route resolution,
and topology operations. Media Center proves the first bounded consumer without
turning core into a media catalog or transparent distributed database.

Generic package placement is an admission dependency owned by milestone `AP8`
of the [Artifact Source, Package, and Activation Roadmap](artifact-source-package-activation-roadmap.md).

## Priority And Maturity

- `[must]`: required for the first Media Center distributed-service proof.
- `[should]`: required before unattended rebalance, replicated authority, or a
  second production consumer.
- `[could]`: useful but non-blocking extension.
- `[deferred]`: excluded until the named condition.

Maturity progresses through `hypothesis -> specified -> implemented ->
integrated -> validated-local -> validated-stand -> production-accepted`.
Checked implementation work must link reproducible evidence.

## Guardrails

1. Do not implement a transparent arbitrary-data replication engine.
2. Do not put domain partition keys, merge, ranking, or payload schemas in core.
3. Do not equate component activation with service readiness.
4. Do not grant authority without an epoch/fencing contract.
5. Do not call a replica fresh from process health alone.
6. Do not move large snapshots or deltas through Yjs or command envelopes.
7. Do not promise all-topology atomicity.
8. Do not automatically retry an uncertain state-changing adapter phase.
9. Do not promote a cache or derived projection as authority unless its
   conformance profile explicitly permits it.
10. Do not delete external authoritative data as a generic replica cleanup.

## Delivery Snapshot

| Milestone | State | Target maturity |
| --- | --- | --- |
| DS0 Boundary and contracts | validated-local | specified |
| DS1 Service groups, instances, and leases | validated-stand | validated-stand |
| DS2 Datasets, partitions, replicas, and routing | validated-stand | validated-stand |
| DS3 Adapter operations and safe topology change | validated-stand | validated-stand |
| DS4 SDK, projections, and representative consumers | validated-stand | validated-stand |
| DS5 Replicated authority and production acceptance | validated-stand/review-open | production-accepted |

## Milestone DS0: Boundary And Contract Specification

**Outcome:** core and domain ownership are explicit and align with Project
deployment, activation, projections, identity, and transport.

- [x] `[must]` `DS0-01` Specify ServiceDefinition, ServiceGroup,
  ServiceInstance, Dataset, Partition, Replica, lease/epoch, route, operation,
  and adapter boundaries.
- [x] `[must]` `DS0-02` Map the first Media Center consumer and explicit
  non-guarantees without adding media nouns to core.
- [x] `[must]` `DS0-03` Audit existing lifecycle leases, supervisor activation,
  endpoint routing, Yjs projections, durable operations, and identity contracts;
  select reuse points and record incompatible private mechanisms.
- [x] `[must]` `DS0-04` Produce versioned schema proposals and failure/compatibility
  matrices before implementation.
- [x] `[should]` `DS0-05` Select a second contract fixture from model runtime,
  research execution, or document indexing to prevent media-specific bias.

Checked audit evidence: [Distributed Media Center Baseline - 2026-08-19](distributed-media-center-baseline-2026-08-19.md).
Checked contract evidence: [Distributed Deployment And Topology Conformance -
2026-08-20](distributed-runtime-conformance-2026-08-20.md).

## Milestone DS1: Service Groups, Instances, And Authority

**Outcome:** an activated component can join a logical service with truthful
readiness and one fenced authority where policy requires it.

**Admission gate:** core `AP8` can activate exact components on selected nodes.

**Exit proof:** one singleton and one multi-instance group survive instance
restart, lease expiry, stale registration, incompatible release, and explicit
handoff without dual accepted authority.

- [x] `[must]` `DS1-01` Add fail-closed schemas and typed models for
  ServiceDefinition, ServiceGroup, ServiceInstance, group generation, lease and
  authority epoch.
- [x] `[must]` `DS1-02` Bind registration to trusted node identity, exact
  ComponentActivation, ProjectRelease, protocol version, and runtime generation.
- [x] `[must]` `DS1-03` Implement renewable service membership leases with
  observation freshness distinct from process health.
- [x] `[must]` `DS1-04` Implement monotonic authority epochs and receiver-side
  fencing for singleton groups.
- [x] `[must]` `DS1-05` Separate desired topology from observed instances and
  reconcile with expected revisions and idempotent operation ids.
- [x] `[must]` `DS1-06` Publish bounded group readiness, degradation, pressure,
  compatibility, lease and operation projections.
- [x] `[must]` `DS1-07` Add SDK registration, renewal, inspect, drain and route
  ports without exposing supervisor, Hub, Yjs or package internals.
- [x] `[should]` `DS1-08` Add capacity reservation and anti-affinity admission
  after explicit placement works.
- [ ] `[deferred]` `DS1-09` Add unattended singleton election after explicit
  handoff and fencing pass stand failure injection.

## Milestone DS2: Dataset, Partition, Replica, And Route Topology

**Outcome:** services can declare opaque domain partitions and report
authoritative, derived, follower, or cache replicas with truthful freshness.

**Admission gate:** DS1 identity, membership, and epochs are stable.

**Exit proof:** Media Center external-root partitions and compact derived
catalog replicas are assigned, routed, degraded, and restored with exact
generations and partial-participation status.

- [x] `[must]` `DS2-01` Add schemas and typed models for Dataset, Partition,
  Replica, consistency profile, checkpoint, watermark, lag, lifecycle and data
  labels.
- [x] `[must]` `DS2-02` Keep partition descriptors opaque to core while binding
  every policy and adapter version to the Dataset generation.
- [x] `[must]` `DS2-03` Implement `external_authority` and `derived_projection`
  conformance profiles for the first Media Center proof.
- [x] `[must]` `DS2-04` Implement desired assignment and observed replica state
  with capacity, locality, trust and compatibility admission.
- [x] `[must]` `DS2-05` Resolve logical service/partition routes with topology
  revision, eligible instances, freshness constraints, authorization, expiry,
  partial participation and fallback reason.
- [x] `[must]` `DS2-06` Expose cursor-backed topology inventory and aggregated
  projections; keep high-cardinality/high-frequency data out of synchronized
  product state.
- [x] `[must]` `DS2-07` Distinguish observed empty, unavailable, stale, partial,
  rebuilding and failed in contracts and tests.
- [x] `[should]` `DS2-08` Add `single_authority` follower profile after snapshot
  and fencing operations exist.
- [ ] `[deferred]` `DS2-09` Add `multi_writer_crdt` admission beyond existing
  specifically governed Yjs state only after generic conformance tests exist.

## Milestone DS3: Adapter Operations And Safe Topology Change

**Outcome:** domain adapters can prepare, copy, catch up, verify, promote,
drain, and remove replicas through journaled bounded operations.

**Admission gate:** DS2 topology identities and routes are integrated.

**Exit proof:** a derived Media Center catalog replica is rebuilt/moved and an
external root authority is drained/detached; interruption at every phase has a
known, inspectable, or explicitly uncertain outcome.

- [x] `[must]` `DS3-01` Define typed adapter ports for inspect, prepare,
  snapshot, delta stream, catch-up, verify, read activation, drain and remove.
- [x] `[must]` `DS3-02` Require idempotency/operation ids or uncertain outcome
  for every state-changing adapter call.
- [x] `[must]` `DS3-03` Journal immutable reviewed topology plans and per-phase
  intents/receipts with expected desired and observed revisions.
- [x] `[must]` `DS3-04` Add bounded authenticated snapshot/delta transport with
  content witnesses, resume/checkpoints, cancellation and backpressure.
- [x] `[must]` `DS3-05` Enforce prepare/catch-up/verify before read admission and
  authority handoff; reject stale epochs after promotion.
- [x] `[must]` `DS3-06` Separate replica-data retention from component/Project
  removal and fail closed for external authoritative data.
- [x] `[must]` `DS3-07` Add resource reservation, pressure pause, rollback where
  truthful, and manual reconciliation for uncertain phases.
- [x] `[should]` `DS3-08` Add generic rebalance planning and bounded parallelism
  after single-partition move is validated on stand.
- [x] `[could]` `DS3-09` Add operator cost estimates for bytes, time, temporary
  capacity, availability impact and rollback limits.

## Milestone DS4: SDK, Operations, And Representative Proof

**Outcome:** product skills use only the public distributed SDK and operators
can understand desired/observed topology without domain-private access.

**Admission gate:** DS1 through DS3 must tasks are integrated.

**Exit proof:** Media Center and one second fixture use the same SDK for service
membership, topology status, routing and at least one topology operation.

- [x] `[must]` `DS4-01` Publish `adaos.sdk.distributed` service, topology,
  routing, operation, adapter and subscription interfaces with compatibility
  fixtures.
- [x] `[must]` `DS4-02` Add permission/policy checks and audit for topology
  change, authority handoff, replica removal, route grant and data retention.
- [x] `[must]` `DS4-03` Add generic operator projections for groups, instances,
  datasets, partitions, replicas, operations, freshness, pressure and routes.
- [x] `[must]` `DS4-04` Integrate Media Center coordinator, agents, external
  roots and derived catalog without importing core-private modules.
- [x] `[must]` `DS4-05` Integrate a second bounded consumer or conformance fixture
  that uses different partition semantics.
- [x] `[must]` `DS4-06` Run failure injection for stale instance, expired lease,
  old epoch, missing partition, stale replica, interrupted transfer,
  incompatible release and partial route.
- [x] `[should]` `DS4-07` Add topology explain and dry-run tools suitable for
  Builder and Infrascope consumption.

Project rollout admission is also decoupled from caller RPC lifetime: the
public deployment SDK accepts reviewed work into a durable serialized worker,
publishes an operation id immediately, and resumes accepted/running operations
from their immutable authorization record after runtime restart. This prevents
slow disks or multi-node activation from turning a healthy rollout into a
command timeout.

The serialized deployment worker does not replace component-level runtime
serialization. Project activation and post-boot skill migration share one
cross-process mutation lease, and handler reload validates the exact active
version/slot from the activation receipt. This prevents a migration fallback
from replacing a project-managed patch release while its lifecycle hook is
still running.

## Milestone DS5: Replicated Authority And Production Acceptance

**Outcome:** at least one stateful service can fail over authority without
split-brain or freshness misrepresentation.

This milestone is not required for the first Media Center external-root plus
derived-catalog proof. It becomes required before automatic coordinator
failover or a product relies on replicated authoritative state.

- [x] `[should]` `DS5-01` Implement and conform-test `single_authority` snapshot,
  follower catch-up, promotion, fencing and old-authority rejection.
- [x] `[should]` `DS5-02` Prove planned handoff and unplanned authority loss on
  two physical nodes with exact epochs and data witnesses.
- [x] `[should]` `DS5-03` Define recovery point/time objectives and backup versus
  replica semantics for the representative service.
- [x] `[should]` `DS5-04` Prove upgrades across compatible adapter versions and
  reject incompatible topology changes before data mutation.
- [ ] `[should]` `DS5-05` Complete security, privacy, resource, soak and operator
  recovery review and record an explicit production decision.
- [ ] `[deferred]` `DS5-06` Add automatic cross-subnet placement and failover
  before one-subnet trust and routing are production-accepted.
- [ ] `[deferred]` `DS5-07` Add a general multi-writer profile beyond explicit
  CRDT adapters before conflict, compaction and partition-healing proofs exist.

The [2026-08-21 two-node stand receipt](distributed-media-center-stand-validation-2026-08-21.md)
closes `DS5-02`: planned handoff in both directions, unplanned member loss,
stale-owner rejection, monotonic recovery through epoch `11`, exact
`catalog:40663` witnesses and retained external media were observed on two
physical nodes. It also validates compatible definition admission and
incompatible definition rejection before mutation.

An earlier normal ProjectRelease rollout correctly failed closed with
`topology_skill_activation_identity_mismatch` after replacing an activation
that an old topology plan still referenced. The compatible-overlap design and
the physical closure below resolve that rolling-order gap. The one-hour local
server gate passes on 20,000 items with zero errors, 39.02 MiB peak RSS and
13.533% CPU p95. `DS5-05` remains open for the Android TV
browser/playback/Yjs-pressure gate, exact-candidate security/privacy
repetition, and final operator review. The
[production review](distributed-topology-production-review-2026-08-21.md)
records the security, privacy, resource, rolling upgrade and recovery gates,
runbooks and explicit rejection decision; the remaining physical acceptance
evidence is still required before that checkbox can close.

Local DS5-04 implementation checkpoint: `ServiceDefinition` v2 adds a bounded
exact-release overlap while retaining v1 read compatibility. Group admission
requires the previous desired release and every live membership release to
remain accepted, and membership rollover now gives topology-only generation
changes a collision-resistant lease identity. Contract, membership, runtime,
adapter and Project deployment suites pass locally. The required batch-one
physical sequence is recorded below.

Physical DS5-04 closure was recorded on 2026-08-22 with exact Media Center
release `sha256:4bd827fd9f819107c1d20d85dc13d31b4ce5f0f75f18f57a5238a596ec8ddfe0`.
Definition v19 admitted the previous live release during a batch-one Project
rollout. Four failed member activations remained inspectable and rolled back
without losing the ready old membership; the stable failure was traced to an
over-conservative ordinary-wheel disk reserve rather than bypassed. After core
`0.1.925` (`a3559e14`) reached both physical nodes, deployment operation
`deploymentop.01M0MCVXNM45RR6Q5Q8MCCFSHJ` activated the exact
`media_library_agent@0.6.20` package on the member, passed service restart and
health, and committed activation
`activation.71c96bf7af8bf4efb50f646d113d3a7e`. Definition v20 then removed
the compatibility digest. Both generation-18 instances were ready on the
exact release, and bounded topology inspection reported `partial=false`.
Incompatible definitions continued to fail before mutation under the existing
conformance matrix. The stand receipt retains the exact sequence and open
production gates.

The exact operator lifecycle slice was subsequently completed. The member was
drained and its exact activation removed by durable operations
`deploymentop.01M0ME88HHV3VA46VA97F2AGXA` and
`deploymentop.01M0ME8TR124KFGA7DJAWBPFWA`; the receipt retained external data.
Deployment revision `49` and operation
`deploymentop.01M0MEAH08T72VGSX8174PK1Q9` restored exact agent `0.6.20` as
activation `activation.c93de6188890381ddaf47a7251993c9e`. Core `0.1.926`
(`10cb9d9a`) then reached both nodes and automatically readmitted the same
stable member instance under definition v21 and generation `19`. Both exact
instances were ready, topology inspection was non-partial, the source
size/mtime/inode witness was unchanged and range playback remained `206`.
This closes the drain/remove/restore portion of `DS5-05`; the exact security
matrix, Android TV soak and final production decision remain open.

## Evidence Policy

- Every implementation checkbox links exact core/client/consumer revisions and
  reproducible tests.
- Lease and fencing proofs include old-owner rejection, not only new-owner
  readiness.
- Freshness proofs include checkpoint/watermark witnesses and unavailable-node
  cases.
- Transfer proofs record byte counts, content witnesses, retries, cancellation,
  resource pressure and interruption phase without exposing payload data.
- Stand proof uses at least two physical nodes and one real process/node loss.
- Media Center success alone cannot close a generic contract unless a
  non-media conformance fixture proves the abstraction boundary.
- Production acceptance is explicit and cannot be inferred from local or stand
  success.
