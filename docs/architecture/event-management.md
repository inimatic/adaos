# Event Management

Status: current primer subordinate to the operational event target architecture.

Last reviewed: 2026-08-07.

This document is the high-level event-management primer for AdaOS.

It describes how services, skills, scenarios, browser runtimes, projection
dispatchers, and operational surfaces should use events. It intentionally does
not duplicate the full projection/Yjs architecture or its delivery roadmap.

Authoritative companion documents:

- [Operational Event Model](operational-event-model.md):
  target event taxonomy, projection demand, lifecycle, node scope, platform
  emitters, and Yjs materialization rules.
- [Operational Event Model Roadmap](operational-event-model-roadmap.md):
  master implementation order for event, projection, browser/runtime, and
  platform-emitter work.
- [Operational Event Model Reference Plan](operational-event-model-reference-plan.md):
  coverage gates and review checklist for implementation slices.
- [Projection Subscription Roadmap](projection-subscription-roadmap.md):
  detailed client subscription and projection ABI checklist.
- [Realtime Reliability Roadmap](realtime-reliability-roadmap.md):
  transport and ordering prerequisites.

## Current Implementation Baseline

AdaOS currently has a local runtime event foundation:

- `src/adaos/domain/types.py` defines the legacy `Event(type, payload, source,
  ts)` record.
- `src/adaos/domain/event_envelope.py` defines the shared operational event
  envelope compatibility layer.
- `src/adaos/services/eventbus.py` implements `LocalEventBus` with prefix
  subscription, async handler scheduling, bounded hot-topic queues,
  supersede/drop counters, slow-handler logging, crash incident reporting, and
  backlog snapshots.
- `src/adaos/sdk/data/events.py` exposes `adaos.sdk.data.events.publish()`,
  which enriches payloads with `_meta.event`.
- `src/adaos/sdk/status.py` publishes status-card events through the same bus.
- Tests cover legacy event normalization, nested `_meta.event` metadata,
  payload enrichment, generated event ids, and eventbus `emit()` metadata
  enrichment.

This means the system already has an MVP-compatible event envelope and a
bounded local bus. The remaining work is broader adoption and consolidation,
not inventing a second event system.

## Scope

Event management covers:

- service-to-service runtime notifications
- skill/core interaction
- scenario triggers and effects
- platform operational diagnostics
- status-card and notification updates
- projection invalidation and lifecycle
- NLU/Builder/Root MCP audit and repair signals where event semantics are
  useful
- eventual inter-node or root-mediated transport

Event management does not cover:

- browser projection payload shape in detail
- Yjs materialization rules
- projection subscription ABI details
- Root MCP request/response contracts
- durable conversation history
- arbitrary workflow orchestration DSLs

Those topics are owned by the companion architecture documents listed above.

## Design Principles

1. Events are facts or requests, not shared mutable state.
2. Domain events update runtime memory first; Yjs writes happen only through
   demanded, fingerprinted projections.
3. The local bus is the current runtime substrate; transport remains
   pluggable.
4. Event producers own event type and payload semantics.
5. Core owns envelope normalization, trace/scope propagation, dispatcher input
   shape, and platform-level guardrails.
6. Consumers must be idempotent where delivery can be repeated.
7. High-rate topics must be bounded, coalesced, or superseded.
8. Events must carry enough metadata to be traceable without leaking secrets.
9. Event names must describe stable runtime contracts, not temporary code
   locations.
10. State-changing workflows need explicit approval/policy gates outside the
    event bus.

## Event Envelope

AdaOS supports legacy events:

```json
{
  "type": "node.status",
  "source": "runtime",
  "ts": 20.0,
  "payload": {
    "state": "ready"
  }
}
```

The preferred operational envelope adds event metadata under `_meta.event` in
the payload:

```json
{
  "type": "node.status",
  "source": "runtime",
  "ts": 20.0,
  "payload": {
    "state": "ready",
    "_meta": {
      "event": {
        "event_id": "evt-demo-1",
        "trace_id": "trace-demo-1",
        "source_authority": "platform",
        "actor": {"kind": "system"},
        "scope": {"webspace_id": "desktop", "node_id": "node-a"},
        "schema": "node.status",
        "version": 1,
        "priority": "normal"
      }
    }
  }
}
```

The shared ABI is `adaos.operational-event-envelope.v1`.

Required base fields:

- `type`
- `source`
- `ts`
- `payload`

Preferred metadata fields:

- `event_id`
- `source_authority`
- `actor`
- `scope`
- `trace_id`
- `cause_event_id`
- `schema`
- `version`
- `priority`

Use `normalize_event_envelope()` before routing event data into dispatchers,
diagnostics, or policy-aware logic. Use `enrich_event_payload()` or
`adaos.sdk.data.events.publish()` when producing events.

