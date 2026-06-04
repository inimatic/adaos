# NLU Roadmap Checklist

Current runtime implementation estimate: **97%** for the practical AdaOS NLU
pipeline and provider boundary. The target NLU Teacher architecture is tracked
separately below because it adds candidate state, correction threads, MCP
descriptors, UI authoring, and safety gates that are not part of the runtime
parser itself.

The roadmap is organized into working lanes:

- Runtime lane: phrase parsing, provider fallback, dispatch evidence, and
  readiness.
- Teacher lane: miss capture, candidate lifecycle, correction threads, durable
  training changes, and development-task candidates.
- MCP lane: governed descriptors and preview/apply operations exposed to LLMs.
- UI lane: operator-visible probe, trace, correction, approval, and rollback.
- Safety/evaluation lane: auth, audit, false-positive checks, regression, and
  promotion gates.

The existing API remains the implementation backend. Root MCP should wrap or
proxy governed NLU authoring capabilities; it should not become a second source
of truth for templates, candidates, or dispatch behavior.

Current provider note: `neuro_nlu_lite_skill` is an experimental weak-device
provider stage. It is intentionally separate from `neural_nlu_service_skill`
and should not be counted as a replacement for the production Neural NLU
provider or the Teacher governance loop.

Current M4 status: **complete for candidate Apply validation and first
voice-confirmed dispatch slice**. LLM
misses can produce structured action/template envelopes or clarification
sessions; Root MCP exposes a contextual action surface; Teacher enforces
multi-engine authoring strategies; and candidate Apply now runs a dry-run
validation gate before durable regex/example changes. Voice-confirmed safe
candidates can now emit the normal AdaOS `nlp.intent.detected` path after
`understanding.acquired`. Remaining target-roadmap items belong mainly to
dispatch outcome verification, promotion, and UI/operator surfaces.

Current governance/offline slice: **implemented for local learned artifacts**.
Teacher candidates and accepted regex rules now carry promotion, portability,
provenance, privacy, MCP audit, rollback, and verification metadata. Teacher
state exposes retention/threat/budget policy snapshots, budget counters, and a
bounded deferred enrichment queue for Root/OpenAI failures or empty LLM output.

Current MCP-aware LLM status: **hybrid bridge implemented**. NLU Teacher still
collects a bounded Root MCP snapshot for compatibility, but it can also attach
a scoped Root MCP `responses_tool` descriptor to `/v1/llm/response` so OpenAI
can call Root MCP directly. If descriptor/session preparation fails or times
out, Teacher logs the reason and continues with the snapshot path.
Root now also has a first root-public cached `NLUTeacherRead` slice: hub
lifecycle reports publish a bounded `nlu_authoring_snapshot`, and the public
Root MCP endpoint serves read-only NLU context/template/registry tools from
the root subnet-info cache.

## Status Labels

Markdown checkboxes only distinguish done from not done. This roadmap uses a
four-level MoSCoW-style priority vocabulary for planned work:

- `[must]`: first-order work required for the target NLU Teacher architecture
  to be functionally coherent.
- `[should]`: improvement/hardening work that materially raises quality,
  operator confidence, or reuse, but can follow the main vertical slice.
- `[could]`: useful optional work that improves ergonomics, diagnostics, or
  breadth, but should not compete with `[must]` / `[should]` delivery.
- `[deferred]`: intentionally postponed until the contract, working loop,
  evaluation data, or owning surface is stable.

Legacy `[polish]` items mean `[could]` unless the surrounding section promotes
them to `[should]`. An unchecked `[should]`, `[could]`, or `[deferred]` item
must not be counted as a blocker for the next `[must]` implementation gate
unless the gate explicitly depends on it.

## Target Architecture

The target NLU Teacher is not a regex generator. It is a governed authoring and
clarification loop over a deterministic AdaOS runtime:

1. **Deterministic runtime**: skills, scenarios, UI actions, endpoint commands,
   process state, permissions, and dispatch live here. LLMs do not call SDK
   functions, publish events, invoke skill tools, or mutate UI state directly.
2. **Context plane**: Root MCP/API exposes current scenario, UI state, available
   actions, process state, named entities, templates, traces, dialog context,
   skill/scenario descriptors, and policy boundaries.
3. **Decision plane**: NLU Teacher classifies the utterance as an actionable
   request, correction, ambiguity, entity issue, descriptor gap, missing
   capability, or non-actionable utterance.
4. **Clarification plane**: when uncertainty is high, AdaOS opens a structured
   dialog session. The LLM may propose short clarification questions and
   options, but AdaOS owns session state, allowed answers, retry policy, and
   the final structured candidate.
5. **Validation plane**: AdaOS runs phrase probes, action previews,
   side-effect gates, conflict checks, stale-write checks, and optional
   operator/user confirmation before any durable change or dispatch.
6. **Multi-engine authoring plane**: the Teacher chooses the right improvement
   strategy: regex, Rasa example, Neural example, entity alias, descriptor
   fix, development task, or ignore. Regex is only one strategy and should be
   rejected for broad semantic cases where it would overfit.
7. **Persistence and promotion plane**: local learned overlays can be promoted
   to workspace artifacts, then pushed/published to skill/scenario
   repositories only after ownership, audit, rollback, and regression gates.
8. **Privacy, security, and cost plane**: utterances, named entities, prompt
   context, MCP bearer scope, and LLM calls are governed by retention,
   anonymization, rate-limit, abuse-case, and cost-control policies.
9. **Developer handoff plane**: when a capability or descriptor is missing,
   Teacher creates structured development candidates for skill/scenario
   authoring instead of inventing fake intents.

Core invariant: AdaOS acts deterministically when understanding is sufficient,
and uses LLM dialog only to reduce uncertainty or improve the domain model.

## Architectural Detail Anchors

The target design should preserve these anchors while individual components are
implemented in smaller slices:

- **Understanding and execution are different gates.** A candidate is not
  "good" merely because a template matches the source phrase. AdaOS must also
  preview the action, check side-effect policy, and, when dispatched, verify
  the runtime outcome.
- **Dialog is a structured uncertainty reducer.** Clarification questions are
  not free-form chat state. Each question must be backed by a
  `clarification_session`, explicit options or missing slots, rejected
  alternatives, and a resolution path.
- **Named entities are voice-control objects.** Devices, nodes, endpoints,
  browsers, apps, modals, scenarios, skills, and processes need canonical ids,
  voice aliases, locale, scope, ownership, and ambiguity evidence. Entity
  learning must not be folded into intent learning by default.
- **NLU strategy is selected, not assumed.** Regex is preferred only for stable
  command phrases and lookup-backed slots. Broad semantic phrasing, repeated
  corrections, or high ambiguity should push the Teacher toward Rasa/Neural
  examples, entity aliases, descriptor fixes, clarification, or development
  tasks.
- **Local learning and public reuse are separate products.** A locally useful
  alias or phrase may be private, user-specific, or workspace-specific. Public
  promotion requires portability, privacy, provenance, rollback, and regression
  evidence.
- **Missing descriptors are not NLU failures.** If a skill/scenario can perform
  an action but has not published a conversational skeleton, the correct
  outcome is `descriptor_fix`, not a guessed template.

Reference flows that must stay representable:

- **Known entity command**: `Покажи медиасервер` -> entity lookup ->
  `desktop.open_modal` action candidate -> action preview -> dispatch ->
  outcome verified.
- **Ambiguous entity command**: `Покажи индекс` -> Media Indexer vs Media
  Server ambiguity -> clarification session -> selected option -> action
  candidate -> optional entity alias/example learning.
- **Correction after wrong hypothesis**: user rejects a candidate -> rejected
  alternative is stored -> retry uses the rejected evidence -> second rejection
  asks for clarification or creates a development/descriptor task.
- **Descriptor gap**: phrase refers to a real skill capability that has no
  action descriptor or examples -> create `descriptor_fix` candidate for the
  skill/scenario owner instead of creating an overfitted regex.
- **Capability gap**: phrase asks for behavior no installed skill/scenario can
  provide -> create `development_task` with the original phrase, context,
  missing surface, and replay check.

Minimal milestone gates:

- **M1: Action candidate and clarification core.** `[complete]` Misses can
  become structured action candidates or clarification sessions, and short
  answers resolve the active session before normal NLU.
- **M2: Contextual action surface.** `[complete]` Root/API context exposes current state,
  named entities, available actions, process state, and developer-authored
  hints with enough data to preview a candidate.
- **M3: Multi-engine authoring decision.** `[complete]` Teacher output includes
  `training_strategy` and can intentionally reject regex in favor of another
  strategy.
- **M4: Validation and safety gates.** `[complete for candidate Apply]`
  Action/template preview, side-effect policy, conflict checks, and basic abuse
  checks run before durable candidate Apply. Dispatch outcome verification,
  endpoint command preview, and full cost/privacy controls remain later gates.
- **M5: Promotion and developer handoff.** Local learning can be promoted or
  rejected with provenance, while missing descriptors/capabilities create
  structured work for skill/scenario development.

## Balanced Target Roadmap

This checklist is the canonical target-roadmap slice built from the current
implementation and the architecture above. The detailed historical phases
below remain useful for tracking existing implementation work.

### A. Contracts and State Model

- [x] `[must]` Define first-class `action_candidate` records separate from
  `template_candidate` records: candidate id, class, planned action/intent,
  slots, owner, side-effect class, action-preview status, dispatch status,
  feedback status, audit ids, and rollback pointer.
- [x] `[must]` Define `template_candidate` records for regex/Rasa/Neural/entity
  alias/descriptor patches, each linked to an action candidate or explicit
  non-action decision.
- [x] `[must]` Contract slice: `src/adaos/abi/nlu.teacher.v1.schema.json`
  defines request/thread, action candidate, template candidate,
  clarification, lifecycle, idempotency, scope, response policy, feedback, and
  MCP capability profile records.
