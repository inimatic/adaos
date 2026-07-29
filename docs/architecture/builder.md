# AdaOS Builder

Status: target architecture and terminology anchor.

This page owns Builder's role and artifact-creation boundary. The broader
human-signal -> Issue -> Builder -> release -> runtime-evidence loop is defined
by [Governed Evolution](governed-evolution.md); its cross-domain proof order is
tracked in the [Governed Evolution Roadmap](governed-evolution-roadmap.md).

Implementation alignment (2026-07-24): the single-user Builder delivery path
now uses immutable source checkpoints, component packages, a dependency-locked
project release, an isolated candidate trial, explicit acceptance, and
transactional Workspace activation. Builder assigns a distinct `change_id` to
every Automation iteration and never automatically repeats an uncertain
state-changing phase. See the
[Artifact Source, Package, and Activation Architecture](artifact-source-package-activation.md)
and its [local evidence](artifact-pipeline-local-evidence-2026-07-24.md).

The chat-first product model, canonical `Issue -> Change -> Run -> Revision ->
Release` terminology, semantic UI changes, context packets, channel capability
boundary, and future collaboration seams are defined in
[Builder Conversational Development Architecture](builder-conversational-development.md).
That document refines the current `change_set` and per-turn Builder Change
compatibility terminology; this page continues to own the active implementation
pipeline until migration is complete.

AdaOS Builder is the role and workflow that turns an idea into governed AdaOS
artifacts: skills, scenarios, manifests, UI descriptors, NLU hints, tests, and
runtime-ready changes.

The role is intentionally not tied to one executor. A Builder may be:

- a human developer using AdaOS tools
- an AI-assisted agent using Root MCP and local repository context
- a human-in-the-loop workflow where AI drafts and humans review, approve, or
  redirect

The product phrase is:

```text
I have an idea. Let's build it.
```

The architectural term is `Builder`. Terms such as `LLM programmer` should be
treated as historical or implementation-specific wording and should point back
to this page.

## Purpose

AdaOS should let any person become a creator by giving them a governed path
from intent to working capability.

The Builder does not bypass AdaOS. It uses AdaOS contracts to create changes
that can be inspected, validated, installed, activated, observed, repaired, and
rolled back.

Core invariant:

- humans and AI can propose capability changes
- AdaOS owns deterministic validation, permission gates, runtime activation,
  observability, and rollback
- risky or ambiguous changes stay reviewable before they become durable runtime
  behavior

## Scope

The Builder owns the development path for:

- new skills
- updates to existing skills
- new scenarios
- updates to scenario flows, bindings, and desktop surfaces
- `webui.json` UI descriptors and browser-facing data contracts
- `skill.yaml` and canonical `scenario.yaml` metadata; `scenario.json` is a
  derived compatibility projection and is never the version authority
- NLU hints, examples, aliases, and descriptor fixes
- tests, smoke checks, runtime validation evidence, and release notes

The Builder does not own:

- direct runtime command dispatch on behalf of the user
- hidden mutation of Yjs, registries, or skill runtime state
- silent model retraining
- direct bypass of skill/scenario publication, activation, or policy gates
- operational incident handling outside the development/repair loop

Those surfaces are handled by the deterministic runtime, NLU Teacher,
Root MCP operational planes, Infrascope, and supervisor/runtime governance.

## Builder Pipeline

The target pipeline is a vertical slice across existing AdaOS architecture:

1. **Intent capture**: a person states an idea, correction, missing capability,
   or desired workflow.
2. **Context read**: Builder reads architecture, SDK, schema, template,
   registry, named-entity, current scenario, and runtime evidence through
   governed descriptors.
3. **Capability classification**: Builder decides whether the change belongs to
   a skill, scenario, UI descriptor, NLU overlay, entity alias, descriptor fix,
   or new development task.
4. **Design plan**: Builder records the artifact plan, data route plan,
   side-effect class, permissions, runtime lifecycle needs, and test strategy.
5. **Draft generation**: Builder creates or edits workspace artifacts through
   the core `adaos dev skill|scenario create` service contract and ordinary
   repository files and templates. Chat and API entrypoints do not copy
   templates through a separate Builder-only path.
6. **Static validation**: AdaOS validates schemas, manifests, route plans,
   imports, handler boundaries, and unsafe runtime patterns.
7. **VCS checkpoint**: after a complete validated LLM result is materialized,
   Builder invokes the core `adaos dev skill|scenario push` service contract.
   The LLM `comment` is normalized into the Forge commit message, and the
   returned commit SHA is attached to the local revision evidence and the
   turn's Builder Change.
8. **Preview**: AdaOS runs phrase probes, action previews, UI/materialization
   previews, and install/test dry-runs where available.
9. **Review gate**: a human, policy rule, or narrower auto-apply profile
   approves, rejects, or redirects the candidate.
10. **Prepare/install**: AdaOS uses skill/scenario lifecycle commands and
   runtime slots rather than hot-patching live behavior.
11. **Activate**: AdaOS activates the prepared runtime and records rollback
    evidence.
12. **Observe and repair**: guard, quarantine, NLU Teacher, status, and
    runtime diagnostics feed new Builder tasks when the design needs repair.

