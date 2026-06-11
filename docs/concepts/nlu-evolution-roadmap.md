# NLU Teacher Evolution Roadmap

This document is the use-case-gated roadmap for controlled AdaOS NLU
evolution. It does not replace the detailed technical checklist in
[nlu-roadmap.md](./nlu-roadmap.md) or the runtime contract notes in
[nlu-teacher-llm.md](./nlu-teacher-llm.md). It defines the order in which the
system should become useful, observable, reversible, and promotable.

The central product question is not "can the LLM produce a regex?". The central
question is whether AdaOS can evolve safely through user feedback:

```text
observe -> understand -> clarify -> learn -> execute -> verify -> repair/promote
```

Every implementation slice should close at least one real user loop. Technical
surfaces such as Root MCP, `voice_affordances`, Builder tasks, NLU templates,
analytics, and promotion gates are introduced only when they make the next loop
replayable and auditable.

## Reading Order

- This document: vertical use-case gates and acceptance criteria.
- [nlu-roadmap.md](./nlu-roadmap.md): detailed backlog by architecture lane.
- [nlu-teacher-llm.md](./nlu-teacher-llm.md): current Teacher implementation
  contract, APIs, prompt boundaries, and apply flow.
- [Builder](../architecture/builder.md) and
  [Builder Roadmap](../architecture/builder-roadmap.md): handoff when a
  descriptor or capability is missing.

## Evolution Invariants

These invariants are stronger than individual milestones:

- LLMs never execute AdaOS actions, SDK functions, or file mutations directly.
- Every learned change moves through `candidate -> preview -> apply -> verify`.
- Every user-facing request reaches a terminal visible outcome: executed,
  clarification requested, deferred, rejected, quarantined, provider outage, or
  Builder handoff.
- User feedback is linked to a concrete prior request/candidate/action, not to
  unstructured chat history.
- Local learned behavior is separate from reusable skill/scenario artifacts.
- Every durable learned artifact has scope, provenance, privacy policy,
  rollback pointer, and verification evidence.
- Missing descriptors and missing capabilities are not treated as normal NLU
  misses. They become `descriptor_fix` or `development_task` candidates.
- A repeated request should demonstrate what the system learned without another
  Root/OpenAI call, unless the target behavior explicitly requires generative
  reasoning.

## Use-Case Gate Template

Each gate must define:

- `user story`: one or more concrete phrases, including RU/EN or STT-noisy
  variants when relevant.
- `capability`: target AdaOS capability, action, query, entity alias, or
  development task.
- `first-run behavior`: what happens before the system has learned.
- `clarification behavior`: what AdaOS asks when the request is ambiguous,
  underspecified, unsafe, or preference-dependent.
- `learned artifact`: regex/template, Rasa/Neural example, entity alias,
  behavior binding, descriptor fix, Builder task, or promotion patch.
- `execution path`: normal AdaOS dispatcher, query/read model, UI affordance
  activation, process command, or Builder draft.
- `verification`: phrase replay, action preview, UI/client acknowledgement,
  query result contract, process acknowledgement, or golden conversation.
- `rollback`: how the artifact can be removed or superseded.
- `operator evidence`: what NLU Teacher or logs show.

## Industrial Reference Patterns

AdaOS should reuse known conversational-system patterns instead of inventing a
private vocabulary where an established one fits:

- **Conversation repair**: no-match, no-input, correction, interruption,
  disambiguation, cancel, resume, and parameter change are first-class dialog
  states, not incidental fallback text. Rasa CALM treats these as repair
  patterns.
- **Intent/capability publication**: Apple App Intents, Alexa skills, Home
  Assistant Assist, and similar systems require applications/integrations to
  publish actions, entities, parameters, aliases, and examples instead of
  letting the assistant infer private implementation details.
- **Entity exposure**: not every runtime object is voice-controllable by
  default. Entities need explicit exposure, aliases, locale, scope, and privacy
  policy.
- **Golden conversations and continuous tests**: Dialogflow CX-style test cases
  validate dialog paths, not only single utterances.
- **Analytics pipeline**: Rasa-style event analytics should answer operational
  questions such as miss rate, repair rate, cost per learned behavior, rollback
  rate, and provider health.
- **Agent tracing and handoff**: agent/tool systems such as the OpenAI Agents
  SDK make tool calls, handoffs, guardrails, and custom events traceable. AdaOS
  needs equivalent trace continuity across Teacher, Root MCP, Builder, and
  runtime dispatch.

