# Subnet Knowledge Fabric Target Architecture

Status: target cross-domain architecture.

Last reviewed: 2026-08-23.

This document owns the cross-domain resource identity, knowledge, derived
artifact, enrichment, retrieval, optional versioning, localization, aliasing,
and user/skill interaction boundary for one AdaOS subnet. Delivery order and
evidence gates are owned by the
[Subnet Knowledge Fabric Roadmap](subnet-knowledge-fabric-roadmap.md).

Generic service placement, authority, partitions, replicas, freshness, and
topology operations remain owned by
[Distributed Service and Data Topology](distributed-service-and-data-topology.md).
Media semantics remain owned by the
[Distributed Media Center Target Architecture](media-center-target-architecture.md).
Canonical naming rules remain owned by
[Named Entities and Canonical Naming](named-entities.md), and transport-neutral
dialog remains owned by
[Conversation and Channel Architecture](conversation-and-channel-architecture.md).

## Decision Summary

AdaOS provides a **Subnet Knowledge Fabric** over heterogeneous, independently
owned sources. Original bytes stay in filesystems, NAS exports, AdaOS Drive,
object stores, external services, or other source systems. Source agents
observe those systems, execute bounded work close to the data, and reconcile
their observations with one canonical subnet Knowledge Ledger.

The Knowledge Fabric owns stable resource identity, accepted claims,
relationships, aliases, policies, desired work, and artifact manifests. It does
not own every source byte. Search, graph, vector, recommendation, and UI models
are replaceable projections over the ledger and artifact manifests.

Every supported resource may have several complementary representations:
exact fingerprints, structured metadata, lexical terms, sparse and dense
vectors, modality-specific fingerprints, hierarchical segments, and graph
relations. No single embedding is canonical identity or durable truth.

The Fabric is an actionable knowledge and retrieval control plane. A result can
be opened, played, copied, shared, restored, enriched, or routed to an
authorized surface through a skill-owned action. It is not merely a search
index.

## Goals

The target system must:

1. provide one stable, searchable identity plane across resources visible to a
   trusted subnet without copying originals into AdaOS-managed storage;
2. avoid repeated expensive enrichment when the same content is rediscovered,
   remounted, renamed, or observed by another authorized node;
3. support exact, structured, lexical, semantic, multimodal, graph, and hybrid
   retrieval with truthful freshness and availability;
4. retain provenance, model version, locale, confidence, access policy, and
   source revision for every reusable claim or derived artifact;
5. allow optional, bounded resource version recovery without keeping complete
   history in the hot operational projection;
6. preserve locale-neutral canonical identity while supporting localized
   labels, metadata, aliases, search, dialog, and voice;
7. expose safe SDK contracts so skills can contribute sources, schemas,
   enrichers, actions, presentation descriptors, and conversational intents;
8. support personal, household, guest, and policy-restricted scopes;
9. continue bounded local reads and work through temporary Knowledge Service
   or network unavailability;
10. reuse proven storage, search, vector, snapshot, and object-store engines
    instead of implementing a new distributed filesystem or vector engine.

## Non-Goals

The Knowledge Fabric does not require:

- a POSIX or HDFS-compatible distributed filesystem namespace;
- transparent block placement or replication of every original file;
- one universal embedding model or one permanent latent space;
- embeddings as proof of equality, ownership, or authorization;
- active-active multi-writer filesystem semantics;
- rewriting source files to inject AdaOS identity or metadata;
- moving domain schemas, ranking policy, or user-facing skill vocabulary into
  core;
- loading an unbounded knowledge graph, version history, or vector set into
  Yjs or a browser document;
- a cross-subnet consensus system in the first production slice.

If AdaOS later owns a managed file store, that store must implement or adopt a
separate storage contract. It must not silently turn the Knowledge Fabric into
a filesystem data plane.

## Architectural Invariants

1. **Canonical knowledge and physical reality are distinct.** The Knowledge
   Ledger is authoritative for identity, accepted knowledge, policy, and
   decisions. A source agent is authoritative only for a fenced, time-bounded
   observation of physical state.
2. **Original storage remains authoritative for source bytes.** A catalog entry
   cannot prove that bytes are currently readable; availability always carries
   observation freshness.