## Prototype And Automation Workflow

Builder projects every user request onto one bounded
`adaos.builder.change_set.v1`. A change set contains the original intent,
individually testable issue items, acceptance criteria, durable Builder Change
references, and one execution route. Follow-up remarks normally extend the
active set; they do not create a new set unless the previous set is terminal or
the user explicitly starts unrelated work. This is a project-local execution
contract, not the deferred global/multi-user Issue registry.

Issue items use two execution lanes:

- `prototype`: interface, layout, content hierarchy, presentation, and other
  safely mockable interaction requirements;
- `automation`: behavior, persistence, integration, dependency, migration,
  runtime, and test requirements.

If any unresolved item belongs to `prototype`, the route is
`prototype_first`. The built-in interactive LLM changes only the deterministic
Prototype, and Automation cannot start until that gate is approved. A purely
functional request may use `automation_direct`; Builder records the set and
requires an explicit implementation brief before isolated Codex starts. A
heuristic classification is never sufficient to launch Codex by itself.

Builder still has one mutable process at a time. `Prototype` and `Automation`
are the only possible values of `workflow.active_phase`; `Publication` is an
immutable release snapshot and is never an active editing phase. The
authoritative persisted contract is `adaos.builder.workflow.v1` in the DEV
project's `prompt_state.json`, with the active change set embedded in that
projection.

Lifecycle selection, workflow state, and Preview are independent:

- selecting a Lifecycle node only changes local Builder navigation;
- `workflow.active_phase` controls which conversation and files may be edited;
- `preview_target` controls what the paired Preview renders and may point to a
  read-only snapshot that is different from the active process.

The supported transitions are:

| Change-set gate | Action | Next gate | Durable effect |
| --- | --- | --- | --- |
| Prototype | approve/stabilize Prototype | Automation | approved revision becomes immutable requirement input |
| Automation | isolated Codex completes | Trial | result, checks, source Prototype, and member changes are checkpointed |
| Trial | reject or request changes | Prototype or Automation | candidate stays non-promotable and the affected issue items reopen |
| Trial | accept | Publication | accepted trial evidence admits stable promotion |
| Publication | publish | Complete | immutable release and publication evidence reference the change set |
| Complete/Automation | return result to Prototype | Prototype | built-in LLM derives a new safe Prototype revision for a later set |

Returning to Prototype does not thaw or overwrite the completed Automation.
The local realization worker receives the retained Automation as immutable
input, may edit only scenario-facing declarative prototype files, and is
rejected if it changes the companion skill or the retained snapshot. Real
tool, service, credential, device, external-network, and production-data
bindings are removed from the new Prototype and replaced with bounded local
state or mock data. A later handoff receives both that Prototype as the current
requirement and the retained Automation as its previous implementation.
It also receives the currently installed Workspace Publication as a separate
immutable implementation baseline. Companion skills are resolved from the
current Prototype, retained Automation, and current Publication as a union, so
a safely disconnected Prototype cannot silently erase established functional
dependencies. The Publication attachment is read-only, is rejected if Codex
changes it, and is stripped before DEV activation and package construction.
If adaptation fails, Builder records the adaptation diagnostic and restores
the retained Automation to `completed`; the failed side process never
invalidates the last working implementation or Publication snapshot.

Lifecycle projects dependency, not three independent stage buckets:

```text
Prototype revision
  -> Automation result produced from that revision
       -> Publication produced from that Automation result
```

Only the retained current Automation and current Publication are previewable.
Legacy publication evidence without provenance is shown under an explicitly
inferred historical Automation node instead of being silently attributed to
the current result. Only one Automation snapshot is retained under Builder
runtime state; a new completed Automation replaces it. It is not copied into
the published scenario. Historical implementation recovery remains a
Forge/VCS concern.

The scenario version source of truth is `scenario.yaml`. Compatibility
`scenario.json` content must not override its lifecycle or publication version.

## Relationship To Skills

Skills remain the reusable capability unit.

Builder-created skills must follow:

- [Skills](../skills.md)
- [Skill Runtime Lifecycle](../skill_runtime.md)
- [Builder-Safe Skill Development Guide](../guides/llm-skill-development.md)
- [Skill Projection Runtime SDK](skill-projection-runtime-sdk.md)
- [Runtime Guarding](runtime-guarding.md)

The Builder must make browser-facing data routes explicit in `skill.yaml`
before choosing Yjs, streams, tools/details, skill-local storage, or disk
diagnostics. Runtime guards may warn, throttle, block, or quarantine unsafe
routes, but they should not silently redesign a skill.

## Builder Skill SDK Boundary

Builder and Prompt IDE skills are application code. They consume stable
capabilities through `adaos.sdk` and do not import or construct
`adaos.services` implementations:

```text
Builder scenario UI -> skill tools -> adaos.sdk -> adaos.services -> adapters
```

The functional replacement control is Builder UI revision `032`, derived from
the approved three-pane prototype `029` while preserving user-generated
revision `031`. `builder_sdk_control_skill` owns the
SDK-backed project, technical-specification, LLM, workflow, preview,
Automation, and Forge presentation adapters; `builder_skill` continues to own
the dialog stream and UI-revision restore operation. The detailed capability
mapping, migration checklist, and deferred granularity work live in
[Builder SDK Boundary](builder-sdk-boundary.md).

