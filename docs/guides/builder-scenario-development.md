# Builder-Safe Scenario Development Guide

Status: stage-aware authoring guidance, reviewed against the Builder and
semantic Application contracts on 2026-09-22; not a completion checklist.

This guide is written for Builder workflows that create or update AdaOS
scenarios. Read it together with:

- [AdaOS Builder](../architecture/builder.md)
- [Scenarios](../scenarios.md)
- [Skill Activation and Scenario Binding](../architecture/skill-activation-and-scenario-binding.md)
- [Capability, Binding, and State Separation](../architecture/capability-binding-state-separation.md)
- [Semantic Application Composition And Evolution Roadmap](../architecture/application-semantic-composition-roadmap.md)
- [Web UI Architecture](../architecture/web-ui-architecture.md)
- [Webspace Scenario Pointer/Projection Roadmap](../architecture/webspace-scenario-pointer-projection-roadmap.md)
- [Builder-Safe Skill Development Guide](llm-skill-development.md)

## Golden Rule

Do not turn a scenario into hidden implementation code or a semantic dependency
on one skill.

A scenario should orchestrate skills, desktop surfaces, NLU affordances, and
resolved runtime bindings. Record requested behavior first as an
`ApplicationRequirement`. Reuse an admitted `CapabilityContract` and
`BindingDefinition` when available. If none exists, Automation may create an
Application-local binding implemented by a skill; only the resolved physical
closure makes the scenario depend on that skill. External IO, long-running
work, heavy state, and new permissions remain outside scenario code.

## Scenario Plan

These are Builder's internal authoring/validation facts, not a questionnaire
that the user must fill in or a demand that the Prototype model invent final
integrations. Use the [Prototype/Automation boundary](../architecture/builder-intent-to-prototype.md#stage-specific-acceptance):
local disposable CRUD may be executable during Prototype; installed persistence,
external effects and server-side rules require Automation evidence. Record
material unknowns instead of silently promoting suggestions to obligations.

Before editing a Builder-authored scenario, record:

- `intent`: what human workflow the scenario enables.
- `requirements`: semantic capability/state requirements and unresolved gaps;
  never raw tool, skill, provider, package-member, endpoint, or credential refs.
- `entrypoints`: manual, boot, event, voice/text/NLU, catalog app, modal, or
  widget launch.
- `required_skills`: resolved compatibility/runtime closure that must be
  installed and prepared before the scenario can run; not authoring-time
  semantic requirements.
- `optional_skills`: resolved runtime components that improve the scenario but
  are not hard blockers.
- `bindings`: which scenario steps, UI actions, and NLU hints point to which
  skill tools or runtime actions.
- `desktop_surface`: page, modal, widget, catalog entry, or no browser surface.
- `state_contract`: what state is projected by the scenario itself versus what
  state belongs to skills.
- `approval_risks`: new skill dependencies, endpoint control, destructive
  actions, external IO, broad NLU triggers, and secrets.
- `test_plan`: schema validation, dependency bootstrap preview, NLU phrase
  probe, action preview, and scenario runtime smoke when available.

## Dependency Bootstrap

Every scenario dependency should be previewable before apply.

Use `depends` or `runtime.skills.required` for required skills. Builder preview
must report which dependencies are present, missing, or blocked. Missing
dependencies should block apply unless the review explicitly redirects the work
to a new skill draft.

Optional capabilities should be marked as optional instead of being silently
assumed. A scenario that needs a missing optional skill must expose a degraded
state or a guided install path.

## NLU And Action Hints

Scenario-level `llm_hints` and `nlu.nlu_hints` describe how a person naturally
asks for the scenario. They should not pretend that an unavailable skill action
exists.

Use:

- `llm_hints.description` for the scenario purpose.
- `llm_hints.aliases` for short names people may use.
- `llm_hints.primary_actions` for scenario-owned actions or launch flows.
- `nlu.nlu_hints.examples` for phrase probes and Teacher repair context.
- `nlu.nlu_hints.slot_schemas` only when the scenario really owns the slot.

If the missing behavior belongs to a skill descriptor, create a
`descriptor_fix` Builder task. If the behavior requires new code, create a
`development_task` for a skill or scenario draft.

## UI Boundaries

Scenarios may compose desktop surfaces, but generated UI must stay inside stable
Web UI contracts. A scenario should not depend on client-private component
internals.

Use scenario UI for:

- page layout and catalog entries
- modal/widget composition
- scenario-level state seeds
- binding existing skill widgets into a workspace

Use skill `webui.json` for reusable skill-owned widgets, receivers, and
data-source contracts.

## Preview Checklist

Before a Builder scenario draft is eligible for human approval, preview must
show:

- manifest diff
- schema result for `scenario.schema.json`
- dependency bootstrap report
- NLU phrase probe inputs
- action preview from scenario hints
- UI preview summary for pages, modals, widgets, and data bindings
- risk summary and human-review reasons

Preview review is not stable apply or publication. It must not silently
install dependencies, mutate the production host or emit NLU dispatch events.
An explicit Preview request may materialize the exact accepted/generated
candidate and its admitted DEV runtime bindings in the owned paired preview,
under the [Preview Runtime contract](../architecture/builder-preview-runtime.md).
Read-only inspection must not create topology or change lifecycle acceptance.
