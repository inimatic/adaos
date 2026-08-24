# Distributed Media Center Target Architecture

Status: target architecture for the AdaOS Media Center product family.

Last reviewed: 2026-08-19.

This document owns the target product boundary, distributed topology, domain
model, playback model, presentation profiles, and production invariants for
Media Center. Delivery order and evidence gates are owned by the
[Media Center Roadmap](media-center-roadmap.md). The currently implemented
bounded slice remains documented in [Media Center MVP](media-center-mvp.md).
Generic service groups, instances, partitions, replicas, leases, freshness,
and route guarantees are owned by
[Distributed Service And Data Topology](distributed-service-and-data-topology.md).
Cross-domain canonical resource identity, source observations, reusable
artifacts, optional versions, aliases, localization, hybrid/latent retrieval,
and actionable dialog/voice contracts are owned by the
[Subnet Knowledge Fabric Target Architecture](subnet-knowledge-fabric.md).

## Decision Summary

AdaOS Media Center is one distributed Project with several independently
placed roles:

- one logical `media_center_skill` coordinator per home subnet;
- one `media_library_agent` on each selected storage or compute node;
- one shared `media_center` scenario with explicit TV, desktop-library, and
  compact presentation profiles;
- one separately discoverable `media_control_skill` contribution for remote
  control from another webspace;
- one persistent browser or native playback coordinator per playback endpoint.

The Project is the immutable unit of composition and delivery. A separate core
`ProjectDeployment` desired-state contract places Project components on nodes.
The existing `ProjectPlacement` remains the presentation binding for a webspace
and must not become a mixed software-deployment and UI-placement record.

Media discovery, indexing, metadata, grouping, search, personalization, queues,
domain partition keys, query merge, and source-selection policy remain product
semantics. Core owns reusable package activation, node identity and
capabilities, service membership, partition/replica topology, authority
leases/epochs, freshness and route facts, projections, access policy, media
resource descriptors, safe ranged transport, and browser host capabilities.
Replication payloads and domain merge execute through skill/service adapters;
core is not a transparent distributed database.

## Goals

The target system must:

1. browse, search, group, and play large home libraries without copying source
   media into `.adaos`;
2. use storage and compute available on several AdaOS nodes in one subnet;
3. keep one coherent household catalog while preserving node-local ownership
   and availability;
4. support TV, desktop, phone-control, and embedded presentations without
   forking domain behavior;
5. preserve personal favorites, history, progress, recommendations, and policy
   while allowing explicitly shared queues and playlists;
6. continue useful browsing and playback during partial node or control-channel
   failure;
7. make scanning, enrichment, deployment, routing, and playback observable and
   bounded;
8. provide stable extension points for voice control, metadata providers,
   renditions, subtitles, recommendations, and future node federation.

## Non-Goals

The first production-oriented milestones do not require:

- a public streaming service or internet-scale multi-tenant catalog;
- moving media domain entities into AdaOS core;
- automatic copying of an entire library into a managed store;
- a consensus database across all home nodes;
- simultaneous active versions of the same canonical skill id on one node;
- guaranteed background playback after a browser process is killed;
- automatic metadata edits with no provenance or conflict policy;
- cross-household or untrusted internet federation.

## Architectural Invariants

1. **Original bytes stay at their owner.** Folder import registers and indexes
   files in place. A rendition, thumbnail, subtitle, waveform, or cache is a
   separately identified derived artifact with provenance, quota, and cleanup
   policy.
2. **Release is not deployment.** `ProjectRelease` says exactly what is
   deliverable; `ProjectDeployment` says where its components should run;
   `ProjectPlacement` says where a presentation is exposed.
3. **Placement is not sharding.** Component placement is a core deployment
   concern. Generic partition assignment, replica lifecycle, freshness and
   route resolution are core distributed-runtime concerns. What a
   `LibraryShard` contains, its partition key, catalog delta format, search
   fan-out, result merge and ranking are Media Center concerns.
4. **The coordinator is logically singular, not process-global.** One active
   coordinator owns a subnet catalog revision and routing decisions. A future
   lease-based standby may replace it without changing product contracts.
