# AdaOS Realtime Sidecar

## Goal

Move fragile realtime transport ownership out of the main hub process.
This document is intentionally narrower than the overall reliability model.
It describes an ownership boundary, not the full semantics of delivery, replay, idempotency, or degraded mode.

Read this together with:

- [Channel Semantics](channel-semantics.md)
- [Authority And Degraded Mode](authority-and-degraded-mode.md)
- [Hub-Root Protocol](hub-root-protocol.md)
- [Transport Ownership](transport-ownership.md)
- [AdaOS Supervisor](adaos-supervisor.md)

For the first rollout, `adaos-realtime` owns the remote hub<->root WebSocket and exposes a local `nats://127.0.0.1:<port>` endpoint to the hub. The existing hub NATS bridge stays in place and connects to the local sidecar instead of the remote `wss://.../nats` endpoint when sidecar mode is enabled.

This is intentionally minimal:

- no route/Yjs/WebRTC rewrite yet
- no protocol change between hub bridge and root
- no hub business-logic move
- only transport ownership moves out of the main process

## Status Labels

Checklist items use the same four-level MoSCoW-style priority vocabulary as
[Builder Roadmap](builder-roadmap.md):

- `[must]`: required for the sidecar contract or current cutover gate.
- `[should]`: hardening or operator-confidence work.
- `[could]`: optional diagnostics or ergonomics.
- `[deferred]`: intentionally postponed until a later phase owns the contract.

## Current Contract

`adaos-realtime`:

- accepts one local NATS TCP client
- opens one remote NATS-over-WebSocket session to root
- relays raw NATS bytes in both directions
- writes periodic diagnostics to `.adaos/diagnostics/realtime_sidecar.jsonl`
- exposes a runtime status surface in protocol terms:
  - transport readiness
  - control readiness
  - reconnect, quarantine, and supersede counters
  - transport provenance and ownership boundary
  - current scope, lifecycle manager, and next planned boundaries
- can be inspected and restarted independently through the local control API / CLI without restarting the hub process

Supervisor / runtime boundary:

- in managed topology, `adaos-supervisor` starts, restarts, and observes the sidecar
- standalone runtime keeps a temporary fallback and may still start the sidecar itself when supervisor is absent
- hub runtime connects its existing NATS client to local `nats://127.0.0.1:7422`
- hub runtime does not install the internal WebSocket NATS transport patch while sidecar mode is enabled
- browser `/ws` and `/yws` transport can be proxied through sidecar local
  websocket listeners for the current transport-only scope when sidecar mode is
  enabled and listeners are ready
- browser `/ws` and `/yws` session semantics still terminate in the runtime;
  full Yjs room/session authority is not part of the current sidecar contract

## Why this split

The WS failures observed on Windows are transport-loop failures, not hub domain-logic failures. Keeping WS ownership in a dedicated process gives:

- isolated event loop and socket lifecycle
- smaller failure surface
- simpler diagnostics
- a direct path to moving WebRTC and Yjs data-plane later

What this split does not solve by itself:

- durable outbox/inbox
- replay cursor semantics
- idempotent command handling
- authority boundaries
- degraded-mode policy
- local update supervision and restart-state authority

Those remain protocol and system responsibilities.

In the target local architecture, those process/update responsibilities belong to `adaos-supervisor`, not to `adaos-realtime`.

## Live Media Continuity Target

There is one especially important target scenario for the later phases.

- a member is currently producing live media over WebRTC
- the member update must be deferred while that member remains the live media producer
- the hub may still need to restart or switch runtime slots
- hub-side continuity should therefore depend on an independent sidecar path, not on the main hub runtime staying up

The intended future behavior is:

- `member` update policy: defer while member-owned live media is active
- `hub` update policy: allow runtime restart only if the hub-side realtime sidecar stays alive and can continue serving the browser/hub proxy or signaling continuity path
- sidecar continuity must be visible in diagnostics before the orchestration logic is allowed to rely on it

Current status:

- this is a target contract, not a completed capability
- reliability/runtime diagnostics now expose this as planned continuity behavior rather than silently assuming restart safety
- `adaos-supervisor` now also consumes that continuity guard and conservatively defers unsafe update/start paths instead of pretending hub restart continuity already exists
- the current sidecar owns only transport boundaries and does not yet preserve
  live WebRTC continuity during hub runtime restart

## Rollout

### Phase 1 - NATS transport sidecar

Implemented for the managed hub role. Hub runtimes now enable sidecar as the
default realtime transport unless explicitly opted out with
`ADAOS_REALTIME_ENABLE=0` or `HUB_REALTIME_ENABLE=0`.

