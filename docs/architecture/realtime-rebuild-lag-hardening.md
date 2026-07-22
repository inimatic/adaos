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

### Scenario Switch Live-Room Refresh

Detached semantic rebuilds can also defer `refresh_live_webspace_effective_branches`
for `scenario_switch_rebuild`. The scheduler is keyed by webspace and the newest
pending request wins.

Runtime knobs:

- `ADAOS_WEBSPACE_SCENARIO_SWITCH_DEFER_LIVE_ROOM_REFRESH`
- `ADAOS_WEBSPACE_LIVE_ROOM_REFRESH_DEBOUNCE_S`
- `ADAOS_WEBSPACE_REBUILD_REFRESH_LIVE_ROOM`

`restore` still resets the live room synchronously. `reload` and `reset` still
refresh synchronously.

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

## Snapshot Restore Direction

AdaOS already has persisted YStore restore and semantic snapshot restore paths.
The next architectural step is to make scenario back-switches prefer a recent
per-scenario Yjs document dump when it is still compatible, then replay later
changes. That should be treated as a correctness-sensitive follow-up, not as a
replacement for the coalescing guards above.

The current checkpoint deliberately keeps recovery operations synchronous and
only defers quick scenario-switch refresh work.

## Operational Reading

When diagnosing a rebuild lag incident:

1. Check the loop-lag dump for active eventbus handlers.
2. Check `webio_snapshot_demand` for coalesced or suppressed stream requests.
3. Check YStore backup reason counters before assuming disk snapshot churn.
4. Check rebuild timings for `workflow_sync_deferred` and
   `live_room_refresh_deferred`.
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
   projection work and avoid synchronous snapshot construction in subscription
   handlers.
9. If `scenario_projection_sync` is dominated by `ystore_apply_updates`, inspect
   `replay_window_entries`, `replay_window_bytes`, `last_auto_backup_reason`,
   and `auto_backup_inflight`. Replay pressure detected during an auto-backup
   cooldown should leave a delayed backup scheduled; otherwise the next cold
   room open can pay the replay cost before compaction has a chance to run.
