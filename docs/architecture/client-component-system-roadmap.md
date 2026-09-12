# Client Component System Roadmap

Status: active prerequisite and growth roadmap for the universal AdaOS Client.

Last reviewed: 2026-09-11.

Architecture owner: [Web UI Architecture](web-ui-architecture.md).
Builder dependency: [Builder Intent-to-Prototype Architecture](builder-intent-to-prototype.md).

This roadmap removes application-domain knowledge from the universal browser
runtime, makes the semantic/component ABI truthful, and establishes a
repeatable way to grow the Client component set. It owns Client work only.
Builder intent interpretation, capability selection, compilation, and
evaluation remain in their Builder architecture and roadmap.

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
- [ ] `[should]` Replace public `any`/unbounded dictionary inputs with generated
  discriminated types at the renderer boundary.
- [ ] `[should]` Require fixtures for empty, loading, error, permission-denied,
  long-content, compact, wide, English, and Russian states where applicable.

Exit gate: adding or changing a component has one declaration and a complete,
machine-checked impact set.

## C3. Renderer Conformance And Delivery

- [ ] `[must]` Add contract-driven renderer tests for semantic input,
  interactions, action emission, state ownership, and structured failures.
- [ ] `[must]` Add compact and wide browser probes with DOM, accessibility
  tree, screenshot, console, network, and retained failure trace evidence.
- [ ] `[must]` Verify EN/RU key-set parity, interpolation, pluralization,
  locale formatting, and long-content layout for all admitted components.
- [ ] `[must]` Enforce stable dimensions and no incoherent overlap in declared
  representative states.
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

Exit gate: each new primitive improves matched task outcomes without adding
domain branches or regressing Client conformance, bundle, or latency gates.

## Deferred

- [ ] `[deferred]` Generate or ship arbitrary Angular/JavaScript renderer code
  as part of Builder Prototype creation.
- [ ] `[deferred]` Introduce a micro-frontend runtime or separately deployed
  component marketplace before the generated contract registry is proven.
- [ ] `[deferred]` Build a general visual component editor or multi-user
  semantic layout editor.
- [ ] `[deferred]` Tune components directly against the sealed held-out suite.
