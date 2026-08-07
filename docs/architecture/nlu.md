# NLU in AdaOS

Status: current runtime direction with explicitly labeled future work.

Last reviewed: 2026-08-07.

This document describes the current production MVP direction for intent detection in AdaOS.

For the controlled-evolution sequence of NLU Teacher, voice capabilities,
Builder handoff, promotion, and regression gates, see
[NLU Teacher Evolution Roadmap](./nlu-evolution-roadmap.md). The detailed
technical checklist remains in [NLU Roadmap Checklist](./nlu-roadmap.md).

## MVP baseline

- Pipeline: `regex` -> `neuro_lite (experimental weak-device service-skill, optional)` -> `neural (service-skill, optional when installed)` -> `rasa (service-skill, long-term fallback)` -> `teacher (LLM in the loop)`
- System boundary: NLU runtime code is one; only **data** varies per scenario/skill.
- Transport: intent detection is integrated into AdaOS event bus (not CLI-only).

The target install policy is:

- Neural NLU is opt-in during setup: `adaos install --neural-nlu` prepares the
  service skill, while plain `adaos install` leaves it absent.
- Runtime dispatch uses Neural automatically only when
  `neural_nlu_service_skill` is installed/active. `ADAOS_NLU_NEURAL=1` forces
  the stage and `ADAOS_NLU_NEURAL=0` disables it.
- Neuro Lite is experimental and separate from the production Neural NLU
  provider. It is controlled by `ADAOS_NLU_NEURO_LITE` and the runtime
  `neuro_lite_enabled` flag, and is intended for weak-device validation before
  changing the production neural path.
- Rasa remains a long-term fallback, not only a temporary migration bridge.
- The hot request path must only discover/start installed service skills. It
  must not create workspace skills or A/B runtime slots on demand.

## Ownership boundaries

Core AdaOS owns orchestration, contracts, confidence policy, traces, named
entity canonicalization, and the fallback/governance loop. Core AdaOS must not
bundle concrete NLU engines, Torch/FAISS dependencies, model weights, or service
skill source trees under the Python package.

The NLU Teacher LLM is also outside the execution boundary. It may propose
intent/action candidates, template patches, entity corrections, or development
tasks, but it must not call SDK functions, publish events, invoke tools, or
mutate UI state directly. AdaOS validates those proposals, records trace/audit
evidence, and dispatches only through normal intent/action surfaces.

Concrete NLU engines are providers:

- `neural_nlu_service_skill` is a registry/workspace service skill sourced from
  `.adaos/workspace/skills/neural_nlu_service_skill`.
- `neuro_nlu_lite_skill` is an experimental registry/workspace service skill
  sourced from `.adaos/workspace/skills/neuro_nlu_lite_skill`.
- `rasa_nlu_service_skill` is a registry/workspace service skill.
- Model artifacts and indexes are service-owned runtime data, not core package
  data.
- Provider installation and A/B slot activation belong to install/update
  flows, not to `nlp.intent.detect.*` handling.

The historical `src/adaos/interpreter_data` package is an early experiment and
should be retired as a provider delivery mechanism.

## Current implementation status

Implemented now:

- Regex-first event pipeline with optional Rasa service-skill fallback.
- Optional neural delegation event behind `ADAOS_NLU_NEURAL` or installed
  `neural_nlu_service_skill` auto-detection.
- Optional experimental Neuro Lite delegation stage behind
  `ADAOS_NLU_NEURO_LITE` / `neuro_lite_enabled`, using
  `neuro_nlu_lite_skill` and `nlp.intent.detect.neuro_lite`.
- Neural NLU service-skill install preparation from normal workspace/registry
  source during install flow.
- Neural bridge discovery/start of installed service only; no hot-path
  workspace mutation or A/B slot preparation.
- Neural service-skill venv execution with `torch`, `numpy`, and `faiss-cpu`
  declared as skill dependencies, keeping neural packages out of the hub root
  venv.
