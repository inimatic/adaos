# Client Component System Roadmap

Status: active prerequisite and growth roadmap for the universal AdaOS Client.

Last reviewed: 2026-09-10.

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

The gate remains open. The generated inventory makes the next failures
explicit: the semantic contract advertises action kinds `apply_review_change`,
`emit`, `navigate`, and `patch_y`, and binding kinds `projection` and `view`,
without corresponding lowering. Product-specific compatibility still exists
in generic chat/voice/command/file widgets, the collection grid, desktop shell
orchestration, and selected desktop compatibility widgets. Those branches
require typed ports or explicit product ownership before a clean Builder
baseline.

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

Exit gate: the generic Client profile can render and fail honestly with all
product extensions disabled. This gate is required before the clean Builder
baseline.

## C2. Single Component Contract

- [ ] `[must]` Publish a versioned component contract beside each supported
  implementation. It declares semantic roles, data shapes, properties,
  defaults, events, actions, side effects, state ownership, i18n,
  accessibility, responsive behavior, loading class, compatibility, fixtures,
  and compiler mappings.
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
- [ ] `[should]` Load non-shell components by generated async factories and set
  bundle budgets per loading class.
- [ ] `[should]` Run focused component tests on contract changes and a complete
  cross-component suite on Client/semantic ABI changes.

Exit gate: contract conformance and browser behavior are reproducible without
running a subject-specific Builder prompt.

## C4. Measured Cross-Domain Growth

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
- [ ] `[should]` Keep development archetypes and component fixtures visible;
  keep the sealed prompt-autonomy set unavailable to generation and tuning.
- [ ] `[could]` Add map/spatial, scanner/camera capture, richer offline draft,
  and advanced visualization only after measured demand and ownership exist.

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
