# Artifact Pipeline Critical Audit — 2026-07-26

Status: confirmed local findings; remediation is required before the package
update path becomes the default.

This audit reviews the implementation recorded by
[Artifact Pipeline Local Evidence — 2026-07-24](artifact-pipeline-local-evidence-2026-07-24.md)
against the invariants in
[Artifact Source, Package, and Activation Architecture](artifact-source-package-activation.md).
It does not invalidate the bounded proof: immutable packaging, isolated trial
materialization, rollback injection, and live Builder publication did occur.
It narrows what that proof establishes and identifies missing adversarial,
concurrent, and migration coverage.

## Method

The review combined:

- contract and schema inspection;
- state-transition and write-order inspection;
- focused temporary-directory reproductions against the current branch;
- review of backend storage and channel mutation semantics;
- package verification-pass instrumentation;
- comparison of the implementation with roadmap task wording.

The probes did not modify Workspace or DEV content. They created disposable
packages, release repositories, and WorkspaceLock state below temporary
directories.

## Remediation Status

The status below is deliberately narrower than production acceptance. A
corrected finding has a regression at every implemented trust boundary; remote
changes still require merge, deployment, and a stand proof before the package
pipeline becomes the default.

| Finding | Status | Evidence or next gate |
| --- | --- | --- |
| B1 version identity | corrected, validated-local | local release regression and backend artifact smoke reject a second digest for one version |
| B2 fail-open schemas | corrected, validated-local | explicit v1 readers reject unknown schemas, fields, and malformed collection members |
| B3 package scrub/paths | corrected, validated-local | local and backend admission reject secrets and non-portable aliases |
| B4 resolver consistency | corrected, validated-local | bounded fixed-point selection rebuilds all bindings from the final complete constraint set |
| B5 orphan removal | corrected, validated-local | active slot roots define reachability; removed packages are backed up, pruned, and restored on failed health |
| B6 Workspace writers | corrected, validated-local | activation/recovery share a cross-process lease and verify the observed lock digest before mutation |
| B7 channel CAS | corrected, validated-local | local and backend channel mutations use a lease, expected digest, conflict preservation, and idempotent retry |
| B8 promotion continuation | corrected, validated-local | candidate-scoped receipt journal reconciles lost channel responses and resumes projection/subscription without replaying completed mutations |
| B9 runtime/trial evidence | corrected, validated-local | activation requires reload/health receipts or named policy skips; trial slots enforce data identity/isolation and acceptance requires healthy rollback-complete evidence |
| B10 backend release admission | corrected, validated-local | backend recomputes release identity and verifies package references before visibility |
| B11 operator retry identity | corrected, validated-local | Builder binds confirmation and idempotency to the exact reviewed plan digest |
| R1 repeated verification | improved, open | cached activation reduced from four archive traversals to two; carry one verified receipt into extraction |
| R2 base64 transport | open (`should`) | add streaming transport behind the existing adapter |
| R3 materialization identity | improved, validated-local | new packages persist and activation consumes an exact portable target; historical alias migration remains in AP0-07/AP6 cutover |
| R4 filesystem durability | open | add directory sync and terminal history states |
| R5 runtime freshness | improved, validated-local | DEV manifest activation and core-process reload are explicit; stale runtime returns an explicit unavailable result rather than retrying mutation |

## What Remains Sound

The following foundations are worth retaining:

- package bytes and their canonical file manifest are deterministic and
  content-addressed;
- exact Git source materialization resolves a revision to an immutable commit;
- package traversal, symlink, file-count, size, digest, and corruption checks
  fail closed for the cases already covered;
- Forge checkpoint writes use durable intent/receipt state and do not
  automatically repeat an unknown remote mutation;
- Builder gives every Automation iteration a distinct change identity;
- Codex works from a content-addressed snapshot and uses compare-and-switch
  before replacing DEV;
- activation journals all declared phases and the injected failure suite proves
  bounded rollback behavior;
- unknown migration outcomes require explicit one-shot reconciliation.

These mechanisms reduce the remediation scope. The pipeline needs stronger
admission and concurrency contracts, not another path-copy implementation.

## Confirmed Correctness Blockers

### B1. Release version identity is not unique

`ReleaseRepository` and the backend store releases by digest only. Two
different packages with the same `project_id` and semantic version were both
accepted and could both be selected by a channel. This contradicts the rule
that one version identity cannot map to different content.

Required correction:

- maintain an immutable `project_id + version -> release_digest` index;
- accept an idempotent repeat of the same digest;
- reject a different digest with a durable conflict;
- enforce the invariant in both local and backend repositories.

### B2. Contract readers are schema fail-open

