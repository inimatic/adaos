# Realtime Reliability Roadmap

Status: active roadmap and checklist for realtime reliability, sidecar
transport ownership, supervisor-assisted continuity, browser/member channels,
Yjs sync, and media route hardening.

## Goal

Fix AdaOS reliability from the top down:

1. message semantics
2. authority and degraded behavior
3. hub-root protocol hardening
4. transport ownership boundaries
5. hub-member transport abstraction and semantic channels
6. sync and media specialization
7. skills and scenarios lifecycle hardening

This ordering is deliberate.
The project must not start with sidecar or transport adapters as if they alone solved reliability.

## Reading Rules

- Checked items mean an implementation slice exists in the current tree. They
  do not by themselves mean rollout acceptance on a live stand.
- Every checklist item carries a four-level MoSCoW-style priority label.
- `sidecar transport ownership` means socket/listener/relay lifecycle only.
  It does not mean protocol authority, Yjs room/session authority, or media
  authority unless a checklist item says that explicitly.
- Event Model `Phase 0` can rely on the current transport-only readiness only
  after the target stand reports sidecar enabled and `/ws` / `/yws` handoff
  ready through the same node API, CLI, and browser/runtime surfaces.
- `docs/architecture/adaos-realtime-sidecar.md` owns the narrow sidecar
  contract. This roadmap owns cross-phase sequencing and acceptance gates.
- `docs/architecture/client-availability-status.md` owns the browser-facing
  availability projection over these lower-level reliability contracts.

## Status Labels

Markdown checkboxes only distinguish done from not done. This roadmap uses the
same four-level MoSCoW-style priority vocabulary as
[Builder Roadmap](builder-roadmap.md):

- `[must]`: first-order work required for the named phase or cutover gate to be
  functionally coherent.
- `[should]`: hardening, rollout safety, or operator-confidence work that
  materially improves reliability but can follow the main `[must]` gate.
- `[could]`: useful optional diagnostics, ergonomics, or product polish.
- `[deferred]`: intentionally postponed until a later phase owns the contract,
  authority boundary, or user experience.

An unchecked `[should]`, `[could]`, or `[deferred]` item must not be counted as
a blocker for the next `[must]` gate unless the gate explicitly depends on it.

## MoSCoW Gate View

| Phase | `[must]` gate | `[should]` layer | `[could]` layer | `[deferred]` layer |
| --- | --- | --- | --- | --- |
| 0. Architecture freeze | Complete: channel semantics, authority, protocol, transport ownership docs. | None. | None. | None. |
| 1. Observability | Complete for observability scope; readiness, incident, and provenance surfaces exist. | Open: keep routed/local diagnostics aligned during rollout. | None. | None. |
| 2. Hub-root hardening | Complete for current `hub_root.*` inventory; Class A flows and route budgets are represented. | Open: broaden incident separation and policy switching evidence. | None. | None. |
| 3. Sidecar transport boundary | Partial: code supports hub-root sidecar transport and local `/ws`/`/yws` proxy listeners, and hub role now enables sidecar by default; local transport-only stand evidence exists, while full A/B/root-routed acceptance remains open. | Open: rollout/soak and operator evidence. | None. | Open: full Yjs session authority and media continuity. |
| 3.5. Supervisor continuity | In progress: supervisor owns process/update authority and candidate runtime flow; warm-switch hardening remains. | Open: browser/root signaling polish and recovery soak. | None. | None. |
| 4. Semantic channels | Complete for current browser/hub-member semantic ownership scope. | Open: live-session validation under churn. | None. | None. |
| 5. Yjs as SyncChannel | Complete for current sync-channel scope. | Open: operational validation across A/B and routed browsers. | None. | Open: sidecar-owned Yjs room/session runtime. |
| 6. Media plane | Partial: bounded file media, hub loopback validation, and opt-in sidecar endpoint media proxy exist; general media continuity remains open. | Open: direct ReDevice stand acceptance, direct browser-member admission/signaling validation. | Open: multi-source expansion. | None. |
| 7. Skill/scenario lifecycle | Open: communication model must feed lifecycle and artifact hardening. | Open: provenance UX and operator clarity. | None. | None. |

## Current Status

### Stand checkpoint: 2026-05-28

The `.30` stand did not match the completed transport-only sidecar claims during
the latest stabilization check. `adaos node reliability` reported sidecar
disabled by `role_default`, `event_model.phase0.communication` still
`in_progress`, and blockers stating that browser route websocket and Yjs
websocket/session ownership still terminate in the runtime FastAPI/gateway.
Treat the implementation as present in code/docs but not accepted on this stand
until rollout/config is reconciled and the same reliability surfaces report the
transport-only `/ws` and `/yws` handoff as ready.

### Repository checkpoint: current tree

- The `192.168.0.30` incident on 2026-08-04 exposed two coupled livelocks.
  A closed subnet member WebSocket raised a synchronous Starlette
  `RuntimeError`; the receive loop retried it without yielding until the
  runtime consumed a CPU and filled the `8778` accept backlog. Independently,
  supervisor diagnostics referenced `re` without importing it, so every
  monitor iteration failed before the existing API-unready watchdog could
  restart that live-but-unresponsive runtime. Subnet receive failures are now
  terminal except for explicitly recoverable malformed JSON, stale connection
  cleanup is generation-bound, and the supervisor exception boundary advances
  runtime self-heal even when auxiliary monitor work fails.
- A promoted target that reached `root_restart_timeout` can now converge after
  that self-heal only when the replacement runtime is ready and the active
  manifest matches the original immutable target. The reconciler records a
  terminal validation instead of replaying the update or silently claiming
  rollback.
- The one-shot `.30` recovery advanced from slot B to slot A at exact target
  `bf8ba37edfc743e85a1a62baefd17e808147ff78` (`0.1.665`) and reached
  `succeeded / validate` without redispatching the state-changing command. The
  replacement supervisor reconciled root restart completion and adopted the
  already-ready slot A runtime.
- That rollout also exposed an unbounded `candidate_not_ready` loop. Candidate
  startup completed in the same second that the old 12-second readiness windows
  expired and the supervisor stopped it; the scheduled attempt then rebuilt the
  inactive slot from scratch. The current tree uses 60-second readiness windows,
  persists a deferral counter, permits one automatic retry by default, and then
  fails explicitly in `prewarm` with public evidence instead of repeating
  preparation forever or silently violating strict warm-switch policy.
- Automatic release reconciliation on `.30` subsequently detected immutable
  target `ffee59be46c496049421c1c8ba19e25dcfc5044a` (`0.1.666`) without an
  operator redispatch. Slot B preparation took about 182 seconds under local I/O
  pressure, the passive runtime became ready during the normal 60-second
  countdown, and warm cutover completed through root promotion plus a managed
  supervisor self-restart. The replacement generation reported
  `succeeded / validate`, exact manifest parity, no candidate listener, a clean
  monitor (`last_failure=null`, `consecutive_failure_total=0`), and ready
  sidecar/upstream-route evidence. This is the live acceptance for bounded
  slow-candidate handling and automatic distribution recovery.
- The hub-root bridge now has two independent recovery rails. Child transport
  cleanup cancellation is classified separately from owner-requested task
  cancellation, so an abnormal sidecar EOF cannot silently terminate the
  supervisor loop; a periodic runtime watchdog also rearms a bridge task that
  nevertheless disappears. Sidecar/direct-WSS oscillation after a transient
  remote failure is disabled by default, while listener-unavailable fallback
  remains intact.
- Regression coverage now exercises `established -> abnormal remote close ->
  local session close -> new runtime session`, missing-bridge rearm, and both
  child and owner cancellation semantics. It also proves that a fragmented
  `PUB` payload crosses the sidecar byte-for-byte even when legacy sidecar NATS
  ping configuration is present. Live soak after deployment remains open.
- A planned warm cutover is now a bounded YWS guard exception, not a reconnect
  storm. While the persisted update transition is active, and for 120 seconds
  after successful completion by default, reconnect attempts and short
  sessions are excluded from storm accounting. Admission still requires a
  live route (or an already-active YWS session) and never bypasses the active
  session limit. The grace/max-age values and current transition evidence are
  exposed in the Yjs balancer snapshot. Operators can tune them with
  `ADAOS_YWS_GUARD_PLANNED_TRANSITION_GRACE_S` and
  `ADAOS_YWS_GUARD_PLANNED_TRANSITION_MAX_AGE_S`.
- Browser lifecycle policy now retains control and YWS capabilities during
  `root_promotion_pending` only when the authoritative snapshot is fresh and
  both runtime readiness and sidecar/root-route continuity are confirmed. The
  update badge remains the transition surface; availability does not fall
  through to generic `Recovering` solely because of this planned handoff. Any
  stale or degraded dependency still fails closed.
- Code and tests now keep realtime sidecar enabled by default for hub runtimes;
  `ADAOS_REALTIME_ENABLE=0` or `HUB_REALTIME_ENABLE=0` is the explicit opt-out.
- Yjs materialization fallback is now explicitly bounded and degraded-aware.
  `/api/node/yjs/webspaces/{id}/materialization/snapshot` reads a detached
  disk/cache snapshot by default instead of depending on the live YWS room. If
  the seed cannot be read inside `ADAOS_YJS_MATERIALIZATION_SNAPSHOT_TIMEOUT_S`,
  the endpoint returns a degraded seed contract (`state`, `reason`, `source`,
  `stale`, `last_good_snapshot_at`) instead of hanging until a routed `502`.
  This keeps the browser operable without marking sync healthy.
- YWS room bootstrap timeout is a sticky semantic incident, not a hidden
  reconnect detail. A timed out bootstrap records `bootstrap_stuck`,
  `stuck_step`, `stuck_since`, `stuck_reason`, `stuck_attempt_id`, and
  `recommended_action` in runtime diagnostics. Recovery starts by resetting the
  runtime room/lock and evicting YStore runtime state; repeated timeouts
  escalate the recommendation to controlled runtime restart.
- State-sync health now separates process/route readiness from semantic Yjs
  readiness. `/api/node/status` may remain process-ready while reliability
  reports `semantic_health.yjs_room.state=stuck`,
  `materialization_seed.state=degraded`, and
  `supervisor_action_required=true`.
- Browser diagnostics now surface degraded seed and stuck bootstrap reasons in
  the YJS signal (`seed=...`, `sync-blocked=...`) so fallback preserves
  manageability without masking the root problem.
- Incident registry v1 now collects domain-attributed runtime/transport
  signals (`runtime_api_timeout`, `slow_event_handler`,
  `event_handler_crash`, and channel transitions), exposes them through
  `runtime.incident_registry`, and feeds canonical reliability incidents for
  LLM-oriented planning.
- Managed autostart generation no longer writes truthy hub sidecar defaults
  into the wrapper as env overrides; old stand wrappers should be refreshed
  after removing legacy `ADAOS_REALTIME_ENABLE=1`/route-proxy exports.
- The sidecar implementation can start local route proxy listeners for `/ws`
  and `/yws` and bootstrap route selection can prefer those listeners for
  matching paths.
- Supervisor-managed sidecar launch no longer depends on the full `adaos` CLI
  import graph: the process entrypoint is now
  `python -m adaos.services.realtime_sidecar`.
- Supervisor sidecar status/restart responses no longer need
  `GET /api/node/reliability`; the sidecar runtime block is built from local
  process snapshots and the sidecar diagnostics JSONL, so the control surface
  can remain responsive while the runtime event loop is lagging or frozen.
- The route tunnel contract now clears stale blocker strings when `/ws` or
  `/yws` handoff is ready, and the sidecar proxy accepts browser-compatible
  `/yws/{room}` paths in addition to `/yws?ws=<room>`.
- The sidecar can expose an explicitly enabled read-only HTTP media proxy for
  endpoint file delivery. This is the preferred legacy ReDevice slideshow path
  when `ADAOS_REDEVICE_MEDIA_BASES` points to the listener; `root_relay_inline`
  remains the emergency fallback when no endpoint-reachable base is published.
- This repository state should be described as **implemented with hub default
  enabled and partially stand-accepted for local transport-only handoff**.
  Full acceptance still requires A/B/root-routed browser survival and
  WebRTC/Yjs auto-upgrade evidence with server-side opt-outs verified.

### Local incident checkpoint: 2026-08-04, `sn_6acf0c01`

- The sidecar listener and process remained alive, but its last established
  remote session had closed with WebSocket `1006`; the runtime NATS bridge task
  had disappeared after `UnexpectedEOF`, while HTTP heartbeat continued to be
  accepted. Telegram input therefore never reached the local Builder runtime.
- Correlated Root NATS logs exposed two `Client parser ERROR` records while the
  local runtime was publishing large payloads. NATS parser state `41` is
  `MSG_END_R`: the server had consumed the declared payload length but did not
  find the required carriage return. The sidecar's timer-driven NATS `PING`
  could be queued between TCP fragments of the same `PUB` payload, adding bytes
  that were absent from the declared size. The upstream NATS close then
  surfaced at the client as WebSocket `1006` (no close frame observed).
- WebSocket `1006` is a local API diagnostic for an absent close frame, not a
  close code that may be transmitted on the wire. Healthy traffic and managed
  restart must therefore not produce it. Managed stop now calls a loopback-only
  sidecar control endpoint first so the relay itself can send a close frame.
  A dedicated process-group signal and then hard termination remain bounded
  fallbacks for an unresponsive child.
- A single `POST /api/node/hub-root/reconnect` restored the route. Sidecar
  diagnostics changed from `remote_session_state=down` and
  `transport_ready=false` to `ready` and `true`, with a new remote session and
  active NATS traffic. The routed node-status endpoint changed from transport
  `503` to the expected authentication boundary (`401` without credentials).
