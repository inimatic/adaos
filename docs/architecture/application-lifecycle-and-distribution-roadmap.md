# Application Lifecycle and Distribution Roadmap

Status: target implementation roadmap.

Last reviewed: 2026-09-26.

Target architecture:
[Application Lifecycle, Distribution, and Feedback](application-lifecycle-and-distribution.md).
Companion access architecture:
[Application Access, Permissions, and Roles](application-access-permissions.md).
Companion fast-read projection architecture and embedded roadmap:
[Application Registry Projection](application-registry-projection.md).
Cross-roadmap semantic creation and evolution sequence:
[Semantic Application Composition And Evolution](application-semantic-composition-roadmap.md).

This roadmap sequences the Application domain, SDK/MCP surface, Builder-built
Applications product, trusted prerelease pilot, stable release proof, and later
hardening. Artifact mechanics remain implemented through the existing Artifact
Pipeline; this roadmap does not create a parallel package or activation stack.

## Priority Model

- `[must]`: blocks the exit gate of the named phase or the first coherent
  Application end-to-end proof;
- `[should]`: required before repeated or broader cross-subnet use;
- `[could]`: useful improvement that does not block the first proof;
- `[deferred]`: deliberately postponed until the core single-publisher path is
  proven or a named expansion requires it.

Priority is independent from maturity. An item is checked only with linked
tests, operation receipts, or end-to-end evidence.

## Sequencing Rules

1. Core contracts precede Applications UI.
2. SDK precedes MCP; MCP adapts SDK and never reimplements lifecycle logic.
3. Builder creates Applications only after deterministic Core/SDK/MCP rails
   exist.
4. Same-zone trusted publishing precedes cross-zone federation.
5. Central Root Guard hardening follows, rather than blocks, the first trusted
   publisher pilot.
6. One publisher and one prerelease line precede ownership transfer,
   organizations, or collaborative development.
7. Existing `Project*` records remain compatibility inputs until explicit
   migration evidence permits retirement.
8. Application permission profiles, Application roles, per-user access
   management, and Builder final verification follow the
   [Application Access, Permissions, and Roles Roadmap](application-access-permissions-roadmap.md);
   this roadmap keeps release/install/update lifecycle authority.
9. A successful Builder response, Prototype, or Automation run is not release
   readiness. Trial/publication gates must require the relevant verification
   report when the Application access roadmap makes it mandatory.
10. Application catalog/search performance, trusted startup snapshots,
    background manifest validation, and federated Application fact indexing are
    owned by the Application Registry Projection roadmap. This roadmap consumes
    those read models but does not redefine their trust or rebuild semantics.
11. Semantic requirements and admitted implementation/state resolution precede
    new native Application releases. Exact skill/scenario/package closure
    remains a resolved delivery fact; it is not a semantic source dependency.
12. Applications adopts additive browser-safe requirement/resolution fields
    before the semantic resolver is complete and renders unavailable facts
    explicitly. It must not invent them from component names.

## Current Baseline

The repository already provides important lower-level foundations:

- mutable DEV preview isolated under `dev/.runtime`;
- immutable Candidate and Workspace-shaped Trial roots under
  `.adaos/trials/<candidate-id>`;
- stable Workspace activation under `workspace/.runtime` and WorkspaceLock;
- deterministic package/release digests and detached signing records;
- transaction journals, reviewed plan digests, rollback, retention, and
  operation recovery;
- root-hosted MCP foundation and session leases;
- local Development Signals/Dev Tickets and Builder handoff;
- hub-root cursor, outbox/inbox, replay, and idempotency concepts.

The missing vertical slice is the Application aggregate and product contract
that composes these mechanisms without exposing component-first or
Infrastate-owned UX.

Runtime qualification correction (2026-09-13): Trial package materialization and
its activation record do not yet prove an isolated skill executor. The retained
small Builder lifecycle discovered DEV fallback on Trial tool calls and source
identity loss on browser reopen. These are release-blocking runtime boundaries,
not deferred hardening. Execution, source recovery and live qualification are
tracked in the [Builder lifecycle checklist](builder-intent-to-prototype-roadmap.md);
do not interpret package health or successful UI rendering as execution admission.

## APP0. Contract and Terminology Freeze

**Outcome:** Application is the canonical product/distribution identity and all
later implementation shares one object and state model.

- [x] `[must]` `APP0-01` Define versioned schemas for `Application`,
  `ApplicationRelease`, `ApplicationInstallation`,
  `ApplicationSubscription`, `RuntimeSelection`, `TrialAccessGrant`, and
  `ApplicationOperation`.
- [x] `[must]` `APP0-02` Define one-to-one compatibility mapping from legacy
  `Project`, `ProjectRelease`, `ProjectInstallation`, and placement records
  without changing existing immutable digests.
- [x] `[must]` `APP0-03` Make Application the installed/catalog product; model
  scenarios and entry points as Application hosts/launch targets rather than
  separate product identities.
- [x] `[must]` `APP0-04` Freeze channel vocabulary: local `DEV`, local/link
  `Trial`, publisher `prerelease`, and Marketplace `stable`; remove `alpha` as
  a Workspace activation stage.
- [x] `[must]` `APP0-05` Define one stable and one prerelease pointer per
  Application, exact-digest promotion, immutable history, and
  supersede/retire/archive/purge states.
- [x] `[must]` `APP0-06` Define `publisher_ref = subnet:<id>` and purpose-scoped
  release-signing key binding without introducing a separate PublisherIdentity
  aggregate.
- [x] `[must]` `APP0-07` Define publisher presentation as a self-declared name
  plus stable short subnet ref, release-key fingerprint, home zone, and local
  trust relationship; never imply global verification from display text.
- [x] `[must]` `APP0-08` Assign Application inventory, Catalog, subscription,
  release detail, and Development Report UX to Applications; retain only
  runtime/component diagnostics in Infrastate.
- [x] `[should]` `APP0-09` Publish schema examples and compatibility guidance for
  component/Project SDK consumers.
- [x] `[could]` `APP0-10` Add `derived_from` metadata for independently created
  Applications without granting upstream channel authority.
- [ ] `[deferred]` `APP0-11` Ownership transfer, publisher succession,
  organization publishers, threshold authority, and collaborative development.
- [x] `[must]` `APP0-12` Freeze Application installation at subnet scope.
  Treat node and endpoint identity as mutable component-placement and activation
  metadata backed by `ProjectDeployment`; never create per-node Application
  identities, installations, subscriptions, or desktop entries.
- [ ] `[must]` `APP0-13` Extend native `ApplicationRelease` with an immutable
  semantic revision ref and portable binding-delivery metadata. Define the
  versioned, implementation-free `SemanticApplicationRevision` schema. Extend
  `ApplicationInstallation`/`RuntimeSelection` with an optional admitted
  `application_resolution_ref`/digest. Compatibility Applications may omit
  these fields and must then report `semantic_resolution=not_available`, not an
  inferred success. Never embed the environment-specific resolution in the
  portable release.
- [ ] `[must]` `APP0-14` Keep semantic requirements, selected bindings, package
  delivery closure, local installation, runtime selection, state spaces, and
  observed placement as separate refs and projections. No display model may
  collapse them into one component or provider field.
- [x] `[must]` `APP0-15` Define managed Applications as Projects with
  `kind=project` and one immutable `owner_application_id`. Keep the relation
  distinct from package dependencies and research-domain objects; missing
  `kind` in an existing v1 record means an ordinary Application.

**Exit proof:** schema round trips and compatibility fixtures distinguish all
seven Application objects, preserve legacy release identity, and reject
Project/Application identity collapse.

## APP1. Application Core and Inventory

**Outcome:** one Core service owns installed and catalog Application state.

- [x] `[must]` `APP1-01` Implement an Application repository/service over the
  existing artifact, WorkspaceLock, Candidate, TrialActivation, placement, and
  operation records.
- [x] `[must]` `APP1-02` Implement installed, available, update-available,
  pinned, prerelease-following, retired, and operation-state read models.
