# Application Experience and Guided Acceptance Roadmap

Status: target implementation roadmap.

Last reviewed: 2026-09-30.

Target architecture:
[Application Experience, Guided Acceptance, and Cooperative Evolution](application-experience-and-guided-acceptance.md).

This roadmap owns the Application-facing lifecycle projection, runtime-choice
coordination, guided acceptance, mock-backed demonstration, recorded
walkthrough, and cooperative contribution experience. Canonical release and
publication mechanics remain in the Application Lifecycle roadmap; Builder,
Development Signals, Pending Actions, package activation, and Client work stay
in their owning roadmaps and expose the contracts required here.

## Priority Model

- `[must]`: blocks a coherent Application dashboard and guided Beta acceptance
  proof;
- `[should]`: required before repeated publisher/contributor use or broad
  public Beta operation;
- `[could]`: useful extension that does not block the primary proof;
- `[deferred]`: deliberately postponed until its named evidence gate.

Limits are design targets, not information-loss mechanisms. If a compact UI,
context budget, or payload limit would discard material evidence, the system
retains the evidence and exposes a summary plus drill-down or retrieval path.

## Sequencing Rules

1. Freeze the projection and story contracts before generating UI or video.
2. Reuse canonical lifecycle, release, activation, and publication operations;
   do not implement lifecycle truth in the Client.
3. Prove one leased demonstration runtime before introducing concurrency.
4. Prove text stories and live walkthroughs before recording video.
5. Prove mock/user binding conformance before calling guided acceptance
   representative.
6. Prove one Publisher contribution loop before succession or family ranking.
7. Retain cooperation provenance now; defer economic mechanisms until
   Evolnomics Gate A2 authorizes an experiment.

## AEX0. Contract and Vocabulary Freeze

### AEX0-01

- [ ] `[must]` Define the browser-safe lifecycle projection contract for the
  five optional positions, exact source/release digests, availability reasons,
  active runtime, admissible actions, and policy summary.
- Evidence: schema tests and fixtures covering missing, stale, unauthorized,
  active, accepted, published, and promoted states.

### AEX0-02

- [ ] `[must]` Map `Private Alpha/Beta/Stable` and `Public Beta/Stable` to the
  existing DEV, Trial, publisher prerelease, and Marketplace stable records
  without adding a competing lifecycle state store.
- Evidence: deterministic projection tests from canonical records.

### AEX0-03

- [ ] `[must]` Freeze `adaos.application.change_story.v1`, State Story, Change
  Story, feedback-anchor, acceptance-receipt, and recorded-demonstration
  envelopes with digest closure and version negotiation.
- Evidence: JSON/schema validation, forward-compatibility fixtures, and invalid
  reference rejection.

### AEX0-04

- [ ] `[must]` Define the `demonstration` and `user` binding-profile contract,
  synthetic-data marker, package-owned media rule, and provider-conformance
  boundary.
- Evidence: paired fixtures prove identical semantic schemas and operations.

### AEX0-05

- [ ] `[must]` Add terminology and ownership checks preventing `Alpha` from
  becoming a release channel, `Private/Public` from replacing access policy,
  or Application Story from colliding with conversational stories.
- Evidence: documentation lint/contract review and negative tests.

## AEX1. Compact Application Instrument Panel

### AEX1-01

- [ ] `[must]` Replace the flat Application lifecycle summary with a responsive
  two-scope instrument panel rendering only factual positions and versions.
- Evidence: wide and compact Client screenshots plus projection fixtures.

### AEX1-02

- [ ] `[must]` Implement adaptive `Open` versus `Switch and open`, explicit
  publication destinations, Private Alpha `Preview`/`Open in Builder`, and no
  generic `Revise` control.
- Evidence: action-admission and browser interaction tests for every position.

### AEX1-03

- [ ] `[must]` Add the bounded Runtime selector using only existing admissible
  sources; exclude Alpha and explain unavailable sources without executable
  controls.
- Evidence: role/source matrix and accessibility tests.

### AEX1-04

