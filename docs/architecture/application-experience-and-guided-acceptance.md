# Application Experience, Guided Acceptance, and Cooperative Evolution

Status: target architecture.

Last reviewed: 2026-09-30.

This document defines the user-facing projection of Application development,
runtime selection, acceptance, publication, demonstration, and cooperative
improvement. It turns the lower-level DEV, Trial, prerelease, stable, Builder,
Development Signal, and package contracts into one comprehensible Application
experience without creating a second lifecycle authority.

Canonical Application identity, releases, publication, installation, and
publisher authority remain owned by
[Application Lifecycle, Distribution, and Feedback](application-lifecycle-and-distribution.md).
Builder sessions and candidate realization remain owned by the Builder
architecture. Development Signals and Dev Tickets remain owned by
[Development Signals and Evolution Feedback](development-signals.md). Package
materialization and activation remain owned by
[Artifact Source, Package, and Activation](artifact-source-package-activation.md).
This document owns their Application-facing projection, the guided-acceptance
companion, and the cooperation seams between them.

Implementation order is owned by the
[Application Experience and Guided Acceptance Roadmap](application-experience-and-guided-acceptance-roadmap.md).

## Decision Summary

1. Applications presents one compact instrument panel with two user-facing
   scopes: **Private** and **Public**. These labels explain availability; they
   do not redefine canonical visibility or authorization.
2. The panel projects five possible positions: `Private Alpha`, `Private Beta`,
   `Private Stable`, `Public Beta`, and `Public Stable`. A position is rendered
   only when its underlying artifact exists.
3. `Alpha` means the mutable Preview/Builder plane. It is not a new release
   channel and is never selectable as an installed runtime.
4. Runtime selection is independent from development progress. Builder never
   silently replaces a user's explicit runtime choice.
5. Beta acceptance is a guided, digest-bound decision. The live walkthrough
   ends with exactly `Accept Beta` or `Open for revision`; generic `Revise`
   controls do not appear on the lifecycle panel.
6. Every guided or recorded walkthrough uses package-owned synthetic data.
   Operational verification against user data is a separate, private process.
7. Lightweight text (`What's New` plus an executable Change Story) is the
   iterative acceptance source. Video is rendered from the same story only
   when preparing publication.
8. Mock and user data are interchangeable binding profiles for one semantic
   Application contract. They must not produce divergent product logic.
9. Adaptation is cooperation-first: a user may adapt locally, share an accepted
   contribution with the Publisher, join maintenance, or create an explicitly
   related continuation. No path silently changes publisher authority.
10. Contribution provenance and value observations are retained, but economic
    allocation, settlement, currency, royalties, or ownership formulas remain
    outside this architecture until the Evolnomics evidence gates permit them.

## Vocabulary and Canonical Mapping

The compact UI vocabulary is a projection, not storage vocabulary:

| UI position | Canonical source | Meaning |
| --- | --- | --- |
| `Private Alpha` | mutable DEV preview and current Builder context | work in progress available to its authorized developer; no immutable release implied |
| `Private Beta` | immutable local Trial/candidate release | accepted or awaiting acceptance in the publisher's private development space |
| `Private Stable` | accepted immutable release retained by the Publisher | stable private source; not evidence of Marketplace publication |
| `Public Beta` | Publisher prerelease | exact published Beta digest offered for broader opt-in and feedback |
| `Public Stable` | Marketplace stable release | exact promoted public Beta digest approved for general distribution |

`Private` means “available in your authorized development space.” It is not a
replacement for `application.visibility`, ACLs, Trial access grants, or
package authorization. `Public` means distributed through a publisher-owned
prerelease or Marketplace channel; it does not make private Application data
public.

Internal names such as Trial, Candidate, prerelease, release digest, and
Workspace activation remain visible in diagnostics and evidence. They need not
be taught to ordinary users before they can understand the panel.

## Application Instrument Panel

