# Builder Roadmap

Status: high-level sequencing for the AdaOS Builder vertical slice.

Last reviewed: 2026-09-05.

This roadmap tracks how AdaOS evolves from separate skill/scenario/runtime
surfaces into a coherent Builder workflow: idea -> governed artifact -> preview
-> validation -> activation -> observation -> repair.

Detailed implementation remains in the specialized roadmaps. This page is the
cross-cutting source of truth for Builder readiness.

The [Governed Evolution Roadmap](governed-evolution-roadmap.md) places Builder
inside the larger managed-deployment, Issue-first repair, and trusted-reuse
sequence. It references Builder gates but does not duplicate this checklist.

The 2026-07-24 package-pipeline slice is `validated-local`: Builder itself was
advanced by the built-in LLM and isolated Codex, checkpointed as scenario
`0.2.20` plus skill `0.1.28`, trialed, accepted, and materialized into
Workspace. The exact evidence is recorded in
[Artifact Pipeline Local Evidence](artifact-pipeline-local-evidence-2026-07-24.md).
Clean-stand repetition remains open, so this is not a production-acceptance
claim.

The 2026-07-28 change-oriented slice is `validated-local`: scenario `0.2.28`,
control skill `0.1.36`, and interactive `builder_skill` `0.3.13` were advanced
through one bounded change set, isolated Automation, Forge checkpoints, an
accepted dependency-locked trial, stable Publication, and exact Workspace
materialization. It adds issue lanes and gates, dependent Lifecycle lineage,
and exact `proto:`/`active:`/`public:` target selection. The accepted candidate
was `builder-0-2-28-940229ddbf49`, with release digest
`sha256:ad74d7a6ccec5d4787e793ff244a5bc08ce682db12dd4cc7b5928714278ab4df`.
Clean-stand and production-soak repetition remain open, so this is not a
production-acceptance claim.

The 2026-07-29 non-Builder slice is `validated-local`: `test05_recipes` was
created from an empty scenario, advanced through one canonical Change and
three Prototype revisions produced by the built-in LLM, approved for isolated
Automation, implemented by the local Codex worker, trialed, published, and
materialized into Workspace. Stable release `test05_recipes@0.1.4` locks the
companion `test05_recipes_skill@0.1.1`; WorkspaceLock revision `10` completed
reload and exact health verification. The `proto:` / `active:` / `public:`
Preview bindings were checked independently in the nested Builder preview
workspace. Human wide/compact and mutating Telegram-callback acceptance remain open, so
this is still a local architecture proof rather than production acceptance.

The 2026-07-29 limited-channel routing slice is `validated-local`: Telegram
pairing and bindings can carry an explicit Webspace, DEV manifests select the
DEV skill runtime without a stale Workspace-first fallback, and the same route
is preserved through the node conversation ledger. A UTF-8 HTTP-fallback
acceptance turn against `dev1-dev` produced the DEV Builder reply in 3.052 s.
This proves the local transport/runtime boundary; it does not replace the open
live bot/backend deployment gate.

The 2026-07-30 Builder self-hosting slice is `published-local`: Prototype
`UI 058` was stabilized, implemented once by isolated Codex, recovered from
its confirmed checkpoint without rerunning the mutating command, and promoted
as `builder@0.2.55`. The accepted candidate
`builder-0-2-55-a3c36bcaf45e` locks `builder_sdk_control_skill@0.1.58`,
`builder_skill@0.3.24`, and `voice_chat_skill@0.6.17`; WorkspaceLock revision
`11` completed reload and exact health verification. A restart audit then
exposed an obsolete post-boot git-sync path inside release-owned Workspace;
the same three installed releases were recovered from durable promotion
receipts into WorkspaceLock revision `3` with unchanged Builder and dependency
digests. The post-boot worker now consumes the materialized lock without git
sync, skips projection rebuild when there are no runtime changes, and leaves
the active A/B slot available until prepare succeeds. An earlier candidate was
rejected because it resolved stale companion versions, proving that the
dependency-lock gate fails closed. Exact `proto:` / `active:` / `public:`
Preview selection was repeated against the isolated nested Preview. This is a
local publication and transition to the current Workspace edition, not yet a
clean-stand or multi-user production acceptance claim.

## Reading Rules

- [Builder](builder.md) defines the role and architecture boundary.
- [Builder Conversational Development Architecture](builder-conversational-development.md)
  defines the chat-first product model, canonical Issue/Change/Run terms,
  context packets, semantic UI changes, and future collaboration seams.
- [Conversational Control Interface](conversational-interface.md) defines the
  shared conversational input/output contract, NLU data boundary,
  Teacher-to-Builder promotion path, and conversation-story testing model.
- [Development Signals And Evolution Feedback](development-signals.md) defines
  feedback intake, scoped signal storage, Dev Tickets,
  autonomous-versus-interactive Builder handoff, and the boundary with NLU
  Teacher. It also owns Builder intake qualification, SDK Understanding
  Signals, and Core Dev Tickets for platform evolution requested from project
  repair work.
- [Builder SDK Boundary](builder-sdk-boundary.md) defines the public SDK
  dependency direction and tracks the functional replacement-control slice.
- [Navigation Intent And Location](navigation-intent-and-location.md) defines
  cross-zone/subnet/Webspace links shared by AdaOS Connect, Builder, and other
  skills.
- Checked items mean an implementation slice exists, not necessarily full
  product maturity.
- Every checklist item carries a four-level MoSCoW-style priority label.
- Specialized roadmaps continue to own detailed sequencing for their domains.
- Builder milestones should link out to domain roadmaps instead of duplicating
  every low-level checklist.
- Builder works against public SDK/API/resource contracts. When a project task
  needs core, runtime, SDK, API, policy, observability, or lifecycle evolution,
  Builder creates or links a Core Dev Ticket and pauses/rescopes the project
  ticket instead of modifying core from the project repair context.

## Status Labels

Markdown checkboxes only distinguish done from not done. This roadmap uses a
four-level MoSCoW-style priority vocabulary for planned Builder work:

- `[must]`: first-order work required for the Builder architecture to be
  functionally coherent through the named phase.
- `[should]`: hardening, guidance, or workflow work that materially improves
  safety, operator confidence, or reuse, but can follow the main vertical
  slice if the `[must]` path remains coherent.
- `[could]`: useful optional ergonomics, diagnostics, or product polish that
  should not compete with `[must]` / `[should]` delivery.
- `[deferred]`: intentionally postponed until a later phase owns the contract,
  working loop, policy boundary, or user experience.

An unchecked `[should]`, `[could]`, or `[deferred]` item must not be counted as
a blocker for the next `[must]` implementation gate unless the gate explicitly
depends on it.

## MoSCoW Gate View

This view is the priority-layer projection of the phase checklist below. The
phase sections remain the canonical checklist; this table makes the current
gate easy to read by priority.

| Phase | `[must]` gate | `[should]` layer | `[could]` layer | `[deferred]` layer |
| --- | --- | --- | --- | --- |
| 0. Terminology | Complete: role name, executor-neutral wording, terminology anchor. | None. | Complete: future product glossary hook. | None. |
| 1. Context | Complete: Root MCP context, schemas, hints, redaction, descriptor sets. | Complete: descriptor freshness/provenance in task context. | None. | None. |
| 2. Task Model | Complete: task schema, Teacher candidate links, descriptor-fix materialization, lifecycle states. | None. | None. | Open: backlink from completed Builder task to originating candidate/idea. |
| 3. Draft Rails | Complete: draft contract, templates, CLI/API draft route, CTX dev artifact roots, Builder-aware scaffolds, template quality gates, dev lifecycle CLI facade. | Complete: scenario-specific Builder guidance and artifact listing ergonomics. | None. | None. |
| 4. Validation/Preview | Complete locally: preview bundle, static checks, route-budget validation, dependency-aware checkpoint/publish preflight, durable Forge receipt, and Builder validation facade. | Complete locally: blast radius, webui preview, scenario dependency bootstrap, and explicit reconciliation without automatic mutation replay. | None. | None. |
| 5. Human Review | Complete for the current Builder scope: approval profiles, policy evidence, Pending Actions producer/response migrations, durable Review anchors, approval identity, and reject/redirect dispositions are implemented. | Complete: browser Review packets are promoted to revision-aware Builder context and the public SDK exposes the full disposition lifecycle. | None. | Open: delegated Pending Actions subscription handshake. |
| 6. Activation | Complete for the current Builder scope: immutable release evidence, unique-version admission, durable apply/setup recovery, post-activation repair routing, and channel-neutral rollback plans are implemented. | Complete: rollback presentation separates inspection, confirmed mutation, and verification with stable idempotency/conflict keys. | Optional setup-assistant UX. | Automatic free-form setup programming remains postponed; Publication only accepts the declarative plan and tested setup tool. |
| 7. Repair Loop | Complete: guard/quarantine/post-activation/test/import/route/memory/NLU evidence becomes bounded Builder repair context with acceptance evidence. | Complete: repair deduplication and explicit supersession are implemented. | None. | None. |
| 8. Product Experience | Complete for the current Builder refactor: dedicated Builder scenario, durable Review context, declarative tabs/modal/page/multistep behavior ABI, and stable no-op projections are covered. | Complete: a 32-cycle equal-object reload soak preserves live Yjs root identity. | Open: richer Automation log and cross-project history views. | Open: autonomous reproduction, large-module decomposition, and legacy Prompt IDE retirement. |
| 9. Reference Runtime | Complete for the current reference slice: stable async job messages, bounded context/memory/repair evidence, broadened goldens, eval-to-repair backlinks, and a validated public generated-skill example exist. | Complete: the generated example demonstrates skill-owned conversation, consent-gated memory, Pending Action, and browser UI. | Open: optional model-backed repair graders. | None. |
| 10. Skill Factory | Complete for the single isolated-node scope: task-scoped access leases, User Hub validation/staging/approval, exact assignment, sparse path enforcement, and six golden outcomes are implemented. | Complete: success and failure rails are replayed through the real service contracts. | Open: multi-node pools and parallel dev tasks. | None. |
| 11. Conversational Development | Locally validated semantic foundation: canonical Change/Run/Project model, shared statechart/resolver, capability negotiation, context capsules, risk-aware controls, dependent Process projection, durable prose continuation, outcome-oriented stable installation/placement, isolated Trial Workspace activation, chat-first Workbench, neutral channel ingress, cross-topology navigation, conversational package contract, output IR, story/static reports, and one non-Builder semantic proof. Open must gates include the executable-prototype foundation: bounded local CRUD/provider mocks, semantic activity requirements and simulation trace, compact composition slices, and the constrained conversational workflow profile and Automation handoff; plus one production IntentProposal rail, compatibility-rail retirement, complete registered activity/reply recovery, and one fresh operational empty-scenario acceptance run through the isolated Trial path. | Open: complete Builder-caller migration, deterministic representative-state profiles, structured renderer composition evidence, durable external delivery receipts, live English/Russian mutating Telegram parity, human wide/compact acceptance, richer view registry, issue split/merge, transport recovery inspector, and browser soak. Web/Voice consumed-control parity is locally complete. | Open: additional semantic operations, education-on-the-go exports, and optional rich-channel adapters. | Explicitly deferred: screenshot/multimodal context, general workflow prototype slices and round trips, protected-node migration/retirement, reverse/legacy workflow inference, visual workflow studio, non-conversational/advanced workflow prototyping, full Trial data isolation, simultaneous shared-skill versions, public beta/prerelease rollout channels, hard Telegram parity, miniapp, free-form overlay Review migration, WorkLog extraction, trusted groups, proposal federation, and evidence network. |
| 12. Project Composition And Scoped Development | Specified: Project is a distribution definition; Development Session, presentations, fallback skill preview, local artifact context, and canonical navigation have target contracts. | Open: registry/Catalog Project projection and portable artifact groups. | None. | External artifact providers/MCP and remote multi-component Project install/remove are deferred until local TLP proof. |
| 13. Context-Compiled Execution | Open: typed subject resolution, ref-only automation state, role-scoped restoration, Context Receipts, domain handoff isolation, and Subscription accounting proofs. | Open: Context Inspector, related-work batching, and evidence-gated warm caches. | Open: model-assisted ranking behind deterministic policy gates. | Distributed context federation remains owned by the context/distributed roadmaps. |

## Phase 0. Terminology And Ownership

- [x] `[must]` Adopt `Builder` as the canonical role name.
- [x] `[must]` Define Builder as executor-neutral: human, AI-assisted, or hybrid.
- [x] `[must]` Treat `LLM programmer` as historical wording and replace it in
  documentation surfaces.
- [x] `[must]` Create this roadmap and [Builder](builder.md) as the terminology anchor.
- [x] `[could]` Add a short glossary entry in product terminology once product naming is
  ready.

Phase is complete when all architecture and developer docs point to Builder
for capability creation terminology.

## Phase 1. Read-Only Context Surface

Goal: Builder can understand AdaOS without guessing.

Current implementation slices:

- [x] `[must]` Root MCP foundation exists.
- [x] `[must]` `AdaOSDevPlane` exposes architecture, SDK metadata, template catalog,
  public skill registry, public scenario registry, and named entities.
- [x] `[must]` `NLUAuthoringPlane` exposes current action context, phrase check,
  traces, dialog context, training targets, templates, and patch preview
  surfaces.
- [x] `[must]` Skill and scenario schemas exist under `src/adaos/abi/`.
- [x] `[must]` `llm_hints` / `nlu_hints` are partially consumed through skill/scenario
  descriptors and `webui.json`.
- [x] `[must]` `builder.get_context` exposes a compact read-only Builder context bundle
  through Root MCP.
- [x] `[must]` Builder task and draft schemas are published as Root MCP descriptor sets
  with provenance.

Open work:

- [x] `[must]` Freeze initial `llm_hints` / `nlu_hints` schemas for skills, scenarios, and
  `webui.json`.
- [x] `[should]` Make Root MCP descriptor freshness and provenance visible in Builder
  task context.
- [x] `[must]` Add a compact Builder context bundle that links architecture, SDK,
  templates, registries, current webspace, NLU context, and runtime status.
- [x] `[must]` Add redaction policy for Builder prompt/context bundles.

Primary references:

- [Root MCP Foundation](root-mcp-foundation.md)
- [Root MCP Roadmap](root-mcp-roadmap.md)
- [Conversation and Channel Architecture](conversation-and-channel-architecture.md)
- [NLU Roadmap](nlu-roadmap.md)
- [Roadmap Inventory](roadmap-inventory.md)

## Phase 2. Task And Candidate Model

Goal: missing capabilities become structured Builder work, not fake runtime
actions.

Current implementation slices:

- [x] `[must]` NLU Teacher emits `descriptor_fix` candidates.
- [x] `[must]` NLU Teacher emits `development_task` candidates.
- [x] `[must]` Teacher state persists candidates and event evidence.
- [x] `[must]` Root MCP exposes phrase checks and action context used to avoid inventing
  unavailable actions.
- [x] `[must]` `builder.task.v1` defines the first structured Builder handoff packet.
- [x] `[must]` NLU Teacher attaches Builder tasks to `descriptor_fix` and
  `development_task` candidates.

Open work:

- [x] `[must]` Define Builder task schema with requested behavior, source utterance,
  context snapshot, target artifact hints, side-effect class, privacy notes,
  and acceptance evidence.
- [x] `[must]` Link `development_task` candidates to Builder tasks.
- [x] `[must]` Link `descriptor_fix` candidates to Builder tasks that target
  manifest/webui/nlu hint surfaces.
- [x] `[must]` Add concrete patch materialization for `descriptor_fix` tasks across
  manifest, `webui.json`, and NLU hint files.
- [x] `[must]` Add candidate lifecycle states shared by Teacher UI and Builder:
  `proposed`, `accepted`, `drafting`, `previewed`, `approved`, `applied`,
  `rejected`, `rolled_back`, and `superseded`.
- [ ] `[deferred]` Link completed Builder tasks back to the originating Teacher candidate or
  user idea.

Primary references:

- [NLU Teacher LLM](nlu-teacher-llm.md)
- [NLU Roadmap](nlu-roadmap.md)
- [Operational Event Model](operational-event-model.md)

## Phase 3. Draft Generation Rails

Goal: Builder can create skill/scenario drafts through stable templates and
schemas.

Current implementation slices:

- [x] `[must]` Skill scaffold exists.
- [x] `[must]` Scenario scaffold exists.
- [x] `[must]` Skill and scenario templates exist.
- [x] `[must]` Skill manifest supports `data_routes` and `data_projections`.
- [x] `[must]` Skill runtime supports prepare/test/activate/rollback.
- [x] `[must]` Scenario manager supports install/validate/run/test and dependency
  bootstrap.
- [x] `[must]` `builder.draft.v1` defines draft workspace metadata before runtime apply.
- [x] `[must]` Default skill and scenario templates include `builder.draft.json`
  metadata.

Open work:

- [x] `[must]` Create a Builder draft workspace contract distinct from active runtime
  slots.
- [x] `[must]` Store Builder-authored skill/scenario source under the existing
  CTX dev roots (`.adaos/dev/<subnet>/skills` and
  `.adaos/dev/<subnet>/scenarios`) so `adaos dev skill|scenario validate`,
  `push`, `test`, and `activate` continue to own the lifecycle.
- [x] `[must]` Route every Builder/chat skill or scenario creation through the
  same core `RootDeveloperService.create_skill/create_scenario` contract used
  by `adaos dev skill|scenario create`; remove the parallel template-copy path.
- [x] `[must]` Keep Builder draft indexes and preview records as service
  metadata under `state/builder`, not as an alternate source tree.
- [x] `[must]` Define draft metadata: task id, source idea, selected template,
  target artifact, assumptions, risk notes, and expected tests.
- [x] `[must]` Add `adaos builder draft` or equivalent API/CLI route after the draft
  contract stabilizes.
