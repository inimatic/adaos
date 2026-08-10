# Scenario Guidance and Help Contract

Status: implemented reference contract, validated first on `tlp_research`.

Last reviewed: 2026-08-10.

AdaOS scenarios need a discoverable explanation and workflow-aware next-step guidance that carries the same meaning on web, text, and voice surfaces. This contract keeps those semantics in the scenario and its governed workflow instead of hiding them in one UI layout, channel adapter, or language-model prompt.

## Decision

A scenario may declare `guidance: adaos.scenario.guidance.v1` in `scenario.yaml`. The descriptor binds four things:

1. a versioned Markdown README owned by the scenario;
2. localized one-paragraph overviews;
3. supported presentation channels and the web modal identity;
4. one read-only skill operation that projects current workflow state and allowed next actions.

The adjacent `conversational/` package binds help and next-step intents to the same read-only operation. A deterministic matcher is sufficient for the baseline. An NLU or LLM provider may improve recognition later, but it must not become the source of workflow state or invent an action that the projection did not return.

## Manifest Contract

```yaml
conversational:
  manifest: conversational/manifest.yaml
guidance:
  schema: adaos.scenario.guidance.v1
  readme: README.md
  overview:
    en: Govern one reproducible experiment.
    ru: Управление одним воспроизводимым экспериментом.
  presentation:
    channels: [web, text, voice]
    modal_id: experiment_help
  workflow:
    state_source:
      kind: skill
      name: experiment_manager.describe_experiment
      params:
        experiment_id: $state.experimentId
    state_path: workflow.state
    actions_path: next_actions
  conversational:
    help_intent: experiment.help
    next_steps_intent: experiment.next_steps
```

README paths are relative and confined to the scenario root. The state source must be a tool of a declared scenario dependency. `$state.<path>` values are resolved from the current scenario state; missing values fail explicitly.

## Guidance Projection

The state-source tool returns a channel-neutral projection:

```json
{
  "schema": "adaos.scenario.guidance_projection.v1",
  "locale": "ru",
  "channel": "voice",
  "overview": "...",
  "workflow": {
    "state": "locked",
    "generation": 2,
    "description": "..."
  },
  "next_actions": [
    {
      "id": "start_preflight",
      "label": "Запустить CPU preflight",
      "description": "...",
      "tool": "start_experiment",
      "priority": 1
    }
  ],
  "text": "...",
  "speech_text": "...",
  "message": "..."
}
```

`text` is suitable for a chat or details view. `speech_text` is a compact linear rendering without visual assumptions. `message` selects the correct representation for the requested channel. Action identifiers are semantic and stable; channel adapters choose buttons, numbered text, or speech without changing their meaning.

## Web, Text, and Voice

The web application places Help beside its primary navigation and opens the declared modal. The modal renders the README plus a fresh state-source projection. It must not copy a static list of next steps.

The conversational package declares two read-only query affordances:

- help returns overview plus current guidance;
- next steps returns only the current state description and admitted actions.

Both invoke the same skill operation used by the modal. Voice is therefore a rendering of governed data, not a separate voice workflow. Mutating phrases such as “start the experiment” remain separate intents with their own risk and confirmation policy; they must not match a help query.

## SDK

`adaos.sdk.scenarios.read_guidance(...)` reads the static, localized document without calling a provider. `adaos.sdk.scenarios.describe_guidance(...)` additionally resolves declared state parameters and invokes the workflow-aware provider through the normal skill runtime. Cross-skill invocation requires `skills.invoke`, validates target identifiers, retains normal tool schemas and timeouts, and has bounded nesting.

## Ownership and Security

- The scenario owns wording, README, intent examples, and presentation bindings.
- The workflow or domain-manager skill owns state interpretation and next-action policy.
- A channel adapter owns only rendering and delivery.
- A tracker or executor UI is a diagnostic view and does not provide next-action authority.
- Help operations are read-only. A returned tool name is descriptive; invoking it still passes normal capabilities, input schema, workflow generation, and confirmation checks.

## TLP Reference

`tlp_research` `0.3.0` is the first conformance package. Its README is shown in `tlp_research_help`; current guidance comes from `research_manager_skill.describe_experiment`. Russian and English deterministic intents cover “what can this scenario do?” and “what should I do next?”, including a voice story. No external LLM is used.

## Adoption Checklist

- [ ] Add a concise user README beside `scenario.yaml`.
- [ ] Declare `guidance` with web, text, and voice only where each representation is actually supported.
- [ ] Implement a read-only state projection with stable action IDs and localized text/speech renderings.
- [ ] Put Help in the primary scenario navigation and bind the declared modal.
- [ ] Add conversational help and next-step query affordances using the same projection.
- [ ] Test README/modal consistency, representative workflow states, both locales, voice rendering, hard negatives, and package compilation.