2026-06-11 target-stand status (`adaost1` / `91.98.89.76`): active slot
`A | 0.1.235+1.fce3706`, sidecar process owns `7422`, `7423`, and `7424`;
runtime is behind supervisor on `8777`. Reliability and supervisor status both
report authoritative hub enablement as `role_default`, and both `/ws` and
`/yws` route contracts report `current_owner=sidecar` with
`handoff_ready=true`.

- [x] `[must]` Add `adaos realtime serve`.
- [x] `[must]` Add local TCP NATS relay.
- [x] `[must]` Route hub NATS bridge through sidecar when sidecar mode is on.
- [x] `[must]` Disable direct hub WS transport when sidecar mode is on.
- [x] `[must]` Expose sidecar runtime state in
  `GET /api/node/reliability`, CLI, and Infra State.
- [x] `[must]` Reconcile default enablement across code, tests, deployment
  config, and docs before calling sidecar the accepted default hub transport.
  The managed autostart wrapper no longer persists truthy hub sidecar flags as
  exports; `=0` opt-outs remain explicit.
- [x] `[must]` Make sidecar launch independent from unrelated CLI imports and
  root-checkout drift; the 2026-06-07 `adaost1` test showed supervisor-owned
  sidecar startup can fail when `/root/adaos` lacks a module already present in
  the active slot. The supervisor now launches
  `python -m adaos.services.realtime_sidecar`, so sidecar boot no longer needs
  the full `adaos` CLI import graph.

Success criteria:

- [x] `[must]` Target stand shows hub NATS transport selected through the
  sidecar: `selected_server=nats://127.0.0.1:7422`, sidecar listener pid owns
  `7422`, and hub enablement is reported as `role_default`.
- [ ] `[must]` Root sees one stable hub WS-NATS session through the sidecar.
  Current blocker: the sidecar NATS relay is still a byte relay bound to the
  local hub NATS client lifetime. When the runtime NATS client is replaced
  during restart, the sidecar opens a new remote session without quarantine,
  but the root still observes session churn. Closing this requires a
  protocol-aware relay or sidecar-owned hub-root NATS session authority, not
  only listener routing.
- [x] `[must]` Hub-root sidecar NATS avoids `UnexpectedEOF`, remote
  quarantine, and connect-failure churn during the target-stand acceptance
  window.
- [x] `[must]` No `nats keepalive pong missing` caused by hub-local WS stalls
  during the target-stand acceptance window.
- [x] `[must]` Operators can see that sidecar owns transport only and can
  inspect `transport_ready`, `control_ready`, reconnect counters, and selected
  remote provenance.
- [x] `[must]` Operators can restart sidecar transport runtime independently
  from hub business runtime.

### Phase 2 - Route tunnel ownership

Implemented for local transport proxy mechanics; acceptance and rollout remain
open.

- [x] `[must]` Move `/ws` and `/yws` tunnel transport into sidecar local
  websocket proxy listeners for the current transport-only scope.
- [x] `[must]` Keep local sidecar-to-runtime forwarding narrow and explicit:
  sidecar proxies websocket frames to the runtime upstream and does not absorb
  HTTP/API orchestration.
- [x] `[must]` Leave HTTP/API orchestration in hub main process.
- [x] `[must]` Expose `current_owner`, `planned_owner`, `handoff_ready`, and
  blockers for each websocket transport in diagnostics.
- [x] `[must]` Capture target-stand evidence that the sidecar route listeners
  are active and report `current_owner=sidecar` plus `handoff_ready=true`.
  2026-06-11 `91.98.89.76`: supervisor and reliability snapshots agree for
  both `/ws` and `/yws`, and `ss` shows sidecar pid owning `7423` and `7424`.
- [x] `[must]` Prove an already-open `/ws` and `/yws` session remains usable
  across runtime restart with sidecar enabled. 2026-06-11 synthetic
  browser-equivalent clients held both sidecar websocket connections open for
  45 ping/pong cycles while `POST /api/supervisor/runtime/restart` replaced
  the runtime process.
- [ ] `[must]` Prove the same already-open `/ws` and `/yws` survival through a
  full A/B slot promotion with real root-routed browser ingress, not only a
  local sidecar-listener restart test.
- [x] `[must]` Keep the sidecar browser-facing websocket open while the runtime
  upstream reconnects, replaying bounded browser-to-runtime setup messages
  after reconnect.
- [x] `[must]` Re-discover the active supervisor runtime URL on route-proxy
  reconnect so A/B slot ports do not pin sidecar to the old runtime.
- [x] `[must]` Cover both `/ws` and `/yws/{room}` route-proxy continuity in
  tests when the runtime upstream disappears or moves to another slot port.
- [x] `[must]` Preserve browser-compatible `/yws/{room}` path routing through
  the sidecar proxy, not only `/yws?ws=<room>`.
