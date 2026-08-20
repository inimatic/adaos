# Distributed Service And Data Topology

Status: target core architecture.

Last reviewed: 2026-08-20.

## Purpose

AdaOS Projects can place components on several trusted nodes. Installation
alone is insufficient for distributed products: runtime services need stable
logical identity, instance discovery, ownership leases, partition and replica
assignment, freshness, routing, drain/rebalance, and truthful partial-failure
state.

This document defines the reusable core control plane for those concerns. It
does not define a transparent distributed database and does not move domain
partitioning, indexing, merge, or replication payload semantics into core.

Implementation order is owned by the
[Distributed Service And Data Topology Roadmap](distributed-service-and-data-topology-roadmap.md).
Project package placement remains owned by
[Project Composition](project-composition-and-development-context.md) and the
[Artifact Source, Package, and Activation Architecture](artifact-source-package-activation.md).

## Decision

Core owns the topology and guarantees of distributed services and datasets:

- logical service and dataset identity;
- desired and observed service instances;
- partition and replica assignment;
- authority leases, monotonically increasing epochs, and fencing;
- health, readiness, pressure, checkpoints, watermarks, and freshness;
- route resolution and partial-result participation;
- drain, move, promote, rebuild, and remove operation journals;
- capability, capacity, anti-affinity, trust, and compatibility admission;
- typed SDK ports through which skills register adapters and publish evidence.

Skills and system services own:

- the meaning of a dataset and partition key;
- which records and bytes belong to a partition;
- snapshot, delta, log, hash, and compaction formats;
- domain merge, conflict, deduplication, and ranking rules;
- whether a replica is authoritative, derived, cached, or rebuildable;
- the actual read/write/query protocol exposed to callers;
- domain retention and deletion policy.

Core can provide reusable transport and checkpoint helpers, but a service must
explicitly bind an adapter. Declaring replication does not make arbitrary local
state safely replicated.

## Relationship To Project Deployment

The layers remain distinct:

```text
ProjectDefinition / ProjectRelease       exact software and compatibility
                 |
                 v
ProjectDeployment / ComponentActivation  desired and observed node placement
                 |
                 v
ServiceGroup / ServiceInstance            live logical service topology
                 |
                 v
Dataset / Partition / Replica             data ownership and availability
                 |
                 v
domain API and payloads                   skill/service semantics
```

`ComponentActivation` proves that a package is active on a node. A
`ServiceInstance` proves that a compatible runtime instance has registered,
passed readiness, and joined a logical group. One activation may host no
distributed service, one service instance, or several explicitly declared
instances.

`ProjectPlacement` is not part of this chain. It binds a Project result to a
presentation/webspace and remains independent from runtime service placement.

### Trusted Member-Link Transport

A connected member publishes a bounded deployment-inventory projection in its
authenticated node snapshot: architecture, runtime version, capabilities,
protocols, labels, integer capacity, and optional endpoints. It does not put
package bytes, domain records, filesystem paths, credentials, or replica
payloads into lifecycle state. The hub-side inventory adapter admits a remote
node only from this explicit projection and current trusted/ready link state.

The control plane can execute exact component phases, topology-adapter phases,
and service invocations over distinct fail-closed member-link RPC methods. Each
receiver revalidates target node, activation/release identity, reviewed plan or
operation identity, and schema before local mutation. A lost acknowledgement
is `uncertain`; a missing/replaced link is retryable; an unknown method or
identity mismatch is terminal. Direct authenticated HTTP remains a supported
adapter transport, but a node does not need to expose its runtime API on the
LAN merely to participate through its existing member link.

The member link has a bounded frame budget. Component packages above the
member-link package limit require an explicit chunked artifact transport or a
direct package endpoint; they are rejected before dispatch and are never
silently inserted into Yjs or split by ad hoc skill code.

