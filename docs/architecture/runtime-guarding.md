# Runtime Guarding

Status: target architecture and roadmap.

This document defines the target shape for AdaOS runtime guarding: memory, CPU,
Yjs pressure, HTTP health, skill execution pressure, and supervisor process
health should be evaluated through one small contract instead of growing as
independent guard islands.

The goal is not to hide overload. The goal is to keep the node alive while
preserving enough evidence to identify and fix the component that caused the
overload.

## Problem statement

AdaOS skills can evolve quickly, including code produced or edited by LLMs.
The kernel must therefore tolerate expensive, buggy, or write-amplifying skills
without letting one skill make the browser, Yjs runtime, or hub process
unusable.

Recent Yjs pressure incidents showed that a single long-running tool call is
not the only dangerous shape. A skill can also overload the runtime through many
short expensive calls, event subscriptions, stream handlers, projections, or
repeated snapshot materialization. Guarding must cover both:

- `single_call_overload`: one tool, subscription, or projection handler runs too
  long or consumes too much resource.
- `aggregate_window_overload`: many bounded operations from the same owner
  exceed a CPU, memory, Yjs, or latency budget over a time window.

## Target model

All guard sources should publish normalized signals into a shared arbiter:

```text
GuardSignal -> GuardArbiter -> GuardAction
```

The guard sources remain specialized, but the decision vocabulary is shared.

```text
MemoryGuard
CpuGuard
YjsPressureGuard
InboundYwsUpdateGuard
BrowserStreamFanoutGuard
HttpHealthGuard
SkillExecutionGuard
SupervisorProcessGuard
InteractiveRouteBudgetGuard
```

The shared state machine is:

```text
ok -> observe -> warn -> throttle -> quarantine -> restart_candidate
```

`restart_candidate` is intentionally not the same as immediate restart. It means
the runtime has crossed a critical safety boundary and the supervisor may need
to act after cooldown, diagnostics, and continuity checks.

## Guard signal contract

Runtime and supervisor guards should converge on a compact signal shape:

```python
@dataclass
class GuardSignal:
    source: str
    scope: str
    owner: str | None
    severity: str
    reason: str
    confidence: float
    metrics: dict
    ts: float
```

Required fields:

- `source`: guard source, for example `memory`, `cpu`, `yjs_pressure`,
  `http_health`, `skill_execution`, or `supervisor_process`.
- `scope`: resource scope, for example `runtime_process`, `webspace:desktop`,
  `skill:infrastate_skill`, or `node:hub`.
- `owner`: best-known owner to blame or protect; can be absent when attribution
  is not reliable.
- `severity`: normalized state such as `observe`, `warn`, `throttle`,
  `quarantine`, or `restart_candidate`.
- `reason`: stable reason code, not only free-form text.
- `confidence`: attribution confidence from `0.0` to `1.0`.
- `metrics`: compact evidence used by the decision.
- `ts`: event timestamp.

The action shape should also be compact:

```python
@dataclass
class GuardAction:
    kind: str
    target: str | None
    reason: str
    ttl_s: int | None
    confidence: float
```

Expected actions:

- `log`: preserve evidence without changing runtime behavior.
- `sample`: collect a bounded diagnostic sample.
- `throttle`: reduce frequency, coalesce repeated work, or reject noncritical
  work.
- `quarantine`: deny a skill owner for a TTL while preserving visible state.
- `snapshot`: persist a bounded diagnostic snapshot to disk.
- `restart_candidate`: ask the supervisor to evaluate a hard recovery path.

## Interactive route budget guard

Browser-facing interactive routes must be protected even when a client, skill,
or operator asks for a synchronous expensive action. The runtime should treat
route responsiveness as a core health invariant, not as a best-effort client
convention.

Examples:

- switching a Yjs webspace scenario
- returning a webspace home
- rebuilding or materializing desktop compatibility caches
- refreshing heavy snapshots for UI details
- any action that can trigger large Yjs reads, writes, or projection fan-out

Default behavior:

- interactive routes acknowledge accepted work quickly
- expensive rebuild/materialization runs in the background
- explicit `wait_for_*` requests are bounded by a small server-side budget or
  coerced to background mode when they would exceed that budget
- the response includes a compact guard record when the runtime overrides an
  unsafe request