- [x] `[must]` Keep sidecar status/control APIs responsive during runtime
  event-loop lag; the 2026-06-07 `SIGSTOP` test timed out
  `/api/supervisor/sidecar/status` while the runtime was frozen. The supervisor
  status/restart surface now builds the sidecar runtime block from local
  process snapshots and sidecar diagnostics instead of querying runtime
  reliability.
- [x] `[should]` Clear stale blocker strings from ready route tunnel
  diagnostics when `listener_ready=true` and `handoff_ready=true`.
- [ ] `[should]` Add soak coverage for sidecar listener restart, runtime event
  loop lag, root reconnect, and fallback path behavior.
- [ ] `[should]` Treat normal websocket `Close(1000)` relay shutdown as an
  expected session close in diagnostics instead of emitting traceback noise;
  keep exceptional close codes visible as errors.

Success criteria:

- [x] `[must]` Browser realtime traffic no longer depends on the hub
  main-process socket loop for the accepted local sidecar-listener
  transport-only path.
- [x] `[must]` Already-open `/ws` and `/yws` sidecar connections survive a
  supervisor runtime restart without closing only because the runtime upstream
  disappeared.
- [ ] `[must]` Already-open `/ws` and `/yws` sidecar connections survive a full
  A/B promotion with real root-routed browser ingress.
- [x] `[must]` Route-proxy failures do not tear down control-plane logic.
- [ ] `[should]` Operators can distinguish accepted sidecar path, runtime
  fallback path, and root relay path in one reliability snapshot.

### Phase 3 - Full realtime runtime

Later.

- [ ] `[deferred]` Move WebRTC signaling/media control into sidecar.
- [ ] `[deferred]` Move Yjs session ownership into sidecar.
- [ ] `[deferred]` Keep hub core focused on orchestration, skills, API, and
  state transitions while sidecar owns all long-lived realtime session runtime.
- [ ] `[deferred]` Make live media continuity explicit during updates:
  defer member updates while member-owned live media is active, preserve
  hub-side sidecar continuity while hub runtime restarts, and keep that
  continuity observable through reliability, CLI, and supervisor surfaces.

Success criteria:

- [ ] `[deferred]` All long-lived realtime sockets are owned by one dedicated
  runtime.
- [ ] `[deferred]` Hub restart and realtime restart can be reasoned about
  independently.
- [ ] `[deferred]` A hub runtime restart does not implicitly terminate the live
  media continuity path that has already been delegated to sidecar ownership.

## Deferred Design Block: Sidecar-Owned Yjs Session Runtime

This is a separate future block.
It is intentionally not part of the current `Phase 2 - Route tunnel ownership`
closeout and should not be mixed into Event Model `Phase 0` completion criteria.

Why this needs its own block:

- moving only browser `"/yws"` socket ingress into sidecar is not enough to make Yjs survive slot switch
- current live Yjs room lifecycle, in-process `YRoom` ownership, direct live-room mutation paths, and room reset/reload orchestration still live in the runtime process
- as long as those room/session responsibilities stay runtime-owned, a runtime slot switch can still tear down the live Yjs continuity path even if the public `"/yws"` transport ingress has already moved

Target for the later block:

- sidecar owns Yjs websocket termination and live room/session lifecycle
- sidecar owns room reset/reload/idle-eviction orchestration for the browser-facing Yjs runtime
- runtime/core interacts with Yjs through a narrow explicit gateway instead of reaching into in-process `y_server.rooms`
- diagnostics distinguish transport ownership, session ownership, and persistence ownership instead of collapsing them into one `yws` bit

Preparatory work that is allowed before that block starts:

- reduce direct runtime dependencies on in-process live-room globals
- introduce a shared Yjs runtime gateway abstraction that can later point to runtime-local or sidecar-owned session authority
- keep `YStore`/persistence semantics explicit so session ownership can move without smuggling hub business logic into sidecar
- continue the current roadmap focus on public `"/yws"` transport cutover and communication prerequisites without claiming full Yjs session continuity yet

What this means for the current roadmap:

- Event Model `Phase 0` still depends on the current `"/yws"` transport ownership cutover track
- full sidecar-owned Yjs session/runtime continuity is a later reliability/runtime block, not a hidden extra acceptance criterion for the current phase

## Operational Notes

- Hub runtimes use sidecar as the default realtime transport.
- `ADAOS_REALTIME_ENABLE=0` or `HUB_REALTIME_ENABLE=0` explicitly opts out and
  keeps direct runtime-owned hub-root transport.
- Non-hub roles still stay `sidecar off` by default unless enabled explicitly.
- Local endpoint defaults to `nats://127.0.0.1:7422`.
- Remote candidate selection still uses existing node/root NATS configuration.
- Managed process topology prefers `systemd -> adaos-supervisor -> {adaos-runtime, adaos-realtime}`.
- Standalone runtime-owned sidecar lifecycle remains transitional compatibility only and is not the target long-term architecture.
