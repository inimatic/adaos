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

## Current target decisions

- [x] `DeviceInventoryService` is the canonical device-facing aggregation layer, not a replacement storage owner.
- [x] `access_links` remains the authoritative source for durable access policy.
- [x] `subnet_directory` remains the authoritative source for remembered member runtime snapshots and capacity.
- [x] Live browser session and member-link layers remain the authoritative source for transient presence.
- [x] Skills should access device inventory and device access commands through SDK surfaces, not direct `services.*` imports.
- [x] The device-facing connectivity field should converge on `connected_to_subnet`, while low-level routing details remain separate.

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

The registry and device inventory must be reusable by skills.
The canonical access path is an SDK helper surface, not direct imports of runtime services from skills.

The target SDK split is:

- `sdk.data.access_links.*` for low-level access-link policy records
- `sdk.data.devices.*` for aggregated device read models
- `sdk.data.device_access.*` for device-facing commands and settings schemas

Representative examples:

- `sdk.data.access_links.list_browser_links()`
- `sdk.data.access_links.list_member_links()`
- `sdk.data.devices.list_devices()`
- `sdk.data.devices.get_device(device_ref)`
- `sdk.data.devices.inspect_device(device_ref)`
- `sdk.data.device_access.get_command_profile(device_ref)`
- `sdk.data.device_access.rename_device(device_ref, display_name)`
- `sdk.data.device_access.set_device_lifetime(device_ref, preset)`
- `sdk.data.device_access.detach_device(device_ref)`

This keeps the skill API stable while allowing the core storage, aggregation, and enforcement internals to evolve.

## 6. Device inventory read model

The core runtime should expose one canonical device-facing read model through `DeviceInventoryService`.

This read model is an aggregate over:

- durable access policy from `access_links`
- remembered member metadata and runtime snapshots from `subnet_directory`
- transient browser and member presence from live runtime channels

The base payload should stay focused on facts and computed device semantics, not UI action flags or debug provenance.

Recommended shape:

```text
DeviceRecord
  ref: "browser:<device_id>" | "member:<node_id>"
  kind: "browser" | "member"
  identity:
    link_id
    browser_device_id?
    node_id?
    hostname?
    node_names[]
    base_url?
  policy:
    present
    managed_state: "managed" | "observed_only" | "revoked" | "expired"
    display_name?
    effective_name
    access_class
    lifetime_mode
    expires_at?
    revoked
    revoked_at?
  observation:
    online
    connection_state?
    last_seen_at?
    source: "browser_session" | "member_link" | "subnet_directory"
    last_webspace_id?
  runtime:
    snapshot_ready?
    snapshot_state?
    route_mode?
    connected_to_subnet?
    runtime_version?
```

Important boundaries:

- `managed_state` belongs in the canonical read model because it is a device-level semantic derived from policy plus observation.
- `observation.source` belongs in the read model because it explains whether the device is live, link-backed, or only remembered.
- command availability such as `can_rename` or `can_detach` should not live inside `DeviceRecord`; it belongs to a separate command-profile surface.
- diagnostics such as `policy_source`, raw runtime sources, or aggregation timestamps should not be part of the default payload; they belong to an explicit inspect surface.

For member devices, the device-facing connectivity bit should converge on `connected_to_subnet`.
That field answers whether the device is currently attached strongly enough to the subnet control plane to be treated as reachable in device UX.
It intentionally does not encode whether the underlying path is hub-relayed, direct peer-to-peer, or another future transport mode.
Low-level routing detail should remain in separate fields such as `route_mode`.
Existing `connected_to_hub` consumers can be preserved behind a compatibility alias during the migration, but the target vocabulary should move to `connected_to_subnet`.

## 7. DeviceInventoryService boundaries

`DeviceInventoryService` should be the canonical interface for device-facing consumers:

- `web_desktop`
- settings modals
- inventory and admin skills
- assistant and automation skills
- system-model views that need device semantics instead of raw runtime topology

Its responsibilities are:

- define canonical `DeviceRef` identity
- merge policy, runtime, and presence inputs into `DeviceRecord`
- compute `effective_name`
- compute `managed_state`
- expose stable list and get queries for devices
- orchestrate device-facing commands through core policy and runtime services

Its responsibilities do not include:

- owning policy persistence
- owning member runtime snapshot persistence
- owning app catalog or marketplace state
- owning presentation-only state such as `Hide` or `Show`
- becoming a second hidden registry of raw facts

The supporting surfaces should stay separate:

- `DeviceRecord` for default read access
- `DeviceCommandProfile` for command availability, presets, and reasons
- `DeviceDiagnostics` for explicit inspect and debug flows

## 8. Skill layer

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

## 9. `web_desktop` as a device-centric shell

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

## 10. Browsers UI model

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

## 11. Marketplace and app management stay node-scoped

The device-centric shell does not remove node-scoped capability management.

Instead, it clarifies ownership:

- `Apps` is the installed app catalog for a concrete node
- `Marketplace` is the list of installable skills and scenarios not yet installed on that node
- `Hide` is desktop presentation state
- rename, lifetime, and detach belong to device access management

`Marketplace` therefore remains a node-scoped operational action, but it is launched from the device settings context instead of being mixed with every other section button.

## 12. Offline semantics

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

## 13. Relationship to other architecture slices

This design complements:

- [Member-Hub Connectivity](member-hub-connectivity.md): lifecycle ownership of hub-member transport and restart-aware member semantics
- [Registry Marketplace And Operations](registry-marketplace-operations-roadmap.md): node-scoped marketplace publication and install flows
- [Operational Event Model](operational-event-model.md): browser-facing projections and operator materialization
- [Semantic State Plane](semantic-state-plane.md): separating access policy from short-lived transport status

## Transition roadmap

The recommended implementation order is documented in
[Device Access Roadmap](device-access-roadmap.md).
