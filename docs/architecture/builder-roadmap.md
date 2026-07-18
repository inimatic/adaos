# Builder Roadmap

Status: high-level sequencing for the AdaOS Builder vertical slice.

This roadmap tracks how AdaOS evolves from separate skill/scenario/runtime
surfaces into a coherent Builder workflow: idea -> governed artifact -> preview
-> validation -> activation -> observation -> repair.

Detailed implementation remains in the specialized roadmaps. This page is the
cross-cutting source of truth for Builder readiness.

## Reading Rules

- [Builder](builder.md) defines the role and architecture boundary.
- [Builder SDK Boundary](builder-sdk-boundary.md) defines the public SDK
  dependency direction and tracks the functional replacement-control slice.
- Checked items mean an implementation slice exists, not necessarily full
  product maturity.
- Every checklist item carries a four-level MoSCoW-style priority label.
- Specialized roadmaps continue to own detailed sequencing for their domains.
- Builder milestones should link out to domain roadmaps instead of duplicating
  every low-level checklist.

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
| 4. Validation/Preview | Complete: preview bundle, static checks, route-budget validation, Builder validation facade. | Complete: blast radius, webui preview, scenario dependency bootstrap, Forge push facade. | None. | None. |
| 5. Human Review | Partial: approval profiles and mandatory human-review classes are enforced in preview; Pending Actions core/SDK, global browser surface, NLU Teacher candidate-confirmation, and initial service-supervisor runtime recovery slices exist; Builder/pairing/broader runtime producer migrations and applied-change evidence are open. | Open: review workbench and reject/redirect feedback. | None. | Open: delegated Pending Actions subscription handshake. |
| 6. Activation | Open: release record and post-activation repair routing. | Open: durable operation recovery and rollback UX. | None. | None. |
| 7. Repair Loop | Open: guard/test/route/memory/NLU evidence into Builder repair tasks and acceptance evidence. | Open: repair deduplication/supersession. | None. | None. |
| 8. Product Experience | Partial: revision 032 preserves the prototype 029 geometry, includes revision 031's immutable project-type requirement, and provides the complete SDK-backed Prompt IDE surface with corrected live bindings; autonomous from-zero reproduction is still required before Prompt IDE retirement. | Open: eliminate coarse no-op projection replacement and complete a browser reconnect/soak pass. | Open: richer Automation log and cross-project history views. | Open: autonomous reproduction, large-module decomposition, and legacy Prompt IDE retirement. |
| 9. Reference Runtime | Partial: `builder_skill` owns the first conversation-native flow with eval fixtures, topic refs, Pending Actions, Prompt IDE widget binding, and async Root LLM job execution for UI transformations; full context-packet/memory/repair coverage remains open. | Open: public-quality generated-skill examples. | Open: optional model-backed repair graders. | None. |
| 10. Skill Factory | Partial: target architecture, RealizeRequest schema, Root dev queue, dev-node registry, Root MCP task tools, sparse path validation, forge task-branch policy, local Codex worker, and the first Builder Automation runtime skill exist; task-scoped credentials/MCP bridge and User Hub validation loop remain open. | Partial: queue diagnostics and a render-safe Automation projection exist; dev-node simulator and failure fixtures remain open. | Open: multi-node pools and parallel dev tasks. | None. |

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
- [NLU Roadmap](../concepts/nlu-roadmap.md)
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

- [NLU Teacher LLM](../concepts/nlu-teacher-llm.md)
- [NLU Roadmap](../concepts/nlu-roadmap.md)
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
- [ ] `[must]` Migrate Builder, pairing, runtime operations, guarded skill
  actions, and the remaining NLU Teacher clarification flow to produce and
  consume Pending Actions. NLU Teacher candidate confirmations and
  service-supervisor runtime recovery failures have initial migrations.
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
- [ ] `[should]` Add review UI/workbench for Builder tasks and previews.
- [ ] `[must]` Attach policy evidence and approval identity to every applied Builder
  runtime change. The apply/release step still needs to persist approval identity.
- [ ] `[should]` Support reject/redirect feedback that becomes new Builder context instead
  of being lost as chat history.
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

- [ ] `[must]` Make Builder apply create a release record linking draft, validation,
  approval, runtime slot, and rollback target.
- [ ] `[should]` Add durable operation recovery for long Builder install/test/apply flows.
- [ ] `[should]` Define rollback UX for Builder-authored changes across skill,
  scenario, NLU overlay, and entity alias surfaces.
- [ ] `[must]` Add post-activation checks that can route failures back to Builder repair
  tasks.

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

- [ ] `[must]` Convert guard/quarantine reports into Builder repair tasks when the
  issue is design-time fixable.
- [ ] `[must]` Feed failed tests, import errors, route pressure, memory growth, and NLU
  misses into task context.
- [ ] `[should]` Add repair task deduplication and supersession.
- [ ] `[must]` Add acceptance evidence that proves the repaired capability now works and
  did not regress the triggering behavior.

Primary references:

- [Runtime Guarding](runtime-guarding.md)
- [Operational Event Model Roadmap](operational-event-model-roadmap.md)
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
- [ ] `[should]` Avoid replacing `ui.application`, `data.catalog`,
  `data.desktop`, and `data.webio` during a semantic reload when their
  user-visible projection is unchanged. The newer branch-diff path is usable
  for real changes, but a repeated no-op reload still fell back to four coarse
  replacements; harden fingerprint convergence and prove stable widget
  identity with a reconnect/reload soak test.
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
- [ ] `[must]` Treat `prompt_engineer_scenario` as the Builder Workbench UI:
  it renders active draft state, mockup preview, validation evidence, and
  actions while `builder_skill` owns LLM dialogue, patching, validation, and
  lifecycle decisions.