3. **Representations are plural and versioned.** Structured metadata, text,
   embeddings, fingerprints, and graph edges may coexist and evolve.
4. **Search indexes are projections.** Full-text, vector, graph-optimized,
   recommendation, and UI indexes can be rebuilt without repeating valid
   source extraction or enrichment.
5. **Artifacts are content addressed.** Artifact manifests bind exact inputs,
   operation specification, producer/model versions, outputs, provenance,
   policy, and storage locations.
6. **Aliases never replace identity.** Canonical refs are locale-neutral and
   stable. Aliases are scoped, typed, versioned, conflict-checked records.
7. **Language is explicit.** Linguistic claims, labels, aliases, transcripts,
   and queries carry BCP-47 locale/language evidence. Translation is a claim,
   not an overwrite of the original value.
8. **Versioning is policy controlled.** No source silently becomes versioned.
   Hot projections expose the current head and bounded summaries; historical
   bytes and manifests live in an archive backend.
9. **Authorization precedes retrieval and action.** A vector or graph index
   must not reveal the existence, semantics, or location of unauthorized
   resources.
10. **All unbounded work is asynchronous.** Scan, hashing, extraction,
    transcription, embedding, indexing, archive, restore, and migration return
    durable operation identities and publish bounded progress.
11. **Skills own domain meaning.** Core can store a versioned claim envelope;
    it does not decide what constitutes an album, legal document, duplicate
    film, or acceptable translation.
12. **UI and dialog share actions.** A button, text command, voice request, or
    automation resolves to the same canonical action, policy, confirmation,
    and operation journal.

## Plane Model

| Plane | Responsibility |
| --- | --- |
| Source | Original resources, provider-native identity/version evidence, and source-specific read/write behavior |
| Observation | Fenced agents, scan epochs, deltas, tombstones, freshness, capability and pressure |
| Canonical knowledge | Stable refs, occurrences, content objects, logical items, claims, relations, aliases, decisions and policies |
| Artifact | Content-addressed manifests and blobs for reusable derived results and optional archived versions |
| Enrichment | Capability-aware job planning, leases, source-local execution, provenance, budgets and retry |
| Retrieval | Structured filters, lexical/sparse/dense retrieval, graph expansion, fusion, reranking and authorization |
| Action | Governed open/play/copy/share/version/restore/enrich/deploy operations and surface routing |
| Experience | UI-as-data, system management, domain views, widgets, dialog, voice and accessibility |

## Canonical Resource Model

| Entity | Meaning |
| --- | --- |
| `StorageDomain` | Stable identity for one storage authority or provider boundary, independent of a node mount path |
| `Source` | Registered logical root, bucket, account, collection, API scope, or other observation boundary |
| `SourceView` | Capability-scoped subtree or subset delegated to a node, skill, user, or operation |
| `ResourceOccurrence` | One resource observed at one source-relative location and source revision |
| `ContentObject` | One exact byte/content identity; several occurrences may reference it |
| `LogicalItem` | Domain interpretation such as a film, episode, track, book, document, note, or contact |
| `Collection` | Typed grouping such as folder, album, series, season, playlist, project, or user collection |
| `Claim` | Immutable assertion with subject, schema, value/ref, authority, confidence, locale and provenance |
| `Relation` | Typed edge such as `memberOf`, `sameContent`, `sameWork`, `versionOf`, `derivedFrom`, or `translationOf` |
| `AliasRecord` | Scoped human, provider, compatibility, search, or redirect alias targeting a canonical ref |
| `RepresentationManifest` | Description of one exact, lexical, latent, perceptual, hierarchical, or graph representation |
| `DerivedArtifact` | Manifest-backed output such as thumbnail, preview, waveform, OCR, transcript, embedding, rendition, or exported metadata |
| `ResourceVersion` | Restorable historical source revision or metadata-only revision under an explicit version policy |
| `Surface` | Authorized playback, display, storage, dialog, or control endpoint with declared capabilities |

`ResourceOccurrence`, `ContentObject`, and `LogicalItem` must remain separate.
Two Drive paths containing identical bytes are not automatically one logical
file. Two differently encoded films may have different content digests but one
logical work. A rename changes location evidence without changing content
identity.

