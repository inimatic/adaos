# Builder Roadmap

Status: high-level sequencing for the AdaOS Builder vertical slice.

This roadmap tracks how AdaOS evolves from separate skill/scenario/runtime
surfaces into a coherent Builder workflow: idea -> governed artifact -> preview
-> validation -> activation -> observation -> repair.

Detailed implementation remains in the specialized roadmaps. This page is the
cross-cutting source of truth for Builder readiness.

## Reading Rules

- [Builder](builder.md) defines the role and architecture boundary.
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
| 8. Product Experience | Partial: addressed Builder entrypoint, dedicated Builder conversation, paired Prompt IDE dev webspace, first phrase-level build flow, thread-aware embedded chat, and non-specialist draft summary exist; Prompt IDE as full Builder Workbench remains open. | Open: guided clarification and developer evidence views. | Open: catalog/scenario/skill history. | None. |
| 9. Reference Runtime | Partial: `builder_skill` owns the first conversation-native flow with eval fixtures, topic refs, Pending Actions, and Prompt IDE widget binding; full context-packet/memory/repair coverage remains open. | Open: public-quality generated-skill examples. | Open: optional model-backed repair graders. | None. |
| 10. Skill Factory | Open: target architecture exists; RealizeRequest schema, Root dev queue, dev-node registry, task-scoped MCP, forge task branches, and User Hub validation loop are not implemented. | Open: dev-node simulator, queue diagnostics, and failure fixtures. | Open: multi-node pools and parallel dev tasks. | None. |

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
- [x] `[must]` Builder preview and Builder skill Pending Actions carry
  conversation action-risk evidence. The preview gate uses
  `conversation_safety.classify_action_risk(...)` and blocks auto-apply when
  filesystem, network, device-control, credential, or cross-node classes
  require review.
- [x] `[must]` CLI/API expose approval profiles through
  `adaos builder approval-profiles` and `GET /api/builder/approval-profiles`.
- [x] `[must]` Legacy draft metadata with `human_review_required=true` is treated
  as an explicit manual-review override.
- [x] `[must]` `builder_skill` consumes the first
  `builder.pending_action.response` slice for patch review responses: approved
  patches are marked with review evidence, the draft preview is refreshed, and
  the Builder dialog receives a topic-scoped status message. Full release/apply
  evidence remains in Phase 6.

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
- [x] `[must]` Make Builder draft and patch review produce Pending Actions
  before browser apply/approve flows. The current Builder skill publishes
  `builder.scenario_draft.review` and `builder.scenario_patch.review` actions
  with conversation/thread/source refs and routes responses to
  `builder.pending_action.response`.
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
  runtime change. Draft/patch review actions already carry source refs; the
  apply/release step still needs to persist the approval identity.
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
- [ ] `[must]` Treat `builder_skill` as the semantic owner of Builder
  conversations across browser, Voice/global dialog, Telegram, and Prompt IDE.
- [ ] `[must]` Make `builder_skill` consume conversation context packets,
  retrieved evidence refs, scoped memory, and Pending Actions instead of raw
  UI chat state.
- [x] `[must]` Complete the browser/Voice/Prompt IDE ownership slice:
  `builder_skill` returns canonical dialog/topic refs, emits chat with the same
  thread metadata, owns draft/patch Pending Actions, and `attach_dialog_widget`
  exposes the current workbench binding instead of a separate UI transcript.
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

Open work:

- [ ] `[must]` Define `adaos.builder.realize_request.v1` and link it to Builder
  conversations, drafts, previews, acceptance criteria, and sparse repo paths.
- [ ] `[must]` Add Root dev queue and dev-node registry contracts with
  lifecycle states, heartbeat, assignment, cancellation, timeout, retry, and
  result events.
- [ ] `[must]` Define forge task branch discipline and result evidence:
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
