# Declarative Resource Workbench

Status: target architecture.

Last reviewed: 2026-08-30.

This document defines the target AdaOS architecture for a reusable declarative
resource workbench. It turns repeated list/detail/form/action UI patterns into
typed resource declarations without making a generic CRUD generator the source
of truth for domain logic.

It refines the [Web UI Architecture](web-ui-architecture.md), the
[Operational Event Model](operational-event-model.md),
[Development Signals And Evolution Feedback](development-signals.md),
[Executable Prototype Architecture](executable-prototype-architecture.md), and
the [AdaOS Builder](builder.md). The implementation sequence is owned by the
[Declarative Resource Workbench Roadmap](declarative-resource-workbench-roadmap.md).

## Decision Summary

The workbench is a declarative layer over typed resources, typed queries,
typed operations, workflow transitions, events, evidence, and UI views.

It is not:

- a direct database-table admin UI;
- a replacement for skill-owned domain services;
- a hidden mock backend;
- a second lifecycle truth beside Builder, NLU Teacher, Development Signals,
  workflows, or domain stores;
- an automatic public issue tracker.

CRUD is one supported operation family. AdaOS resources also need commands,
transitions, append-only revisions, evidence gates, artifact actions,
subscriptions, and conversational routes. A resource may be backed by a skill
tool, runtime API, synthetic prototype store, relational store, external
connector, or Builder prototype binding.

The first real proof is Dev Tickets. The first low-risk demonstration surface
is Demo Metrics. Notebook is the first simple CRUD migration candidate. Media
Center and Research Workbench are later adoption targets that prove the model
does not collapse complex domains into naive edit forms.

## Why This Exists

Current skills already implement CRUD-like behavior explicitly:

- Notebook exposes note list, create, select, save, attach, and delete tools.
- Media Center exposes roots, playlists, playlist items, metadata claims,
  profiles, favorites, catalog browsing, and playback commands.
- Research Workbench exposes studies, experiments, revisions, attempts,
  evidence, and workflow commands.
- Dev Tickets expose a governed backlog lifecycle over Development Signals.

Those implementations are valuable because they contain domain rules. The
problem is that every skill currently repeats its own UI wiring, filtering,
selection state, action rails, evidence views, screenshots, event logs,
diagnostics, and Builder handoff conventions.

The workbench should preserve domain authority while making the common surface
declarative, inspectable, testable, and usable by both people and agents.

## Core Concepts

### Resource Definition

A resource definition is the typed declaration that tells AdaOS how one class
of objects can be inspected, queried, mutated, observed, and routed.

Minimum conceptual shape:

```json
{
  "schema": "adaos.resource.definition.v1",
  "resource_type": "adaos.dev.ticket",
  "version": "1.0.0",
  "title": "Dev Ticket",
  "scope": {
    "owner": "workspace",
    "target_refs": ["workspace", "project", "skill", "scenario", "surface"]
  },
  "authority": {
    "provider": "api",
    "binding": "development_tickets",
    "writes": "governed",
    "source_of_truth": "workspace_inbox"
  },
  "record_schema_ref": "abi:dev_ticket.v1",
  "query": {
    "default": "open",
    "filters": ["status_group", "scenario_id", "skill_id", "surface_id", "severity", "blocking", "owner", "updated_since", "search"],
    "sort": ["updated_at", "severity", "relevance"],
    "cursor": true
  },
  "operations": [
    {"id": "create", "kind": "create"},
    {"id": "postpone", "kind": "transition"},
    {"id": "open_builder", "kind": "handoff"},
    {"id": "resolve", "kind": "transition", "requires": ["evidence_ref"]},
    {"id": "verify", "kind": "transition", "requires": ["validation_evidence_ref"]},
    {"id": "close", "kind": "transition"},
    {"id": "reopen", "kind": "transition", "requires": ["reason"]}
  ],
  "views": ["list", "detail", "form", "evidence", "events", "trace"],
  "events": {
    "emits": ["resource.record.created", "resource.operation.completed"],
    "semantic_types": ["dev_ticket.created", "dev_ticket.verified"]
  }
}
```