- [x] `[must]` `APP1-03` Implement compare-and-swap `RuntimeSelection` per
  Webspace and startup reconciliation from immutable release evidence.
- [ ] `[must]` `APP1-04` Remove Application inventory authority from
  Infrastate; expose a technical deep link from Application detail to component
  and runtime diagnostics.
- [x] `[must]` `APP1-05` Implement aggregate install/update/remove planning over
  one exact ApplicationRelease and the transactional Artifact Pipeline.
- [x] `[must]` `APP1-06` Add component reference accounting for bound/shared
  lifecycle, active runtime leases, rollback holds, uncertain operations, and
  separate data-retention policy.
- [x] `[must]` `APP1-07` Reject incompatible active shared component versions
  with a deterministic conflict plan rather than mutating another Application.
- [x] `[must]` `APP1-08` Add pre-update snapshot identity, consistency boundary,
  retention, restore receipt, and `snapshot_restore` migration mode.
  This qualifies snapshot primitives, not Beta data adoption or the live
  Application channel switch; those remain open under `APP1-13`/`APP1-14`.
- [x] `[should]` `APP1-09` Add ABI, platform, permission, migration, and release
  compatibility summaries to every plan.
- [x] `[could]` `APP1-10` Add operator simulation for removal and retention
  outcomes before apply.
- [ ] `[deferred]` `APP1-11` General dependency solver and side-by-side versions
  of one shared component.
- [ ] `[deferred]` `APP1-12` Backward data migration, including preserving
  newer-schema writes on downgrade, and unattended irreversible migration.
- [ ] `[must]` `APP1-13` Enforce one effective Stable/Beta selection per local
  Application installation and one channel-specific desktop representation.
  Reconcile all Webspaces for that installation; reject parallel verification
  Beta, stale-tab/API execution and old background workers after cutover.
  Consume Artifact Pipeline `AP4-20`, not a UI-only hide/show implementation.
  Implemented: one atomic local channel with revisioned Webspace projections;
  native skill-call read leases span actual execution across threads/processes,
  including a caller timeout. New rooms cannot silently choose another channel.
  Inactive tool calls fail without member fallback; DEV remains independent.
  A durable transition journal now retains the native-call fence after process
  interruption and includes newly introduced target components before selection.
  Remaining: lifecycle/background service drain, unplaced legacy installations,
  explicit stale-UI generation admission, all-room projection refresh and the
  combined crash-recoverable data/configuration cutover. Do not treat the tool
  lease alone as completion of `AP4-20` or permission to reseed live data.
- [ ] `[must]` `APP1-14` Integrate forward-migrated Workspace snapshot data into
  each new Beta from the accepted Stable baseline, not the previous Beta, and
  preserve Beta writes on Stable acceptance (`Keep data=true`). Consume
  `AP4-21`; expose migration evidence, protected recovery copies and explicit
  loss confirmation for snapshot rollback. Never publish user data or infer a
  reset/rollback approval from the channel toggle. Show Beta's data-loss risk
  and gate Beta-to-Beta reseeding after writes, including automatic updates.
  Local prerequisite: checksum-pinned SQLite snapshots, forward staging and
  installation pass two synthetic incremental cycles including Beta-created
  records. Snapshot-finalization interruption is reconciled without reseeding.
  Local native Builder placement and Root candidate publication now use this
  journal for declared SQLite stores and typed configuration. Synthetic tests
  are prerequisites, not live qualification. Two Reading List Stable/Beta/Stable
  increments now pass the bounded live proof below. Volunteer Roster adds two
  independent local cycles and qualifies bounded content-addressed photo-blob
  snapshot/adoption alongside SQLite rows. Service/shared-store adapters and
  Beta-to-Beta recovery/loss confirmation remain open.
  Failed preparation now has exact-contract SDK cancellation back to the
  unchanged Stable, with durable recovery fencing and retained private evidence.
  This is not post-adoption rollback or loss-confirmed Beta replacement.
  The scoped SQLite initialization SDK now shares Beta's checksum executor:
  empty creation, retry, legacy DEV migration, installed-upgrade refusal and
  transactional rollback have synthetic coverage. Generated Reading List code
  now passes actual SDK initialization, HTTP CRUD/settings, public discovery,
  desktop/mobile browser review and restart persistence. Both native Beta
  cutovers and Stable adoptions preserve private records and declared settings.
  Same-Candidate replay does not reseed Beta. This does not establish the
  separate successive-Beta replacement and loss-consent proof in `APP6-10`.
- [ ] `[must]` `APP1-16` Automatically select the node's own Builder Beta after
  each completed local development cycle, including installed Stable upgrades,
  without a second Applications permission. Project the pre-release flag from
  selected local Beta plus external subscription intent, without silently opting
  into public testing. Verify publisher denial, failed-migration fencing,
  replay, all-room refresh, Stable acceptance and unchanged external subscription.
  The SDK delegates local placement to the migration coordinator. Both native
  Reading List increments qualify automatic local selection, one desktop tile,
  unchanged external subscription and Stable acceptance. Scoped release notices
  are reconciled before room refresh, including idempotent candidate replay.
  Web Desktop `0.3.28` additionally qualifies a first-release foreground Beta:
  the production `desktop` Webspace resolves the exact local Trial and renders
  its accepted 45-widget schema on wide and compact viewports without a second
  Applications approval. This is not an installed-Stable upgrade or all-room
  background-execution proof.
  Background execution and all-room qualification remain open.
- [ ] `[must]` `APP1-15` Inherit declared Workspace configuration and scoped
  credential bindings in Beta without copying plaintext into packages or model
  context. Retain compatible overrides across Beta updates/runtime rebuilds;
  migrate configuration explicitly, adopt it on Stable acceptance, and preserve
  rotation/revocation and least-privilege checks across rollback. Qualify
  settings, secrets, restart, schema changes and cross-Application denial
  independently from business-data reseeding.
  Implemented prerequisite: the local revisioned configuration store separates
  typed values and opaque credential references, preserves compatible Beta
  overrides and checks adoption conflicts. Twelve local tests cover two Betas,
  schema rejection, deletion/null semantics and concurrent edits. The typed
  runtime SDK now resolves a bound composition owner implicitly, applies skill
  capability/profile checks, separates DEV and rejects pending cutovers or stale
  schemas. The settings facade does not expose credential bindings. The separate
  secrets SDK now resolves declared purpose-bound slots through the existing
  vault for the verified owner, with synthetic DEV isolation and live revocation.
  A local Windows Keyring roundtrip/revoke probe passes. Delegated/background
  grants, legacy plaintext import, credential retention/garbage collection and
  full credential lifecycle qualification remain open. Reading List's two native
  Stable/Beta/Stable cycles preserve settings, including Beta edits and one
  additive setting in the second increment; DEV restart preservation and stale
  revision rejection are independently qualified. Reading List declares no
  credential slot, so these live cycles do not qualify credential transfer.
- [ ] `[must]` `APP1-17` Project desired component placement and observed
  activations into the Application read model from the existing
  `ProjectDeploymentRuntime`. Include aggregate state, node/component counts,
  health, freshness, partial-result markers, and an honest `not_reported`
  state without creating a parallel placement store.
- [~] `[must]` `APP1-18` Make release-owned setup a first-class lifecycle rail.
  Compile typed settings, required/optional credential slots, connected-account
  requirements, permissions, placement and verification into an immutable
  secret-free `ApplicationSetupContract`; bind its digest to the exact release
  and reconcile a revisioned `ApplicationSetupState`. The contract/state ABI,
  deterministic compiler/projector, CAS store and optional Weather credential
  fixture are implemented and covered locally. Wire setup reads/mutations to
  Applications SDK/MCP, vault/account/permission/placement adapters and the
  install/update operation journal; qualify restart, denial, stale revision,
  revoked secret, required versus optional inputs and failed verification in a
  real Beta before marking complete.
