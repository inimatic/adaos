# Subnet Knowledge Fabric Roadmap

Status: implementation roadmap for the
[Subnet Knowledge Fabric Target Architecture](subnet-knowledge-fabric.md).

Last reviewed: 2026-08-23.

## Outcome

AdaOS provides one canonical, multilingual and actionable knowledge plane over
heterogeneous subnet resources. Nodes observe and enrich data close to its
source; the Knowledge Service reconciles stable identity and accepted
decisions; artifact stores retain reusable outputs and optional cold versions;
and proven search engines provide bounded hybrid retrieval. UI, text, voice and
automation use the same governed actions.

This roadmap owns cross-domain sequencing. It does not replace the
[Distributed Service and Data Topology Roadmap](distributed-service-and-data-topology-roadmap.md),
[Media Center Roadmap](media-center-roadmap.md),
[Conversation and Channel Architecture](conversation-and-channel-architecture.md),
[Named Entities and Canonical Naming](named-entities.md), or
[Model Runtime Roadmap](model-runtime-roadmap.md). Tasks reference those
owners rather than cloning their implementation details.

## Priority And Maturity

- `[must]`: required for the first production-oriented Knowledge Fabric proof.
- `[should]`: required before broad unattended or high-value household use.
- `[could]`: useful extension that must not delay the proof.
- `[deferred]`: intentionally excluded until its stated condition is met.

Implementation maturity follows:

```text
hypothesis -> specified -> implemented -> integrated
  -> validated-local -> validated-stand -> production-accepted
```

Checked specification work does not imply implemented or stand-validated
behavior.

## Guardrails

1. Do not implement a DFS, POSIX namespace, block replication protocol, vector
   engine, or object store in AdaOS core.
2. Do not treat embeddings, search indexes, node-local databases, physical
   paths, inodes, or provider display names as canonical identity.
3. Do not copy originals into `.adaos` as an indexing side effect.
4. Do not let a node become an unfenced alternate Knowledge authority while
   disconnected.
5. Do not archive source bytes unless an explicit source/profile policy enables
   versioning and names retention, quota and restore behavior.
6. Do not claim recovery for externally overwritten bytes that were never
   captured by AdaOS or a native source version facility.
7. Do not mix skill-specific translations, metadata vocabulary or intent
   phrases into core dictionaries.
8. Do not resolve ambiguous aliases, semantic duplicates or destructive voice
   actions without the required domain decision or clarification.
9. Do not reveal unauthorized resource existence through vectors, aliases,
   counts, suggestions, diagnostics, logs or timing-sensitive detail APIs.
10. Do not put unbounded catalogs, histories, graph neighborhoods, versions or
    high-rate metrics into Yjs.
11. Do not call a retrieval system state of the art without a frozen evaluation
    corpus, named baselines, reproducible versions and measured evidence.

## KF0: Authority And Contract Baseline

- [ ] `[must]` Publish versioned schemas for `StorageDomain`, `Source`,
  `SourceView`, `ResourceOccurrence`, `ContentObject`, `LogicalItem`, `Claim`,
  `Relation`, `RepresentationManifest`, `DerivedArtifact`, `ResourceVersion`
  and `Surface`.
- [ ] `[must]` Specify desired/canonical/observed states, authority epoch,
  source sequence, scan revision, freshness and complete-scan tombstone rules.
- [ ] `[must]` Specify stable canonical refs and merge/split/redirect behavior
  across node replacement, source remount, rename and Project update.
- [ ] `[must]` Publish SDK ownership boundaries for knowledge, sources,
  artifacts, enrichment, retrieval, versions and actions.
- [ ] `[must]` Define capability and privacy scopes for subnet, household,
  profile, webspace, source, skill, operation and public/guest access.
- [ ] `[must]` Record the explicit no-DFS, no-vector-engine and originals-stay-
  owned-by-source decisions in architecture and SDK guidance.
- [ ] `[should]` Add one non-media fixture before freezing v1 so contracts are
  not accidentally shaped only around Media Center.

