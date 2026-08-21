# Distributed Topology Production Review - 2026-08-21

Status: bounded trusted-subnet pilot only. Production acceptance is rejected
until every open gate in this review has physical evidence.

This review covers the generic distributed service/data contracts used by the
Media Center Project. It does not make Media Center behavior part of core and
does not authorize cross-subnet placement or a general multi-writer profile.

## Candidate Boundary

| Surface | Candidate | Review boundary |
| --- | --- | --- |
| AdaOS core | `rev2026` release candidate based on `0.1.917` | service definition v2, exact release overlap, membership, fencing, routes, transfer and deployment control |
| Media Center Project | `0.6.45` | representative coordinator plus node-local agents and retained external media |
| Deployment scope | two trusted physical nodes in one subnet | selected-node staged rollout; separate TV/controller presentations |

Exact commit, package and release digests must be copied from the normal
published build and stand receipts. A working-tree or overlay run is not
acceptance evidence.

## Security Review

- Node selection is restricted to admitted trusted node identities. A service
  instance is identified by node, component activation, release digest,
  topology generation and lease identity; a same-name or historical process
  cannot satisfy current placement.
- Component deployment accepts only exact immutable package and ProjectRelease
  digests. Fetch, verify, stage, activate, health and commit remain journaled
  phases; partial subnet success is reported instead of being described as an
  atomic cross-node commit.
- Topology mutation uses an immutable reviewed plan and idempotency key.
  Definition compare-and-switch rejects stale versions. Authority transfer is
  fenced by a monotonic epoch and lease; old-authority writes and stale route
  grants fail closed.
- Service definition v2 permits only a bounded list of exact compatible release
  digests. The previous desired release and every live membership release must
  be admitted before definition mutation. The old digest is removed only after
  every live instance has converged to the new release.
- Public SDK and operator projection v2 expose bounded identifiers, states,
  counts, digests and failure classes. Subscribed recent-operation rows omit
  adapter receipts and idempotency payloads; full details remain behind the
  authorized cursor API. Projections do not expose credentials, package
  authorization records, source bytes or raw node-local media paths.
- Adapter terminal failures and remote service, topology and Project deployment
  failures retain only bounded machine-code strings without credential markers;
  successful remote receipts are bounded and sanitized too. Skills map those
  stable codes to their own localized human-readable messages.
- Automatic cross-subnet placement and general multi-writer behavior remain
  deferred. Their threat and conflict models are not inherited from the
  trusted-subnet pilot.

Open security proof: repeat incompatible-release, stale epoch, expired lease,
revoked instance and unauthorized retention mutation rejection against the
exact published candidate on the stand. Export sanitized receipts.

## Privacy And Retention Review

- Original media remains external data at its source node. Deployment,
  indexing, replication, drain and component removal must not copy or delete
  those bytes.
- The replicated Media Center catalog contains derived metadata, opaque source
  references, indexes and bounded artwork/rendition references. Replica data,
  derived data, package data and external source data have separate retention
  decisions.
- Public catalog and topology projections use logical resource and node ids.
  Node-local paths are available only to the owning agent and authorized
  operator diagnostics; media delivery activity records only aggregate
  audio/video/other stream counts.
- Logs and operation evidence are bounded and sanitized. Transfer receipts may
  expose byte counts, checkpoints, hashes, retries and phases, but never payload
  contents or credentials.

Open privacy proof: after staged update, drain and removal, record filesystem
and catalog witnesses showing retained external source bytes, the reviewed
replica/derived-data decision and absence of source paths in exported
diagnostics.

## Resource And Soak Review

- Deployment and topology operations are durable and asynchronous; browser or
  tool RPC lifetime does not bound slow-disk installation or transfer.
- Transfer plans include estimates and use bounded chunks, retries,
  cancellation, temporary space and resource-pressure admission. Interrupted
  transfer cannot advertise a partial replica as ready.
- Membership, leases, operation histories and projections are bounded. Catalog
  reads use cursor pagination; background indexing/enrichment and rendition
  work have concurrency, RSS, I/O and disk limits.
- Active HTTP/range or direct WebRTC DataChannel source delivery holds a
  path-free lease. Supervisor defers a disruptive hub/member update while that
  source delivery is active.
- The server acceptance workload is one hour with at least 20,000 catalog
  records, concurrent FTS/page/playback reads and continuous agent deltas. TV
  acceptance separately measures renderer/main-thread/GPU CPU, heap growth,
  Long Tasks, input latency, UI/Yjs mutation pressure, decode and dropped
  frames during browse, playback, reconnect and update.

