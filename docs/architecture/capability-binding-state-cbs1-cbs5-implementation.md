# Capability, Binding, and State: CBS1-CBS5 Implementation

Status: executable CRUD-first implementation and adoption guide.

Last verified: 2026-09-26.

This document records the implemented CRUD-first scope of `CBS1` through
`CBS5`. All non-deferred items through `CBS5` have validated-local evidence. The
implemented boundary remains deliberately bounded to the Flowboard typed-CRUD
proof; deferred generalizations are not implied.

## Result

The implementation now exercises this authority path:

```text
ApplicationRequirement
  -> SemanticCandidate
  -> exact PackageRelease closure
  -> evidence admission
  -> immutable ApplicationResolution
  -> immutable ResolutionPlan
  -> WorkspaceLock v2 authority commit
```

Portable contracts, local identities, resolutions, plans, locks, telemetry,
and the proof bundle are independently canonical and digest-addressed. Derived
graph data remains outside this authority path.

The executable proof activates one unchanged semantic Application revision in
three materializations:

```text
prototype SQLite StateSpace
  -> local-production package A + production StateSpace
  -> relocated local-production package B + the same production StateSpace
```

Simulation and production intentionally have different `state_space_ref`
values. Production A and B intentionally retain the same `state_space_ref` and
record digest.

## Implemented Surface

### CBS1: portable contract spine

The following closed JSON Schemas and immutable typed records are implemented:

- `adaos.capability.contract.v1`;
- `adaos.state.contract.v1`;
- `adaos.binding.definition.v1`;
- `adaos.binding.delivery.v1`;
- `adaos.environment.profile.v1`;
- `adaos.evidence.claim.v1` and local evidence assessment;
- `adaos.application.requirement.v1`.

Canonical records reject unknown fields, non-finite JSON numbers, unsupported
schema versions, and portable secret/path leakage. A package-independent
`BindingDefinition` uses a logical implementation entry point. Delivery
metadata owns the physical package member, so moving an implementation changes
the package and delivery digests without changing binding semantics.

The first evidence vocabulary is intentionally limited to capability
conformance and state compatibility. Portable bundles reuse the existing
Ed25519 package-attestation trust store and issuer allow-list: the package is
the signed subject and the exact sorted CBS bundle is the predicate digest.
This is local trust-domain admission, not a federated registry.

An explicit-compatibility edge helper covers semantic cases that cannot be
inferred from SemVer. Deterministic reference and field-diff projections are
generated directly from canonical records. The persistent-document terminology
review is available as `scripts/check_cbs_terminology.py` and rejects ambiguous
new `capabilities` declarations and persistent WebUI-style `state_ref` keys.

### CBS2: local binding and state identity

`BindingInstance` and `StateSpace` have stable refs plus append-only immutable
revisions. A revision pins its predecessor, binding definition, delivery,
environment, generation, and authority epoch. Separate access-relation,
lifecycle-operation, and observation records keep mutable health and lifecycle
facts out of identity records.

The local identity store enforces contiguous revisions and non-decreasing
generation and epoch. Effective state guarantees are the intersection of the
state contract, binding support, and environment profile; every state-port
requirement must be included in that result.

Two compatibility projectors exist:

- the current local CRUD registry is projected read-only into a stable
  production identity;
- Builder Preview's SQLite-backed prototype storage is projected into a
  distinct disposable simulation identity.

Projection adopts existing records in place. It does not copy data, transfer
ownership, or grant destruction authority.

The read-only state inspector exposes identity, owner, contract, custodian,
generation, portability, locator, and latest revision-bound operational
observations through `GET /api/v1/cbs/state-spaces/inspect`. Backup/restore and
capacity observations can extend the effective operational guarantee
projection without mutating the `StateSpace`. The local JSON CRUD provider and
the SQLite-backed Prototype provider are both projected, providing a second
legacy shape for compatibility measurement.

### CBS3: two-stage resolution

The bounded resolver first enumerates semantic candidates, then delegates exact
package closure to the existing artifact resolver, and only then admits
evidence. `ApplicationResolution` is created after both stages succeed. Failed
package or evidence admission leaves no resolution or authority mutation.

The immutable result pins:

