# NLU Teacher Evolution Roadmap

Status: active use-case-gated roadmap.

Last reviewed: 2026-08-07.

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

- [Governed Evolution](governed-evolution.md): cross-domain
  boundary between NLU, Support intake, AdaOS Issues, Builder, and runtime
  evidence.
- This document: vertical use-case gates and acceptance criteria.
- [nlu-roadmap.md](./nlu-roadmap.md): detailed backlog by architecture lane.
- [nlu-teacher-llm.md](./nlu-teacher-llm.md): current Teacher implementation
  contract, APIs, prompt boundaries, and apply flow.
- [Builder](builder.md) and
  [Builder Roadmap](builder-roadmap.md): handoff when a
  descriptor or capability is missing.
- [Conversational Control Interface](conversational-interface.md):
  shared input/output contract, NLU data lifecycle, Builder promotion boundary,
  and conversation-story tests.

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
- NLU Teacher improves interaction with the deterministic design-time skeleton;
  it does not author workflow definitions, protected effects, or public package
  source directly.
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
  query result contract, process acknowledgement, conversation story, or
  runtime trace promoted into a story candidate.
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
- [x] `[must]` Golden conversation records can replay at least the first
  existing-action teaching flow end to end.
  Evidence: `tests/fixtures/nlu/gate1_existing_action_golden.json` and
  `tests/test_nlu_golden_conversations.py` replay first-run miss, candidate
  apply, repeated deterministic dispatch, rollback, and miss-after-rollback.
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
- [x] `[must]` A replayable golden conversation proves first-run learn,
  repeated-run no-LLM behavior, rollback, and repeated miss after rollback.
  Evidence: `test_gate1_golden_conversation_learn_replay_rollback` verifies
  `regex.dynamic` replay and normal dispatcher execution without a Teacher/LLM
  call after apply.

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
- [x] `[must]` Missing published affordance for an existing UI capability creates
  `descriptor_fix`, not an overfitted template.
- [x] `[must]` Live Root MCP evidence for a real request proves that the LLM saw
  the matching `voice_capability` / `voice_affordance`, including owner,
  freshness/fingerprint, and the MCP tool-call/evidence row in the Teacher log.
  Evidence: contextual action surface tests now publish owner/fingerprint and
  reachability for UI affordances and `callSkill` endpoint actions; Teacher
  policy tests bind matching action-surface rows to `voice_capability_binding`
  and route descriptor gaps away from regex candidates.
- [x] `[must]` If a matching published capability/affordance exists in live MCP
  context, Teacher must not create a `development_task` for the same behavior.
  Runtime policy repair now converts ignored/development-task LLM output into a
  `voice_capability_binding` candidate when MCP action surface contains a
  matching published capability/affordance.
- [x] `[must]` Stale pending regex hypotheses cannot outrank a matching
  `voice_capability_binding`, and read-only inventory phrases cannot reopen
  mutating candidates such as `desktop.toggle_app_install` unless the phrase
  explicitly contains a mutation verb.

Live-trial blocker:

- Phrase: `Покажи установленные навыки`.
- Expected published surface:
  `infrastate.inventory.installed_skills.query` and
  `infrastate.inventory.installed_skills`.
- Wrong observed outcome: Teacher proposed a new "Show Installed Skills"
  development task instead of binding the phrase to the published Infrastate
  inventory affordance.
- Implementation status: backend policy repair, contextual action surface
  evidence, and voice-surface binding tests are in place for this exact phrase
  shape. Gate 2 is closed for the deterministic runtime contract; live node
  smoke tests should keep checking that deployed skills publish the same rows.
- Runtime hardening: repeated attempts for the same phrase now prefer published
  voice-surface bindings over stale regex hypotheses, so a bad earlier
  candidate should no longer block the correct Infrastate inventory binding.
- Skill-owned actions can now be learned as training examples without scenario
  mapping. Candidate Apply carries `action_candidate` into a scoped example
  overlay and promotion candidate, including the proposed `skillTool` binding;
  the dispatcher can execute the runtime binding through the normal tool path
  without Teacher editing `skill.yaml`.
  This is the first deterministic route for phrases such as ReDevice slideshow
  controls that belong to a skill rather than to a core UI modal.

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
- [x] `[must]` Client/runtime emits acknowledgement for the selected section or
  failed activation reason.
- [x] `[must]` Learned template or binding replays into the compound action
  without another LLM call.
- [ ] `[must]` The first Gate 3 golden conversation is
  `Покажи установленные навыки`: first run clarifies and applies the binding;
  repeat run opens Infrastate Inventory / Installed skills through the
  deterministic runtime path, without Root/OpenAI.
- [ ] `[must]` Failure to activate a nested affordance creates actionable
  `descriptor_fix` or runtime-ack evidence instead of falling through to a
  generic Builder `development_task`.
- [x] `[must]` Empty or missing activation plans are reported as
  `nlu.action.dispatch_failed` with `activation_plan_empty` and do not emit a
  false positive Voice acknowledgement.
- [ ] `[should]` Affordance aliases are locale-aware and can include
  STT-correction variants.

Implementation note:

- `voice_capability_binding` is now a first-class Teacher candidate. It stores a
  deterministic regex anchor for the phrase, but the learned behavior is the
  published capability/affordance activation plan, not a guessed modal regex.
- The baseline dispatcher exposes `voice.capability.activate`; applying a
  binding persists a normal Teacher regex rule with static slots containing the
  capability id, affordance id, and JSON activation plan.