- After loading the patch locally, a managed `POST /api/node/sidecar/restart`
  closed session `rt-60c8c88921` with WebSocket `1000` at the Root proxy,
  opened `rt-02f4d1481b`, and restored the hub route subscriptions on the next
  one-second status sample. The operation completed in 7.85 seconds including
  process replacement; public ingress remained at the expected unauthenticated
  `401`, sidecar-originated NATS ping counters remained zero, and Root NATS had
  no new parser error. This closes the controlled-restart `1006` case; injected
  abnormal-close and long-duration large-payload soak remain open.
- Root cause: cleanup-level cancellation was interpreted as cancellation of
  the entire supervisor, and no in-process invariant repaired the missing
  bridge. The initiating protocol defect was sidecar-originated NATS keepalive
  injection into a transparent byte stream. The current tree removes that
  injection, separates child/owner cancellation, adds an independent bridge
  watchdog, and disables transient sidecar/direct-WSS failover by default.
  Deployment plus large-payload and injected-close soak remains the acceptance
  gate.

### Stand checkpoint: 2026-06-07, `adaost1` / `91.98.89.76`

Explicit sidecar enablement was tested on the managed hub stand with
`ADAOS_REALTIME_ENABLE=1`.

- Baseline before enablement matched the repository contract: sidecar disabled
  by `role_default`, no listeners on `7422` / `7423` / `7424`, and `/ws` plus
  `/yws` reported `current_owner=runtime`, `planned_owner=sidecar`,
  `handoff_ready=false`.
- First enablement attempt exposed a lifecycle blocker: supervisor launches
  sidecar from `/root/adaos`, while the active runtime uses slot `A`; the root
  checkout missed `heavy_dependency_names` and `python -m adaos realtime serve`
  failed before binding. A temporary stand hotfix copied the missing active-slot
  module into the root checkout so the test could continue.
- After the hotfix, sidecar bound `7422`, `7423`, and `7424`; diagnostics
  reported `status=ready`, `transport_ready=true`, `route_ready=ready`,
  `sync_ready=ready`, and both `/ws` and `/yws` reported
  `current_owner=sidecar` plus `handoff_ready=true`.
- The same ready diagnostics still carried stale blocker strings such as
  `browser route websocket still terminates in the runtime FastAPI app` and
  `sidecar local websocket proxy listener is not running yet`.
- Direct websocket probes confirmed `/ws` through sidecar can subscribe and
  receive `node.status`; `/yws` through sidecar works for `/yws?ws=default` and
  `/yws`, but `/yws/default` closes with `1008 unexpected_path` while the
  runtime endpoint accepts that room-path form.
- Already-open `/ws` and `/yws` sidecar connections did **not** survive
  supervisor runtime restart; both closed with code `1000` shortly after the
  active runtime stopped.
- Runtime lag test with `SIGSTOP` for eight seconds showed `/ws` through
  sidecar can remain open across a frozen runtime and resumes receiving after
  `SIGCONT`; `/yws` closed with code `1000` after runtime continuation.
- During the same runtime `SIGSTOP`, `GET /api/supervisor/sidecar/status` timed
  out, so the sidecar control/diagnostic surface is not yet independent from a
  stalled runtime in this topology.
- Hub-root NATS through `nats://127.0.0.1:7422` connected, but repeatedly hit
  `UnexpectedEOF`, quarantine, and reconnect churn. Treat hub-root sidecar
  transport as not accepted on this stand.

Repository follow-up after this checkpoint fixed the local causes for three of
those findings: the dedicated sidecar module entrypoint removes the root CLI
import drift blocker, `/yws/{room}` is accepted by the proxy, and supervisor
sidecar status no longer calls the runtime reliability API. These fixes still
need to be redeployed and revalidated on the target stand before the stand
checkpoint can be marked accepted.

Target-stand smoke after the repository follow-up confirmed the narrow fixes
when the hotfix was copied to both `/root/adaos` and the active slot `B` source
tree, because supervisor code sync otherwise restored the old sidecar file from
the active slot. With `ADAOS_REALTIME_ENABLE=1`, supervisor reported sidecar
`status=ready`, `control_ready=ready`, `route_ready=ready`,
`sync_ready=ready`, and empty `/ws` plus `/yws` blockers; `/yws/default`
connected through the sidecar listener; and `GET
/api/supervisor/sidecar/status` returned in roughly 23 ms while the managed
runtime process was stopped with `SIGSTOP`. The stand was restored to
sidecar-off after the smoke. This does not close A/B survival or hub-root
`UnexpectedEOF` soak acceptance.

Follow-up implementation added route-proxy reconnect support and active
supervisor runtime URL discovery, so sidecar no longer has to close an already
open browser websocket only because the runtime upstream disappears or moves
from slot port `8777` to `8778`. The 2026-06-07 target-stand retry exposed a
separate rollout/config blocker before full A/B acceptance could be completed:
the stand produced concurrent supervisor starts during the interrupted smoke,
then the managed runtime repeatedly logged `NATS connect failed (no
candidates)` and shut down. Treat this as an acceptance-environment blocker,
not as completed A/B survival evidence.

### Stand checkpoint: 2026-06-18, `.30` / `192.168.0.30`

The `.30` stand now validates the current transport-only sidecar shape on an
active autostart slot, but it also exposed a WebRTC/Yjs configuration
observability gap.

- Active slot was `B | 0.1.318+1.7035698`; `adaos autostart update-status`
  reported a successful transition.
- Sidecar owned `7422`, `7423`, and `7424`; diagnostics reported NATS
  connected, route tunnel support ready, `/ws` and `/yws` current owner
  `sidecar`, `handoff_ready=true`, and upstream runtime discovery through the
  active slot port.
- Runtime WebRTC reached connected state and opened datachannels, but logs
  showed `yjs datachannel disabled by ADAOS_WEBRTC_YJS_CHANNEL_ENABLED`.
- The cause was stale `/root/adaos/.env` with
  `ADAOS_WEBRTC_YJS_CHANNEL_ENABLED=0`. After changing it to `1` and restarting
  autostart, the runtime opened `events`, `yjs`, and `media` datachannels.
- Treat this as a cutover gate: browser `webrtc_data:yjs` acceptance is invalid
  unless server-side Yjs datachannel enablement is visible in diagnostics and
  browser-side direct-path cooldown has been cleared or expired.

### Stand checkpoint: 2026-07-27, `.30` / `192.168.0.30`

The routed browser exposed a protocol-level first-sync defect after a successful
core rollout. Runtime, sidecar, and root route diagnostics were ready, while the
browser remained `runtime=connecting:yws`, `first=timeout`, and
`resync=provider_disconnected`; Infra State therefore kept an old `slot --`
projection and inventory widgets remained at their initial `Loading` values.

- The server-authoritative Yjs guard incorrectly discarded browser
  `SYNC_STEP1`. That frame is a read-only state-vector request; discarding it
  prevents the server from returning `SYNC_STEP2`, so `y-websocket` never marks
  the provider synced and repeatedly recreates it.
- Six short reconnects activated the server reconnect-storm guard and converted
  the protocol defect into a ten-minute `client_reconnect_backoff`, which made
  healthy sidecar and root routes look like a transport outage.
- Release `0.1.615` now processes `SYNC_STEP1` while still rejecting the initial
  mutating browser `SYNC_STEP2`/`SYNC_UPDATE` under server-authoritative policy.
  The full gateway suite passed, CI run `30265531570` passed, and `.30`
  automatically promoted slot `B` with runtime port `8778`.
- A live post-promotion probe through sidecar `/yws/desktop` received the
  expected `SYNC_STEP2`. Direct Infra State tools returned
  `slot B | 0.1.615 | a96d3fe`, update state `succeeded`, 36 installed skills,
  and 6 registered scenarios.
- Published `infrastate_skill` release `0.75.55` was migrated once through the
  normal runtime path. Its always-demand summary then materialized from the
  disk snapshot as `ready`, `slot B | 0.1.615 | a96d3fe`, rather than retaining
  the initial `slot --` placeholder.
- Browser-protocol stream probes subscribed through the live `/ws` endpoint and
  received complete inventory lists: 36 installed skills and 6 registered
  scenarios. This closes the two indefinitely loading inventory states.
- A sidecar `/yws/desktop` session held for 25 seconds, exchanged server
  `SYNC_STEP1` and `SYNC_STEP2`, and remained `attached / complete / ready /
  fresh` without reconnect-storm activation.

This closes the handshake defect and proves the post-promotion endpoint. It
does not yet close the wider acceptance item for one already-open real
root-routed browser session surviving the complete A/B interval.

### Stand checkpoint: 2026-07-27, `91.98.89.76`

The hub exposed two independent bootstrap deadlocks while converging from
`0.1.565` to the current core release.

- The host explicitly disabled warm switch, so candidate prewarm correctly
  returned `skipped`. The strict warm-cutover gate nevertheless required
  candidate readiness and repeatedly cycled through prepare, countdown, and
  `candidate_not_ready` deferral. Release `0.1.617` now applies that readiness
  gate only when warm switch is enabled; configured cold transitions no longer
  require an impossible passive candidate.
- The first recovered cold transition reached slot `B` and validated runtime
  `0.1.617`, but root parity remained pending. Partial root promotion had
  projected only the files named by the bootstrap comparison, so import
  preflight combined new candidate modules with stale root modules and failed
  on a transitive `adaos.services.skill.declarations` import.
- Release `0.1.618` treats `src/adaos` as one atomic root-promotion unit whenever
  any package member changes. Preflight therefore validates the exact complete
  import graph that will be committed; the existing backup and rollback
  transaction still covers the whole promoted unit.
- Live recovery completed on slot `A` with build
  `0.1.618+1.5d59c42`: update state is `succeeded / validate`, root promotion is
  no longer required, runtime API readiness and active-slot ownership agree,
  and the supervisor monitor has zero consecutive failures. The temporary cold
  fallback was removed after validation; the original configuration backup is
  retained on the host.

A supervisor restart cancelled the already scheduled push-driven attempt, and
the release was not reissued during the bounded observation window. Recovery
used one pinned operator request. Automatic intent redelivery across that exact
restart boundary remains an explicit acceptance item rather than an assumed
property.

### Done

- architecture documents for channel semantics, authority, hub-root protocol, and transport ownership are in place
- runtime reliability model is represented in code and exposed through `GET /api/node/reliability`
- `adaos node reliability` surfaces readiness, degraded matrix, and channel diagnostics
- browser/page runtime now consumes read-only communication diagnostics through shared `runtime.reliability`, `runtime.supervisor`, and `runtime.phase0.communication` transforms instead of keeping sidecar/supervisor visibility inside one header-only component path
- node API, CLI, canonical control-plane reliability projection, and browser/page runtime now share one explicit `event_model_phase0_communication` checkpoint for the current Event Model Phase 0 communication status
- those same reliability surfaces now also share a bounded `supervisor_runtime` snapshot, so browser-safe transition mode, candidate runtime visibility, and warm-switch evidence are carried through one canonical runtime payload instead of being reconstructed separately per surface
- those same reliability/checkpoint surfaces now also carry routed-browser active-runtime selection for root-routed `/ws`, so supervisor-aware browser continuity is explicit in node API, CLI, canonical control-plane projection, and browser diagnostics instead of living only inside bootstrap route-base selection
- those same reliability/browser surfaces now also carry one explicit sidecar enablement policy (`role_default` vs explicit env override), so hub runtime sidecar adoption remains observable while the hub role defaults to sidecar transport
- `adaos-realtime` now boots dedicated local websocket listeners for `/ws` and `/yws`, root-routed browser ingress can prefer them for matching paths, and runtime diagnostics can report both transport handoffs as `ready` when sidecar is enabled and listeners are ready
- supervisor-owned sidecar boot now has a narrow module entrypoint, and
  supervisor sidecar status/restart responses are locally derived from process
  state plus sidecar diagnostics instead of depending on runtime reliability
- Infra State shows realtime summary and transport diagnostics through Yjs-backed UI
- runtime now exposes canonical channel overview entries for `hub_root`, `hub_root_browser`, and `browser_hub_sync`
- runtime now exposes `hub_root_transport_strategy` with current transport, candidate list, recent attempts, reconnect/failure history, and active hypothesis parameters
- CLI and Infra State now surface the current hub-root transport strategy instead of only the last readiness bit
- hub runtimes expose an explicit sidecar enablement policy; current code keeps
  sidecar enabled by default for the hub role and preserves explicit opt-out,
  so acceptance is now a live rollout/soak gate rather than a local config gate
- detailed channel trace is no longer a default console behavior; summary/incident output remains visible while deep console trace is explicit opt-in
- channel stability is now assessed from incidents and transport churn, not only from the last connected snapshot
- Yjs runtime diagnostics now expose explicit ownership boundaries for `ui.current_scenario`, effective `ui/data/registry` branches, compatibility caches, and `yws` transport/session lifecycle
- repo workspace fallback exists for built-in skills, scenarios, and `webui.json`
- built-in fallback for `web_desktop` restores the return path from scenario views when scenario assets are missing on a hub
- canonical runtime store for skill-local env and memory lives under `.runtime/<skill>/v<major>.<minor>/data/db/skill_env.json`

### In progress

- hub-root delivery guarantees are explicit for the current `hub_root.*` flow inventory, but the broader communication track remains open because live continuity hardening, media/browser admission, and deeper sidecar scope beyond transport-only handoff are still incomplete
- route and root-control incident classes still need clearer separation
- transport strategy is now visible, but automatic policy-driven transport switching is not yet the default runtime behavior
- sidecar can own the current `hub_root` transport boundary and transport-only `/ws`/`/yws` routed-browser ingress when enabled and accepted, but full Yjs session authority and media transport are still outside sidecar scope
- WebRTC physical connectivity and WebRTC/Yjs sync promotion are now separate
  acceptance facts: `rtc=connected` is insufficient when the server has disabled
  the Yjs datachannel or browser policy is in cooldown
