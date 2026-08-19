# Distributed Media Center Roadmap

Status: implementation roadmap for the
[Distributed Media Center Target Architecture](media-center-target-architecture.md).

Last reviewed: 2026-08-19.

## Outcome

AdaOS delivers a production-oriented household media system in which one
ProjectRelease can place a logical coordinator, distributed library agents,
TV/library presentations, and remote-control surfaces across trusted nodes in
one subnet. Original media remains in place, large collections are bounded,
personal state synchronizes by profile, and playback remains controllable and
observable through partial failure.

This roadmap owns Media Center product sequencing. Generic Project deployment
contracts and activation behavior are owned by milestone `AP8` in the
[Artifact Source, Package, and Activation Roadmap](artifact-source-package-activation-roadmap.md).
Generic service groups, partitions, replicas, fencing, freshness, and routing
are owned by the
[Distributed Service And Data Topology Roadmap](distributed-service-and-data-topology-roadmap.md).
Media tasks reference those ids instead of duplicating core work.

## Priority Vocabulary

- `[must]`: required for the distributed household MVP acceptance proof.
- `[should]`: required before broad, repeated, or unattended household usage,
  but not required for the first bounded end-to-end proof.
- `[could]`: useful enhancement that must not delay the proof gate.
- `[deferred]`: intentionally excluded until the named later condition.

Priority is independent of implementation state and maturity.

## Maturity Vocabulary

Tasks and milestones progress through:

```text
hypothesis -> specified -> implemented -> integrated
  -> validated-local -> validated-stand -> production-accepted
```

A checked task has met the acceptance statement in its current scope and links
to evidence where implementation is involved. A specification task can close
at `specified`; code, deployment, and behavior tasks cannot. Local tests do not
imply stand or production acceptance.

## Scope Guardrails

1. Do not add Media Center entities or ranking policy to core.
2. Do not let a media skill invoke package-manager internals or install a remote
   component outside the public deployment SDK.
3. Do not overload `ProjectPlacement` with node activation state.
4. Do not copy imported source media into `.adaos`.
5. Do not block a browser skill action until a scan, enrichment, or rendition
   finishes.
6. Do not materialize an unbounded catalog, folder, search result, queue, or
   node inventory into one Yjs object or UI control.
7. Do not identify a resource across nodes by physical path.
8. Do not make source deletion an implicit consequence of agent or Project
   removal.
9. Do not fork business behavior between TV, desktop, and controller profiles.
10. Do not claim browser background guarantees that require a native runtime.
11. Do not mark partial results fresh when an expected shard is unavailable.
12. Do not let scan, hashing, enrichment, rendering, or transcoding starve
    playback, command routing, sync, or the OS.

## Current Baseline

The current bounded implementation already provides useful seams:

- a shared core `MediaResource` descriptor and ranged content path;
- in-place file registration distinct from managed publication;
- a single-node Media Center catalog and scenario;
- folder-root import and catalog projections;
- compatibility discovery from Media Server and `media_indexer`;
- browser media runtime leases and the architectural direction for persistent
  shell-owned playback;
- skill-owned localized human action feedback over machine-readable reasons;
- ProjectDefinition, ProjectRelease, ProjectInstallation, and webspace-oriented
  ProjectPlacement contracts.

Before implementation resumes, `MC0-03` must re-audit exact current revisions,
tests, schemas, and stand behavior because the core, client, and skill
repositories may have changed independently. This baseline is not a production
or distributed-readiness claim.

## Delivery Snapshot

| Milestone | State | Target maturity | Main dependency |
| --- | --- | --- | --- |
| MC0 Architecture and baseline | in progress | specified | current repositories |
| MC1 Project and agent deployment | open | validated-stand | core `AP8`, `DS1` |
| MC2 Distributed roots and indexing | open | validated-stand | MC1, `DS2`, `DS3` |
| MC3 Catalog, search, folders, and collections | open | validated-stand | MC2 |
| MC4 Playback sessions and control surfaces | open | validated-stand | MC1, MC3 |
| MC5 Adaptive UI and settings | open | validated-stand | MC3, MC4 |
| MC6 Personalization, access, and voice | open | validated-stand | identity roadmap, MC4, MC5 |
| MC7 Enrichment, variants, and production operations | open | production-accepted | MC2-MC6 |

## Dependency Order

