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

The current `webui.v1` compatibility slice exposes a smaller domain interface
for this layer:

- `webui.interface.views` declares skill-owned UI tasks such as
  `notebook.notes.list` or `notebook.note.edit`.
- A modal can declare `implements` and `schema.interface.routes` to map those
  views to concrete modal routes.
- `navigate` opens a domain view on a supported surface; today the implemented
  surface is `modal`.
- `navigateModal` changes the route of the current modal by validating route
  params and applying the declared private state patch.

This keeps the source of truth declarative: the skill owns the public view
contract, the modal owns its route-to-state mapping, and browser actions only
carry addresses plus params.

Addressed modals also expose a small domain state contract. The modal
interface declares domain states such as `notes.list` or `note.edit`, maps them
to concrete routes, and declares ownership for skill domain state, browser
route state, browser-local view state, and durable persistence acknowledgments.
Modals can opt in to URL/history binding with `schema.interface.history`, which
lets the browser expose the current modal route/view/params as a copyable
deeplink contract.

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
- `open_url`
- `set_view_state`
- `patch_y`
- `invoke_skill_action`
- `open_workspace`
- `apply_review_change`

## Interaction Behavior Model

Some browser behavior is neither domain state nor renderer styling. It should
be modeled as UI behavior data so skills, scenarios, Builder, and NLU surfaces
can reason about it without changing the client core.

The first system-level behavior contracts belong in `webui.json`:

- `interaction.initialFocus`: the first semantic element to focus when a
  page, modal, form, or widget becomes interactive. This should reference a
  semantic target such as `widget:weather-city-input`, not a CSS selector.
- `interaction.submit.defaultAction`: the action that runs when Enter submits
  the current form context. Enter must not implicitly activate the first
  visible button.
- `resources`: stable system, skill, or scenario browser resources such as
  icons, assistant avatars, SVGs, preview images, templates, and i18n data.
  Authored manifests reference them as `resource:<id>`, while AdaOS resolves and
  delivers them through the core/browser channel and, for remote browsers,
  through a Root-side cache or relay.
- `action.feedback`: pending, success, error, and timeout behavior for an
  action that expects an asynchronous Yjs or stream state change. The current
  `params._observe` shape is a legacy compatibility pattern and should
  converge into `feedback.observe`.
- `loading`: element-level loading and degraded-state behavior. This extends
  the coarse `loadHint` readiness model without turning every widget into a
  custom renderer.

Example:

```json
{
  "resources": {
    "weather.current": {
      "kind": "svg",
      "path": "assets/icons/current.svg",
      "delivery": "core",
      "cacheKey": "sha256:..."
    },
    "assistant.default.avatar": {
      "kind": "image",
      "scope": "system",
      "path": "assets/avatars/assistant-default.webp",
      "delivery": "core",
      "cacheKey": "sha256:..."
    },
    "weather.i18n.ru": {
      "kind": "data",
      "role": "i18n",
      "locale": "ru",
      "path": "assets/i18n/ru.json",
      "delivery": "core",
      "cacheKey": "sha256:..."
    },
    "weather.preview": {
      "kind": "image",
      "delivery": "external",
      "url": "https://cdn.example/weather/preview.webp",
      "mime": "image/webp"
    }
  },
  "apps": [
    {
      "id": "weather_app",
      "title": "Weather",
      "icon": "resource:weather.current",
      "launchModal": "weather_modal"
    }
  ],
  "registry": {
    "modals": {
      "weather_modal": {
        "schema": {
          "id": "weather_modal",
          "interaction": {
            "initialFocus": "widget:weather-city-input",
            "submit": {
              "defaultAction": "weather.search",
              "enterKey": "submit",
              "scope": "focused_form"
            }
          },
          "layout": {
            "type": "single",
            "pattern": "stack",
            "areas": [{ "id": "main" }]
          },
          "widgets": [
            {
              "id": "weather-city-input",
              "type": "input.text",
              "area": "main",
              "loading": {
                "loadingText": "Loading weather...",
                "skeleton": "card",
                "timeoutMs": 9000
              },
              "actions": [
                {
                  "id": "weather.search",
                  "on": "change",
                  "type": "callHost",
                  "target": "skill.event.publish",
                  "params": {
                    "event_type": "weather.location.requested",
                    "payload": {
                      "city": "$event.value",
                      "request_id": "$client.requestId"
                    }
                  },
                  "feedback": {
                    "pending": {
                      "disable": true,
                      "label": "Searching...",
                      "icon": "sync-outline"
                    },
                    "observe": {
                      "kind": "y",
                      "path": "data/weather/current",
                      "scope": "node",
                      "timeoutMs": 9000,
                      "match": {
                        "request_id": "$client.requestId",
                        "pending": false
                      },
                      "advanceFields": ["request_id", "updated_at", "pending"]
                    },
                    "timeout": {
                      "state": "degraded",
                      "message": "Weather update timed out"
                    }
                  }
                }
              ]
            }
          ]
        }
      }
    }
  }
}
```

