# Builder-Safe Scenario Development Guide

Status: current guidance and target contract.

This guide is written for Builder workflows that create or update AdaOS
scenarios. Read it together with:

- [AdaOS Builder](../architecture/builder.md)
- [Scenarios](../scenarios.md)
- [Skill Activation and Scenario Binding](../architecture/skill-activation-and-scenario-binding.md)
- [Web UI Architecture](../architecture/web-ui-architecture.md)
- [Webspace Scenario Pointer/Projection Roadmap](../architecture/webspace-scenario-pointer-projection-roadmap.md)
- [Builder-Safe Skill Development Guide](llm-skill-development.md)

## Golden Rule

Do not turn a scenario into hidden skill code.

A scenario should orchestrate skills, desktop surfaces, NLU affordances, and
bindings. If the requested behavior needs reusable capability, external IO,
long-running work, heavy state, or new permissions, create or update a skill
draft and make the scenario depend on it.

## Scenario Plan

Before editing a Builder-authored scenario, record:

- `intent`: what human workflow the scenario enables.
- `entrypoints`: manual, boot, event, voice/text/NLU, catalog app, modal, or
  widget launch.
- `required_skills`: skills that must be installed and prepared before the
  scenario can run.
- `optional_skills`: capabilities that improve the scenario but are not hard
  blockers.
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

The preview is not an apply. It must not install dependencies, mutate the active
webspace, emit NLU dispatch events, or activate skill runtimes.
