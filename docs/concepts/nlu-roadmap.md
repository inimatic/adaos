# NLU Roadmap Checklist

Current runtime implementation estimate: **92%** for the practical AdaOS NLU
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

- **M1: Action candidate and clarification core.** Misses can become structured
  action candidates or clarification sessions, and short answers resolve the
  active session before normal NLU.
- **M2: Contextual action surface.** Root/API context exposes current state,
  named entities, available actions, process state, and developer-authored
  hints with enough data to preview a candidate.
- **M3: Multi-engine authoring decision.** Teacher output includes
  `training_strategy` and can intentionally reject regex in favor of another
  strategy.
- **M4: Validation and safety gates.** Action preview, side-effect policy,
  conflict checks, cost controls, and privacy gates run before durable apply,
  dispatch, or promotion.
- **M5: Promotion and developer handoff.** Local learning can be promoted or
  rejected with provenance, while missing descriptors/capabilities create
  structured work for skill/scenario development.

## Balanced Target Roadmap

This checklist is the canonical target-roadmap slice built from the current
implementation and the architecture above. The detailed historical phases
below remain useful for tracking existing implementation work.

### A. Contracts and State Model

- [ ] `[must]` Define first-class `action_candidate` records separate from
  `template_candidate` records: candidate id, class, planned action/intent,
  slots, owner, side-effect class, action-preview status, dispatch status,
  feedback status, audit ids, and rollback pointer.
- [ ] `[must]` Define `template_candidate` records for regex/Rasa/Neural/entity
  alias/descriptor patches, each linked to an action candidate or explicit
  non-action decision.
- [x] First implementation slice: LLM-generated regex candidates now carry
  backward-compatible `action_candidate`, `template_candidate`, and
  `training_strategy` envelopes while the existing Apply/rollback path keeps
  using the legacy fields.
- [ ] `[must]` Define `clarification_session`: source request, active
  uncertainty kind, question, allowed answers, rejected candidates, retry
  count, timeout, final resolution, and thread id.
- [x] First implementation slice: current Voice confirmation state is mirrored
  into `data.nlu_teacher.clarification_sessions[]` with question,
  allowed answers, status, answer, attempt, target, and rejected candidate
  evidence.
- [ ] `[must]` Define candidate lifecycle states across understanding and
  execution: `proposed`, `phrase_previewed`, `action_previewed`,
  `clarification_requested`, `user_confirmed`, `applied`,
  `replay_intent_matched`, `dispatch_attempted`, `dispatch_succeeded`,
  `accepted`, `corrected`, `rejected`, `quarantined`, and `rolled_back`.
- [ ] `[must]` Define idempotency keys for request capture, LLM proposal,
  clarification answer, preview, apply, dispatch, feedback, promotion, and
  rollback.
- [ ] `[must]` Define scope fields for every request/thread/candidate:
  channel, route, webspace, scenario, device, user/session when available,
  locale, and privacy boundary.
- [ ] `[should]` Add RU/EN/STT-noise fixtures for request threads,
  clarification sessions, correction threads, and template patch previews.

### B. Context and Action Surface

- [ ] `[must]` Expose contextual action surface through API/MCP: current
  scenario, available apps/modals/scenarios, runtime-backed UI actions,
  skill-routed actions, endpoint commands, required slots, examples,
  side-effect class, owner, and preview method.
- [ ] `[must]` Expose current state through API/MCP: open modals, home/current
  scenario, focused route/node/browser, selected device, active/pending
  confirmations, recent errors, and user route context.
- [ ] `[must]` Expose process state relevant to language: active jobs, failed
  jobs, long-running operations, last user command, last assistant action,
  current warnings, and owning skill/process.
- [ ] `[must]` Extend skill/scenario manifests with authored `llm_hints` /
  `nlu_hints`: aliases, user-facing action descriptions, examples, slot
  schemas, entity names, owner hints, and side-effect class.
- [ ] `[must]` Connect named entities to voice control: expose voice-safe
  aliases, canonical ids, ambiguity evidence, entity ownership, and allowed
  voice actions for devices, nodes, endpoints, browsers, apps, modals,
  scenarios, skills, and processes.
- [ ] `[must]` Include entity scope and portability class in context:
  `session`, `user`, `workspace`, `scenario`, `skill`, `system`, or `public`.
- [ ] `[should]` Publish process/action affordances as named entities when the
  user can naturally refer to them by voice, for example "scan", "indexing",
  "display", "current browser", or "last failed job".
