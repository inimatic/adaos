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
6. Teacher runtimes store state for UI inspection (YJS, per webspace):
   - `data.nlu_teacher.events[]` (includes `llm.request` / `llm.response`)
   - `data.nlu_teacher.candidates[]` (regex rules / skill candidates / scenario candidates)
   - `data.nlu_teacher.revisions[]` (proposed dataset revisions)
   - `data.nlu_teacher.llm_logs[]` (request/response logs; debugging)
7. Teacher state is also persisted on disk so it survives YJS reload/reset:
   - `.adaos/state/skills/nlu_teacher/<webspace_id>.json`

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
- skill manifests (`skills_manifest`: tools/events/llm_policy summary for installed skills)

Goal: prefer improving existing intents (regex rule / dataset revision) over creating a new capability, when possible.

## Целевой UI

Модалка NLU Teacher должна стать рабочим местом оператора для проверки и обучения NLU-поведения.

Целевые элементы:

- **Проверить фразу**: поле ввода, которое запускает dry-run через текущий NLU pipeline.
- **Trace view**: показывает `voice text -> regex/neural/rasa -> intent -> action`, stage, confidence, latency и причину fallback.
- **Candidate view**: показывает intent ranking, извлеченные entities/slots, совпавшие lookup values и action preview.
- **Правильно**: подтверждает текущую интерпретацию и пишет фразу как positive feedback.
- **Исправить**: позволяет выбрать или поправить intent, slots, action и storage target.
- **Сохранить пример**: сохраняет curated example в scenario или skill training content, без правки кода.

Первую реализацию лучше держать узкой: dry-run phrase check, ranking/entities от Rasa и сохранение примеров для baseline desktop modal intents.
Более широкую генерацию tool/action стоит включать после появления Root MCP descriptors.

## MCP-assisted teacher context

Чтобы Teacher понимал, к какому skill относится фраза, какой tool/action вызывать и какие слоты извлекать, ему нужен управляемый
machine-readable context, а не только свободный prompt. Целевая архитектура использует `Root MCP Foundation` как agent-facing слой
контекста и авторизации.

Token/session flow:

1. В web-модалке **MCP Server** появляется кнопка **Issue token**.
2. Оператор выбирает target, TTL и capability profile, сначала `NLUTeacherAuthor`.
3. Root выпускает target-scoped MCP session lease или access token.
4. Browser хранит только bearer token/session reference, нужный для следующих root requests.
5. Root по токену восстанавливает `subnet_id`, `zone`, target и capabilities, затем маршрутизирует разрешенные calls в root descriptors или managed hub.

Токен должен сопровождать root requests как authorization context. Его нельзя вставлять в LLM prompt, training examples или generated NLU artifacts.

Минимальные Root MCP surfaces для NLU Teacher:

- `nlu.describe_pipeline`: stages regex/neural/rasa, thresholds, поддерживаемые template types и apply capabilities.
- `nlu.check_phrase`: dry-run интерпретация фразы с trace, ranking, entities и action preview.
- `nlu.list_training_targets`: scenario/skill locations, куда можно сохранять examples/rules.
- `nlu.propose_templates`: контракт LLM-facing template proposals.
- `desktop.registry.lookup`: текущие `modal_id`, `node_ref`, `app_id`, `scenario_id`, webspace и установленные desktop objects.
- `skill.describe_tools`: tools навыков, event subscriptions/publications, input schemas и ownership hints.

Так мы не ломаем существующую regex-модель: Root MCP дает descriptors и governed operations, а текущий runtime pipeline остается
`regex-first`; data-owned rules продолжают жить в scenario/skill artifacts.

## NLU authoring contract

Держим реализацию разделенной на три слоя:

- `Teacher UI`: показывает trace, current templates, diffs и approval controls. UI не должен сам решать ownership и писать файлы.
- `NLU authoring service`: валидирует phrase checks, template patches, training targets и safe-apply rules.
- `Root MCP`: дает governed descriptors, current template inventory, token/session resolution, routing и audit.

LLM должна получать current template inventory до того, как предлагать изменения. Это убирает duplicate regex rules, повторы Rasa examples
и blind rewrites training content.

Дополнительные Root MCP surfaces для template inventory и коррекции:

- `nlu.list_templates`: текущие regex/Rasa/neural/lookup templates со stable ids, owners, fingerprints и status.
- `nlu.get_template`: один текущий template с полным editable content и provenance.
- `nlu.preview_template_patch`: проверить proposed correction и вернуть diff без записи.
- `nlu.apply_template_patch`: применить approved correction с audit и защитой от stale writes.

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

Template ids должны быть достаточно стабильными для correction и audit:

- `regex`: использовать rule id, если он есть (`rx.<uuid>`), и namespaced owner/intent в MCP.
- `rasa`: деривировать deterministic id из owner, intent, example text и entity annotations.
- `neural`: деривировать из owner, intent, masked text и label.
- `lookup`: деривировать из registry namespace, entity name, snapshot id и value-set fingerprint.