The Application summary is a state relationship, not a flat property list. It
SHOULD render a compact grouped table or equivalent responsive instrument
panel:

| Scope | Position | Primary control | Lifecycle control |
| --- | --- | --- | --- |
| Private | Alpha | `Preview` | `Open in Builder` |
| Private | Beta | `Open` when active, otherwise `Switch and open` | `Accept Beta` is inside guided acceptance |
| Private | Stable | `Open` when active, otherwise `Switch and open` | publish exact release as `Public Beta` when one does not exist |
| Public | Beta | `Open` when active, otherwise `Switch and open` | Publisher: `Promote to Public Stable`; others: `My contribution` |
| Public | Stable | `Open` when active, otherwise `Switch and open` | new development begins in `Private Alpha` |

The panel follows these rules:

- absent positions are shown as unavailable only when that information helps
  explain the next step; it must not fabricate versions;
- each row normally has no more than two controls;
- `Open` and `Switch and open` are mutually exclusive forms of one adaptive
  control, not two adjacent actions;
- the target of every publication or promotion is named explicitly;
- a generic `Revise` action is omitted because Beta is already a revision
  cycle; `Open for revision` is offered after viewing the exact candidate;
- internal Trial links and package transport controls stay out of the ordinary
  Application panel;
- advanced diagnostics may disclose exact artifact and operation identifiers
  without flattening them into the primary summary.

The header MAY expose one `Runtime` selector containing only sources that
exist and are admissible for this user: Private Beta, Private Stable (for an
authorized Publisher), Public Beta, and Public Stable. Private Alpha is a
preview source and never appears in this selector.

## Lifecycle and Publication Rules

Private Alpha is reviewed in Preview or Builder. Acceptance remains in that
context so that semantic intent, implementation evidence, and unresolved
questions remain visible. Applications displays the result; it does not create
a second Alpha approval flow.

Accepting an immutable Private Beta creates or updates the corresponding
Private Stable release. If the Publisher's policy enables publication after
acceptance, the same exact accepted digest is also published as Public Beta.
Without that policy, publishing it is an explicit action. Publication must
never rebuild the accepted artifact.

Public Stable is promoted from an exact Public Beta digest. This provides one
publicly observable proving ground and prevents a different private build from
being labelled as the release that users tested. If only Private Stable exists,
the next publication destination is Public Beta, not Public Stable.

Public Stable starts the next development cycle in a new Private Alpha; it is
not mutated in place. Historical positions remain addressable by digest for
audit, rollback, walkthrough receipts, and contribution lineage.

## Runtime Selection and Builder Coordination

Development progress and the active runtime are separate state machines. A
newly ready Beta is information, not authority to replace the active source.

Each Application has an explicit runtime-follow policy:

- `Follow Builder`: switch to a newly admitted Builder Beta under the granted
  policy;
- `Ask before switching`: create a Pending Action such as “Beta is ready.
  Switch runtime?”;
- `Pinned`: preserve the selected source until the user resumes following.

A manual runtime choice pins the selection. `Resume following Builder` is an
explicit action. Builder may recommend a source and attach evidence, but cannot
silently undo the pin.

A switch is a governed activation operation, not a UI-only dropdown change. It
must declare source and target digests, compatibility, data migration or
state-space consequences, permission deltas, rollback, and result evidence.
Unavailable, stale, or unauthorized sources remain visible only with a clear
reason and no executable control.

## Participation Policy

Routine cooperation should be authorized as a process rather than interrupting
the user for every low-risk step. The Application presents a separate policy
block, for example:

- follow accepted Builder runtime updates;
- after Publisher acceptance, publish automatically to Public Beta;
- participate in improvements;
- share my accepted improvements with the Publisher;
- admit compatible updates within the approved permission and migration
  envelope.

Consent is scoped to an Application, actor, destinations, permissions, data
disclosure, risk envelope, and revocation rule. All automatic effects are
audited. A deviation from the admitted envelope, a material permission or data
migration change, an ambiguous Publisher destination, or a failed proof creates
a Pending Action rather than being silently widened.

