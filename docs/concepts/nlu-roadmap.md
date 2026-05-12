# NLU Roadmap Checklist

Current implementation estimate: **49%** for the practical AdaOS NLU roadmap.

## Phase 1: Baseline Runtime

- [x] Regex-first pipeline with dynamic scenario/skill regex rules.
- [x] Rasa NLU service-skill isolated from the hub Python environment.
- [x] Rasa service-skill prepared in A/B skill runtime slots.
- [x] Confidence/fallback path to `nlp.intent.not_obtained`.
- [x] Baseline desktop intents for opening modals and node-scoped modals.

## Phase 2: Operator Feedback Loop

- [x] NLU Teacher stores not-obtained requests per webspace.
- [x] Teacher can apply regex candidates into scenario/skill-owned artifacts.
- [x] Teacher can apply dataset revisions into scenario training content.
- [x] Dry-run phrase probe API for Teacher UI:
  - `POST /api/nlu/teacher/{webspace_id}/probe`
  - regex-first, optional Rasa fallback
  - returns `intent_ranking`, `entities`, `slots`, `stages`
  - does not dispatch actions
- [x] Human verification checklist separates current API/CLI checks from target UI behavior.
- [ ] UI field for "check phrase" wired to the probe endpoint.
- [ ] UI buttons: "correct", "fix", "save example".
- [ ] Operator-approved positive feedback stored with audit metadata.

## Phase 3: Observability

- [x] `data.nlu_trace.items[]` stores request/detected/not-obtained events.
- [x] Stage trace event `nlu.trace.stage` records:
  - `request`
  - `regex`
  - `pipeline delegate`
  - `rasa`
  - `dispatcher action/reject`
- [ ] Trace UI should show `voice text -> regex/neural/rasa -> intent -> action`.
- [ ] Add latency per stage and service timing.
- [ ] Add golden phrase regression reports.

## Human Verification Gates

- [x] Current implemented behavior has a manual checklist: [nlu-human-verification.md](./nlu-human-verification.md).
- [x] Documentation marks which NLU Teacher behaviors are current UI, backend/API only, or target architecture.
- [ ] NLU Teacher UI can run a phrase probe without terminal access.
- [ ] NLU Teacher UI shows stage trace, ranking, entities, slots, lookup matches, confidence, and action preview.
- [ ] NLU Teacher UI supports Correct/Fix/Save example with target selection and audit metadata.
- [ ] Template correction flow uses stable ids and stale-write fingerprints.

## Phase 4: Dynamic Lookups and Template Inventory

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

## Phase 5: MCP-Assisted Authoring

- [ ] MCP Server modal issues scoped NLU authoring token.
- [ ] Root resolves token to subnet/zone/capabilities.
- [ ] Root MCP surfaces:
  - `nlu.describe_pipeline`
  - `nlu.check_phrase`
  - `nlu.list_templates`
  - `nlu.get_template`
  - `nlu.preview_template_patch`
  - `nlu.apply_template_patch`
  - `desktop.registry.lookup`
  - `skill.describe_tools`
- [ ] LLM receives current template inventory before proposing changes.
- [ ] Template patches are previewed and operator-approved before apply.

## Phase 6: Neural Stage

- [ ] Neural service-skill model artifacts.
- [ ] Neural parse contract and confidence gates.
- [ ] Neural abstain path to Rasa/Teacher.
- [ ] Model registry, canary rollout, rollback pointer.

## Immediate Next Steps

1. Add a UI-capable action/bridge for safe NLU Teacher API calls, then wire Check phrase to `POST /api/nlu/teacher/{webspace_id}/probe`.
2. Render probe response in the Teacher modal: trace, intent ranking, entities, slots, lookup matches, confidence, and action preview.
3. Add "save correct example" backend action with scenario/skill target selection and audit metadata.
4. Expose stable template ids for regex, Rasa examples, neural labels, and lookup sets.
5. Add stage latency and golden phrase checks.

## Last Completed Slice

- Rasa is packaged as an optional default-on service-skill and installed into skill runtime slots.
- NLU Teacher has a dry-run phrase probe API with regex-first and optional Rasa fallback.
- NLU Teacher exposes baseline desktop lookup tables for `modal_id`, `node_ref`, `app_id`, `scenario_id`, and `webspace_id`.
- Teacher lookup API overlays live YJS values from `ui.application.modals`, `registry.merged.modals`, `data.catalog.apps`,
  `data.installed.apps`, `data.nodes`, and `ui.current_scenario`.
- Rasa export writes native lookup tables and `data/lookup_tables.json`; lookup summary is included in the training fingerprint.
- Runtime emits stage trace events for regex, pipeline delegation, Rasa, and dispatcher actions/rejects.
- Trace items are persisted to `data.nlu_trace.items[]` for the future UI timeline.
- NLU documentation now includes a human verification checklist and clearly separates current UI, backend/API-only behavior, and target UI.
