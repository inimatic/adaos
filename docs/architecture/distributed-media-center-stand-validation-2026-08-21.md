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

## Local And CI Evidence

- Media library agent: `37` tests passed; Ruff passed.
- Core distributed/project focused suite: `59` tests passed; Ruff passed.
- Client zone/connectivity suite: `37` tests passed and production build passed.
- Core CI for membership serialization: run `32458794016`, succeeded.
- Core CI containing membership serialization and deployment read retry: run
  `32458989756`, succeeded.
- Firebase/client CI for the zone fix: run `32450364469`, succeeded.

## Open Gates

1. A normal ProjectRelease rollout activates the new package before the old
   topology source is drained. A topology plan that referenced the replaced old
   activation correctly failed with
   `topology_skill_activation_identity_mismatch`. Zero-downtime rolling
   activation/topology ordering remains open (`DS5-04`).
2. TV and controller browsers still need a recorded cross-surface E2E with
   screenshots, D-pad/touch control, reconnect and resume (`MC7-08`).
3. Long playback-under-indexing, browser render, CPU/RSS and Yjs pressure soak
   remains open (`MC7-07`, `DS5-05`).
4. Automatic authority election, cross-subnet placement and native mobile
   background playback remain deferred by their roadmaps.

## Update Duration Note

An earlier core slot preparation on the same slow stand took about 239 seconds,
including roughly 152 seconds of environment installation. Increasing timeout
budgets prevents false failure but does not reduce this cost. The preferred
optimization is an immutable dependency layer or wheel cache keyed by
`uv.lock`, platform, architecture and Python ABI, with a small application
overlay and prewarm/import validation. This remains an updater optimization,
not a Media Center workaround.
