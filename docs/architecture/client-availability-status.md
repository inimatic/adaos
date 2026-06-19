# Client Availability Status

## Goal

Give the browser a compact, user-readable availability model that explains
whether AdaOS is usable now, why it is degraded when it is degraded, and what
scope is affected.

This document covers the browser-facing projection. It does not replace the
lower-level contracts in:

- [Realtime Reliability Roadmap](realtime-reliability-roadmap.md)
- [AdaOS Realtime Sidecar](adaos-realtime-sidecar.md)
- [Member-Hub Connectivity](member-hub-connectivity.md)
- [AdaOS Supervisor](adaos-supervisor.md)

## Status Labels

Checklist items use the same MoSCoW vocabulary as
[Builder Roadmap](builder-roadmap.md):

- `[must]`: required for the user-facing availability contract.
- `[should]`: important clarity, diagnostics, or operator-confidence work.
- `[could]`: useful optional polish.
- `[deferred]`: intentionally later because the source contract is not ready.

## Current Problem

The client currently has enough raw evidence, but the header collapses it into
two technical badges:

- link summary: command, sync, route, media, RTC, sidecar, browser route, and
  state-sync are concatenated into one debug string.
- Yjs signal: provider, materialization, widget data, replay, and stale-state
  evidence are collapsed into `YJS green` or `YJS red`.

This is accurate for engineers but ambiguous for users. Examples:

- `rtc=connected` can be true while sync still uses `yws`.
- `YJS green` can be true while the browser control route is degraded.
- `link degraded` can mean network instability, planned update transition,
  server-side WebRTC/Yjs opt-out, member unavailability, or Yjs pressure.

## Target Model

The browser should expose one availability summary plus drill-down evidence.

### Summary

`AvailabilitySummary` is the top-level user contract:

```text
state: ready | relay | limited | recovering | updating | blocked | offline
code: D | R | L | W | U0 | U1 | U! | ! | X
label: Online direct | Online via relay | Limited | Recovering |
       Updating 0 | Updating 1 | Update failed | Action required | Offline
impact: none | slow | reconnect_expected | partial_features | unavailable
reason: stable | relay_fallback | yjs_stale | rtc_cooldown | update_transition |
        member_unavailable | server_opt_out | auth_required | offline
scope: browser | sync | media | hub | member | update
```

The summary must be stable enough for a mobile header. It should not expose
debug tokens as the primary text.

Expanded desktop presentation uses the icon plus the label and hides the code.
Compact mobile presentation uses the icon plus the 1-2 character code and hides
the label. The narrowest form must still fit both icon and code; it must not
render icon, code, and full label together.

### Detail Lanes

The details panel expands the summary into lanes:

- `Browser`: current command/control path and browser route health.
- `Sync`: WebRTC Yjs, YWS relay, first-sync, freshness, materialization, and
  Yjs pressure.
- `Media`: WebRTC media, HTTP relay, local preview, upload/playback/broadcast.
- `Hub`: sidecar, hub-root, slot/update visibility, and root-routed ingress.
- `Members`: online/stale/offline aggregate and per-member blockers when a
  canonical member availability source exists.
- `Update`: idle, preparing, switching, validating, failed, deferred, and
  expected user impact.

Raw diagnostics remain available as a copy action. They are not the primary UI.

The panel must also include a `Limitations` row whenever `state != ready` or
when the effective path is only partially direct. That row summarizes what the
user should expect to be missing, for example relay-only commands, stale state
sync, root-routed media, incomplete sidecar handoff, or unavailable members.

## Mobile Rule

The mobile header has room for at most one compact availability affordance.

Required behavior:

- show one icon or 1-2 character code in the header;
- hide separate link/Yjs/update text badges on narrow screens;
- open the detail panel on tap;
- keep the panel scannable with lane labels and short values;
- keep raw diagnostics behind an explicit copy action.

Desktop may continue to show richer badges, but the same detail panel should be
available so the interpretation is consistent.

## Member Availability

Member availability belongs in the same model because user-visible channel
quality depends on available devices, not only on browser-hub transport.

Target member aggregate:

```text
members: total, online, stale, offline, updating, unknown, excluded
media_capable: ready/total
direct_candidates: ready/total
blocking_members: ids with reason
```

The first browser implementation may use best-effort `data.nodes` evidence.
The target implementation must expose a canonical API/projection so the client
does not infer member health from ad hoc node payload shapes.

`total` is the active accountable member count. Members that have existing
device-policy lifecycle states such as `revoked`, `expired`, `disabled`,
`ignored`, `retired`, or `deleted` are reported under `excluded` and must not
keep the hub in `Limited`. `knownTotal` can still expose the raw inventory size
for diagnostics.

## Update Integration

Update state is availability-affecting:

- planned runtime switch can make reconnect expected instead of anomalous;
- member update may be deferred because live media is active;
- hub update must account for sidecar continuity;
- failed transition should become `blocked` or `limited` with an action reason.

The summary should prefer update-aware labels when degradation is caused by a
known transition, for example `Updating` instead of `Recovering` or
`link degraded`.

Update stages:

- `U0` / `Updating 0`: preparation, validation, countdown, candidate readiness,
  or deferred/promoted work where hub access is expected to remain available.
- `U1` / `Updating 1`: runtime switch, slot cutover, applying transition,
  restart, or shutdown window where reconnects are expected.
- `U!` / `Update failed`: failed transition that needs operator attention.

## Checklist

- [x] `[must]` Keep low-level semantic channel evidence for command, sync,
  route, media, RTC runtime, direct recovery, and link upgrade.
- [x] `[must]` Keep Yjs runtime/materialization evidence separate from generic
  link health.
- [x] `[must]` Add a browser `AvailabilitySummary` projection that combines
  semantic channel state, Yjs state, sidecar/root route state, update state, and
  member aggregate evidence.
- [x] `[must]` On mobile, render one compact availability affordance and move
  detailed status into a tap-open panel.
- [x] `[must]` Include update state in the availability summary so planned
  restart/switch windows are not shown as unexplained network degradation.
- [x] `[must]` Include member availability aggregate in the detail panel.
  The client prefers canonical `memberAvailability` from reliability summary
  and falls back to best-effort `data.nodes` only for older runtimes.
- [x] `[must]` Keep raw diagnostic strings copyable for support/debugging.
- [x] `[must]` Surface server-side WebRTC/Yjs opt-out state as a concrete
  blocker when it prevents `webrtc_data:yjs`.
- [x] `[must]` Open the availability detail panel on click/tap, including on
  mobile where hover hints are unavailable.
- [x] `[must]` Use expanded label-only and compact code-only header
  presentation; do not render icon, code, and full label together.
- [x] `[must]` Split update presentation into `U0`, `U1`, and `U!` so safe
  preparation differs from expected reconnect windows and failed transitions.
- [x] `[must]` Add a `Limitations` row that explains what `Limited` or relay
  availability currently removes or delays.
- [x] `[should]` Add canonical backend member availability to reliability
  summary instead of relying on best-effort client inference from `data.nodes`.
- [x] `[should]` Exclude revoked/expired/disabled/retired member policies from
  active member availability totals while keeping raw inventory evidence.
- [ ] `[should]` Add full tests for summary state selection: direct, relay,
  recovering, blocked, offline, and Yjs-stale cases.
- [x] `[should]` Add focused tests for staged updates, limitations, and member
  lifecycle exclusion.
- [ ] `[should]` Add Infrastate device/member actions to revoke, disable,
  retire, or delete obsolete member records through the existing device policy
  lifecycle instead of requiring manual data edits.
- [ ] `[could]` Add per-member expandable details after the aggregate source is
  canonical.
