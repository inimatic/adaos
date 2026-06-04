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
| 5. Human Review | Partial: approval profiles and mandatory human-review classes are enforced in preview; applied-change evidence remains open. | Open: review workbench and reject/redirect feedback. | None. | None. |
| 6. Activation | Open: release record and post-activation repair routing. | Open: durable operation recovery and rollback UX. | None. | None. |
| 7. Repair Loop | Open: guard/test/route/memory/NLU evidence into Builder repair tasks and acceptance evidence. | Open: repair deduplication/supersession. | None. | None. |
| 8. Product Experience | Open: first entrypoint, phrase-level build flow, non-specialist preview language. | Open: guided clarification and developer evidence views. | Open: catalog/scenario/skill history. | None. |

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
- [x] `[must]` CLI/API expose approval profiles through
  `adaos builder approval-profiles` and `GET /api/builder/approval-profiles`.
- [x] `[must]` Legacy draft metadata with `human_review_required=true` is treated
  as an explicit manual-review override.

Open work:

- [x] `[must]` Define approval profiles: manual-only, low-risk auto-draft,
  low-risk auto-apply, and restricted maintenance repair.
- [x] `[must]` Define which changes always require human approval: secrets, new
  permissions, external IO, destructive actions, endpoint control, high-rate
  streams, broad NLU patterns, and service processes.
- [ ] `[should]` Add review UI/workbench for Builder tasks and previews.
- [ ] `[must]` Attach policy evidence and approval identity to every applied Builder
  change.
- [ ] `[should]` Support reject/redirect feedback that becomes new Builder context instead
  of being lost as chat history.

Primary references:

- [Authority and Degraded Mode](authority-and-degraded-mode.md)
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

- [ ] `[must]` Define the first user-facing Builder entrypoint.
- [ ] `[must]` Support the phrase-level flow: "I have an idea. Let's build it."
- [ ] `[should]` Provide guided clarification when the idea is underspecified.
- [ ] `[must]` Show assumptions, preview, risks, and expected behavior in non-specialist
  language.
- [ ] `[should]` Keep advanced diffs, schemas, route plans, and runtime evidence available
  for developers.
- [ ] `[could]` Make completed Builder work visible in catalog, scenario, and skill
  history.

## Cross-Document Anchors

Builder is intentionally cross-cutting. Detailed work remains in:

- [Builder](builder.md): role, pipeline, and source-of-truth terminology
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
