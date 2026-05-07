# UI Addressing

## Purpose

This document fixes the target addressing model for AdaOS browser-facing UI,
runtime manifests, projections, and domain-facing operator surfaces.

The goal is not to replace every existing path or identifier with one universal
string format.
The goal is to define one coherent vocabulary so that:

- skills and scenarios can be authored by humans and LLMs without guessing
- browser-facing manifests can bind data and actions safely
- runtime services can preserve scope and ownership across Yjs, streams, and
  projections
- domain-specific surfaces such as device access can reuse the same model

This is a target-state architecture document.
Current implementation details remain documented in:

- [Web IO](../io/webio.md)
- [Operational Event Model](operational-event-model.md)
- [Webspace Evolution Roadmap](webspace-evolution-roadmap.md)
- [Device Access and Browsers](device-access-and-browsers.md)

## Governing Rules

1. Do not expose raw storage topology as the primary authoring contract.
2. Keep logical authoring addresses separate from runtime materialization refs.
3. Keep domain identity refs separate from state refs and action refs.
4. Keep scope explicit whenever state may be shared, node-owned, or local.
5. Prefer deterministic, typed refs over ad hoc string conventions.
6. LLM-oriented authoring should use a compact, repetitive vocabulary.

## Non-Goals

This document does not require:

- one URI syntax for every internal AdaOS concept
- immediate replacement of existing `ctx.*`, `data_projections`, or
  `webio.stream.*` contracts
- direct skill authoring against Yjs paths
- a hard mandate that every ref becomes browser-visible

## Addressing Layers

AdaOS now needs one model with several layers, not one flat namespace.

### 1. Logical authoring layer

This is the layer skills and scenarios should reason about first.

Examples:

- `ctx.user.profile`
- `ctx.state.review.selection`
- logical projection slots declared through `data_projections`

This layer is stable for authoring and should remain storage-agnostic.

### 2. Projection-routing layer

This is the layer that maps logical state to runtime backends.

Examples:

- `(scope, slot)` pairs in `data_projections`
- scenario- or skill-owned mapping into Yjs, KV, SQL, or future backends

This layer is authoritative for data placement decisions.

### 3. Runtime UI binding layer

This is the layer browser-facing manifests and runtime adapters bind to.

Examples:

- Yjs-backed state refs
- browser stream receiver refs
- local view-state refs
- action refs
- projection refs

This layer is where semantic UI manifests should live.

### 4. Domain identity layer

This layer identifies real entities and reusable domain objects.

Examples:

- `device:browser:<device_id>`
- `device:member:<node_id>`
- `webspace:<webspace_id>`

This layer is shared across UI, SDK, runtime, and operator tooling.

## Canonical Ref Classes

The target model uses a small typed vocabulary.

### Logical refs

Logical refs are authoring-level and storage-agnostic.

Examples:

- `ctx.user.profile`
- `ctx.ui.web.desktop`
- `ctx.voice.morning.current_step`

### Runtime state refs

Runtime state refs bind browser-visible state to a concrete runtime-backed
state family.

Examples:

- `y:data/reviews/current`
- `y:data/nodes/member-01/infrastate/summary`
- `y:ui/application`

### Stream refs

Stream refs identify transport-independent live browser receiver families.

Examples:

- `stream:chat.live`
- `stream:infrastate.operations.active`

Stream refs identify the semantic receiver family.
Webspace targeting, node ownership, and transport preference remain binding
properties rather than part of the ref identity itself.

### View-state refs

View-state refs identify client-owned interaction state.

Examples:

- `view:filters.status`
- `view:columns.enabled`
- `view:review.selection`

### Projection refs

Projection refs identify demanded materialized UI views.

Examples:

- `projection:overview`
- `projection:inventory:members`
- `projection:modal:workspace-manager`

`projection_key` remains the canonical key within projection payloads.
This document treats `projection:<projection_key>` as the addressing form used
when projection identity must participate in a broader ref vocabulary.

### Action refs

Action refs identify typed browser-visible or runtime-visible operations.

Examples:

- `action:skill.chat.send`
- `action:device_access.rename`
- `action:webspace.open`

For current compatibility work, one action ref may still resolve through
existing `callSkill`, `callHost`, or modal-opening behavior.
The important boundary is to keep the browser-facing action identity stable
even while the underlying compatibility bridge evolves.

### Domain refs

Domain refs identify reusable domain entities rather than UI state.

Examples:

- `device:browser:abc123`
- `device:member:member-01`
- `webspace:desktop`

## Scope Model

Addressing is not complete without scope.

The target scope vocabulary is:

- `shared`: collaborative state shared within one webspace
- `node`: state owned by one node inside a shared webspace
- `local`: browser-local state not synchronized through shared runtime state
- `workspace`: durable or logical scenario/workspace scope outside a browser
  local session

The preferred semantic binding shape is an object, not only a string:

```json
{
  "kind": "y",
  "path": "data/infrastate/summary",
  "scope": "node",
  "nodeRef": "$context.nodeId"
}
```

This avoids encoding too much meaning into one ad hoc path string.

## Canonical vs Derived State