- [x] `[must]` Add `adaos builder create <id> --kind skill|scenario` as a
  facade over the existing `adaos dev skill|scenario create` owner workspace
  flow.
- [x] `[should]` Add `adaos builder list --kind skill|scenario` so Builder
  operators can inspect the same dev artifacts without switching command
  branches.
- [x] `[must]` Make skill/scenario scaffolds Builder-aware: hints, route plan skeleton,
  tests, lifecycle hooks, and webui descriptors.
- [x] `[should]` Provide scenario-specific Builder guidance matching the skill guide.
- [x] `[must]` Add template quality gates so templates are safe defaults for generated
  work.

Primary references:

- [Skills](../skills.md)
- [Scenarios](../scenarios.md)
- [Skill Runtime Lifecycle](../skill_runtime.md)
- [Builder-Safe Skill Development Guide](../guides/llm-skill-development.md)
- [Builder-Safe Scenario Development Guide](../guides/builder-scenario-development.md)

## Phase 4. Validation And Preview

Goal: Builder changes are inspectable before they mutate durable runtime
behavior.

Current implementation slices:

- [x] `[must]` Skill runtime can prepare, test, activate, and rollback.
- [x] `[must]` Scenario install/update APIs can use async operation records.
- [x] `[must]` NLU phrase probe exists.
- [x] `[must]` Root MCP exposes `nlu_authoring.check_phrase`.
- [x] `[should]` Root MCP exposes NLU template patch preview.
- [x] `[should]` Runtime guards and status cards provide initial safety evidence.

Open work:

- [x] `[must]` Add Builder preview bundle: diff, schemas, route plan, NLU probe,
  action preview, UI preview, test plan, and risk summary.
- [x] `[must]` Add `adaos builder validate <id> --kind skill|scenario` as a
  facade over the existing dev validators, including JSON scenario manifests
  created by Builder drafts.
- [x] `[should]` Add blast-radius preview for learned regex and action descriptor changes.
- [x] `[should]` Add browser/webui preview for generated widgets, modals, and data
  bindings.
- [x] `[should]` Keep Builder/Prompt IDE workflow widgets on shared control-plane
  YDoc paths (`data/prompt/*`) so preview actions and status bars do not drift
  into node-scoped runtime data.
- [x] `[must]` Add static checks for unsafe direct Yjs mutation and unbounded process
  memory in generated skills.
- [x] `[must]` Add route-budget validation for `data_routes`, streams, and projections.
- [x] `[should]` Add previewable scenario dependency bootstrap report.
- [x] `[should]` Add `adaos builder push <id> --kind skill|scenario` as a
  convenience facade over the existing Forge dev upload path. Runtime activation
  and policy approval stay in later phases.
- [x] `[must]` Create a Forge VCS checkpoint after each complete validated LLM
  materialization through `RootDeveloperService.push_skill/push_scenario`.
  Normalize the LLM `comment` as the commit message and persist commit SHA,
  digest, remote path, version, and failure evidence in the UI revision. Keep
  a valid local revision intact when the remote checkpoint fails.
- [x] `[must]` Link each artifact-changing turn through a durable Builder
  Change aggregate: source messages, project topic, LLM job/model, UI revision,
  affected artifacts, terminal response, and one or more Forge commits.
- [x] `[must]` Use one canonical Builder conversation across webspaces and
  transports. Keep project history isolated by stable topic/thread and migrate
  legacy webspace-specific Builder conversations without dropping messages.
- [x] `[must]` Add allowlisted Builder Change trailers to Forge commits and
  reconcile them on `dev update`. Recover synthetic chat from revision evidence
  only when the project thread is empty; never duplicate a surviving transcript.
- [x] `[must]` Apply the checkpoint contract to completed automation output as
  well as UI-only turns. Scenario automation pushes both the scenario and its
  companion skill with the terminal result summary before runtime preparation.
- [x] `[must]` Make `dev skill|scenario push` and `publish` fail closed on
  validation errors before mutating the artifact version. Skill preflight runs
  static validation plus handler/tool probing; scenario preflight validates
  the JSON Schema and declared dependency tool routes.
- [x] `[must]` Keep Automation finalization non-terminal until every required
  Forge checkpoint is confirmed. A false checkpoint result now stops DEV
  activation/materialization and projects a `forge_checkpoint` failure.
- [x] `[should]` Retry the same archive once for transient Forge transport
  failures without applying another semantic version bump; require a commit
  and current allowlisted metadata in the success response.
- [x] `[must]` Repair the Root `/v1/scenarios/draft` durable-commit path. Forge
  now stages only the requested artifact, rebases before push, and returns the
  authoritative post-push commit/tree receipt. Scenario timeout reconciliation
  proves that one accepted request produces one durable Forge write and current
  metadata; archive parity alone does not satisfy the gate.
- [x] `[must]` Remove worker-owned DEV scaffolding. Builder chat and automation
  create missing scenarios, skills, and companion skills through the core
  developer service; workers only modify artifacts already created in DEV.

Primary references:

- [Runtime Guarding](runtime-guarding.md)
- [Skill Projection Runtime SDK](skill-projection-runtime-sdk.md)
- [Post-Deploy E2E Testing](post-deploy-e2e-testing.md)
- [Web UI Architecture](web-ui-architecture.md)
- [Builder-Safe Scenario Development Guide](../guides/builder-scenario-development.md)

## Phase 5. Human-In-The-Loop Apply

Goal: Builder can accelerate creation without removing human authority where it
matters.

Current implementation slices:

- [x] `[must]` Builder preview accepts approval profiles:
  `manual_only`, `low_risk_auto_draft`, `low_risk_auto_apply`, and
  `restricted_maintenance_repair`.
- [x] `[must]` Builder preview emits `review_policy` with profile, mandatory
  review classes, policy blocks, auto-apply eligibility, decision, and evidence.
- [x] `[must]` Builder preview and release/destructive Pending Actions carry
  conversation action-risk evidence. The preview gate uses
  `conversation_safety.classify_action_risk(...)` and blocks auto-apply when
  filesystem, network, device-control, credential, or cross-node classes
  require review.
- [x] `[must]` CLI/API expose approval profiles through
  `adaos builder approval-profiles` and `GET /api/builder/approval-profiles`.
- [x] `[must]` Legacy draft metadata with `human_review_required=true` is treated
  as an explicit manual-review override.
- [x] `[must]` Keep local UI draft creation and ABI-valid revision promotion
  outside Pending Actions. They are revisioned, reversible through `Set current`,
  and reviewed through chat/review notes. Pending Actions begin at destructive,
  activation, release, external-I/O, permission, or mandatory-policy boundaries.

Open work:

- [x] `[must]` Define approval profiles: manual-only, low-risk auto-draft,
  low-risk auto-apply, and restricted maintenance repair.
- [x] `[must]` Define which changes always require human approval: secrets, new
  permissions, external IO, destructive actions, endpoint control, high-rate
  streams, broad NLU patterns, and service processes.
- [x] `[must]` Add initial core Pending Actions plane for durable human
  responses, stored under `data.pending_actions`.
- [x] `[must]` Migrate deferred/cross-channel Builder, pairing, runtime,
  guarded-skill, and NLU Teacher decisions to Pending Actions. Implemented
  producers include Builder review/release/delete, ReDevice LAN admission,
  service-supervisor recovery, declarative skill setup, memory proposals, Skill
  Factory User Hub admission, eval repair, NLU candidate confirmation, and NLU
  clarification. Immediate same-turn guards remain workflow confirmation rather
  than creating artificial Pending Actions.
- [x] `[must]` Retire `builder.scenario_draft.review` and
  `builder.scenario_patch.review` from the local prototyping loop. The client
  suppresses stale instances; `builder.scenario_delete.review` and future
  release/activation actions retain durable approval.
- [x] `[must]` Make Pending Actions node-aware: producer and response handler
  identity must include `node_id` plus skill/scenario/system actor identity.
- [x] `[must]` Define Pending Actions localization contract: every system title,
  summary, action label, and short outcome supports `*_i18n` plus fallback text.
- [x] `[must]` Keep Pending Actions separate from notifications. Notifications
  may deep-link to an action, but the pending action remains the source of truth
  for response state and audit.
- [x] `[should]` Add SDK helpers for publishing Pending Actions, resolving
  expiration, declaring explicit response routes, and handling responses
  idempotently.
- [x] `[should]` Add a global browser Pending Actions surface that reads
  `data.pending_actions` and responds through the event command plane.
- [x] `[should]` Add review UI/workbench for Builder tasks and previews. Browser
  Review packets are promoted into durable revision-aware Review anchors before
  the next transform context is built.
- [x] `[must]` Attach policy evidence and approval identity to every applied
  Builder runtime change through `adaos.builder.applied_release.v1`.
- [x] `[should]` Support reject/redirect feedback as durable Review dispositions:
  dismiss, withdraw, accept-as-constraint, convert-to-Issue, supersede, and
  resolve are exposed through `adaos.sdk.builder.review`.
- [ ] `[deferred]` Support delegated Pending Actions subscription handshake where
  one skill asks another skill to become the response handler. The first
  implementation should use explicit `response_route`.

Primary references:

- [Authority and Degraded Mode](authority-and-degraded-mode.md)
- [Pending Actions](pending-actions.md)
- [Root MCP Foundation](root-mcp-foundation.md)
- [Infrascope](infrascope.md)

## Phase 6. Runtime Activation And Rollback

Goal: Builder output lands through normal AdaOS lifecycle rails.

Current implementation slices:

- [x] `[must]` Skill runtime has A/B slots, semantic buckets, lifecycle hooks,
  deactivation, quarantine, and rollback.
- [x] `[must]` Scenario manager handles dependency bootstrap and webspace rebuild.
- [x] `[should]` Runtime operations and notifications are projected into Yjs.

Open work:

- [x] `[must]` Make Builder apply create a release record linking draft, validation,
  approval, runtime slot, and rollback target.
- [x] `[should]` Add durable operation recovery for long Builder install/test/apply flows.
- [x] `[must]` Reject a DEV skill checkpoint whose semantic version already
  identifies different bytes, or invalidate/refetch every local A/B cache by
  content digest. A same-version `builder_skill 0.3.16` checkpoint was accepted
  by Forge while local activation retained the older cached bytes; current
  Builder acceptance therefore always advances to a unique version.
- [x] `[should]` Define rollback UX for Builder-authored changes across skill,
  scenario, NLU overlay, and entity alias surfaces. One channel-neutral plan
  exposes inspect -> Pending Action restore -> surface-specific verification,
  with stable idempotency and conflict keys.
- [x] `[must]` Add post-activation checks that can route failures back to Builder repair
  tasks.

### Publication-owned setup design

Setup authoring and setup execution are separate operations. Publication is
the right stage to make setup part of the immutable release contract, but it
must not execute credentials, network calls, or host changes while publishing.

- [x] `[must]` Define a versioned declarative setup-plan contract describing
  required inputs/secrets, capabilities, side-effect classes, preconditions,
  idempotency keys, verification, and rollback/compensation evidence.
- [x] `[must]` Add a Publication authoring gate that detects setup needs,
  generates or updates the skill-owned `setup` tool and focused tests, validates
  the plan, and includes its hash in the release record before registry push.
- [x] `[should]` Execute an approved setup plan only after install/activation as
  a separate durable operation or Pending Action. Support dry-run, restart
  recovery, bounded logs, idempotent retry, and explicit partial-failure state.
- [x] `[should]` Reuse the existing `adaos skill setup` runtime entrypoint as the
  executor adapter instead of teaching Publication to call skill internals.
- [ ] `[could]` Add a Builder setup assistant that renders missing inputs,
  secret references, capability review, and verification results from the
  declarative plan.
- [ ] `[deferred]` Implement automatic setup programming or execution in the
  current refactor. First stabilize the Publication release record and
  scenario Forge acknowledgement, then validate setup authoring with an
  isolated fixture.

Primary references:

- [Skill Runtime Lifecycle](../skill_runtime.md)
- [Registry, Marketplace, and Operations Roadmap](registry-marketplace-operations-roadmap.md)
- [AdaOS Supervisor](adaos-supervisor.md)

## Phase 7. Observation And Repair Loop

Goal: runtime evidence becomes actionable improvement work.

Current implementation slices:

- [x] `[must]` Runtime guards can produce diagnostics and quarantine evidence.
- [x] `[must]` NLU Teacher stores misses, candidates, and LLM audit fingerprints.
- [x] `[must]` Root MCP audit and target status exist.

Open work:

- [x] `[must]` Convert guard/quarantine reports into Builder repair tasks when the
  issue is design-time fixable.
- [x] `[must]` Feed failed tests, import errors, route pressure, memory growth, and NLU
  misses into task context.
- [x] `[should]` Add repair task deduplication and supersession.
- [x] `[must]` Add acceptance evidence that proves the repaired capability now works and
  did not regress the triggering behavior.

Primary references:

- [Runtime Guarding](runtime-guarding.md)
- [Operational Event Model Roadmap](operational-event-model-roadmap.md)
- [Development Signals Roadmap](development-signals-roadmap.md)
- [Root MCP Roadmap](root-mcp-roadmap.md)

## Phase 8. Product Experience

Goal: a non-specialist can say what they want and safely become a creator.

Current functional control milestone:

- [x] `[must]` Run the dedicated `builder` scenario in DEV with explicit
  `builder_skill` and `builder_sdk_control_skill` dependencies and no static
  project/file/preview/lifecycle mocks.
- [x] `[must]` Exercise live project browsing, bounded file save, Builder
  Change evidence, synchronous preview materialization, Builder dialog,
  automation state, and release dry-run through the runtime tool boundary.
- [x] `[must]` Preserve approved Builder prototype `029` in functional revisions
  `030` and `032`, including its three-pane layout and original widget/modal
  contracts. Revision `031` remains the immutable autonomous/user-request input
  to the runtime-correction revision `032`.
- [x] `[must]` Reflect the complete Prompt IDE project, technical-specification,
  LLM, workflow, and VCS tool surface through SDK-backed Builder controls.
- [x] `[should]` Protect the visual baseline and capability parity with focused
  golden and browser-contract tests.
- [x] `[should]` Keep Builder control operations on the public `adaos.sdk`
  plane and enforce the boundary with validation and tests.
- [x] `[must]` Make preview actions use one canonical source/DEV pair even when
  Builder is opened inside the DEV webspace. Compare/select now targets
  `dev1` / `dev1-dev`, opening uses native workspace navigation, and QR is
  rendered locally from the same relative preview URL.
- [x] `[must]` Bind Builder chat to the canonical conversation and selected
  project thread so ledger-backed user, progress, and terminal messages are
  restored after tab navigation or reconnect.
- [x] `[must]` Keep project type immutable after creation at the developer SDK
  boundary and render version, workflow stage, and DEV webspace as structured
  data fields rather than literal `$data` text.
- [x] `[must]` Resolve the local Codex executable in the Automation worker and
  expose terminal failure, retry, evidence, and diagnostic fields directly in
  the Builder Automation view.
- [x] `[must]` Publish Builder-owned `ru` and `en` dictionaries as scenario
  resources, attach semantic `*_i18n` keys to prototype copy and SDK
  projections, and localize dynamic list/tree payloads in the browser.
- [x] `[must]` Route Automation follow-ups through ordinary
  `builder_skill:chat`, increment both scenario and companion DEV versions,
  retain focused test evidence, and keep previous completion evidence in
  bounded history rather than the active projection.
- [x] `[must]` Verify real workspace Publication for an isolated skill/scenario
  pair, including semantic version increment, workspace registry metadata,
  commit/push to `origin/main`, and a clean shared monorepo after repeated
  scenario publication.
- [x] `[should]` Avoid replacing `ui.application`, `data.catalog`,
  `data.desktop`, and `data.webio` during a semantic reload when their
  user-visible projection is unchanged. Branch fingerprints converge even when
  the resolver reconstructs equal Python objects; a 32-cycle reload soak proves
  stable live Yjs identity for all four roots.
- [ ] `[could]` Add governed open/copy actions for Automation event, stderr,
  and result evidence instead of showing paths only.
- [ ] `[deferred]` Recreate the control skill from an empty DEV project using
  AdaOS autonomous programming and pass the same functional checks.
- [ ] `[deferred]` Decompose large Builder, Prompt Engineer, scenario-runtime,
  and conversation modules under a separate characterization-test plan.
- [ ] `[deferred]` Remove legacy Prompt IDE only after autonomous reproduction
  and rollback procedures are proven.

Open work:

- [x] `[must]` Define the first user-facing Builder entrypoint: addressed
  messages to `builder` / `Builder` / `Строитель` / `строитель` enter the
  Builder channel and route to `builder_skill` with the address stripped.
  Router tests cover addressed Builder entry and explicit Builder channel
  dispatch to `builder_skill.chat`.
- [x] `[must]` Give Builder a dedicated conversation with isolated context,
  linked threads for draft/preview/repair work, and Pending Action evidence
  backlinks. The first durable slice uses the node-local conversation ledger:
  `builder` conversation/channel, per-draft/per-scenario `thread_id`, topic
  refs in workbench widget metadata, `conversation_context` thread filtering,
  thread-aware Voice history, and Pending Action `domain_ref` / `source_refs`.
- [x] `[must]` Supersede the earlier plan to repurpose
  `prompt_engineer_scenario`: the dedicated `builder` scenario is the Builder
  Workbench and renders project/process/preview/validation controls while
  `builder_skill` owns dialogue and lifecycle decisions. Prompt Engineer stays
  a compatibility/developer tool until its deferred retirement gate is met.
- [x] `[must]` Reuse the Voice/global-dialog UI as an embeddable Voice Chat
  widget inside Prompt IDE. Prompt IDE must configure the widget for the
  `builder` channel and source Builder conversation instead of implementing a
  second chat/transcript surface. `BuilderWorkbenchService.dialog_widget_config`
  and `builder_skill.attach_dialog_widget` publish the first embedded widget
  contract.