- [x] `[must]` `APP1-19` Enforce the first managed Project lifecycle slice:
  registered ordinary owner, same publisher, no nested/system Project,
  owner-present install admission with apply-time recheck, no independent
  update track, active-child owner-removal block, inverse detail projections,
  default list/Catalog filtering, registry projection, and Builder/MCP create
  fields. Joint owner-plus-Project install remains a future reviewed aggregate
  plan; the implemented path is owner-first provisioning.
- [x] `[must]` Qualify the `APP1-14` failed-preparation recovery primitive:
  exact Candidate/contract, unchanged Stable verification, durable interrupted
  recovery, retained evidence and no false completed-migration admission.
- [x] `[must]` Qualify the bounded `APP1-15` owner-only credential SDK adapter:
  declared slots/purpose, profile denial, cross-Application isolation, synthetic
  DEV, inherited/adopted refs and live revocation. Keep full lifecycle/UI and
  delegated actor qualification open above.
- [x] `[must]` Qualify the first Reading List local Beta cutover prerequisite:
  native Builder preparation/reconciliation, unchanged original Stable records,
  migrated records in Beta, exactly one desktop icon and local prerelease flag
  without external-feed opt-in. Wide/compact browser CRUD/settings/reopen and
  live HTTP discovery/import pass without mutating DEV data. Evidence:
  `reading-list-migrations-20260915/cycle-1/beta-observed-02.json`,
  `beta-browser-03.json`, `beta-http-01.json`.
- [x] `[must]` Qualify first-cycle native Stable acceptance and data adoption:
  exact Candidate changelog acceptance, Stable runtime selection, both original
  records plus three Beta-only test records and both settings retained. Remove
  only E2E-owned records through public tools and reverify the original records.
  Evidence: `reading-list-migrations-20260915/cycle-1/stable-01` and
  `stable-data-http-01.json`; value comparisons remain private recovery proofs.
- [x] `[must]` Qualify a second native Reading List increment against accepted
  Prototype `006`: expanded HTTP validation, wide/compact browser commands,
  restart, immutable published migration history and every supported prior
  schema. Prepare the same Beta through Builder and accept its exact changelog
  into Stable `0.1.8`. All three current user records and three settings survive;
  three Beta-only E2E records also survive adoption and are removed through
  public tools after verification. Private comparisons reverify both original
  records and the third added between increments. Evidence:
  `reading-list-migrations-20260915/cycle-2/acceptance-03`, `beta-replay-01`,
  `stable-02`, `stable-data-http-01.json`. Broader `APP6-10` remains open.

**Exit proof:** two Applications sharing one component install and remove
without premature package/data deletion; an incompatible version is rejected;
a failed migration restores the exact pre-update snapshot and prior runtime.

## APP2. Application SDK and MCP Plane

**Outcome:** Applications and Builder can use the domain without internal
imports, raw registry parsing, or filesystem mutation.

- [x] `[must]` `APP2-01` Add typed SDK reads for Application list/detail,
  Catalog, releases, subscriptions, RuntimeSelection, operations, and
  Development Report status.
- [x] `[must]` `APP2-02` Add SDK `plan/apply` mutations for install, update,
  remove, update-track selection, and Trial-link resolution/install.
- [x] `[must]` `APP2-03` Add bounded Builder SDK operations for create,
  catalog metadata, materialize, DEV preview, Candidate/Trial, link-only Trial
  publication, prerelease publication, exact-digest stable promotion, and
  stable source publication.
- [x] `[must]` `APP2-04` Require actor/subnet/capability context, expected
  state identity, and idempotency identity at every public mutation boundary;
  require a reviewed plan digest and durable `ApplicationOperation` for
  install/update/remove/track apply, and a durable revisioned domain receipt
  for bounded grant, redemption, selection, and development mutations.
- [x] `[must]` `APP2-05` Add an `ApplicationsPlane` to Root MCP as a thin adapter
  over the SDK with bounded read resources and reviewed mutation tools.
- [x] `[must]` `APP2-06` Deny arbitrary filesystem paths, raw Git credentials,
  direct registry writes, and unrestricted process operations through MCP.
- [x] `[should]` `APP2-07` Add operation subscriptions plus reconnect-safe
  polling fallback and structured recovery reasons.
- [x] `[should]` `APP2-08` Add machine-readable SDK/MCP examples and Builder
  context capsules for every supported lifecycle transition.
- [x] `[could]` `APP2-09` Add dry-run explain traces for release and dependency
  resolution.
- [ ] `[deferred]` `APP2-10` Third-party administrative MCP clients and broad
  remote publisher automation.

**Exit proof:** a test client performs the full local lifecycle through SDK and
MCP only, and contract tests prove both surfaces return the same plans,
operations, and terminal receipts.

Implementation note, 2026-09-05: the public `adaos.sdk.applications` facade
owns product reads and reviewed deployment operations, while
`adaos.sdk.builder.applications` owns the bounded authoring lifecycle. The
Builder facade is explicitly registered in SDK discovery and accepts no raw
path, process, Git credential, registry, or private-key arguments. Its durable
coordinator records intent digest, actor, subnet, capability, expected
Application revision, idempotency identity, outcome, and an explicit
reconciliation fence for uncertain callbacks. `ApplicationsPlane` remains a
thin adapter over the product SDK and publishes the bounded
`applications.development.*` tools over the Builder facade. Those tools accept
domain IDs, revisions, and intent only; paths, processes, registries, Git
destinations/credentials, and keys stay inside trusted adapters. Full
chat-driven use of these rails is the `APP4`/`APP6` proof and is not implied by
this preparatory completion.
Development recovery is a separate `applications.recover` capability. It
requires original actor/subnet ownership, preserves the exact stored intent,
allows immediate reconciliation of `unknown`, waits for the five-minute
`applying` lease, and records every attempt and outcome. No caller-supplied
callback or replacement intent crosses the SDK/MCP boundary.
Product release reads are allowlisted: exact release/package/publisher and
composition-lock identities remain visible, while private source, build paths,
credentials, and free-form validation/acceptance evidence are omitted or
reduced to digest/count summaries.
Application mutations are bound to the local subnet. An active skill must also
pass manifest/profile capability admission; a caller-supplied capability token
is not authority. Development and report idempotency replay compares the full
Application, actor/subnet, capability, revision, action, and normalized intent
identity, including an atomic in-lock recheck for concurrent submissions.

Local preparation evidence is recorded in
[Application Preparation Evidence - 2026-09-05](application-preparation-evidence-2026-09-05.md).

## APP3. Prerelease Storage, Channels, and Access

**Outcome:** trusted publishers can publish one prerelease line without using a
Git repository as the artifact store.

- [x] `[must]` `APP3-01` Add Root on-disk content-addressed archive storage and
  durable release/channel metadata behind the existing artifact store ports.
- [x] `[must]` `APP3-02` Stream bounded archives, verify digest before
  visibility, preserve immutable identity, and reconcile unknown upload
  outcomes without blind replay.
- [x] `[must]` `APP3-03` Publish a link-only Trial, or a prerelease after the
  first stable, only from an accepted exact Trial; bind publisher signature,
  release digest, source/build refs, and acceptance evidence.
- [x] `[must]` `APP3-04` Bootstrap first stable from the exact accepted
  link-only Trial digest; promote later stable releases only from the current
  prerelease digest and never rebuild during promotion.
- [x] `[must]` `APP3-05` Implement `stable|prerelease` subscriptions and derive
  effective channel/release without mutating persistent user intent.
  Subscription primitives do not qualify the Applications control or exclusive
  runtime/data cutover; see `APP4-03`, `APP1-13` and `APP1-14`.
- [x] `[must]` `APP3-06` Implement targeted, expiring, revocable
  `exact_release|follow_prerelease` TrialAccessGrant resolution and replay
  protection.
- [x] `[must]` `APP3-07` Require link installation before first stable and for
  private Applications; exclude prerelease from global Catalog search.
- [x] `[must]` `APP3-08` Implement prerelease retirement, retained tombstones,
  reference/rollback/report holds, grace periods, and fail-closed CAS GC.