- [ ] `[should]` Add advanced digest, operation, migration, and evidence detail
  as progressive disclosure without crowding the primary panel.
- Evidence: compact-layout usability review and deterministic detail links.

### AEX1-05

- [ ] `[should]` Add `What's New` beside Overview/README and Versions, with an
  optional `Watch video` action only when a valid bound recording exists.
- Evidence: Client route tests for Alpha, Beta, stable update, and Marketplace.

### AEX1-06

- [ ] `[should]` Define release-owned `PresentationVariantSet` and user-owned
  `PresentationPreference` contracts for bounded density, grouping,
  list/cards/related-tables, information priority, responsive, and accessibility
  choices without changing semantic refs, permissions, commands, or data.
- Evidence: schema and fallback tests across at least two declared variants.

### AEX1-07

- [ ] `[should]` Make semantic compilation, story playback, and Client
  validation operate over stable semantic refs across all declared variants;
  reject variants that hide mandatory disclosures or actions.
- Evidence: the same Change Story passes wide/compact and two presentation
  variants without coordinate-specific steps.

### AEX1-08

- [ ] `[could]` Allow a user to export an explicit non-personal presentation
  preference proposal to the Publisher without including it automatically in
  a contribution package.
- Evidence: privacy-safe proposal and Publisher import fixture.

## AEX2. Runtime Choice and Autonomous Policy

### AEX2-01

- [ ] `[must]` Persist `Follow Builder`, `Ask before switching`, and `Pinned`
  policies per Application and actor; make manual selection pin the runtime.
- Evidence: restart-safe policy and compare-and-swap tests.

### AEX2-02

- [ ] `[must]` Route Builder switch recommendations through Pending Actions
  when policy requires a decision, including exact source/target digests,
  compatibility, permissions, migration, and rollback summary.
- Evidence: “Beta is ready. Switch runtime?” local and routed response E2E.

### AEX2-03

- [ ] `[must]` Implement runtime switching as a governed activation operation,
  not a Client preference mutation, and preserve explicit rollback evidence.
- Evidence: success, denial, stale-CAS, migration failure, and rollback tests.

### AEX2-04

- [ ] `[must]` Add scoped, revocable, audited policies for post-acceptance Public
  Beta publication and accepted-contribution sharing; deviations create
  Pending Actions.
- Evidence: policy-envelope tests and audit records for automatic and stopped
  operations.

### AEX2-05

- [ ] `[should]` Present autonomy and participation policy separately from
  lifecycle actions with plain-language scope, effect, and revocation.
- Evidence: EN/RU copy and comprehension review.

## AEX3. Text Story and Guided Acceptance

### AEX3-01

- [ ] `[must]` Make Builder emit a digest-bound Change Story and `What's New`
  text from accepted semantic requirements, implementation evidence, and known
  limitations for every Beta candidate.
- Evidence: repeatable Builder fixture and exact provenance links.

### AEX3-02

- [ ] `[must]` Validate story steps against packaged semantic view, control,
  command, and resource references; reject invented or stale UI targets.
- Evidence: positive and negative Core validation tests.

### AEX3-03

- [ ] `[must]` Implement Client story playback with chapters, semantic
  navigation/highlighting, narration text, pause/repeat, mock disclosure, and
  step-bound feedback.
- Evidence: deterministic browser E2E across compact and wide layouts.

### AEX3-04

- [ ] `[must]` End live Beta review with exactly `Accept Beta` and `Open for
  revision`; bind the decision to candidate/story/evidence digests.
- Evidence: acceptance receipt and denial/stale-candidate tests.

### AEX3-05

- [ ] `[must]` Open Builder revision with exact Application, candidate, story,
  failed/annotated step, Development Signal/Dev Ticket, and relevant evidence
  context rather than a reconstructed free-form prompt.
- Evidence: context-packet inspection and remote Builder MCP retrieval proof.

### AEX3-06

- [ ] `[should]` Reuse State Story as installed-Application education and the
  Change Story for stable-update review without treating video viewing as
  acceptance.
- Evidence: context-specific action and receipt tests.

