# Navigation Intent And Location Architecture

Status: target contract with the first local implementation slice validated on
2026-08-03.

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
obtain a fresh synchronized state, and verify materialization.

## Ordered Resolution

`webspace.open` is resolved in this order:

1. Compare zone and propose an explicit zone switch when it differs.
2. Establish authentication in that zone while retaining the destination.
3. Compare `subnet_id` and propose an explicit subnet switch.
4. Compare `webspace_id` and propose an explicit Webspace switch.
5. Verify the authoritative Webspace source boundary.
6. Wait for fresh synchronized state; stale Yjs state cannot confirm success.
7. Compare the expected scenario and, when observable, revision.
8. Open the destination or explain the remaining mismatch.

The resolver itself is pure. Its output is `ready`, `input_required`,
`waiting`, or `blocked`, with an action, stable reason, current context, and
choices. `input_required` never silently changes zone, subnet, Webspace, or
scenario. A copied link therefore cannot mutate workflow state merely because
it was opened.

`webspace_id` inside `webspace.open` is a destination, not a bootstrap query.
The Yjs client must not enter that room while zone/subnet resolution is still
pending. It starts from the current safe context and calls the normal Webspace
switch command only after the user accepts the preceding topology decisions.

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

Builder also reports the source Webspace and its related Preview Webspace in
project/current/Preview-link responses. A Telegram conversation without an
explicit trusted Webspace binding is labelled as a persisted dialog scope;
selecting a Project changes that conversation focus but does not silently
claim or switch a browser Webspace.

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

## Acceptance Evidence

The 2026-08-03 local slice includes:

- Python SDK round-trip and ordered-resolution tests;
- Builder SDK tests proving full Preview expectations in the URL;
- AdaOS Connect tests proving SDK-owned registration destinations;
- backend TypeScript compilation after removal of legacy `mode` generation;
- client Navigation/App/YDoc regression tests (228/228), including the exact
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
