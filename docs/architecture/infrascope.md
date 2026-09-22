# Infrascope Retirement Architecture

`Infrascope` is a legacy operator-facing composition. It is not part of the
target AdaOS product model and will be removed after its useful capabilities
have moved to the application-centric control surfaces.

The target is not a renamed Infrascope. It is a set of public platform
services and projections consumed by the products that own each user task:

- `web_desktop` owns Home, Devices, Activity, Settings, System, and
  Development entry points;
- `Applications` owns installed applications, Marketplace discovery,
  lifecycle, release/channel state, permissions, component topology, and
  desired/observed execution placement;
- `Users & Access` owns people, invitations, memberships, sessions, device
  trust, grants, recovery, and access audit;
- Builder owns development orchestration and Codex-facing diagnostics;
- Root/Core owns canonical system, operation, reliability, configuration,
  audit, and access data. A scenario or skill must not become a second source
  of truth for these records.

The implementation sequence and deletion gates are tracked in
[Infrascope Retirement Roadmap](infrascope-roadmap.md).

## Decision

1. Do not add new product behavior to `scenario:infrascope`,
   `skill:infrascope_skill`, or the Infrastate Inventory surface.
2. Preserve the current release only as a compatibility and recovery
   checkpoint while replacement surfaces are qualified.
3. Move behavior by ownership and use case, not by copying Infrascope panels.
4. Extract reusable data acquisition and mutations behind neutral SDK/MCP
   contracts before deleting a legacy skill.
5. Delete product-specific projections, aliases, actions, presets, and Client
   extensions only after no supported consumer depends on them.
6. Do not remove generic Core services merely because Infrascope consumed
   them. System model, operations, reliability, update, policy, audit,
   projection, and WebIO services remain platform capabilities.

## Target Ownership

| Legacy capability | Target owner | Target presentation |
| --- | --- | --- |
| Application/project/skill/scenario inventory | Applications | Application summary, typed conditions, compact attention, and expandable component topology |
| Marketplace install/update/remove | Applications | Reviewed lifecycle operations and bounded reviewed batch updates |
| desired/observed runtime placement | Applications | Application detail, node/component drill-down |
| node, browser, session and link state | Desktop Devices | Device list, current-device context, connection detail |
| people, roles, grants, invitations and recovery | Users & Access | Subject-centric administration and audit |
| subnet/runtime health and service state | Desktop System | Compact health summary with diagnostic drill-down |
| active and historical operations | Desktop Activity and contextual product views | Shared operation projection |
| core update, slot, validation and rollback | Desktop System and Development | Governed system operation, not inventory action |
| logs, events, test reports and search | Desktop Development, Builder/Codex | Bounded diagnostic MCP with search and redaction |
| environment and runtime flags | Desktop Settings/System | Typed, scoped configuration; secrets remain opaque |
| Root MCP/Codex connection leases | Users & Access or Development, depending on actor | Audited credential/session management |
| generic object inspection | Owning application or System diagnostics | Stable object references and on-demand detail |

The first migration rule is already in force: Applications owns product
inventory. Infrascope/Infrastate may expose technical compatibility diagnostics
until their replacement is qualified, but may not mutate Application catalog
or installation state as a parallel authority.

## Platform Contracts To Preserve

The following capabilities are useful and should survive retirement under
neutral names and ownership:

- canonical system objects and relationships;
- node/member/browser/device reliability projections;
- operation status, progress, cancellation, retry, rollback, and receipts;
- core update status, slot identity, validation evidence, and recovery state;
- service health and bounded diagnostic reports;
- searchable logs and events through an authorized MCP/API, without arbitrary
  local-path reads;
- typed configuration, drift, effective-source, and restart-impact metadata;
- projection demand, stream budgets, snapshot-on-subscribe, and guard
  diagnostics;
- object detail references that can deep-link from an owning product into
  technical diagnostics.

These contracts must be independent of whether Infrascope is installed. Their
tests should use neutral fixtures such as `system`, `operations`, or
`diagnostics`, not `infrascope` as the implicit default application.

## Current Coupling Assessment

