# AdaOS Realtime Sidecar

## Goal

Move fragile realtime transport ownership out of the main hub process.
This document is intentionally narrower than the overall reliability model.
It describes an ownership boundary, not the full semantics of delivery, replay, idempotency, or degraded mode.

Read this together with:

- [Channel Semantics](channel-semantics.md)
- [Hub-Browser Connectivity](hub-browser-connectivity.md)
- [Authority And Degraded Mode](authority-and-degraded-mode.md)
- [Hub-Root Protocol](hub-root-protocol.md)
- [Transport Ownership](transport-ownership.md)
- [Local Supervisor / Autostart](adaos-supervisor.md)

The rollout started with `adaos-realtime` owning the remote hub<->root
WebSocket and exposing a local `nats://127.0.0.1:<port>` endpoint to the hub.
The existing hub NATS bridge stays in place and connects to the local sidecar
instead of the remote `wss://.../nats` endpoint when sidecar mode is enabled.
The current implementation also has transport-only local websocket proxy
listeners for browser `/ws` and `/yws`.

This is intentionally minimal:

- route proxying is transport-only and still forwards session semantics to the
  runtime
- no Yjs room/session authority move yet
- no WebRTC signaling/media authority move yet
- no protocol change between hub bridge and root
- no hub business-logic move
- only transport ownership moves out of the main process

The sidecar never treats a new local client as permission to cancel the old
one. Each relay has independent NATS PING/PONG accounting. Candidate bootstrap
does not consume the overlap slot during prewarm; it opens transport only after
supervisor calls `promote-active`, and the old session closes naturally during
drain after new route authority is confirmed.

Overlap is an explicit supervisor transition, not a reconnect heuristic. The
sidecar resolves the loopback TCP owner and admits a second relay only when the
persisted supervisor state proves a ready warm-switch candidate, the existing
owner is the managed runtime, and PID plus runtime-instance identities match.
An ordinary reconnect from the same PID/runtime instance first aborts and drains
its prior relay, then opens a replacement without overlap. An unresolved or
different owner waits a bounded interval for the prior relay to drain and is
rejected if that relay is still live. Admission decisions are serialized so
simultaneous clients cannot pass against the same stale session count.

Retiring the old runtime is explicitly runtime-scoped. It closes the old
listener and process-local resources without publishing node-wide
`subnet.stopping/subnet.stopped`; the replacement runtime and sidecar continue
to represent the same online subnet throughout the transition.

## Status Labels

Checklist items use the same four-level MoSCoW-style priority vocabulary as
[Builder Roadmap](builder-roadmap.md):

- `[must]`: required for the sidecar contract or current cutover gate.
- `[should]`: hardening or operator-confidence work.
- `[could]`: optional diagnostics or ergonomics.
- `[deferred]`: intentionally postponed until a later phase owns the contract.

## Current Contract

`adaos-realtime`:

- accepts one normal local NATS TCP client and a bounded second client during
  A/B authority handoff (`ADAOS_REALTIME_MAX_LOCAL_SESSIONS`, default `2`)
- opens one remote NATS-over-WebSocket session per admitted local client; the
  two sessions may overlap only until the old runtime drains
- relays raw NATS bytes in both directions
- writes periodic diagnostics to `.adaos/diagnostics/realtime_sidecar.jsonl`
- records local TCP owner PID, runtime instance/transition role, overlap
  allow/reject/drain and same-owner replacement counters, and the oldest
  unresolved runtime NATS PING
- acts as the durable diagnostic bridge to Root by combining its transport
  observation with persisted supervisor/runtime lifecycle snapshots; it sends
  on semantic change plus a bounded heartbeat and never becomes the update or
  runtime-semantics authority
- exposes a runtime status surface in protocol terms:
  - transport readiness
  - control readiness
  - reconnect, quarantine, active-session, and handoff-overlap counters
  - transport provenance and ownership boundary
  - current scope, lifecycle manager, and next planned boundaries