- [x] `[must]` `APP3-09` Add signed, versioned, expiring Root/target/snapshot/
  freshness metadata or an equivalent TUF-compatible threat model; persist
  client high-water marks and reject rollback, freeze, mix-and-match, unknown
  publisher, and inconsistent-size/digest responses.
- [x] `[must]` `APP3-10` Bind every release to immutable source/builder/build
  policy/input/package provenance and verify the attestation set before channel
  movement or install.
- [x] `[must]` `APP3-11` Add signed yank, release/key revocation, emergency
  disable, and explicit stale-metadata behavior without claiming that already
  delivered private bytes can be revoked.
- [x] `[should]` `APP3-12` Add backup, restore, storage quota, compaction, and
  object-store-compatible diagnostics for the initial on-disk CAS.
- [x] `[should]` `APP3-13` Add sticky staged rollout/pause and health-based halt
  before broad prerelease use without creating multiple visible beta lines.
- [x] `[could]` `APP3-14` Retain GitHub projection for explicitly public stable
  source and release notes.
- [ ] `[deferred]` `APP3-15` Use `adaos-trials` as a canonical prerelease
  repository. The target is Root archive storage; any interim Git adapter must
  be explicitly temporary.
- [ ] `[deferred]` `APP3-16` Global prerelease search and public beta ranking.

**Exit proof:** an accepted Trial publishes once to CAS, installs by capability
link on a clean trusted subnet, bootstraps first stable without digest change,
then completes one public prerelease-to-stable cycle and survives Root restart
and upload-response loss.

Implementation note, 2026-09-05: `APP3-13` applies a publisher-owned,
compare-and-swap policy to the canonical prerelease pointer. Assignment is
deterministic per subscriber subnet, non-selected prerelease subscribers fall
back to stable without losing their track intent, and the latest observation
from each distinct eligible subnet drives an automatic halt. Explicit resume
and idempotent mutation/health receipts prevent a retry from reopening a halted
rollout. Signed cross-zone health transport remains part of `APP5-10`.
`APP3-14` is wired to the production runtime through the configured Workspace
registry repository, remote, and branch; Builder cannot inject Git controls or
publish anything except the exact current public stable source closure.
`APP3-12` is implemented by the Root filesystem CAS and its bounded offline
operator command. Quota and streamed digest verification fail before CAS
visibility; backup/restore and reviewed compaction have immutable receipts, and
authenticated clients can request ordinary or full-integrity diagnostics.
`APP3-06` replay returns an existing redemption receipt only after revalidating
the stored token hash and exact recipient subnet, purpose key, zone, grant, and
receipt identities. A known redemption ID therefore cannot bypass the
capability link bearer or its bindings.

## APP4. Applications Builder Dogfood

**Outcome:** the full-screen Applications product is created through managed
Builder development and consumes only public contracts.

- [x] `[must]` `APP4-01` Start Applications from a conversational Builder
  request with no manually created scenario/application skeleton. The managed
  operation is `appdevop.d80be62139d3cc53fd11282bf907aae2`; Builder session
  `builder_session_f50dcf8e` owns the resulting scenario.
- [x] `[must]` `APP4-02` Build Installed, Catalog, Updates/Operations, and
  Application Detail views with explicit `Marketplace`, `Installed`, and
  read-only `My developments` sections, exact release/track state, and human
  acceptance of the current Prototype. Revision `027` is accepted with exact
  UI and locale-resource evidence; governed state is `automation_ready`.
- [ ] `[must]` `APP4-03` Expose install, update, remove, stable/prerelease track,
  Trial-link install, pause/pin, and operation recovery through SDK/MCP-backed
  actions. The accepted Prototype already shows the channel control; its live
  implementation is pending. Switching replaces the existing Application
  representation, never adds a parallel Beta. A schema-incompatible return to
  Stable opens the snapshot recovery decision with possible data loss.
- [ ] `[must]` `APP4-04` Show publisher display identity and technical
  fingerprint, visibility, exact effective release, permission-profile summary,
  role-impact summary, final verification summary, dependencies,
  migration/backup state, release notes, and Development Report status. Deep
  permission, role, user, child, guest, secret, approval management, and
  verification-checklist semantics are owned by `AAPR4`/`AAPR5` in the
  Application Access roadmap.
- [~] `[must]` `APP4-05` Make Applications the product inventory authority and
  remove duplicate Inventory from Infrastate UI while preserving diagnostics.
  Applications Beta `0.1.25` renders the authoritative inventory and the
  current DEV Infrascope composition omits product Inventory. DEV revision
  `028` adds typed Application conditions, deterministic compact attention,
  independent release-cycle accents, Installed/Marketplace/My developments
  sections, and reviewed batch update assessment/plan/apply. Browser
  qualification and promotion remain open. The previously published
  compatibility surface stays installed until the replacement diagnostics/
  lifecycle matrix and zero-use gate in the Infrascope retirement roadmap
  pass.
- [ ] `[must]` `APP4-06` Mark Applications as a protected system Application:
  bootstrap-capable, ordinary-release updatable, unable to remove its active
  installation, and recoverable through CLI/MCP.
- [x] `[must]` `APP4-07` Perform subsequent UI corrections through Builder and
  route missing Core/SDK behavior to Core Dev Tickets instead of internal
  imports or workarounds. Revisions `002` through `027` were produced by
  Builder chat; renderer, SDK, MCP, and authoring-navigation gaps were fixed in
  their owning layers.
- [x] `[must]` `APP4-12` Project existing local development without creating it,
  show Builder phase/status/revision, and open the exact persisted source
  Webspace and object. Browser proof confirms `desktop/builder` navigation and
  no read-side Application development operation.
- [x] `[must]` `APP4-13` Replace creation-prompt text in Application metadata
  with a concise publisher-reviewed product summary through a governed Builder
  metadata operation; do not patch the Application store manually. Builder
  operation `appdevop.340c4106a5e15c171f2c5e2a5676d702` advanced the aggregate
  to revision `2`, and browser evidence confirms the summary/categories while
  hiding the original prompt. Lost-response recovery is idempotent.
- [ ] `[must]` `APP4-14` Exercise real browser `plan -> review -> apply` for
  install/update/track/remove, including stale revision, failed apply, restart,
  and operation-state recovery before the Automation/Trial exit gate. The
  batch update contracts and review surface are implemented and covered by
  SDK/MCP/scenario tests; browser proof must include zero-eligible, partial,
  resumed, and successful batches.
- [x] `[must]` `APP4-15` Store scenario-owned EN/RU dictionaries as declared
  Prototype resources, verify both locales during qualification, bind their
  exact digest into acceptance, and invalidate acceptance after locale changes.
- [x] `[must]` `APP4-16` Present direct lifecycle intent (`Install`, `Update`,
  `Uninstall`) while retaining plan digest and apply as the internal reviewed
  protocol. Treat auto-update and prerelease-following as direct idempotent
  preferences rather than a separately reviewed plan. Default auto-update to
  `true` and prerelease-following to `false`. Applications `0.1.21` qualifies
  authoritative inventory, placement metadata, Home pin state, adaptive
  section overflow, and search/pagination. Direct preference mutation remains
  `APP4-46`; permission-toggle, registry-completeness, and public-Root delivery
  work remain separately open.
- [x] `[must]` `APP4-18` Keep scenario creation subject-neutral. Builder starts
  Applications from the universal `scenario_default`; `recipe.application_manager`
  supplies only versioned capability compositions, phase dependencies, and
  machine postconditions. No Applications UI or locale dictionary is bundled
  as a scenario template or zero-model materializer.
- [x] `[must]` `APP4-19` Measure a governed Prototype path from a fresh generic
  Project. `applications_phased_prototype_experiment_c118d053` reached qualified
  revision `018` through six cumulative phases, bounded candidate replay, one
  locale-quality correction, structured review, and repeated performance
  probes. It remains unaccepted and did not enter Automation, Trial, or
  publication.
