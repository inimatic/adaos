# Application Lifecycle, Distribution, and Feedback

Status: target architecture.

Last reviewed: 2026-09-05.

This document defines the canonical AdaOS model for creating, testing,
publishing, discovering, installing, updating, removing, and improving an
Application. It also defines the boundary between the user-facing Applications
scenario, Builder development, immutable artifact delivery, subnet publisher
identity, and cross-subnet Development Reports.

The implementation currently uses `Project`, `ProjectRelease`, and
`ProjectInstallation` in several APIs and persisted records. Those names are
compatibility vocabulary. New product surfaces and new public contracts use
`Application`, `ApplicationRelease`, and `ApplicationInstallation`. During
migration, one legacy Project definition maps to one Application definition;
Project entry points become Application launch targets rather than separate
Application identities.

The compatibility mapping is structural and one-to-one:

| Canonical contract | Compatibility source | Identity rule |
| --- | --- | --- |
| `Application` | Project definition | stores `legacy_project_id`; `application_id` remains distinct |
| `ApplicationRelease` | `ProjectRelease` | embeds the exact legacy record and preserves its `release_digest` |
| `ApplicationInstallation` | `ProjectDeployment` plus exact package refs | stores `legacy_deployment_id`; placement becomes component state |
| `ApplicationSubscription` | `StableSubscription` | maps `channel` to `stable|prerelease` update intent |
| `RuntimeSelection` | Workspace/Trial activation evidence | adds explicit Webspace-scoped compare-and-swap state |
| `TrialAccessGrant` | no legacy aggregate | introduces bounded capability-link access only |
| `ApplicationOperation` | activation/deployment/publication journals | provides one reviewed durable operation envelope |

Adapters may read legacy records, but new code must not rewrite a
`ProjectRelease` into a new digest merely to rename it. Component SDK consumers
continue to use `skill:*` and `scenario:*` refs inside an ApplicationRelease;
those refs are composition members and launch targets, not Marketplace product
identities.

Detailed package construction, activation, and rollback remain owned by
[Artifact Source, Package, and Activation](artifact-source-package-activation.md).
Builder session and source-context mechanics remain owned by
[Project Composition, Presentation, and Development Context](project-composition-and-development-context.md)
until its compatibility vocabulary is retired. Implementation order is owned
by the
[Application Lifecycle and Distribution Roadmap](application-lifecycle-and-distribution-roadmap.md).

## Decision Summary

1. Application is the canonical user-facing and distributable product object.
   A Scenario is an implementation host, not a separately installable product
   identity merely because it can be launched.
2. An Application may compose one or more owned or shared skills, scenarios,
   workflows, providers, and launch targets. An immutable ApplicationRelease
   locks the complete resolved composition.
3. Builder mutates only Application DEV source. Mutable source runs only from
   `dev/.runtime`; it never activates in the stable Workspace and is never a
   remotely followed channel.
4. A local Trial is an immutable Candidate materialized as a Workspace-shaped
   root under `.adaos/trials/<candidate-id>`. It does not modify
   `workspace/.runtime`.
5. `stable` and `prerelease` are publication channels over the same immutable
   ApplicationRelease identity. `alpha` is not a Workspace channel.
6. There is one canonical prerelease line per Application. Only the current
   publisher may move it. Other subnets submit Development Reports rather than
   publishing upstream beta variants or code contributions.
7. Public prerelease discovery is not a separate Marketplace search surface.
   After the first stable release, an installed user may opt into the
   `prerelease` update track. Before the first stable, and for private
   Applications, Trial installation is capability-link based.
8. Stable promotion moves channel metadata to the exact accepted Trial or
   prerelease digest. The first stable may bootstrap from a link-only accepted
   Trial; later promotion uses the current prerelease. Promotion never rebuilds
   or alters the accepted artifact.
9. Applications is a dedicated full-screen scenario and the sole
   product-facing owner of Application inventory, Catalog, release detail,
   installation, subscriptions, and Development Report status. Infrastate
   retains only technical runtime/component diagnostics and links to
   Applications where useful.
10. The publisher principal is initially the existing subnet identity:
    `publisher_ref = subnet:<subnet-id>`. A separate PublisherIdentity aggregate
    is not required.