For a user who is not the Publisher, `Publish` is replaced by user language
such as `Share with Publisher`. With consent, accepting a local Beta may
automatically create a contribution package. It never grants the contributor
publication authority over the upstream Application.

## Adaptation and Contribution

The ordinary UI uses `Adapt for me`, `My adaptation`, `Participate in
improvements`, and `Share accepted improvements with Publisher`. The technical
lineage may be implemented as a fork, but that term is reserved for diagnostics
and developer detail.

An accepted contribution package contains at least:

- upstream Application identity and exact base release digest;
- contributor and receiving Publisher references allowed by policy;
- semantic requirement and affected component references;
- semantic, UI, permission, data, and migration diffs;
- tests, Change Story, acceptance receipt, and relevant execution evidence;
- compatibility and known limitation declarations;
- provenance for the human need, Builder/Codex realization, review, fixes,
  localization, diagnosis, and acceptance.

It must not contain personal records, runtime secrets, connected-account
credentials, or unrelated conversation context. The Publisher imports the
proposal into its own Builder context. Rebase, conflict resolution, acceptance,
and publication remain explicit publisher-governed operations.

Development Feedback and Dev Tickets are the lightweight circulation layer;
an executable contribution package is the bounded candidate layer. The two
must link by stable identifiers so a remote Builder can retrieve only the
needed context and evidence.

## Publisher Collaboration and Application Families

One Application identity has one Publisher authority at a time. The Publisher
may itself be a governed team with delegated maintainers, but independent
publishers never write the same identity concurrently.

The cooperation ladder is:

1. personal adaptation;
2. share an accepted improvement;
3. join or help the Publisher maintain the Application;
4. governed Publisher succession;
5. independent community continuation when cooperation is unavailable.

Publisher succession preserves Application identity through a signed,
auditable transfer containing old and new Publisher authorities, key epoch,
effective time, recovery policy, and user-visible notice. It is not inferred
from inactivity.

A continuation creates a new Application identity and records
`continuation_of`, the exact upstream digest, license/adaptation policy, and
provenance. It never masquerades as the original or inherits subscriptions
without user consent. A claim that an Application is abandoned requires
bounded reachability attempts, inactivity evidence, security posture, license
compatibility, and governance review. Continuation can proceed under its own
identity while a succession request is pending.

Catalog may group related Applications into an `ApplicationFamily` containing
the original, a verified successor, community continuations, and compatible
alternatives. Each member retains independent identity, Publisher, channels,
permissions, reviews, and security evidence. A family card presents one
explainable recommended path and a restrained alternatives view comparing:

- Publisher and succession/continuation status;
- maintenance freshness and support posture;
- permissions and data disclosure differences;
- compatibility and migration cost;
- provenance, verification, and security evidence.

Installed Applications show alternatives as auxiliary information, becoming
prominent only under maintenance or security risk. Choosing another Publisher
is an explicit migration with preview and rollback; it is not another value in
the runtime source selector.

## Application Story Contract

An Application has a durable, portable companion named
`adaos.application.change_story.v1`. This name is intentionally distinct from
the conversational `story` contract. It has two projections:

- **State Story**: what the Application currently does;
- **Change Story**: what changed between exact base and target candidates.

The contract includes:

- Application identity, story revision, and content digest;
- exact base and target Trial/Release/package digests;
- requirement, issue, Development Signal, and acceptance references;
- ordered chapters and steps with stable view, control, command, and semantic
  resource references;
- mock setup, action, expected visible state, narration, limitations, and
  disclosure text;
- feedback anchors and expected evidence at each step;
- locale-independent text source plus localized narration/subtitle refs;
- renderer and compatibility requirements.

Builder authors the story as part of candidate realization. Core validates its
references, digest closure, mock-only disclosure, and package binding. Client
executes semantic navigation, highlighting, narration, pause/repeat, chapter
seeking, and anchored feedback. Applications chooses the relevant story;
distribution surfaces expose its text and the applicable recorded projection.

