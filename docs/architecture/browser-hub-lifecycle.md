# Browser-Hub Lifecycle Authority

## Decision

Root publishes the authoritative browser-facing lifecycle of a Hub. A browser
opens one lifecycle snapshot request and one Server-Sent Events subscription,
then uses the received capabilities to decide whether HTTP, control WS, YWS,
or an already-established direct WebRTC path may be used.

An unavailable or updating Hub is an event wait. It is not a reason for the
browser to poll `status`, `summary`, update status, control WS, and YWS on
independent timers.

This contract replaces the browser's former `/v1/browser/hub/status` decision
path. Client compatibility with that path is not required; hosted clients are
versioned and self-update.

## Authority and delivery chain

| Component | Authority | Delivery responsibility |
| --- | --- | --- |
| Supervisor | update attempt, slot transition, restart and rollback state | persists process-local transition snapshots |
| Runtime | command semantics, local API readiness, YWS room/session readiness, WebRTC peer | reports runtime/control/YWS readiness; keeps YWS and WebRTC authority |
| Realtime sidecar | root transport lifecycle and socket evidence | combines transport observation with persisted supervisor/runtime snapshots and reports a compact heartbeat to Root |
| Root | browser-visible Hub lifecycle and route state | derives one leased snapshot and publishes it over HTTP/SSE |
| Browser lifecycle coordinator | no server authority | consumes the Root lease, gates transports and projects availability flags |

The supervisor does not report browser state directly to Root. It may be
restarted, upgraded, or temporarily unavailable without removing the sidecar's
transport observation. The sidecar is the durable diagnostic bridge, but it
does not reinterpret supervisor update state or runtime business semantics.

Root must not publish browser lifecycle from its process-local socket registry
alone. In a blue-green or multi-replica deployment the Hub transport and the
browser SSE can terminate on different Root processes. Root therefore combines
the Redis-backed sidecar transport lease with the Redis-backed runtime
control/route report. Sidecar transport without runtime route evidence projects
`connecting`; both fresh reports project the same `ready` state and semantic
revision on owning and non-owning Root replicas.

During a core A/B cutover the outgoing supervisor leaves the sidecar process
alive and persists the fingerprint of the code generation that process actually
loaded. The incoming supervisor adopts both the listener and that fingerprint;
it must not relabel the inherited process with newly promoted files. If the
generations differ, the normal debounce and restart budget perform one rolling
sidecar replacement after the cutover instead of dropping the bridge at the
supervisor restart boundary.

## Public Root contract

Initial snapshot:

```text
GET /v1/browser/hubs/{hub_id}/lifecycle
```

Watch stream:

```text
GET /v1/browser/hubs/{hub_id}/lifecycle/watch
Accept: text/event-stream
event: lifecycle
```

The snapshot includes:

- semantic `state` and `reason`;
- normalized `outage.kind` and `outage.planned`;
- `transition` with update/restart phase and target version;
- root transport and route readiness;
- runtime, control, and runtime-owned YWS readiness;
- `server_capabilities` for `accept_commands`, `accept_mutations`,
  `open_control`, `open_yws`, and `live_reads`;
- source freshness for sidecar, supervisor, runtime, and root route;
- `revision`, `observed_at`, and the lease deadline `valid_until`;
- `wait.mode`, which is `event` whenever work must wait.

Semantic revisions do not change for ordinary report heartbeats. Root still
renews the SSE lease when source freshness extends or at least every 25
seconds. A browser fails closed when `valid_until` expires and waits for the
next lifecycle event; it does not start a fallback polling loop.
Root accepts up to 10 seconds of positive Hub clock skew when judging source
freshness; a small NTP offset must not alternate semantic revisions.

The sidecar-to-Root report is an mTLS-only internal call:

```text
POST /v1/hub/lifecycle/report
```

The sidecar scans local persisted state every two seconds, sends on semantic
change, and otherwise sends a 15-second heartbeat. Report failures use bounded
exponential backoff in the sidecar only. Browser clients never multiply that
recovery loop.

On an orderly service stop, sidecar first stops its heartbeat loop and sends
one final `shutdown.kind=planned` report. If the process or host disappears
without that marker, Root changes the diagnosis to `unexpected_shutdown` when
the last sidecar lease expires. A new sidecar epoch replaces either diagnosis.

## Browser behavior

The browser lifecycle coordinator is created before control WS and YWS:

1. Resolve the selected Root and Hub exactly once.
2. Install a pending, fail-closed transport gate.
3. Open the lifecycle SSE watch and fetch one initial snapshot in parallel.
4. Apply capabilities to HTTP, control WS, YWS, diagnostics, and availability.
5. On `wait.mode=event`, keep blocked transports closed until an SSE event
   changes the applicable capability.
6. On lease expiry, close/gate the affected transports and keep the SSE watch.