- [x] `[must]` First implementation slice: LLM-generated regex candidates now carry
  backward-compatible `action_candidate`, `template_candidate`, and
  `training_strategy` envelopes while the existing Apply/rollback path keeps
  using the legacy fields.
- [x] `[must]` Define `clarification_session`: source request, active
  uncertainty kind, question, allowed answers, rejected candidates, retry
  count, timeout, final resolution, and thread id.
- [x] `[must]` First implementation slice: current Voice confirmation state is mirrored
  into `data.nlu_teacher.clarification_sessions[]` with question,
  allowed answers, status, answer, attempt, target, and rejected candidate
  evidence.
- [x] `[must]` M1 implementation slice: LLM `need_clarification` responses now create
  `llm_clarification` sessions with questions, options, risk notes,
  training strategy, route metadata, and Teacher events.
- [x] `[must]` Define candidate lifecycle states across understanding and
  execution: `proposed`, `phrase_previewed`, `action_previewed`,
  `clarification_requested`, `user_confirmed`, `applied`,
  `replay_intent_matched`, `dispatch_attempted`, `dispatch_succeeded`,
  `accepted`, `corrected`, `rejected`, `quarantined`, and `rolled_back`.
- [x] `[must]` Define idempotency keys for request capture, LLM proposal,
  clarification answer, preview, apply, dispatch, feedback, promotion, and
  rollback.
- [x] `[must]` Define scope fields for every request/thread/candidate:
  channel, route, webspace, scenario, device, user/session when available,
  locale, and privacy boundary.
- [ ] `[should]` Add RU/EN/STT-noise fixtures for request threads,
  clarification sessions, correction threads, and template patch previews.

### B. Context and Action Surface

- [x] `[must]` Expose contextual action surface through API/MCP: current
  scenario, available apps/modals/scenarios, runtime-backed UI actions,
  skill-routed actions, endpoint commands, required slots, examples,
  side-effect class, owner, and preview method.
- [x] `[must]` M2 implementation slice: `nlu_authoring.get_context` now embeds
  `action_surface.available_actions` with system/interface actions,
  skill/scenario intent routes, required slots, examples, side-effect class,
  owner, preview method, and fingerprint.
- [x] `[must]` Expose current state through API/MCP: open modals, home/current
  scenario, focused route/node/browser, selected device, active/pending
  confirmations, recent errors, and user route context.
- [x] `[must]` M2 implementation slice: `runtime_state` now exposes webspace, current
  scenario, available modal ids, app/widget catalogs, installed ids, nodes,
  active Teacher confirmations/clarifications, recent Teacher errors, lookup
  counts, and read errors.
- [x] `[must]` Expose process state relevant to language: active jobs, failed
  jobs, long-running operations, last user command, last assistant action,
  current warnings, and owning skill/process.
- [x] `[must]` M2 implementation slice: `process_state` now exposes Teacher queue
  counts, workbench signals, recent Teacher events, and compact
  `data.jobs`/`data.operations`/`data.processes`/`data.tasks` rows.
- [x] `[must]` Extend skill/scenario manifests with authored `llm_hints` /
  `nlu_hints`: aliases, user-facing action descriptions, examples, slot
  schemas, entity names, owner hints, and side-effect class.
- [x] `[must]` M2 implementation slice: skill/scenario manifests and skill `webui.json`
  can publish `llm_hints` / `nlu_hints`, and Root MCP forwards compact
  developer hints to the LLM prompt.
- [x] `[must]` Connect named entities to voice control: expose voice-safe
  aliases, canonical ids, ambiguity evidence, entity ownership, and allowed
  voice actions for devices, nodes, endpoints, browsers, apps, modals,
  scenarios, skills, and processes.
- [x] `[must]` Current named-entity slice: Root MCP `nlu_authoring.get_context` includes
  canonical named-entity registry payload and bearer-derived target scope.
- [x] `[must]` Include entity scope and portability class in context:
  `session`, `user`, `workspace`, `scenario`, `skill`, `system`, or `public`.
- [ ] `[should]` Publish process/action affordances as named entities when the
  user can naturally refer to them by voice, for example "scan", "indexing",
  "display", "current browser", or "last failed job".
- [ ] `[should]` Cache Root MCP descriptive-plane snapshots per target/subnet
  with TTL and invalidation, so LLM context is usually complete without
  blocking the Voice path.
- [x] `[must]` M2 implementation slice: LLM Teacher caches descriptor evidence from
  `nlu_authoring.get_context`, `desktop.registry.lookup`,
  `nlu_authoring.list_training_targets`, `nlu_authoring.list_templates`, and
  `sdk.describe_surface`; phrase checks and dialog context remain uncached.
- [x] `[must]` Root-public cached slice: hub control lifecycle reports publish a
  bounded `nlu_authoring_snapshot`, and TS Root MCP serves
  `nlu_authoring.get_context`, `desktop.registry.lookup`,
  `nlu_authoring.get_dialog_context`,
  `nlu_authoring.get_recent_failures`,
  `nlu_authoring.list_templates`,
  `nlu_authoring.list_training_targets`, and `sdk.describe_surface` from the
  root-side subnet-info cache.
- [ ] `[should]` Add freshness/invalidation metrics for the root-cached
  `nlu_authoring_snapshot`, including report age, cache hit/miss, partial
  section errors, and descriptor fingerprint changes.
- [ ] `[deferred]` Publish deep SDK descriptors beyond read-only ownership and
  affordance discovery; LLM execution remains prohibited.

### C. Decision and Clarification

- [x] `[must]` Update LLM output contract to return `decision`,
  `action_candidate`, `training_strategy`, `need_clarification`,
  `clarification_question`, `options`, `why_not_regex`, and `risk_notes`.
- [x] `[must]` Implement uncertainty policy: direct action, confirmation,
  clarification, development task, or ignore based on confidence, ambiguity,
  side-effect class, and context.
- [x] `[must]` M3 implementation slice: `training_strategy=clarification` opens the
  structured clarification path when a question is present, and low-confidence
  non-read-only regex proposals are demoted to a non-regex strategy candidate
  instead of becoming an applyable regex rule.
- [x] `[must]` Route short answers such as `yes/no/first/second/да/нет` through
  active clarification/confirmation sessions before normal NLU.
- [x] `[must]` First implementation slice: generic `clarification.answered` events now
  store the selected option, answer kind, raw answer text, route metadata, and
  final session status.
- [x] `[must]` Record negative feedback and rejected alternatives as structured
  evidence, not only as a retry trigger.
- [ ] `[could]` Let Voice/UI present disambiguation options for ambiguous
  entities and actions, for example Media Indexer vs Media Server.
- [ ] `[deferred]` Support long multi-turn task planning dialogs; the first
  Teacher dialog loop should stay short and resolution-oriented.
- [ ] `[deferred]` Support multi-user/concurrent dialog ownership. The target
  model must preserve route/session fields now, but full parallel-user policy
  is intentionally postponed.

### D. Multi-Engine NLU Authoring

- [x] `[must]` Add `training_strategy` selection:
  `regex`, `rasa_example`, `neural_example`, `entity_alias`,
  `descriptor_fix`, `development_task`, `clarification`, or `ignore`.
- [x] `[must]` First implementation slice: regex candidates and LLM clarification
  sessions preserve the selected `training_strategy` in persisted Teacher
  state.
- [x] `[must]` M3 implementation slice: Teacher normalizes strategy aliases, stores the
  selected strategy on every candidate, and routes non-regex strategies into
  first-class `training_example`, `entity_alias`, `descriptor_fix`,
  `development_task`, or `clarification` records.
- [x] `[must]` Require LLM to reject regex for broad semantic, highly
  contextual, or ambiguous phrases and choose Rasa/Neural/clarification or a
  descriptor fix instead.
- [x] `[must]` M3 implementation slice: if the LLM selects a non-regex strategy,
  provides `why_not_regex`, or proposes an overbroad/too-short regex, AdaOS
  rejects the regex path and stores a non-regex strategy candidate with
  `regex_rejection` evidence.
- [x] `[must]` Keep regex for deterministic command phrases and lookup-backed
  slots; add blast-radius preview before durable apply.
- [x] `[must]` M3 implementation slice: regex candidates are only applyable when
  `training_strategy.primary=regex`, the source phrase preview matches, and
  the simple policy guard does not reject the pattern. Full blast-radius
  preview remains an M4 validation gate.
- [x] `[must]` Save Rasa/Neural examples through owner artifacts or curated
  feedback overlays, not through direct model mutation.
- [x] `[must]` M3 implementation slice: `training_example` candidate Apply emits the
  governed `nlp.teacher.example.save` flow into skill/scenario/system-action
  artifacts or feedback overlays; it does not mutate Rasa/Neural models
  directly.
- [ ] `[should]` Capture STT/raw text, normalized text, locale guess,
  transliteration/typo evidence, and entity canonicalization evidence for
  each teachable request.
- [x] `[must]` Treat entity-alias learning as a first-class strategy distinct
  from intent/template learning, including voice aliases and negative alias
  evidence.
- [x] `[must]` M3 implementation slice: `entity_alias` candidates are persisted as
  first-class strategy candidates and can be accepted into the Teacher plan
  for later owner-specific alias APIs.
- [x] `[must]` Treat `descriptor_fix` and `development_task` as first-class
  strategies when the system action or skill capability exists but is not
  sufficiently described for NLU/MCP.
- [x] `[must]` M3 implementation slice: `descriptor_fix` and `development_task`
  candidates are persisted separately from regex/template candidates and can
  be accepted into the Teacher plan for developer handoff.