Useful follow-on behavior contracts that should be considered before the
client grows more per-widget special cases:

- idempotency keys for actions that can be double-clicked, retried, or
  resumed after reconnect
- explicit cancel actions for long-running pending controls
- stale state and dirty-form state, separate from loading
- destructive-action confirmation policy with side-effect class and preview
  evidence
- optimistic update policy, including rollback on failed observation
- accessibility hints such as live-region priority for async status changes

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

### Forms

Forms are a first-class semantic surface, not a collection of renderer-specific
Ionic or Taiga controls.

The target browser form model should support Google-Forms-like composition:
a skill, scenario, or Builder workflow declares a stable form contract and the
browser chooses the best renderer profile for the current surface. Taiga UI is
the preferred rich desktop renderer; Ionic remains valuable for mobile-friendly
shell behavior and compact controls. Neither toolkit is the ABI.

Current compatibility status:

- `ui.form` exists as a `webui.v1` widget type.
- The current browser implementation handles only simple field inputs:
  text, textarea, number, date, toggle, and select.
- `webui.v1` validates the widget type but does not yet validate the shape of
  `ui.form.inputs.fields`.
- `webui.v1.types.d.ts` currently exposes widgets with `type: string` and
  generic `inputs`, so authoring tools cannot rely on typed form fields yet.

Target contract:

- `form` is the semantic view kind for survey, editor, settings, review, and
  quiz-like forms.
- `form_matrix` is reserved for grid-shaped questions and field matrices where
  rows and columns are part of the answer contract.
- `ui.form` remains the compatibility renderer container while the typed field
  ABI is added under `webui.v1`.
- Form definitions use stable field ids and answer keys. Render labels,
  descriptions, help text, localization, and layout hints are presentation
  metadata, not storage keys.
- Field definitions describe intent: answer type, cardinality, allowed options,
  validation, default value, conditional visibility, and branching behavior.
- Renderer-specific details are optional hints. The canonical manifest should
  say `singleChoice`, not `ion-radio` or `tui-radio`.

Required field families:

- text: short text, long text, email, URL, phone, password/PIN where needed
- numeric: number, integer, range, slider/scale
- choice: single choice, multiple choice, dropdown, searchable combobox,
  chips/tags, boolean/toggle
- date and time: date, time, date-time, date range, time range
- file and media: file upload, image attachment, capture reference, artifact
  reference
- structured: object group, repeated group, address/contact-like composite
  groups
- matrix: single-choice grid, checkbox grid, rating grid
- survey and quiz: linear scale, rating, correct answer, points, feedback
- static content: section title, description, markdown, image, video/embed,
  separator, page break

### Markdown surfaces

`static.markdown` is the read-only Markdown presentation contract. The browser
parses its `inputs.content` (or a bound string field), sanitizes the generated
HTML with the Angular HTML security context, and renders it without granting
scripts, arbitrary components, or executable extensions. It is suitable for
scenario README/help, protocol notes, and workflow guidance. Manifest
validation and the client widget registry must admit the same widget type; an
unknown renderer is a validation defect, not an acceptable runtime fallback.

Markdown editing is a separate semantic surface and must not be hidden behind
`static.markdown` or the plain `item.textEditor`. A future
`item.markdownEditor` should persist canonical Markdown, offer visual and
source modes, and pass lossless round-trip fixtures for links, lists, tables,
code blocks, and domain-specific inline notation. The current preferred
product direction is a Markdown-first ProseMirror editor such as Milkdown
Crepe for visual editing; CodeMirror 6 plus a synchronized preview remains the
source-first option. No editor dependency is part of `webui.v1` until those
round-trip, accessibility, mobile, clipboard, and collaborative-editing gates
are proven.