- [ ] `[should]` Cache Root MCP descriptive-plane snapshots per target/subnet
  with TTL and invalidation, so LLM context is usually complete without
  blocking the Voice path.
- [ ] `[deferred]` Publish deep SDK descriptors beyond read-only ownership and
  affordance discovery; LLM execution remains prohibited.

### C. Decision and Clarification

- [ ] `[must]` Update LLM output contract to return `decision`,
  `action_candidate`, `training_strategy`, `need_clarification`,
  `clarification_question`, `options`, `why_not_regex`, and `risk_notes`.
- [ ] `[must]` Implement uncertainty policy: direct action, confirmation,
  clarification, development task, or ignore based on confidence, ambiguity,
  side-effect class, and context.
- [ ] `[must]` Route short answers such as `yes/no/first/second/да/нет` through
  active clarification/confirmation sessions before normal NLU.
- [ ] `[must]` Record negative feedback and rejected alternatives as structured
  evidence, not only as a retry trigger.
- [ ] `[could]` Let Voice/UI present disambiguation options for ambiguous
  entities and actions, for example Media Indexer vs Media Server.
- [ ] `[deferred]` Support long multi-turn task planning dialogs; the first
  Teacher dialog loop should stay short and resolution-oriented.
- [ ] `[deferred]` Support multi-user/concurrent dialog ownership. The target
  model must preserve route/session fields now, but full parallel-user policy
  is intentionally postponed.

### D. Multi-Engine NLU Authoring

- [ ] `[must]` Add `training_strategy` selection:
  `regex`, `rasa_example`, `neural_example`, `entity_alias`,
  `descriptor_fix`, `development_task`, or `ignore`.
- [ ] `[must]` Require LLM to reject regex for broad semantic, highly
  contextual, or ambiguous phrases and choose Rasa/Neural/clarification or a
  descriptor fix instead.
- [ ] `[must]` Keep regex for deterministic command phrases and lookup-backed
  slots; add blast-radius preview before durable apply.
- [ ] `[must]` Save Rasa/Neural examples through owner artifacts or curated
  feedback overlays, not through direct model mutation.
- [ ] `[should]` Capture STT/raw text, normalized text, locale guess,
  transliteration/typo evidence, and entity canonicalization evidence for
  each teachable request.
- [ ] `[must]` Treat entity-alias learning as a first-class strategy distinct
  from intent/template learning, including voice aliases and negative alias
  evidence.
- [ ] `[must]` Treat `descriptor_fix` and `development_task` as first-class
  strategies when the system action or skill capability exists but is not
  sufficiently described for NLU/MCP.
- [ ] `[should]` Collect per-engine statistics before adding calibration
  logic: accept/abstain/reject, confidence bands, fallback chain, STT
  confidence, clarification rate, correction rate, and false-accept samples.
- [ ] `[deferred]` Apply Neural/Rasa model promotion automatically; model
  rebuild/reindex remains behind explicit quality gates.

### E. Validation, Policy, and Evaluation

- [ ] `[must]` Make action preview a required gate for interface, skill, and
  endpoint action candidates before apply or dispatch.
- [ ] `[must]` Define side-effect classes and approval policy:
  `read_only`, `ui_navigation`, `local_state_change`,
  `durable_configuration_change`, `external_io`, `device_control`,
  `destructive`, and `unsupported`.
- [ ] `[must]` Add conflict checks: duplicate templates, overbroad regex,
  owner conflicts, entity-alias ambiguity, and action mismatch.
- [ ] `[must]` Add lightweight security abuse checks that reduce high-risk
  failures early: prompt-injection markers in user utterances and descriptors,
  overbroad templates for non-read-only actions, alias collisions with system
  commands, unexpected MCP target scope, and untrusted skill-authored hints.
- [ ] `[must]` Verify both replayed understanding and action outcome when an
  action is dispatched: modal opened, scenario switched, skill result emitted,
  endpoint command acknowledged, or failure recorded.
- [ ] `[must]` Add rate limits, duplicate suppression, and queue/backpressure
  policy for Root/OpenAI Teacher calls by webspace, route, request class, and
  repeated phrase hash.
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
- [ ] `[must]` Add promotion states: `local_learned`,
  `promotion_candidate`, `promoted_to_workspace`, `pushed_to_repo`,
  `published`, and `rejected_for_publication`.
- [ ] `[must]` Add template portability class: `session-local`,
  `user-local`, `workspace-local`, `scenario-local`, `skill-global`,
  `system-global`, or `public-reusable`.
- [ ] `[must]` Attach provenance to every accepted artifact: request id,
  thread id, prompt/context hashes, model id/version, owner, operator/user
  feedback, verification result, rollback pointer, and commit/push id when
  promoted.