- [x] `[must]` Reuse the Voice/global-dialog UI as an embeddable Voice Chat
  widget inside Prompt IDE. Prompt IDE must configure the widget for the
  `builder` channel and source Builder conversation instead of implementing a
  second chat/transcript surface. `BuilderWorkbenchService.dialog_widget_config`
  and `builder_skill.attach_dialog_widget` publish the first embedded widget
  contract.
- [x] `[must]` Add source-to-dev webspace binding for Prompt IDE:
  `dev_webspace_id = f"{safe_source_webspace_id}-dev"`. Reuse one paired dev
  webspace per source webspace instead of creating a new webspace per draft.
  `BuilderWorkbenchService.ensure_dev_webspace` creates/reuses that binding.
- [x] `[must]` Store Builder Workbench projections under `data/builder/*`,
  including active draft, draft list, preview snapshot, validation evidence,
  and workspace binding. Keep `data/prompt/*` only for migration or
  compatibility. The first service snapshot writes `data.builder`.
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
  locally drag widgets/fields to express layout intent. The first slice is
  intentionally lightweight: annotations are browser-session feedback, not a
  durable Builder review store.
- [ ] `[should]` Promote review annotations into a durable Builder context
  artifact that follows UI revisions and is automatically included in the next
  LLM transform request.
- [ ] `[should]` Extend declarative UI prototyping beyond single-page forms:
  model buttons, tabs, modal opening, page transitions, and multi-step forms
  as WebUI ABI behavior data before asking the LLM to synthesize those flows.
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
- [ ] `[must]` Make `builder_skill` consume conversation context packets,
  retrieved evidence refs, scoped memory, and Pending Actions instead of raw
  UI chat state.
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
- [ ] `[should]` Replace phase-message grouping with one durable conversation
  message updated by stable job/message id; retain the bounded phase journal as
  evidence rather than separate transcript entries.
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
- [ ] `[must]` Broaden Builder golden conversations beyond the current first
  idea, draft creation, review handoff, and mockup patching fixtures to include
  clarification, validation failure, review approval, rejection, and repair.
- [ ] `[must]` Link Builder eval failures to repair tasks with conversation,
  trace, draft, validation, and file refs.
- [x] `[must]` Validate generated skills against conversation-native rules:
  no direct transcript files, no direct Yjs chat writes, bounded context
  access, declared memory policy, and explicit action risk class. The first
  shared validation slice blocks direct Yjs symbols, raw transcript files,
  transport-owned chat/memory references, and unbounded process-local
  conversation state in `SkillValidationService`.
- [ ] `[should]` Publish a public-quality Builder-generated skill example that
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
- [x] `[must]` Route Prompt IDE Builder chat through the installed Automation
  skill while a project automation session is active, retaining the direct
  local service route only as migration fallback.

Open work:

- [x] `[must]` Define `adaos.builder.realize_request.v1` and link it to Builder
  conversations, drafts, previews, acceptance criteria, and sparse repo paths.
- [x] `[must]` Add Root dev queue and dev-node registry contracts with
  lifecycle states, heartbeat, assignment, cancellation, timeout, retry, and
  result events.
- [x] `[must]` Define forge task branch discipline and result evidence:
  `result.json`, test report, changed files, sanitized logs, and commit hash.
- [ ] `[must]` Add task-scoped MCP and credential leases for isolated dev-node
  work; do not reuse broad runtime or user-subnet credentials.
- [ ] `[must]` Implement the private developer skill / Codex runner wrapper
  that prepares instruction packets, enforces allowed paths, runs tests,
  commits, reports, and cleans up.
- [ ] `[must]` Add User Hub result fetch, validation, staging, and Pending
  Action approval before normal skill/scenario activation.
- [ ] `[should]` Add a local dev-node simulator for tests and operator trials.
- [ ] `[should]` Add golden task fixtures for success, test failure, forbidden
  file edit, MCP denial, cancellation, and User Hub validation failure.
- [ ] `[could]` Add multi-node pools, task placement policy, and parallel tasks
  after one-task-per-node isolation is proven.

## Cross-Document Anchors

Builder is intentionally cross-cutting. Detailed work remains in:

- [Builder](builder.md): role, pipeline, and source-of-truth terminology
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
- [NLU Roadmap](../concepts/nlu-roadmap.md): Teacher, clarification,
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

The Builder Workbench prototype now uses one project-oriented surface instead
of the legacy Prompt IDE toolbar split:

- the left navigation owns project selection and the three-stage lifecycle
  tree (`Prototype`, `Automation`, `Publication`)
- the main area switches between project overview, the durable Builder
  conversation, and the selected artifact
- the auxiliary area is contextual: runtime projection on Overview,
  development/LLM controls on Conversation, and the artifact tree on Artifacts
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
- [ ] `[must]` Persist the authoritative Builder transition record in the skill
  and make transition submission idempotent by generation id.
- [ ] `[must]` Materialize only the latest desired generation and mark older
  queued/running generations `superseded`.
- [ ] `[must]` Stream transition and observed-projection changes to the
  Workbench; do not poll or infer readiness from the browser command response.
- [ ] `[must]` Reconcile desired and observed versions after reconnect, process
  restart, and manual runtime changes; expose an explicit drift state.
- [ ] `[should]` Connect project cards and artifact trees to Builder-owned data
  instead of the prototype's static examples.
- [ ] `[should]` Add browser acceptance for wide, medium, and compact lifecycle
  layouts plus multiline keyboard behavior.

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
