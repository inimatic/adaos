# NLU Roadmap Checklist

Current runtime implementation estimate: **89%** for the practical AdaOS NLU
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

Markdown checkboxes only distinguish done from not done. This roadmap keeps
plain unchecked items for active planned work and uses explicit labels for
work that should not block the next NLU Teacher slice:

- `[deferred]`: intentionally postponed until the contract, working loop, or
  owning surface is stable.
- `[polish]`: useful hardening or operator experience work, but not required
  for the first functional vertical slice.

An unchecked `[deferred]` or `[polish]` item must not be counted as a blocker
for the current NLU Teacher implementation gate.

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
- [ ] Define LLM prompt data policy: redact secrets/tokens, bound dialog
  history, avoid embedding bearer tokens, and record which trace/context was
  sent to the LLM.
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
- [ ] `[polish]` Trace UI should show `voice text -> regex/neural/rasa -> intent -> action`.
- [x] Add machine-readable Neural NLU readiness check for artifacts, service
  discovery, live health, model load, and index backend.
- [ ] `[polish]` Add latency per stage and service timing.
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
- [ ] NLU Teacher UI can run a phrase probe without terminal access.
- [ ] `[polish]` NLU Teacher UI shows stage trace, ranking, entities, slots, lookup matches, confidence, and action preview.
- [ ] `[deferred]` NLU Teacher UI supports Correct/Fix/Save example with target selection and audit metadata for the currently safe existing-API flows.
- [ ] `[deferred]` Template correction flow uses stable ids and stale-write fingerprints.
- [ ] `[polish]` Operator-facing evidence distinguishes NLU gap, service/provider outage, low confidence, unsupported action, and missing capability.

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
- [ ] Expose stable template ids for regex, Rasa examples, neural labels, and lookup sets.
- [ ] Implement stale-write protection using template fingerprints.
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
- [ ] `[polish]` Ensure Rasa and neural training fingerprints exclude runtime aliases by
  default.

## Phase 5: Teacher Authoring and MCP

### Ground Rule

- [ ] LLM cannot call SDK functions, publish events, invoke skill tools, or mutate UI state directly.
- [ ] LLM can only propose AdaOS-owned candidates and patches; AdaOS validates, traces, previews, applies, and dispatches them.
- [ ] Every teacher step has a trace/audit surface: `nlu.trace`, `data.nlu_teacher.*`, Root MCP audit, or event bus evidence.

### 5a: Existing-API Working Loop

- [x] Use the current Teacher API as the first operational loop before adding new MCP write surfaces:
  - `POST /api/nlu/teacher/{webspace_id}/probe`
  - `GET /api/nlu/teacher/{webspace_id}/lookups`
  - `POST /api/nlu/teacher/{webspace_id}/candidate/apply`
  - `POST /api/nlu/teacher/{webspace_id}/example/save`
- [x] Start with a narrow candidate type: regex/template candidate for an existing AdaOS intent, not a generic action candidate.
- [x] Record planned intent, owner hint, proposed regex template, and verification status.
- [ ] Record dispatch status and correction-thread link.
- [x] LLM Teacher prompt includes governed Root MCP evidence from
  `nlu_authoring.get_context` and `nlu_authoring.check_phrase`.
- [x] Add repeatable LLM-training smoke examples for:
  - `skill_action`: LLM proposes a regex for an existing skill-routed intent,
    Apply stores it in `skill.yaml`, replay matches, rollback restores miss.
  - `interface_action`: LLM proposes a regex for a scenario-owned host action,
    Apply stores it in `scenario.json`, replay matches, rollback restores miss.
  - `endpoint_command`: LLM proposes a regex for an existing endpoint-routed
    action such as showing text on an assigned display endpoint; Apply stores
    it in the owning skill/scenario artifact, replay matches, and dispatch
    preview resolves the endpoint role before any command is sent.
- [x] After a regex/template candidate is trusted-applied, re-run phrase check and mark it verified only if the returned intent
  matches the LLM-planned intent.
- [ ] Dispatch verified candidates only through the normal AdaOS intent/action path and only when the candidate's action side-effect class is
  allowed for auto-dispatch.
- [ ] Link user corrections such as "no, that is not it" to the previous request/candidate for the next teacher cycle.
- [ ] Distinguish true NLU gaps from service-down or provider-disabled states before asking the LLM to create templates.
- [x] Add smoke tests for candidate apply -> regex persist -> probe match -> `understanding.acquired`.
- [ ] Add smoke tests for miss -> LLM candidate proposal, false candidate quarantine, duplicate candidate suppression, and correction-thread
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
- [ ] Add/read remaining MCP surfaces:
  - `[deferred]` `nlu.describe_pipeline`
  - `nlu.get_trace`
  - `nlu.get_dialog_context`
  - `nlu.get_recent_failures`
  - `desktop.registry.lookup`
  - `skill.describe_tools`
  - `skill.describe_nlu`
  - `scenario.describe_nlu`
  - `sdk.describe_surface` (descriptors only, no execution)