These are reference patterns, not external dependencies. AdaOS keeps execution
and durable mutation inside its own deterministic runtime.

## Gate 0: Evidence Spine

Goal: every request can be explained before broadening the voice surface.

User stories:

- "Покажи состояние инфраструктуры"
- "Покажи браузеры"
- "Покажи media indexe"

Required result:

- The trace shows channel, text, request id, webspace/device, NLU stages,
  confidence, provider fallback/outage reason, Teacher decision, LLM/MCP status,
  candidate id, preview result, apply result, dispatch result, and terminal user
  feedback.

Acceptance checklist:

- [x] `[must]` Requests and Teacher events are persisted beyond transient UI
  state.
- [x] `[must]` Provider/stage outages are distinguished from teachable NLU
  gaps.
- [x] `[must]` Voice-origin Teacher terminal outcomes are visible to the user.
- [ ] `[must]` Golden conversation records can replay at least the first
  existing-action teaching flow end to end.
- [ ] `[should]` Trace view groups related events by request/candidate instead
  of showing only raw chronological events.

## Gate 1: Teach Existing Deterministic UI Action

Goal: an unknown phrase can be bound to an existing safe AdaOS action and then
work without another LLM call.

Primary use case:

```text
User: Покажи состояние инфраструктуры
AdaOS: Открыть Infra State на запрос "Покажи состояние инфраструктуры"?
User: да
AdaOS: Готово. Новое понимание установлено и проверено. Открываю Infra State.
User: Покажи состояние инфраструктуры
AdaOS: opens Infra State through the deterministic NLU/runtime path
```

Learned artifact:

- `template_candidate` for an existing `action_candidate`, initially regex or
  deterministic example data.

Acceptance checklist:

- [x] `[must]` Voice confirmation answers are routed to the active Teacher
  session before normal NLU.
- [x] `[must]` Apply is idempotent and stale duplicate LLM events cannot reopen
  the same loop endlessly.
- [x] `[must]` Replay phrase verification is required before
  `understanding.acquired`.
- [x] `[must]` Safe voice-confirmed candidates dispatch through the normal
  AdaOS intent/action path.
- [ ] `[must]` A replayable golden conversation proves first-run learn,
  repeated-run no-LLM behavior, rollback, and repeated miss after rollback.

## Gate 2: Published Voice Capability Surface

Goal: skills, scenarios, and core publish what can be controlled by voice.

Primary use cases:

- "Покажи Infrastate"
- "Покажи установленные навыки"
- "Покажи установленные сценарии"
- "Покажи переменные окружения подсети"

Required model:

- `voice_capability`: a user-facing ability with owner, parameters, result
  modes, side-effect class, examples, and verification.
- `voice_affordance`: a UI-visible or UI-reachable target such as modal, tab,
  section, filter, toolbar command, row action, or process control.
- `current availability`: runtime projection of which capabilities are
  currently visible, reachable, permitted, or blocked.

Acceptance checklist:

- [x] `[must]` Define minimal `voice_capabilities` / `voice_affordances`
  descriptor contract for skill, scenario, and core surfaces.
- [x] `[must]` Root MCP/API exposes the current available voice surface for the
  target webspace/subnet.
- [x] `[must]` Infrastate publishes affordances for installed skills,
  installed scenarios, and core status sections; `subnet_env` publishes the
  subnet environment variables capability as its owning skill surface.
- [x] `[must]` NLU Teacher prompt rails prefer a published
  capability/affordance candidate over a guessed modal regex.
- [ ] `[must]` Missing published affordance for an existing UI capability creates
  `descriptor_fix`, not an overfitted template.

## Gate 3: Nested UI Affordance Execution

Goal: a request can target a section inside a modal or page, not only the
container.

Primary use case:

```text
User: Покажи установленные навыки
AdaOS: Открыть Infra State и показать раздел "Установленные навыки"?
User: да
AdaOS: opens infrastate_modal and activates inventory.installed_skills
```

Execution path:

- `desktop.open_modal(infrastate_modal)`
- `ui.affordance.activate(infrastate.inventory.installed_skills)`

Acceptance checklist:

- [ ] `[must]` Compound action preview validates container availability,
  affordance existence, side-effect class, and activation path.
- [ ] `[must]` Client/runtime emits acknowledgement for the selected section or
  failed activation reason.
- [ ] `[must]` Learned template or binding replays into the compound action
  without another LLM call.
- [ ] `[should]` Affordance aliases are locale-aware and can include
  STT-correction variants.