### Persistent command regions

A layout area with `role: footer` is the semantic bottom command region. When
it contains only command/status widgets (`input.commandBar`, its `ui.actions`
alias, or `feedback.statusBar`), Desktop docks it to the viewport bottom and
reserves content space above it. Scenario authors should use this region for
workflow-wide commands that must remain reachable while the main and auxiliary
panes scroll. A `toolbar` role remains an in-flow local toolbar and must not be
used merely to obtain fixed positioning.

Validation must be declarative:

- required and optional state
- min/max for numeric values, lengths, dates, times, file counts, and file sizes
- regex and format validators
- enum and option-set validators
- cross-field validators for dependent answers
- localized error messages and remediation hints

Form lifecycle:

- `draft` state is browser-local or selectively synchronized view state while a
  user is editing.
- `domain` state belongs to the skill or scenario after validation and submit.
- `response` payloads are immutable submission records unless a form explicitly
  declares edit semantics.
- File answers store artifact refs, not local filesystem paths or inline large
  payloads.
- Aggregates and live response summaries use projections or streams, not the
  form draft branch.

Actions:

- `validate` checks the draft without committing domain state.
- `submit` validates and dispatches the declared typed action.
- `save_draft` persists a draft through an explicit ownership contract.
- `reset` restores defaults for the current form or section.
- `next_section` and `previous_section` are navigation actions for paged forms.
- Branching is declared as data on fields/options and should resolve to a
  section id or terminal state, not to renderer callbacks.

Accessibility and interaction rules:

- Every answerable field needs a stable label, even when the visual renderer
  chooses a compact presentation.
- Help text, validation errors, required markers, and async submit feedback
  must be accessible through the same semantic contract.
- `interaction.initialFocus` and `interaction.submit.defaultAction` remain the
  page/modal-level behavior contracts for form focus and Enter handling.
- Dirty state, pending submit state, optimistic submit policy, and cancel
  behavior should be explicit, not inferred from button order.

Analytics:

- Forms may expose response summaries through separate semantic views such as
  `metric_chart`, future `bar_chart`, `pie_chart`, or `response_summary`.
- Chart renderer APIs from Taiga should inform implementation, but chart
  selection remains a semantic view concern outside the form draft contract.

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

### Login-Time Progressive Hydration

After authentication, the desktop shell may render from the last known good
desktop render snapshot before the live Yjs room finishes first sync.

This snapshot is a read-only browser render cache, not a second source of
truth. It can provide `ui`, `data`, and `registry` branches for first paint
when the live Yjs branch is still absent, but live Yjs always wins as soon as a
branch materializes. The cache is written only from live materialization states
that are at least `interactive`, and normal Yjs persistence remains opt-in
because replaying an old Yjs document can mutate or overwrite freshly seeded
server state.

Expected behavior:

- login can show the previous usable desktop immediately
- Yjs sync and materialization continue in the background
- widgets and schemas refresh as live branches arrive
- degraded/sync status remains visible instead of being hidden by the cache
- user writes should continue through authoritative runtime commands, not by
  mutating cached JSON

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
- browser renderers now consume a shared layout render plan, so `role`
  remains semantic slot metadata and placement is selected by `layout.type`,
  optional `layout.pattern`, or `semantic.layout.pattern`
- `collection_grid`, `metric_chart`, and `event_log` already materialize into
  browser renderers
- `chat_panel` now materializes into the shared browser chat surface through
  the semantic adapter
- `metric_chart` now has a Taiga-specific semantic renderer path, while the
  earlier temporary widget remains available as a compatibility renderer
- `form` and `form_matrix` are target semantic contracts; the implemented
  compatibility path is still `ui.form` with a small untyped field subset
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

### 3b. Forms Architecture Slice

- [x] document forms as a semantic browser surface separate from Ionic/Taiga
  implementation details