- can be inspected and restarted independently through the local control API / CLI without restarting the hub process
- a managed restart synchronizes validated sidecar source before launch and is
  completed by an explicit active-runtime hub-root reconnect; code sync and
  operator requests are coalesced into one process generation

Managed autostart / runtime boundary:

- in managed topology, the existing autostart-managed control process starts,
  restarts, and observes the sidecar
- standalone runtime keeps a temporary fallback and may still start the sidecar itself when supervisor is absent
- hub runtime connects its existing NATS client to local `nats://127.0.0.1:7422`
- hub runtime does not install the internal WebSocket NATS transport patch while sidecar mode is enabled
- browser `/ws` and `/yws` transport can be proxied through sidecar local
  websocket listeners for the current transport-only scope when sidecar mode is
  enabled and listeners are ready
- endpoint media content can be proxied through an explicitly enabled,
  read-only sidecar HTTP listener for `/api/node/media/files/content/{filename}`,
  `/media/files/content/{filename}`, and the legacy media-indexer playback
  routes backed by the core media resource gateway
- browser `/ws` and `/yws` session semantics still terminate in the runtime;
  full Yjs room/session authority is not part of the current sidecar contract
- WebRTC direct channels are still negotiated by the runtime/browser transport
  layer; a server-side Yjs datachannel opt-out prevents browser sync promotion
  to `webrtc_data:yjs` even when the peer and other datachannels are connected
- Root publishes the resulting browser-facing lifecycle through the contract
  in [Browser-Hub Lifecycle Authority](browser-hub-lifecycle.md); browsers wait
  on that event stream instead of polling sidecar, supervisor, and runtime
- the media proxy does not expose the full hub API to the LAN; it serves only
  token-protected media resources resolved by the core media plane and supports
  `GET`, `HEAD`, and byte `Range` requests

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

In the target local architecture, those process/update responsibilities belong
to the managed autostart control process, not to `adaos-realtime`.

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
- the managed control plane now also consumes that continuity guard and
  conservatively defers unsafe update/start paths instead of pretending hub
  restart continuity already exists
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

2026-06-18 `.30` status (`192.168.0.30`): active slot
`B | 0.1.318+1.7035698`, sidecar process owns `7422`, `7423`, and `7424`;
diagnostics report local `/ws` and `/yws` handoff ready and upstream runtime
discovery through the active slot port. The same stand exposed a config
acceptance gap: stale `/root/adaos/.env` had
`ADAOS_WEBRTC_YJS_CHANNEL_ENABLED=0`, so the server logged
`yjs datachannel disabled ...` and the browser could not promote sync from
`yws` to `webrtc_data:yjs` even though WebRTC reached connected state. After
switching the flag to `1` and restarting autostart, the runtime opened
`events`, `yjs`, and `media` datachannels.

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
- [x] `[must]` Treat an abnormal remote-session close as loss of the current
  byte-relay session: close the matching local NATS socket and let the hub
  runtime recreate `CONNECT` and every `SUB` on a new session. The sidecar must
  not reconnect its remote WebSocket transparently behind an already-open
  local socket because it cannot reconstruct NATS protocol state.
- [x] `[must]` Do not replay an arbitrary tail of route WebSocket frames after
  the local runtime changes. Already-sent control and Yjs frames do not share a
  transport-level idempotency contract, and replay before session bootstrap can
  create an unbounded `hello_required` reconnect loop. `/ws/subnet` may retain
  its downstream socket only by storing the explicit `hello`, completing a new
  `hello/hello.ack` exchange, and then forwarding only frames that were still
  queued. Protocol-opaque `/ws` and `/yws` tunnels close downstream with 1012
  after upstream loss so the owning client performs its normal reconnect and
  state reconciliation. Session resume, handshake failure, forced downstream
  reconnect, uncertain send, and queue-pressure counters are part of the
  sidecar route-tunnel contract.