- [ ] `[should]` Provide operator controls to promote high-value local
  examples into skill/scenario repositories after regression checks.
- [ ] `[must]` Keep private/local aliases and user-specific names out of
  public artifacts unless explicitly approved.
- [ ] `[deferred]` Publish a shared public NLU template registry across
  independent AdaOS installations.
- [ ] `[deferred]` Implement anonymization/redaction of named entities before
  public promotion. The target model must reserve the hook now; actual
  anonymizer quality gates come later.

### G. Operator UI and Human Verification

- [ ] `[must]` Add Teacher UI Check phrase with trace, ranking, entities,
  canonical slots, selected action, and action preview.
- [ ] `[must]` Add Teacher UI views for candidates, clarification sessions,
  lifecycle state, Apply, Rollback, Reject, and "not that" feedback.
- [ ] `[must]` Add UI evidence that distinguishes NLU gap, provider outage,
  descriptor gap, unsupported action, ambiguous entity, and missing
  capability.
- [ ] `[should]` Add template inventory, patch preview, promotion controls,
  and regression impact preview.
- [ ] `[could]` Add compact Voice affordances for listening from every
  scenario while preserving chat history and avoiding stale message playback.
- [ ] `[deferred]` Build full operator analytics dashboards; start with focused
  trace and lifecycle evidence.

### H. Privacy, Security, and Retention

- [ ] `[must]` Define retention policy for raw utterances, STT text,
  normalized text, LLM prompt context, traces, candidates, and feedback.
- [ ] `[must]` Define promotion privacy gates: local entity names, device
  names, user aliases, and personal examples stay private unless explicitly
  approved.
- [ ] `[must]` Ensure MCP bearer/session scope is recorded as audit evidence
  but never embedded into prompts, templates, examples, or published artifacts.
- [ ] `[must]` Add minimal threat-model checklist for NLU Teacher and skill
  hints: prompt injection, malicious descriptors, alias hijacking, overbroad
  destructive templates, and cross-subnet MCP scope confusion.
- [ ] `[should]` Add delete/export hooks for Teacher traces and learned local
  overlays by request/thread/webspace.
- [ ] `[deferred]` Implement robust anonymization of named entities for public
  template promotion.

### I. Cost, Quota, and Offline Operation

- [ ] `[must]` Add Teacher LLM budget controls: per-webspace rate limit,
  repeated-miss dedupe, max retries, queue depth, and fallback behavior when
  Root/OpenAI is unavailable.
- [ ] `[must]` Keep NLU fast path fully operational when Root/OpenAI is down;
  store misses for later batch enrichment instead of blocking Voice/chat.
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
  candidates for the future LLM programmer workflow, with requested behavior,
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

- [ ] Define the teacher request/thread model:
  `request_id`, `thread_id`, previous request link, current correction target,
  user phrase, route context, and source channel.
- [ ] Define candidate records:
  `candidate_id`, class, planned intent/action, target owner, proposed
  template/patch, verification status, dispatch status, feedback status, audit
  ids, and rollback pointer.
- [ ] Define supported candidate classes:
  - `skill_action`
  - `interface_action`
  - `endpoint_command`
  - `scenario_flow`
  - `entity_correction`
  - `nlu_correction`
  - `development_task`
  - `non_actionable`
- [ ] Define candidate lifecycle states:
  `proposed`, `previewed`, `intent_matched`, `dispatch_previewed`,
  `dispatched`, `accepted`, `corrected`, `rejected`, `quarantined`,
  `applied`, and `rolled_back`.
- [ ] Define event names and idempotency keys for proposal, preview, apply,
  dispatch, feedback, rollback, and duplicate suppression.
- [ ] Define response policy for voice/chat/UI:
  when to dispatch, ask a clarification, save feedback, create a development
  task, or avoid mutation.
- [ ] Define MCP capability profiles:
  read-only context, probe/preview, authoring proposal, durable apply,
  dispatch preview, and operator-approved dispatch.
- [x] Define and implement the first LLM prompt data policy: redact stored
  prompt logs, avoid embedding bearer tokens, bound context snapshots, and
  record request/context/prompt hashes for audit.
- [ ] Add RU/EN Unicode fixtures for Teacher probes, correction threads, and
  template patch previews.

## Phase 1: Baseline Runtime

- [x] Regex-first pipeline with dynamic scenario/skill regex rules.
- [x] Optional neural delegation event (`nlp.intent.detect.neural`) behind
  `ADAOS_NLU_NEURAL` or installed `neural_nlu_service_skill` auto-detection.