The definition is the authoritative declarative header. It owns identity,
version, scope, authority, operation contracts, query affordances, UI affordance
hints, event expectations, privacy flags, and lifecycle links. Domain payloads
may stay in existing skill stores or services.

### Resource Record

A resource record is one domain object visible through a resource definition.
The workbench should index enough metadata for cross-resource navigation and
query without copying all domain truth.

Minimum indexed fields:

- stable `resource_ref`;
- `resource_type` and definition version;
- owner scope and target scope;
- title, summary, status, status group, severity, blocking flag;
- created, updated, actor, assignee or owner hints;
- version, digest, revision, and stale markers when relevant;
- relation refs;
- artifact and evidence refs;
- search text and tags when policy permits.

### Resource Operation

Operations are typed user- or agent-visible actions. Standard CRUD verbs are
only one group.

Operation kinds:

- `list`, `show`, `search`;
- `create`, `update`, `patch`, `delete`, `archive`;
- `transition`, `claim`, `comment`, `verify`, `reopen`;
- `command` for domain effects such as play, scan, start, retry, lock;
- `handoff` for Builder, NLU Teacher, external issue draft, or support routing;
- `attach_artifact`, `open_artifact`, `preview_evidence`;
- `subscribe`, `refresh`, `rebuild_projection`.

Every operation declares input schema, output schema, idempotency policy,
authority, risk, privacy, optimistic revision behavior, expected events, and
trace fields. The provider executes the operation; the workbench standardizes
how it is rendered, invoked, observed, and validated.

### Resource Query

AdaOS needs one query vocabulary even when providers implement it differently.

The query shape should support:

- exact filters: type, status, status group, scope, owner, assignee, severity,
  blocking, project, skill, scenario, modal, component, source, target version;
- relation filters: related-to, duplicate-of, parent, child, depends-on,
  blocks, derived-from, produced-by;
- text search over policy-approved indexed fields;
- relevance ranking by current Builder task, open files, active scenario,
  current modal, current conversation, commit diff, and recent evidence;
- time filters and sorting;
- cursor pagination;
- `include` hints for heavy fields such as evidence, artifacts, trace, comments,
  and full payload.

Providers may translate this query into SQL, a skill tool call, Yjs projection
read, in-memory prototype store lookup, or external API query. Unsupported
filters must be reported explicitly, not silently ignored.

### Resource View

A resource view is a declarative UI surface over one resource query or one
record.

Common view kinds:

- list or collection grid;
- detail;
- form;
- action rail;
- artifact/evidence preview;
- event log;
- trace inspector;
- relation graph;
- workflow state and transition panel;
- conversational intake panel.

The Web UI renderer decides the concrete toolkit. The definition decides which
fields, operations, states, and constraints exist.

### Resource Event

Every material operation should emit a generic resource event and may also emit
a domain semantic event.

Conceptual envelope:

```json
{
  "schema": "adaos.resource.event.v1",
  "event_id": "evt_...",
  "event_type": "resource.operation.completed",
  "semantic_type": "dev_ticket.resolved",
  "resource_ref": "dev-ticket:dticket...",
  "resource_type": "adaos.dev.ticket",
  "operation_id": "resolve",
  "actor_ref": "user:...",
  "scope": {"workspace_id": "desktop"},
  "revision": "7",
  "evidence_refs": ["test:...", "trace:..."],
  "trace_ref": "trace:...",
  "occurred_at": "..."
}
```

The event model follows [Operational Event Model](operational-event-model.md):
events are facts, Yjs state is a projection, and browser streams are delivery
channels. Where policy requires it, events should be signed or accompanied by
tamper-evident receipts before cross-node or support export.

## Storage Direction