## AEX4. Demonstration Runtime and Binding Conformance

### AEX4-01

- [ ] `[must]` Add one exclusive-lease demonstration runner and runtime
  directory that hydrates an exact Trial package, verifies identity, emits
  evidence, and cleans up without changing installed runtime selection.
- Evidence: collision, crash recovery, cleanup, and runtime-isolation tests.

### AEX4-02

- [ ] `[must]` Package deterministic synthetic data and media covering positive,
  empty, loading, validation, error, and permission-denied states.
- Evidence: package inventory and replayable state fixtures.

### AEX4-03

- [ ] `[must]` Add automated conformance comparing demonstration and user
  profiles for schemas, queries, commands, result shapes, and semantic UI
  bindings while keeping effects and credentials separate.
- Evidence: paired adapter contract suite and divergence rejection.

### AEX4-04

- [ ] `[must]` Enforce synthetic-only narration/recording and separate private,
  non-recording operational verification over user bindings.
- Evidence: data-classification, redaction, and forbidden-binding tests.

### AEX4-05

- [ ] `[must]` Retain content-addressed mock/story packages while a Beta is
  current or referenced by acceptance, feedback, video, audit, or rollback;
  allow the hydrated runtime directory to remain ephemeral.
- Evidence: retention/GC tests with every hold type.

## AEX5. Publication Demonstration

### AEX5-01

- [ ] `[must]` Record the actual Client deterministically from the exact
  publication candidate, story, and demonstration profile immediately before
  publication, not on every Builder iteration.
- Evidence: recorder E2E and digest-bound recording manifest.

### AEX5-02

- [ ] `[must]` Produce separate timed subtitles, transcript, chapters/seek
  markers, and optional narration tracks; support translation/redubbing while
  the bound visual digest remains valid.
- Evidence: at least two locale tracks and invalidation tests.

### AEX5-03

- [ ] `[must]` Remove personal chrome, notifications, identities, secrets, and
  non-synthetic content from capture, failing closed when provenance is
  uncertain.
- Evidence: privacy scanner fixtures and negative publication tests.

### AEX5-04

- [ ] `[must]` Publish text plus video metadata with Public Beta/Stable, expose
  Public Beta through a direct or explicit opt-in prerelease detail, and expose
  Public Stable in Marketplace without shipping the interactive demo runtime
  or enabling global prerelease discovery.
- Evidence: clean-subnet opt-in Beta and Marketplace stable playback plus text
  fallback proof.

### AEX5-05

- [ ] `[should]` Add accessibility validation for narration, captions, reduced
  motion, keyboard chapter navigation, and text-only equivalence.
- Evidence: automated checks and manual accessibility review.

## AEX6. Cooperative Contribution Loop

### AEX6-01

- [ ] `[must]` Define the accepted contribution package with upstream digest,
  semantic/component diffs, tests, story, permission/migration changes,
  evidence, and privacy-safe provenance.
- Evidence: schema tests and secret/personal-data rejection.

### AEX6-02

- [ ] `[must]` Connect `My contribution` and `Share with Publisher` to
  Development Signals/Dev Tickets and Publisher Builder intake using bounded
  MCP retrieval rather than broad context export.
- Evidence: contributor-to-Publisher round-trip on separate subnets.

### AEX6-03

- [ ] `[must]` Prove Publisher import, conflict/rebase presentation, review,
  acceptance, and publication while preserving upstream and contributor
  provenance.
- Evidence: conflicting and clean contribution E2E cases.

### AEX6-04

- [ ] `[should]` Implement process-level consent for automatic sharing of an
  accepted adaptation with clear scope, revocation, destination, and audit.
- Evidence: opt-in, revoke, destination-change, and policy-deviation tests.

### AEX6-05

- [ ] `[should]` Add Publisher-side grouped feedback/contribution views and
  status return to contributors without disclosing Publisher-private context.
- Evidence: multi-contributor privacy and status tests.

## AEX7. Publisher Continuity and Application Families

### AEX7-01

