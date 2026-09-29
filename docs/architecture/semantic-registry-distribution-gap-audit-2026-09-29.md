# Semantic Registry Distribution Gap Audit

Date: 2026-09-29  
Scope: the shared CBS projection in the existing AdaOS source/package registry.  
Decision: keep one registry and one package store; complete install-time
distribution contracts instead of creating a semantic package manager.

## Executive Result

The semantic registry is already a working **publication and replication
index**. It is not yet a complete **distribution system**.

The following production properties are implemented and covered by tests:

- exact admitted Application releases publish content-addressed portable CBS
  records beside their existing source/package release;
- one stable semantic identity and revision cannot silently acquire a second
  digest;
- shared publication excludes credentials, connected accounts,
  `BindingInstance`, `StateSpace`, and local operational evidence;
- registry synchronization verifies canonical digests and hydrates the
  node-local portable catalog without installing the publisher Application;
- a consumer can resolve imported capability contracts, binding definitions,
  and exact binding deliveries through that local catalog;
- public Application catalog import creates installable product and release
  facts without activating them;
- the Gmail clean-subnet proof exercised publication, import, native admission,
  exact delivery selection, stable runtime selection, and auto-update.

The missing boundary is install-time acquisition. Today a subnet normally
replicates the complete Git semantic projection, imports it wholesale, and
then uses local catalog scans. The published `distribution.thin` and
`distribution.resolved` objects describe intent, but neither is yet an
executable, independently validated distribution protocol.

## Authority Findings

| Concern | Current authority | Finding |
| --- | --- | --- |
| Portable semantic identity | `semantic/index.json` plus immutable records | Implemented and fail-closed by digest and identity/revision. |
| Application publication | existing governed Application/source publish commit | Implemented; no second publisher path is needed. |
| Package bytes | existing content-addressed package store | Correct authority; semantic distribution must reference it, never copy its ownership model. |
| Local resolution | `PortableContractCatalog` and CBS resolver/admission | Implemented after records have been imported. |
| Online discovery | none with a versioned request/result contract | Gap: clients must already possess the replicated index. |
| Thin installation | descriptive fields in Application projection | Gap: no snapshot-pinned query/fetch/admit pipeline. |
| Offline installation | exact package/artifact lists in Application projection | Gap: no sealed bundle manifest, carried byte closure, or offline admission receipt. |
| Explanation | local resolver facts and logs | Gap: no portable query result that preserves eligible/rejected candidates and reasons. |
| Revocation/federation | existing release/channel policy within one registry | Sufficient for the current single-registry proof; cross-registry conflicts and global revocation remain deferred. |

## Contract Gaps

### 1. Registry query is not a protocol

The shared index can be read, but there is no fail-closed request/result schema
for:

- capability ref plus compatible contract range;
- optional StateContract requirements;
- target environment/profile and policy inputs;
- eligible BindingDefinitions and exact BindingDeliveries;
- required portable evidence and observed freshness dependencies;
- exact registry snapshot/commit and rejection explanations.

A query result must contain immutable references only. It must not create a
`BindingInstance`, attach credentials, mutate state, or activate a release.

### 2. Thin distribution is not executable

The current projection says that registry access is required, but no runtime
path takes a thin Application requirement set from a cold cache through:

```text
snapshot-pinned semantic query
  -> portable record verification/import
  -> existing exact package resolver/fetch
  -> evidence and policy admission
  -> immutable ApplicationResolution
  -> reviewed ResolutionPlan
```

This path must preserve the established authority order. Semantic discovery
does not select mutable local credentials or bypass the package resolver.

### 3. Resolved distribution is not a portable bundle

The existing `resolved` object lists exact closure members, but it is neither a
sealed manifest nor a transport bundle. A first executable bundle needs:

- ApplicationRelease and semantic requirement-set digests;
- registry snapshot/commit identity;
- exact portable-record digests and payloads;
- exact existing package refs and optionally their verified bytes;
- portable evidence digests;
- bundle digest and producer provenance;
- explicit exclusions for local authority and secrets;
- an admission receipt proving every carried member was verified.

The bundle may carry bytes, but it does not become a second store. Imported
packages enter the existing content-addressed package store.

### 4. Existing schemas are too permissive at the distribution edge

`semantic_registry.application_release.v1` currently types `thin` and
`resolved` as unconstrained objects, and the shared index leaves
`application_releases` structurally open. This was acceptable for the
publication proof, but an installer must not infer required semantics from
those fields. Executable manifests require dedicated fail-closed schemas.

### 5. Full replication and linear local scans are correctness-first only

`import_to_local_catalog()` imports the selected set or the complete record
index. Local matching iterates verified catalog entries. This is deterministic
and adequate for the current corpus, but it is not a scalable online discovery
surface. Selective fetch, reverse indexes, pagination, and bounded caches come
after query correctness and measured cold-start cost.

## Delivery Decision

Complete this boundary in the existing registry and resolver:

1. Define a snapshot-pinned semantic query/result/explanation ABI.
2. Implement cold-cache thin resolution as a read-only acquisition step ahead
   of the existing resolver/admission order.
3. Define and implement one sealed resolved-bundle manifest that imports into
   the existing semantic catalog and package store.
4. Prove both paths on a clean subnet with Gmail provider reuse, while creating
   credentials and connected-account attachment locally.
5. Only then optimize full-index replication and local scans.

Do not introduce a new package kind, package manager, credential transport,
registry-owned `BindingInstance`, or registry-owned `StateSpace`.

## Acceptance Proofs

The distribution boundary is complete when all of the following pass:

- a cold-cache subnet installs a thin Gmail consumer using a named registry
  snapshot and exact package closure;
- the same release installs from a resolved bundle with registry access
  disabled;
- both installations produce equivalent semantic/package selections but
  distinct local binding/account identities;
- neither registry nor bundle contains a client secret, refresh token,
  connected-account ref, local StateSpace, or operational evidence;
- tampered records, packages, bundle members, unknown required fields, stale
  required evidence, and snapshot drift fail closed with typed explanations;
- interruption before activation leaves the prior `WorkspaceLock` unchanged;
- repeated import/install is idempotent and emits a stable receipt.

## Priority

- **Must:** query ABI, thin cold-cache acquisition, resolved bundle ABI and
  admission, exact snapshot/package/evidence explanations, clean-subnet E2E.
- **Should:** selective synchronization, reverse indexes, pagination, bounded
  caches, and retention/yank projection within the existing registry.
- **Could:** precomputed ranking and mirrors after measurements justify them.
- **Deferred:** cross-registry federation, global naming arbitration,
  ecosystem-wide revocation propagation, and global garbage collection.
