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