- [ ] `[should]` Add delegated maintainer membership for one Publisher
  authority with explicit roles, quorum/recovery policy, and audit.
- Evidence: authority and key-rotation tests.

### AEX7-02

- [ ] `[should]` Implement signed Publisher succession preserving Application
  identity, old/new authority, key epoch, notices, subscriptions, and rollback
  constraints.
- Evidence: transfer, denial, recovery, and clean-client verification.

### AEX7-03

- [ ] `[should]` Implement explicit community continuation with a new
  Application identity, `continuation_of`, exact upstream digest, license, and
  provenance; never inherit subscriptions automatically.
- Evidence: Catalog lineage and explicit migration proof.

### AEX7-04

- [ ] `[could]` Add `ApplicationFamily` projection and restrained comparison of
  Publisher status, freshness, permissions, compatibility, migration,
  provenance, and security evidence.
- Evidence: original/successor/continuation/alternative fixtures and explainable
  recommendation output.

### AEX7-05

- [ ] `[could]` Surface alternatives prominently only for maintenance or
  security risk and require an explicit governed migration to change Publisher.
- Evidence: risk-state and migration browser E2E.

## AEX8. Shadow Value Observation

### AEX8-01

- [ ] `[could]` Retain privacy-safe, tamper-evident provenance for need
  articulation, realization, testing, correction, curation, diagnosis,
  localization, and acceptance without assigning economic value.
- Evidence: lineage queries and deletion/privacy policy tests.

### AEX8-02

- [ ] `[deferred]` Evaluate shadow value hypotheses only after the Evolnomics
  Gate A2, including Sybil, collusion, usage-inflation, lineage-tampering,
  idea-squatting, micro-entitlement, and governance-capture threats.
- Evidence: separately approved experiment protocol and red-team results.

### AEX8-03

- [ ] `[deferred]` Define any paid-Application allocation, settlement,
  currency, royalty, ownership, or revenue-sharing mechanism.
- Evidence gate: explicit Evolnomics decision; this roadmap supplies no default.

## Deferred Expansion

- [ ] `[deferred]` `AEX-D01` Distribute interactive walkthrough runtimes through
  Marketplace; text and video remain the distribution contract.
- [ ] `[deferred]` `AEX-D02` Narrate or record user/personal data; operational
  verification remains private and non-recording.
- [ ] `[deferred]` `AEX-D03` Add concurrent demonstration runtime pools before
  measurement shows the single leased runner is insufficient.
- [ ] `[deferred]` `AEX-D04` Perform silent cross-Publisher migration, automatic
  takeover, or subscription reassignment.
- [ ] `[deferred]` `AEX-D05` Add general Application-family ranking or abandoned
  Publisher arbitration before succession and continuation proofs.
- [ ] `[deferred]` `AEX-D06` Treat raw commits, tokens, tickets, usage, or model
  output volume as contribution value.
- [ ] `[deferred]` `AEX-D07` Admit arbitrary CSS, executable UI plugins,
  model-generated per-render layouts, or unbounded component substitution as
  user customization.

## End-to-End Exit Gate

The initial roadmap is proven only when one Application can demonstrate this
exact flow:

1. Builder produces a Private Alpha using contract-compatible synthetic and
   user binding profiles.
2. Preview executes its text Change Story over synthetic data.
3. Builder materializes an immutable Private Beta with exact story and package
   digests.
4. Applications shows the correct compact lifecycle positions and does not
   override a pinned runtime.
5. Guided acceptance runs in the isolated demonstration runtime and returns
   either a digest-bound acceptance or exact-step revision context.
6. Acceptance produces Private Stable; policy either publishes or asks before
   publishing the same digest as Public Beta.
7. A clean participant selects Public Beta, views `What's New`, supplies
   anchored feedback, and can share an accepted adaptation with the Publisher.
8. Publisher imports and accepts the contribution with provenance intact.
9. The exact tested Public Beta digest is promoted to Public Stable.
10. Marketplace presents text and the privacy-qualified recorded walkthrough;
    a clean subnet installs it while all audit, rollback, and retention holds
    remain verifiable.
