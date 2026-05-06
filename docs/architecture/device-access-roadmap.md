# Device Access Roadmap

## Goal

Move AdaOS from a collection of separate browser, member, marketplace, and desktop UI behaviors to a single reusable device access plane.

The target state is described in
[Device Access and Browsers](device-access-and-browsers.md).

## Principles

- keep the authoritative model in core runtime
- expose stable SDK helpers before proliferating skill-local logic
- separate bootstrap issuance from long-lived access policy
- treat browser and member links with the same operator mental model
- keep `web_desktop` compact by moving section operations into modal settings

## Phase 0. Freeze vocabulary and UI intent

Deliverables:

- rename the desktop surface from `Applications` to `Devices`
- define the terms `device`, `client`, `access link`, `detach`, and `display_name`
- define the UI split between:
  - node capability management: `Apps`, `Marketplace`, `Hide`
  - access management: rename, lifetime, detach

Exit criteria:

- the team uses one shared vocabulary in code, docs, and UX review

## Phase 1. Introduce core access link registry

Deliverables:

- create a runtime-owned access link registry backed by durable state
- support browser links keyed by `device_id`
- support member links keyed by `node_id`
- store display name, lifetime mode, expiry, revocation, last seen, connectivity, and webspace affinity
- publish SDK helpers under `sdk.data.access_links`

Exit criteria:

- skills and runtime services can read and mutate access links without duplicating storage logic

## Phase 2. Attach runtime enforcement to live ingress

Deliverables:

- enforce browser policy on Yjs or control ingress using `device_id`
- ensure browser HTTP requests carry `X-AdaOS-Device-Id`
- enforce member policy on hub-side member hello or registration
- deny revoked and expired links before they become active runtime sessions

Exit criteria:

- `detach` and expiry are runtime truths, not only UI annotations

## Phase 3. Ship `browsers_skill` as the first consumer

Deliverables:

- publish browser inventories into Yjs projections
- expose skill actions for rename, lifetime changes, and detach
- present `Devices` and `Clients` as separate browser groups
- keep only browsers that were actually used

Exit criteria:

- browser observability no longer depends on ad hoc inspection of raw session internals

## Phase 4. Reframe `web_desktop` around devices

Deliverables:

- rename the top-level desktop surface to `Devices`
- add a `Browsers` entry point
- replace per-section action rows with a single settings affordance
- move `Apps`, `Marketplace`, `Hide`, rename, lifetime, and `Detach` into settings modal UX
- keep compact-screen labels short and icon-first where needed

Exit criteria:

- the desktop remains compact on mobile while preserving the full control surface

## Phase 5. Preserve node-scoped operations inside device context

Deliverables:

- keep `Apps` bound to the current node context
- keep `Marketplace` bound to the current node context and filtered to items not yet installed on that node
- retain visibility controls such as `Hide` or `Show` as presentation-only settings

Exit criteria:

- device management and capability management are clearly separated but reachable from the same section settings shell

## Phase 6. Converge browser and member lifecycle semantics

Deliverables:

- use the same access policy concepts for browsers and member nodes
- allow rename for member devices through runtime-controlled node naming flows
- allow detach for connected members through link manager unregistration
- define offline behavior for members that are detached while currently disconnected

Exit criteria:

- operators do not need separate mental models for browsers and member nodes

## Phase 7. Add real issuer-side autorotation

Deliverables:

- turn `autorotate` from a policy flag into a full token lifecycle capability
- rotate permanent browser access without forcing manual reconnect or re-pair
- ensure revocation fan-out invalidates active server-side session state

Exit criteria:

- permanent device access is durable without becoming a permanently static token

## Phase 8. Add voice and automation integration

Deliverables:

- use `display_name` as the canonical voice-facing device label
- expose device policies to automation and assistant skills
- allow intent patterns such as:
  - "disconnect the living room TV"
  - "show apps on kitchen tablet"
  - "give this browser access for one day"

Exit criteria:

- device access management becomes part of the assistant platform, not only the operator UI

## Recommended implementation order

1. Phase 0 and Phase 1
2. Phase 2
3. Phase 3 and Phase 4
4. Phase 5 and Phase 6
5. Phase 7
6. Phase 8

This order keeps the runtime contract stable first, then builds observability and operator UX on top of it.

