# Context Compression Layer

This document defines the target AdaOS context compression architecture for
LLM-facing development, Builder, NLU Teacher, Root MCP, SDK, ABI, and future
resource-catalog workflows.

The core rule is:

- keep machine contracts strict and lossless: JSON, JSON Schema, MCP envelopes,
  ABI files, and `structuredContent`
- expose compact model views for LLM reading: summaries, aliases, cards,
  table-like rows, JSONL, and optionally TOON
- use drill-down tools for details instead of placing every descriptor in the
  prompt
- validate model outputs against the canonical JSON/ABI contracts

This layer does not replace Root MCP, JSON-RPC, JSON Schema, or AdaOS ABI. It is
an LLM-facing projection over those contracts.

## Current Implementation Baseline

AdaOS already has several pieces of this architecture:

- Root MCP Foundation exists under `src/adaos/services/root_mcp/`.
- Root MCP publishes typed descriptors for architecture, SDK metadata,
  templates, public registries, schemas, Builder task/draft schemas, NLU Teacher,
  and Skill Factory schemas.
- `AdaOSDevPlane`, `NLUAuthoringPlane`, `ProfileOpsPlane`, and
  `SkillFactoryTaskPlane` are registered as Root MCP planes.
- `builder.get_context` returns a compact Builder context bundle with descriptor
  summaries, NLU authoring context, named entities, runtime status, redaction
  policy, and next-tool hints.
- `nlu_authoring.get_context` exposes NLU Teacher context: named entities,
  action surfaces, voice capabilities, voice affordances, process state,
  developer hints, and read-only boundaries.
- `adaos.sdk.conversation.context` and conversation context packets provide
  thread-aware, budgeted context over recent messages, segments, scoped memory,
  evidence refs, token estimates, and diagnostics.
- SDK export already has a compact line-oriented shape in
  `src/adaos/sdk/descriptions/mini.jsonl`.
- The Codex Root MCP bridge returns both JSON text and `structuredContent`.
  This gives us a clean place to add compact LLM text without breaking MCP
  clients that rely on structured JSON.

The missing part is a consistent model-view format, shared token accounting,
and golden tests that prove compact context preserves or improves model task
quality.

## Architecture

### 1. Contract Layers

AdaOS context should be split into three contract layers:

```text
Canonical data
  JSON, JSON Schema, ABI, Root MCP envelopes, persisted descriptors

Model overview
  compact summaries, cards, JSONL rows, TOON tables, aliases, hashes

Model drill-down
  selected descriptor payloads, selected ABI/schema fragments, selected tool
  schemas, selected logs or evidence refs
```

Canonical data is used for storage, API boundaries, validation, and
interoperability. Model overview is used for breadth. Model drill-down is used
for depth.

LLM outputs that may affect state must return validated JSON, not TOON. TOON is
only an input/context optimization unless a specific local parser and retry
policy are implemented for one workflow.

### 2. Root MCP As The Context Front Door

Root MCP should be the default entry point for agent-facing context.

The current and target Root MCP context flow is:

```text
LLM client
  -> Root MCP tool
  -> plane-specific descriptor or runtime handler
  -> canonical JSON result
  -> compact model text projection
  -> structuredContent remains canonical JSON
```

This preserves the MCP machine contract while allowing `content[].text` to be
optimized for LLM attention and token cost.

The first surfaces that should support compact model views are:

- `builder.get_context`
- `nlu_authoring.get_context`
- `sdk.describe_surface`
- `adaos_dev.get_sdk_metadata`
- `adaos_dev.get_template_catalog`
- `adaos_dev.get_public_skill_registry`
- `adaos_dev.get_public_scenario_registry`
- `adaos_dev.get_named_entity_registry`
- `development.list_descriptor_sets`
- `tools/list` in the local Codex bridge

### 3. Overview Before Details

Every large descriptor family should expose an overview before full payloads:

- SDK methods: name, kind, summary, stability, side effects, required capability
- Root MCP tools: id, plane, summary, input digest, side effects, capability
- ABI schemas: schema id, title, stable fields, required fields, version,
  descriptor id
- skills/scenarios: id, title, version, owner, public status, NLU/LLM hints
  digest
- NLU action surfaces: id, owner, intent, slots, side-effect class, preview
  method, fingerprint
- voice capabilities and affordances: id, label digest, owner, activation path,
  side-effect class, visibility, fingerprint
- named entities: canonical ref, kind, display label, aliases digest, locale,
  scope, owner, fingerprint
- logs and traces: bounded refs, stage/status/timestamp, error class, linked
  event ids

