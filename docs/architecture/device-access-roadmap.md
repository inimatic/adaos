# Device Access Roadmap

Target state: [Device Access and Browsers](device-access-and-browsers.md)

## Working principles

- [x] Keep the authoritative access model in core runtime.
- [x] Expose reusable SDK helpers before proliferating skill-local logic.
- [x] Separate bootstrap issuance from long-lived access policy.
- [x] Treat browser and member links with the same operator mental model.
- [x] Keep `web_desktop` compact by moving section operations into settings modals.

## Locked target decisions

- [x] Treat `DeviceInventoryService` as a canonical aggregation layer, not as a replacement raw-data registry.
- [x] Keep `access_links` as the authoritative source for durable access policy.
- [x] Keep `subnet_directory` as the authoritative source for remembered member runtime snapshots and capacity.
- [x] Keep live browser and member-link layers as the authoritative source for transient presence.
- [x] Keep skill access to device inventory and device commands SDK-first rather than `services.*`-first.
- [x] Converge the device-facing connectivity field on `connected_to_subnet`, while preserving low-level route detail separately.

## Core access model

- [x] Rename the desktop surface from `Applications` to `Devices`.
- [x] Define and use the terms `device`, `client`, `access link`, `detach`, and `display_name`.
- [x] Introduce a runtime-owned access link registry backed by durable state.
- [x] Support browser links keyed by `device_id`.
- [x] Support member links keyed by `node_id`.
- [x] Store display name, lifetime mode, expiry, revocation, last seen, connectivity, and webspace affinity.
- [x] Publish SDK helpers under `sdk.data.access_links`.

## Device inventory model

- [x] Introduce `DeviceInventoryService` as the core aggregation layer over policy, remembered runtime state, and live presence.
- [x] Define one canonical `DeviceRecord` read model for both browser and member endpoints.
- [x] Standardize device references as `browser:<device_id>` and `member:<node_id>`.
- [x] Keep the default `DeviceRecord` focused on identity, policy, observation, and runtime state.
- [x] Keep command availability out of `DeviceRecord` and expose it through a separate command-profile surface.
- [x] Keep diagnostics and provenance out of `DeviceRecord` and expose them through an explicit inspect surface.
- [x] Rename the device-facing member connectivity field from `connected_to_hub` to `connected_to_subnet`.
- [x] Preserve a compatibility alias or adapter for existing `connected_to_hub` consumers during migration.
- [x] Preserve `route_mode` and related routing detail separately from device-facing connectivity.

## SDK-first device surfaces

- [x] Publish aggregated device read APIs under `sdk.data.devices`.
- [x] Publish device command APIs under `sdk.data.device_access`.
- [ ] Keep `sdk.data.access_links` as the low-level access-policy surface.
- [x] Migrate device skills to SDK entrypoints instead of direct `services.*` imports.
- [x] Expose a stable settings-schema or command-profile contract through the SDK for modal and assistant consumers.
- [x] Expose ReDevice endpoint command helpers through `sdk.data.device_access`.
- [x] Move skill-facing ReDevice endpoint commands from skill-local bridge calls
  to `sdk.data.device_access` and the minimal core `EndpointRouter`.

## Current ReDevice implementation snapshot

The ReDevice implementation is already beyond the original "first dashboard"
slice. The current baseline is:

- [x] ReDevice endpoints are materialized through the access-link registry and
  `DeviceInventoryService`, with scope filtering by subnet, hub, owner, and
  current endpoint identity.
- [x] Superseded or revoked admission rows are treated as historical evidence,
  not as independent live endpoints. SDK helpers may resolve an old code for
  operator convenience, but commands target the current endpoint record.
- [x] `DeviceRecord` projections include ReDevice identity, trust, policy,
  manifest, health, diagnostics, service state, build/version data,
  `active_app`, `active_surface`, and `assignment`.
- [x] `sdk.data.device_access` exposes ReDevice-compatible operations:
  endpoint resolution, command send, profile update, rename, assignment,
  revoke, and retire.
- [x] `sdk.redevice` still owns compatibility transport selection and compact
  endpoint projection, but scenario skills no longer call it directly for
  ordinary endpoint commands.
- [x] `EndpointRouter` owns the skill-facing ReDevice command envelope:
  `sdk.data.device_access` resolves endpoint identity and calls router command
  send, while the router builds `endpoint-command.v1`, selects transport
  evidence, and emits the legacy-compatible payload for the current command
  queue adapter.
