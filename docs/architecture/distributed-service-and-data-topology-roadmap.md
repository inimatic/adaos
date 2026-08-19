# Distributed Service And Data Topology Roadmap

Status: implementation roadmap for
[Distributed Service And Data Topology](distributed-service-and-data-topology.md).

Last reviewed: 2026-08-19.

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
| DS0 Boundary and contracts | specified | specified |
| DS1 Service groups, instances, and leases | open | validated-stand |
| DS2 Datasets, partitions, replicas, and routing | open | validated-stand |
| DS3 Adapter operations and safe topology change | open | validated-stand |
| DS4 SDK, projections, and representative consumers | open | validated-stand |
| DS5 Replicated authority and production acceptance | deferred/open | production-accepted |

## Milestone DS0: Boundary And Contract Specification

**Outcome:** core and domain ownership are explicit and align with Project
deployment, activation, projections, identity, and transport.

- [x] `[must]` `DS0-01` Specify ServiceDefinition, ServiceGroup,
  ServiceInstance, Dataset, Partition, Replica, lease/epoch, route, operation,
  and adapter boundaries.
- [x] `[must]` `DS0-02` Map the first Media Center consumer and explicit
  non-guarantees without adding media nouns to core.
- [ ] `[must]` `DS0-03` Audit existing lifecycle leases, supervisor activation,
  endpoint routing, Yjs projections, durable operations, and identity contracts;
  select reuse points and record incompatible private mechanisms.
- [ ] `[must]` `DS0-04` Produce versioned schema proposals and failure/compatibility
  matrices before implementation.
- [ ] `[should]` `DS0-05` Select a second contract fixture from model runtime,
  research execution, or document indexing to prevent media-specific bias.

## Milestone DS1: Service Groups, Instances, And Authority

**Outcome:** an activated component can join a logical service with truthful
readiness and one fenced authority where policy requires it.

**Admission gate:** core `AP8` can activate exact components on selected nodes.

**Exit proof:** one singleton and one multi-instance group survive instance
restart, lease expiry, stale registration, incompatible release, and explicit
handoff without dual accepted authority.

- [ ] `[must]` `DS1-01` Add fail-closed schemas and typed models for
  ServiceDefinition, ServiceGroup, ServiceInstance, group generation, lease and
  authority epoch.
- [ ] `[must]` `DS1-02` Bind registration to trusted node identity, exact
  ComponentActivation, ProjectRelease, protocol version, and runtime generation.
- [ ] `[must]` `DS1-03` Implement renewable service membership leases with
  observation freshness distinct from process health.
- [ ] `[must]` `DS1-04` Implement monotonic authority epochs and receiver-side
  fencing for singleton groups.
- [ ] `[must]` `DS1-05` Separate desired topology from observed instances and
  reconcile with expected revisions and idempotent operation ids.
- [ ] `[must]` `DS1-06` Publish bounded group readiness, degradation, pressure,
  compatibility, lease and operation projections.
- [ ] `[must]` `DS1-07` Add SDK registration, renewal, inspect, drain and route
  ports without exposing supervisor, Hub, Yjs or package internals.
- [ ] `[should]` `DS1-08` Add capacity reservation and anti-affinity admission
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

- [ ] `[must]` `DS2-01` Add schemas and typed models for Dataset, Partition,
  Replica, consistency profile, checkpoint, watermark, lag, lifecycle and data
  labels.
- [ ] `[must]` `DS2-02` Keep partition descriptors opaque to core while binding
  every policy and adapter version to the Dataset generation.
- [ ] `[must]` `DS2-03` Implement `external_authority` and `derived_projection`
  conformance profiles for the first Media Center proof.
- [ ] `[must]` `DS2-04` Implement desired assignment and observed replica state
  with capacity, locality, trust and compatibility admission.
- [ ] `[must]` `DS2-05` Resolve logical service/partition routes with topology
  revision, eligible instances, freshness constraints, authorization, expiry,
  partial participation and fallback reason.
- [ ] `[must]` `DS2-06` Expose cursor-backed topology inventory and aggregated
  projections; keep high-cardinality/high-frequency data out of synchronized
  product state.
