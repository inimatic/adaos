# Incident Registry

This document defines the production direction for AdaOS operational incident
collection. The goal is not another log stream. The goal is a domain-shaped
registry that turns transport, runtime, sync, skill, and resource-pressure
symptoms into evidence that humans and LLM planning tools can reason about.

Read this with:

- [Hub-Browser Connectivity](hub-browser-connectivity.md)
- [Hub-Root Protocol](hub-root-protocol.md)
- [Runtime Guarding](runtime-guarding.md)
- [Semantic State Plane](semantic-state-plane.md)
- [Skill Projection Runtime SDK](skill-projection-runtime-sdk.md)

## Problem

Recent stand checks showed that a browser can report a healthy logical channel
while the runtime still emits warning-grade evidence such as:

- supervisor reliability preflight `ReadTimeout` against `127.0.0.1:8777`
- route relay `no_upstream` / `forced_close_no_upstream` transitions
- slow `webio.stream.*` or `webio.yjs.*` event handlers
- Yjs write pressure attributed to core or a skill owner
- IO pressure that delays otherwise healthy local APIs

Raw logs are not enough for follow-up engineering. A timeout needs domain
context: did it belong to core runtime, sidecar, hub-root route, a skill
handler, Yjs storage, or a member/browser endpoint?

## Current Implementation

The first production slice is intentionally small and in-process:

- `src/adaos/services/incident_registry.py` stores a bounded in-memory incident
  registry.
- `reliability_snapshot()` exposes it as `runtime.incident_registry`.
- Canonical reliability projection includes registry items in its `incidents`
  list so LLM-oriented control-plane consumers do not need to parse logs.
- Eventbus records:
  - `slow_event_handler`
  - `event_handler_crash`
- Supervisor records:
  - `runtime_api_timeout` for runtime reliability preflight failures
- Reliability channel diagnostics record:
  - `channel_transition` for root-control and root-browser route incidents

The v1 registry is volatile across process restart. That is acceptable for the
first slice because the immediate need is runtime attribution and LLM context,
not long-term audit retention.

## Incident Shape

Every incident has:

- `id`: stable short incident id derived from a fingerprint
- `class`: normalized incident class
- `signal`: precise observed signal
- `severity`: `info`, `warning`, `degraded`, or `critical`
- `domain`: owner-style attribution such as `core.runtime`,
  `core.sidecar`, `hub_root`, `hub_root_browser`, `skill:<name>`,
  `member:<node>`, or `browser:<device>`
- `component`: optional lower-level component such as `eventbus`,
  `runtime_reliability_api`, or `route`
- `summary`: human-readable operator summary
- `occurrence_count`: count of merged sightings for the same fingerprint
- `first_seen_at` / `last_seen_at`
- `active`: true while the last sighting is inside the active window
- `latest_evidence`: bounded structured evidence
- `tags`: routing hints such as `latency`, `transport`, `eventbus`, or
  `blocking-evidence`

Evidence must be sanitized. Tokens, credentials, bearer headers, and secrets
must never be stored in registry payloads.

## Domain Attribution

The registry should attribute before it summarizes.

Current rules:

- event handler labels with `skill=<name>` become `skill:<name>`
- runtime reliability API timeouts become `core.runtime`
- root control channel incidents become `hub_root`
- route channel incidents become `hub_root_browser`
- process snapshots under `.adaos/workspace/skills/.runtime/<skill>` become
  `skill:<skill>`
- supervisor and sidecar command lines become `core.supervisor` and
  `core.sidecar`

Target rules:

- Yjs pressure owner and route metadata should map to skill/core/domain owners
  without string parsing.
- Browser diagnostics should submit client-side route, WebRTC, and provider
  failure windows as registry candidates.
- Member link diagnostics should map incidents to `member:<node_id>`.
- Root MCP should preserve domain attribution when presenting subnet-level
  incidents to LLM tools.

## Runtime Timeout Evidence

For `runtime_api_timeout`, the registry captures local blocking evidence:

- `/proc/pressure/io`
- `/proc/pressure/cpu`
- `/proc/pressure/memory`
- top processes by RSS
- top processes by cumulative write bytes
- process-domain hints derived from command line paths

This is deliberately diagnostic, not proof. Cumulative process IO is only a
hint. Production-grade attribution should add short delta sampling around the
incident window.

## Production Pipeline

Target pipeline:

1. Source emits a normalized candidate incident close to where the signal is
   observed.
2. Registry merges by fingerprint and increments occurrence count.
3. Registry enriches with cheap local evidence when the incident class needs
   it.
4. Reliability exposes the compact registry.
5. Status cards and canonical projections surface active incidents to humans,
   browser UI, and LLM agents.
6. Root MCP aggregates subnet incidents across hub/member/root surfaces.
7. Planning tools group incidents by domain and propose remediation tasks.
8. Repeated or critical incident classes trigger guard actions only after the
   policy owner has been explicitly defined.

## First Incident Classes

Implemented:

- `runtime_api_timeout`: supervisor could not read runtime API within the
  caller timeout.
- `slow_event_handler`: eventbus handler exceeded the configured slow-handler
  threshold.
- `event_handler_crash`: eventbus handler raised an exception.
- `channel_transition`: reliability channel recorded a non-ready or forced
  transition.

Next:

- `io_pressure`: PSI crosses an explicit budget for a sustained window.
- `yjs_pressure`: owner guard warning/throttle/block with owner attribution.
- `browser_transport_fallback`: browser moved from preferred WebRTC path to
  WS/YWS/root relay.
- `action_timeout`: command/action timeout with route, skill, and scenario
  metadata.
- `member_link_stale`: member advertised in subnet directory but no fresh link
  exists.

## Checklist

Implemented:

- [x] Add bounded in-memory incident registry.
- [x] Record supervisor runtime reliability preflight timeouts.
- [x] Record slow/crashing eventbus handlers.
- [x] Record root-control/root-browser reliability channel incidents.
- [x] Expose registry through `runtime.incident_registry`.
- [x] Include registry incidents in canonical reliability projection.

Required before production acceptance:

- [ ] Persist recent incidents across runtime restart with TTL and size limits.
- [ ] Add delta-based process IO sampling for blocking-process evidence.
- [ ] Add Yjs pressure incident emission from owner guard/load-mark policy.
- [ ] Add browser-side incident submission for route/WebRTC/YWS fallback
      windows.
- [ ] Add action timeout incidents with skill/scenario/action metadata.
- [ ] Add CLI/API view for registry-only inspection.
- [ ] Add status-card materialization for active high-severity incidents.
- [ ] Add Root MCP aggregation over hub/member/root incidents.
- [ ] Add post-deploy soak assertions for no repeated `runtime_api_timeout`,
      no route flapping, and no unbounded eventbus backlog.
- [ ] Define guard policies that can act on repeated critical incidents without
      causing restart loops or hiding the original evidence.
