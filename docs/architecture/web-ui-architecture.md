# Web UI Architecture

## Purpose

This document fixes the target architecture for the AdaOS browser client.

The target is a stable universal client that:

- does not require client-core changes for every new skill
- accepts UI as data rather than shipped framework code
- uses Taiga UI as the primary rich presentation toolkit
- keeps Ionic focused on shell, navigation, and mobile-friendly interaction
- supports staged loading, lazy rendering, Yjs-backed shared state, browser
  streams, and LLM-oriented UI evolution

This document is target-state architecture.
It is intentionally compatible with the current `webui.v1` runtime manifest and
the current Angular/Ionic client while defining the next structural boundary.

## Governing Rules

1. Skills and scenarios contribute manifests, semantic views, bindings, and
   typed actions, not arbitrary Angular or Taiga code.
2. Taiga UI is a renderer toolkit, not the canonical language of the manifest.
3. Shell/runtime concerns, semantic UI contracts, and renderer-specific
   presentation details must remain separate.
4. Browser-facing state must distinguish domain truth, stream state, and
   client-owned view state.
5. The same semantic schema should be renderable in desktop-rich and
   mobile-compact profiles.

## Current Implementation Base

The current runtime already provides important pieces of the target:

- `webui.v1` as a shipped skill contribution ABI
- `catalog`, `registry`, `webio.receivers`, `ydoc_defaults`, and
  `contributions`
- dynamic widget and modal host behavior in the browser client
- Yjs-backed collaborative state
- transport-independent browser stream receivers
- coarse intent-level `loadHint` support

The target architecture builds on those pieces rather than replacing them.

## Layer Model

### 1. Shell layer

The shell is the stable browser container.

Responsibilities:

- app bootstrap
- routing
- desktop/workspace/operations container surfaces
- session and connection state
- modal and overlay hosts
- local UI preference handling
- responsive profile selection
- renderer registry and lazy loading

The shell must not own skill business logic.

### 2. Runtime manifest layer

The current `webui.v1` remains the runtime shell manifest layer.

Responsibilities:

- catalog of apps and widgets
- modal and widget registry declarations
- `webio.receivers`
- `ydoc_defaults`
- extension-point contributions
- load hints

This layer remains the shipped contribution contract for current skills.

### 3. Semantic view layer

This is the main new contract.

It describes UI in semantic terms rather than framework components.

Representative kinds:

- `collection_grid`
- `metric_chart`
- `form_matrix`
- `form`
- `detail_view`
- `event_log`
- `chat_panel`
- `status_panel`
- `dashboard`
- `tree_view`
- `document_view`
- `review_surface`
- `operations_view`

This layer is the primary contract between skill/scenario authoring, runtime,
LLM tooling, and browser renderers.

### 4. Renderer layer

Renderers translate semantic views into concrete UI.

Primary renderer:

- Taiga renderer for rich workspace and operator surfaces

Supporting renderers:

- Ionic shell renderer for shell/navigation/mobile interaction
- fallback/basic renderer for degraded or compatibility modes
- future compact/mobile renderer profile

### 5. Data and state layer

The browser must distinguish:

- domain state
- stream state
- local or selectively synchronized view state

Primary sources:

- Yjs-backed shared state
- browser stream receivers
- browser-local interaction state

### 6. Action layer

Actions must move from loosely typed button behavior to typed semantic actions.

Representative action kinds:

- `emit`
- `open_modal`
- `navigate`
- `call_host`
- `set_view_state`
- `patch_y`
- `invoke_skill_action`
- `open_workspace`
- `apply_review_change`

## Top-Level Surface Model

The browser client should be organized around three top-level surface classes.

### Desktop

The user's operational home surface.

Typical content:

- apps
- widgets
- pinned views
- active runs
- alerts and errors
- recommendations
- quick actions

### Workspace

The focused working surface for one entity or one bounded operational context.

Typical workspace targets:

- skill
- scenario
- agent
- run
- review artifact
- resource

Workspaces should be composed from object kind, capabilities, lifecycle stage,
and available semantic views rather than from one bespoke screen per entity
class.

### Operations

