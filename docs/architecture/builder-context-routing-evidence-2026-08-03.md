# Builder Context Routing Evidence — 2026-08-03

## Decision

Builder conversation state is a two-level focus, not a global current
Webspace:

1. `BuilderContextRef` selects one Webspace in which the Builder scenario is
   active and binds it to that host's explicit Preview relation.
2. Project focus is stored inside the selected Builder context.

For a normal Builder host the relation is `builder host -> development
Preview`. When Builder itself is developed, the bounded self-host relation is
`development Builder host -> its child Preview`. Webspace names and `-dev`
suffixes are never parsed to infer this topology.

Web turns resolve the Builder context from the current surface and registered
relation. Telegram turns persist the selected Builder host per `chat_id` and
`thread_id`; the shared skill conversation id is not used as a user-focus key.
Changing the project does not change the selected Builder host.

## Contract

`adaos.builder.context_ref.v1` contains:

- Builder Webspace identity, title, kind, source mode, and active scenario;
- Preview Webspace and relation identity/generation;
- readiness status, selection availability, and a stable failure reason.

Discovery is read-only. It does not call the mutating workbench binding getter,
create a relation, or provision a Preview. A stale binding such as legacy
`default -> default-dev` is excluded when Builder is not active in `default`.
Unavailable active Builder surfaces remain observable but are not offered as
actions.

## Interaction

When Telegram has no valid Builder focus:

1. Builder returns `builder_context_required`.
2. A capability-negotiated single-choice interaction offers ready Builder
   hosts; text fallback remains semantically equivalent.
3. `builder.context.select` validates the selected context again and stores it
   for the originating Telegram chat/thread.
4. Builder then presents projects within that host.
5. Preview links use that host's registered Preview Webspace.

Web calls keep compatibility for authorized synthetic API/test surfaces, but
runtime browser surfaces use exact topology resolution.

## Completed checks

- [x] `[must]` Add and validate `adaos.builder.context_ref.v1`.
- [x] `[must]` Discover active Builder hosts without topology mutation.
- [x] `[must]` Exclude the legacy non-Builder `default` binding.
- [x] `[must]` Remove suffix-based Builder host inference from `builder_skill`.
- [x] `[must]` Separate Telegram Builder focus from project focus.
- [x] `[must]` Scope Telegram focus by chat/thread, not the shared skill
  conversation id.
- [x] `[must]` Generate Preview links from the selected Builder relation.
- [x] `[must]` Make confirmed client Webspace navigation canonical across the
  intent reload and fail closed when the runtime rejects the switch.
- [x] `[must]` Treat a Preview deep link as one destination: prepare its
  expected scenario before reloading into the target Webspace, instead of
  materializing the persisted home scenario and asking for a second switch.
- [x] `[must]` Keep compatibility with an older runtime whose
  `desktop.webspace.use` acknowledgement does not prove live-room scenario
  preparation; complete `desktop.scenario.set` before browser reload.
- [x] `[must]` Make the runtime `desktop.webspace.use` transition update the
  live room through the canonical scenario-switch transaction and return the
  prepared `scenario_id` as evidence.
- [x] `[must]` Run Builder workbench/SDK/ABI tests and the complete Builder
  skill test suite.
- [x] `[must]` Run a local DEV runtime smoke: choose `dev1`, then observe
  `Builder: dev1 -> Preview dev1-dev`, current project `test04_recipes`, and
  project-choice controls.
- [x] `[must]` Publish the validated skill locally to Workspace version
  `0.3.32`, activate slot `B`, and run a Workspace Telegram-route smoke that
  returns `builder_context_required` with `builder.context.select` controls.
- [x] `[must]` Bind the local API/Telegram runtime to the current checkout;
  the previous core slot `0.1.604` predated the Builder host-discovery ABI.
- [x] `[must]` Distinguish an empty Builder inventory from an unavailable or
  incompatible discovery runtime; a discovery failure can no longer be
  reported as "no active Builder".
