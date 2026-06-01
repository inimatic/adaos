# NLU Teacher (LLM) MVP

This document describes the minimal teacher-in-the-loop implementation for AdaOS NLU.

## Pipeline (MVP)

1. Router emits `nlp.intent.detect.request` (`text` + `webspace_id` + `request_id`).
2. `nlu.pipeline` tries:
   - built-in + dynamic `regex` (fast, deterministic)
   - if not matched -> delegates to Rasa service (`nlp.intent.detect.rasa`)
3. If intent is found -> `nlp.intent.detected { via: "regex" | "regex.dynamic" | "rasa" }`.
4. If intent is not obtained -> `nlp.intent.not_obtained { reason, via, ... }`.
5. Teacher bridge reacts to `nlp.intent.not_obtained` and emits:
   - `nlp.teacher.request { webspace_id, request }`
   - if the reason is provider/stage unavailable (`rasa_timeout`,
     `rasa_disabled`, `no_active_nlu_stages`, etc.), it records
     `nlp.teacher.skipped` instead and does not call Root/OpenAI
6. Teacher runtimes store state for UI inspection (YJS, per webspace):
   - `data.nlu_teacher.events[]` (includes `llm.request` / `llm.response`)
   - `data.nlu_teacher.candidates[]` (regex rules / skill candidates / scenario candidates)
   - `data.nlu_teacher.revisions[]` (proposed dataset revisions)
   - `data.nlu_teacher.llm_logs[]` (request/response logs; debugging)
7. Teacher state is also persisted on disk so it survives YJS reload/reset:
   - `.adaos/state/skills/nlu_teacher/<webspace_id>.json`

Each LLM turn records audit fingerprints, not raw secrets:

- `request_hash`
- `context_hash`
- `prompt_hash`

These hashes are stored on `data.nlu_teacher.llm_logs[]` and on generated
candidates through `candidate.llm.audit`. They let an operator correlate a
candidate with the bounded prompt/context snapshot without embedding bearer
tokens or unbounded dialog history.

## Enable

Set env vars on hub:

- `ADAOS_NLU_TEACHER=1`
- `ADAOS_NLU_LLM_TEACHER=1`
- optional: `ADAOS_NLU_LLM_MODEL=gpt-4o-mini`
- optional: `ADAOS_NLU_LLM_TIMEOUT_S=20`

## Teacher context (inputs)

LLM teacher receives a compact context snapshot (per webspace), including:

- current scenario id
- scenario-level NLU (`scenario.json:nlu`)
- catalog of apps/widgets (with origins) + installed ids
- built-in regex rules (`nlu.pipeline`)
- existing regex rules (from skills/scenarios + legacy per-webspace cache)
- routing hints (`intent_routes`: scenario intent -> callSkill topic -> skill)
- system actions visible in the current scenario (`system_actions`) and a published host action catalog (`host_actions`)
  with stable action ids, linked intents, slots, host event names, and training examples
- skill manifests (`skills_manifest`: tools/events/llm_policy summary for installed skills)

Goal: prefer improving existing intents (regex rule / dataset revision) over creating a new capability, when possible.

Teacher bridge classifies `nlp.intent.not_obtained.reason` before the LLM
runtime receives a request. Low-confidence/no-intent outcomes are teachable NLU
gaps; provider disabled/down/timeout/unresolved states are stored for
diagnostics but skipped so the LLM does not create templates for outages.

## Target UI

The NLU Teacher modal should become the operator-facing workbench for testing and curating NLU behavior.

Current UI status:

- Implemented: User requests tab, Candidates tab, raw JSON event inspection, revision Apply, and candidate Apply.
- Implemented in backend/API: dry-run phrase probe, dynamic lookup inspection,
  and operator-approved example save with skill/scenario/system-action target
  selection.
- Missing in UI: Check phrase, trace/ranking/entities/action preview, Correct/Fix/Save example, template inventory, and patch preview.

Target controls:

