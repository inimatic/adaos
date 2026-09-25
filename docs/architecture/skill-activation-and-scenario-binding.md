# Skill Activation And Scenario Binding

## Goal

Introduce one explicit runtime model for:

- always-on service skills
- lazy scenario-support skills
- on-demand tool or UI helper skills
- scenario to skill bindings

The model must improve startup behavior, reduce accidental background work, and avoid metadata drift between `skill.yaml` and `scenario.yaml`.

## Problem

Today AdaOS already has:

- skill install and activation mechanics
- scenario manifests with legacy `depends`
- runtime code that can eagerly subscribe and refresh during startup

But it does not yet have one typed architectural layer that answers:

- when should a skill be active
- who declares that a scenario needs a skill
- whether a skill is always-on, lazy, or fully on-demand
- how heavy background workers should behave when no client is using the scenario

This gap is visible in `infrascope_skill`, where a UI-facing scenario support skill can do heavy refresh work during runtime startup even when the scenario is not actively open.

## Design Principles

### Separate loaded from active

A skill may be:

- `loaded`
  - manifest is known
  - handlers can be discovered
  - lightweight event subscriptions may exist
- `active`
  - background workers may run
  - periodic refresh is allowed
  - scenario-facing projections may be eagerly maintained

This separation lets AdaOS keep discovery simple without forcing every skill into eager runtime work.

### Defer tool-only code, keep event wiring explicit

AdaOS loads every selected manifest at boot, but imports only handler modules
that must register event subscriptions or explicitly request eager/startup
loading. Tool-only handlers are imported by the existing tool runner on their
first invocation.

That means:

- event subscriptions are registered during startup
- tool declarations, projection declarations, routes and other manifest data
  remain discoverable without importing tool-only Python modules
- `on_demand` tool-only modules do not run the full runtime safety scan or
  contend for the process-wide Python import lock during desktop startup
- inactive handlers must avoid repository, git, config, filesystem, or YDoc-heavy work
- inactive handlers may enqueue lightweight invalidation or no-op quickly
- expensive refresh and background maintenance must wait for activation

AdaOS does not dynamically unsubscribe event handlers in this pass. It removes
only imports that are provably unnecessary for event delivery. If the manifest
is missing an event declaration, a static `@subscribe(...)` source check keeps
the handler on the early-import path.

### One source of truth for physical runtime dependency ownership

Within the current physical runtime closure, scenario-to-skill dependency
ownership belongs to the scenario manifest.

That means:

- scenario manifests declare which skills they require or optionally use
- skill manifests declare activation policy and runtime constraints
- skill manifests do not own scenario dependency truth

This avoids a fragile fully mirrored declaration model.

This rule does not make a skill ID a semantic Application requirement. Native
Applications declare `ApplicationRequirement` records against capability/state
contracts. Resolution selects binding definitions and exact packages, then
projects the selected physical scenario/skill closure into the manifests and
release records interpreted here. Existing component-first Applications remain
valid compatibility inputs.

The activation policy below therefore answers when an already selected runtime
component is loaded or active. It does not select the implementation that
satisfies an Application capability.

### Activation policy belongs to the skill

A skill still needs to declare how it behaves at runtime.

That belongs in `skill.yaml`, because the skill owns:

- whether it is `eager`, `lazy`, or `on_demand`
- whether startup activation is allowed
- whether background refresh is permitted
- whether it only becomes active when specific scenarios are active

### Legacy compatibility first

Existing scenario manifests use `depends`.

The target architecture keeps them working by treating:

- `depends` as a legacy alias of `runtime.skills.required`

New code should prefer the typed `runtime.skills` block.

## Manifest Model

### `scenario.yaml`

Target typed form:

```yaml
runtime:
  skills:
    required:
      - infrascope_skill
    optional:
      - telemetry_skill
```

Compatibility form:

```yaml
depends:
  - infrascope_skill
```

Rules:

- `runtime.skills.required` is authoritative for mandatory scenario support
- `runtime.skills.optional` means the scenario can use the skill when available, but installation or activation must not fail if it is absent
- `depends` is read as additional required skills for compatibility

### `skill.yaml`

Target typed form:

```yaml
runtime:
  python: "3.11"
  activation:
    mode: lazy
    startup_allowed: false
    background_refresh: false
    when:
      scenarios_active:
        - infrascope
      client_presence: true
      webspace_scope: active
```