- the semantic revision and requirement;
- environment and policy digests;
- selected capability and state contracts;
- binding definition and physical delivery;
- exact package closure;
- binding-instance and state-space revisions;
- admitted evidence and prior rejection explanations.

Before exact package/evidence admission, the resolver can emit a deterministic
read-only candidate report. It contains the ranking policy, selected-candidate
reason, typed rejections, effective guarantees, and a bounded set of
non-duplicate Pareto alternatives. Builder/Application semantic viability now
reports both capability gaps and still-unassessed evidence obligations.

### CBS4: plan and authority commit

`ResolutionPlan` separates selection from mutation. It pins the base
`WorkspaceLock`, desired local revisions, generations, epochs, evidence,
package release, classified steps, compensation, recovery, and expiry.

The existing activation journal now carries the application-resolution and
resolution-plan digests through staging, lock switching, recovery, and
diagnosis. The sole authority commit remains the atomic replacement of the
existing `WorkspaceLock`; staged immutable identity revisions are not active
until that lock pins them.

`WorkspaceLock` v2 adds the CBS authority section while the reader remains able
to load v1 locks. Once a workspace has a CBS-managed v2 lock, a legacy
activation cannot silently downgrade it. Single-writer local CRUD operations
validate the active authority epoch, so an old writer is rejected immediately
after a successful rebinding.

Read-only planning tooling adds a structured/human-readable diff, bounded
fresh/expiring/expired re-planning suggestions, and a content-addressed cache
keyed by every planning input. Cached plans remain dry-run records: activation
still rechecks the exact immutable plan and never silently rebases it.

### CBS5: executable CRUD proof

The Flowboard fixture defines one `resource.records.manage` capability, one
record-state contract, simulation and restricted sandbox SQLite bindings, and
a local-production binding.
The same create/update/delete contract scenario runs against the existing
prototype and local CRUD providers.

One E2E test proves all five invariants:

| Invariant | Executable assertion |
| --- | --- |
| materialization independence | simulation and production resolutions pin the same semantic revision and different state spaces |
| package-topology independence | packages A and B deliver the same binding-definition ref and digest from different physical members |
| state continuity | production state ref, record count, record digest, schema lineage, and access relation survive A to B |
| Application independence | canonical semantic bytes stay unchanged and contain no package, binding-instance, provider member, endpoint, or credential identity |
| atomic rebinding | an injected pre-commit failure preserves the old lock, records, generation, and writer; successful commit advances the epoch and fences the old writer |

The proof emits `adaos.cbs.benchmark_telemetry.v1` and
`adaos.cbs.crud_proof.v1`. The proof bundle includes source, semantic,
contract, package, delivery, resolution, plan, lock, local-revision, state,
claim, assessment, telemetry, trace, and test-result facts. Both stores are
content addressed and reject digest collisions.

The same CRUD scenario is exercised against materially different
SQLite-backed Prototype and local JSON providers. Sandbox viability is tested
as a third materialization with its own `BindingInstance` and `StateSpace`,
separate from both Preview simulation and production.

The 2026-09-23 regression gate covered CBS contracts/resolution/activation,
Resource Workbench and both CRUD providers, package storage and workspace
activation, Application runtime/access/data lifecycle, relational storage,
and migration behavior. It exposed and fixed a first-open SQLite race:
`ResourceStorage` schema/WAL initialization is now serialized across threads
and processes through the dependency-neutral mutation lock. The original race
test then passed twenty consecutive runs before the complete gate passed.

Applications now projects the exact Application record into a compact,
read-only CBS lifecycle view covering requirement, resolution, plan,
activation, and lock stages. Builder task
`task.01M36WQ12JBWTK96Z1CG4MYAH5` validated the view in wide and compact
browser modes. Candidate `applications-0-1-34-2a7d35b40618` then completed the
real Trial and public component-update acceptance path. The stable
`RuntimeSelection` pins the accepted release and `WorkspaceLock`; the derived
view did not participate in either authority decision.

### Maintained Application batch qualification

The 2026-09-26 independent-subnet run qualified Desktop and the requested
maintained Application set against Core commit
`e4ed1f10c9857e061fa1bbacdb29aacc6a3ea2b8`. The following exact releases are
active on `192.168.0.30`:

| Application id | Version | Application release digest |
| --- | --- | --- |
| `web_desktop` | `0.3.54` | `sha256:641531165944c32c224c12fcecde6fceb53967fab816ce02f22bb8d48c06f88d` |
| `adaos_drive` | `0.1.18` | `sha256:9983881b57191e9a45700f929a5df5ef9b35593df5418377d824b56cd650ea8f` |
| `adaos_builder` | `0.3.15` | `sha256:88cf68a2859338d97fd38e0784062bbecbe736efde096cb6d82cca4cc75a77aa` |
| `applications` | `0.1.39` | `sha256:c662fbb8ca92dde442ddaf88d1128e7c36599d3656e5e20595a51d430731588d` |
| `cv_descriptor_lab` | `0.1.19` | `sha256:c1ce20e76249515da99dd3e24cc37e5b5fb4592496fc1b481319e09de7788396` |
| `semantic_ui_demo` | `0.10.28` | `sha256:675f32d34f00fbc0ec6e68f61997fe0d1bb7aecacbd3e51d1ae3ac0f0627efc0` |
| `flowboard_lab_for_safely_prototyping_a_r_17528146` | `0.1.17` | `sha256:9794e4f50c83b45d7772b4c7d5832c39b4fe89c6b0872d6b281acb03756080f4` |
| `media_center` | `0.6.114` | `sha256:4c47394b70f86cecf82c93c18029732b121f5a452358e6dd9d38d2430eaa9c7e` |
| `notebook` | `0.1.2` | `sha256:5af5487a9fdddf83fb0566255d3b1e98e5daf60e72b05ae9a5339cccaf5a9275` |
| `redevice_control` | `0.1.22` | `sha256:c15939868394b4de6e4fc1de7b1db28379eb9470123b96692fcbc5f014428fb8` |
| `research_platform` | `0.1.21` | `sha256:758e6dac8a7bbf92b46400fd6de9680ce5f5277ae5c5a2ab3bf4eb20d08e40f7` |
| `slideshow` | `0.1.5` | `sha256:f0ff99f653c0ea864fee7401afef43b9b6aacfe9548b8d89b29f3451632ef410` |
| `subscription_status` | `0.1.17` | `sha256:fa91fb66a431e7336be9369d50c0d2a1850b6d14d4f407346e677ae7b32844b8` |
| `research_tlp` | `0.3.18` | `sha256:ad8c1d22fb71bb4d1a15809765f2dc8194a4f10fb9571de8afda2d50eb477e39` |
| `voice` | `0.1.7` | `sha256:0b891e2da738e254317eb958eda533333ba2a5d17752ed3c75850f19e0dede55` |
| `users_access` | `0.1.21` | `sha256:56b4a980eacf222cadea789e1fc506aeee7a327db83771f808ee53257bb8f256` |

For every row the installed model is active, matches the registry track,
reports `update_available=false` and `auto_update=true`, and exposes the
non-authoritative `derived_read_only` lifecycle projection with requirement
`compiled`, resolution `admitted`, plan `ready`, activation `active`, and lock
`committed`. The runtime smoke additionally proves:

- Desktop accepts the `applications` scenario instead of returning
  `scenario_not_found`;
- Drive's `select_item` call succeeds without the former undeclared
  `workspace.write` denial;
- the Media Center `media_library_agent` is callable from its published
  permission closure;
- Notebook's governed `workspace.read`/`workspace.write` grant permits a
  binary attachment upload and byte-identical download;
- one `api serve` process owns port `8777`; no supervisor is running.

The Media Center update was a deliberate recovery proof rather than a clean
happy path. Topology admission first rejected an incomplete provider closure.
After topology correction, an update worker deadlocked behind the global
component lock still held by registry synchronization and left a known-partial
terminal operation. Commit `92b49f603d036cb87eb60e057e36d35b6824f3a3`
made failed automatic operations retryable; commit `01ad3d88e` moved
auto-update execution after the registry lock handoff and made known-partial
results converge. The published Core merge commit is
`e4ed1f10c9857e061fa1bbacdb29aacc6a3ea2b8`. The next run applied exactly one
update with zero failed and zero uncertain operations, advancing Media Center
to `0.6.111`.

