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
- Specialized roadmaps continue to own detailed sequencing for their domains.
- Builder milestones should link out to domain roadmaps instead of duplicating
  every low-level checklist.

## Phase 0. Terminology And Ownership

- [x] Adopt `Builder` as the canonical role name.
- [x] Define Builder as executor-neutral: human, AI-assisted, or hybrid.
- [x] Treat `LLM programmer` as historical wording and replace it in
  documentation surfaces.
- [x] Create this roadmap and [Builder](builder.md) as the terminology anchor.
- [x] Add a short glossary entry in product terminology once product naming is
  ready.

Phase is complete when all architecture and developer docs point to Builder
for capability creation terminology.

## Phase 1. Read-Only Context Surface

Goal: Builder can understand AdaOS without guessing.

Current implementation slices:

- [x] Root MCP foundation exists.
- [x] `AdaOSDevPlane` exposes architecture, SDK metadata, template catalog,
  public skill registry, public scenario registry, and named entities.
- [x] `NLUAuthoringPlane` exposes current action context, phrase check,
  traces, dialog context, training targets, templates, and patch preview
  surfaces.
- [x] Skill and scenario schemas exist under `src/adaos/abi/`.
- [x] `llm_hints` / `nlu_hints` are partially consumed through skill/scenario
  descriptors and `webui.json`.
- [x] `builder.get_context` exposes a compact read-only Builder context bundle
  through Root MCP.
- [x] Builder task and draft schemas are published as Root MCP descriptor sets
  with provenance.

Open work:

- [x] Freeze initial `llm_hints` / `nlu_hints` schemas for skills, scenarios, and
  `webui.json`.
- [x] Make Root MCP descriptor freshness and provenance visible in Builder
  task context.
- [x] Add a compact Builder context bundle that links architecture, SDK,
  templates, registries, current webspace, NLU context, and runtime status.
- [x] Add redaction policy for Builder prompt/context bundles.

Primary references:

- [Root MCP Foundation](root-mcp-foundation.md)
- [Root MCP Roadmap](root-mcp-roadmap.md)
- [NLU Roadmap](../concepts/nlu-roadmap.md)
- [Roadmap Inventory](roadmap-inventory.md)

## Phase 2. Task And Candidate Model

Goal: missing capabilities become structured Builder work, not fake runtime
actions.

Current implementation slices:

- [x] NLU Teacher emits `descriptor_fix` candidates.
- [x] NLU Teacher emits `development_task` candidates.
- [x] Teacher state persists candidates and event evidence.
- [x] Root MCP exposes phrase checks and action context used to avoid inventing
  unavailable actions.
- [x] `builder.task.v1` defines the first structured Builder handoff packet.
- [x] NLU Teacher attaches Builder tasks to `descriptor_fix` and
  `development_task` candidates.

Open work:

- [x] Define Builder task schema with requested behavior, source utterance,
  context snapshot, target artifact hints, side-effect class, privacy notes,
  and acceptance evidence.
- [x] Link `development_task` candidates to Builder tasks.
- [x] Link `descriptor_fix` candidates to Builder tasks that target
  manifest/webui/nlu hint surfaces.
- [ ] Add concrete patch materialization for `descriptor_fix` tasks across
  manifest, `webui.json`, and NLU hint files.
- [x] Add candidate lifecycle states shared by Teacher UI and Builder:
  `proposed`, `accepted`, `drafting`, `previewed`, `approved`, `applied`,
  `rejected`, `rolled_back`, and `superseded`.
- [ ] Link completed Builder tasks back to the originating Teacher candidate or
  user idea.

Primary references:

- [NLU Teacher LLM](../concepts/nlu-teacher-llm.md)
- [NLU Roadmap](../concepts/nlu-roadmap.md)
- [Operational Event Model](operational-event-model.md)

## Phase 3. Draft Generation Rails

Goal: Builder can create skill/scenario drafts through stable templates and
schemas.

Current implementation slices:

- [x] Skill scaffold exists.
- [x] Scenario scaffold exists.
- [x] Skill and scenario templates exist.
- [x] Skill manifest supports `data_routes` and `data_projections`.
- [x] Skill runtime supports prepare/test/activate/rollback.
- [x] Scenario manager supports install/validate/run/test and dependency
  bootstrap.
- [x] `builder.draft.v1` defines draft workspace metadata before runtime apply.
- [x] Default skill and scenario templates include `builder.draft.json`
  metadata.

Open work:

- [x] Create a Builder draft workspace contract distinct from active runtime
  slots.
- [x] Define draft metadata: task id, source idea, selected template,
  target artifact, assumptions, risk notes, and expected tests.
- [ ] Add `adaos builder draft` or equivalent API/CLI route after the draft
  contract stabilizes.
- [x] Make skill/scenario scaffolds Builder-aware: hints, route plan skeleton,
  tests, lifecycle hooks, and webui descriptors.
- [ ] Provide scenario-specific Builder guidance matching the skill guide.
- [x] Add template quality gates so templates are safe defaults for generated
  work.

Primary references:

