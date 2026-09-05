# Application Lifecycle and Distribution Roadmap

Status: target implementation roadmap.

Last reviewed: 2026-09-05.

Target architecture:
[Application Lifecycle, Distribution, and Feedback](application-lifecycle-and-distribution.md).

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
- [ ] `[must]` `APP1-05` Implement aggregate install/update/remove planning over
  one exact ApplicationRelease and the transactional Artifact Pipeline.
- [x] `[must]` `APP1-06` Add component reference accounting for bound/shared
  lifecycle, active runtime leases, rollback holds, uncertain operations, and
  separate data-retention policy.
- [x] `[must]` `APP1-07` Reject incompatible active shared component versions
  with a deterministic conflict plan rather than mutating another Application.
- [x] `[must]` `APP1-08` Add pre-update snapshot identity, consistency boundary,
  retention, restore receipt, and `snapshot_restore` migration mode.
- [x] `[should]` `APP1-09` Add ABI, platform, permission, migration, and release
  compatibility summaries to every plan.
- [x] `[could]` `APP1-10` Add operator simulation for removal and retention
  outcomes before apply.
- [ ] `[deferred]` `APP1-11` General dependency solver and side-by-side versions
  of one shared component.
- [ ] `[deferred]` `APP1-12` General backward data migration and unattended
  irreversible migration.

**Exit proof:** two Applications sharing one component install and remove
without premature package/data deletion; an incompatible version is rejected;
a failed migration restores the exact pre-update snapshot and prior runtime.

## APP2. Application SDK and MCP Plane

**Outcome:** Applications and Builder can use the domain without internal
imports, raw registry parsing, or filesystem mutation.

- [ ] `[must]` `APP2-01` Add typed SDK reads for Application list/detail,
  Catalog, releases, subscriptions, RuntimeSelection, operations, and
  Development Report status.
- [ ] `[must]` `APP2-02` Add SDK `plan/apply` mutations for install, update,
  remove, update-track selection, and Trial-link resolution/install.
- [ ] `[must]` `APP2-03` Add bounded Builder SDK operations for create,
  materialize, DEV preview, Candidate/Trial, link-only Trial publication,
  prerelease publication, exact-digest stable promotion, and stable source
  publication.
- [ ] `[must]` `APP2-04` Require actor/subnet/capability context, expected
  revision, reviewed plan digest, idempotency key, and durable operation ID for
  every mutation.
- [x] `[must]` `APP2-05` Add an `ApplicationsPlane` to Root MCP as a thin adapter
  over the SDK with bounded read resources and reviewed mutation tools.
- [x] `[must]` `APP2-06` Deny arbitrary filesystem paths, raw Git credentials,
  direct registry writes, and unrestricted process operations through MCP.
- [ ] `[should]` `APP2-07` Add operation subscriptions plus reconnect-safe
  polling fallback and structured recovery reasons.
- [ ] `[should]` `APP2-08` Add machine-readable SDK/MCP examples and Builder
  context capsules for every supported lifecycle transition.
- [x] `[could]` `APP2-09` Add dry-run explain traces for release and dependency
  resolution.
- [ ] `[deferred]` `APP2-10` Third-party administrative MCP clients and broad
  remote publisher automation.

**Exit proof:** a test client performs the full local lifecycle through SDK and
MCP only, and contract tests prove both surfaces return the same plans,
operations, and terminal receipts.

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
- [ ] `[should]` `APP3-12` Add backup, restore, storage quota, compaction, and
  object-store-compatible diagnostics for the initial on-disk CAS.
- [ ] `[should]` `APP3-13` Add sticky staged rollout/pause and health-based halt
  before broad prerelease use without creating multiple visible beta lines.
- [ ] `[could]` `APP3-14` Retain GitHub projection for explicitly public stable
  source and release notes.
- [ ] `[deferred]` `APP3-15` Use `adaos-trials` as a canonical prerelease
  repository. The target is Root archive storage; any interim Git adapter must
  be explicitly temporary.
- [ ] `[deferred]` `APP3-16` Global prerelease search and public beta ranking.

**Exit proof:** an accepted Trial publishes once to CAS, installs by capability
link on a clean trusted subnet, bootstraps first stable without digest change,
then completes one public prerelease-to-stable cycle and survives Root restart
and upload-response loss.

## APP4. Applications Builder Dogfood