## Desired, Canonical, And Observed State

Knowledge records expose three state planes:

```text
desired   - policy or work that should become true
canonical - accepted durable identity, relation, decision, or last-known fact
observed  - time-bounded evidence reported by a source authority
```

An observation includes at least:

- `source_id`, `authority_node_id`, `authority_epoch` and `scan_revision`;
- a monotonic source sequence or idempotency key;
- `observed_at`, freshness deadline, evidence kind and source revision;
- the visible source-relative identity, never a browser-visible raw host path;
- explicit complete-scan or partial-scan state.

Loss of a heartbeat changes availability to `unknown` or `stale`; it does not
delete occurrences. Absence becomes a tombstone only after an authoritative
complete scan or explicit governed source operation.

## Identity And Content Recognition

Identity resolution is progressive:

```text
provider object/version id
-> source occurrence evidence
-> size, timestamps and bounded quick fingerprint
-> strong content digest
-> perceptual or domain fingerprint
-> semantic candidate relation
-> accepted merge/split decision
```

Provider ids, filesystem ids, xattrs, embedded ids, and sidecars are useful
accelerators but are not universally portable. Strong content digests support
exact reuse and deduplication, but they do not capture path context, ACL,
collection membership, or semantic equivalence.

Merges never destroy prior refs. They create governed redirects and an audit
record. Splits retain the decision lineage and rematerialize dependent views.

## Aliasing

Aliasing is a first-class cross-domain capability. Alias kinds are distinct:

- `display`: additional human-facing name;
- `speech`: phrase accepted by dialog and voice resolution;
- `search`: synonym, abbreviation, spelling or transliteration aid;
- `provider`: external catalog or provider identifier;
- `location`: previous or alternate source-relative locator;
- `redirect`: deprecated canonical ref retained after merge or migration;
- `compatibility`: legacy skill, scenario, deep-link, or import identifier.

An alias record carries canonical target, kind, locale, effective scope,
authority, confidence, status, provenance, validity interval, and conflict
state. Effective scopes include global package vocabulary, subnet, household,
profile, webspace, source, and skill domain.

Alias proposal and mutation use the governed proposal/apply contract from
[Named Entities and Canonical Naming](named-entities.md). Ambiguous aliases
produce candidates and a clarification interaction; they never silently select
one resource. Runtime alias changes invalidate resolver and search projections
without retraining NLU models by default.

## Internationalization

Canonical ids, schema ids, digests, operation ids, and provider ids are
locale-neutral. Human language is carried explicitly:

- labels, aliases, claims, transcripts, extracted text, summaries and queries
  use BCP-47 tags or `und` where language is unknown;
- original text and translated text are separate claims connected by
  `translationOf`, with model/provider/user provenance;
- locale preference resolves from request, profile, household/subnet policy,
  skill fallback and finally language-neutral labels;
- retrieval may use locale-specific analyzers, transliteration and cross-
  lingual representations, but must preserve why a result matched;
- speech recognition, synthesis and dialog prompts negotiate locale per turn
  and expose fallback rather than pretending a requested language is present;
- skill-specific labels, domain errors, metadata vocabulary and intent phrases
  ship with the skill; core owns only platform/system vocabulary and fallback
  contracts.

Search evaluation must include Cyrillic, Latin, mixed-script, transliterated,
translated and language-neutral examples. Aliases and translations remain
policy- and profile-scoped so one user's vocabulary does not silently alter a
household result set.

## Optional Versioning And Archive

Versioning is a source policy, not a global default. Initial policy modes are:

- `off`: retain only current knowledge and normal source/provider history;
- `metadata_only`: retain revisioned manifests, claims and relations without
  historical source bytes;
- `archive_previous`: capture the previous content before an AdaOS-governed
  overwrite or delete;
- `native`: reference provider-native versions or filesystem/NAS snapshots;
- `archive_policy`: retain versions by count, age, quota, class and pin/hold.

AdaOS should prefer proven native version facilities such as provider object
versions or filesystem/NAS snapshots. When no suitable facility exists, a
version adapter may place immutable content-addressed blobs in an
`ArtifactRepository` archive class. Core defines the port and lifecycle; it
does not implement a new filesystem.