- media/runtime diagnostics now also expose a planned continuity contract for live member media: member update should defer, while future hub restart behavior is expected to preserve an independent sidecar path
- supervisor now enforces the first conservative continuity gate on top of that model: live-media-sensitive update transitions are deferred and unsafe manual runtime restart is refused until sidecar continuity becomes a real capability instead of only a declared target
- local process/update supervision now has a separate supervisor authority in managed deployments, and default plus root-routed browser surfaces now read one shared supervisor transition/routed-base story, but warm-switch recovery soak, cleanup, and constrained-topology hardening are still in progress
- router-side media route administration now has a normalized contract in code and a browser-visible Yjs carrier at `data.media.route`, but direct `browser <-> member` admission and signaling are still not implemented
- full sidecar-owned Yjs room/session runtime is intentionally deferred as a separate redesign block; the current roadmap has implemented the `"/yws"` transport cutover mechanics for the current scope and keeps rollout acceptance plus preparatory decoupling from runtime-local live-room ownership in this track

### Event Model dependency note

For [Operational Event Model Roadmap](operational-event-model-roadmap.md)
Phase 0 dependency tracking, the current implementation should be read as:

- `browser/member semantic channels`: materially ready for current scope
- `Yjs ownership boundaries`: now explicit in runtime diagnostics for selector, effective branches, compatibility caches, and transport/session lifecycle
- `Yjs as SyncChannel`: complete for the current sync-channel scope; remaining browser-facing work now sits in `/yws` transport ownership migration rather than in the sync contract itself
- `sidecar-owned Yjs session runtime`: explicitly deferred beyond current Event Model `Phase 0`; for the current track it is preparatory work plus `"/yws"` transport cutover, not full room/session migration
- `hub_root` Class A coverage: explicit in runtime diagnostics and now consumed by browser/page runtime communication snapshots instead of being visible only in CLI/control-plane tooling
- `event_model_phase0_communication` checkpoint: explicit across node API, CLI, canonical control-plane projection, and browser/page runtime, so Event Model Phase 0 reads the same transport-only communication status everywhere
- sidecar rollout policy: explicit across runtime diagnostics and browser/runtime summaries, so opt-in hub transport adoption can be audited separately from the still-open post-Phase-0 continuity and session-runtime work
- `local supervisor browser-safe continuity`: default browser/runtime surfaces now read one shared `supervisor_runtime` snapshot, and routed-browser `/ws` continuity now exposes supervisor-aware active-runtime selection explicitly; the remaining work is warm-switch soak/recovery and final hardening, not visibility
- `sidecar continuity`: now only blocks Event Model Phase 0 when the current runtime/media contract actually marks it as required
- `/ws` and `/yws` ownership migration: implemented for the current
  transport-only scope when sidecar is enabled and listeners are ready, with
  root-routed browser ingress able to prefer sidecar local websocket listeners;
  acceptance still requires target-stand evidence that diagnostics report
  `current_owner=sidecar` and `handoff_ready=true`; full sidecar-owned Yjs
  room/session runtime remains deferred beyond current Event Model `Phase 0`
- 2026-05-28 `.30` rollout caveat: the live stand reported sidecar disabled and
  `event_model.phase0.communication` `in_progress`; reconfirm this checklist on
  the target stand before using it as acceptance evidence.

That means Realtime Reliability is strong enough to continue Event Model
baseline alignment work, but the current Event Model `Phase 0` communication
gate should not be treated as accepted for a rollout until the target stand
confirms the sidecar enablement and transport-only `/ws` / `/yws` handoff
evidence.

### Newly implemented foundation

- per-member `browser <-> member` media capability is now advertised through `capacity.io` as `io_type=webrtc_media`
- router/reliability/media runtime now resolve member-browser direct candidates from persisted subnet capacity instead of raw `connected_total`
- normalized media route contracts now preserve `preferred_member_id` even when the selected path degrades to hub loopback or relay
- live member `node.snapshot` payloads now include local capacity, so router/reliability can use a fresher fallback view before the next heartbeat lands
- router now re-evaluates tracked browser media routes on `browser.session.changed`, member snapshot/link changes, and local `capacity.changed`

### Confirmed gaps

- transport/resource isolation is still weaker than subject naming suggests
- `.30` rollout/config can still carry stale realtime opt-outs; the latest
  example was `ADAOS_WEBRTC_YJS_CHANNEL_ENABLED=0`, which blocked browser sync
  promotion to `webrtc_data:yjs` while sidecar route handoff itself was healthy
- target-stand evidence for policy-driven transport switching is still incomplete
- route/session incident coverage still needs broader target-stand evidence
- routed topology coverage for update-state visibility while the main runtime is intentionally down remains open
- system skills and scenarios still rely on a transitional mix of `workspace`, `repo workspace`, `runtime slot`, and `built-in seed`

## Phase 0: Architecture freeze

### Status

Completed.

### Deliverables

- [Channel Semantics](channel-semantics.md)
- [Authority And Degraded Mode](authority-and-degraded-mode.md)
- [Hub-Root Protocol](hub-root-protocol.md)
- [Transport Ownership](transport-ownership.md)

### Exit criteria

- [x] `[must]` Message taxonomy approved.
- [x] `[must]` Delivery classes approved.
- [x] `[must]` Readiness tree approved.
- [x] `[must]` Degraded matrix approved.
- [x] `[must]` Authority boundaries approved.

## Phase 1: Observability and incident-driven readiness

### Status

Completed for observability scope.
The model is visible in diagnostics, route/session incidents are now classified separately from root-control incidents, and transport/sidecar provenance is exposed in the runtime snapshot.
The next step is protocol hardening, not more ad-hoc diagnostics.

The 2026-08-18 `.30` loopback fault injection exposed a second observability
boundary: one 853 ms runtime-to-sidecar NATS outage and one reconnect were
expanded into eight apparent route non-ready records by three browser sessions
finishing their own publish/no-upstream/forced-close cleanup. The runtime now
stores state transitions and incidents as different event kinds, labels
incident impact as channel, request, or session scoped, and exposes raw versus
readiness-impacting counts. Session cleanup remains inspectable but does not
restart the channel recovery hold. `RT-ROUTE-INCIDENT-SCOPE-001` retains the
target-stand/browser/Root acceptance campaign; implementation evidence alone
does not close it.

### Focus

Make readiness and degradation visible before changing protocol ownership.

### Work items

- [x] `[must]` Keep readiness tree and degraded matrix visible in node API,
  CLI, and Infra State.
- [x] `[must]` Keep channel stability derived from incidents, reconnect churn,
  and watchdog failures.
- [x] `[must]` Separate `root_control` transport assessment from
  route/session incidents.
- [x] `[must]` Separate channel state transitions from request/session incident
  samples without dropping either class from operator diagnostics.
- [x] `[must]` Expose provenance of current transport and current artifact
  source in diagnostics.
- [ ] `[should]` Keep remote and direct hub diagnostics consistent when
  browser is connected to `:8777`.
- [x] `[should]` Keep a bounded pre-incident process CPU/RSS/I/O and system
  network/disk-I/O history, and persist it with hub-root transport failures,
  skill execution pressure, slow event handlers, and event-loop lag.
- [x] `[must]` Observe event-loop stalls from an independent runtime thread so
  a blocked loop cannot suppress its own evidence. Persist the live loop stack,
  attributed skill/core domain, active skill handlers, and process/I/O lookback;
  the watchdog is diagnostic only and must not restart or hide the offender.
- [x] `[must]` Bound the browser runtime-beacon callback independently of
  executor queue health. Coalesce equivalent builds, serve explicitly marked
  stale state only within a finite window, return an explicit unavailable
  response after that window, and prewarm the candidate beacon before A/B
  promotion.
- [ ] `[should]` Complete `RT-POST-INCIDENT-001`: repeat the correlated
  node/Root log analysis after deployment and explicitly assess skills,
  subprocesses, and large downloads in the pre-failure window.

### Candidate code areas

- `src/adaos/services/reliability.py`
- `src/adaos/services/bootstrap.py`
- `.adaos/workspace/skills/infrastate_skill`
- `tools/diag_nats_ws.py`
- `tools/diag_route_probe.py`

### Exit criteria

- [x] `[must]` `ready/stable` is never reported when fresh incidents prove
  the channel is unstable.
- [x] `[must]` Route-session failures and root-control failures are visible as
  different incident classes.
- [x] `[must]` Operator can tell whether a problem is transport, route, sync,
  or artifact-source related.

## Phase 2: Hub-root protocol hardening

### Focus

Strengthen the most critical control plane first.

### Status

Completed for the current `hub_root.*` flow inventory.
Runtime now exposes explicit hub-root traffic classes with per-class pending budgets, live subscription/backpressure metrics, route runtime pressure, and integration outbox state.
Route runtime now also separates `hub_root.route.control` and `hub_root.route.frame` semantics with distinct counters and state (`active` / `pressure` / `degraded`), so operators can tell whether the route layer is failing on tunnel lifecycle or on frame delivery.
The critical control-plane state report `hub_root.control.lifecycle` is now also explicit: hub reports carry stable `stream_id/message_id/cursor`, hub persists pending ack state locally, and root rejects stale or duplicate lifecycle reports by cursor/message id.
`hub_root.control.lifecycle` now also emits a bounded heartbeat from hub runtime, and protocol assessment treats missing or aging lifecycle acks as explicit authority health signals instead of relying only on transport reconnect status.
Runtime now surfaces this as explicit `control_authority` state (`fresh` / `aging` / `stale` / `missing`), so operators can inspect control-plane freshness directly instead of parsing assessment reasons.
The first concrete Class A stream is now explicit for `hub_root.integration.github_core_update`: hub reports carry stable `stream_id/message_id/cursor`, the hub persists pending ack state locally, and root rejects stale or duplicate state reports by cursor/message id.
The selected retryable integration flow `hub_root.integration.telegram` now carries an explicit `operation_key`, and root suppresses duplicate Telegram sends inside a bounded Redis TTL window instead of relying on text-only heuristics.
The hub-side Telegram outbox is now also persisted locally, so pending `must_not_lose` Telegram operations survive a hub restart and continue draining after reconnect instead of existing only in memory.
The selected retryable integration flow `hub_root.integration.llm` now carries an explicit `request_id`, and root suppresses duplicate LLM retries by serving a bounded cached response when the same logical request is replayed with the same request fingerprint.
Root-side report provenance is now queryable for the explicit control/core-update streams, including root receive time and ack result, so operators can verify protocol state without direct Redis inspection.
Runtime now also exposes `hardening_coverage`, and for the current `hub_root.*` flow inventory the protocol layer reports complete coverage (`6/6`) across cursor/ack streams, route semantics, idempotency keys, request keys, and durable Telegram outbox handling.

### Work items

- [x] `[must]` Classify current hub-root messages by taxonomy and delivery
  class.
- [x] `[must]` Isolate control, integration, route, and sync-metadata traffic
  by real budgets.
- [x] `[must]` Split queues, workers, limits, and backpressure policy, not only
  subject prefixes.
- [x] `[must]` Add explicit per-stream cursors where replay is required.
- [x] `[must]` Add durable outbox only for Class A and selected integration
  flows.
- [x] `[must]` Add inbox dedupe where retry or replay exists.
- [x] `[must]` Define command-specific idempotency rules.
- [x] `[must]` Define stale-authority thresholds per hub-root flow.
- [ ] `[should]` Add target-stand evidence that automatic transport policy
  decisions use these flow classifications instead of only reporting them.

### Candidate code areas

- `src/adaos/services/bootstrap.py`
- `src/adaos/integrations/adaos-backend/backend/app.ts`
- `src/adaos/services/reliability.py`
- `tools/diag_nats_ws.py`
- `tools/diag_route_probe.py`

### Bootstrap decomposition constraint

Extracted helpers are ownership seams, not a second bootstrap implementation:

- `BootstrapService` composes lifecycle, subscriptions, and service
  start/stop only
- the NATS bridge owns credentials, connect/reconnect, subscriptions, delivery
  budgets, outboxes, and transport diagnostics
- the hub route proxy owns HTTP/WS tunnels, resend/backpressure, and route
  caches
- root transport owns the required upstream link plus bridge/watchdog state,
  without duplicating route policy
- the realtime sidecar remains transport-only while protocol and Yjs authority
  migration is deferred

Migration removes synchronized helper globals and wrapper callbacks while
preserving delivery and idempotency contracts. `run_boot_sequence()` remains
composition-only. Stand evidence must cover transport policy, rooted A/B
cutover, and reconnect soak acceptance.

Implementation status, 2026-08-06:

- lifecycle now owns boot serialization, readiness, app binding, task
  adoption/replacement, and cancellation
- root transport owns bridge/watchdog execution and bounded route reset
- status/watchdog owns environment policy plus heartbeat registration
- NATS decisions are consumed through a typed composed policy
- hub-route local-runtime discovery cache and diagnostics are instance-owned
  by the route proxy policy
- `run_boot_sequence()` and its compatibility implementation are now thin
  composition delegates; boot ordering/subscriptions live in
  `bootstrap_runtime/boot_sequence.py`
- `bootstrap_runtime/nats_root_runtime.py` is now composition-only; the
  long-lived connection/session owner lives in `nats_transport_runtime.py`,
  credential persistence and refresh throttling live in `nats_credentials.py`,
  and browser/root HTTP/WS tunnel state plus cleanup live in
  `route_tunnel_runtime.py`