Builder-authored manifests also contain a deterministic
`runtime.activation.assessment`. It records the Builder compiler identity,
handler digest, observed and declared subscription topics, classification and
decision reasons. This makes the boot decision reviewable and invalidates it
when handler source changes.

Supported activation modes:

- `eager`
  - runtime should keep the skill active by default
  - suitable for service or platform skills
- `lazy`
  - skill may be loaded, but expensive work starts only after activation conditions are met
  - suitable for scenario support skills
- `on_demand`
  - no background work by default
  - suitable for tool providers and detail inspectors

Supported `when` conditions in the first typed pass:

- `scenarios_active`
- `client_presence`
- `webspace_scope`
- `webspaces`

## Runtime Interpretation

### Service skills

Service skills should usually use:

```yaml
runtime:
  activation:
    mode: eager
```

They may still choose `lazy` if the service should stay dormant until a client or route explicitly needs it.

### Scenario support skills

Scenario support skills should usually use:

```yaml
runtime:
  activation:
    mode: lazy
    startup_allowed: false
    background_refresh: false
```

They should become active only when:

- a dependent scenario is active in a webspace
- the webspace currently has an interested client, or the runtime has another explicit reason to keep projections warm

### On-demand skills

On-demand skills should usually use:

```yaml
runtime:
  activation:
    mode: on_demand
```

They should answer explicit tool, API, or UI requests, but should not keep heavy background workers alive.

## Startup Policy

User-facing lazy skills must not block runtime startup.

Target rule:

- boot-critical runtime code may load the manifest
- boot-critical runtime code must not start expensive refresh loops for a lazy or on-demand skill unless startup explicitly admits it

For a skill like `infrascope_skill`, this means:

- no eager full snapshot rebuild on `sys.ready`
- no heavy inventory refresh on the event loop during boot
- expensive refresh should move into `asyncio.to_thread(...)` or another non-loop worker path

## Webspace Semantics

Activation decisions should be made per webspace, not only globally.

Important examples:

- scenario `infrascope` is active in webspace `default`
- the same skill may remain inactive for another webspace
- background refresh should follow the active webspace scope, not all webspaces indiscriminately

`webspace_scope` is therefore part of the activation policy.

## Registry Surface

Workspace registry entries should surface normalized metadata so higher-level services do not need to parse raw manifests repeatedly.

Target normalized fields:

- skill registry entry:
  - `activation`
- scenario registry entry:
  - `skills.required`
  - `skills.optional`

This gives one cheap lookup surface for orchestration, diagnostics, and UI tooling.

## Initial Implementation Scope

The first implementation pass should do only the minimum needed to establish the architecture safely:

1. Add typed manifest parsing and normalization.
2. Surface normalized metadata in workspace registry entries.
3. Teach scenario dependency bootstrap to read `runtime.skills.required` with compatibility for `depends`.

Later passes should add:

1. runtime activation state tracking
2. per-webspace active/inactive transitions
3. lazy background worker admission
4. explicit client-presence gating
5. non-blocking refresh execution for heavy skills such as `infrascope_skill`

## Current Implementation Status

Implemented in the current pass:

1. typed manifest parsing for:
   - `skill.runtime.activation`
   - `scenario.runtime.skills.required`
   - `scenario.runtime.skills.optional`
2. compatibility mapping from legacy `depends` to `runtime.skills.required`
3. normalized activation and skill-binding metadata in workspace registry
4. scenario dependency bootstrap now reads normalized required skill bindings
5. `infrascope_skill` and `infrastate_skill` are marked as lazy scenario-support skills
6. heavy background refresh work for `infrascope_skill` and `infrastate_skill` no longer runs on the event loop thread
7. shutdown handling now suppresses executor-closing races for those background refresh workers
8. skill context resolution now prefers workspace registry metadata before falling back to repository `ensure()` logic
9. SDK subscription wrappers now evaluate activation policy before calling user handlers
10. lazy and on-demand subscription handlers are skipped cheaply when the policy does not admit the current event payload
11. skipped lazy subscription events are logged with the skill name, topic, activation mode, and skip reason
12. boot discovery loads all manifest declarations but defers tool-only handler
    imports until the first tool call
13. installed source with `@subscribe` remains early-loaded even when legacy
    `events.subscribe` metadata is incomplete
