# Realtime Rebuild Lag Hardening

Status: implemented stabilization checkpoint for rebuild storms, stream snapshot
storms, and event-loop lag diagnostics.

## Problem

Browser reconnects, scenario rebuilds, and skill stream subscriptions can create
bursts of duplicate work:

- many `webio.stream.snapshot.requested` events after a rebuild
- repeated live-room refreshes while the user quickly switches scenarios
- repeated workflow synchronization while the active scenario is still changing
- YStore backup writes that look like unexplained churn without reason counters
- event-loop lag dumps that show transport tasks but not the active slow handler

The runtime should admit one useful unit of work per target and coalesce the rest.
The user-facing route should stay responsive while recovery and refresh work catches
up in the background.

## Implemented Guards

### Stream Snapshot Demand

`webio.stream.snapshot.requested` is now routed through a shared demand coalescer
before fan-out from the browser-facing WebRTC/Yjs gateway paths.

Runtime knobs:

- `ADAOS_WEBIO_SNAPSHOT_DEMAND_DEBOUNCE_MS`
- `ADAOS_WEBIO_SNAPSHOT_DEMAND_COOLDOWN_MS`
- `ADAOS_WEBIO_SNAPSHOT_DEMAND_MAX_PENDING`
- `ADAOS_WEBIO_SNAPSHOT_DEMAND_MAX_RECENT`

The coalescer keeps repeated requests for the same stream receiver from becoming
parallel skill work. Gateway diagnostics expose the demand snapshot under
`webio_snapshot_demand`.

### Scenario Switch Workflow Sync

`scenario_switch_rebuild` no longer has to run workflow synchronization inline.
The rebuild response records `workflow_sync.deferred=true` and
`timings_ms.workflow_sync_deferred=0.0` when the work is scheduled.

Runtime knobs:

- `ADAOS_WEBSPACE_SCENARIO_SWITCH_DEFER_WORKFLOW_SYNC`
- `ADAOS_WEBSPACE_WORKFLOW_SYNC_DEBOUNCE_S`

`reload`, `reset`, and `restore` keep the synchronous path because those actions
are explicit recovery operations, not quick UI navigation.

### Scenario Switch Live-Room Commit

Ordinary `scenario_switch_rebuild` always resolves a plain payload and applies
it to the existing live room inline. Selector and effective branches share one
Yjs transaction. Skip/defer-refresh switches were removed because they allowed
the control state and live document to converge at different times.

Builder revision projection may still use its dedicated keyed background
refresh because it is a data update inside the Builder scenario, not an
ordinary scenario switch. `restore` remains an explicit room-reset operation;
`reload` and `reset` remain synchronous recovery operations.

Ordinary scenario switching now preserves the existing YStore base and writes
the effective-branch diff. Clearing YStore while preserving the live room had
forced every switch to encode and synchronously persist a full document before
broadcast. Full-state replacement is now reserved for hard reset/restore.

If the target YRoom is cold while a materialized payload is already available,
room bootstrap loads the durable YStore with `seed_if_missing=false` and applies
that payload before exposing the room. It must not project the scenario or emit
`scenarios.synced`: doing so previously launched a duplicate semantic rebuild
inside the original switch. A browser opening a room without an authoritative
payload retains the normal seed-and-materialize bootstrap path. The payload
handoff finishes by persisting `runtime.bootstrap=ready` as a small YStore diff,
so reconnect diagnostics cannot remain stuck at the earlier loading marker.
A normal cold open with no incoming payload first checks the durable ready
marker, selector, materialization scenario, and required top-level keys. A
matching snapshot is reused without decoding large application/catalog trees.

### Materialization Source Ownership

The API runtime builds one stamp-validated skill UI declaration catalog during
startup. Payload-only scenario switches reuse that process-owned catalog and
run resolver CPU work in a worker thread; they do not start a second AdaOS
runtime. Isolated fresh-document workers receive the declarations plus their
fingerprint in the request.

Resolver caching has two levels: a scenario/source/skills/access-policy core
and a per-webspace overlay. Installed state, pinned widgets, ordering/visibility,
and live state are never shared. Scenario topbar/page schema stay core-owned.
This permits two related DEV previews to reuse
the generated scenario core without encoding topology in their ids.