- a separate admin/debug endpoint may expose synchronous waits with an explicit
  timeout budget

Recommended response shape:

```json
{
  "ok": true,
  "accepted": true,
  "background_rebuild": true,
  "guards": {
    "wait_for_rebuild": {
      "requested": true,
      "effective": false,
      "reason": "scenario_switch_rebuild_runs_in_background_to_protect_route_budget"
    }
  }
}
```

This guard is intentionally not a fallback that hides failure. If the background
operation later fails, the rebuild/materialization state must expose that
failure through reliability, 360log, and Web UI details. The route budget guard
only prevents an interactive request from monopolizing the public route long
enough to cause browser `502/504` failures or route starvation.

## Yjs transport pressure guards

Yjs pressure has two different overload shapes and both need guards:

- `skill_or_projection_write_pressure`: a skill, SDK stream, projection, or
  materializer writes too much into the shared document.
- `transport_replay_pressure`: a browser or gateway provider replays an
  oversized update after reconnect and repeatedly forces the runtime to process
  the same large CRDT payload.

The target split is:

- `YjsPressureGuard` attributes durable write pressure to an owner and can
  throttle or quarantine skill-owned work.
- `BrowserStreamFanoutGuard` limits browser-facing SDK stream fanout before it
  becomes repeated Yjs/UI churn.
- `InboundYwsUpdateGuard` protects the runtime from oversized inbound YWS
  updates even when owner attribution is `gateway_ws` or `core`.
- `SyncYDocSessionGuard` caps and times out synchronous `get_ydoc()` sessions
  so skill-owned worker threads cannot pin Yjs storage sessions indefinitely.

`InboundYwsUpdateGuard` should be observable before it is clever:

- log `webspace_id`, `update_bytes`, `block_bytes`, and reset decision
- expose block counters and last block reason in reliability / snapshot data
- close the affected YWS room with a specific reason, for example
  `inbound_yws_update_payload_blocked`
- avoid persisting the rejected update into the room store
- keep the default block threshold at a truly critical single-update size
  (`ADAOS_YJS_ROOM_INBOUND_GUARD_BLOCK_BYTES`, default 4 MiB); lower values are
  useful for stress tests, but can turn legitimate first-sync/recovery updates
  into a reset loop.

The browser must treat `inbound_yws_update_payload_blocked` as a hard local
document reset signal. A normal provider reconnect can resend the same poisoned
in-memory `Y.Doc`; the safe first implementation is a throttled page reload
after clearing optional IndexedDB persistence. A later implementation may
replace this with an app-level document recreation once every consumer can
resubscribe safely to a new `Y.Doc` instance.

## Responsibility split

### Runtime/core

The runtime is closest to semantic ownership. It should attribute overload to
skills, tools, subscriptions, projections, webspaces, and Yjs owners whenever it
can do so cheaply.

Runtime responsibilities:

- wrap tool execution, skill subscription handlers, projection writers, and
  direct Yjs write boundaries with owner metadata
- track wall time, active calls, call rate, error rate, Yjs bytes/writes, and
  approximate CPU attribution per owner
- detect both single-call and aggregate-window overload
- enforce `throttle` and `quarantine` for noncritical skill owners when
  confidence is high enough
- call optional `onQuarantine` / `on_quarantine` skill hooks with `ttl_s`,
  `reason`, and compact metrics
- persist skill-local quarantine incidents for later LLM-assisted repair
- publish active quarantine state into the service branch used by Web UI, for
  example `data.yjs_qrnt`
- enforce route budgets for browser-facing actions and coerce unsafe waits into
  observable background operations
- expose compact guard summaries in reliability, CLI, and 360log snapshots

### Supervisor

The supervisor is outside the restartable runtime process. It should watch
whole-process and node-level health, but it should not invent skill blame unless
the runtime publishes an attributed signal.

Supervisor responsibilities:

- sample runtime process CPU, RSS, memory slope, restart loops, and availability
  at low frequency
- record process-level overload incidents even when no skill blame is available
- request or collect bounded diagnostics when sustained overload is detected
- respect continuity guards before restart or promotion
- apply restart/rollback only on critical limits, cooldown, and policy checks
- avoid automatic profiling loops that can become their own overload source

## CPU guard design

CPU guarding needs two layers.

### Supervisor CPU guard