The model must distinguish canonical truth from browser-facing derived state.

### Canonical examples

- logical state behind `ctx.*`
- durable workspace metadata
- shipped scenario content
- shipped skill `webui.json`
- access-link policy records

### Derived examples

- `ui.application`
- demanded projection payloads
- grouped or filtered client view state
- browser stream buffers

Yjs may contain both live collaborative truth and derived materialization, but
the addressing model must not blur those ownership boundaries.

## Relationship to Existing Contracts

### `ctx.*`

`ctx.*` remains the primary logical authoring contract for skills and
scenarios.
It is not replaced by browser-visible Yjs or stream refs.

### `data_projections`

`data_projections` remain the canonical routing layer between logical state and
physical backend placement.
This document does not replace `(scope, slot)` pairs.
It makes clear how browser-facing refs relate to that routing layer.

### `webio.receivers`

`webio.receivers` remain the canonical declaration mechanism for
transport-independent browser stream families.
This document treats each receiver family as a `stream:<receiver>` ref at the
addressing level.

### `projection_key`

`projection_key` remains the canonical deterministic identity for demanded
materialized UI views.
This document only gives it a consistent typed-ref role in the wider
vocabulary.

### `DeviceRef`

Device-facing refs such as `browser:<device_id>` and `member:<node_id>` should
evolve toward an explicitly typed domain-ref vocabulary:

- `device:browser:<device_id>`
- `device:member:<node_id>`

Device-facing architecture may continue to document the shorter
`browser:<device_id>` and `member:<node_id>` forms during migration, but the
target vocabulary should be explicit about the domain class.

## Priority Slice for Web UI

The first addressing slice needed by the browser runtime is intentionally
small.

### Required first

- Yjs state refs
- stream refs
- local view-state refs
- typed action refs
- projection refs
- `webspace:<id>` and `device:*` domain refs where browser surfaces need them

### Deferred

- broad generalized URI layers for non-UI backends
- a universal cross-plane query language
- automatic ref rewriting across all historical contracts
- per-user projection payload forks

## Recommended Binding Shape for Semantic UI

Semantic UI manifests should bind through typed objects rather than opaque
renderer-specific strings.

Example:

```json
{
  "source": {
    "kind": "stream",
    "ref": "stream:chat.live",
    "scope": "shared"
  },
  "viewState": {
    "ref": "view:chat.input"
  },
  "actions": [
    {
      "ref": "action:skill.chat.send",
      "trigger": "submit"
    }
  ]
}
```

For chart-like views, a compatible semantic binding should follow the same
shape and only vary by semantic kind and source contract, not by introducing a
second addressing model for time-series data.

## LLM Authoring Guidance

LLM-authored skills and scenarios should follow these rules:

- prefer declared logical paths and typed refs over inventing new branches
- do not write directly to arbitrary Yjs paths unless the runtime contract
  explicitly exposes them
- keep business truth out of `view:*`
- keep renderer decisions out of addressing
- keep node ownership explicit instead of implied
- prefer one of the documented ref classes before introducing a new one

## Roadmap

### 0. Vocabulary Fixation

- [ ] publish this addressing vocabulary as the canonical browser/runtime
  reference model
- [ ] freeze the distinction between logical, routing, runtime, projection,
  action, and domain refs
- [ ] align naming guidance across `ctx.*`, `data_projections`,
  `webio.receivers`, and `projection_key`

### 1. Cross-Document Harmonization

- [ ] update scenario and skill architecture docs so `ctx.*` is explicitly the
  logical authoring layer
- [ ] update projection docs so `projection_key` is explicitly the projection
  ref identity
- [ ] update device-access docs so `DeviceRef` is documented as a domain-ref
  slice
- [ ] update browser/runtime docs so Yjs, streams, and local view state are
  described through the same scope vocabulary

### 2. Web UI Priority Slice

- [ ] define the first browser-facing binding object shape for `y`, `stream`,
  and `view` refs
- [ ] define typed browser action refs for the first semantic UI actions
- [ ] define the first canonical `webspace:<id>` and `device:*` refs needed by
  workspace and device surfaces
- [ ] define the first projection-ref usage rules for pages, widgets, modals,
  and platform-emitted projections
- [ ] define the first shared binding pattern that both table-like and
  chart-like semantic views can reuse

### 3. Runtime and ABI Alignment

- [ ] teach runtime manifest and semantic UI ABI docs to use the same ref
  vocabulary
- [ ] align browser adapters with explicit scope handling instead of implicit
  path conventions alone
- [ ] preserve compatibility for current `webui.json`, Yjs, and stream
  contracts during migration

### 4. Authoring and Tooling

- [ ] publish LLM-oriented authoring examples using the finalized ref classes
- [ ] validate manifests against allowed ref kinds and scope combinations
- [ ] add repository examples covering shared, node-scoped, and local browser
  state

### 5. Deferred Generalization

- [ ] decide whether a generalized non-UI data URI layer is still needed after
  browser/runtime addressing stabilizes
- [ ] if kept, position that layer as a facade over the canonical addressing
  model rather than as a competing architecture