- [x] `[must]` Never inject sidecar-originated NATS `PING` bytes into the
  transparent relay. A relay read boundary is not a NATS frame boundary, so a
  timer can insert `PING\r\n` inside a fragmented `PUB` payload and invalidate
  its declared size. Runtime-owned bounded NATS protocol roundtrips provide
  end-to-end liveness without an independent writer modifying the relayed byte
  stream. Defaults are one probe every 30 seconds, a five-second response
  timeout, and reconnect after the first failed roundtrip; the timeout path
  removes its PONG waiter so a late PONG cannot terminate the nats-py reader.
- [x] `[must]` Treat unresolved runtime-owned NATS PING/PONG roundtrips as live
  transport evidence. Once the oldest outstanding PING crosses
  `ADAOS_REALTIME_CLIENT_PING_STALE_S` (default six seconds), readiness becomes
  `remote_unresponsive` even when the WebSocket and historical connect time are
  still present. This state and the outstanding count/age must also reach the
  durable sidecar lifecycle report. The observer must parse the NATS stream
  across TCP/WebSocket chunk boundaries and skip declared `MSG/HMSG/PUB/HPUB`
  payload lengths; exact-chunk matching is invalid because Root may coalesce a
  PONG with application frames, while payload bytes may themselves contain
  `PING\r\n` or `PONG\r\n`.
- [x] `[must]` Admit overlapping local NATS relays only for a verified
  supervisor warm switch. Resolve loopback socket ownership off the sidecar
  event loop, match managed/candidate PID and runtime-instance identities, and
  serialize admission. Ordinary reconnects wait up to
  `ADAOS_REALTIME_SESSION_DRAIN_TIMEOUT_S` for old relay cleanup and otherwise
  fail closed. A reconnect whose socket owner matches the sole active
  PID/runtime instance actively aborts and drains that old relay before opening
  its replacement. Neither path may increment the admitted-handoff counter or
  leave parallel Root sessions.
- [x] `[must]` Probe process readiness with `GET /ready` on the loopback control
  port (`ADAOS_REALTIME_CONTROL_PORT`, default NATS port plus four). The response
  carries `adaos.realtime_sidecar.control.v1`; readiness must not open the NATS
  listener or increment local/remote session counters. PID ownership remains a
  non-invasive fallback, while raw TCP connect is legacy opt-in only. This also
  applies to synchronous listener snapshots used by runtime diagnostics: when
  PID discovery is unavailable because `y_py` is loaded, adopted-process
  liveness is read from the control port and a managed child is verified by the
  following asynchronous control probe. Snapshot collection must never use the
  NATS data port as a bind probe.
- [x] `[must]` Request graceful sidecar process shutdown before forced
  termination. Managed restart gives each live relay time to close its remote
  WebSocket with a close frame; the peer must not observe synthetic `1006`
  merely because an operator requested a restart. Forced termination remains
  a bounded fallback when the child does not acknowledge the loopback control
  request or the following shutdown signal.
- [x] `[must]` Keep the hub-root bridge supervisor alive when a child transport
  cleanup surfaces `CancelledError`, while still propagating cancellation
  requested by shutdown or an explicit rearm.
- [x] `[must]` Rearm an unexpectedly missing hub-root bridge from an independent
  runtime watchdog. Automatic sidecar-to-direct-WSS failover after a transient
  remote EOF is disabled by default; direct fallback remains available when
  the local sidecar listener is unavailable and transient failover remains an
  explicit emergency opt-in.
- [x] `[must]` Keep a healthy direct WSS fallback authoritative in reliability
  and project the idle sidecar as `standby`. After
  `HUB_NATS_SIDECAR_FAILBACK_STABLE_S` (default 120 seconds) and local
  quarantine expiry, close direct WSS before reconnecting sidecar so the two
  remote sessions do not overlap. `HUB_NATS_SIDECAR_FAILBACK_ENABLE=0` is the
  explicit rollback switch.
