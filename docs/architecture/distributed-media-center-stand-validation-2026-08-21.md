# Distributed Media Center Stand Validation - 2026-08-21

Status: bounded two-node trusted-subnet technical pilot accepted. Production
acceptance and unattended failover remain rejected pending the open gates in
this receipt.

This receipt covers the designated `.30` hub and one trusted physical member in
subnet `sn_92ffc943`. It records exact Project deployment, replicated catalog,
fenced authority, search, route, playback, recovery and failure evidence. It
does not treat a successful stand run as production acceptance.

## Accepted Revisions

| Surface | Revision | Accepted boundary |
| --- | --- | --- |
| AdaOS core | `7db59cca` | authority epochs remain monotonic after lease expiry |
| AdaOS core | `59bd1533` | membership placement admission is serialized with lease/instance commit |
| AdaOS core | `a81a3e7b` | transient deployment-journal sharing violations use bounded read retry |
| Media Center registry | `eb3d2ab` | follower snapshots cannot replace the authority checkpoint; promoted authority can recover from persisted replica state |
| AdaOS client | `7647943` | a confirmed hub/subnet zone overrides and removes a stale URL `zone` hint |
| Media Center Project | `0.6.37`, release `sha256:e13832e3cb77b20e110f249c12d5f0d9fc46974696757bcf9e6d62b6e6bd402a` | exact immutable scenario and four-skill compatibility closure |
| AdaOS core | `0.1.926`, `10cb9d9a` | remote activation diagnostics, practical ordinary-wheel disk admission and drained-instance readmission after replacement |
| Media Center Project | `0.6.45`, release `sha256:4bd827fd9f819107c1d20d85dc13d31b4ce5f0f75f18f57a5238a596ec8ddfe0` | exact two-node compatible rolling release and exact-only topology convergence |

The Project release was built twice from registry revision
`eb3d2ab5df1d2ca8f2fbe6bc6ef2c6fe695214ec`. Both builds produced the same
release file hash and the same five package digests:

| Component | Version | Package digest |
| --- | --- | --- |
| `scenario:media_center` | `0.6.3` | `sha256:9dece9ea5b50a40d1d8e23cc51cb4b3dbb109b4793f6091efaa520fe7c0ee4e7` |
| `skill:media_center_skill` | `0.8.36` | `sha256:09cedca5f2939e1f7cb02ebeaaa01bcdcdde334aa0b6799b2001d9fa2f456236` |
| `skill:media_control_skill` | `0.2.0` | `sha256:092c4b387468d21e26cb9eb0a2619dbc8b27c1815e4cac606d3181fb8b92aa52` |
| `skill:media_library_agent` | `0.6.17` | `sha256:04c7ee412083fce99cb03cf664b3afb5ac0ae67f854f2dcc1355ee851dac1f0e` |
| `skill:mediaserver` | `0.9.15` | `sha256:f9ad5ec2614847eea19b1888b2741b1567f69e17c1ac6fa8609530da74f4ea5c` |

## Physical Topology

| Role | Node id | Exact service instance |
| --- | --- | --- |
| Hub, coordinator and catalog authority | `9161e4df-772a-4795-a6b3-1c4b95158802` | `service-2482673a5b6ddc82e2c16b175ffe` |
| Member catalog follower | `adb099fe-32db-4252-addb-4a060e0834b4` | `service-5610f62ca8980cc9787aed007a52` |

Both instances are ready on Project deployment generation `39`, topology
generation `15`, protocol `1`, and the exact `0.6.37` release digest. The group
has desired count `2`, selected-node placement and
`max_instances_per_node=1`.

The final partition and replica witnesses are:

| Record | State |
| --- | --- |
| Partition `media-catalog-authority:home` | ready, revision `29`, epoch `11`, checkpoint `catalog:40663` |
| Hub replica `replica_893101edafb76008e313b0ed338a` | authority, epoch `11`, `40,663` items, `2,165,208,996,736` source bytes |
| Member replica `replica_aa7b381876db1ae557ee246382a2` | follower, epoch `11`, same checkpoint/item/byte witness |
| Authority lease `authority-48e793431afa793aa574f398` | active and automatically renewed by the membership supervisor |

Every observed transfer and replica receipt reports
`external_media_copied=false`. The approximately 9.7 MB compressed catalog
snapshot contains metadata and source references; original media remains at its
initial storage path and is not copied into `.adaos`.

## Deployment Evidence