Feedback records the candidate and story digests, chapter/step, semantic UI
reference, observed state, and human comment. It becomes scoped Development
Feedback or a Dev Ticket rather than an unstructured global prompt.

## Guided Acceptance

The live walkthrough executes the exact Trial package in a dedicated
demonstration runtime and explains it with synthetic data. At completion it
offers exactly:

- `Accept Beta`;
- `Open for revision`.

`Open for revision` opens Builder with the exact Application, candidate, story,
step feedback, evidence, and relevant Development Tickets. It does not ask an
LLM to reconstruct context from a screenshot or a generic complaint.

Guided acceptance validates intended behavior and experience. It does not
replace permission verification, migration qualification, real-provider
conformance, security review, or operational checks. Acceptance receipts bind
the actor, decision, exact candidate and story digests, relevant evidence, and
time. Watching a Marketplace video is not acceptance.

The same companion is surfaced according to context:

- Private Alpha: guided preview, with acceptance remaining in Preview/Builder;
- Private Beta: review and accept the Beta;
- Public Beta: understand what is being tested, opt in, and provide feedback;
- stable update: inspect `What's New` before switching;
- installed stable: revisit the State Story for education;
- Marketplace: watch the recorded demonstration before installation.

## What's New and Recorded Demonstration

`What's New` is a first-class text section beside README/Overview and Versions.
It is available immediately during iteration and contains the concise summary,
chapter list, limitations, compatibility notes, and explicit synthetic-data
disclosure. `Watch video` is a secondary action, not a replacement for text.

Video is generated only for a publication candidate. A deterministic recorder
drives the actual Client through the exact Change Story and mock package. It
must not synthesize unrelated screens or personal content. The publication
record binds the video to story, Trial/package, Client renderer, locale,
viewport, and creation digests. A relevant change marks it outdated.

The video bundle contains separate timed subtitle tracks, transcript, chapter
and seek markers, and optional locale-specific narration tracks. Translation
or redubbing can reuse the visual stream when its bound UI digest is unchanged.
User chrome, notifications, identities, secrets, and personal data are removed
or replaced before capture.

Public Beta distributes the companion through its direct or explicit opt-in
prerelease surface. Public Stable distributes it through Marketplace.
Marketplace does not need global prerelease search or ranking for this first
path. These surfaces distribute text and video, not the interactive
demonstration runtime; interactive Marketplace projection is deferred.

## Demonstration Runtime and Retention

One demonstration runtime directory and one serialized runner are sufficient.
The Trial package carries the Application, mock bindings and data, Application
Story, and narration/subtitle sources. The runner:

1. acquires an exclusive lease;
2. verifies all package and story identities;
3. hydrates the exact package into the demonstration runtime;
4. executes the story or recorder;
5. emits evidence and feedback anchors;
6. removes runtime material after completion.

This process does not install the Application into the user's Trial or
Workspace and never changes their selected runtime. The directory is ephemeral;
the content-addressed Trial package is retained while its Beta is current and
while acceptance, feedback, video, audit, or rollback records reference it.
Story or mock-data changes create a new digest.

## Mock and User Binding Profiles

Replaceability between synthetic and user data is a core development
principle. Applications are built against semantic data, media, query,
operation, and activity contracts with at least two binding profiles:

- `demonstration`: deterministic synthetic records, assets, identities,
  failures, and effects packaged with the candidate;
- `user`: authorized runtime adapters, state spaces, providers, credentials,
  and real effects.

Both profiles expose the same schemas, semantic views, commands, result shapes,
and expected outcomes. Application logic must not fork into a separate mock
product. Profile-specific bindings may emulate time, errors, permissions, or
provider latency, but those differences are declared and tested.