- [x] `[must]` `APP4-20` Make recipe execution phase-aware and cumulative.
  Builder selects the current phase from a versioned workflow, supplies only
  that phase's composition keys and required postconditions, and preserves all
  previously qualified phases. Narrow requests need not execute the full graph.
- [x] `[should]` `APP4-21` Return a bounded Builder creation receipt instead of
  embedding complete Preview, workbench, and Application state. Keep full state
  behind explicit read operations and enforce a sub-10-KiB receipt with
  oversized stored-state regression fixtures. Builder skill `0.3.124` provides
  the bounded receipt.
- [x] `[must]` `APP4-22` Create and materialize a fresh Preview Webspace through
  its owning AdaOS process, require the exact requested DEV scenario, and fail
  closed instead of accepting an unrelated workspace fallback.
- [x] `[must]` `APP4-23` Treat a removed Project, skill, or scenario as a
  terminal availability state rather than a data-source reconnect or tool
  manifest mismatch. Scenario removal moves every affected Webspace to its
  valid current/home scenario or the built-in `web_desktop` fallback. Direct
  navigation to a missing scenario keeps the valid home visible and presents a
  localized system-level choice of available scenarios; the same chooser is
  the no-home 404 surface.
- [x] `[must]` `APP4-24` Journal the exact model request and generation options,
  normalized primary/repair candidate, candidate digest, validation findings,
  and provider job identity before activation. Retry validates scenario,
  revision, and digest and replays a valid candidate without another model
  call. Builder chat owns the single persistent result emit for nested updates.
- [x] `[must]` `APP4-25` Make context composition stage-sensitive. Builder sends
  one deterministic cacheable ABI/capability/recipe prefix and one dynamic
  current-phase request containing the current WebUI, latest semantic delta,
  bounded history, runtime context, and only the active cumulative
  postconditions. Exact request artifacts and provider telemetry preserve
  input, cached-input, output, and latency evidence for each job.
- [ ] `[should]` `APP4-26` Require an ABI impact declaration whenever WebUI or the
  capability catalog changes. The gate names affected Client renderers,
  supported catalog/schema ranges, migration behavior, shared conformance
  fixtures, component tests, and EN/RU wide/compact browser evidence.
- [x] `[must]` `APP4-27` Recognize canonical `preview-<12 hex>` Builder
  Webspaces as development-only Prototype surfaces in the Client while
  rejecting preview-like arbitrary names and all stable Webspaces. Verify MCP
  fixtures in Client contract tests and in the Applications browser matrix.
- [x] `[must]` `APP4-31` Apply exact Prototype review moves through stable
  component references and structured operations before considering model
  inference. Preserve the complete submitted note as ticket evidence, dedupe
  repeated captures, and resolve the canonical ticket against the resulting UI
  revision.
- [x] `[must]` `APP4-32` Keep iterative Prototype checkpoints local and
  content-addressed. Do not synchronously publish the growing `ui_revisions`
  history to Forge after every edit; remote `adaos dev project push` remains an
  explicit milestone/finalization operation.
- [x] `[must]` `APP4-33` Run DEV runtime synchronization only when source is
  newer than the active runtime receipt. A tool call must not rebuild an
  unchanged skill merely because a wall-clock throttle expired.
- [ ] `[should]` `APP4-08` Add responsive wide/compact layouts, keyboard and
  accessibility checks, long-text fixtures, and reconnect-safe operation state.
- [ ] `[should]` `APP4-09` Add advanced component/runtime drill-down without
  exposing it as the default product model.
- [ ] `[should]` `APP4-17` Add an opt-in bounded screenshot review after
  deterministic qualification: compact plus wide capture, no more than two
  model repair iterations, and a structured Development Ticket when a renderer
  or declarative-ABI gap remains.
- [ ] `[should]` `APP4-28` Define a reusable chat IO-route contract and policy
  surface before adding Telegram mirroring to Builder, Voice Chat, Research
  Workbench, and Dev Tickets. Route identity, authorization, loop prevention,
  delivery receipts, redaction, and replay behavior must be shared rather than
  implemented as Builder-only UI.
- [ ] `[should]` `APP4-29` Add a declarative compact-sidebar interaction policy
  (`close on select|click|never`) and Client conformance tests so a drawer does
  not hide the result of a Process or catalog selection.
- [ ] `[should]` `APP4-30` Reduce repair cost without weakening evidence. Prefer
  the latest semantic delta plus a digest-bound candidate reference over
  repeating the complete WebUI and candidate when the provider can retrieve
  the exact journaled artifacts. The 2026-09-08 run showed 56-70k aggregate
  input-token repair paths despite successful prefix caching.
- [ ] `[should]` `APP4-34` Enforce performance budgets per development stage.
  Record cold and warm Client build, focused and complete browser tests, AdaOS
  readiness, DEV skill activation, Builder preflight, handler, validation,
  workflow projection, and checkpoint timing. Use a fast affected-test gate on
  ordinary changes and reserve the complete sequential SDK/browser suites for
  qualification, scheduled runs, or an explicit full-validation request.
- [ ] `[should]` `APP4-35` Split the Client root dependency graph at capability
  boundaries. Load optional CV/TensorFlow and heavyweight widgets only when a
  scenario requires them, preserve shared ABI fixtures for every lazy
  component, and keep unknown declarative icons on the on-demand static asset
  path rather than importing the complete Ionicons barrel. The icon and
  CV/TensorFlow slices are complete; continue only with component-level ABI
  regression coverage so a loader change cannot silently break a widget type.
- [ ] `[should]` `APP4-36` Cache Builder source analysis, ABI selection, and
  validation indexes by content digest. Invalidate by the exact source/catalog
  digest and expose cache hit/miss timing in the generation receipt.
- [ ] `[should]` `APP4-37` Move noncritical AdaOS catalog/status hydration after
  service readiness and make workflow projection incremental. Keep exact
  readiness semantics for routes that need the hydrated state and publish p50,
  p95, and cold-start budgets.
  A 2026-09-25 intermediate step separates latency-sensitive Root MCP calls
  from the background executor, makes Applications collection reads compact by
  default, and bounds them to 100-row server pages (20 for explicit full view).
  Installed collection reads first fell to about 209 KB and then to about
  102 KB/0.65 s in an isolated cold process after removing repeated release
  catalogs and the unnecessary full-YDoc Home read. Cold browser readiness
  still contends with YRoom materialization and post-ready service startup, so
  the milestone remains open.
- [ ] `[should]` `APP4-39` Expose the exact-checkpoint precondition before a
  Project release operation does expensive work. Report the required
  `checkpoint -> push -> trial` order, the stale component ref, and the last
  confirmed Change id; offer an explicit composed command without collapsing
  the review boundary between component checkpoint and ProjectRelease. A
  2026-09-21 Desktop/Users & Access release exposed a sharper ordering defect:
  `project push --bump patch` mutates owned component versions after their
  checkpoint, so Trial rejects the now-stale primary ref; checkpointing again
  then changes the ProjectRelease source ref under an already immutable Project
  version. The composed operation must reserve/bump versions first, checkpoint
  every owned component second, and build the ProjectRelease without another
  source mutation.
- [ ] `[should]` `APP4-40` Preflight immutable Project versions against Root
  before building or uploading a registry batch. Reserve or advance every
  conflicting version first, then checkpoint the batch under one reachable
  Forge revision and report per-project build, upload, and retry timings.
- [~] `[must]` `APP4-41` Show subnet installation and mutable execution
  placement in Applications. Keep the default summary Application-centric and
  provide an expandable component/node drill-down with desired versus observed
  state and reviewed relocation/drain entry points. The SDK/Root read model now
  projects existing deployment/activation authorities, and Applications Beta
  `0.1.21` browser evidence proves the real desired/observed read model.
  Versions also projects the current immutable Workspace manifest for legacy
  read-only applications that have not migrated to an Application aggregate;
  it does not grant lifecycle mutation authority. Reviewed placement mutations
  and relocation/drain qualification remain open.