- [x] Rasa NLU service-skill isolated from the hub Python environment.
- [x] Rasa service-skill prepared in A/B skill runtime slots.
- [x] Confidence/fallback path to `nlp.intent.not_obtained`.
- [x] Baseline desktop intents for opening modals and node-scoped modals.
- [x] Remove Neural NLU runtime-provider delivery through `src/adaos/interpreter_data`.
- [x] Ensure Neural NLU parse bridge only discovers/starts installed service skills and does
  not mutate workspace skills or A/B slots on demand.

## Phase 2: Operator Feedback Loop

- [x] NLU Teacher stores not-obtained requests per webspace.
- [x] Teacher can apply regex candidates into scenario/skill-owned artifacts.
- [x] Teacher candidate Apply is available through API:
  `POST /api/nlu/teacher/{webspace_id}/candidate/apply`.
- [x] Applied regex candidates are immediately checked against the original
  phrase and marked `intent_matched` only when the runtime probe returns the
  LLM-planned intent.
- [x] Successful candidate verification emits
  `nlp.teacher.understanding.acquired` and records Teacher audit events.
- [x] Regex candidate rollback is available through
  `POST /api/nlu/teacher/{webspace_id}/candidate/rollback` and removes the
  applied rule from owner artifact plus runtime cache.
- [x] Teacher can apply dataset revisions into scenario training content.
- [x] Dry-run phrase probe API for Teacher UI:
  - `POST /api/nlu/teacher/{webspace_id}/probe`
  - regex-first, optional Rasa fallback
  - returns `intent_ranking`, `entities`, `slots`, `stages`
  - does not dispatch actions
- [x] Human verification checklist separates current API/CLI checks from target UI behavior.
- [ ] UI field for "check phrase" wired to the probe endpoint.
- [ ] `[deferred]` UI buttons: "correct", "fix", "save example".
- [x] Operator-approved positive feedback stored with audit metadata.
- [x] Route accepted feedback to the owning NLU training artifact:
  skill, scenario, or system action feedback overlay.
- [ ] `[deferred]` Route named-entity corrections to the governed named-entity source.
- [ ] `[deferred]` Add explicit correction targets for core/client actions that are not
  implemented as skills.

## Phase 3: Observability

- [x] `data.nlu_trace.items[]` stores request/detected/not-obtained events.
- [x] Stage trace event `nlu.trace.stage` records:
  - `request`
  - `regex`
  - `pipeline delegate`
  - `rasa`
  - `dispatcher action/reject`
- [ ] `[could]` Trace UI should show `voice text -> regex/neural/rasa -> intent -> action`.
- [x] Add machine-readable Neural NLU readiness check for artifacts, service
  discovery, live health, model load, and index backend.
- [ ] `[could]` Add latency per stage and service timing.
- [ ] `[deferred]` Add golden phrase regression reports.
- [x] Add neural usage statistics: request count, latency, confidence
  histogram, accept/abstain/reject counts, fallback ratio, and per-intent
  status evidence.
- [x] Add named-entity canonicalization statistics: hit/miss/ambiguity counts
  and unresolved spans.
- [x] Voice chat desktop widget can show a non-dispatching Neural NLU probe
  result (`intent`, `via`, confidence, and slots) in node-scoped chat history
  when `ADAOS_VOICE_CHAT_INTENT_DEMO=1`.

## Cross-Lane Human Verification Gates

- [x] Current implemented behavior has a manual checklist: [nlu-human-verification.md](./nlu-human-verification.md).
- [x] Documentation marks which NLU Teacher behaviors are current UI, backend/API only, or target architecture.
- [x] NLU Teacher UI has a Signals tab backed by `data.nlu_teacher.workbench_signals` for queue, quarantine, LLM error, skip, and acquired-understanding monitoring.
- [x] NLU Teacher Signals accordion opens details inline without also opening
  an implicit modal.
- [x] NLU Teacher Candidate Apply has one primary action that uses the
  backend-resolved owner target; the obsolete duplicate "Apply to scenario"
  shortcut is removed from the current UI.
- [ ] NLU Teacher UI can run a phrase probe without terminal access.
- [ ] `[could]` NLU Teacher UI shows stage trace, ranking, entities, slots, lookup matches, confidence, and action preview.
- [ ] `[deferred]` NLU Teacher UI supports Correct/Fix/Save example with target selection and audit metadata for the currently safe existing-API flows.
- [x] Backend/API/MCP preview flow exposes stable template fingerprints and stale-write checks for template corrections.
- [ ] `[could]` Operator-facing evidence distinguishes NLU gap, service/provider outage, low confidence, unsupported action, and missing capability.