Changing a serialized PackageRef schema to an unknown `v999` value was silently
accepted and re-emitted as v1. Several `from_mapping` readers ignore `schema`,
unknown fields, and malformed collection members.

Required correction:

- reject unknown schema identifiers at every trust boundary;
- keep explicit v1 readers and explicit v1-to-v2 adapters;
- never interpret a future schema as the current one;
- require external immutable records to carry and match their digest.

### B3. Package scrub and portable extraction are incomplete

A scenario containing `.env` packaged successfully. Portable path validation
also accepted Windows alternate-data-stream and alias cases such as
`dir/file:stream`, `dir/CON`, and trailing-dot names. Case-fold collisions are
not checked before Windows extraction.

Required correction:

- add a fail-closed publication scrub for credential, private-key, runtime-data,
  and local configuration patterns;
- distinguish an explicit reviewed exception from silent inclusion;
- reject portable path aliases, ADS names, reserved devices, trailing dots or
  spaces, and case-fold collisions;
- apply the same policy to local and backend verification.

### B4. Dependency resolution can emit inconsistent bindings

With two consumers, the first accepting shared skill v2 and the second
narrowing the combined constraint to v1, the resolver selected v1 but retained
one v2 binding. Activation later rejected the internally inconsistent plan.
This is safe from partial mutation but rejects a compatible solution and makes
the update plan unreliable.

Required correction:

- resolve from the complete constraint set rather than retaining bindings from
  an earlier selection;
- rebuild transitive constraints when the selected package changes;
- assert every binding digest equals the final selected package before a
  ReleasePlan becomes visible;
- detect bounded fixed-point oscillation and fail with an explainable plan.

### B5. Removed dependencies remain active

Updating a scenario from a release that required a shared skill to one that did
not left the skill in WorkspaceLock and left its directory materialized.
`_desired_lock` currently starts from every old component and only adds or
replaces packages.

Required correction:

- rebuild the desired component set from all active slot release roots;
- retain a shared dependency while any active consumer requires it;
- transactionally remove packages that become unreachable;
- restore removed packages during rollback;
- record added, changed, retained, and removed components in the update plan.

### B6. Workspace activation has no single-writer serialization

Atomic file replacement does not make a multi-file activation serializable.
Two processes can read the same lock revision and race through staging and
switching, causing a lost lock update or conflicting filesystem moves.

Required correction:

- acquire one cross-process writer lease per Workspace for plan refresh and
  activation;
- compare the expected lock digest immediately before switch;
- reject stale plans rather than overwriting a newer lock;
- keep package download and immutable verification outside the shortest
  critical section where possible.

### B7. Channel freshness is not an atomic compare-and-switch

Local and backend `set_channel` accept no expected previous digest. A stale
writer can move `stable` from 1.1.0 back to 1.0.0 after doing an earlier
freshness read. Backend channel updates also use an unlocked read-modify-write,
so concurrent updates can be lost.

Required correction:

- require `expected_release_digest` for an existing channel;
- serialize or transactionally compare channel updates in the backend;
- return the observed pointer on conflict;
- make the Builder stale/rebase decision consume that authoritative conflict.

### B8. Promotion continuation is not durably journaled

Source checkpoint writes are recoverable, but the sequence from accepted
candidate through channel movement, publisher Workspace activation, registry
projection, and subscription observation is not one durable promotion
operation. A failure after channel movement can make the API report failure
even though stable already moved; a normal retry can then classify the original
candidate as stale.

Required correction:

- persist promotion intent and phase receipts;
- treat channel movement as an idempotent CAS phase;
- reconcile an already-moved channel without repeating the mutation;
- continue local activation/projection/subscription from the durable receipt.

### B9. Trial and live activation evidence is weaker than its label

Candidate trial proves package materialization, but runtime reload and health
checks are optional callbacks. The live Root publication path supplies neither.
Trial `snapshot` data mode and audience are recorded, but snapshot creation and
isolation are not enforced by the activation contract. Acceptance can contain
no health observation.

Required correction:

- require an explicit health policy: performed with a durable receipt, or a
  named policy-approved skip;
- require the same explicit contract for runtime reload;
- distinguish `empty`, `mock`, `snapshot`, `read_only`, and `real` data modes;
- attach snapshot identity and isolation evidence when `snapshot` is claimed;
- prevent stable promotion when required trial evidence is absent.

### B10. The backend stores structurally unverified release plans

The backend checks that request and embedded project/digest strings agree, but
does not recompute the release digest, verify referenced package presence, or
enforce version uniqueness. Invalid records can therefore be stored and a
channel can point to them, even though a current AdaOS client later rejects
them.

Required correction:

- validate supported release-plan schemas server-side;
- recompute canonical release identity;
- verify every referenced package exists;
- enforce version uniqueness and channel CAS;
- retain client verification as defense in depth.

