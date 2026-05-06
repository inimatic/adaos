# Device Access and Browsers

## Purpose

This document captures the target architecture for device-centric access management in AdaOS.
It consolidates the model behind:

- the `Devices` panel in `web_desktop`
- browser access links issued through the web pairing flow
- member-node links joined through the subnet join flow
- per-node marketplace and app management in a device-centric UI
- future voice- and automation-facing device naming and policy control

The goal is to stop treating browser sessions, member links, app catalogs, and marketplace actions as isolated UI features.
Instead, AdaOS should expose one coherent device access plane with:

- durable identity
- explicit lifetime policy
- detachment and revocation
- observability
- reusable SDK and skill surfaces

## Problem statement

The current runtime already has most of the raw ingredients:

- browser pairing and `session_jwt` bootstrap
- persistent browser `device_id`
- member join codes and member hub links
- Yjs and browser presence signals
- per-node app and marketplace catalogs
- local durable state, Yjs projections, and skill-hosted modal UI

What was missing is a single architectural model that answers:

1. Which connected entities are treated as long-lived devices versus temporary clients?
2. Where is the canonical lifetime policy stored?
3. How are browser links and member links managed with the same mental model?
4. Which layer owns rename, detach, and lifetime control?
5. How does `web_desktop` stay device-centric while reusing generic platform components?

## Core vocabulary

### Access link

An `access link` is the canonical managed relationship between AdaOS and a remote endpoint.

Two link kinds exist:

- `browser`: a web client identified by persistent `device_id`
- `member`: a subnet member node identified by `node_id`

The access link is the authoritative policy object.
Bootstrap artifacts such as join codes, pair codes, or approval tokens are only temporary issuance flows.

### Device vs client

Browser links are grouped into two operator-facing classes:

- `device`: a long-lived trusted endpoint, usually with permanent access and editable name
- `client`: a temporary endpoint with fixed lifetime

This is intentionally a policy distinction, not a transport distinction.
A phone browser, TV browser, or laptop browser can all be promoted to a `device`.

### Device name

Each long-lived endpoint can carry a human-facing `display_name`.
This name is intended to become the stable label used by:

- `web_desktop`
- browser observability UI
- future voice commands
- future automation rules

### Webspace affinity

Browser links should remember the last known or current webspace.
This lets the operator view browser inventory in the context in which it is used, instead of as a flat token list.

## Target architecture

## 1. Bootstrap and live access are separate concerns

The architecture separates:

- `issuance`: pair codes, join codes, approvals, bootstrap session material
- `managed access`: the durable access link registry and runtime enforcement

This means:

- a browser pair code is not itself the browser inventory record
- a member join code is not itself the member device policy record
- a browser that received a key but never actually connected does not need to become part of the long-term inventory

The durable model starts when the runtime sees first real usage:

- browser live session or `device.register`
- member websocket hello and link registration

## 2. Access link registry in core runtime

AdaOS should maintain a small core registry of access links.

The registry lives in the runtime layer, not inside a single skill, because multiple skills and client surfaces need to reuse it.
The initial persistence mechanism is the local durable state store.

Recommended registry shape:

```text
namespace: access_links
key: registry
```

Per-entry fields should stay transport-agnostic:

- `id`
- `kind`
- `display_name`
- `access_class`
- `lifetime_mode`
- `expires_at`
- `autorotate`
- `revoked`
- `revoked_at`
- `created_at`
- `updated_at`
- `last_seen_at`
- `online`
- `connection_state`
- `last_webspace_id`
- `hostname`
- `node_names`

Keying rules:

- browser links are keyed by persistent browser `device_id`
- member links are keyed by member `node_id`

## 3. Lifetime policy

The default policy is:

- permanent access
- token or session rotation handled automatically by the platform

Operator-facing lifetime modes:

- `permanent`
- fixed duration presets such as `1h`, `1d`, `7d`, `30d`

Policy rules:

- permanent browser links are shown under `Devices`
- fixed-lifetime browser links are shown under `Clients`
- expired browser links do not need to be preserved as historical archive
- detached links become revoked policy objects and are denied on future ingress

For member links, lifetime support exists in the same model even if the most common operational mode remains permanent access.