- explicit reconnect and authority waiting remain on `RootTransportService`;
  the transport runtime consumes those lifecycle operations instead of
  duplicating them
- `bootstrap.py` remains the compatibility/composition surface (about 1.3k
  lines after the split), while transport state has one owner and the promoted
  root dependency closure explicitly includes every extracted module
- the promoted root dependency closure also includes the extracted
  Builder/publication runtime plus supervisor config and watchdog-status
  owners, preventing a validated slot from importing a component omitted from
  root-promotion classification

Follow-up ownership cleanup on 2026-08-10 removed the synchronized private
helper registry and all bootstrap wrapper callbacks. `BootstrapService` now
wires status policy and core-update convergence operations directly to their
owning modules, and transport policy tests target `nats_bridge`,
`hub_route_proxy`, `status_policy`, and `transport_cleanup` instead of patching
the compatibility facade. The FastAPI composition root now uses
`RuntimeApplicationLifecycle`; router discovery and mounting are owned by a
separate lazy registry, leaving `lifespan()` with only lifecycle start/stop.

The 2026-08-06 `.30` delivery checkpoint promoted commit `5422f6c7` through
the rooted A/B path to slot `B`; supervisor update state finished as
`succeeded` and the replacement root supervisor validated the active runtime.
The sidecar remained a single long-lived process with `/ws` and `/yws` route
listeners ready. The thin reliability contract reported the required
`hub_root` link as `ready`, served by `supervisor_sidecar`, with no blockers;
state sync was attached, complete, semantically ready, and fresh.

The checkpoint is not a pressure-soak claim. An extended observation through
23:49 UTC still showed recurring blocking pressure: 19 event-loop-lag warnings
and four `subnet.member.link.down` event publications after 23:27. The link
reconverged to `ready` with no blockers, and the same window contained no
`recovering`, traceback, or error records, but the lag/drop pattern is not
closed by this decomposition tranche. The remaining fanout/blocking-work
investigation stays tracked separately under `RT-FANOUT` / `LRLT-001` /
`LRLT-002`.

The first follow-up hardening slice is implemented locally. Supervisor public
status reads are now single-flight and TTL-cached; a transient local probe
failure serves an explicitly marked stale last-known-good projection for a
bounded window instead of immediately turning a ready required upstream link
into false `degraded`. `core.update.status` and `hub.core_update.status` are
latest-state bounded eventbus topics with per-handler supersession, and
obsolete queued revisions do not amplify subscriber work. Synchronous skill
subscriptions now execute in a dedicated bounded worker pool after their event
payload is converted to a thread-safe plain value on the owner loop. Admission,
active thread identity, duration, failure, overload, and pre-incident process
activity remain visible in reliability and durable incidents. Async skill code
remains cooperative event-loop code; strict validation now follows local helper
calls from subscriptions and detached tasks and rejects known synchronous
filesystem, network, subprocess, future wait, and skill-memory operations. The
SDK provides context-preserving async skill-memory operations for the common
persistent-state path.
An independent daemon-thread watchdog now probes the runtime loop every 500 ms.
When the loop cannot acknowledge within the configured lag threshold, the
watchdog captures the loop thread stack while it is still blocked, attributes
skill paths as `skill:<name>`, and records bounded process and skill-execution
evidence. Recovery finalizes that same incident and the runtime maximum with the
full observed duration rather than the first threshold sample. Its runtime
counters are exposed separately from the cooperative async lag monitor, and it
performs no automatic recovery.

The first `.30` deployment of the independent watchdog produced direct evidence
for the remaining channel stalls: `infrastate_skill` background refresh called
`skill_memory_get`, which synchronously reached `pathlib.Path.read_text` on the
runtime loop while update-related disk writes were active. A registry-wide
audit then found 22 known blocking async paths across nine skills. Those paths
were moved behind bounded workers, and the same validation pass now reports
zero known blocking async paths for workspace skills. This does not prove that
arbitrary CPU loops are impossible; the independent runtime stack capture
remains the diagnostic backstop for evolved or generated skill code.
NLU Teacher persisted-state reads, merges, comparisons, and writes triggered by
`sys.ready` also run in workers. Target-stand pressure evidence is still needed
before closing the tracking items.

Delivery validation on 2026-08-06 promoted `0.1.684+1.37e2655` to `.30` slot
`A`. The first attempt exposed a bootstrap defect: `Path(source) / "."` lost
the literal `/.`, so GNU `cp` nested a seeded virtualenv under `venv/venv` and
forced a network-only pip fallback while PyPI was unavailable. The updater now
preserves the literal contents path; a one-time supervisor `copy` override
bootstrapped the fixed updater, and the successful retry used the active-slot
seed with `uv` before rooted promotion. During the three-minute post-cutover
window the node, required upstream link, `/ws` and `/yws` handoff, sidecar, and
runtime-fault gates remained ready; there were no errors, `recovering` records,
NATS disconnects, or Root timeouts, and one 287.7 ms event-loop-lag warning.

That checkpoint did not close browser reconnect acceptance. The browser
control connection remained active, but the replacement runtime observed zero
active YWS connections and no post-cutover `/yws` open attempt; state sync
therefore correctly stayed `degraded` with `firstSyncState=timeout` instead of
being masked as ready. Phase 3 browser channel-survival work and the M4
provider reattach proof remain open.

### Exit criteria

- [x] `[must]` Route pressure cannot starve control readiness.
- [x] `[must]` Reconnect restores control readiness through explicit protocol
  state.
- [x] `[must]` Critical hub-root actions are duplicate-safe.
- [x] `[must]` Degraded mode is driven by explicit authority and delivery
  rules.

## Phase 3: Sidecar as transport ownership boundary

### Status

Implemented for the current transport-only sidecar scope with hub default
enablement in code/tests. Local target-stand handoff evidence exists for
`/ws` and `/yws`; full root-routed browser and A/B slot-promotion acceptance
is still open.
The sidecar now exposes a protocol-facing runtime surface with explicit ownership boundary, transport readiness, control readiness, reconnect counters, quarantine/supersede history, and transport provenance.
Sidecar lifecycle is also independently observable and restartable through the
local control API and CLI, and managed deployments now place that lifecycle
under the existing autostart-managed control process instead of the runtime
lifespan.
This implementation is intentionally transport-only: when enabled, the sidecar
can own the `hub_root` NATS transport lifecycle plus the current routed-browser
`/ws` and `/yws` ingress handoff, but it does not yet own Yjs room/session
authority or media transport.
The intermediate ownership split is now explicit in diagnostics: current sidecar scope, lifecycle manager, and planned next boundaries are exposed alongside the deferred post-Phase-0 work for Yjs session authority and media continuity.

### Focus

Move transport ownership where it reduces blast radius, without moving protocol semantics.

### Work items

- [x] `[must]` Define sidecar status API in protocol terms.
- [x] `[must]` Expose control readiness, route readiness, reconnect
  diagnostics, and transport provenance.
- [x] `[must]` Ensure hub main process remains owner of durability and degraded
  policy.
- [x] `[must]` Implement sidecar-first routing support for hub-root transport
  lifecycle after protocol guarantees are explicit.
- [x] `[must]` Implement local `/ws` and `/yws` sidecar route proxy listeners
  for the current transport-only scope.
- [x] `[must]` Make bootstrap route-base selection able to prefer sidecar local
  websocket listeners for matching `/ws` and `/yws` paths.
- [x] `[must]` Reconcile sidecar default enablement across code, tests,
  deployment config, and docs.
- [x] `[must]` Make sidecar launch independent from unrelated CLI imports and
  root-checkout drift; managed sidecar startup must use validated sidecar code
  or a narrow entrypoint that does not import the full CLI surface.
- [x] `[must]` Capture target-stand acceptance showing sidecar enabled and
  `/ws` plus `/yws` diagnostics reporting `current_owner=sidecar` and
  `handoff_ready=true`. 2026-06-11 on `91.98.89.76`, active slot
  `A | 0.1.235+1.fce3706`: reliability and supervisor snapshots agree on
  `role_default` enablement, route readiness, and sidecar ownership for both
  route tunnels.
- [x] `[must]` Revalidate local route handoff on `.30`. 2026-06-18 on
  `192.168.0.30`, active slot `B | 0.1.318+1.7035698`: sidecar owned
  `7422`/`7423`/`7424`, `/ws` and `/yws` handoff were ready, and active-slot
  upstream discovery was working.
- [x] `[must]` Remove stale route-tunnel blocker text from ready diagnostics so
  `handoff_ready=true` snapshots do not still claim listeners are missing or
  runtime owns the route.
- [ ] `[must]` Stabilize hub-root sidecar NATS relay on the target stand. The
  2026-06-11 run no longer showed `UnexpectedEOF`, remote quarantine,
  keepalive-pong failures, or connect-failure churn, but it still showed remote
  session churn when the runtime NATS client was replaced. The remaining
  blocker is architectural: the current sidecar NATS path is a byte relay tied
  to the local runtime client lifetime. A stable root-visible hub session needs
  a protocol-aware relay or sidecar-owned hub-root NATS session authority.
- [x] `[must]` Recover the current runtime NATS session after an abnormal
  sidecar remote close without reusing stale protocol state. The byte relay
  closes the affected local socket; the runtime supervisor recreates NATS
  subscriptions, child cleanup cancellation cannot terminate the supervisor,
  and an independent watchdog rearms a missing bridge task.
- [x] `[must]` Preserve transparent NATS byte-stream integrity. Sidecar does
  not synthesize application-level NATS keepalive commands; protocol keepalive
  stays with the runtime NATS client and end-to-end liveness uses bounded NATS
  protocol roundtrips rather than raw inbound-frame idleness.
- [x] `[must]` Probe sidecar readiness through the identity-aware loopback
  control endpoint. PID fallback remains non-invasive; a raw connection to the
  NATS listener is an explicit legacy opt-in and cannot be the default probe.
- [x] `[must]` Treat a connected direct WSS fallback as the effective transport
  and project the idle sidecar as `standby`, not as a failed remote session.
- [ ] `[must]` Prove controlled direct-WSS-to-sidecar failback on the target
  stand after the stable window and quarantine expiry, with no competing
  remote sessions and bounded subscription recovery.
- [x] `[must]` Stop automatic oscillation between a healthy local sidecar
  listener and direct WSS after transient remote EOF. Direct fallback is used
  when the listener is unavailable; transient failover is explicit opt-in.
- [x] `[must]` Add route-proxy runtime-reconnect support so an already-open
  browser `/ws` or `/yws` socket is not closed only because the current runtime
  upstream disappears.
- [x] `[must]` Make route-proxy reconnect discover the active supervisor
  runtime URL instead of pinning sidecar to the original slot port.
- [x] `[must]` Add a runtime-restart acceptance scenario with already-open
  `/ws` and `/yws` sidecar sessions that remain usable while the runtime
  restarts. 2026-06-11 on `91.98.89.76`, both sockets survived
  `POST /api/supervisor/runtime/restart` with 45 ping/pong cycles and the
  sidecar pid stayed stable.
- [ ] `[must]` Add the same acceptance scenario for a full A/B slot promotion
  with real root-routed browser ingress.
- [x] `[must]` Preserve `/yws/{room}` browser compatibility through sidecar
  route proxy, not only `/yws?ws=<room>`.
- [x] `[must]` Keep sidecar status/control surfaces responsive while the main
  runtime event loop is stalled.
- [x] `[must]` Apply a new sidecar generation automatically after the active
  runtime transition is complete and stable; do not defer healthy-process code
  upgrades forever. Allow built-in NATS recovery to settle and force reconnect
  only when the supervisor-channel contract does not become stably ready.
- [ ] `[must]` Expose server-side WebRTC/Yjs datachannel enablement in
  reliability and browser diagnostics. The `.30` auto-upgrade failure was
  caused by `ADAOS_WEBRTC_YJS_CHANNEL_ENABLED=0`, but the browser symptom was
  only `first_sync_timeout` / `runtime=connected:yws`.
- [ ] `[should]` Add sidecar soak coverage for root reconnect, local listener
  restart, remote candidate quarantine, and runtime event-loop lag.
- [ ] `[should]` Add a target-stand realtime preflight that rejects stale
  sidecar/WebRTC opt-outs before network or TURN hypotheses are investigated.
- [ ] `[deferred]` Move Yjs room/session authority into sidecar.
- [ ] `[deferred]` Move WebRTC signaling/media continuity into sidecar.

### Candidate code areas

- realtime sidecar runtime
- hub startup and shutdown wiring
- diagnostics aggregation

### Exit criteria

- [x] `[must]` Transport failures are isolated from hub business logic for
  hub-root sidecar transport.
- [x] `[must]` Sidecar does not become a hidden protocol authority.
- [x] `[must]` Target stand proves transport-only `/ws` and `/yws` handoff
  without relying on runtime fallback as the success path for local sidecar
  listeners.
- [ ] `[must]` Target stand proves the same handoff through real root-routed
  browser ingress.
- [ ] `[should]` Operator can see whether a browser path is served by sidecar
  listener, runtime fallback, root relay, or direct local runtime.

## Phase 3.5: Local supervisor as process and update authority

### Status

In progress.

