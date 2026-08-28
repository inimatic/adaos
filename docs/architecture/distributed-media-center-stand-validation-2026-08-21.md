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
files. Client `3bb15f3` additionally coalesces repeated native `waiting` and
`stalled` events while playback is already buffering; the focused coordinator
and transition-overlay suite passed 13 tests. Client release `0.0.367` (build
`474b3b6`, containing `3bb15f3`) was built and deployed by GitHub Actions run
`32605061308`; both version bump and Firebase deployment completed successfully
with the workflow actions running on Node 24.

## Exact 0.6.52 Stand Rollout And Browser Reproduction - 2026-08-23

Deployment revision `58`, plan
`sha256:a7009daf9b0a922af2ee85b3463bd2b8eac48d12527e8d2b7bc47db8dcaf4133`
and operation `deploymentop.01M0NTQ6YVAX4FJ18VGJ7825KA` converged both
physical nodes with `state=succeeded` and `uncertain=false`. The installed
packages exactly matched Project `0.6.52`: scenario `0.6.13`
(`sha256:b5088ee429e5b408030dcda1d0b7aaa3c87ce38ef22f8e4f8191d58962055c8c`),
coordinator `0.8.45`
(`sha256:0d8454caf0d0a2903cd7ff3628bb3c700a700c94141504079a57c1e2697db89a`),
control `0.2.4`
(`sha256:d31dccec62c16900ee90d3ddd1214083a9a521c5e9029fec23eacc7cbabce122`)
and agent `0.6.26`
(`sha256:259a5c90c529b379da6f80f5edf82977cf2d4aec2e464ec657c6ae55f5d4b989`).

The coordinator activation took 130.779 seconds because its one-time
`2026-08-23.1` SQLite migration built folder/profile indexes over a 1.16 GB
catalog on the slow stand disk. The runtime stayed responsive, the operation
completed, and subsequent activation phases were normal. Lifecycle migrations
still need explicit progress/heartbeat evidence so a long but advancing index
build is distinguishable from a stuck activation.

Published client build `3f97355` reproduced the reported desktop path on a
fresh Chrome renderer: Movies, List, `Harry Potter`, open, scroll and close.
The modal opened in 1.151 seconds; after close exactly one media element was
owned by the shell mini-player, `readyState=4`, and event-loop delay p95 was
6 ms. A second interaction navigated Folders to `!Audiobooks`; breadcrumbs and
seven child folders changed without opening a playback modal. The root folder
view remained cursor-backed at 30 rows and reported page `1 / 178`.

The Harry Potter resource returned `206 Partial Content` for bytes `0-1023`
of `8,804,736,172` with `video/x-matroska`. Its catalog descriptor retained
`storage_mode=reference` and resolved to the original
`/mnt/disk1/Video/share/!Nina/Harry Potter/Harry.Potter.and.the.Prisoner.of.Azkaban.2004.Extended.WEB-DL.1080p.mkv`
path (folder names transliterated here for a stable ASCII evidence record).
The only files above 100 MB in Media Center runtime storage were the 1.16 GB
coordinator and 704 MB agent SQLite databases; no media payload was copied into
`.adaos`.

Two Chrome tabs that had entered the pre-fix renderer loop could not execute a
bounded CDP expression and had to be closed. A fresh tab loaded build
`3f97355`, remained responsive through the exact reproduction above, and is
the accepted desktop evidence. Existing hung documents cannot hot-recover to a
new JavaScript bundle. Representative Android TV responsiveness and the
one-hour playback/indexing/reconnect soak remain open acceptance gates.

After release `0.0.367` reached production, a second fresh Chrome renderer
repeated Movies, List, `Harry Potter`, open, scroll and close. The modal closed,
the single media element moved to `data-playback-view=mini` with `readyState=4`,
and a 20-sample timer probe recorded event-loop delay p95/max of 2.1 ms. This
also exercised the loading/buffering transition path and found no informer
writeback loop.

## Project 0.6.53 Single-Node Surface Addendum - 2026-08-23

Registry revision `afad575cd6102154965b15feacf57054c21f771b` defines immutable
Project `media_center@0.6.53` as
`sha256:19856947b5f6f58427d4b4729e17df0c2d198bed468881dbe6a8edf2798b2b0e`.
The changed packages are scenario `0.6.14`
(`sha256:7594c47cfe9aab18ee0a1d39056c8bd67e11132a6344d1a001b1fa14089b6a09`),
coordinator `0.8.46`
(`sha256:5571a3c3270f20a0103a25d3703cb56f6f2f1c8ab976ac5c27c5f51bbdcc8be5`)
and control skill `0.2.5`
(`sha256:7a04fc0318b5771273933cf71571cd341c1e2b0aba0563da9c405d744d895370`).
The unchanged agent `0.6.26` and Media Server `0.9.18` complete the release.

