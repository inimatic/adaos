# Distributed Topology Production Review - 2026-08-21

Status: bounded trusted-subnet pilot only. Production acceptance is rejected
until every open gate in this review has physical evidence.

This review covers the generic distributed service/data contracts used by the
Media Center Project. It does not make Media Center behavior part of core and
does not authorize cross-subnet placement or a general multi-writer profile.

## Candidate Boundary

| Surface | Candidate | Review boundary |
| --- | --- | --- |
| AdaOS core | `rev2026` through `0.1.926` (`10cb9d9a`) | service definition v2, exact release overlap, membership, replacement readmission, fencing, routes, transfer and deployment control |
| Media Center Project | `0.6.45` at registry `7119aabac4b74e055755ddff4b6f19175a6efb16`; release `sha256:4bd827fd9f819107c1d20d85dc13d31b4ce5f0f75f18f57a5238a596ec8ddfe0` | representative coordinator plus node-local agents and retained external media |
| Deployment scope | two trusted physical nodes in one subnet | selected-node staged rollout; separate TV/controller presentations |

Exact commit, package and release digests must be copied from the normal
published build and stand receipts. A working-tree or overlay run is not
acceptance evidence.

Two independent local builds with separate package/release caches produced the
same release and package digests, and every archive hash matches its package
reference. The exact package set is:

| Component | Package digest |
| --- | --- |
| `scenario:media_center@0.6.8` | `sha256:56ab1426e7001db890c2773a76709972b4cfd2d49487ff55d33e973e6f533ca5` |
| `skill:media_center_skill@0.8.41` | `sha256:26ebfd0c79d46a126b163c77f5e83fb0321549b7b78cf41e337842e9f1d61507` |
| `skill:media_control_skill@0.2.1` | `sha256:ad1a647521751d75cc1498e42dc4c5deccf344f6912a4a615cafcf11a375ead3` |
| `skill:media_library_agent@0.6.20` | `sha256:8c26a77ae7e40c391eaef1f954ae5e05aad2a26a40e22296ebdf5e356a589be0` |
| `skill:mediaserver@0.9.15` | `sha256:e225581bd044e94ea09134efdb092e899975b77fcb42d6a072da51e9d6281024` |

The release was subsequently published and deployed through the normal durable
Project path on both physical nodes. The exact rolling sequence is recorded in
the 2026-08-22 addendum to the stand receipt; this closes reproducibility and
compatible rolling-release admission, not the remaining product and production
gates.

## Compatible Rolling Release Result - 2026-08-22

Definition v19 admitted the old and candidate exact release digests while the
batch-one Project rollout was in progress. Failed member activations stayed on
the ready old release and rolled back without route or external-data loss.
After the stable disk-admission cause was fixed in core rather than bypassed,
deployment revision `48` and operation
`deploymentop.01M0MCVXNM45RR6Q5Q8MCCFSHJ` converged the member to exact agent
`0.6.20`. Definition v20 removed the old digest only after both physical
generation-18 instances were ready on release
`sha256:4bd827fd9f819107c1d20d85dc13d31b4ce5f0f75f18f57a5238a596ec8ddfe0`.
Topology inspection was non-partial. The later revision-49
drain/remove/restore run replaced the member activation, retained external
media and returned both exact instances to ready under definition v21 and
generation `19`. This closes the compatible rolling-release gate and
`DS5-04`; it does not substitute for the remaining exact-candidate rejection,
browser or soak gates below.

## Bounded Diagnostics Follow-up - 2026-08-22

Project `0.6.46`, release
`sha256:7c2f9b8910d0318bbb06b43c3d052c2331ef563b5578b4360f1f79a34eca856b`,
was admitted through the hub's verified content-addressed package/release
repositories and deployed as revision `50`. Operation
`deploymentop.01M0MHYGHXNCXVRA772C4E2G5Z` succeeded on both nodes. Definitions
v22 and v23 preserved old/new overlap on each side of the primary-release
switch; exact-only v24/generation `22` now reports both stable instances ready
and `partial=false`.