Resolved-cache admission estimates retained memory from compact JSON with a
conservative Python-container multiplier. The previous exact recursive object
walk synchronously traversed roughly 90,000 objects twice on a cold switch and
could cost more than the scenario merge itself.

### Builder Control Admission

DEBUG workspace autosync is limited to executable mutating tools. Read-only
Builder calls (`read_*`, `get_*`, `list_*`) and UI navigation do not acquire the
workspace runtime update lock. Builder context/preview projection writes are
coalesced by source webspace and published in background, so event handlers do
not hold the event bus while a YDoc projection is persisted.

### Conversation Ledger I/O

The conversation ledger is a blocking SQLite boundary. Voice-chat snapshot
recovery and history paging run in worker threads before their compact stream
projection is published on the event loop. A browser attach can therefore wait
for ledger recovery without delaying scenario-switch control, websocket
publishing, or loop-lag monitoring.

Demand coalescing remains useful burst control, but it is not a substitute for
this ownership rule: subscription handlers must not execute ledger queries on
the event-loop thread.

### Supervisor Projection Single-Flight

The reliability summary no longer starts an independent local supervisor HTTP
probe for every concurrent browser/status consumer. Supervisor transition and
required-link state use a short TTL cache with a single refresh owner. If that
refresh times out, a bounded last-known-good value is returned with
`_cache.state=stale` and refresh error evidence. An explicit successful
supervisor response reporting a down link still replaces the cache; stale
fallback covers probe failure, not real down state.

Runtime knobs:

- `ADAOS_SUPERVISOR_SNAPSHOT_CACHE_TTL_SEC`
- `ADAOS_SUPERVISOR_SNAPSHOT_STALE_MAX_SEC`

### Core Update Status Fanout

`core.update.status` and `hub.core_update.status` are state reports, so queued
obsolete revisions do not need event semantics. The local eventbus now bounds
these topics and supersedes stale queued work per handler and node while
preserving the latest revision for every subscriber. Synchronous SDK skill
handlers are not moved wholesale because several schedule owner-loop Yjs work;
blocking sub-operations must be offloaded inside the owning handler.

### Teacher Startup Rehydration

NLU Teacher startup rehydration keeps live YDoc access on its async owner but
moves persisted-state I/O, plain-data merge/compaction, large equality checks,
ledger reconciliation, and persisted writes to worker threads. A `sys.ready`
handler can therefore reconcile Teacher state without monopolizing the runtime
event loop.

### Skill Activation Admission

SDK subscription wrappers now load the skill activation policy and evaluate it
before invoking user handlers. Lazy and on-demand skills can remain registered,
but their handlers are skipped cheaply when the current event does not satisfy
the policy.

This is the first production-safe layer for skills such as `new_face_vision_skill`:
mark the skill lazy, disable startup background refresh, and admit it only when
its scenario is active.

Example:

```yaml
runtime:
  activation:
    mode: lazy
    startup_allowed: false
    background_refresh: false
    when:
      scenarios_active:
        - new_face_vision_scenario
      client_presence: true
      webspace_scope: active
```

### Event-Loop Lag Diagnostics

Loop-lag dumps now include active bounded eventbus handlers in addition to the
raw asyncio task list. This makes a lag event actionable when the visible tasks
are mostly NATS, aiortc, uvicorn, or websocket transport waits.

The useful signal is the active eventbus line:

- handler name
- skill name when known
- topic and event type
- duration so far

Loop-lag dumps also include the most recent live-room command and named-entity
projection phases: webspace, source/outcome, owner-loop handoff, queue/apply
time, payload/update bytes, and projection stage timings. The same counters are
available in Yjs reliability and `diag360` snapshots.

The runtime status path follows the same ownership boundary as skill state.
Blocking process/socket inspection, filesystem and SQLite status assembly, and
large reliability JSON encoding run in worker threads. Member heartbeat
repository reads and display-name lookup are also offloaded before the compact
plain-data projection returns to the event loop; Yjs access remains on its
owner thread. Runtime diagnostics consume only a fresh supervisor-owned sidecar
contract, so a separately owned listener is not mistaken for a missing local
socket and stale persisted ownership cannot mask a failed process.

Legacy HTTP member registration follows this boundary too. Registration,
heartbeat, and directory queries perform SQLite/capacity work in worker threads.
Repeated registration by an already-online member refreshes its durable record
but does not publish another `net.subnet.node.up`; that event is reserved for an
unknown/offline-to-online edge. This keeps a recovering member from converting
its retry cadence into eventbus rebuild pressure on the Hub.

