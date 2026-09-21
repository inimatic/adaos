# Infrascope Retirement Roadmap

This roadmap tracks decomposition and removal of the legacy Infrascope product.
Target ownership and deletion rules are defined in
[Infrascope Retirement Architecture](infrascope.md).

Status markers: `[x]` complete, `[~]` partial, `[ ]` open.

## IR0. Freeze And Baseline

- [x] `[must]` Mark Applications as the sole product inventory authority.
- [x] `[must]` Remove product Inventory from the current DEV Infrascope
  composition while retaining technical diagnostics.
- [x] `[must]` Preserve `ops_infrascope@0.10.24` as a non-Beta recovery
  checkpoint.
- [x] `[must]` Inventory legacy project components and Core/Client coupling.
- [ ] `[must]` Add compatibility-usage telemetry for legacy routes, receivers,
  projection paths, actions, and setup activation.
- [ ] `[must]` Verify a clean boot with Infrascope and Infrastate disabled and
  record every missing producer or consumer.

## IR1. Move Product Responsibilities

- [~] `[must]` Move Application inventory, Marketplace, lifecycle, release,
  permissions, component topology, and placement to Applications. Authoritative
  inventory and desired/observed placement reads are live in Applications Beta;
  reviewed relocation/drain and full lifecycle failure/retry qualification stay
  open under `APP4`.
- [~] `[must]` Move people, invitations, grants, recovery, sessions, and device
  trust to Users & Access. The Root-backed Beta is active; richer permission
  mutation and some connected-session cutoff UX remain open in the identity and
  access roadmap.
- [~] `[must]` Move node/browser/device/link state to Desktop Devices. Pairing,
  filtering, and canonical device merge exist; complete remote-node and
  connected-browser parity still needs a browser matrix.
- [~] `[must]` Move compact reliability, update, service, connection, quota,
  and incident status to Desktop System and Activity without copying the
  complete operator cockpit. The section-driven `adaos.sdk.system` read model
  and Desktop views are implemented; `web_desktop@0.3.51` passed immutable
  checkpoint, isolated Trial, and local Beta acceptance. Routed browser
  acceptance remains open.
- [ ] `[must]` Move typed environment/configuration views to Desktop Settings
  and System with policy, audit, drift, restart impact, and opaque secrets.
- [ ] `[must]` Move Root MCP/Codex leases and operator access workflows to
  Users & Access or Development according to actor and purpose.

## IR2. Shared Diagnostics

- [ ] `[must]` Publish neutral searchable log/event/report MCP contracts for
  Desktop Development, Builder, and Codex. Do not expose arbitrary local paths.
- [~] `[must]` Publish neutral operation and system-health projections with
  bounded detail and stable object references. System reads are available via
  `adaos.sdk.system`; policy-checked operation commands and detail links remain
  open.
- [ ] `[must]` Move core update, slot, validation, cancel, rollback, drain, and
  recovery actions behind policy-checked system operation commands.
- [ ] `[must]` Retain stream budgets, projection demand, freshness, and guard
  evidence while replacing `infrastate.*` receiver names.
- [ ] `[should]` Add deep links from Applications component placement and
  Desktop health summaries to the neutral diagnostic object detail.

## IR3. Neutralize Core And Client

- [~] `[must]` Replace `/infrastate/*` Node API routes and
  `infrastate.action` with neutral system/operation contracts. The routes are
  deprecated compatibility aliases; mutation replacement and measured zero-use
  evidence remain open.
- [x] `[must]` Remove active member `infrastate` payload publication and use
  versioned reliability/runtime projection names for new consumers.
- [x] `[must]` Remove Infrastate-specific aliasing from scenario projection and
  use neutral Yjs load-mark naming.
- [ ] `[must]` Remove the Client Infrastate product extension after equivalent
  declarative actions use generic SDK/MCP commands.
- [x] `[must]` Remove setup-preset activation and tool-bridge allowlist entries
  for legacy skills.
- [~] `[should]` Rename NLU, projection-pilot, Builder compatibility, and test
  fixtures to neutral specimens so platform tests do not imply a product
  dependency. Active NLU and projection-pilot examples are neutral; the Builder
  compatibility pack remains.
- [x] `[must]` Prove no Core module imports or loads an Infrascope/Infrastate
  skill to provide canonical state; Core reads are built by public SDK and
  system-model services.

## IR4. Remove Legacy Product

- [~] `[must]` Stop publishing and installing `project:ops_infrascope`. New
  composition no longer depends on `skill:infrastate_skill`; the recovery
  checkpoint remains until replacement acceptance.
- [ ] `[must]` Remove `scenario:infrascope`, `skill:infrascope_skill`, and
  `skill:infrastate_skill` after the zero-use gate passes.
- [ ] `[must]` Retain, rename, or remove `infra_access_skill`, `subnet_env`, and
  `service_doctor_skill` independently according to their migrated neutral
  contracts; do not delete useful platform behavior merely because it was
  packaged with Infrascope.
- [ ] `[must]` Remove legacy projection data and runtime state according to an
  explicit retention/export policy.
- [ ] `[must]` Remove compatibility aliases and legacy API routes after one
  observed release with zero use.
- [ ] `[must]` Run local and routed wide/compact browser qualification plus
  restart, reconnect, update, access, and remote-node tests with all legacy
  components absent.

## Exit Evidence

Retirement is complete only when:

- Desktop, Applications, Users & Access, and Builder cover the accepted use
  cases without Infrascope installed;
- Core health, updates, operations, diagnostics, configuration, and access
  remain available through neutral contracts;
- no supported source or runtime record references the legacy scenario, skills,
  routes, actions, receivers, or projection paths;
- a clean node and an upgraded node both pass the same acceptance suite.