The universal surface for observation and execution state.

Typical content:

- runs
- queues
- execution stack
- health
- logs
- errors
- traces
- replay
- pending actions

## Capability Model

Entities should describe capabilities, not browser code.

Representative capabilities:

- `inspectable`
- `configurable`
- `listable`
- `streamable`
- `eventful`
- `runnable`
- `testable`
- `reviewable_by_llm`
- `versioned`
- `publishable`
- `searchable`
- `composable`

Capability-aware workspace composition should remain a composition concern, not
an excuse to create a second hidden business model.

## Semantic View Contracts

### Collection grid

The canonical semantic type for:

- sortable/filterable lists
- table-like review and operations surfaces
- grouped collections
- selection-driven workflows
- bounded inline editing where appropriate

Columns must be described through semantic display and editor contracts rather
than through Taiga directives.

### Form matrix

This is a distinct semantic type for field-centric grid layouts where the table
is a layout container, not only a collection view.

### Event log

This is the canonical semantic type for append-heavy runtime tails, logs,
notifications, and status feeds.

### Chat panel

This is the canonical semantic type for assistant-like interaction.

### Metric chart

This is the first chart-oriented semantic type needed by the browser MVP.

It should cover the browser-facing needs of:

- time-series metrics
- operational trend lines
- simple comparative series
- selection-linked charts paired with a table or grid

It should not try to become a universal visualization language on day one.
The first contract only needs enough structure for one strong reusable chart
slice that can be rendered through Taiga-compatible browser composition.

### Review surface

This should become a standard semantic type, but it does not need to be in the
first browser MVP as a fully generalized universal contract.

## Layout Model

The current `layout.type + areas[]` contract should evolve into a stronger
surface model while preserving compatibility.

Supported patterns should include:

- `stack`
- `split`
- `tabs`
- `grid`
- `sidebar-content`
- `dashboard`
- `modal`
- `sheet`
- `focus-detail`
- `desktop-zones`

Each layout should also support:

- roles
- responsive collapse rules
- preferred focus phase
- lazy boundaries

## Load and Readiness Model

The target browser must treat staged rendering as a first-class contract.

The current `loadHint` direction remains correct and should evolve into the
canonical readiness model for browser-facing surfaces.

Important dimensions:

- structure readiness
- data readiness
- focus priority
- off-focus ready state

The shell should be allowed to:

- build structure first
- prioritize focused zones
- defer supporting zones
- expose honest readiness state rather than pretending full hydration already
  exists

## Renderer Registry

The renderer registry is the universalization mechanism.

Each semantic view kind maps to a renderer entry that declares:

- semantic kind
- lazy component loader
- supported variants
- load policy
- feature flags
- device-profile compatibility

The target client should use lazy `import()` for semantic renderer entries.

## Responsive Strategy

Desktop and tablet should prefer:

- dense data views
- split layouts
- tabs
- tables
- review surfaces
- operations dashboards

Mobile and constrained devices should prefer:

- Ionic shell navigation
- collapsed layouts
- card/list projections of dense collections
- fewer simultaneous surfaces

This is not a second semantic UI.
It is a different renderer profile for the same semantic schema.

## Relationship to Current Contracts

### `webui.v1`

Keep:

- `catalog`
- `apps`
- `widgets`
- `registry`
- `webio`
- `ydoc_defaults`
- `contributions`
- `loadHint`

Add on top:

- semantic `view`
- typed `actions`
- explicit `viewState`
- definition versus instance split
- capability-aware workspace composition

### Yjs and streams

Yjs remains the reconnect-stable shared state layer.
Browser streams remain the live high-churn layer.
Neither should be treated as a substitute for the other.

### Addressing

Semantic view bindings should use the canonical typed ref model described in
[UI Addressing](ui-addressing.md).

### Demo-first ABI discipline

The first semantic ABI slice should be designed against one concrete demo
scenario and one demo skill rather than against a hypothetical universal UI.

That demo slice should exercise:

- one table-like semantic view
- one chart-like semantic view
- one shared selection model
- one live stream
- one local view-state branch
- one honest staged-loading flow

## Explicit Prohibitions