5. **Data follows the shortest authorized path.** Media bytes normally travel
   from the source node to the selected playback endpoint. The coordinator is
   not a mandatory media-byte proxy.
6. **All unbounded work is asynchronous.** Import, scan, probe, hash,
   enrichment, rendition, and migration requests return a durable job identity
   and publish progress. A client action must not wait minutes for a complete
   library scan.
7. **Every large collection is cursor-backed.** Search, folder children,
   collections, queues, and deployment inventories have bounded pages. The UI
   never materializes a 20,000-item result into one document or dropdown.
8. **Presentation profile is semantic.** TV focus and remote navigation are not
   inferred only from viewport size. Endpoint input capabilities and webspace
   intent select a profile.
9. **Playback outlives its views.** Closing a detail or player modal detaches a
   controller; it does not stop the app-shell playback session.
10. **Personal and shared state are explicit.** Actor, profile, household,
    playback target, controller, and session identities are never inferred from
    one webspace id.
11. **Partial failure is normal.** Offline nodes make their sources unavailable
    without corrupting the global catalog, blocking unrelated nodes, or
    reporting stale data as fresh.
12. **Skills use public SDK planes.** Media skills do not call internal package
    managers, node stores, route tables, or Yjs implementation helpers.

## Plane Model

| Plane | Core responsibility | Media Center responsibility |
| --- | --- | --- |
| Delivery | immutable packages, `ProjectRelease`, signatures, compatibility locks | Project composition and required component roles |
| Deployment | node inventory, capability selectors, desired/observed activation, operation journal, rollback | placement intent and Media Center administration projection |
| Distributed topology | service groups/instances, partitions/replicas, leases/epochs, freshness, route and topology operations | root/shard keys, catalog deltas, query merge, domain replication adapters |
| Media transport | resource descriptors, root containment, MIME, Range, authorized route primitives | source choice, rendition choice, queue and playback policy |
| Projection and jobs | typed projections, subscriptions, bounded streams, operation status | catalog deltas, scan/enrichment progress, favorites, recent and now playing |
| Identity and access | actor, profile, device, endpoint, webspace and policy evaluation | library grants, parental policy, personal/shared media state |
| Product domain | none | roots, folders, sources, works, variants, collections, metadata and search |
| Presentation | shell, UI-as-data renderers, focus and media host capabilities | information architecture, cards, rails, details, settings and remote control |

## Project Composition

The target distributable Project owns the following logical components:

| Component | Project role | Exposure | Placement owner |
| --- | --- | --- | --- |
| `scenario:media_center` | primary presentation | application | `ProjectPlacement` in selected webspaces |
| `skill:media_center_skill` | coordinator | project-only or advanced | `ProjectDeployment` singleton in subnet |
| `skill:media_library_agent` | distributed implementation | project-only | `ProjectDeployment` on selected capable nodes |
| `skill:media_control_skill` | control presentation | application | component activation plus `ProjectPlacement` contribution |

The exact component names may evolve before their first ProjectRelease. Their
responsibilities and deployment separation are stable decisions.

The Project may expose several entry points backed by the same domain
contracts:

- `living_room`, with `presentation_profile=tv`;
- `library`, with `presentation_profile=desktop`;
- `remote`, with `presentation_profile=mobile_control`;
- `diagnostics`, with advanced exposure and explicit operator authorization.

`media_library_agent` is omitted from ordinary Catalog and Desktop discovery.
It remains an ordinary signed, versioned, directly diagnosable package.

## Distributed Project Deployment

The generic core deployment extension introduces separate durable identities:

- `NodeInventoryRecord`: node identity, trust state, online state, architecture,
  runtime version, storage/compute capabilities, labels, and capacity summary;
- `ProjectDeployment`: desired ProjectRelease, component placement policies,
  rollout policy, data-retention intent, and generation;
- `DeploymentPlan`: immutable planner result resolving policies to exact nodes,
  packages, compatibility checks, expected changes, warnings, and approvals;
- `ComponentActivation`: observed package, runtime instance, health, generation,
  and evidence for one component on one node;
- `DeploymentOperation`: journaled install, update, drain, remove, reconcile, or
  rollback operation with per-node results;