Exit gate: schema validation proves that the same core envelopes can represent
one Media Center source and one AdaOS Drive source without domain fields in
core.

## KF1: Canonical Ledger And Reconciliation

- [ ] `[must]` Implement the canonical Knowledge Ledger with idempotent append,
  compare-and-switch revision, checkpoints, bounded recovery and audit.
- [ ] `[must]` Implement source-agent registration, fenced observations,
  durable outbox replay, complete/partial scan epochs and explicit tombstones.
- [ ] `[must]` Materialize a compact catalog with availability, freshness,
  source participation and stable refs.
- [ ] `[must]` Preserve accepted decisions independently from rebuildable
  search/vector projections and node-local indexes.
- [ ] `[must]` Provide cursor-backed query/detail APIs and bounded demanded
  subscriptions through public SDK contracts.
- [ ] `[must]` Expose lag, stale sources, rejected epochs, replay, reconciliation
  failures, pressure and checkpoint health.
- [ ] `[should]` Add read replicas or warm standby only after single-authority
  recovery and fencing pass failure injection.

Exit gate: restart, duplicate replay, stale authority, partial scan and source
offline tests produce no false deletion or duplicate canonical mutation.

## KF2: Naming, Aliasing, And Internationalization

- [ ] `[must]` Extend the existing named-entity contract to resource, logical
  item, collection, source and surface refs without creating a second alias
  authority.
- [ ] `[must]` Add typed `display`, `speech`, `search`, `provider`, `location`,
  `redirect` and `compatibility` aliases with locale, scope, provenance,
  validity, status and conflict evidence.
- [ ] `[must]` Reuse governed alias proposal/apply, optimistic concurrency,
  lifecycle events, deprecation and ambiguity behavior.
- [ ] `[must]` Carry BCP-47 locale/language through claims, labels, aliases,
  transcripts, queries, result explanations and dialog turns.
- [ ] `[must]` Represent translation as a provenance-bearing claim linked to
  its source value; never overwrite original text silently.
- [ ] `[must]` Keep skill/domain dictionaries and intent phrases in their
  packages while core owns only system vocabulary and fallback contracts.
- [ ] `[must]` Add locale negotiation from request, profile and household/subnet
  policy, including explicit fallback reporting.
- [ ] `[should]` Add locale-aware analyzers, transliteration candidates and
  cross-lingual retrieval with explanation.
- [ ] `[should]` Add profile-owned aliases and vocabulary without leaking them
  into household or other-profile search.

Exit gate: English, Russian, mixed-script and transliterated queries resolve
canonical refs consistently; same-scope ambiguity opens a clarification path.

## KF3: Optional Versioning And Archive

- [ ] `[must]` Publish `off`, `metadata_only`, `archive_previous`, `native` and
  retention-policy version modes.
- [ ] `[must]` Define `ResourceVersion`, version lineage, recoverability,
  archive health, retention, quota, hold and garbage-collection semantics.
- [ ] `[must]` Add a provider capability contract for native object versions,
  snapshots and restore without normalizing provider implementation details
  into core.
- [ ] `[must]` Add a content-addressed archive adapter for sources without
  suitable native versioning; use the shared ArtifactStore port.
- [ ] `[must]` Capture pre-images before governed overwrite/delete when policy
  requires it, and report best-effort-only recovery for external mutations.
- [ ] `[must]` Keep hot projections bounded to head plus summary; page full
  history from archive manifests.
- [ ] `[must]` Implement reviewed restore plan/apply/verify that creates a new
  head and preserves intervening history.
- [ ] `[should]` Add archive tiering, scheduled compaction and policy simulation
  before applying retention changes.
- [ ] `[could]` Export a portable signed version/knowledge manifest for moving a
  source to a new subnet.

Exit gate: a governed update archives and restores one source version, native
and CAS-backed adapters pass the same contract, and a large synthetic history
does not grow the hot browser or Yjs projection.

## KF4: Artifact And Enrichment Plane

- [ ] `[must]` Publish the `EnrichmentKey`, artifact manifest, lineage,
  producer/model version, locale, quality, policy and invalidation schemas.