- [x] `[must]` `APP4-42` Make `web_desktop` the application-centric launch and
  overview shell: Home contains pinned Applications only, complete inventory
  and lifecycle navigate to Applications, and people/role management navigates
  to Users & Access. Do not reproduce Marketplace mutations in the shell.
  Beta `web_desktop@0.3.39` proves real wide/compact Home navigation to
  Applications and Users & Access without duplicate embedded product sections.
- [x] `[must]` `APP4-43` Restore the canonical desktop widget lifecycle in the
  new shell: catalog, pin/unpin, reorder, responsive placement, and persisted
  layout preferences. Widgets are capabilities offered by installed
  Applications, not independently installed packages; Home membership is the
  pinned set and the catalog retains node placement only as diagnostic detail.
  Qualify parity against the previous
  `desktop-icons`/`desktop-widgets` use cases before replacing the beta. The
  generic Client product extension owns catalog presentation. Earlier Beta
  `0.3.39` evidence is no longer sufficient: a persisted edit-layout preference
  was ignored when a declarative state key existed. The generic Client fix and
  focused tests pass. A local active-Beta `0.3.43` rerun now passes reorder,
  unpin, repin, exact presentation-order restore, Settings mutation and chat
  visibility on the authoritative Webspace without direct YJS writes. Client
  `0.0.415+a464f57` is deployed at `inimatic.com`. The Client now uses CDK's
  mixed-orientation strategy for the CSS grid, freezes authoritative stream
  replacement during a gesture, and keeps stable preview/placeholder identity.
  Focused tests and production build pass; external interactive confirmation
  on 2026-09-21 reports predictable reorder behavior. Client `0.0.416` removes
  obsolete Install/Remove and Installed/Pinned duplication, keeps one
  Pin/Unpin action, hides node labels on Home, and excludes the reserved Skill
  Preview scenario through declarative data policy.
- [x] `[must]` `APP4-44` Keep Home presentation separate from installation and
  placement: successful install pins by default, Home customization and
  Applications can unpin without uninstalling, uninstall removes the launcher,
  and order/pin state persists in the target Webspace overlay. A 2026-09-21 live
  rerun found that skill-worker reorder could read an empty process-local
  projection and overwrite all Home pins. Core now owns atomic
  `applications.reorder_home`; Desktop routes Application move and unpin through
  Root MCP and rejects worker-local Application mutation. SDK and contract tests
  pass with canonical and legacy refs. Pin membership is held in
  `pinnedApplications`; reorder mutates only `iconOrder`, so order changes cannot
  replace the membership set. Active-Beta `0.3.43` browser evidence proves the
  complete atomic reorder/unpin/restore path against the deployed Client
  source revision.
  Publisher-local Trial remains an effective installed state and Builder
  placement projects it to Home without creating a Stable installation.
- [ ] `[must]` `APP4-45` Surface governed action approval in the invoking
  workflow. An `action_approval_required` response must replace indefinite
  progress with a localized explanation, the exact Pending Action, and a
  resumable deep link. Browser and Node pairing already complete inline;
  Telegram pairing correctly creates a network-risk Pending Action but still
  leaves its originating modal in a generic preparing state.
- [~] `[must]` `APP4-46` Apply auto-update and prerelease-following changes as
  direct idempotent commands with disabled/in-progress/error/retry states. Do
  not expose `Save update settings` or a plan-review modal for preferences.
  When changing the effective track requires migration, snapshot restore, or
  another risky runtime transition, retain the lifecycle safety boundary and
  show its resulting operation without treating the preference itself as an
  irreversible approval. Core and the Applications DEV scenario now expose one
  `applications.update_settings` command that atomically creates and applies
  its own reviewed plan with the same idempotency identity. The declarative
  toggles update optimistically and invalidate the authoritative summary. A
  local browser/API qualification changed Family Tasks through revisions 2-5,
  observed the persisted values after each refresh, and restored the defaults
  (`auto_update=true`, `use_prerelease=false`). Core now also consumes a
  successfully imported public Application catalog after `sys.ready` or a
  retained `applications.registry.updated` event and runs the ordinary exact
  Application plan/apply protocol for every enabled subscription. Automatic
  apply is fail-closed: component conflicts, missing compatibility or
  permission assessments, elevated authority, uncertain operations, and
  required migrations produce a durable `review_required`/failed outcome
  instead of changing the installation. Safe outcomes and skipped decisions
  are recorded under `state/applications/auto_update_runs`, and registry events
  are retained and replayed to reconnecting subnet members. Startup schedules
  this work after readiness so it does not extend desktop first paint. A clean
  subnet proof imported the public catalog, then advanced Mail Focus Reader
  from `0.1.2` to `0.1.3` with operation
  `appop.31a9057df62bb87719506bb3e8ffbca0`; all package stages and health checks
  completed and installation revision advanced from 3 to 4 without a manual
  update command. This proves the Core-update/restart polling path as well as
  safe exact apply. The public producer is now deployed too: a signed
  `adaos-registry` GitHub push reaches global Root, is retained by registry
  revision, fans out through the existing Root-to-zone management rail, and
  publishes `applications.registry.updated` to connected and subsequently
  reconnecting subnets. A production E2E delivered revision
  `54b7cddc7cd2731c8a38a9110e5cc3dbd6a89827` to both local and clean-subnet
  hubs; the clean subnet completed registry sync and skipped Applications,
  Desktop, and Mail Focus Reader as `already_current`. The item remains open
  for the failure/retry presentation and for a real
  permission-elevation or migration handoff through Applications UI.

  Runtime materialization correction (2026-09-25): a completed promotion is
  no longer considered healthy solely because its `WorkspaceLock` and terminal
  receipts exist. Replay verifies each exact installed package plus retained
  development projection; missing or corrupt source is restored from CAS under
  the Workspace writer lock, then runtime reload and health admission run again
  before success is returned. This closes the observed Applications
  `scenario_not_found` state in which logical installation authority survived
  but the physical Scenario directory did not. The repaired local runtime now
  lists Applications, accepts the Desktop scenario switch, and renders the
  Applications collection/detail journey in a real browser.

  Applications publication/autoupdate qualification (2026-09-26): Builder
  produced the missing behavior-contract evidence through one governed chat
  correction, after which Trial acceptance pinned an unambiguous exact
  `RuntimeSelection`. `applications@0.1.38` was finalized and published with
  release digest
  `sha256:92e3d0c12b8dbec064d609fee711f3aec682ce6d3592a5201fbbf4b61750959a`
  and source-registry commit `45a47c467429bb44e8dfd5cde251c64db57f4602`.
  The clean subnet consumed that publication through its retained registry
  event: run `appautorun.0c1a3099e37cde3113dd9054897b9dd9` applied one
  candidate, failed none, and advanced the active installation to revision 2.
  A post-run audit exposed a projection-order defect rather than an activation
  failure: the workspace and Application installation were exact, while the
  legacy SQLite Scenario row still reported `0.1.63`. Workspace sync now
  reconciles SQLite again after any successful Application auto-update, and
  the list API prefers the materialized registry during that bounded window.
  After the ordinary Scenario sync, the clean subnet reports Applications
  `0.1.65`; Desktop accepted the Applications switch and reached a ready
  29-widget materialization with no missing required branches. The correction
  is covered by the green Core CI at `c04407b14610bc1d9555461c37562808284c50c7`.
- [x] `[must]` `APP4-47` Carry universal Application icon metadata from
  `project.yaml` through Project composition, registry projection and SDK read
  models. `ProjectRelease.catalog` is the immutable ABI boundary, so build,
  publication, installation, Home and Applications consume one release-owned
  value; product ids and titles are not icon lookup keys. The local ABI and SDK
  contract tests pass. The Webspace launcher resolver now maps workspace/dev
  primary scenario refs back to Application Registry metadata and reads Trial
  icons from the signed `ProjectRelease.catalog`; its legacy fallback is used
  only when metadata is absent. Publishing the first releases that contain
  this field is intentionally gated on deploying a Root that admits the
  extended release schema; the older RU Root rejects the unknown field instead
  of silently dropping it. Raster/generated variants remain a compatible media
  extension rather than a Desktop-only contract.