A subsequent runtime-degradation investigation found that terminal rendition
and scan snapshots were replaying `media_library_agent.catalog.changed`. That
made a read-side snapshot request look like a new catalog mutation and could
form a refresh feedback loop. The provider now carries an internal
`_publish_catalog_change=false` marker on snapshot replay, removes the marker
before publishing the stream payload, and emits the catalog event only for a
real terminal transition. The regression is covered by the provider's full
100-test suite.

The first attempted publication exposed a separate source-authority hazard.
`media_center@0.6.113` contained the advanced `media_library_agent@0.6.52`
manifest but not the handler change because the edit had been made in the
managed runtime projection rather than the authoritative owner-development
workspace. The final checkpoint was therefore rebuilt from
`b3ff84c35be31bffd3c8d76d4fd473ab157131ca`; its immutable provider package was
inspected before promotion and contains `media_library_agent@0.6.54`, digest
`sha256:205f206c38665cd2ecc5f777ac1ab6b0e28e58172b707f92b332a9ce7083bb4d`.
The accepted Application release is `media_center@0.6.114`, digest
`sha256:4c47394b70f86cecf82c93c18029732b121f5a452358e6dd9d38d2430eaa9c7e`.

On `192.168.0.30`, registry notification and auto-update deployed all four
exact packages successfully. The inner deployment completed, but its outer
`ApplicationOperation` remained `applying`; the governed reconciliation API
observed the exact succeeded deployment and atomically committed installation
revision 7. Forced rendition and scan snapshot requests after activation
produced no `catalog.changed` event. This closes the Media feedback-loop proof,
but automatic bridging of a completed inner deployment to an interrupted outer
Application operation remains required under MUST Dev Ticket
`dticket.01M3FA71K71C9ES1F5Y2S87RGV`.

Four publication/CBS reuse development tickets for Builder and ReDevice are
verified against exact release, deployment, CBS lifecycle, and independent
node evidence. The separate MUST tickets for managed-Project UX in
Applications, Desktop, and Research Workbench remain open: their acceptance
criteria require a dedicated Project lifecycle implementation and wide/
compact EN/RU browser receipts, which this batch does not pretend to provide.

Startup remains a measured operational debt. The post-update node observed
about 74 seconds before runtime context entry, 19 seconds in router
initialization, and 6.469 seconds in
`hydrate_webspace_materialization_statuses`. Duplicate `adaos_connect` UI-view
materialization and SQLite/fsync contention were also observed. These timings
do not invalidate CBS authority or recovery proofs, but they block any claim
that the current maintained-Application startup path is optimized.

## Adoption And Migration Policy

`CBS1-CBS5` keeps the current runtime readable and recoverable:

- existing skill manifests, resource declarations, provider contracts, and
  package releases remain authoritative in their current domains;
- legacy CRUD state is adopted by projection without changing its path or
  records;
- current semantic Applications do not gain package or provider fields;
- v1 `WorkspaceLock` remains readable and can be upgraded on the first planned
  CBS activation;
- native CBS activation never writes a v1 lock and refuses a later downgrade;
- no current skill is required to publish native portable contracts merely to
  continue running.

This is not a promise of source-level reverse compatibility for maintained
beta Applications. Flowboard, Applications, and AdaOS Drive may be updated
directly when the Core/Application ABI changes. Builder is not currently
required to discover or apply those migrations. Data migration remains an
explicit `ResolutionPlan` concern only when a selected transition changes
schema/provider in a way that cannot preserve the existing StateSpace
attachment.

## Builder Adaptation

Builder has adopted the bounded CBS handoff without becoming a general
Application migration engine:

1. compile accepted semantic activities and state needs into
   `ApplicationRequirement` and state-port references;
2. materialize Preview resources as simulation bindings with disposable
   `StateSpace` identities;
3. display semantic, simulation, and production viability plus typed rejection
   and evidence obligations;
4. allow an Application-local binding when no admitted reusable binding exists;
5. show the exact `ApplicationResolution` and `ResolutionPlan` diff before
   activation;
6. route Trial and production installation through the CBS-aware activation
   path;
7. only later offer capability extraction/curation into reusable packages.

Automatic modernization of an older Application to a newer Core ABI is
deferred to a separate Builder qualification. Until then, maintained beta
Applications are modernized directly and pass through the same compile,
viability, Trial, and exact-activation gates.