- The web client subscribes to `desktop.modal.open`, `ui.state.set`, and
  `ui.focus_widget`, and emits best-effort acknowledgement/failure events for
  the UI activation steps.

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
- [ ] `[must]` Promotion candidates targeting reusable behavior identify the
  owning `conversational/` package files, affected workflow commands/entities/
  outputs/stories, and the Builder Change that will review the patch. Example
  and matcher candidates now identify their target package file, bounded patch,
  allowed paths, acceptance criteria, evidence refs, and intended Change id;
  affected semantic-id expansion and durable Change creation remain open.
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

- [ ] `[must]` Conversation stories can assert NLU result, action preview,
  dispatch acknowledgement, workflow state transition, semantic output, UI
  outcome, and result-mode behavior. Current deterministic stories assert
  proposal, workflow transition/state, semantic output, repair,
  `ConversationInteraction`, and channel fallback; provider parsing, dispatch
  acknowledgement, and concrete UI outcome remain open.
- [ ] `[must]` Failed conversation stories create quarantine, Teacher candidate,
  or Builder repair evidence, not silent pass/fail text.
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

## Gate 13: Endpoint Audio and Dialog Sessions

Goal: endpoint-originated voice becomes a governed audio session before it
becomes text, intent, dialog, or dictation.

Primary use cases:

- "Tablet, next" from a ReDevice endpoint running push-to-talk or VAD.
- "Ada, listen" followed by a long dictation segment, then "what do you think?"
  or "add it to notes".
- A Bluetooth headset connected to a ReDevice endpoint acts as the microphone
  and speaker while STT and NLU run on a member node or trusted cloud provider.

Required model:

- `EndpointAudioService` owns audio session lifecycle, transport selection,
  STT routing, activation events, diagnostics, and retention policy.
- ReDevice, iOS native agent, browser endpoints, and future endpoints publish
  capabilities through Endpoint Registry and receive policy from the hub.
- Skills consume transcripts, dialog events, and text buffers through SDK
  interfaces. Skills do not implement private microphone transport, cloud STT
  routing, or retention rules.

Acceptance checklist:

- [x] `[must]` Endpoint Registry exposes audio input/output capabilities,
  Bluetooth route state, local STT benchmark state, and allowed activation
  strategies per endpoint.
- [~] `[must]` `EndpointAudioService` can create, stop, recover, and audit
  `command`, `dialog`, `dictation`, and `audio_debug` sessions. Current code
  has an `audio-session.v1` facade for command/audio-debug sessions and
  response-route evidence; durable dialog and dictation sessions are still
  open.
- [x] `[must]` Push-to-talk and VAD work on legacy ReDevice without local STT;
  audio is segmented, bounded, and routed to the member-hosted audio pipeline.
- [x] `[must]` Final transcript dispatch reuses the normal AdaOS Voice/NLU path,
  including Teacher sessions, confirmations, action governance, and published
  voice capability bindings.
- [ ] `[must]` Dialog and dictation sessions produce durable text-buffer events
  that can be reviewed, edited, sent to a skill, added to notes, or discarded.
- [~] `[must]` Cloud STT/LLM usage is policy-gated and visible in evidence:
  provider, cost class, retention mode, fallback route, and terminal outcome.
  The local/member STT path has visible degraded states; broad cloud evidence is
  part of the dialog/dictation milestone.
- [x] `[must]` Audio/video/media payloads do not travel through Yjs; the client
  receives only references, state summaries, and UI-safe events.
- [~] `[should]` Bluetooth speaker/headset setup is supported through assisted
  native settings, route tests, profile diagnostics, and preferred route memory.
  ReDevice Settings can assist native settings and tests; profile diagnostics
  and preferred route memory need hardening.
- [ ] `[should]` Wake-word or endpoint-name activation is available only after
  VAD/PTT reliability, false-positive rate, and battery impact are measured.
- [ ] `[should]` Multi-endpoint arbitration suppresses duplicate commands and
  prefers the active endpoint, nearest endpoint, or explicitly named endpoint.
- [ ] `[could]` Add local lightweight keyword spotting for modern endpoints
  while keeping legacy Android on VAD/PTT plus member-side STT.
- [ ] `[deferred]` Always-on conversational listening, speaker verification,
  diarization, and cross-device beamforming wait until the first command and
  dictation loops are stable and observable.

## Implementation Order

The recommended delivery order is:

1. Finish Gate 0 and Gate 1 until the repeated-request proof is stable.
2. Re-close Gate 2 and Gate 3 with Infrastate Inventory as the first real
   nested affordance. Do not broaden into Builder or new capability creation
   for phrases that should be covered by published `voice_capabilities` /
   `voice_affordances`.
3. Add Gate 4 for query/result-mode learning, still using Infrastate Inventory.
4. Harden Gate 5 and Gate 7 so STT variants and corrections do not corrupt
   templates.
5. Add Gate 13 for endpoint audio sessions before broad dialog or dictation
   UX. Keep the first slice bounded to ReDevice push-to-talk/VAD, member-side
   STT, and normal Voice/NLU dispatch.
6. Add Gate 8 and Gate 9 to route gaps into Builder.
7. Add Gate 10 and Gate 11 before broad promotion or public reuse.
8. Add static conversation-story and workflow/statechart reports before
   interactive replay or studio work.
9. Add Gate 12 metrics continuously, but keep dashboards secondary until the
   event questions are stable.

This order intentionally forms a spiral. Each pass adds capability surface,
learning power, and autonomy only after the previous pass is observable,
replayable, and reversible.
