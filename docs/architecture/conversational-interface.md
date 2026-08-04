# Conversational Control Interface

Status: target architecture and cross-domain roadmap for conversational input,
semantic output, NLU data, Builder authoring, and conversation-story tests.

AdaOS treats conversation as a governed control surface, not as a second source
of truth. A user may speak or type freely, but any durable mutation must pass
through explicit semantic records, workflow admission, policy checks, evidence,
and package/version ownership.

Related documents:

- [Governed Data-Driven Workflow Model and Interaction Architecture](governed-workflow-runtime.md):
  workflow definitions, commands, guards, interactions, reply routes, and
  effect/admission invariants
- [Builder Conversational Development Architecture](builder-conversational-development.md):
  Builder `Project -> Issue -> Change -> Run -> Revision -> Trial -> Release`
  model and context packets
- [Conversation and Channel Architecture](conversation-and-channel-architecture.md):
  durable conversations, channels, presentation negotiation, reply routing, and
  delivery attempts
- [NLU Roadmap Checklist](../concepts/nlu-roadmap.md) and
  [NLU Teacher Evolution Roadmap](../concepts/nlu-evolution-roadmap.md):
  NLU runtime, Teacher candidates, promotion, and regression gates
- [Builder Roadmap](builder-roadmap.md) and
  [Governed Data-Driven Workflow Model Roadmap](governed-workflow-runtime-roadmap.md):
  implementation sequencing and proof gates

## Decision Summary

AdaOS introduces a **Conversational Control Protocol** for turning human input
into governed workflow and artifact changes:

```text
channel event
  -> normalized turn
  -> IntentProposal evidence
  -> DialogFrame / ConversationInteraction when clarification is needed
  -> WorkflowCommand or BuilderTask candidate
  -> workflow admission, effect, Run, evidence
  -> ConversationOutput
  -> channel-specific presentation and delivery
```

The protocol is broader than NLU. NLU engines, LLMs, deterministic controls,
forms, Telegram callbacks, browser actions, and future skill-specific agents all
enter through the same semantic boundary. The workflow runtime remains the only
authority for mutating workflow state.

The target product loop is:

```text
human intent
  -> executable model: workflow + conversation story + web prototype
  -> bounded Builder Change
  -> implementation Run by human, Codex, Claude, or another executor
  -> evidence, review, trial, release
```

This is the main advantage over direct coding-agent access to a repository:
models work against an inspectable product model and acceptance evidence instead
of treating code and recent chat as the whole system.

## Reference Pattern Baseline

This architecture intentionally reuses established international patterns but
keeps AdaOS as the authority for state, source, and effects:

| Reference pattern | Source | AdaOS interpretation |
| --- | --- | --- |
| Agent loop, tools, handoffs, guardrails, sessions, tracing, and approval pauses | [OpenAI Agents SDK](https://developers.openai.com/api/docs/guides/agents) and [Claude Code Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) | Executors and specialist agents are `Run` backends with bounded context, not owners of product state. |
| Tool/context protocol, authorization, elicitation, resources, prompts, and async tasks | [MCP 2026-07-28 specification](https://modelcontextprotocol.io/specification/2026-07-28) | MCP is an adapter and discovery plane for context/tools; workflow, package, and Builder records remain AdaOS-owned. |
| Durable graph state, human interrupts, time travel, and thread/checkpoint memory | [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) and [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) | AdaOS needs resumable interactions and replayable story/trace evidence, but the canonical command journal is not a model graph transcript. |
| Durable execution, Signals, Queries, Updates, idempotency, and deterministic replay | [Temporal messages](https://docs.temporal.io/handling-messages) and [workflow definition](https://docs.temporal.io/workflow-definition) | Long waits and human decisions need journaled state and idempotent ingress; external engines are optional adapters after the semantic proof. |
| Statecharts, actors, invoked work, guards, and visual inspection | [XState actors](https://stately.ai/docs/state-machine-actors) and [invoke](https://stately.ai/docs/invoke) | Workflow definitions should be inspectable statecharts with registered activities, not hidden handler branches. |
| LLM understanding separated from task execution, flows, and repair patterns | [Rasa CALM](https://rasa.com/docs/learn/concepts/calm/), [flows](https://rasa.com/docs/reference/primitives/flows/), and [conversation patterns](https://rasa.com/docs/learn/concepts/conversation-patterns/) | NLU/LLM produces interpretation and repair evidence; deterministic workflow/flows execute. |
| Versioned conversational agents, test cases, coverage, and continuous deployment gates | [Dialogflow CX continuous tests](https://docs.cloud.google.com/dialogflow/cx/docs/concept/continuous-tests) and [versions/environments](https://docs.cloud.google.com/dialogflow/cx/docs/concept/version) | Conversation stories are package tests and release gates, not one-off chat logs. |
| Recognizers, dialog stack, adaptive inputs, and language generation templates | [Bot Framework dialogs](https://learn.microsoft.com/en-us/azure/bot-service/bot-builder-concept-dialog?view=azure-bot-service-4.0) and [adaptive recognizers](https://learn.microsoft.com/en-us/azure/bot-service/adaptive-dialog/adaptive-dialog-prebuilt-recognizers?view=azure-bot-service-4.0) | Dialog frames, input binding, and output templates are first-class design-time artifacts. |
| Coding agents operating in repos, branches, instructions, hooks, permissions, and PR review | [Codex CLI](https://learn.chatgpt.com/docs/codex/cli), [Codex code review](https://learn.chatgpt.com/docs/third-party/github), [AGENTS.md](https://agents.md/), and [GitHub Copilot cloud agent](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/about-cloud-agent) | Direct coding agents are useful implementation workers, but AdaOS raises the control plane to Issues, Changes, Runs, stories, trials, and releases. |

The baseline should be revisited when a major agent, MCP, conversational AI, or
durable-workflow spec changes. Updating this matrix may change roadmap priority,
but it must not silently change AdaOS authority boundaries.

## Ownership Boundaries

| Plane | Owns | Must not own |
| --- | --- | --- |
| Workflow | legal states, transitions, guards, effects, risk, generation, and evidence requirements | natural-language interpretation, transport rendering, or raw transcript storage |
| Conversation | durable thread, channel, reply route, interaction, response, presentation, and delivery records | business workflow truth or package source truth |
| NLU runtime | parsing, ranking, canonicalization, confidence, abstain/reject, and proposal evidence | protected effects, workflow mutation, or source artifact mutation |
| Dialog runtime | clarification, repair, follow-up focus, cancellation, slot/form state, and bounded answer routing | permanent workflow state or NLU training source |
| NLU Teacher | runtime diagnosis, learning candidates, descriptor/capability gap candidates, and promotion evidence | direct edits to workflow definitions, skills, scenarios, public catalogs, or git-tracked source |
| Builder | design-time source patches, validation, tests, review, trials, releases, and promotion of candidates | unbounded runtime personalization or hidden Teacher memory |
| Channel adapters | rendering, fallback shape, receipt handling, and transport-specific delivery | command meaning, authorization, or retry of effects |
| Executors | one bounded Run against an admitted context packet | choosing product state, relaxing guards, or reading unbounded chat/code context |

## Conversational Input Contract

Raw user input is never a durable decision by itself. The input contract moves
from free text or UI control into semantic evidence and then into workflow
commands only after mediation:

1. A channel creates a normalized turn with actor, locale, route, active
   conversation, channel capability profile, and idempotency metadata.
2. Deterministic controls may submit an `InteractionResponse` directly when
   they carry a valid action token, target refs, and workflow generation.
3. Free text is interpreted by rule, NLU, neural provider, LLM, or skill-owned
   parser as one or more `IntentProposal` records.
4. A proposal can resolve to an answer for a pending interaction, a workflow
   command, a feedback/correction act, a new Issue, a Builder task candidate, a
   clarification request, or a rejection.
5. Protected or mutating behavior must pass through workflow admission and any
   required confirmation/review. NLU confidence is rationale, not authority.
6. Ambiguity, interruption, correction, cancellation, and underspecified input
   belong to DialogFrame/repair policy and must not be implemented as ad hoc
   fallback strings in individual channels.

The same contract covers Builder chat, voice, Telegram, browser controls,
skill-owned dialogs, and future agent handoffs.

## Core And Skill Integration

The shared SDK contract must let a skill or scenario participate in conversation
without creating a private NLU or dialog authority.

A conversational package publishes:

| Source | Required content | Consumed by |
| --- | --- | --- |
| `conversational/manifest.yaml` | package version, owner refs, workflow refs, source file list, locales, compiled-output refs, privacy defaults, and compatibility aliases | Builder, package admission, runtime compiler |
| `input.yaml` | intent/capability descriptions, slot schemas, hard-negative classes, confidence/abstain policy, and links to workflow commands or read-only query handlers | NLU runtime, Teacher, story runner |
| `affordances.yaml` | stable user-facing controls, nested targets, side-effect class, required capabilities, permission refs, preconditions, and presentation hints | NLU context, interaction projection, Builder |
| `entities.yaml` | exposed entity types, alias scope, canonical refs, privacy, ambiguity policy, and allowed actions | entity resolver, NLU providers, Teacher |
| `repair.yaml` | no-match, no-input, correction, interruption, disambiguation, cancel, resume, slot-change, and retry policy | dialog runtime, story runner |
| `output.yaml` | semantic response kinds, result modes, action groups, field templates, explanation slots, sensitivity, and channel fallback hints | response planner, channel adapters |
| `examples.yaml` | positive, negative, ambiguous, locale, and STT-noisy examples with source/provenance | provider build, Teacher, evaluation |
| `matchers.yaml` | optional deterministic exact, keyword, and regex matchers with intent, locale, slots, and provenance | deterministic NLU stage, Teacher promotion, evaluation |
| `tests/stories/*.yaml` | expected dialog/workflow paths and semantic output assertions | CI, Builder validation, release gates |

The SDK should expose stable ports rather than provider-specific internals:

- `conversational.validate(package_ref, profile)`: static package validation
  and graph/story coverage report.
- `conversational.compile(package_ref, target)`: derived provider/runtime
  artifacts with source digests and rollback refs.
- `conversation.create_interaction(...)`: bounded human input/action request.
- `conversation.emit_output(...)`: semantic output emission before rendering.
- `intent.propose(...)`: deterministic or model-backed proposal evidence.
- `workflow.explain(...)` and `workflow.invoke(...)`: single workflow command
  authority.
- `teacher.record_candidate(...)`: runtime learning candidate with evidence.
- `builder.open_change_for_candidate(...)`: promotion from candidate to
  design-time patch.

Generated skills may own domain parsers or specialized agents, but those
components return proposals, outputs, or task evidence through these ports. They
do not dispatch protected effects, mutate package source, or write transport
messages directly as the target path.

## Conversational Output Contract

Output must be semantic before it is channel-specific. A channel adapter should
not decide what the system means; it should render a negotiated presentation of
an already-typed response.

`ConversationOutput` is the target intermediate representation for user-visible
responses:

```yaml
schema: adaos.conversation.output.v1
kind: clarification | confirmation | accepted | progress | result | repair | refusal | handoff
audience: user | developer | operator
risk_level: none | low | medium | high | destructive
conversation_ref: conversation:...
workflow_ref: workflow:...
state_ref: state:...
change_ref: change:...
run_ref: run:...
summary: human-readable short text
details:
  - label: Current state
    value: prototype_review
actions:
  - id: approve
    command: builder.change.accept_trial
    risk_level: medium
    target_refs: []
fields: []
evidence_refs: []
next_expected_input:
  kind: action | text | form | none
channel_constraints:
  preferred: web
  fallbacks: [telegram, text]
```

The response planner may produce compact and rich variants, but both variants
must point to the same semantic output identity. Delivery retry may repeat
rendering and transport send; it must not re-run an LLM, tool, Codex task, or
workflow effect.

The current implementation exposes this as a pure ABI bridge in
`adaos.services.conversational_runtime`: it validates `IntentProposal`,
constructs workflow and skill invocation proposals, converts one proposed
workflow act into the canonical `WorkflowInvocation`, builds
`ConversationOutput` from workflow execution results, and derives a
`ResponseEnvelope` ref/materialization record without touching durable stores or
LLM providers.

## NLU Data Boundary

NLU data must stop being scattered across runtime state, prompts, scenarios,
skills, model files, and Teacher memory. AdaOS uses three storage planes.

### Design-Time Conversational Package

Git-versioned source lives beside the skill or scenario and is reviewed through
Builder:

```text
skill-or-scenario/
  skill.yaml | scenario.yaml
  workflow.json
  conversational/
    manifest.yaml
    input.yaml
    output.yaml
    entities.yaml
    affordances.yaml
    examples.yaml
    matchers.yaml            # optional deterministic baseline
    repair.yaml
    locale.en.yaml
    locale.ru.yaml
    tests/
      stories/
        create-first-change.yaml
      regressions.yaml
```

This package owns reusable intents, examples, aliases, entity exposure, slot
schemas, repair rules, output templates, result modes, side-effect classes,
locale coverage, and conversation stories. It is the source of truth for
catalog-ready behavior.

Compiled routers, embeddings, model indexes, optimized prompts, provider
bundles, and runtime caches are derived artifacts. They may be packaged for
release, but they must not be edited as source.

### Runtime Specialization

Node, user, webspace, or organization-specific learning lives in runtime
storage:

- private aliases and vocabulary;
- preferred result modes and output style;
- recent disambiguations and negative examples;
- channel/device constraints;
- scoped behavior bindings and rollback pointers.

Runtime specialization can improve ranking and reduce repeated clarification,
but it cannot silently become reusable package truth.

### Teacher Candidate Store

NLU Teacher records bridge runtime observation and design-time source:

- observed failures and successful clarifications;
- `entity_alias`, `example_candidate`, `descriptor_fix`, and
  `development_task` candidates;
- candidate scope, privacy, provenance, evidence, confidence, confirmation
  state, regression status, and rollback path;
- promotion target: private runtime overlay, webspace/team overlay,
  owner-artifact candidate, repository patch, or public catalog candidate.

Teacher candidates are durable evidence. Builder decides whether a candidate
becomes a git-versioned source patch.
Operator-approved Teacher examples are first written to
`adaos.nlu.teacher_overlay_store.v1` under node-local runtime state. For
skill/scenario targets the same save creates an
`adaos.nlu.teacher_promotion_candidate.v1` record that names the Builder Change
request, an allowed `conversational/examples.yaml` or
`conversational/matchers.yaml` patch, acceptance criteria, and source overlay
evidence. Runtime overlays can improve local recognition before review, but
git-versioned package source changes only through Builder.

### Candidate Lifecycle

The Teacher candidate lifecycle is the bridge between runtime learning and
design-time evolution:

```text
observed
  -> proposed
  -> scoped
  -> previewed
  -> user_or_operator_confirmed
  -> local_overlay_applied
  -> replay_verified
  -> promotion_candidate
  -> Builder Change
  -> package_patch_validated
  -> trialed
  -> released | rejected | quarantined | rolled_back
```

Every state change carries:

- source turn/trace refs and normalized text;
- affected skill/scenario/workflow/entity/output/story ids;
- privacy and portability scope;
- human confirmation or rejection evidence;
- provider/model/prompt/context digests when a model participated;
- static validation and story-runner results before promotion;
- rollback target and supersession link.

The lifecycle supports a useful middle ground: a user can benefit from a local
overlay quickly, while reusable artifacts still move through Builder, git,
tests, and release gates.

## Conversational Artifact Pipeline

The SDK should expose one pipeline used by Builder design-time authoring,
Teacher promotion, CI, and package publication:

```text
collect sources
  -> draft/generate
  -> normalize
  -> static validate
  -> run conversation stories
  -> simulate workflow paths
  -> compile/package
  -> publish/promote
```

Required checks:

- schema validity for every conversational source file;
- referential integrity between intents, entities, affordances, output templates,
  workflow commands, result modes, and side-effect classes;
- locale coverage for user-visible prompts, actions, repair text, and outputs;
- protected-action confirmation and policy coverage;
- ambiguity and hard-negative examples for risky commands;
- conversation-story regression over expected NLU proposal, dialog repair,
  workflow command, state transition, semantic output, and channel fallback;
- package admission that rejects unreferenced or duplicate source files and
  records exact digest/provenance.

Builder uses this pipeline to author and evolve design-time conversational
artifacts. NLU Teacher uses it indirectly by submitting candidates with enough
evidence to create a Builder Change.

Compiled artifacts must remain traceable to source:

- deterministic routers and regex tables record source file digests;
- neural/Rasa/provider training bundles record dataset and model ids;
- embeddings/indexes record build parameters and compatible runtime version;
- prompt packs record allowed source sections and redaction policy;
- rollout metadata records active version, previous version, and rollback ref.

Runtime may select a compiled artifact by node/profile/locale/channel, but the
selection must point back to a reviewed package or explicitly scoped local
overlay.

## Conversation Stories

Conversation stories are human-readable executable paths through the workflow
and dialog graph. They are useful for testing, design review, onboarding, and
future education-on-the-go because they explain how a human is expected to
control the system.

A story is not a transcript snapshot. It is a specification of expected
semantic behavior:

```yaml
id: builder.create_report_app.en.happy_path
title: Create a reporting application from chat
workflow: adaos.builder.change.v1
locale: en
channel: web
start:
  project_ref: project:test-reporting
  state: idle
steps:
  - user: "Build a small reporting app for monthly sales."
    expect:
      proposal:
        kind: workflow_command
        command: builder.change.plan
      output:
        kind: clarification
        asks_for: [data_source, audience]
  - user: "Use the CRM demo data. It is for managers."
    expect:
      command: builder.change.start_prototype
      state: prototype_in_progress
      output:
        kind: accepted
        actions: [show_process]
  - event:
      activity: prototype_revision_recorded
      revision_ref: revision:ui-001
    expect:
      state: prototype_review
      output:
        kind: result
        actions: [approve, request_changes]
```

Story assertions should prefer stable semantic fields over exact prose:

- proposal kind, command id, target refs, and confidence band;
- dialog frame, missing slots, offered choices, and repair outcome;
- admitted or rejected workflow command and reason code;
- workflow state/generation before and after the command;
- `ConversationOutput.kind`, actions, fields, evidence refs, and expected next
  input;
- channel presentation class and fallback, not every rendered word.

Runtime traces can be promoted into story candidates when they reveal a common
path, a failure, or a teachable pattern. A story candidate follows the same
Teacher -> Builder Change -> validation -> release path as other conversational
artifacts.

### Story Runner Contract

The first story runner should be deterministic and side-effect isolated:

- run against a pinned workflow definition, conversational package, locale,
  channel profile, actor scope, and initial workflow snapshot;
- use registered mock activities for mutating effects unless the story is
  explicitly marked as an integration trial;
- admit external provider/model calls only through recorded fixtures or a
  separate non-blocking evaluation profile;
- compare expected and actual semantic records, not exact natural-language
  prose;
- report every command, state generation, interaction, output, activity mock,
  delivery attempt, and evidence ref in one static timeline;
- produce coverage for states, transitions, guards, repair policies, output
  kinds, entities, slots, hard negatives, locales, and channel fallbacks;
- distinguish design-time story failures from runtime trace anomalies and
  Teacher candidate opportunities.

Story types:

| Story type | Purpose |
| --- | --- |
| `happy_path` | expected primary path through a capability |
| `clarification` | missing slots, ambiguity, disambiguation, and repair |
| `negative` | unsafe, unsupported, unrelated, or hard-negative phrases |
| `cross_channel` | same semantic interaction through Web, Telegram, text, or voice |
| `regression` | previously failed or corrected behavior |
| `education` | accepted path safe to expose as guided user learning material |

## Static First, Interactive Later

The first implementation should produce static, reviewable artifacts:

- workflow/statechart graph export generated from `workflow.json`;
- conversation-story listing and per-story execution report;
- path coverage over states, transitions, guards, output kinds, repair paths,
  risky actions, locales, and channel fallbacks;
- candidate diff explaining how a Teacher observation would change the
  conversational package;
- static evidence bundle linked from Builder Change, Run, Trial, and Release.

An interactive studio is intentionally deferred. The deferred target is a
Temporal-like review surface where a human can replay traces, inspect workflow
state, compare expected/actual story steps, promote failures into Teacher
candidates, and open a Builder Change from the same screen. Static exports are
the admission path for that later UI; the UI must not become a second model.

## Builder Integration

Builder uses the conversational package as a design-time artifact next to the
web prototype and workflow definition:

```text
web prototype: what the product looks like
workflow: what state changes are legal
conversation stories: how humans control and learn the product
code: implementation under the accepted model
```

The Builder context packet for a conversational Change must include:

- relevant workflow definition digest and graph summary;
- current conversational package digest and coverage report;
- Teacher candidate refs and evidence, when the Change originates from runtime
  learning;
- affected command/entity/output/story ids;
- active acceptance constraints and known regressions;
- package/source refs and privacy scope for any promoted runtime data.

Executors receive this bounded context packet. They may propose source patches,
tests, or migration code, but they may not read unbounded conversation history
or mutate runtime learning stores directly.

Design-time package admission is shared by Builder, skill/scenario validation,
and the developer SDK through `compile_conversational_package`. The pipeline
validates source files, runs deterministic story tests when requested, and
projects `adaos.workflow.static_report.v1` without provider calls. Builder
context packets surface the package digest, validation diagnostics, bounded
story summaries, and static workflow-story coverage so an executor repairs
source artifacts against the same evidence that publication will later check.

## Observability, Safety, And Evaluation

The conversational control plane needs trace continuity across domains. A single
human request should be explainable through:

```text
turn_trace_id
  -> intent_proposal_id
  -> dialog_frame_id / interaction_id
  -> interaction_response_id
  -> workflow_command_id
  -> workflow_event_id
  -> run_id / activity_attempt_id
  -> conversation_output_id
  -> delivery_attempt_id
```

Semantic response presentation preserves this chain. A `ConversationOutput` can
be normalized directly for chat rendering, or first wrapped in a durable
`ResponseEnvelope`; both paths keep the semantic output id, reason, risk,
provenance, trace, envelope id, and response status in response metadata rather
than reducing the output to display text.

Minimum metrics:

- proposal accept/abstain/reject rates by source, locale, provider, and skill;
- clarification, correction, interruption, cancel, and repair success rates;
- false-positive and hard-negative failures for risky actions;
- story coverage and story failure rate by package version;
- output delivery latency and missing/duplicated delivery attempts;
- Teacher candidate reuse, rollback, promotion, rejection, and quarantine rates;
- time from user request to accepted Builder Change, first valid patch, trial,
  and release;
- comparison against direct coding-agent work for diagnosis time, rework, test
  evidence, and human review load.

Security checks must cover:

- prompt injection through descriptors, examples, traces, and user-supplied
  story text;
- alias hijacking and entity poisoning;
- private/user-specific data leaking into reusable or public package source;
- stale or forged action tokens and generation mismatches;
- output/action mismatch where a rendered control does not match the admitted
  command;
- MCP/resource authorization confusion across users, nodes, subnets, or
  webspaces;
- provider drift where a compiled NLU/model artifact no longer corresponds to
  its source digest.

## Roadmap

This checklist routes implementation work. Domain roadmaps remain authoritative
for their detailed gates and evidence.

### Must

- [x] `[must]` Publish `adaos.conversation.output.v1` as the semantic output
  ABI linked to current `ResponseEnvelope` materialization.
- [ ] `[must]` Align `ResponseEnvelope`/`InteractionPresentation` producers to
  the same semantic output identity.
- [x] `[must]` Add a pure runtime ABI bridge for workflow/skill
  `IntentProposal`, canonical workflow invocation, workflow execution result ->
  `ConversationOutput`, and `ConversationOutput` -> `ResponseEnvelope` ref.
- [x] `[must]` Define the `conversational/manifest.yaml` package contract for
  skill/scenario-owned input, output, affordance, repair, example, optional
  deterministic matcher, locale, and story sources.
- [x] `[must]` Implement a Builder/SDK validation command that checks
  conversational source schemas, cross-file refs, locale coverage, side-effect
  policy, and package cardinality. Workspace admission, package build, archive
  verification, Builder context assembly, and the developer SDK all use the
  same `conversational_pipeline` service.
- [ ] `[must]` Define the skill/scenario SDK ports for validation,
  compilation, proposal emission, semantic output, interactions, Teacher
  candidate capture, and Builder promotion.
- [x] `[must]` Add first workflow-facing conversation-story fixtures and runner
  support for deterministic `IntentProposal` fixtures, workflow command, state
  transition, semantic output, and no-LLM execution.
- [x] `[must]` Extend story assertions to full dialog repair,
  `ConversationInteraction`, and channel fallback coverage. Story expectations
  can now assert repair reason/next input, command-preserving
  `ConversationInteraction` projections, and channel `InteractionPresentation`
  fallback mode, reason, command identity, and semantic equivalence.
- [x] `[must]` Make the story runner side-effect isolated by default and require
  explicit integration-trial profiles for live effects or provider calls.
  The first runner records workflow activities as mocked timeline entries and
  does not call providers.
- [ ] `[must]` Route Teacher `descriptor_fix`, `development_task`, alias,
  example, and deterministic matcher candidates through Builder Changes before
  they can update git-versioned conversational package source. Example and
  regex matcher candidates now create Builder promotion-candidate records and
  no longer mutate `scenario.json`, `skill.yaml`, or package source directly;
  descriptor, development-task, alias, and execution of package patches remain
  open.
- [x] `[must]` Preserve runtime specialization as scoped runtime data with
  provenance, privacy, rollback, and promotion state; prevent silent public
  promotion. `adaos.nlu.teacher_overlay_store.v1` retains approved Teacher
  examples as runtime overlays, and `adaos.nlu.teacher_promotion_candidate.v1`
  records the explicit Builder promotion boundary for reusable package source.
- [x] `[must]` Persist trace continuity from turn through proposal,
  interaction, command, Run/activity, semantic output, and delivery attempt.
  `adaos.workflow.trace_identity.v1` proves the cross-record chain, and response
  normalization preserves semantic output identity through direct
  `ConversationOutput` rendering and durable `ResponseEnvelope` rendering.

### Should

- [x] `[should]` Generate static workflow/statechart and conversation-story
  reports from the package for human review and model context. The shared
  `compile_conversational_package` pipeline returns validation evidence plus
  `adaos.workflow.static_report.v1`; Builder context packets, skill/scenario
  validators, and `adaos.sdk.developer.conversational.compile_package` use the
  same source. `adaos.sdk.developer.conversational.export_package` additionally
  writes validation JSON, static-report JSON, and Markdown/Mermaid review
  evidence without making Markdown authoritative.
- [ ] `[should]` Add coverage metrics over workflow paths, dialog repair paths,
  output kinds, risky actions, locales, and channel fallbacks.
- [ ] `[should]` Add conversational threat-model checks for prompt injection,
  descriptor poisoning, alias hijacking, private-data promotion, output/action
  mismatch, and MCP scope confusion.
- [ ] `[should]` Add candidate-to-story promotion so repeated runtime failures
  become reviewable story candidates.
- [ ] `[should]` Update legacy NLU and scenario documentation to mark
  `nlp.intent.detected` and direct `intent -> scenario.run` as compatibility
  paths, not target conversational authority.
- [ ] `[should]` Add Builder authoring affordances for editing conversational
  artifacts without exposing provider-specific model internals as source.
- [ ] `[should]` Record direct-coding-agent comparison metrics: diagnosis time,
  context needed, rework, missing tests, review load, and release confidence.
- [ ] `[should]` Revisit the reference-pattern baseline when major agent, MCP,
  conversational AI, or durable-workflow specs change.

### Could

- [ ] `[could]` Add generated Markdown documentation for a skill/scenario's
  conversational capabilities, examples, repair behavior, and outputs.
- [ ] `[could]` Add story mutation tests that remove guards, examples, or output
  templates and prove the conversational artifact pipeline fails.
- [ ] `[could]` Add education-on-the-go exports that turn accepted stories into
  user-facing walkthroughs or contextual training cards.

### Deferred

- [ ] `[deferred]` Interactive workflow/conversation studio with trace replay,
  expected/actual diff, candidate promotion, and Builder Change creation.
- [ ] `[deferred]` Public cross-skill conversation-story catalog until privacy,
  anonymization, ownership, and promotion policy are production-accepted.
- [ ] `[deferred]` Autonomous workflow induction from raw conversations. Raw
  traces can suggest candidates, but admitted workflow definitions remain
  design-time artifacts.