The first durable store should be relational with JSON payload columns, not an
object database as the primary system of record.

Reasoning:

- cross-resource filtering and dedup need indexed scope, status, relation, and
  lifecycle fields;
- evidence-gated transitions need transactional operation ledgers;
- Builder and Codex need stable query surfaces over many domains;
- domain payloads remain heterogeneous and versioned, so JSON columns or
  provider-owned blobs are still useful;
- skills such as Media Center and Research already have meaningful domain
  stores that should not be replaced wholesale.

Recommended shared tables or equivalent service concepts:

- `resource_definition`: declarative headers and digests;
- `resource_record_index`: searchable/indexed record metadata;
- `resource_relation`: duplicate, related, parent, child, source, target,
  produced-by, blocked-by, and supersession edges;
- `resource_operation`: attempted and completed operations, idempotency keys,
  actor, authority, input/output summaries, and result state;
- `resource_event`: generic resource events with semantic type and trace refs;
- `resource_artifact_ref`: screenshots, logs, test reports, traces, content
  refs, retention and sensitivity metadata;
- `resource_trace`: query, render, provider, operation, validation, and event
  delivery diagnostics;
- `workflow_instance_link`: association between resources and governed
  workflow instances when lifecycle is workflow-owned.

This shared store is an index and operation ledger. It is not automatically the
domain truth for every resource.

## Provider Bindings

The workbench must admit several provider kinds:

| Provider | Use |
| --- | --- |
| `skill_tool` | Existing skill handlers remain authoritative, while the workbench describes their query and operation surface. |
| `api` | Core services such as Dev Tickets expose typed endpoints directly. |
| `synthetic` | Builder and Demo Metrics use deterministic examples or disposable stores. |
| `sql` | Future resources may be backed by a shared relational store when domain ownership permits it. |
| `projection` | Read-only or cached Yjs projections can be viewed without becoming truth. |
| `external` | Connectors and issue trackers can be linked or drafted through policy-aware adapters. |

Provider adapters must report capability, unsupported filters, latency,
validation diagnostics, revision conflicts, and emitted events.

## Builder Integration

Builder is both a consumer and a producer of resources.

As a consumer, Builder needs:

- current task-related ticket/resource queries;
- list/detail/forms for domain resources;
- artifact and evidence previews;
- traces for query, rendering, operation, and event delivery;
- source availability choices for missing DEV source:
  `use_existing_source`, `materialize`, `fork`, `overlay`, or `defer`.

As a producer, Builder needs:

- synthetic data and disposable local CRUD during prototype work;
- resource operation requirements handed to Automation;
- validation evidence written back to tickets or domain resources;
- operation traces and cost estimates visible before autonomous repair;
- comments, claim/in-progress state, resolve evidence, verify evidence, close,
  duplicate, related, and reopen lifecycle operations.

Builder planning remains Builder-owned. The workbench supplies typed resource
access, not a competing development state machine.

## Observability

The minimum workbench observability layer is part of the product contract,
especially for Builder.

The first read-only Builder inspector should expose:

- resource definition id, version, digest, provider, capabilities, and schema;
- query trace: filters, sort, cursor, provider, latency, row count, unsupported
  query features, and source projection;
- operation trace: operation id, actor, payload summary, validation result,
  authority, idempotency key, revision, risk, and result;
- event trace: emitted events, subscribed handlers, delivery status, dropped or
  superseded deliveries, and projection invalidations;
- render trace: selected resource view, renderer, missing fields, component
  refs, validation errors, and degraded mode;
- evidence: screenshots, test refs, runtime guard refs, log refs, trace refs,
  and artifact preview status.

The inspector should be read-only first. Mutating debug tools can be added only
after operation authority and audit are in place.

## Development Signals And Dev Tickets

Dev Tickets are the first production resource family.

Target lifecycle:

```text
captured/proposed
  -> accepted
  -> claimed/in_progress
  -> resolved
  -> verified
  -> closed
```