- [x] `[must]` Human Telegram acceptance with the real conversation buttons.
- [ ] `[must]` Human browser acceptance of a generated Preview link after the
  updated client is deployed.
- [x] `[must]` Automated local browser acceptance with subnet-scoped current
  Webspace `dev1-dev` and scenario `builder`: the raw intent without a
  canonical `webspace` hint remained in `dev1-dev`, presented only the real
  scenario mismatch, switched once to `test04_recipes`, and cleared the
  overlay after materialization.
- [x] `[must]` Repeat acceptance from a new tab with the unmodified raw intent
  and no legacy `webspace` query: after zone/subnet confirmation, startup
  enters `dev1-dev`, runs one scenario preparation, renders a bounded
  `Preparing requested preview` state, and then renders `test04_recipes`.
  Builder and both mismatch overlays are absent from the trace.
- [x] `[must]` Reproduce and diagnose Yjs amplification: one 296,101-byte
  update was sent 170 times because cancelled WebRTC adapters remained in the
  room client list.
- [x] `[must]` Add cancellation-safe adapter cleanup, failed-client pruning,
  page-scoped peer identity, and authoritative scenario-selector repair; pass
  the focused core suites.
- [x] `[must]` Activate the core transport hardening and verify on the routed
  runtime that one update is delivered only to currently live page peers,
  with no memory-pressure restart, 1006/1012 cascade, or induced relay fallback.
- [x] `[must]` Keep repository `api serve` and `api restart` on the source
  checkout rather than silently selecting an older core slot.
- [x] `[must]` Preserve an already connected WebRTC peer while its
  DataChannels finish opening, with a bounded grace deadline and deterministic
  listener cleanup on success, timeout, cancellation, and close.
- [x] `[must]` Collapse the initial Yjs handshake to one authoritative
  `SYNC_STEP1 -> SYNC_STEP2` exchange. Do not add an eager full-state replay or
  replay again when the server-authoritative gateway rejects the browser's
  initial state; retain explicit full replay only for malformed/preflight
  recovery.
- [x] `[must]` Reopen the same exact target after it is current: no Webspace or
  scenario command is repeated and no navigation overlay is rendered.
- [x] `[must]` Bound startup navigation when the first control WebSocket cannot
  open. A pre-command transport failure becomes observable and resumes once on
  the next confirmed control-session open; a command that was already attempted
  is never replayed. Client `0.0.266` covers this with the complete 125/125 YDoc
  suite.
- [x] `[must]` Relay public immutable browser assets through the routed hub
  boundary. The exact `en.json` blob exists and returns locally; Root previously
  returned `404` because it routed only `/api/*`. Backend commit `f2b018d`
  admits only validated content-addressed `GET/HEAD` paths and passes 19/19
  backend tests.
- [ ] `[should]` Reduce full-page cold-start time for links opened in a new tab.
  This is client boot/transport restoration, not Preview switching. Navigation
  of an already materialized target is now a deterministic no-op.
- [ ] `[should]` Add lifecycle management for stale smoke Builder Webspaces so
  the selection surface does not accumulate inactive test hosts.

## Local activation and publication status

`builder_skill` version `0.3.41` is validated, pushed, and activated in the
local DEV runtime. The hardened content is locally published as Workspace
version `0.3.34`. Its core compatibility floor is the explicit
Builder-context ABI commit `e4f794b8` (`0.1.660+4316`). The local API reports
source build `0.1.667+4391.89ec14a3`, not a core slot, and an HTTP
`/api/tools/call` smoke discovers both
`desktop` and `dev1` with a button presentation plan. The registry publication
commits are local and were not pushed. The AdaOS core commits are also local.
Only the client repository was pushed remotely in this iteration. Client
commits through `ade734a` contain the topology-confirmed startup room,
single-command scenario preparation, and stale-render hold; 271/271 client
navigation tests, the focused 10/10 WebRTC transport suite, the production
build, and the exact unmodified local raw-link route passed. The release path
also keeps component and conversational manifest
versions atomic and rejects drift during validation.