- [x] `[must]` Add explicit Builder host-to-preview relationships. Webspace ids
  are opaque; legacy suffix-shaped ids are migration inputs, not topology.
  Builder self-development is the only two-level case: production Builder to
  development Builder through `builder_self_host`, then development Builder to
  its project preview through `builder_project_preview`.
- [x] `[must]` Store live Builder Workbench identity under `data/builder/*`.
  The source-only runtime projection is compact and contains selection,
  binding, and preview status; large draft/dialog/evidence snapshots remain
  explicit reads. Keep `data/prompt/*` only for migration or compatibility.
- [x] `[must]` Keep the Builder host mounted when Choose Project changes data
  context. Revision 033 hydrates page state from
  `data/builder/selection`; `403` data-source failures no longer invalidate the
  browser session, and project selection does not refresh the Prompt workflow.
- [x] `[must]` Keep Select Project off the dynamic skill/tool command path.
  Revision 034 reads the bounded project catalog through
  `GET /api/builder/workbench/projects`, passes the source webspace explicitly,
  prefetches the workspace-scoped read, and caches the response until
  `builder.project.catalog` invalidation. The endpoint reads manifests and
  prompt summaries without the workspace lock, full prompt contexts, or
  per-project preview resolution. Manifest headers are prewarmed and
  invalidated by file identity rather than reparsing nested tool/runtime data.
- [x] `[must]` Make scenario preview selection asynchronous. The SDK persists
  desired context and emits `builder.preview.desired`; the reconciler owns the
  bounded materialization and observed status while skill selection leaves the
  preview scenario unchanged. Declarative background skill commands keep local
  selection and modal feedback independent from materialization latency.
- [x] `[must]` Add workbench commands:
  `builder.ensure_dev_webspace`, `builder.get_workspace_binding`,
  `builder.open_dev_webspace`, `builder.set_active_draft`,
  `builder.list_development_skills`, and
  `builder.delete_development_skill`.
- [x] `[must]` Support "skills in development": list drafts/development skills,
  switch the active draft in the paired dev webspace, and delete the current
  draft through governed Builder lifecycle paths.
- [x] `[must]` Keep Prompt IDE embedded chat in the selected Builder project
  context. The workbench widget now carries active draft/scenario topic
  metadata, ChatWidget sends it with turns and history-more requests, and the
  router publishes only the matching thread projection.
- [x] `[must]` Treat the incoming Builder message topic as the turn-level
  source of truth when Prompt IDE state and workbench binding diverge.
  `builder_skill.update_current_scenario` now aligns the workbench binding to
  `prompt-project:scenario:<id>` from chat/API metadata before selecting the
  target session, so a stale `desktop-dev` binding cannot patch another
  prototype.
- [x] `[should]` Surface API-origin Builder turns in the same administrative
  chat history as browser turns. Tool-bridge calls stamp `action_source=api_tool_call`
  and `origin_label=API`; Builder echoes API requests as `API -> Builder` and
  labels replies as `Builder -> API`.
- [x] `[must]` Render the first mockup from Builder-authored `webui.json` and
  apply user comments as patches against the current draft and current
  `webui.json`. The first deterministic slice is covered by
  `test_chat_first_idea_creates_preview_and_accepts_correction`.
- [x] `[must]` Support the phrase-level flow: "I have an idea. Let's build it."
  `builder_skill.chat` treats first-idea/build phrases as draft-creation turns
  even when the user does not say "create app".
- [x] `[should]` Provide guided clarification when the idea is underspecified.
  `builder_skill.chat` now returns `clarification_required` with a structured
  `adaos.builder.guided_clarification.v1` payload instead of creating a weak
  draft from an empty "I have an idea" turn.
- [x] `[must]` Show assumptions, preview, risks, and expected behavior in non-specialist
  language. Builder draft responses now return `user_summary` and include a
  compact plain-language summary in the dialog message.
- [x] `[must]` Support the first non-trivial prototype correction controls:
  product units, availability fields, and simple segmented filters are written
  to the current preview and generated `webui.json`.
- [x] `[should]` Add the first reviewable prototype workbench affordances:
  the dev preview can enter review mode, select widgets/fields, collect
  per-revision comments, copy those notes into the next Builder prompt, and
  locally drag widgets/fields to express layout intent. Layout dragging now
  requires `Ctrl`/`Command`, leaving ordinary drag available for text
  selection, and every pending note can be removed before Apply so an
  erroneous instruction is never inevitable. Unsent notes remain local drafts;
  Apply promotes the accepted packet into the durable Builder Review store.
- [x] `[should]` Promote review annotations into a durable Builder context
  artifact that follows UI revisions and is automatically included in the next
  LLM transform request. Stable anchors retain Change/revision/semantic target,
  author, and original element snapshot; dispositions remain auditable.
- [x] `[should]` Extend declarative UI prototyping beyond single-page forms:
  model buttons, tabs, modal opening, page transitions, and multi-step forms
  as WebUI ABI behavior data before asking the LLM to synthesize those flows.
  `webui.v1` now validates deterministic `button|tabs|modal|page|multistep`
  state machines whose transitions select declared actions rather than code.
- [ ] `[could]` Add API-driven prototyping: a user can provide an OpenAPI/API
  documentation URL or pasted contract, Builder stores the source reference in
  project memory, then drafts list/detail/action interfaces with explicit
  auth placeholders and no captured credentials.
- [x] `[should]` Keep advanced diffs, schemas, route plans, and runtime evidence available
  for developers. `builder_skill.get_session` and `get_preview_state` now
  return `adaos.builder.developer_evidence.v1` with artifact file refs,
  schema names, route/topic plan, patch diffs, preview refs, workbench binding,
  and Pending Action ids.
- [ ] `[could]` Make completed Builder work visible in catalog, scenario, and skill
  history.

## Phase 9. Reference Runtime And Evaluation

Goal: Builder becomes the reference implementation for modern AdaOS
conversation-native skills.

Open work:

- [x] `[must]` Add the first Builder review-handoff golden fixture to the
  conversation golden suite. It covers addressed Builder entry, draft creation,
  and Pending Action review handoff.
- [x] `[must]` Add a first-idea preview/correction golden fixture. It covers
  phrase-level Builder entry, draft preview, `webui.json` evidence, and a
  follow-up `change_view_representation` patch in the same Builder topic.
- [x] `[must]` Treat `builder_skill` as the semantic owner of Builder
  conversations across browser, Voice/global dialog, Telegram, and Prompt IDE.
- [x] `[must]` Make `builder_skill` consume conversation context packets,
  retrieved evidence refs, scoped memory, and project-scoped Pending Action
  refs instead of raw UI chat state. Prototype LLM execution now receives the
  same persisted Change-bound capsule as Automation and fails closed when it
  cannot be built.
- [x] `[must]` Complete the browser/Voice/Prompt IDE ownership slice:
  `builder_skill` returns canonical dialog/topic refs, emits chat with the same
  thread metadata, owns revision/review-note history, and `attach_dialog_widget`
  exposes the current workbench binding instead of a separate UI transcript.
- [x] `[must]` Move long Builder LLM transformations from synchronous
  `/v1/llm/response` calls to Root-managed async jobs. `builder_skill` now
  submits `/v1/llm/jobs`, stores pending job refs in the Builder session,
  polls the same root base URL, validates the returned JSON, and only then
  writes `webui.json`, `ui_revisions/NNN.json`, and
  dev-webspace refresh events.
- [x] `[must]` Preserve per-revision LLM timing and usage evidence. Root job
  responses now report queue/execution/total timing, provider IDs, service
  tier, token/cache counts, retry/tool/MCP traces, and Builder stores the
  bounded summary in `ui_revisions/NNN.json`.
- [x] `[must]` Define the backward-compatible target protocol for provider SSE,
  bounded Root job progress, staged logical UI patches, atomic ABI-validated
  promotion, and legacy full-response models in
  [Builder Streaming Patch Architecture](builder-streaming-patches.md).
- [x] `[must]` Stream typed provider events into a bounded replayable Root job
  progress journal while retaining the complete terminal response.
- [x] `[must]` Expose Root progress through the SDK and project it into one
  stable Builder chat job card instead of displaying one message per phase.
  The first slice groups bounded phase messages by `progress_group_id`; durable
  single-message ledger upsert remains follow-up work.
- [x] `[must]` Bound the Voice chat stream snapshot by actual UTF-8 bytes, not
  only message count, and preserve `progress_seq`. Full history remains in the
  conversation store; the compact stream cannot trip the WebIO fanout budget and
  delay all Builder phases until terminal recovery.
- [x] `[must]` Add strict `adaos.builder.webui_patch_stream.v1` JSONL output,
  compatible batch parsing, staged
  RFC 6902 application, source revision/hash guards, full ABI validation, and
  atomic revision promotion. Stable `@<id>` JSON Pointer tokens prevent earlier
  array edits from shifting later widget targets. Prompt and repair contracts
  require explicit creation of intermediate parent containers and the ABI-required
  nested `pageSchema.autoActions[*].action` shape.
- [x] `[must]` Preserve unrelated interactions during additive Builder edits.
  The compact contract now distinguishes per-item `ui.list.inputs.buttons`
  from the list-level `addButton` command, keeps existing select/navigation/
  modal actions unless replacement is explicit, and stamps the promoted
  revision into `pageSchema.meta.builder`.
- [x] `[must]` Make Review Apply traceable and consumable. Review requests are
  labeled with a localized semantic origin instead of generic API, and the
  client removes only comments included in the accepted review packet.
- [x] `[must]` Keep non-streaming and legacy full-`adaos.webui.v1` model
  profiles operational through the same Root job and Builder commit contract.
- [x] `[must]` Persist scenario-local terminal LLM job outcomes before session
  projection updates. Builder now reconciles `llm_jobs/<job_id>.json` into its
  pending-job view so a worker/runtime boundary cannot leave a completed job
  permanently queued or block the next prototype request.
- [x] `[must]` Repair invalid semantic patch streams from the complete current
  `webui.json`. The bounded repair turn returns one full `adaos.webui.v1`
  document, which is ABI/component validated and atomically promoted; Builder
  does not maintain intent-specific normalization rules for model output.
- [x] `[must]` Make the full-document repair deterministic at the transport
  boundary. Builder requests Root Responses JSON-object mode for the fallback,
  preflights the current WebUI/component contract, and includes every detected
  migration issue in both the primary and repair context. Invalid JSON or an
  invalid component graph is reported and never promoted as a revision.
- [x] `[should]` Stabilize the persistent prompt prefix, provide a versioned
  `prompt_cache_key`, and record provider cache effectiveness per revision.
  Stable ABI/runtime/affordance context precedes project state and instruction;
  a measured warm request reused 8,832 of 11,593 input tokens.
- [x] `[should]` Add recipe-book and other control prompts that compare TTFT,
  total generation time, patch correctness, preservation, and cache reuse. The
  July 2026 recipe-book run established a useful `gpt-5` prototype from the
  generic scaffold and then corrected details through small stable-id patches.
- [x] `[should]` Replace phase-message grouping with one durable conversation
  message updated by stable job/message id; retain the bounded phase journal as
  evidence rather than separate transcript entries. Router and response paths
  use `materialize_message`; delivery replay updates the same sequence/message
  id and cannot repeat the underlying job.
- [ ] `[could]` Add Root push delivery (NATS/WS) for job progress after polling
  replay is proven reliable; polling remains the recovery path.
- [ ] `[deferred]` Retrieve extended ABI knowledge through MCP only as a repair
  fallback after compact embedded contracts and patch repair fail.
- [x] `[should]` Add root-owned development LLM model profiles. Root policy now
  exposes `dev_model_profiles` through `/v1/llm/models?scope=development`;
  Prompt IDE shows that scoped list in the LLM Profile modal and persists the
  selected model in `prompt_state.json`; Builder uses that project profile
  before env/default model fallback. `gpt-5` is the development baseline
  (`default=true`); regional Root relays preserve the scope query so the Hub
  receives the same authoritative list instead of the complete provider
  catalog.
- [x] `[must]` Add thread-aware context packet plumbing for Builder:
  `conversation_links.builder_context_packet`, router dialog context payloads,
  and `adaos.sdk.conversation.context` can now select the active Builder
  draft/scenario thread instead of the whole conversation.
- [x] `[must]` Broaden Builder golden conversations beyond the current first
  idea, draft creation, review handoff, and mockup patching fixtures to include
  clarification, validation failure, review approval, rejection, and repair.
- [x] `[must]` Link Builder eval failures to repair tasks with conversation,
  trace, draft, validation, and file refs.
- [x] `[must]` Validate generated skills against conversation-native rules:
  no direct transcript files, no direct Yjs chat writes, bounded context
  access, declared memory policy, and explicit action risk class. The first
  shared validation slice blocks direct Yjs symbols, raw transcript files,
  transport-owned chat/memory references, and unbounded process-local
  conversation state in `SkillValidationService`.
- [x] `[should]` Publish a public-quality Builder-generated skill example that
  demonstrates skill-owned conversation, memory proposal, Pending Action, and
  browser widget patterns.

## Phase 10. Skill Factory And Isolated Dev Nodes

Goal: Builder can hand a normalized realization task to Root, Root can assign
it to an isolated AdaOS dev node, and the User Hub can validate the resulting
forge task branch before any runtime activation.

Current implementation slices:

- [x] `[must]` Local Builder draft/preview and Prompt IDE dev-webspace slices
  exist.
- [x] `[must]` Root MCP descriptors, session leases, and `AdaOSDevPlane`
  provide the first governed context foundation.
- [x] `[must]` Pending Actions and runtime action-risk gates exist for review
  and approval mechanics.
- [x] `[must]` The target architecture is now documented in
  [Skill Factory and Isolated Dev Nodes](skill-factory.md).
- [x] `[must]` `adaos.builder.realize_request.v1` and Skill Factory dev-node,
  assignment, result, ready-event, and failure schemas are published through
  Root descriptors.
- [x] `[must]` Root has a local Skill Factory queue, dev-node registry, Root
  MCP task tools, sparse checkout policy, branch validation, and diagnostics.
- [x] `[must]` Add the first platform-owned Automation-stage adapter.
  `builder_automation_skill` delegates implementation to
  `BuilderAutomationService`, exposes `start`, `chat`, and `get_state`, and
  publishes the ABI-validated `adaos.builder.automation_projection.v1`
  lifecycle without duplicating executor state in the skill.
- [x] `[must]` Route ordinary Builder chat through the `builder_skill` owner;
  the skill selects the current project/workflow and calls the public Builder
  Automation SDK. Remove the generic HTTP transport's Builder-specific service
  interception so transport code cannot choose a stale session.

Open work:

- [x] `[must]` Define `adaos.builder.realize_request.v1` and link it to Builder
  conversations, drafts, previews, acceptance criteria, and sparse repo paths.
- [x] `[must]` Add Root dev queue and dev-node registry contracts with
  lifecycle states, heartbeat, assignment, cancellation, timeout, retry, and
  result events.
- [x] `[must]` Define forge task branch discipline and result evidence:
  `result.json`, test report, changed files, sanitized logs, and commit hash.
- [x] `[must]` Add task-scoped MCP and credential leases for isolated dev-node
  work; do not reuse broad runtime or user-subnet credentials.
- [x] `[must]` Implement the private developer skill / Codex runner wrapper
  that prepares instruction packets, enforces allowed paths, runs tests,
  commits, reports, and cleans up. The local worker now consumes one exact
  submitted task id instead of dequeuing an unrelated older request.
- [x] `[must]` Add User Hub result fetch, validation, staging, and Pending
  Action approval before normal skill/scenario activation.
- [x] `[should]` Add a local dev-node simulator for tests and operator trials.
  The local worker path completed the isolated `test05_recipes` Automation
  run, retained an older unrelated queue item, and produced Forge/test evidence.
- [x] `[should]` Add golden task fixtures for success, test failure, forbidden
  file edit, MCP denial, cancellation, and User Hub validation failure.
- [ ] `[could]` Add multi-node pools, task placement policy, and parallel tasks
  after one-task-per-node isolation is proven.

### Phases 4-10 closure evidence (2026-08-05)

- Durable draft: backend `5df75ae` stages one artifact and returns the
  authoritative post-rebase/push receipt; root scenario timeout reconciliation
  verifies one Forge mutation and current commit/tree metadata.
- Human authority: NLU clarification now uses the same Pending Action response
  plane as candidate confirmation; Review SDK and Builder Apply cover the full
  durable annotation/disposition lifecycle. The corrected `builder_skill`
  checkpoint passed its complete focused suite and tool probing, then reached
  Forge as `8f46ce28e3f4babeda5b9b28172f3e899cc70f1b`.
- Activation: `adaos.builder.applied_release.v1`, setup-plan/operation tests,
  same-version digest rejection, post-activation repair, and
  `adaos.builder.rollback_plan.v1` cover release through rollback.
- Repair/evaluation: guard/eval/executor evidence enters one bounded repair
  context; five new Builder conversation goldens and six Skill Factory task
  goldens exercise positive and negative rails.
- Projection/runtime: a 32-cycle semantic no-op soak preserves live Yjs root
  identity, while stable async job materialization keeps one transcript entry.
- Public reference: `examples/builder-generated-conversation-skill` passes
  install-mode skill and WebUI validation and demonstrates governed
  conversation, consent-gated memory, Pending Action, and browser projection.

Focused acceptance commands and results are recorded in the repository commit
trail; the final phase gate also runs the combined Builder/conversation/Skill
Factory/setup/NLU suites on the local machine before push.

## Phase 11. Conversational Development Control Plane

Goal: make Builder conversation-first without using chat history as the source
of truth, while simplifying the product model to `Issue -> Change -> Run ->
Revision -> Trial/Release` and preserving the recovered functional control
plane.

Current must-order for the next refactoring slices:

1. `GWR2-20`: publish localized semantic workflow/affordance/interaction/output
   ABI and complete English/Russian package validation.