Builder must not persist package names, skill IDs, physical entry points,
storage locators, endpoints, or credentials into the semantic Application.

### Compact CBS authoring and compilation

Builder now has a bounded authoring envelope for explicit semantic capability
requirements. `adaos.builder.cbs_intent.v1` is stored under
`pageSchema.meta.builder.cbs_intent`, pinned into Prototype acceptance, and
expanded by compiler `1.1.0` into canonical `ApplicationRequirement` records.
The compact record contains only a stable requirement id, `capability:` ref,
contract range, authoring origin, and optional semantic policy/evidence
constraints. It cannot name a package, skill, entry point, endpoint, account,
or credential.

The compiler records separate counts for human-authored, Builder-inferred, and
compiler-generated requirements. This prevents generated boilerplate from
being reported as human authoring cost in later CBS9 measurements. Existing
compiler `1.0.0` records remain readable; new output is `1.1.0`.

Provider packages have a separate compact, package-local authoring seam:
`contracts/provider.cbs.yaml` with schema
`adaos.cbs.provider_authoring.v1`. It names semantic operations and references
the already declared `skill.yaml` tools, so input/output schemas are not copied.
The deterministic package compiler emits canonical
`CapabilityContract` and package-neutral `BindingDefinition` files and creates
the exact `BindingDelivery` only after the package digest exists. Digests,
delivery templates, and authoring provenance are deterministically derived from
the packaged descriptor and generated contracts instead of extending the
closed `adaos.artifact.component_package.v1` transport manifest. A verifier
recompiles the descriptor and rejects modified generated contracts or any
historical embedded metadata that no longer matches. Therefore a physical
member can move between packages without changing the binding-definition
digest, while the delivery and package digests change as required. Keeping CBS
metadata derived also permits current native-provider packages to pass older
v1 registry validators during a rolling Root/Core upgrade.

The provider compiler also reports `human_authored`, `builder_inferred`, and
`compiler_generated` counts independently from Application-requirement
authoring. Generated canonical JSON is included in source snapshots used by
Forge checkpoints but remains compiler-owned: authors may not supply or
override those output paths.

The first retained use is Gmail Mail Client Prototype revision `007`, whose
accepted intent requires `capability:mail.messages.manage` at `^1.0.0` while
keeping Gmail as a later local binding choice. Wide and compact visual and
interaction evidence is stored under `e2e/artifacts/`. This proves the compact
authoring-to-acceptance boundary, not provider reuse. The exact beta lifecycle,
digests, permissions, runtime evidence, and remaining qualifications are in
[Gmail Mail Client: Stage-One CBS Beta Proof](gmail-mail-client-cbs-beta-proof-2026-09-24.md).

### Bounded Gmail provider boundary

Core now exposes one typed `google.gmail` provider used by the first Gmail
Application proof. OAuth state, PKCE, tokens, refresh and the fixed Google API
destination remain below `adaos.sdk.providers.gmail`; the Application skill
receives only bounded results. Trial placement derives its connected-account
setup requirement from the immutable release declaration, and Builder receives
the exact provider authoring contract only for Gmail-related Automation.

The vault key is provider/user/account scoped rather than Application scoped,
so a credential can be attached to another admitted consumer without being
copied into either package. The follow-up native provider package now publishes
the package-independent mail `CapabilityContract`, `BindingDefinition`, exact
delivery, and portable evidence through the shared semantic registry. Local
reuse by Gmail Mail Client, Inbox Triage, and Mail Focus Reader has exercised
one provider-owned connected account without exposing the credential to any
consumer. A clean subnet imported and activated the same portable identities
and delivery without receiving a credential. This provider may now be counted
as the first native reusable CBS provider; cross-subnet account attachment
remains a local setup step and is not registry portability.

### Native research provider reuse

The 2026-09-26 maintained-Application qualification added a second independent
native provider family. `research_platform@0.1.20` publishes and activates four
exact requirements: the generated UI capability plus
`research.workbench.manage`, `research.lifecycle.manage`, and
`research.tracking.manage`. Its stable public release is
`sha256:00565bcd5abcf282b2fd1478ade556d02dc0461452867c3a42bb3789c608a02e`.