## Phase 4a: Dynamic Lookups and Template Inventory

- [x] Export baseline desktop lookup tables from workspace/packaged desktop manifests:
  - `modal_id`
  - `node_ref`
  - `app_id`
  - `scenario_id`
  - `webspace_id`
- [x] Feed lookup tables into Rasa training data.
- [x] Expose lookup tables for Teacher/LLM inspection:
  - `GET /api/nlu/teacher/{webspace_id}/lookups`
- [x] Overlay live YJS desktop registry values on top of manifest lookups for Teacher API.
- [x] Canonicalize lookup-backed dynamic regex slots before dispatch for
  `scenario_id`, `modal_id`, `app_id`, `node_ref`, `webspace_id`, and
  `skill_id`, so learned templates can match user-facing labels while actions
  receive stable ids.
- [x] Expose stable template ids/fingerprints for current regex rules, examples, intent routes, and system-action examples through API and MCP inventory.
- [x] Implement preview-time stale-write checks using target/template fingerprints.
- [ ] `[deferred]` Extend the same inventory to Rasa/neural labels and lookup-set patching.
- [x] Define the system action catalog for currently runtime-backed core/client
  commands such as open, switch, reload, reset, and install toggle. Move,
  hide, and pin remain blocked on runtime host actions.
- [x] Include system action examples in NLU authoring context without treating
  those actions as user skills.

## Phase 4b: Runtime Named Entities and Canonicalization

- [x] Add a named-entity read model over devices, nodes, browsers, webspaces,
  scenarios, skills, apps, and modals.
- [x] Add a deterministic resolver that maps display names, observed names, and
  aliases to canonical refs before model dispatch.
- [x] Add entity masking so model-facing text can use placeholders such as
  `{device}`, `{webspace}`, and `{scenario}`.
- [x] Add ambiguity handling instead of silently choosing between conflicting
  aliases.
- [x] Add Teacher/probe output for resolved entities, unresolved spans,
  canonical refs, and ambiguity evidence.
- [x] Add regression tests proving alias and device-name changes do not require
  Rasa/neural retraining.
- [x] Track the full target design in
  [Named Entities and Canonical Naming](../architecture/named-entities.md).
- [x] Feed canonicalized text and entity evidence into the neural provider
  contract.
- [ ] `[could]` Ensure Rasa and neural training fingerprints exclude runtime aliases by
  default.

## Phase 5: Teacher Authoring and MCP

### Ground Rule

- [x] LLM cannot call SDK functions, publish events, invoke skill tools, or mutate UI state directly in the current Teacher loop.
- [x] LLM can only propose AdaOS-owned candidates and patches; AdaOS validates, traces, previews, applies, and dispatches them.
- [x] Every implemented teacher step has a trace/audit surface: `nlu.trace`, `data.nlu_teacher.*`, Root MCP audit, or event bus evidence.

### 5a: Existing-API Working Loop

- [x] Use the current Teacher API as the first operational loop before adding new MCP write surfaces:
  - `POST /api/nlu/teacher/{webspace_id}/probe`
  - `GET /api/nlu/teacher/{webspace_id}/lookups`
  - `POST /api/nlu/teacher/{webspace_id}/candidate/apply`
  - `POST /api/nlu/teacher/{webspace_id}/example/save`
- [x] Start with a narrow candidate type: regex/template candidate for an existing AdaOS intent, not a generic action candidate.
- [x] Record planned intent, owner hint, proposed regex template, and verification status.
- [x] Record correction-thread link for follow-up correction phrases.
- [ ] Record dispatch status.
- [x] LLM Teacher enablement inherits `root.llm.allow_nlu_teacher` when env
  overrides are unset; disabled LLM runtime records `llm.skipped` instead of
  silently dropping captured requests.
- [x] LLM Teacher prompt includes governed Root MCP evidence from
  `nlu_authoring.get_context`, `nlu_authoring.check_phrase`,
  `nlu_authoring.get_dialog_context`, `nlu_authoring.list_training_targets`,
  `nlu_authoring.list_templates`, and `sdk.describe_surface`.
- [x] LLM Teacher collects MCP evidence off the API/event loop with a bounded
  timeout so slow Root MCP/tool probes do not block Teacher state/UI reads.
- [x] Teacher events are durably persisted as they are appended, so partial LLM
  traces survive backend restart before a candidate is generated.