- [ ] `[should]` Collect per-engine statistics before adding calibration
  logic: accept/abstain/reject, confidence bands, fallback chain, STT
  confidence, clarification rate, correction rate, and false-accept samples.
- [ ] `[deferred]` Apply Neural/Rasa model promotion automatically; model
  rebuild/reindex remains behind explicit quality gates.

### E. Validation, Policy, and Evaluation

- [ ] `[must]` Make action preview a required gate for interface, skill, and
  endpoint action candidates before apply or dispatch.
- [x] `[must]` M4 implementation slice: `nlp.teacher.candidate.apply` now runs a
  validation gate before durable mutation. Built-in system/interface actions
  use `desktop.preview_action`; custom route candidates carry a warning until
  a route-specific preview surface exists.
- [ ] `[must]` Define side-effect classes and approval policy:
  `read_only`, `ui_navigation`, `local_state_change`,
  `durable_configuration_change`, `external_io`, `device_control`,
  `destructive`, and `unsupported`.
- [x] `[must]` M4 implementation slice: candidate Apply records a side-effect policy
  decision and blocks high-risk classes (`destructive`, `external_io`,
  `device_control`, `unsupported`) before mutation.
- [ ] `[must]` Add conflict checks: duplicate templates, overbroad regex,
  owner conflicts, entity-alias ambiguity, and action mismatch.
- [x] `[must]` M4 implementation slice: Apply validation checks duplicate regex/example
  templates through `preview_template_patch`, blocks overbroad non-read-only
  regex rules, and rejects action-intent or owner-target mismatches.
- [ ] `[must]` Add lightweight security abuse checks that reduce high-risk
  failures early: prompt-injection markers in user utterances and descriptors,
  overbroad templates for non-read-only actions, alias collisions with system
  commands, unexpected MCP target scope, and untrusted skill-authored hints.
- [x] `[must]` M4 implementation slice: Apply validation checks prompt-injection
  markers, overbroad templates for non-read-only actions, and system-command
  alias collisions. MCP target-scope and untrusted-hint validation remain a
  deeper policy pass.
- [ ] `[must]` Verify both replayed understanding and action outcome when an
  action is dispatched: modal opened, scenario switched, skill result emitted,
  endpoint command acknowledged, or failure recorded.
- [x] `[must]` Current regex Apply verifies replayed understanding by re-probing the
  source phrase and requiring the resulting intent to match the planned
  candidate intent before emitting `understanding.acquired`.
- [x] `[must]` M4 implementation slice: voice-confirmed candidates with safe
  side-effect policy now dispatch only by emitting the normal
  `nlp.intent.detected` event, and blocked candidates record
  `dispatch_status=blocked` instead of mutating UI/host state directly.
- [x] `[must]` M4 implementation slice: dispatcher now emits `nlu.action.dispatched`
  / `nlu.action.dispatch_failed`; Teacher records `dispatch_status=emitted`
  or `failed` with action target, action payload, reason, and a Teacher event.
- [x] `[must]` Add client-level `desktop.modal.opened` / `desktop.modal.open_failed`
  acknowledgements; Teacher links them to `dispatch_status=succeeded` or
  `failed`.
- [ ] `[should]` Add host/skill/endpoint acknowledgements beyond dispatcher event
  emission: scenario switched, skill result emitted, endpoint command
  acknowledged.
- [ ] `[must]` Add rate limits, duplicate suppression, and queue/backpressure
  policy for Root/OpenAI Teacher calls by webspace, route, request class, and
  repeated phrase hash.
- [x] `[must]` M4 implementation slice: LLM Teacher now has an in-process rate/repeated
  phrase gate before MCP evidence and Root/OpenAI calls. It records
  `llm.skipped` / `nlp.teacher.llm.skipped` with rate evidence, keeps the
  existing background concurrency semaphore and in-flight request de-dup, and
  exempts correction/confirmation retry metadata.
- [x] `[must]` M4 implementation slice: repeated voice misses re-open confirmation for
  an existing `pending` / `validation_failed` regex candidate before falling
  back to the generic "not understood" message, so unresolved hypotheses stay
  actionable even when Root/OpenAI times out or duplicate suppression skips a
  new LLM candidate.
- [ ] `[should]` Add golden positive/negative phrase suites, ambiguity
  fixtures, STT-noise fixtures, and per-skill regression reports.
- [ ] `[should]` Add cost accounting metrics for Teacher: LLM calls, retries,
  timeouts, tokens/estimated cost when available, cache hit rate, and
  resolved-miss cost.
- [ ] `[should]` Keep QA metrics backed by logs first: miss rate,
  clarification success, correction rate, rollback rate, promotion acceptance,
  and time to resolution, each tied to concrete questions the operator can ask.
- [ ] `[could]` Show explainability evidence: selected action, alternative
  candidates, rejected strategy, used MCP facts, confidence, and risk.

### F. Persistence, Promotion, and Publication

- [ ] `[must]` Separate local learned overlays from repo-owned skill/scenario
  artifacts and public reusable templates.
- [x] `[must]` Implementation slice: LLM-created candidates, accepted regex
  rules, governed training examples, and plan/development candidates now carry
  a local learned promotion envelope. Public export is blocked by default until
  a future explicit promotion gate approves it.
- [ ] `[must]` Add promotion states: `local_learned`,
  `promotion_candidate`, `promoted_to_workspace`, `pushed_to_repo`,
  `published`, and `rejected_for_publication`.
- [x] `[must]` Implementation slice: `local_learned` is the default state for
  every accepted Teacher artifact; applied plan candidates are marked for
  operator/developer handoff rather than treated as publishable.
- [ ] `[must]` Add template portability class: `session-local`,
  `user-local`, `workspace-local`, `scenario-local`, `skill-global`,
  `system-global`, or `public-reusable`.
- [x] `[must]` Implementation slice: candidates and accepted artifacts carry
  target-derived portability (`scenario-local`, `skill-global`,
  `system-global`, `workspace-local`, `session-local`, `user-local`, or
  `public-reusable`).
- [ ] `[must]` Attach provenance to every accepted artifact: request id,
  thread id, prompt/context hashes, model id/version, owner, operator/user
  feedback, verification result, rollback pointer, and commit/push id when
  promoted.
- [x] `[must]` Implementation slice: candidates store request/thread, model,
  decision, prompt/context/request hashes, owner, route/device audit, MCP tool
  audit without bearer material; accepted regex candidates store rollback
  pointer and verification result, and governed training examples store
  request/candidate provenance in Teacher dataset/system-action feedback.
- [ ] `[should]` Provide operator controls to promote high-value local
  examples into skill/scenario repositories after regression checks.
- [ ] `[must]` Keep private/local aliases and user-specific names out of
  public artifacts unless explicitly approved.
- [x] `[must]` Implementation slice: promotion/privacy policy snapshot marks
  local entity names, device names, user aliases, personal examples, and MCP
  session scope as private fields that block public promotion until explicit
  approval.
- [ ] `[deferred]` Publish a shared public NLU template registry across
  independent AdaOS installations.
- [ ] `[deferred]` Implement anonymization/redaction of named entities before
  public promotion. The target model must reserve the hook now; actual
  anonymizer quality gates come later.

### G. Operator UI and Human Verification

- [ ] `[must]` Add Teacher UI Check phrase with trace, ranking, entities,
  canonical slots, selected action, and action preview.
- [x] `[must]` Implementation slice: NLU Teacher has a `Check` tab wired to
  `POST /api/nlu/teacher/{webspace_id}/probe` through a client-side
  `nlu.teacher.probe` host action. It displays the raw dry-run probe result
  in the modal without dispatching the matched action. Full trace/ranking
  layout and action-preview widgets remain to be added on top of the same
  result payload.
- [ ] `[must]` Add Teacher UI views for candidates, clarification sessions,
  lifecycle state, Apply, Rollback, Reject, and "not that" feedback.
- [x] `[must]` Implementation slice: current candidate UI uses explicit Teacher
  API calls for Apply and Rollback. The materialized NLU Teacher scenario shows
  Rollback for applied candidates; Reject / "not that" still needs a dedicated
  backend/UI contract beyond the existing Voice confirmation rejection path.
- [ ] `[must]` Add UI evidence that distinguishes NLU gap, provider outage,
  descriptor gap, unsupported action, ambiguous entity, and missing
  capability.
- [x] `[must]` Implementation slice: Teacher bridge now separates hard provider
  or stage outages from teachable NLU gaps before invoking the LLM. Transient
  provider failures with multi-engine miss evidence remain teachable and carry
  `provider_issue` warning evidence for the UI/read-model; hard provider
  states emit `not_obtained.skipped` / `nlp.teacher.skipped` instead.
- [ ] `[should]` Add template inventory, patch preview, promotion controls,
  and regression impact preview.
- [ ] `[could]` Add compact Voice affordances for listening from every
  scenario while preserving chat history and avoiding stale message playback.
- [ ] `[deferred]` Build full operator analytics dashboards; start with focused
  trace and lifecycle evidence.

### H. Privacy, Security, and Retention

- [ ] `[must]` Define retention policy for raw utterances, STT text,
  normalized text, LLM prompt context, traces, candidates, and feedback.
- [x] `[must]` Implementation slice: Teacher state carries
  `nlu.teacher.retention.v1` with local retention scopes for raw utterances,
  STT text, normalized text, prompt context hashes, traces, candidates, and
  feedback.
- [ ] `[must]` Define promotion privacy gates: local entity names, device
  names, user aliases, and personal examples stay private unless explicitly
  approved.
- [x] `[must]` Implementation slice: `nlu.teacher.promotion.v1` blocks public
  export by default and requires explicit approval before local private fields
  can leave the workspace/local overlay.
- [ ] `[must]` Ensure MCP bearer/session scope is recorded as audit evidence
  but never embedded into prompts, templates, examples, or published artifacts.