2. `GWR3-13..15`: connect every live conversational channel to the admitted
   package and IntentProposal mediation, then measure and retire private/direct
   mutation rails.
3. `GWR2-19`, `GWR4-28..29`: replace Builder handler filtering with one
   prioritized dependent Interaction projection.
4. `GWR4-30..31`: register bounded LLM/Codex/Trial/Publication activities and
   remove compatibility promotion shortcuts.
5. `GWR5-37..38`: run and record the operational empty-scenario Web/Telegram,
   English/Russian acceptance proof.

The numbered tasks and evidence in the governed workflow roadmap are the
execution authority. Chat discussion may reveal a requirement but does not
silently change this order or mark a gate complete.

Architecture:

- [x] `[must]` Define the chat-first, state-backed target architecture,
  channel capability boundary, canonical development model, context packet,
  semantic UI Change IR, risk-aware actions, Process view, evidence trace, and
  multi-user extension seams in
  [Builder Conversational Development Architecture](builder-conversational-development.md).
- [x] `[must]` Keep Telegram and similar transports as limited control
  channels rather than a lowest-common-denominator product requirement. Rich
  search, selection, diff, artifact, and spatial Review surfaces remain Web
  capabilities with compact/deep-link fallbacks.
- [x] `[must]` Define the normative Builder Change statechart, transition
  catalogue, Project aggregate, scoped Change concurrency/focus, Run purposes,
  data modes, submitted Review lifecycle, context-sufficiency contract, and
  decision traceability map in
  [Builder Conversational Development Architecture](builder-conversational-development.md).
- [x] `[must]` Define the shared conversational input/output, NLU data,
  Teacher-candidate, artifact-pipeline, and story-test target contract in
  [Conversational Control Interface](conversational-interface.md).
- [x] `[must]` Link the domain definition to the shared compiler/resolver and
  conformance proof in the
  [Governed Data-Driven Workflow Model Roadmap](governed-workflow-runtime-roadmap.md)
  instead of treating current skill JSON and UI handlers as the target model.
- [x] `[deferred]` Reserve, but do not implement in this refactoring slice,
  miniapp rendering, hard cross-channel feature parity, trusted groups,
  proposal exchange, WorkLog extraction, public candidate discovery, and
  cross-deployment evidence aggregation.

Conversational artifacts and learning:

- [x] `[must]` Add Builder/SDK support for a git-versioned `conversational/`
  package beside a skill/scenario, including input, output, affordance, repair,
  entity, example, optional deterministic matcher, locale, and story sources.
  Workspace/package admission, Builder context, SDK scaffold/compile/export,
  and deterministic stories share the canonical package pipeline.
- [x] `[must]` Complete Builder/SDK ports for conversational proposal emission,
  semantic output, interaction creation, Teacher candidate capture, and
  candidate-to-Change promotion. Project-bound scaffold and compilation ports
  are implemented and use the canonical pipeline.
- [x] `[must]` Expose bounded project-identity conversational authoring tools
  in Builder. `scaffold_conversational_package` refuses replacement, while
  `validate_conversational_package` runs source validation, dependency-aware
  affordance checks, deterministic stories, and static evidence for the
  selected `(kind, id)` project.
- [x] `[must]` Include conversational package digest, diagnostics, bounded
  story summaries, and workflow/output/repair/risk/locale/channel coverage in
  Builder context packets for conversational Changes.
- [x] `[must]` Include originating Teacher candidate refs and promotion privacy
  scope in Builder context packets when a Change is created from runtime
  learning evidence.
- [x] `[must]` Route Teacher `descriptor_fix`, `development_task`, alias, and
  example candidates through a Builder Change before any git-versioned package
  source or public catalog candidate is updated.
- [x] `[must]` Add a conversational artifact validation command that checks
  source schemas, cross-file refs, workflow command refs, declared locale-file
  coverage,
  protected-action policy, and package cardinality. The developer SDK exposes
  non-destructive scaffold, compile, story execution, and static export over
  this same pipeline.
- [x] `[must]` Land the workflow-facing validation service slice behind the
  future Builder/SDK command. `conversational_artifacts` now validates package
  source schemas, checks affordance refs against the manifest-bound workflow,
  rejects affordance-owned workflow shape, and runs deterministic stories with
  mocked activities.
- [x] `[must]` Land the pure runtime ABI bridge that Builder/SDK ports can call
  before they own durable integration: workflow/skill `IntentProposal`
  construction, proposed workflow act -> canonical workflow invocation,
  workflow execution result -> `ConversationOutput`, and semantic output ->
  `ResponseEnvelope` ref/materialization record.
- [x] `[must]` Compile conversational package sources into provider/runtime
  artifacts with source digests, rollout metadata, and rollback refs; compiled
  routers, embeddings, indexes, prompt packs, and model bundles must not become
  independent source truth.
- [x] `[must]` Preserve conversational trace continuity from user turn through
  proposal, dialog/interaction/response, workflow command/event, Run/activity,
  semantic output, envelope, and delivery attempt in Builder evidence. Every
  record carries one `turn_trace_id` plus trace context; the trace-identity
  report and both direct/durable response paths validate the complete chain.
- [x] `[should]` Generate static workflow/statechart and conversation-story
  reports for Builder review, model context, and trial evidence. The shared
  conversational pipeline now returns validation JSON plus
  `adaos.workflow.static_report.v1`; the developer SDK can export both JSON
  reports and a Markdown/Mermaid human-review projection.
- [x] `[should]` Persist workflow-quality evidence on Builder Runs and artifact
  Trials. `adaos.workflow.metrics_evidence.v1` retains definition complexity,
  context sufficiency, and clarification/repair/retry/action-failure rates and
  counts derived from the canonical metrics report and story results.
- [x] `[should]` Add conversational package threat-model checks for
  instruction-like authored descriptors, normalized alias hijacking,
  private/unreviewed Teacher data in public source, output/action risk
  mismatch, and embedded credential/MCP session material.
- [x] `[should]` Track comparison metrics against direct Codex/Claude access:
  time to diagnosis, context needed, rework, missing tests, review load, and
  release confidence.
- [x] `[should]` Start a bounded Builder dogfood package with English/Russian
  authored examples and matchers, Prototype workflow affordances and outputs,
  a deterministic multi-turn workflow story, and no-match repair coverage.
  This admits design-time scenario-development trials but does not mark live
  canonical NLU ingress or unattended runtime activation complete. Story
  runner v2 adds interaction/fallback, stale/concurrent, retry,
  executor-unavailable, repair, and expected-negative cases.
- [x] `[must]` Extend workflow/conversational/interaction/output ABIs with
  semantic i18n refs, `default_locale`, catalog digests, complete key-level
  English/Russian validation, deterministic fallback, and locale-coherent
  presentation after Web/Telegram limits are applied. Stable action tokens and
  command identities must not depend on translated labels.
- [x] `[must]` Route live Web, Telegram, voice, and Builder free text through the
  admitted conversational package and one
  `IntentProposal -> interaction/query/Issue/workflow.invoke` rail. Builder's
  private regex/keyword parser and `nlp.intent.detected -> callSkill` remain
  measured compatibility inputs only until replay and live evidence permit
  removal.
- [x] `[could]` Export accepted conversation stories as user-facing learning or
  education-on-the-go material after they pass privacy and localization checks.
- [ ] `[deferred]` Build an interactive workflow/conversation studio with trace
  replay, expected/actual story diffs, candidate promotion, and Builder Change
  creation only after static artifacts and runners are stable.

Canonical model and context:

- [x] `[must]` Publish versioned `Issue`, `Change`, `Run`, context-packet,
  interaction-frame, semantic-UI-change, and Review-anchor ABI contracts.
- [x] `[must]` Migrate the existing persisted `change_set` to one canonical
  `change` projection. `change_set_id` remains a compatibility alias of
  `change_id`; divergent identities are rejected.
- [x] `[must]` Reclassify per-turn
  `adaos.conversation.development_change.v1` evidence as Runs linked to one
  Change. A follow-up turn extends the Change and creates a new Run, not a
  second product-level Change. The node ledger persists strict Builder Runs;
  release-pipeline evidence ids remain an explicitly labelled compatibility
  projection until all candidate consumers migrate.
- [x] `[must]` Construct a bounded, stable-digested
  `adaos.builder.context_packet.v1` from Change, acceptance, exact base,
  selected Prototype, retained Implementation, dependencies, permissions,
  relevant refs, and prior-Run evidence.
- [x] `[must]` Carry context-packet identity through Prototype/Implementation
  execution, Forge evidence, candidate preparation, Trial, and Publication.
  Automation builds a fresh bounded packet for each isolated Run; direct legacy
  entry first creates an `automation_direct` Change. Session, realize request,
  workflow Run, checkpoint, candidate validation, and Publication metadata use
  the same canonical Change id and packet digest. Interactive Prototype work
  receives Router conversation/memory evidence and project Pending Action refs
  through that packet; transport payloads and raw transcripts do not cross the
  boundary.
- [x] `[should]` Add a context-packet inspector that shows included refs,
  omitted categories, budgets, digest, and construction diagnostics without
  exposing secrets or copying full transcripts.
- [x] `[must]` Add per-Run required context facets and a machine-readable
  coverage report. For spatial UI work include target parent/siblings/order,
  responsive constraints, complete referenced ABI definitions, current data
  bindings, and active acceptance constraints; fail before model submission
  when a required facet is absent or ambiguous. Context packets now retain Run
  purpose, semantic target structure, ABI digest/retrieval ref, constraints,
  data policy, execution authority, and an enforceable coverage report.
- [x] `[must]` Permit several open Changes per project while binding one focused
  Change to each conversation/Webspace command context. Admit modifying Runs by
  exact base generation and affected-ref conflict keys rather than a global
  project lock. Parallel plans, scoped focus, direct conflict indexing,
  mutation leases, artifact generations, and explicit verified rebase are
  implemented and covered by development-portfolio projection tests.
- [x] `[must]` Persist Run purpose as `iteration`, `experiment`, `evaluation`,
  or `recovery`; keep Experiment output off the accepted line until an explicit
  reviewed `adopt_experiment` command. The experiment ledger records pending,
  adopted, or discarded disposition and adoption advances the project artifact
  generation exactly once.
- [x] `[must]` Publish and persist the historical
  `adaos.builder.project.v1` compatibility projection with exact
  source/stable/installed/DEV/candidate identities, component/dependency
  boundary, project policy, open Change portfolio, conflict/dependency index,
  scoped focus, workflow versions, and archive state. Known source, DEV,
  candidate, and stable refs are projected without inventing absent installed
  identity. Phase 12 migrates canonical distribution fields to
  `adaos.project.v1` and mutable fields to Development Session/portfolio
  records; this checked implementation is not authority for the new Project
  manifest.
- [x] `[must]` Derive project summary and project-level commands without
  inventing one current stage from the focused Change, latest Run, selected
  Process node, or Preview target. The projection reports portfolio facts and
  plan/focus/rebase/archive or restore controls without a synthetic stage.

Commands, projections, and Workbench:

- [x] `[must]` Emit typed interaction actions with command, risk class,
  expected workflow generation, target refs, presentation hint, and fallback.
  Stale actions fail without mutation and return the current projection.
- [x] `[must]` Adapt Builder Interaction Frames to the shared
  ConversationInteraction/InteractionResponse registry. Consume negotiated
  Web/Telegram/text presentations and one canonical action ingress rather than
  maintaining Builder-specific token, fallback, or response lifecycles. The
  core adapter now projects a Builder `explain()` result into the shared
  registry and binds compatibility Web actions to exact workflow commands and
  generations. All current Builder conversational entry points now negotiate
  one bounded action set for Web/Telegram/text and return callbacks through one
  response event. The executable-command allowlist, supplemental navigation
  action table, and hard-coded Russian action-label map are no longer control
  authorities; the governed definition and locale catalog are.
- [ ] `[must]` Project the dependent conversational bridge from one governed
  snapshot: accept Prototype, start Automation, accept verification, start
  Trial, accept Trial, and begin Publication. Show the primary continuation
  before optional inspect/navigation actions, preserve lineage and generation,
  and explain missing approval, input, or executor readiness. The state-derived
  actions exist, but this gate remains open until prose actions create durable
  `input_required` continuations and published/Trial outcomes stop falling back
  to generic Preview and state-machine diagnostics.
- [x] `[must]` Return accepted/progress/input-required/terminal
  ResponseEnvelopes for long LLM/Codex/Trial/Publication work. Persist the
  terminal result once and retry ReplyRoute DeliveryAttempts independently;
  never repeat a modifying Run to recover a missing chat response.
- [x] `[must]` Introduce explicit conversation focus, inspected ref, and
  Preview target fields. Selecting a Process item must not implicitly
  materialize it in Preview.
- [x] `[must]` Replace the permanent Lifecycle navigation surface with an
  on-demand Process projection derived from Change/Run/Revision/Trial/Release
  provenance. Retain the old Lifecycle data tool as a compatibility adapter.
- [x] `[must]` Refactor the recovered Builder Workbench around the canonical
  conversation, dynamic action row, exact context/Preview header, adjacent
  Preview, and contextual Process/Overview/Specification/Artifacts/Run detail
  views while retaining the functional-parity gate.
- [x] `[must]` Reserve the project header for the complete project title.
  Change status and working activity belong in the left control panel. Exact
  `proto:`/`active:`/`public:` identity belongs in a separate compact Preview
  control and never shortens the project title.
- [x] `[must]` Isolate Builder self-development from the project it previews.
  A Builder running in `dev1-dev` owns the one terminal `dev1-dev-dev`
  project preview; selecting a project must not replace Builder in its host.
- [x] `[must]` Preserve rich Web project/file search, artifact browsing, diff,
  and spatial Preview behavior. Limited channels receive compact status,
  deterministic actions, and context-preserving deep links.
- [x] `[must]` Replace URL `mode` with the versioned
  `NavigationDestination` intent contract. Core/SDK owns validation, URL
  construction, and ordered explainable resolution; AdaOS Connect and Builder
  are thin producers. Builder Preview links carry zone, subnet, opaque
  Webspace, development/workspace boundary, scenario, revision, and stage.
  The client checks zone -> authentication -> subnet -> Webspace -> source
  boundary -> fresh sync -> scenario, and never changes context without the
  declared user decision. See
  [Navigation Intent And Location](navigation-intent-and-location.md).
- [x] `[should]` Add canonical `NavigationLocation` address-bar projection:
  `pushState` for semantic user navigation, `replaceState` for hydration,
  normalization and secret removal, one `popstate` resolver, and a Copy Link
  command built from resolved non-secret state. Browser Back navigates views;
  it never compensates or repeats workflow effects.
- [x] `[must]` Route Telegram text through the same neutral
  `dialog.user_message` and Builder dialog contract as Web instead of invoking
  NLU directly. Preserve transport reply metadata and project topic context,
  then project Builder assistant text back to `tg.output.*`. Live package-bound
  mediation now produces the same IntentProposal/interaction/workflow command
  identity across the supported transports; private keyword parsing remains a
  measured compatibility proposal source, never a mutation rail.
- [x] `[must]` Claim Telegram updates durably before dispatch, suppress exact
  duplicates, reject idempotency conflicts, and prohibit automatic replay of
  an uncertain state-changing turn. NATS envelope and HTTP fallback inputs use
  the same UTF-8 normalization path. Nested backend `80c5a15` now gives the
  local publish, root relay, callback retry, and HTTP fallback one canonical
  `tg:<bot>:<update>` key; callback actions take priority over the bot-authored
  message text. Core `351d66d6` rejects unknown, expired, or flattened `ia:`
  tokens before Builder, Automation, NLU, or an LLM can observe them.
- [x] `[must]` Bind limited-channel execution to an explicit trusted Webspace.
  Pairing persists `webspace_id`; DEV manifests execute the DEV skill runtime
  directly, while unbound/non-DEV routes remain Workspace-authoritative. A DEV
  route fails closed when its runtime is absent instead of falling back to a
  stale installed Builder.
- [x] `[should]` Add a declarative rich-view registry with browser
  panel/modal/drawer presentations and compact message/link fallbacks.
- [x] `[should]` Render typed deterministic actions as Telegram inline
  callbacks with generation/precondition checks, and persist per-attempt
  delivery receipts. Inline callback rendering, opaque action-token ingress,
  generation checks, idempotent response persistence, callback acknowledgement,
  and consumed-message replacement are implemented. The bot-authored prompt is
  retained, its keyboard is removed, and the exact selection is appended.
  Core `6186ca4b` and backend `2726686` add durable per-operation transport
  attempts, hub-scoped final receipts, external Telegram message ids, and
  optional linkage to ResponseEnvelope DeliveryAttempts.
- [x] `[must]` Project a mutating control only when its declared effect/activity
  executor is registered and ready. The shared
  `description_with_executor_readiness` guard produces
  `executor_unavailable`; exact bounded LLM/Codex/Trial/Publication adapters
  are registered through the activity registry and expose their controls only
  with accepted/progress/input-required/terminal recovery contracts.
- [x] `[should]` Add an operator-visible transport ingress/recovery inspector
  for claimed-but-not-dispatched turns. Recovery must issue a new explicit
  operation instead of replaying the original mutation.
- [x] `[should]` Keep dialog turns independent of compact Yjs projection
  latency. The durable ledger and dialog registry are authoritative; bounded
  chat-tail projection is coalesced off the routed-turn handler and its
  browser-attached latency/soak budget is covered by the dialog projection
  regression.
- [x] `[should]` Make repository-local `adaos api restart` preserve the active
  developer checkout. The exception is limited to `api restart` invoked by the
  checkout's own `.venv`; production entry points remain bound to the promoted
  core slot. The detached child clears inherited slot identity, pins the source
  checkout and exact Git commit, and uses a bounded listener-progress readiness
  grace. Local acceptance on 2026-07-31 restarted the full 37-skill catalog in
  93.5 seconds, returned exact commit `07ce8ed1`, and restored the hub-root
  sidecar to `active_session=true` without a manual reconnect.