- `DeploymentRevision`: compare-and-switch authority preventing two operators
  from silently applying conflicting desired state.

Initial placement modes are:

- `singleton`: exactly one eligible active member;
- `selected_nodes`: an explicit operator-maintained node set;
- `all_matching`: all trusted nodes matching capability and label selectors;
- `per_endpoint`: colocated with selected endpoint roles;
- `co_located_with`: activated with another declared component role.

Webspace exposure is resolved separately through `ProjectPlacement`; it is not
a component deployment mode.

The first Media Center release uses manual `selected_nodes` placement for
library agents and one explicit coordinator node. Automatic scoring, leader
election, and relocation are later policies over the same contract.

Deployment reconciliation is idempotent per node but is not presented as an
all-subnet atomic transaction. A rollout may be partially successful. The
coordinator records compatible old and new agent versions, stages updates,
stops on policy-defined failure, and retains enough evidence for bounded retry
or rollback.

Removing a stateful agent follows:

```text
cordon -> stop new jobs -> drain/checkpoint -> detach shards and routes
       -> revoke runtime leases -> uninstall package
```

Source roots are never deleted by this sequence. Derived indexes and renditions
follow a separate `retain`, `remove`, or `export` decision.

## Runtime Topology

```text
                         Media Center Project

 phone / desktop control ----+                    +---- TV endpoint
                             |                    |
                             v                    v
                      PlaybackSession / target leases
                                  |
                                  v
                 one logical MediaCenterCoordinator
                  catalog, policy, plans, query merge
                       /                     \
              deltas / queries         deltas / queries
                   v                         v
          MediaLibraryAgent A       MediaLibraryAgent B
          roots + local shard       roots + local shard
                   |                         |
                disk/NAS                  disk/NAS

 media data: selected source node --------------------> playback endpoint
```

### Coordinator responsibilities

- maintain the compact global catalog and its revision;
- merge agent inventory deltas and availability;
- execute global search and federated query planning;
- resolve works, collections, variants, duplicates, and metadata claims;
- own household-level jobs, schedules, policies, and provider configuration;
- choose a source/rendition and issue an authorized playback plan;
- own durable playback-session, queue, shared playlist, and control-lease state;
- publish bounded projections for catalog, jobs, deployment, personalization,
  and playback.

The coordinator does not read every file directly and does not proxy every
media byte.

### Library agent responsibilities

- validate and persist node-local library roots;
- enumerate folders incrementally with cancellation and backpressure;
- derive stable folder/source identity and detect add, modify, move, and delete;
- extract filenames, folder tokens, stat data, MIME, duration, codecs, tracks,
  embedded tags, artwork references, and bounded fingerprints;
- maintain a node-local searchable shard and change journal;
- execute permitted metadata, subtitle, thumbnail, waveform, transcription,
  fingerprint, or rendition jobs within resource budgets;
- serve authorized source and derived resources through core media contracts;
- publish availability, shard revision, progress, health, pressure, and errors.

Agents do not decide household favorites, global canonical identity, or which
TV should play a request.

In production, one persistent `media_library_agent` service process is the
exclusive owner of scan/rendition workers. Root-runtime tools only enqueue,
cancel or update durable policy in the shared agent database. This prevents
two processes from claiming/recovering the same queue while preserving a
standalone embedded worker as an explicit development mode.

### Generic topology mapping

The runtime topology is represented through the common core layer:

- the logical coordinator is a singleton `ServiceGroup` with a fenced authority
  epoch;
- each agent is a `ServiceInstance` in the library-agent service group;
- each root-owned shard is initially an `external_authority` partition because
  original bytes remain under the node filesystem's authority;
- the compact coordinator catalog is a `derived_projection` Dataset whose
  watermarks name participating shard revisions;
- agent query participation and source playback paths are resolved through
  generic partition/service routes, while Media Center performs result merge,
  ranking and source choice.

The initial release does not require replicated original media or a replicated
coordinator database. Those are later topology policies, not new Media Center
protocols.

## Media Domain Model

The catalog separates physical storage from semantic content:

| Entity | Meaning |
| --- | --- |
| `LibraryRoot` | authorized node-local directory with display alias, policy, and scan configuration |
| `FolderNode` | opaque, stable-as-possible node in one root hierarchy; physical path remains agent-private |
| `MediaSource` | concrete original or derived resource on one node, including revision and availability |
| `MediaVariant` | one playable rendition or edition with language, container, codec, resolution, bitrate, and quality facts |
| `MediaWork` | semantic content identity such as movie, episode, track, audiobook chapter, or home video |
| `MediaCollection` | typed grouping such as series, season, album, disc, audiobook, playlist, box set, or folder-derived set |
| `CollectionMembership` | ordered, typed edge between collection and work or nested collection |
| `MetadataClaim` | value plus subject, provider, provenance, confidence, locale, observed revision, and conflict status |
| `LibraryProjectionItem` | denormalized read model for one view/search result, never the authoritative aggregate |

The normal relation is:

```text
LibraryRoot -> FolderNode -> MediaSource -> MediaVariant -> MediaWork
                                                     ^          |
                                                     |          v
                                              duplicate set  collections
```

Several sources may realize the same variant; several variants may realize the
same work. A playlist reuses ordered membership mechanics but remains a
user-owned collection with different edit, authorization, and lifecycle rules
from a detected series or album.

File names alone are not safe semantic identity. In particular, numbered audio
files use normalized folder/collection context plus track/chapter title for
their provisional work identity. Matching contextual identities on different
agents may become variants; different books containing `0.mp3` must remain
different works until reviewed fingerprint/provider/user evidence merges them.
Physical source identity remains stable when a work is regrouped.

Stable opaque ids, aliases, and merge/split records are required. A metadata
provider changing its title must not silently change favorites, history, deep
links, or queue entries.

## Storage And Source Semantics

`register_media_file(...)` remains the mandatory path for original user-owned
files. The agent records reference metadata and streams the original through a
root-contained core resource. `publish_media_file(...)` is used only when a
generated or explicitly uploaded artifact is intended to become managed data.

The system distinguishes:

- original source bytes, owned outside `.adaos`;
- node-local shard/index data, rebuildable and retention-controlled;
- coordinator catalog and relationship data, durable product state;
- personal state, keyed by actor/profile and synchronized under policy;
- derived artifacts, quota-managed and linked to exact source revisions;
- ephemeral caches, safe to evict and never treated as the only copy.

Derived media stays on the source-owning node whenever that node can perform
the work. Its identity is content-addressed from the exact source fingerprint,
the transformation recipe, tool version, and policy revision. Reconnecting a
root or rebuilding a coordinator can therefore recover valid thumbnails,
subtitles, embeddings, remuxes, and transcodes without treating any derived
artifact as the source of truth. A node may keep the bytes beside its managed
shard state or in a policy-approved sidecar directory on the same storage; the
catalog and subnet knowledge projections retain only descriptors, provenance,
availability, and checksums. Derived bytes are quota-managed, evictable, and
never overwrite the original.

The default physical layout is a node-managed, content-addressed derived store,
separate from user library folders. Logical context does not depend on that
directory layout: each derived `MediaSource` has a transformation receipt that
names its exact input source revision, recipe and tool version, and realizes a
`MediaVariant` of the same `MediaWork`. Remuxing or transcoding therefore does
not create another movie or episode. Different editions, cuts, languages, or
content fingerprints remain distinct variants; codec/container/resolution
representations of one exact input remain renditions of that variant.

One content-addressed output may be referenced by several equivalent sources,
but every provenance edge remains explicit. A source tombstone moves dependent
outputs to an orphan grace state. Garbage collection removes bytes only after
the grace period, when no live source, pinned offline policy, active playback,
queue, or retained transformation receipt references them. Reappearing content
with the same strong fingerprint may reclaim the output without recomputation;
a changed source revision invalidates it. Removing a derived output never
deletes an original. Writing hidden cache folders into user libraries is an
opt-in portability policy, not the default and not required for recovery.

A source path is never used as a public cross-node identifier. Diagnostics may
show it only to an authorized operator on the owning node.