```text
MC0
  -> AP8 core distributed Project deployment
  -> DS1 service groups, instances, leases and fencing
  -> MC1 Project composition and agent placement
  -> DS2/DS3 partition topology and adapter operations
  -> MC2 roots, shards, jobs, and catalog deltas
  -> MC3 global search, folders, grouping, and queue sources
  -> MC4 persistent playback and remote control
  -> MC5 TV/desktop/mobile-control presentation profiles
  -> MC6 synchronized personal state, access, and voice
  -> MC7 enrichment, variants, resilience, and acceptance
```

MC3 UI read-model work may proceed in parallel with late MC2 agent work using
contract fixtures. MC4 shell playback work may proceed in parallel with MC3,
but stand acceptance requires real distributed playback plans. MC6 must reuse
the platform identity and access contracts rather than inventing media-local
accounts.

## Milestone MC0: Architecture And Exact Baseline

**Outcome:** the target decisions, ownership boundaries, task sequence, and
current implementation delta are explicit before another broad refactor.

**Exit proof:** architecture and roadmap are linked from the authority map; an
exact-revision audit maps existing behavior to roadmap ids and records all
known compatibility obligations.

- [x] `[must]` `MC0-01` Specify the distributed topology, Project/deployment
  boundary, domain entities, search topology, playback ownership,
  personalization, presentation profiles, security, and acceptance shape in
  the target architecture.
- [x] `[must]` `MC0-02` Publish this prioritized roadmap with milestone
  dependencies, exit proofs, and evidence rules.
- [ ] `[must]` `MC0-03` Audit the current core, client, scenario, Media Center,
  Media Server, and media-indexer compatibility revisions; record implemented,
  partial, obsolete, and missing behavior against every affected milestone.
- [ ] `[must]` `MC0-04` Convert active defects and bounded implementation slices
  into Issue Tracker entries linked to these stable task ids; do not duplicate
  the roadmap checklist in issues.
- [ ] `[should]` `MC0-05` Capture representative library fixtures: 20,000-item
  synthetic catalog, nested audiobook folders, series/seasons, album/disc
  metadata, duplicates, unavailable nodes, non-ASCII names, unsupported codecs,
  and slow/blocked filesystems.
- [ ] `[should]` `MC0-06` Record supported minimum node, browser, TV input, and
  network profiles plus explicit CPU, memory, I/O, and latency budgets.

## Milestone MC1: Project Composition And Agent Deployment

**Outcome:** Media Center is installed and reconciled as one Project whose
roles can be placed on selected trusted nodes through generic core contracts.

**Admission gate:** `AP8-01` through `AP8-11` and `DS1-01` through `DS1-07`
are at least integrated locally.

**Exit proof:** one reviewed ProjectDeployment installs a coordinator and two
agents, exposes TV and controller placements, reports desired/observed state,
survives one partial failure, and removes one drained agent without touching
source files.

- [ ] `[must]` `MC1-01` Define one Media Center Project with scenario,
  coordinator, project-only library agent, control skill, compatibility locks,
  lifecycle policy, and TV/library/remote entry points.
- [ ] `[must]` `MC1-02` Package and activate every owned component through the
  ordinary ProjectRelease pipeline; remove scenario-driven local skill-copy or
  ad hoc install behavior.
- [ ] `[must]` `MC1-03` Express coordinator and agent placement intent through
  the public Deployment SDK and bind resulting activations by stable refs.
- [ ] `[must]` `MC1-04` Implement the media-specific administration projection
  over generic deployment records: compatible nodes, desired placement,
  version, health, generation, roots, shard revision, pressure, and operations.
- [ ] `[must]` `MC1-05` Implement agent `cordon`, `drain`, shard detach, route
  revocation, package removal, and independent derived-data retention choice.
- [ ] `[must]` `MC1-06` Preserve the colocated single-node shape as a normal
  deployment policy, not a separate compatibility implementation.
- [ ] `[must]` `MC1-07` Reject incompatible coordinator/agent release sets
  before activation and expose bounded version-skew policy during staged
  rollout.
- [ ] `[should]` `MC1-08` Add capability-based `all_matching` placement after
  manual selected-node placement is proven.
- [ ] `[could]` `MC1-09` Add planner recommendations using free storage,
  architecture, probe/rendition capability, and operator labels.
- [ ] `[deferred]` `MC1-10` Add automatic coordinator leader election and
  standby relocation after one explicit coordinator is production-accepted.

## Milestone MC2: Distributed Roots, Shards, And Jobs

