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

- [ ] Introduce `DeviceInventoryService` as the core aggregation layer over policy, remembered runtime state, and live presence.
- [ ] Define one canonical `DeviceRecord` read model for both browser and member endpoints.
- [ ] Standardize device references as `browser:<device_id>` and `member:<node_id>`.
- [ ] Keep the default `DeviceRecord` focused on identity, policy, observation, and runtime state.
- [ ] Keep command availability out of `DeviceRecord` and expose it through a separate command-profile surface.
- [ ] Keep diagnostics and provenance out of `DeviceRecord` and expose them through an explicit inspect surface.
- [ ] Rename the device-facing member connectivity field from `connected_to_hub` to `connected_to_subnet`.
- [ ] Preserve a compatibility alias or adapter for existing `connected_to_hub` consumers during migration.
- [ ] Preserve `route_mode` and related routing detail separately from device-facing connectivity.

## SDK-first device surfaces

- [ ] Publish aggregated device read APIs under `sdk.data.devices`.
- [ ] Publish device command APIs under `sdk.data.device_access`.
- [ ] Keep `sdk.data.access_links` as the low-level access-policy surface.
- [ ] Migrate device skills to SDK entrypoints instead of direct `services.*` imports.
- [ ] Expose a stable settings-schema or command-profile contract through the SDK for modal and assistant consumers.

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
- [ ] Route all device settings actions through one stable generic modal contract.
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
- [ ] Build a reconciler that materializes a consistent device aggregate from policy, remembered runtime state, and live presence.
- [ ] Define how `observed_only` devices are promoted into managed policy records, if at all.
- [ ] Define the merge rules for `display_name`, `node_names`, `hostname`, and effective device naming.
- [ ] Define offline behavior for members detached while currently disconnected.
- [ ] Close policy/runtime drift for revoke, rename, expiry, and offline-detach flows.

## System-model alignment

- [ ] Move device-facing projections to `DeviceInventoryService` rather than rebuilding them ad hoc from `subnet_directory` and link state.
- [ ] Keep topology and routing projections separate from device inventory semantics.
- [ ] Migrate user-facing device status fields and labels onto the canonical `DeviceRecord` vocabulary.

## Voice and automation follow-up

- [ ] Use `display_name` as the canonical voice-facing device label.
- [ ] Expose device policies to automation and assistant skills.
- [ ] Support operator and assistant intents such as:
  - [ ] "disconnect the living room TV"
  - [ ] "show apps on kitchen tablet"
  - [ ] "give this browser access for one day"

## Recommended execution order

- [x] Phase 0 and Phase 1: vocabulary and core access model.
- [x] Phase 2: ingress enforcement.
- [x] Phase 3: first browser observability slice.
- [~] Phase 4: `web_desktop` device shell.
- [ ] Phase 5: `DeviceInventoryService`, canonical `DeviceRecord`, and device reference normalization.
- [ ] Phase 6: SDK-first `devices` and `device_access` surfaces plus skill migration off direct service imports.
- [ ] Phase 7: unified settings contract and command-profile surface.
- [ ] Phase 8: browser and member convergence cleanup, reconciler rollout, and `connected_to_subnet` migration.
- [ ] Phase 9: issuer-side autorotation.
- [ ] Phase 10: system-model, voice, and automation integration.