## Scan, Index, Search, And Enrichment

### Root admission

Adding a root validates containment, readability, duplicate/overlap policy,
filesystem characteristics, and authorization. It creates a durable import job
and returns immediately. Default job deadlines are measured in minutes or are
operation-specific; browser action timeouts do not terminate the durable job.

Images are excluded by the initial Media Center product policy while image
playback is unsupported. The root keeps an explicit media-kind policy so image
support can be enabled later without changing scan contracts.

### Scan pipeline

```text
enumerate -> stat/classify -> technical probe -> local upsert/tombstone
          -> publish delta -> group/deduplicate -> schedule enrichment
```

Every stage is resumable, bounded, cancellable where the underlying operation
allows it, and observable. Slow or blocking filesystem reads run behind strict
concurrency, process, or I/O boundaries appropriate to the platform. Playback
gets higher resource priority than scan, hash, enrichment, and rendition work.

The searchable document includes normalized title, original filename, all
meaningful folder segments, embedded tags, people, collection names, provider
aliases, language, and user-visible root alias. Folder names are evidence, not
unconditionally authoritative metadata.

### Query topology

The coordinator keeps a compact full-text catalog sufficient for ordinary
global search while agents retain detailed local indexes. The initial query
path is coordinator-local and continues to return known unavailable items.
Optional federated stages query eligible agents for deep technical, transcript,
embedding, or newly indexed data and merge results by stable identity.

Search contracts include normalized query, filters, sort/ranking version,
cursor, bounded limit, catalog revision, participating shard revisions,
partial-result status, and continuation. Search submission is explicit or
debounced by declared UI behavior; it is never ambiguous to the user.

### Background jobs and projections

Each job publishes a compact projection containing:

- id, kind, owner node, root/collection scope, state, and generation;
- discovered, examined, added, changed, removed, skipped, failed, and remaining
  estimates;
- current bounded phase and sanitized subject;
- throughput, pressure, start/update/checkpoint times, and retry disposition;
- last human-facing message reference plus machine-readable reason and params.

High-frequency counters are sampled or coalesced before shared-state
publication. File-by-file events stay in bounded diagnostics, not Yjs.

Metadata enrichment is provider-based. Providers emit claims with provenance
and confidence; they do not overwrite the canonical record directly. Local
embedded metadata, folder-derived hints, external databases, user corrections,
transcription, OCR, and embeddings use the same claim/reconciliation model.

Collection artwork is a bounded projection, not a generated animation. The
coordinator chooses a ready representative rendition from a child collection
or member. The source-owning agent evaluates up to three deterministic video
sample positions and rejects near-black or otherwise low-information frames
before publishing one static rendition. A client may later rotate several
existing representatives as a slideshow, but animated GIF generation is not a
canonical catalog or indexing responsibility.

## Grouping, Duplicates, And Alternatives

Grouping is a staged process, not filename parsing embedded in the UI:

1. deterministic technical facts and folder hierarchy;
2. filename/folder tokenization and embedded tags;
3. provider matches and confidence-scored structural inference;
4. duplicate and variant candidates;
5. canonical merge, split, or user correction with durable provenance.

Episode filename parsing uses a bounded, versioned parser and deterministic
fallback. External databases such as TMDB contribute localized titles,
external ids, posters and structural claims; they do not silently merge an
ambiguous match. A high-confidence external id, an explicit user correction,
or reviewed reconciliation can promote a claim to canonical identity. This
keeps multiple season folders in one series without making network provider
availability a prerequisite for stable browsing.

The model must represent at least:

- series -> season -> episode;
- album -> disc -> track;
- audiobook -> part/chapter;
- movie or work -> edition/variant -> one or more sources;
- arbitrary personal or shared playlist;
- ordered queue derived from a collection without becoming that collection.

Duplicate detection progresses from cheap size/technical signatures to partial
and full hashes only when policy permits. Perceptual matching is optional. A
duplicate decision never deletes a source automatically.

Variant selection considers reachability, browser/native codec support,
language, subtitle/audio preferences, resolution, bitrate, endpoint limits,
network estimate, and user override. Unsupported variants may schedule a
bounded rendition job owned by a media skill worker; transcoding is not a core
media responsibility.