The hot knowledge projection contains only the current head, version policy,
last restorable revision, bounded version summary and archive health. Full
history is cursor-backed and manifests/blobs stay in cold storage. CAS
deduplicates equal archived bytes and derived artifacts.

A managed overwrite can guarantee pre-image capture within policy. A change
made outside AdaOS can only be archived if the source/provider retained the
old revision; discovering a changed checksum after the old bytes disappeared
cannot recover them. UI and diagnostics must state this distinction.

Restore is a reviewed operation:

```text
select version -> validate availability and policy -> immutable plan
  -> impact/target review -> apply -> verify -> publish new head
```

Restoring normally creates a new head revision and does not erase intervening
history. Archived source versions retain references to their compatible
derived artifacts; obsolete search and vector indexes remain rebuildable.

## Artifact And Enrichment Plane

The target artifact pattern is:

```text
EnrichmentKey -> ArtifactManifest -> content-addressed blobs
```

`EnrichmentKey` binds all relevant input revisions, operation/schema version,
producer or model identity, parameters, locale, and policy. The manifest
records provenance, output digests, locations, retention, access inheritance,
quality evidence and invalidation rules.

Placement order is normally:

```text
valid existing artifact
-> source-local capable agent
-> another authorized capable node
-> explicitly enabled external provider
```

Artifact storage is supplied through adapters for local filesystems,
S3-compatible object storage, read-only portable repositories, or later
approved backends. A `SourceView` carries an authorized artifact-repository
reference, so a skill delegated to a subtree never searches parent directories
for a hidden metadata folder.

Sidecars such as XMP, NFO, artwork, or an AdaOS portable manifest are optional
import/export projections for interoperability and recovery. They are not the
only canonical database.

## Representation And Retrieval

A resource may expose a versioned representation set:

```text
exact digest
structured metadata
lexical fields and chunks
sparse representation
dense cross-modal representation
modality-specific representations
perceptual or acoustic fingerprints
hierarchical segment representations
graph relations
```

Large resources are represented hierarchically. Documents use sections and
chunks; films use scenes, keyframes and transcript segments; audiobooks use
chapters; music uses albums, tracks and optional segments; folders and
collections use bounded aggregate representations.

The retrieval pipeline is:

```text
identity, profile and ACL filter
-> structured and lexical candidate retrieval
-> sparse/dense/multimodal candidate retrieval
-> bounded fusion and reranking
-> graph and collection expansion
-> freshness, availability and route ranking
-> skill-owned action/presentation projection
```

Exact deduplication uses content digests. Perceptual and latent similarity only
produce candidates that domain policy or a reviewed decision may classify as
the same rendition, work, or collection.

AdaOS should use proven full-text, vector and object-store engines behind
ports. Engine-specific ids, query syntax and index formats do not enter skill
contracts. A defensible state-of-the-art claim requires frozen multilingual
and multimodal evaluation sets, named baselines, relevance and duplicate
metrics, latency/resource budgets, ACL non-disclosure tests, and reproducible
model/index versions.

## Distribution And Resilience

The canonical Knowledge Service is a logical singleton with a fenced authority
epoch in the first release. Its durable ledger and checkpoints may be placed on
one selected node and later gain read replicas or a lease-based standby through
the generic distributed topology plane.

Agents maintain durable outboxes, bounded local read projections and
capability-scoped artifact caches. During a control-plane interruption they may
continue already authorized reads, playback, and safe queued work. Global
mutations either remain pending or fail explicitly; they do not create an
untracked alternate authority.

High-cardinality catalogs, graph neighborhoods, version lists, job histories,
and search results are cursor-backed. Yjs carries compact demanded summaries
and stable refs. High-rate progress and resource metrics use bounded streams or
observability channels.

## User, Skill, And Operator Management

The system management surface is domain-neutral:

```text
Nodes | Sources | Services | Knowledge | Jobs | Operations
```

It exposes component placement, source grants, capabilities, pressure,
enrichment queues, index health, archive policy, versions, aliases, conflicts,
routes, retention, and reviewed operations according to authorization.

