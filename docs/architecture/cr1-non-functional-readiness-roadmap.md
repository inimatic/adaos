# CR 1.0 Non-Functional Readiness Roadmap

Status: release-gating roadmap for CR 1.0. Product roadmaps continue to own
features; this document owns cross-cutting measurable quality gates.

Status markers: `[x]` complete, `[~]` partial, `[ ]` open.

## NFR0. Reproducible Baselines

- [x] `[must]` Add a section-isolated system-surface benchmark with cold-start,
  warm p50/p95/max, payload size, RSS delta, timeout, and machine metadata.
- [ ] `[must]` Run the same benchmark profile in local and routed modes and
  retain release evidence outside source documentation.
- [ ] `[must]` Define versioned workload fixtures for empty, ordinary, and
  upper-bound Application, user, device, operation, and widget counts.
- [ ] `[must]` Publish one release qualification command that runs contract,
  migration, browser, restart/reconnect, and benchmark gates.

## NFR1. Latency And Responsiveness

- [~] `[must]` Keep warm read-only Desktop sections below 100 ms p95 locally.
  The first `adaos.sdk.system` benchmark is below 27 ms p95 after warm-up.
- [ ] `[must]` Reduce cold system section materialization below 1 s locally.
  The current reliability-dependent cold path is approximately 3.5-3.7 s and
  fails this gate.
- [ ] `[must]` Keep ordinary user commands below 250 ms to accepted/visible
  progress; long-running work must return an operation id instead of blocking.
- [ ] `[should]` Keep wide and compact first useful paint below 2 s on the
  reference machine and below 4 s through the routed client.
- [ ] `[must]` Eliminate duplicate snapshot construction through request
  coalescing, bounded caches, invalidation, and section-specific producers.

## NFR2. Resource Efficiency

- [ ] `[must]` Establish idle and active CPU/RSS/handle/thread baselines for API,
  sidecar, browser, and Builder/Codex workers.
- [ ] `[must]` Prove bounded memory over an eight-hour idle soak and a two-hour
  scenario-switch, Yjs reconnect, widget, and operation workload.
- [ ] `[must]` Define payload and frequency budgets for Yjs, WebIO streams,
  status cards, MCP results, and browser materialization.
- [ ] `[should]` Add cache hit/miss, build duration, payload bytes, and stale-age
  metrics for expensive canonical projections.

## NFR3. Reliability And Recovery

- [ ] `[must]` Pass graceful restart, forced termination, sidecar handoff,
  YWS/WebRTC reconnect, scenario switching, and stale projection recovery.
- [ ] `[must]` Eliminate periodic YWS 1006 and transient previous-scenario
  restoration, or provide a proven bounded recovery contract with no stale
  interaction.
- [ ] `[must]` Prove operation idempotency and reconciliation for install,
  update, remove, migration, rollback, invitation, and permission mutation.
- [ ] `[must]` Preserve stable data, settings, secrets, and access grants across
  beta migration, acceptance, restart, and forward-only rollback.

## NFR4. Security And Privacy

- [ ] `[must]` Run permission allow/deny tests for owner, coordinator, viewer,
  child, guest, revoked user, and untrusted browser.
- [ ] `[must]` Verify that browser projections, logs, diagnostics, screenshots,
  LLM context, and Dev Tickets redact secrets and unauthorized user data.
- [ ] `[must]` Threat-model invitation, recovery, pairing, routed MCP, media,
  application install, and autonomous Builder/Codex paths.
- [ ] `[must]` Produce dependency, secret, static-analysis, and artifact-signing
  evidence for each release candidate.

## NFR5. Compatibility And Evolvability

- [ ] `[must]` Validate all Workspace applications against the current UI ABI,
  SDK contracts, manifests, and data migrations without hidden legacy fallbacks.
- [ ] `[must]` Prove clean-node and upgraded-node installation with identical
  accepted behavior.
- [ ] `[must]` Track deprecated APIs with callers, usage telemetry, removal
  version, and owner; CR 1.0 must not add new consumers of deprecated surfaces.
- [ ] `[should]` Establish compatibility budgets for Client ABI, public SDK,
  MCP tools, persisted records, and release artifacts.

## NFR6. Operability And Supportability

- [ ] `[must]` Provide authorized searchable logs, events, operation history,
  test reports, and browser diagnostics to users and Builder/Codex without
  arbitrary filesystem reads.
- [ ] `[must]` Correlate user command, operation, projection, browser action,
  release, migration, and Dev Ticket through stable trace identifiers.
- [ ] `[must]` Define SLO indicators and alert thresholds for availability,
  command acceptance, fresh state sync, reconnect, operation failure, and
  migration failure.
- [ ] `[should]` Export a bounded support bundle with consent, redaction,
  provenance, and retention controls.

## Release Gate

CR 1.0 is not ready while any `[must]` item in latency, resource bounds,
recovery, security, migration integrity, or clean/upgraded installation lacks
reproducible evidence. Feature completeness does not waive these gates.
