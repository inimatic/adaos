# Semantic Registry Distribution Gap Audit

Date: 2026-09-29  
Scope: the shared CBS projection in the existing AdaOS source/package registry.  
Decision: keep one registry and one package store; complete install-time
distribution contracts instead of creating a semantic package manager.

## Executive Result

The semantic registry is already a working **publication and replication
index**. It is not yet a complete **distribution system**.

Update, 2026-09-29: `CBS10-08` closed the discovery-contract gap and
`CBS10-09` closed the cold-cache thin-resolution gap. The v1 snapshot-pinned
query/result ABI is implemented as a read-only projection, and the thin path
now verifies the exact semantic Application release, provenance and package
closure before producing immutable `ApplicationResolution` records. Sealed
offline bundles and clean-subnet install/activation proofs remain open.

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
  exact delivery selection, stable runtime selection, and auto-update;
- a cold local cache can now acquire only the selected portable records and the
  exact existing ProjectRelease package closure, admit provenance/policy/
  evidence, and emit an immutable resolution without local authority.

The remaining missing boundary is portable offline installation and full
clean-subnet install/activation proof. Online thin acquisition is executable,
but it intentionally stops at `ApplicationResolution`; local provisioning,
reviewed planning and activation stay on the established installation rails.
The published `distribution.resolved` object still describes intent rather
than a sealed, independently admitted offline bundle.

## Authority Findings

| Concern | Current authority | Finding |
| --- | --- | --- |
| Portable semantic identity | `semantic/index.json` plus immutable records | Implemented and fail-closed by digest and identity/revision. |
| Application publication | existing governed Application/source publish commit | Implemented; no second publisher path is needed. |
| Package bytes | existing content-addressed package store | Correct authority; semantic distribution must reference it, never copy its ownership model. |
| Local resolution | `PortableContractCatalog` and CBS resolver/admission | Implemented after records have been imported. |
| Online discovery | snapshot-pinned v1 query/result ABI over one verified local registry snapshot | Contract implemented; remote/selective acquisition from a cold cache remains part of thin installation. |
| Thin installation | snapshot-pinned query plus `ThinSemanticDistributionResolver` over existing registry/package/provenance services | Resolution boundary implemented; clean-subnet install, provisioning and activation E2E remains in `CBS10-11`. |
| Offline installation | exact package/artifact lists in Application projection | Gap: no sealed bundle manifest, carried byte closure, or offline admission receipt. |
| Explanation | immutable v1 query result plus local resolver facts | Implemented for portable discovery with typed eligible/rejected candidates; activation explanations remain local. |
| Revocation/federation | existing release/channel policy within one registry | Sufficient for the current single-registry proof; cross-registry conflicts and global revocation remain deferred. |

## Contract Gaps

### 1. Registry query protocol — closed by CBS10-08

The shared index now has fail-closed request/result schemas and a read-only
execution path for:

- capability ref plus compatible contract range;
- optional StateContract requirements;
- target environment/profile and policy inputs;
- eligible BindingDefinitions and exact BindingDeliveries;
- required portable evidence and observed freshness dependencies;
- exact registry snapshot/commit and rejection explanations.

The result contains immutable references and typed rejection explanations
only. Tests pin the Git revision and index digest, validate portable record
digests, reject schema drift and tampering, and assert that the query creates
no `BindingInstance`, credential attachment, state mutation, or activation.
This does not yet acquire a missing snapshot from a remote registry.

### 2. Thin distribution — closed by CBS10-09

The runtime path now takes an exact thin Application requirement set from a
cold cache through:

```text
snapshot-pinned semantic query
  -> portable record verification/import
  -> existing exact package resolver/fetch
  -> evidence and policy admission
  -> immutable ApplicationResolution
  -> explicit provisioning obligations
```

The resolver verifies that the query requirement set is exactly the immutable
Application release requirement set; selects only candidates published in the
same release; compares the semantic and ProjectRelease package closures;
requires the existing provenance admission; verifies fetched package bytes in
the existing content-addressed store; and imports only selected portable
records. It returns immutable resolutions but no local materialization. Tests
prove that provenance rejection and package tampering fail before any
resolution or authority is returned. Mutable credentials, local provider
instances, state attachments, plans, locks and activation remain outside this
boundary.

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

1. **Done (`CBS10-08`):** define a snapshot-pinned semantic
   query/result/explanation ABI.
2. **Done (`CBS10-09`):** implement cold-cache thin resolution as a verified
   acquisition step after read-only discovery and before local provisioning.
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
