# Distributed Media Center Baseline - 2026-08-19

Status: exact-revision implementation audit for `MC0-03`, `MC0-04`, and
`DS0-03`.

This record is the starting evidence for the
[Media Center Roadmap](media-center-roadmap.md), the
[Distributed Service and Data Topology Roadmap](distributed-service-and-data-topology-roadmap.md),
and artifact milestone `AP8`. It reports observed behavior and gaps; it does not
promote the current single-node slice to distributed or production acceptance.

## Audited Revisions

| Repository/surface | Revision | State |
| --- | --- | --- |
| AdaOS core baseline branch | `37677e341e64920d10aa7d8917f992ab801349fc` | clean isolated worktree created from the shared `rev2026` head |
| Media architecture baseline | `86b3eb5b` in the core history | target architecture and roadmaps committed |
| AdaOS client submodule | `3be78eb14bd2122b108c00f37ee8c8ad4211ee97` | clean `main`; isolated implementation worktree created |
| Canonical skills/scenarios registry | `773089f` (`origin/main`) | clean isolated worktree; the older local registry branch was not rewritten |
| Latest shared media implementation in registry history | `652a026` | Media Center `0.1.10`, scenario `0.1.7` |
| `.30` core | `0.1.868+1.4ca7733`, commit `4ca77337741940ea9f69e53c7419954960c8054e` | runtime ready on supervisor-selected slot B / port 8778 |
| `.30` Media Center | skill `0.1.10`, scenario `0.1.7` | matches audited registry versions |

Parallel work was active in the original core worktree during the audit. All
new implementation uses `D:\git\inimatic\adaos-media-center`,
`D:\git\inimatic\adaos-client-media-center`, and
`D:\git\inimatic\adaos-registry-media-center` so it does not reset or rewrite
the shared branches.

## Reproducible Local Checks

- Core Project/release baseline:
  `pytest tests/test_sdk_project_composition.py tests/test_artifact_release_contracts.py tests/test_artifact_release_resolver.py -q`
  passed `42/42`.
- Media skill/scenario baseline:
  `pytest skills/media_center_skill/tests scenarios/media_center/tests -q`
  passed `22/22` using the audited core SDK.
- Client media baseline passed `26/26` targeted Chrome Headless tests covering
  the media-player widget, browser playback-state service, and browser media
  runtime.
- The documentation baseline passed `mkdocs build --strict` for English and
  Russian builds before implementation began.

The local shared runtime did not answer its selected port within the bounded
audit probe while parallel work was active. Per operator instruction it was not
restarted or killed. Contract and unit checks remained independent from that
runtime. Runtime validation must be repeated after the shared hub becomes
available or the isolated implementation starts its own validated runtime.

## Current Core And SDK Seams

### Reusable

- `ProjectDefinition` authoring and validation through
  `adaos.sdk.developer.compositions`;
- immutable, digest-verified `ProjectRelease` component/dependency locks;
- package verification, local Workspace planning, transactional activation,
  health evidence, rollback, retention, and operation journals;
- webspace-oriented `adaos.project.placement.v1` and Builder placement helpers;
- exact node identity normalization and subnet member snapshots;
- member `tools.call` RPC routing over authenticated subnet links;
- skill runtime activation slots and lifecycle hooks including `drain`,
  persistence, rehydrate, healthcheck, dispose, and rollback;
- runtime/supervisor drain and A/B core-update state machines;
- durable operation, operational event, projection demand, bounded projection,
  and status-card infrastructure;
- Hub/browser lifecycle leases, room-winner leases, writer leases, and
  compare-and-switch patterns that provide reusable implementation guidance;
- media resource descriptors, in-place media references, path containment,
  direct/fallback browser routes, Range delivery, and bounded media I/O.

### Missing or private

- no `ProjectDeployment`, deployment revision, immutable multi-node plan,
  per-node component activation, or deployment operation ABI;
- no public Deployment SDK or generic remote Project component activation;
- no normalized node capability/capacity inventory for placement planning;
- no logical ServiceGroup/ServiceInstance registration contract;
- no reusable authority epoch/fencing contract for singleton services;
- no Dataset/Partition/Replica/checkpoint/watermark topology;
- no generic topology route or partial-participation contract;
- no domain adapter lifecycle for snapshot, delta, catch-up, verify, promote,
  drain, and replica removal;
- current leases are purpose-specific and cannot be presented as generic
  service/partition authority;
- current Yjs replication is suitable for its governed state plane but is not a
  large-index or arbitrary-data replication substrate;
- current activation is local to one node/Workspace and supports one active
  package per canonical skill id in that activation context.

