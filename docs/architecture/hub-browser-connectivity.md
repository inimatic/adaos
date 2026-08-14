# Hub-Browser Connectivity

This document defines the target guarantees for browser connectivity to a hub.
It covers local browsers on the hub LAN and root-routed browsers that reach the
hub through the root control plane.

Read this with:

- [Channel Semantics](channel-semantics.md)
- [Browser-Hub Lifecycle Authority](browser-hub-lifecycle.md)
- [AdaOS Realtime Sidecar](adaos-realtime-sidecar.md)
- [Semantic State Plane](semantic-state-plane.md)
- [Device Access and Browsers](device-access-and-browsers.md)
- [Post-Deploy E2E Testing](post-deploy-e2e-testing.md)

## Scope

The hub-browser link is not one transport. It is a set of semantic channels that
can move across a transport ladder without changing the meaning of the data:

- API snapshots and one-shot actions over HTTP.
- Command, event, presence, and route control over WebSocket.
- Collaborative document sync over Yjs WebSocket.
- Optional WebRTC data channels for low-latency command/event/presence traffic.
- Optional WebRTC data channel for Yjs sync when the direct path is healthy.
- Media over WebRTC or the local media proxy, depending on device capability and
  network reachability.

The invariant is semantic continuity: changing transport may change latency and
quality, but must not lose acknowledged durable state or silently change command
ordering semantics.

## Current Shape

The browser currently uses multiple logical paths:

- Runtime HTTP APIs provide snapshots, actions, and diagnostics.
- `/ws` carries browser control, command, event, presence, and route traffic.
- `/yws/<webspace>` carries Yjs document synchronization.
- WebRTC direct channels may upgrade command/event/presence and Yjs sync.
- Media can use WebRTC or the local media proxy.

In hub-sidecar mode, `adaos-realtime-sidecar` owns the local transport listeners
for `/ws` and `/yws`. It remains a transport-only proxy: application semantics
still terminate in the runtime. Root-routed browsers reach the hub through the
root route proxy and the hub-root NATS tunnel.

The routed tunnel preserves WebSocket close semantics end to end. A Hub close
message carries the upstream standard close code and UTF-8 reason through NATS;
Root validates the code, bounds the reason to the WebSocket control-frame
budget, and closes the browser socket with the same signal. Root may normalize
missing, reserved, or malformed values, but it must not flatten an upstream
`1013 yws_guard_*` into a clean `1000 upstream_close`. Client backoff and guard
cooldowns depend on this contract, and losing it can turn one rejected YWS
session into an unbounded reconnect loop.

## Protocol Ladder

The client should treat the ladder as a quality model, not as a binary online
flag.

| Layer | Use | Guarantee | Normal fallback |
| --- | --- | --- | --- |
| HTTP | snapshots, diagnostics, request-scoped actions | request completion or explicit failure | retry/idempotent action policy |
| WebSocket `/ws` | commands, events, presence, route control | ordered stream per connection, explicit reconnect | HTTP snapshot plus WS reconnect |
| YWS `/yws` | Yjs updates | CRDT convergence after provider reconnect | reconnect to routed/local YWS |
| WebRTC data `events` | direct low-latency events and commands | ordered DataChannel per peer while open | `/ws` |
| WebRTC data `yjs` | direct Yjs sync | CRDT convergence while channel is open | `/yws` |
| WebRTC/media proxy | media | best-effort stream quality | proxy/local fallback |

The root route path is allowed to be the default for WAN browsers. Direct WebRTC
is an upgrade, not a dependency for correctness.

## Production Strategy

The production strategy is lifecycle-gated baseline-first with parallel
upgrade:

1. Subscribe to the Root lifecycle authority and install its fail-closed
   capability gate.
2. Bootstrap over HTTP only when `live_reads` permits it, establishing identity,
   runtime epoch, initial snapshot,
   and diagnostics.
3. Attach `/ws` when `open_control` permits the baseline
   command/event/presence path.
4. Attach `/yws/<webspace>` when `open_yws` permits collaborative sync.
5. Start WebRTC probing in parallel after baseline correctness is available.
6. Promote a semantic channel to WebRTC only after the data channel is open,
   acked, and stable for that channel's stability window.