The supervisor samples process CPU from outside the runtime. This is reliable
for detecting that the runtime is overloaded, but it usually cannot identify the
skill owner alone.

Default behavior:

- sample every `5-15s`
- record process CPU percent, system load, RSS, and runtime availability
- transition to `warn` only after sustained overload, not a single spike
- request a runtime guard snapshot or sampled profile only after a sustained
  anomaly
- mark restart as a candidate only when CPU overload combines with degraded
  runtime health, event-loop lag, HTTP failure, or memory pressure

### Runtime skill CPU guard

The runtime samples cheaper semantic signals and attributes CPU pressure to the
active skill owner when confidence is high.

Recommended tracked metrics:

- active tool/subscription/projection count per owner
- wall time per active execution
- calls per owner in `1m`, `5m`, and `1h` windows
- approximate process CPU while a single owner dominates active work
- Yjs bytes/writes generated by the owner
- error and timeout rate
- admission denials, throttles, and quarantine TTLs

The CPU guard should quarantine only when confidence is high. If multiple owners
are active and attribution is unclear, it should publish a process-level or
webspace-level warning instead of blaming one skill.

## Cost control

Guarding must not become a material CPU load.

Rules:

- never run continuous `py-spy`, `memray`, or stack profiling in the hot path
- use low-frequency process sampling
- use counters, EWMA, and ring buffers instead of full histories
- avoid deep JSON normalization or large snapshot serialization inside guard
  loops
- emit detailed evidence only on state transitions
- cap diagnostic payload size before writing it to Yjs or logs
- cache and fingerprint heavy UI snapshots before projection; repeated
  `project=true` reads must skip Yjs projection when the cached projection is
  already current
- distinguish normal YRoom bootstrap payloads from route starvation; route
  pending-data guard defaults should tolerate multi-hundred-KB scenario
  materialization bursts while still catching multi-MiB stuck queues
- keep expensive stack/profile collection on-demand and time-bounded
- give guard loops their own budget; if a guard tick exceeds that budget, reduce
  detail rather than doing more work

Suggested default ring buffers:

- `GuardSignal`: last `512` signals per runtime
- owner activity: last `256` owner events per hot owner
- process samples: last `1h` at `10s` cadence
- quarantine incidents: append-only JSONL on disk plus compact active state in
  Yjs

## Evidence preservation

Every guard action must preserve enough evidence to debug the source later.

Minimum evidence set:

- owner and source attribution
- webspace, root path, channel, and operation kind when available
- threshold that was crossed
- observed metrics and window duration
- confidence and reason code
- action taken and TTL/cooldown
- whether the work was dropped, throttled, coalesced, quarantined, or merely
  observed

Guarding must not make a failure look like success. For example, a quarantined
skill should return a structured `skill_owner_quarantined` result, and Web UI
should render a visible disabled state instead of silently hiding the problem.

## Web UI contract

The browser should receive a compact, stable guard view rather than parsing
individual guard implementations.

Target UI branch:

```text
data.guard_state
data.yjs_qrnt
```

`data.guard_state` should be the compact node/webspace guard summary.
`data.yjs_qrnt` remains the active skill-owner quarantine projection for
desktop icons, widgets, and actions.

Web UI behavior:

- disabled icon/widget state for quarantined owners
- visible reason and retry-after when available
- action failure messages include owner, tool, reason, and retry metadata
- details panel can fetch full guard evidence by snapshot or log ID

## 360log and snapshots

Guard state should be first-class in 360log-style diagnostics.

Required snapshot sections:

- process CPU and memory guard state
- active guard signals and recent transitions
- active quarantines
- Yjs pressure by owner/root/channel
- slow tool/subscription/projection owners
- HTTP and event-loop health
- interactive route budget overrides
- recent guard actions
- diagnostic profile references, if collected

Snapshot IDs should be stable enough for later CLI/MCP use:

```text
guard-snap-<timestamp>-<shortid>
```

The snapshot should store compact evidence on disk first. Larger optional
artifacts, such as `memray` or sampled stack files, should be referenced by ID
instead of embedded into Yjs.

## Roadmap

### Phase 1 - Shared contract and passive observability

- [ ] Add `GuardSignal` and `GuardAction` dataclasses or equivalent runtime
  model.
