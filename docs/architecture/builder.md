# AdaOS Builder

Status: target architecture and terminology anchor.

AdaOS Builder is the role and workflow that turns an idea into governed AdaOS
artifacts: skills, scenarios, manifests, UI descriptors, NLU hints, tests, and
runtime-ready changes.

The role is intentionally not tied to one executor. A Builder may be:

- a human developer using AdaOS tools
- an AI-assisted agent using Root MCP and local repository context
- a human-in-the-loop workflow where AI drafts and humans review, approve, or
  redirect

The product phrase is:

```text
I have an idea. Let's build it.
```

The architectural term is `Builder`. Terms such as `LLM programmer` should be
treated as historical or implementation-specific wording and should point back
to this page.

## Purpose

AdaOS should let any person become a creator by giving them a governed path
from intent to working capability.

The Builder does not bypass AdaOS. It uses AdaOS contracts to create changes
that can be inspected, validated, installed, activated, observed, repaired, and
rolled back.

Core invariant:

- humans and AI can propose capability changes
- AdaOS owns deterministic validation, permission gates, runtime activation,
  observability, and rollback
- risky or ambiguous changes stay reviewable before they become durable runtime
  behavior

## Scope

The Builder owns the development path for:

- new skills
- updates to existing skills
- new scenarios
- updates to scenario flows, bindings, and desktop surfaces
- `webui.json` UI descriptors and browser-facing data contracts
- `skill.yaml` and `scenario.json` / `scenario.yaml` metadata
- NLU hints, examples, aliases, and descriptor fixes
- tests, smoke checks, runtime validation evidence, and release notes

The Builder does not own:

- direct runtime command dispatch on behalf of the user
- hidden mutation of Yjs, registries, or skill runtime state
- silent model retraining
- direct bypass of skill/scenario publication, activation, or policy gates
- operational incident handling outside the development/repair loop

Those surfaces are handled by the deterministic runtime, NLU Teacher,
Root MCP operational planes, Infrascope, and supervisor/runtime governance.

## Builder Pipeline

The target pipeline is a vertical slice across existing AdaOS architecture:

1. **Intent capture**: a person states an idea, correction, missing capability,
   or desired workflow.
2. **Context read**: Builder reads architecture, SDK, schema, template,
   registry, named-entity, current scenario, and runtime evidence through
   governed descriptors.
3. **Capability classification**: Builder decides whether the change belongs to
   a skill, scenario, UI descriptor, NLU overlay, entity alias, descriptor fix,
   or new development task.
4. **Design plan**: Builder records the artifact plan, data route plan,
   side-effect class, permissions, runtime lifecycle needs, and test strategy.
5. **Draft generation**: Builder creates or edits workspace artifacts through
   ordinary repository files and templates.
6. **Static validation**: AdaOS validates schemas, manifests, route plans,
   imports, handler boundaries, and unsafe runtime patterns.
7. **Preview**: AdaOS runs phrase probes, action previews, UI/materialization
   previews, and install/test dry-runs where available.
8. **Review gate**: a human, policy rule, or narrower auto-apply profile
   approves, rejects, or redirects the candidate.
9. **Prepare/install**: AdaOS uses skill/scenario lifecycle commands and
   runtime slots rather than hot-patching live behavior.
10. **Activate**: AdaOS activates the prepared runtime and records rollback
    evidence.
11. **Observe and repair**: guard, quarantine, NLU Teacher, status, and
    runtime diagnostics feed new Builder tasks when the design needs repair.

## Relationship To Skills

Skills remain the reusable capability unit.

Builder-created skills must follow:

- [Skills](../skills.md)
- [Skill Runtime Lifecycle](../skill_runtime.md)
- [Builder-Safe Skill Development Guide](../guides/llm-skill-development.md)
- [Skill Projection Runtime SDK](skill-projection-runtime-sdk.md)
- [Runtime Guarding](runtime-guarding.md)