7. Demote commands/events to `/ws` and sync to `/yws` quickly on direct-path
   failure. Demotion must not mark the whole browser offline if the baseline
   path is still healthy.
8. While the baseline path remains healthy, keep one bounded recovery timer for
   the preferred direct path. A disconnect grace period, failed probe, backoff,
   or cooldown must always end in another scheduled probe; a page reload must
   never be required to promote the connection back to WebRTC.
9. Keep HTTP request-scoped actions and explicit diagnostics as the brownout
   fallback. Offline/update recovery waits on lifecycle SSE; it does not start
   long polling or independent status/summary reconnect loops.

WebRTC-first is not the default production policy. ICE and browser lifecycle
can add seconds of uncertainty, while WS/YWS can already provide correctness.
Long-polling-first is also not the default policy because it increases latency
and load. The correct default is a guaranteed baseline plus opportunistic
quality upgrades.

The strategy has two acceptance implications:

- logical readiness is achieved by the baseline path;
- quality readiness requires the selected higher-quality path to be stable, or
  a visible and acceptable fallback reason.

## WebRTC Signaling Contract

WebRTC signaling uses `/ws`, but an ordered WebSocket alone is not sufficient:
browser ICE gathering can emit a candidate before application code has sent the
offer that creates the server peer. The protocol therefore observes these
rules:

- Every fresh browser `RTCPeerConnection` has a random `generation_id`.
- `rtc.offer` declares both `generation_id` and `negotiation_mode`:
  `fresh_peer`, `ice_restart`, or `renegotiate`.
- The browser queues local ICE candidates until the matching `rtc.offer` is
  acknowledged. It then sends `rtc.ice` in order with the same `generation_id`.
- The runtime replaces an existing peer for `fresh_peer` or a changed
  `generation_id`. It may reuse a peer only for `ice_restart` or `renegotiate`
  of the same generation.
- Candidates are applied only to the matching generation. A bounded,
  short-lived server buffer covers candidates that arrive before their offer;
  stale candidates for a different active generation are discarded.
- A full-recovery attempt must create a fresh browser peer and close the prior
  server peer before accepting the new offer. Reapplying fresh offers to one
  aiortc peer is forbidden because old ICE transports can remain allocated.

Compatibility fields are optional for old clients, but current clients must
send them. Runtime diagnostics expose the active generation, accepted remote
candidate count/types, and pending pre-offer candidate count without exposing
candidate addresses.

## Readiness Model

The browser must distinguish logical readiness from quality readiness.

Logical readiness means the user can operate the hub through a valid channel:

- The browser has an authenticated hub session.
- The route or local runtime endpoint is reachable.
- `/ws` is attached or a request-scoped HTTP path is sufficient for the action.
- `/yws` has either completed first sync or is explicitly not required for the
  current view.

Quality readiness means the current path is stable enough for high-confidence
interaction:

- No recent `hub_open_ack_timeout` bursts.
- No recent `dc_open_timeout` bursts for the selected WebRTC upgrade path.
- Yjs first-sync latency is within the documented budget.
- Yjs pressure is not in `high` or persistent `warning`.
- Eventbus backlog is not growing.
- Runtime epoch changes have been observed and reconciled by the browser.
- Fallback state is visible to diagnostics and UI, not hidden behind `ready`.

The UI may show an operable hub while still reporting degraded quality. This is
expected and useful: it prevents "green but unreliable" states.

## Failure Rules

The browser should apply these rules consistently:

- A route, WS, YWS, or DataChannel open timeout is evidence. Keep a recent
  rolling window, not only the last state.
- A fallback should clear only after the preferred path has been stable for its
  channel-specific stability window.
- Direct WebRTC failure must not block correctness; it should degrade to WS/YWS.
- Yjs provider closure must not be treated as harmless if it repeats or if first
  sync never completed.
- Routed proxies must preserve upstream close code/reason so the client can
  distinguish backoff, authorization, planned transition, and transport loss.
- Runtime restart or slot promotion must force session and epoch reconciliation
  before the browser reports quality-ready.
- Diagnostics must expose the selected transport per semantic channel and the
  reason for any fallback.

## Observer Domains

Reliability evidence must retain its observer. The independent domains are
`root_browser`, `hub_root`, `hub_browser`, and browser-local
`browser_hub_direct`. A response produced by a Hub reports the Hub runtime's
understanding even when HTTP reached it through a Root proxy. A Root response
reports only Root-local browser/session/routing evidence. Neither response may
claim that another domain is healthy by implication.