Deployment revision `39` used plan
`sha256:cc55c14604e869442e6615234d49f9fe065db609246c2b9769b0bc59837eec3b`
and operation `deploymentop.01M0HJHD8ZY1XM20RVZ860E44E`. It completed in
about 3.5 minutes. Hub scenario, coordinator, control skill and agent, followed
by the member agent, each completed `fetch`, `verify`, `stage`, `activate`,
`health` and `commit`.

Project rollout is durable and inspected by operation id. A browser/tool RPC is
not held open for the rollout lifetime. The longest hub activation in this run
was coordinator handler/service readiness, not package transfer.

## Authority And Failure Proof

The following two-node operations used exact checkpoint and content witnesses:

| Operation | Result |
| --- | --- |
| Replica create `topology-e72035c79ebbda254f11` | resumed after runtime restart; all phases succeeded; media bytes were not copied |
| Planned hub to member `topology-d2bedddf6d96f8586583` | checkpoint verified and authority advanced to epoch `4` |
| Planned member to hub `topology-1cb08feafac7b79a4106` | all phases succeeded and authority advanced to epoch `5` |
| Unplanned hub loss recovery `topology-1c93425fbc2c9bea7a5d` | member route remained readable and authority advanced to epoch `6` |
| Member unavailable return `topology-9151d2f1feb5e2da052e` | failed closed with `remote_topology_member_link_unavailable` |
| Explicit hub recovery | epoch `7`; stale old epoch rejected |
| Expired-lease generation-14 recovery | epoch `8`; proved historical epoch preservation after lease expiry |
| Generation-15 verified member and hub handoffs | epochs `9`, `10`, then auto-owned hub epoch `11` |

After Project `0.6.37` activation, the member had no local roots but retained a
verified transferred snapshot. Follower observation returned `catalog:40663`
without changing Partition revision `23`. Promotion recovered the same witness,
and subsequent hub/member observations left the canonical checkpoint intact.

Route explanation at the final state reports the partition ready, the hub
authority eligible, the member follower eligible, and historical replicas
ineligible because their membership or authority lease is inactive.

## Product Behavior

- An audio page returned `30` bounded rows with `has_more=true`; it did not
  materialize the full catalog in one response.
- Search for the real audio title `! PART006` returned the exact item through
  coordinator FTS and additional folder-aware discovery candidates.
- Folder terms from the audiobook hierarchy contributed search matches even
  when individual files were numbered.
- Playback planning returned a direct source-node candidate and an explicit
  root-routed HTTP fallback for the original registered MediaResource.
- Project and topology operation status were observable through bounded public
  SDK-backed skill tools.
- The client now treats URL `zone` as a bootstrap hint. Once a hub/subnet zone
  is confirmed, it wins over a stale URL value and the URL is normalized.

## Exact 0.6.45 Rolling Upgrade Addendum - 2026-08-22

The exact candidate was exercised through the normal durable Project path,
not by copying skill sources into either node:

1. Service definition v19 and group generation `17` admitted exact release
   `sha256:4bd827fd9f819107c1d20d85dc13d31b4ce5f0f75f18f57a5238a596ec8ddfe0`
   plus the previous live release
   `sha256:c700d17da1210b961997f27652de0923993fc52061131c3148af79132d9e3cb4`.
2. Failed member attempts remained durable and rolled back to ready agent
   `0.6.18`. Revision `47` exposed the exact terminal reason
   `skill_runtime_dependency_disk_budget_failed`; no compatibility check or
   disk guard was disabled.
3. Core commits `3e5474bd`, `2beeb78f`, `6c6525a1` and `fe2c6d69` made stale
   candidates reprepare, exposed authorized remote runtime state, classified
   preparation failures and changed the ordinary-wheel reserve from an
   unrealistic 4 GiB for two small dependencies to 1 GiB. The separate 12 GiB
   heavy/native dependency threshold remains intact and all thresholds remain
   operator-configurable.
4. Both nodes converged to core `0.1.925` (`a3559e14`). Deployment revision
   `48`, plan
   `sha256:f244d00fa1c39a726e34c515b89d615968ddbe0765153ec3e67e8e94a15329ad`
   and operation `deploymentop.01M0MCVXNM45RR6Q5Q8MCCFSHJ` then succeeded.
   The member completed fetch, verify, stage, activate, health and commit for
   package
   `sha256:8c26a77ae7e40c391eaef1f954ae5e05aad2a26a40e22296ebdf5e356a589be0`,
   and registered activation
   `activation.71c96bf7af8bf4efb50f646d113d3a7e`.
