# Semantic State Plane

## Goal

Define the minimal target-state architecture for browser-visible shared state reliability in AdaOS.

The purpose of this document is not to add more status objects.
It is to keep only the kernel-level entities that answer distinct operational questions:

- can this actor communicate at all?
- is the shared state for this webspace semantically current?
- is a skill or core service applying unsafe pressure to the primary Yjs document?

Read this together with:

- [Member-Hub Connectivity](member-hub-connectivity.md)
- [Operational Event Model](operational-event-model.md)
- [Realtime Reliability Roadmap](realtime-reliability-roadmap.md)
- [AdaOS Realtime Sidecar](adaos-realtime-sidecar.md)

## Why this exists

AdaOS now has enough realtime and browser-facing behavior that one aggregated readiness bit is no longer truthful enough.

Recent incidents showed a real pattern:

- upstream link can already be healthy
- command/control route can already be healthy
- browser can still see stale operational data
- heavy skill writes into the shared desktop Yjs document can amplify this mismatch and destabilize recovery

That means the system must distinguish:

- connectivity truth
- semantic sync truth
- pressure and policy around writes to the primary shared document

without inventing unnecessary extra entities.

## Design constraints

### 1. Keep the kernel model minimal

The kernel should persist and publish only three canonical contracts:

- `connectivity`
- `state_sync`
- `yjs_pressure`

Everything else should be derived.

### 2. Do not create surface-specific truth objects

The kernel should not introduce separate first-class entities for:

- `surface_delivery`
- `projection_freshness`
- `hub_root_browser`

Those are useful views, but they should be computed from the three canonical contracts above.

### 3. Keep Yjs as the primary shared-state plane

Yjs remains the primary collaborative state plane.

Snapshot fallback is allowed only for:

- first bootstrap
- explicit recovery
- hard degraded incidents

It must not silently become a second steady-state transport.

### 4. Enforce safety at the kernel boundary

Skills will continue to evolve quickly.
The kernel must therefore protect the primary shared document from unsafe write amplification, even when the skill author did not intend harm.

## Canonical contract 1: Connectivity

### Question answered

Can this actor communicate at all?

### Why this must exist

A healthy or degraded upstream link is operationally meaningful even when browser sync is stale.

Examples:

- `hub -> root`
- `member -> hub`
- browser control/route channel to the active runtime

### Canonical shape

Suggested minimum shape:

```json
{
  "kind": "hub_root | member_hub | browser_control_route",
  "scope_id": "node_or_session_id",
  "transport_state": "ready | degraded | disconnected",
  "transition_state": "ready | reconnecting | waiting_restart | restarting | paused_for_update | disabled",
  "planned_transition": {
    "active": true,
    "reason": "core_update | memory_pressure_critical | manual_restart"
  },
  "blockers": [],
  "served_by": "runtime | supervisor | sidecar"
}
```

### Notes

- This contract says nothing about whether browser-visible shared state is current.
- `ack` on a command/event path belongs here, not in `state_sync`.

## Canonical contract 2: State Sync

### Question answered

Is the shared state for this webspace semantically current and materialized?

### Why this must exist

A browser may have a healthy control route and still render stale data.
That is a different failure class from transport loss.

This contract exists to prevent the system from reporting "ready" when only the control path is healthy but semantic state is not.

### Scope

`state_sync` is defined per webspace.

The primary MVP webspace is `desktop`, but the model should not assume that only one webspace matters forever.

### Canonical shape

Suggested minimum shape:

```json
{
  "webspace_id": "desktop",
  "transport_state": "attached | degraded | disconnected",
  "first_sync_state": "pending | complete | timeout",
  "semantic_state": "ready | stale | degraded",
  "freshness_state": "fresh | aging | stale",
  "last_good_sync_at": 1778055331.0,
  "last_materialization_at": 1778055331.0,
  "replay": {
    "mode": "snapshot_plus_diff",
    "cursor": "3/32"
  },
  "fallback_mode": "off | one_shot_recovery | hard_degraded_recovery",
  "blockers": []
}
```

### Notes

- `state_sync` is where the kernel records whether the browser can trust the materialized shared state.
- `projection_freshness` should be a field inside this contract, not a separate entity.
- A browser status line such as `sync=degraded` should be derived from this contract, not reconstructed from multiple unrelated heuristics.

## Canonical contract 3: Yjs Pressure

### Question answered

Is a skill or core path applying unsafe pressure to the primary shared Yjs document?

### Why this must exist

This is no longer a theoretical problem.
Operational skills can produce large or repeated writes that:

- broaden browser invalidation
- slow recovery and reconnect
- increase snapshot churn
- correlate with misleading or stale browser-visible state
- in the worst case destabilize adjacent runtime behavior

The kernel needs a first-class contract for this because "just log it" is not enough.

### Canonical shape

Suggested minimum shape:

```json
{
  "webspace_id": "desktop",
  "owner": "_by_owner/skill_infrastate_skill",
  "recent_bytes": 167296,
  "recent_writes": 1,
  "peak_bps": 167296.0,
  "peak_wps": 1.0,
  "policy_state": "ok | warn | throttle | block",
  "target": "primary_shared_doc",
  "reason": "write_amplification | broad_branch_rewrite | repeated_reseed",
  "blocked_roots": []
}
```

### Policy states

The kernel should keep only three enforcement states:

- `warn`
  - observe and surface the source clearly
- `throttle`
  - coalesce, rate-limit, or defer writes to the primary shared doc
- `block`
  - reject writes to the primary shared doc and make the refusal operator-visible

No extra `quarantine` state is required at the contract level.
If a future implementation needs a shadow branch or alternate write path, that is one possible realization of `block`, not a new public state.

## Derived views

The following should remain derived views, not canonical kernel entities:

- browser status line
- `Infra State` summary string
- per-surface trust badges
- legacy aggregate terms such as `hub_root_browser`

Those views may remain user-facing, but they must be computed from:

- `connectivity`
- `state_sync`
- `yjs_pressure`

This preserves one source of truth while allowing compact UI.

## Architectural rules

### 1. Command acceptance is not state delivery

`ack` means:

- the runtime accepted a command

It does not mean:

- the resulting state is already materialized into the browser-visible shared document

### 2. Connectivity does not imply semantic freshness

The kernel must never imply that `connectivity=ready` means `state_sync=ready`.

### 3. Operational skills must not repeatedly rewrite broad desktop branches

Operational views such as `infrastate` may publish summary data into shared desktop state,
but they must not rely on high-frequency broad branch rewrites as a normal steady-state mechanism.

### 4. Snapshot fallback is recovery, not transport

Snapshot fallback must stay bounded and explicit.
If the normal user experience depends on frequent snapshot substitution, the real defect is in `state_sync` or `yjs_pressure`, not in the lack of more snapshots.

## Roadmap

### Phase 1 - Contract freeze and mapping

Define the three canonical contracts and map current signals into them without changing transport behavior yet.

Work items:

- freeze contract names and scopes:
  - `connectivity`
  - `state_sync`
  - `yjs_pressure`
- map current `required_upstream_link` and browser control-route diagnostics into `connectivity`
- map current webspace sync/replay/recovery diagnostics into `state_sync`
- map current Yjs load-mark owner alerts into `yjs_pressure`

Success criteria:

- operator can tell whether a problem is transport, semantic sync, or write pressure
- no new UI-only truth objects are introduced

### Phase 2 - Canonical state-sync status for webspaces

Make `state_sync` a first-class kernel contract for browser-facing webspaces.

Work items:

- add canonical `WebspaceSyncStatus` production in core
- distinguish:
  - transport attached
  - first sync complete
  - semantic sync healthy
  - freshness state
- make browser/runtime surfaces read that one contract instead of reconstructing sync health ad hoc
- treat legacy aggregate fields such as `hub_root_browser` as derived compatibility views

Success criteria:

- browser can say "control path ready but semantic sync stale" truthfully
- stale materialization no longer presents itself as generic readiness

### Phase 3 - Yjs pressure governance

Turn `yjs_pressure` from warning-only telemetry into enforced kernel policy.

Work items:

- define owner budgets for the primary shared desktop doc
- implement `warn`, `throttle`, and `block`
- surface blocked or throttled owners in reliability and Infra State
- make pressure policy visible enough that skill authors are forced to redesign abusive write patterns

Success criteria:

- aggressive skill writes cannot silently degrade the primary shared document
- block/throttle decisions are operator-visible, not hidden

### Phase 4 - Skill migration away from broad branch rewrites

Move operational skills toward safer materialization patterns.

Priority targets:

- `infrastate`
- `infrascope`
- other operational or diagnostics-heavy skills that currently rewrite wide desktop branches

Work items:

- shrink primary shared-doc writes to summary-level state where possible
- move heavy detail payloads to on-demand or separately governed projections
- keep snapshots as explicit recovery tools rather than normal refresh loops

Success criteria:

- reconnect/recovery does not trigger large repeated desktop rewrites
- operational skills no longer dominate `yjs_pressure` in healthy steady state

### Phase 5 - UI adoption and legacy cleanup

Make browser UI consume the canonical contracts directly and retire misleading aggregates.

Work items:

- keep the current compact status line shape if it remains useful
- make drill-down open from one canonical observability source
- remove browser heuristics that infer semantic health from connectivity alone
- retire obsolete compatibility aggregates once all main surfaces use canonical status

Success criteria:

- user-facing surfaces remain compact
- deeper detail is still available by click
- kernel and UI tell the same story

## Non-goals

This architecture does not require:

- replacing Yjs as the primary collaborative state plane
- moving all operational data out of shared webspaces
- giving every surface its own persistent status entity
- introducing a second steady-state snapshot transport

The goal is a smaller and truer kernel model, not a larger one.