## Naming

Event names are dot-separated topics.

Recommended patterns:

- facts: `namespace.entity.changed`, `namespace.entity.created`,
  `namespace.entity.deleted`, `namespace.action.completed`
- requests: `namespace.action.requested`
- lifecycle: `namespace.lifecycle.started`, `namespace.lifecycle.ready`,
  `namespace.lifecycle.stopped`
- diagnostics: `namespace.diagnostics.changed`, `namespace.failure.recorded`
- projection lifecycle: `adaos.projection.lifecycle.changed`

Current examples in code include:

- `browser.session.changed`
- `subnet.member.snapshot.changed`
- `adaos.status.card.changed`
- `adaos.status.card.single`
- `adaos.status.card.batch`
- `adaos.projection.lifecycle.changed`
- `webio.stream.snapshot.requested`
- `webio.stream.subscription.changed`
- `webio.yjs.snapshot.requested`
- `webio.yjs.subscription.changed`
- `io.out.stream.publish`
- `builder.workbench.ensure_requested`
- `builder.preview.selected`

Avoid names that encode implementation details, file names, temporary UI
component names, or transport internals unless the event is explicitly a
transport diagnostic.

## Event Categories

### Domain Events

Domain events are facts about runtime or product state:

- skill installed
- scenario selected
- browser session changed
- subnet member snapshot changed
- status card changed

They should not directly write large browser-visible state. They should update
runtime memory and trigger demanded projection refresh where appropriate.

### Interaction Events

Interaction events coordinate core services, skills, and scenarios:

- a runtime surface should refresh
- a Builder workbench projection is needed
- a skill should warm or cool a projection family
- a repair or pending-action path needs attention

These events are internal contracts. They may later produce browser-visible
projections, but they are not themselves UI payloads.

### UI Intent Events

UI intent events represent what a browser or runtime shell asks to show or do:

- open a modal
- select a panel
- request a snapshot
- change a stream subscription

The Operational Event Model owns the detailed projection-demand rules. This
document only states the boundary: UI intent may change demand; it must not
become an unbounded stream of full projection writes.

### Platform Events

Platform events are emitted by AdaOS itself:

- status cards
- notifications
- runtime diagnostics
- projection lifecycle changes
- transport degradation
- materialization failures

These events should not be hidden inside one skill payload. They belong to the
platform plane and can be projected to UI surfaces when demanded.

## Local Event Bus Semantics

`LocalEventBus` is the current in-process implementation.

It supports:

- `subscribe(type_prefix, handler)`
- `publish(Event(...))`
- prefix matching
- async and sync handlers
- thread-safe scheduling onto the owning event loop
- bounded queues for selected high-rate topics
- superseding stale queued work for selected topic/key combinations
- slow handler and crash incident reporting
- backlog snapshots for diagnostics
- bounded retained snapshots for explicitly configured state topics

Default bounded topics include stream/Yjs control events, stream publishes,
browser session changes, status-card changes, projection lifecycle changes, and
subnet member snapshot changes.

High-rate producers should prefer events that can be coalesced by key:

- webspace id
- node id
- projection key
- card id
- receiver id
- stream id
- source
- parameters fingerprint

## Publishing Events

Preferred SDK path:

```python
from adaos.sdk.data.events import publish

publish(
    "demo.event",
    {"value": 1},
    source="skill.demo",
    source_authority="skill",
    scope={"webspace_id": "desktop"},
    schema="demo.event",
    version=1,
    generate_event_id=True,
)
```

Service-level code may use `adaos.services.eventbus.emit()` when it already has
the bus instance:

```python
from adaos.services.eventbus import emit

emit(
    bus,
    "demo.event",
    {"value": 1},
    "service.demo",
    source_authority="platform",
    scope={"webspace_id": "desktop"},
    schema="demo.event",
    version=1,
    generate_event_id=True,
)
```

Do not mutate payloads after publishing. Treat payloads as immutable event data.

## Subscribing

Use narrow prefixes where possible:

```python
bus.subscribe("adaos.status.card.", handle_status_card_event)
```

Avoid subscribing to all events in production handlers unless the handler is a
bounded diagnostics collector.

State-like topics that must support late consumers can be retained by core. A
skill declares immediate replay and reads the same snapshot through the SDK:

```python
from adaos.sdk.core.decorators import subscribe
from adaos.sdk.status import current_update_status

@subscribe("core.update.status", replay_latest=True)
async def on_update_status(event):
    status = current_update_status()
```

Retained delivery is in-memory and bounded to exact allowlisted topics. Durable
recovery remains the producer's responsibility; consumers must not read the
producer's files directly.

Handlers should:

- be idempotent
- return quickly
- delegate expensive work to bounded queues or services
- tolerate repeated delivery
- tolerate missing optional metadata
- normalize the envelope before relying on trace/scope/schema fields
- never perform unbounded Yjs writes directly from a hot event path

## Reliability Model

Current local runtime semantics are best-effort in-process delivery with
bounded protection for selected hot topics.

Target reliability directions:

- keep the `EventBus` port transport-neutral
- add durable or inter-node transport only behind the same event contract
- use idempotency keys or event ids for side-effecting consumers
- use bounded retry/dead-letter patterns only for operations that genuinely
  require durability
- keep projection refresh coalesced rather than replaying every intermediate
  change

NATS/JetStream or another broker may be used later for inter-node delivery, but
the current architecture should not bake broker-specific subject rules into
skill or scenario contracts.

## Security And Policy

Events must not carry:

- bearer tokens
- secrets
- private keys
- raw credentials
- unbounded logs
- large binary payloads
- direct browser trust assumptions

Events that can lead to mutation must carry enough metadata for policy:

- actor
- source authority
- scope
- trace id
- schema/version
- target ids
- side-effect or priority hints when relevant

Policy decisions and human approvals belong in the relevant service or Pending
Action flow, not in ad hoc event handlers.

## Relationship To Projections And Yjs

The event bus is not a Yjs write API.

Correct flow:

```text
domain/platform event
  -> service/skill runtime memory update
  -> demanded projection refresh selected by dispatcher
  -> ProjectionRecord update if fingerprint changed
  -> Yjs materialization for active webspace demand
```

Incorrect flow:

```text
domain event
  -> direct large Yjs branch rewrite
```

Detailed projection demand, lifecycle, node scope, and browser materialization
rules are defined in the Operational Event Model documents.

## Relationship To Root MCP

Root MCP is not the event bus.

Root MCP exposes typed tool calls, descriptors, audit history, managed target
status, and operational snapshots to agents. Events may feed Root MCP reports
or audit surfaces, but agents should not treat event topics as a general-purpose
remote command API.

Use Root MCP for agent-facing inspection and controlled operations. Use events
for runtime coordination and projection invalidation.

## Skill And Scenario Declarations

The target direction is for skills and scenarios to declare event interests in
their manifests when those interests are part of their public contract.

Example skill manifest direction:

```yaml
events:
  subscribe:
    - "adaos.status.card.changed"
  publish:
    - "demo.skill.result.changed"
```

Example scenario trigger direction:

```yaml
triggers:
  - event: "system.boot.completed"
effects:
  - publish: "demo.scenario.started"
```

These declarations should not replace runtime policy checks. They are
descriptive contracts for validation, documentation, Builder, and future Root
MCP descriptors.

## Event Registry Direction

AdaOS should converge on a lightweight event registry, but it should align with
the existing ABI and descriptor system instead of creating a parallel catalog.

Target registry fields:

- `type`
- `version`
- `kind`: `fact`, `request`, `lifecycle`, `diagnostic`, `projection`
- `summary`
- `payload_schema`
- `producers`
- `consumers`
- `source_authority`
- `scope_model`
- `delivery_notes`
- `idempotency`
- `status`: `draft`, `stable`, `deprecated`

Registry entries should be published through Root descriptors when stable
enough for Builder, NLU Teacher, Skill Factory, and documentation consumers.

## Operational Guidance

When adding a new event:

1. Check whether an existing event already covers the runtime fact.
2. Name the event as a stable semantic contract.
3. Define the payload shape and expected metadata.
4. Decide whether the event is domain, interaction, UI intent, platform, or
   projection lifecycle.
5. Decide whether consumers must be idempotent.
6. Decide whether the topic needs bounded/coalesced handling.
7. Add tests for envelope enrichment or normalization if new metadata is used.
8. Link projection effects through the dispatcher, not direct Yjs writes.
9. Document public event contracts in manifests or descriptors when they are
   part of a skill/scenario surface.

## Roadmap Summary

This file does not own the implementation roadmap. The authoritative ordering
is in [Operational Event Model Roadmap](operational-event-model-roadmap.md).

From the current code baseline, the near-term event-management priorities are:

- adopt `_meta.event` enrichment in more platform and projection-related
  producers
- keep legacy `Event(type, payload, source, ts)` compatibility during migration
- route projection-related topics through the shared dispatcher path
- add manifest/descriptor exposure for stable skill/scenario event contracts
- expand diagnostics around bounded queues, superseded work, drops, and slow
  handlers
- avoid new direct Yjs writes from hot event handlers

Deferred target-state items:

- durable broker-backed delivery
- cross-subnet event federation
- full event registry with schema examples
- replay/debug tooling
- global event sourcing

Those should be added only after the current local envelope, projection
dispatcher, and platform-emitter paths are consistently adopted.