The Builder must make browser-facing data routes explicit in `skill.yaml`
before choosing Yjs, streams, tools/details, skill-local storage, or disk
diagnostics. Runtime guards may warn, throttle, block, or quarantine unsafe
routes, but they should not silently redesign a skill.

## Relationship To Scenarios

Scenarios remain the orchestration and desktop/workflow unit.

Builder-created scenarios must follow:

- [Scenarios](../scenarios.md)
- [Builder-Safe Scenario Development Guide](../guides/builder-scenario-development.md)
- [Skill Activation and Scenario Binding](skill-activation-and-scenario-binding.md)
- [Webspace Scenario Pointer/Projection Roadmap](webspace-scenario-pointer-projection-roadmap.md)
- [WebIO](../interfaces/webio.md)

The Builder should decide whether an idea is:

- a reusable skill capability
- a scenario flow over existing skills
- a UI/catalog binding
- an NLU/action descriptor improvement
- a missing capability that needs a new skill or scenario artifact

## Relationship To NLU Teacher

NLU Teacher handles utterance understanding, correction, and teachable gaps.
Builder handles capability creation and artifact changes.

When NLU Teacher sees a missing capability, it should create a
`development_task` candidate for Builder instead of inventing fake intents or
pretending the action exists.

Relevant documents:

- [NLU Teacher LLM](../concepts/nlu-teacher-llm.md)
- [NLU Roadmap](../concepts/nlu-roadmap.md)
- [Named Entities and Canonical Naming](named-entities.md)

The handoff boundary is:

- `descriptor_fix`: improve existing skill/scenario/action descriptions,
  hints, examples, or slot schemas
- `development_task`: build or modify an AdaOS capability artifact
- `entity_alias`: update governed entity understanding, usually without new
  code

## Relationship To Root MCP

Root MCP is the Builder's governed machine-readable context and tool surface.
It is not the Builder itself.

The current read-only Builder context is exposed as `builder.get_context`.
It bundles descriptor provenance, architecture/SDK/template/registry summaries,
NLU authoring context, named entities, redaction policy, and no-write authoring
boundaries.

Builder reads from:

- `AdaOSDevPlane`: architecture, SDK metadata, schemas, template catalog,
  public skill/scenario registry, and named entities
- `NLUAuthoringPlane`: current action surface, phrase checks, traces, dialog
  context, training targets, templates, and patch previews

Relevant documents:

- [Root MCP Foundation](root-mcp-foundation.md)
- [Root MCP Roadmap](root-mcp-roadmap.md)

Root MCP should expose enough context for Builder to reason without scraping
the repository blindly. Writes through Root MCP must remain governed,
capability-scoped, audited, and previewable.

## Builder Contracts

Builder work should move through explicit contracts before runtime mutation:

- `src/adaos/abi/builder.task.v1.schema.json`: handoff packet for human ideas,
  NLU Teacher gaps, runtime guard reports, and repair requests.
- `src/adaos/abi/builder.draft.v1.schema.json`: draft workspace metadata that
  links a task to an artifact, selected template, assumptions, risks, expected
  tests, and quality gates.
- `src/adaos/abi/skill.schema.json`, `src/adaos/abi/scenario.schema.json`, and
  `src/adaos/abi/webui.v1.schema.json`: artifact contracts that now carry
  Builder-oriented `llm_hints` and `nlu_hints`.

The default skill and scenario templates include `builder.draft.json` metadata
so generated work starts as a reviewable draft rather than an active runtime
change.

The first implemented write-neutral Builder surface is:

- `adaos builder draft`: creates an isolated draft workspace under Builder
  control while using the existing CTX dev artifact roots
  (`.adaos/dev/<subnet>/skills/<id>` or
  `.adaos/dev/<subnet>/scenarios/<id>`). `builder.draft.json` is written into
  the dev artifact, and `state/builder/drafts` only keeps an index by
  `draft_id`.