**Outcome:** selected agents index large node-local roots in place and publish
resumable catalog deltas and bounded operational state.

**Admission gate:** MC1 activation refs and deployment status are stable.
`DS2` external-authority/derived-projection topology and the required `DS3`
adapter operation slice are integrated.

**Exit proof:** two agents import representative roots, restart mid-scan,
converge to exact shard/catalog revisions, and preserve playback/control
budgets while one slow source and one offline node are present.

- [ ] `[must]` `MC2-01` Define versioned contracts for `LibraryRoot`,
  `FolderNode`, `LibraryShard`, source delta, scan checkpoint, and media job.
- [ ] `[must]` `MC2-02` Move node-local root enumeration, technical probing,
  source registration, and shard ownership into `media_library_agent`.
- [ ] `[must]` `MC2-03` Keep original media in place through
  `register_media_file(...)`; add a regression that proves imported byte
  content is absent from `.adaos` while references and derived metadata exist.
- [ ] `[must]` `MC2-04` Make root import asynchronous: return a durable job id,
  use operation-appropriate multi-minute deadlines, and separate browser action
  acknowledgement from scan lifetime.
- [ ] `[must]` `MC2-05` Implement incremental add/change/move/delete detection,
  tombstones, exact source revisions, resumable checkpoints, cancellation
  disposition, and restart recovery.
- [ ] `[must]` `MC2-06` Publish coalesced progress and health projections with
  counts, throughput, phase, pressure, checkpoint age, bounded error reason,
  and skill-localized human message.
- [ ] `[must]` `MC2-07` Enforce media-kind policy with images disabled by
  default until an image presentation/player is supported.
- [ ] `[must]` `MC2-08` Bound scan/probe/hash concurrency and CPU, RSS, disk I/O,
  network and synchronized-state pressure; playback and command transport have
  priority.
- [ ] `[must]` `MC2-09` Represent agent/shard availability independently from
  known catalog identity and expose truthful stale/partial state.
- [ ] `[should]` `MC2-10` Add scheduled scans and filesystem watching with
  debounce, overflow recovery, and periodic reconciliation.
- [ ] `[should]` `MC2-11` Support root overlap detection, exclusion patterns,
  symlink/mount policy, and node-local path diagnostics restricted to operators.
- [ ] `[could]` `MC2-12` Add scan windows and pause-on-playback policy per node.

## Milestone MC3: Catalog, Search, Folders, And Collections

**Outcome:** one global catalog provides fast bounded search and familiar media
navigation while retaining shard provenance and partial-result truth.

**Admission gate:** MC2 publishes stable source and shard revisions.

**Exit proof:** the representative 20,000-item catalog supports cursor-backed
search, list/grid/rail reads, folder browsing, typed collections, duplicate
candidates, and unavailable-node behavior without renderer or Yjs pressure.

- [ ] `[must]` `MC3-01` Implement stable `MediaSource`, `MediaVariant`,
  `MediaWork`, `MediaCollection`, `CollectionMembership`, `MetadataClaim`, and
  merge/split alias identities with schema and migration tests.
- [ ] `[must]` `MC3-02` Maintain a compact coordinator catalog from idempotent
  agent deltas; retain exact source/shard/catalog revision evidence.
- [ ] `[must]` `MC3-03` Index normalized title, filename, meaningful folder
  segments, embedded tags, collection names, aliases, people, locale, and
  user-visible root alias.
- [ ] `[must]` `MC3-04` Fix global filename and folder-name search with explicit
  query execution behavior, ranking version, filters, cursor, bounded limit,
  partial participation, and deterministic continuation.
- [ ] `[must]` `MC3-05` Implement opaque cursor-backed root/folder browsing,
  breadcrumbs, lazy children, sorting, access filtering, and stable-as-possible
  rename/move identity.
- [ ] `[must]` `MC3-06` Add bounded read models for Home, Movies, Series, Music,
  Audiobooks, Playlists, Folders, Favorites, Recent, and Continue.
- [ ] `[must]` `MC3-07` Default catalog pages to 30 visible records and short
  player/playlist selectors to at most 10; prove no unbounded dropdown or shared
  document is created.
- [ ] `[must]` `MC3-08` Model ordered series/season/episode, album/disc/track,
  audiobook/part/chapter, and playlist membership without collapsing their
  ownership and lifecycle semantics.