- [x] LLM Teacher parses plain JSON and fenced JSON responses, previews regex
  candidates against the source phrase, and quarantines candidates that do not
  compile or do not match the phrase.
- [x] LLM regex candidates normalize common lookup slot aliases
  (`scenario` -> `scenario_id`, `modal` -> `modal_id`, etc.) and repair
  host-action storage targets to the current scenario owner.
- [x] Applying a Teacher regex rule enables `regex_enabled` for the webspace if
  the runtime regex stage was disabled, so a verified rule is actually used by
  the next normal `nlp.intent.detect.request`.
- [x] Voice-originated regex candidates use a confirmation loop before Apply:
  LLM asks a concrete hypothesis question in Voice, `да` applies, first `нет`
  rejects the candidate and triggers one retry with rejected-candidate context,
  and a second rejection asks for clarification.
- [x] Voice confirmation answers are not routed into normal NLU detection, so
  short replies such as `да` and `нет` do not create extra Teacher misses.
- [x] Voice chat no longer reads the last loaded hub message when the modal is
  opened; only newly arriving hub messages are eligible for auto-speak.
- [x] Teacher read-model API methods that use synchronous YDoc/read-model
  helpers run off the API event loop, so trace/template/target reads do not
  fail inside async FastAPI handlers.
- [ ] Persist `nlu_trace` outside the live scenario document as well as in
  `data.nlu_trace`, because scenario switches can rebuild runtime state and
  clear the short-lived trace timeline after a successful UI action.
- [x] Add repeatable LLM-training smoke examples for:
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
- [x] After a regex/template candidate is trusted-applied, re-run phrase check and mark it verified only if the returned intent
  matches the LLM-planned intent.
- [ ] Dispatch verified candidates only through the normal AdaOS intent/action path and only when the candidate's action side-effect class is
  allowed for auto-dispatch.
- [x] Link user corrections such as "no, that is not it" to the previous request/candidate for the next teacher cycle.
- [x] Distinguish true NLU gaps from service-down or provider-disabled states before asking the LLM to create templates.
- [x] Add smoke tests for candidate apply -> regex persist -> probe match -> `understanding.acquired`.
- [x] Add smoke tests for miss -> LLM candidate proposal, false candidate
  quarantine, duplicate candidate suppression, and correction-thread
  continuation.

### 5b: Minimal Read-Only MCP Plane

- [ ] MCP Server modal issues scoped NLU authoring token.
- [ ] Root resolves token to subnet/zone/capabilities.
- [x] Add Root MCP `nlu_authoring.get_context` for named-entity and authoring-boundary evidence.
- [x] Add Root MCP `nlu_authoring.check_phrase` backed by the current probe service.
- [x] Root MCP passes bearer/session subnet scope into NLU authoring handlers
  and returns `root_scope` / `target_id` so the LLM sees which subnet target the
  context belongs to.
- [x] Add Codex bridge tool `check_nlu_phrase`.
- [x] Add/read current MCP surfaces:
  - `nlu_authoring.get_trace`
  - `nlu_authoring.get_dialog_context`
  - `nlu_authoring.get_recent_failures`
  - `desktop.registry.lookup`
  - `skill.describe_nlu`
  - `scenario.describe_nlu`
  - `sdk.describe_surface` (descriptors only, no execution)
- [ ] `[deferred]` Add `nlu.describe_pipeline` and `skill.describe_tools`.
- [x] Keep MCP read-only for context/inventory; preview APIs return dry-run gates without mutation or dispatch.
- [ ] `[could]` Add stricter request timeouts, result-size limits, and audit event summaries for every context-reading call.

### 5c: Action and Ownership Plane

- [ ] Classify teacher decisions as:
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
- [x] Add `desktop.preview_action` to show the host event/action without dispatch for the current system-action catalog.
- [ ] `[deferred]` Add `endpoint.preview_command` to show the resolved endpoint
  role, concrete endpoint id, service, policy gate, expected transport, and
  side-effect class without dispatch.
- [ ] `[deferred]` Add `nlu.resolve_owner` to map intent/action candidates to skill, scenario, system action, endpoint assignment/router, entity alias, or development task ownership.
- [ ] `[deferred]` Define action side-effect classes:
  read-only, UI navigation, reversible UI mutation, durable configuration
  mutation, external side effect, and unsupported.
- [ ] `[deferred]` Define owner conflict policy when a scenario route, skill NLU metadata, and system action catalog all match the same phrase.

### 5d: Template Inventory and Safe Apply