- **Check phrase**: input field that sends a dry-run request through the current NLU pipeline.
- **Trace view**: shows `voice text -> regex/neural/rasa -> intent -> action`, with stage, confidence, latency, and fallback reason.
- **Candidate view**: shows intent ranking, extracted entities/slots, matched lookup values, and the proposed action target.
- **Correct**: marks the current interpretation as accepted and records the example as positive feedback.
- **Fix**: lets the operator choose or edit intent, slots, action, and storage target.
- **Save example**: persists the curated example into scenario or skill training content, without mutating code.

The first implementation can be intentionally narrow: support dry-run phrase checks, show ranking/entities for Rasa, and save examples for the
default desktop modal and runtime-backed system action intents. Broader tool/action authoring should wait until Root MCP descriptors are available.

## Current dry-run API

The first backend slice is available for the Teacher UI check-phrase field:

- `POST /api/nlu/teacher/{webspace_id}/probe`
- request:

```json
{
  "text": "open apps catalog",
  "use_rasa": true,
  "emit_trace": true
}
```

The endpoint runs a dry check through regex first and optionally Rasa second. It does not emit `nlp.intent.detected`
and does not dispatch actions, so it is safe to call from a UI preview.

Response shape:

```json
{
  "ok": true,
  "accepted": true,
  "via": "rasa",
  "intent": "desktop.open_modal",
  "confidence": 0.87,
  "slots": {"modal_id": "apps_catalog"},
  "entities": [{"entity": "modal_id", "value": "apps_catalog"}],
  "intent_ranking": [{"name": "desktop.open_modal", "confidence": 0.87}],
  "stages": [
    {"stage": "request", "status": "received"},
    {"stage": "regex", "status": "miss"},
    {"stage": "rasa", "status": "hit"}
  ]
}
```

When `emit_trace=true`, each stage is also persisted through `nlu.trace.stage` into `data.nlu_trace.items[]`.

## Current candidate Apply API

The current backend can apply a stored Teacher candidate without requiring the
UI to publish directly to the event bus:

- `POST /api/nlu/teacher/{webspace_id}/candidate/apply`
- request:

```json
{
  "candidate_id": "cand.123",
  "target": {"type": "scenario", "id": "web_desktop"}
}
```

The endpoint emits `nlp.teacher.candidate.apply`. For `kind="regex_rule"`
candidates, AdaOS persists the rule into the selected scenario/skill owner,
mirrors it into runtime regex state, then immediately re-runs the original
phrase through the probe path.

Before a regex candidate can be applied, LLM Teacher performs a local preview:
the pattern must compile as Python regex and match the original phrase. Valid
proposals stay `pending` and carry `preview.status="regex_matched"` plus
captured slots. Invalid or non-matching proposals are stored as
`quarantined`; Apply rejects them and emits
`nlp.teacher.candidate.apply.rejected`.

LLM Teacher also suppresses duplicate active regex candidates with the same
planned intent, pattern, and storage target. The duplicate is not appended to
`data.nlu_teacher.candidates[]`; AdaOS emits
`nlp.teacher.candidate.duplicate_suppressed` and records a
`candidate.duplicate_suppressed` Teacher event instead.

If the probe result matches the planned candidate intent, AdaOS:

- marks the candidate as `intent_matched`
- stores compact verification evidence on the candidate
- emits `nlp.teacher.candidate.verified`
- appends Teacher events `candidate.verified` and `understanding.acquired`
- emits `nlp.teacher.understanding.acquired`

If the probe returns another intent or still misses, the candidate is marked
`verification_failed`. Dispatch is still a separate future gate; the LLM does
not execute actions directly.

Current rollback surface:

- `POST /api/nlu/teacher/{webspace_id}/candidate/rollback`
- request:

```json
{
  "candidate_id": "cand.123"
}
```

For applied regex candidates, rollback removes the rule from the owning
scenario/skill artifact and from the runtime regex cache mirror, marks the
candidate `rolled_back`, and emits `nlp.teacher.regex_rule.rolled_back`. This
keeps smoke examples repeatable: after rollback, the same phrase should miss
again until the candidate is applied once more.

## Current lookup API

