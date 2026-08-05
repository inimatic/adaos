# Navigation Intent And Location Architecture

Status: target contract with the same-Webspace navigation correction validated
locally on 2026-08-04.

This document defines cross-zone, cross-subnet, cross-Webspace navigation for
AdaOS. It covers links produced by AdaOS Connect, Builder, notifications, chat
controls, and future skills. It does not grant access and does not replace the
authorization or workflow models.

## Decision

AdaOS uses a canonical `intent`, not a UI `mode`, to describe why a link was
created. The first version supports:

- `connect.register` — continue registration from a one-time code;
- `auth.login` — establish a session in a declared zone/subnet context;
- `webspace.open` — resolve and open an exact synchronized Webspace context.

`mode=login` and `mode=registration` are not a second compatibility language.
Code under AdaOS control must emit and consume `intent`. Presentation state
such as which login tab is visible remains a private client concern.

The normative input is `adaos.navigation.destination.v1`; the explainable,
non-mutating decision is `adaos.navigation.resolution.v1`. Their schemas live
under `src/adaos/abi/`, while `adaos.sdk.navigation` owns validation, URL
construction, parsing, runtime-scope discovery, and pure resolution. Skills do
not assemble these query strings themselves.

## Ownership

The dependency direction is:

```text
AdaOS Connect / Builder / another skill
  -> adaos.sdk.navigation
     -> NavigationDestination ABI
        -> client resolver and presentation adapter
           -> existing auth, subnet, Webspace, and scenario commands
```

- Core and SDK own meaning, validation, ordering, and reason codes.
- AdaOS Connect is a thin registration/invite producer. It may acquire a code,
  but delegates destination construction to the SDK.
- Builder adds its selected `proto:` / `active:` / `public:` materialization
  expectations, then delegates link construction to the same SDK.
- The Web client is an adapter. It observes current state, renders the proposed
  transition, and invokes existing commands only after the required user
  decision.
- Telegram and other channels carry the same URL or semantic action. They do
  not invent transport-specific topology.

## Destination Contract

`webspace.open` requires:

- `zone`;
- `subnet_id`;
- opaque `webspace_id`;
- `space_kind`: `workspace`, `development`, `preview`, or `trial`.

It may also declare `expected_scenario_id`, `expected_revision`, and
`preview_stage`. Builder uses `space_kind=development` for its nested DEV
Preview: Preview is a role of that Webspace in Builder, while `source_mode=dev`
is its authoritative artifact boundary.

No consumer may derive topology from a `-dev` suffix. Webspace ids are opaque;
the authoritative Webspace registry supplies `kind` and `source_mode`.

The destination is an expectation, not an authority token. The client still
has to authenticate, pass authorization, connect to the requested subnet,
obtain a complete scenario-consistent materialization, and verify it. Live Yjs
transport freshness remains part of runtime availability, but is not a reason
to repeat or indefinitely delay a destination that has already materialized.

## Ordered Resolution

`webspace.open` is resolved in this order:

1. Compare zone and propose an explicit zone switch when it differs.
2. Establish authentication in that zone while retaining the destination.
3. Compare `subnet_id` and propose an explicit subnet switch.
4. Compare `webspace_id` and propose an explicit Webspace switch.
5. Verify the authoritative Webspace source boundary.
6. Wait until the target Webspace has a complete, scenario-consistent
   materialization. A reconnecting transport alone does not invalidate an
   already complete materialization.
7. Compare the expected scenario and, when observable, revision.
8. Open the destination or explain the remaining mismatch.

The resolver itself is pure. Its output is `ready`, `input_required`,
`waiting`, or `blocked`, with an action, stable reason, current context, and
choices. `input_required` never silently changes zone, subnet, Webspace, or
scenario. A copied link therefore cannot mutate workflow state merely because
it was opened.

`webspace_id` inside `webspace.open` is a conditional startup destination. The
Yjs client must not enter that room while zone/subnet resolution is pending.
Once the selected zone and authenticated/stored subnet match the destination,
however, the target Webspace becomes the startup room before device
registration or Yjs attachment. A canonical `webspace` query is not required
and must not be duplicated into Builder-generated ingress links. Deferring the
intent target until the overlay layer would first boot `desktop` or a persisted
home scenario and then perform an unnecessary second transition. If topology
is not yet confirmed, the client resumes its current location only for the
zone/subnet handoff and enters the destination after that decision.

When `expected_scenario_id` is present on an already confirmed subnet, startup
first performs a bounded read-only materialization preflight in parallel with
control setup. The preflight is sufficient only when the server reports the
exact Webspace and scenario, `ready` and non-degraded materialization, and a
complete renderable snapshot. That proof closes destination resolution without
issuing `desktop.scenario.set`; direct transport promotion remains independent.
If any identity, readiness, or snapshot check is absent or differs, startup
falls through to exactly one idempotent `desktop.scenario.set` before joining
the Yjs room. Until either proof has prepared a scenario-consistent
materialization, the renderer shows a bounded preparation state and must not
expose the persisted/home scenario. Failure releases the hold into the normal
explainable resolver; it does not retry the state-changing command.