Browser runtime summaries carry an `observer` block with `domain`, `role`, and
`authority=local_runtime_only`, plus the domains the observation does not
imply. The browser composes that server evidence with its own selected WS/YWS
or WebRTC path. A direct WebRTC channel can therefore remain ready while the
Root route is degraded, and the UI reports both facts instead of flattening
them into one status.

## Checklist

Implemented:

- [x] Sidecar owns local `/ws` and `/yws` transport listeners in hub mode.
- [x] Runtime remains the semantic owner behind the sidecar transport proxy.
- [x] Browser semantic channels declare transport priorities for command, event,
      presence, sync, and route traffic.
- [x] Browser has a Yjs DataChannel provider for direct WebRTC sync experiments.
- [x] Browser arms a routed YWS idle recovery watchdog when an initialized
      root-routed session has no live sync provider.
- [x] Root publishes one leased lifecycle/capability snapshot and SSE stream;
      browser control, YWS, diagnostics, and action availability consume it.
- [x] Browser waits for lifecycle events during offline/update windows instead
      of polling status, reliability summary, and supervisor state.
- [x] Established WebRTC member commands can bypass Root control while runtime
      capability remains ready; runtime continues to own the peer.

Required for a reliable hub-browser quality bar:

- [x] Add a `hubBrowserQuality` block to reliability summaries.
- [x] Track recent timeout windows for `hub_open_ack_timeout`,
      `dc_open_timeout`, and repeated Yjs provider closes.
- [x] Surface the selected transport and fallback reason per semantic channel.
- [x] Define the production protocol strategy as baseline-first with parallel
      WebRTC upgrade and explicit demotion.
- [x] Re-arm WebRTC promotion after disconnect grace, retry backoff, and
      in-memory cooldown without requiring browser reload.
- [x] Bind offers and ICE candidates to a peer generation, queue browser
      candidates until offer acknowledgement, and replace server peers on full
      recovery instead of reusing an old ICE transport.
- [ ] Separate logical `ready` from quality `ready` in diagnostics and UI.
- [ ] Report Yjs first-sync latency and pressure as hub-browser quality gates.
- [ ] Record browser route/WebRTC/YWS fallback windows in the incident registry.
- [ ] Verify root-routed browser behavior through runtime restart and A/B slot
      promotion.
- [ ] Verify WebRTC failure degrades to WS/YWS without command or sync loss.
- [ ] Add post-deploy soak checks for local browser, root-routed browser, and
      member-browser paths.
- [ ] Move bulky mutable device lists out of Yjs where possible so Yjs pressure
      does not become the limiting factor for connectivity quality.

## Acceptance Gates

A hub-browser release should not be considered high quality until these checks
pass in diagnostics and post-deploy tests:

- Local browser can attach to HTTP, `/ws`, and `/yws`.
- Root-routed browser can attach to HTTP, `/ws`, and `/yws`.
- Runtime restart is detected and recovered without a stale green state.
- A/B slot promotion is detected and recovered without a stale green state.
- WebRTC direct channel timeout produces a visible fallback and does not block
  operation.
- Direct recovery after a closed peer reaches WebRTC again without a browser
  reload; each full retry uses a new generation and the runtime owns at most one
  active ICE socket generation per browser peer.
- Runtime diagnostics show at least one accepted remote ICE candidate for an
  attempted direct connection, or report candidate absence as the blocker.
- Yjs first sync completes within budget or reports an explicit degraded reason.
- Reliability summary reports logical state, quality state, active transports,
  and recent failure evidence.
- Diagnostics do not expose secrets, bearer tokens, or private key material.

## Initial Implementation Order

1. Add the reliability summary shape for `hubBrowserQuality` using existing
   route, state-sync, WebRTC, Yjs pressure, and eventbus evidence.
2. Add browser-side recent failure windows for route open acks, DataChannel open
   attempts, and Yjs provider closes.
3. Surface per-channel active transport and fallback reason in diagnostics.
4. Add post-deploy root-routed browser soak scenarios.
5. Reduce Yjs pressure by moving large inventory-like state to snapshot plus
   delta channels where it is not collaborative document state.