Client `0.0.368` at `81280060305c43cca87ee2affe509315913e20e5`
makes rail arrows perform an immediate bounded scroll even when native smooth
scrolling is unavailable, keeps all mini-player controls in one stable shell
surface, makes Close detach the shell view without issuing Stop, and orders the
remote as target, now-playing context and transport. The scenario sends typed
Home selections: folders update folder navigation, while only items,
collections and playlists open the player. Metadata processing is lazy behind
the Settings command. The focused client suite passed 44 tests, the Node 24
production build passed, and local desktop/mobile browser checks observed rail
movement without overflow or console errors. The scenario/skill suite passed
88 tests and Ruff.

The `.30` hub runs AdaOS `0.1.929` at `ecbe6c38`. A reviewed local-only batch
activated scenario `0.6.14`, coordinator `0.8.46` and control `0.2.5`; all
three committed their exact packages. The agent remained healthy at version
`0.6.26` with two roots, 40,661 available sources, background discovery
running, `storage.mode=external_reference` and `media_bytes_copied=false`.
Its package activation did not commit. The batch operation is intentionally
recorded as partial: process-local deployment convergence could not observe
the supervisor-owned agent service and rolled that activation phase back. No
remote removal from the misleading offline plan was executed. This is not
claimed as a complete distributed Project rollout.

The `desktop` webspace was then rebuilt from the exact scenario projection.
All required materialized branches are ready, and the stand source contains
the separate `select:folder` handlers for Home and Folders views. A bounded
catalog call returned three playable rows plus an opaque next cursor from the
68,429-row catalog. A real Harry Potter item produced a one-item bounded queue,
direct `.30` candidate and root-routed fallback. Agent inspection resolved the
descriptor to the original `/mnt/disk1/Video` path with
`storage_mode=reference`; a search under `/root/.adaos` found no MKV, MP4, MP3,
FLAC or AVI media payload. Coordinator status reports both enrichment and
agent-sync workers running.

Generic Project deployment still needs an authority-process RPC boundary for
inventory, remote planning and service convergence. A skill/runtime process
must not infer removals from an empty process-local `HubLinkManager`, and a
heartbeat directory must not be promoted into mutation authority. This gap is
tracked separately as `PROJECT-DEPLOYMENT-AUTHORITY-001`.

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

## Exact 0.6.98 Playback Ownership Addendum - 2026-08-28

Registry source revision `3acb9c85f48b4d9bbce46e7aa16137f1afb8fcd3`
publishes immutable Project `media_center@0.6.98` as
`sha256:ddf2206f198e8147dee1767f6084f7fadfcd8679c63542cd9fc8de0672414a88`.
Its control package is `media_control_skill@0.2.19` with digest
`sha256:74b9296b9c581eb4cc4aea11700e4c4b60e22105413e12e265802d1a68902484`.
Client revision `77ae410f9f5a75d3c73815189668e5ec28e1e479` was deployed directly to
Firebase Hosting while hosted CI quota was unavailable. Public
`version.json` reports client `0.0.375`, build
`local-1787929426496`, built at `2026-08-28T15:03:46.496Z`.

The playback authority now enforces one attached session per physical target.
Creating or reattaching a session atomically marks competing attached sessions
as `superseded`; startup schema reconciliation repairs legacy duplicates.
Endpoint observations carry the authoritative queue revision. A stale endpoint
cannot replace the server-selected item and receives a bounded `load` command
for the current item and queue instead. The client re-adopts an existing
session when its queue revision changes and immediately refreshes its inbox
after a reconciliation mismatch. This is the contract fix for the observed TV
oscillation and for Media Remote rendering two titles for one Android TV.

The isolated control-skill suite passed `39` tests and Ruff. The focused client
endpoint-session suite passed `7` tests. A Node 24 production build completed
successfully with hash `6373cfc635f82e46`; the Firebase build completed with
hash `cfde537d89d676d2`. Only the existing initial-bundle, component-style and
CommonJS warnings remain. Local activation of control version `0.2.19` repaired
`170` legacy rows to `superseded`, after which no target had more than one
attached session.

