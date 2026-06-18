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
code: D | R | L | W | U | ! | X
label: Online direct | Online via relay | Limited | Recovering | Updating | Action required | Offline
impact: none | slow | reconnect_expected | partial_features | unavailable
reason: stable | relay_fallback | yjs_stale | rtc_cooldown | update_transition |
        member_unavailable | server_opt_out | auth_required | offline
scope: browser | sync | media | hub | member | update
```

The summary must be stable enough for a mobile header. It should not expose
debug tokens as the primary text.

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
members: total, online, stale, offline, updating, unknown
media_capable: ready/total
direct_candidates: ready/total
blocking_members: ids with reason
```

The first browser implementation may use best-effort `data.nodes` evidence.
The target implementation must expose a canonical API/projection so the client
does not infer member health from ad hoc node payload shapes.

## Update Integration

Update state is availability-affecting:

- planned runtime switch can make reconnect expected instead of anomalous;
- member update may be deferred because live media is active;
- hub update must account for sidecar continuity;
- failed transition should become `blocked` or `limited` with an action reason.

The summary should prefer update-aware labels when degradation is caused by a
known transition, for example `Updating` instead of `Recovering` or
`link degraded`.

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
- [x] `[should]` Add canonical backend member availability to reliability
  summary instead of relying on best-effort client inference from `data.nodes`.
- [ ] `[should]` Add tests for summary state selection: direct, relay,
  recovering, updating, blocked, offline, member-stale, and Yjs-stale cases.
- [ ] `[could]` Add per-member expandable details after the aggregate source is
  canonical.
