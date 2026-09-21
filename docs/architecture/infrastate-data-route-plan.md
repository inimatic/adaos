# Infrastate Compatibility Route

Status: compatibility boundary. New product code must not depend on this
route. Target ownership and deletion gates live in
[Infrascope Retirement Architecture](infrascope.md) and the
[Infrascope Retirement Roadmap](infrascope-roadmap.md).

## Decision

The earlier plan to evolve `infrastate_skill` into a permanent operational
control plane is superseded. Infrastate is not a target application or SDK
boundary. Its accepted Stable release remains temporarily available only so
the current Stable desktop is not broken while the replacement Desktop beta
is qualified.

New consumers use these neutral owners:

| Capability | Authority | Product surface |
| --- | --- | --- |
| Application inventory, Marketplace, lifecycle, channel and placement | Applications SDK/MCP | Applications |
| People, grants, invitations, sessions and device trust | access SDK/MCP | Users & Access |
| Nodes, browsers and connection state | control-plane SDK | Desktop Devices |
| Health, services, connections, quotas, incidents and update state | `adaos.sdk.system` | Desktop System |
| Operations, logs, events and reports | operation and diagnostic SDK/MCP | Desktop Activity/Development and Builder |
| Configuration and secrets | typed configuration SDK/MCP | Desktop Settings/System |

## Compatibility Rules

- `/api/node/infrastate/snapshot`, `/api/node/infrastate/action`, the Client
  extension, and the Workspace skill are deprecated aliases for the accepted
  Stable surface. They receive no new behavior.
- Setup presets, tool routing, member-link publication, scenario projection,
  and Yjs load marks must use neutral contracts. These active Core paths no
  longer depend on Infrastate.
- `project:ops_infrascope` no longer composes `skill:infrastate_skill` for new
  installations.
- The compatibility skill may be physically deleted only after the replacement
  Desktop beta is accepted and a clean boot plus local/routed browser matrix
  passes with the skill disabled.
- Compatibility routes must be observable and removed after one release with
  zero supported use. They may not become a second cache or source of truth.

## Current Migration State

- [x] Applications owns product inventory and lifecycle.
- [x] Desktop uses the section-driven `adaos.sdk.system` read model.
- [x] Active member-link Infrastate projection publication is removed.
- [x] Infrastate-specific scenario mirroring and Yjs load-mark naming are
  removed from active Core paths.
- [x] Default setup activation and tool-bridge special routing are removed.
- [x] New Infrascope installations no longer depend on Infrastate.
- [ ] Publish and qualify the replacement Desktop beta locally and through the
  routed external client.
- [ ] Add compatibility-use telemetry and prove zero use.
- [ ] Remove the Workspace skill, Client extension, deprecated Node API aliases,
  Stable NLU references, and retained runtime data.

The old receiver-by-receiver migration checklist was historical implementation
observation and is intentionally not retained as a parallel target plan.