The follow-up removes an FTS5 full-token-table count from coordinator status.
The real 68,429-row, 1.1 GiB stand catalog returned status in 0.803 seconds,
exact filename search in 0.165 seconds and bounded topology inspection in 0.292
seconds. Source identity and range playback were unchanged. The deployed RU
Root endpoint rejected this current release plan as `invalid_project_release`
and could not retrieve the already active `0.6.45` record. Current backend
source contains the stricter release validator, but deployment and a successful
Root publish/read proof remain required; local artifact admission does not
close that platform gate.

That platform gate was subsequently closed for the next exact candidate.
Backend `0.1.183` at commit `926c2de` accepted only the governed Project
composition-lock shape and published/retrieved `media_center@0.6.50` with exact
digest
`sha256:c56a0c2527fb8bf7d9a898beca2dddeb134267a2384d906e682890e4c394e6fa`.
Deployment revision `54` and operation
`deploymentop.01M0NHQ3X6F712AP18V4TCHVC1` then converged both physical nodes.
Definition v28 provided bounded old/new overlap; exact-only v29 and group
generation `27` removed it after both instances were ready. The authority
handoff advanced the representative partition to revision `33`, epoch `13`,
with matching `catalog:40663` witnesses and an authority-eligible route. This
closes Root publication/retrieval and repeats compatible exact-candidate
admission; it does not close the physical Android TV soak gate.

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

Exact-candidate incompatible-release admission was rejected before mutation;
the later valid rollout reached exact v24/generation `22`. Open security proof:
repeat stale epoch, expired lease, revoked instance and unauthorized retention
mutation rejection against the exact candidate on the stand, then export
sanitized receipts.

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

The exact drain/remove/restore run recorded `external_data=retained`; source
size `57387298`, mtime `1150860693` and inode `89788` were unchanged, member
storage remained `external_reference` with `media_bytes_copied=false`, and
range playback still returned `206`. Open privacy proof is narrowed to the
reviewed replica/derived-data cleanup decision and confirmation that exported
diagnostics contain no source paths.

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
| Compatible rolling release | normal ProjectRelease rollout on two physical nodes; old route retained; all live memberships converge; overlap then removed | accepted on exact `0.6.45`, operation `deploymentop.01M0MCVXNM45RR6Q5Q8MCCFSHJ` |
| Failure and recovery | planned handoff, unplanned authority loss, stale owner rejection and retained checkpoint on exact candidate | repeat on candidate |
| Security/privacy | rejection matrix plus sanitized export and retained external-data witness | external retention and incompatible release accepted; remaining rejection/export matrix open |
| Resource/soak | passing server and Android TV one-hour gates plus update-under-playback QoE/I/O | server passed; TV/update proof open |
| Product E2E | separate TV and authorized controller with Now Playing, D-pad/touch, reconnect/resume, i18n and screenshots | open |
| Operator recovery | runbook executed by operation ids with route/checkpoint evidence and reviewed rollback result | drain/remove/restore accepted; authority rollback/recovery review open |

Production remains rejected while any row is open. Passing contract tests or a
single successful deployment cannot change this decision by implication.

## Related Evidence

- [Distributed Media Center Stand Validation - 2026-08-21](distributed-media-center-stand-validation-2026-08-21.md)
- [Distributed Runtime Conformance - 2026-08-20](distributed-runtime-conformance-2026-08-20.md)
- [Media Center Security And Privacy Review - 2026-08-20](media-center-security-review-2026-08-20.md)
- [Distributed Service And Data Topology](distributed-service-and-data-topology.md)
- [Distributed Service And Data Topology Roadmap](distributed-service-and-data-topology-roadmap.md)
- [Distributed Media Center Roadmap](media-center-roadmap.md)