Teacher/LLM can inspect the desktop ids that should be treated as entity candidates:

- `GET /api/nlu/teacher/{webspace_id}/lookups`
- lookup sets:
  - `modal_id`
  - `node_ref`
  - `app_id`
  - `scenario_id`
  - `webspace_id`

The backend reads workspace desktop/scenario manifests and falls back to packaged desktop manifests when the test/install workspace is still
empty. For this API, the manifest snapshot is then overlaid with live read-only YJS values from `ui.application.modals`,
`registry.merged.modals`, `data.catalog.apps`, `data.installed.apps`, `data.nodes`, and `ui.current_scenario`.

Rasa export intentionally consumes the stable manifest snapshot as native lookup-table entries and writes the full snapshot to
`state/interpreter/rasa_project/data/lookup_tables.json`. That keeps training reproducible while the Teacher API can still show the current
desktop registry.

## Human verification contract

The current implementation is considered verifiable only when the operator can reproduce the result through tests/API and, where available,
the current UI. The checklist lives in [nlu-human-verification.md](./nlu-human-verification.md).

Minimum acceptance for every NLU slice:

- A focused test or smoke command exists.
- The expected response includes intent, confidence, slots/entities, ranking when available, and stage trace.
- Fallback behavior is explicit when confidence is too low or no stage accepts the phrase.
- The operator can tell whether a value came from regex, Rasa, lookup tables, or Teacher fallback.
- Documentation states whether the behavior is available in UI now or only through API/CLI.

Teacher UI becomes the primary human control surface only after Check phrase, trace view, Correct/Fix/Save example, and template inventory are
implemented. Until then, the API/CLI checklist remains the source of truth.

## MCP-assisted teacher context

For the teacher to decide which skill/tool owns a phrase, it needs governed machine-readable context, not only free-form prompt text.
The target architecture uses `Root MCP Foundation` as the agent-facing context and authorization layer.

The LLM is not allowed to call SDK functions, event publishers, skill tools, or host/UI actions directly. It may only propose an
AdaOS-owned interpretation, candidate, or patch. AdaOS then validates, traces, previews, applies, and dispatches through its own event and
authoring surfaces. This keeps every step observable in `nlu.trace`, `data.nlu_teacher.*`, Root MCP audit, and the normal runtime event bus.

The target loop is:

1. NLU misses or returns a low-confidence result.
2. AdaOS builds an authoring context through MCP/API surfaces.
3. LLM proposes one or more candidates: intent/action, regex/Rasa/neural template, entity correction, or development task.
4. AdaOS validates the candidate with `nlu.check_phrase` / preview APIs and records trace.
5. If the proposed template produces the LLM-planned intent, AdaOS may dispatch the normal runtime intent to the user-facing system.
6. If the user says "no, that is not it" or gives a correction, AdaOS treats the new phrase as a correction linked to the previous
   request/candidate and starts another analysis/training cycle.
7. Durable training changes require preview, ownership resolution, audit, and either operator approval or an explicit per-owner trust policy.

This makes the LLM an authoring assistant, not an execution authority.

Token/session flow:

1. The web **MCP Server** modal exposes an **Issue token** button.
2. The operator chooses target, TTL, and capability profile, initially `NLUTeacherAuthor`.
3. Root issues a target-scoped MCP session lease or access token.
4. The browser stores only the returned bearer token/session reference needed for subsequent root requests.
5. Root resolves the token to `subnet_id`, `zone`, target, and capabilities, then routes allowed calls to root descriptors or the managed hub.

The token should accompany root requests as authorization context. It should not be embedded into LLM prompts, training examples, or generated
NLU artifacts.

Required Root MCP surfaces for NLU Teacher:

- `nlu.describe_pipeline`: regex/neural/rasa stages, thresholds, supported template types, and apply capabilities.
- `nlu.check_phrase`: dry-run phrase interpretation with trace, ranking, entities, and action preview.
- `nlu.get_trace`: fetch the stored trace for a request/candidate without re-running inference.
- `nlu.get_dialog_context`: recent user/system turns, previous failures, current route, and the active correction target.
- `nlu.get_recent_failures`: last not-obtained/low-confidence requests grouped by request/thread.
- `nlu.list_templates`: current regex/Rasa/neural/lookup templates with stable ids, owners, fingerprints, and status.
- `nlu.get_template`: one current template with full editable content and provenance.
- `nlu.list_training_targets`: scenario and skill locations where examples/rules may be saved.
- `nlu.propose_templates`: LLM-facing contract for multi-engine template proposals.
- `nlu.preview_template_patch`: validate a proposed correction and return a diff without writing.
- `nlu.apply_template_patch`: apply an approved correction with audit and stale-write protection.
- `desktop.registry.lookup`: current `modal_id`, `node_ref`, `app_id`, `scenario_id`, webspace, and installed desktop objects.
- `desktop.describe_actions`: runtime-backed host/UI actions such as modal open/close, scenario switch, home scenario, reload/reset, and
  app install toggle.
- `desktop.get_state`: current scenario, home scenario, open modals, installed apps, focused node/browser, and current route.
- `desktop.preview_action`: validate an action candidate and show the event/action preview without dispatching it.
- `skill.describe_tools`: skill tools, event subscriptions/publications, input schemas, and ownership hints.
- `skill.describe_nlu`: skill-owned intents, examples, slots, regex rules, and `llm_policy`.
- `scenario.describe_nlu`: scenario-owned intents, actions, examples, and routing hints.
- `sdk.describe_surface`: SDK functions/events/projections as read-only descriptors for ownership and affordance discovery. This is not an
  execution surface for the LLM.

This keeps the existing regex model intact: Root MCP supplies descriptors and governed operations, while the current runtime pipeline remains
`regex-first` and data-owned rules stay in scenario/skill artifacts.

Current Root MCP implementation status:

- implemented: `nlu_authoring.get_context`
- implemented: `nlu_authoring.check_phrase`, backed by the same probe service
  as the Teacher API and returning `authoring_boundaries` with
  `dispatch=false` and `training_mutation=false`
- implemented in the Codex bridge: `check_nlu_phrase`
- implemented in LLM Teacher: the prompt context includes read-only Root MCP
  evidence from `nlu_authoring.get_context` and `nlu_authoring.check_phrase`
  before Root/OpenAI is asked to propose a candidate
- implemented: NLU authoring MCP results include bearer/session-derived
  `root_scope` and `target_id`, so the LLM can see which subnet target the
  context belongs to
- not yet implemented: trace/dialog/recent-failure/template inventory and
  desktop action preview surfaces

## NLU action classes

The teacher should classify every actionable phrase into one of these classes before proposing templates:

- `skill_action`: an existing skill owns the behavior. The output should resolve to an existing intent/action route, skill event, or skill tool
  ownership hint. Training data belongs to the skill unless the scenario deliberately overrides routing.
- `interface_action`: the phrase controls the AdaOS shell/client, for example open/close modal, switch scenario, go home, reload/reset
  webspace, install/toggle app, or route output to a node. Training data belongs to the scenario or system-action overlay; dispatch goes
  through host/UI action descriptors, not through SDK calls from the LLM.
- `scenario_flow`: the phrase selects or advances scenario-level behavior. Training data belongs to the scenario, and actions should be
  represented as scenario intent actions (`callHost`, `callSkill`, or future governed action descriptors).
- `entity_correction`: the phrase changes how AdaOS should understand a name, alias, node, device, app, modal, scenario, webspace, or skill.
  It should produce a named-entity patch, not a new intent.
- `nlu_correction`: the phrase corrects a previous NLU miss or false positive. It must be linked to the previous request/candidate and should
  prefer patching an existing template.
- `development_task`: the user asks for behavior that does not exist yet. The teacher should create a task/candidate for the LLM programmer
  to modify an existing skill/scenario or create a new one. It should not pretend the capability exists.
- `non_actionable`: the utterance is chat/noise/out of scope. The teacher should not mutate training data.

This classification prevents the teacher from forcing every phrase into regex/Rasa examples. It also tells the UI whether the next step is
dispatch, correction, template patching, or a development backlog item.