`research_tlp@0.3.16` is a distinct consumer. Its semantic composition requires
`research.tlp.evaluate`, `research.lifecycle.manage`, and
`research.tracking.manage`; admission resolved all four requirements exactly.
The lifecycle and tracking bindings are delivered by the shared Research
Manager and MLflow packages rather than redefined by TLP. Trial, acceptance,
stable activation, Workspace authority commit, attestations, and registry
publication completed for release
`sha256:24a333824da7d24e70058e78713374972f01d5ea5ea914869454c80c8defe8b6`.

This run also qualified two data-lifecycle rails. Research Manager evidence was
moved to Core `storage.blob` without exposing its contents, and Research
Orchestrator declares its derived backup directory reconstructible while
retaining the exact immutable v1-v7 database migration chain. Rejection now
aborts a retained failed-preparation journal even when failure happened before
Trial selection. The remaining performance debt is physical materialization:
the semantic/package closure is reused, but an immutable MLflow vendor tree is
still copied into each new Candidate runtime instead of using a verified
content-addressed cache/reflink layer.

## Skill Adoption

Existing skills need no bulk rewrite. Native adoption should proceed per skill:

1. inventory current semantic capabilities, provider/tool entry points,
   resource declarations, schema locks, permissions, and state ownership;
2. generate candidate contracts and binding definitions, but require explicit
   contract admission rather than treating manifest text as canonical;
3. project and adopt the existing state in place under a stable StateSpace ref;
4. publish package delivery metadata for the admitted binding definition;
5. run shared conformance scenarios and create exact claims;
6. activate a v2 lock and move writers to epoch fencing;
7. retain a reviewed fallback until recovery and rollback evidence pass.

Record migration is unnecessary for package-only relocation. It becomes an
explicit `ResolutionPlan` migration only for incompatible schema or provider
transitions.

## When Legacy Code Can Be Removed

The completion of `CBS5` is not permission to remove runtime recovery and
legacy data readers. Application source compatibility may be dropped now for
maintained beta packages, but removal of a legacy runtime surface is safe only
after all of these gates hold for that surface:

- Builder emits native requirements and can complete Preview, Trial, and
  production activation without a legacy-only path;
- every active workspace has a v2 lock and every writer validates the pinned
  epoch;
- every installed legacy store has a deterministic projection/adoption report
  and its backup/restore and recovery paths have passed;
- package installation always enters through an admitted resolution and exact
  plan;
- operational telemetry reports zero legacy write-path use for at least two
  release cycles;
- downgrade, rollback, and disaster-recovery procedures have been exercised on
  retained evidence.

Even then, remove write paths before readers. Keep v1 lock reading and legacy
state/package import for a longer recovery window. Do not remove
`skill.yaml` permission or provider metadata that remains authoritative for
SDK/runtime policy; only stop using it as an implicit semantic capability.

## Program Continuation

The originally planned continuation is now validated locally:

1. Builder/Application APIs expose native compilation, viability, exact
   evidence explanations, and the read-only lifecycle projection;
2. 52 installed skills are classified; the curated Gmail package is now the
   first native reusable CBS provider, while the other classifications remain
   inventory rather than implied contracts;
3. `CBS6` freshness and external dependency invalidation are executable;
4. `CBS7` proves `booking.reserve` concurrency, idempotency, partial effects,
   compensation, evidence admission, and planning;
5. `CBS8` rebuilds semantic/impact/viability projections and advisory Evolver
   observations from canonical records;
6. `CBS9` consumes the original CBS5 telemetry and publishes a matched bounded
   benchmark. Its primary cost-reduction claim is not supported by the current
   Application-local inventory.

The next delivery step is therefore to expand native capability curation only
where independent consumers justify it, then prove thin online resolution,
resolved offline bundles, and registry explanations. The matched benchmark is
rerun only after that usable inventory exists. Builder automatic migration
remains a later, separately qualified program.

## Verification

The primary proof is
`tests/test_capability_binding_state_e2e.py`. Supporting suites cover portable
contracts, local identity projection, resolver ordering and failures, exact
planning, phase-fault rollback, crash recovery, package integrity, Resource
Workbench behavior, Application compatibility, relational storage, access,
and migration.

Standard local proof artifacts belong under `e2e/artifacts/`; they are run
evidence, not source authority and are not committed as architecture records.