Project selection and preview materialization follow the explicit topology,
event, reconcile, catalog, and process-isolation contracts in
[Builder Preview Runtime](builder-preview-runtime.md). A preview webspace ID is
opaque; `-dev` is accepted only while migrating an existing binding.

## Relationship To Scenarios

Scenarios remain the orchestration and desktop/workflow unit.

Builder-created scenarios must follow:

- [Scenarios](../scenarios.md)
- [Builder-Safe Scenario Development Guide](../guides/builder-scenario-development.md)
- [Skill Activation and Scenario Binding](skill-activation-and-scenario-binding.md)
- [Webspace Scenario Pointer/Projection Roadmap](webspace-scenario-pointer-projection-roadmap.md)
- [WebIO](../interfaces/webio.md)

The Builder should decide whether an idea is:

- a reusable skill capability
- a scenario flow over existing skills
- a UI/catalog binding
- an NLU/action descriptor improvement
- a missing capability that needs a new skill or scenario artifact

## Relationship To NLU Teacher

NLU Teacher handles utterance understanding, correction, and teachable gaps.
Builder handles capability creation and artifact changes.

When NLU Teacher sees a missing capability, it should create a
`development_task` candidate for Builder instead of inventing fake intents or
pretending the action exists.

Relevant documents:

- [NLU Teacher LLM](../concepts/nlu-teacher-llm.md)
- [NLU Roadmap](../concepts/nlu-roadmap.md)
- [Named Entities and Canonical Naming](named-entities.md)

The handoff boundary is:

- `descriptor_fix`: improve existing skill/scenario/action descriptions,
  hints, examples, or slot schemas
- `development_task`: build or modify an AdaOS capability artifact
- `entity_alias`: update governed entity understanding, usually without new
  code

## Relationship To Root MCP

Root MCP is the Builder's governed machine-readable context and tool surface.
It is not the Builder itself.

The current read-only Builder context is exposed as `builder.get_context`.
It bundles descriptor provenance, architecture/SDK/template/registry summaries,
NLU authoring context, named entities, redaction policy, and no-write authoring
boundaries.

Builder reads from:

- `AdaOSDevPlane`: architecture, SDK metadata, schemas, template catalog,
  public skill/scenario registry, and named entities
- `NLUAuthoringPlane`: current action surface, phrase checks, traces, dialog
  context, training targets, templates, and patch previews

Relevant documents:

- [Root MCP Foundation](root-mcp-foundation.md)
- [Root MCP Roadmap](root-mcp-roadmap.md)

Root MCP should expose enough context for Builder to reason without scraping
the repository blindly. Writes through Root MCP must remain governed,
capability-scoped, audited, and previewable.

## Relationship To Root LLM Jobs

Builder must not depend on a single long synchronous HTTP request when asking
an LLM to transform UI or generate implementation artifacts. The current
runtime uses Root-managed asynchronous LLM jobs:

1. Builder submits a bounded Responses API payload to `POST /v1/llm/jobs`.
2. Root records the job in Redis with `request_id` idempotency, caller identity,
   model, status, attempts, and a TTL.
3. Root executes the upstream model request in the background, consumes typed
   provider SSE when the model supports it, and stores a bounded replayable
   progress journal plus either the complete response or a structured error.
4. If the submit response times out, the SDK first calls
   `POST /v1/llm/jobs/lookup` on the same root with the same payload. Root
   returns the existing job only when the request fingerprint matches; otherwise
   it reports `llm_request_id_conflict` with diagnostic fingerprint tags.
5. Builder polls `GET /v1/llm/jobs/{job_id}` on the same root base URL that
   accepted the job, forwarding new monotonic progress entries to one grouped
   chat card. Root push delivery may be added later without removing polling as
   the recovery path.
6. Builder stages complete semantic JSONL patches on a private copy and validates
   the reconstructed document against the Builder/webui contracts
   before writing `webui.json`, `ui_revisions/NNN.json`, and
   dev-webspace refresh events.

Builder also writes a small atomic terminal marker under the scenario-local
`llm_jobs/` directory before updating session memory. This journal is the
recovery authority when a worker succeeds or fails while the runtime session
projection is unavailable; stale in-memory `queued` entries cannot block later
requests indefinitely.

Each terminal Root job exposes bounded telemetry alongside the response:
queue/execution/total duration, provider response and request IDs, service tier,
input/cache/output/reasoning token counts, retry attempts, requested and used
tools, output item types, and MCP usage. Builder persists this summary under
`ui_revisions/NNN.json -> llm.telemetry` and copies the provider response ID and
service tier into `inference`. Raw prompts, credentials, and tool payloads are
not duplicated into telemetry. This is the primary evidence for separating
local preparation, Root queueing, provider generation, repair, validation, and
runtime materialization latency.

This makes OpenAI/read-timeout failures visible as job state instead of
breaking the Voice or Prompt IDE request path. The synchronous
`/v1/llm/response` endpoint remains a compatibility path for smaller calls,
but Builder-like long transformations should use jobs by default.