- [ ] `[should]` Complete a target-stand soak that injects abnormal WS close,
  proves automatic bridge recreation and subscription restoration, and
  observes no sidecar/direct-WSS oscillation. The 2026-08-04 local incident was
  recovered manually and is covered by regression tests, but the patched
  runtime still requires deployed soak evidence.
- [ ] `[must]` Repeat the incident log review after the reliability changes are
  deployed on both the Windows development node and Linux stand `.30`. Preserve
  a pre-failure window and correlate route-owner transitions, root/sidecar
  sessions, browser first-sync/materialization state, sparse workspace runtime
  requirements, skill action wall time, and process network/disk activity. The
  acceptance record must distinguish a transport outage from a healthy
  transport blocked by scenario or skill materialization, and identify any
  skill/process that overlaps the interruption instead of attributing it from
  timing alone.
- [x] `[must]` Local managed-restart acceptance on 2026-08-04 closed remote
  session `rt-60c8c88921` with WebSocket `1000`, opened replacement session
  `rt-02f4d1481b`, restored route subscriptions on the next one-second sample,
  kept public ingress at the expected unauthenticated `401`, and produced no
  new Root NATS parser error. This validates controlled restart; the abnormal
  close and long-duration target-stand soak above remains open.
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
- [x] `[must]` Capture target-stand evidence on `.30` that route handoff stays
  sidecar-owned on the current autostart slot. 2026-06-18 `192.168.0.30`:
  sidecar diagnostics report `/ws` and `/yws` handoff ready with runtime
  upstream discovery through the active slot.
- [x] `[must]` Prove an already-open `/ws` and `/yws` session remains usable
  across runtime restart with sidecar enabled. 2026-06-11 synthetic
  browser-equivalent clients held both sidecar websocket connections open for
  45 ping/pong cycles while `POST /api/supervisor/runtime/restart` replaced
  the runtime process.
- [ ] `[must]` Prove the same already-open `/ws` and `/yws` survival through a
  full A/B slot promotion with real root-routed browser ingress, not only a
  local sidecar-listener restart test.
- [x] `[must]` Keep only the session-aware `/ws/subnet` downstream open across
  transient runtime loss. Resume it by repeating the stored `hello` handshake,
  never by replaying already-sent application frames. Treat normal completion,
  protocol/policy/auth failures, and private `4xxx` closes such as
  `4001 link_replaced` as terminal: propagate the close downstream and do not
  resurrect the displaced identity. Preserve the last upstream close
  code/reason/classification and a terminal-close counter in diagnostics.
- [x] `[must]` Re-discover the active supervisor runtime URL on route-proxy
  reconnect so A/B slot ports do not pin sidecar to the old runtime.
- [x] `[must]` Treat protocol-opaque `/ws` and `/yws/{room}` upstream loss as a
  downstream `1012` reconnect requirement. Their owning clients perform normal
  protocol bootstrap against the newly discovered runtime; sidecar does not
  replay opaque frames.
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
- [ ] `[must]` Surface server-side WebRTC/Yjs capability and opt-out state in
  reliability/browser diagnostics. `ADAOS_WEBRTC_YJS_CHANNEL_ENABLED=0` must be
  reported as a concrete blocker for `webrtc_data:yjs`, not hidden behind
  `first_sync_timeout`, cooldown, or generic degraded transport text.
- [ ] `[should]` Add a stand preflight that flags stale realtime opt-outs such
  as `ADAOS_REALTIME_ENABLE=0`, `HUB_REALTIME_ENABLE=0`, and
  `ADAOS_WEBRTC_YJS_CHANNEL_ENABLED=0` before network, TURN, or browser
  cooldown hypotheses are investigated.

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

### Phase 3 - Endpoint media proxy

Implemented as an opt-in sidecar listener for endpoint-reachable file media.
This phase is intentionally narrower than general WebRTC media authority.