- [~] `[must]` `APP4-48` Replace legacy inventory status heuristics with typed
  Application conditions and a deterministic attention projection. Keep
  health severity separate from release-cycle decoration: Beta is warning,
  installed-current is success, installed-outdated is tertiary, and
  Marketplace-only is unmarked. Core projection, generic Client rendering,
  DEV UI, and contract tests are complete; wide/compact and routed browser
  qualification remains open.
- [~] `[must]` `APP4-49` Provide a bounded reviewed batch-update workflow:
  assess, durable exact plan, human review, idempotent apply, resumable item
  receipts, and explicit partial completion. SDK and Root MCP contracts plus
  the DEV review modal are implemented. Do not claim cross-Application
  atomicity. Real update, failed item, restart-resume, and audit evidence are
  still required before promotion.
- [ ] `[must]` `APP4-50` Reserve typed browser-safe detail projections for
  semantic requirements, admitted resolution, binding summaries, state-space
  portability, evidence freshness, and unresolved gaps. Until the owning
  contracts exist, render `not_available` with source/freshness metadata; do
  not derive capability identity from skills, tools, providers, or titles.
- [~] `[must]` `APP4-51` Keep the default detail compact instead of preserving
  five low-information peer sections. Show active/latest/channel identity and
  direct valid actions first, then About, Configuration, Runtime placement,
  Access and Categories summaries. Open focused task modals for setup and
  placement; show Activity/diagnostics only when evidence is actionable. DEV
  Prototype `0.1.54`, UI revision `009` (`proto 034`) and wide/compact browser
  review qualify this composition with static fixtures. Live read models,
  mutations and production Beta qualification remain open.
- [ ] `[should]` `APP4-52` Add reviewed implementation/provider substitution
  and state-migration impact views after `ApplicationResolution` and
  `ResolutionPlan` are implemented. The UI must show identity preservation,
  state portability, access changes, evidence freshness, and rollback limits
  before applying the exact plan.
- [~] `[must]` `APP4-53` Render and operate release-owned setup without
  Application-specific UI code. Separate typed settings, write-only credential
  slots, connected accounts, access, runtime placement and verification;
  optional gaps stay visible without blocking Ready. The Applications
  Prototype and optional Weather token fixture pass desktop/compact review,
  including secret non-retention, primary relocation, eligible-node install and
  reviewed distributed-component uninstall. SDK/MCP adapter wiring and a real
  Beta install/update/restart cycle remain open under `APP1-18`.
- [ ] `[must]` `APP4-54` Add managed Project UX to Applications: default-hidden
  inventory with **Show projects**, owner **Projects** section, Project
  **Managed by** link, and suppression of standalone install/update-track/
  Catalog actions. Prove search, pagination, direct navigation, restart, and
  owner-removal blocker rendering against the public Applications plane.
- [ ] `[could]` `APP4-10` Add saved Catalog filters and locally pinned
  Application detail sections.
- [ ] `[could]` `APP4-38` Store UI revisions as base plus content-addressed
  semantic deltas with periodic compact snapshots. Retain deterministic replay
  and evidence export without copying the complete UI for every local edit.
- [ ] `[deferred]` `APP4-11` Multi-user publisher collaboration UI and proposal
  review.

**Exit proof:** Builder produces and revises the Applications scenario through
chat, its Trial is accepted and released, and a browser completes ordinary
Application operations without Infrastate owning the workflow.

2026-09-21 checkpoint: access-aware final verification and Builder
`place_local_trial` selected exact Betas on `desktop`:

- `web_desktop@0.3.48`, candidate
  `web_desktop-0-3-48-b92fd39aa512`, digest
  `sha256:a4d3d571ca3d93ecf3d7ec213b6d490b0d719b213b04b5e3dc24b92fd39aa512`;
- `applications@0.1.25`, candidate
  `applications-0-1-25-ec695c66fc49`, digest
  `sha256:43ec06f838884aab106cb14f5a570fd86e26aae25f8a89f1142dec695c66fc49`;
- `users_access@0.1.19`, candidate
  `users_access-0-1-19-02fe0d7f04a0`, digest
  `sha256:88b70517a628b3ece2420b22a9ed656a71d06d244c83d68d4f4902fe0d7f04a0`.

The Applications and Users & Access browser transitions materialize exact
scenario ids, authoritative records, and zero renderer failures. This evidence
does not close the remaining APP4 or APP6 items and is not Stable promotion.

## APP5. Development Reports and Relay

**Outcome:** a trusted external subnet can report a problem and observe its
publisher-controlled resolution without receiving source authority.

- [x] `[must]` `APP5-01` Define local `DevelopmentReport`, encrypted relay
  envelope, publisher intake, public status event, ACK, and resync schemas.
- [x] `[must]` `APP5-02` Bind sender and recipient to existing subnet identities
  and purpose-scoped signing/encryption keys; do not reuse mTLS transport keys
  implicitly.
- [x] `[must]` `APP5-03` Implement same-zone durable Root mailbox delivery with
  at-least-once retry, idempotent dedupe, TTL, dead-letter, backpressure, and
  publisher delivery ACK.
- [x] `[must]` `APP5-04` Keep relay content encrypted end to end; expose only
  bounded routing metadata to Root and record ciphertext retention/deletion.
- [x] `[must]` `APP5-05` Deterministically validate report schema, size, MIME,
  attachment/archive policy, replay identity, quotas, installed release proof,
  Unicode, URL policy, and secret redaction before publisher intake.
- [x] `[must]` `APP5-06` Require publisher acceptance before creating a local
  Dev Ticket or admitting normalized report text to Builder context.
- [x] `[must]` `APP5-07` Project only public report states to the guest; keep
  internal Dev Ticket comments, priorities, evidence, and Builder tasks local.
- [x] `[must]` `APP5-08` Bind addressed report IDs to exact prerelease/stable
  releases and require guest-side verification before terminal closure.
- [x] `[should]` `APP5-09` Add isolated tool-free/network-free LLM
  classification after deterministic admission; preserve raw/normalized/model
  provenance and require publisher acceptance.
- [x] `[should]` `APP5-10` Implement signed subnet home-zone/key directory and
  Root-to-Root store-and-forward before an inter-zone pilot.
- [x] `[could]` `APP5-11` Add publisher-side explainable duplicate clustering,
  an encrypted transparent appeal, and scoped factual reporter history without
  a global reputation score or automatic intake decisions.
- [ ] `[deferred]` `APP5-12` Foreign code proposals, upstream beta variants,
  and automatic contribution merging.
- [ ] `[deferred]` `APP5-13` Multiple independent Root relays within one zone,
  automatic failover, and general relay federation.

**Exit proof:** a clean guest subnet submits a signed encrypted report while
the publisher is offline, Root later delivers it exactly once semantically,
publisher accepts it, a release addresses it, and the guest verifies or reopens
the report after installing the exact release.

Implementation note, 2026-09-05: `APP5-01` through `APP5-08` are covered by a
hermetic two-subnet round trip over the durable same-zone mailbox, including
offline publisher delivery, quarantine, explicit acceptance, local Dev Ticket
creation, an exact addressed release, guest verification, and status resync.
The sender outbox and Root mailbox retain unknown/backpressured work without
blind semantic replay. `APP5-10` now adds a bounded live HTTP Root endpoint,
pinned peer adapter, shared signed-directory projection, durable prepare and
verified-complete handoff, bounded ingress, immediate forwarding, and retained
queue flush after an offline or unknown response. `APP5-09` has an optional digest-pinned OCI
adapter with no pull, network, secrets, AdaOS tools, or broad host mount;
read-only root, dropped capabilities, resource/output bounds, and exact
input/output/image provenance are enforced. Classifier unavailability is
advisory and cannot turn a deterministically valid report into delivery loss.
`APP5-11` adds a versioned encrypted appeal, visible publisher rationale,
advisory same-Application duplicate evidence, and a bounded 365-day factual
reporter history. It exposes neither score nor rank, does not aggregate across
Applications or publishers, and cannot accept, decline, prioritize, or create a
Dev Ticket. The complete report flow is now available through capability-scoped
SDK and `ApplicationsPlane` MCP contracts: `applications.report` for reporter
actions, `applications.publisher.read` for intake evidence, and
`applications.publisher.triage` for publisher decisions.
Distribution validates addressed report IDs against accepted intake and
eligible publisher state before any remote publication. Link-only Trial,
prerelease, and stable publication then append idempotent exact-digest report
status announcements. This closes the release/report coupling at contract and
hermetic-service level; the clean guest/publisher proof remains `APP6`.