The same submit/poll contract is the preferred bridge for future remote skill
programming in isolated dev nodes: Root can later move the worker from the
backend process to NATS/dev-node execution without changing the Builder-facing
API.

## UI Prototyping LLM Contract

Long-running UI transformations follow the target
[Builder Streaming Patch Architecture](builder-streaming-patches.md). Provider
SSE, Root job progress, and semantic UI patches are separate protocols. The
active `webui.json` remains a complete, atomically promoted
`adaos.webui.v1` document; partial output is never rendered as canonical UI.

For rapid UI prototyping, Builder should treat the LLM as an adaptive
designer-programmer and treat AdaOS as the deterministic guardrail. The model is
allowed to reshape the declarative `adaos.webui.v1` UI inside the ABI boundary;
AdaOS owns schema validation, revision storage, review, safe apply, runtime
refresh, and rollback.

The current UI is starting material, not a fixed product contract. When a user
asks for a design, workflow, layout, copy, data, or prototype change, the LLM
should make a meaningful visible transformation rather than a rename-only,
duplicate-only, or no-op patch. It may change:

- field order, grouping, labels, helper text, defaults, options, validation, and
  field types
- layout pattern, areas, density, auxiliary panels, and widget placement
- display widgets such as tables, cards, lists, details, metrics, and JSON
  previews
- local prototype interactions through selectors, command bars, actions,
  `initialState`, and `visibleIf`
- static/mock data and examples that demonstrate the intended interface

An additive request must preserve unrelated behavior from the current UI.
Adding a command to an existing widget must not replace its selection,
navigation, or modal actions unless the user explicitly asks to remove or
replace them. For `ui.list`, `inputs.buttons` are per-item/card commands. The
single list-level Add command next to card search uses `inputs.addButton`,
`inputs.addButtonLabel`, and an `add` or `click:add` widget action.

Visual freedom does not make the functional control plane disposable. A
schema-valid prototype may replace layout, copy, and bounded mock presentation,
but every existing skill data source, stream, mutation action, Lifecycle
command, project kind, and governed confirmation remains a compatibility
contract unless an accepted issue explicitly removes it. Builder carries the
machine-readable `adaos.builder.functional_parity.v1` contract with the
scenario and rejects Prototype or Automation results that lose required
bindings, even when the resulting UI still renders successfully.

Builder self-development always uses a shadow scenario and an executable
reference revision. The active Builder is never used simultaneously as the
experimental prototype, the functional baseline, and the recovery tool. A
self-hosted change must pass deterministic parity, scenario validation, SDK
tests, and A/B browser rendering before it can replace `dev:builder`; Workspace
and stable Publication remain unchanged until the recovered DEV revision passes
Trial. Static/mock-only experiments may inform a later implementation, but they
cannot become the Automation source for a functional replacement without an
explicit binding migration plan.

Every promoted revision stamps the actual revision and scenario into
`pageSchema.meta.builder`. Review Apply messages carry a localized semantic
origin (`Review notes` / `Замечания`) instead of being indistinguishable from a
generic API call. Once Builder accepts the job, the client consumes only the
comments present in that submitted packet; comments added concurrently remain
available for the next revision.

Interactive controls in a prototype may update local page state or static/mock
data. They must not imply real external IO, device control, credentials,
network calls, or durable mutations unless the user explicitly asks for such
behavior and the corresponding skill/scenario contract exists.

Builder's prompt context should stay compact and general: expose an affordance
map of what the UI can express, not a long list of intent-specific recipes.
Specialized deterministic checks may validate ABI compatibility and unsafe
effects, but they should not replace the LLM's responsibility to understand the
user's request.

Builder prompt assembly is profile-based. The default profile is
provider/model-agnostic and sends a compact ABI summary plus a prototyping
affordance map. Later profiles may tune wording, temperature, examples, or
schema compression for a specific provider/model, but the output contract must
remain the same complete `adaos.webui.v1` manifest.

User-authored text crosses Builder boundaries as Unicode, not as console text.
Browser/API ingress, Builder workflow persistence, Skill Factory packets,
Codex prompts, Forge evidence, and UI projections must all preserve the same
UTF-8 code points. Windows operator tooling passes non-ASCII payloads through
UTF-8 files and `--json-file`; PowerShell native-process pipelines are not a
supported request transport. New text containing a replacement character or a
long question-mark run is rejected before persistence. Historical corrupted
evidence remains immutable; UI projections must label it transport-corrupted,
and Builder adds a clean follow-up record instead of guessing the lost source
characters.

The Root-owned `development` model profile uses `gpt-5` as its baseline. Prompt
IDE obtains the scoped profile list from `/v1/llm/models?scope=development`, and
new Builder projects inherit the profile marked `default=true`. Other Root LLM
workloads retain their own defaults; selecting a comparison model in Prompt IDE
changes only the current development project.

## UI Generation Control Examples

These examples are acceptance probes for Builder's rapid prototyping behavior.
They are intentionally phrased like ordinary user requests. They should later
become golden fixtures that compare generated `pageSchema` structure, supported
component use, visible semantic change, and absence of stale sample data.