To keep the browser stable:

- skills must not ship arbitrary Angular/Taiga implementation code into the
  client
- semantic manifests must not depend directly on Taiga directives
- renderer-specific props must not leak into the canonical semantic layer
- browser renderers must not own business logic
- every new skill must not require a new client-core feature by default

## Success Criteria

The target architecture is successful when:

- a new skill or scenario is integrated through manifest and contributions
- the browser client does not need per-skill bespoke core changes
- the same semantic schema renders in desktop-rich and mobile-compact profiles
- LLM tooling can safely evolve UI at the semantic-schema level rather than by
  editing framework templates
- staged loading and off-focus hydration are native contracts
- one demo skill and scenario can showcase the reusable UI patterns without
  private browser hacks

## Roadmap

Status note:

- `webui.semantic.v0` draft ABI is published
- semantic desktop and modal surfaces already pass through a runtime
  compatibility bridge
- `collection_grid`, `metric_chart`, and `event_log` already materialize into
  browser renderers
- `chat_panel` now materializes into the shared browser chat surface through
  the semantic adapter
- `metric_chart` now has a Taiga-specific semantic renderer path, while the
  earlier temporary widget remains available as a compatibility renderer
- the first Taiga-specific renderer slice is now live for `collection_grid`
- the second Taiga-specific renderer slice is now live for `metric_chart`
- the browser widget host now resolves semantic renderers through a dedicated
  registry service with cached lazy `import()` loading for Taiga-backed
  renderer entries
- the browser client baseline now builds on Angular 19 with Taiga UI v5
- production client builds now use a modern browser baseline compatible with
  Angular 19 optimization

Current pre-stand milestone:

- the demo skill and demo scenario are now ready for first-environment manual
  verification
- both semantic and compatibility paths render a table, chart, event log, and
  chat surface
- table selection now drives the linked chart series through shared `view:`
  state
- semantic `collection_grid` now renders through a Taiga-backed surface instead
  of the legacy compatibility table
- semantic `metric_chart` now renders through a Taiga-backed surface instead of
  the temporary compatibility chart
- demo action surfaces now exercise `open_modal`, `call_host`, and
  `invoke_skill_action` against the live event surface
- desktop and modal runtime paths now apply the same node-aware data scoping
  for semantic and compatibility bindings
- a first capability-aware workspace composer now filters semantic views by
  declared capabilities, lifecycle stage, object kind, and surface class
- `open_workspace` now has a typed browser runtime bridge that can open a
  workspace-oriented modal surface or switch webspaces without falling back to
  untyped host wiring
- runtime page materialization now preserves `surfaceClass` and `objectKind`
  from semantic workspace metadata so shell and modal layout layers can react
  differently to `workspace` versus `operations` surfaces
- semantic workspace metadata is now also projected into `runtime.surface.*`
  page state so typed host and skill actions can observe the current surface
  class, object kind, entity ref, lifecycle stage, and capabilities
- the browser shell now has an explicit route-aware `workspace` surface path
  that loads `ui.application.workspace.pageSchema` before falling back to the
  legacy desktop schema branch
- modal page schemas now respect declared layout areas instead of stacking every
  widget linearly, which makes capability-composed `operations` surfaces
  inspectable on the stand

### 0. Architecture Fixation

- [x] freeze the shell/manifest/semantic/renderer/data/action layer split
- [x] publish semantic UI as the primary future browser contract
- [x] explicitly preserve compatibility with current `webui.v1`

### 1. Browser Manifest Preservation

- [x] keep `webui.v1` as the runtime shell manifest layer
- [x] document current `catalog`, `registry`, `webio`, `ydoc_defaults`, and
  `contributions` as preserved inputs
- [x] stop treating `webui.v1` as the long-term complete UI language

### 2. Semantic UI ABI

- [x] define the first semantic `view` block shape
- [x] define typed action shapes for the first browser actions
- [x] define explicit `viewState` ownership rules
- [x] define the compatibility rule for pages and modals that still use current
  widget schemas

### 3. Web UI Priority Slice

- [x] implement the first four semantic view kinds:
  `collection_grid`, `metric_chart`, `event_log`, `chat_panel`