## APP6. Full End-to-End Release Proof

**Outcome:** the main single-publisher track is proven before security and
collaboration scope expands.

- [x] `[must]` `APP6-01` Create a fresh non-system Application from chat through
  Builder and complete DEV preview, Candidate, Trial, and acceptance.
  Volunteer Roster completes two local cycles without hand-editing generated
  source; the second reaches exact Stable `0.1.7` with permission, relational,
  blob and wide/compact browser evidence. This closes only the local lifecycle;
  link delivery and clean consumer installation begin at `APP6-02`.
- [ ] `[must]` `APP6-02` Publish the accepted release as a link-only Trial,
  install it on a clean trusted same-zone subnet, and prove stable Workspace
  isolation.
- [ ] `[must]` `APP6-03` Promote that exact Trial digest to first stable, install
  stable on a clean guest subnet, and submit a Development Report.
- [ ] `[must]` `APP6-04` Complete publisher intake, Dev Ticket/Builder repair,
  first public prerelease, guest prerelease subscription, verification, and
  report status synchronization.
- [ ] `[must]` `APP6-05` Promote the exact prerelease digest to the next stable,
  prove prerelease subscription remains opted in, then update and remove with
  correct shared reference and data-retention behavior.
- [ ] `[must]` `APP6-06` Inject failure into migration, artifact upload, channel
  movement, installation, Hub restart, Root restart, relay duplicate/order, and
  report status resync; reconcile without false success or duplicate effect.
- [ ] `[must]` `APP6-07` Capture exact browser, operation, release, Trial,
  WorkspaceLock, artifact, Application verification report, Development Report,
  and recovery evidence in one bounded report.
- [ ] `[should]` `APP6-08` Repeat with a second Application composition and a
  different shared dependency shape.
- [ ] `[could]` `APP6-09` Run a longer trusted prerelease pilot with staged
  rollout and aggregate failure metrics.
- [ ] `[must]` `APP6-10` Qualify existing Stable data -> exclusive Beta ->
  algorithmic migration -> reviewed Stable adoption with Beta-created records.
  Publish two successive Betas before Stable acceptance and prove that each
  validates the full Stable migration, not a Beta-to-Beta-only path. Confirm
  potential loss/retention of first-Beta writes and reject silent auto-reset.
  Also cover first-release `Keep data`, rejected old tabs/direct links/workers,
  restart during cutover/migration, preserved data on runtime rebuild, failed
  migration before writes and explicitly confirmed snapshot restore after
  writes. Prove one desktop representation, no repeated migration on channel
  promotion, no silent reset and no user records in model requests or packages.
  Implemented prerequisite: a publisher-owned new local Beta may replace an
  existing Beta only with explicit data-reset acknowledgement. The replacement
  is rebuilt from the accepted Stable snapshot, never from the previous Beta;
  prior Beta data remains retained for diagnosis. Full successive-Beta browser,
  write-loss disclosure, restart and Stable-adoption qualification remains open.

**Exit proof:** every required proof in the target architecture passes without
manual state/database edits or source changes outside Builder for Application
code.

## APP7. Deferred Hardening and Expansion

These tasks remain visible but do not block APP0-APP6.

- [ ] `[deferred]` `APP7-01` Add Root Guard quarantine and bounded structural,
  secret, malware, SBOM, dependency, and license scanning.
- [ ] `[deferred]` `APP7-02` Sign `GuardReceipt` against exact artifact digest,
  scanner versions, and policy revision; never map `not_evaluated` to `passed`.
- [ ] `[deferred]` `APP7-03` Run risky Guard parsers/scanners in isolated
  workers and prove quarantine cannot mutate signed bytes.
- [ ] `[deferred]` `APP7-04` Add ownership transfer, publisher succession,
  organization publisher principals, threshold stable approval, and audit.
- [ ] `[deferred]` `APP7-05` Add multi-user Application development, trusted
  development groups, reviewable proposals, and WorkLog/ChangeSet semantics.
- [ ] `[deferred]` `APP7-06` Add backward data migration and separately
  governed irreversible migrations. The initial track moves forward and uses
  explicitly acknowledged, potentially lossy snapshot recovery on downgrade;
  it does not promise reverse transformation or merging two active datasets.
- [ ] `[deferred]` `APP7-07` Add simultaneous side-by-side shared component
  versions and a general dependency solver.
- [ ] `[deferred]` `APP7-08` Add public prerelease discovery, ranking, and broad
  untrusted publisher admission.
- [ ] `[deferred]` `APP7-09` Add multi-Root zone redundancy, relay failover, and
  subnet home-zone migration.
- [ ] `[deferred]` `APP7-10` Add commercial entitlement, billing, and licensing
  enforcement.

## Documentation and Commit Gate

- [x] `[must]` Keep the target architecture, this roadmap, Product Terminology,
  Roadmap Inventory, Artifact Pipeline, Development Signals, Identity, Root
  MCP, and MVP roadmap synchronized when a contract changes.
- [ ] `[must]` Do not mark a task complete without evidence matching its exit
  proof.
- [ ] `[should]` Commit coherent implementation slices independently and avoid
  triggering remote CI until the full local verification for that slice has
  passed.
- [ ] `[should]` For every ABI/catalog revision, update the Client impact matrix,
  affected component contract tests, compatibility range, and browser fixture;
  a catalog-only green validator is insufficient.

## Readiness Boundary (2026-09-08)

The preparatory Core, SDK, MCP, artifact, channel, recovery, and Development
Report rails are implemented and locally testable. Applications revision `027`
is the accepted EN/RU Prototype, its Application aggregate is revision `2`, and
the governed workflow is `automation_ready`. The separate generic-template
experiment `applications_phased_prototype_experiment_c118d053` is qualified at
revision `018` across all cumulative recipe postconditions and the EN/RU
wide/compact browser matrix; it is deliberately not recorded as human accepted.
Exact request/candidate journaling, candidate replay, one-result chat ownership,
canonical Builder preview fixtures, and stage-sensitive context composition are
implemented. Structured review now avoids model inference, local Prototype
checkpoints avoid synchronous Forge publication, and unchanged DEV runtimes no
longer rebuild during tool preflight. Semantic-delta repair optimization, cold
startup reduction, Client dependency-graph splitting, and the formal Client ABI
impact gate remain open SHOULD work. Root
ingress and cross-language release canonicalization were production-verified
while checkpointing all 26 registry project manifests through
`adaos project push`. That 2026-09-08 DEV sweep checkpointed 34 of 177 projects;
143 entries were classified as historical research/calibration fixtures with
the retired `project:adaos_research_platform` dependency. The 2026-09-16 Layout
ABI cutover subsequently published all 25 current Workspace projects and all
17 viable DEV projects. The current DEV projection contains 159 projects: the
other 142 are retained fixtures in that same explicit migration-or-archival
class and must not be silently renamed. Eighteen references to already absent
smoke/E2E sources were removed from the legacy registry.
The preparation gate remains in
[Application Preparation Evidence - 2026-09-05](application-preparation-evidence-2026-09-05.md),
and current dogfood evidence is in
[Applications Builder Dogfood Evidence - 2026-09-07](applications-builder-dogfood-evidence-2026-09-07.md).
This is not release readiness. Automation, `APP1-04`, the remaining `APP4`
operation gates, and all of `APP6` remain open until the real UI replaces
Infrastate Inventory and the complete clean-subnet chain is captured without
manual state edits.