- [Skills](../skills.md)
- [Scenarios](../scenarios.md)
- [Skill Runtime Lifecycle](../skill_runtime.md)
- [Builder-Safe Skill Development Guide](../guides/llm-skill-development.md)

## Phase 4. Validation And Preview

Goal: Builder changes are inspectable before they mutate durable runtime
behavior.

Current implementation slices:

- [x] Skill runtime can prepare, test, activate, and rollback.
- [x] Scenario install/update APIs can use async operation records.
- [x] NLU phrase probe exists.
- [x] Root MCP exposes `nlu_authoring.check_phrase`.
- [x] Root MCP exposes NLU template patch preview.
- [x] Runtime guards and status cards provide initial safety evidence.

Open work:

- [ ] Add Builder preview bundle: diff, schemas, route plan, NLU probe,
  action preview, UI preview, test plan, and risk summary.
- [ ] Add blast-radius preview for learned regex and action descriptor changes.
- [ ] Add browser/webui preview for generated widgets, modals, and data
  bindings.
- [ ] Add static checks for unsafe direct Yjs mutation and unbounded process
  memory in generated skills.
- [ ] Add route-budget validation for `data_routes`, streams, and projections.
- [ ] Add previewable scenario dependency bootstrap report.

Primary references:

- [Runtime Guarding](runtime-guarding.md)
- [Skill Projection Runtime SDK](skill-projection-runtime-sdk.md)
- [Post-Deploy E2E Testing](post-deploy-e2e-testing.md)
- [Web UI Architecture](web-ui-architecture.md)

## Phase 5. Human-In-The-Loop Apply

Goal: Builder can accelerate creation without removing human authority where it
matters.

Open work:

- [ ] Define approval profiles: manual-only, low-risk auto-draft,
  low-risk auto-apply, and restricted maintenance repair.
- [ ] Define which changes always require human approval: secrets, new
  permissions, external IO, destructive actions, endpoint control, high-rate
  streams, broad NLU patterns, and service processes.
- [ ] Add review UI/workbench for Builder tasks and previews.
- [ ] Attach policy evidence and approval identity to every applied Builder
  change.
- [ ] Support reject/redirect feedback that becomes new Builder context instead
  of being lost as chat history.

Primary references:

- [Authority and Degraded Mode](authority-and-degraded-mode.md)
- [Root MCP Foundation](root-mcp-foundation.md)
- [Infrascope](infrascope.md)

## Phase 6. Runtime Activation And Rollback

Goal: Builder output lands through normal AdaOS lifecycle rails.

Current implementation slices:

- [x] Skill runtime has A/B slots, semantic buckets, lifecycle hooks,
  deactivation, quarantine, and rollback.
- [x] Scenario manager handles dependency bootstrap and webspace rebuild.
- [x] Runtime operations and notifications are projected into Yjs.

Open work:

- [ ] Make Builder apply create a release record linking draft, validation,
  approval, runtime slot, and rollback target.
- [ ] Add durable operation recovery for long Builder install/test/apply flows.
- [ ] Define rollback UX for Builder-authored changes across skill,
  scenario, NLU overlay, and entity alias surfaces.
- [ ] Add post-activation checks that can route failures back to Builder repair
  tasks.

Primary references:

- [Skill Runtime Lifecycle](../skill_runtime.md)
- [Registry, Marketplace, and Operations Roadmap](registry-marketplace-operations-roadmap.md)
- [AdaOS Supervisor](adaos-supervisor.md)

## Phase 7. Observation And Repair Loop

Goal: runtime evidence becomes actionable improvement work.

Current implementation slices:

- [x] Runtime guards can produce diagnostics and quarantine evidence.
- [x] NLU Teacher stores misses, candidates, and LLM audit fingerprints.
- [x] Root MCP audit and target status exist.

Open work:

- [ ] Convert guard/quarantine reports into Builder repair tasks when the
  issue is design-time fixable.
- [ ] Feed failed tests, import errors, route pressure, memory growth, and NLU
  misses into task context.
- [ ] Add repair task deduplication and supersession.
- [ ] Add acceptance evidence that proves the repaired capability now works and
  did not regress the triggering behavior.

Primary references:

- [Runtime Guarding](runtime-guarding.md)
- [Operational Event Model Roadmap](operational-event-model-roadmap.md)
- [Root MCP Roadmap](root-mcp-roadmap.md)

## Phase 8. Product Experience

Goal: a non-specialist can say what they want and safely become a creator.

Open work:

- [ ] Define the first user-facing Builder entrypoint.
- [ ] Support the phrase-level flow: "I have an idea. Let's build it."
- [ ] Provide guided clarification when the idea is underspecified.
- [ ] Show assumptions, preview, risks, and expected behavior in non-specialist
  language.
- [ ] Keep advanced diffs, schemas, route plans, and runtime evidence available
  for developers.
- [ ] Make completed Builder work visible in catalog, scenario, and skill
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
- [Scenarios](../scenarios.md): scenario lifecycle basics
- [Web UI Architecture](web-ui-architecture.md): browser-facing generated UI
- [Runtime Guarding](runtime-guarding.md): guard/quarantine feedback into repair