## Candidate lifecycle

The initial closed-loop implementation can stay narrow and regex-first:

1. LLM proposes a candidate with planned `intent`, optional `target`, and a template patch such as a regex rule for an existing AdaOS intent.
2. AdaOS applies the candidate only in preview or trusted-autoapply mode.
3. AdaOS runs the same phrase through the normal dry-run pipeline.
4. If the returned intent matches the planned intent, AdaOS records the verification and may dispatch the resulting runtime intent/action
   through the normal event bus.
5. If dispatch succeeds, the user sees the requested behavior.
6. If the user corrects it, the correction references the same request/candidate and the teacher receives both the failed action and the new
   phrase.
7. Only after repeated success or explicit approval does the candidate become durable training data.

The key invariant: runtime execution always uses AdaOS intent dispatch and host/skill surfaces. The LLM never bypasses tracing by calling an
SDK function, publishing an event, or invoking a tool directly.

## NLU authoring contract

Keep the implementation split into three layers:

- `Teacher UI`: renders trace, current templates, diffs, and approval controls. It should not decide ownership or write files directly.
- `NLU authoring service`: validates phrase checks, template patches, training targets, and safe-apply rules.
- `Root MCP`: provides governed descriptors, current template inventory, token/session resolution, routing, and audit.

The LLM should receive the current template inventory before proposing changes. This prevents duplicate regex rules, repeated Rasa examples,
and blind rewrites of training content.

Current template inventory response:

```json
{
  "templates": [
    {
      "template_id": "rx.web_desktop.desktop.open_weather.01HX...",
      "engine": "regex",
      "intent": "desktop.open_weather",
      "owner": {"type": "scenario", "id": "web_desktop"},
      "status": "active",
      "fingerprint": "sha256:...",
      "summary": "RU/EN weather phrase with optional city",
      "source": {"path": "scenarios/web_desktop/scenario.json", "json_pointer": "/nlu/regex_rules/0"}
    },
    {
      "template_id": "rasa.web_desktop.desktop.open_node_modal.example.4f9c...",
      "engine": "rasa",
      "intent": "desktop.open_node_modal",
      "owner": {"type": "scenario", "id": "web_desktop"},
      "status": "active",
      "fingerprint": "sha256:...",
      "summary": "open [apps_catalog](modal_id) on [member-1](node_ref)"
    }
  ],
  "snapshot_id": "nlu-template-snapshot.2026-05-11T12:00:00Z",
  "generated_from": ["scenario:web_desktop", "skills:*", "desktop.registry"]
}
```

Template ids should be stable enough for correction and audit:

- `regex`: use the rule id when available (`rx.<uuid>`), namespaced by owner/intent when exposed through MCP.
- `rasa`: derive a deterministic id from owner, intent, example text, and entity annotations.
- `neural`: derive from owner, intent, masked text, and label.
- `lookup`: derive from registry namespace, entity name, snapshot id, and value-set fingerprint.

Corrections are patches against existing templates, not raw file writes:

```json
{
  "template_id": "rasa.web_desktop.desktop.open_node_modal.example.4f9c...",
  "base_fingerprint": "sha256:...",
  "operation": "replace",
  "patch": {
    "example": "open [apps_catalog](modal_id) on [kitchen](node_ref)",
    "reason": "Use real node alias from desktop registry instead of hardcoded member-1"
  }
}
```

Safe apply state machine:

1. `list/get templates`: collect current ids, owners, fingerprints, and editable fields.
2. `propose patch`: LLM references an existing `template_id` or explicitly asks to create a new template.
3. `preview diff`: authoring service validates owner, capability, schema, duplicates, and `base_fingerprint`.
4. `operator approval`: UI shows before/after, affected intent/action, and expected pipeline impact.
5. `apply`: write through scenario/skill training APIs only, then record audit event and rollback pointer.
6. `verify`: run `nlu.check_phrase` and optional golden phrase regression checks.

If `base_fingerprint` no longer matches, the patch must be rejected as stale and the UI should refresh template inventory.