11. Subnet identity is reused, but cryptographic key material is
    purpose-scoped. Transport authentication, release signing, and Development
    Report encryption must not silently reuse one key.
12. Root stores immutable remote Application archives and channel metadata. A
    Git repository is not the artifact store for Trial/prerelease packages.
    Public stable source may still be projected to GitHub when publication
    policy requires it.
13. Root Guard quarantine, malware scanning, SBOM policy admission, and signed
    scan receipts are explicitly deferred until the core Application path is
    proven. Existing package signature, digest, path, extraction, permission,
    and Trial-isolation checks remain mandatory.
14. Forward migrations are supported. Every state-changing activation creates
    or names a verified pre-update data snapshot when policy requires it.
    General backward migration is deferred; snapshot restore is the first
    rollback mechanism.
15. Application installation and removal are aggregate operations. Shared
    components are reference-counted and are removed only when no installation
    or active runtime lease still references them.
16. Cross-subnet Development Reports are untrusted external input. They become
    publisher-local Dev Tickets only after deterministic admission and explicit
    publisher acceptance.
17. Root is a durable encrypted relay for Development Reports and public status
    events. It owns delivery, not semantic ticket state.
18. The Applications product is itself created through managed Builder
    development after the Application Core, SDK, and MCP contracts exist. This
    dogfoods the same chat-to-release path that third-party Applications use.

## Current Implementation Boundary

The preparatory implementation covers contracts, Application Core, SDK/MCP,
remote archive/channel/access rails, Development Report relay, and Builder
authoring context. It ends when Builder can start Applications using only
those public contracts. Building the full-screen Applications product and its
release proof are APP4 and APP6 work.

Until APP4 moves the real product workflow, Infrastate Inventory remains an
unchanged compatibility UI. It must not gain new Application authority, and it
must not be removed before Applications has equivalent tested behavior.

Application Core persists its product records under
`state/applications/`. Package archives and legacy release plans remain under
the Artifact Pipeline roots. Core operations therefore reference exact
digests and call a registered executor port; they do not copy package bytes or
perform filesystem/process mutations themselves. A missing executor leaves a
reviewed operation in `planned`, while an unknown executor outcome moves it to
`unknown` and requires reconciliation rather than replay.

`ApplicationsPlane` exposes this core through Root MCP as a thin adapter over
the public SDK. Its mutation contracts accept aggregate identifiers,
revisions, plan digests, and idempotency keys only; filesystem paths, process
commands, Git credentials, and direct registry writes are outside the plane.
Durable operation reads provide the baseline reconnect-safe polling path until
streaming subscriptions are added.

Trial capability links are backed by `TrialAccessGrant` records plus private
credential and redemption records. Links are bound to the recipient subnet,
purpose-scoped key, zone, expiry, scope, and use limit. Only a token hash is
stored; redemption is idempotent by a caller-supplied identity and consumes the
grant atomically. A `follow_prerelease` grant resolves the channel at each new
semantic redemption, while `exact_release` remains digest-pinned.

Application Distribution reuses the Artifact Pipeline package CAS,
`ReleaseRepository`, accepted Candidate records, and exact attestation-set
admission. It journals upload, channel movement, and prerelease retirement
before remote calls. Unknown outcomes require remote observation before a
retry. Link-only Trial is the only first-stable bootstrap source; later stable
promotion requires the exact current prerelease digest and clears that remote
pointer with compare-and-swap. Ordinary install/update planning enforces the
same boundary, so a raw digest cannot bypass Trial redemption for private,
link-only, or otherwise undiscoverable releases.

## Practice Anchors

AdaOS should reuse established security models where their threat boundaries
fit rather than inventing an update or envelope protocol from first principles:

- [The Update Framework](https://theupdateframework.github.io/specification/latest/)
  anchors signed, versioned, expiring update metadata; separation of root,
  target, snapshot, and timestamp authority; key rotation/revocation; and
  rollback, freeze, and mix-and-match resistance.
- [SLSA provenance](https://slsa.dev/spec/v1.2/provenance) anchors verifiable
  source, builder, build process, and input lineage for immutable releases.
- [CycloneDX](https://cyclonedx.org/specification/overview/) is the preferred
  future SBOM representation used by Root Guard and Application detail.
- [HPKE, RFC 9180](https://www.rfc-editor.org/rfc/rfc9180.html) anchors hybrid
  public-key encryption for Development Report envelopes.
- [NIST AI 600-1](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf)
  anchors treatment of indirect prompt injection and data poisoning in
  optional LLM preprocessing.

These are design anchors, not claims that the current implementation already
conforms to every standard or assurance level.

## Canonical Objects

### Application

`Application` is the durable product identity. It is not a mutable source tree,
chat, Builder session, runtime process, or Webspace placement.

Minimum target shape:

```yaml
application:
  application_id: app_01...
  publisher_ref: subnet:sn_...
  slug: research-workbench
  display:
    title: Research Workbench
    summary: Governed research workspace
  visibility: private | link | public
  entrypoints:
    - entrypoint_id: main
      presentation_ref: scenario:research_workbench
  lifecycle: active
```

`application_id` is opaque and immutable. Human-readable slug and publisher
display name may change. The publisher relation is separate so deferred
ownership transfer does not require changing release or installation identity.

### ApplicationRelease

An `ApplicationRelease` is immutable and includes:

- Application definition and composition digest;
- semantic version and exact release digest;
- exact component package and Application dependency locks;
- source revision and deterministic builder/build-policy identity;
- launch-target bindings and required AdaOS/core ABI;
- permission/capability requirements;
- data schema and migration contract;
- validation, Trial acceptance, and activation-health evidence;
- publisher signature and publication receipts.

The legacy `ProjectRelease` record is the current storage-compatible form of
this object. Migration must preserve its digest and evidence lineage.

### ApplicationInstallation

An `ApplicationInstallation` is the local aggregate that binds one Application
to one exact installed release, component references, data-retention policy,
and placement state. It is distinct from source and channel state.

```yaml
installation:
  application_id: app_01...
  installed_release_digest: sha256:...
  component_refs:
    - package_digest: sha256:...
      lifecycle: bound | shared
  data_policy: retain
  status: active
```

### ApplicationSubscription

A subscription records user update intent, not the currently executing
runtime by itself:

```yaml
subscription:
  application_id: app_01...
  update_track: stable | prerelease
  update_policy: notify | auto_compatible | pinned
  observed_release_digest: sha256:...
  paused: false
```

`update_track=prerelease` is persistent intent. While a prerelease exists, it
selects the latest admitted immutable prerelease. When that exact release is
promoted, the effective channel becomes stable without leaving the prerelease
track; a later prerelease may be selected according to the same policy.

### RuntimeSelection

`RuntimeSelection` answers which installed or Trial release is presented in a
particular Webspace. Selection is explicit and scoped; it is not inferred from
directory contents or a mutable manifest label.

```yaml
runtime_selection:
  webspace_id: main
  application_id: app_01...
  source: stable_installation | prerelease_trial | local_trial
  release_digest: sha256:...
  runtime_root_ref: workspace | trial:<candidate-id>
  expected_revision: 17
```

Compare-and-swap revision protects selection from concurrent user and automatic
update operations. Startup reconciliation re-resolves missing or stale derived
runtime roots from immutable release evidence.

### TrialAccessGrant

A Trial access link is a revocable capability, not a discoverable beta listing.
It binds:

- exact Application and publisher;
- `exact_release` or `follow_prerelease` scope;
- recipient subnet/key when targeted;
- issue, expiry, use count, and revocation state;
- allowed zone and delivery policy;
- signed nonce and anti-replay identity.

An expired grant prevents future fetch or update. It cannot erase bytes already
delivered to a recipient.

### ApplicationOperation

Every install, update, remove, track change, Trial install, publication, and
promotion is a durable operation. Mutating APIs use:

```text
plan -> human/policy review -> apply(plan_digest, idempotency_key)
     -> durable operation_id -> reconcile or terminal receipt
```

Unknown mutation outcomes are not replayed blindly. The operation reads
authoritative channel, lock, and artifact state before it decides whether to
resume, reconcile, or require attention.

## Source and Runtime Planes

```text
Application DEV source
  -> dev/.runtime preview
  -> immutable Candidate/ApplicationRelease
  -> .adaos/trials/<candidate-id> local Trial
  -> accepted Trial evidence
  -> Root artifact CAS
     -> link-only Trial delivery -> exact-digest first stable
     -> after first stable: prerelease -> subscriber Trial roots
                            -> exact-digest next stable
  -> Workspace source/lock and workspace/.runtime
  -> optional public stable source projection
```

The authorities are intentionally separate:

| Plane | Authority | Mutable |
| --- | --- | --- |
| DEV source | Builder Development Session | yes |
| DEV preview | `dev/.runtime` derived from DEV source | replaceable |
| Candidate/Release | immutable package and release records | no |
| Local or installed Trial | `.adaos/trials/<candidate-id>` plus TrialActivation | derived root only |
| Stable runtime | WorkspaceLock plus `workspace/.runtime` | only transactionally |
| Channel | signed Root publication metadata | pointer only |
| Public source | optional Git source projection | append/versioned by policy |

`alpha` may be informal UI copy for local DEV preview, but it is not a channel
and never feeds Workspace runtime.

## Channel and Promotion Semantics

The bootstrap and recurring publication paths are:

```text
accepted local Trial -> link-only Trial artifact -> first stable
first stable -> next accepted Trial -> prerelease -> next stable

historical release -> superseded | rejected | expired | promoted | retired
                   -> archived -> purged when unreferenced and outside retention
```

One Application has at most one current stable pointer and one current
prerelease pointer. Historical immutable releases may coexist without becoming
additional visible beta lines.

Promotion requires:

1. the accepted Candidate base is still fresh;
2. for a post-bootstrap promotion, the active prerelease digest equals the
   accepted release digest; the first stable instead names the exact accepted
   link-only Trial digest;
3. validation and health evidence applies to that exact digest;
4. permissions and data migration policy remain admissible;
5. stable channel movement is journaled and idempotent;
6. local publisher Workspace activation is a separate recoverable operation.

A stable release supersedes the promoted prerelease pointer when one exists.
Prerelease subscribers observe stable for the promoted digest and remain opted
in for the next publisher prerelease.

Public prerelease may exist only after the first stable release. Before that,
and for private Applications, Trial installation requires a link/grant. The
Catalog does not provide global prerelease search in the initial architecture.

### Trusted channel metadata

A valid publisher signature over package bytes is insufficient update
protection. Root channel delivery and clients also preserve signed, versioned,
and expiring metadata for trust root, release targets, one consistent catalog
snapshot, and freshness/timestamp state. The exact wire format may adapt TUF to
AdaOS, but it must provide the same minimum properties:

- clients pin an initial trusted Root key set and store the highest accepted
  metadata versions;
- target metadata binds exact release digest, archive size, publisher, channel,
  compatibility, and expiry;
- snapshot metadata binds a consistent set of target/channel metadata to
  prevent mix-and-match;
- freshness metadata expires so a mirror or relay cannot freeze a client on a
  stale channel without detection;
- rollback to metadata or release older than the client's trusted observation
  fails closed unless an explicit incident-recovery policy authorizes it;
- release-signing and online freshness keys have separate purposes and
  compromise boundaries;
- yank, revocation, key rotation, and emergency disable are signed auditable
  events, not mutable database flags with no client proof.

Channel metadata can be mirrored or cached by zones, but no zone may invent a
newer publisher release or combine metadata generations that never existed
together. Offline clients may continue an already installed release according
to local policy, while clearly distinguishing `stale_metadata` from a verified
absence of updates.

### Build provenance

Each ApplicationRelease carries or references an immutable attestation set that
binds its exact source revision, builder identity, build policy, top-level
inputs, package digests, validation evidence, and publisher authorization. The
current AdaOS detached attestation model is the native baseline and should
remain mappable to SLSA provenance. Stable promotion reuses the same
attestation set because it reuses the same artifact digest.

Root Guard will later verify SBOM, dependency, vulnerability, malware, and
license policy. Before Guard exists, absence of those checks is represented
explicitly and does not weaken existing signature/provenance verification.

## Remote Artifact Storage

Remote Trial and prerelease delivery uses a Root-owned content-addressed
archive store:

```text
release metadata -> package digests -> immutable archives
channel metadata -> exact release digest
```

The initial backend may be an on-disk CAS with durable metadata and backup.
The storage contract must allow later object-store and multi-zone replication
without changing package or release identity.

Git is used only where source history and human source access are intentional.
Removing a Trial path from Git does not bound Git history, and a private Git
repository is not an artifact revocation mechanism. `adaos-trials` therefore
must not become the canonical prerelease binary store.

Retention distinguishes:

- removing a release from discovery;
- revoking new installation;
- retaining metadata and tombstones;
- retaining archives for active installations, rollback, or report evidence;
- physical CAS garbage collection.

Offline clients are assumed. Root uses references, holds, and grace periods;
it does not purge an archive solely because a newer stable release exists.

## Applications Product Surface

Applications is a full-screen scenario modeled after the information density
and navigation ergonomics of a mature extension manager, without copying an
IDE-specific information architecture.

The primary views are:

- Installed Applications;
- Catalog, with an `installed` filter;
- updates and active operations;
- Application detail.

Application detail includes publisher identity, visibility, installed and
available versions, current update track, exact effective release, permissions,
component/dependency detail, release notes, Trial access where authorized,
Development Reports, and operation history.

The default surface never exposes raw skill/scenario inventory as peer product
items. Advanced diagnostics may reveal the resolved composition and deep-link
to Infrastate. Infrastate does not own or duplicate Application inventory.

Applications is a system Application. Initial provisioning may bootstrap it,
but subsequent versions use the ordinary ApplicationRelease lifecycle. The
active Applications installation cannot remove itself; CLI/MCP recovery remains
available if its UI is unavailable.

## Application Core, SDK, and MCP

The dependency direction is fixed:

```text
Application Core
  -> typed Application SDK
  -> capability-scoped Applications MCP plane
  -> Applications scenario and Builder
```

Core owns schemas, persistence, policy, reference accounting, resolution, and
operations. SDK is the only supported programmatic interface for local
consumers. MCP adapts SDK operations; it does not duplicate business logic or
expose unrestricted filesystem, Git, registry, or process access.

Minimum user/agent SDK surface:

```text
applications.list/get
applications.catalog.list/get
applications.releases.list
applications.install.plan/apply
applications.update.plan/apply
applications.remove.plan/apply
applications.subscription.set_track
applications.trial.resolve_link/install
applications.operations.get/cancel/retry
```

Builder development uses a separate bounded surface:

```text
application_development.create/materialize
application_development.preview
application_development.create_trial
application_development.publish_trial
application_development.publish_prerelease
application_development.promote_stable
application_development.publish_stable
```

Every MCP mutation binds actor, subnet, capability, target, expected revision,
plan digest, and idempotency identity. Read resources are bounded and expose
exact refs rather than raw mutable registries.

## Installation, Dependencies, and Removal

Install resolves one exact ApplicationRelease and the complete dependency
closure before mutation. The client never sequences independent component
install calls as an Application transaction.

Trial packages are isolated and Workspace-shaped, but isolation must still
cover shared providers, runtime service names/ports, AdaOS ABI, data schema,
external integrations, and active reverse consumers. A full source directory
alone does not prove a closed runtime dependency graph.

The first stable resolver policy is conservative:

- equal shared package digest is reusable;
- an unreferenced component may be installed;
- an incompatible active shared version blocks the plan with a visible
  conflict;
- simultaneous side-by-side versions of one shared component are deferred.

Removal decrements references only after runtime stop and health/reconciliation
receipts. Bound components are removed with the last owning installation.
Shared components remain while any installation, active runtime lease,
rollback hold, or uncertain operation references them. User/domain data follows
its own `retain`, `archive`, or `purge-with-approval` policy.

## Data Migration and Rollback

Semantic version alone does not authorize a migration. Every release declares
the source and target data schema and one of:

- `none`;
- `reversible`, with an executable rollback contract;
- `snapshot_restore`, with a consistent pre-update snapshot;
- `irreversible`, which is not admitted to unattended activation.

For the initial Application track, a state-changing activation:

1. quiesces writes or obtains a consistency boundary;
2. creates or resolves an immutable snapshot;
3. records schema, snapshot digest, retention, and restore procedure;
4. runs the forward migration once;
5. activates and verifies the new release;
6. restores the prior runtime and complete snapshot on failure when policy
   permits.

Snapshot restore may discard writes accepted after cutover. Automatic policy
must either prevent those writes until acceptance or disclose and require an
attended decision. General backward data transformation remains deferred.

Exact-digest prerelease-to-stable promotion does not rerun migration merely
because the channel label changed. A migration runs only when local effective
data/runtime state changes.

## Publisher Identity and Authority

The existing subnet identity is the initial publisher principal:

```yaml
publisher_ref: subnet:sn_...
publisher_key_id: sha256:...
```

Root already knows the subnet public identity. The target subnet identity
record adds purpose-scoped keys:

```yaml
keys:
  - key_id: sha256:...
    purpose: transport_auth | release_signing | message_encryption
    algorithm: rsa-3072 | ed25519 | hpke-x25519
    valid_from: ...
    valid_to: ...
    status: active | retiring | revoked
```

The Application contract does not introduce a separate publisher person or
organization. Root binds the first accepted publication of an Application to
the current `publisher_ref` and valid release-signing key. Key rotation changes
the key binding, not Application identity.

Human-readable subnet names are presentation metadata, not global authority.
Applications shows the publisher's self-declared display name together with a
stable short subnet reference, release-key fingerprint, home zone, and the
local trust relationship (`local`, `previously_installed`, `explicitly_trusted`,
or `unknown`). It must not present an unverified display name as a globally
unique or Root-endorsed identity.

Subnet key loss, compromise, rotation, revocation, and recovery belong to the
subnet identity lifecycle. Ownership transfer, publisher succession, publisher
organizations, threshold approval, and multi-user development are deferred.
An independent subnet may create and publish a different Application under its
own publisher; it must receive a new Application identity and cannot move the
upstream channels.

## Cross-Subnet Development Reports

A guest subnet does not create a Dev Ticket in the publisher subnet directly.
It owns a local `DevelopmentReport` and sends an encrypted signed envelope.

```text
guest DevelopmentReport
  -> deterministic local admission
  -> encrypted Root relay envelope
  -> publisher inbox/quarantine
  -> publisher accepts
  -> publisher-local Dev Ticket
  -> Builder work and release
  -> signed public status events
  -> guest installs release and verifies outcome
```

The public report lifecycle is deliberately distinct from the publisher's
internal Dev Ticket workflow:

```text
draft -> queued -> delivered -> received -> triaged
  -> accepted | declined | duplicate
  -> planned -> prerelease_available -> released
  -> awaiting_local_verification -> verified | still_reproduces
```

Release metadata explicitly lists report IDs it intends to address. Receipt of
a release does not close the report. The guest verifies after installing the
exact digest and may report that the problem still reproduces.

External reports are untrusted input. Before publisher acceptance, deterministic
admission validates schema, size, MIME, archive expansion, Unicode, URL and
attachment policy, quotas, replay identity, installation/release proof, and
secret redaction. Raw report content is not inserted into privileged Builder or
Codex context by default.

Optional LLM preprocessing is classification only. It runs without tools,
network, long-term memory, release authority, or permission to create a Dev
Ticket. Human or publisher policy acceptance remains the semantic boundary.

## Encrypted Relay and Zones

Development Report content and publisher responses use purpose-scoped subnet
encryption keys. Envelope encryption uses a standard hybrid public-key scheme;
the routing header remains minimal plaintext so Root can deliver it. The sender
signs the envelope independently.

```text
Guest Hub
  -> Guest home-zone Root durable outbox/inbox
  -> Publisher home-zone Root durable outbox/inbox
  -> Publisher Hub durable inbox
```

Root stores ciphertext while a publisher subnet is offline. Therefore it is a
durable mailbox/relay, not a stateless proxy. Delivery is at least once;
consumers deduplicate by report/event identity. ACK, TTL, dead-letter,
backpressure, ordering, replay protection, and status resynchronization are
explicit protocol concerns.

A signed subnet directory resolves `subnet_id` to home zone and active public
keys. Root-to-Root transport is authenticated. Forwarding records hop limit,
route generation, destination acceptance, and publisher delivery receipts to
avoid loops and false terminal success. Same-zone relay is the first slice;
cross-zone store-and-forward is required before an inter-zone pilot. Multiple
independent Roots within one zone and automatic failover remain deferred.

## Deferred Root Guard

The future Root Guard will add private quarantine, structural and malware
scanning, SBOM/dependency/license policy, optional isolated dynamic analysis,
and a signed `GuardReceipt` bound to exact artifact digest, scanner versions,
and policy revision.

It is not an admission dependency for the first trusted-publisher Application
pilot. Until it exists:

- only explicitly trusted publisher/tester subnets participate in external
  prerelease pilots;
- public Marketplace prerelease search remains disabled;
- existing local Artifact Pipeline verification remains mandatory;
- Root never represents an unscanned artifact as guard-approved.

Guard will be an additional admission authority, not the publisher or artifact
identity authority. It may reject or quarantine bytes but never rewrite a
signed release.

## Builder Dogfooding Sequence

Applications is built only after the deterministic Application Core, SDK, and
MCP interfaces are usable. Builder creates its definition, scenario, UI, tests,
and releases through the ordinary managed flow. Application source must not
import internal Core services or parse raw registry/state files.

The required dogfooding path is:

```text
chat request
  -> Builder Development Session
  -> Application definition and UI
  -> DEV preview
  -> human revision through Builder
  -> immutable Trial
  -> Trial acceptance
  -> link-only Trial publication/install on a clean trusted subnet
  -> exact-digest first stable and stable install
  -> Development Report round trip and publisher repair
  -> first public prerelease and subscriber verification
  -> exact-digest next stable
  -> stable install/update/remove verification
```

Application UI corrections discovered during the proof should also pass
through Builder. Core/SDK defects become Core Dev Tickets rather than hidden
Application workarounds.

## Required Invariants

1. Mutable DEV source can materialize only `dev/.runtime`.
2. Stable Workspace and each Trial root have independent locks and runtime
   authority.
3. Channel movement names one immutable ApplicationRelease digest.
4. First-stable promotion of a link-only Trial and later promotion of a
   prerelease preserve the exact digest.
5. Only a current valid publisher release-signing key may authorize channel
   publication.
6. Application inventory has one Core source of truth; Applications and
   Infrastate do not maintain competing copies.
7. An Application mutation requires a reviewed plan digest, idempotency key,
   and durable operation record.
8. Removal never deletes a package or data object still referenced by an
   installation, runtime, rollback hold, report, or uncertain operation.
9. A Development Report cannot become a publisher Dev Ticket without the
   publisher acceptance boundary.
10. Report status events are signed, monotonic, idempotent, and recoverable
    after a delivery gap.
11. Root relay cannot decrypt Development Report content without the publisher
    private encryption key.
12. Root Guard absence is represented as `not_evaluated`, never as `passed`.

## Required End-to-End Proof

The first coherent proof must create a fresh Application through chat and then
exercise:

1. DEV preview without Workspace mutation;
2. Candidate and isolated local Trial;
3. accepted link-only Trial publication to Root artifact storage;
4. link installation on a clean trusted same-zone subnet;
5. exact-digest first-stable promotion and clean stable installation;
6. Development Report delivery, publisher acceptance, and status return;
7. a publisher repair, public prerelease, and subscriber verification;
8. exact-digest next-stable promotion while the subscriber remains opted in;
9. update, removal, shared-component reference accounting, and data retention;
10. snapshot-backed migration failure and restore;
11. Hub/Root interruption during publication, installation, and report
    delivery, followed by deterministic reconciliation;
12. Applications UI operation using only the public SDK/MCP contracts.

## Explicit Deferred Directions

- Root Guard and signed scan receipts;
- general backward migration and unattended irreversible migration;
- Application ownership transfer and publisher succession;
- multi-user publisher organizations, threshold approval, and collaborative
  source development;
- upstream code contributions or foreign-beta proposals;
- multiple canonical beta lines for one Application;
- global Marketplace prerelease search;
- simultaneous side-by-side shared component versions;
- multiple independent Root relays per zone and automatic failover;
- commercial entitlement, billing, and remote artifact revocation claims.