14. Builder compiles `runtime.activation` from changed handler source, records a
    digest-bound assessment, synchronizes literal `@subscribe` topics exactly
    into `events.subscribe`, and validates the result before checkpointing;
    service runtimes and dynamic decorators preserve their explicit manifest
    inventory because source inspection is not complete for those cases
15. deferred service-skill startup waits for every client-started desktop room
    to complete its first bootstrap, subject to a bounded headless-node grace
    period
16. runtime/workspace discovery is location-only for selected runtime skills;
    manifest parsing is cached for one loader pass and happens only while
    declarations and activation policy are evaluated
17. runtime services are excluded from the API process unless their source
    manifest explicitly declares `runtime.in_process_events: true`; the
    resolved delivery manifest is not used as a substitute for this authoring
    decision
18. the service supervisor starts only `mode: eager` services during the
    post-ready sweep; `lazy` and `on_demand` services start on their first
    stable tool or authenticated service-UI request and then enter the ordinary
    supervised health/restart lifecycle

On the 2026-09-24 development workspace, cold runtime plus workspace discovery
fell from approximately 8.8 seconds to 0.35 seconds (`runtime=74.954 ms`,
`workspace=278.259 ms`, repo fallback `4.340 ms`). The complete handler phase,
including declaration loading, policy assessment, safety checks and the actual
imports, took 5.692 seconds: 52 skills were selected, 31 were imported and 21
tool-only handlers were deferred. These values are an observed inventory, not
a stable product limit.

On the 2026-09-25 inventory all three previously auto-started service skills
(`mlflow_tracker_skill`, `rasa_nlu_service_skill`, and
`research_manager_skill`) declared `mode: lazy`. The old supervisor treated
`startup_allowed: true` as an instruction to start the process and spent about
25.8 seconds after first paint, including about 18.5 seconds on MLflow. Runtime
now interprets the mode first: `startup_allowed` permits a startup activation
but does not turn `lazy` into `eager`. Explicit tool and service-UI routes are
the activation rails for these services. A clean restart then reported
`attempted=0`, `skipped=8` for the service sweep; the remaining 4.1 seconds were
discovery/status work, not provider process startup. A first stable-tool call
subsequently started `research_manager_skill` on demand and reached its tool in
about 2.7 seconds.

Important current limitation:

- AdaOS does not yet have a global activation service.
- Event-bearing lazy skills are still imported and subscribed at startup.
- Dynamic subscribe/unsubscribe wiring is not implemented.
- The current implementation gates SDK-decorated subscription handlers, but it does not yet centralize activation state, background worker lifecycle, or client-presence accounting.

This is an intentional transitional step:

- first remove event-loop blocking and startup regressions
- then centralize activation state and policy enforcement

Current subscription decision:

- eager skills may use normal always-registered handlers
- lazy and on-demand event skills keep early subscriptions, but SDK-decorated handlers must pass activation-policy admission before user code runs
- tool-only skills are not imported during boot unless their manifest explicitly requests eager/startup loading
- central runtime activation should eventually decide whether background workers and non-SDK entry points are admitted, but handler registration itself does not need to be deferred in the first production-safe rollout

## Migration Guidance

Recommended migration order:

1. Keep existing `depends` in current scenarios.
2. Add `runtime.skills.required` to the same scenarios.
3. Add `runtime.activation` to scenario-support skills.
4. Update runtime services to use normalized registry metadata.
5. Remove legacy-only assumptions after the runtime path no longer depends on `depends`.

## Remaining Work

Still required for the target architecture:

1. introduce a shared activation runtime that tracks `loaded` vs `active`
2. complete per-webspace enforcement of `background_refresh`, scenario state,
   and `client_presence`; service process startup now respects activation mode,
   but a global activation authority still does not exist
3. decide whether a later optimization should add truly deferred event
   subscription wiring; the current safe boundary defers only tool-only modules
4. move more hot-path metadata reads from repository/git/config access into registry or SQLite-backed fast paths
5. convert UI-heavy scenario skills to true on-demand detail loading instead of broad eager projection rebuilds

## Decision

AdaOS should use an asymmetric but explicit architecture:

- scenarios declare required and optional skills
- skills declare activation policy
- runtime decides when a skill is loaded versus active

This keeps dependency ownership stable, allows service-style skills, supports lazy UI-facing skills, and gives a clear path to removing startup stalls caused by eager scenario support work.