## Multi-engine teacher output

To avoid overfitting the first Teacher version to one NLU engine, the LLM should return a template bundle for all relevant pipeline stages.
The runtime can apply only the supported/safe subset at first.

Proposed bundle shape:

```json
{
  "phrase": "open apps catalog on kitchen display",
  "intent": "desktop.open_node_modal",
  "target": {"type": "scenario", "id": "web_desktop"},
  "slots": [
    {"name": "modal_id", "value": "apps_catalog", "source": "desktop.registry.lookup"},
    {"name": "node_ref", "value": "kitchen", "source": "desktop.registry.lookup"}
  ],
  "action": {"event": "desktop.modal.open", "params": {"modal_id": "$slot.modal_id", "target_node_id": "$slot.node_ref"}},
  "templates": {
    "regex": [{"pattern": "open\\s+(?P<modal_id>...)\\s+on\\s+(?P<node_ref>...)", "priority": "draft"}],
    "rasa": [{"example": "open [apps_catalog](modal_id) on [kitchen](node_ref)"}],
    "neural": [{"masked": "open {modal_id} on {node_ref}", "label": "desktop.open_node_modal"}],
    "lookups": [{"entity": "modal_id", "values_ref": "desktop.registry.modal_ids"}, {"entity": "node_ref", "values_ref": "desktop.registry.node_refs"}]
  },
  "rationale": "Phrase maps to the default desktop modal action and uses known registry values."
}
```

Initial apply policy:

- `regex`: apply only after explicit operator confirmation, and never overwrite existing rules.
- `rasa`: save examples and lookup references into scenario/skill training content.
- `neural`: store labels/masked examples as future training metadata; do not enable inference behavior until neural stage is explicitly enabled.
- `lookups`: generate from live desktop registry snapshots, not from hardcoded examples such as `member-1`.

## Apply

Apply can be triggered from UI or programmatically:

- apply a proposed dataset revision:
  - `nlp.teacher.revision.apply { revision_id, intent, examples[], slots }`
- apply a teacher candidate:
  - `nlp.teacher.candidate.apply { candidate_id, target? }`
  - `POST /api/nlu/teacher/{webspace_id}/candidate/apply`
  - for `regex_rule` candidates the runtime delegates to `nlp.teacher.regex_rule.apply { intent, pattern, target? }`
- rollback an applied regex candidate:
  - `nlp.teacher.regex_rule.rollback { candidate_id, rule_id?, target? }`
  - `POST /api/nlu/teacher/{webspace_id}/candidate/rollback`
- save an operator-approved positive example:
  - `nlp.teacher.example.save { text, intent, target, slots?, request_id? }`
  - `POST /api/nlu/teacher/{webspace_id}/example/save`
  - `target.type` is `skill`, `scenario`, or `system_action`
  - system-action examples are saved to
    `state/interpreter/system_action_feedback.jsonl` and included in later
    Rasa/Neural exports

## Where regex rules are stored

The teacher does not "bake" regexes into the hub code. A rule is stored as data owned by a workspace artifact:

- **Skill-owned** (preferred): `.adaos/workspace/skills/<skill>/skill.yaml` -> `nlu.regex_rules[]`
- **Scenario-owned**: `.adaos/workspace/scenarios/<scenario>/scenario.json` -> `nlu.regex_rules[]`
- **Legacy runtime cache**: mirrored into YJS `data.nlu.regex_rules[]` so it starts matching immediately after Apply.

Every rule has a stable identity: `id="rx.<uuid>"`.

## Target selection (skill vs scenario)

When the teacher proposes a regex rule, it should also propose a storage target:

- Prefer the skill that actually handles the intent (derived from scenario intent `callSkill` actions + skill `events.subscribe`).
- If the intent triggers host/system behavior (`callHost`), the target is usually the scenario.

Apply supports a UI override ("Apply to Scenario"), in addition to an LLM-suggested target.

## Auto-apply policy (trusted skills)

Skills can opt into automatic application of teacher-proposed regex rules:

- `skill.yaml: llm_policy.autoapply_nlu_teacher: true`

If enabled and the candidate target is that skill, the hub auto-emits `nlp.teacher.candidate.apply` after a candidate is proposed.

## Observability: regex usage journal

Each time the dynamic regex stage matches, the hub appends a JSONL record to:

- `state/nlu/regex_usage.jsonl`

This is intended for later cleanup/optimization (identify dead rules, consolidate duplicates, etc.).

## Example: improve existing intent via regex rule

Utterance: `Покажи температуру в Берлине`

Assume built-in weather regex only matches `погода` / `weather`, so the regex stage misses the intent.

Expected teacher decision:

- `decision="propose_regex_rule"`
- `regex_rule.intent="desktop.open_weather"`
- `regex_rule.pattern` should be a Python regex with named capture groups, e.g. `(?P<city>...)`
- `target` should usually be the owning skill (e.g. `{"type":"skill","id":"weather_skill"}`)

After you click **Apply** (UI emits `nlp.teacher.candidate.apply`):

- the rule is persisted into the chosen owner (skill/scenario) and mirrored into `data.nlu.regex_rules`
- the original phrase is probed again; if the result intent matches the
  candidate intent, the candidate becomes `intent_matched` and AdaOS emits
  `nlp.teacher.understanding.acquired`
- the next time the same utterance is sent, `nlu.pipeline` should resolve it as `via="regex.dynamic"` without calling the LLM

## Roadmap

The canonical execution checklist lives in [nlu-roadmap.md](./nlu-roadmap.md).
This section keeps the compact Teacher-specific sequence.
Status labels mirror the canonical checklist: `[deferred]` means intentionally
postponed, and `[polish]` means hardening/operator experience after the first
functional slice.

### Phase 0 - Teacher contracts and baseline guardrails

- Define request/thread, candidate, lifecycle, event, idempotency, response-policy, MCP capability, and LLM prompt-data contracts.
- Add RU/EN Unicode fixtures for Teacher probes, correction threads, and template patch previews.
- Keep the existing API as the implementation backend; Root MCP wraps governed capabilities instead of becoming a second source of truth.
- Keep LLM output limited to candidates and patches; no direct SDK/tool/event/UI execution.

- Keep `regex -> Rasa -> fallback/teacher` working through event bus.
- Preserve low-confidence fallback to `nlp.intent.not_obtained`.
- Keep service skills out of the user NLU fingerprint unless they provide training metadata.
- Maintain smoke coverage for `[homepoint] Voice -> Rasa -> desktop.modal.open`.

### Phase 1 - Use existing APIs as the working loop

- Use the existing Teacher API as the first source of truth:
  - `POST /api/nlu/teacher/{webspace_id}/probe`
  - `GET /api/nlu/teacher/{webspace_id}/lookups`
  - `POST /api/nlu/teacher/{webspace_id}/example/save`
- Start with a narrow candidate type: regex/template candidate for an existing AdaOS intent, not a generic action candidate.
- Record planned intent, owner hint, proposed template, verification status, dispatch status, and correction-thread link.
- Parse both plain JSON and fenced JSON LLM responses; quarantine regex
  proposals that fail compile/source-phrase preview.
- Store request/context/prompt hashes and suppress duplicate active regex candidates.
- After previewing or trusted-applying a regex/template candidate, immediately run the probe; mark the candidate verified only if the returned
  intent matches the planned intent.
- Include Root MCP `nlu_authoring.get_context` and `nlu_authoring.check_phrase`
  evidence in the LLM prompt so candidate generation uses the governed MCP
  plane, not only ad-hoc runtime snapshots.
- Dispatch only through the normal AdaOS intent/action path and only when the candidate's action side-effect class allows auto-dispatch.
- Link "no, that is not it" corrections to the previous request/candidate so the next teacher pass has the failure context.
- Distinguish true NLU gaps from service-down or provider-disabled states before asking the LLM to create templates.
- Surface stage decisions, confidence, ranking, entities, fallback reasons, and action preview in trace data before expanding the MCP plane.