- Neural positive-example retrieval now supports an optional lazy `faiss.index`
  with a Torch tensor cache fallback when `faiss` is not installed in the
  service venv.
- Neural negative-example retrieval now persists a parallel FAISS/Torch index
  and records contrastive evidence for close other-intent examples.
- Neural `/parse` contract with `top_intent`, `confidence`, `alternatives`,
  `slots`, `model_id`, `evidence`, canonicalized text, and named-entity
  evidence.
- Neural intent mapping through `intent_map.json`, so research/notebook labels
  can be translated to AdaOS canonical intents and optional action ids while
  preserving the original model label in evidence.
- Neural usage statistics in `state/nlu/neural_usage.json`: request/fallback
  counts, latency summary, confidence bands, accept/abstain/reject counts,
  per-intent status counts, canonicalization buckets, downstream Rasa outcomes
  for neural fallbacks, and review samples.
- Bridge-level Neural NLU probe through the same service discovery,
  canonicalization payload, confidence gates, and usage-stat path as runtime
  dispatch:
  - `adaos interpreter neural-probe "какая погода в москве" --locale ru`
- Voice chat desktop demo path is opt-in with
  `ADAOS_VOICE_CHAT_INTENT_DEMO=1`: `voice.chat.user` still publishes the
  normal `nlp.intent.detect.request`, and the router can also append a
  node-scoped `Intent detector: ... | via=neural | ...` probe message into
  `data/nodes/<node_id>/voice_chat` so the web desktop widget can show whether
  the neural detector is reachable.
- Machine-readable Neural NLU readiness check for artifacts, service
  discovery, optional `/health`, model load, and active index backend:
  - `adaos interpreter neural-readiness --start --stop-after`
- Operator-facing Neural NLU diagnostics that combine readiness with
  node-local usage aggregates:
  - `adaos interpreter neural-diagnostics --start --stop-after`
- Active Neural service reindex:
  - `adaos interpreter neural-reindex --start --stop-after`
  - calls service `POST /reindex` to reload artifacts and rebuild stale
    positive/negative example indexes.
- Notebook artifact preparation script for Neural NLU:
  `.adaos/workspace/skills/neural_nlu_service_skill/scripts/prepare_artifacts.py`.
- Rasa NLU service-skill isolation from the hub Python environment.
- Dry-run probe API for safe phrase checks:
  - `POST /api/nlu/teacher/{webspace_id}/probe`
- Lookup API with live desktop-registry overlay:
  - `GET /api/nlu/teacher/{webspace_id}/lookups`
- Versioned system action catalog at
  `src/adaos/services/nlu/system_actions_catalog.py` for runtime-backed host
  commands. The catalog exposes stable action ids, host event names, slots,
  examples, and dispatcher mappings for default desktop actions such as modal
  open, scenario switch, app install toggle, webspace reload, and webspace
  reset.
- Curated Neural training export:
  - `adaos interpreter export-neural-training`
  - writes skill/scenario/system-owned examples to
    `state/interpreter/neural_training/examples_manifest.jsonl`
  - strips Rasa entity annotations into plain text while preserving
    `raw_example` and owner metadata.
  - `adaos interpreter neural-reindex --from-curated` dry-runs an operator
    compatibility plan, and `--from-curated --apply` replaces active examples
    only when all curated labels already exist in the active Neural model.
- Curated Neural candidate rebuild:
  - `adaos interpreter neural-rebuild --from-curated`
  - trains a candidate service artifact layout under
    `state/interpreter/neural_candidates`
  - `--promote` backs up the active model, writes rollback pointers, clears
    stale indexes, and reloads the Neural service.
- Operator-approved example save backend:
  - `POST /api/nlu/teacher/{webspace_id}/example/save`
  - event: `nlp.teacher.example.save`
  - targets: `skill`, `scenario`, or `system_action`
  - writes a scoped `adaos.nlu.teacher_overlay_store.v1` runtime example with
    provenance/privacy/rollback metadata; skill/scenario targets also create an
    `adaos.nlu.teacher_promotion_candidate.v1` for a future Builder patch to
    `conversational/examples.yaml`.