Full JSON payloads should be fetched only after the model or deterministic
selector chooses ids.

### 4. Alias Registry

Aliases let prompts reference stable artifacts without embedding their full
content.

Target alias families:

- `ARCH.*`: architecture descriptors
- `SDK.*`: SDK method groups and surface descriptors
- `ABI.*`: schemas and ABI descriptors
- `TPL.*`: templates
- `SKILL.*`: skill manifests and hint cards
- `SCN.*`: scenario manifests and hint cards
- `NLU.*`: NLU action surfaces, templates, and training targets
- `NE.*`: named entity registries
- `CTX.*`: context packet snapshots
- `TRACE.*`: trace/log evidence refs
- `TASK.*`: Builder and Skill Factory task descriptors

An alias must include at least:

```json
{
  "alias": "SDK.manage.skills",
  "descriptor_id": "sdk_metadata",
  "path": "tools[manage.skills.*]",
  "hash": "sha256:...",
  "level": "mini",
  "fresh_until": "2026-07-15T12:00:00+00:00"
}
```

The hash is used to make hallucinated or stale references detectable.

### 5. Cards

Cards are deterministic summaries that can be concatenated into prompts.

Card rules:

- stable field order
- no secrets or bearer/session tokens
- no unbounded logs or transcript dumps
- short rows and compact language
- include ids and hashes when the model may need drill-down
- include explicit side-effect and authority boundaries
- keep examples sparse and high-signal

Example SDK card:

```text
SDK.manage.skills @ sdk_metadata#8f20c1a4
- tools: manage.skills.list, install, uninstall
- writes: install/uninstall require request_id and dry_run support
- stable: list, install, uninstall
- drilldown: get_descriptor("sdk_metadata", level="std")
```

Example NLU action card:

```text
NLU.actions.desktop @ nlu_authoring.get_context#1d0a7f92
- preferred order: capability -> affordance -> descriptor_fix -> development_task
- preview before mutation; no dispatch from context reads
- slots: modal_id, scenario_id, skill_id, node_ref, webspace_id
```

### 6. Delta Prompts

Repeated Builder and repair turns should send deltas instead of re-sending the
same context.

Delta prompts should include:

- base context hash
- changed aliases and descriptor ids
- selected previous decision
- current user request
- exact action requested from the model
- output schema id

Example:

```text
BASE: CTX.builder.thread#5c2f9a1b
DELTA:
- SCN.shopping_list: webui preview changed table -> card_list
- ABI.webui.v1: unchanged
- USER: "Show the result as cards."
ACTION: propose webui patch only
OUTPUT_SCHEMA: adaos.builder.patch_plan.v1
```

### 7. Budgeted Drill-Down

For any task with a large context pool, AdaOS should use the same budgeted
drill-down algorithm:

1. Build the fixed head: role, safety, authority boundaries, output contract.
2. Retrieve `k` overview rows from Root MCP descriptors, local indexes, or
   runtime snapshots.
3. Fit overview and detail counts against token budgets.
4. Ask the model or deterministic ranker to pick `m` detail ids.
5. Fetch only selected details.
6. Ask for a validated plan, patch, or decision.
7. Record context hash, prompt hash, selected ids, token estimate, and
   diagnostics.

Pseudo-code:

```py
head = build_head(task, output_schema)
overview = root_mcp.overview(task, limit=k, format="toon")
selected = pick_details(task, overview, max_items=m)
details = root_mcp.details(selected.ids, level="std")
prompt = pack(head, overview, details, budget=budget)
result = llm(prompt)
validated = validate_json(result, schema=output_schema)
```

## Compact Formats

### JSON

Use JSON for canonical contracts, tool calls, structured outputs, persisted
fixtures, ABI, schema validation, and `structuredContent`.

### Minified JSON

Use minified JSON when a full exact object is needed in prompt text and no
compact projection is available.

### JSONL

Use JSONL for append-only or line-oriented overview rows, especially when each
row is self-contained and keys are already short.

AdaOS already has this shape in `src/adaos/sdk/descriptions/mini.jsonl`.

### TOON

TOON can be used as an optional compact model-view format for uniform arrays
and table-like descriptor data.

Canonical reference: <https://github.com/toon-format/toon>

Use TOON when:

- rows are mostly uniform
- repeated keys dominate token cost
- the data is LLM input, not a machine contract
- a canonical JSON source remains available
- the response also includes ids/hashes for drill-down

Avoid TOON when:

- data is deeply nested and irregular
- the target consumer is a normal API client
- the output must be validated directly by JSON Schema
- the payload is small enough that format conversion adds more risk than value
- exact escaping or binary-safe transport matters

Recommended TOON examples:

SDK methods:

```toon
sdk_methods[4]{name,kind,summary,stability,side_effects}:
  manage.skills.list,tool,list installed skills with registry metadata,stable,none
  manage.skills.install,tool,install a skill from catalog or git,stable,write
  manage.scenarios.create,tool,create a scenario from a template,experimental,write
  resources.request,tool,create a resource ticket for operators,experimental,write
```

Root MCP tool overview:

```toon
mcp_tools[3]{id,plane,summary,capability,side_effects}:
  builder.get_context,adaos_dev,compact Builder context,development.read.descriptors,none
  nlu_authoring.get_context,nlu_authoring,NLU authoring context,development.read.descriptors,none
  nlu_authoring.check_phrase,nlu_authoring,side-effect-free phrase probe,development.read.descriptors,none
```

NLU actions:

```toon
actions[3]{id,owner,intent,slots,side_effect,preview,fingerprint}:
  desktop.open_modal,system,desktop.open_modal,modal_id,ui,yes,fp:a1
  skill.weather.get,skill:weather,get_weather,city,network,yes,fp:b2
  builder.create_draft,skill:builder,builder.create_draft,kind|idea,dev_write,review,fp:c3
```

Named entities:

```toon
entities[3]{ref,kind,label,aliases,scope,owner}:
  device:browser:main,device.browser,Main browser,desktop|browser,webspace,system
  skill:weather,skill,Weather,weather|forecast,workspace,registry
  scenario:desktop,scenario,Desktop,home|workspace,workspace,registry
```

## Prompt Packing Policy

Every LLM request should separate:

- fixed instructions
- authority and side-effect boundaries
- compact overview
- selected details
- task
- output contract

Suggested section order:

```text
[ROLE]
[AUTHORITY]
[OUTPUT_CONTRACT]
[OVERVIEW]
[DETAILS]
[TASK]
[DIAGNOSTICS]
```

The output contract should name a JSON schema or strict object shape. Even when
overview/details are TOON, the output should normally be JSON.

## MCP Bridge Projection Policy

The Codex Root MCP bridge should continue returning canonical JSON
`structuredContent`.

For `content[].text`, it may return:

- pretty JSON for debugging
- minified JSON for exact compact objects
- JSONL for row-oriented overviews
- TOON for uniform arrays
- mixed Markdown cards for human-readable summaries

Target response shape:

```json
{
  "content": [
    {
      "type": "text",
      "text": "format: toon\nsdk_methods[2]{name,summary}:\n  ..."
    }
  ],
  "structuredContent": {
    "descriptor": {
      "payload": {}
    }
  },
  "meta": {
    "model_text_format": "toon",
    "canonical_format": "json",
    "token_estimate": 420
  }
}
```

This keeps MCP clients deterministic while giving the model a smaller and
clearer read surface.

## Safety And Governance

Context compression must not weaken authority boundaries.

Rules:

- never include bearer tokens, session tokens, secrets, raw credentials, or
  unrestricted filesystem paths in compact views
- include capability and side-effect class in every action/tool overview
- include freshness/fingerprint/hash when context can go stale
- distinguish root-local descriptors from live target evidence
- include policy denials and unavailable retrieval channels in diagnostics
- never let a compact row become the only source of truth for a mutation
- validate all mutation proposals against canonical JSON/ABI contracts
- log context hash, prompt hash, selected aliases, and descriptor versions

## Evaluation

Context compression should be measured on both cost and behavior.

Required metrics:

- token estimate before/after compact projection
- selected detail count
- budget exhaustion rate
- context utilization
- model tool/action selection accuracy
- schema validation pass rate
- repair/fallback rate
- latency impact
- hallucinated id rate
- stale descriptor use rate

Existing conversation eval metrics already track context packet count, token
estimate p95, utilization, and budget exhaustion. Those should be extended to
cover compact MCP/descriptor projections.

## Roadmap

Priority labels:

- `[must]`: required for a coherent production direction
- `[should]`: important hardening or quality work after the main slice
- `[could]`: useful but not required for the first successful rollout
- `[deferred]`: intentionally postponed until the surrounding surface matures

### Phase 0. Architecture And Boundaries

- [x] `[must]` Define Root MCP as the agent-facing context front door.
- [x] `[must]` Split Root MCP foundation from plane-specific surfaces.
- [x] `[must]` Establish root descriptor cache as the source for pseudo-static
  SDK, ABI, architecture, template, and registry descriptors.