| Scenario | User request | Expected evaluation focus |
| --- | --- | --- |
| Survey form | `Сделай форму опроса для жителей района: контакты, возрастная группа, как часто пользуются парками, оценка безопасности, несколько любимых мест, комментарий и согласие на обработку данных. Добавь пример заполнения.` | Uses specific field types instead of generic text: email/phone where needed, select/radio for age/frequency, rating/scale for safety, multi-choice/chips for places, textarea for comment, toggle for consent; mock data matches the domain. |
| Conference application | `Сделай прототип анкеты участника городской исследовательской конференции. Нужны личные данные, организация, формат участия, удобные даты, темы интереса, загрузка тезисов, блок доклада и оценка важности факторов развития города.` | Produces a structured form with meaningful sections, date/dateRange, select/multiChoice, fileUpload, textarea, rating/grid where appropriate, plus useful preview/summary widgets. |
| Online shop | `Сделай черновик интерфейса интернет-магазина для выбора товаров: фильтры, карточки товаров, корзина, промокод и пример оформления заказа без реальной оплаты.` | Separates catalog, filters, cart/order summary, promo input, and checkout mock flow; uses local state/static data only; does not imply real payment or external network calls. |
| Today's tasks | `Сделай список задач на сегодня: быстрое добавление, приоритет, время, статус выполнения, фильтр по состоянию и компактный вид для телефона.` | Uses form plus list/cards/table as appropriate, boolean/toggle for done, time/date, select/rating for priority, local filter controls, responsive/stacked layout without unused side panels. |
| Task workbench | `Сделай рабочий экран для ведения задач: слева список, справа детали выбранной задачи, кнопки смены статуса, комментарий и журнал последних изменений на моковых данных.` | Uses split/sidebar only when useful, list/detail/actions/log widgets, local selected item state or static examples, and no hidden real persistence. |
| Shopping list | `Сделай список покупок для семьи: продукты по категориям, количество, цена, отметка куплено, быстрый ввод и пример данных.` | Uses typed fields for quantity/price/done/category, table/cards with boolean display, realistic grocery mock data, and no leftover task-domain sample rows. |
| Recipe catalog | `Сделай книгу рецептов для телефона и компьютера: поиск и фильтры, карточки с фотографиями, избранное по категориям, подробности выбранного рецепта и форму добавления. Для временных фотографий используй стабильные ссылки Picsum с понятными seed.` | Uses responsive image cards, numeric filters for numeric data, selected-item master-detail, local tab switching, grouped favorites, a typed add form, and deterministic replaceable image URLs. Details render the selected image rather than displaying its URL as text. |
| Layout exploration | `Сделай две формы одну под другой с разными вариантами разметки на экране, чтобы можно было выбрать.` | If multiple comparable surfaces are generated, they should differ visibly in grouping, field order, component types, copy, density, support widgets, or interaction model. A valid answer must not only duplicate the same form twice. |

Evaluation should score both contract correctness and prototype usefulness:

- output is a complete `adaos.webui.v1` manifest with renderable
  `ui.application.desktop.pageSchema`
- generated widgets use only supported component types and valid field
  descriptors
- the result visibly satisfies the user's request rather than only preserving
  the previous UI
- interactions are local/mock unless real integrations are requested and
  available
- labels, examples, and mock data stay in the requested domain and language
- stale widgets, empty layout areas, and irrelevant sample rows are removed or
  adapted

## Relationship To Skill Factory

The [Skill Factory](skill-factory.md) is the target remote realization layer
for Builder work. It does not replace Builder, Prompt IDE, or the User Hub
validation loop.

Current Builder flows create local drafts, previews, patches, and review
Pending Actions inside the user's AdaOS workspace. The target Skill Factory
adds a Root-managed dev queue and isolated AdaOS dev nodes that can turn a
normalized Builder `realize_request` into a forge task branch.

The boundary is:

- Builder owns idea capture, conversation context, draft metadata, acceptance
  criteria, preview state, and human-facing review.
- Root owns queue policy, dev-node identity, task assignment, task-scoped
  credentials, MCP leases, timeouts, retries, and audit.
- Isolated Dev Node owns workspace checkout, Codex execution packet
  preparation, local tests, commit creation, result reporting, and cleanup.
- User Hub owns final pull, validation, staging, Pending Actions, approval, and
  normal skill/scenario lifecycle activation.

The first local implementation-stage adapter is
[`builder_automation_skill`](builder-automation-skill.md). It exposes the
Builder-facing `start`, `chat`, and `get_state` contract while keeping Codex
execution, task persistence, and artifact validation in core services. This
boundary lets the local worker be replaced by an isolated Skill Factory dev
node without changing the Builder UI contract.

This means remote realization must still produce ordinary AdaOS artifacts:
skills, scenarios, manifests, `webui.json`, tests, reports, and release
evidence. It must not directly mutate the user's live runtime.

## Builder Contracts

Builder work should move through explicit contracts before runtime mutation:

- `src/adaos/abi/builder.task.v1.schema.json`: handoff packet for human ideas,
  NLU Teacher gaps, runtime guard reports, and repair requests.