- `adaos builder preview`: creates an inspectable preview bundle with diff,
  schemas, route plan, NLU/action/UI preview summaries, static safety checks,
  dependency bootstrap evidence, review-policy evidence, and human-review
  reasons. Preview records are service metadata under `state/builder/previews`,
  not an alternate source tree.
- `POST /api/builder/draft` and `POST /api/builder/preview`: HTTP equivalents
  for local UI/workbench integration.
- `GET /api/builder/approval-profiles` and
  `adaos builder approval-profiles`: expose the current Builder approval
  profiles for UI, CLI, and workbench surfaces.

Preview accepts an approval profile:

- `manual_only`: every preview requires explicit human review before apply.
- `low_risk_auto_draft`: Builder may draft and preview, but apply remains a
  human decision.
- `low_risk_auto_apply`: only clean low-risk previews without mandatory review
  classes are eligible for automatic apply.
- `restricted_maintenance_repair`: only narrow descriptor, NLU-hint, and
  metadata repairs can be eligible without review.

Mandatory human-review classes are:

- secrets or credential-like material
- new permissions or capability declarations
- external IO
- destructive actions
- endpoint, route, tunnel, browser, or control-plane control
- high-rate streams or projections
- broad NLU patterns
- service or process management

`review_policy` in the preview bundle records the chosen profile, detected
mandatory classes, policy blocks, eligibility decision, and evidence. Older
drafts with `metadata.human_review_required=true` are treated as an explicit
manual-review override.

Builder also exposes an operational CLI facade over the existing dev lifecycle:

- `adaos builder create <id> --kind skill|scenario`: creates the artifact through
  the same owner dev workspace flow as `adaos dev skill|scenario create`.
- `adaos builder list --kind skill|scenario`: lists the same dev artifacts the
  owner workspace already manages.
- `adaos builder validate <id> --kind skill|scenario`: delegates to the dev
  skill/scenario validators, including JSON scenario manifests created by
  Builder drafts.
- `adaos builder push <id> --kind skill|scenario`: uploads through the existing
  Forge dev push path. It does not replace activation, install, approval, or
  runtime apply gates.

This facade is intentionally not a new storage layer. It exists so Builder
work can be driven from one command branch while source ownership remains in
the current dev workspace and lifecycle tools.

## Relationship To Web UI

Builder may create browser-facing UI descriptors, but the browser runtime owns
rendering mechanics.

Builder must respect:

- [Web UI Architecture](web-ui-architecture.md)
- [UI Addressing](ui-addressing.md)
- [Semantic State Plane](semantic-state-plane.md)
- [Skill Assets and Icons Roadmap](skill-assets-and-icons-roadmap.md)

Generated UI should use stable `webui.v1` and semantic descriptors rather than
client-private assumptions. Data shown in widgets and modals must be routed
through declared Yjs projections, stream receivers, details tools, or local
diagnostic surfaces.

## Relationship To Runtime Governance

Builder output is only useful if AdaOS can operate it safely.

The runtime must provide:

- schema validation
- import/smoke tests
- skill runtime prepare/test/activate/rollback
- scenario install/validate/run/test
- route, memory, stream, and Yjs guard evidence
- lifecycle diagnostics and quarantine summaries
- status/notification projections that explain failure without hiding it

Relevant documents:

- [AdaOS Supervisor](adaos-supervisor.md)
- [Runtime Guarding](runtime-guarding.md)
- [Operational Event Model](operational-event-model.md)
- [Post-Deploy E2E Testing](post-deploy-e2e-testing.md)

## Source Of Truth

This page owns the Builder role, terminology, and end-to-end capability
creation boundary.

Other documents should describe their local projection of Builder:

- skill docs describe what a Builder-authored skill must satisfy
- scenario docs describe scenario authoring and activation constraints
- NLU docs describe when Teacher creates Builder handoff candidates
- Root MCP docs describe the governed context and tool planes Builder consumes
- runtime docs describe how Builder output is validated and activated

If a document needs to mention an AI-assisted programmer, use `Builder` and
link back here.