Every source/endpoint decision has one explicit result: `direct`, `remux`,
`transcode`, or `unsupported`. Direct playback is preferred. Lossless remux is
preferred over codec conversion when only the container is incompatible.
Transcoding writes a content-addressed derived resource under CPU, memory,
disk, concurrency, cancellation, progress, and retention budgets. The source
is never modified. Background pre-transcode is the first production mode;
real-time transcoding is admitted only after node capacity and playback QoE
gates are proven. A failed load records a source/endpoint compatibility verdict
but does not enter Recent, resume, or watched history until the endpoint emits
a confirmed playback event.

## Playback And Control

### Playback session

`PlaybackSession` is independent from the controller browser and presentation.
It contains:

- active work, chosen variant/source, queue, queue revision, and position;
- playback state, rate, volume intent, selected tracks, subtitle policy, and
  autoplay decision;
- target endpoint, output lease, controlling actors, and control-lease revision;
- source and route generation, last checkpoint, interruption, and recovery
  state.

Commands use expected revisions or idempotency keys. Stale phone controls must
not overwrite a newer TV queue or seek.

### Persistent endpoint coordinator

The application shell owns one `PlaybackCoordinator` and persistent media
element per browser endpoint. Fullscreen player, modal details, mini-player,
Picture-in-Picture, and remote-control views are controllers of that runtime.
Only explicit Stop or Dismiss releases the source and output lease.