Topology snapshots use a separate `distributed.topology.transfer` RPC and
HTTP endpoint. The authority plane relays at most 96 KiB of opaque adapter
payload per chunk, journals the transfer checkpoint and byte/item counts, and
requires matching source and sink SHA-256 witnesses before completion. The
receiver revalidates the immutable reviewed plan, participant activation,
dataset owner, adapter tool, partition, epoch, direction, and request size for
every chunk. Interrupted known-outcome transfers resume from the durable
checkpoint; a lost acknowledgement remains `uncertain`.

## Core Identities

### ServiceDefinition

Immutable release-bound declaration containing:

- logical service kind and provided contracts;
- compatible component/package identities;
- singleton or multi-instance topology policy;
- required node capabilities and trust class;
- supported dataset/replication adapter contracts;
- health, drain, and compatibility protocol versions.

### ServiceGroup

Durable logical runtime identity for one configured service, for example one
Media Center coordinator group or one research executor pool. It records:

- desired topology and generation;
- selected ServiceDefinition and compatibility range;
- instance placement refs and authority policy;
- group health, availability, and last reconciled revision;
- linked datasets and route policy.

### ServiceInstance

Observed runtime instance bound to exact node, ComponentActivation, release,
process/runtime generation, and protocol version. It publishes a renewable
lease, readiness, health, pressure, capabilities, endpoints, and last accepted
topology generation.

Process id alone is not instance identity. A restart creates a new instance
generation and cannot inherit an expired authority epoch implicitly.

### Dataset

Logical data topology identity owned by one ServiceGroup or explicitly shared
between compatible groups. It declares:

- domain and adapter contract;
- partition policy identity and version;
- consistency profile and authority model;
- desired replica count and placement constraints;
- retention, rebuildability, encryption, and data-class labels;
- current topology generation.

Dataset metadata describes topology. It does not contain arbitrary domain
records.

### Partition

Stable domain-defined unit of assignment. Core stores opaque partition key or
range descriptors plus generation and placement. The domain adapter decides
whether a partition represents a root, tenant, hash range, time interval,
model shard, or another concept.

Repartitioning creates an explicit operation and new topology generation. It
must not silently reinterpret an existing partition id.

### Replica

One observed realization of a partition on a ServiceInstance. It records:

- role: `authority`, `follower`, `derived`, or `cache`;
- lifecycle: `preparing`, `catching_up`, `ready`, `draining`, `stale`,
  `unavailable`, `failed`, or `removed`;
- authority epoch where applicable;
- source checkpoint, applied watermark, content/revision witness, and
  observation time;
- health, pressure, lag, and rebuildability;
- adapter-specific bounded status.

A `ready` replica is not necessarily authoritative. A `cache` is never promoted
unless its adapter and operation plan explicitly support promotion.

## Consistency Profiles

Core records and enforces only the topology-level guarantees it can prove:

- `single_authority`: one fenced writer/authority; optional followers;
- `multi_writer_crdt`: adapter declares convergent multi-writer state and emits
  compatible epoch/checkpoint evidence;
- `derived_projection`: rebuildable data derived from named authoritative
  inputs and watermarks;
- `read_through_cache`: non-authoritative, evictable copies;
- `external_authority`: AdaOS routes and observes data whose authority remains
  an external filesystem, database, or service;
- `domain_managed`: topology is observed by core but consistency is explicitly
  outside core guarantees.

These names are contracts, not marketing labels. An adapter must pass the
profile's conformance tests before a Dataset can select it. Core must not report
`multi_writer_crdt` or follower freshness merely because several copies exist.

## Authority, Leases, Epochs, And Fencing

Every authoritative assignment has:

- a group/dataset/partition scope;
- an owner ServiceInstance;
- a monotonically increasing epoch;
- an issued and valid-until time;
- a topology generation and operation ref;
- a renewable lease whose loss is observable.

Authority-bearing writes, checkpoints, and publications carry the epoch.
Receivers reject an older epoch even if the old process is still alive. A new
authority is admitted only after policy-defined evidence such as lease expiry,
explicit handoff, or quorum/external decision. Clock time alone is not a safe
fencing token.