- [x] `[must]` Implementation slice: candidate provenance records MCP
  mode/status/source/tool-list hashes and `mcp_session_scope_recorded`,
  redacts token-like keys, and tests assert `mcp_bearer_embedded=false`.
- [ ] `[must]` Add minimal threat-model checklist for NLU Teacher and skill
  hints: prompt injection, malicious descriptors, alias hijacking, overbroad
  destructive templates, and cross-subnet MCP scope confusion.
- [x] `[must]` Implementation slice: Teacher state publishes
  `nlu.teacher.threat_model.v1` covering prompt injection, untrusted
  descriptors, alias hijacking, overbroad destructive templates, unexpected
  MCP target scope, and cross-subnet scope confusion.
- [ ] `[should]` Add delete/export hooks for Teacher traces and learned local
  overlays by request/thread/webspace.
- [ ] `[deferred]` Implement robust anonymization of named entities for public
  template promotion.

### I. Cost, Quota, and Offline Operation

- [ ] `[must]` Add Teacher LLM budget controls: per-webspace rate limit,
  repeated-miss dedupe, max retries, queue depth, and fallback behavior when
  Root/OpenAI is unavailable.
- [x] `[must]` Implementation slice: existing per-webspace/route rate and
  repeated-miss gates now write budget counters, skipped/error/deferred
  reasons, recent events, policy metadata, and bounded
  `deferred_enrichment_queue` entries.
- [ ] `[must]` Keep NLU fast path fully operational when Root/OpenAI is down;
  store misses for later batch enrichment instead of blocking Voice/chat.
- [x] `[must]` Implementation slice: Root/OpenAI call failures and empty LLM
  outputs now record `llm.deferred`, update budget state, and keep the
  original miss available for later batch enrichment without mutating the
  deterministic NLU fast path.
- [ ] `[should]` Add MCP descriptor cache metrics and invalidation triggers so
  cost/performance tuning can be based on evidence.
- [ ] `[could]` Add batch review mode for accumulated misses instead of
  realtime LLM processing.

### J. Skill and Scenario Developer Workflow

- [ ] `[must]` Define `nlu_hints` / `llm_hints` schema for skills and
  scenarios: user-facing actions, aliases, examples, slots, entities, owner,
  side-effect class, preview method, and public/private scope.
- [ ] `[must]` Generate a conversational-interface skeleton when creating a
  skill or scenario, so NLU Teacher can reason over capabilities without code
  access.
- [ ] `[must]` Add validation/lint checks for hints during skill/scenario push:
  malformed examples, missing side-effect class, ambiguous aliases, unknown
  action ids, and unsafe public/private scope.
- [ ] `[should]` Add generated NLU/MCP descriptor docs for each skill/scenario
  so humans and LLMs see the same capability surface.
- [ ] `[deferred]` Auto-generate rich phrase sets for new skills/scenarios;
  start with a minimal skeleton and explicit developer-authored examples.

### K. Channels, Locale, and Accessibility

- [ ] `[must]` Treat Voice, typed chat, command palette, API-originated text,
  browser/device-specific input, and remote Root-originated requests as
  first-class channels with different confirmation UX and trace metadata.
- [ ] `[must]` Add locale policy for RU/EN and mixed-language phrases:
  locale-specific aliases, transliteration, STT-noise variants, and
  locale-safe promotion rules.
- [ ] `[must]` Ensure named-entity voice aliases are locale-aware and can be
  kept local/private independently from public skill/scenario examples.
- [ ] `[should]` Improve typed chat as a Teacher-visible channel, not only as a
  Voice transcript viewer.
- [ ] `[deferred]` Add full multi-locale model/profile management beyond
  RU/EN until usage statistics justify it.

### L. Development Handoff and Recovery

- [ ] `[must]` Represent missing action descriptors as `descriptor_fix`
  candidates when a skill/scenario capability exists but is invisible or
  underspecified for NLU/MCP.
- [ ] `[must]` Represent missing capabilities as `development_task`
  candidates for the future [Builder](../architecture/builder.md) workflow, with requested behavior,
  likely owner, missing surface, evidence, and replay phrase.
- [ ] `[should]` Link completed skill/scenario development tasks back to the
  original NLU request and rerun the phrase through the normal pipeline.
- [ ] `[deferred]` Plan wrong-dispatch recovery and undo: if AdaOS performed
  the wrong UI/config/endpoint action, record the correction and restore state
  when the side-effect class supports it.

### M. Quality and Analytics Plane

- [ ] `[must]` Define the first QA questions before adding dashboards:
  "why did this phrase miss?", "why did it dispatch?", "what did it cost?",
  "what changed after learning?", and "is this safe to publish?".
- [ ] `[must]` Ensure logs contain enough structured fields to answer those
  questions without a dedicated analytics UI.
- [ ] `[should]` Build lightweight analytics over logs for miss rate,
  false-accept samples, clarification success, correction rate, rollback
  rate, promotion acceptance, latency, and cost.
- [ ] `[could]` Add multidimensional QA dashboards after the logging model is
  stable and real usage questions are known.

## Phase 0: Teacher Contracts and Guardrails

- [x] `[must]` Define the teacher request/thread model:
  `request_id`, `thread_id`, previous request link, current correction target,
  user phrase, route context, and source channel.
- [x] `[must]` Define candidate records:
  `candidate_id`, class, planned intent/action, target owner, proposed
  template/patch, verification status, dispatch status, feedback status, audit
  ids, and rollback pointer.
- [x] `[must]` Define supported candidate classes:
  - `skill_action`
  - `interface_action`
  - `endpoint_command`
  - `scenario_flow`
  - `entity_correction`
  - `nlu_correction`
  - `development_task`
  - `non_actionable`
- [x] `[must]` Define candidate lifecycle states:
  `proposed`, `previewed`, `intent_matched`, `dispatch_previewed`,
  `dispatched`, `accepted`, `corrected`, `rejected`, `quarantined`,
  `applied`, and `rolled_back`.
- [x] `[must]` Define event names and idempotency keys for proposal, preview, apply,
  dispatch, feedback, rollback, and duplicate suppression.
- [x] `[must]` Define response policy for voice/chat/UI:
  when to dispatch, ask a clarification, save feedback, create a development
  task, or avoid mutation.
- [x] `[must]` Define MCP capability profiles:
  read-only context, probe/preview, authoring proposal, durable apply,
  dispatch preview, and operator-approved dispatch.
- [x] `[must]` Define and implement the first LLM prompt data policy: redact stored
  prompt logs, avoid embedding bearer tokens, bound context snapshots, and
  record request/context/prompt hashes for audit.
- [ ] `[should]` Add RU/EN Unicode fixtures for Teacher probes, correction threads, and
  template patch previews.

## Phase 1: Baseline Runtime

- [x] `[must]` Regex-first pipeline with dynamic scenario/skill regex rules.
- [x] `[must]` Optional neural delegation event (`nlp.intent.detect.neural`) behind
  `ADAOS_NLU_NEURAL` or installed `neural_nlu_service_skill` auto-detection.
- [x] `[must]` Rasa NLU service-skill isolated from the hub Python environment.
- [x] `[must]` Rasa service-skill prepared in A/B skill runtime slots.
- [x] `[must]` Confidence/fallback path to `nlp.intent.not_obtained`.
- [x] `[must]` Baseline desktop intents for opening modals and node-scoped modals.
- [x] `[must]` Remove Neural NLU runtime-provider delivery through `src/adaos/interpreter_data`.
- [x] `[must]` Ensure Neural NLU parse bridge only discovers/starts installed service skills and does
  not mutate workspace skills or A/B slots on demand.

## Phase 2: Operator Feedback Loop

- [x] `[must]` NLU Teacher stores not-obtained requests per webspace.
- [x] `[must]` Teacher can apply regex candidates into scenario/skill-owned artifacts.
- [x] `[must]` Teacher candidate Apply is available through API:
  `POST /api/nlu/teacher/{webspace_id}/candidate/apply`.
- [x] `[must]` Applied regex candidates are immediately checked against the original
  phrase and marked `intent_matched` only when the runtime probe returns the
  LLM-planned intent.
- [x] `[must]` Successful candidate verification emits
  `nlp.teacher.understanding.acquired` and records Teacher audit events.
- [x] `[must]` Regex candidate rollback is available through
  `POST /api/nlu/teacher/{webspace_id}/candidate/rollback` and removes the
  applied rule from owner artifact plus runtime cache.
- [x] `[must]` Teacher can apply dataset revisions into scenario training content.
- [x] `[must]` Dry-run phrase probe API for Teacher UI:
  - `POST /api/nlu/teacher/{webspace_id}/probe`
  - regex-first, optional Rasa fallback
  - returns `intent_ranking`, `entities`, `slots`, `stages`
  - does not dispatch actions
- [x] `[must]` Human verification checklist separates current API/CLI checks from target UI behavior.
- [ ] `[should]` UI field for "check phrase" wired to the probe endpoint.
- [x] `[should]` Implementation slice: current NLU Teacher UI can run a dry-run
  phrase probe from the `Check` tab and stores the result in modal state.
- [ ] `[deferred]` UI buttons: "correct", "fix", "save example".
- [x] `[must]` Operator-approved positive feedback stored with audit metadata.
- [x] `[must]` Route accepted feedback to the owning NLU training artifact:
  skill, scenario, or system action feedback overlay.
- [ ] `[deferred]` Route named-entity corrections to the governed named-entity source.
- [ ] `[deferred]` Add explicit correction targets for core/client actions that are not
  implemented as skills.

## Phase 3: Observability

- [x] `[must]` `data.nlu_trace.items[]` stores request/detected/not-obtained events.
- [x] `[must]` Stage trace event `nlu.trace.stage` records:
  - `request`
  - `regex`
  - `pipeline delegate`
  - `rasa`
  - `dispatcher action/reject`