- [x] `[should]` Add an explicit Issue split/merge/regroup workbench when
  automatic decomposition is ambiguous.

Semantic changes and Review:

- [x] `[must]` Implement at least one reversible
  `adaos.builder.semantic_ui_change.v1` operation against stable widget/field
  refs, with source-revision precondition, ABI validation, Revision output,
  provenance, and deterministic undo data.
- [x] `[must]` Translate supported Review feedback into typed semantic
  acceptance constraints and verify them against later UI revisions. The first
  deterministic set covers presence, label/property equality, visibility,
  sibling order, and declared data mode on stable widget/field refs. The
  constraints persist under Change, enter later context packets, and are
  re-evaluated after every Builder Prototype revision without automatic repair.
- [x] `[should]` Extend semantic operations from move/rename/show/hide to
  bounded field/widget add/remove and mock/real binding changes after the
  first operation is proven.
- [x] `[must]` Add typed Preview binding profiles for `mock`, `fixture`,
  `sandbox`, `live_readonly`, and `live`. Prototype defaults to mock/fixture;
  a compatible profile switch does not rewrite the UI Revision; every
  Prototype data contract requires an explicit Automation mapping. The first
  implementation makes mode visible in Process/chat status and fails closed on
  live Prototype writes or declared missing mappings.

Executable Prototype foundation:

- [x] `[must]` Define the bounded target contract in
  [Executable Prototype Architecture](executable-prototype-architecture.md): a
  Prototype is an executable requirement model over distinct UI composition,
  data binding, conversational workflow, and activity-requirement planes, not a
  static mockup or an implementation claim.
- [x] `[must]` Implement one shared Prototype Data Runtime contract for static
  examples, local state, disposable local CRUD, recorded provider fixtures, and
  typed synthetic/model-generated data. Every operation uses a semantic command,
  visible data mode, bounded reset policy, and no undeclared external authority.
  `adaos.builder.prototype_data.v1` and `PrototypeDataRuntime` now validate
  schemas, generations, deterministic fixtures/generation, local mutations,
  reset, and trace provenance behind the SDK facade.
- [x] `[must]` Emit typed, implementation-neutral Activity Requirements and a
  semantic Simulation Trace for every demonstrated non-local behavior. Mock
  execution must not satisfy Automation evidence or claim that a database,
  provider, or model implementation exists. The trace ABI carries explicit
  Prototype provenance, while Builder context marks it
  `implementation_evidence=false` and exposes missing Automation mappings.
- [x] `[must]` Build a compact UI Composition Slice for LLM work containing exact
  target refs and source revision, parent/siblings/order/grouping, responsive
  constraints, actions, bindings, ABI fragments, and acceptance constraints.
  Spatial requests such as "put checkboxes on the left" fail for clarification
  before model execution when target/reference/breakpoint is ambiguous. Exact
  slices are now included in the bounded Builder context facet for declared
  executable Prototype projects.
- [x] `[should]` Add bounded structured renderer evidence for the affected
  viewport and breakpoints, then verify spatial acceptance constraints after
  render without serializing the complete desktop into the model packet. The
  evidence contract admits only target-local visible order/rects under an
  explicit budget and the handoff requires compact and wide evidence.
- [x] `[must]` Implement the constrained conversational
  `PrototypeWorkflowProfile`: one bounded entry, sequential states, conditional
  choice, typed input, mock activities, `success`/`failure`/`input_required`,
  cancel, one declared retry, localized interactions, and three deterministic
  stories. The LLM proposes a candidate slice/semantic patch; it never writes an
  active `workflow.json` directly. Validation reuses the canonical compiler and
  existing conversational story runner; accepted output remains an
  `automation_only` candidate patch.
- [x] `[must]` Produce an Automation handoff bundle containing exact UI and
  conversational revisions, logical schemas, binding profile, Activity
  Requirements, implementation-mapping report, fixtures/provenance, accepted
  stories, outcomes, and Review constraints. Missing required mappings block
  Automation or candidate promotion; the same stories run against implemented
  bindings. `BuilderAutomationService.start_from_execute` admits the exact
  project-bound digest before mutation and passes the full handoff to isolated
  Codex context.
- [x] `[should]` Prove representative `empty`, normal, validation-failure,
  provider-unavailable, delayed/input-required, role-denied, long-content,
  English/Russian, compact, and wide profiles. The stabilized reference proof
  also covers offline, conflict, rate-limit, and large-volume fixtures with
  typed inputs and expected observations.

Implementation evidence (2026-08-06): commits `c3b963d5` through `d70d3e23`
add the five ABI schemas, shared runtime/SDK services, manifest binding,
bounded Builder context projection, fail-closed Automation admission, and the
portable `examples/executable_prototype_lab` scenario. Focused Builder, ABI,
workflow, Automation, and end-to-end tests exercise both rejected incomplete
mapping and admitted exact-handoff paths. The same scenario is installed in
local DEV under `sn_6acf0c01` and passes the standard scenario validator.
The same package passed workflow/validation/adapter lock admission and was
uploaded through the ordinary Scenario Forge path as version `0.1.1`, Forge
commit `a6ff3ac226427fb9679557ef1aa32bc28dd33304`, payload SHA-256
`fcbb16fff49e636e6cfd1e3c0641c83059bddefef53524b4910efd04e4384b4d`.
- [x] `[deferred]` Keep screenshots out of the default context packet. Defer
  capture, multimodal target resolution, screenshot-derived layout inference,
  and visual-diff acceptance until structured composition evidence is shown
  insufficient for a named class of defects.
- [x] `[deferred]` Defer general UI-driven/mixed-channel Workflow Prototype
  Slices, full semantic workflow round trips, boundary expansion/rebase,
  reverse projection from stable applications, protected automated-node
  deprecation and migration obligations, legacy code/trace inference, visual
  workflow studio, and parallel/nested/long-running prototype workflows. The
  target contracts and protection boundary remain documented for later work.

- [x] `[must]` Implement submit/withdraw/dismiss/convert-to-Issue,
  accept-as-constraint/supersede/resolve for durable submitted Reviews. Only an
  unsent local draft may be hard-deleted; withdrawn Reviews are omitted from
  future model context but retain a minimal audit tombstone. Submitted records
  are persisted under the focused Change and cannot use the delete path.
- [ ] `[deferred]` Migrate legacy free-form browser-overlay annotations and
  drafts into the submitted Review workflow. The previously diagnosed
  local-storage loss remains tracked. New typed submitted Reviews and
  `adaos.builder.acceptance_constraint.v1` records must use the durable Change
  store; unsent free-form drafts may remain client-local until this migration.

Autonomy, evidence, and acceptance:

- [x] `[must]` Apply a common risk vocabulary to inspect, reversible local
  edit, isolated DEV write, Trial activation, Workspace activation,
  Publication, and destructive commands. Model confidence is rationale, not
  authorization. `adaos.builder.action_risk.v1` now projects side-effect,
  confirmation, approval, isolation, rollback, and limited-channel admission
  policy into every Interaction Frame action; unknown classes fail closed.
- [x] `[must]` Preserve exact base digest, actor, environment, executor,
  allowed paths, semantic/source changes, commits, tests, Trial, Release, and
  activation evidence across the Change trace. The `test05_recipes` trace
  carries one context digest from approved Prototype into its exact Automation
  task, Forge checkpoints, candidate, Trial, stable Release, WorkspaceLock,
  reload, and health receipts.
- [x] `[must]` Prove the slice on a non-Builder scenario through request,
  Change, Prototype or direct Implementation, isolated Run, Trial,
  Publication, and exact DEV evidence. Do not use autonomous Builder
  self-modification as the first acceptance case. `test05_recipes@0.1.4` with
  `test05_recipes_skill@0.1.1` is the accepted local case.
- [x] `[must]` Run Builder workflow/SDK/scenario tests, ABI validation,
  functional parity, and wide/compact browser acceptance. Workspace Builder
  remains unchanged until the DEV candidate is explicitly trialed and
  accepted.
- [x] `[must]` Add Builder interaction conformance for Web rich controls,
  Telegram limits, numbered text, required-capability failure, and deep-link
  handoff with identical command/risk/confirmation semantics. Shared
  presentation fixtures plus the governed Builder E2E proof preserve command,
  target, risk, confirmation, and generation across Web, Telegram, and text.
- [x] `[must]` Add asynchronous restart evidence: accepted command, progress,
  input-required or terminal result, failed first delivery, and successful
  redelivery without repeating LLM/Codex/Trial/Publication mutation. The
  reference fault matrix covers all four modifying activity types at both
  effect boundaries; ResponseEnvelope redelivery is independent of business
  execution and an offline terminal result remains queryable.
- [x] `[must]` Repeat the complete request -> Issues/Change -> Prototype ->
  Automation -> verification -> Trial -> Publication flow on a fresh scenario
  with the built-in LLM, isolated Codex, concrete registered activities, real
  persisted callbacks, and Web/Telegram responses. The fresh
  `workflow_lab_dashboard` proof used five extracted Issues, built-in LLM
  Prototype `001`, isolated Codex task
  `task.01KZ9S388A01VP2GAR2VAE76TZ`, a paired scenario/skill checkpoint,
  empty-data Trial, explicit acceptance, stable Publication, and exact
  Trial/Workspace package trees. Deterministic semantic E2E remains a
  regression test, not the operational evidence for this completed gate.
- [x] `[must]` Remove `prepare_trial_compatibility` and
  `publish_compatibility` after every caller traverses the normative waiting and
  result transitions and shadow telemetry records zero shortcut use. Align the
  declared `trial_waiting` state with the Trial activity callback contract.
- [x] `[must]` Add Project portfolio evidence for concurrent non-overlapping
  Changes and an indirect shared-skill conflict, including explicit
  rebase/split/supersede resolution and no partial candidate promotion. The
  governed E2E proof covers parallel Changes, indirect dependencies, and
  rebase; the typed composition join keeps a scenario-plus-skill candidate
  non-promotable until both required child Runs succeed.
- [x] `[should]` Add evaluator evidence for semantic UI constraints,
  functional tests, usability probes, and source/dependency impact without
  requiring a separate model agent for every low-risk change.
- [x] `[could]` Add optional planner/generator/evaluator Run topologies for
  long or high-risk Changes after single-executor latency, quality, and cost
  baselines exist.

### Phase 11 implementation evidence (2026-07-29)

The consolidated current proof is
[Builder Phase 11 Verification — 2026-08-05](builder-phase-11-verification-2026-08-05.md).
The dated entries below remain the chronological implementation ledger.

- [x] The core compatibility slice is committed as `7308fe5d`, `a65ecd92`,
  `a5c29719`, `05ba3671`, and `313fc53b`: target architecture, canonical
  Change/Run/context contracts, risk-aware Interaction Frames, the first
  reversible semantic UI operation, the chat-first Workbench, and hardened
  lineage invalidation.
- [x] DEV Builder is `0.2.53 / UI 058`; the page metadata, active UI pointer,
  `webui.json`, and canonical `scenario.yaml` version agree. `scenario.json`
  remains a derived legacy projection and is not compared as an authority.
  Forge scenario commit `4231be4b3fc3e74cbf6ff11e75ad98bb67ec457b`
  contains the compact project header, left-panel Change/Preview context, and
  explicit Builder surface identity without overwriting selected-project
  identity.
  The supporting DEV control skill is `0.1.52` at Forge commit
  `3f4d01d6c699ea1147d5ea35fe62c48931a1b93e`; it is activated in local
  runtime slot B and bound to AdaOS `0.1.638`.
- [x] The current focused core Builder regression set passes 120/120 tests.
  DEV control-skill and scenario tests pass 58/58, functional-parity reports no
  missing/forbidden contracts, and scenario plus strict probed skill
  validation report no issues.
- [x] Commit `916b02c6` adds the first transport-independent Telegram Builder
  slice: neutral dialog ingress, durable no-replay claims, addressed Builder
  dispatch, and bounded Telegram output projection. A follow-up compatibility
  patch accepts both NATS envelopes and raw HTTP fallback events. The focused
  ingress/store/router set passes 70/70 tests; the expanded conversation and
  Builder regression set passes 166/166. A live read-only DEV
  `builder_skill:chat` call with the Telegram capability profile resolves the
  current canonical project without mutating it or delivering an external
  message.
- [x] The canonical execution ledger now persists strict
  `adaos.builder.run.v1` records with immutable `run_id -> change_id` binding,
  terminal-state protection, context digest, environment, input/output, and
  evidence refs. DEV control skill `0.1.53` mirrors successful compatibility
  checkpoint/publication evidence into the Run ledger and is checkpointed in
  Forge at `46d504eb514c976193df0f3df8d7128b2486a78e`; focused workflow/store and
  control-skill regressions pass 51/51 and 44/44 respectively.
- [x] Core Automation now refuses context-free execution: each initial or
  follow-up Codex request contains `adaos.builder.context_packet.v1`, while a
  legacy direct call is projected into one minimal canonical Change. Automation
  regressions pass 43/43. DEV control skill `0.1.54` carries the same identity
  through Forge checkpoint, candidate evidence, Trial decisions, and
  Publication transitions; it passes 44/44 tests and is checkpointed at
  `3733639fe5fcd259627f5efe72d7bf102883dad4`.
- [x] Builder Interaction Frames now carry the shared versioned
  `adaos.builder.action_risk.v1` policy. It separates immediate read callbacks,
  generation-guarded reversible DEV commands, confirmed isolated/Trial work,
  and Web-reviewed Workspace/Publication/destructive operations. Contract and
  workflow regressions pass 35/35.
- [x] The interactive Prototype lane now consumes the canonical bounded
  development capsule. Router conversation/memory context and only the
  selected project's Pending Action refs are filtered by core, persisted under
  the Change, marked as untrusted evidence, and passed to the Prototype LLM.
  Missing context fails closed before model submission. Core workflow,
  Automation, and ABI regressions pass 104/104; DEV `builder_skill` regressions
  pass 145/145.
- [x] Structured Review feedback now compiles into the versioned
  `adaos.builder.acceptance_constraint.v1` contract and persists under the
  canonical Change. The evaluator checks each later `webui.json` revision and
  records satisfied, violated, or unverifiable evidence; violations return the
  Change to Prototype review without mutating the UI. Focused core Review,
  workflow, semantic-UI, and ABI tests pass 66/66; the combined DEV Builder and
  control-skill suite passes 191/191.
- [x] DEV `builder_skill 0.3.19` (Forge
  `68139df17dd66c68f26fc73b4299a96f28a80440`) and
  `builder_sdk_control_skill 0.1.55` (Forge
  `38b165d5bcba56f8ec99e54242535fce364903de`) are active in local A/B slots.
  A live context inspection returned the bounded conversation/Pending Action
  fields and digest; a Telegram-capability read resolved the same canonical
  selected project with intact Russian UTF-8.
- [x] A freshly started API process from the current checkout materializes
  `prototype:builder:058` into `dev1-dev` and atomically records the independent
  Preview context (`interaction_updated=true`). The normal local DEV server
  was then restarted from the same checkout and repeated this live call
  successfully.
- [x] Live self-host verification promotes `dev1 -> dev1-dev` to the persisted
  `builder_self_host` relation and materializes `test05_recipes` through the
  separate `dev1-dev -> dev1-dev-dev` `builder_project_preview` relation.
  Workbench bindings and runtime logs confirm that `dev1-dev` continues to run
  Builder while `dev1-dev-dev` runs `test05_recipes`.
- [x] The nested identity contract is explicit: `_meta.current_scenario`
  identifies the Builder surface, while `scenario_id` / `project_id` identify
  the selected target. Revision 057 exposed the collision in a
  Telegram-capability call; revision 058 corrected it and resolved
  `scenario:test05_recipes` under the canonical project topic.
- [x] The current persisted Builder source, UI, translations, and workflow
  contain no replacement code point or four-character question-mark run.
  The manifest declares only `en` and `ru`; no Ukrainian-specific locale text
  was found. UTF-8 JSON-file/tool ingress remains mandatory for non-ASCII
  automation.
- [x] Commit `7717319d` adds the governed semantic acceptance proof on a fresh
  empty scenario. It exercises request/Issue/Change, Prototype approval,
  Automation, verification, exact-digest Trial, and Publication; asserts the
  dependent Process and `proto:`/`active:`/`public:` projections; proves
  Web/Telegram/text command equivalence; prevents a background result from
  inheriting the current UI focus; and detects an indirect shared-skill
  conflict. The focused workflow, Project, Review/context, interaction, and
  intent suites pass 79/79 locally.
- [x] Commits `b05ed896` and `afcddb0b` close the definition-evolution and
  composition seams and add a compact chat explanation. Workflow upgrades are
  explicit generation-guarded events with versioned replay; a required
  scenario/skill join cannot partially promote; Builder derives its concise
  current-state, reason, and next-command answer from the canonical snapshot.
- [x] Commit `66650e02` supplies the reference durable workflow and reply
  outboxes. The 2026-07-30
  [persistence decision](workflow-reference-persistence-decision.md) records
  16 passing durability/delivery cases, two recovery branches, zero automatic
  uncertain-effect retries, and postpones external engines until a measured
  distributed, availability, scale, timer, or operator-cost requirement exists.
- [x] Commits `96b699d0` and `22510818` close two acceptance defects found by
  live inspection. Automation start and completion now share the exact Codex
  task as `run_id`, so a completed execution cannot leave a second synthetic
  Run permanently `running`. A legacy published Change without a stored
  governed snapshot hydrates as `published`, not `ready`; its compact chat
  explanation and Interaction Frame both expose the same deterministic
  `builder.change.plan` next action.