## Gate 4: Queryable Capability and Result Mode Learning

Goal: informational requests are not forced into UI navigation. AdaOS learns the
preferred result form separately from the target capability.

Primary use case:

```text
User: Какие навыки установлены?
AdaOS: Хотите посмотреть, услышать или и то и другое?
User: услышать
AdaOS: [stores result_mode=voice_summary] ... answers by voice

User: Какие навыки установлены?
AdaOS: answers by voice without asking again

User: Выводи результат еще на экран
AdaOS: [updates result_mode=voice_and_ui] ... answers by voice and opens UI
```

Learned artifact:

- `learned_behavior_binding`:
  - source phrase class,
  - `capability_id`,
  - `result_mode`,
  - scope,
  - version,
  - provenance,
  - rollback pointer.

Acceptance checklist:

- [ ] `[must]` Define queryable capability descriptors with typed parameters,
  query contract, result modes, and default result mode.
- [ ] `[must]` Store learned behavior bindings separately from NLU templates.
- [ ] `[must]` Correction phrases such as "показывай еще на экран" update the
  previous binding instead of creating a new unrelated intent.
- [ ] `[must]` Voice/UI can explain the current learned preference and rollback
  it.
- [ ] `[should]` Query result summaries include source, freshness, and count
  evidence.

## Gate 5: Entity Exposure and Alias Learning

Goal: names, aliases, STT variants, and ambiguity are handled as entity data,
not as accidental intent templates.

Primary use cases:

- "Покажи НЛО teacher" -> likely `NLU Teacher`.
- "Покажи медиа сервер" -> `Media Server`.
- "Покажи индекс" -> ambiguous between Media Indexer and other indexed
  surfaces.

Acceptance checklist:

- [x] `[must]` Named entities are exposed in Root MCP authoring context with
  canonical ids and aliases.
- [x] `[must]` LLM/Teacher can create `entity_alias` candidates distinct from
  intent/template candidates.
- [ ] `[must]` Voice confirmation names both the raw phrase and canonical
  target, for example `Открыть NLU Teacher на запрос "Покажи НЛО teacher"?`.
- [ ] `[must]` Ambiguous aliases create a clarification session and record
  rejected alternatives.
- [ ] `[must]` Private/local aliases are blocked from public promotion until
  anonymization or explicit review.

## Gate 6: Process and Tool Action Governance

Goal: voice can control processes and internal tools only through governed
AdaOS actions.

Primary use cases:

- "Останови индексирование"
- "Перезапусти медиасервер"
- "Покажи последние ошибки"

Acceptance checklist:

- [ ] `[must]` Process/action affordances are published with side-effect class,
  confirmation policy, ownership, and outcome acknowledgement.
- [ ] `[must]` Mutating or destructive actions require explicit confirmation and
  cannot be auto-applied from a new Teacher candidate.
- [ ] `[must]` Read-only diagnostic actions can answer or open supporting UI
  with source evidence.
- [ ] `[should]` Process state transitions create repairable failure evidence
  when the action does not complete.

## Gate 7: Conversation Repair Policy

Goal: repair behavior is first-class and consistent across Voice, typed chat,
Teacher UI, and API-originated text.

Repair types:

- `no_match`
- `provider_outage`
- `misrecognition`
- `wrong_target`
- `ambiguous_entity`
- `missing_parameter`
- `change_parameter`
- `change_result_mode`
- `cancel_pending`
- `resume_previous`
- `repeat_last`

Acceptance checklist:

- [ ] `[must]` Define `repair_policy` state machine and event taxonomy.
- [ ] `[must]` Short answers, cancellation, correction, and interruption route
  through active repair/clarification sessions before normal NLU.
- [ ] `[must]` A failed provider path tells the user whether the request was
  deferred, skipped, or sent to another active NLU stage.
- [ ] `[should]` Repair attempts have a bounded retry policy before asking for
  a clearer user instruction or creating a Builder task.

## Gate 8: Descriptor Gap to Builder Handoff

Goal: if an installed capability exists but is not published for voice, Teacher
creates a development item instead of inventing a fake intent.

Primary use case:

```text
User: Покажи установленные сценарии
Evidence: Infrastate can show scenarios, but no voice affordance exists.
Result: descriptor_fix candidate for Infrastate descriptor/webui/nlu_hints.
```

Acceptance checklist:

- [x] `[must]` `descriptor_fix` candidates are first-class Teacher candidates.
- [x] `[must]` Builder task schema can represent descriptor fixes.
- [ ] `[must]` Teacher creates descriptor fixes from affordance/capability gaps
  with owner, missing surface, source utterance, and replay expectation.
- [ ] `[must]` Completed Builder descriptor fixes link back to the originating
  Teacher request and rerun the phrase.

## Gate 9: Missing Capability to Builder Draft

Goal: if the requested behavior does not exist, AdaOS creates structured work
instead of pretending it can do the action.

Primary use case:

```text
User: Покажи сломанные навыки по версиям
Evidence: no current query/capability can answer this.
Result: development_task for Builder with requested behavior and acceptance
        replay phrase.
```

Acceptance checklist:

- [x] `[must]` `development_task` candidates are first-class Teacher candidates.
- [ ] `[must]` Builder draft can include capability descriptor, UI/data route,
  NLU hints, tests, and acceptance replay phrase.
- [ ] `[must]` After Builder apply, the originating Teacher request is replayed
  and marked resolved or still blocked.
- [ ] `[should]` Duplicate missing-capability requests dedupe into one Builder
  task with multiple evidence examples.

## Gate 10: Promotion, Publication, and Release Channels

Goal: useful local learning can become reusable AdaOS knowledge without leaking
private names or bypassing regression.

Release states:

- `session_candidate`
- `local_learned`
- `webspace_learned`
- `owner_artifact_candidate`
- `repo_promoted`
- `public_reusable`

Acceptance checklist:

- [x] `[must]` Accepted Teacher artifacts carry promotion, provenance, privacy,
  portability, and rollback metadata.
- [ ] `[must]` Promotion flow checks scope, private entities, target owner,
  duplicate/conflict risk, and regression suite before repo push.
- [ ] `[must]` Skill/scenario push validates voice descriptors, aliases, examples,
  side-effect policy, and result contracts.
- [ ] `[deferred]` Public template/capability registry requires anonymization
  and explicit operator approval.

## Gate 11: Regression, Quarantine, and Self-Repair

Goal: AdaOS notices when learned or promoted behavior stops working and routes
the failure to Teacher or Builder repair.

Primary use case:

```text
After an Infrastate update:
User or CI: Покажи установленные навыки
Expected: open Infrastate Installed Skills
Actual: modal opens but section activation fails
Result: learned binding quarantined; Builder repair task created
```

Acceptance checklist:

- [ ] `[must]` Golden conversations can assert NLU result, action preview,
  dispatch acknowledgement, UI outcome, and result-mode behavior.
- [ ] `[must]` Failed golden conversations create quarantine or repair evidence,
  not silent pass/fail text.
- [ ] `[must]` Rollback restores the previous local learned behavior or removes
  the faulty overlay without touching unrelated user artifacts.
- [ ] `[should]` Regression coverage is visible by capability owner.

## Gate 12: Analytics, Cost, and Runtime Quality

Goal: decide future work from operational evidence, not anecdotal failure logs.

Questions to answer:

- Which request classes miss most often?
- Which learned artifacts are reused, corrected, or rolled back?
- Which capabilities are missing descriptors?
- Which NLU engine is effective for which class of request?
- What is the cost and latency per successful learned behavior?
- How often does user-visible feedback fail or arrive too late?

Acceptance checklist:

- [ ] `[must]` Define the first event fields needed to answer the questions
  above from logs before building dashboards.
- [ ] `[must]` Track LLM/MCP budget counters by route, webspace, request class,
  terminal outcome, and deferred queue reason.
- [ ] `[should]` Build a lightweight analytics read model over Teacher/runtime
  events.
- [ ] `[could]` Add dashboards after the event model answers concrete QA
  questions reliably.

## Implementation Order

The recommended delivery order is:

1. Finish Gate 0 and Gate 1 until the repeated-request proof is stable.
2. Implement Gate 2 and Gate 3 with Infrastate Inventory as the first real
   nested affordance.
3. Add Gate 4 for query/result-mode learning, still using Infrastate Inventory.
4. Harden Gate 5 and Gate 7 so STT variants and corrections do not corrupt
   templates.
5. Add Gate 8 and Gate 9 to route gaps into Builder.
6. Add Gate 10 and Gate 11 before broad promotion or public reuse.
7. Add Gate 12 metrics continuously, but keep dashboards secondary until the
   event questions are stable.

This order intentionally forms a spiral. Each pass adds capability surface,
learning power, and autonomy only after the previous pass is observable,
replayable, and reversible.