- Stage trace persistence in `data.nlu_trace.items[]`.
- Schema-driven NLU Teacher modal that shows missed requests, candidates, raw event payloads, and Apply actions.

Not implemented yet:

- Teacher UI Check phrase field wired to the probe API.
- Teacher UI trace/ranking/entities/action preview panel.
- Operator feedback buttons: Correct, Fix, Save example.
- Stable template inventory for regex, Rasa examples, neural labels, and lookup sets.
- Root MCP token/session flow for governed LLM-assisted authoring.

The neural stage is now sourced as a normal service skill. Runtime dispatch is
installed-skill driven by default: unset `ADAOS_NLU_NEURAL` auto-detects an
active `neural_nlu_service_skill`, `ADAOS_NLU_NEURAL=1` forces Neural routing,
and `ADAOS_NLU_NEURAL=0` keeps Rasa as the next stage.

## Event flow (high level)

1. UI / Telegram / Voice publishes:
   - `nlp.intent.detect.request { text, webspace_id, request_id, _meta... }`
   - For the `voice_chat` route, `ADAOS_VOICE_CHAT_INTENT_DEMO=1` additionally
     runs a non-dispatching Neural NLU probe for browser demonstration and
     appends the result to the node-scoped voice chat history. This does not
     replace the normal pipeline or dispatcher path.
2. Named-entity canonicalization resolves runtime names and aliases before
   model-specific interpretation becomes final:
   - device/browser/node/webspace/scenario/skill/app/modal aliases are resolved
     to canonical refs;
   - model-facing text may be masked with placeholders such as `{device}`,
     `{scenario}`, or `{app}`;
   - `resolved_entities`, ambiguities, and unresolved spans are recorded in
     trace.
3. `nlu.pipeline` tries regex rules:
   - scoped runtime Teacher overlays from Yjs `data.nlu.regex_rules` first;
   - git-versioned deterministic `conversational/matchers.yaml` sources from
     installed skills and the active scenario;
   - legacy `scenario.json:nlu.regex_rules` and `skill.yaml:nlu.regex_rules` as
     read-only compatibility baselines;
   - built-in rules (`nlu.pipeline`).
4. If regex does not match:
   - if Neuro Lite is enabled by runtime flag and policy, emits
     `nlp.intent.detect.neuro_lite`
   - if `ADAOS_NLU_NEURAL=1`, or if the variable is unset and
     `neural_nlu_service_skill` is installed/active: emits
     `nlp.intent.detect.neural`
   - otherwise emits `nlp.intent.detect.rasa`
5. Neuro Lite bridge:
   - calls `neuro_nlu_lite_skill:/parse`;
   - uses a weak-device oriented hash n-gram prototype baseline;
   - on high confidence -> emits `nlp.intent.detected { via: "neuro_lite" }`;
   - on abstain/error -> falls through to the next configured provider stage.
6. Neural bridge:
   - calls `neural_nlu_service_skill:/parse`
   - the service skill is installed/prepared by install/update flows, not by
     the hot parse path;
   - passes named-entity `canonicalized_text` and `resolved_entities` evidence
     into the provider request;
   - neural service can run notebook-compatible Char-CNN + BiLSTM weights plus
     lazy FAISS positive/negative example indexes when `faiss` is installed,
     or Torch tensor k-NN fallbacks otherwise;
   - maps model/research labels through the service-owned `intent_map.json`
     before returning canonical `top_intent` values to the bridge;
   - default deployment uses one active model per node, with usage telemetry
     collected so later per-locale/webspace/profile splits can be justified by
     evidence.
   - aggregate neural usage telemetry is persisted under
     `state/nlu/neural_usage.json` for operator diagnostics and retraining
     review.
   - on high confidence -> emits `nlp.intent.detected { via: "neural" }`
   - on abstain/error -> falls back to `nlp.intent.detect.rasa`