Enablement:

```text
ADAOS_REALTIME_MEDIA_PROXY_ENABLE=1
ADAOS_REALTIME_MEDIA_PROXY_HOST=0.0.0.0
ADAOS_REALTIME_MEDIA_PROXY_PORT=7425
ADAOS_REDEVICE_MEDIA_BASES=http://<hub-lan-ip>:7425
```

Contract:

- [x] `[must]` Keep the listener read-only and path-bounded to published media
  file content routes.
- [x] `[must]` Require the same token forms as the runtime media endpoint:
  `?token=`, `X-AdaOS-Token`, or `Authorization: Bearer`.
- [x] `[must]` Publish `media_proxy_contract` in sidecar listener snapshots and
  diagnostics with `current_owner`, `planned_owner`, `handoff_ready`,
  `public_bases`, route paths, and blockers.
- [x] `[must]` Support `GET`, `HEAD`, and byte `Range` requests so the same
  narrow listener can serve slideshow images now and larger audio/video
  artifacts later.
- [x] `[must]` Keep endpoint direct URLs explicit. The SDK must not turn a
  loopback runtime URL into a LAN URL unless the operator publishes a real
  endpoint-reachable base such as `ADAOS_REDEVICE_MEDIA_BASES`.
- [x] `[must]` Keep the media proxy independent from runtime `AgentContext`.
  The sidecar resolves media storage from `ADAOS_BASE_DIR` and the skill
  runtime layout so endpoint file delivery can continue while the main runtime
  context is unavailable.
- [ ] `[must]` Capture stand evidence that a legacy ReDevice receives slideshow
  content through `local_http`/sidecar media proxy instead of
  `root_relay_inline`.
- [ ] `[should]` Promote this route into transport selection diagnostics as a
  distinct `sidecar_media_http` path instead of only `local_http`.
- [ ] `[should]` Add expiry and revocation-friendly signed media URLs so query
  tokens do not need to be embedded in long-lived endpoint commands.
- [deferred] General live audio/video streaming remains a later WebRTC or
  chunked-media authority problem; this phase only solves file-content delivery.

### Phase 4 - Full realtime runtime

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

- [x] introduce an awaitable runtime-local live-room command boundary with
  owner-loop handoff, completion semantics, transaction-local diffs, and
  diagnostics; named-entity projection is the first consumer
- [x] remove detached YStore/YDoc replay from named-entity projection and keep
  desired revisions pending until its room is live
- [ ] `[could]` reduce remaining direct runtime dependencies on in-process
  live-room globals by migrating completion-sensitive writers to the command
  boundary
- [ ] `[could]` generalize the command boundary into a Yjs runtime gateway
  interface that can point to runtime-local or sidecar-owned session authority
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
- `ADAOS_WEBRTC_YJS_CHANNEL_ENABLED=0` explicitly disables server-side
  Yjs-over-WebRTC. It does not necessarily disable `events` or `media`
  datachannels, so operators can see `rtc=connected` while sync remains on
  `yws`. The default is enabled; target-stand WebRTC/Yjs acceptance must verify
  that stale env opt-outs are absent.
- Production and A/B slot lifecycle are entered through `adaos autostart ...`;
  `adaos api serve` is a foreground development/runtime debugging path and does
  not own production slot cutover semantics.
- Development should use `adaos dev serve` as the normal foreground command. It
  reuses the existing API runtime, adopts an already-running
  `adaos realtime serve` listener when present, or starts `adaos-realtime` as a
  dev-managed child when realtime sidecar mode is enabled.
- Low-level sidecar diagnostics can still run `adaos realtime serve` directly.
- Managed process topology prefers `systemd -> autostart runner -> {adaos-runtime, adaos-realtime}`.
- Standalone runtime-owned sidecar lifecycle remains transitional compatibility only and is not the target long-term architecture.