- [ ] `[could]` Trace UI should show `voice text -> regex/neural/rasa -> intent -> action`.
- [x] `[must]` Add machine-readable Neural NLU readiness check for artifacts, service
  discovery, live health, model load, and index backend.
- [ ] `[could]` Add latency per stage and service timing.
- [ ] `[deferred]` Add golden phrase regression reports.
- [x] `[must]` Add neural usage statistics: request count, latency, confidence
  histogram, accept/abstain/reject counts, fallback ratio, and per-intent
  status evidence.
- [x] `[must]` Add named-entity canonicalization statistics: hit/miss/ambiguity counts
  and unresolved spans.
- [x] `[must]` Voice chat desktop widget can show a non-dispatching Neural NLU probe
  result (`intent`, `via`, confidence, and slots) in node-scoped chat history
  when `ADAOS_VOICE_CHAT_INTENT_DEMO=1`.

## Cross-Lane Human Verification Gates

- [x] `[must]` Current implemented behavior has a manual checklist: [nlu-human-verification.md](./nlu-human-verification.md).
- [x] `[must]` Documentation marks which NLU Teacher behaviors are current UI, backend/API only, or target architecture.
- [x] `[must]` NLU Teacher UI has a Signals tab backed by `data.nlu_teacher.workbench_signals` for queue, quarantine, LLM error, skip, and acquired-understanding monitoring.
- [x] `[must]` NLU Teacher Signals accordion opens details inline without also opening
  an implicit modal.
- [x] `[must]` NLU Teacher Candidate Apply has one primary action that uses the
  backend-resolved owner target; the obsolete duplicate "Apply to scenario"
  shortcut is removed from the current UI.
- [x] `[should]` NLU Teacher UI can run a phrase probe without terminal access.
- [ ] `[could]` NLU Teacher UI shows stage trace, ranking, entities, slots, lookup matches, confidence, and action preview.
- [ ] `[deferred]` NLU Teacher UI supports Correct/Fix/Save example with target selection and audit metadata for the currently safe existing-API flows.
- [x] `[must]` Backend/API/MCP preview flow exposes stable template fingerprints and stale-write checks for template corrections.
- [ ] `[could]` Operator-facing evidence distinguishes NLU gap, service/provider outage, low confidence, unsupported action, and missing capability.

## Phase 4a: Dynamic Lookups and Template Inventory

- [x] `[must]` Export baseline desktop lookup tables from workspace/packaged desktop manifests:
  - `modal_id`
  - `node_ref`
  - `app_id`
  - `scenario_id`
  - `webspace_id`
- [x] `[must]` Feed lookup tables into Rasa training data.
- [x] `[must]` Expose lookup tables for Teacher/LLM inspection:
  - `GET /api/nlu/teacher/{webspace_id}/lookups`
- [x] `[must]` Overlay live YJS desktop registry values on top of manifest lookups for Teacher API.
- [x] `[must]` Canonicalize lookup-backed dynamic regex slots before dispatch for
  `scenario_id`, `modal_id`, `app_id`, `node_ref`, `webspace_id`, and
  `skill_id`, so learned templates can match user-facing labels while actions
  receive stable ids.
- [x] `[must]` Expose stable template ids/fingerprints for current regex rules, examples, intent routes, and system-action examples through API and MCP inventory.
- [x] `[must]` Implement preview-time stale-write checks using target/template fingerprints.
- [ ] `[deferred]` Extend the same inventory to Rasa/neural labels and lookup-set patching.
- [x] `[must]` Define the system action catalog for currently runtime-backed core/client
  commands such as open, switch, reload, reset, and install toggle. Move,
  hide, and pin remain blocked on runtime host actions.
- [x] `[must]` Include system action examples in NLU authoring context without treating
  those actions as user skills.

## Phase 4b: Runtime Named Entities and Canonicalization

- [x] `[must]` Add a named-entity read model over devices, nodes, browsers, webspaces,
  scenarios, skills, apps, and modals.
- [x] `[must]` Add a deterministic resolver that maps display names, observed names, and
  aliases to canonical refs before model dispatch.
- [x] `[must]` Add entity masking so model-facing text can use placeholders such as
  `{device}`, `{webspace}`, and `{scenario}`.
- [x] `[must]` Add ambiguity handling instead of silently choosing between conflicting
  aliases.
- [x] `[must]` Add Teacher/probe output for resolved entities, unresolved spans,
  canonical refs, and ambiguity evidence.
- [x] `[must]` Add regression tests proving alias and device-name changes do not require
  Rasa/neural retraining.
- [x] `[must]` Track the full target design in
  [Named Entities and Canonical Naming](../architecture/named-entities.md).
- [x] `[must]` Feed canonicalized text and entity evidence into the neural provider
  contract.
- [ ] `[could]` Ensure Rasa and neural training fingerprints exclude runtime aliases by
  default.

## Phase 5: Teacher Authoring and MCP

### Ground Rule

- [x] `[must]` LLM cannot call SDK functions, publish events, invoke skill tools, or mutate UI state directly in the current Teacher loop.
- [x] `[must]` LLM can only propose AdaOS-owned candidates and patches; AdaOS validates, traces, previews, applies, and dispatches them.
- [x] `[must]` Every implemented teacher step has a trace/audit surface: `nlu.trace`, `data.nlu_teacher.*`, Root MCP audit, or event bus evidence.

### 5a: Existing-API Working Loop

- [x] `[must]` Use the current Teacher API as the first operational loop before adding new MCP write surfaces:
  - `POST /api/nlu/teacher/{webspace_id}/probe`
  - `GET /api/nlu/teacher/{webspace_id}/lookups`
  - `POST /api/nlu/teacher/{webspace_id}/candidate/apply`
  - `POST /api/nlu/teacher/{webspace_id}/example/save`
- [x] `[must]` Start with a narrow candidate type: regex/template candidate for an existing AdaOS intent, not a generic action candidate.
- [x] `[must]` Record planned intent, owner hint, proposed regex template, and verification status.
- [x] `[must]` Record correction-thread link for follow-up correction phrases.
- [x] `[must]` Record first dispatch status and dispatcher outcome for the
  voice-confirmed safe candidate path.
- [ ] `[should]` Generalize dispatch status to all candidate/action classes and attach
  factual host/skill/endpoint outcome evidence.
- [x] `[must]` LLM Teacher enablement inherits `root.llm.allow_nlu_teacher` when env
  overrides are unset; disabled LLM runtime records `llm.skipped` instead of
  silently dropping captured requests.
- [x] `[must]` LLM Teacher prompt includes governed Root MCP evidence from
  `nlu_authoring.get_context`, `nlu_authoring.check_phrase`,
  `nlu_authoring.get_dialog_context`, `nlu_authoring.list_training_targets`,
  `nlu_authoring.list_templates`, and `sdk.describe_surface`.
- [x] `[must]` LLM Teacher can run in MCP-aware hybrid mode: it prepares an OpenAI
  `responses_tool` descriptor from either an explicit debug bearer or a
  short-lived root-issued MCP session, passes it through `/v1/llm/response`,
  records redacted descriptor/audit fields, and falls back to prompt snapshot
  evidence when MCP tool preparation is unavailable.
- [ ] `[must]` Move NLU authoring context retrieval from pre-collected prompt
  snapshots toward LLM-selected MCP calls after public Root MCP exposes the
  same scoped `nlu_authoring.*`, `desktop.registry.*`, template, and preview
  tools that the local Root MCP service already provides.
- [x] `[must]` LLM Teacher prompt now treats
  `context.root_mcp.nlu_authoring_context.action_surface.available_actions` as
  the primary governed action inventory and uses `runtime_state`,
  `process_state`, and `developer_hints` for contextual disambiguation.
- [x] `[must]` LLM Teacher collects MCP evidence off the API/event loop with a bounded
  timeout so slow Root MCP/tool probes do not block Teacher state/UI reads.
- [x] `[must]` LLM Teacher caches Root MCP descriptor evidence with a short TTL so
  repeated misses in the same target/subnet can reuse heavy context while each
  phrase still gets a fresh `check_phrase` and dialog context.
- [x] `[must]` Teacher events are durably persisted as they are appended, so partial LLM
  traces survive backend restart before a candidate is generated.
- [x] `[must]` LLM Teacher parses plain JSON and fenced JSON responses, previews regex
  candidates against the source phrase, and quarantines candidates that do not
  compile or do not match the phrase.
- [x] `[must]` LLM regex candidates normalize common lookup slot aliases
  (`scenario` -> `scenario_id`, `modal` -> `modal_id`, etc.) and repair
  host-action storage targets to the current scenario owner.
- [x] `[must]` LLM regex candidates for `desktop.open_modal` repair captured
  display labels/aliases to canonical modal evidence before preview/apply, so
  commands like "show subnet environment variables" can validate against
  `subnet_env_modal`.
- [x] `[must]` Skill/webui descriptor aliases are consumed by lookup normalization and
  Apply validation; `subnet_env` now publishes RU/EN aliases for the Subnet
  Environment modal so voice phrases for subnet environment variables resolve
  to `subnet_env_modal`.
- [x] `[must]` Applying a Teacher regex rule enables `regex_enabled` for the webspace if
  the runtime regex stage was disabled, so a verified rule is actually used by
  the next normal `nlp.intent.detect.request`.
- [x] `[must]` Voice-originated regex candidates use a confirmation loop before Apply:
  LLM asks a concrete hypothesis question in Voice, `да` applies, first `нет`
  rejects the candidate and triggers one retry with rejected-candidate context,
  and a second rejection asks for clarification.
- [x] `[must]` Voice confirmation answers are not routed into normal NLU detection, so
  short replies such as `да` and `нет` do not create extra Teacher misses.