**Outcome:** the full-screen Applications product is created through managed
Builder development and consumes only public contracts.

- [ ] `[must]` `APP4-01` Start Applications from a conversational Builder
  request with no manually created scenario/application skeleton.
- [ ] `[must]` `APP4-02` Build Installed, Catalog, Updates/Operations, and
  Application Detail views with an installed filter and exact release/track
  state.
- [ ] `[must]` `APP4-03` Expose install, update, remove, stable/prerelease track,
  Trial-link install, pause/pin, and operation recovery through SDK/MCP-backed
  actions.
- [ ] `[must]` `APP4-04` Show publisher display identity and technical
  fingerprint, visibility, exact effective release, permissions, dependencies,
  migration/backup state, release notes, and Development Report status.
- [ ] `[must]` `APP4-05` Make Applications the product inventory authority and
  remove duplicate Inventory from Infrastate UI while preserving diagnostics.
- [ ] `[must]` `APP4-06` Mark Applications as a protected system Application:
  bootstrap-capable, ordinary-release updatable, unable to remove its active
  installation, and recoverable through CLI/MCP.
- [ ] `[must]` `APP4-07` Perform subsequent UI corrections through Builder and
  route missing Core/SDK behavior to Core Dev Tickets instead of internal
  imports or workarounds.
- [ ] `[should]` `APP4-08` Add responsive wide/compact layouts, keyboard and
  accessibility checks, long-text fixtures, and reconnect-safe operation state.
- [ ] `[should]` `APP4-09` Add advanced component/runtime drill-down without
  exposing it as the default product model.
- [ ] `[could]` `APP4-10` Add saved Catalog filters and locally pinned
  Application detail sections.
- [ ] `[deferred]` `APP4-11` Multi-user publisher collaboration UI and proposal
  review.

**Exit proof:** Builder produces and revises the Applications scenario through
chat, its Trial is accepted and released, and a browser completes ordinary
Application operations without Infrastate owning the workflow.

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
- [ ] `[should]` `APP5-09` Add isolated tool-free/network-free LLM
  classification after deterministic admission; preserve raw/normalized/model
  provenance and require publisher acceptance.
- [ ] `[should]` `APP5-10` Implement signed subnet home-zone/key directory and
  Root-to-Root store-and-forward before an inter-zone pilot.
- [ ] `[could]` `APP5-11` Add publisher-side duplicate clustering and reporter
  reputation only after transparent appeal and privacy policy exist.
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
blind semantic replay. `APP5-10` has signed directory and authenticated
store-and-forward primitives, but remains open until the peer adapter is wired
to the live Root protocol. `APP5-09` remains open because an advisory Python
interface alone is not an enforceable OS isolation boundary.

## APP6. Full End-to-End Release Proof

**Outcome:** the main single-publisher track is proven before security and
collaboration scope expands.

- [ ] `[must]` `APP6-01` Create a fresh non-system Application from chat through
  Builder and complete DEV preview, Candidate, Trial, and acceptance.
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
  WorkspaceLock, artifact, report, and recovery evidence in one bounded report.
- [ ] `[should]` `APP6-08` Repeat with a second Application composition and a
  different shared dependency shape.
- [ ] `[could]` `APP6-09` Run a longer trusted prerelease pilot with staged
  rollout and aggregate failure metrics.

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
- [ ] `[deferred]` `APP7-06` Add general backward migration and separately
  governed irreversible migrations.
- [ ] `[deferred]` `APP7-07` Add simultaneous side-by-side shared component
  versions and a general dependency solver.
- [ ] `[deferred]` `APP7-08` Add public prerelease discovery, ranking, and broad
  untrusted publisher admission.
- [ ] `[deferred]` `APP7-09` Add multi-Root zone redundancy, relay failover, and
  subnet home-zone migration.
- [ ] `[deferred]` `APP7-10` Add commercial entitlement, billing, and licensing
  enforcement.

## Documentation and Commit Gate

- [ ] `[must]` Keep the target architecture, this roadmap, Product Terminology,
  Roadmap Inventory, Artifact Pipeline, Development Signals, Identity, Root
  MCP, and MVP roadmap synchronized when a contract changes.
- [ ] `[must]` Do not mark a task complete without evidence matching its exit
  proof.
- [ ] `[should]` Commit coherent implementation slices independently and avoid
  triggering remote CI until the full local verification for that slice has
  passed.