The coordinator integrates the W3C
[Media Session API](https://www.w3.org/TR/mediasession/) for metadata,
play/pause/seek actions, and position state. It publishes bounded now-playing
status through the browser media runtime rather than writing every time update
to synchronized state.

### Queue policy

Opening a work constructs a queue according to an explicit policy:

- one item;
- ordered album, audiobook, season, series, folder, or playlist;
- search/browse result snapshot;
- user-edited queue.

Autoplay moves to the next valid member and skips unavailable items under a
visible policy. Audiobook and long-form resume is chapter-aware. `autoplay` and
`auto fullscreen` are profile/endpoint preferences with product defaults, not
hard-coded widget behavior.

### Remote control and handoff

A controller selects a `PlaybackTarget`, inspects now playing and queue state,
and acquires or shares a policy-governed control lease. It can play, pause,
seek, change volume/tracks, edit the queue, stop, or hand off to another target.
The data route remains source node to target endpoint even when the command
originates from a phone.

After a node or channel interruption, the endpoint retains its local checkpoint
and bounded queue snapshot. Reconnection reconciles session generation,
source availability, and route authority before resuming. Recovery never seeks
or starts playback twice merely because a command acknowledgement was lost.

Native guaranteed background playback is deferred to Android Media3
`MediaSessionService` and Apple background `AVAudioSession` integration. The
browser target supports only behavior that the active browser process can
guarantee.

## Personalization, Household, And Access

State keys explicitly distinguish:

- household and library;
- actor and active profile;
- controller device/browser;
- playback target endpoint;
- webspace and current presentation;
- playback session and shared-room context.

Favorites, history, resume checkpoints, ratings, hidden items, language and
track preferences, and recommendations are personal by default. Library roots,
detected works, provider metadata, and operator job state are household or
library state. Playlists and queues declare `personal`, `household`, or named
shared ownership.

Favorites, recent, continue-watching, queue, and now-playing surfaces use
subscription-backed projections. A mutation acknowledges only after the
authoritative state revision changes; another browser in the same authorized
scope observes the update without a catalog reload.

Access policy covers root discovery, item visibility, playback, remote control,
playlist editing, history visibility, metadata correction, agent deployment,
and diagnostics. Shared TVs require explicit profile switching and privacy-safe
home rows. Parental policy is enforced in query and playback planning, not only
by hiding cards.

## Voice Interface

Voice is an interaction channel over the same search, collection, queue,
target, and playback contracts. It is not a second media backend.

Representative intents include:

- find or browse by title, person, folder, kind, collection, or recency;
- play on a named endpoint;
- pause, resume, seek, next, previous, stop, and change volume;
- play a season, album, audiobook, playlist, or folder from a position;
- add/remove favorite or queue item;
- answer availability and scan-status questions.

Resolution uses active profile, room, current playback target, focused result,
and dialog context. Ambiguous work, variant, profile, or endpoint identity
requires clarification. Spoken results are bounded and can project a visual
result set to the current webspace. Voice authorization is identical to direct
UI authorization, and destructive deployment or library actions require a
stronger confirmation policy.

## Presentation Architecture

One product does not mean one layout. The scenario emits common semantic data
and actions while the client selects an explicit presentation profile.

### TV profile

- content-first 10-foot layout with overscan-safe spacing;
- collapsed navigation rail or overlay for Home, Movies, Series, Music,
  Audiobooks, Playlists, and Folders;
- vertically stacked horizontal rails for Continue, Favorites, Recently Added,
  and typed collections;
- grid screens for complete result sets;
- large, stable focus states and deterministic directional navigation;
- full-screen player with transient overlay controls;
- no dense node tables, root editors, or long diagnostic forms in normal TV
  navigation.

TV is selected from endpoint/input semantics, not simply a wide viewport. The
design follows the content-first, 10-foot and D-pad principles in the
[Android TV guidance](https://developer.android.com/design/ui/tv/guides/foundations/design-for-tv),
its [Browse and Grid templates](https://developer.android.com/design/ui/tv/guides/styles/layouts),
and the separation of focus from activation described by
[Apple focus guidance](https://developer.apple.com/design/human-interface-guidelines/focus-and-selection/).

### Desktop/library profile

- compact top bar with search, active profile, playback target, now playing,
  and Settings;
- stable side navigation or tabs using the same destinations as TV;
- list/grid switch, typed filters, sorting, cursor pagination, and multi-facet
  inspection;
- at most 30 immediately rendered result rows per page by default;
- details and player controllers in modals or drawers;
- persistent bottom mini-player while playback is active;
- folder navigation using tree-plus-contents or drill-down according to width.

### Mobile-control profile

- Now Playing and target selection first;
- transport, seek, volume, tracks, queue, and handoff within one reachability
  surface;
- Browse, Search, and Queue as secondary tabs or sheets;
- compact media cards and drill-down folder navigation;
- no administration tables in the primary remote flow.

### Now Playing and media details

The primary modal is content-first: artwork/video, title and playback state are
followed by one stable transport row. Favorite, minimize-to-mini-player,
fullscreen and capability-gated Picture-in-Picture are peer actions in a
single secondary toolbar, not vertically stacked forms. Auxiliary technical
metadata and provenance appear below the content or in a details disclosure.
Source/rendition, quality, audio, subtitle and compatibility controls are shown
only when alternatives or an actionable compatibility decision exist. A
background remux/pre-transcode action reports a durable job and keeps browsing
usable; it never implies that playback already started.

### Generic client capabilities

The UI remains UI-as-data. The client should gain generic semantic primitives,
not a hard-coded Media Center page tree:

- cursor-backed collection data source;
- `list`, `grid`, `rail`, and bounded `carousel` projections;
- virtualized rendering and skeleton/degraded states;
- focus groups, directional navigation, focus restoration, and Back semantics;
- typed detail/modal navigation and stable deep links;
- persistent player/now-playing host capability;
- queue editor, output selector, filter controls, and job/status projection;
- responsive density inside each semantic profile.

A file chooser used as a short playlist requests at most 10 records. It cannot
be the primary catalog transport. Card dimensions and focused scale reserve
space so focus cannot overlap adjacent content.

## Settings Information Architecture

Settings are presented in a modal or dedicated operator route, grouped as:

1. **General:** default view, autoplay, auto fullscreen, profile and locale.
2. **Libraries:** roots, aliases, included media kinds, exclusions, schedules,
   rescan and folder navigation policy.
3. **Nodes and agents:** compatible nodes, desired placement, install/update,
   cordon/drain/remove, versions, health, shard revision, capacity and budgets.
4. **Playback:** default target, preferred quality, audio/subtitle language,
   resume, background and interruption policy.
5. **Metadata:** providers, credentials, privacy, claim priority, schedules,
   progress and failed jobs.
6. **Profiles and access:** household profiles, library grants, history privacy,
   parental policy and remote-control permissions.
7. **Storage and performance:** derived-artifact quotas, cache, scan windows,
   CPU/I/O/network concurrency and playback priority.
8. **Diagnostics:** deployment generation, catalog/shard revisions, route and
   session state, bounded errors and exportable evidence.

Primary browse surfaces expose only Search, kind/facet controls, result
presentation, profile/target context, now playing, and Settings.

## Reliability And Observability

The product exposes desired and observed state separately for deployments,
roots, shards, jobs, catalog revision, providers, playback sessions, routes,
and endpoints.

Required operational properties include:

- resource budgets and admission for scan, hash, enrichment and rendition;
- bounded process/thread pools and cancellation disposition;
- backpressure and coalescing before synchronized projections;
- incremental checkpoints and restart-safe job generations;
- no renderer polling loop that can consume a CPU core while data is absent;
- explicit `fresh`, `aging`, `partial`, `unavailable`, and `reconciling` states;
- version-skew compatibility during staged agent rollout;
- metrics for query latency, scan throughput, route setup, first frame, rebuffer,
  rendition work, queue transitions, and projection lag;
- structured machine errors plus skill-owned localized human messages;
- sanitized logs that do not expose source paths, tokens, or personal history.

The catalog may show known offline works with availability state. Search and
folder responses identify partial participation instead of silently pretending
that an unavailable shard returned no matches.

## Security And Privacy

- Remote component installation and removal require trusted node identity,
  capability admission, explicit operator authority, audit, and reviewed plan.
- Agents expose only configured roots and opaque resource/folder identities.
- Route grants are short-lived, target-bound, and source-revision-bound.
- Node-local paths and provider credentials are not replicated into ordinary
  catalog or browser state.
- Search, suggestions, home rows, voice answers, and playback planning apply
  profile/library policy before projection.
- Derived artifacts inherit source access and retention policy.
- External metadata providers are opt-in and expose the exact data categories
  sent outside the home.
- Shared-screen history and recommendations default to privacy-safe behavior
  until a profile is selected.

## Compatibility And Evolution

`media_indexer` remains a compatibility producer and source of implementation
ideas. Its metadata can enter as provider claims and its playback records can
resolve through core media resources. Its FAISS files, ad hoc JSON, SQLite
schema, and path assumptions are not target contracts.

The existing single-node Media Center remains a supported deployment shape:
coordinator and one library agent may be colocated. Migration to distributed
roles must preserve registered source identity, favorites, history, and deep
links or record explicit aliases/redirects.

The architecture intentionally leaves these later extensions possible without
changing the main boundaries:

- automatic coordinator standby and lease-based failover;
- capability-based agent placement and shard relocation;
- trusted federation across several home subnets;
- native Android/iOS background playback;
- hardware-accelerated transcoding and adaptive bitrate packaging;
- advanced embeddings, perceptual duplicate review, recommendations, and
  semantic voice retrieval beyond the shared Knowledge Fabric baseline;
- offline controller operations with policy-governed reconciliation.

## Acceptance Shape

Production acceptance requires one recorded end-to-end proof showing:

1. a ProjectRelease deployed to a coordinator, two agents, a TV webspace, and a
   controller webspace through normal core APIs;
2. large roots indexed in place with folder-name search, resumable progress,
   resource budgets, and no media copy into `.adaos`;
3. cursor-backed list, grid, rail, folder, and search behavior under a
   representative large synthetic catalog;
4. profile-scoped favorites, history and resume synchronization across two
   browsers;
5. phone control of TV playback, queue/autonext, modal-to-mini-player behavior,
   interruption recovery, and direct source-node data path;
6. one agent offline, one partial rollout, one interrupted scan, one unsupported
   codec/rendition, and one coordinator restart with truthful degraded state;
7. bounded CPU, memory, disk, network, Yjs/projection pressure, route latency,
   and browser rendering on the supported machine classes;
8. exact revisions, package digests, deployment generations, test results, and
   stand observations linked from the roadmap evidence record.