- [x] `[must]` Repeated voice phrases reuse a matching unresolved candidate and repeat
  the confirmation prompt before the generic miss/fallback response. This
  covers `pending` and `validation_failed` candidates whose Apply can pass
  after descriptor/lookup aliases are fixed.
- [x] `[must]` Voice chat no longer reads the last loaded hub message when the modal is
  opened; only newly arriving hub messages are eligible for auto-speak.
- [x] `[must]` Voice router suppresses short non-command STT tails while an active
  Teacher confirmation is awaiting an answer, so fragments like "от сети" do
  not become a second Teacher request.
- [x] `[must]` Voice chat stream is router/YJS-owned and compact: the browser receives
  only the last few turns, and assistant/system responses from modal Voice and
  the header Listen button share the same history.
- [x] `[must]` Candidate Apply rejection writes a Teacher event and visible UI/chat
  feedback instead of failing silently when validation blocks a candidate.
- [x] `[must]` Teacher read-model API methods that use synchronous YDoc/read-model
  helpers run off the API event loop, so trace/template/target reads do not
  fail inside async FastAPI handlers.
- [ ] `[should]` Persist `nlu_trace` outside the live scenario document as well as in
  `data.nlu_trace`, because scenario switches can rebuild runtime state and
  clear the short-lived trace timeline after a successful UI action.
- [x] `[must]` Add repeatable LLM-training smoke examples for:
  - `skill_action`: LLM proposes a regex for an existing skill-routed intent,
    Apply stores it in `skill.yaml`, replay matches, rollback restores miss.
  - `interface_action`: LLM proposes a regex for a scenario-owned host action,
    Apply stores it in `scenario.json`, replay matches, rollback restores miss.
  - `interface_action/scenario_switch`: LLM can learn phrases such as
    `Покажи Infrascope`; replay returns the existing scenario-switch intent
    with canonical `scenario_id=infrascope`.
  - `endpoint_command`: LLM proposes a regex for an existing endpoint-routed
    action such as showing text on an assigned display endpoint; Apply stores
    it in the owning skill/scenario artifact, replay matches, and dispatch
    preview resolves the endpoint role before any command is sent.
- [x] `[must]` After a regex/template candidate is trusted-applied, re-run phrase check and mark it verified only if the returned intent
  matches the LLM-planned intent.
- [x] `[must]` Dispatch verified voice-confirmed candidates only through the normal AdaOS intent/action path and only when the candidate's action side-effect class is
  allowed for auto-dispatch.
- [ ] `[should]` Extend verified-candidate dispatch to non-modal action classes after
  outcome ack/error contracts are available.
- [x] `[must]` Link user corrections such as "no, that is not it" to the previous request/candidate for the next teacher cycle.
- [x] `[must]` Distinguish true NLU gaps from service-down or provider-disabled states before asking the LLM to create templates.
- [x] `[must]` Add smoke tests for candidate apply -> regex persist -> probe match -> `understanding.acquired`.
- [x] `[must]` Add smoke tests for miss -> LLM candidate proposal, false candidate
  quarantine, duplicate candidate suppression, and correction-thread
  continuation.

### 5b: Minimal Read-Only MCP Plane

- [ ] `[should]` MCP Server modal issues scoped NLU authoring token.
- [x] `[must]` Root resolves token to subnet/zone/capabilities.
- [x] `[must]` LLM Teacher can attach a scoped Root MCP OpenAI tool descriptor to the
  root LLM proxy in hybrid mode. The descriptor bearer is redacted from
  Teacher logs; cache keys include target, zone, server label, and allowed
  tools.
- [x] `[must]` Define a root-public `NLUTeacherRead` capability profile that
  exposes only read-only NLU authoring/descriptive tools:
  `nlu_authoring.get_context`, `desktop.registry.lookup`,
  `nlu_authoring.check_phrase`, `nlu_authoring.get_dialog_context`,
  `nlu_authoring.list_training_targets`, `nlu_authoring.list_templates`,
  `sdk.describe_surface`, and `desktop.preview_action`.
- [x] `[must]` Cache subnet-scoped NLU descriptive snapshots on root so
  OpenAI MCP calls can read action/entity/template context without repeatedly
  waiting on a live hub roundtrip.
- [x] `[must]` First root-public slice: `NLUTeacherRead` is available as a capability
  profile, public Root MCP exposes OpenAI-compatible read tool names, and the
  cached NLU tools return target/subnet scope, report freshness, and cache
  metadata.
- [x] `[must]` Add a live hub/proxy path for deterministic
  `nlu_authoring.check_phrase` and `desktop.preview_action`. Public Root MCP
  now calls `/api/admin/root_mcp/call` on the active scoped hub through the
  root route proxy; if the hub is disconnected or not updated, it falls back to
  cached `requires_live_hub` evidence.
- [x] `[must]` Expose Root MCP HTTP JSON-RPC endpoint at `/v1/root/mcp` for
  remote-MCP clients: `initialize`, `tools/list`, `tools/call`, and
  notification handling. This is the transport required for the target mode
  where Root/OpenAI can let the LLM choose which MCP tools to call instead of
  only receiving pre-collected MCP evidence in the prompt.
- [x] `[must]` Add Root MCP `nlu_authoring.get_context` for named-entity and authoring-boundary evidence.
- [x] `[must]` Extend Root MCP `nlu_authoring.get_context` with M2 contextual action
  surface, runtime state, process state, developer hints, lookup summary, and
  fingerprints.
- [x] `[must]` Add Root MCP `nlu_authoring.check_phrase` backed by the current probe service.
- [x] `[must]` Root MCP passes bearer/session subnet scope into NLU authoring handlers
  and returns `root_scope` / `target_id` so the LLM sees which subnet target the
  context belongs to.
- [x] `[must]` Add Codex bridge tool `check_nlu_phrase`.
- [x] `[must]` Add/read current MCP surfaces:
  - `nlu_authoring.get_trace`
  - `nlu_authoring.get_dialog_context`
  - `nlu_authoring.get_recent_failures`
  - `desktop.registry.lookup`
  - `skill.describe_nlu`
  - `scenario.describe_nlu`
  - `sdk.describe_surface` (descriptors only, no execution)
- [x] `[must]` Keep contextual action surface read-only: no dispatch, no direct SDK
  call, and no training mutation can be performed through the descriptor.
- [ ] `[deferred]` Add `nlu.describe_pipeline` and `skill.describe_tools`.
- [x] `[must]` Keep MCP read-only for context/inventory; preview APIs return dry-run gates without mutation or dispatch.
- [ ] `[could]` Add stricter request timeouts, result-size limits, and audit event summaries for every context-reading call.

### 5c: Action and Ownership Plane

- [x] `[must]` Classify teacher decisions as:
  - `skill_action`
  - `interface_action`
  - `endpoint_command`
  - `scenario_flow`
  - `entity_correction`
  - `nlu_correction`
  - `development_task`
  - `non_actionable`
- [ ] `[deferred]` Publish runtime-backed interface action descriptors:
  - modal open/close
  - scenario switch
  - go to home scenario
  - set home scenario
  - reload/reset webspace
  - app install/toggle
- [ ] `[deferred]` Publish runtime-backed endpoint command descriptors:
  - resolve endpoint role or alias through `EndpointAssignment`
  - show text or image on a `display_endpoint`
  - play prompt or content through an `audio_output_endpoint`
  - request endpoint diagnostics
  - subscribe to endpoint streams when policy allows
  - revoke, retire, or disable endpoint services through governed owner APIs
- [ ] `[deferred]` Add `desktop.get_state` for current scenario, home scenario, open modals, installed apps, focused route/node/browser.
- [x] `[must]` Add `desktop.preview_action` to show the host event/action without dispatch for the current system-action catalog.
- [ ] `[deferred]` Add `endpoint.preview_command` to show the resolved endpoint
  role, concrete endpoint id, service, policy gate, expected transport, and
  side-effect class without dispatch.
- [ ] `[deferred]` Add `nlu.resolve_owner` to map intent/action candidates to skill, scenario, system action, endpoint assignment/router, entity alias, or development task ownership.
- [ ] `[deferred]` Define action side-effect classes:
  read-only, UI navigation, reversible UI mutation, durable configuration
  mutation, external side effect, and unsupported.
- [ ] `[deferred]` Define owner conflict policy when a scenario route, skill NLU metadata, and system action catalog all match the same phrase.

### 5d: Template Inventory and Safe Apply

- [x] `[must]` Root MCP/API surfaces:
  - `nlu_authoring.list_templates`
  - `nlu.get_template`
  - `nlu_authoring.list_training_targets`
  - `nlu_authoring.preview_template_patch`
  - `nlu.apply_template_patch`
- [ ] `[deferred]` Add `nlu.get_template` and `nlu.apply_template_patch` as first-class MCP calls; current durable apply still uses candidate/example APIs.
- [x] `[must]` LLM receives current template inventory before proposing changes.
- [x] `[must]` Template preview uses stable template ids/fingerprints and `base_fingerprint` stale-write checks.
- [x] `[must]` Template patches can be previewed before durable apply; operator approval still uses existing candidate/example APIs.
- [x] `[must]` M4 implementation slice: existing candidate/example Apply now calls the
  template preview gate before durable mutation and stores validation evidence
  on the candidate.
- [ ] `[deferred]` Durable apply writes only through owner services/APIs: skill, scenario, system-action feedback, or named-entity alias source.
- [ ] `[deferred]` Add rollback pointers and audit records for every applied patch.
- [x] `[must]` M4 implementation slice: duplicate-template detection and simple
  overbroad-regex blast-radius guard run before durable candidate Apply.
- [ ] `[deferred]` Add golden-phrase impact preview before durable apply and
  expand blast-radius checks beyond simple overbroad-pattern guards.