7. Rasa bridge:
   - calls the installed `rasa_nlu_service_skill`;
   - remains a supported long-term fallback, especially for ambiguous neural
     outputs and domains where Rasa training data is already stronger;
   - can be disabled on weak devices if neural/regex coverage is sufficient.
8. If an intent is found:
   - `nlp.intent.detected { intent, confidence, slots, text, webspace_id, request_id, via }`
   - this event and direct `intent -> scenario.run` dispatch remain compatibility
     projections. The target authority boundary emits an `IntentProposal` and
     admits its workflow command or skill invocation before any protected effect.
9. If intent is not obtained:
   - `nlp.intent.not_obtained { reason, text, via, webspace_id, request_id }`
   - Router emits a human-friendly `io.out.chat.append` and records the request for NLU Teacher.
10. If teacher is enabled:
   - `nlp.teacher.request { webspace_id, request }` is emitted for teacher runtimes.

## Runtime trace

AdaOS records NLU decisions as a stage trace so the UI can explain why a phrase worked or failed.

- `nlu.trace.stage` is emitted for `request`, `regex`, `pipeline delegate`, `rasa`, and `dispatcher action/reject`.
- Trace items are stored under `data.nlu_trace.items[]`.
- The Teacher dry-run API can emit the same trace without dispatching actions:
  - `POST /api/nlu/teacher/{webspace_id}/probe`
  - request: `{ "text": "...", "use_rasa": true, "emit_trace": true }`
  - response: `intent`, `confidence`, `slots`, `entities`, `intent_ranking`, `stages`

The implementation checklist is tracked in [nlu-roadmap.md](./nlu-roadmap.md).
Human verification steps are tracked in [NLU Human Verification](../guides/nlu-human-verification.md).

## Dynamic lookup tables

AdaOS now exports baseline NLU lookup tables from workspace desktop/scenario manifests, with packaged/default desktop ids as an
empty-workspace fallback. The lookup sets are:

- `modal_id`
- `node_ref`
- `app_id`
- `scenario_id`
- `webspace_id`

The Teacher/LLM inspection endpoint is:

- `GET /api/nlu/teacher/{webspace_id}/lookups`

Rasa project export consumes the same snapshot and writes:

- native Rasa lookup entries into `state/interpreter/rasa_project/data/intents_from_config.yml`
- the full inspected snapshot into `state/interpreter/rasa_project/data/lookup_tables.json`

The Teacher endpoint overlays live read-only YJS registry state on top of manifest-derived values, so runtime desktop objects can be inspected
without waiting for a training export. Rasa training continues to use the stable manifest snapshot for reproducibility.

The lookup summary participates in the Rasa training fingerprint, so changing available manifest desktop ids can mark the NLU model stale.

## Runtime entity canonicalization

Device, browser, webspace, node, skill, and scenario names should not become
permanent model behavior. AdaOS should resolve registered display names,
observed names, and aliases to canonical refs before or alongside intent
detection, then pass the model masked text such as `open weather on {device}`.

Target behavior:

- runtime aliases and observed device names do not require Rasa/neural
  retraining by default;
- NLU trace records `resolved_entities`, original spans, canonical refs, and
  ambiguity decisions;
- Teacher/probe APIs show both static lookup matches and live named-entity
  resolver matches;
- dispatch receives canonical refs such as `device:member:<node_id>` rather
  than display strings.

The target architecture and roadmap are documented in
[Named Entities and Canonical Naming](named-entities.md).

## NLU data ownership

Curated examples should live where the behavior is owned:

- Skill-owned actions: the owning skill stores reusable intents, entities,
  examples, matchers, affordances, repair, and output contracts in its
  git-versioned `conversational/` package.
- Scenario-owned flows: the owning scenario stores the same source categories
  in its package, with affordances bound to admitted scenario workflow/skill
  operations rather than duplicating transitions.
