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
corrected finding has a regression at every implemented trust boundary. Backend
admission and channel-CAS hardening is merged through
[inimatic/adaos-backend#2](https://github.com/inimatic/adaos-backend/pull/2)
with a required build/smoke workflow and deployed as backend `0.1.142`. Live
health identifies commit `5570f33`, while hub-mTLS rejection probes confirm the
new fail-closed route contracts. Backend PR `#3` subsequently added bounded
binary package routes and deployed commit `0bc1f82` as `0.1.144`.
Infrastructure PR
[inimatic/infra-inimatic#1](https://github.com/inimatic/infra-inimatic/pull/1)
gave both blue/green slots one persistent host package root. A clean-stand
round-trip and a second deployment proved that package,
release, and channel state survive a single-zone redeploy. Default rollout,
multi-zone durability, and continuous route handoff remained separate gates.
Infrastructure PRs `#2` through `#5` later added durable slot state,
deployment serialization, pre-stop upstream retirement with rollback, pinned
proxy releases, and a single explicit cutover reload. Two clean production
control runs then proved continuous backend HTTP health in both zones; the
broader frontend/WebSocket handoff remains separate.

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
| B12 verifier source fidelity | corrected, validated-local | proof adapter tracks current CAS/reload contracts and rejects DEV content that differs from the exact checkpoint inventory |
| B13 registry trust boundary | corrected, validated-local | corrupt or unknown registry payloads, unsafe paths, and ambiguous aliases fail closed; read-modify-write mutations are serialized, durable writes are atomic, and historical incomplete manifests receive deterministic non-publishable compatibility identities |
| B14 identity drift visibility | corrected, validated-local | read-only diagnostics distinguish registry/channel, installed subscription, immutable source/package/release, and active WorkspaceLock identities; Builder's installed subscription and lock agree while its missing discovery pointer is an explicit AP6 rollout gate |
| B15 channel/subscription admission | corrected, validated-local | malformed or partial ChannelPointers, inconsistent channel indexes, and malformed/duplicate subscription records fail closed before reconciliation |
| B16 remote registry loss recovery | corrected, recovered-live (bounded) | reviewed recovery uploaded exact verified packages/release and created the absent stable channel through CAS; ordinary reconciliation then projected it locally and both plans now return `noop` |
| B17 historical trial contract drift | corrected, recovered-live (bounded) | legacy Builder acceptance claimed `snapshot` without current `data_ref`; recovery refused it until the same immutable release passed a new isolated empty-data activation under current reload/health contracts |
| B18 update-route policy drift | corrected, validated-local | REST, WebSocket, and Builder now share a versioned subscription-based route decision; subscribed package failure cannot fall through to legacy source pull and malformed subscription state fails closed |
| B19 local slot/restart identity | corrected, validated-local | active/previous markers use durable replacement; restart requires ready health, the exact active-slot Git commit, and a stability window rather than accepting a listener or bootstrap PID |
| B20 untrusted initial Yjs reconnect | contained, validated-local (bounded) | malformed/native-risk payloads are subprocess-preflighted and initial browser state is server-authoritative; real reconnects survived, while offline initial draft merge remains deliberately unsupported |
| B21 builder identity is self-declared | corrected, validated-stand (bounded) | backend `8f4f2c1` stores immutable assets/exact release bindings; a separately provisioned signer/trust pair passed exact-bound required activation from an empty cache/Workspace, wrong trust failed before mutation, and no-replay/reconciliation paths have deterministic regressions |
| R1 repeated verification | corrected, validated-local | cached activation verifies and extracts every package in one ZIP/file-hash traversal into operation-private staging |
| R2 base64 transport | improved, validated-stand (bounded) | deployed binary route removes base64 expansion; whole-body buffering and object-store streaming remain open in AP1-12 |
| R3 materialization identity | improved, validated-local | new packages persist and activation consumes an exact portable target; v1 migration preserves and validates historical install aliases, while their package-only activation cutover remains in AP6 |
| R4 filesystem durability | corrected, validated-local | durable rename metadata plus pending/active/rolled-back history sidecars prevent false successful history |
| R5 runtime freshness | improved, validated-local | DEV manifest activation and core-process reload are explicit; stale runtime returns an explicit unavailable result rather than retrying mutation |
| R6 blue/green route handoff | regressed, fix-pending | earlier clean runs passed, but backend PR `#4` exposed candidate admission before health and one strict public-probe `curl rc=55`; infra `5f9a5b0` gates proxy network attachment on health and still needs merge plus clean production control |
| R7 local core-slot preparation | open, should | exact local slot A took 246.6 s: 169.115 s for venv seed/copy and 29.6 s for install; cache/reflink by lock digest is needed without weakening source/import/build gates |
| R8 memory-profile finalizer logging | open, could | the 375-test regression passes, but late finalizers can log after pytest capture closes and emit non-fatal `ValueError: I/O operation on closed file` noise |

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

### B12. The acceptance verifier drifted from production contracts and source

The first post-audit rerun failed because its local remote still exposed the
pre-CAS channel signature and its live promotion omitted the newly mandatory
runtime reload decision. After those were corrected, a deeper review found
that the verifier loaded an exact checkpoint identity but then rebuilt from a
mutable DEV directory without comparing that content with the checkpoint. It
could therefore emit a green proof for different source labelled with an older
revision.

Current correction: the verifier implements the same channel compare-and-swap
signature as production, records an approved reload skip only for its isolated
non-runtime Workspace, and compares the complete publishable path/size/digest
inventory with the verified checkpoint package before tests or proof writes.
It permits a changed package digest only when the source inventory is identical
and the change is attributable to an explicitly recorded package-policy
identity. A dedicated full-path regression exercises the verifier with current
promotion contracts, and a negative regression rejects DEV mutation after the
checkpoint.

### B16. Local activation can outlive remote release and channel state

The package durability deployment occurred after the first live Builder
publication. A current authenticated probe found that local Builder
`0.2.20` still has a matching stable subscription, active WorkspaceLock,
immutable release receipt, accepted trial, and both content-addressed packages,
while the registry service returns `404 release_not_found` for the exact release
digest and `404 channel_not_found` for `builder/stable`. The timing is
consistent with pre-persistence backend state loss, but that cause is not
treated as proven by the local evidence alone.

The new registry reconciliation operation correctly refuses this case: it only
projects a freshly fetched authoritative remote pointer and release into local
discovery and never synthesizes central state from Workspace. The remaining
recovery needs a separate reviewed operation that:

- verifies the installed subscription, WorkspaceLock, release plan, accepted
  candidate/trial, local package digests, and immutable Forge source refs;
- plans the exact remote package, release, and absent-channel writes before any
  mutation;
- journals every remote phase and never automatically repeats an unknown
  write;
- creates the channel only with absent-channel CAS and then uses the ordinary
  reconciliation path for local discovery;
- blocks if any remote release or channel exists with a conflicting identity.

Current correction: recovery is a separate `plan -> reviewed digest -> apply`
operation. It verifies every local and remote identity above, records durable
phase receipts, uploads a missing package/release only once per explicit
invocation, and creates a missing channel only with absent-channel CAS. Lost
responses pause the operation; an explicit retry verifies remote state before
continuing. Builder recovery plan
`sha256:4e2cdbbfcd22e4ebda0cd4cd444283769807a58553de6655f86a96f9e921e06c`
completed with two uploaded package receipts, one immutable release receipt,
and one created-channel receipt. Reconciliation plan
`sha256:c275ccd1b8c1a1d61a99f312c73e1d2f6ca330ad456847faaf8f8ee49cf24dd3`
then projected the exact pointer. Read-only postchecks report no identity
warnings and both operations now plan `noop`.

### B17. Historical acceptance can be weaker than the current trial contract

The target Builder candidate was recorded before the strengthened trial data
contract. It said `data_mode=snapshot` but carried no required immutable
`data_ref`. The current Candidate reader correctly rejected it, which initially
blocked remote recovery even though the old completed activation and user
decision still existed.

Current correction: unrelated candidates are ignored before strict parsing,
but a matching legacy candidate is never silently upgraded. Its immutable core
identity, exact accepted decision, completed historical activation, lock, and
package set are checked through a narrow compatibility adapter. Recovery then
requires a separate explicit revalidation of the same immutable release in a
new isolated empty-data Workspace under current reload and health contracts.
The resulting operation digest is
`sha256:4b8d35827ff4f0b1dd04b2c42f10c8e8f1e1abb4271f897a4bd15da56bec19da`.
The strict Candidate reader remains unchanged and future recovery plans bind the
legacy record digest plus the current revalidation receipt.

### B18. Package rollout policy was duplicated across transports

Scenario REST, skill REST, and skill WebSocket each independently checked for a
subscription before choosing package activation or the legacy source bridge.
They currently branched in the same direction, but the duplicated boolean
decision left room for one transport to reinterpret a package error as grounds
for source pull or to treat an invalid subscription store as no subscription.

Current correction: `adaos.artifact.update_route.v1` is selected once by the
shared coordinator. A present valid subscription means `package_required=true`
and `legacy_allowed=false`; only genuine absence selects the explicitly labelled
compatibility route. Subscription parsing errors remain errors. REST and
WebSocket consume that object, and package activation responses expose it for
Builder/operator diagnostics. Focused transport and coordinator regressions
cover the shared route and fail-closed store behavior.

### B19. A listener and bootstrap PID were weaker than runtime build identity

The local recovery path could observe a bound port while a stale or incomplete
slot was running, and Windows process bootstrap could hand ownership to a child
whose PID differed from the launcher. Slot marker writes also lacked the same
durability boundary as artifact activation. Together these defects could turn
"something is listening" into a false successful restart.

Current correction: slot markers are written through a same-directory temporary
file, flush, `fsync`, and atomic replace. Candidate structure and imports are
validated before marker cutover. Restart waits on `/health/ready`, requires the
full Git commit from the active-slot manifest, and then requires a bounded
stability window; it does not require the short-lived bootstrap PID to remain
the listener. The durable restart log retains the launched generation and
failure evidence. Commit `bc603cb8` passed a one-shot live recovery: slot A
reported the exact commit for seven readiness samples under one PID after all
35 installed handlers passed isolated import.

### B20. Browser reconnect payloads could abort the native Yrs process

A real browser reconnect against a newly prepared slot triggered a native Yrs
index-out-of-bounds panic even though structurally similar payloads passed a
cloned-document subprocess preflight. The remaining risk was application of an
accepted initial client state to the shared live YDoc while runtime
materialization and reconnect activity overlapped.

Current correction: malformed frames and native-risk payloads still fail closed
through a subprocess. In addition, the bounded MVP mode treats the server's
durable state as authoritative for the initial browser handshake: client
`SYNC_STEP1` and the first `SYNC_STEP2`/`SYNC_UPDATE` are validated but not
applied to the live YDoc, and the server sends its effective state. Subsequent
client updates use the normal path. Real reconnects for `dev1`, `dev1-dev`, and
`desktop-dev` exercised this branch without a panic while exact-build readiness
remained stable. The explicit product limitation is that offline-only browser
drafts present at reconnect are discarded rather than merged. A future safe
offline-merge protocol requires generation identity and deterministic conflict
handling; silently re-enabling initial merge is not allowed.

### B21. Builder identity was provenance metadata, not cryptographic authority

Package manifests persist `builder_id`, build-policy digest, source revision,
and validation evidence references, but those fields were committed only by the
package digest. Anyone able to publish arbitrary package bytes could make the
same self-declarations. Digest verification proved integrity after selection;
it did not prove which publisher authorized the subject.

Current bounded correction: detached `adaos.artifact.attestation.v1` records
use Ed25519 to bind package/release subject digest, project, issuer/key,
issuance time, predicate type, and predicate digest. Local trust keys are
purpose-scoped, rotate by key id, enforce signing windows and issuer allowlists,
and fail closed after revocation. Attestations have their own content identity
and can use local or external immutable storage without changing existing
release digests. Required activation checks the full release/package set before
fetch and repeats admission under the Workspace writer lease before staging.

This closes the bounded trust/admission path, not broad end-to-end provenance.
The local AP1-07 publisher now journals one deterministic package-then-release
attestation set before dispatch, refuses to replay an uncertain write, and
separates read-only exact-digest reconciliation from explicit continuation. It
also recomputes each expected predicate from the reviewed release plan, so a
valid signature over different provenance is not admitted. Backend PR `#4` is
deployed as `8f4f2c1`, and stand `20260727t070101z-required` proved exact-bound
required activation over mTLS from an empty cache and Workspace. Consumer
admission now rejects valid but unbound signatures; a wrong trust store failed
before fetch or mutation. Deterministic tests cover unknown write outcomes,
read-only reconciliation, and explicit continuation without inducing transport
failure in production. The registry validates exact set connectivity but
deliberately cannot make its signatures trusted.

## Reliability And Performance Gaps

### R1. Cached activation verified each archive repeatedly

Instrumentation recorded four complete verification passes for one cached
package during activation. `verify`, `read`, and `extract_to_directory` repeat
the same ZIP traversal and hashes.

Current correction: the activation `verify` phase verifies and extracts each
cached archive in one ZIP/file-hash traversal directly into operation-private
staging. The later `stage` phase admits that already verified tree; it does not
read the archive again. Permission or migration rejection and any interruption
still remove the private tree before live switch. A package fetched across a
remote trust boundary is intentionally verified once before store visibility
and once when it enters activation staging.

### R2. Package transport used base64 JSON

The original remote adapter increased payload size and memory pressure by about
one third and materialized the entire archive in memory on both sides. This was
acceptable for the small proof artifacts but was not a broad package transport.

Current correction: backend `0.1.144` and the AdaOS adapter prefer a bounded
binary media-type route, preserve structured JSON errors, verify the archive
digest before visibility, and use the legacy base64 route only after explicit
`404`/`405` route absence. An unknown upload outcome is propagated and is not
retried through the compatibility path. The representative scenario package
was 8,130 bytes as binary versus 10,840 base64 payload bytes.

The backend still buffers the bounded request to verify the ZIP, and the
deployed package root is a single-zone host filesystem. Streamed verification,
object-store lifecycle, and replicated durability therefore remain open in
`AP1-12`; this finding is improved, not fully corrected.

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
canonical target so their digest is unchanged. Checked-in v1 migration fixtures
now prove that canonical manifest identity and the historical installation
directory remain separate, derived `scenario.json` cannot supply version truth,
and unsafe or ambiguous aliases fail closed. An incomplete canonical YAML
version receives a deterministic, explicitly non-publishable compatibility
version based on its digest. Package-only reconciliation of that historical
directory is still open in AP6; activation must not guess it.

### R4. Crash durability is file-atomic, not fully filesystem-durable

Files are flushed before rename, but parent-directory metadata is not fsynced
on platforms that support it. Lock history can also be written just before an
interruption that later rolls the activation back.

Current correction: every shared durable replace now uses
`MOVEFILE_WRITE_THROUGH` on Windows. POSIX performs best-effort `fsync` for the
target directory and, for cross-directory moves, the source directory after
the atomic rename. The retry remains bounded to the filesystem switch and does
not replay the enclosing mutation. Lock history now has a durable sidecar bound
to operation id, lock revision, and digest. Commit transitions it from
`pending` to `active`; rollback/recovery marks it `rolled_back`. Retention keeps
pending or malformed history fail-closed, counts only active/legacy history for
rollback retention, and expires rolled-back history without pinning packages.

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

### R6. Blue/green deployment had a public-route handoff gap

Both the binary-backend deployment and the persistent-store deployment briefly
returned public `502` while the workflow was replacing the active slot. Further
fail-closed experiments found three distinct causes: rsync deleted the original
slot pointer, the old process stopped while nginx still routed new requests to
it, and redundant reloads on a floating proxy image produced transport resets.
Failed runs `30228183747`, `30228459894`, `30228943924`, and `30229165792`
remain part of the evidence rather than being reclassified as successes.

Current correction: infrastructure PRs `#2` through `#5` persist slot state
under the protected runtime root, serialize workflow and host mutations, admit
the healthy candidate, atomically install and validate a new-only proxy config
with rollback, commit the new slot, drain before stop, pin `nginx-proxy 1.11.0`
and `acme-companion 2.6.3`, and remove duplicate observer reloads. Bootstrap run
`30229453608` passed both zones with `325/295` strict samples. Clean reverse
controls `30229653248` and `30229788369` passed `322/298` and `321/297` strict
samples with no failures and no proxy recreation. This closes `AP7-14` for the
bounded backend HTTP route. It does not prove seamless frontend replacement,
long-lived WebSocket handoff, object-store durability, or broad production
readiness.

### R7. Windows core-slot preparation is dominated by virtualenv copying

The exact local slot A preparation on 2026-07-27 took 246.6 seconds. Venv
seed/copy and repair accounted for 169.115 seconds, while project installation
took 29.6 seconds. Earlier preparations were in the same broad 216-345 second
range. This is acceptable for a rare recovery but too slow for an interactive
development loop.

The next optimization should cache or clone a dependency layer by lock digest
and platform/interpreter identity. Every candidate must still rerun checkout
identity, required-file, package install metadata, import, installed-handler,
and build-identity gates; a fast path must not reuse an unverified source tree
or turn stale slot contents into runtime authority.

### R8. Memory-profile finalizers can outlive test log capture

The final 375-test core/Yjs/artifact regression passed, but two memory-profile
finalizers attempted a warning after pytest had closed its captured stream.
Python logging emitted non-fatal `ValueError: I/O operation on closed file`
diagnostics. This does not change runtime state or test outcome. The eventual
cleanup should make finalization idempotent and avoid emission through a closed
handler without hiding real profiling lifecycle failures.

### R9. Root promotion success originally proved runtime readiness, not supervisor replacement

The first recovered second-machine updates exposed two distinct false-success
paths. The candidate-owned root runner inherited no process `AgentContext`, so
root copy committed but wrapper refresh failed. After that was corrected, the
old supervisor could still observe the already-ready slot runtime and write
`root restart completed` immediately before its own scheduled exit. The files
were correct, but the receipt did not prove that the promoted supervisor code
was actually loaded.

Current correction: the standalone runner initializes a candidate-owned
context before promotion services are used. Promotion and restart status also
record the immutable supervisor instance id and PID that owned the operation;
that same instance is barred from finalizing its restart. The replacement
process records a distinct completion instance/PID only after root parity and
active-runtime readiness pass. A terminal status boolean cannot authorize a
queued mutation without a durable `subsequent_transition_request`.

Bounded live proof on `192.168.0.30` ended at release `0.1.614` (`f9faba41`, CI
run `30260047119`). The watcher captured `root_promoted` under PID `824452` with
no completion timestamp, then PID exit, systemd restart, and completion under
PID `827238` with a distinct instance id. Runtime ping returned active slot A on
8777; systemd restart count advanced once and both queued-transition flags were
false. This closes the second-machine core convergence gate, but not broad
production or long-lived browser transport acceptance.

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
8. **Completed locally:** add durable delayed verification bound to the exact
   WorkspaceLock revision, with bounded pending markers and read-only replay.
9. **Completed locally:** add explicit dry-run-first cleanup/retention with
   active/recovery reachability protection and exact-path revalidation.
10. **Completed locally:** rerun the representative proof against the current
    CAS/reload contracts and bind mutable DEV to the exact checkpoint package
    inventory before rebuilding.
11. **Completed:** repeat the proof on a fresh isolated stand through deployed
    hub-mTLS package, release, and channel routes, with an empty cache and
    Workspace plus exact-lock delayed verification.
12. **Completed:** deploy bounded binary package transfer with structured
    failures, explicit legacy-route fallback, and no retry on unknown upload
    outcome.
13. **Completed for one zone:** persist one package root across both blue/green
    slots and prove exact package, release, and channel survival after a second
    deployment.
14. **Completed for bounded backend HTTP:** eliminate the public-route handoff
    gap with serialized, rollback-safe pre-stop cutover and two clean controls
    across both deployment zones.
15. **Completed locally:** add deterministic historical registry/manifest
    migration fixtures, fail-closed registry admission, and atomic registry
    replacement without turning derived JSON into version authority.
16. **Completed locally:** add a read-only identity explanation command and use
    it on the current Builder installation. The installed subscription and
    WorkspaceLock agree, while the registry has no stable channel/source
    pointer; the tool reports this drift and does not mutate either authority.
17. **Completed locally:** make ChannelPointer, channel-index, and subscription
    readers fail closed so malformed discovery data cannot authorize repair.
18. **Completed locally:** reconcile a freshly validated remote channel and
    immutable release plan into local registry discovery through explicit
    plan/review/apply, registry CAS, WorkspaceLock stability checks, and
    operation-receipt recovery. The live Builder probe correctly failed closed
    because the remote release and channel are absent.
19. **Completed live for Builder:** implement source/package-attested recovery,
    refuse its legacy incomplete trial, revalidate the same immutable release in
    an isolated empty-data Workspace, restore both packages plus exact release
    and absent stable channel, then complete ordinary registry reconciliation.
    Identity, recovery, and reconciliation postchecks are clean/noop.
20. **Completed locally:** record and enforce the bounded rollout boundary with
    one versioned update-route contract. Subscribed projects are package-only;
    only genuine subscription absence admits labelled legacy source pull, and
    package failure never causes fallback.
21. **Completed on the bounded stand:** rerun the source-faithful local proof,
    304 focused artifact/Builder REST/WebSocket regressions, and an external
    empty-cache/empty-Workspace activation against backend `0.1.144`. The
    dedicated `stand-route-5dd1492f` channel passed exact release read-back and
    delayed verification without moving `stable`.
22. **Completed locally:** harden active/previous slot markers and one-shot API
    restart admission around exact build identity, then recover the local
    runtime from slot A and keep it ready under real browser reconnects.
23. **Completed locally:** make an up-to-date subscribed Builder dry-run return
    a typed package no-op plan without relaxing real activation admission.
24. **Next release boundary:** synchronize the remaining client/core release
    state. Before broad or multi-zone rollout, replace single-zone whole-body
    buffering with streamed object storage, add signed attestations, and retain
    the compatibility route until telemetry proves historical subscriptions
    have migrated.

This order intentionally handles correctness before format expansion. Adding
attestations to a release that can be concurrently overwritten or retain stale
dependencies would increase ceremony without increasing trust.

## Acceptance Rule

No blocker above is closed by code presence alone. Closure requires:

- a regression that reproduces the original failure;
- an implementation test at the local trust boundary;
- backend coverage when the invariant crosses the remote boundary;
- exact operation or digest evidence for state-changing paths;
- a source-faithful verifier regression compiled against the current
  production-side contracts;
- an updated roadmap checkbox in the same coherent delivery slice.