5. Definition v20 removed every compatibility digest only after hub instance
   `service-16d6c176293b22270e7c364bfc74` and member instance
   `service-af4aa981321519b6bfaf2f50f74a` were ready at topology generation
   `18` on the exact release. Bounded inspection reported `partial=false`.

Post-deployment product probes observed two configured hub roots, two fresh
participating agents and `40,663` hub source records. Search matched both the
real filename `PART006` and Cyrillic folder terms from the audiobook hierarchy.
Catalog pages remained capped at `30`, the playback queue at `10`, and two
non-overlapping HTTP range reads returned `206 audio/mpeg` directly from the
original registered source. An isolated profile favorite advanced through
revisions `1` and `2` and was visible through the profile-filtered catalog
between mutations. No original media bytes were copied: the member status
reported `storage.mode=external_reference` and `media_bytes_copied=false`.

The bounded artwork path was also exercised. A source with adjacent
`Folder.jpg` produced a 7,654-byte derived JPEG with provider
`media_library_agent.folder_artwork.v1`; agent delta `40664` made the sanitized
URL, dimensions, source revision and fingerprint visible in the coordinator,
and the image route returned HTTP `200 image/jpeg`. A file without artwork
reported `artwork_not_found`, while video-frame fallback reported
`artwork_video_backend_unavailable` because ffmpeg is absent on this stand.
Those are truthful provider limitations, not successful artwork coverage.

## Exact Operator Lifecycle Addendum - 2026-08-22

The exact candidate also completed a destructive-control-plane lifecycle while
preserving external media:

1. Member instance `service-af4aa981321519b6bfaf2f50f74a` was moved to
   `draining` by compare-and-switch at topology revision `30`.
2. Deployment operation `deploymentop.01M0ME88HHV3VA46VA97F2AGXA` cordoned
   and drained member activation
   `activation.71c96bf7af8bf4efb50f646d113d3a7e`; operation
   `deploymentop.01M0ME8TR124KFGA7DJAWBPFWA` then deactivated and removed it.
   The removal receipt explicitly reported `external_data=retained`.
3. Deployment revision `49`, plan
   `sha256:3982f0aeb5d08ade69b0dfea10b57b17005e4e907f2914a446fe538b295b8cc9`
   and operation `deploymentop.01M0MEAH08T72VGSX8174PK1Q9` restored exact
   agent `0.6.20` as activation
   `activation.c93de6188890381ddaf47a7251993c9e` through all installation and
   service-health phases.
4. Definition v21 and group generation `19` retained exact-only release
   admission. Core `0.1.926` (`10cb9d9a`) fixed the generic lifecycle rule so
   a replacement activation can readmit the same stable instance id after the
   previous activation was drained. Both nodes updated through the normal
   hub/member update path, and the member automatically returned to `ready`
   with its new activation and lease. Inspection with a bounded limit of 100
   reported both exact instances ready and `partial=false`.

Before removal and after restoration, the original source witness remained
size `57387298`, mtime `1150860693` and inode `89788`. A non-leading range read
still returned `206 audio/mpeg` with the expected total size. The restored
member reported `storage.mode=external_reference` and
`media_bytes_copied=false`; catalog participation reported both agents fresh,
and filename search for `PART006` still returned the original item. No source
file was copied into AdaOS state or removed by component lifecycle operations.

## Large-Library Diagnostics Addendum - 2026-08-22

Project `0.6.46` closes a production defect found by probing the real stand
catalog. The coordinator database was 1.1 GiB and a compact `status` call
executed `SELECT COUNT(*) FROM catalog_search`. Because `catalog_search` is an
FTS5 virtual table, this traversed the full token payload and held the request
for minutes. Coordinator `0.8.42` derives the search row count from the ordinary
`catalog_items` table under the enforced one-to-one rowid invariant; regression
coverage records every executed statement and rejects an FTS count.

The immutable Project release is
`sha256:7c2f9b8910d0318bbb06b43c3d052c2331ef563b5578b4360f1f79a34eca856b`.
Deployment revision `50`, plan
`sha256:c8f4ba3caab178532c278351985f22a5d825441c1c4df8c6103a6c56c46de470`
and operation `deploymentop.01M0MHYGHXNCXVRA772C4E2G5Z` updated the scenario,
coordinator, control skill and both node-local agents through all six normal
activation phases. The rollout used an old-primary/new-compatible definition
v22, a new-primary/old-compatible definition v23 and exact-only definition v24.
Group generation `22` then reported both stable instances ready at runtime
generation `50`, exact release `0.6.46`, with `partial=false`.