- [x] Root MCP/API surfaces:
  - `nlu_authoring.list_templates`
  - `nlu.get_template`
  - `nlu_authoring.list_training_targets`
  - `nlu_authoring.preview_template_patch`
  - `nlu.apply_template_patch`
- [ ] `[deferred]` Add `nlu.get_template` and `nlu.apply_template_patch` as first-class MCP calls; current durable apply still uses candidate/example APIs.
- [x] LLM receives current template inventory before proposing changes.
- [x] Template preview uses stable template ids/fingerprints and `base_fingerprint` stale-write checks.
- [x] Template patches can be previewed before durable apply; operator approval still uses existing candidate/example APIs.
- [ ] `[deferred]` Durable apply writes only through owner services/APIs: skill, scenario, system-action feedback, or named-entity alias source.
- [ ] `[deferred]` Add rollback pointers and audit records for every applied patch.
- [ ] `[deferred]` Add duplicate-template detection, regex blast-radius checks, and golden-phrase impact preview before durable apply.
- [ ] `[deferred]` Decide migration policy for legacy `data.nlu.regex_rules[]` mirrors versus owner-authored scenario/skill artifacts.

### 5e: Development Task Candidates

- [ ] `[deferred]` Represent missing capabilities as development tasks, not fake intents.
- [ ] `[deferred]` Task candidate shape includes requested behavior, likely owner, missing action/tool surface, suggested skill/scenario change, and evidence.
- [ ] `[deferred]` Route task candidates to the LLM programmer workflow for existing skill/scenario modification or new skill/scenario creation.
- [ ] `[deferred]` After the skill/scenario is changed, re-run the original phrase and link the result back to the task candidate.

### 5f: Teacher Acceptance Gates

- [ ] `[could]` Every phase has at least one test or smoke command that can be run without the UI.
- [ ] Every accepted candidate stores trace, prompt/context hash, verification result, owner, and operator/trust policy evidence.
- [ ] `[deferred]` False positives can be rejected, quarantined, or rolled back without deleting unrelated user-authored training data.
- [ ] `[could]` RU and EN phrases pass through the same correction-thread and template-preview paths without mojibake or lossy normalization.
- [ ] `[could]` The UI can explain whether the result came from regex, Rasa, neural, lookup canonicalization, Teacher candidate, or provider fallback.

## Phase 6: Neural NLU Provider

### Provider Boundary

- [x] Move `neural_nlu_service_skill` out of `src/adaos/interpreter_data` into
  normal registry/workspace skill delivery.
- [x] Add separate experimental `neuro_nlu_lite_skill` delivery for weak-device
  validation without changing the production Neural NLU provider.
- [x] Add opt-in `adaos install --neural-nlu` preparation for Neural NLU.
- [x] Keep plain `adaos install` free of Neural NLU heavy dependencies.
- [x] Make the neural bridge discover/start only installed service skills.
- [x] Remove hot-path workspace mutation/bootstrap from neural parse handling.
- [x] Keep provider dependencies (`torch`, `faiss-cpu`, etc.) out of the hub
  root venv.
- [x] Keep Neuro Lite free of Torch/FAISS/Rasa dependencies for the first
  prototype baseline.

### Neuro Lite Experimental Stage

- [x] Add `nlp.intent.detect.neuro_lite` bridge and stage trace events.
- [x] Add runtime policy/flag support for `neuro_lite_enabled` and
  `ADAOS_NLU_NEURO_LITE`.
- [x] Add `neuro_nlu_lite_skill` with `/health`, `/parse`, and `/rebuild`.
- [x] Implement the first hash n-gram prototype baseline with accept/abstain
  behavior.
- [x] Fall through to Neural/Rasa when Neuro Lite abstains or is disabled.
- [ ] `[deferred]` Add real hard-negative evaluation before considering attention or a tiny
  encoder.
- [ ] `[deferred]` Decide whether Neuro Lite should remain a separate provider, become a
  low-resource Neural profile, or be retired after evaluation.

### Inference Contract

- [x] Freeze `/parse` request/response schema with `top_intent`,
  `confidence`, `alternatives`, `slots`, `model_id`, and `evidence`.
- [x] Pass named-entity canonicalization evidence into `/parse`.
- [x] Return matched examples, score components, and canonicalized text in
  `evidence`.
- [x] Add confidence gates for accept/abstain/reject.
- [x] Add neural abstain/error fallback to Rasa.
- [ ] Route Rasa miss/low confidence to NLU Teacher.

### Notebook Approach Port