- Core/client actions such as opening, switching, reloading/resetting the
  desktop, and toggling installed apps are described in a versioned **system
  action catalog** with stable action ids, argument schemas, aliases, and
  training examples. Move, hide, and pin must be added only after matching
  runtime host actions exist.

The system action catalog is data, not provider code. Regex, Rasa, neural,
Teacher, and MCP authoring can all consume it. The default desktop NLU merges
active catalog intents into its dispatcher config, while interpreter export
keeps those examples system-owned instead of pretending they came from a user
skill.

The current neural provider uses service-owned `intent_map.json` as the
node-level bridge from research labels to canonical intents and optional
`action_id` values. This keeps model labels stable while the system action
catalog matures into the shared source of truth for built-in commands.

The curated Neural export is not an active model promotion step. It produces a
reviewable/rebuildable bundle under `state/interpreter/neural_training`; the
active provider layout under `state/nlu/neural` is updated only by explicit
artifact preparation or future governed rebuild/reindex tooling.

NLU Teacher writes accepted corrections only to scoped runtime overlays. For a
skill or scenario owner it also records a bounded promotion candidate naming
the target `conversational/examples.yaml` or `conversational/matchers.yaml`;
Builder alone may turn that candidate into a reviewed source patch. System
action feedback and named-entity aliases follow the same runtime-first,
explicit-promotion rule; legacy JSONL and manifest fields are migration inputs,
not current Teacher write targets.

## Rasa as a service-skill

Rasa is treated as a **service-type skill** (separate Python/venv, managed lifecycle) to avoid dependency conflicts with the hub runtime. AdaOS uses the NLU-only `rasa-port` package, not upstream `rasa==3.6.x` in the root venv.

Install behavior:

- `adaos install` prepares `rasa_nlu_service_skill` into an active skill slot and trains once by default.
- `--no-rasa-nlu` disables service-skill preparation.
- `--no-train-nlu` keeps the service-skill ready but skips post-install training.
- `ADAOS_RASA_PORT_PATH` can point to a local `rasa-port` checkout.
- `ADAOS_NLU_RASA=0` disables the Rasa stage at runtime.

Runtime behavior:

- `adaos api serve` does not prepare new Rasa skill slots on demand.
- Rasa parse/train bridges only discover and start an already installed service-skill through `ServiceSkillSupervisor`.
- If Rasa is missing, the bridge falls back with `rasa_base_url_unresolved` instead of mutating slot A/B.
- Creating or switching slot A/B belongs to install/update/supervisor rollout flows, not to the hot NLU parse path.

The hub supervises:

- health checks
- crash frequency
- request failures/timeouts

Issues can trigger:

- `skill.service.issue`
- `skill.service.doctor.request` -> `skill.service.doctor.report` (LLM doctor can be plugged later)

## Teacher-in-the-loop (LLM)

When `regex` and `rasa` do not produce an intent, AdaOS calls an LLM teacher to:

- propose a **dataset revision** (existing intent + new examples + slots), or
- propose a **regex rule** to improve the `regex` stage, or
- propose an **action candidate** that maps a phrase to an existing skill,
  interface action, scenario flow, or system action, or
- propose a **new capability** (skill / scenario candidate), or
- propose an **entity correction** such as an alias patch, or
- decide to ignore (non-actionable).

Teacher receives scenario + skill context, including:

- active conversational package input/examples/matchers and workflow-bound
  affordances, when present
- installed catalog (apps/widgets + origins)
- active runtime overlays plus git-versioned package matchers
- legacy scenario/skill regex rules as read-only compatibility evidence
- built-in regex rules (`nlu.pipeline`)
- selected skill-level NLU artifacts (e.g. `interpreter/intents.yml`)
- intent routing hints (`intent_routes`: scenario intent -> callSkill topic -> skill)
- system/host actions catalog (`system_actions`, `host_actions`), including
  stable action ids, linked intents, slots, and training examples