The live transport proof used two controlled transitions in `dev1-dev`:
`test04_recipes -> builder -> test04_recipes`. Each transition produced one
Yjs update and one send per live room client, without a YWS reconnect, peer
replacement, runtime restart, or post-transition load. Exact session telemetry
reduced the apparent `client_count=14` to five live YWS page sessions, each with
one `bs_*` identity and one attempt id. Separate tabs on the same device used
separate `rp_*` WebRTC peer ids. Client `0.0.265` additionally keeps a connected
peer through a bounded DataChannel-opening grace period and removes all wait
listeners on every outcome.

The 2026-08-05 delayed-link trace separates the remaining startup phases. Once
`desktop.scenario.set` reached the runtime, `builder -> test04_recipes`
materialized in 2.3-2.5 seconds. The hundreds-of-seconds delay happened before
that send: a failed initial control open was followed by successful Yjs startup,
leaving a URL-derived synthetic preparation state with no continuation. Client
`0.0.266` makes this phase resumable exactly once before command execution.
Cancelled short status probes are diagnostics of the same unavailable startup
window, not evidence of a long-running scenario transition.

Production acceptance on 2026-08-05 deployed backend commits `f2b018d` and
`44a237a` through successful Infra runs `30979052485` and `30979428815`.
The exact routed dictionary returns `200 application/json`, 9,657 bytes, and a
byte SHA-256 equal to the URL digest; forged shards return `404` and `POST`
returns `405`. The final response is classified as `asset` and carries
`public, max-age=31536000, immutable`. Client run `30979460239` deployed
`0.0.266+ade734a`.

The deploy trace also exposed an independent reconnect boundary. A root
cutover can terminate the hub's NATS route while live WebSocket tunnels are
open. Sequential unbounded `ws.close()` calls kept the bridge inside
`finalizing` and prevented its retry loop from running. Core commit `0e836930`
closes all route tunnels concurrently behind one-second per-operation bounds.
The complete NATS routing suite passes 74/74. With the corrected source runtime
and 8 WS / 7 YWS sessions observed, a controlled reconnect moved from
`finalizing` to `bridge connected` in 3.9 seconds and restored the public route
in 9.2 seconds.

The remaining same-peer `3x` payload was separately traced to protocol
redundancy, not leaked clients: the gateway sent an eager effective-state
replay, answered the client's `SYNC_STEP1`, and replayed once more after
rejecting the browser's initial `SYNC_STEP2`. Build `cd36f88c` removes both
redundant replays. A normal admission now sends the authoritative full state
once; recovery replay is reserved for a malformed/preflight path. The focused
gateway test exercises this invariant, and the post-restart YWS log contains
the expected ignored browser-state evidence without an eager effective-state
replay.

The final controlled restart loaded `89ec14a3` from the checkout. Runtime
telemetry then reported three connected WebRTC peers with three open Yjs
DataChannels, six exact YWS page sessions with one attempt each, and
`storm_detected=false`. Each WebRTC peer received one initial chunked document
message. The selected `dev1-dev` room reported
`effective_initial_replay_total=0` and
`effective_initial_replay_dedupe_total=6`; the log contained no peer
replacement, DataChannel timeout, unexpected 1006, or eager full-state replay.

The 2026-08-05 follow-up measured the transition independently from browser
startup. `desktop.scenario.set` changed `dev1-dev` from `builder` to
`test04_recipes` in 838 ms (784 ms of semantic rebuild). The renderer already
held the exact authoritative target while the navigation overlay continued to
wait for the live Yjs provider. Client `0.0.267` accepts that rendered target
only when the one-shot startup preparation is `prepared` and Webspace/scenario
identity is exact; transport quality remains visible as a separate status and
cannot repeat the mutation.

That acceptance is deliberately scoped to navigation completion. It does not
detach the page from the provider or promote the render snapshot into an
independent state store. Browser pages and devices that resolve the same
`webspace_id` still acquire one shared Yjs room; page/device identities only
distinguish transport connections. A regression test now asserts both the
shared-room property and that an inactive startup preparation cannot use a
stale render snapshot in place of live Yjs state.