Control transport establishment and scenario preparation are separate
protocol steps. A successful read-only exact-target proof does not require a
control WebSocket and is not recorded as a mutation attempt. If that proof is
unavailable or inconclusive and the initial control WebSocket cannot open, no
scenario command has been attempted: startup records an observable
`control_transport_unavailable` outcome and installs one one-shot continuation
for the next confirmed control-session open. The continuation unregisters
before it sends the command and first verifies that the URL still names the
same Webspace and scenario. If a scenario command was already attempted, its
failed or unknown outcome is never repaired by transport reconnect. This rule
prevents both an indefinitely synthetic `Checking destination` state and an
unsafe replay of a state-changing command.

If authentication is absent, the client presents login but preserves the
complete `webspace.open` destination. Successful login continues resolution;
it does not discard the requested subnet/Webspace. One-time registration
secrets are removed after consumption and must not become durable location
state.

## Builder Preview Link

Builder obtains the authoritative relation from
`adaos.sdk.builder.preview.get_binding()` and builds the link with
`adaos.sdk.builder.preview.navigation_link()`. The link includes:

- runtime zone and subnet identity;
- related Preview Webspace id;
- development source boundary;
- selected scenario;
- selected Prototype/Automation/Publication revision and stage when known.

Opening it from another subnet first explains the mismatch. A user may switch
to the referenced subnet, cancel, or keep the current context. The link does
not assume that the Telegram-bound subnet is also the browser's active subnet.

Builder also reports the selected Builder host and its related Preview
Webspace in project/current/Preview-link responses. Telegram stores a selected
Builder host per chat/thread. Within it, selecting a Project changes the
Builder working context; it does not change the already open Preview target.
`Show prototype`, `Show implementation`, or `Show publication` selects that
target. `Preview link` then opens the stored target in the related Preview
Webspace and may request an explicit scenario switch in the browser, without
changing the Builder Project or workflow state.

## Address Bar And Browser History

Navigation intent and current location are different lifetimes:

- `intent` is an ingress instruction that may require several transitions;
- canonical location is the resolved, shareable state currently displayed;
- authentication codes and other one-time inputs are transient secrets.

The target client model is a versioned `NavigationLocation` projection. Once a
destination is resolved, the client should replace the ingress URL with a
canonical share URL containing only non-secret location identity. Semantic
user navigation should use `history.pushState`; hydration, secret removal,
alias normalization, and synchronization corrections should use
`history.replaceState`. `popstate` must enter the same resolver and must not
directly replay a workflow command.

The browser Back button navigates views and contexts; it does not undo a
published Change, Trial, scenario switch command, or another business effect.
If a historical context no longer exists or access changed, the resolver
shows an explainable unavailable state. “Copy link” must build from the current
resolved `NavigationLocation`, never copy an unresolved URL or a URL carrying
`user_code`/`pair_code`.

The first implementation keeps the complete `webspace.open` destination in
the address bar while it is being resolved and removes transient auth intents
after use. Full `NavigationLocation`, `pushState`/`popstate`, and canonical
share-link materialization are tracked as follow-up work rather than being
mixed into the initial cross-topology safety slice.

## Transport Lifecycle Invariants

Opening a navigation target in another tab creates another page-scoped
WebRTC/Yjs peer, not another logical device replacement. The device id remains
stable for identity and authorization; the page peer id is unique for transport
ownership. Closing, replacing, or cancelling a peer must remove its Yjs adapter
from `YRoom.clients` before the binding is forgotten.

A room broadcast is delivered at most once to each live adapter. A failed or
closed adapter is removed immediately and its send failure is isolated from the
room task group. It must not remain as a retry target, and retrying delivery must
not recreate the originating Yjs update. These rules prevent reconnect churn
from multiplying a single materialization update by the number of historical
bindings.

Runtime diagnostics identify a browser transport by the complete
`webspace_id + page peer/session id`, never only by the stable device id. A
device may legitimately own several tabs; each live tab has one attempt and one
adapter. Capacity warnings count those live tabs, while reconnect-storm rules
count repeated attempts by the same page session. This distinction is required
both for enforcement and for trustworthy incident evidence.

The client uses a bounded two-phase DataChannel admission deadline. If ICE or
the peer connection is not connected at the normal deadline, the attempt fails.
If the peer is connected but SCTP/DataChannels are still opening, the client
keeps that peer for one bounded grace period. Every success, timeout, cancel,
and close removes the temporary `open` listeners. A timeout must therefore not
destroy a healthy connected peer or leave callbacks that trigger a later
renegotiation storm.

On a source checkout, `python -m adaos api serve` and `api restart` are
development commands and must keep running that checkout. Core slot selection
is reserved for the installed/global runtime. Otherwise a local restart can
silently execute an older slot and invalidate both page-peer isolation and
adapter-cleanup verification.

## Acceptance Evidence

The 2026-08-03 local slice includes:

- Python SDK round-trip and ordered-resolution tests;
- Builder SDK tests proving full Preview expectations in the URL;
- AdaOS Connect tests proving SDK-owned registration destinations;
- backend TypeScript compilation after removal of legacy `mode` generation;
- client Navigation/App/YDoc regression tests (238/238), including the exact
  `ruhub` to `sn_6acf0c01` same-zone mismatch, and a successful Ionic build;
- localized English/Russian navigation explanations; no new shell-written
  Cyrillic fixtures;
- published `adaos_connect@0.16.5`, DEV `builder_skill@0.3.36`, and Workspace
  `builder_skill@0.3.30`; both Builder runtimes are active locally;
- live calls through the real tool bridge: Workspace Builder resolved
  `desktop-dev` / `builder` / Prototype `047`, while DEV Builder resolved
  `dev1-dev` / `test04_recipes` / Prototype `003`. Both destinations used
  `zone=ru`, `subnet_id=sn_6acf0c01`, `space_kind=development`, and
  authenticated `openUrl` actions;
- 61/61 focused core/SDK/publication tests and 160/160 tests against each of
  the DEV and published Workspace Builder copies.

The production-client correction is commit `e10e3e8`: the previous feature
branch had not reached `main`, so Firebase still served a client that ignored
the new intent and treated `webspace_id` as an immediate startup room. The
corrected client both deploys the resolver from `main` and prevents destination
room admission before consent. Firebase Hosting and Notify Infra runs
`30811335897` and `30811335892` completed successfully; the live hashed bundle
contains the canonical destination schema, `webspace.open`, and
`subnet_mismatch` resolver reason.

Publication also proved the Windows lock recovery path. Artifact activation
keeps a staged source and rollback copy, prefers a whole-directory atomic swap,
and falls back to file-atomic replacement when either removal of the live
directory or installation of the staged directory is denied by an open handle.
It never retries the remote push or another state-changing command; a partial
local activation rolls back from the sibling copy.

The 2026-08-04 same-Webspace correction is client commit `eb053fe`. Opening an
unresolved `webspace.open` intent while the subnet-scoped current Webspace is
already `dev1-dev` no longer boots `desktop` and no longer reports a false
Webspace mismatch. If `builder` is open, the resolver asks once whether to
switch to `test04_recipes`; after that materialization completes, the overlay
closes even while the Yjs transport is reconnecting. Reopening the same target
is a no-op at the navigation layer. A new browser tab can still spend time on
the client's cold boot and transport restoration, but it performs no repeated
Project or scenario transition.

The 2026-08-04 exact raw-link follow-up found two additional defects. The early
client resolver ignored intent `webspace_id` unless the legacy `webspace` query
was also present, and cancelled WebRTC/Yjs bindings remained in
`YRoom.clients`. The observed room sent the same 296,101-byte payload 170 times
in about 36 seconds (up to 34 copies in one second), reached critical memory
pressure, restarted the runtime, and consequently produced YWS disconnect and
relay fallback symptoms. The corrected client boots a topology-confirmed
`dev1-dev` destination directly, prepares only `test04_recipes`, and never
renders Builder; the local raw-link trace reached the requested UI in 5.8 s
without `webspace_mismatch` or `scenario_context_mismatch`. The focused client
suite passes 271/271 and the production build succeeds. Core tests cover
adapter cleanup under cancellation, failed-recipient pruning, page-scoped peer
identity, authoritative selector repair, and atomic materialization ordering.
Core deployment remains a separate release gate from the client deployment.

The 2026-08-04 transport follow-up activated source build
`0.1.667+4391.89ec14a3` under the repository `.venv`, without Supervisor or a
core slot. Two controlled `dev1-dev` switches
(`test04_recipes -> builder -> test04_recipes`) completed with one Yjs update
per transition, one send per live room client, no YWS reconnect, no peer
replacement, and idle post-transition load. Exact telemetry reported five
live YWS page sessions rather than the earlier aggregate of fourteen attempts;
each `bs_*` session had exactly one attempt id. Two simultaneous tabs from one
device also received different `rp_*` peer ids, so neither replaced the other.

Client `0.0.265` adds the bounded connected-peer DataChannel grace and removes
all temporary channel listeners on every outcome. Its focused transport suite
passes 10/10. The remaining cold-start delay is a separate startup-performance
item: synchronous skill loading can delay readiness, but it must not be treated
as a Preview transition or repaired by replaying a state-changing command.

Initial Yjs admission is likewise a protocol exchange, not a reason to replay
the complete document speculatively. The server sends its sync vector, answers
the browser's `SYNC_STEP1` with one authoritative `SYNC_STEP2`, and ignores the
browser's initial state under the server-authoritative policy without sending a
second copy. Full effective-state replay is an exceptional malformed/preflight
recovery mechanism. This removes the observed same-peer triple delivery while
preserving standard Yjs synchronization semantics.

The final restart evidence reports three connected WebRTC peers with open Yjs
channels, six exact YWS sessions with one attempt each, one initial document
send per direct peer, zero eager effective-state replays, six rejected-state
dedupes, and no reconnect storm or peer replacement.
