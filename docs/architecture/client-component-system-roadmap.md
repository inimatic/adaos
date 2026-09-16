# Client Component System Roadmap

Status: active prerequisite and growth roadmap for the universal AdaOS Client.

Last reviewed: 2026-09-16.

Architecture owner: [Web UI Architecture](web-ui-architecture.md).
Builder dependency: [Builder Intent-to-Prototype Architecture](builder-intent-to-prototype.md).

This roadmap removes application-domain knowledge from the universal browser
runtime, makes the semantic/component ABI truthful, and establishes a
repeatable way to grow the Client component set. It owns Client work only.
Builder intent interpretation, capability selection, compilation, and
evaluation remain in their Builder architecture and roadmap.

The Builder-side dependency and current qualification scope are tracked once in
[BIP-08](builder-intent-to-prototype-roadmap.md#bip-08) and
[BIP-16](builder-intent-to-prototype-roadmap.md#bip-16). This page owns the
Client tasks; a passing Builder cohort does not automatically close C0-C3.
Dated Builder measurements are consolidated in the
[engineering journal](builder-engineering-journal.md), not copied into another
acceptance checklist.

## Decision

A bounded Client integrity pass is required before recording a clean generic
Builder baseline. Building all missing cross-domain components first is not.

The prerequisite pass must ensure that the baseline measures a universal
renderer rather than hidden Applications, Builder, Infrastate, Voice, or Media
Center behavior. The first eight application archetypes used to discover
component gaps become a visible development suite. They are not held-out after
the component design has been derived from them. New components are selected
after the clean baseline from the observed cross-domain gap taxonomy.

## Priority Rules

- `must`: required for a truthful generic Client or clean Builder baseline;
- `should`: required for maintainable component growth and release quality;
- `could`: useful after measured demand exists;
- `deferred`: deliberately excluded from the current correction.

## Current Audit

Layout baseline, 2026-09-16:

- [x] `[must]` Record the current layout failure boundary in the target
  architecture: inferred roles, three renderer strategies, global compact
  stacking, weak pane sizing, heuristic demotion/inlining, canvas-like tool
  placement, variant drift, and incomplete conformance evidence.
- [x] `[must]` Publish and validate Layout & Interaction ABI v2 as the only
  page-layout contract. Keep `webui.json` canonical; a normalized LayoutGraph is
  derived and immutable, never a second authored document.
- [x] `[must]` Upgrade the coherent Taiga UI package family and add only the
  layout/table modules used by universal Client components.
- [ ] `[must]` Implement universal semantic surface, region, collection-tools,
  adaptive detail/inspector, command-overflow, and disclosure components. No
  component may contain product ids, labels, endpoint rules, or workflow state.
  The generic semantic region/surface and Taiga table slice is complete;
  collection-tools, command overflow, and the complete adaptive detail API
  remain open.
- [ ] `[must]` Add contract and browser conformance for each admitted pattern at
  wide and compact sizes, including long EN/RU content, keyboard/focus,
  overflow, empty/loading/error/permission states, DOM/a11y/screenshot evidence,
  and stable render-plan identity. Render-plan identity and structural/browser
  geometry are covered; the complete state, keyboard, a11y, and locale matrix
  remains open.
- [x] `[must]` Migrate all authoritative local Workspace and DEV manifests,
  delete obsolete TEST/E2E projects, reject legacy layouts, and run Workspace
  browser qualification. DEV applications are qualified later when changed.
- [ ] `[must]` Record successful `project push` and `dev project push` after
  source validation; do not rewrite historical Trial/runtime/snapshot evidence.

Cutover evidence, 2026-09-16:

- Core conformance covers 151 authoritative `webui.json`, `scenario.json`,
  Android scenario, and Android seed documents with zero findings; a second
  migration pass changes zero files/pages.
- All 18 Workspace scenarios materialize in Chrome at 1440x960 and 390x844
  without page errors, clipping, horizontal overflow, or unintended region
  overlap. Compact drawers/sheets are intentional overlays and are evaluated
  separately from in-flow regions.
- Client `0.0.398` passes 1426 tests, a production-compatible local build, the
  21-source generic boundary check, and the 41-widget inventory check. Taiga UI
  packages are aligned at `5.24.0`.
- Workspace Builder scenario/skill/control tests pass as a component-scoped
  suite. A monolithic `.adaos/workspace` pytest collection remains invalid
  because independently packaged skills reuse top-level Python module names;
  test orchestration must preserve package isolation.
- Client `ng lint` remains blocked before source lint by the existing Nx/Angular
  `Workspaces is not a constructor` toolchain failure; build, tests, inventory,
  boundaries, and browser qualification are the current executable gates.

- [x] `[must]` Qualify generic schema-modal resize and fullscreen restoration on desktop
  and mobile. Persist current-user device/scenario/webspace scopes; resolve exact
  scopes before wildcards. Saving application defaults from alpha/beta requires
  explicit DEV source confirmation and source-digest concurrency control.
  `modal-settings-03` verifies device save/reopen, current-user shared preference,
  explicit DEV default edit, preference precedence over that default, and loading
  shared settings in a fresh compact browser context. Native non-schema dialogs
  and physical-device sync/orientation qualification are not included.
- [ ] `[should]` Qualify opt-in current/disabled collection rows with keyboard
  behavior and truthful semantic/ABI discovery. No Builder-specific renderer.

- [x] `[must]` Add opt-in, tag-addressed chat read invalidation for new messages
  and lifecycle transitions, excluding initial/older history and token deltas.
  Keep the implementation generic; ABI and 21 chat unit tests cover the contract.
- [ ] `[must]` Qualify focus restoration after asynchronous detail commands on
  wide/compact surfaces. Pending command guards must not blur the opener before
  an overlay captures it. The local component fix passes unit tests; browser
  rerun remains necessary.

Small-prototype qualification, 2026-09-12:

- [x] `[must]` Add a generic responsive query toolbar with page-scoped state,
  typed options, native-calendar commits, active filters and isolated reset.
- [x] `[must]` Fix standalone date input commits without changing manual-save mode.
- [x] `[must]` Expose per-field wrapping/truncation/alignment and read typed chart
  points from resource-query collections; do not coerce missing values to zero.
- [x] `[must]` Distinguish persisted board lane moves from unsupported rank changes.
- [ ] `[must]` Qualify the above through freshly generated small prototypes in the
  local browser, including mobile, actual mutations, sections and settings.
- [x] `[must]` Extend the boundary audit beyond its previous 15 checked sources;
  inspect residual product-specific layout selectors before a clean-baseline claim.
  Client `554e763` checks 21 sources, including desktop and modal styles, and
  moves the existing Newface chart-layout exception into product-owned
  compatibility styles. Inventory (41 widgets) and 14 focused renderer/modal
  tests pass. This bounded audit does not establish full baseline qualification.

Builder owns the semantic adapters and generation experiments in its
[executable-contract qualification](builder-intent-to-prototype-roadmap.md#bip-16).

The current Client is a useful compatibility runtime, but not yet a clean
generic baseline:

- generic-looking widgets contain product names, endpoints, modal IDs, and
  action branches for Applications, Builder, Infrastate, Voice, and Media
  Center;
- the semantic adapter accepts more action, binding, and layout kinds than it
  can faithfully lower, and some unsupported values are dropped or
  approximated;
- component declarations are repeated across handwritten TypeScript unions,
  registries, Core catalogs, Builder context, documentation, and tests;
- several widgets are eagerly loaded despite being optional or heavy;
- renderer error classification sometimes infers diagnostic codes from human
  message text;
- i18n, long-content, accessibility, and representative-state behavior are
  not uniformly declared or tested.

These findings do not invalidate existing renderer evidence. They limit its
claim to the explicit compatibility profile in which it was produced.

## C0. Inventory And Compatibility Freeze

Progress note (2026-09-10): Client commit `4e0639f` adds a deterministic,
TypeScript-AST-derived capability inventory at
`architecture/evidence/client-capability-inventory.v1.json` in the Client
repository. Its first snapshot records 40 widgets, 16 page action types, nine
data-source kinds, five static modals, semantic advertised/lowered sets, three
product action extensions, source digests, loading class, and ownership. The
inventory check rejects Widget ABI/runtime registry disagreement and declared
page actions without a dispatcher branch. It is a bounded C0 artifact, not yet
the exhaustive inventory required by the exit gate: aliases, all endpoint
assumptions, modal/data compatibility branches, runtime reachability, retained
browser traces, and bundle attribution remain open.

- [ ] `[must]` Inventory every registered widget, modal, page-data provider,
  page action, semantic mapping, alias, load class, and owning product.
- [ ] `[must]` Classify each entry as shell, generic component, generic
  adapter, or product-owned extension. Record all domain identifiers and
  endpoint assumptions reachable from shell/generic code.
- [ ] `[must]` Retain representative compatibility fixtures and wide/compact
  browser traces for currently published applications before extraction.
- [ ] `[must]` Record the exact Client commit, build, WebUI ABI, semantic ABI,
  registry digest, locale set, and browser profiles used by the legacy run.
- [ ] `[should]` Add bundle ownership and size reporting by shell, common,
  on-demand, and heavy class.

Exit gate: every current behavior has an owner and a retained compatibility
fixture; no branch is moved based only on a filename or component title.

## C1. Baseline Integrity Gate

Progress note (2026-09-10): Client commits `55fcc6b` and `4e0639f` establish
typed semantic adaptation diagnostics and the first product-extension seam.
Builder workbench address adaptation, Infrastate/Infra Access host routing and
recovery, and NLU Teacher actions no longer live in the generic
`PageActionService`; they are independently registered through the
`PAGE_ACTION_EXTENSIONS` multi-provider. The static boundary gate now includes
that dispatcher. Focused evidence is 73/73 action tests, the inventory and
boundary checks, TypeScript compilation, and a successful development build.
On this machine, test execution took 0.79 seconds after a 52.0-second test
bundle build, while the application bundle took 90.5 seconds. Build-graph
latency is therefore tracked separately from runtime behavior.

Client commit `559413e` adds the equivalent `PAGE_MODAL_EXTENSIONS` port and
moves the desktop Apps/Widgets catalog schemas, IDs, materialization recovery,
and diagnostics into an explicit compatibility extension. With product modal
extensions disabled, the generic runtime now reports `modal.not_found` instead
of silently exposing a catalog. The generated inventory records modal
extensions and their matched IDs. Evidence is 31/31 focused modal tests,
TypeScript compilation, and the inventory/boundary gates; the test bundle took
27.6 seconds while actual tests took 0.21 seconds.

Client commit `e4820a4` adds the generic `PAGE_DATA_EXTENSIONS` port. Operational
snapshot roots, their skill/API recovery routes, freshness rules, local-path
alias, and plain-JSON observation policy now live in the registered
`OperationalPageDataExtension`; the generic `PageDataService` only supplies
typed path, read, addressing, reliability, and `DataSourceConfig` primitives.
The no-extension profile reads YDoc without inferring a product recovery route,
and compatibility fallback calls now pass through the normal read-intent policy
and cache. Five tests for the old private Infrastate freshness helper were
removed because that helper was unreachable from the production read path;
retained tests exercise observable read and no-fallback behavior instead. The
generated inventory and static boundary gate now include the data port and
generic service.

Client commit `4a787a0` extends that port with product-owned YDoc transform
resolution and dependency declaration. Desktop icon, widget, installed/pinned
catalog, node-label, shared-order, and quarantine materialization moved out of
`PageDataService` into `DesktopCatalogDataExtension`; the universal service lost
711 lines while retaining generic path/observation machinery. With extensions
disabled, `data/catalog/apps` is returned unchanged and `desktop.icons` is not
interpreted. The inventory now records both data extensions, and the boundary
gate rejects reintroduction of the desktop transform and installed-list
contracts into the generic service. Evidence on this machine is TypeScript
application compilation, 116/116 focused `PageDataService` tests, inventory and
boundary checks. The Angular test bundle took 26.8 seconds; browser execution
took 0.22 seconds.

Client commit `3566e96` makes widget ownership executable instead of merely
descriptive. `PageWidgetRegistryService` now contains only generic and shell
registrations; desktop catalog/grid, Voice, Media, and Vision components are
supplied by the explicit `client.product-widgets.compatibility.v1` extension.
Constructing the registry without extensions cannot resolve those component
types, while the normal Client composition root opts into the compatibility
pack. The generated inventory reads both registries, rejects duplicate or
missing ABI registrations, and the generic boundary gate covers the registry
and its extension contract. Evidence is TypeScript compilation, 15/15 focused
registry/host tests, and current inventory/boundary checks. The Angular test
bundle took 35.2 seconds; browser execution took 0.35 seconds.

Client commit `e566476` exposes the same boundary through the real composition
root. A browser opened with `client_profile=generic` registers none of the
product action, modal, data, or widget extensions; the default remains
`compatibility` so existing user behavior is unchanged. The selector is exact
and fail-safe: unknown values do not silently enter the evaluation profile.
TypeScript compilation, profile tests, registry tests, inventory, and boundary
checks pass. Compact/wide browser traces for both profiles are still required
by C0/C3.

Client commit `0e20627` removes the hidden Builder control injection from the
generic command bar. Recognition by widget ID/title, the synthetic Issues
button, and its Dev Tickets action now belong to the registered
`builder.command-bar.compatibility.v1` extension and are absent from the
generic profile. Declarative buttons/actions remain the normal universal path.
The generated inventory records the behavior extension, and the boundary gate
now covers the command bar implementation. Evidence is TypeScript compilation,
18/18 focused command-bar tests, inventory, and boundary checks. Test bundle
generation took 43.6 seconds; browser execution took 0.04 seconds.

Client commit `9e9df96` stops advertising the current chat, file-upload, and
document-viewer implementations in the generic registry. Their implementations
remain unchanged and available in the default compatibility profile, with
explicit `conversation.compatibility`, `resource-upload.compatibility`, and
`builder.artifact.compatibility` ownership. This preserves published Research,
Drive, Vision, and Builder behavior while ensuring the clean profile reports a
capability gap until C2 defines transport-neutral conversation and resource
ports. Evidence is TypeScript compilation, 11/11 registry tests, inventory, and
boundary checks; test bundle generation took 31.5 seconds and browser execution
took 0.14 seconds.

Client commit `50eb154` makes the draft semantic adapter inventory total for
its advertised surface. Generic `navigate` and `projection` now lower to
existing typed runtime contracts; `view` is admitted only as a selection
binding, not a data source. Unused, unimplemented `emit`, `patch_y`, and
Builder-specific `apply_review_change` commands are no longer advertised by
the generic Client union. Old documents containing them still fail closed with
a typed semantic diagnostic. Core schema tests now enforce the binding roles,
and the generated inventory reports no unsupported view, action, or binding
kinds. Evidence is 5/5 focused adapter tests, TypeScript compilation, 79 Core
ABI tests, and the Client inventory/boundary gates; focused test bundle
generation took 27.6 seconds while browser execution took 0.10 seconds.

The gate remains open. Product-specific compatibility still exists in desktop
shell orchestration and product-owned compatibility components. The collection
grid, voice, media, vision, and desktop widget implementations are explicitly
product-owned and can be disabled by browser profile, but still require retained
compatibility fixtures and compact/wide clean-profile traces before the clean
baseline.

A clean-profile RU equipment-inspection E2E is evidence of an unresolved
semantic-granularity decision, not yet a measured Client gap. The generated
candidate modeled inspections as records and collapsed checklist entries into
one long-text field. Existing generic list/form components can plausibly model
checks as separate records, so that composition must be evaluated first. A
bounded nested repeated-field group remains a C2/C4 candidate only when the
accepted semantic shape requires nesting and either generic composition is
proven insufficient or an unrelated archetype supplies the second occurrence
required by C4. The inventory should record the unresolved need without
advertising a component contract that has not been admitted.

The retained volunteer-roster run on 2026-09-11 likewise does not justify a
product-shaped Client component. Existing `ui.table`, `item.details`, and
`ui.form` contracts rendered every field emitted by the semantic compiler.
The missing coverage/conflict behavior came from a single-resource semantic
shape that could not represent independent volunteers, shifts, and
assignments. Multi-resource and structural state-proof work belongs in the
Brief/semantic compiler first; C4 should add a schedule/availability primitive
only after an unrelated archetype demonstrates a remaining renderer gap.

The first cross-layer command-policy slice is now explicit. Semantic
Prototype commands may compile a localized confirmation descriptor into a
runtime action, and the Client blocks every such action before extension
adaptation or side effects through a dedicated confirmation service. The
service uses an accessible Ionic alert, escapes declarative message content,
and fails closed when the confirmation interface is unavailable. The new
runtime source participates in the content-addressed Client capability
inventory. This closes the immediate direct-transition gap without completing
C2: the v1 compiler still supplies a generic safety default for transition
and delete commands, while the target contract must derive confirmation from
explicit authority, reversibility, and risk policy.

Focused evidence separates Client correctness from build latency: 74
PageAction tests executed in 0.68 seconds, while Angular test bundle generation
took 29.6 seconds and total wall time was 42.0 seconds. TypeScript checking took
14-19 seconds. A shorter test timeout would hide no runtime defect; test-target
cache and compilation-graph improvements must be measured separately.

Client commit `20f0aca` closes the first typed relationship-selector defect
found by semantic-v2. The generic selector preserves scalar option values and
emits boolean `false` as a boolean instead of converting every option to text
or losing false through fallback coercion. Thirteen focused selector tests
pass; bundle preparation took about 27.6 seconds while browser execution took
about 0.16 seconds. The repository lint command currently fails before source
analysis with the existing Nx/Angular `Workspaces is not a constructor`
toolchain incompatibility, which remains a build-tool issue rather than a
selector correctness result.

- [ ] `[must]` Extract Builder, Applications/Marketplace, Infrastate, Voice,
  and Media Center behavior from generic widgets and runtime services into
  explicitly registered product adapters or extensions.
- [ ] `[must]` Make generic actions operate on typed addresses, resources, and
  capabilities. They must not select endpoints, tools, modals, or copy from a
  product name, widget ID, title, or payload shape.
- [ ] `[must]` Make semantic lowering total for the advertised contract. An
  unsupported action, binding, layout, or state mapping produces a typed
  capability-gap/diagnostic result; it is never silently omitted or replaced
  by an unrelated widget.
- [ ] `[must]` Replace message-text error inference with structured diagnostic
  codes, owner, retryability, and remediation metadata from the failing layer.
- [ ] `[must]` Preserve the current behavior through explicit compatibility
  adapters, with no reverse dependency from generic code into product code.
- [ ] `[must]` Add a static boundary check that rejects known product IDs,
  endpoints, localized product vocabulary, and product action names in the
  shell/generic allowlist.
- [ ] `[should]` Normalize generic attachment, document, media, and chat
  integration around typed resource/action ports; product adapters may map
  those ports to existing endpoints.
- [ ] `[should]` Add a cached focused test target for runtime contract tests
  and report bundle-generation separately from browser execution. Do not tune
  correctness timeouts to compensate for repeated Angular bundling.

Exit gate: the generic Client profile can render and fail honestly with all
product extensions disabled. This gate is required before the clean Builder
baseline.

## C2. Single Component Contract

- [ ] `[must]` Publish a versioned component contract beside each supported
  implementation. It declares semantic roles, data shapes, properties,
  defaults, events, actions, side effects, state ownership, i18n,
  accessibility, responsive behavior, loading class, compatibility, fixtures,
  and compiler mappings.
- [ ] `[must]` Make action risk, reversibility, and confirmation policy part
  of that contract. Remove the semantic-v1 transition/delete safety default
  only after every admitted producer declares and validates the replacement.
- [ ] `[must]` Generate Client registration, TypeScript types, Core validation
  indexes, Builder retrieval units, documentation indexes, and conformance
  manifests from that contract.
- [ ] `[must]` Reject a component whose runtime registration and generated
  manifest disagree. Do not diagnose this condition from display text.
- [ ] `[must]` Emit an ABI impact report naming affected compiler mappings,
  applications/scenarios, migrations, fixtures, and Client tests.
- [ ] `[must]` Generate the LayoutGraph normalizer and universal component
  registrations from the same contract used by Core validation and Builder
  retrieval. Handwritten aliases cannot introduce additional layout behavior.
- [ ] `[should]` Replace public `any`/unbounded dictionary inputs with generated
  discriminated types at the renderer boundary.
- [ ] `[should]` Require fixtures for empty, loading, error, permission-denied,
  long-content, compact, wide, English, and Russian states where applicable.

Exit gate: adding or changing a component has one declaration and a complete,
machine-checked impact set.

## C3. Renderer Conformance And Delivery

- [ ] `[should]` Qualify conversational choice controls as a reusable composition
  of chat, menus and message actions. Scope selection, action/response identity,
  draft isolation, keyboard focus and narrow layout must preserve the canonical
  interaction contract and limited-channel alternatives. Builder's prototype
  currently composes existing `ui.chat`, `ui.actions` and `ui.form`; it does not
  establish a new chat transport or grant dropdowns workflow authority.

- [ ] `[must]` Add contract-driven renderer tests for semantic input,
  interactions, action emission, state ownership, and structured failures.
- [ ] `[must]` Add compact and wide browser probes with DOM, accessibility
  tree, screenshot, console, network, and retained failure trace evidence.
- [ ] `[must]` Verify EN/RU key-set parity, interpolation, pluralization,
  locale formatting, and long-content layout for all admitted components.
- [ ] `[must]` Enforce stable dimensions and no incoherent overlap in declared
  representative states.
- [ ] `[must]` Exercise every layout pattern through the same conformance
  harness with at least one multi-region and one compact-disclosure fixture;
  assert region order, scroll owner, focus return, action reachability, and
  absence of clipped or overlapping content.
- [x] `[must]` Correct cross-widget stacking for an open adaptive-toolbar menu;
  later toolbars must not cover its options or intercept pointer events. Retain
  a long-menu browser regression. Generic Ionic root overlays replace the inline
  stacking context; bounded long labels, Arrow opening and Escape focus return
  are exercised in Builder design probes. `displaySelectedLabel=false` keeps
  an authored short trigger. This is not a claim of complete APG conformance.
- [x] `[must]` Remove a widget host from layout when its `visibleIf` is false;
  hidden renderers must not retain flex/grid gaps. Qualification covers plain
  hosts; compact outer collapsible wrappers still need the C3 composition review.
- [x] `[should]` Resolve declared `resource:<id>` images/posters in the generic
  media preview through the page resource registry; preserve missing/loading/
  error states and existing media safety checks. No Builder-specific resolver.
- [x] `[should]` Support opt-in `rememberSelection` for a static command menu's
  top-level `selectedStateKey`, using existing subnet-scoped browser storage
  plus page/widget/button identity. Restore only declared scalar choices without
  action dispatch; exclude dynamic menus, unsafe state paths and all undeclared
  state. Failed actions or navigation to another scope must not save a choice.
  Core schema, types, capability catalog and Client tests evolve together; this
  preference is presentation-only, never workflow authority or draft storage.
- [ ] `[should]` Qualify dense Workbench composition without product-specific
  CSS: long primary-command labels, compact view navigation, hidden-widget gaps
  and two-column tables. Review the table's fixed 520px minimum as a declared
  responsive policy, rather than silently making all narrow tables scroll.
- [ ] `[must]` Verify Dev Tickets target identity through both explicit
  declarative invocation and the global shell entry: derive the source space
  from trusted materialization, retain the exact revision and optional selected
  element, and test screenshot cancellation/upload failure. An explicit DEV
  scope in one Builder button does not qualify the global fallback.
- [ ] `[should]` Retain a viewport-change regression for an already-open
  localized modal: locale, theme, filters, selection and focus must remain
  consistent. The 2026-09-11 Builder picker review exposed mixed EN/RU labels
  during wide-to-compact switching; do not treat a nonblank screenshot as closure.
- [ ] `[should]` Load non-shell components by generated async factories and set
  bundle budgets per loading class.
- [ ] `[should]` Run focused component tests on contract changes and a complete
  cross-component suite on Client/semantic ABI changes.

Exit gate: contract conformance and browser behavior are reproducible without
running a subject-specific Builder prompt.

## C4. Measured Cross-Domain Growth

Current cross-boundary correction (before new primitive work): the semantic
compiler emitted named form buttons and record data sources that `ui.form` did
not execute. Unit-level schema validation missed this mismatch.

- [x] `[must]` Add named-command dispatch and selected-record hydration to the
  generic form; retain edits on operation failure and suppress completion after
  cancelled confirmation. Focused ChromeHeadless suite: 127 tests pass, including
  resource-query details and preservation across selected-record loading and
  same-selection refresh. The retained media browser probe verifies update/save/
  reopen/restore on two editors in both viewports; full CRUD remains below.
  A fresh appointments side sheet also verifies dismissal/focus restoration and
  cancelled mutation confirmations. Read-only record guard inputs and compiled
  numeric conditions now match the Client evaluator without widening payloads.
- [ ] `[must]` Replay the actual compiler artifact in the browser: create,
  inspect, edit, validation, cancellation and deletion on wide/compact surfaces.
  Add this probe to ABI impact checks; structural capability inventory alone
  cannot verify renderer semantics.
  Progress: the retained appointments editor now passes create/read/delete of
  a probe-owned record, update/restore and confirmation cancellation on both
  viewports. Unsupported editor field combinations are marked not exercised,
  never counted as full task coverage. Required-field and EN/RU browser gates
  remain open. Apparent modal background bleed-through was traced to a capture
  during Ionic's enter animation, not a renderer opacity defect. The probe now
  waits for the shadow wrapper's final opacity and finishes finite animations
  before capture. A computed background color alone is insufficient evidence.
- [x] `[should]` Add opt-in local table sorting before pagination and localized
  ISO date/time rendering. Disable local sort for cursor/server pagination;
  never imply a whole-catalog order from one loaded page.
- [x] `[should]` Persist table page size and declared filter modes in browser
  storage scoped by subnet, webspace/scenario and widget; exclude search,
  selection, records and cursors. Add targeted reload and keep bound controls
  synchronized with restored state. Builder metadata edits invalidate its
  catalog. ChromeHeadless: 39 focused checks pass. Browser evidence:
  `table-preferences-20260911-04` verifies reload (including four user-renamed
  titles), full browser reload, filters/page size, wide/compact capture and
  selected new-window preview. Earlier failed captures remain retained.
- [ ] `[should]` Verify modal/side-sheet forms preserve selection, expose
  dismissal and restore focus, including EN/RU and narrow viewports.

Start this phase only after the clean generic Builder baseline. Prioritize a
primitive when failures in at least two unrelated development archetypes show
the same semantic gap. Do not create product-named components.

- [ ] `[must]` Convert baseline failures into typed capability gaps with
  affected jobs, data shapes, operations, layouts, locales, and view states.
- [ ] `[must]` Select the first component tranche from measured reuse and task
  impact, then rerun the same suite and baseline comparison.
- [ ] `[should]` Evaluate form repetition, money/units/computed values,
  attachments, lifecycle/status, schedule/availability, timeline, multi-series
  chart, annotations, and generic collection exploration as candidate
  primitives.
- [ ] `[should]` Add explicit display contracts for relationship labels versus
  stored IDs, localized choice values, time-of-day and numeric units. The
  appointments Prototype displays raw relationship IDs and numeric minutes;
  solve this through generic field/column semantics and compiler mappings,
  with EN/RU, compact/wide and edit-round-trip tests, not an appointments widget.
- [ ] `[should]` Qualify generic collection-tool layout with the retained
  `effort-low-provider-max-20260911-01` and `effort-high-provider-max-20260911-01`
  artifacts. Full-width stacked query controls dominate the first viewport in
  both profiles; editor openers often follow all supporting collections.
  Keep primary actions near their collection, group filters responsively and
  preserve context without forcing one application-specific screen layout.
  Record this as UX evidence, not a retrospective generation failure.
- [ ] `[must]` Distinguish attachment-reference text from a rendered image,
  playable media or readable document in browser task evidence. A populated
  card with a filename is not proof that the requested inspection works.
  Verify actual assets or report a capability/fixture gap; keep missing browser
  probe support separate from model and renderer failures.
- [ ] `[must]` Unify literal dotted-field and nested-path lookup across lists,
  tables, details, command payloads and Core Prototype queries. Add cross-layer
  round-trip fixtures, including empty collections and delayed locale loading.
  The context-engineering cohort exposed valid generated field IDs losing values
  in display and submission; prompting the model to avoid these IDs is not a fix.

- [ ] `[should]` Keep development archetypes and component fixtures visible;
  keep the sealed prompt-autonomy set unavailable to generation and tuning.
- [ ] `[could]` Add map/spatial, scanner/camera capture, richer offline draft,
  and advanced visualization only after measured demand and ownership exist.

Media progress (2026-09-11): Client `6b242dc` adds a generic native image/video/audio
preview to `item.details`, table image cells, and delayed-dictionary refresh for
collection choice labels. Catalog 2.2.0 and semantic v2 declare media field
bindings; compiler lowering, attribution-bearing local media samples and
wide/compact playback/error probes exist. Client `45f567e` unifies dotted-key
display/submission; `09ae8ec` preserves empty-string length. Related component
tests passed (83), with 15 expression tests rerun after the final correction.
The unchanged appointments candidate passed browser edit/save/reopen on both
viewports after the path fix. This is not complete application qualification.

Primitive progress (2026-09-12): Client `388b014` adds stored-record read-only
conditions, authenticated Prototype attachment upload/download, and sanitized
Markdown details. Follow-up stable tracking of file references fixes a repeated
Angular component-creation loop on opening a populated attachment form. Library
browser tasks on 1440px/390px prove upload/save/reopen/download byte equality,
modal dismissal and edit round trips; the generated source was not hand-patched.
- [x] `[must]` Qualify durable attachment references and transfer failures with
  provider tests and browser byte-level evidence, not filename-only fixtures.
- [ ] `[must]` Add generic live resource-backed form options with invalidation,
  loading/error states and preserved drafts. Compile relationship selectors to
  current records, not seed-time enums; qualify newly created related records.
  Client `ab83285` implements form lookup, draft preservation, source status and
  retry (EN/RU); 138 focused Client tests passed. Resource-query status/retry now
  uses the same normalized request identity as loading. Full browser qualification
  and collection lookup propagation are still required.
  Run 05 procurement now proves creating a parent and selecting its new identity
  in a child editor on 1440px/390px; both records are visibly persisted. Volunteer
  assignments also pass create/visible-record tasks. Unsupported delete operations
  are not used for cleanup; retained fixtures are reported explicitly.
- [x] `[must]` Observe late-created and replaced resource branches for scenario
  dictionaries. Run 05 dictionary probes remain at revision 1 with zero value
  labels after 30 seconds on both viewports: subscribing only to an already
  existing nested Y.Map misses materialization. Use stable root observation with
  path-scoped invalidation, test replacement/deletion/unsubscribe, and rerun the
  same unchanged artifacts. Longer waits are not the fix.
  Stable-root path observation now passes 121 focused tests. The unchanged
  run 05 volunteer artifact loads all 13 value labels at 1440px/390px, with
  dictionary revisions 3/2, zero loading indicators and no horizontal overflow.
- [ ] `[should]` Group collection search/filters into responsive tool regions.
  The current library still spends much of its first viewport on stacked
  full-width filters. This is shared Client/compiler UX debt, not evidence that
  the model ignored a working layout primitive.
- [x] `[must]` Honor canonical `readOnly` and legacy `readonly` field flags
  through the actual renderer. Run 08 volunteer command checks found an enabled
  boolean field despite Core's `readOnly=true`; generated source must remain
  unchanged during the renderer regression test. Client `93ef263` fixes field
  parsing and change guards; 25 focused form tests pass. The unchanged volunteer
  prototype passed create/visible-record/delete-cleanup at 1440px/390px after
  this fix, including the disabled conflict context fields.
- [x] `[must]` Keep unavailable read-only lookup context from blocking unrelated
  writable commands. Such fields remain visibly unavailable and immutable;
  editable unavailable selectors still block submission. Test validation,
  save-draft, direct field handlers, and stored-record locks independently.
  Client `93ef263` covers these paths without suppressing loading/error status.
  The library's unchanged stored-record UI/provider lock probe also passes on
  both viewports after the Client change.
- [ ] `[must]` Diagnose intermittent lookup readiness separately from field
  editability. One post-fix volunteer replay still stopped with two loading
  selectors before a later replay passed unchanged. Retain both, capture source
  received/status/option counts, and measure request timing; neither a longer
  assertion wait nor a single passing rerun establishes reliability.
- [ ] `[should]` Include clipped child content in narrow-screen review, not
  only document-level overflow. Run 08 media cards clip metadata at 390px even
  though their outer grid fits; this is UX debt, not a passing visual verdict.

Exit gate: each new primitive improves matched task outcomes without adding
domain branches or regressing Client conformance, bundle, or latency gates.

## Readable Resource Projections

- [x] `[must]` Preserve explicit column wrapping and alignment through normal
  widget input parsing, not only direct component-property tests. Keep typed
  dates and numbers unbroken by default without overriding explicit overflow.
- [x] `[must]` Share live related-record caption projection across table cells,
  list metadata and details; preserve scalar canonical IDs, invalidate on source
  changes and release subscriptions. Choice captions retain the authored locale
  as a fallback without demanding another language from generation.
- [x] `[must]` Admit owned `skill` reads for the same related-record caption
  projection during Automation, not only Prototype `resourceQuery` sources.
  Inventory's independent browser journey exposed UUIDs after otherwise valid
  Automation because the shared parser dropped the skill source. Use the shared
  causal data loader; keep IDs unchanged and cover updates, failure cleanup and
  literal dotted label fields. The focused table/list/label suite passes 58 tests;
  live browser qualification below remains a separate gate.
- [ ] `[must]` Qualify rename, removal, reload and unavailable lookups through
  real browser surfaces in both layouts. Unit tests do not prove HTTP latency
  or live materialization. Collection-filter lookups and datasets exceeding the
  current 100-record lookup window remain separate debt.

Catalog 2.3.0 describes these additive display inputs. Semantic compilation uses
the same resource-backed label contract as form selectors, with source-map links.
Host language remains the user's setting; evaluation browsers must set their
declared locale rather than changing a user's browser preference implicitly.

## Automation Event Contracts

- [x] `[must]` Allow clearing an optional radio choice without inventing a
  domain-specific sentinel option. Keep required/disabled/in-flight controls
  protected, preserve explicit null on submission, and localize the clear action.
  Focused form suite: 41 passing tests. Native Prototype review at 1440/390 px
  qualifies select/save/reopen and clear/save/reopen on the same record.
  No new widget or application-specific Client logic is needed.

- [x] `[must]` Align item.details named-command sequencing with command bars:
  read/selection/open-editor actions sharing `on=click:<command>` run in order,
  stop on false/throw and reject repeated clicks while pending. Honor enabledIf,
  render one labeled command and preserve independent generic click buttons.
  Library Automation exposed this renderer gap; 24 focused Client tests pass.
  Actual unchanged-application qualification remains tracked in the Builder map.
- [ ] `[should]` Audit action cancellation/failure semantics across remaining
  widgets: named chains must not continue into side effects after a cancelled
  confirmation or denied prerequisite. Do not assume all event dispatchers use
  the same contract merely because WebUI shares an action schema.
- [x] `[must]` Describe and test loaded revision propagation in board moves,
  including the lane-selector alternative and optimistic rollback. The renderer
  spreads displayed fields at event top level; owned tools bind `$event.revision`,
  while forms use `$event.record.revision`. The catalog and Automation binding
  guide now state this existing distinction. Eleven focused board tests pass.
  A model correctly stopped at the former documentation gap instead of inventing
  an event field or reading a newer revision to bypass stale-write checks.
- [ ] `[should]` Keep human-readable related-choice captions short enough for
  native compact selects while retaining precise details elsewhere. Roster's
  long UTC interval labels are truthful but truncated by the native control;
  functional Automation acceptance is not unconditional visual approval.

## Media Descriptor Compatibility

- [x] `[must]` Render the SDK browser-media `path` descriptor in the shared image
  surface using authenticated local/routed endpoints, without per-Application
  URL conversion. Scope descriptor caches to the selected hub route. Ten focused
  Client tests and About's wide/compact existing-draft pixel/reopen checks pass.
- [x] `[must]` Support raster collection icons with a vector fallback and fixed
  geometry. Persist explicit local desktop overrides independently of Application
  releases; qualify shared-cache Webspace isolation and restart/rebuild retention.
  The focused suites pass 156 Client and 105 Core tests. Browser evidence in
  `e2e/artifacts/builder/desktop-raster-icon-20260916/review-04` passes nonblank
  image, fallback and reload checks at 1440/390px. This local appearance option
  does not close portable About asset publication.
- [ ] `[must]` Qualify the generic crop/position/zoom draft and explicit apply
  boundary described in `content-generation.md` (CG-06). An image that renders
  correctly is not proof of portable Application asset publication.

## Deferred

- [ ] `[deferred]` Generate or ship arbitrary Angular/JavaScript renderer code
  as part of Builder Prototype creation.
- [ ] `[deferred]` Introduce a micro-frontend runtime or separately deployed
  component marketplace before the generated contract registry is proven.
- [ ] `[deferred]` Build a general visual component editor or multi-user
  semantic layout editor.
- [ ] `[deferred]` Tune components directly against the sealed held-out suite.