The legacy product itself lives in `project:ops_infrascope`. The preserved
recovery checkpoint originally composed:

- `scenario:infrascope`;
- `skill:infrascope_skill`;
- `skill:infrastate_skill`;
- `skill:infra_access_skill`;
- `skill:subnet_env`;
- `skill:service_doctor_skill`.

Most business logic is in those skills, especially the large Infrastate
snapshot/action composer. Core does not import the Infrascope skill as its
canonical domain model. The coupling is nevertheless material because legacy
names and payload shapes appear in several adapters:

- Node API still exposes deprecated `/infrastate/*` routes and an
  `infrastate.action` compatibility command for the accepted Stable desktop;
- the compatibility skill and Client extension still use `infrastate.*`
  receiver and action names;
- the Client registers an Infrastate action/data extension;
- the Builder compatibility fixture and retained Stable sources still use
  Infrascope as a specimen.

Default setup activation, tool-bridge special routing, active subnet member
projection publication, scenario projection aliases, Yjs load-mark naming,
and active NLU/projection-pilot examples have been neutralized. New
`ops_infrascope` installations no longer compose `skill:infrastate_skill`.

This is historical adapter coupling, not proof that the product must remain.
Core can be cleaned after replacement consumers use neutral contracts. A
single destructive removal would be unsafe because it would also remove
working update, reliability, diagnostics, and access paths that currently sit
behind legacy adapters.

## Neutralization Strategy

Introduce neutral contracts before removing compatibility names:

- `system.snapshot` / `system.action` for host and subnet health;
- `operations.list|get|act` for long-running work;
- `runtime.components` and `runtime.placement` for technical topology;
- `diagnostics.logs.search`, `diagnostics.events.search`, and bounded report
  reads for humans and Codex;
- `configuration.describe|get|plan|apply` for typed settings and drift;
- the existing Applications and Users & Access SDK/MCP contracts for product
  lifecycle and identity/access behavior.

During migration, legacy routes may adapt to these contracts. New consumers
must use only the neutral contracts. Compatibility adapters must carry usage
telemetry and a removal version; they must not contain an independent cache or
authority.

Legacy `Test all`, `Validate all`, and `Update all` buttons are not migration
requirements by themselves. Applications owns release-aware readiness and
reviewed product updates with per-Application evidence. Desktop System owns
Core/node validation, update, slot, drain, and rollback. Shared diagnostics and
operation services provide the reusable evidence; neither product recreates an
Infrastate-shaped action dispatcher.

## Data And Security Boundary

- Yjs and WebIO carry bounded projections, never canonical operation or access
  state.
- Mutations use policy-checked SDK/MCP commands with idempotency and audit.
- Logs and diagnostics are searched through an authorized service. Product
  code and Codex do not read arbitrary filesystem paths.
- Configuration exposes typed values and effective-source metadata. Secret
  values remain opaque references and are never projected into browser state.
- Remote node data is attributed to node, generation, timestamp, and freshness;
  stale technical data cannot impersonate current authoritative state.

## Removal Gate

`ops_infrascope` can be uninstalled and deleted only when all of the following
are true:

1. every capability in the ownership table has a qualified replacement or an
   explicit decision to drop it;
2. Desktop, Applications, Users & Access, and Builder operate with Infrascope
   and Infrastate disabled at bootstrap;
3. no supported scenario reads `data/infrascope`, `data/infrastate`, or
   `infrascope.*` / `infrastate.*` WebIO receivers;
4. no supported action targets `infrascope.action` or `infrastate.action`;
5. member reliability, updates, diagnostics, and access operations pass with
   neutral projections and commands;
6. setup presets, Client extensions, NLU/catalog fixtures, and production tests
   no longer require the legacy ids;
7. retained runtime data has a documented export/retention decision;
8. one release keeps read-only compatibility telemetry long enough to prove
   zero use, after which aliases and legacy API routes are removed.

The current DEV checkpoint `ops_infrascope@0.10.24` is preserved for recovery
and comparison. It is deliberately not promoted as a new Beta because the
target direction is decomposition and retirement.