### Named-Entity Projection Convergence

The named-entity registry no longer treats every registry event as an
instruction to load, mutate, encode, and persist an independent Yjs document.
It now uses:

- a canonical versioned snapshot per webspace
- a level-triggered desired/applied revision reconciler
- pending-until-room-ready behavior instead of detached YStore replay
- keyed `registry.namedEntitiesV2.entities[canonical_ref]` materialization
- per-source registry invalidation with pre-load entity fingerprint admission
- live-room-generation admission and `changed_refs` patches
- an awaitable owner-loop mutation command with state-vector-bounded Yjs diffs

This distinction matters: coalescing is only burst control. It cannot hide a
missed update because the reconciler converges state revisions rather than
counting event deliveries. Source snapshot construction runs in a worker
thread, and unchanged sources are reused from the canonical snapshot. The
thread-affine live `YDoc` remains on its owner loop. Its state vector is captured
immediately before mutation and supplied to `txn.diff_v1`, so a no-op produces
no replication update; a consecutive revision visits only changed entity refs.

The repeatable local check is:

```text
python tools/named_entity_projection_benchmark.py
```

It validates full/incremental convergence, no-op update suppression,
source-scoped refresh, and a bounded fingerprint burst. Treat its timing output
as synthetic regression evidence and confirm deployment behavior from loop-lag
and reliability diagnostics.

### YStore Backup Reason Counters

YStore diagnostics now separate backup attempts, writes, and skips by kind.

Relevant fields:

- `backup_by_kind`
- `backup_written_by_kind`
- `backup_skipped_by_kind`
- `last_backup_kind`
- `last_backup_skip_reason`
- `last_backup_written_bytes`

Use these counters to distinguish real write churn from harmless
generation-current skips.

## CRDT Checkpoint Direction

YStore replay compaction bounds the replay tail but cannot remove Yjs struct
history already encoded in the base snapshot. Alternating large derived
projections currently adds about 0.8 KiB to that snapshot per switch even when
process memory has reached a stable allocator plateau.

The next storage step is a generation-aware checkpoint contract: create a
fresh canonical server document, advance a document generation, and require
clients to replace the corresponding in-memory/IndexedDB document before they
can publish again. Server-only compaction is incorrect because an old client
can merge the discarded history back. Ordinary switching must not use room
reset as a substitute; reset/restore remain explicit recovery operations.

## Operational Reading

When diagnosing a rebuild lag incident:

1. Check the loop-lag dump for active eventbus handlers.
2. Check `webio_snapshot_demand` for coalesced or suppressed stream requests.
3. Check YStore backup reason counters before assuming disk snapshot churn.
4. Check rebuild timings for `workflow_sync_deferred`, `semantic_rebuild`, and
   inline `live_room_refresh`.
5. Check lazy skill activation policy for heavy skills that should not respond
   outside their active scenario.
6. Correlate `named_entities.projection` with `yjs.live_room_command`; a large
   snapshot-build value points to source aggregation, queue time points to
   owner-loop contention, and apply time/update bytes point to Yjs mutation.
   Source breakdown keys such as `source.devices`, `source.lookups`,
   `registry.collect_sources`, and `registry.payload` identify whether the
   cost is device inventory, manifest lookup aggregation, payload hashing, or
   the Yjs owner mutation. `deferred_room_not_ready` is expected during cold
   room attach and means payload construction was skipped until `room_ready`.
7. Check `last_snapshot_mode`, `projection_patch_mode`, room generation, and
   `fingerprint_skip_total` before attributing repeated events to coalescing.
8. For cold reconnect lag, separate startup event handlers from scenario
   switch cost. `browsers_skill`, diagnostics, notebook, voice/chat, and other
   stream snapshot handlers may run during browser attach; they should schedule
   projection work and avoid synchronous snapshot construction or conversation
   ledger I/O in subscription handlers.
9. If `scenario_projection_sync` is dominated by `ystore_apply_updates`, inspect
   `replay_window_entries`, `replay_window_bytes`, `last_auto_backup_reason`,
   and `auto_backup_inflight`. Replay pressure detected during an auto-backup
   cooldown should leave a delayed backup scheduled; otherwise the next cold
   room open can pay the replay cost before compaction has a chance to run.