- [ ] add typed `formField` definitions to `webui.v1.schema.json`
- [ ] add TypeScript helpers for form widgets, form fields, options,
  validation, branching, and submit payloads in `webui.v1.types.d.ts`
- [ ] keep `ui.form` backward compatible while validating
  `ui.form.inputs.fields` when present
- [ ] add contract fixtures for a survey form, an editor/settings form, a
  multi-section form, and a quiz-like form
- [ ] extend `skill validate` and Builder validation diagnostics so unsupported
  field types, broken branch targets, invalid option values, and missing answer
  keys fail before runtime

### 3c. Forms Renderer Slice

- [ ] render the existing field subset through the typed field adapter:
  short text, long text, number, date, toggle, and select
- [ ] add choice controls: radio/single choice, checkbox/multiple choice,
  dropdown, searchable combobox, and chips/tags
- [ ] add scale controls: linear scale, rating, slider/range, and numeric range
- [ ] add date/time controls: time, date-time, date range, and time range
- [ ] add file answers using artifact refs and the existing browser upload
  path, not inline payloads
- [ ] add matrix questions: single-choice grid, checkbox grid, and rating grid
- [ ] support sections, page breaks, static content, markdown descriptions,
  and media/help blocks
- [ ] define desktop-rich and mobile-compact renderer mappings for the same
  field contract

### 3d. Forms State and Submission Slice

- [ ] define draft answer ownership separately from submitted domain state
- [ ] support declarative validation with localized error messages
- [ ] support dirty state, reset, save draft, validate, submit, and pending
  submit feedback
- [ ] support conditional visibility and section branching by field/option
  values
- [ ] support immutable response records and explicit edit semantics for forms
  that allow response updates
- [ ] expose response summaries through projections/streams and separate chart
  semantic views rather than through the draft form branch

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
- [x] add desktop/workspace/operations as a complete top-level shell trio
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
- [x] the `operations` surface is now available as a dedicated top-level shell
  route instead of only as a modal/demo variant
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

### 8a. Contract Hardening

- [x] `[must]` keep the relevant baseline green while hardening contracts:
  Webspace phase2, WebUI ABI/schema, skill validation, and Notebook skill tests
- [x] `[must]` reject broken WebUI view/modal/action cross-links during
  `skill push` before version bump or publication
- [x] `[must]` validate materialized `ui.application.interfaces` and
  `ui.application.modals` together so remote/member declarations cannot fail
  silently in the browser
- [x] `[must]` preserve validation evidence in `ui.application.diagnostics`
  and skill UI diagnostics logs
- [x] `[must]` add Notebook as the reference contract test for addressed modal
  navigation, editor persistence, and Yjs soft reload recovery
- [x] `[must]` formalize modal domain states as declarative route/view/entity
  contracts rather than incidental page-state keys
- [x] `[must]` define explicit ownership for modal domain state, route state,
  browser-local view state, and persistence acknowledgments
- [x] `[must]` publish a stable diagnostic code catalog with severity, owner,
  and remediation for UI contract failures
- [x] `[must]` add Python SDK/Builder-safe helpers and contract fixtures for
  skill interfaces, modal routes, domain states, ownership, and navigation
  actions
- [x] `[should]` add a compact UI-contract diagnostics endpoint and payload for
  operators and Builder reviews
- [ ] `[should]` add a real browser/CDP e2e smoke for Notebook addressed-modal
  recovery after the current contract tests
- [x] `[should]` generate TypeScript declaration helpers from the WebUI ABI for
  skill and scenario authoring
- [x] `[could]` connect modal addresses to URL/deeplink history after the
  runtime contract is stable
- [x] `[could]` add a browser diagnostics panel consuming the contract
  diagnostics endpoint
- [ ] `[deferred]` remove legacy `openModal` compatibility once migrated
  third-party packages and unfinished drafts have enough coverage; active
  published repository skills/scenarios now use `navigate` or `navigateModal`,
  while the runtime keeps one diagnostic compatibility path for older callers

The repository migration can be repeated for selected manifests with
`python -m adaos.apps.open_modal_migrate --write <webui-or-scenario-json> ...`.
It declares missing public modal views/routes before rewriting actions and is
idempotent. Do not rewrite immutable Builder `ui_revisions`; they are audit
evidence rather than active runtime manifests.