- `src/adaos/abi/builder.draft.v1.schema.json`: draft workspace metadata that
  links a task to an artifact, selected template, assumptions, risks, expected
  tests, and quality gates.
- `src/adaos/abi/skill.schema.json`, `src/adaos/abi/scenario.schema.json`, and
  `src/adaos/abi/webui.v1.schema.json`: artifact contracts that now carry
  Builder-oriented `llm_hints` and `nlu_hints`.

The default skill and scenario templates include `builder.draft.json` metadata
so generated work starts as a reviewable draft rather than an active runtime
change.

The first implemented write-neutral Builder surface is:

- `adaos builder draft`: creates an isolated draft workspace under Builder
  control while using the existing CTX dev artifact roots
  (`.adaos/dev/<subnet>/skills/<id>` or
  `.adaos/dev/<subnet>/scenarios/<id>`). `builder.draft.json` is written into
  the dev artifact, and `state/builder/drafts` only keeps an index by
  `draft_id`.
- `adaos builder preview`: creates an inspectable preview bundle with diff,
  schemas, route plan, NLU/action/UI preview summaries, static safety checks,
  dependency bootstrap evidence, review-policy evidence, and human-review
  reasons. Preview records are service metadata under `state/builder/previews`,
  not an alternate source tree.
- `POST /api/builder/draft` and `POST /api/builder/preview`: HTTP equivalents
  for local UI/workbench integration.
- `GET /api/builder/approval-profiles` and
  `adaos builder approval-profiles`: expose the current Builder approval
  profiles for UI, CLI, and workbench surfaces.

Preview accepts an approval profile:

- `manual_only`: every preview requires explicit human review before apply.
- `low_risk_auto_draft`: Builder may draft and preview, but apply remains a
  human decision.
- `low_risk_auto_apply`: only clean low-risk previews without mandatory review
  classes are eligible for automatic apply.
- `restricted_maintenance_repair`: only narrow descriptor, NLU-hint, and
  metadata repairs can be eligible without review.

Mandatory human-review classes are:

- secrets or credential-like material
- new permissions or capability declarations
- external IO
- destructive actions
- endpoint, route, tunnel, browser, or control-plane control
- high-rate streams or projections
- broad NLU patterns
- service or process management

`review_policy` in the preview bundle records the chosen profile, detected
mandatory classes, policy blocks, eligibility decision, and evidence. Older
drafts with `metadata.human_review_required=true` are treated as an explicit
manual-review override.

Builder also exposes an operational CLI facade over the existing dev lifecycle:

- `adaos builder create <id> --kind skill|scenario`: creates the artifact through
  the same owner dev workspace flow as `adaos dev skill|scenario create`.
- `adaos builder list --kind skill|scenario`: lists the same dev artifacts the
  owner workspace already manages.
- `adaos builder validate <id> --kind skill|scenario`: delegates to the dev
  skill/scenario validators, including JSON scenario manifests created by
  Builder drafts.
- `adaos builder push <id> --kind skill|scenario`: uploads through the existing
  Forge dev push path. It does not replace activation, install, approval, or
  runtime apply gates.

The same lifecycle is mandatory for non-CLI entrypoints. Builder chat creates
artifacts through `RootDeveloperService.create_skill/create_scenario`. Once all
files from a successful LLM turn are written and validated, it calls
`RootDeveloperService.push_skill/push_scenario` with the normalized LLM
`comment`. `ui_revisions/NNN.json -> vcs_checkpoint` records the attempt,
message, Forge commit, digest, and remote path. A remote push failure is
reported but does not erase or invalidate the already validated local
revision; retry and recovery remain possible from the dev workspace.
Automation completion applies the same rule to every materialized artifact:
a scenario and its companion skill receive separate Forge checkpoints using
the terminal implementation-result summary as their commit message before
runtime preparation begins. The workflow advances to `checkpoint_recorded`
only when the primary artifact checkpoint contains a change id, package
digest, and source revision. An explicit recovery path can reuse those durable
receipts after an interrupted finalization; it never reruns isolated Codex or
repeats an already confirmed Forge push.

Candidate dependency resolution considers every approved checkpoint member of
the active change set, not only the most recent primary-artifact receipt. This
allows an earlier companion-skill checkpoint from the same set to satisfy the
scenario dependency while still rejecting an unrelated or unapproved DEV
dependency.

Before automation starts, every missing DEV target (including a scenario's
companion skill) is scaffolded through `RootDeveloperService.create_scenario`
or `create_skill`. The worker consumes existing DEV sources and must not copy a
template directly into DEV; this keeps creation events, validation, paths, and
ownership on the core `adaos dev scenario|skill create` lifecycle.

This facade is intentionally not a new storage layer. It exists so Builder
work can be driven from one command branch while source ownership remains in
the current dev workspace and lifecycle tools.

## Conversation, Change, And Forge

Builder uses one canonical conversation:
`conv.skill.builder_skill.default`. Project isolation is provided by a stable
topic/thread (`prompt-project:scenario:<id>` or
`prompt-project:skill:<id>`), not by webspace-specific conversations. Prompt
IDE, Voice, API, and other entrypoints therefore expose the same transcript
when they select the same project.