- [ ] `[must]` `MC3-09` Implement cheap duplicate and variant candidates using
  exact technical facts and bounded hashing; never delete a candidate source.
- [ ] `[should]` `MC3-10` Add federated deep-search stages for transcripts,
  technical fields, embeddings, or not-yet-replicated agent data.
- [ ] `[should]` `MC3-11` Add operator/user merge, split, regroup, and metadata
  correction flows with provenance and reversible audit.
- [ ] `[could]` `MC3-12` Add semantic and phonetic ranking for multilingual and
  voice-originated queries after deterministic full-text evaluation exists.

## Milestone MC4: Playback Sessions And Control Surfaces

**Outcome:** playback belongs to a persistent endpoint runtime and can be
controlled from another authorized webspace without routing bytes through the
controller or coordinator.

**Admission gate:** MC3 can resolve work, collection, source, and variant.

**Exit proof:** phone controls TV playback from two agents, modal closure keeps
the session in a mini-player/PiP policy, queue/autonext works, and coordinator,
source node, endpoint, or command-channel interruption has deterministic
recovery evidence.

- [ ] `[must]` `MC4-01` Define versioned `PlaybackSession`, `PlaybackTarget`,
  control/output lease, queue, queue item, playback plan, command revision, and
  checkpoint contracts.
- [ ] `[must]` `MC4-02` Move the media element and active playback ownership to
  one app-shell `PlaybackCoordinator`; make modal, fullscreen, mini-player and
  PiP views controllers only.
- [ ] `[must]` `MC4-03` Route source bytes directly from the selected agent to
  the playback endpoint through core-authorized media routes; record the actual
  path and fallback reason.
- [ ] `[must]` `MC4-04` Implement explicit source/variant selection using
  availability, codec support, quality, language, endpoint capability, network
  estimate, and user override.
- [ ] `[must]` `MC4-05` Build queues from a work, album, audiobook, season,
  series, folder, playlist, or bounded browse snapshot; support edit, next,
  previous, skip-unavailable and ordered autonext.
- [ ] `[must]` `MC4-06` Add remote target selection and revision-safe
  play/pause/seek/volume/tracks/queue/stop/handoff commands with authorization
  and idempotency.
- [ ] `[must]` `MC4-07` Persist bounded resume checkpoints and reconcile after
  browser, coordinator, source-node, or channel interruption without duplicate
  start/seek.
- [ ] `[must]` `MC4-08` Implement profile/endpoint `autoplay` and `auto
  fullscreen` settings with visible effective policy.
- [ ] `[must]` `MC4-09` Integrate Media Session metadata, handlers and position
  state; keep high-frequency position updates browser-local.
- [ ] `[must]` `MC4-10` Support audio background-page playback and explicit
  video PiP/pause/audio-only policy within browser guarantees.
- [ ] `[should]` `MC4-11` Add subtitle/audio-track selection, playback speed,
  chapter navigation, sleep timer and gapless-audio evaluation.
- [ ] `[should]` `MC4-12` Add playback QoE metrics: plan latency, first frame,
  seek, rebuffer, route changes, interruptions, and completion.
- [ ] `[deferred]` `MC4-13` Guarantee playback after process suspension/kill
  through Android Media3 and Apple native background sessions.

## Milestone MC5: Adaptive UI And Settings

**Outcome:** one UI-as-data product feels familiar and efficient on TV,
desktop, and phone-control surfaces without component overlap or large-list
failure.

**Admission gate:** MC3 read models and MC4 playback controls have fixtures;
real distributed acceptance follows MC4 integration.

**Exit proof:** TV D-pad, desktop keyboard/mouse, and mobile touch E2E suites
cover browse, search, details, playback, settings, degraded data, long text, and
large collections at representative viewports with screenshots and performance
budgets.

- [ ] `[must]` `MC5-01` Add explicit `tv`, `desktop`, `mobile_control`, and
  `embedded` presentation-profile context independent of viewport breakpoints.
- [ ] `[must]` `MC5-02` Extend generic UI-as-data contracts with cursor-backed
  collections and `list`, `grid`, `rail`, and bounded `carousel` projections;
  keep business logic in scenario/skills.
- [ ] `[must]` `MC5-03` Add virtualized rendering, stable dimensions, loading,
  empty, partial, stale, failed, and retry states for large collections.
- [ ] `[must]` `MC5-04` Add semantic focus groups, deterministic D-pad movement,
  focus/activation separation, focus restoration, Back behavior, and
  overscan-safe TV layout.