`resolved` means a candidate fix or decision exists and evidence has been
attached. It is not final acceptance. `verified` means acceptance evidence has
passed. `closed` is terminal for the current lineage. `reopen` is an operation
that creates a new lifecycle event and returns the ticket to an active state
with regression or insufficiency evidence.

GitHub Issues and other trackers are optional external refs or redacted
exports. They are not the primary AdaOS resource.

## Demo Metrics Harness

Demo Metrics is the preferred safe harness for the first visual workbench
demonstration because it already exercises:

- Yjs projections;
- semantic collection views;
- chart views;
- selection state;
- stream events;
- host and skill actions;
- an operations surface.

Recommended demo resources:

- `demo.metric`: read/list/detail/query with filters for group, status, and
  text search;
- `demo.metric_note`: synthetic CRUD with create/edit/delete, validation
  errors, revision conflict, and optimistic update behavior;
- `demo.metric_event`: event stream and operation trace demonstration;
- `demo.metric_artifact`: preview-only artifact/evidence sample.

The demo should prove the renderer and observability model. It must not become
the canonical Dev Ticket implementation.

## Skill Adoption Model

### Notebook

Notebook is the first simple CRUD migration candidate.

Target resource split:

- `notebook.note`: list, show, create, update, delete;
- `notebook.note_attachment`: attach, list, open, remove;
- `notebook.session`: selected note and local view state, not domain truth;
- optional `notebook.export`: send/share operations such as Telegram delivery.

This should demonstrate how existing skill tools can be declared as resource
operations before any domain logic is rewritten.

### Media Center

Media Center should adopt the workbench selectively.

Good first resources:

- `media.root`: list, add, disable/remove, delete, scan;
- `media.playlist`: list, create, update, delete;
- `media.playlist_item`: add, remove, reorder;
- `media.profile`: inspect and update safe policy fields;
- `media.metadata_claim`: list, accept/reject, evidence and provenance view;
- `media.catalog_item`: read/search/facet only at first.

Playback, queue execution, scanning, rendition jobs, and distributed route
planning are domain commands, not generic CRUD.

### Research Workbench

Research Workbench is an advanced adoption target.

Resources include studies, experiments, protocol revisions, attempts, evidence
bundles, tracker projections, reviews, and claim decisions. Most operations
are workflow transitions or append-only revisions. This domain proves that the
workbench must support workflow-bound resources, evidence gates, immutable
history, verification, and audit rather than table editing.

## Conversational And Channel Route

Resources should be routable from conversational, voice, Telegram, browser,
Builder, and Codex surfaces through the same operation contracts.

The conversational layer should classify whether the user expects:

- immediate action;
- understanding correction;
- feedback note;
- development request;
- personal adaptation;
- support explanation;
- runtime failure repair.

For resource workbench purposes, the output is a typed resource intent:

```json
{
  "kind": "resource_operation_intent",
  "resource_type": "adaos.dev.ticket",
  "operation_id": "create",
  "scope": {"scenario_id": "media_center"},
  "payload": {"summary": "The metadata modal needs clearer actions"},
  "conversation_ref": "conv:..."
}
```

NLU Teacher owns understanding correction. Feedback Skill and Dev Tickets own
development feedback. Builder owns implementation planning. The workbench
gives all of them a common resource route without merging their state machines.

## Invariants

- Resource definitions are versioned and digest-addressable.
- Resource operations are typed, validated, authorized, and observable.
- Provider-specific behavior must be explicit in capabilities and traces.
- Yjs and streams are projections/delivery, not the source of truth.
- Screenshots, logs, audio, DOM snapshots, and traces are artifact refs with
  sensitivity and retention metadata.
- The UI can render a useful read-only view before mutating operations exist.
- Builder can inspect why a resource view or operation behaved as it did.
- Domain services keep domain logic; the workbench standardizes the control
  surface.
- External issue trackers are optional projections with redaction and approval.

