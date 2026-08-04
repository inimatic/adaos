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
commits through `b8b4c68` contain the topology-confirmed startup room,
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