Corrections — это patches к существующим templates, а не raw file writes:

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

1. `list/get templates`: собрать current ids, owners, fingerprints и editable fields.
2. `propose patch`: LLM ссылается на existing `template_id` или явно просит create new template.
3. `preview diff`: authoring service проверяет owner, capability, schema, duplicates и `base_fingerprint`.
4. `operator approval`: UI показывает before/after, affected intent/action и expected pipeline impact.
5. `apply`: запись только через scenario/skill training APIs, затем audit event и rollback pointer.
6. `verify`: запуск `nlu.check_phrase` и optional golden phrase regression checks.

Если `base_fingerprint` уже не совпадает, patch отклоняется как stale, а UI должен обновить template inventory.

## Multi-engine teacher output

Чтобы первая версия Teacher не была зашита под один NLU engine, LLM должна возвращать bundle шаблонов для всех релевантных стадий.
Runtime на первом этапе применяет только поддерживаемую и безопасную часть.

Предлагаемая форма bundle:

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

Начальная apply policy:

- `regex`: применять только после явного подтверждения оператора и никогда не перезаписывать существующие rules.
- `rasa`: сохранять examples и lookup references в scenario/skill training content.
- `neural`: сохранять labels/masked examples как будущую training metadata; не менять inference behavior, пока neural stage явно не включен.
- `lookups`: генерировать из live desktop registry snapshots, а не из hardcoded examples вроде `member-1`.

## Apply

Apply can be triggered from UI or programmatically:

- apply a proposed dataset revision:
  - `nlp.teacher.revision.apply { revision_id, intent, examples[], slots }`
- apply a teacher candidate:
  - `nlp.teacher.candidate.apply { candidate_id, target? }`
  - for `regex_rule` candidates the runtime delegates to `nlp.teacher.regex_rule.apply { intent, pattern, target? }`

## Where regex rules are stored

The teacher does not “bake” regexes into the hub code. A rule is stored as data owned by a workspace artifact:

- **Skill-owned** (preferred): `.adaos/workspace/skills/<skill>/skill.yaml` → `nlu.regex_rules[]`
- **Scenario-owned**: `.adaos/workspace/scenarios/<scenario>/scenario.json` → `nlu.regex_rules[]`
- **Legacy runtime cache**: mirrored into YJS `data.nlu.regex_rules[]` so it starts matching immediately after Apply.

Every rule has a stable identity: `id="rx.<uuid>"`.

## Target selection (skill vs scenario)

When the teacher proposes a regex rule, it should also propose a storage target:

- Prefer the skill that actually handles the intent (derived from scenario intent `callSkill` actions + skill `events.subscribe`).
- If the intent triggers host/system behavior (`callHost`), the target is usually the scenario.

Apply supports a UI override (“Apply to Scenario”), in addition to an LLM-suggested target.

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
- the next time the same utterance is sent, `nlu.pipeline` should resolve it as `via="regex.dynamic"` without calling the LLM

## Roadmap

### Phase 0 - Полировка текущего baseline

- Сохранить рабочий `regex -> Rasa -> fallback/teacher` через event bus.
- Сохранить low-confidence fallback в `nlp.intent.not_obtained`.
- Не включать service skills в пользовательский NLU fingerprint, если у них нет training metadata.
- Держать smoke coverage для `[homepoint] Voice -> Rasa -> desktop.modal.open`.

### Phase 1 - Trace и dry-run foundation

- Добавить structured `nlu.trace` для каждой фразы.
- Добавить dry-run контракт `nlu.check_phrase` в service/API/MCP.
- Показывать stage decisions, confidence, ranking, entities и action preview.

### Phase 2 - MCP token и descriptor context

- Добавить **Issue token** в MCP Server modal.
- Выпускать target-scoped Root MCP session leases с capability profile `NLUTeacherAuthor`.
- Публиковать NLU pipeline, skill tool, scenario action и desktop registry descriptors через Root MCP.
- Публиковать current NLU template inventory с `template_id`, owner, status, fingerprint и provenance.

### Phase 3 - Полезный Teacher UI

- Добавить поле Check phrase.
- Показать intent ranking/entities/action preview.
- Показывать existing templates, релевантные phrase/intent, и давать выбрать template для correction.
- Добавить действия Правильно/Исправить.
- Сохранять curated examples в scenario/skill training content.

### Phase 4 - Multi-engine template application

- Принимать Teacher template bundles для regex, Rasa, neural и lookup metadata.
- Принимать correction patches к existing `template_id` с `base_fingerprint` stale-write protection.
- Применять только поддерживаемую часть безопасно.
- Оставить regex deterministic и data-owned.
- Кормить Rasa фактическими lookup values из desktop registry.

### Phase 5 - Feedback и promotion

- Собирать статистику по phrase, intent, stage, confidence и operator feedback.
- Продвигать полезные examples в training sets.
- Настраивать confidence thresholds по observed misses и false accepts.
- Добавить rollout/rollback controls для neural и Rasa model updates.