The next reliability gap after transport isolation is local process/update supervision.
AdaOS currently loses its primary local admin/update surface exactly when the runtime is stopped for update or restart.
This phase uses the existing autostart-managed local control process so a
management surface remains available while the main runtime is down.
Production runtime remains slot-only; root promotion becomes a separate post-validation step for bootstrap-managed code.
Current MVP coverage now includes slot-first validation, explicit root-promotion states, an explicit `root restart in progress` attempt stage after root promotion, forced shutdown recovery for hung runtime restarts, one queued subsequent transition after an in-flight transition, minimum-interval scheduling for normal update requests, operator-driven defer for planned/countdown updates, a browser-shell transition badge, pushed browser-safe supervisor transition delivery over the control `/ws` channel with `/hubs/<id>/api/supervisor/public/update-status` fallback polling when that control path is unavailable, a canonical supervisor runtime object in the control-plane model so Infrascope/overview surfaces can project transition state as an operator runtime instead of only a transport outage, browser-safe and canonical operator surfaces that both carry the current transition `action` plus passive-candidate prewarm stage, formal safe supervisor actions in that canonical object for `cancel`, `defer`, and `promote_root` where the transition state allows them, routed root-facing subnet snapshots that retain transition action/scheduling/passive-candidate metadata for non-default browser topologies, a slot-bound runtime-port model with an explicit supervisor-side warm-switch admission decision (`warm_switch` vs `stop_and_switch`) based on reserved A/B ports and local memory headroom, per-runtime identity (`runtime_instance_id`, `transition_role`) threaded into supervisor/root-facing reports so parallel runtimes no longer collapse into one opaque `hub_id`, runtime self-identification/guardrails so candidate runtimes are skipped by local fallback control discovery and reject mutating local update commands until cutover, early inactive-slot preparation with deferred skill-runtime commit so heavy slot build work moves before shutdown without mutating live skill runtime selection during countdown, and real candidate-runtime fast cutover where supervisor promotes/adopts a prewarmed passive candidate and falls back to stop-and-switch if that authority handoff fails.
That MVP now also includes a first live-media continuity gate: supervisor consults runtime reliability before restart/update, defers transitions that would violate the declared continuity contract, and keeps that reason visible through planned update state.
The shared reliability/runtime surfaces now also carry that transition state directly through `supervisor_runtime`, and the routed-browser `/ws` path now surfaces supervisor-aware active-runtime base selection through the same checkpoint family, so default browser, routed browser, CLI, and canonical control-plane consumers no longer need separate heuristics to see transition mode, candidate runtime, or routed continuity evidence.
Service-skill supervision now explicitly quiesces on runtime `subnet.stopping`: the service watchdog and health loop are disabled before child service processes are terminated, preventing auto-respawn during supervisor-managed shutdown windows.
Core update preparation now has a prepare-specific deadline (`ADAOS_SUPERVISOR_PREPARE_TIMEOUT_SEC`, default 900s) and a supervisor-owned prepare lease. If supervisor timeout/cancel recovery revokes the lease, a late prepare worker is refused before it can replace the inactive A/B slot, so slow or blocked `pip`/`git` I/O cannot mutate slot state after rollback.
The remaining supervisor gap is no longer the existence or visibility of fast cutover itself but the last-mile hardening around it: smoother root/browser signaling during warm-switch authority handoff so the shell is not reduced to generic reconnect churn, plus more soak/recovery coverage for dual-runtime registration, candidate cleanup, and constrained-memory fallback.
Browser delivery now also separates the supervisor-owned raw transition surface from the wider semantic control-plane event: `supervisor.update.status.raw` carries the browser-safe `status/attempt/runtime` payload, while `core.update.status` remains the normalized compatibility event for the rest of the control plane.

### Focus

Separate:

- transport ownership
- runtime execution ownership
- local process/update supervision ownership

The sidecar remains transport-only.
The managed local control process becomes the authority for local runtime
lifecycle and update attempt state.

### Work items

- [x] `[must]` Define the managed local control authority boundary.
- [x] `[must]` Persist explicit local update attempt state independent of
  runtime bind state.
- [x] `[must]` Add restart/apply/validate deadlines and stale-attempt recovery.
- [x] `[must]` Move update-status and restart control to a supervisor API that
  remains live while runtime is down.
- [x] `[must]` Make service/autostart topology target supervisor instead of the
  main runtime process in managed deployments.
- [x] `[must]` Keep production runtime sourced from slot `A|B` even after
  supervisor/root updates.
- [x] `[must]` Validate every candidate in an inactive slot before allowing any
  root/bootstrap promotion.
- [x] `[must]` Validate the exact projected post-promotion root imports before
  mutation and enforce bootstrap re-export dependency closure by regression
  test.
- [x] `[must]` Treat an explicitly disabled warm switch as a configured cold
  transition; do not apply passive-candidate readiness gates to that mode.
- [x] `[must]` Promote the root `src/adaos` package as one transactional import
  graph so newly introduced transitive modules cannot be mixed with stale root
  modules. Live proof completed on `91.98.89.76` with release `0.1.618`.
- [x] `[must]` Make root promotion transactional: confine relative paths, back
  up the complete change set before apply, atomically persist metadata, and
  roll back every partial apply/commit failure.
- [x] `[must]` Keep an independent Linux recovery path in the managed wrapper:
  if root imports fail, launch the supervisor from a verified active/previous
  A/B slot without ad-hoc root mutation and retain a durable recovery marker.
- [x] `[must]` Allow only the supervisor control plane—not a surviving active or
  candidate runtime—to confirm completion of a root restart.
- [x] `[must]` Bridge the supervisor-owned `root_promoted -> validate` commit
  back into the new runtime event bus and make Infra State materialize
  `sys.ready` plus terminal core-update state inline.
- [x] `[must]` Arm supervisor convergence from the passive candidate's first
  bounded transition state (`preparing` / `countdown` / drain / restart), not
  only after `root_promoted`, so fast warm-switch promotion cannot lose the
  terminal event when the candidate becomes active without another bootstrap.
- [x] `[must]` Await the actual live-room Yjs transaction before reporting a
  projection as applied; when no room is ready, write through the detached
  replay log and synchronously persist/compact the resulting snapshot. Surface
  persistence failure to the SDK caller instead of recording false success.
- [x] `[must]` Recover a same-version quarantined skill runtime only through an
  explicit named migration, retain backup/rollback semantics, and keep
  background migration from automatically repeating that state-changing
  operation.
- [x] `[must]` Prove terminal Infra State server/disk convergence on both Linux
  nodes. Automatic release `0.1.624` (`a25f227`) converged `192.168.0.30` and
  `91.98.89.76`; fresh processes read the same slot summary from disk, both
  runtimes retained active `infrastate_skill 0.75.59`, and no update or refresh
  command was repeated.
- [x] `[must]` Prevent a structural last-good render snapshot from overriding
  live operational `data/infrastate` and node-scoped Infra State. The client
  regression covers stale `slot —` cache versus current local and member Yjs
  values while materialization is incomplete.
- [x] `[must]` Resolve local node-owned projections to their live unscoped
  producer path instead of the first-paint `data/nodes/<local>/...` federation
  alias. Fix `f623040` is released in client `0.0.245+a497386`; its regression
  distinguishes a current local `data/infrastate/summary` from a stale local
  alias, and all 95 focused page-data tests plus the production build pass.
  Remote node projections remain scoped. Live probes on `192.168.0.30` and
  `91.98.89.76` both report `succeeded`, `slot A | 0.1.628 | fede6a4`, from
  active `infrastate_skill 0.75.60`.
- [x] `[must]` Keep manual and CI client publication on the same Firebase
  project. `.firebaserc`, local deploy scripts, and CI use the `inimatic`
  project alias, and release verification uses `https://inimatic.com`. The
  immutable legacy Google project ID is confined to the alias mapping and
  service-account identity.
- [x] `[must]` Recover the interrupted same-version `infrastate_skill`
  quarantine on `91.98.89.76` through one explicit named transactional
  migration. Operation `skill-migrate-bab5a15abc` passed all 115 skill tests,
  activated slot B, cleared the deactivation marker, and did not introduce an
  automatic state-changing retry.
- [ ] `[must]` Confirm restart-safe Infra State first paint in the existing
  routed browser sessions on `192.168.0.30` and `91.98.89.76`. Core `0.1.628`
  (`fede6a4`) and client `0.0.245` are released and both live server projections
  are current; visual confirmation remains open until the existing browser
  sessions reload the released client bundle.
- [x] `[must]` Provide a pinned one-shot recovery entry point for a node with a
  broken root checkout. `tools/recover-node-update.sh` selects an importable
  root/A/B control runtime, validates an exact remote branch SHA, persists the
  intent before dispatch, invokes the normal transactional updater exactly
  once, and treats a lost acknowledgement as ambiguous instead of retrying.
  Observation proves active-slot/root-import/replacement-supervisor agreement;
  the separately guarded `--finalize-root-restart` handles only the legacy
  `succeeded/root_promoted` boundary and has its own one-shot durable receipt.
  The script is included in bootstrap-critical root promotion, ensuring the
  runbook remains locally executable after recovering a non-Git root checkout.
- [x] `[must]` Decouple periodic core-release reconciliation from realtime
  hub-root route readiness; use the ready local runtime and its direct root mTLS
  client as the bounded update-discovery path.
- [ ] `[should]` Prove that a push-driven update intent cancelled by supervisor
  restart is automatically redelivered and completed without a pinned operator
  request.
- [x] `[must]` Make routed member credentials survive Root Redis
  restart/redeploy and rotate before expiry. Root now issues signed,
  subnet/node-scoped join sessions and accepts one-time legacy-session upgrade;
  member runtime refreshes the credential proactively and reports refresh
  diagnostics without logging the token. An explicit rejoin/reconnect also
  reuses the original boot-generation readiness callback after successful
  registration, so recovery cannot leave a connected member permanently at
  `ready=false`. Production backend `0.1.148` (`80b7942`) issued the signed
  session used to rejoin `192.168.0.40`; after its next ordinary restart the
  node reports `ready=true`, `connected_to_subnet=true`, and
  `connected_to_hub=true`.
- [x] `[must]` Distinguish scenario Catalog, Workspace source, and active
  Runtime in Infra State. Release `infrastate_skill 0.75.60` labels a missing
  sparse source explicitly and presents its cloud action as source restore;
  equal Catalog/Runtime versions no longer look like a hidden version update.