The target MCP-assisted loop classifies candidates as `skill_action`,
`interface_action`, `scenario_flow`, `entity_correction`, `nlu_correction`,
`development_task`, or `non_actionable`. Verified action candidates are
dispatched through AdaOS after phrase-check evidence confirms that the applied
template returns the planned intent. User corrections such as "no, that is not
it" are linked back to the previous candidate and start another teacher cycle.

Teacher operational state is projected into YJS under
`data.nlu_teacher.*` for UI inspection and persisted as a bounded recovery
projection under `.adaos/state/skills/nlu_teacher/<webspace_id>.json`. The
node-local Teacher conversation ledger owns full event and LLM-log history;
older data is loaded through the paged Teacher history API. Upgrades backfill
the legacy disk/YJS rows into that ledger before applying projection limits.

## Web UI: NLU Teacher

In the default web desktop scenario the current NLU Teacher UI is a schema-driven modal:

- Tabs: **User requests** / **Candidates** / **Signals**
- Grouping:
  - User requests: grouped by `request_id`
  - Candidates: grouped by `candidate.name`, then by `request_id`
- Logs: groups show event payloads inline (raw JSON); Signals expand through
  an accordion without opening an implicit detail modal
- Apply actions:
  - `nlp.teacher.revision.apply`
  - `nlp.teacher.candidate.apply`:
    - UI exposes one primary Apply action that uses the backend-resolved owner
      target
    - for `regex_rule` candidates: applies a scoped runtime overlay to
      `data.nlu.regex_rules` so the next request can match immediately, and
      records a Builder promotion candidate for `conversational/matchers.yaml`
    - for `skill`/`scenario` candidates: creates a development plan item
  - `nlp.teacher.example.save`: persists an operator-approved positive example
    in the node-local runtime overlay store and, for reusable skill/scenario
    targets, records a Builder promotion candidate
  - a successful apply emits `ui.notify` with runtime scope and promotion
    candidate identity; it does not claim that package source was changed
- Voice-originated regex candidates ask the user for confirmation before
  Apply. `да` applies the candidate; the first `нет` rejects it and triggers
  one retry with the rejected candidate in context; a second rejection asks for
  clarification.

Required UI expansion:

- Check phrase field that runs the dry-run probe without dispatching actions.
- Intent ranking, entities, slots, lookup matches, confidence, and fallback reason.
- Trace timeline: `voice text -> regex/neural/rasa -> intent -> action`.
- Correct/Fix/Save example actions with explicit target selection and audit metadata.
- Current-template view with stable ids so the operator can correct existing templates instead of creating duplicates.

Until this UI expansion lands, the current implementation is human-verifiable through API/CLI using
[NLU Human Verification](../guides/nlu-human-verification.md).

## Deterministic matcher storage (current contract)

- Reusable design-time source of truth:
  - skill/scenario `conversational/matchers.yaml`, validated and promoted only
    through Builder/package admission
- Runtime specialization:
  - Yjs `data.nlu.regex_rules[]` for active scoped matching
  - node-local `state/interpreter/nlu_teacher_overlays.json` for governed
    example overlays and promotion-candidate evidence
- Compatibility input:
  - `skill.yaml:nlu.regex_rules[]` and `scenario.json:nlu.regex_rules[]` are
    still read by the runtime but are not Teacher write targets
- Rule identity:
  - every rule has `id="rx.<uuid>"`
- Observability:
  - every `regex.dynamic` match appends a JSONL record into `state/nlu/regex_usage.jsonl` (webspace_id, scenario_id, rule_id, intent, slots...)
- Optional trust policy:
  - `skill.yaml: llm_policy.autoapply_nlu_teacher=true` may auto-apply a
    trusted runtime overlay; it never authorizes a direct package-source or
    public-catalog mutation

## Later (not MVP)

- Rhasspy / offline NLU
- Retriever-style NLU (graph/context retrieval)
- Multi-step, stateful NLU workflows across scenarios