Each artifact-changing turn creates a Builder Change record. The record is the
join between source messages, the Root LLM job, UI revisions, affected
artifacts, and Forge commits. A single automation change may contain both a
scenario commit and companion-skill commit. UI revision JSON stores the
`change_id`; the conversation store keeps the complete aggregate.

Forge commits contain:

- a short human title derived from the validated LLM result or request;
- allowlisted `AdaOS-Change-Id`, conversation/topic/thread, revision, model,
  request/result, and source-message trailers.

Forge never stores the full transcript as commit metadata. The former
`adaos dev scenario|skill update` draft pull is retired: it replaced the DEV
tree from a mutable remote draft and could erase unrelated local work. Builder
reconciliation now occurs at an exact checkpoint or explicit exact-base
rebase/migration boundary. Historical trailer and `ui_revisions/NNN.json`
recovery remains available only to that bounded reconciliation path; it is not
an implicit source update. Recovered messages are marked
`source=forge_recovery`, are idempotent, and never replace existing chat.

## Prompt IDE And Dev Webspace

The rapid-prototyping Builder experience uses two cooperating surfaces:

- `builder_skill` owns dialogue, draft lifecycle, LLM planning, `webui.json`
  patching, validation, preview evidence, and apply/review handoffs.
- `prompt_engineer_scenario` is the Builder Workbench UI. It renders active
  draft state, mockup preview, validation output, file/status views, and
  actions, but it is not the LLM brain.
- Prompt IDE does not own a separate chat implementation. It embeds the shared
  Voice/global-dialog widget configured for the `builder` channel, so voice
  input, typed input, channel selection, transcript rendering, STT/TTS state,
  and browser recovery stay in one reusable dialog component.

Prompt IDE may be loaded in any Builder host webspace. Each host owns one
explicit `builder_project_preview` relation to its preview webspace. Neither
side of the relation is inferred from an id suffix; ids are opaque and legacy
names such as `dev1-dev` are adopted only as relation targets during migration.
Selecting another project changes the binding carried by that relation. It
does not change or rematerialize the Builder host scenario.

There is one additional topology used while developing Builder itself. A
production Builder host may own one `builder_self_host` relation to a
development Builder host. That development host may in turn own one
`builder_project_preview` relation for the scenario currently inspected by the
development Builder. A project-preview webspace cannot own another preview, so
the topology remains bounded to these two explicit levels.

Preview controls share this binding but keep their native responsibilities:
Compare/select materializes through `adaos.sdk.builder.preview`, Open uses the
browser workspace-navigation action in a new window, and QR renders the same
relative workspace URL locally. No external QR service is part of the Builder
contract.

Project selection is a command/status flow:

1. `select_project` persists `binding.selection` and publishes
   `builder.context.selected`.
2. The declarative `callSkill` is marked as a background command, so local
   `updateState` and `closeModal` do not wait for preview work. Failures still
   use the normal action notification path.
3. The source host receives a compact `data/builder` projection and hydrates
   page state from `data/builder/selection` without navigation or page reload.
4. Scenario projects additionally publish `builder.preview.desired`; the
   reconciler materializes only the explicitly related preview in the
   background and later publishes `builder.preview.observed`.
5. Skill projects change Builder data context only and leave the preview
   scenario untouched.

`builder.context.selected` is not a content-change event. It must not refresh
the complete Prompt workflow projection. Artifact writes use
`project.content.changed` and may trigger the heavier refresh path.

The Select Project catalog is a read model, not a Builder command. The browser
loads it through `GET /api/builder/workbench/projects` with the explicit source
`webspace_id`. The endpoint enumerates development project directories and
reads only each manifest plus the bounded fields in `prompt_state.json`; it
must not load full prompt context, call a dynamic skill, acquire the workspace
command lock, or resolve preview topology once per project. The persisted
source-to-preview binding is read once for the response. The browser caches the
result under `builder.project.catalog` until a catalog mutation invalidates
that tag. Revision 034 declares the workspace-scoped source for prefetch as
soon as the Builder application schema is available. Opening the modal then
uses the same semantic cache entry; if prefetch is still in flight, both
consumers share its single `GET` (an HTTP CORS preflight may precede it).
Modal node addressing must not add `node_id` parameters to this workspace read
because doing so would create a different cache identity.

Builder copy is locale-neutral at the SDK boundary. Static widget and modal
fields carry semantic `*_i18n` references backed by scenario-owned `ru` and
`en` resources. Dynamic SDK projections use the same sibling-field convention
for project and lifecycle labels; the browser resolves them after loading the
data source. Tool handlers therefore return one stable payload shape rather
than locale-specific response schemas.

The source webspace owns the user's conversation and requirements context. The
paired dev webspace owns the visual workbench and live mockup projection. The
binding is explicit service state, for example:

```yaml
builder_workspace_binding:
  source_webspace_id: dev1
  preview_webspace_id: dev1-dev
  relationship:
    purpose: builder_project_preview
  scenario_id: prompt_engineer_scenario
  purpose: builder_prompt_ide
  selection:
    object_type: scenario
    object_id: shopping_list
    title: Shopping List
```