- [x] `[must]` Prove the hardened root promotion and slot fallback through two
  consecutive release updates on the affected second machine. Evidence is
  recorded under
  [`AP7-16`](artifact-source-package-activation-roadmap.md#milestone-ap7-end-to-end-proof-and-legacy-retirement-decision):
  automatic `0.1.611`/`0.1.612` convergence followed by the generation-bound
  `0.1.614` control on `192.168.0.30`.
- [x] `[must]` Detect bootstrap-managed file changes and surface
  `root_promotion_required` explicitly instead of silently mixing slot and
  root drift.
- [x] `[must]` Keep supervisor-owned sidecar lifecycle observable through both
  supervisor and runtime-compatible node-control surfaces.
- [x] `[must]` Retain standalone runtime fallback only for non-supervised
  deployments, without turning sidecar into protocol or update authority.
- [x] `[must]` Migrate installed skill runtimes as an explicit core-update
  subflow rather than assuming old interpreter dependencies remain valid.
- [x] `[must]` Persist per-skill migration diagnostics (`prepare` / `test` /
  `activate` / `rollback` / `deactivate`) in core-update results.
- [x] `[must]` Surface skill migration failures and selective post-commit
  deactivations in Infra State and Infrascope.
- [x] `[must]` Keep supervisor transition state visible in canonical operator
  projections (`active_runtimes`, health strips, recent changes) rather than
  only in ad-hoc browser badges.
- [x] `[must]` Separate runtime liveness from listener/API readiness in
  supervisor-visible status.
- [x] `[must]` Keep live-but-unresponsive runtime self-heal independent of
  auxiliary monitor success, and regression-test the exception boundary.
- [x] `[must]` Terminate closed/replaced subnet member receive handlers without
  a synchronous retry loop, and prevent stale handler cleanup from removing a
  replacement member link.
- [x] `[must]` Reconcile `root_restart_timeout` after bounded runtime self-heal
  only when the active manifest still matches the requested immutable target.
- [x] `[must]` Surface the active managed runtime command/source in supervisor
  diagnostics.
- [x] `[must]` Surface active-slot structure validation in supervisor
  diagnostics so broken slot layouts fail explicitly.
- [ ] `[should]` Harden diagnostic skills so Yjs-backed operator surfaces keep
  the last usable local snapshot during transient control-plane file failures.
- [x] `[must]` Keep browser-facing update visibility alive through pushed
  supervisor status on `/ws`, with supervisor polling only as fallback while
  `/ws` and `/yws` reconnect during slot restart.
- [x] `[must]` Expose a read-only browser-safe supervisor transition surface so
  restart/update state is not collapsed into generic `offline`.
- [x] `[must]` Distinguish browser-facing `hub restarting`, `update applying`,
  `rollback`, `root promotion pending`, `root restart in progress`, and
  `update failed` from ordinary transport reconnect state.
- [x] `[must]` Surface `planned`, `deferred`, minimum-window scheduling, and
  queued follow-up transition state through that same browser-safe/read-only
  supervisor surface.
- [ ] `[must]` Extend the routed read-only supervisor transition surface across
  every browser deployment topology, not only the default
  `/hubs/<id>/api/...` entry path.
- [x] `[must]` Reserve stable runtime ports per slot so supervisor can reason
  about `active` and `candidate` runtimes explicitly.
- [x] `[must]` Add a memory gate that decides when dual-runtime warm-switch is
  safe and when supervisor must fall back to stop-and-switch.
- [x] `[must]` Surface `transition_mode`, candidate runtime URL/port, and
  warm-switch admission reason in operator and browser-safe status.
- [x] `[must]` Assign every runtime process a stable-per-boot
  `runtime_instance_id` and `transition_role` so root/NATS/browser can
  distinguish `active` from `candidate`.
- [x] `[must]` Keep candidate runtimes passive on root-routed traffic subjects
  until cutover so prewarm does not create duplicate hub traffic consumers.
- [x] `[must]` Automatically prewarm passive candidate runtime when warm-switch
  is admitted, surface its readiness/failure in supervisor/browser-safe status,
  and keep the candidate passive until supervisor explicitly commits cutover.
- [x] `[must]` Bound slow-candidate readiness and automatic warm-switch
  deferrals; expose their count and terminate exhausted attempts as an explicit
  `failed / prewarm` outcome rather than an infinite prepare loop.
- [x] `[must]` Harden fast-cutover authority handoff so promoted candidate
  runtime becomes the sole live root/browser traffic owner without ambiguous
  overlap. Promotion now waits for route authority before retiring the old
  listener, retirement skips node-wide subnet lifecycle, root restart adopts a
  slot-matched listener even before its API is responsive, supervisor-managed
  children cannot self-initiate listener takeover, and unfinished retired
  processes receive bounded detached cleanup.
- [ ] `[should]` Add stronger soak/recovery coverage for candidate promotion
  fallback, stale candidate cleanup, and low-memory warm-switch downgrade
  paths.
- [ ] `[should]` Keep the supervisor public/status control surface responsive
  while a promoted candidate retires a slow old runtime and root promotion is
  being finalized. The `.30` acceptance kept the new runtime and routed data
  plane available, but the old-runtime shutdown took about 55 seconds and the
  supervisor API was temporarily unavailable until the managed self-restart.

### Candidate code areas

- `src/adaos/apps/autostart_runner.py`
- `src/adaos/services/core_update.py`
- `src/adaos/services/autostart.py`
- `src/adaos/apps/cli/commands/setup.py`
- `src/adaos/apps/cli/commands/node.py`
- `src/adaos/apps/supervisor.py`

### Exit criteria

- [x] `[must]` Update status remains visible while runtime is stopped.
- [x] `[must]` Stale `restarting` / `applying` states resolve
  deterministically.
- [x] `[must]` Rollback decision is owned by supervisor logic rather than only
  runtime-side best effort.
- [x] `[must]` A failed root import cannot be reported as a successful root
  restart by a surviving runtime process.
- [x] `[must]` A second-machine recovery record shows root import failure,
  verified-slot fallback, transactional root repair, and a subsequent clean
  release cycle. `192.168.0.40` recovered transactionally to slot B on
  `0.1.625` (`5407a0d`): the exact pinned update was dispatched once, root
  import passed after promotion, the legacy `root_promoted` boundary received
  one guarded service restart, and the replacement supervisor committed
  `succeeded/validate`. With its signed Root-proxy session restored, the hub
  then delivered the ordinary `0.1.626` (`9d319b9`) release automatically.
  That clean cycle completed through candidate validation, root promotion, and
  replacement-supervisor validation without operator update/reconnect; final
  probes confirm root imports and member readiness/connectivity.
- [x] `[must]` Sidecar remains transport-only and does not absorb
  process/update authority.
- [x] `[must]` Operators can identify which installed skill failed during a
  core migration and at which stage.
- [x] `[must]` Operators can distinguish `slot validation`,
  `root promotion pending`, and `root restart in progress` from
  supervisor-visible state.
- [x] `[must]` Browser header/status surfaces can distinguish controlled
  supervisor-managed restart/update transitions from plain hub offline or
  transport loss.
- [ ] `[must]` Routed browser sessions can continue reading live supervisor
  transition state even while runtime `/api`, `/ws`, and `/yws` are
  unavailable.
- [x] `[must]` Browser/operator transition surfaces can distinguish a passive
  `candidate` runtime from the current `active` runtime by
  `runtime_instance_id`, role, and candidate readiness state instead of showing
  only one opaque "hub restarting" bucket.
- [x] `[must]` Operators can tell whether the next transition is planned as
  `warm_switch` or `stop_and_switch`, and why.
- [x] `[must]` Local fallback control resolution cannot accidentally target a
  passive `candidate` runtime as if it were the active admin endpoint.
- [x] `[must]` Root/browser diagnostics can distinguish concurrent `active`
  and `candidate` runtimes by explicit runtime instance identity instead of
  only `hub_id`.
- [x] `[must]` When warm-switch is admitted and candidate prewarm succeeds,
  supervisor can promote/adopt that candidate without ambiguous overlap, while
  fallback to stop-and-switch remains deterministic. Live `.30` recovery
  promoted the second bounded candidate attempt and completed root validation.

## Phase 4: Hub-member semantic channels

### Status

Completed for current browser/hub-member semantic ownership scope.
Runtime now exposes explicit hub-member semantic channels (`command`, `event`, `sync`, `presence`, `route`, `media`) with one selected active path per channel, live transport evidence from `/ws`, `/yws`, WebRTC datachannels, and root relay runtime, plus explicit failover order, freeze windows, and duplicate-suppression notes.
Frontend command delivery and sync-provider creation now also use a shared semantic channel selector instead of branching directly on WebRTC-vs-WS in application code, and the web header transport indicator now follows the selected semantic member path instead of raw WebRTC peer state.
Frontend transport notifications now also follow semantic member-path transitions, and the client no longer keeps a separate application-level `useWebRtc` authority for command routing.
The browser shell now consumes semantic member transport state directly from the channel selector service, while raw WebRTC visibility/reconnect state is pushed down into the transport/runtime layer instead of remaining an app-shell concern.
Yjs startup no longer performs raw WebRTC upgrade orchestration itself; it now asks the member-transport layer to prepare direct paths and then builds sync providers through the semantic selector.
The browser connection client no longer owns raw WebRTC callback wiring either; low-level RTC state is now contained inside the transport runtime and the semantic channel selector.
The browser-side semantic selector now also carries explicit live path evidence for routed `/ws` and `/yws` (`idle` / `connecting` / `connected` / `disconnected`) instead of treating those fallback paths as implicitly healthy, so UI transport state and channel snapshots reflect real browser transport state rather than static assumptions.
The frontend semantic channel model now also declares `route` and `media` explicitly, and routed fallback availability is now derived from live browser-side path evidence instead of being treated as always-available by definition.
Command-path exceptions that must stay on the control plane, such as `rtc.*` signaling, are now also resolved inside the semantic channel layer instead of being hard-coded in the browser connection client.
Control-plane subscription orchestration is now also part of that semantic layer: browser member channels track the active control subscription set, dedupe it, and replay it on control-WS reconnect instead of leaving resubscribe behavior as ad-hoc client logic.
Control-plane session bookkeeping is now also owned there: browser member channels track control-WS session state, reconnect/open counts, close reasons, in-flight command count, and last command completion outcome instead of leaving that protocol state implicit inside the client socket wrapper.
Command envelope shaping and ack parsing for the browser control path are now also routed through the semantic member-channel layer, and browser header UI can surface the semantic snapshot (`command` / `sync` / `route` / `media`, control session state, recovery state) instead of exposing only a raw transport icon.
The browser client no longer owns pending control-command lifecycle either: in-flight command registration, ack completion, timeout/error/close failure, and route-health interpretation are now semantic-layer responsibilities rather than socket-wrapper details.
Direct-path probing is now also user-honest: when relay `ws/yws` paths are healthy, semantic channel authority no longer flips to a merely `connecting` WebRTC candidate, and repeated direct-path failures move into exponential-backoff background probes instead of keeping browser status stuck in an over-optimistic `recovering` state.
Raw control-WS session creation is now also driven through the semantic member-channel layer rather than the browser client owning its own socket/promise lifecycle, and `media` is now explicitly frozen as `out_of_scope` in the semantic snapshot instead of remaining an unnamed implicit gap.
Direct-path enablement is now also decided inside the semantic member-channel layer: browser Yjs startup no longer parses `?p2p` / `?webrtc` flags itself, and the connection client no longer takes an application-owned `allowDirect` flag when preparing member transport.
Direct-path recovery policy is now also owned by the semantic member-channel layer: browser member channels decide when visibility or control-WS recovery should trigger a direct-path renegotiation, while the low-level WebRTC transport remains only the executor of that renegotiation.
Sync self-heal policy is now also part of that semantic layer: routed Yjs fallback recovery on first-sync timeout or provider disconnect is tracked and gated by browser member-channel policy, while `YDocService` remains only the executor that recreates the concrete sync provider.
Hub-member update propagation now also exists as an explicit Phase 4 concern: hub mirrors `core.update.status` to connected members over the member link, members mirror that state locally as `hub.core_update.status`, and member runtimes can follow the hub-triggered core update through their own local admin API instead of relying on out-of-band coordination.
Node naming and member observability were also moved into the same semantic layer checkpoint: `node.yaml` now carries `node.node_names`, member hello advertises those names to the hub, reliability exposes canonical `hub_member_connection_state`, and Infra State can project node selectors plus per-member connection/update visibility on top of that runtime model.
Hub/member observability now also carries compact remote runtime snapshots over the member link: members periodically publish their own local lifecycle/build/update state to the hub, the hub stores that snapshot as part of hub-member connection state, and Infra State node tabs can render selected member build/update state from remote data instead of only showing link-level telemetry.
Member rollout semantics are now modeled on top of those snapshots as well: hub-member connection state distinguishes fresh/pending/stale member snapshots, derives a cohort-level rollout state (`nominal` / `transitioning` / `pressure` / `degraded`) from member update progress, and surfaces that rollout summary in CLI and Infra State instead of treating all connected members as equally healthy.
Hub/member observability is now also on-demand instead of purely periodic: the hub can request a fresh remote member snapshot over the member link, Infra State uses that when selecting or refreshing remote member tabs, and canonical channel overview now includes `hub_member` control plus `member_hub_sync` alongside the earlier hub-root channels.
Those same hub-member semantics are now also promoted into the canonical readiness/degraded model: readiness tree exposes explicit `hub_member` and `member_sync` nodes, and degraded matrix can now explain whether remote snapshot projection or hub-triggered member rollout is currently allowed.
Hub-triggered member rollout is now also an explicit operator control surface instead of passive follow only: the hub can request `update` / `cancel` / `rollback` on a selected member over the member link, members execute that through their own local admin API, and CLI plus Infra State surface the last remote control request/result together with the member snapshot.
Hub/member observability no longer depends only on an active member link either: hub runtime now also tracks known members from subnet directory / heartbeat state, and Infra State node tabs can render those observed members even before a full snapshot-bearing member link is established.
This is still intentionally a semantic-path checkpoint, not a full transport rewrite: signaling and subscription setup remain explicit control-plane WS behavior, and transport-specific orchestration still exists around negotiation, reconnect, and low-level datachannel runtime.
The remaining direct-path reconnect policy is now also mostly lifted into the semantic layer: browser member channels decide when a disconnected direct path should first try `ICE restart` versus a full renegotiation, apply disconnect grace and exponential backoff there, and expose the low-level RTC runtime snapshot (`rtc state`, ICE state, last failure reason) to the browser UI. The WebRTC transport service now acts primarily as an executor of SDP/ICE operations instead of hiding retry policy inside the transport runtime itself.
Control-plane RTC signaling ownership is now also aligned with that boundary: browser member channels route inbound `rtc.answer` / `rtc.ice` messages and outbound local ICE candidates through the semantic layer, while the low-level WebRTC transport remains responsible only for peer lifecycle, SDP/ICE execution, and datachannel runtime.
With that boundary in place, the browser-side exit criteria are met for implementation scope: one logical stream has one active authority path, failover rules are explicit, and application/browser adapters no longer decide transport semantics themselves. Remaining work is live-session validation and later media specialization, not more structural Phase 4 refactoring.

### Focus

Build abstraction from logical channel semantics, not from transport names.

### Work items

- [x] `[must]` Define `CommandChannel`, `EventChannel`, `SyncChannel`,
  `PresenceChannel`, `RouteChannel`, and `MediaChannel`.
- [x] `[must]` Map existing `/ws`, `/yws`, WebRTC data channels, and root
  relay traffic to those channel types.
- [x] `[must]` Define path selection, failover, freeze period, and duplicate
  suppression rules.
- [x] `[must]` Keep one active authority path per logical stream unless
  multipath is explicitly designed.
- [ ] `[should]` Add live-session validation under routed browser reconnect,
  direct-path probe failure, and update-state fanout churn.

### Candidate code areas

- `src/adaos/services/webrtc/peer.py`
- browser/hub websocket gateways
- route proxy logic

### Exit criteria

- [x] `[must]` One logical stream has one active authority path.
- [x] `[must]` Failover rules are explicit.
- [x] `[must]` Adapters no longer leak transport semantics into application
  code.
- [ ] `[should]` Browser/operator evidence proves the semantic selector stays
  stable through reconnect and fallback churn.

## Phase 5: Yjs as SyncChannel

### Status

Completed for the current sync-channel scope.
Hub and browser runtime surfaces now expose an explicit SyncChannel contract:
bounded replay window, snapshot+diff recovery, optional browser IndexedDB
persistence, explicit resync controls, and explicit separation of ephemeral
awareness from document recovery. Remaining `/yws` ownership migration belongs
to the sidecar/transport boundary work, not to the SyncChannel contract itself.

### Focus

Make Yjs transport-independent without building a second distributed system around it.

### Work items

- [x] `[must]` Append-only bounded update log.
- [x] `[must]` Snapshot + diff recovery.
- [x] `[must]` Client local persistence.
- [x] `[must]` Awareness explicitly ephemeral.
- [x] `[must]` Explicit resync path after route or transport churn.
- [x] `[must]` Preserve upstream WebSocket close code/reason through the routed
  NATS proxy and keep routed YWS recovery under one browser-side retry owner.
- [x] `[must]` Preserve a standards-complete server-authoritative handshake:
  answer the read-only browser `SYNC_STEP1` and reject only the initial mutating
  client state/update frames.
- [x] `[must]` Validate the released handshake and Infra State first-paint path
  on `.30`: sidecar `/yws` reached `ready:fresh`, the current core summary was
  materialized, and both inventory streams returned complete lists.
- [ ] `[should]` Validate SyncChannel recovery during A/B runtime switch with
  an already-open rooted browser `/yws` session.
- [ ] `[should]` On `.30`, prove a rejected or interrupted routed `/yws`
  handshake produces one bounded application recovery sequence, no autonomous
  provider reconnect storm, and a successful first sync after route recovery.
- [ ] `[deferred]` Move Yjs websocket termination and live room/session
  lifecycle into sidecar.

### Candidate code areas

- sync engine
- Yjs gateway and recovery paths
- local persistence integration

### Exit criteria

- [x] `[must]` Document updates survive reconnect within replay window.
- [x] `[must]` Yjs reliability is not duplicated blindly across transport,
  log, and UI layers.
- [x] `[must]` Awareness may drop without compromising document state.
- [ ] `[should]` A/B acceptance evidence confirms Yjs document state recovers
  after runtime slot switch without treating awareness continuity as durable.

### Completed for current scope

- hub-side YStore runtime now exposes bounded log and snapshot+diff state for operator diagnostics
- failed native snapshot encoding or replay compaction now opens an observable
  per-webspace circuit breaker. Accepted updates remain in the replay log,
  repeated maintenance attempts use exponential backoff, and a successful
  snapshot backup closes the breaker. Operators can tune the initial and
  maximum delays with `ADAOS_YSTORE_SNAPSHOT_COMPACTION_FAILURE_BACKOFF_SEC`
  and `ADAOS_YSTORE_SNAPSHOT_COMPACTION_FAILURE_BACKOFF_MAX_SEC`.
- browser sync now has an explicit resync path and runtime snapshot instead of scattered provider recreation logic
- node reliability / hub-root status surface Yjs sync runtime alongside transport and protocol state
- browser header now exposes a manual Yjs resync action, separate from scenario reseed/reload
- browser sync runtime now separates document recovery from ephemeral awareness state
- hub/browser runtime surfaces now also expose the SyncChannel contract explicitly instead of requiring operators to infer it from scattered implementation details
- node API / CLI now expose explicit Yjs runtime and snapshot-backup control paths
- hub-side node API / CLI now expose explicit per-webspace Yjs reload/reset control paths
- Infra State now surfaces Yjs runtime state and local Yjs backup/reload/reset operator actions
- node API / CLI and Infra State can now focus Yjs diagnostics and local actions on a selected webspace instead of assuming `default`
- hub-side node API / CLI and Infra State now expose explicit per-webspace Yjs restore-from-snapshot recovery when a disk snapshot exists
- legacy `/api/yjs/reload` has been removed entirely; node-scoped per-webspace Yjs controls are the only supported override path
- Yjs sync runtime now carries an explicit operator recovery playbook (`reload` vs `restore` vs `reset`) and surfaces that policy consistently in CLI and Infra State
- Yjs runtime now computes immediate recovery guidance (`backup first` vs direct `reload`) from live webspace state and surfaces the recommended next action across CLI and Infra State
- selected webspace manifest/projection state is now surfaced alongside Yjs runtime, including home scenario, source mode, rebuild status, and `go-home` guidance when projection drifts from home
- node API / CLI and Infra State now expose `set-home-current` so operators can explicitly adopt the current projected scenario as the new webspace home without typing a scenario id
- direct node-scoped Yjs/webspace control paths now publish canonical `node.yjs.control.*` events, and Infra State refreshes from those events instead of relying only on the original desktop bus commands
- Yjs recovery/control scope is now explicit as hub-local-only in operator surfaces; remote member tabs show that sync control is not applicable there

## Phase 6: Media plane

### Status

In progress with explicit media-plane policy, bounded relay authority, relay throughput tuning, and live operator validation.
Local media upload/playback MVP exists as an intentionally isolated direct-local HTTP path, reliability exposes media runtime separately from control/sync readiness, and browser semantic channels classify file media as `direct_local_http` vs `root_routed_http_relay`.
There is also a dedicated bounded root media relay path (`/hubs/<id>/media/*`) for upload and playback, separate from the generic buffered `/hubs/<id>/api/*` route proxy, and that relay now uses larger bounded chunks plus unbuffered nginx proxying for materially better large-file throughput.
WebRTC audio/video tracks are now part of the target Phase 6 scope as a direct live-validation path: browser camera/microphone tracks are negotiated on the same peer and looped back through the hub for real end-to-end operator testing.
Phase 6 is complete for the current scope: bounded file-media authority and direct hub loopback validation are in place. This is still not a general multi-party media plane: the current A/V scope is hub loopback validation plus bounded file-media authority, not a full broadcast/session mesh.

### Focus

Keep media architecture separate, but do not let it block core messaging stabilization.

### Confirmed Phase 6 gap: peer rebuild couples media to control and sync

Current browser-hub P2P behavior still has one architectural weakness:
media lifecycle is coupled too tightly to peer lifecycle.

Today the browser transport may intentionally rebuild the whole `RTCPeerConnection`
when live media starts or stops, and the hub currently favors replacing the
existing peer on every fresh offer.
This is acceptable for operator-grade loopback validation, but it is not an
acceptable steady-state design for a reliable multi-channel media plane.

That coupling creates self-inflicted failure modes:

- starting or stopping media can tear down `events` and `yjs` data channels
- a UI component destroy path can indirectly trigger full P2P renegotiation
- direct media actions and direct recovery policy share too much blast radius
- file transfer over the media data channel can be interrupted by unrelated media actions
- media growth toward multiple concurrent sources cannot be implemented safely on top of "replace the whole peer"

The next Phase 6 target is therefore not "more loopback features first".
It is to decouple media-session behavior from peer-session behavior.

Another confirmed gap is topology:
current direct WebRTC covers `browser <-> hub`, but not a direct
`browser <-> member` media peer for member-hosted media producers.
That means a media-producing skill running on a member cannot yet expose its
best direct browser path even when the network topology would allow it.

### Target architecture

The target browser-hub direct transport model is:

- one long-lived peer session per browser member session
- one stable control/data container peer, not a disposable peer per media action
- independent logical channel lifecycles on top of that peer:
  - control/events
  - sync/Yjs
  - file media transfer
  - live media tracks
- media source enable/disable must not require tearing down data-path authority
- failures in one logical media flow must degrade locally before escalating to whole-peer recovery

In that target design, `RTCPeerConnection` is transport state, while media is
workload state carried over that transport.
The browser shell, scenario layer, and widget lifecycle must not own peer
teardown authority except for explicit session shutdown.

### Architectural invariants

- peer lifecycle and media lifecycle are separate state machines
- control and sync channels remain valid when live media is added, removed, muted, or replaced
- UI open/close behavior must not implicitly stop or rebuild the underlying peer session
- one logical stream keeps one active authority path, but different logical streams may use different paths simultaneously when explicitly designed
- direct media recovery is local-first:
  - restart ICE if the peer is intact
  - renegotiate the affected media shape if needed
  - rebuild the whole peer only as the final fallback
- peer replacement must be explicit and versioned, not an automatic side effect of every fresh offer

### Media multi-channel target

Phase 6 should evolve toward a multi-channel media model rather than one
"live session" toggle.

The intended shape is:

- one control peer session
- multiple media subflows on that session
- explicit per-flow identity such as `media_session_id`, `slot_id`, or source id
- independent enable/disable and health for:
  - microphone
  - camera
  - screen share
  - file upload / binary media transfer
  - hub loopback validation
  - later broadcast or room-style media flows

This does not mean uncontrolled multipath authority.
It means media concurrency must be explicit inside the semantic channel model
instead of being approximated by repeated whole-peer rebuilds.

### Member-browser direct media target

Phase 6 should also grow from one direct topology into an explicit set of
direct media topologies:

- `browser <-> hub`
- `browser <-> member`
- bounded relayed fallback when direct peer setup is not allowed or not possible

The intended authority split is:

- router chooses which runtime should answer the media need
- hub and/or root act as rendezvous, signaling, and policy authorities
- the direct media peer may still terminate on the selected member rather than on the hub

This is especially important for member-hosted media skills.
If a media server skill runs on a member, the preferred target state is not
"member sends media to hub and hub re-originates it by default".
The preferred target state is:

- router resolves the member as the media producer
- signaling is mediated by hub/root as needed
- browser attempts a direct peer to that member when policy and topology allow it
- fallback remains available through bounded relay paths when direct media is unavailable

### Preferred implementation shape

The preferred technical direction is:

- keep `events` and `yjs` data channels long-lived once the peer is established
- stop treating `negotiate()` as synonymous with `close existing peer and rebuild`
- move to serialized renegotiation with one negotiation authority at a time
- keep stable peer/session identifiers so stale answers or superseded negotiations can be rejected safely
- use transceiver- and sender-level media control where possible:
  - pre-created transceivers
  - `replaceTrack(...)`
  - direction changes such as `inactive`, `recvonly`, `sendrecv`
- keep media upload/data transfer logic independent from live A/V track lifecycle

### Migration roadmap

#### Stage 1: remove UI-owned peer teardown side effects

- stop binding widget/component destroy directly to live-media shutdown semantics
- ensure closing a modal or unmounting a media widget only detaches UI observers unless the user explicitly requested media stop
- document and test that ordinary scenario/UI transitions do not call whole-peer teardown implicitly

#### Stage 2: split peer shutdown from renegotiation

- separate `closePeer()` from `renegotiatePeer()` in the browser transport runtime
- remove the current "always `close()` before `negotiate()`" behavior
- preserve existing data channels and peer state when only media shape changes
- keep explicit full teardown only for logout, page unload, protocol incompatibility, or unrecoverable peer corruption

#### Stage 3: stop unconditional hub-side peer replacement

- hub should no longer replace an existing peer on every fresh browser offer by default
- introduce explicit peer/session generation or epoch checks
- only supersede the old peer when the offer belongs to a new session generation or the old peer is proven unrecoverable

#### Stage 4: introduce serialized negotiation ownership

- add a negotiation mutex / coordinator on the browser side
- coalesce multiple local changes into one negotiation pass
- implement glare-safe / stale-answer-safe handling so concurrent UI actions do not create negotiation races
- expose negotiation diagnostics separately from raw connection state

#### Stage 5: move media control to subflow semantics

- treat live media as one or more explicit media subflows on top of the stable peer
- add per-subflow health, diagnostics, and recovery tracking
- keep file upload over media data channel independent from audio/video track transitions
- make semantic channel snapshots report media-subflow readiness separately from control/sync readiness

#### Stage 6: support real media multi-channel behavior

- support multiple simultaneous local sources without peer rebuild
- support explicit policy for which media subflows are direct, relayed, loopback-only, or bounded
- keep multi-channel media observable in operator surfaces without collapsing it into a single boolean `webrtc connected`

### Exit criteria for the next Phase 6 checkpoint

- starting or stopping live media does not tear down control and sync data channels
- closing or reopening a media UI surface does not implicitly rebuild the peer
- direct file upload over media data channel survives unrelated media UI transitions
- whole-peer rebuild becomes an explicit last-resort recovery action rather than a routine media operation
- runtime and operator diagnostics distinguish:
  - peer session health
  - control/sync path health
  - per-media-subflow health
- the architecture is ready for true media multi-channel expansion without introducing hidden authority conflicts between control, sync, and media

### Implementation plan by code area

This section turns the target architecture into an implementation backlog tied
to the current codebase.

#### Browser transport runtime: `webrtc-transport.service.ts`

Current role:

- owns peer creation and teardown
- owns data-channel wiring
- owns media upload data channel
- currently equates `negotiate()` with "close old peer and build a new one"

Current coupling to remove:

- `negotiate()` begins with unconditional `close()`
- media start/stop depends on full peer rebuild
- media upload session is tied to whole-peer lifetime rather than a narrower channel/session scope

Planned refactor:

- split lifecycle entry points into explicit operations:
  - `ensurePeer(sendCommand)`
  - `renegotiatePeer(reason, options)`
  - `closePeer(reason)`
  - `restartIceTransport()`
- preserve an existing peer when renegotiation is only updating media shape
- add explicit negotiation state tracking:
  - peer session id
  - negotiation id
  - negotiation in-flight flag / mutex
  - last negotiated media shape
- add explicit media sender/transceiver registry:
  - audio sender/transceiver
  - video sender/transceiver
  - later screen-share sender/transceiver
- change live media control from "replace peer" to:
  - acquire local track
  - attach or replace track on an existing sender/transceiver
  - request serialized renegotiation only if SDP shape changed
- keep media upload over the `media` data channel independent from live track enable/disable as much as possible

Expected code changes:

- replace the current unconditional `this.close()` path in `negotiate()`
- introduce internal helpers for peer bootstrap vs peer update
- introduce a transport snapshot that distinguishes:
  - peer state
  - negotiation state
  - media-subflow state
- ensure pending media upload is failed only when the media data channel or peer actually becomes unusable, not merely because UI toggled a live preview

Minimum acceptance tests:

- direct command/events data channel stays open while starting camera loopback
- direct Yjs data channel stays open while stopping microphone loopback
- file upload can continue across unrelated media preview UI detach/reattach

#### Browser semantic orchestration: `hub-member-channels.service.ts`

Current role:

- owns semantic path selection and direct-path recovery policy
- owns initial direct negotiation and direct recovery
- currently triggers full renegotiation for media start/stop

Current coupling to remove:

- `startMediaLoopback()` calls full `rtc.negotiate(...)`
- `stopMediaLoopback()` calls full `rtc.negotiate(...)`
- direct recovery and media lifecycle both escalate too quickly to whole-peer rebuild semantics

Planned refactor:

- keep semantic ownership of policy, but narrow the commands it sends to the transport runtime
- replace "start/stop media loopback => full renegotiate" with explicit media-intent calls:
  - `ensureLiveMediaSubflow(...)`
  - `disableLiveMediaSubflow(...)`
  - `refreshMediaNegotiation(...)`
- separate recovery ladders:
  - peer recovery ladder
  - live-media recovery ladder
  - media-upload recovery ladder
- expose media-subflow evidence in the semantic snapshot, for example:
  - `live_audio`
  - `live_video`
  - `media_upload`
  - later `screen_share`
- keep control/sync path selection authority unchanged unless peer-level health actually degrades

Expected code changes:

- `prepareDirectPaths()` should create or validate a stable peer session, not create an assumption that every future media action will rebuild it
- `startMediaLoopback()` should become a policy method that requests the live media subflow rather than a whole-peer rebuild
- `stopMediaLoopback()` should disable the relevant subflow and only renegotiate the affected media shape if necessary
- direct recovery should prefer:
  - `ICE restart`
  - targeted peer renegotiation
  - full peer rebuild last

Minimum acceptance tests:

- semantic `command` and `sync` active paths remain unchanged while live media starts and stops
- `media` semantic state can degrade independently without forcing `command` and `sync` to fallback
- visibility changes or modal/widget lifecycle do not trigger whole-peer rebuild unless actual peer health requires it

#### Router authority for response and media routing

Current gap:

- media path choice and skill-response path choice are still too fragmented across local helpers
- transport layers know too much about fallback intent
- there is no single semantic owner of "need -> capability -> ability -> attempt -> degradation -> observed failure"

Target role:

- router should become the semantic administrator of response routing for skills and scenarios
- router should also become the semantic administrator of browser-visible media route choice
- transport implementations remain executors of the chosen route, not owners of route semantics

Current foundation in code:

- `resolve_media_route_intent(...)` defines one normalized route-administration contract for media needs
- reliability and media runtime snapshots already expose that contract through the shared vocabulary:
  - `need`
  - `capability`
  - `ability`
  - `attempt`
  - `degradation`
  - `observed failure`
  - `monitoring`
- `RouterService` now projects this router-owned view into `data.media.route` so browser surfaces can observe the chosen route without inferring it from transport internals
- the router now also advances an explicit media-route `attempt` contract with `sequence`, `switch_total`, `previous_route`, `previous_member_id`, `last_switch_at`, `observed_failure`, and refresh cause whenever the chosen topology or member target changes
- `browser <-> member` direct media is represented as a capability foundation only until per-member capability inventory and signaling rendezvous are implemented

Route-administration state to make explicit:

- need:
  - what the caller is trying to receive or deliver
- capability:
  - which targets advertise they can satisfy that need
- ability:
  - whether those targets are currently reachable, authorized, and healthy enough
- attempt:
  - which target/path is currently active or in-flight
- degradation:
  - which fallback class is allowed
- observed failure:
  - which concrete incident happened on the current route
- monitoring:
  - which signals determine recovery, failover, or operator-visible degraded mode

Implication for implementation:

- route selection for skill/scenario response delivery should move toward router-owned semantics
- media path selection should reuse the same route-administration vocabulary
- direct `browser <-> member` media should be introduced as a new routed capability, not as a transport-only shortcut

#### Browser sync/runtime integration: `ydoc.service.ts` and media widgets

Current role:

- `ydoc.service.ts` owns sync provider recreation and webspace switching
- media widgets subscribe to semantic channel snapshots and currently may stop loopback on component destroy

Current coupling to remove:

- media widget/component destroy currently implies live media shutdown in some paths
- UI attachment is too close to session ownership

Planned refactor:

- move live media session ownership fully into service state
- keep widgets as observers/controllers, not owners of peer lifetime
- make component unmount close only the local UI binding by default
- require explicit user or policy action to stop live media capture

Expected code changes:

- remove implicit live-media stop from widget destroy paths
- add explicit media session controller APIs for:
  - attach local preview
  - attach remote preview
  - detach preview
  - stop live media session
- keep webspace/sync resync logic isolated from peer/media state except where browser reload naturally resets everything

Minimum acceptance tests:

- opening and closing a media modal does not interrupt an ongoing direct upload
- destroying a media component does not drop the peer unless the page itself unloads

#### Hub WebRTC peer runtime: `services/webrtc/peer.py`

Current role:

- owns hub-side peer instances keyed by `device_id`
- currently replaces the peer unconditionally on every fresh `rtc.offer`
- loops received live tracks back to the browser

Current coupling to remove:

- "new offer => replace existing peer" is used as a safety shortcut
- this makes browser-side renegotiation indistinguishable from full peer replacement

Planned refactor:

- introduce explicit peer-session identity on the signaling path
- keep one active peer session per browser member unless a new session generation is explicitly declared
- allow in-session offer handling for renegotiation on the existing peer
- support explicit supersede only when:
  - protocol/session epoch changed
  - existing peer is failed/closed/unrecoverable
  - operator/debug policy explicitly requests reset
- keep loopback sender/transceiver bookkeeping scoped per subflow instead of relying on full peer replacement cleanup

Expected code changes:

- extend signaling payloads with stable peer session identity and negotiation identity
- teach `handle_rtc_offer(...)` to distinguish:
  - renegotiation for existing peer session
  - replacement of an old peer session
- maintain stronger diagnostics for:
  - active peer session id
  - last negotiation id
  - supersede reason
  - active loopback tracks by subflow

Minimum acceptance tests:

- a renegotiation offer for the current peer session does not close the existing data channels on the hub side
- stale or superseded answers/offers are ignored safely
- repeated start/stop of live media does not accumulate duplicate loopback senders or invalid SDP state

#### Signaling contract and protocol changes

The browser-hub signaling contract will need a small explicit upgrade.

Additions to the signaling model:

- `peer_session_id`
- `negotiation_id`
- optional `media_shape` or equivalent declarative summary of intended live media subflows
- explicit supersede/reset reason when full peer replacement is required

Protocol rules:

- one `peer_session_id` identifies the long-lived direct session
- many `negotiation_id` values may exist within one peer session
- stale answers or ICE for an unknown/superseded session are ignored
- full replacement is explicit, not inferred from the existence of a new offer alone

For `browser <-> member` direct media, the signaling contract will also need:

- target runtime identity, for example `target_node_id`
- explicit media-producer identity, for example `media_session_id` or producer id
- router-visible route intent so signaling can distinguish:
  - browser-hub media
  - browser-member media
  - bounded relay fallback

#### Recommended delivery order

1. browser UI/session ownership cleanup
2. browser transport split between peer shutdown and renegotiation
3. browser semantic-channel API split between peer and media-subflow control
4. router semantic contract for response/media route administration
5. signaling contract upgrade with `peer_session_id`, `negotiation_id`, and target route identity
6. hub-side in-session renegotiation support
7. transceiver-based live media control
8. explicit `browser <-> member` direct media path via hub/root-mediated signaling
9. media-subflow observability and later multi-source expansion

#### Definition of done for the refactor tranche

- direct peer session is long-lived across ordinary media start/stop actions
- semantic channel routing no longer treats media toggles as peer replacement events
- hub no longer assumes every offer means "new peer instance"
- operator surfaces can tell whether an incident is:
  - peer-session failure
  - negotiation failure
  - live-media-subflow failure
  - media-upload-subflow failure
  - route-administration failure such as capability mismatch, policy denial, or producer unavailability

### Work items

- [x] `[must]` Define media signaling authority for the current hub-loopback
  validation scope.
- [x] `[must]` Define direct vs relay policy for bounded file media and current
  A/V validation.
- [x] `[must]` Keep media readiness outside core control readiness.
- [x] `[must]` Advertise slot-aware direct hub media candidates for endpoint
  content commands, with root inline relay kept only as bounded fallback.
- [x] `[must]` Validate the first endpoint audio-in content artifact from SDK
  and diagnostics instead of treating a successful command as proof that audio
  bytes are usable.
- [ ] `[must]` Validate browser-member direct media admission and signaling
  beyond the current hub-loopback route.
- [ ] `[must]` Promote ReDevice media from command-scoped `local_http`
  candidates to a router-owned direct session contract once live LAN/WebRTC
  evidence is stable.
- [ ] `[must]` Define sidecar continuity requirements for live media during hub
  runtime restart before allowing orchestration to rely on it.
- [ ] `[should]` Add soak evidence for peer rebuild, ICE restart, full
  renegotiation, and media route downgrade paths.
- [ ] `[could]` Expand from bounded hub loopback/file media into general
  multi-party or multi-source media behavior.

### Exit criteria

- [x] `[must]` Media path is architecturally isolated from control and sync
  hardening.
- [x] `[must]` Bounded relay upload/playback works through root on a live hub.
- [x] `[must]` Direct WebRTC audio/video loopback can be validated end-to-end
  on a live hub.
- [x] `[must]` Operator UI exposes media runtime, relay state, and live
  loopback status clearly enough for incident/debug use.
- [x] `[must]` Revoked or superseded ReDevice admission rows do not become
  independent current endpoint identities in SDK projections.
- [ ] `[must]` A live media session has an explicit update/restart policy:
  defer member update, preserve or reject hub runtime restart based on real
  sidecar continuity evidence.

Local bounded checkpoint: runtime-served HTTP/range media and direct WebRTC
media DataChannel downloads now have a path-free in-memory delivery lease.
Supervisor defers both hub and member transitions
while such a stream is active, and diagnostics expose aggregate stream/media
kind counts. The broader checkbox remains open because sidecar-owned media,
browser-member direct sessions, and live stand continuity still require their
own admission and evidence.

## Phase 7: Skills and scenarios lifecycle hardening

### Focus

Handle artifact provenance, scenario UX, and runtime lifecycle after the communication model is hardened.

### Work items

- [ ] `[must]` Define a first-class artifact model for system skills and
  scenarios.
- [ ] `[must]` Separate `source sync`, `runtime refresh`, and `A/B rollout` as
  distinct lifecycle operations.
- [ ] `[must]` Add change classification for skill updates so the system can
  decide whether `runtime_update` is enough.
- [ ] `[must]` Make `desktop.scenario.set` transactional and observable with
  `requested`, `effective`, and `error` state.
- [ ] `[must]` Define explicit ownership for Yjs subtrees such as `ui`, `data`,
  `registry`, and desktop-installed artifacts.
- [ ] `[should]` Surface artifact provenance in diagnostics: `workspace`,
  `repo_workspace`, `runtime_slot`, `built_in_seed`, and `dev`.
- [ ] `[should]` Preserve scenario install/open state through reload and
  rebuild of current Yjs projection.

### Candidate code areas

- `src/adaos/services/skill/manager.py`
- `src/adaos/services/skill/update.py`
- `src/adaos/services/scenario/manager.py`
- `src/adaos/services/scenarios/loader.py`
- `src/adaos/services/scenario/webspace_runtime.py`
- `src/adaos/services/skills_loader_importlib.py`
- `src/adaos/services/yjs/bootstrap.py`
- `src/adaos/services/yjs/gateway_ws.py`
- `src/adaos/integrations/adaos-client/src/app/runtime/desktop-schema.service.ts`

### Exit criteria

- [ ] `[must]` Operator can explain how a skill or scenario update propagates
  without reading the code.
- [ ] `[must]` Scenario switch result is observable and not inferred from UI
  side effects.
- [ ] `[must]` Remote hubs behave consistently even when local workspace assets
  are absent.
- [ ] `[should]` Installed desktop items and current scenario recover correctly
  after Yjs rebuild or reconnect.

## Immediate implementation order

The next coding steps should follow this order:

1. [x] `[must]` Inventory existing hub-root subjects and messages by taxonomy
   and delivery class.
2. [x] `[must]` Isolate route backlog from control backlog in real runtime
   resources.
3. [x] `[must]` Define Class A hub-root flows and add outbox, inbox, and
   idempotency where required.
4. [x] `[must]` Separate root-control and route/session incidents in readiness
   and diagnostics.
5. [x] `[must]` Reconcile sidecar rollout/config and capture target-stand
   acceptance for local `/ws` / `/yws` transport-only handoff.
6. [ ] `[must]` Surface WebRTC/Yjs server-side opt-out state in diagnostics
   and preflight checks so `ADAOS_WEBRTC_YJS_CHANNEL_ENABLED=0` cannot masquerade
   as generic `first_sync_timeout` or browser cooldown.
7. [ ] `[must]` Stabilize hub-root sidecar NATS session authority so root sees
   one stable hub session across runtime NATS-client replacement.
8. [ ] `[must]` Validate browser channel survival across A/B runtime switch
   with sidecar enabled and runtime fallback treated as fallback, not success.
9. [ ] `[should]` Harden supervisor warm-switch authority handoff and recovery
   soak.
10. [ ] `[should]` Validate hub-member, Yjs, and media semantics under the same
   reconnect/A-B/load scenarios.
11. [ ] `[must]` After communication acceptance, harden skills and scenarios
   lifecycle and scenario UX.

## Non-goals for the first iteration

- universal transport abstraction for every edge case
- production-grade media relay
- infinite replay of all traffic
- exactly-once delivery for every message
- replacing all fallback paths before provenance and lifecycle are formalized

The first iteration is about explicit guarantees, provenance, and operational clarity, not maximum theoretical reliability.