On `68,429` catalog rows, post-deployment `status` completed in 0.803 seconds,
an exact filename catalog search in 0.165 seconds and bounded topology
inspection in 0.292 seconds. Federated deep search matched `PART006` in the
coordinator and both agent stages, including its Cyrillic audiobook folder
metadata. The hub agent's direct technical FTS completed in 0.071 seconds.
The same non-leading byte range returned `206 audio/mpeg`; the original source
still had size `57387298`, mtime nanoseconds `1150860693572081600` and inode
`89788`. Agent diagnostics again reported `storage.mode=external_reference` and
`media_bytes_copied=false`.

The deployed RU Root artifact endpoint rejected the current release-plan
schema as `invalid_project_release`; it also did not contain the already active
`0.6.45` release. For this stand run, archives were transferred to the hub's
artifact staging area and admitted through `ContentAddressedPackageStore` and
`ReleaseRepository`: every package and PackageRef was reverified before the
ordinary Project deployment consumed it. No skill source was copied into an
active runtime. Aligning the deployed Root artifact validator with the current
core/backend release schema remains a platform deployment gate; the local
content-addressed admission is evidence for the candidate, not evidence that
the Root publication path is healthy.

## Exact 0.6.50 Publication And Handoff Addendum - 2026-08-22

Project `media_center@0.6.50` was published to the RU Root through the normal
artifact API after backend commit `926c2de` added strict governed
`composition_lock` validation. Root returned and re-read the exact release
digest
`sha256:c56a0c2527fb8bf7d9a898beca2dddeb134267a2384d906e682890e4c394e6fa`;
the retrieved record was byte-for-byte equal to the local release plan. The
exact owned packages were scenario `0.6.12`
(`sha256:22cbf335a04e2b63b3f608075eb2ead45e7f565ab9c2cf771da1af42d1ec1f71`),
coordinator `0.8.44`
(`sha256:2b113b7d62be2e745340d5235dd84572c42eb1df4ea950106c892fabb233f590`),
control `0.2.4`
(`sha256:ded8da0b2d5fa7976013e78e8487a9e95c6e9c2dcdce7a95b171840b01e4e50d`)
and agent `0.6.25`
(`sha256:0104c66eee7e7da936ea1dce5c15f2c88ef3a607105c02603b5a85b86e2d8164`).

The hub imported those immutable records into its verified local cache and
created deployment revision `54`, plan
`sha256:585621627afae2966827dd2bb71db61127b246fa4553d5417d4cc3871c396d87`.
Operation `deploymentop.01M0NHQ3X6F712AP18V4TCHVC1` completed all six normal
phases on hub and member in 172 seconds with `uncertain=false`. Both agent
health receipts proved the exact package and a restarted matching service.
The in-process coordinator activation no longer ran global service discovery;
core commit `b679d254` reports it as `not_a_service_skill`.

Definition v28 temporarily admitted the previous exact release while both
agents converged to release `0.6.50`. Definition v29 then removed the overlap;
both stable exact instances were ready at group generation `27`. A fenced
handoff to member instance `service-af4aa981321519b6bfaf2f50f74a` completed in
0.522 seconds by reusing the unchanged persisted snapshot. Replica observations
then committed partition revision `33`, epoch `13`, member authority and hub
follower, both at checkpoint `catalog:40663` with `40,663` items. Route explain
reported `authority_eligible=true`. A unique stale request with revision `31`
and epoch `12` was rejected as `partition_revision_conflict`.

Post-rollout checks found `/mnt/disk1/Music`, `/mnt/disk1/Video` and one disabled
fixture tombstone. Filename search for `Wayne Dyer` and folder search for
`101 Ways to Transform Your Life` each returned the two original audiobook
files in under 0.5 seconds. Catalog responses remained capped at 30 and
returned an opaque keyset `next_cursor`. A 1,024-byte read returned `206
Partial Content`, `audio/mpeg` and the exact total size. Resolving the resource
internally produced the original
`/mnt/disk1/Music/!Аудиокниги/101 Ways to Transform Your Life/Wayne Dyer - (1 Of 2).mp3`
path with `storage_mode=reference`; no managed Media Server copy was involved.

The first two configure attempts also exposed a control-plane atomicity gap:
`configure_deployment` persisted desired revisions `52` and `53` before
planning failed because the verified local release cache was not populated.
CAS correctly rejected stale retries, and revision `54` succeeded after exact
cache admission, but define-plus-plan should become one atomic operator
transition or compensate the desired write on planning failure.

## Defects Found And Closed

