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

## Target skill write architecture

Skills, especially LLM-authored skills, should not treat the primary shared Yjs
document as a free-form database. The target architecture is:

- `ProjectionService` is the only normal skill-facing write ingress for
  browser-visible primary shared state.
- SDK helpers may stay ergonomic, but primary-doc writes from those helpers
  should route through `ProjectionService` or another governed projection
  facade.
- Direct Yjs access from skills is a legacy or explicitly-capability-gated path,
  not the default.
- Core/runtime internals may use direct Yjs primitives, but only through
  explicitly marked internal paths with ownership metadata.
- Details, diagnostics, logs, and large operational payloads should use
  section endpoints, streams, or `360log` snapshots rather than broad primary
  Yjs rewrites.

The goal is not to make skills weaker. The goal is to make browser-visible state
safe by default:

- one schema and budget boundary
- one place for compaction and generation ids
- one place for `warn` / `throttle` / `block`
- one operator-visible trail for abusive writes

### Direct Yjs policy target

The eventual default should be deny-by-default for skill-owned direct writes to
the primary shared document, with narrow capability exceptions:

```yaml
runtime:
  yjs:
    primary_doc:
      direct_write: false
      projections:
        - id: weather.current
          path: data/weather/current
          max_bytes: 8192
          mode: replace
    details:
      stream: true
      http: true
```

Temporary legacy skills may declare an explicit migration state:

```yaml
runtime:
  yjs:
    primary_doc:
      direct_write: legacy_warn
      allowed_paths:
        - data/weather
```

Path awareness matters. The policy should distinguish safe narrow writes from
unsafe broad rewrites:

- allowed target shape: `data/weather/current`
- risky target shape: replacing `data` or `ui` as a whole branch
- skill-private runtime data should prefer `runtime/skills/<skill_id>/...` or
  skill-local storage, not primary desktop branches
- heavy details should not live in primary Yjs unless explicitly compacted and
  budgeted

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

Current implementation progress:

- [x] Load-mark telemetry computes owner/root pressure and maps it to `warn`, `throttle`, and `block`.
- [x] Reliability and Infra State expose compact `yjs_pressure` plus blocked/throttled counters.
- [x] Kernel write-boundary guard exists for `get_ydoc`, `async_get_ydoc`, `mutate_live_room`, and direct `YStore.write_update`.
- [x] Guard decisions preserve evidence: owner, roots, source, channel, path, update size, policy state, reason, and counters.
- [x] `ProjectionService` delegates governance decisions to the shared kernel governor and marks already-governed writes so downstream write paths do not double-throttle.
- [x] SDK Yjs wrappers attach explicit skill ownership metadata for both async and sync usage.
- [ ] Replace remaining skill-local pressure guards with calls into the shared kernel governor where they still carry custom logic.
- [ ] Add correlation/generation ids across snapshot, rebuild, route, and Yjs governance events.
- [ ] Add acceptance coverage for abusive LLM-generated skill write patterns without depending on a specific `infrastate` workaround.

Operational knobs:

- `ADAOS_YJS_PRIMARY_DOC_GOVERNANCE_ENABLE=1` enables kernel enforcement.
- `ADAOS_YJS_PRIMARY_DOC_GOVERNANCE_FAIL_OPEN=1` keeps policy-evaluation failures from blocking core liveness.
- `ADAOS_YJS_PRIMARY_DOC_PRESSURE_THROTTLE_SEC=0.35` controls per-owner/root throttle spacing.
- `ADAOS_YJS_LOAD_MARK_HIGH_BPS`, `ADAOS_YJS_LOAD_MARK_CRITICAL_BPS`, and `ADAOS_YJS_LOAD_MARK_BLOCK_BPS` define byte-pressure thresholds.
- `ADAOS_YJS_LOAD_MARK_HIGH_WPS`, `ADAOS_YJS_LOAD_MARK_CRITICAL_WPS`, and `ADAOS_YJS_LOAD_MARK_BLOCK_WPS` define write-rate thresholds.

Expected operator signals:

- Reliability `yjs_pressure.policy_state` reports the current `ok` / `warn` / `throttle` / `block` state.
- Reliability governance counters report `attempted_total`, `allowed_total`, `throttled_total`, and `blocked_total`.
- Logs include `throttled YJS primary-doc write` or `blocked YJS primary-doc write` with owner, roots, source, channel, path, reason, and update size.

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

### Phase 5 - ProjectionService as the skill write boundary

Make `ProjectionService` the required path for normal skill-owned writes into
browser-visible primary shared state.

Migration stages:

- [ ] Observe direct skill-owned Yjs writes and expose them as
  `direct_yjs_write=true` with owner, source, channel, root, path, and size.
- [ ] Warn on direct skill writes that bypass `ProjectionService`:
  `deprecated_direct_skill_yjs_write`.
- [ ] Apply stricter budgets to direct skill writes than to governed projection
  writes.
- [ ] Block broad direct skill writes to roots such as `data`, `ui`,
  `registry`, and shared desktop branches unless explicitly allowlisted.
- [ ] Add `skill.yaml` capability declarations for direct Yjs exceptions and
  projection targets.
- [ ] Make direct skill-owned primary-doc writes deny-by-default outside
  declared capabilities.
- [ ] Provide migration tooling that reports each skill's direct Yjs usage and
  suggests projection declarations.
- [ ] Update LLM skill-generation prompts/templates so generated skills use
  projections, streams, HTTP details, or skill-local storage instead of direct
  primary Yjs writes.

Success criteria:

- LLM-generated skills cannot accidentally rewrite broad browser-visible Yjs
  branches.
- Direct skill Yjs writes are either rejected or tied to explicit capabilities.
- Operators can see which skills still depend on legacy direct Yjs access.

### Phase 6 - UI adoption and legacy cleanup

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