- [x] Endpoint resolution returns `endpoint-resolution.v1` evidence with
  matched names, assignment, active app/surface, online state, and historical
  admission-code healing details.
- [x] ReDevice assignments have a structured `endpoint_assignment` projection
  with role, owner, source, reason, and updated timestamp while preserving the
  legacy `assignment` field for existing skills.
- [x] Local LAN admission is available for same-network onboarding without QR:
  `redevice_settings` opens a discovery window, unpaired endpoints submit
  diagnostics to the hub, approval is exposed as a pending action and as
  Settings skill actions, and credentials are issued only after approval.
- [x] Endpoint records preserve endpoint-facing and hub-local control URLs so
  SDK calls can route commands to a LAN-admitted endpoint without using the
  public root URL.
- [x] LAN onboarding now treats loopback endpoint URLs as explicit adb-reverse
  development mode only. Normal LAN admission must use a hub LAN address that
  the endpoint can reach.
- [x] Local ReDevice command polling uses lease plus ack semantics. Commands
  are not removed when selected by `/commands/next`; they are redelivered until
  endpoint acknowledgement or expiry.
- [x] `redevice_settings`, `redevice_voice`, `slideshow_skill`, and
  `redevice_list` are consumers of core registry/SDK/router state. They are
  not independent registries or transport owners.
- [x] Android ReDevice Agent supports active endpoint mode, command polling,
  slideshow/display commands, active-app reporting, logout/re-admission,
  native settings intents, VAD/PTT audio capture, bounded audio segment upload,
  and degraded-friendly diagnostics.
- [x] ReDevice User Face is a scenario composition in the regular desktop
  webspace. A separate webspace is not required for the current debug/product
  slice and can be added later without changing the endpoint contracts.

Remaining architecture work is therefore not "create ReDevice registry and
settings". It is:

- [~] Promote command-scoped ReDevice media routes to router-owned direct media
  sessions. `slideshow_skill` now sends `endpoint-media-session.v1` with
  `endpoint_media_pull` as the primary intent and inline only as fallback; the
  remaining work is replacing the compatibility adapter with proven direct
  LAN/WebRTC media sessions.
- [ ] Replace the current manual/dev LAN hub URL with automatic LAN discovery
  evidence such as mDNS, UDP beacon, or hub-published local address hints.
- [ ] Upgrade LAN admission transport from MVP HTTP to local TLS or mTLS where
  the target platform can support it.
- [~] Move endpoint assignments fully into a first-class core
  `EndpointAssignment` model with audit and conflict handling.
- [~] Replace remaining compatibility bridge calls with generic
  `EndpointRouter` APIs. Ordinary ReDevice settings, slideshow, and voice
  capture commands use router-owned send; profile/revoke/retire and future
  direct-media adapters still use compatibility helpers.
- [ ] Add sidecar/runtime restart continuity rules for active endpoint media
  sessions.
- [ ] Close response routes from Voice/NLU/dialog back to endpoint speaker,
  display, notification, or text buffer.
- [ ] Add multi-endpoint audio arbitration and duplicate suppression.

## Enforcement and lifecycle

- [x] Enforce browser policy on live ingress using `device_id`.
- [x] Ensure browser HTTP requests carry `X-AdaOS-Device-Id`.
- [x] Enforce member policy on hub-side member hello or registration.
- [x] Deny revoked and expired links before they become active runtime sessions.
- [ ] Add true issuer-side autorotation for permanent browser access.
- [ ] Fan out revocation into all active server-side browser session state.

## Browser observability

- [x] Ship `browsers_skill` as the first consumer of the access-link registry.
- [x] Publish browser inventories into Yjs projections.
- [x] Expose skill actions for rename, lifetime changes, and detach.
- [x] Present `Devices` and `Clients` as separate browser groups.
- [x] Ignore bootstrap approvals that never turned into real browser usage.
- [x] Skip archival storage for expired browser clients.
- [ ] Group browser inventory explicitly by last or current webspace in the operator UI.
- [ ] Add browser settings UX parity between transient client modal and skill-hosted modal flow.

## `web_desktop` device shell

- [x] Add a `Browsers` entry point to the `Devices` panel.
- [x] Replace per-section action rows with a single settings affordance.
- [x] Move `Apps`, `Marketplace`, `Hide`, rename, lifetime, and `Detach` into device settings UX.
- [x] Keep compact-screen labels short and icon-first where needed.
- [x] Route all device settings actions through one stable generic modal contract.
- [ ] Add confirmation and richer status messaging for destructive detach flows.

## Node-scoped operations inside device context