- [ ] `[must]` Implement ArtifactStore adapters for bounded local storage and
  one S3-compatible backend without exposing backend ids to skills.
- [ ] `[must]` Implement source-local-first capability matching, leases,
  idempotency, cancellation, retry, pressure budgets and durable progress.
- [ ] `[must]` Reuse valid artifacts across rename, remount and authorized
  rediscovery through exact input and operation identity.
- [ ] `[must]` Inherit source ACL, retention and privacy constraints for derived
  artifacts and deny digest enumeration.
- [ ] `[must]` Separate immutable artifacts from ephemeral caches and record all
  available locations and verification state.
- [ ] `[should]` Add portable XMP/NFO/domain sidecar import/export adapters as
  optional projections, not the canonical store.
- [ ] `[should]` Add source-local CPU/GPU/model capability budgets and playback
  priority preemption.

Exit gate: two nodes request the same derivation concurrently, one execution
wins, both resolve the verified result, and no original is copied or modified.

## KF5: Hybrid And Latent Retrieval

- [ ] `[must]` Define a backend-neutral retrieval request/result/explanation
  contract with profile, ACL, locale, freshness, availability and paging.
- [ ] `[must]` Integrate a proven structured/full-text engine and establish
  exact, prefix, folder/context and metadata retrieval baselines.
- [ ] `[must]` Integrate a proven vector engine through a replaceable adapter;
  vectors remain model-versioned projections.
- [ ] `[must]` Implement bounded lexical/sparse/dense fusion and reranking with
  deterministic fallback when a representation or shard is unavailable.
- [ ] `[must]` Implement exact digest deduplication separately from perceptual
  and semantic candidate generation.
- [ ] `[must]` Apply authorization before candidate disclosure and verify no
  cross-profile/source leakage through suggestions or explanations.
- [ ] `[must]` Publish indexing coverage, model/version skew, queue depth,
  freshness, query latency and partial-result evidence.
- [ ] `[should]` Add hierarchical document/chapter/scene/track representations
  and multi-stage retrieval.
- [ ] `[should]` Add cross-modal text/image/audio/video retrieval where local
  models and representative evaluation justify it.
- [ ] `[should]` Add graph expansion and user-approved relevance feedback.

Exit gate: a frozen multilingual, multi-source corpus records lexical, hybrid
and reranked relevance, duplicate precision/recall, p50/p95 latency, resource
cost, partial-shard behavior and named baseline comparison.

## KF6: User, Skill, And Operator Management

- [ ] `[must]` Add a domain-neutral management projection and UI-as-data
  components for `Nodes | Sources | Services | Knowledge | Jobs | Operations`.
- [ ] `[must]` Add compact resource location, freshness, representation,
  version and archive summaries with cursor-backed detail.
- [ ] `[must]` Let Project packages declare source agents, enrichers, models,
  indexes and operator presentation descriptors for normal core deployment.
- [ ] `[must]` Route install, remove, move, drain, archive, restore and rebuild
  through immutable plan/review/apply operations with impact wording.
- [ ] `[must]` Support personal, household, kids/restricted and guest scopes in
  search, history, aliases, versions and actions.
- [ ] `[must]` Keep domain labels, errors, settings and help resources skill-
  owned and localized; the client only renders generic components.
- [ ] `[must]` Distinguish not-loaded, empty, partial, stale, unavailable and
  failed states in every product projection.
- [ ] `[should]` Add operator conflict, merge/split, archive simulation and
  relevance-review workbenches.

Exit gate: Media Center and Drive render different domain vocabulary over the
same generic topology/knowledge components with no skill-id branch in client.

## KF7: Dialog, Voice, And Actionable Results

- [ ] `[must]` Define skill-declared intent, entity, action, risk,
  confirmation, presentation and localized response contracts over canonical
  refs.
- [ ] `[must]` Reuse conversation identity, Pending Actions, delivery attempts
  and operation journals rather than creating a Knowledge-specific chat store.
- [ ] `[must]` Preserve profile, locale, selected resources, collection and
  target surface across follow-up turns.