Builder state uses `data/builder/*` projections. The live source projection is
bounded to selection identity, binding identity, preview status, and compact
preview identity. Full reconciler results, page schemas, draft lists, dialog
diagnostics, and validation evidence remain explicit tool/snapshot reads rather
than being copied into every YDoc update. The projection is written only to the
Builder host; the preview owns its scenario data. Legacy `data/prompt/*` paths
may be read only for migration or compatibility, not as canonical Builder
state.

An HTTP `403` from a Builder data source means that the authenticated caller is
forbidden or that an approval is required. It is a local source failure and
must not invalidate the browser session. Only an authentication failure such
as `401` may enter session recovery.

Acceptance checks for project selection are:

- the Builder host document and Yjs connection stay mounted;
- `ui.application` in the Builder host is unchanged;
- selected project identity survives a later render or reload through
  `data/builder/selection`;
- only a scenario selection changes the explicitly related preview;
- the selection command returns before preview materialization completes;
- repeated selections remain coalesced and do not create unbounded runtime
  records, YDoc snapshots, tasks, or memory growth.
- opening Select Project does not invoke `builder_sdk_control_skill.list_projects`
  or wait for the workspace command lock; the list is populated from the
  bounded project-catalog read model.

The workbench-facing control surface should include:

- `builder.ensure_dev_webspace`: create or reuse the paired dev webspace and
  load `prompt_engineer_scenario`.
- `builder.attach_dialog_widget`: configure the embedded Voice/global-dialog
  widget for the source conversation and the `builder` channel.
- `builder.get_workspace_binding`: return source/dev webspace ids, workbench
  scenario id, and active draft.
- `builder.open_dev_webspace`: return the browser/open URL for the paired
  dev webspace.
- `builder.set_active_draft`: switch the paired dev webspace to another draft
  without creating another workbench.
- `builder.list_development_skills`: list drafts and development skills
  available to the current source webspace.
- `builder.delete_development_skill`: remove a development draft through the
  governed draft lifecycle.

The first-turn flow is:

1. The user addresses `builder` / `Builder` / `Строитель` / `строитель` and
   asks to create or change a capability.
2. The router enters the Builder channel and strips the address before sending
   the request to `builder_skill`.
3. `builder_skill` creates or selects a draft, ensures the paired dev webspace,
   sets the active draft binding, and writes an initial `webui.json` draft.
4. Prompt IDE renders the active draft preview in the paired dev webspace and
   shows the embedded Voice/global-dialog widget bound to the source Builder
   conversation.
5. Follow-up comments are processed as patches against the current draft and
   current `webui.json`, preserving conversation history and validation
   evidence.

## Relationship To Web UI

Builder may create browser-facing UI descriptors, but the browser runtime owns
rendering mechanics.

Builder must respect:

- [Web UI Architecture](web-ui-architecture.md)
- [UI Addressing](ui-addressing.md)
- [Semantic State Plane](semantic-state-plane.md)
- [Skill Assets and Icons Roadmap](skill-assets-and-icons-roadmap.md)

Generated UI should use stable `webui.v1` and semantic descriptors rather than
client-private assumptions. Data shown in widgets and modals must be routed
through declared Yjs projections, stream receivers, details tools, or local
diagnostic surfaces.

## Relationship To Runtime Governance

Builder output is only useful if AdaOS can operate it safely.

The runtime must provide:

- schema validation
- import/smoke tests
- skill runtime prepare/test/activate/rollback
- scenario install/validate/run/test
- route, memory, stream, and Yjs guard evidence
- lifecycle diagnostics and quarantine summaries
- status/notification projections that explain failure without hiding it

## Relationship To Pending Actions

Local prototype creation and ABI-valid UI revisions do not create Pending
Actions. They are reversible revisions: chat and element review notes provide
feedback, while `Set current` provides rollback. A binary approval stack is a
poor fit for an incremental prototype where each revision builds on the current
one.

Pending Actions are the durable human-in-the-loop surface for deletion,
activation/release, new permissions, external I/O, credentials, device control,
or another mandatory policy boundary. Notifications may point to those choices,
but notifications must not become the source of truth for approval.

Relevant document:

- [Pending Actions](pending-actions.md)

Relevant documents:

- [AdaOS Supervisor](adaos-supervisor.md)
- [Runtime Guarding](runtime-guarding.md)
- [Operational Event Model](operational-event-model.md)
- [Post-Deploy E2E Testing](post-deploy-e2e-testing.md)

## Source Of Truth

This page owns the Builder role, terminology, and end-to-end capability
creation boundary.

It does not own Support intake, the durable AdaOS Issue aggregate, managed
deployment economics, or trusted collaboration between independently owned
Builders. Those cross-domain boundaries belong to
[Governed Evolution](governed-evolution.md). This page remains authoritative
for the work that begins after an approved development request reaches
Builder.

Other documents should describe their local projection of Builder:

- skill docs describe what a Builder-authored skill must satisfy
- scenario docs describe scenario authoring and activation constraints
- NLU docs describe when Teacher creates Builder handoff candidates
- Root MCP docs describe the governed context and tool planes Builder consumes
- runtime docs describe how Builder output is validated and activated

If a document needs to mention an AI-assisted programmer, use `Builder` and
link back here.