Local server proof passed on 2026-08-21 for 3,600.063 seconds, 20,000 items and
307,950 agent deltas with zero errors, 39.02 MiB peak RSS, 0.793 MiB sustained
RSS growth and 13.533% CPU p95. The JSON is retained at
`.adaos/state/codex/evidence/media-center-server-acceptance-soak-2026-08-21.json`.
Open resource proof: attach passing one-hour Android TV JSON/screenshots,
source/update CPU and disk-I/O evidence, and a member update during active
playback. A shortened desktop run is diagnostic only.

## Rolling Upgrade Runbook

1. Publish and verify the exact core and ProjectRelease; record package digests
   before changing desired deployment state.
2. Read the current service definition and live memberships. Reject rollout if
   either is stale, unavailable or reports an unexpected release.
3. Compare-and-switch to definition v2 whose desired release is the candidate
   and whose bounded overlap admits the previous desired release plus every
   live release.
4. Apply batch one while retaining at least one ready old-release route. Verify
   new component activation, membership identity, health, route and data
   checkpoint before continuing.
5. Roll remaining nodes one at a time. Stop on uncertain health, stale
   membership, transfer mismatch, epoch regression, route loss or resource
   budget violation.
6. After all live memberships report the candidate release and checkpoint,
   compare-and-switch a later definition revision that removes the old digest.
7. Drain obsolete activations. Apply package, replica, derived-data and external
   data retention decisions independently and record each result.

Rollback is a new reviewed desired revision. Re-admit the previous exact
release while it is still installed and data-compatible, roll nodes through the
same bounded overlap, and never lower an authority epoch or replace a newer
checkpoint with an older snapshot. If compatibility or freshness cannot be
proved, cordon the candidate and keep the last verified route instead of
attempting an in-place downgrade.

## Operator Recovery Runbook

1. Inspect deployment desired/observed generation and operation journal by id.
   Distinguish running, retrying, uncertain, failed, rolled back and complete.
2. Inspect service definition version/release overlap, live memberships,
   activation identities and lease expiries. Cordon stale or duplicate
   instances before topology mutation.
3. Inspect partition revision, authority epoch, checkpoint/watermark and every
   replica witness. Never infer freshness from process health alone.
4. Prefer planned handoff to a verified follower. For unplanned loss, promote
   only a follower that satisfies the declared freshness policy and fence the
   old authority with a higher epoch.
5. Explain route eligibility after recovery and probe the selected route. Keep
   historical replicas ineligible until their membership, lease and checkpoint
   are current.
6. Reconcile a durable accepted operation instead of issuing an unrelated
   replacement command. Use explicit retention confirmation for replica or
   package cleanup.
7. Export the bounded deployment/topology timeline, exact revisions and
   sanitized diagnostics before removing recovery evidence.

Escalate rather than mutate when trusted node identity, exact activation,
definition version, authority epoch or checkpoint cannot be established.

## Production Decision Gates

| Gate | Required evidence | Current decision |
| --- | --- | --- |
| Compatible rolling release | normal ProjectRelease rollout on two physical nodes; old route retained; all live memberships converge; overlap then removed | open |
| Failure and recovery | planned handoff, unplanned authority loss, stale owner rejection and retained checkpoint on exact candidate | repeat on candidate |
| Security/privacy | rejection matrix plus sanitized export and retained external-data witness | open |
| Resource/soak | passing server and Android TV one-hour gates plus update-under-playback QoE/I/O | server passed; TV/update proof open |
| Product E2E | separate TV and authorized controller with Now Playing, D-pad/touch, reconnect/resume, i18n and screenshots | open |
| Operator recovery | runbook executed by operation ids with route/checkpoint evidence and reviewed rollback result | open |

Production remains rejected while any row is open. Passing contract tests or a
single successful deployment cannot change this decision by implication.

## Related Evidence

- [Distributed Media Center Stand Validation - 2026-08-21](distributed-media-center-stand-validation-2026-08-21.md)
- [Distributed Runtime Conformance - 2026-08-20](distributed-runtime-conformance-2026-08-20.md)
- [Media Center Security And Privacy Review - 2026-08-20](media-center-security-review-2026-08-20.md)
- [Distributed Service And Data Topology](distributed-service-and-data-topology.md)
- [Distributed Service And Data Topology Roadmap](distributed-service-and-data-topology-roadmap.md)
- [Distributed Media Center Roadmap](media-center-roadmap.md)