- [ ] `[must]` `DS2-07` Distinguish observed empty, unavailable, stale, partial,
  rebuilding and failed in contracts and tests.
- [ ] `[should]` `DS2-08` Add `single_authority` follower profile after snapshot
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

- [ ] `[must]` `DS3-01` Define typed adapter ports for inspect, prepare,
  snapshot, delta stream, catch-up, verify, read activation, drain and remove.
- [ ] `[must]` `DS3-02` Require idempotency/operation ids or uncertain outcome
  for every state-changing adapter call.
- [ ] `[must]` `DS3-03` Journal immutable reviewed topology plans and per-phase
  intents/receipts with expected desired and observed revisions.
- [ ] `[must]` `DS3-04` Add bounded authenticated snapshot/delta transport with
  content witnesses, resume/checkpoints, cancellation and backpressure.
- [ ] `[must]` `DS3-05` Enforce prepare/catch-up/verify before read admission and
  authority handoff; reject stale epochs after promotion.
- [ ] `[must]` `DS3-06` Separate replica-data retention from component/Project
  removal and fail closed for external authoritative data.
- [ ] `[must]` `DS3-07` Add resource reservation, pressure pause, rollback where
  truthful, and manual reconciliation for uncertain phases.
- [ ] `[should]` `DS3-08` Add generic rebalance planning and bounded parallelism
  after single-partition move is validated on stand.
- [ ] `[could]` `DS3-09` Add operator cost estimates for bytes, time, temporary
  capacity, availability impact and rollback limits.

## Milestone DS4: SDK, Operations, And Representative Proof

**Outcome:** product skills use only the public distributed SDK and operators
can understand desired/observed topology without domain-private access.

**Admission gate:** DS1 through DS3 must tasks are integrated.

**Exit proof:** Media Center and one second fixture use the same SDK for service
membership, topology status, routing and at least one topology operation.

- [ ] `[must]` `DS4-01` Publish `adaos.sdk.distributed` service, topology,
  routing, operation, adapter and subscription interfaces with compatibility
  fixtures.
- [ ] `[must]` `DS4-02` Add permission/policy checks and audit for topology
  change, authority handoff, replica removal, route grant and data retention.
- [ ] `[must]` `DS4-03` Add generic operator projections for groups, instances,
  datasets, partitions, replicas, operations, freshness, pressure and routes.
- [ ] `[must]` `DS4-04` Integrate Media Center coordinator, agents, external
  roots and derived catalog without importing core-private modules.
- [ ] `[must]` `DS4-05` Integrate a second bounded consumer or conformance fixture
  that uses different partition semantics.
- [ ] `[must]` `DS4-06` Run failure injection for stale instance, expired lease,
  old epoch, missing partition, stale replica, interrupted transfer,
  incompatible release and partial route.
- [ ] `[should]` `DS4-07` Add topology explain and dry-run tools suitable for
  Builder and Infrascope consumption.

## Milestone DS5: Replicated Authority And Production Acceptance

**Outcome:** at least one stateful service can fail over authority without
split-brain or freshness misrepresentation.

This milestone is not required for the first Media Center external-root plus
derived-catalog proof. It becomes required before automatic coordinator
failover or a product relies on replicated authoritative state.

- [ ] `[should]` `DS5-01` Implement and conform-test `single_authority` snapshot,
  follower catch-up, promotion, fencing and old-authority rejection.
- [ ] `[should]` `DS5-02` Prove planned handoff and unplanned authority loss on
  two physical nodes with exact epochs and data witnesses.
- [ ] `[should]` `DS5-03` Define recovery point/time objectives and backup versus
  replica semantics for the representative service.
- [ ] `[should]` `DS5-04` Prove upgrades across compatible adapter versions and
  reject incompatible topology changes before data mutation.
- [ ] `[should]` `DS5-05` Complete security, privacy, resource, soak and operator
  recovery review and record an explicit production decision.
- [ ] `[deferred]` `DS5-06` Add automatic cross-subnet placement and failover
  before one-subnet trust and routing are production-accepted.
- [ ] `[deferred]` `DS5-07` Add a general multi-writer profile beyond explicit
  CRDT adapters before conflict, compaction and partition-healing proofs exist.

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