- [x] `[must]` Establish `builder.get_context` as a compact Builder context
  bundle.
- [x] `[must]` Establish thread-aware conversation context packets with token
  estimates, budgets, evidence refs, diagnostics, messages, segments, and
  scoped memory.
- [ ] `[must]` Adopt this document as the canonical context compression target
  for Builder, NLU Teacher, SDK, ABI, and Root MCP model-facing context.

### Phase 1. Canonical Overview And Drill-Down Contracts

- [x] `[must]` Publish descriptor ids for architecture, SDK metadata,
  templates, public registries, skill/scenario schemas, Builder schemas, NLU
  Teacher schema, and Skill Factory schemas.
- [x] `[must]` Expose `mini`, `std`, and `rich` descriptor levels for SDK and
  Builder context consumers.
- [x] `[must]` Keep descriptor payloads omitted by default in
  `builder.get_context`, with `include_payloads` available for trusted local
  debugging.
- [ ] `[must]` Define a common overview-row schema for SDK methods, MCP tools,
  ABI schemas, skills, scenarios, NLU actions, voice capabilities, voice
  affordances, named entities, templates, and training targets.
- [ ] `[must]` Add drill-down ids and hashes to every compact overview row.
- [ ] `[should]` Add deterministic rankers for overview selection before
  asking the model to pick detail ids.
- [ ] `[could]` Add vector search over descriptor cards and resource overviews.

### Phase 2. Compact Model Text Formats

- [x] `[must]` Keep canonical MCP `structuredContent` as JSON.
- [x] `[must]` Keep ABI and JSON Schema as canonical validation contracts.
- [x] `[should]` Maintain compact SDK JSONL export for minimal SDK overview.
- [ ] `[must]` Add `model_text_format` support to the Codex Root MCP bridge for
  selected tools, initially `json`, `min_json`, `jsonl`, and `toon`.
- [ ] `[must]` Add a TOON encoder for uniform overview arrays sourced from
  canonical JSON.
- [ ] `[must]` Record `canonical_format`, `model_text_format`, and
  `token_estimate` in bridge/tool metadata.
- [ ] `[should]` Add a feature flag or request argument so compact text can be
  rolled out per tool without changing default behavior for all clients.
- [ ] `[should]` Add tests proving `structuredContent` stays unchanged while
  `content[].text` changes format.
- [ ] `[could]` Add Markdown card output for human-inspection flows where TOON
  is too terse.

### Phase 3. Builder Context Compression

- [x] `[must]` Route Builder descriptive context through Root MCP descriptors
  and `builder.get_context`.
- [x] `[must]` Include redaction policy and authoring boundaries in Builder
  context.
- [x] `[must]` Add thread-aware Builder context packet plumbing.
- [x] `[must]` Move long Builder LLM transformations to Root-managed async jobs
  and validate returned JSON before materialization.
- [ ] `[must]` Make `builder_skill` consume context packets, retrieved evidence
  refs, scoped memory, and Pending Actions instead of raw UI chat state across
  all Builder entry surfaces.
- [ ] `[must]` Add Builder prompt pack sections for role, authority, compact
  overview, selected details, task, and output contract.
- [ ] `[must]` Add delta prompt support for follow-up Builder turns.
- [ ] `[should]` Add compact TOON/JSONL views for Builder descriptor summaries
  and NLU action surface overviews.
- [ ] `[should]` Extend Builder golden fixtures to validate compact context
  behavior for clarification, validation failure, approval, rejection, and
  repair turns.
- [ ] `[could]` Add a developer UI preview of the exact compact context pack
  sent to the model.

### Phase 4. NLU Teacher Context Compression

- [x] `[must]` Expose `nlu_authoring.get_context` through Root MCP.
- [x] `[must]` Expose `nlu_authoring.check_phrase`, trace, dialog context,
  recent failures, template inventory, training targets, and patch preview
  surfaces.
- [x] `[must]` Document decision order: existing voice capability, existing
  affordance, descriptor fix, then development task.
- [x] `[must]` Cache heavy Root MCP evidence for NLU Teacher for a short TTL.
- [ ] `[must]` Encode `available_actions`, `voice_capabilities`,
  `voice_affordances`, named entities, templates, and training targets as
  compact overview rows.
- [ ] `[must]` Include fingerprints and freshness metadata in compact NLU rows.
- [ ] `[must]` Add tests that compact NLU context still chooses the published
  capability or affordance before creating a development task.
