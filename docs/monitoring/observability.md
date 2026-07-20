# Observability

Status: navigation and responsibility map.

AdaOS observability exists to support operators, deterministic guards, release
verification, and governed repair. Raw logs are diagnostic inputs; they are not
the normal product interface and are not durable development work by
themselves.

The intended evidence path is:

```text
logs / metrics / traces / runtime signals
  -> deterministic checks and domain attribution
  -> normalized incidents and status
  -> release or post-deploy evidence
  -> optional AdaOS Issue after Support triage
```

## Authoritative Documents

- [Runtime Guarding](../architecture/runtime-guarding.md) owns resource,
  pressure, quarantine, and hard-safety policy.
- [Incident Registry](../architecture/incident-registry.md) owns normalized,
  domain-attributed operational incidents.
- [Operational Event Model](../architecture/operational-event-model.md) owns
  event and projection lifecycle contracts.
- [Semantic State Plane](../architecture/semantic-state-plane.md) owns the
  separation of connectivity, synchronization freshness, and pressure.
- [Post-Deploy E2E Testing](../architecture/post-deploy-e2e-testing.md) owns
  release-linked verification evidence.
- [Version Observability](../architecture/version-observability.md) owns source,
  served, target, used, and active-version distinctions.
- [Governed Evolution](../architecture/governed-evolution.md) defines how
  structured operational evidence may become support or repair work without
  making raw-log analysis the default agent behavior.
- [Issue Tracker](../issue-tracker.md) records active repository incidents and
  acceptance evidence until a dedicated AdaOS Issue system exists.

## Current Boundary

AdaOS already exposes observe endpoints, reliability summaries, status cards,
runtime diagnostics, guard evidence, and an initial Incident Registry. The
coverage and durability of those surfaces vary by subsystem. A feature must not
claim production observability merely because it emits logs or has a debug
endpoint.

Current implementation state and the next repository-wide acceptance gates are
tracked in the [MVP Roadmap](../mvp_roadmap.md) and the owning domain roadmaps.
This page intentionally contains no competing checklist.