Transport actions are edge-triggered. Renewing a lease or receiving an SSE
heartbeat with unchanged capabilities must not call `connect()` or
`disconnect()` again. A routed YWS URL additionally requires
`transport.route_ready`; this route condition does not disable an already
established direct `webrtc_data:yjs` channel.

The channel layer itself starts denied, before Angular finishes constructing
eager subscribers. Only the lifecycle coordinator may install a remote lease
or explicitly clear the gate for a local-only runtime. This closes the startup
race in which an injected data source could open one legacy control WS before
`AppComponent.ngOnInit()` established the pending Hub lease.

The client does not fan out zone status probes from YDoc. Reliability summary
is sampled once after a meaningful lifecycle or transport change; it has no
independent degraded-state heartbeat. Supervisor/update presentation is
derived from the lifecycle stream, not from periodic runtime or supervisor
requests.

UI diagnostics use the same lifecycle gate. While a routed Root path is
unavailable, events remain in a bounded local queue and no diagnostics POST is
started. After recovery, diagnostics are coalesced behind a 30-second network
window, and the reliability probe has a 15-second retrigger cooldown so the
lifecycle, control, and sync edges of one restart cannot each produce a
request.

Local bootstrap probing also has exactly one request owner. A CORS `fetch`
includes its preflight in the same completion path; the client never aborts it
and immediately starts an XHR fallback. Failed local discovery uses exponential
backoff from 2 to 60 seconds and probes only the canonical node-status route.

Availability flags use the lifecycle lease as their authoritative source.
Components may use `canRunCommands`, `canTrustStateSync`,
`disablePrimaryActions`, and `disableStateDependentActions`. Local browser
transport evidence remains useful detail, but cannot override an expired or
denied server capability.

## Direct WebRTC and Root load

WebRTC is not sidecar-owned in this phase. Runtime owns signaling semantics and
the server `RTCPeerConnection`; the sidecar owns only the transport and
diagnostic bridge described above.

Runtime likewise remains the semantic owner of YWS rooms, documents, readiness,
and sessions. A sidecar listener may transparently proxy YWS frames to preserve
the route across an A/B slot switch; that transport pass-through is not a
transfer of YWS authority to sidecar.

After signaling, an established `webrtc_data:events` channel may carry member
commands without reopening Root control WS. `webrtc_data:yjs` may carry sync
without Root YWS. Root route readiness is deliberately separate from server
capability: when runtime is ready but the Root route is degraded, direct
commands may continue while `open_control` remains denied.

A sparse control WS may remain for control-only commands, subscriptions, and
future signaling. Moving WebRTC peer ownership into sidecar is deferred until
there is a concrete continuity requirement and a single explicit owner for
peer admission, generation, ICE recovery, and teardown. YWS room/session
authority also remains in runtime.

## Update and failure interpretation

- `draining`, `updating`, and `restarting` come from supervisor state delivered
  through sidecar; the UI shows a planned transition instead of a generic
  network failure.
- `warming` means transport may exist but runtime control or YWS is not ready.
- `degraded` can allow direct commands while denying Root-routed control.
- `offline` means Root has no usable Hub transport/runtime evidence.
- stale source evidence expires capabilities even if the last semantic state
  was `ready`.

The browser-facing reasons are deliberately distinct:

| Reason | Authority | Meaning |
| --- | --- | --- |
| `planned_shutdown` | sidecar final marker / supervisor | orderly service stop |
| update phase such as `shutdown` or `activate_candidate` | supervisor through sidecar | planned staged core update |
| `runtime_crashed` | sidecar plus supervisor process evidence | runtime was desired but is no longer alive |
| `unexpected_shutdown` | Root lease inference | Hub vanished without a planned marker |
| `root_transport_disconnected` | Root plus sidecar | Hub/runtime may live, but the Root route is down |
| `root_unreachable` | browser | internet appears online, but lifecycle authority cannot be reached |
| `no_internet` | browser network event | the browser itself reports network loss |

`root_unreachable` and `no_internet` cannot be authored by Root because Root is
the missing observer in those cases. They are fail-closed client projections
and are replaced only by a fresh Root lifecycle snapshot/event.

This separation keeps an update, a route outage, and a runtime readiness gap
diagnostically distinct without asking every browser to rediscover the cause.

## Verification gates

- Root lifecycle unit tests cover offline, ready, update, stale-source,
  direct-work-with-degraded-route states, and identical cross-replica route
  projection during blue-green operation.
- Sidecar tests cover compact state assembly, heartbeat/change reporting, and
  report failure backoff.
- Browser tests cover fail-closed lease expiry, event-gated WS/YWS, lifecycle
  availability flags, zero diagnostic polling while waiting, and direct
  command delivery without reopening Root control.
- Stand verification must observe one lifecycle SSE connection per browser,
  no repeated browser `status`/`summary` requests while offline/updating, and a
  transition to the ready capabilities after the Root event. A stop/start
  capture must show at most one routed YWS connection attempt for the recovery
  edge and no canceled-fetch/XHR pair behind a pending CORS preflight.