- [ ] `[must]` `MC5-05` Implement TV navigation and Home rails plus complete Grid
  views for Movies, Series, Music, Audiobooks, Playlists, and Folders.
- [ ] `[must]` `MC5-06` Implement compact desktop navigation, explicit search,
  filters/sort, list/grid toggle, folder tree/drill-down, details modal, and
  persistent mini-player.
- [ ] `[must]` `MC5-07` Implement mobile Now Playing, target picker, transport,
  queue/handoff, and secondary Browse/Search surfaces as the control skill.
- [ ] `[must]` `MC5-08` Implement Settings sections for General, Libraries,
  Nodes and agents, Playback, Metadata, Profiles/access, Performance, and
  Diagnostics using typed actions and localized feedback.
- [ ] `[must]` `MC5-09` Ensure cards, focus scale, controls, translated text and
  dynamic metadata never overlap or resize fixed tool surfaces across supported
  profiles.
- [ ] `[must]` `MC5-10` Keep all player controls connected to the app-shell
  coordinator and remove the media dropdown as a primary catalog surface.
- [ ] `[should]` `MC5-11` Add profile/device customization for home-row order,
  default list/grid view, density, and default target while retaining stable
  navigation.
- [ ] `[could]` `MC5-12` Add editorial featured rails only when real artwork and
  metadata quality are sufficient; do not block core browsing on hero content.

## Milestone MC6: Personalization, Access, And Voice

**Outcome:** several household members and surfaces share the library while
personal state, policy, and control authority remain correctly separated.

**Admission gate:** platform actor/profile/device/endpoint contracts required
by the Personalization and Device Access roadmaps are integrated.

**Exit proof:** two profiles and two browsers demonstrate isolated personal
state, explicitly shared playlists/queues, policy-filtered search/playback, and
voice control with target disambiguation.

- [ ] `[must]` `MC6-01` Key favorites, history, resume, ratings, hidden items,
  track/language preferences and recommendations by actor/profile rather than
  browser or webspace.
- [ ] `[must]` `MC6-02` Publish subscription-backed Favorites, Recent,
  Continue, Queue and Now Playing projections; prove cross-browser convergence
  after authoritative revision acknowledgement.
- [ ] `[must]` `MC6-03` Model personal, household and named-shared playlist and
  queue ownership with explicit edit/read/control permissions.
- [ ] `[must]` `MC6-04` Enforce library and parental policy in query and playback
  planning, not only in the renderer.
- [ ] `[must]` `MC6-05` Add profile selection and privacy-safe shared-TV home
  behavior without leaking another profile's history or recommendations.
- [ ] `[must]` `MC6-06` Define voice intents over existing search, collection,
  queue, favorite, status, target and playback actions.
- [ ] `[must]` `MC6-07` Resolve voice requests using profile, room, target,
  focused result and dialog context; clarify ambiguous work, collection,
  variant, profile or endpoint.
- [ ] `[must]` `MC6-08` Project bounded visual voice results and use the same
  authorization and localized human feedback as direct UI actions.
- [ ] `[should]` `MC6-09` Add household recommendations with explainable source
  signals and opt-out after history quality and privacy controls are proven.
- [ ] `[could]` `MC6-10` Add natural compound controls such as "play the next
  episode in the living room and lower volume after 10 PM" through governed
  workflow mediation.

## Milestone MC7: Enrichment, Variants, And Production Operations

**Outcome:** metadata and playback quality improve in the background while the
distributed system remains bounded, repairable, secure, and supportable.

**Admission gate:** MC1 through MC6 must tasks are integrated and their local
proofs are reproducible.

**Exit proof:** the acceptance shape in the target architecture passes locally
and on the designated stand with exact release/deployment evidence, failure
injection, resource budgets, and a reviewed production decision.

- [ ] `[must]` `MC7-01` Implement provider-based background enrichment with
  claim provenance, confidence, locale, conflict status, schedules, retries,
  privacy disclosure, and observable progress.
- [ ] `[must]` `MC7-02` Implement deterministic grouping plus reversible
  provider/user-assisted merge and split for series, seasons, albums,
  audiobooks, duplicates, and alternatives.
- [ ] `[must]` `MC7-03` Add bounded technical probing and rendition planning for
  browser-incompatible media; register every output as a derived resource tied
  to an exact source revision.