- [ ] `[must]` Render bounded ambiguity choices through both dialog and
  UI-as-data, and resume the same pending interaction after a response.
- [ ] `[must]` Prove one canonical action identity invoked by UI, text and voice
  with identical authorization and side effects.
- [ ] `[must]` Require stronger confirmation for destructive, privacy-sensitive
  and topology-changing voice actions; voice identity alone is insufficient.
- [ ] `[should]` Add non-blocking multilingual ASR/TTS negotiation, no-input,
  interruption, barge-in and synthesis fallback states through the production
  voice roadmap.
- [ ] `[should]` Add cross-surface commands such as open here, continue on an
  authorized display, and control the current playback session.

Exit gate: a multilingual multi-turn interaction searches two skills,
clarifies one alias ambiguity, selects a surface, performs one safe action and
records one end-to-end trace independent of browser or voice transport.

## KF8: Reference Product Adoption

- [ ] `[must]` Migrate Media Center source/catalog identity, artwork, metadata,
  progress and search to Knowledge/Artifact contracts without moving media
  domain entities into core.
- [ ] `[must]` Add Media Center hierarchical collections, exact/perceptual/
  semantic duplicate distinctions, reusable enrichment and semantic retrieval.
- [ ] `[must]` Add Media Center domain management, remote widget, surface
  routing, profile synchronization and multilingual dialog/voice actions.
- [ ] `[must]` Adapt AdaOS Drive from local path identity to Source/SourceView,
  occurrence/content identity and canonical refs.
- [ ] `[must]` Add Drive previews, OCR/full text, aliases, optional versions,
  location/sync presentation and reviewed copy/move/pin/share/restore actions.
- [ ] `[must]` Prove that exact duplicate bytes in two Drive occurrences remain
  separately authorized while sharing eligible derived artifacts.
- [ ] `[should]` Add a third non-media/non-file fixture to validate that the
  Knowledge contracts are not a disguised media catalog or filesystem model.

Exit gate: one exact ProjectRelease deploys the required roles to a trusted
multi-node stand and records Media Center plus Drive end-to-end evidence.

## KF9: Production Hardening

- [ ] `[must]` Run failure injection for authority loss, ledger restart, stale
  epoch, outbox replay, source disappearance, artifact loss, index rebuild,
  archive failure and interrupted restore.
- [ ] `[must]` Record CPU, memory, disk, network, background I/O, ledger growth,
  artifact reuse, index size, query latency and browser rendering budgets.
- [ ] `[must]` Run ACL and privacy tests covering vectors, aliases, translations,
  counts, stale records, logs, dialogs and cross-profile caches.
- [ ] `[must]` Prove upgrade/rollback compatibility for ledger schemas, claim
  schemas, model/index versions, artifact manifests and skill adapters.
- [ ] `[must]` Run long-lived TV, desktop and phone-control acceptance while
  indexing, enriching, archiving and updating selected nodes.
- [ ] `[must]` Publish exact stand evidence with release digests, topology
  generations, source revisions, model/index versions and residual risks.
- [ ] `[should]` Add a read replica or warm standby and prove bounded recovery
  without split authority.

Exit gate: every `[must]` item is integrated and validated on representative
hardware; local unit or fixture coverage alone is insufficient.

## Deferred

- `[deferred]` AdaOS-owned DFS, POSIX namespace, block placement or transparent
  replication of arbitrary original files; revisit only for an explicitly
  approved managed-storage product.
- `[deferred]` Active-active multi-writer Knowledge Ledger or filesystem
  namespace; revisit after single-authority recovery and conflict semantics are
  proven insufficient.
- `[deferred]` Cross-subnet federation and consensus; first prove one-subnet
  identity, policy, privacy and operational recovery.
- `[deferred]` A universal embedding or irreversible conversion of originals to
  latent form; representations remain plural and replaceable.
- `[deferred]` A custom vector database, search engine, object store or snapshot
  implementation.
- `[deferred]` Native mobile background execution guarantees; keep the current
  browser boundary and adopt platform-native services through a separate
  approved roadmap.
