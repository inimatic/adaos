# Infrastate Data Route Plan

Snapshot date: 2026-05-19.

This plan is the STATUS-004 entry point for converting `infrastate_skill` to
the shared YJS|Stream data-route plane. It records the intended route before
changing runtime behavior.

## Route Boundary

- Yjs remains the compact bootstrap/control surface for the widget, modal
  skeleton, selected node, small actions, and degraded/quarantine badges.
- Stream receivers carry current operator variables, active rows, inventory
  tables, realtime diagnostics, marketplace rows, event/log tails, and detail
  snapshots.
- Status cards summarize the same surfaces and point to Yjs/stream/details
  routes; they do not carry rows or diagnostics.
- Tool/details routes are explicit drill-down paths and debug surfaces, not
  background polling fallbacks.

## Current Surface Map

| Surface | Current source | Target route | Budget target | Notes |
| --- | --- | --- | --- | --- |
| `infrastate_widget` | `data/infrastate/summary` | Yjs `infrastate.summary` | <= 4 KiB, <= 0.5 Hz | First paint control state. |
| `infrastate-summary` | `data/infrastate/summary` | Yjs `infrastate.summary` | <= 4 KiB, <= 0.5 Hz | Same compact card as widget. |
| `infrastate-nodes` | `data/infrastate/nodes` | Yjs `infrastate.nodes` | <= 8 KiB, <= 0.2 Hz | Small node tabs/control. |
| `infrastate-node-editor` | `data/infrastate/node_editor` | Yjs `infrastate.node_editor` | <= 2 KiB, on change | Control form state. |
| Core/update actions | `data/infrastate/*_actions` | Yjs action slots | <= 4 KiB each | Stable commands and disabled reasons. |
| `infrastate-build` | `infrastate.build` | Stream replace | <= 8 KiB, snapshot-on-subscribe | Build metadata rows. |
| `infrastate-steps` | `infrastate.steps` | Stream replace | <= 16 KiB, snapshot-on-subscribe | Pipeline rows. |
| `infrastate-realtime` | `infrastate.realtime` | Stream replace + detail stream | <= 16 KiB | Realtime summary rows; detail receiver per row. |
| `infrastate-slots` | `infrastate.slots` | Stream replace | <= 16 KiB | Slot manifest rows. |
| `infrastate-operations` | `infrastate.operations.active` | Stream replace + detail stream | <= 16 KiB, <= 2 Hz | Active operations should never require broad Yjs. |
| Installed skills | `infrastate.skills` | Stream replace | <= 64 KiB, snapshot-on-subscribe | Direct inventory builder; guard-visible first-paint. |
| Installed scenarios | `infrastate.scenarios` | Stream replace | <= 64 KiB, snapshot-on-subscribe | Direct inventory builder; guard-visible first-paint. |
| Marketplace skills/scenarios | `infrastate.marketplace.*` | Stream replace | <= 64 KiB, on demand | Marketplace rows stay out of Yjs. |
| Recent logs | `infrastate.logs.recent` | Stream replace + detail stream | <= 32 KiB | Bounded current log cards; raw log remains external. |
| Event history | `infrastate.events.recent` | Stream replace + detail stream | <= 32 KiB | Bounded recent events; raw evidence stays in logs/eventbus. |
| Yjs load mark | `infrastate.yjs.load_mark` | Stream replace | <= 24 KiB, throttled | Diagnostic table only. |
| Core update diagnostics | `infrastate.core_update_diagnostics` | Stream replace + detail stream | <= 64 KiB | Bounded diagnostic cards. |
| Full snapshot/debug | `get_snapshot(project=false)` | Tool/details | on demand only | Read-only by default; no background fallback. |

## Migration Checklist

- [x] Declare the route plan in architecture docs.
- [x] Add manifest `data_routes` for the current browser-facing surfaces.
- [x] Add stream receiver budget/guard/route metadata in `webui.json`.
- [x] Preserve pressure semantics while validating: Yjs `block` stops
  projection, Yjs `throttle` stretches the projection interval, and stream
  snapshots continue through stream guard.
- [ ] Publish shared status cards for runtime, realtime/route, Yjs, operations,
  core update, marketplace, and skill/scenario registry.
- [ ] Remove `infrastate.operations.active` from Yjs projection after the stream
  path has status-card coverage and resubscribe tests.
- [ ] Split remaining stream builders so snapshot requests for one receiver do
  not rebuild unrelated sections.
- [ ] Add regression tests for unchanged snapshot/card dedupe, stream
  resubscribe recovery, and guard-visible suppression/quarantine.
- [ ] Run a focused two-browser `.30` soak and record Yjs owner pressure, stream
  guard pressure, eventbus stream-control counters, and status-card
  compact-boundary diagnostics.

## Human Verification

1. Run `adaos skill validate infrastate_skill`.
2. Open `[homepoint] Infrastructure State`.
3. Confirm `Installed skills` and `Installed scenarios` first render from their
   `initialState` and then fill through `infrastate.skills` /
   `infrastate.scenarios` stream snapshots.
4. Request `GET /api/node/reliability/summary?mode=thin&webspace_id=desktop`
   and verify `statusPlane.diagnostics.oversizedCardTotal == 0`.
5. Under Yjs quarantine, confirm stream-control counters move while Yjs writes
   remain guarded and stream payloads remain governed by the stream guard.