- [ ] `[must]` `MC7-04` Enforce rendition concurrency, CPU, RSS, I/O, disk quota,
  cancellation, source-change invalidation, cleanup, and no-partial-advertise
  guarantees.
- [ ] `[must]` `MC7-05` Add deployment, scan, catalog, search, provider,
  playback, route, projection, and browser performance dashboards with
  sanitized diagnostic export.
- [ ] `[must]` `MC7-06` Exercise coordinator restart, agent loss, partial
  deployment, blocked filesystem I/O, interrupted scan, stale shard, route
  fallback, unsupported codec, browser reconnect, and conflicting controllers.
- [ ] `[must]` `MC7-07` Run local large-library, long-duration, CPU/memory,
  Yjs-pressure, browser-render, and playback-under-indexing tests against the
  declared budgets.
- [ ] `[must]` `MC7-08` Deploy the exact ProjectRelease through normal channels
  to the designated stand, execute TV plus controller E2E, and record package
  digests, deployment generation, node activations, catalog/shard revisions,
  routes, test output and screenshots.
- [ ] `[must]` `MC7-09` Perform security/privacy review for remote deployment,
  root containment, route grants, provider egress, shared-screen state, voice,
  logs, derived data and uninstall retention.
- [ ] `[must]` `MC7-10` Record an explicit production acceptance, bounded pilot,
  or rejection decision; do not infer acceptance from a successful stand run.
- [ ] `[should]` `MC7-11` Add automatic repair recommendations and reviewed
  reconcile plans for missing agents, stale shards, failed providers and
  incompatible variants.
- [ ] `[could]` `MC7-12` Add embeddings, perceptual duplicate detection,
  semantic search and richer recommendations behind provider/resource budgets.
- [ ] `[deferred]` `MC7-13` Add trusted cross-subnet federation after one-subnet
  authorization, routing, consistency and operations are production-accepted.
- [ ] `[deferred]` `MC7-14` Add adaptive-bitrate packaging and hardware
  transcoding after the bounded single-rendition worker is proven.

## Cross-Domain Dependencies

| Requirement | Owning architecture/roadmap | Media Center dependency |
| --- | --- | --- |
| Distributed Project desired/observed state | Project composition and artifact activation, `AP8` | MC1 |
| Service groups, partitions, replicas and routes | Distributed Service And Data Topology Roadmap, `DS1`-`DS4` | MC1-MC4 |
| Typed subscriptions and bounded projections | Skill Projection Runtime SDK and Projection Subscription Roadmap | MC2, MC3, MC6 |
| Actor/profile/device/endpoint identity | Personalization and Device Access roadmaps | MC4, MC6 |
| Browser media leases and shell ownership | Browser Media Runtime | MC4 |
| Route recovery and sync truth | Realtime Reliability Roadmap | MC4, MC7 |
| UI-as-data collections and focus | Web UI Architecture and Webspace Evolution Roadmap | MC5 |
| Human-readable localized action errors | operational events/projection and skill i18n contracts | all operator surfaces |
| Native background sessions | Android full-node and future Apple runtime work | deferred MC4-13 |

Cross-domain owners implement their generic contracts. Media Center supplies a
representative consumer and acceptance fixture; it does not clone those
contracts into product code.

## Evidence Policy

- Commit each coherent architecture, core, agent, catalog, playback, client,
  and rollout slice independently.
- Link every completed implementation task to exact commits and reproducible
  tests or operation records.
- Record repository and submodule revisions together for cross-repository UI or
  skill changes.
- Keep failed stand and failure-injection evidence when it changes a decision.
- Distinguish local, stand, pilot, and production acceptance.
- Validate UI changes with desktop, TV-like, and mobile screenshots plus
  keyboard/D-pad/touch interaction tests.
- Validate large-library behavior with generated metadata fixtures and real
  slow-storage cases; do not require redistributing private source media.
- Validate playback with compatible and incompatible codecs, seeking, route
  fallback, modal closure, queue transition, reconnect and explicit Stop.
- Update this roadmap only from evidence; use the Issue Tracker for active work
  and short-lived investigation state.

## Definition Of Distributed Household MVP

The distributed household MVP is complete only when all `[must]` items through
MC6 are at least `validated-stand`, MC7-01 through MC7-10 have their stated
evidence, and an explicit bounded production or pilot decision is recorded.
Completing the current single-node scenario, a UI mockup, or an agent install
alone does not satisfy this definition.