- [ ] Add a `GuardArbiter` service with no hard enforcement by default.
- [ ] Adapt existing memory guard output into the shared signal contract.
- [ ] Adapt existing Yjs pressure guard output into the shared signal contract.
- [x] Add interactive route budget guard records for browser-facing endpoints
  that coerce unsafe synchronous waits into background work.
- [ ] Add `BrowserStreamFanoutGuard` and `InboundYwsUpdateGuard` counters to
  the shared signal contract.
- [ ] Add compact guard summary to reliability and `adaos node reliability`.
- [ ] Add guard sections to 360log/snapshot output.

### Phase 2 - Skill execution attribution

- [ ] Wrap public skill tool execution with owner, tool, wall-time, and outcome
  accounting.
- [ ] Wrap skill subscription/event handlers with the same owner accounting.
- [ ] Wrap projection and Yjs write boundaries with owner accounting.
- [ ] Track aggregate windows for `1m`, `5m`, and `1h`.
- [ ] Distinguish `single_call_overload` from `aggregate_window_overload`.
- [ ] Publish high-cost owners in diagnostics without enforcement first.

### Phase 3 - CPU guard

- [ ] Add supervisor process CPU sampler with low-frequency `/proc` or `psutil`
  sampling.
- [ ] Add runtime approximate owner CPU attribution based on active owner
  windows.
- [ ] Add thresholds for `warn`, `throttle`, and `quarantine` with conservative
  defaults.
- [ ] Add `.env` overrides for debug and stress testing.
- [ ] Ensure CPU guard only collects stack/profile artifacts after sustained
  anomaly.
- [ ] Add CPU overload incidents to disk snapshots and 360log.

### Phase 4 - Partial enforcement

- [ ] Enable `warn` and `throttle` for noncritical owners.
- [x] Enforce background mode for known heavy interactive routes such as
  scenario switch and go-home rebuilds.
- [x] Block oversized inbound YWS payloads before they are persisted or
  rebroadcast.
- [x] Cap and time out synchronous `get_ydoc()` sessions used from skill/helper
  worker threads.
- [x] Trigger browser hard local-document recovery on
  `inbound_yws_update_payload_blocked`.
- [x] Throttle/drop oversized browser stream fanout before it amplifies into
  route or Yjs pressure.
- [x] Skip redundant infrastate snapshot projection when browser/tool polling
  asks for `project=true` but the cached projection fingerprint is already
  current.
- [x] Prewarm the YRoom after scenario-switch room reset so scenario changes do
  not need to reset the broader browser route runtime.
- [x] Raise route pending-data guard defaults so normal YRoom bootstrap payloads
  do not trigger route guardrail pressure.
- [ ] Enable quarantine only for high-confidence noncritical skill owners.
- [ ] Invoke optional `onQuarantine` / `on_quarantine` hook with TTL, reason,
  metrics, blocked operation, webspace, and owner.
- [ ] Persist skill-local quarantine incidents for repair context.
- [ ] Publish active quarantines to `data.yjs_qrnt`.
- [ ] Make Web UI disable quarantined icons/widgets with visible reason.

### Phase 5 - Supervisor hard safety

- [ ] Teach supervisor to consume runtime guard summaries.
- [ ] Gate restart/rollback on critical process guard state plus continuity
  policy.
- [ ] Add cooldown/circuit breaker around repeated guard-triggered profiling or
  restarts.
- [ ] Keep restart as a last-resort action after diagnostics and TTL-based
  containment have failed.
- [ ] Record every hard action as a guard incident with snapshot ID.

### Phase 6 - MCP and operator workflows

- [ ] Expose guard snapshots through CLI by ID.
- [ ] Expose guard snapshots through MCP read tools.
- [ ] Add MCP action to request a bounded guard snapshot.
- [ ] Add operator command to release or extend a quarantine TTL.
- [ ] Add operator command to show top owners by CPU, Yjs bytes, wall time, and
  recent guard actions.

## Default policy direction

Production defaults should be conservative:

- observe and warn early
- throttle only after sustained evidence
- quarantine only high-confidence noncritical owners
- restart only on critical process health, continuity-safe policy, and cooldown
- never hide evidence to make health look green

Debug and stress profiles may lower thresholds through explicit `.env`
overrides, but those overrides must be visible in reliability output.