### B11. A repeated Builder confirmation originally created a new operation

The first operator UI draft generated a random idempotency key for every
confirmation. If activation committed but the browser lost the response, a
second click represented a new operation even though the reviewed plan had not
changed. The activation layer already had a deterministic safe-replay contract,
so the UI weakened the lower-level invariant.

Current correction: Builder derives its default idempotency identity from the
artifact kind, project id, and exact reviewed plan digest. The activation path
still re-plans and compares that digest before mutation. An explicit caller key
remains supported for recovery tooling, but the ordinary UI no longer invents
a new logical operation on every click.

## Reliability And Performance Gaps

### R1. Cached activation verifies each archive four times

Instrumentation recorded four complete verification passes for one cached
package during activation. `verify`, `read`, and `extract_to_directory` repeat
the same ZIP traversal and hashes.

Correction: pass one verified archive handle/result through the verify and
stage boundary, or use a verified immutable-store receipt. Re-read only when a
trust boundary or file identity changed.

### R2. Package and release transport is base64 JSON

The current remote adapter increases payload size and memory pressure by about
one third and materializes the entire archive in memory on both sides. This is
acceptable for the small proof artifacts but is not a broad package transport.

Correction: keep the adapter boundary, then add streaming binary/object-store
transport with digest verification before visibility. This remains a `should`
gate unless stand sizes expose it earlier.

### R3. Materialization target identity is implicit

Package activation targets the canonical artifact id. Legacy registry entries
can have a different installation directory name, so cutover can create a
second directory instead of replacing the active projection.

Correction: resolve and persist the materialization target in the migration
plan or WorkspaceLock v2, and reject ambiguous aliases.

Current correction: all newly built PackageRefs persist a portable
`materialization_path`; WorkspaceLock retains that PackageRef identity and
activation uses it instead of recomputing the destination. Duplicate targets
fail before staging in release-plan reads, WorkspaceLock construction, and
activation admission. Historical PackageRefs remain readable with their
canonical target so their digest is unchanged. Discovery and explicit
reconciliation of a non-canonical historical directory is intentionally still
open in AP0-07/AP6; it must not be guessed during activation.

### R4. Crash durability is file-atomic, not fully filesystem-durable

Files are flushed before rename, but parent-directory metadata is not fsynced
on platforms that support it. Lock history can also be written just before an
interruption that later rolls the activation back.

Correction: add a portable best-effort directory sync helper and distinguish
active, rolled-back, and orphan history records.

### R5. Runtime code and resolved manifests have separate freshness boundaries

A source push does not mutate the active DEV slot, and an active Python process
does not hot-import a newly committed core SDK symbol. The first live Builder
probe therefore reached the old resolved manifest, and the next probe reached
the new handler through a process that still held the old SDK module.

Current correction: validation now includes the explicit sequence `skill push`
then DEV activation, followed by one managed core-process reload when the SDK
boundary changed. The read surface reports `unavailable` on a stale runtime;
it does not repeat or approximate a state-changing call. Longer term, runtime
version diagnostics should expose source revision and loaded core build in one
operator view.

## Corrected Implementation Order

1. Harden schema admission, version uniqueness, package scrub, portable paths,
   and verification-pass reuse.
2. Fix complete-set dependency resolution, orphan pruning, removal rollback,
   and Workspace writer lease/CAS.
3. Add backend/local channel CAS, server-side release validation, and durable
   promotion recovery.
4. Require explicit runtime reload, health, and trial data evidence policies.
5. Add builder identity/attestation and typed schema/migration locks on those
   strengthened boundaries.
6. **Completed locally:** cut subscribed scenario/skill REST and WebSocket
   update entrypoints over to digest-reviewed package planning and transactional
   activation; retire DEV update and LLM pull, while explicitly labelling the
   bounded non-subscribed compatibility bridge. The transports now share one
   runtime coordinator, so reload, projection, health, and post-commit event
   semantics cannot drift independently.
7. **Completed locally:** add the Builder diff/update-plan UI and bind its
   confirmation to the exact reviewed digest with deterministic idempotency.
8. Add delayed observation and cleanup/retention, then repeat the proof on a
   clean stand.

This order intentionally handles correctness before format expansion. Adding
attestations to a release that can be concurrently overwritten or retain stale
dependencies would increase ceremony without increasing trust.

## Acceptance Rule

No blocker above is closed by code presence alone. Closure requires:

- a regression that reproduces the original failure;
- an implementation test at the local trust boundary;
- backend coverage when the invariant crosses the remote boundary;
- exact operation or digest evidence for state-changing paths;
- an updated roadmap checkbox in the same coherent delivery slice.