1. Expired authority leases previously reset observed epoch to zero during
   recovery. Core now derives the next epoch from all historical authority
   leases and verifies the expected partition epoch.
2. An empty follower previously replaced the canonical Partition checkpoint.
   The agent now separates follower Replica evidence from authority Partition
   evidence and reuses persisted snapshots after restart.
3. Concurrent automatic and explicit membership registration could both pass
   placement admission. The authority runtime now serializes admission through
   membership lease and instance commit; a deterministic concurrent test proves
   one node cannot consume the same slot twice.
4. Windows readers could receive a transient `PermissionError` while a
   deployment operation journal was atomically replaced. The store retries only
   bounded sharing violations; malformed JSON still fails immediately.
5. A stable instance that had been explicitly drained stayed draining after a
   replacement activation and topology generation were ready. Core now
   preserves drain only for the same activation, release and generations, and
   readmits a replacement through the ordinary compare-and-switch registration
   path. The focused suite proves both preservation and replacement behavior.

## Local And CI Evidence

- Media library agent: `37` tests passed; Ruff passed.
- Core distributed/project focused suite: `59` tests passed; Ruff passed.
- Client zone/connectivity suite: `37` tests passed and production build passed.
- Core CI for membership serialization: run `32458794016`, succeeded.
- Core CI containing membership serialization and deployment read retry: run
  `32458989756`, succeeded.
- Firebase/client CI for the zone fix: run `32450364469`, succeeded.
- Core ordinary-dependency disk admission: `22` focused tests and Ruff passed;
  AdaOS CI run `32564373453` succeeded and published `0.1.925`.
- Drained-instance replacement: `33` focused core tests and Ruff passed; AdaOS
  CI run `32566490113` succeeded and published `0.1.926` to both stand nodes.
- Project `0.6.46`: `157` isolated scenario and skill tests passed; coordinator
  `82`-test suite and Ruff passed with explicit no-FTS-count SQL tracing.

## Open Gates

1. TV and controller browsers still need a recorded cross-surface E2E with
   screenshots, D-pad/touch control, reconnect and resume (`MC7-08`).
2. Long playback-under-indexing, browser render, CPU/RSS and Yjs pressure soak
   remains open (`MC7-07`, `DS5-05`).
3. Complete the remaining exact-candidate stale-epoch, expired-lease, revoked
   instance and unauthorized-retention rejection matrix, plus sanitized export
   review (`DS5-05`). Incompatible release rejection and the operator
   drain/remove/restore retention slice are now recorded.
4. Deploy the current artifact release validator to Root and prove immutable
   Project publication plus retrieval before treating Root as a release source.
5. Automatic authority election, cross-subnet placement and native mobile
   background playback remain deferred by their roadmaps.

## Local 0.6.52 Candidate - 2026-08-23

Registry revision `84914f30b37d3b9c59e34603e7b98b53a5325481` builds exact
Project `media_center@0.6.52` as
`sha256:895afd1dc002a57bfcdac6ffedd85af733f98847e1a262109537dbbf344f9165`.
Its changed components are scenario `0.6.13` and coordinator `0.8.45`.

The complete local skill/scenario suite passed 102 tests and Ruff. The enforced
20,000-item benchmark passed with FTS p95 36.442 ms, catalog-page p95 18.630
ms, Home p95 227.967 ms, root-folder p95 14.280 ms and 30-file leaf-folder p95
28.246 ms. The folder projection stores metadata and counts only; original
media remains under its registered external path.

Client `0b6649a` makes mini-player host attachment idempotent and prevents the
hard snapshot loop observed after open, scroll and close. Its focused playback
and list suites passed 51 tests and the Node 24 production build succeeded.
Client `cab3ffb` supplies typed declarative list selection for folders versus
files. Stand deployment, real playback close/open-second, idle CPU, folder
navigation and Android TV responsiveness remain to be recorded below this
local checkpoint.

## Update Duration Note

An earlier core slot preparation on the same slow stand took about 239 seconds,
including roughly 152 seconds of environment installation. Increasing timeout
budgets prevents false failure but does not reduce this cost. The preferred
optimization is an immutable dependency layer or wheel cache keyed by
`uv.lock`, platform, architecture and Python ABI, with a small application
overlay and prewarm/import validation. This remains an updater optimization,
not a Media Center workaround.

The `0.1.925` update confirmed the same cost profile: fresh slot preparation
took about five minutes before prewarm and countdown, while the active runtime
and member route remained available. This validates the larger timeout but
strengthens the case for cached immutable dependency layers.