The stand runs core revision `efc54324a` as
`0.1.950+5764.efc54324`. Its updater consumes a bundled, hash-verified y-py
wheel instead of building Rust on the node. On this stand the y-py install step
fell from `498.617` to `3.463` seconds and complete slot preparation fell from
`653.778` to `125.878` seconds. Remaining preparation time is dominated by
repository and environment work, not Rust compilation.

Project revision `88` for release `0.6.97` succeeded on the `.30` coordinator
but remained explicitly `partial`: selected node
`adb099fe-32db-4252-addb-4a060e0834b4` failed activation and rollback with
`remote_member_link_unavailable`. The selected placement was retained. For
release `0.6.98`, service definition and group revision `42` admit the new
release while retaining the previous release in the compatibility overlap.
Deployment revision `90`, plan
`sha256:b64741c319ed99a73c7ccbc4e194259a6eaaba07da8742fecf6ba47bc514d0cf`
and operation `deploymentop.01M14EYDYNT2Z1PC0QRJYJQJSY` are the reviewed
stand rollout identifiers. The operation reached `succeeded` with
`uncertain=false`: scenario, coordinator, control and agent committed on node
`9161e4df-772a-4795-a6b3-1c4b95158802`, and the agent committed on selected
node `adb099fe-32db-4252-addb-4a060e0834b4`. Every terminal phase completed on
its first attempt. The `.30` runtime reports control version `0.2.19`.

Post-activation database reconciliation on `.30` marked `25` historical
sessions `superseded`; no target retained more than one attached session. A
bounded `now_playing` read returned zero rows while no browser endpoint was
fresh, rather than returning the three stale stopped/playing rows retained for
resume. Live Android TV switching remains a separate browser acceptance test.

Open acceptance work remains a live Android TV switch test proving that a
stale tab cannot restore the old queue, the one-hour TV soak, update and scan
under playback, unavailable-disk and unsupported-codec injection, second-node
link recovery, updater child-process cancellation cleanup, and reducing the
remaining client bundle/style budget warnings.

## Endpoint Idle Loop And Browser Contract Addendum - 2026-08-28

Client revision `5841168d12c16b74ed769e3e2de3c00901d5d831` removes a
feedback loop between endpoint heartbeat registration and playback-surface
identity publication. `endpoint_inbox` registered the current target, while
`registerTarget` unconditionally re-emitted the unchanged identity; its
subscriber immediately requested another inbox refresh. A production-bundle
browser run measured `125` inbox calls and `10.686%` steady main-thread CPU.
After identity publication became change-sensitive, the same bounded run made
`9` inbox calls in 75.866 seconds, matching immediate registration plus the
declared 15-second heartbeat. Steady main-thread CPU fell to `4.068%`, heap
growth was `0.615 MB`, event-loop delay p95 was `0.2 ms`, frame delay p95 was
`12.6 ms`, and the steady window recorded no long tasks, actionable HTTP
errors, console errors, overlap, or unresolved state tokens. All 54 Home cards
rendered. The surface-identity and endpoint-session suites passed `4` and `7`
tests respectively.

Firebase Hosting version `bdd5115b62b76937` publishes client `0.0.375`, build
`local-1787932052228`, and `main.f49e270f4f03c360.js`. The public
`version.json` and index were fetched without cache after release and matched
those identifiers.

The post-release `.30` control database contains one `playing`, two `stopped`,
and 25 `superseded` historical sessions. No physical target has more than one
non-superseded attached session. The retained rows are durable resume/history
state; freshness filtering prevents stale endpoints from entering Now Playing.
This is server-side evidence for single ownership, but it does not replace the
remaining live Android TV old-queue rejection test.

Two unrelated UI data-contract defects found by the browser run were released
separately. `infrastate_skill@0.75.90` now publishes skill-owned `ru/en`
resources through the immutable asset-blob contract instead of browser-relative
`i18n/*.json` paths. `subscription_status_skill@0.1.12` reads its maintained
`data/subscription_status` Yjs projection instead of invoking `local_write`
tools as declarative read sources. Their focused suites passed 152 and 11
tests, and local browser validation no longer produced the former 404 and 409.

Activation of those two standalone skills on `.30` remains deferred. The
stand reported sustained full I/O pressure above 64%, and registry install
correctly stopped when sparse-workspace auto-stash could not reconcile the
dirty exact-Project materialization. The failed attempt did not replace active
Media Center packages. Core lifecycle debt is to make forced sparse-workspace
migration transactional, bounded, observable, and independent of materialized
Project test/state exclusions; repeated auto-stashes are not an acceptable
steady-state deployment mechanism.