- [x] Port masking logic into provider-owned runtime code.
- [x] Port Char-CNN + BiLSTM model loader.
- [x] Fix and test special-token compatibility between training and runtime.
- [x] Port supervised-contrastive embedding projection usage.
- [x] Persist a lazy Torch tensor positive-example k-NN cache as an
  intermediate step before FAISS indexes.
- [x] Add optional lazy FAISS positive example index with Torch tensor fallback.
- [x] Add FAISS negative example indexes.
- [x] Add weighted ranker over softmax, k-NN similarity, and action/skill
  priors.
- [x] Add intent/action id mapping from research labels to AdaOS canonical
  intents and system actions.

### Artifacts and ModelOps

- [x] Define node-level active model layout owned by the service skill runtime.
- [x] Add a notebook-output preparation script that writes `model.pt`,
  `labels.json`, `vocab.json`, example/intent manifests, ranker config, and
  provenance metrics into the active node-level layout.
- [x] Store `model.pt`, `labels.json`/`intents_manifest.json`, `vocab.json`,
  optional `faiss.index`/`faiss.index.json`,
  `negative_faiss.index`/`negative_faiss.index.json`,
  `examples_manifest.jsonl`, `intent_map.json`, `ranker_config.json`, and
  `metrics.json` in the service-owned active layout.
- [x] Add immutable `model_id` and model provenance metadata for prepared
  notebook artifacts.
- [x] Add rollback pointer for the node-level active model.
- [x] Add golden phrase regression report before model promotion.
- [ ] `[could]` Add full quality gates using macro-F1, abstain rate, and latency.
- [ ] `[deferred]` Per-locale/webspace/profile models until usage statistics justify
  the added operational complexity.

### Usage Statistics

- [x] Record neural request count and latency per stage.
- [x] Record confidence distributions and threshold bands.
- [x] Record accept/abstain/reject counts per intent.
- [x] Record fallback ratio `neural -> Rasa`.
- [x] Record canonicalization hit/miss/ambiguity/unresolved counts for neural
  requests.
- [x] Record abstained/rejected samples for Teacher review and retraining.
- [x] Add bridge-level `neural-probe` check using the runtime confidence gates
  and usage-stat path.
- [x] Link final Rasa accept/miss outcomes back to the neural fallback sample
  so `neural -> Rasa -> Teacher` can be measured end to end.
- [x] Add operator diagnostics that combine Neural readiness and usage
  aggregates.
- [x] Add experimental Neuro Lite runtime stage and weak-device service-skill
  baseline, separate from the production Neural NLU service.

### Training Data Feedback

- [x] Export skill-owned examples from skills.
- [x] Export scenario-owned examples from scenarios.
- [x] Export core/client command examples from the system action catalog.
- [ ] `[could]` Export named-entity classes as masks, not as local alias training data.
- [x] Let Teacher-approved corrections update regex, Neural, and Rasa datasets
  through the owning artifact.
- [x] Add governed Neural reindex planning/apply flow for curated examples
  that are compatible with the active model labels.
- [x] Rebuild/retrain the neural provider for curated examples that introduce
  new model labels.

## Immediate Next Steps

1. Add safe dispatch preview/dispatch gates for verified candidates that are
   allowed to run through the normal AdaOS intent/action path.
2. Expand read-only MCP wrappers for trace, dialog context, recent failures,
   lookups, skill/scenario NLU descriptors, and SDK descriptors.
3. Wire the Teacher UI Check phrase flow to show canonicalization, neural,
   Rasa, provider health, and action-preview evidence.
4. Add full model promotion gates using macro-F1, abstain rate, latency,
   false-positive checks, and rollback evidence.

## Last Completed Slice

- LLM-generated regex candidates now include structured
  `action_candidate`, `template_candidate`, and `training_strategy` envelopes.
  This starts the M1 action-candidate contract without breaking the current
  legacy regex Apply/rollback behavior.
- Voice-originated confirmation prompts now mirror into
  `data.nlu_teacher.clarification_sessions[]`, giving the future dialog layer
  a structured session record while preserving the existing
  `pending_confirmations[]` flow.
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
- NLU Teacher UI now has a single Candidate Apply action using the backend
  owner target, and Signals accordion expansion no longer opens a second modal.
- LLM Teacher now stores request/context/prompt hashes on LLM logs and
  candidates, suppresses duplicate active regex candidates, and passes
  correction-thread context into the next LLM prompt when the user says
  "no/not that/нет/не то/...".
- Teacher bridge now classifies `nlp.intent.not_obtained` reasons and skips
  Root/OpenAI for provider/stage unavailable cases such as `rasa_timeout`,
  while still treating low-confidence/no-intent outcomes as teachable NLU gaps.
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