- [x] Keep `Apps` bound to the current node context.
- [x] Keep `Marketplace` bound to the current node context.
- [x] Filter `Marketplace` to items not yet installed on that node.
- [x] Keep `Hide` or `Show` as presentation-only desktop state.
- [ ] Unify node capability management and device access management under one reusable settings schema.

## Browser and member convergence

- [x] Use the same access policy concepts for browsers and member nodes.
- [x] Allow rename for member devices through runtime-controlled node naming flows.
- [x] Allow detach for connected members through link manager unregistration.
- [x] Build a reconciler that materializes a consistent device aggregate from policy, remembered runtime state, and live presence.
- [x] Define how `observed_only` devices are promoted into managed policy records, if at all.
- [ ] Define the merge rules for `display_name`, `node_names`, `hostname`, and effective device naming.
- [ ] Define offline behavior for members detached while currently disconnected.
- [ ] Close policy/runtime drift for revoke, rename, expiry, and offline-detach flows.

## System-model alignment

- [x] Move device-facing projections to `DeviceInventoryService` rather than rebuilding them ad hoc from `subnet_directory` and link state.
- [ ] Keep topology and routing projections separate from device inventory semantics.
- [x] Migrate user-facing device status fields and labels onto the canonical `DeviceRecord` vocabulary.

## Voice and automation follow-up

- [x] Use `display_name` as the canonical voice-facing device label.
- [ ] Expose device policies to automation and assistant skills.
- [x] Treat ReDevice aliases as endpoint named-entity labels for active-app routing.
- [x] Route the first bounded slideshow voice commands through the selected
  endpoint's active app using `slideshow_skill.voice_control_redevice_slideshow`.
- [x] Resolve slideshow voice commands through `DeviceInventoryService`
  projections, including assignment and `active_app=slideshow_skill`, before
  falling back to the selected endpoint.
- [ ] Support operator and assistant intents such as:
  - [ ] "disconnect the living room TV"
  - [ ] "show apps on kitchen tablet"
  - [ ] "give this browser access for one day"
  - [ ] "open ReDevice settings"
  - [x] "start slideshow on the kitchen tablet"
  - [x] "tablet, next"

## ReDevice User Face scenario

- [x] Create `redevice_user_face` scenario with required skills:
  `redevice_settings`, `slideshow_skill`, and `redevice_voice`.
- [x] Add `redevice_settings` as the service skill for endpoint settings and status.
- [~] Keep scenario assignment in `redevice_settings` memory for the first slice.
- [x] Keep `redevice_settings.state` as a thin browser stream: fleet rows,
  selected identity/status, summary cards, and lightweight sections only.
- [x] Move full endpoint manifest, policy, diagnostics, health, and service
  state inspection behind an explicit skill tool instead of streaming them on
  every browser snapshot.
- [x] Make `webio.stream.snapshot.requested` and
  `webio.stream.subscription.changed` handlers restore the last cached stream
  snapshot instead of performing live Endpoint Registry or ReDevice bridge
  refreshes.
- [~] Move scenario assignment into core `EndpointAssignment`. A structured
  `endpoint_assignment` object is now projected by access links, device
  inventory, and SDK; the remaining work is durable conflict/audit semantics
  and generic router ownership.
- [x] Add native ReDevice Agent support for Wi-Fi/Bluetooth settings intents,
  speaker test, volume, diagnostics, logout, and active-app controls.
- [x] Keep the browser client generic: it renders `webui.json` and does not
  gain ReDevice-specific business logic.

## Recommended execution order

- [x] Phase 0 and Phase 1: vocabulary and core access model.
- [x] Phase 2: ingress enforcement.
- [x] Phase 3: first browser observability slice.
- [x] Phase 4: `web_desktop` device shell.
- [x] Phase 5: `DeviceInventoryService`, canonical `DeviceRecord`, and device reference normalization.
- [x] Phase 6: SDK-first `devices` and `device_access` surfaces plus skill migration off direct service imports.
- [x] Phase 7: unified settings contract and command-profile surface.
- [~] Phase 8: browser and member convergence cleanup, reconciler rollout, and `connected_to_subnet` migration.
- [ ] Phase 9: issuer-side autorotation.
- [~] Phase 10: system-model, voice, and automation integration.
- [~] Phase 11: ReDevice User Face scenario and Endpoint Registry-backed
  endpoint settings surfaces.
- [~] Phase 12: router-owned endpoint media sessions, endpoint audio response
  routes, and restart-safe active endpoint sessions.