The same source-runtime restart loaded the page-peer and cancelled-adapter
fixes that the preceding process had not executed. The old process repeatedly
sent the same 298,207-byte state to historical adapters. After restart each
live peer received one initial `SYNC_STEP2`; the reliability summary later
reported three connected peers, three open Yjs channels, fresh state and idle
pressure. Peer telemetry now carries the page `browser_session_id` and client
build beside `peer_id`, which allows a `dc_open_timeout` to be attributed to
the actual tab instead of an aggregate from other tabs.

One performance issue remains deliberately separate: changing projections in
`dev1` can still emit approximately 375 KiB `sync_update` messages even when
their digests differ and no retry occurs. This is whole-document projection
granularity, not the fixed identical-payload amplification. Structural Yjs
history compaction also needs a transactional design; no live room was reset
during this investigation.

The next same-page raw-link trace exposed two startup-order defects rather than
a scenario-switch defect. For one browser session the new document first
registered and negotiated RTC in persisted Webspace `desktop`, then repeated
the sequence in destination `dev1-dev`. In addition, YDoc initialization
awaited direct transport before it created the YWS provider. This made
authoritative state appear fresh at the server while the page remained
`local-doc=unsynced:degraded` for the duration of ICE/DataChannel setup.

Client startup now treats a same-zone and same-subnet `webspace.open` target as
the initial room at the earliest AdaOS registration boundary. The subnet must
be proven by the restored authorization hint or by the already routed
`/hubs/<subnet>` base; a destination for another topology cannot override the
stored room. Synchronization is relay-first: YWS attaches immediately, direct
preparation runs asynchronously, and the existing isolated candidate probe
promotes to WebRTC only after sync and stability. A failed or slow direct path
therefore remains a quality downgrade, not a state-availability blocker.

Entering a URL in the address bar still performs a full browser document
navigation and necessarily destroys that page's old JavaScript/WebRTC peer.
The invariant is that this page creates one replacement peer in the correct
room, not that a browser process preserves a peer across document replacement.
Other pages and devices in `dev1-dev` remain attached throughout, and the
replacement page joins their one shared Yjs room. Focused regression suites
pass 126/126 for YDoc and 29/29 for the early AdaOS route.

Client commits `f03d1bc` and `2a6d09f` were pushed to `main`; CI advanced the
package to `0.0.269`. Firebase Hosting run `30988292760` completed successfully,
and public `https://inimatic.com/version.json` reports
`0.0.269+2a6d09f`.

The next exact-target trace showed that the remaining six-second
`Checking destination` interval was not a scenario transition. The runtime
received `desktop.scenario.set` only after about 4.8 seconds of control setup
and rejected it as `already_current_ready` in 13.8 ms; YWS and direct RTC then
continued independently. Client commit `f9e9b96` adds a bounded read-only
materialization proof in parallel with control connection. It accepts only the
exact ready/non-degraded Webspace and scenario and applies that authoritative
render snapshot; otherwise it preserves the former one-command transition.
Focused exact/mismatch/unavailable cases and the complete App/YDoc navigation
matrix pass 249/249, and the production bundle succeeds.

Client commit `734023b` was first pushed to `main`; CI advanced the package to
`0.0.264` at `081be46`. The follow-up DataChannel fix and version bump were
pushed through `b8b4c68`. Firebase Hosting run `30940038325` and infra
notification run `30940054591` completed successfully. Public
`https://inimatic.com/version.json` reports `0.0.265+b8b4c68`. The AdaOS core
transport commits remain local, as requested; the routed proof above runs the
source checkout directly rather than an obsolete core slot.

The final DEV draft receipt is Forge commit `2177a2129e8587a1a881213d97feeedbf8c50f4d`
and the Workspace registry commit is
`fb866846664fe197a433dc73d86f8082af2896ee`. Firebase Hosting run
`30915825983`, client Notify Infra run `30915825873`, and infra Build and
Deploy run `30915835673` all completed successfully. CI then advanced the
client package version to `0.0.263` at `5e08451` without changing the routing
implementation.