Singleton services use the same mechanism as authoritative partitions. This
keeps coordinator failover and partition promotion on one primitive without
forcing every singleton to own a Dataset.

## Desired And Observed State

Desired topology and observed runtime state are stored separately.

Desired state includes:

- service count/placement policy;
- dataset partitions and replica factors;
- capability, trust, locality, capacity, and anti-affinity constraints;
- rollout, rebalance, recovery, and data-retention policy;
- expected ProjectRelease and adapter compatibility.

Observed state includes:

- exact active instances and activations;
- current leases/epochs;
- replica lifecycle, checkpoint, watermark, lag and pressure;
- route readiness and last observation;
- operation phase and failure disposition.

The reconciler plans from an expected desired revision and current observed
revision. It never turns stale observation into a destructive plan silently.

## Domain Adapter Contract

A replication/topology adapter may implement:

- `inspect`: publish local identity, revision, size, checkpoint, health and
  pressure;
- `prepare`: reserve capacity and create an empty non-visible replica;
- `snapshot`: export or import a bounded snapshot with content witness;
- `stream_deltas`: transfer ordered changes from a named checkpoint;
- `catch_up`: reach a target watermark and prove it;
- `activate_read`: make a ready non-authoritative copy queryable;
- `promote`: accept a new fencing epoch and become authority;
- `demote`: stop authority and publish the final checkpoint;
- `drain`: stop new work and finish/checkpoint admitted work;
- `remove`: remove derived/local replica state under explicit retention policy;
- `verify`: compare witnesses and run domain-specific consistency checks;
- `route`: return adapter/domain endpoints and participation constraints.

Every state-changing call is idempotent by operation id or reports an uncertain
outcome that requires inspection. Core journals intent and receipt but does not
pretend it can roll back domain bytes after an unknown side effect.

Snapshot and delta payloads travel through bounded, authenticated transport.
Large payloads are not inserted into Yjs, command envelopes, or status records.
The SDK's `BoundedTransferController` connects an
`AuthenticatedTransferSource` to an `AuthenticatedTransferSink`; adapters own
serialization and staging, while core owns authorization, limits, progress,
resume state, pressure pause, and final witness validation. Authority epoch
zero is valid for derived replicas that have no writer authority.

## Placement And Rebalance

The topology planner consumes trusted node inventory, ComponentActivations,
ServiceDefinitions, capacity, locality, anti-affinity, current assignments, and
domain-supplied partition facts. It emits an immutable reviewed plan.

A safe partition move normally follows:

```text
admit target -> prepare -> snapshot -> catch up -> verify
             -> route/read admission
             -> authority handoff when required
             -> drain old replica -> retention/remove
```

The plan records whether service remains available, becomes read-only, or is
temporarily unavailable in each phase. Multi-partition rebalance is a sequence
of independently journaled operations, not a fictitious subnet-wide atomic
transaction.

Resource reservations and headroom are checked before transfer. Reconciliation
backs off under playback, command, sync, or node pressure according to product
policy supplied through generic priority classes.

## Routing Contract

Core resolves a logical service or partition request into eligible instances
and returns:

- topology and route revision;
- selected authority or read replicas;
- freshness/checkpoint constraints;
- endpoint/transport refs;
- authorization scope and expiry;
- partial participation and unavailable partitions;
- fallback choices and reason.

The domain chooses query fan-out, result merge, ranking, and semantic fallback.
Core does not combine media search results, model outputs, or research records.

A data route may bypass the logical coordinator after authorization. Control
plane centrality must not imply data-plane hairpinning.

## SDK Boundary

Skills and system services use an `adaos.sdk.distributed` plane with typed
ports for:

- declaring/binding service and dataset contracts from a ProjectRelease;
- reading authorized node/service/topology inventory;
- previewing and applying desired topology plans;
- registering instances and renewing leases;
- publishing health, pressure, checkpoints and replica state;
- resolving service/partition routes;
- implementing adapter callbacks and operation receipts;
- implementing authenticated bounded transfer sources and sinks without
  importing subnet or runtime transport internals;
- subscribing to bounded desired/observed projections.

The SDK does not expose internal package stores, supervisor processes, Hub
tables, Yjs documents, NATS subjects, or filesystem layout.

## Failure And Degraded Semantics

The common state model distinguishes:

- unavailable instance versus unavailable logical service;
- stale replica versus failed replica;
- missing observation versus observed empty data;
- expired authority versus disconnected observer;
- partial partition participation versus complete query;
- desired mismatch versus active repair;
- recoverable operation versus uncertain side effect;
- data loss risk versus rebuildable derived-data loss.

Products map these states to domain behavior. Core supplies machine reasons,
status and operation evidence; the owning skill supplies localized human text
for domain actions.

## Security And Governance

- Only trusted nodes and admitted ComponentActivations can join a ServiceGroup.
- ProjectRelease and adapter protocol compatibility are verified before
  registration and again before promotion.
- Topology mutation, authority transfer, replica removal, and retention changes
  are authorized, reviewed according to risk, and audited.
- Lease and route grants are scope-bound, epoch-bound, short-lived and
  revocable.
- Dataset labels constrain placement, diagnostics, export and provider egress.
- Domain payloads are encrypted/authenticated in transit and never logged by
  generic control-plane code.
- Removal of a replica is independent from deletion of external authoritative
  data unless an explicit domain operation proves ownership and confirmation.

## Observability

Bounded projections expose:

- desired and observed generations;
- instance/replica counts by lifecycle;
- authority holder and epoch without secret material;
- checkpoint/watermark age and lag;
- operation phase, duration, retry and uncertain disposition;
- capacity, reservation and pressure;
- route availability, partial participation and fallback reason;
- compatibility and policy violations.

High-cardinality partition details are cursor-backed. High-frequency replica
metrics use an observability channel and aggregation, not synchronized product
state.

## Media Center Mapping

Media Center is the first representative consumer:

| Generic core object | Media Center mapping |
| --- | --- |
| ServiceGroup | logical `MediaCenterCoordinator` or library-agent pool |
| ServiceInstance | coordinator or `media_library_agent` runtime on one node |
| Dataset | household library topology, compact global catalog, or playback-session state |
| Partition | initially one node-local `LibraryRoot`/shard assignment |
| authority replica | agent that owns an external root, or active coordinator |
| derived replica | compact coordinator catalog derived from agent deltas |
| external authority | original filesystem bytes registered in place |
| route | query participant, metadata-delta path, or source-node playback path |

Media Center defines root/shard keys, catalog deltas, work/collection merge,
search fan-out, result ranking, and source selection. Core manages instance
membership, authority epochs, assignments, freshness, routing facts, and
operation lifecycle.

The first Media Center milestone does not need byte-for-byte replication of
original media or a fully replicated coordinator database. It proves
`external_authority` root partitions and a `derived_projection` compact catalog
while preserving contracts for later replicas and coordinator standby.

## Other Expected Consumers

The same layer should be usable by:

- model-serving pools and model-artifact caches;
- research executors and result partitions;
- document/search indexes;
- endpoint capability services;
- local automation workers;
- future trusted multi-node storage and backup skills.

No consumer is allowed to add its nouns to the core ABI.

## Explicit Non-Guarantees

- Core does not make SQLite, FAISS, arbitrary files, or Python objects
  multi-writer safe.
- Core does not infer partition keys or resolve domain conflicts.
- A topology plan is not a transaction over all service data.
- A renewable lease is not proof that replica bytes are current; checkpoints
  and adapter verification remain required.
- A derived replica is not a backup unless an explicit backup contract and
  restore proof say so.
- Yjs is one valid convergent-state adapter, not the universal replication
  substrate for large or high-frequency data.