## 4. Runtime enforcement

The access link registry is not only descriptive.
It is also the policy source checked at ingress.

### Browser path

Browser access should be enforced when a browser opens its live runtime channels:

- browser HTTP calls carry `X-AdaOS-Device-Id`
- browser Yjs connections carry `dev=<device_id>`
- browser control and event flows already emit `device.register` and session change events

The runtime should:

1. resolve `device_id`
2. look up the browser access link
3. deny access if the link is revoked or expired
4. touch `last_seen_at`, `connection_state`, and `last_webspace_id` on accepted traffic

This keeps lifetime control in the runtime, not only in UI state.

### Member path

Member access should be enforced on member link handshake:

- member hello carries `node_id`
- hub-side link manager owns registration and unregistration

The runtime should:

1. resolve `node_id`
2. look up the member access link
3. deny registration if the link is revoked or expired
4. update member metadata in the registry on successful registration

## 5. SDK surface

The registry must be reusable by skills.
The canonical access path is an SDK helper surface, for example:

- `sdk.data.access_links.list_browser_links()`
- `sdk.data.access_links.list_member_links()`
- `sdk.data.access_links.rename_*()`
- `sdk.data.access_links.set_*_lifetime()`
- `sdk.data.access_links.detach_*()`

This keeps the skill API stable while allowing the core storage or enforcement internals to evolve.

## 6. Skill layer

The first skill surface for this model is `browsers_skill`.

Its responsibilities are:

- publish operator-facing browser projections into Yjs
- expose generic actions for rename, lifetime changes, and detach
- present browser inventory grouped into `Devices` and `Clients`
- preserve webspace context for operator navigation

`browsers_skill` is intentionally not the owner of the access model.
It is the first consumer of the core registry and SDK.

That makes the architecture reusable for:

- future device management skills
- voice assistant skills
- policy automation skills
- admin or fleet-management surfaces

## 7. `web_desktop` as a device-centric shell

The `desktop-icons` surface should be reframed from `Applications` to `Devices`.

That means:

- the top-level entry point is about managed endpoints, not only app icons
- node sections represent device contexts
- per-node operational actions move behind a settings affordance
- node actions are still backed by generic modals and skill-hosted actions

The device section settings modal is the main operator shell for a node.
It should expose:

- `Apps`
- `Marketplace`
- `Hide` or `Show`
- rename
- lifetime policy
- `Detach`

This keeps the panel compact while preserving the full control surface.

## 8. Browsers UI model

The `Devices` panel should also expose a `Browsers` entry point.

The target browser UX is:

- separate groups for `Devices` and `Clients`
- grouping or filtering by last or current webspace
- ignore pair approvals that never became live browser usage
- no archive of expired browser clients

Browser settings should mirror the same device access model:

- editable name
- permanent versus fixed lifetime
- detach

## 9. Marketplace and app management stay node-scoped

The device-centric shell does not remove node-scoped capability management.

Instead, it clarifies ownership:

- `Apps` is the installed app catalog for a concrete node
- `Marketplace` is the list of installable skills and scenarios not yet installed on that node
- `Hide` is desktop presentation state
- rename, lifetime, and detach belong to device access management

`Marketplace` therefore remains a node-scoped operational action, but it is launched from the device settings context instead of being mixed with every other section button.

## 10. Offline semantics

Offline state should not flap on brief transport loss.

The device-centric desktop should continue to use a grace timeout before showing icons as disabled.
That timeout belongs to presentation semantics.
The access link registry remains the durable policy model and can record:

- online or offline
- last seen
- connection state

This separates:

- access validity
- current connectivity
- UI confidence window

## 11. Relationship to other architecture slices

This design complements:

- [Member-Hub Connectivity](member-hub-connectivity.md): lifecycle ownership of hub-member transport and restart-aware member semantics
- [Registry Marketplace And Operations](registry-marketplace-operations-roadmap.md): node-scoped marketplace publication and install flows
- [Operational Event Model](operational-event-model.md): browser-facing projections and operator materialization
- [Semantic State Plane](semantic-state-plane.md): separating access policy from short-lived transport status

## Transition roadmap

The recommended implementation order is documented in
[Device Access Roadmap](device-access-roadmap.md).