- [x] Local acceptance after these fixes passes 219/219 combined Builder,
  workflow, interaction, intent, persistence, and delivery tests. DEV artifact
  suites pass `builder_sdk_control_skill` 45/45, Builder scenario 14/14, and
  `builder_skill` 147/147. Strict skill/scenario validation is clean. Control
  skill `0.1.60` is active in DEV slot B and was pushed to Forge as
  `92b9a126e8a96983ac24e62b8fd72a80879ea6a6`; a live read returned state
  `published`, exact Preview choices, and the aligned Plan-new-change action.
- [x] Before the accepted 2026-07-30 Publication, the installed Workspace
  Builder remained the earlier published
  `0.2.40 / UI 053`; the new `0.2.53 / UI 058` candidate exists only in DEV.
  Workspace git differences are accounted for by the installed release package
  relative to its older `0.2.19` repository snapshot and by files intentionally
  omitted from release packages. No Workspace source was edited in this slice.
- [x] `test05_recipes` completed the representative non-Builder path. Its
  exact Automation task was `task.01KYQDPG1DRRQPN546RSVZZFC9`; candidate
  `test05_recipes-0-1-4-d73bad2fa5dd` passed isolated Trial and promoted release
  `test05_recipes@0.1.4`. WorkspaceLock revision 10 activated companion skill
  `0.1.1`; reload completed and every scenario/skill health check was exact.
- [x] Explicit `follow_active=false` Preview selection resolves
  `proto: test05_recipes · UI 003`, `active: test05_recipes · 0.1.4`, and
  `public: test05_recipes · 0.1.4` in the isolated nested preview workspace.
- [x] Commit `164a7f43` makes one-shot Automation task-causal and makes Skill
  Factory state mutation cross-process locked and atomic. A requested task can
  no longer consume an older queued task, and corrupt authoritative state
  fails closed.
- [x] Current Workbench/Preview regressions pass 34/34 and neutral
  Web/Telegram dialog/store/runtime regressions pass 28/28. The Automation and
  Skill Factory regression set passed after the task-causality change; its
  tests are intentionally run as a longer group because worker cases are not
  sub-second unit tests.
- [x] DEV `builder_skill 0.3.22` is checkpointed in Forge at
  `595d690b347eeb1f135ea722c67e76644dc0db1c`; the local control skill declares
  `0.1.56`. Limited-channel project selection is conversation focus only and
  does not materialize or replace the owning browser Preview.
- [x] Commits `d7c5b020` and nested backend `d4a5ab7` carry explicit Telegram
  Webspace binding and runtime authority. Existing node/backend SQLite tables
  migrate additively, the backend API build passes, and the focused Router,
  dialog bridge, pairing, and migration suite passes 57/57.
- [x] Commit `15dc6e77` makes compact dialog-state projection coalesced and
  non-blocking. Exceeding the projection latency budget no longer cancels an
  active native `YDoc`, preventing later skill-worker collection on the wrong
  thread. The repeated UTF-8 `dev1-dev` acceptance turn returned the canonical
  Builder answer in 3.052 s with no dialog-state timeout or y_py thread-affinity
  fault.
- [x] The `UI 058` Change completed its remaining gates on 2026-07-30. One
  Automation task produced Builder `0.2.54`; recovery reused the confirmed
  checkpoint after live-readiness failed and did not repeat Codex or Forge
  mutation. The first Trial was rejected because its lock carried stale
  companion versions. A single explicit change group then checkpointed
  Builder `0.2.55`, control skill `0.1.58`, and interactive skill `0.3.24`;
  candidate `builder-0-2-55-a3c36bcaf45e` passed Trial and promoted through
  WorkspaceLock revision `11`. The restart audit recovered the identical
  release set as revision `3` after aborting the obsolete Workspace git rebase;
  `builder@0.2.55` and the locked control/interactive/voice dependencies retain
  their accepted digests. Scenario/strict skill validation passed 206/206 and
  the post-restart AdaOS Builder regression passed 183/183.
- [x] Exact Preview selection after Publication resolves
  `proto: builder · UI 058`, `active: builder · 0.2.54`, and
  `public: builder · 0.2.55` as materialization-ready in `dev1-dev-dev`.
  Automation selection accepts the user-facing current result version from
  the process projection but normalizes it to the immutable task snapshot for
  materialization.
- [x] Post-publication restart recovery is fail-closed and non-destructive.
  Post-boot runtime discovery no longer invokes legacy Workspace git sync,
  quarantined runtimes are not retried implicitly, no-op discovery does not
  rebuild Webspace projections, and A/B prepare no longer disables the old
  slot first. The three runtimes affected while finding the defect were
  explicitly recovered and are ready: `adaos_connect@0.16.4`,
  `conversation_companions@0.1.12`, and `new_face_vision_skill@0.2.25`.
- [x] The 2026-07-31 contextual-control repair is committed as core
  `ee752bf1` plus `1f6dc4a4` and client `ad200ee`; DEV
  `builder_skill@0.3.26` is active in slot B at Forge commit
  `4e1da1529cf4686d21988259cced1a3c670716ac`. “Что выбрано?” now creates one
  durable semantic interaction whose bounded process/change/Preview commands
  render from the same presentation in Web and Telegram. Opaque Web/Telegram
  callbacks return through one response ingress and an explicit core adapter
  into the authoritative DEV runtime; unadapted external-effect controls are
  withheld. Post-fix regressions pass 122/122 core/router/workflow tests,
  150/150 Builder-skill tests, strict probed skill validation, and 18/18
  ChromeHeadless chat tests. A live local paired-Telegram projection preserved
  Russian UTF-8 and five actions; an injected safe `Показать процесс` callback
  was accepted, durably answered, dispatched cross-process, and recorded a
  reply in the originating Telegram conversation. On 2026-08-01 the production
  backend relay retained the negotiated keyboard and the user confirmed that
  all five buttons rendered in the real Telegram client. A human mutating
  callback click and durable backend DeliveryAttempt receipt remain acceptance
  gates rather than inferred proof.
- [ ] Human wide/compact browser comparison and one mutating Telegram callback
  remain production-acceptance gates. Live Telegram ingress, Builder reply,
  UTF-8, and human-visible inline controls are now proven; these remaining
  checks still gate a broader rollout claim.
- [x] The 2026-08-01 workflow-command correction is published as DEV
  `builder_skill@0.3.32` and Workspace `builder_skill@0.3.27`; handler,
  `workflow.json`, and tests are byte-identical and both runtimes are active.
  Exact `Показать процесс` and Preview commands bypass Automation/LLM, while
  `workflow.json`/statechart/TransitionDescriptor correction is classified as
  Automation and visual process-panel layout remains Prototype. DEV Builder
  passes 156/156 tests. Live exact Preview selection resolved
  `proto: builder · UI 058`, `active: builder · 0.2.54`, and
  `public: builder · 0.2.55`, then restored `proto: test04_recipes · UI 003`.
- [x] Core commit `5c20e5fe` preserves the exact governed context packet and
  digest through Skill Factory, adds a bounded Issue/acceptance/facet
  projection to Codex `task.md`, and compiles every manifest-bound
  `workflow.json` before accepting a result. Builder workflow, Automation, and
  Skill Factory worker regressions pass 105/105. Historical lossy records are
  retained as provenance; new question-mark/replacement corruption is rejected
  before model submission.
- [x] Core commits `20319201` and `6088aa2b` harden the final local Preview
  verification path: one-shot UI navigation and materialization remain on the
  Yjs owner thread, resolver inputs are deeply detached before persistent CPU
  execution, and the real CLI restored `proto: test04_recipes · UI 003`
  without the prior cross-thread YDoc finalizer failure. The expanded run also
  preserves explicit remote-node labels when inventory contains only a node-id
  default; relevant regressions pass 63/63 and full Webspace Phase 2 passes
  113/113.
- [x] The 2026-08-01 limited-channel navigation correction is active as DEV
  `builder_skill@0.3.34` (Forge `83ccedda048f1afccb6fca9cea36b379ff801a37`)
  and published as Workspace `builder_skill@0.3.28` (Forge
  `a5ddf74937cd027f34f26f57c9cd6eb2a0975e1c`). Project listing now names one
  conversation-scoped current Project, marks the remainder as DEV-available,
  and emits bounded `Выбрать <id>` controls. Help, Process inspection, project
  selection, and Preview-link commands are deterministic and cannot queue
  Automation/Codex. Core `4ec78aa5` and the skill expose the exact Preview
  target through an `https://inimatic.com` deep-link button. DEV/Workspace
  handler, workflow, and test files are byte-identical; the Builder suite
  passes 159/159. Human verification of the newly deployed Telegram
  project-selection callback remains part of the production gate.
- [x] The 2026-08-03 topology-safe deep-link slice replaces navigation URL
  `mode` with the shared `NavigationDestination` intent ABI. AdaOS Connect is
  published as `0.16.5`; Builder is active as DEV `0.3.35` and published
  Workspace `0.3.29`. Live tool-bridge calls resolved Workspace Builder to
  `desktop-dev / builder / UI 047` and DEV Builder to
  `dev1-dev / test04_recipes / UI 003`, both in zone `ru`, subnet
  `sn_6acf0c01`, with explicit authorization and the development source
  boundary. Focused core/SDK/publication tests pass 61/61 and both complete
  Builder copies pass 159/159. Windows publication now falls back from either
  locked directory-swap boundary to transactional file-atomic activation with
  rollback, without replaying Forge/registry mutation.
- [x] The 2026-08-03 production acceptance correction makes Builder report its
  source and Preview Webspaces explicitly and labels an unbound Telegram
  context as conversation-scoped rather than browser-global. DEV
  `builder_skill@0.3.36` and Workspace `builder_skill@0.3.30` pass 160/160.
  Client commit `e10e3e8` covers the exact same-zone `ruhub` to
  `sn_6acf0c01` mismatch, keeps destination `webspace_id` out of Yjs bootstrap
  until consent, passes 228/228 Navigation/App/YDoc tests, and builds for
  production.
- [x] The 2026-08-04 same-Webspace correction preserves the subnet-scoped
  current Webspace when a raw intent has no canonical `webspace` query, so an
  already open `dev1-dev` no longer silently boots `desktop`. Destination
  completion is gated by complete scenario-consistent materialization rather
  than live transport connection state: `builder -> test04_recipes` is applied
  once, and reopening the current target is a no-op. Client commit `eb053fe`
  passes 238/238 tests, a production build, and the exact local browser route.
  Builder DEV `0.3.41` is pushed/active and Workspace `0.3.34` is
  published/active. Package release
  now retains only manifest-bound `conversational/tests/stories/*.yaml` while
  continuing to exclude ordinary component test suites; component and
  conversational manifest versions update and roll back atomically.
- [x] The 2026-08-04 exact raw-link follow-up makes a topology-confirmed
  `webspace.open` destination the startup room even without the legacy
  `webspace` query. Startup runs one `desktop.scenario.set` before Yjs attach,
  suppresses the persisted Builder render, and exposes preparation as a
  bounded state. The exact two-tab local trace reached `test04_recipes` in
  5.8 seconds with no Builder or mismatch overlay; 271/271 client tests and the
  production build pass.
- [x] Client commit `734023b` is deployed as package `0.0.264`; Firebase Hosting
  `30929921556` and Notify Infra `30929921213` succeeded, and the live hashed
  bundle contains all three startup-preparation markers. This does not close
  the separate core transport release gate.
- [x] The same investigation identifies reconnect amplification rather than a
  merely large Webspace: one 296,101-byte Yjs update was sent 170 times in
  about 36 seconds by leaked WebRTC adapters. Core now uses page-scoped peer
  identity, cancellation-safe adapter removal, failed-recipient pruning, and
  an authoritative selector lease around materialization. Focused WebRTC/Yjs
  tests pass.
- [x] `[must]` Activate the core WebRTC/Yjs lifecycle hardening from the source
  runtime and confirm in a routed multi-tab smoke that room client count maps
  to exact live page sessions, fan-out is bounded, memory remains stable, and
  scenario switching induces no 1006/1012 or relay fallback. Two controlled
  `dev1-dev` transitions emitted one update and one send per live client with
  no reconnect. Final source build `0.1.667+4391.89ec14a3` also exposes exact
  `bs_* -> attempt` and `rp_*` peer identities.
- [x] `[must]` Keep checkout-local `api serve`/`api restart` on the checkout
  runtime. Installed/global startup remains slot-bound; development restart no
  longer silently reactivates an obsolete slot.
- [x] `[must]` Add bounded connected-peer DataChannel grace and deterministic
  listener cleanup in client `0.0.265`; the focused suite passes 10/10.
- [x] `[must]` Remove triple initial full-state delivery from the Yjs protocol
  path. Normal admission has one authoritative `SYNC_STEP1 -> SYNC_STEP2`;
  rejected browser initial state does not trigger another server replay, while
  malformed/preflight recovery remains explicit (`cd36f88c`). Expose the
  replay/dedupe counters in the selected-room diagnostics (`89ec14a3`). The
  final live restart reports three connected/open direct peers, one initial
  document send per peer, zero effective initial replays, six dedupes, and no
  storm.
- [x] `[must]` Make startup Preview preparation resumable across a failed first
  control-WebSocket open without replaying state mutation. A pre-command
  failure registers one bounded continuation; an attempted command remains a
  terminal/unknown outcome for explicit reconciliation. Client `0.0.266`
  passes the complete 125/125 YDoc suite.
- [x] `[must]` Expose member-published public browser assets through the routed
  hub boundary. Backend `f2b018d` validates method, SHA-256 shards, digest, and
  bounded filename before relaying; backend tests pass 19/19. Infra runs
  `30979052485` and `30979428815` deployed the route and immutable cache
  contract; the exact live blob passes byte-digest and UTF-8 JSON checks.
- [x] `[must]` Prevent a root cutover from trapping the hub NATS reconnect loop
  in unbounded route-tunnel cleanup. Core `0e836930` closes live tunnels
  concurrently with bounded cleanup; 74/74 NATS routing tests pass and an
  8-WS/7-YWS controlled reconnect restored the public route in 9.2 seconds.
- [x] Client Infra run `30979460239` deployed the bounded startup continuation
  as `0.0.266+ade734a`.
- [x] `[must]` Separate Preview completion from live transport promotion. A
  one-shot startup preparation acknowledged as `prepared`, together with an
  exact scenario-consistent render materialization, is sufficient to close the
  navigation overlay; the live Yjs provider may finish first sync afterwards.
  This is navigation evidence only: it does not replace, fork, reset or become
  the continuing state authority. All browser tabs and devices attached to the
  same `webspace_id` remain clients of one shared Yjs room and must converge on
  subsequent changes from that room.
  The observed `builder -> test04_recipes` runtime transition took 838 ms, so
  an already visible target must not remain obscured by a transport wait.
- [x] `[must]` Make routed startup relay-first and direct-promoting. A verified
  `webspace.open` destination is now used by the first member registration,
  control socket and RTC peer instead of briefly attaching the page to its
  persisted `desktop` room. YWS establishes the shared authoritative baseline
  without waiting for ICE/DataChannels; WebRTC preparation runs concurrently
  and replaces YWS only after an isolated make-before-break sync/stability
  probe. This changes transport selection, not state ownership: every page and
  device for one `webspace_id` still converges through the same Yjs document.
  Focused YDoc and AdaOS client suites pass 126/126 and 29/29. Client
  `0.0.269+2a6d09f` was deployed by successful Firebase run `30988292760`.
- [x] `[must]` Remove the control-channel wait and state-changing no-op from an
  already satisfied Preview link. Startup now checks the authoritative
  materialization endpoint before channel/transport startup and accepts it only
  for an exact, ready, non-degraded Webspace/scenario snapshot. Mismatch,
  timeout, unavailable evidence, or an unusable render snapshot fail closed to
  the existing single `desktop.scenario.set`; an unsuccessful read does not
  suppress the one-shot control-open continuation. App and YDoc initialization
  coalesce on one in-flight proof, and destination resolution runs immediately
  when it finishes instead of waiting for a new YWS/WebRTC session. Client
  commits `f9e9b96` and `665f29f` pass the combined 250/250 YDoc and navigation
  resolver tests and a production build. Firebase run `30993467779` deployed
  `0.0.271+665f29f`; a steady-state routed trace emitted no scenario command,
  resolved from the authoritative snapshot before YWS, and let direct
  DataChannels promote independently.
- [x] `[must]` Correlate direct peers with the originating browser page and
  client build. RTC signaling and peer snapshots carry
  `peer_id + browser_session_id + client_build_id + client_build_version`, so
  an aggregate open peer is not mistaken for the current tab's direct path.
- [x] `[should]` Move synchronous cold-start inventory and skill readiness work
  off the API event-loop critical path. Builder catalog prewarm runs in a
  worker thread, runtime skill migration/readiness runs as background boot
  work, and Preview switching never repairs startup by replaying a modifying
  command.
- [x] `[should]` Make browser-stream projections delta-sized. After leaked
  adapter cleanup, scoped Yjs map-leaf writes encode the transaction delta
  rather than rewriting the full root projection. No-op writes are suppressed
  and amplification telemetry keeps the full-state recovery path visible.
- [x] `[could]` Add safe structural CRDT compaction with backup, semantic
  verification, and atomic rollback. The offline-only compactor requires an
  explicit witness, compares source digests, verifies the semantic shared
  types, retains a backup, and refuses a live room; runtime replay-log
  compaction remains a separate mechanism.
- [x] `[could]` Evaluate a browser SharedWorker or equivalent per-Webspace
  multi-tab broker after the lifecycle fix so legitimate open tabs can share a
  transport. Do not collapse page identities in the server merely to reduce
  fan-out; authorization, cancellation, and per-tab delivery evidence must
  remain explicit. The evaluation in
  [Webspace SharedWorker Evaluation](webspace-shared-worker-evaluation.md)
  keeps page-scoped transports as the default and admits a broker only after
  measured multi-tab transport pressure justifies its lifecycle complexity.
