# NLU Roadmap Checklist

Current implementation estimate: **40%** for the practical AdaOS NLU roadmap.

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

## Phase 4: Dynamic Lookups and Template Inventory

- [ ] Export live desktop lookup tables:
  - `modal_id`
  - `node_ref`
  - `app_id`
  - `scenario_id`
  - `webspace_id`
- [ ] Feed lookup tables into Rasa training data.
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

1. Wire Teacher UI to `POST /api/nlu/teacher/{webspace_id}/probe`.
2. Add live desktop lookup export and Rasa lookup-table generation.
3. Add "save correct example" backend action with scenario/skill target selection.
4. Add stage latency and golden phrase checks.

## Last Completed Slice

- Rasa is packaged as an optional default-on service-skill and installed into skill runtime slots.
- NLU Teacher has a dry-run phrase probe API with regex-first and optional Rasa fallback.
- Runtime emits stage trace events for regex, pipeline delegation, Rasa, and dispatcher actions/rejects.
- Trace items are persisted to `data.nlu_trace.items[]` for the future UI timeline.