- [ ] Add request timeouts, result-size limits, and audit events for `nlu_authoring.check_phrase` and context-reading calls.
- [ ] Keep MCP read-only until API-level preview, audit, and stale-write checks are stable.

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
- [ ] `[deferred]` Add `desktop.preview_action` to show the host event/action without dispatch.
- [ ] `[deferred]` Add `endpoint.preview_command` to show the resolved endpoint
  role, concrete endpoint id, service, policy gate, expected transport, and
  side-effect class without dispatch.
- [ ] `[deferred]` Add `nlu.resolve_owner` to map intent/action candidates to skill, scenario, system action, endpoint assignment/router, entity alias, or development task ownership.
- [ ] `[deferred]` Define action side-effect classes:
  read-only, UI navigation, reversible UI mutation, durable configuration
  mutation, external side effect, and unsupported.
- [ ] `[deferred]` Define owner conflict policy when a scenario route, skill NLU metadata, and system action catalog all match the same phrase.

### 5d: Template Inventory and Safe Apply

- [ ] `[deferred]` Root MCP surfaces:
  - `nlu.list_templates`
  - `nlu.get_template`
  - `nlu.list_training_targets`
  - `nlu.preview_template_patch`
  - `nlu.apply_template_patch`
- [ ] `[deferred]` LLM receives current template inventory before proposing changes.
- [ ] `[deferred]` Template patches use stable `template_id` values and `base_fingerprint` stale-write protection.
- [ ] `[deferred]` Template patches are previewed and operator-approved before durable apply, except for explicit per-owner trusted-autoapply policies.
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

- [ ] `[polish]` Every phase has at least one test or smoke command that can be run without the UI.
- [ ] Every accepted candidate stores trace, prompt/context hash, verification result, owner, and operator/trust policy evidence.
- [ ] `[deferred]` False positives can be rejected, quarantined, or rolled back without deleting unrelated user-authored training data.
- [ ] `[polish]` RU and EN phrases pass through the same correction-thread and template-preview paths without mojibake or lossy normalization.
- [ ] `[polish]` The UI can explain whether the result came from regex, Rasa, neural, lookup canonicalization, Teacher candidate, or provider fallback.

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
- [ ] `[polish]` Add full quality gates using macro-F1, abstain rate, and latency.
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
- [ ] `[polish]` Export named-entity classes as masks, not as local alias training data.
- [x] Let Teacher-approved corrections update regex, Neural, and Rasa datasets
  through the owning artifact.
- [x] Add governed Neural reindex planning/apply flow for curated examples
  that are compatible with the active model labels.
- [x] Rebuild/retrain the neural provider for curated examples that introduce
  new model labels.

## Immediate Next Steps

1. Run a live Root/OpenAI smoke with real credentials and capture the exact
   prompt/context hash, LLM response, candidate, apply, and verification trace.
2. Add correction-thread state for user feedback such as "no, that is not it"
   and feed that state into the next Teacher analysis cycle.
3. Add safe dispatch preview/dispatch gates for verified candidates that are
   allowed to run through the normal AdaOS intent/action path.
4. Expand read-only MCP wrappers for trace, dialog context, recent failures,
   lookups, skill/scenario NLU descriptors, and SDK descriptors.
5. Wire the Teacher UI Check phrase flow to show canonicalization, neural,
   Rasa, provider health, and action-preview evidence.
6. Add full model promotion gates using macro-F1, abstain rate, latency,
   false-positive checks, and rollback evidence.

## Last Completed Slice

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
- A closed-loop test now covers: regex miss -> LLM regex candidate -> Apply ->
  `understanding.acquired` -> repeated phrase resolves through `regex.dynamic`.
- Added repeatable test examples for `skill_action` and `interface_action`
  training with rollback to the original miss state.
- Rasa is packaged as an optional default-on service-skill and installed into skill runtime slots.
- NLU Teacher has a dry-run phrase probe API with regex-first and optional Rasa fallback.
- NLU Teacher exposes baseline desktop lookup tables for `modal_id`, `node_ref`, `app_id`, `scenario_id`, and `webspace_id`.
- Teacher lookup API overlays live YJS values from `ui.application.modals`, `registry.merged.modals`, `data.catalog.apps`,
  `data.installed.apps`, `data.nodes`, and `ui.current_scenario`.
- Rasa export writes native lookup tables and `data/lookup_tables.json`; lookup summary is included in the training fingerprint.
- Runtime emits stage trace events for regex, pipeline delegation, Rasa, and dispatcher actions/rejects.
- Trace items are persisted to `data.nlu_trace.items[]` for the future UI timeline.
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