- [x] The 2026-08-04 data-driven workflow increment adds the four-channel
  ingress/executor proof and full trace spine (`5e0ed333`), strict active-
  WorkspaceLock Builder package cutover with restart-safe instance migration
  (`6dfd3442`), one code/workflow/adapter/role publication admission
  (`b0dd4fae`), and Story runner v2 plus Run/Trial metrics evidence
  (`b72f2a7d`). The local verification ledger keeps these proofs distinct from
  the open durable Telegram target-zone receipt and default cutover rollout.
- [x] `[must]` Add `adaos.project.placement.v1` and persist the distinction
  between stable publication, Workspace installation, and a runnable Webspace
  placement. Published actions must open the placement through Navigation SDK
  or offer `Place in Webspace` when it is absent. Commit `c736c191` adds the
  aggregate projection and stable placement/open actions; the live
  `workflow_lab_dashboard` projection on 2026-08-06 offered `Place in
  Webspace` because its stable release was installed but not placed.
- [x] `[must]` Replace raw published-state diagnostics with an outcome-oriented
  localized message and a semantic Process lineage containing exact version,
  Prototype, Automation, verification, Trial, stable Release, Workspace
  installation, and placement evidence. Commits `c736c191` and `4b156839`
  make the Trial outcome independent from Publication and render the same
  localized lineage for text and semantic-control inspection.
- [x] `[must]` Replace prompt-only `builder.change.plan`/extend/edit/iterate
  controls with durable typed `input_required` interactions. Resume the exact
  project/change/generation once and project a fresh action set. The durable
  continuation carries the project, Change, command, and expected generation;
  stale or repeated responses cannot start another mutation.
- [x] `[must]` Implement immutable Trial activation from a Candidate PackageRef
  into derived beta state, with a durable TrialActivation
  record, restart reconstruction, explicit detach, bounded data modes, and
  fail-closed shared-skill version-conflict detection. Commit `7ff2a05e`
  implemented the historical compatibility materialization under
  `workspace/.runtime/trials`. Phase 12 commit `a3958e0ed` supersedes that
  layout with an isolated Workspace-shaped
  `.adaos/trials/<candidate-id>` root. Simultaneous versions of a
  conflicting shared skill remain deliberately rejected under the deferred
  boundary below.
- [x] `[should]` Apply the consumed-control lifecycle to Web/Voice chat as well
  as Telegram: accepted historical tokens are unusable, old controls disappear
  on live update and reload, and the replacement Interaction comes from the
  new workflow generation. Core commit `46f681f1` and client commit `466b3c9`
  persist and optimistically project consumed actions as empty; focused server
  and browser tests cover live update, reload, and idempotent re-use.
- [ ] `[deferred]` Add full Trial data sandboxing, simultaneous versions of a
  shared skill, and public beta/prerelease channel rollout. The MVP may render
  a Trial/beta badge but cannot claim a registry channel promotion without a
  beta or stable publication receipt.

## Phase 12. Project Composition And Scoped Development

Goal: replace the overloaded "one Builder project equals one skill/scenario
directory" assumption with a distributable Project definition and a separate,
least-write Builder Development Session. Research Workbench is the first
consumer and acceptance case; the contracts remain domain-neutral.

Architecture owner:
[Project Composition, Presentation, and Development Context](project-composition-and-development-context.md).

- [x] `[must]` Define/validate `adaos.project.v1` with arbitrary non-empty
  owned skill/scenario composition, dependencies, profiles, entry points, and
  lifecycle policy; preserve existing one-component projects as a compatible
  projection.
- [x] `[must]` Define/validate
  `adaos.builder.development_session.v1` with separate focus, primary/secondary
  targets, read-only context exposure, artifact inputs, scratch policy, base,
  and checkpoints.
- [x] `[must]` Extend Builder SDK with atomic create/open/add-component/session
  operations and failure rollback. A domain orchestrator may call this API but
  cannot mutate component sources itself.
- [x] `[must]` Enforce development write scope in tool exposure and final
  change-set validation. A read-only dependency change returns an explicit
  scope-expansion request.
- [x] `[must]` Add skill `presentations`, Project entry-point binding, exact
  verification receipts, and deterministic preview fallback through
  `adaos.system.skill-preview`; never retain an unrelated older scenario.
- [x] `[must]` Make Preview Open and QR render one canonical Navigation SDK
  destination with exact dev webspace, presentation bindings, zone/subnet, and
  applicable auth policy.
- [x] `[must]` Add local `artifacts/partN` Skill SDK enumeration/resolution,
  bounded extraction, digest/staleness, trust/media metadata, and publication
  policy. Native filesystem access is the first Codex provider.
- [x] `[must]` Replace the New Dev Project template list with a Taiga searchable
  selector showing selection, version, description, and source; order builtin
  recommended templates first; keep Create as a distinct action and show a
  field-specific diagnostic for a missing required Project ID.
- [x] `[must]` Fix created-project selection so id, kind, display title,
  Project ref, and initial target update atomically before upload, initialize,
  Preview, or Automation actions become available.
- [x] `[must]` Pass the Research Fabric ARF7.1 reference proof from shared
  Research Workbench through Project/direction creation, local TLP artifact
  intake, exact formulation acceptance, and unopened scoped Codex session.
- [x] `[must]` Add schema-declared one-shot query-to-state addressing for an
  entity-specific first paint. Research Workbench uses it for the exact target
  skill while the reactive `data/builder/selection` projection remains the
  canonical, subsequently editable selection.
- [x] `[must]` Add an authenticated, manifest-resolved DEV artifact content
  endpoint and reusable PDF document renderer. Markdown and text continue to
  use their existing renderers; clients receive logical artifact addresses,
  not native filesystem paths.
- [x] `[must]` Make runtime action approval publication use the asynchronous
  Pending Actions room owner. A live-room thread handoff failure must never
  suppress the global review affordance.
- [x] `[must]` Resolve an absent or divergent DEV source against the exact
  `WorkspaceLock`, cached `ProjectRelease`, and content-addressed package before
  materialization. The read-only recovery plan identifies Project ownership,
  distinguishes owned components from immutable dependencies, fingerprints
  Workspace and DEV projections with the locked package build policy, and
  classifies clean, one-sided drift, converged unpublished work, invalid source,
  missing package, and three-way conflict. Lock-backed materialization fails
  closed on every non-reviewable result instead of creating a blank scaffold.
- [x] `[must]` Complete reviewed source recovery apply: preserve divergent
  Workspace and DEV trees as immutable evidence, synthesize a Project
  definition from the exact release when the legacy project directory is
  absent, create a Change from the selected owned-source delta, and require a
  new Candidate/Trial/acceptance/Publication before moving `WorkspaceLock`.
  Dependencies remain read-only unless their owning Project is opened through
  a separately scoped Change.

Reviewed recovery is exposed through the Builder service facade, public SDK,
authenticated REST API, and Root MCP. Apply requires the exact reviewed plan
digest and explicit decisions for ambiguous owned components. It snapshots
divergent valid trees into the content-addressed package store and Project
source evidence, never materializes dependencies into another Project's DEV
scope, reconstructs a missing legacy Project declaration from the immutable
release, and creates only a `planned` Change. A durable operation journal makes
partial outcomes visible and blocks replay after an uncertain post-Change
failure; an immutable receipt makes completed replay idempotent. WorkspaceLock
bytes remain unchanged until the ordinary Candidate, isolated Trial, explicit
acceptance, and Publication workflow completes.

- [x] `[must]` Split runtime authority in the Builder product flow: mutable DEV
  source may update only `dev/.runtime`; stable may update only primary
  `workspace/.runtime` from WorkspaceLock evidence; beta may update only an
  immutable `.adaos/trials/<candidate-id>` projection.
- [x] `[must]` Replace Builder's compatibility dev-to-workspace overlay actions
  with Candidate/Trial actions, retire alpha Workspace labels, and retain
  historical overlay receipts as non-actionable evidence.
- [ ] `[must]` Route chat-created Project work through one resumable Change and
  Candidate identity from template creation and DEV preview through Trial
  acceptance, stable promotion, and release publication.
- [x] `[must]` Rebind Trial Preview/Open actions and Webspace runtime selection
  to the exact Trial Workspace root while stable sessions continue to resolve
  the primary Workspace root; fail closed when provenance or lock evidence is
  missing.
- [ ] `[should]` Show DEV, beta Trial, and stable roots with exact source,
  package, release, migration, health, and cleanup state in the Builder process
  projection and operator diagnostics.
- [ ] `[could]` Add a bounded retained-Trial selector after exact routing,
  expiry, conflict detection, and cleanup are proven; selecting a Trial must
  never copy it into the stable runtime.
- [ ] `[should]` Merge governed Candidate/Trial events created outside the
  current Builder Automation session into workflow delivery and Process
  projection. Direct `Show Trial` already resolves the exact active Candidate;
  contextual actions must expose the same capability without a manual command.
- [ ] `[should]` Make detached Yjs document ownership and shutdown explicit for
  synchronous chat-driven Preview materialization. A successful command must
  exit without the `YDoc ... dropped on another thread` finalizer diagnostic,
  with a clean-shutdown regression on Windows.
- [ ] `[should]` Add deterministic stable Preview/Open selection by exact
  Project identity and bind it to primary Workspace authority. Do not route a
  recognized stable-selection command into prototype generation or require an
  LLM Context Packet merely to open an already promoted Project.
- [ ] `[should]` Add Project/Application catalog projections with profiles,
  localized categories, free tags, deployment scopes, and an advanced raw
  component view.
- [ ] `[should]` Add portable local artifact-group export/import and exact
  cross-node verification receipts.
- [ ] `[deferred]` Add external `additional_artifacts`, object storage, and MCP
  resource resolution only after the native local contract is validated.
- [ ] `[deferred]` Complete remote multi-component Project publication,
  transactional install/remove, and shared-dependency reference accounting in
  the artifact/registry roadmaps; these do not block the local pre-Codex proof.

E2E evidence, 2026-09-05: chat session `builder_session_ab398ace` created
Project `trial_workspace_lifecycle_e2e_20260905_w_5decc38e`; its immutable
Candidate was selected through `show trial` from sibling `.adaos/trials`,
accepted, promoted with healthy WorkspaceLock revision 43, and published to
registry commit `58c5da0cd969a11e98a3ad5181da9e197c1acacc`. This proves the
artifact lineage and release boundaries. The phase `must` remains open because
the out-of-band CLI Candidate was not merged back into one continuous Builder
Process/Change projection. A `show stable` probe also confirmed the open
deterministic stable-selection task: it safely stopped at Context Packet
preflight instead of opening the promoted Project.

## Phase 13. Context-Compiled Builder Execution

Goal: make Builder a purpose-scoped consumer of the generic Context Graph and
Compiler instead of a producer of self-contained, repeatedly nested context
envelopes. Architecture and generic sequencing remain in
[Agent Context Graph And Capsules](agent-context-capsules.md) and
[Context Resolution, Compilation, And Compression](context-compression.md).

- [ ] `[must]` Resolve every qualification, planning, Automation, Codex,
  validation, and re-entry run from exact DevelopmentSession subject refs,
  purpose, audience, policy, source generation, and agent profile.
- [x] `[must]` Apply a fail-closed execution preflight to singleton Dev Ticket
  automation. Keep domain qualification separate from exact repair-envelope
  admission, expose both in Builder context, and create no Builder/Skill
  Factory work until source and acceptance coverage are qualified.
- [x] `[must]` Persist one Context Plan and Context Attribution Receipt per run;
  show selected/omitted/denied refs, context misses, provider/local-Codex usage,
  cache contribution, latency, and evidence in Builder observability.
- [x] `[must]` Replace nested packet, snapshot, task, readiness, result, and
  provenance copies in Builder/Skill Factory automation state with
  content-addressed refs and a bounded workflow checkpoint. Keep historical
  assignments readable through a compatibility projection.
- [ ] `[must]` Consume domain handoffs as immutable read-only refs. Builder may
  return clarification, feasibility, SDK ambiguity, capability gap, or core
  issue proposals, but cannot mutate the owning domain contract.
- [ ] `[must]` Prove cold restoration for a simple Dev Ticket project and a
  Research Workbench ImplementationTrack after process/model restart, including
  write-scope enforcement, publication, Trial/acceptance evidence, and
  continuous Subscription accounting.
- [ ] `[must]` Route successful/failing runs, user rejection, and validation
  findings to episodic memory candidates only. Reusable repair/SDK guidance
  requires independent evidence-gated promotion and rollback.
- [x] `[should]` Add a Context Inspector to the Builder Workbench using
  Declarative Resource Workbench projections, with hidden/sealed content shown
  only as denied metadata rather than disclosed text.
- [x] `[should]` Batch related Dev Tickets or ImplementationTracks against one
  stable Project capsule while preserving independent Change, budget,
  acceptance, and rollback lifecycles.
- [x] `[must]` Compile language-neutral `prompt_facts` during deterministic or
  bounded LLM qualification and route versioned rule capsules by concepts,
  surface, operation, data plane, effects, lifecycle, access, conversation,
  and i18n before falling back to task text.
- [x] `[must]` Give pre-Codex LLM and isolated Codex a bounded Development
  Feedback output contract for missing, ambiguous, conflicting, inefficient,
  insufficient-context, observability, validation, and policy findings.
  Persist observations in a separate governed registry and require explicit
  acceptance before project/SDK/core promotion.
- [x] `[should]` Project Development Feedback related to the selected project,
  component, or Dev Ticket into Context Inspector with status/category counts,
  while keeping mutation in role-aware Resource Workbench/API/MCP operations.
- [ ] `[should]` Enable warm role/focus caches only after cold-replay equivalence
  and matched-budget evidence show a net benefit.
- [ ] `[could]` Add model-assisted context ranking after deterministic identity,
  dependency, authority, trust, and freshness selection; retain the selected
  plan and deterministic fallback for audit.

Implementation verification, 2026-09-02: bounded Builder Automation runs now
compile a typed platform/project/component/task capsule graph, retain the
Context Plan and Attribution Receipt refs, and expose them through the
Workbench Context Inspector. Dev Ticket handoff carries canonical project
identity through materialization, context compilation, publication, Trial,
verification, and Subscription receipts. Package planning rejects mixed or
partially unknown project scope before worker launch, preserving one stable
Project capsule and independent user-ticket lifecycles. The Home Shopping List
absent-DEV-source run proves project materialization, zero-model structured
repair, ProjectRelease publication, accepted Trial, and ticket closure. Cold
Skill Factory restoration now resolves digest-bound context packet, Development
Session, prototype-handoff, continuation-checkpoint, result, and provenance
artifacts after a process restart while keeping the authoritative task document
compact. Builder context compilation now gives an exact DevelopmentSession its
own immutable subject and capsule, relates each task overlay through
`bounded_by`, and reuses a project-stable capsule while the source generation
and project projection digest remain unchanged. Related Dev Ticket runs
therefore share stable project context without sharing their task overlays,
budgets, acceptance, or rollback lifecycle. Context Attribution Receipts carry
the run, DevelopmentSession, and Project refs, including deterministic
zero-model execution routes. Context Plan lookup normalizes plan and selected
unit subject refs, filters by exact project or run scope before applying query
limits, and backfills legacy plans, so unrelated recent work cannot hide the
selected project's history. Builder and Skill Factory persistence now migrate
legacy inline request, snapshot, task, result, provenance, and workflow copies
to immutable refs on the next mutation; compatibility hydration exists only at
read and worker-assignment boundaries. A production-state copy measured Skill
Factory reduction from 75.9 MB to 4.28 MB (17.7x), while representative Builder
sessions shrank 13-24x. A cold simple Dev Ticket re-entry completed a successor
worker turn with the same Project authority and new source-generation/task
capsules. End-to-end cold publication/Trial/Subscription replay,
episodic-memory routing, and the deferred Research Workbench proof are still
open.

Cost-routing verification, 2026-09-02: the Demo Metrics project E2E showed two
full-Codex attempts consuming 224,000 model/billable tokens before the exact
structured route completed with zero. Builder and Dev Ticket projections now
surface the independent domain qualification and execution preflight, including
missing fields, route, model-call expectation, and estimated budget. Surgical
UI/data/CRUD runs default to 24,000 `fresh_plus_output`; incomplete singleton
repairs stop before work-item creation. The deterministic qualification stage
now indexes authoritative component DEV source, resolves common English and
Russian UI/data/CRUD language to at most four files plus focused validation,
and verifies source digests immediately before work creation at zero model
cost. Exact semantic slices and the Root-accounted residual-language qualifier
remain open.

Prompt-projection verification, 2026-09-03: Skill Factory now compiles a
permanent execution boundary plus conditional SDK, WebUI/tool, declarative UI,
data-route/receiver, resource/storage, runtime-lifecycle,
conversational/i18n, workflow, consumer ABI, and typed research rule capsules.
Structured `prompt_facts` are produced by deterministic qualification and the
bounded Root-accounted language fallback; localized ticket text is no longer
the primary routing contract. Provider/research requirements and exact ABI
bundles are absent from ordinary skill prompts and enter only when admitted
machine facets/contracts select them. The selected IDs, registry digests,
documentation anchors, and Context capsule refs are retained in
`packet.json.prompt_rule_capsules`. Root MCP descriptor search remains an
optional bounded evidence adapter rather than the owner of initial
mini-representation formation. Prompt profile
`adaos-skill-realization/0.18.1` records this projection boundary and the
bounded public method/resource application-trace output contract.