- [ ] `[should]` Add TOON projections for uniform NLU action/capability rows.
- [ ] `[should]` Add compact trace views for recent failures and dialog context.
- [ ] `[could]` Add locale-specific compact label rows for multilingual NLU
  authoring.

### Phase 5. SDK, ABI, And Skill Surface Compression

- [x] `[must]` Export SDK descriptors from the descriptor build pipeline.
- [x] `[must]` Publish skill, scenario, Builder, NLU Teacher, and Skill Factory
  schemas through Root descriptors.
- [x] `[should]` Keep `src/adaos/sdk/descriptions/mini.jsonl` as an existing
  compact SDK overview artifact.
- [ ] `[must]` Define SDK method overview rows with name, module, summary,
  stability, side effects, required args digest, and schema id.
- [ ] `[must]` Define ABI schema overview rows with schema id, title, version,
  required fields digest, stability, and drill-down descriptor id.
- [ ] `[must]` Define skill/scenario overview rows with id, version, NLU/LLM
  hints digest, tools/events digest, schemas, and owner.
- [ ] `[should]` Add TOON/JSONL projections for SDK methods and ABI schema
  catalogs.
- [ ] `[should]` Add schema-card generation that summarizes large JSON Schemas
  without losing required fields and enum constraints.
- [ ] `[could]` Add examples/anti-pattern snippets only for selected detail
  cards, not every overview row.

### Phase 6. Metrics And Golden Evaluation

- [x] `[must]` Track context packet token estimates, budget summaries, and
  budget exhaustion in conversation evals.
- [ ] `[must]` Add compact-projection token metrics for Root MCP bridge outputs.
- [ ] `[must]` Add golden comparisons for JSON text vs compact text on the same
  `structuredContent`.
- [ ] `[must]` Track model action/tool selection accuracy for compact NLU and
  Builder contexts.
- [ ] `[should]` Track hallucinated ids, stale fingerprints, and invalid
  drill-down selections.
- [ ] `[should]` Add regression thresholds for token reduction and validation
  pass rate.
- [ ] `[could]` Add model-specific tokenizer support beyond the current stable
  local estimate.

### Phase 7. Resource Catalog And Broader Retrieval

- [ ] `[must]` Define the resource overview/detail model for tabular, API, bus,
  file, skill, scenario, and operational resources.
- [ ] `[must]` Add `catalog.search`, `catalog.overview`, and `catalog.details`
  contracts or map them to Root MCP descriptor tools.
- [ ] `[must]` Add RBAC and redaction rules for resource details.
- [ ] `[should]` Add schema digest, capability digest, cost hints, quality
  hints, snippets, and anti-pattern cards.
- [ ] `[should]` Add CI verification for catalog URIs, schema digests, hashes,
  and freshness.
- [ ] `[could]` Add vector indexes for resource cards and descriptor cards.
- [ ] `[deferred]` Add broad public/resource marketplace context until registry
  ownership and publication policy are stable.

### Phase 8. Runtime And Operations Context

- [x] `[must]` Expose managed target status, runtime summaries, operational
  surfaces, session leases, and initial subnet diagnostics through Root MCP.
- [x] `[must]` Distinguish descriptive root-cached context from live target
  operational reads.
- [ ] `[must]` Add compact operational overview rows for status, healthchecks,
  route/backlog/ack, YJS pressure, profiler sessions, incidents, and log refs.
- [ ] `[should]` Add compact subnet timeline views that link audit, reports,
  runtime summaries, and bounded logs.
- [ ] `[should]` Add degraded-channel diagnostics to compact views so the model
  sees when evidence is incomplete.
- [ ] `[could]` Add TOON projections for operational rows where fields are
  uniform.
- [ ] `[deferred]` Use compact operation context for autonomous operational
  writes until policy, approvals, and audit maturity are proven.

## Definition Of Done For The First Useful Slice

The first production-useful context compression slice is complete when:

- `structuredContent` remains canonical JSON for all changed MCP bridge tools.
- `content[].text` supports at least JSON and one compact format for
  `sdk.describe_surface`, `get_sdk_metadata(level=mini)`,
  `builder.get_context`, and `nlu_authoring.get_context`.
- compact outputs include ids, fingerprints/hashes, freshness, side-effect
  class, and drill-down hints where relevant.
- token estimates are recorded for compact and non-compact projections.
- golden tests prove compact context does not regress tool/action selection.
- mutation-producing workflows still require validated JSON output.
- docs and examples clearly state that TOON is an LLM input projection, not the
  canonical AdaOS wire format.