- [ ] implement the first typed action kinds:
  `emit`, `open_modal`, `set_view_state`, `call_host`,
  `invoke_skill_action`
- [x] support the first layout patterns:
  `stack`, `split`, `tabs`
- [x] support the first state mechanisms:
  Yjs binding, stream receiver, local view state

### 3a. Demo Control Task

- [x] define one demo skill for Taiga-oriented semantic UI validation
- [x] define one demo scenario that composes table, chart, and event stream
- [x] make table and chart share one selection and filter model
- [x] make the chart consume the same addressing vocabulary as the table
- [x] keep the demo domain operational and neutral rather than product-specific

Recommended identifiers:

- skill: `demo_metrics_skill`
- scenario: `taiga_ui_demo_scenario`

### 4. Renderer Registry

- [x] add a semantic renderer registry with lazy `import()` support
- [x] bridge semantic view kinds to current browser widget infrastructure
- [x] add the first Taiga-backed renderer entries without forcing a same-day
  rewrite of the whole browser client
- [x] keep Ionic focused on shell/navigation/mobile interaction

### 5. Workspace Composition

- [x] define capability-aware workspace composition rules
- [x] materialize semantic `surfaceClass` into runtime page metadata
- [x] exercise both `workspace` and `operations` surface classes in the demo
  package
- [x] project semantic workspace context into runtime page state for typed
  action flows
- [x] add the first explicit top-level `workspace` shell surface
- [ ] add desktop/workspace/operations as a complete top-level shell trio
- [ ] keep capability composition separate from business-domain ownership

### 6. Load and Responsiveness

- [x] align semantic UI loading with `loadHint` and readiness phases
- [ ] support focused and off-focus hydration boundaries
- [ ] define desktop-rich versus mobile-compact renderer-profile rules

### 7. Demo Slice

- [x] create one demo skill for semantic UI coverage
- [x] create one demo scenario that exercises workspace composition
- [ ] cover simple grid, sortable/filterable grid, one chart-oriented surface,
  event stream, and chat panel
- [x] include examples of shared state, node-scoped state, stream-driven state,
  and local view state
- [x] include one shared table-plus-chart drill-down flow suitable for Taiga
  renderer validation
- [x] support an explicit shared browser-ownership contract for skill Web UI
  declarations when a skill should not be node-scoped

### 7a. Stand Verification

- [x] demo skill and scenario exist in the repository
- [x] desktop and modal paths both have a renderable chart surface
- [x] the chart changes with table selection through shared local `view:` state
- [x] the event surface is present through `stream:demo_metrics.events`
- [x] compatibility rendering remains available if semantic rendering is
  bypassed
- [x] semantic `collection_grid` is rendered through a Taiga-backed surface
- [x] semantic `metric_chart` is rendered through a Taiga-backed surface
- [x] semantic `chat_panel` is rendered through the browser chat surface
- [x] production browser build passes with the upgraded Angular/Taiga baseline
- [x] demo action paths exercise `open_modal`, `call_host`, and
  `invoke_skill_action`
- [x] demo host actions have an explicit gateway ack path and no longer rely on
  command timeout behavior
- [x] event-log semantic views now render receiver payload collections such as
  `{ items: [...] }`
- [x] `open_workspace` is wired end-to-end through a typed runtime bridge
- [x] desktop and modal demo surfaces both resolve the same data branches under
  an explicit ownership contract instead of relying on accidental scoping
- [x] the demo now exposes one `operations`-class surface in addition to the
  primary `workspace`-class surface
- [ ] manual verification on the target stand

Recommended demo data shape:

- one collection of metric rows with `id`, `title`, `status`, `value`,
  `updated_at`, and grouping tags
- one time-series collection keyed by metric id
- one event receiver for append-oriented runtime updates
- one shared selection branch that links grid rows and chart series

### 8. Cleanup and Migration

- [ ] migrate existing concrete widget types gradually to semantic view kinds
- [ ] remove browser-core special cases once semantic equivalents are proven
- [ ] keep legacy compatibility paths only where the runtime still depends on
  them