Development-feedback verification, 2026-09-03: pre-Codex qualification and
Codex final output can emit schema-validated observations into
`adaos.development_feedback.v1`. The workspace registry deduplicates repeated
findings, maintains optimistic revisions and lifecycle events, and exposes
query/triage/comment/accept/reject/promote through SDK, API, Root MCP, and
Declarative Resource Workbench. Builder Context Inspector projects only
findings related to its selected project/component/ticket. Promotion is
human/policy-gated and creates linked project review debt, SDK Understanding,
or Core capability work; model output never grants write scope. The Builder
scenario now presents that projection in a dedicated read-only review tab with
project-scoped text/status/category/source filtering. The trusted worker captures
recognized public-contract validation failures only after repair attempts are
exhausted and links the aggregate observation back to its task, test report,
and originating Dev Ticket. Trial rejection is also observable as a distinct
`result_rejected` record: the candidate, session/task/repair, originating user
tickets, rollback evidence, and exact review note remain linked, while the six
possible diagnoses stay explicit or `needs_qualification`. Builder can filter
these records by outcome, producer, and rejection class without treating its
review projection as a second ticket authority.

Economic preflight calibration, 2026-09-03: a real local Codex repair measured
185,106 input tokens with 173,824 cached input across two tool rounds. Builder
now keeps the marginal `fresh_plus_output` ceiling independent while deriving
at least a 200,000-token provider-billable ceiling when none was explicitly
declared. The worker estimates a 176,000-token provider baseline plus bounded
round growth; an explicit lower billable ceiling remains a strict preflight
gate rather than being silently raised.

Source recovery verification, 2026-09-02: the local Builder lock at release
`builder@0.2.55` resolves `scenario:builder` as Project-owned and the three
companion skills as dependencies. The deterministic preflight blocks automatic
materialization because current Workspace and DEV sources contain independent
changes; package bytes remain available. Focused Builder source/workbench/worker
regressions pass 57/57 without mutating either source tree.

## Cross-Document Anchors

Builder is intentionally cross-cutting. Detailed work remains in:

- [Governed Evolution](governed-evolution.md): Issue-first product loop,
  Personal Builder isolation, Support boundary, and verified capability model
- [Governed Evolution Roadmap](governed-evolution-roadmap.md): GE2 Personal
  Builder proof gate and its dependencies on managed deployment and repair
- [Builder](builder.md): role, pipeline, and source-of-truth terminology
- [Project Composition, Presentation, and Development Context](project-composition-and-development-context.md):
  Project distribution, Application/presentation resolution, local artifact
  context, registry classification, and Builder Development Session boundary
- [Agent Context Graph And Capsules](agent-context-capsules.md): typed subject
  graph, context state classes, trust, invalidation, and restoration boundaries
- [Context Resolution, Compilation, And Compression](context-compression.md):
  generic registry/resolver/compiler/curator sequencing and evaluation gates
- [Builder Conversational Development Architecture](builder-conversational-development.md):
  chat-first product model, canonical Change/Run terminology, interaction
  frames, semantic UI operations, and future proposal collaboration
- [Builder Governed Workflow Verification 2026-08-01](builder-workflow-verification-2026-08-01.md):
  local contract audit, live Web/Telegram/Preview evidence, model-context audit,
  and remaining proof gates
- [Governed Workflow Runtime Verification 2026-08-04](governed-workflow-runtime-verification-2026-08-04.md):
  local ingress, package cutover, publication admission, trace, story, metrics,
  and provider-inventory evidence
- [Builder SDK Boundary](builder-sdk-boundary.md): SDK ownership, functional
  Builder control architecture, migration checklist, and local proof record
- [Skill Factory and Isolated Dev Nodes](skill-factory.md): target remote
  realization layer for Root-managed dev queues, isolated dev nodes, forge
  task branches, task-scoped MCP, Codex execution packets, and User Hub
  validation
- [Conversation and Channel Architecture](conversation-and-channel-architecture.md):
  dedicated Builder conversation, skill-owned chats, and transport-independent
  context
- [Roadmap Inventory](roadmap-inventory.md): current cross-roadmap status
- [Root MCP Roadmap](root-mcp-roadmap.md): descriptor, plane, session, and MCP
  readiness
- [NLU Roadmap](nlu-roadmap.md): Teacher, clarification,
  descriptor fix, development task, and NLU authoring gates
- [Skill Runtime Lifecycle](../skill_runtime.md): skill prepare/test/activate
  lifecycle
- [Builder-Safe Skill Development Guide](../guides/llm-skill-development.md):
  generated skill safety and data-route requirements
- [Builder-Safe Scenario Development Guide](../guides/builder-scenario-development.md):
  generated scenario dependency, NLU, UI, and preview requirements
- [Scenarios](../scenarios.md): scenario lifecycle basics
- [Web UI Architecture](web-ui-architecture.md): browser-facing generated UI
- [Runtime Guarding](runtime-guarding.md): guard/quarantine feedback into repair

## Builder Workbench Lifecycle Surface

This section records the recovered Workbench compatibility surface and its
historical local evidence. Phase 11 is authoritative for the target chat-first
refactoring. Checked compatibility items below do not mean that the shared
GWR4 statechart, resolver, data-mode, Review-lifecycle, or context-sufficiency
contracts are implemented.

The Builder Workbench prototype now uses one project-oriented surface instead
of the legacy Prompt IDE toolbar split:

- the left navigation owns project selection and a dependent lifecycle lineage:
  Prototype revisions contain the Automation results derived from them, which
  in turn contain their Publications
- the main area switches between project overview, the durable Builder
  conversation, and the selected artifact
- the auxiliary area is contextual: runtime projection on Overview,
  development/LLM plus active change-set controls on Conversation, and the
  artifact tree on Artifacts
- `ui.chat` supports an optional multiline composer; `Shift+Enter` inserts a
  newline while `Enter` and `Ctrl/Cmd+Enter` submit by default
- split layouts can declare bounded sidebar and auxiliary widths while medium
  and compact breakpoints retain stack/drawer behavior

Long-running lifecycle transitions must not be browser-owned. The Builder skill
owns an idempotent transition record and projects its compact status to the
Workbench:

```text
queued -> preparing -> materializing -> verifying -> ready
                                             \-> failed
queued/running -> superseded
```

The record separates desired state (`project`, `stage`, artifact version,
controlled webspace, generation id) from observed runtime state (materialized
scenario/version, verification time, sync status). A newer desired generation
supersedes older queued or running work. The UI reports `ready` only when the
observed projection matches the latest desired generation.

- [x] `[must]` Prototype project selection, lifecycle navigation, contextual
  Overview/Conversation/Artifacts surfaces, and desired/observed webspace
  metadata in the `builder` dev scenario.
- [x] `[must]` Add reusable multiline `ui.chat` composer behavior and bounded
  declarative split-column widths.
- [x] `[must]` Prototype the compact project workbench requested for the
  Artifacts and Overview surfaces: a rootless file picker, a selected-file
  path and editor/viewer, protected read-only artifacts, editable project
  metadata, direct copy sources in New Project, and explicit archive/restore
  actions. Browser acceptance was completed against Builder revision `024`.
- [x] `[must]` Ignore stale project-selection, preview-selection, and revision
  materialization events when their scenario no longer matches the latest
  source-webspace binding. This prevents an older asynchronous event from
  replacing the currently selected Builder projection.
- [x] `[must]` Persist `adaos.builder.change_set.v1` inside the authoritative
  project workflow with bounded issue items, acceptance criteria, member
  Builder Changes, and `prototype_first` / `automation_direct` routes.
- [x] `[must]` Aggregate follow-up interface and functional remarks into the
  active set, keep unresolved Prototype issues at the Prototype gate, and
  prevent isolated Codex from starting before Prototype approval.
- [x] `[must]` Carry the active change-set identity and acceptance criteria into
  Automation, checkpoint, candidate, trial, and Publication evidence.
- [x] `[must]` Advance the workflow to `checkpoint_recorded` only after the
  primary Automation checkpoint has a confirmed change id, package digest,
  and source revision. Recovery reuses confirmed checkpoints without rerunning
  isolated Codex or repeating a Forge push.
- [x] `[must]` Resolve required candidate dependencies across all approved
  checkpoint members of the active change set, while rejecting unrelated DEV
  dependencies that are not part of the set.
- [x] `[must]` Project the active set and issue progress in the contextual
  Conversation panel; preserve Prototype development settings and expose them
  only while Prototype is editable.
- [x] `[must]` Replace independent stage roots with dependent
  Prototype -> Automation -> Publication lineage. Only the retained current
  Automation and Publication nodes are previewable; uncertain legacy lineage
  is marked inferred.
- [x] `[must]` Verify exact local runtime selection labels and bindings for
  `proto: builder · UI 053`, `active: builder · 0.2.40`, and
  `public: builder · 0.2.40`.
- [x] `[must]` Run one fresh user request through issue extraction, built-in LLM
  Prototype approval or direct Automation, isolated Codex, candidate trial,
  stable Publication, and Workspace materialization on this machine.
  Candidate `builder-0-2-28-940229ddbf49` locked scenario `0.2.28`, control
  skill `0.1.36`, and interactive skill `0.3.13`; the deliverable files in DEV,
  trial, and Workspace were byte-for-byte equal after promotion.
- [x] `[must]` Repeat the complete path on a non-Builder conversational project
  after the data-driven cutover. `workflow_lab_dashboard@0.1.2` progressed from
  five extracted Issues through built-in LLM Prototype `001`, isolated Codex,
  paired scenario/skill checkpoint, empty-data Trial, explicit acceptance and
  stable Publication. Candidate
  `workflow_lab_dashboard-0-1-2-f731ed4209d9` is canonical state `published`;
  WorkspaceLock revision `4` pins the scenario and
  `workflow_lab_dashboard_skill@0.1.1`, and Trial/Workspace trees are
  byte-identical.
- [x] `[must]` Materialize a newly published scenario that contains canonical
  `scenario.yaml -> webui.json` and no legacy `scenario.json`. The
  `workflow_lab_dashboard` launch exposed the old loader dependency; the
  corrected live proof resolves from `loader:workspace`, reports no legacy
  fallback, requires `ui.application.desktop.pageSchema`, and materializes
  three declarative widget definitions backed by the companion skill.
- [x] `[must]` Reject newly corrupted user-authored text at Builder workflow,
  interactive Builder, and Automation ingress before it becomes durable
  evidence. Codex-operated non-ASCII tool requests use UTF-8 files and
  `--json-file`, not PowerShell native-process text pipelines.
- [x] `[must]` Refresh a declarative host from updates to its configured root
  Yjs `initialStateSource`, so create/select project changes redraw Builder
  without navigation or a page reload. The client regression covers a
  `test03_recipes` to `test04_recipes` selection change.
- [x] `[must]` Treat `idle`, `not_started`, and default Automation projections
  as absence rather than lineage evidence. A fresh `test04_recipes` projects
  one Prototype revision, does not synthesize an Automation row, and never
  borrows the Prototype version for a nonexistent result. Control skill
  `0.1.39` carries the focused regression and strict validation evidence.
- [x] `[must]` Preserve every functional companion skill across Automation
  turns and provide the installed Workspace Publication as an immutable
  implementation baseline. The worker rejects changes to that baseline and
  removes it before DEV activation or release-package construction.
- [x] `[must]` Supersede dependency-inferred companion preservation with
  canonical Project ownership. Only skills in
  `adaos.project.v1.components.owned` enter the mutable Automation envelope;
  scenario runtime requirements, prior snapshots, installed publications, and
  `<scenario>_skill` naming no longer grant write authority. Session, worker,
  and projection contracts use `companion_skill_ids` only.
- [x] `[must]` Treat installed-only dependencies as immutable release inputs,
  not mutable DEV companions. A missing legacy stable identity is migrated
  into a deterministic package with an explicit `workspace-migration` source
  reference and included in the project release lock.
- [x] `[must]` Activate every component from the promoted WorkspaceLock and
  require exact scenario and active skill-runtime versions before recording a
  successful Workspace receipt. A failed, rolled-back activation can only be
  resumed by the explicit one-shot `recover-promotion-activation` command,
  which issues a new idempotency key and never repeats the channel move.
- [x] `[must]` Bound Publication reload and health convergence to the candidate
  dependency closure rather than every retained component of the merged
  WorkspaceLock. Completed promotion replay is receipt-only; a terminal
  receipt set can repair a stale local `paused` marker without repeating
  registry writes, channel movement, or activation.
- [x] `[must]` Complete the corrective functional Builder Automation from the
  current Prototype plus installed Publication, then repeat candidate Trial,
  stable Publication, exact Workspace materialization, and byte/lock checks.
  Candidate `builder-0-2-40-26af92f3eaef` passed isolated Trial and promoted
  release `builder@0.2.40`; WorkspaceLock revision `9` pins Builder `0.2.40`,
  `builder_skill` `0.3.16`, `builder_sdk_control_skill` `0.1.44`, and migrated
  `voice_chat_skill` `0.6.17`. Reload and post-activation health receipts are
  exact for every component.
- [x] `[must]` Complete browser acceptance for atomic create/select redraw,
  canonical single-response project chat, and `proto:` / `active:` /
  `public:` Preview selection after the corrective Publication.
  The current browser projects `test04_recipes` as one Prototype revision with
  no synthetic Automation row; Lifecycle node selection switches the
  phase-specific Conversation surface, and all three exact Builder previews
  are synchronized and materialization-ready.
- [x] `[must]` Capture the selected Preview binding when an Automation turn is
  submitted. On completion, advance it to the current `active:` result only
  when the binding is still unchanged or already follows the active result;
  preserve a Lifecycle choice made by the user while Codex was running.
- [x] `[must]` Validate Choose Project through the complete declarative
  initial-state/data-source contract. Every referenced selection field is
  initialized before `list_projects` runs; browser acceptance on Builder
  `0.2.42` resolves two live groups and 22 project cards without a static
  fallback.
- [x] `[must]` Recover Builder after the mock-only self-hosting regression.
  UI revision `042` is preserved as the executable DEV scenario
  `builder_reference_042`; recovery UI `054` rebases the active DEV Builder on
  its complete control plane and forward-ports bounded Yjs project selection,
  live `list_projects`, search, archived filtering, and Scenario/Skill template
  selection. The embedded `adaos.builder.functional_parity.v1` gate requires
  all widgets, modals, bindings, Lifecycle commands, and project kinds.
- [x] `[must]` Complete local recovery evidence: reference and recovered
  scenarios validate, parity reports no missing or forbidden contracts,
  scenario tests pass 13/13, SDK tests pass 40/40, core Automation tests pass
  43/43, and browser A/B rendering resolves `proto: builder_reference_042`
  versus `proto: builder · UI 054`. Both revisions are checkpointed in Forge;
  Workspace remains unchanged.
- [x] `[must]` Complete isolated Trial and Publication of the recovered Builder
  line. The accepted UI 058 release is `builder@0.2.55` in WorkspaceLock
  revision `11`, with exact reload and component health evidence. The
  post-restart recovery reconstructed the same release set and component
  digests as the current WorkspaceLock revision `3`.
- [ ] `[should]` Complete the remaining human comparison and remove the
  temporary reference scenario only after it is no longer needed for visual
  regression analysis.
- [x] `[should]` Mark immutable historical values that already contain lossy
  replacement runs as transport-corrupted in Specification projections while
  retaining their raw provenance; never infer the missing source characters.
- [x] `[should]` Add an explicit split/merge editor when automatic issue
  decomposition is ambiguous. Typed split/merge commands retain
  `derived_from_issue_ids`, mark superseded items, recompute the Prototype or
  Automation gate, and are exposed through the Builder issue SDK.
- [ ] `[deferred]` Promote project-local issue items into a federated multi-user
  Issue/extraction and proposal-exchange model.
- [x] `[must]` Persist the authoritative Builder transition record in the skill
  and make transition submission idempotent by generation id. DEV
  `builder_skill@0.3.32` now owns the strict manifest-bound `workflow.json`;
  Builder loads it through the shared compiler/cache, pins instances by
  definition version and digest, and the existing generation plus payload-bound
  idempotency key admits each transition once. With
  `ADAOS_BUILDER_REQUIRE_ACTIVE_PACKAGE=true`, the service requires the active
  WorkspaceLock package and validates definition/validation/binding pins with
  no DEV/Python-definition fallback; restart-safe migration checkpoints prove
  exact in-flight migration and rollback.
- [x] `[must]` Materialize only the latest desired generation and mark older
  queued/running generations `superseded`.
- [x] `[must]` Stream transition and observed-projection changes to the
  Workbench; do not poll or infer readiness from the browser command response.
- [x] `[must]` Reconcile desired and observed versions after reconnect, process
  restart, and manual runtime changes; expose an explicit drift state.
- [x] `[should]` Connect project cards and artifact trees to Builder-owned data
  instead of the prototype's static examples.
- [x] `[should]` Add browser acceptance for wide, medium, and compact lifecycle
  layouts plus multiline keyboard behavior. Declarative scenario validation
  and the client renderer tests cover the three layout modes, compact tree
  indentation, and Enter/Shift+Enter behavior; the separate human visual
  comparison gate above remains intentionally open.

## Should Readiness Decision

Builder can now start selective `[should]` work without waiting for every
production `[must]` track to finish, because the reference foundation has
durable conversations, topic-aware Builder context, first practical draft and
patch flow, Pending Action review handoff, golden migration gate, runtime
action-risk gates, FTS/search/segment retrieval, governed memory proposal, and
generated-skill conversation lint.

The remaining `[must]` work stays active in parallel:

- Production apply/release/rollback: release records, approval identity on
  applied changes, post-activation checks, and rollback UX.
- Repair loop: convert guard/quarantine/test/import/route/NLU evidence into
  Builder repair tasks with acceptance evidence and eval failure backlinks.
- Product Workbench: make `prompt_engineer_scenario` the full Builder
  Workbench, not only the current widget/projection binding.
- Reference runtime: move the remaining Builder call paths to context packets,
  retrieved evidence refs, scoped memory, and Pending Actions across all
  supported transports.
- Skill Factory foundation: keep current local Builder flows compatible with
  future `realize_request` task envelopes, Root dev queue, isolated dev-node
  execution, and User Hub validation.
- Evaluation/browser acceptance: broaden golden dialogs and add browser
  acceptance for Voice/global dialog, Prompt IDE, and Pending Actions.