The demonstration profile includes representative positive, empty, validation,
error, loading, and permission-denied states. Synthetic media are package-owned
assets. It never references personal provider accounts or secrets. User runtime
storage never mixes synthetic records into real state. Operational verification
uses private user bindings without narration, recording, or content export.

Mock data and story sources remain available until the corresponding Beta is
obsolete, subject to longer evidence and audit holds. This permits repeatable
acceptance, regression reproduction, translation, and publication recording.

## User-Managed Presentation Variants

An Application may expose bounded presentation choices without requiring a
source rewrite or a personal code fork. A release-owned
`PresentationVariantSet` declares stable variant identifiers and typed options
such as density, grouping, list/cards/related-tables strategy, information
priority, and admitted compact/wide behavior. A user-owned
`PresentationPreference` selects among those declared options for a Webspace,
device class, or accessibility profile.

Variants share the same semantic resources, stable control/action refs,
permissions, commands, data contracts, and lifecycle. They may change layout
or emphasis, but cannot hide required disclosures, bypass access checks,
change business meaning, or create incompatible state. Client falls back to a
release-declared default when a preference or variant is unavailable.

Builder and the semantic compiler should generate and validate declared
variants together. Guided stories address semantic refs rather than screen
coordinates so they survive an admitted layout choice. User preferences stay
outside immutable release content and are not silently published with a
contribution; a user may explicitly share a portable, non-personal preference
proposal with the Publisher.

Arbitrary CSS, executable UI plugins, model-generated per-render layouts, and
unbounded component substitution are not part of this contract. This keeps
customization understandable, accessible, testable, and compatible with
repeatable acceptance.

## Cooperation-First Value Observation

The system should maximize cooperation and preserve useful provenance without
prematurely turning activity into a reward mechanism. It records distinct
contributions such as articulating a need, accepting and testing behavior,
realizing a change, correcting it, curating a release, diagnosing a failure,
and translating or documenting it.

The following remain separate:

- historical attribution;
- current governance or Publisher authority;
- observed use or benefit;
- any future economic entitlement.

Before the Evolnomics Gate A2, only shadow observability and attribution are
allowed. No currency, payout, royalty, ownership, ranking reward, or value
allocation formula is selected here. Raw commits, tokens, ticket counts, or
usage events must not become direct value proxies. Future paid-Application
experiments require explicit defenses against Sybil identities, collusion,
usage inflation, lineage tampering, idea squatting, perpetual
micro-entitlements, and governance capture.

## Authority, Privacy, and Safety Invariants

1. The canonical lifecycle and release records remain authoritative; this UI
   projection cannot invent or mutate them.
2. One Application identity has one Publisher authority at a time.
3. A runtime switch, acceptance, publication, promotion, contribution import,
   succession, or migration is a durable governed operation with exact digests.
4. Manual runtime selection is never silently overridden by Builder.
5. Public Stable is promoted from the exact Public Beta digest that was
   evaluated.
6. Guided and recorded walkthroughs contain synthetic data only.
7. Operational verification with user data is private and non-recording.
8. Acceptance of experience does not imply security, permission, migration, or
   provider-conformance acceptance.
9. Contribution packages exclude secrets and personal data by construction.
10. Related Applications preserve independent identity and Publisher
    authority; alternatives never masquerade as updates.
11. Automation may act only inside a revocable, audited policy envelope;
    material deviations become Pending Actions.
12. Attribution never silently becomes authority or economic entitlement.

## Explicitly Deferred

- distribution of interactive walkthrough runtimes through Marketplace;
- narrated or recorded demonstrations over personal/user data;
- multiple concurrent demonstration runtimes before one leased runner proves
  insufficient;
- silent cross-Publisher migration or automatic continuation takeover;
- broad abandoned-project arbitration before signed succession and explicit
  continuation paths are proven;
- general-purpose ranking of Application-family alternatives;
- economic allocation, settlement, currency, royalties, paid-Application
  revenue sharing, or ownership formulas before Evolnomics evidence gates.