### Phase 2 - Minimal MCP descriptive plane

- Add **Issue token** to the MCP Server modal or another operator-controlled surface.
- Issue target-scoped Root MCP session leases with an initial `NLUTeacherAuthor`/read-mostly capability profile.
- Publish read-only MCP contracts for:
  - `nlu.describe_pipeline`
  - `nlu_authoring.get_context`
  - `nlu_authoring.check_phrase` backed by the current probe service
  - `nlu.get_trace`
  - `nlu.get_dialog_context`
  - `nlu.get_recent_failures`
  - `desktop.registry.lookup` backed by the current lookup service
  - `skill.describe_tools`
  - `skill.describe_nlu`
  - `scenario.describe_nlu`
  - `sdk.describe_surface` as descriptors only
- Keep write/apply operations behind the existing Teacher UI/API until preview and audit are stable.

### Phase 3 - Action and ownership surfaces

- `[deferred]` Publish `desktop.describe_actions` for runtime-backed interface actions:
  modal open/close, scenario switch, home scenario navigation, reload/reset, and app install toggle.
- `[deferred]` Publish `desktop.get_state` so the teacher can distinguish "open X" from "close the currently open modal" and can resolve current/home
  scenario context.
- `[deferred]` Publish `desktop.preview_action` so a candidate can show the event/action that would be dispatched without mutating the UI.
- `[deferred]` Add `nlu.resolve_owner` to map intent/action candidates to skill, scenario, system action, or development task ownership.
- `[deferred]` Classify teacher decisions into `skill_action`, `interface_action`, `scenario_flow`, `entity_correction`, `nlu_correction`,
  `development_task`, or `non_actionable`.
- `[deferred]` Define action side-effect classes and owner conflict policy before allowing generic action candidates.

### Phase 4 - Template inventory and safe patching

- `[deferred]` Publish current NLU template inventory with `template_id`, owner, status, fingerprint, and provenance.
- `[deferred]` Add:
  - `nlu.list_templates`
  - `nlu.get_template`
  - `nlu.list_training_targets`
  - `nlu.preview_template_patch`
  - `nlu.apply_template_patch`
- `[deferred]` Accept correction patches against existing `template_id` values with `base_fingerprint` stale-write protection.
- `[deferred]` Apply only through owning scenario/skill/system-action/named-entity services, never by LLM raw file writes.
- `[deferred]` Add rollback pointers and audit events for every applied patch.
- `[deferred]` Add duplicate-template detection, regex blast-radius checks, and golden-phrase impact preview before durable apply.

### Phase 5 - Useful Teacher UI

- Add Check phrase field.
- `[polish]` Show intent ranking/entities/action preview.
- `[deferred]` Show existing templates relevant to the phrase/intent and allow selecting one for correction.
- `[deferred]` Add Correct/Fix actions.
- `[deferred]` Save curated examples into scenario/skill training content.
- `[polish]` Show previous failure/correction thread when the current phrase looks like a correction.
- `[polish]` Show candidate verification state: proposed, previewed, intent-matched, dispatched, accepted, corrected, applied.

### Phase 6 - Multi-engine template application

- `[deferred]` Accept Teacher template bundles for regex, Rasa, neural, and lookup metadata.
- `[deferred]` Accept correction patches against existing `template_id` values with `base_fingerprint` stale-write protection.
- `[deferred]` Apply only the supported subset safely.
- `[deferred]` Keep regex deterministic and data-owned.
- `[deferred]` Feed Rasa from actual desktop registry lookups.
- `[deferred]` Store neural examples as curated training metadata until provider rebuild/reindex gates approve promotion.

### Phase 7 - Feedback, regression, and promotion

- `[polish]` Collect statistics by phrase, intent, stage, confidence, and operator feedback.
- `[polish]` Promote high-value examples into training sets.
- `[polish]` Tune confidence thresholds from observed misses and false accepts.
- `[deferred]` Add rollout/rollback controls for neural and Rasa model updates.
- `[deferred]` Add regex blast-radius checks, duplicate-template detection, golden phrase regression, and false-positive review queues.