These findings confirm the boundary in the target architecture: existing
mechanisms are implementation inputs, not an already implemented distributed
runtime.

## Current Media Center Skill And Scenario

### Implemented and retained

- configured local roots with labels and image inclusion policy;
- source-preserving `register_media_file(...)` registration;
- default audio/video import with images disabled for newly added roots;
- SQLite catalog with source/resource identity, technical/resource descriptor,
  missing state, favorite flag, play count, and scan runs;
- server-side `LIMIT/OFFSET` pagination and bounded playback queue;
- query matching against lowercased title, filename, and full source path;
- source, media-kind, favorite, and missing filters plus bounded sorting;
- direct core-media playback plans and browser route candidates;
- skill-owned English/Russian action errors;
- declarative scenario with search/filter/list, details/player modal, settings,
  roots, and bounded list/player controls;
- source-preserving root deletion under a cross-process mutation lease.

### Gaps against the roadmaps

- root import and scan execute synchronously inside a skill action;
- the root walker and registration pipeline have no durable job/checkpoint,
  background scheduling, filesystem watch, resource budget, or stage pressure;
- a scan admits at most 5000 descriptors and can repeatedly revisit the same
  prefix instead of continuing from a durable cursor;
- the catalog is one skill-local SQLite database with no agent/shard identity,
  deltas, global catalog generation, or partial-participation state;
- folder path happens to be searchable through `source_path`, but folder
  identity, breadcrumbs, cursor-backed children, aliases, and folder read model
  are absent;
- no MediaSource/Variant/Work/Collection/Membership/MetadataClaim model;
- no FTS ranking version, search cursor, federated query, merge/split, grouping,
  duplicate, or variant policy;
- favorite and play-count state are global rows, not actor/profile-scoped
  subscription-backed projections; Recent and Continue are not authoritative
  synchronized aggregates;
- no durable PlaybackSession, target/control lease, queue revision, handoff, or
  interruption reconciliation;
- the client has a shared browser media lease runtime, output volume and local
  video progress, but the media element remains widget-owned and there is no
  app-shell PlaybackCoordinator, mini-player, Media Session integration, or
  remote controller surface;
- no explicit TV/mobile-control presentation profiles, D-pad focus groups,
  generic rail/carousel, or Media Center Project manifest;
- no `media_library_agent` or `media_control_skill` package;
- enrichment is reported only as planned; no provider claims/jobs, rendition
  worker, or production operations projection exists.

## `.30` Observed Library

The stand was inspected over SSH without mutation.

- roots:
  - `/mnt/disk1/Music`, label `Music`, images disabled, last status `ok`;
  - `/mnt/disk1/Video`, label `Video`, images disabled, last status `ok`.
- catalog: `8249` total, `5406` available, `2843` missing;
- playable: `5176`, including `5000` audio and `176` video;
- image rows: `230` from legacy/current discovery sources, excluded by the
  default playable filter but not yet removed from the catalog;
- latest media-server scan: exactly `5000` discovered/updated, confirming the
  current prefix limit;
- source descriptors for sampled audiobook files point to
  `/mnt/disk1/Music/...` and expose `storage_mode=reference` with direct node
  candidates and root-relay fallback;
- sample numbered audiobook files carry useful parent folder names only in
  `source_path`/metadata, validating the need for explicit folder tokens and
  collection inference.

The stand runtime and supervisor were ready. Final roadmap acceptance still
requires deployment of new exact revisions and the failure/resource/browser
campaigns named by each milestone.

## Execution Issues

The stable roadmap remains the source of sequencing. Active execution is
represented by two bounded Issue Tracker entries:

- `DISTRIBUTED-RUNTIME-001`: `AP8`, `DS1`-`DS4`, and their local/two-node proof;
- `MEDIA-CENTER-DISTRIBUTED-001`: `MC1`-`MC7` non-deferred product work and
  exact local/stand acceptance.

Existing `MEDIA-CENTER-001`, `MEDIA-RELAY-RESILIENCE-001`,
`MEDIA-CODEC-COMPAT-001`, and `MEDIA-MAPPED-IO-001` remain valid narrower
acceptance issues and are not duplicated.

## Immediate Implementation Boundary

The first coherent code slice is:

1. fail-closed AP8 and DS ABI plus typed domain models;
2. durable desired/observed stores, planner, leases/epochs, topology and routes;
3. public Deployment and Distributed SDK planes with fake/conformance adapters;
4. local Project/Media Center fixtures before any remote activation or skill
   publication.

No media-specific table or Project installer is added to core during this
slice.