- [ ] `[deferred]` Decide migration policy for legacy `data.nlu.regex_rules[]` mirrors versus owner-authored scenario/skill artifacts.

### 5e: Development Task Candidates

- [ ] `[deferred]` Represent missing capabilities as development tasks, not fake intents.
- [ ] `[deferred]` Task candidate shape includes requested behavior, likely owner, missing action/tool surface, suggested skill/scenario change, and evidence.
- [ ] `[deferred]` Route task candidates to the Builder workflow for existing skill/scenario modification or new skill/scenario creation.
- [ ] `[deferred]` After the skill/scenario is changed, re-run the original phrase and link the result back to the task candidate.

### 5f: Teacher Acceptance Gates

- [ ] `[could]` Every phase has at least one test or smoke command that can be run without the UI.
- [x] `[must]` M4 implementation slice has non-UI tests for safe Apply, duplicate
  rejection, missing-slot action preview rejection, overbroad regex rejection,
  and action-intent mismatch.
- [ ] `[should]` Every accepted candidate stores trace, prompt/context hash, verification result, owner, and operator/trust policy evidence.
- [ ] `[deferred]` False positives can be rejected, quarantined, or rolled back without deleting unrelated user-authored training data.
- [ ] `[could]` RU and EN phrases pass through the same correction-thread and template-preview paths without mojibake or lossy normalization.
- [ ] `[could]` The UI can explain whether the result came from regex, Rasa, neural, lookup canonicalization, Teacher candidate, or provider fallback.

## Phase 6: Neural NLU Provider

### Provider Boundary

- [x] `[must]` Move `neural_nlu_service_skill` out of `src/adaos/interpreter_data` into
  normal registry/workspace skill delivery.
- [x] `[must]` Add separate experimental `neuro_nlu_lite_skill` delivery for weak-device
  validation without changing the production Neural NLU provider.
- [x] `[must]` Add opt-in `adaos install --neural-nlu` preparation for Neural NLU.
- [x] `[must]` Keep plain `adaos install` free of Neural NLU heavy dependencies.
- [x] `[must]` Make the neural bridge discover/start only installed service skills.
- [x] `[must]` Remove hot-path workspace mutation/bootstrap from neural parse handling.
- [x] `[must]` Keep provider dependencies (`torch`, `faiss-cpu`, etc.) out of the hub
  root venv.
- [x] `[must]` Keep Neuro Lite free of Torch/FAISS/Rasa dependencies for the first
  prototype baseline.

### Neuro Lite Experimental Stage

- [x] `[must]` Add `nlp.intent.detect.neuro_lite` bridge and stage trace events.
- [x] `[must]` Add runtime policy/flag support for `neuro_lite_enabled` and
  `ADAOS_NLU_NEURO_LITE`.
- [x] `[must]` Add `neuro_nlu_lite_skill` with `/health`, `/parse`, and `/rebuild`.
- [x] `[must]` Implement the first hash n-gram prototype baseline with accept/abstain
  behavior.
- [x] `[must]` Fall through to Neural/Rasa when Neuro Lite abstains or is disabled.
- [ ] `[deferred]` Add real hard-negative evaluation before considering attention or a tiny
  encoder.
- [ ] `[deferred]` Decide whether Neuro Lite should remain a separate provider, become a
  low-resource Neural profile, or be retired after evaluation.

### Inference Contract

- [x] `[must]` Freeze `/parse` request/response schema with `top_intent`,
  `confidence`, `alternatives`, `slots`, `model_id`, and `evidence`.
- [x] `[must]` Pass named-entity canonicalization evidence into `/parse`.
- [x] `[must]` Return matched examples, score components, and canonicalized text in
  `evidence`.
- [x] `[must]` Add confidence gates for accept/abstain/reject.
- [x] `[must]` Add neural abstain/error fallback to Rasa.
- [ ] `[should]` Route Rasa miss/low confidence to NLU Teacher.

### Notebook Approach Port

- [x] `[must]` Port masking logic into provider-owned runtime code.
- [x] `[must]` Port Char-CNN + BiLSTM model loader.
- [x] `[must]` Fix and test special-token compatibility between training and runtime.
- [x] `[must]` Port supervised-contrastive embedding projection usage.
- [x] `[must]` Persist a lazy Torch tensor positive-example k-NN cache as an
  intermediate step before FAISS indexes.
- [x] `[must]` Add optional lazy FAISS positive example index with Torch tensor fallback.
- [x] `[must]` Add FAISS negative example indexes.
- [x] `[must]` Add weighted ranker over softmax, k-NN similarity, and action/skill
  priors.
- [x] `[must]` Add intent/action id mapping from research labels to AdaOS canonical
  intents and system actions.

### Artifacts and ModelOps

- [x] `[must]` Define node-level active model layout owned by the service skill runtime.
- [x] `[must]` Add a notebook-output preparation script that writes `model.pt`,
  `labels.json`, `vocab.json`, example/intent manifests, ranker config, and
  provenance metrics into the active node-level layout.
- [x] `[must]` Store `model.pt`, `labels.json`/`intents_manifest.json`, `vocab.json`,
  optional `faiss.index`/`faiss.index.json`,
  `negative_faiss.index`/`negative_faiss.index.json`,
  `examples_manifest.jsonl`, `intent_map.json`, `ranker_config.json`, and
  `metrics.json` in the service-owned active layout.
- [x] `[must]` Add immutable `model_id` and model provenance metadata for prepared
  notebook artifacts.
- [x] `[must]` Add rollback pointer for the node-level active model.
- [x] `[must]` Add golden phrase regression report before model promotion.
- [ ] `[could]` Add full quality gates using macro-F1, abstain rate, and latency.
- [ ] `[deferred]` Per-locale/webspace/profile models until usage statistics justify
  the added operational complexity.

### Usage Statistics

- [x] `[must]` Record neural request count and latency per stage.
- [x] `[must]` Record confidence distributions and threshold bands.
- [x] `[must]` Record accept/abstain/reject counts per intent.
- [x] `[must]` Record fallback ratio `neural -> Rasa`.
- [x] `[must]` Record canonicalization hit/miss/ambiguity/unresolved counts for neural
  requests.
- [x] `[must]` Record abstained/rejected samples for Teacher review and retraining.
- [x] `[must]` Add bridge-level `neural-probe` check using the runtime confidence gates
  and usage-stat path.
- [x] `[must]` Link final Rasa accept/miss outcomes back to the neural fallback sample
  so `neural -> Rasa -> Teacher` can be measured end to end.
- [x] `[must]` Add operator diagnostics that combine Neural readiness and usage
  aggregates.
- [x] `[must]` Add experimental Neuro Lite runtime stage and weak-device service-skill
  baseline, separate from the production Neural NLU service.

### Training Data Feedback

- [x] `[must]` Export skill-owned examples from skills.
- [x] `[must]` Export scenario-owned examples from scenarios.
- [x] `[must]` Export core/client command examples from the system action catalog.
- [ ] `[could]` Export named-entity classes as masks, not as local alias training data.
- [x] `[must]` Let Teacher-approved corrections update regex, Neural, and Rasa datasets
  through the owning artifact.
- [x] `[must]` Add governed Neural reindex planning/apply flow for curated examples
  that are compatible with the active model labels.
- [x] `[must]` Rebuild/retrain the neural provider for curated examples that introduce
  new model labels.

## Immediate Next Steps

1. Extend the M4 validation policy from candidate Apply to dispatch: outcome
   checks for modal open, scenario switch, skill result, endpoint ack, and
   recorded failure paths.
2. Wire the Teacher UI Check phrase flow to show canonicalization, neural,
   Rasa, provider health, and action-preview evidence.
3. Add descriptor cache invalidation/metrics beyond TTL-only reuse, tied to
   registry/template/hint fingerprints.
4. Add full model promotion gates using macro-F1, abstain rate, latency,
   false-positive checks, and rollback evidence.

## Last Completed Slice

- NLU Teacher Phase 0 contracts are now captured in
  `src/adaos/abi/nlu.teacher.v1.schema.json`: request/thread, action
  candidate, template candidate, clarification session, lifecycle,
  idempotency, scope, response policy, negative feedback, and MCP capability
  profile records.
- Root MCP publishes `nlu_teacher_schema` as a descriptor set, includes it in
  the NLU authoring plane, and exposes explicit `NLUTeacherRead`,
  `NLUTeacherDryRun`, and `NLUTeacherAuthor` capability profiles.
- Voice/clarification rejection now records structured `negative_feedback`
  evidence with rejected candidate ids and selected answer data, so rejected
  alternatives are visible through Teacher events/dialog context instead of
  existing only as retry metadata.
- Teacher accepted artifacts now share a governance envelope across regex
  rules, governed training examples, and plan/development candidates:
  `promotion`, target-derived portability, `provenance`, and `privacy`.
  System-action feedback JSONL records persist the same envelope, while
  scenario/skill example saves keep the artifact mutation simple and expose
  the envelope through Teacher dataset/result audit.
- M4 candidate Apply validation is now enforced by a dedicated
  `teacher_validation` gate. It runs template preview, built-in action preview,
  side-effect policy, duplicate checks, overbroad-regex checks, prompt-injection
  marker checks, alias collision checks, and action/owner mismatch checks before
  durable mutation.
- Rejected candidates are marked `validation_failed` with stored validation
  evidence and emit `nlp.teacher.candidate.apply.rejected` with
  `reason=m4_validation_failed`; valid candidates store the passed validation
  evidence before continuing through regex/example Apply.
- Root/OpenAI Teacher calls now pass through a lightweight M4 rate gate before
  MCP evidence collection and LLM inference. The gate suppresses repeated
  routed phrases and over-limit webspace/route/request-class bursts while
  preserving correction/confirmation retries.