Skills contribute a presentation descriptor that selects canonical scopes,
domain vocabulary, localized messages, supported actions, impact wording,
default views and redaction policy. Media Center can present "Nodes and
storage" while Drive presents "Locations and sync" over the same generic
contracts.

Management uses progressive disclosure:

- ordinary users see availability, location, version and sync summaries;
- owners manage sources, aliases, retention, archive and enrichment policy;
- operators inspect instances, epochs, revisions, pressure, plans and repair
  evidence.

No client branch is keyed to a skill id. The client renders generic UI-as-data
components and capability-gated actions.

## Dialog And Voice

Text, voice, buttons, widgets and automation use one semantic action path:

```text
input -> locale/profile context -> intent and entity resolution
  -> authorized hybrid retrieval -> disambiguation
  -> action plan -> confirmation when required
  -> skill execution -> operation/result projection -> UI or TTS feedback
```

Skills declare intents, entity kinds, action schemas, risk, confirmation,
presentation hints and localized prompt/error resources through SDK contracts.
Core owns conversation identity, channel negotiation, action dispatch,
authorization, durable pending interactions and delivery semantics.

The entity resolver uses canonical refs plus scoped aliases. Follow-up turns
retain selected resources, collection, profile and surface context. Ambiguous
results produce a bounded clarification and may project visual choices to the
current webspace. Voice authorization is identical to UI authorization; voice
identity alone is insufficient for destructive or privacy-sensitive actions.

The Surface Registry allows requests such as opening a document locally,
continuing playback on a named TV, or controlling another authorized endpoint.
The result is actionable because retrieval returns canonical refs, current
availability and route candidates rather than raw search-engine rows.

## Core And Skill Boundary

| Core/SDK owns | Skills and Projects own |
| --- | --- |
| canonical envelopes, ids, scopes and revisions | domain entity and collection semantics |
| observation, ledger, provenance and artifact ports | source adapters beyond generic filesystem/object ports |
| generic alias, locale, version and archive contracts | domain aliases, translations, retention defaults and merge rules |
| job leases, progress, pressure and operation journals | extraction, enrichment and quality policy |
| generic retrieval query, result and explanation shapes | schema fields, ranking features, result cards and actions |
| authorization, profiles, surfaces and confirmation | domain grants, parental/content policy and action impact wording |
| UI-as-data primitives and dialog/action transport | skill UI descriptors, intents, prompts and i18n dictionaries |

## Representative Product Mapping

Media Center contributes media roots, works, variants, series/albums/playlists,
technical probes, artwork, transcripts, fingerprints, playback plans and
surface-control actions. It uses latent retrieval for semantic discovery and
candidate duplicate/work matching, but keeps media decisions domain-owned.

AdaOS Drive contributes filesystem and provider sources, file/folder/project
semantics, previews, OCR, full text, versions, pin/copy/move/share actions and
location/sync views. A distributed Drive may use native provider versions or an
archive backend without changing Knowledge Fabric contracts.

Future skills can contribute mail, notes, research, home automation, contacts,
or other resources without adding their domain entities to core.

## Acceptance Shape

The architecture is production-proven when one recorded trusted-subnet run
shows:

1. two heterogeneous source agents reconcile stable occurrences through node
   restart, remount, rename and one complete-scan tombstone;
2. rediscovered exact content reuses valid artifacts without repeating
   extraction or embedding;
3. authorized multilingual lexical and semantic queries return explained,
   bounded results across at least two skills and two nodes;
4. exact, perceptual and semantic duplicate paths remain distinct and meet
   declared precision/recall gates;
5. one optional version policy archives and restores a managed pre-image while
   hot projections remain bounded;
6. aliases resolve by locale and scope, conflicts trigger clarification, and a
   canonical merge preserves redirects and user references;
7. UI, text dialog and voice invoke the same authorized action and operation
   identity on a selected surface;
8. an agent and the Knowledge Service each experience an interruption without
   false deletion, unauthorized disclosure, duplicate mutation, or loss of a
   durable accepted decision;
9. CPU, memory, disk, index size, background I/O, query latency and browser
   rendering remain inside declared budgets on representative home hardware.