- Voice-confirmed safe candidates now continue from
  `understanding.acquired` into the regular `nlp.intent.detected` dispatcher
  path. The candidate records `dispatch_status=requested` and a
  `dispatch.requested` Teacher event; unsafe candidates are recorded as
  `dispatch_status=blocked`.
- The regular NLU dispatcher now emits factual action-dispatch outcomes.
  Teacher links `nlu.action.dispatched` / `nlu.action.dispatch_failed` back to
  the candidate and records `dispatch_status=emitted` or `failed` with target,
  payload, and reason.
- The web client now acknowledges `desktop.modal.open` with
  `desktop.modal.opened` or `desktop.modal.open_failed`; Teacher links this
  client ack back to the candidate as `dispatch_status=succeeded` or `failed`.
- M3 multi-engine authoring strategy is now enforced: Teacher normalizes
  `training_strategy`, treats non-regex strategies as first-class candidates,
  and rejects regex proposals when the selected strategy, `why_not_regex`, or
  a simple overbroad-pattern guard says regex is unsafe.
- `training_example` candidates represent Rasa/Neural feedback and Apply routes
  through governed `nlp.teacher.example.save`; no Rasa/Neural model is mutated
  directly by the LLM.
- `entity_alias`, `descriptor_fix`, and `development_task` candidates are
  persisted separately from regex/template candidates and can be accepted into
  the Teacher plan for owner-specific alias APIs or developer handoff.
- Teacher LLM event projections are compacted: request events keep audit
  hashes without embedding prompt messages, thread details use compact raw
  summaries, and normal `llm.response` logs are no longer counted as errors.
- M2 contextual action surface is now exposed through Root MCP
  `nlu_authoring.get_context`: the LLM receives `runtime_state`,
  `action_surface.available_actions`, `process_state`, `developer_hints`,
  named entities, lookup summary, fingerprints, and explicit read-only
  authoring boundaries.
- The contextual action surface includes system/interface actions and
  skill/scenario intent routes with owner, slots, examples, side-effect class,
  preview method, and source fingerprint.
- LLM Teacher now caches heavy Root MCP descriptor evidence with
  `ADAOS_NLU_MCP_EVIDENCE_CACHE_TTL_S` while keeping per-phrase
  `nlu_authoring.check_phrase` and dialog context uncached.
- LLM-generated regex candidates now include structured
  `action_candidate`, `template_candidate`, and `training_strategy` envelopes.
  This starts the M1 action-candidate contract without breaking the current
  legacy regex Apply/rollback behavior.
- Voice-originated confirmation prompts now mirror into
  `data.nlu_teacher.clarification_sessions[]`, giving the future dialog layer
  a structured session record while preserving the existing
  `pending_confirmations[]` flow.
- LLM `need_clarification` responses now create `llm_clarification`
  sessions. Short Voice answers (`yes/no/first/second/да/нет`) resolve the
  active session before normal NLU and record `clarification.answered`
  evidence with the selected option.
- NLU Teacher candidate Apply is exposed through
  `POST /api/nlu/teacher/{webspace_id}/candidate/apply`.
- Regex candidate Apply now re-probes the original phrase, records
  `candidate.verified`, marks matching candidates as `intent_matched`, and
  emits `nlp.teacher.understanding.acquired`.
- Regex candidate rollback removes applied Teacher rules and invalidates the
  dynamic regex cache so smoke checks are repeatable.
- Root MCP now exposes read-only `nlu_authoring.check_phrase`, and the Codex
  bridge exposes it as `check_nlu_phrase`.
- Root MCP NLU authoring results now carry bearer-derived `root_scope` and
  `target_id` evidence.
- LLM Teacher now includes read-only Root MCP authoring evidence
  (`nlu_authoring.get_context` and `nlu_authoring.check_phrase`) in the prompt
  before asking Root/OpenAI for a candidate.
- Skill manifests can publish `webui.json:nlu.llm_hints` with aliases,
  entities, and primary interface actions. Teacher lookup context consumes
  those hints together with inferred app/modal/action metadata so OpenAI can
  choose canonical `modal_id`, `app_id`, and `scenario_id` values.
- LLM Teacher MCP evidence collection now runs off the API/event loop with a
  bounded timeout, and Teacher events are persisted immediately when appended.
- Regex slot normalization now bounds live YJS lookup collection and falls back
  to baseline manifest lookup data, so an applied Teacher rule cannot block the
  Voice path while resolving a `modal_id` or similar lookup slot.
- LLM Teacher now repairs common model output mistakes for interface actions:
  lookup slot aliases are canonicalized and scenario-switch rules are stored in
  the current scenario owner rather than the scenario being opened.
- LLM Teacher now repairs `desktop.open_modal` candidates whose captured
  `modal_id` is a user-facing label/alias by preserving canonical modal
  evidence for validation and dispatch.
- LLM Teacher now prefers `desktop.open_modal` for generic show/open requests
  that target known apps with `launchModal`; `desktop.switch_scenario` remains
  reserved for explicit scenario-switch wording.
- LLM Teacher now accepts both plain JSON and fenced JSON model responses,
  previews proposed regex rules against the original phrase, and marks bad
  proposals as `quarantined` so Apply rejects them.
- Voice-originated regex candidates now ask for explicit user confirmation
  before Apply. Positive feedback applies and verifies the candidate; the
  first rejection retries with the rejected candidate in context; the second
  rejection asks for clarification.
- Voice confirmation replies are consumed as feedback and do not create new
  `да`/`нет` NLU misses. Opening the Voice modal no longer speaks the last
  historical hub message.
- Voice confirmation now suppresses short STT tails during the pending
  confirmation window, and Voice chat history is served as a compact
  router/YJS-owned stream shared by the modal and header Listen button.
- NLU Teacher UI now has a single Candidate Apply action using the backend
  owner target, and Signals accordion expansion no longer opens a second modal.
- LLM Teacher now stores request/context/prompt hashes on LLM logs and
  candidates, suppresses duplicate active regex candidates, and passes
  correction-thread context into the next LLM prompt when the user says
  "no/not that/нет/не то/...".
- Teacher bridge now classifies `nlp.intent.not_obtained` reasons and skips
  Root/OpenAI for hard provider/stage unavailable cases, while still treating
  low-confidence/no-intent outcomes as teachable NLU gaps. Transient provider
  failures such as `rasa_timeout` remain teachable when another active stage
  produced miss/fallback evidence, and carry `provider_issue` warning data for
  UI/read-model inspection.
- A closed-loop test now covers: regex miss -> LLM regex candidate -> Apply ->
  `understanding.acquired` -> repeated phrase resolves through `regex.dynamic`.
- Added repeatable test examples for `skill_action` and `interface_action`
  training with rollback to the original miss state.
- Live Root/OpenAI smokes covered `skill_action` and `interface_action`: both
  produced regex candidates that previewed, applied, replayed through
  `regex.dynamic`, and rolled back to the original miss state.
- Rasa is packaged as an optional default-on service-skill and installed into skill runtime slots.
- NLU Teacher has a dry-run phrase probe API with regex-first and optional Rasa fallback.
- NLU Teacher exposes baseline desktop lookup tables for `modal_id`, `node_ref`, `app_id`, `scenario_id`, and `webspace_id`.
- Teacher lookup API overlays live YJS values from `ui.application.modals`, `registry.merged.modals`, `data.catalog.apps`,
  `data.installed.apps`, `data.nodes`, and `ui.current_scenario`.
- Rasa export writes native lookup tables and `data/lookup_tables.json`; lookup summary is included in the training fingerprint.
- Runtime emits stage trace events for regex, pipeline delegation, Rasa, and dispatcher actions/rejects.
- Trace items are persisted to `data.nlu_trace.items[]` for the future UI timeline; a durable trace fallback is still planned
  so successful scenario-switch actions remain inspectable after the webspace rebuilds.
- Neural bridge records node-local aggregate usage stats in `state/nlu/neural_usage.json`, including latency,
  confidence bands, accept/abstain/reject counts, fallback ratio, canonicalization buckets, and review samples.
- Neural service skill now declares service-owned venv execution and keeps Torch/Numpy dependencies outside the hub root venv.
- Neural artifacts now include `intent_map.json` so notebook labels can map to AdaOS canonical intents and optional
  action ids while evidence preserves the original source label.
- Neural runtime now persists negative example indexes and records contrastive nearest-other-intent evidence.
- `adaos interpreter neural-diagnostics` now combines readiness and node-local usage aggregates for operators.
- A versioned system action catalog now exposes active host actions, system-owned NLU examples, and dispatcher mappings for
  default desktop commands such as modal open, scenario switch, app install toggle, webspace reload, and webspace reset.
- `adaos interpreter export-neural-training` writes a curated Neural training bundle from skill, scenario, and system-action
  examples under `state/interpreter/neural_training` without mutating active provider artifacts.
- `nlp.teacher.example.save` and `POST /api/nlu/teacher/{webspace_id}/example/save` now save operator-approved examples
  into scenario/skill artifacts or a system-action feedback overlay with audit metadata.
- `adaos interpreter neural-reindex` now reloads active Neural artifacts through service `/reindex`; `--from-curated`
  dry-runs the curated bundle and `--from-curated --apply` is guarded so active examples are replaced only when all
  curated labels already exist in the active model.
- `adaos interpreter neural-rebuild --from-curated` now trains a candidate Neural model for curated examples with new
  labels; explicit `--promote` backs up the active model, writes rollback pointers, clears stale indexes, and reindexes
  the service.
- `neuro_nlu_lite_skill` now provides an experimental weak-device
  `neuro_lite` provider stage with hash n-gram prototype matching and fallback
  to the next configured provider.
- NLU documentation now includes a human verification checklist and clearly separates current UI, backend/API-only behavior, and target UI.
