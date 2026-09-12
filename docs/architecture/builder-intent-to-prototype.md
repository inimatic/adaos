# Builder Intent-to-Prototype Architecture

Status: corrective target architecture. The current implementation is a
transitional, recipe-guided prototype path and does not yet satisfy this
contract.

Last reviewed: 2026-09-12.

This page owns how Builder turns an ordinary user request into an executable
Prototype. [AdaOS Builder](builder.md) continues to own the complete governed
development lifecycle. [Executable Prototype Architecture](executable-prototype-architecture.md)
owns Prototype behavior and authority. [Web UI Architecture](web-ui-architecture.md)
owns the browser runtime contract. Delivery order is owned by the
[Builder Intent-to-Prototype Roadmap](builder-intent-to-prototype-roadmap.md).
The [Builder E2E Evaluation Pipeline](builder-evaluation-pipeline.md) owns
reproducible evaluation contracts and the single technological CLI entry. The
[Client Component System Roadmap](client-component-system-roadmap.md) owns
Client decontamination and component-system growth.

## Correction Summary

The intended architecture and the implemented path diverged in two related
ways:

1. The architecture correctly required compact, general, purpose-scoped
   context, semantic composition, and deterministic validation.
2. It did not define a typed product-understanding boundary between user text
   and renderer-level `webui.json`.
3. The implementation filled that gap with prompt rules, lexical domain
   classifiers, detailed recipes, cumulative phases, complete WebUI context,
   and output-repair code.
4. The roadmap then accepted recipe-guided Applications evidence as evidence
   of generic Builder composition. That interpretation is no longer valid.

The Applications Prototype remains useful product and renderer evidence. It
proves that Builder can materialize and revise a detailed, validated recipe. It
does not prove that Builder can understand an unseen application request and
autonomously derive a relevant design.

`recipe.application_manager`, Applications-specific qualification, and its
postconditions are compatibility and evaluation fixtures until migrated. They
must not remain in generic Core request interpretation, and they cannot close
prompt-autonomy gates.

## Current Baseline Audit

The 2026-09-10 source audit found the following concrete gaps. Counts describe
the audited revision and are diagnostic, not permanent architecture limits.

| Concern | Current evidence | Architectural interpretation |
| --- | --- | --- |
| Missing understanding contract | No Prototype Brief or adaptive Prototype Plan ABI exists. The request compiler proceeds from instruction and project memory to selected UI capabilities and a WebUI patch contract. | Product interpretation is implicit and cannot be reviewed, evaluated, or repaired independently. |
| Renderer-level generation | `builder_skill/handlers/main.py` sends complete `current_webui_json` and asks for RFC 6902/extended JSON Pointer output. | The model must simultaneously understand the product and program the renderer artifact. |
| Domain behavior in generic Core | `services/ui_capabilities.py` recognizes Applications and board vocabulary, selects named recipes, and implements Applications-specific lifecycle, fixture, and UI postconditions. | Core qualification and validation are coupled to current dogfood subjects. |
| Detailed solution leakage | `abi/ui.capability_catalog.v1.json` supplies the Applications layout, commands, tools, states, phases, vocabulary, and postconditions. | This is a detailed domain pack, not a generic component capability description. |
| Retrieval declared but unavailable | The bounded context index says `mcp_context_search`, while the examined Prototype model requests exposed no retrieval tool and recorded no tool use. | The model cannot follow the stated progressive-disclosure contract; the orchestrator compensates by embedding more context. |
| Text repair as a normal rail | The Builder handler contains balanced-object extraction and JSON/JSONL repair around provider text. | Output syntax recovery obscures model/contract failures and adds retries that schema-constrained output should prevent. |
| Split ownership without a seam | The DEV Builder handler is 22,472 lines; Core `builder/workflow.py` is 6,384 lines and `ui_capabilities.py` is 3,504 lines. Product rules, workflow state, provider handling, persistence, validation, and lifecycle coordination cross these files. | Physical size is a symptom. The primary defect is duplicated or misplaced authority. |
| Semantic IR is not the generation boundary | `webui.semantic.v0` is an additive draft and is not used by the active Builder generation path. Existing semantic operations cover bounded edits only. | AdaOS has useful semantic primitives but not a complete intent-to-runtime compiler. |

The retained phased Applications journal is intentionally noisy because it
includes repair and performance experiments. It is not a production success
rate. It nevertheless demonstrates the current cost shape: 20 recorded model
results consumed about 726k input tokens, median input was about 36k, maximum
input about 70k, and only about 25% of input tokens were cached. A narrow exact
move still received about 22k input tokens. These measurements justify a
contract and context redesign rather than another timeout or prompt-size
increase.

The documentation drift was therefore bidirectional. Implementation departed
from the compact/general context rule, while architecture and roadmap text did
not provide the missing brief/semantic-compiler contract and accepted local
mechanism tests as broader autonomy evidence. Both documentation and code need
correction; neither can be treated as the sole source of the defect.

The corrective implementation on 2026-09-11 establishes the first clean
semantic-v2 vertical slice: schema-constrained multi-resource candidates,
typed relationships, structural representative-state proof, requirement
bindings, explicit capability gaps, deterministic compilation to WebUI, and
exact model-input attribution. This is a supported subset, not completion of
the target architecture. A retained volunteer run still left actors, entities,
outcome, information hierarchy, and disclosure semantics unknown in the
Prototype Brief. The design model reconstructed much of that meaning from the
raw user turn, but such recovery is stochastic and cannot replace the R4
understanding contract.

## Product Objective

A person describes the application or change in product language. Builder must
be able to:

- identify the user goal, actors, principal tasks, information, operations,
  states, and constraints;
- expose material assumptions and ask only questions that can change the
  result or its authority;
- produce an early executable Prototype without requiring the person to know
  AdaOS schemas, component IDs, JSON paths, prompt phases, or internal tools;
- revise the Prototype from ordinary review language;
- preserve accepted behavior and unrelated parts of the application;
- report uncertainty or a missing platform capability instead of disguising a
  gap with an unrelated component;
- keep every candidate replayable, measurable, reversible, and subordinate to
  the existing Review and lifecycle gates.

The objective is not one-shot generation. It is the minimum effective sequence
of deterministic and model-assisted steps for the request at hand.

### Minimum Working Interpretation

A short user request is not a complete implementation specification. Builder
may choose a simple or richer design without treating unspecified detail as
missing requirements. The first objective is a useful, executable Prototype,
not the most detailed application that fits the output budget.

Acceptance and quality review are separate:

- Acceptance checks explicit requested outcomes and constraints, the controls
  necessary to make them work, supported runtime behavior and honest stage
  disclosures. Broken controls and silently omitted requested behavior fail.
- Quality review discusses relevance, simplicity, richness, density, polish
  and alternative design choices. These do not block acceptance unless the
  request explicitly makes them necessary or a defect prevents actual use.
- An evaluator must not invent domain conventions, extra screens, exact field
  counts, automatic optimization or a preferred component as acceptance criteria.
  Every mandatory product criterion must be traceable to the request or to a
  separately declared platform safety/runtime invariant.
- Material ambiguity affecting the primary outcome or authority can require
  clarification. Reversible layout and detail choices should normally reach
  a working first Prototype for human review, not an exhaustive questionnaire.

User acceptance of the interpretation is distinct from technical validation.
Passing technical gates does not prove satisfaction with unspecified design
choices. Human feedback becomes the next explicit revision request. Future
requirements discovery and context guidance may increase detail, but are not
prerequisites for the minimum working Prototype gate. A failed generation must
be reported as failed, never relabeled as working to promise a guarantee.

### UX Guidance And Feedback Memory

The bounded current context includes a progressive-disclosure recommendation;
the broader golden-rule catalog and feedback memory remain planned:

- Platform safety and runtime invariants remain enforced policy. Neither a
  preference nor an inferred UX convention may override them.
- Builder may carry a small, versioned, domain-neutral set of UX golden rules
  as recommendations. Rules describe applicable conditions, rationale and
  alternatives, not mandatory layouts or additional product functionality.
  For example, consequential cancellation may warrant confirmation or an
  undo path; a harmless reversible change need not gain another dialog.
- Project requirements remain explicit and attributable. A user-requested
  confirmation is mandatory even when confirmation is otherwise only guidance.
- Feedback can propose project-scoped preferences. Reuse across applications
  requires user confirmation of the wider scope; a one-off correction must
  not become a global rule. Do not infer private user preferences from external
  Dev Tickets or other publishers' examples.
- Each reusable preference records source feedback, owner/scope, applicability,
  confidence, confirmation state, version and supersession/deletion. The user
  can inspect, correct and forget it. New explicit instructions override older
  preferences; unresolved material conflicts are surfaced, not silently merged.
- Context includes only relevant, attributed recommendations. Keep the small
  stable rule catalog cacheable and retrieve scoped preferences separately.
  Record what influenced generation. No mutable user memory may leak into a
  clean evaluation cohort or its mandatory rubric.

Golden rules are promoted by reviewed cross-domain evidence, not by one failed
generation. Optional quality evaluation and human feedback measure their effect
on usability, added interactions, cost and latency before broad adoption.

### Editor Surfaces And Executable Acceptance

Semantic editor views select `inline`, `modal` or `side_sheet`; omitted surfaces
on existing documents retain inline behavior. A short focused edit can use a
modal, contextual editing can use a side sheet, and a persistent work area can
remain inline. The choice itself is not a gate. Collections/details currently
remain inline inside their owning section; large-project orchestration is separate.

### Small-Prototype Content Composition

Linked navigation uses semantic v2 `selection_filter`: a target collection names
its target field, source collection (`source_view_ref`) and optional
`source_field_ref` (null/omitted means implicit `id`), not a guessed runtime state
variable. A declared FK/id relationship supports both parent-to-children and
selected-child-to-parent lookup; matching field names are not sufficient.
Links must be acyclic. Core resolves the source's actual selection action and the target query;
no selection shows the unrestricted collection. A linked predicate cannot overlap
a resettable or fixed filter on the same field. Charts cannot act as selection
sources. Row selection that drives related content does not also open an editor;
editing remains an explicit action. Browser qualification must prove both ends
of the link, not only a highlighted row and an independent dropdown.
Changing a parent clears descendant record selections and their captured FK
values. A reverse lookup must not clear the ancestor selection. Selection values
remain resource-scoped across representations; large independent workspaces and
branch-local selection are outside this small-prototype contract.

Collection scope is separate from user query state. Semantic v2 `scope_filters`
contains typed, permanent equality predicates; query controls narrow that scope
on other fields and reset only their own state. A tab title is not a predicate.
Compile these constraints into literal resource-query filters and include them
when checking representative records. Empty-response fixtures still override
the same collection, not its source data. Reject overlapping scope/query fields
instead of silently overriding either. This supports any fixed-subset view
without application-name branches or a new renderer component.

Presentation is a typed capability, not a product recipe or a compulsory design
checklist. Alongside list/table/cards, a collection may request a lane board,
parent-linked tree, numeric chart, or expandable grouped collection. The semantic
contract names record fields; Core maps them to existing Client renderers and
checks their actual data/event contracts. A board move persists its declared
choice field. Ordering within a lane is not implied. A chart plots numeric
records; it must not manufacture calculated aggregates or replace missing values
with zero. Unsupported behavior remains an explicit capability gap.

Provider output uses separate collection and record-view schema alternatives.
Collections require a presentation and inline placement. Record details/editors
cannot declare collection presentations, presentation options, query filters,
selection links or collection-empty states. This prevents incompatible shapes at
generation time without introducing application-specific instructions. Retained
authoring documents still receive explicit compiler diagnostics; schema validity
alone does not prove relationships, usable controls or task completeness.

`view.section` optionally partitions views into tabs or application-settings
modals; absent/null sections remain shared. Section identity, title and kind
must agree across their member views. This reuses normal page state, visibility,
modal and resource-operation contracts, not a parallel renderer. Settings are
real prototype data/editing surfaces and do not grant external effects or replace
the shell's device/assistant settings. Section composition is useful on small
applications without enabling deferred large-project orchestration.

Core groups a collection's query controls into one responsive query toolbar.
Search stays available; additional filters use explicit disclosure with visible
active values and an atomic reset of owned page-state keys. Native date changes
commit without requiring focus loss. False and zero are values, not empty filters.
Selection that drives a related collection must not also open an editor without
an explicit command. Short isolated edit tasks may still use direct row editing.

Visible text wraps by default. `field_display` can opt into truncation or choose
start/center/end alignment per list/card/table field without changing stored data.
Truncated content must remain inspectable. Model guidance explains these choices
without requiring every prototype to use every component. Runtime qualification
must cover desktop/mobile behavior, not only schema admission.

SDK/E2E turns carry explicit ingress provenance and stable message identity.
Builder projects these instructions into the same project topic as its results;
browser/voice ingress already recorded by the chat transport is not duplicated.

Core compiles openers, record selection, typed fields, separate command IDs,
availability guards and source maps. The Client hydrates only the selected
record, validates the chosen mutation, retains input on errors and dismisses an
editor only after success. Cancelled confirmation is not successful mutation.
Tests must execute the compiled artifact across this boundary, not substitute
mocked WebUI or infer working CRUD from valid JSON and visible controls. Report
generation validity, interaction success and UX quality separately.

State-proof repair uses a bounded replacement contract tied to the original
candidate digest. Only reported states and the evidence fields/query controls
or empty-state presentation of their views may change. Fixtures, commands,
bindings and unreported states remain immutable, and the assembled candidate
passes the full compiler again. Keep raw patch output as evidence. Compiler
contract defects stop this route instead of consuming another model generation.
Other repair classes still use full candidates until their own bounded contracts
are qualified; do not describe state-only repair as a general incremental editor.

## Architectural Constraints

1. **The user contract is outcome-oriented.** Internal phase names, ABI paths,
   recipe IDs, and model instructions are not required user vocabulary.
2. **A user turn is not an LLM turn.** One user request may require no model,
   one bounded model call, clarification, or several independently validated
   stages. The orchestrator chooses the route.
3. **Models propose; AdaOS decides and writes.** Model output has no direct
   runtime, file, lifecycle, network, or publication authority.
4. **Core is domain-neutral.** Core may know schemas, semantic operations,
   capability contracts, risk classes, and validation rules. It must not infer
   Applications, shopping lists, recipes, or other product domains with
   hard-coded vocabulary or postconditions.
5. **Domain packs are optional artifacts.** A domain pack may contribute
   vocabulary, examples, capability mappings, and evaluators. It is versioned,
   attributable, removable, and selected explicitly or by measured retrieval.
   It is not a hidden Core branch.
6. **Product semantics precede renderer syntax.** `webui.json` remains the
   canonical runtime artifact but is compiler output, not the primary model of
   user intent.
7. **Context is minimum sufficient evidence.** Every included unit has a
   purpose, source, freshness state, digest, token cost, and omission reason.
   A prompt instruction to retrieve data is invalid unless the model receives
   an actual retrieval tool.
8. **History is decisions, not transcripts.** Accepted requirements,
   assumptions, constraints, semantic deltas, and unresolved questions survive
   a turn. Repeated raw UI, issue, and conversation copies do not.
9. **Safety is independent of model quality.** Better models may improve
   comprehension and design, but do not weaken permission, Review, Trial,
   validation, or rollback boundaries.
10. **The Client component set is open-ended.** Adding a component cannot
    require handwritten changes to independent prompt, catalog, validator,
    documentation, registry, and loading tables.

The current compatibility seam is deliberately narrower than the future
measured-retrieval design. A DEV Project declares any admitted packs in
`project.yaml.development.domain_packs`. Builder resolves only those IDs and
records each pack's version and content digest in `input_attribution`. A
persisted historical `recipe_id` may resolve to its owning pack through the
pack registry so an old session remains replayable. Project names, scenario
IDs, user wording, postcondition prefixes, and product titles never select a
pack. The generic profile supplies an empty pack set.

`applications.compatibility.v1` currently owns the extracted
`recipe.application_manager` catalog, qualification/evaluation adapter,
prototype locale policy, repair guidance, phases, defaults, and
Applications-specific prompt rules. `research.compatibility.v1` owns the
extracted research prompt rules. These are transitional compatibility packs,
not evidence that the target brief/compiler architecture is implemented.

## Required Separation Of Representations

### 1. User Intent

`adaos.builder.intent.v1` retains the exact user statement, locale, source
interaction, selected Project/Change, explicit references, and authority
boundary. It is evidence, not an inferred specification.

### 2. Prototype Brief

`adaos.builder.prototype_brief.v1` is the first interpreted contract. It is
small enough for a person to review and contains:

- problem, outcome, actors, and principal jobs;
- domain entities and information hierarchy;
- user-visible operations and their expected outcomes;
- real-effect, fixture, mock, and unknown boundaries;
- representative states and exceptional states;
- locale, accessibility, compact/wide, and content-volume constraints;
- accepted requirements, assumptions, open questions, and capability gaps;
- source intent refs and interpretation confidence per field.

The brief must distinguish `unknown` from `not required`. An omitted operation
must not silently become a destructive default. Clarification is required only
when an unresolved field changes safety, architecture, or the primary user
experience.

### 3. Request Routing And Deferred Large-Prototype Plan

The active milestone is the complete Builder lifecycle for small applications,
including Automation. Routing between deterministic edits, bounded edits, design
and capability gaps does not require a general dependency-graph executor.
Large-application planning, cross-unit orchestration and resumable DAG execution
are deferred until that small-scale lifecycle is reliable.

`adaos.builder.prototype_plan.v1` is a dependency graph of work units. It is
not a domain recipe and not a fixed series of prompts. Each unit declares its
inputs, expected semantic output, deterministic checks, model class, budget,
and completion evidence.

The normal routes are:

| Class | Typical request | Route |
| --- | --- | --- |
| `D0 deterministic` | rename, move, toggle a declared option | Resolve stable refs and apply a typed semantic operation; no model. |
| `D1 bounded edit` | add one known view or revise one interaction | Retrieve a local semantic slice, generate one typed candidate, validate. |
| `D2 design` | create a new surface or materially change its workflow | Compile/review the brief, retrieve capabilities, produce semantic design, compile and evaluate. |
| `D3 capability gap` | the required prototype has no admitted component or provider contract | Stop unsupported Prototype approximation, record the gap, and offer a governed Automation/Core path. |

### Stage-Specific Acceptance

Prototype acceptance is not application readiness. The same user requirement
has different evidence obligations at different stages; it must not disappear
because its final business behavior cannot run in the declarative preview.

- Supported local CRUD, selection, details, search/filter and field validation
  must work in Prototype. A label or a pending obligation cannot replace them.
- Business rules and external integrations may be represented by realistic,
  visible states and interactions in Prototype. The semantic document retains
  `automation_requirements`: exact Brief job/residual reference, reason,
  EN/RU disclosure, and a testable future acceptance condition. The existing
  requirement binding points to the visible demonstration. Core copies the
  original statement and marks it `pending_automation`; the model cannot mark
  it implemented. Routine deferred business logic is not a platform gap.
- A true `capability_gap` prevents the required prototype representation and
  remains a blocker. Silence, an unbound obligation, hidden fixtures, or a
  claim of real enforcement without executable evidence do not pass.
- Prototype review exposes pending obligations to the human. Digested
  acceptance carries them into Automation context and acceptance checks.
  Automation must implement and test success and failure behavior; screenshots,
  fixtures and disclosures alone cannot satisfy those checks. Trial/release
  readiness requires the separate implementation and verification gates.

The generation-stage context includes this boundary before inference. The
independent grader applies the same stage policy, while case-specific rubrics
remain unavailable to the generation model. Grader v10 supersedes v9's mixed
Prototype/production overlap gate. Previous run evidence stays immutable and
is not silently reclassified as a passing baseline.

Units may be skipped, repeated, or split. Repetition must be caused by a typed
validation finding or an explicit user revision, not by a hard-coded phase
counter.

Representation boundaries do not imply one model call per representation. A
low-ambiguity `D2` route may produce a brief candidate and semantic design in
one schema-constrained response, then validate them independently. A
deterministic route may persist the same contracts with zero model calls. A
separate planner model is admitted only when matched evaluation demonstrates a
quality or cost benefit. The plan itself is normally compiled by the
orchestrator from request class, dependencies, gaps, and validation results.

### 4. Semantic UI Document

The draft `webui.semantic.v0` must be replaced or promoted into a versioned
semantic document that can express the complete supported Prototype surface.
The semantic document describes roles and relationships rather than Client
constructor details:

- surfaces, regions, views, content priority, and responsive behavior;
- domain entities, collection item granularity, fields, relationships,
  selection, filters, and derived presentation;
- semantic commands, expected outcomes, and authority;
- states, visibility, empty/error/loading/denied behavior;
- localization keys and content examples;
- stable semantic refs, accepted constraints, and requirement bindings.

The document must have a deterministic compiler to `adaos.webui.v1`. Unknown
or lossy mappings fail with a capability gap. Direct full-WebUI generation is
a compatibility fallback and must be measured separately.

The current `adaos.builder.semantic_prototype_candidate.v1` single-resource
shape is a bootstrap subset, not the complete semantic document described
above. It is sufficient only when one independently editable record type can
truthfully represent every accepted job and state. A request with independent
collections such as people, shifts, and assignments must either use typed
multi-resource/relationship semantics or expose an exact capability gap; it
must not flatten those collections into fields or claim that absence from one
collection proves the state of another.

Every accepted brief requirement must bind to one or more semantic nodes, or
to a typed capability gap. The compiler emits source maps from those semantic
nodes to runtime resources, widgets, fields, actions, states, and locale keys.
Validation fails when a binding is unresolved or the compiled nodes cannot
provide the declared behavior. Sharing a runtime resource name is not evidence
that a repeated collection has the requested item granularity; that decision
must be explicit in the semantic data shape before component selection.

This is not a second browser runtime ABI. For a new managed Builder Project,
the semantic document is the authoring source and `webui.json` is its compiled,
canonical runtime artifact. Existing Projects without that source remain
WebUI-authoritative behind the compatibility adapter. AdaOS does not maintain
implicit bidirectional synchronization or infer a semantic document from
arbitrary legacy WebUI; migration requires an explicit reviewed conversion.

Existing `adaos.builder.semantic_ui_change.v1` remains the bounded edit
contract. It should target semantic refs first and compile the affected slice,
rather than forcing the model to author RFC 6902 paths.

### 5. Runtime Artifact And Evidence

Large-prototype readiness is a deferred admission dimension. A future plan must assign
requirement ownership to dependency-aware semantic units, preserve shared IDs
and locale/navigation contracts, and resume or repair only affected units.
Small requests may bypass decomposition. Neither a universal sequence of model
calls nor larger candidate limits proves this capability. These target properties
are not implemented by the current full-candidate compatibility route and do not
block small-application Automation qualification.

Supported local CRUD and finite automation primitives belong to Prototype, not
to an automatically deferred implementation backlog. Their executable contracts
must state preconditions, side effects, error behavior and composition limits;
model-declared capability names are not proof of runtime support. Cross-record
business rules remain explicit pending Automation obligations unless an actual
supported primitive implements and verifies them.

Repair diagnostics collect independent proof defects before choosing a repair
scope. Proof-kind rules, fixture cardinality, visible predicates, query
reachability and empty-view rendering must not hide one another behind a
fail-fast validator. A scoped repair cannot change unrelated fixtures, commands,
view surfaces or requirement bindings. Its output schema includes only reachable
patch definitions, and the merged candidate still undergoes full validation.

An incomplete provider response is not a semantic model rejection. Retain its
structured status, reason, partial output and usage; distinguish unknown usage
from zero. Reasoning tokens share a provider's total output budget where that
contract applies. Profile and capacity experiments must record actual request
options and must not silently alter the normal user's defaults.

Generation context must expose one authoritative inventory of required references.
Each operation retains its action, source clause, known target and related jobs;
an isolated verb is not a sufficient description. Unknown entities remain explicit
rather than being guessed by domain classifiers. Deterministic interpretation and
model-selected design must have distinct provenance.

Lexical mentions are interpretation evidence, not automatically mandatory
operations. Noun mentions, negation and subordinate clauses can change their
meaning. Preserve complete source clauses and expose uncertainty; do not turn
an inferred action label into a hard acceptance item without a supported
interpretation. The user's explicit outcome remains authoritative. Evaluation
must include false-positive operation extraction, not only missing coverage.

Executable postconditions follow the admitted outcome binding and resource
model. For example, assignment can create a linking record or update an existing
record; a verb alone cannot require one storage primitive. Equivalent designs
still need reachable controls, editable inputs and observable record effects.
This flexibility must not turn a semantically named but inert command into a
passing outcome.

The generation contract, canonical compiler and runtime must agree on capacity,
relationship identity versus display labels, attachment values, query filters and
representative-state proof. Generic invariants are derived from executable
contracts, versioned with them and shared by initial generation and repair. Case
rubrics, exemplar applications and reference solutions remain evaluation-only.

Prototype qualification separates structural validity, actual resource execution,
rendering, user-task success and human acceptance. A declared target count is not
proof of achieved coverage; an evaluator cannot invent automatic behavior absent
from the request. Grader calibration tests both false positives and false negatives.

The compiler emits complete, atomically validated `webui.json`, locale assets,
fixture declarations, and an attribution receipt. The receipt links intent,
brief, plan, selected component contracts, semantic input, compiler version,
validation, rendered evidence, model usage, and the resulting immutable
Prototype revision.

Runtime sidecars are not members of the WebUI document. In compatibility
JSONL mode, patches address only the WebUI candidate and the terminal
`complete` record carries locale assets, direct representative resource
records, and other declared sidecars in separate typed members. A repair may
replace the WebUI candidate without discarding already valid sidecars. Parsers
may recognize an unambiguous legacy sidecar path for migration, but must remove
it from the WebUI document, record the normalization, and validate the sidecar
against its own schema. This boundary prevents transport envelopes and fixture
payloads from becoming accidental renderer nodes.

## Responsibility Boundaries

| Owner | Responsibilities | Must not own |
| --- | --- | --- |
| Builder skill | Conversation adaptation, progress projection, user clarification and Review presentation through public SDK | Core workflow state, provider protocol, filesystem mutation, domain classifiers, schema repair, publication mechanics |
| Builder Core services | Brief/plan contracts, routing, context plans, semantic compiler, validation, receipts, lifecycle coordination | Product-domain recipes, Client rendering code, provider-specific prose |
| LLM service/adapter | Provider profiles, structured-output/tool protocol, usage, retry classification, cache telemetry | Builder workflow truth or artifact authority |
| Client component package | Renderer plus machine-readable component contract, fixtures, accessibility and responsive evidence | Prompt assembly or Builder domain policy |
| Domain pack | Optional domain vocabulary, examples, mappings, and evaluators | Hidden Core dispatch, lifecycle authority, generic acceptance claims |
| Development Session | Project-specific brief, decisions, accepted constraints, refs, and revisions | Global learned rules or another Project's initiator/context |

The current large `builder_skill/handlers/main.py` is therefore a transitional
composition root. Product interpretation, LLM request construction, candidate
parsing, validation, file operations, and lifecycle orchestration must move to
separate Core/adapter services behind public SDK operations. The skill should
not be split into equally stateful helper modules; state and authority must
first move to their correct owners.

## Context And Model Execution

Each model run receives three logical layers:

1. A byte-stable policy and typed output/tool contract identified by version
   and digest.
2. A selected, immutable capability bundle identified by component-contract
   digests. Only definitions required by the plan unit are included.
3. A dynamic task suffix: brief slice, target semantic slice, active accepted
   constraints, validation findings, and the current user delta.

Context composition is stage-specific. Intent residual interpretation receives
the exact user evidence and unresolved Brief fields, semantic design receives
the accepted Brief and admitted semantic capabilities, repair receives the
candidate plus all independent typed findings, and evaluation receives the
immutable artifact plus a post-generation rubric. A stage must not inherit a
large shared packet merely because another stage needs it.

The context plan is content addressed. Stable policy, schema, and capability
units precede dynamic task content so provider prompt caching can reuse them.
Cached-token price is tracked separately, but caching does not make irrelevant
context acceptable: every unit still needs an owner, purpose, freshness,
digest, and measured contribution. A runtime activation receipt must prove
that the materialized Builder digest matches the edited DEV source; source
changes alone are not evidence that a live experiment used them.

Full `webui.json`, complete project memory, and raw history are excluded by
default. They remain available through bounded ref retrieval for recovery or
diagnosis. Retrieval may be deterministic prefetch or an actual read-only
tool; a textual `drill_down` hint is not retrieval.

Every route has independent ceilings for fresh input, cached input, output,
wall time, model attempts, and repair attempts. Exceeding a ceiling produces a
typed partial result or clarification. It does not silently widen context or
switch to a more expensive route.

Output ceilings are selected from retained response distributions and task
completeness evidence. During development evaluation, AdaOS first retains and
examines the complete provider response and exact sanitized request. It may
reduce a limit only after showing that the removed tail is unnecessary rather
than truncating the response and tuning against an incomplete artifact.

Ceilings and timeouts are safety controls, not latency remediations. Stage
timings must first distinguish local orchestration, validation, Root transport
and queueing, provider time-to-first-token, provider execution, output volume,
and repair. A timeout may enforce a measured SLO after diagnosis; lowering it
must never be reported as a performance improvement.

Provider-native schema-constrained output or typed tool calls are preferred.
Text JSON extraction and bracket repair remain compatibility behavior and are
not a success path for the target architecture.

Validation reports all independent model-correctable findings in one bounded
packet before a repair is considered. Sequential fail-fast disclosure wastes
calls and permits a repair to expose a defect that already existed in the
primary candidate. Deterministic normalization is allowed only for
unambiguous representation aliases and is retained as evidence; semantic
invention remains a model/user decision.

## Component Contract And Client Growth

### Locale And Interaction Policy

An initial Prototype uses the user's current language. Stable localization
keys and scenario-owned `assets/i18n/<locale>.json` remain part of compilation;
generating a second language is not an implicit requirement. An explicit
multilingual request or an existing authored translation set expands the output
locale contract. Template title translations are not evidence that the user
requested a bilingual application. Locale merging must never discard authored
messages merely because another language lacks the same key. Completeness is
validated for requested output locales, independently of storage preservation.

Surface choice remains a reversible model decision, guided by generic UX
recommendations rather than a mandatory layout. A short edit can use a modal;
context-sensitive work can use a side sheet. Collection selection opens the
single editor directly when there is no details view or linked collection; otherwise selection
reveals details and editing starts there. The compiler owns selection ordering,
form hydration, save/error, dismissal and responsive region placement. An
independent footer full of duplicate editor-open buttons is not necessary.

The layout vocabulary should be small and composable, not a catalog of complete
application templates: linear flow, collection workbench, master/detail,
hierarchical navigator, board, equal-priority overview grid, and tabbed tasks.
Focused editors (modal, side sheet, inline) and compact query toolbars are
independent choices. These are design recommendations and possible user
preferences; only implemented compiler capabilities may be advertised as
executable. A hierarchy or board is not a domain classifier. Responsive collapse
and explicit row intent belong to each primitive's contract. Combinations are
open-ended, so this vocabulary does not prescribe a fixed number of screens or
force a short prompt to produce a maximally detailed application. Large-project
planning, nested composition and learned personal preferences remain deferred.

Media requires an explicit binding to a renderer capability. A filename or a
details property list is not a viewer. Image/video/audio loading and unavailable
states must be observed on the actual viewer; collection fixture predicates
alone cannot prove them. Built-in generic samples may provide reproducible
offline preview data, with attribution and no domain-specific application logic.

Lexical interpretation is fallible: actor names and display-state nouns must
not become mandatory mutation or attachment-capture operations. The original
user statement remains available alongside derived references. Qualifying a
relationship assignment must allow creation of a link as well as updating a
foreign key; a fixed CRUD verb mapping is not an outcome model.

Explicit exclusions carry their original evidence separately from required
outcomes. They must not become mandatory interactions, capability gaps or
Automation debt. Diagnostics should report all independently checkable binding
constraints together, so a repair does not discover one missing edge per call.
Literal field identifiers and nested data paths must have consistent resolution
across compilation, resource queries, display and command payloads; a supported
identifier cannot render correctly in one widget and disappear in another.

Record types come from declared semantic fields, not from observed fixture
values. Empty collections and all-null optional fields retain their schema;
form-required inputs remain distinct from persistence constraints on drafts.
Numeric zero and boolean false count as filled values. Fixed transition commands
can use a read-only editor surface without inventing editable inputs.

Simple record locks are a Prototype primitive, not invented business code.
`resource.read_only_when` is evaluated against the stored record before update
or delete; changing the draft cannot bypass the lock. The compiler supplies the
matching `ui.form.readOnlyIf`, and the local resource provider enforces it.
Durable authorization and recovery workflows still belong to Automation.

Attachment fields store scoped references, not binary JSON or metadata pretending
to be a file. Preview-owned content-addressed storage receives authenticated
uploads with per-file and per-resource limits; forms retain the previous value
on upload failure and wait before submission. Existing metadata-only forms remain
compatible unless they explicitly opt into `fileStorage=prototype`. Details can
download attachments or render sanitized Markdown, using shared Client renderers.
These local guards do not imply the deferred Root malware-scan capability.

State-only repair uses a patch contract: mutable view fields are transmitted,
while ownership, title, surface and media remain immutable by construction.
Legacy full-view repair responses remain replayable under their original schema.

Representative states are test variants of the same resource and interface,
not a requirement to create separate empty/live/sample collections. The existing
`collection_empty` fixture overrides the query response for a render test while
retaining normal seeded records. Browser receipts distinguish intercepted empty
responses from real provider-backed mutations. This follows the separation of
[UI state fixtures and interaction tests](https://storybook.js.org/docs/writing-stories/mocking-data-and-modules/mocking-network-requests),
without adding Storybook or a second runtime to Builder.
`field_predicate` can be observed in a collection, selected-record details or
an editor. Its receipt identifies matching records and a selected-record fixture
when appropriate. Collection-empty and query-empty proofs still require an
actual collection. Proof/view compatibility is part of the shared model-visible
rules and first-pass diagnostics, not a hidden late compiler restriction.

Relationship form fields query current target records instead of embedding a
seed-time enum. Options refresh after target invalidation without replacing
the edited draft; unavailable sources block submission. The Preview provider
checks references under its registry mutation lock, including inverse references
on target deletion, scoped to the same project and WebUI revision. Current
collection filters and relationship display labels still need live lookup
qualification; a dynamic form alone does not qualify all relationship workflows.
Lookup-only resources may omit standalone views when another reachable editor
consumes their relationship selector. They still materialize typed read-only
records and undergo the same identity/reference validation. Relationship
`label_field_refs` chooses target fields safe to display; existing target collection
labels or the target identity provide backward-compatible fallbacks. Never infer
private display fields or fabricate a collection solely to satisfy lowering.
Unreachable resources and details without a selection path remain invalid.
Relationship direction follows cardinality: `many_to_one` references a unique
target, while `one_to_many` references the unique parent from each child.
Implicit record identity is valid at either end. `one_to_one` additionally
enforces unique nonempty source values, both in fixtures and under the runtime
mutation lock. Many-to-many uses a separate link resource, not a scalar edge
whose cardinality the provider ignores. Inverse label fields describe the
authored target; they must not be used to disclose unrelated parent fields.
Prefer immutable record IDs over display names. The current provider rejects
renaming a referenced natural key rather than silently breaking its links;
acceptance must test requested rename workflows, not just initial fixtures.
Materialization and final postcondition checks share a typed query-slot walker:
both page/modal widget sources and form-field `optionsDataSource` participate.
Example JSON or metadata is not an executable dependency. Sidecars must match
all and only the queried resources. Explicit label fields remain complete even
when the first seed label happens to be unique.

Candidate field names are resource-local. Repeated names are deterministically
qualified with their owning resource before runtime lowering, with normalization
receipts and preserved raw responses. Typed local references, relationship ends
and state proofs use the same owner map; fixture text and record identities are
not renamed. Requirement bindings with a repeated field name must identify one
owner or use the qualified field ID. Ambiguous bindings and namespace collisions
are errors, not opportunities to guess domain semantics.
Owner-qualified aliases also resolve unique local fields. Literal dotted IDs
must not silently take precedence over another owner's qualified alias; an
ambiguous binding needs an explicit owner. Independent unresolved bindings are
reported together. Details-state evidence may include a field actually rendered
by its native media source/poster binding, but not a hidden media-kind selector.
The implicit record `id` is metadata shared by resource schemas, not a business
field requiring globally unique spelling. An explicit `id` display field must
be read-only string metadata and agree with its record identity. Normalize that
value and its typed identity predicates together; never rename unrelated text
or reconcile genuinely different identities. Positional fixture-arity failures
report every affected resource/row with the expected field order.

An explicitly bound collection closes over its owned query controls for the
requested operation kind. Do not infer which collection the user means from
several resource-owned collections. This closure records existing capabilities;
it cannot create a missing control or satisfy semantic outcome grading by itself.
Editor-local editability follows command inputs, not just the resource field's
global flag. Fields ignored by all local commands remain read-only context;
an unambiguous fixed value supplies their default. Changing such a control must
never appear to affect a command that actually discards the change.

A consumed quoted application title is authoring metadata, not an application
operation or residual implementation requirement. Preserve the exact original
request and the audience/outcome following the title. Changing only a generated
test suffix must not change required semantic-reference enums. This reduces
accidental context variation; cache-hit or latency gains require measurement.
Repair context names its envelope from the SDK's actual output schema, rather
than a second version constant in adapter prose. Validate system text, stable
context and transport schema together, including compatibility versions.
State repair v3 has explicitly additive view patches: `add_field_refs` and
`add_query_controls` preserve existing entries, and `empty_state=null` preserves
the existing empty presentation. Existing query IDs cannot be replaced by an
additive patch. Reported states remain full replacements; unrelated states,
fixtures, commands and bindings remain immutable. Replay keeps the original
v1/v2 semantics instead of reinterpreting old outputs as v3.

Provider schema projection must not hide Core authoring constraints. The
portable projection supplies omitted nested assertions from the authoritative
ABI in generation guidance, including label-field cardinality. More capable
provider profiles may retain supported assertions directly after an actual
Root/provider canary; a quota failure proves neither support nor incompatibility.
Measure the additional context and avoided repairs together. Do not lower
output budgets merely because a complete response is larger than expected.

The independent grader receives revision-bound resources and executable provider
policies, not just visible controls or schema property descriptions. Its pointer
index must include stored-record locks while avoiding a catalogue of nested
schema types as supposed interaction evidence. Preserve UTF-8 input, submission,
terminal or partial response, usage and failure identity separately for each
case attempt. Keep the pre-interaction artifact immutable across grading retries.
Model options, including output budget, belong in request identity. Grader
version changes invalidate direct score comparisons; regrading retained artifacts
is separate from fresh generation and never overwrites the original cohort.

Project Conversation is a durable transcript, not a side effect of a running
browser or Router subscriber. Builder explicitly persists scoped IO chat before
requesting its live projection, using the canonical message/progress identity.
Creation ingress is assigned after the new project identity exists; other ingress
uses the selected project. Technological entrypoints retain a stable identity per
turn/step, not per project. A scheduled asynchronous append is not a persistence
acknowledgment. Browser-originated ingress is not appended again by Builder.

A deferred computation still requires representative output values when those
outputs are requested. Raw inputs plus an explanatory paragraph are not an
inspectable result. Single-record guards cannot stand in for a predicate over
related records. Both distinctions must be visible to the model before generation
and retained as testable Automation obligations, without injecting domain code.

Normalization may decode an exact JSON scalar string according to its declared
number/boolean type and map an optional empty scalar to null. Exact JSON arrays
of strings may likewise be decoded for attachments/multiple choices only.
Plain text, malformed JSON, object arrays and CSV are not reinterpreted. It must preserve
raw model evidence, record each conversion, and never infer units, parse a timecode
as seconds, or rewrite textual identifiers. Validation still rejects ambiguous
or incompatible values; another model call is not needed merely to remove quotes
from an otherwise valid typed numeric literal.

Evidence bindings close over declared command/view/resource ownership. Collection
requirements can inherit a unique owned collection/editor; a search/filter can
inherit its unique query control from a bound view or resource. Ambiguity requires an explicit
binding. Closure never creates views, commands or business rules and is recorded
in normalization evidence. Automation still requires a visible view/state, not
a resource-only assertion. Request-specific provider enums constrain requirement
references to the accepted inventory. Measure schema-cache loss against avoided
repairs; do not assume a dynamic grammar is free.

Every Builder-visible Client component must publish one versioned component
contract beside its implementation. The contract is the source for Builder
retrieval, Core validation, Client registration, documentation, fixtures, and
compatibility tests. It declares:

- semantic roles and supported view/data shapes;
- properties, defaults, events, actions, and side-effect class;
- responsive and accessibility behavior;
- localization and long-content behavior;
- loading class (`shell`, `common`, `on_demand`, or `heavy`);
- compiler mapping and supported semantic operations;
- compatibility range, deprecated members, and migration rules;
- representative fixtures and conformance tests.

Generated indexes replace handwritten parallel registries. Non-shell
components load through async factories by capability. A component ABI change
must produce an impact report naming affected semantic compiler mappings,
Builder fixtures, scenarios, and Client tests before it can be accepted.

A bounded Client integrity pass is an input to the first clean generic
baseline: remove product branches from generic runtime paths, make semantic
lowering total for every advertised capability, return structured diagnostics,
and provide truthful contracts for components used by the baseline. Building
new archetype-specific or cross-domain components is not a baseline
prerequisite. Those additions must be justified by measured capability gaps
after the baseline. The exact sequence is owned by the
[Client Component System Roadmap](client-component-system-roadmap.md).

## Evaluation And Claims

Evidence uses explicit maturity labels:

- `renderer-qualified`: a known runtime artifact renders and behaves correctly;
- `recipe-guided`: Builder materializes a selected detailed domain pack;
- `brief-compiled`: Builder derives a reviewed brief and semantic design;
- `prompt-autonomous`: an ordinary unseen prompt reaches a useful Prototype
  within the declared clarification and repair budget.

An evaluation prompt cannot select or contain a subject-specific recipe used
to grade it. Test domains, wording, and component combinations must be held out
from prompt/profile development. Tests measure at least:

- intent and operation coverage;
- useful clarification versus unnecessary questions;
- semantic and runtime validity;
- primary-task completion in compact and wide layouts;
- English/Russian and long-content behavior;
- first-pass quality and repair count;
- fresh/cached/output tokens, model calls, latency, and deterministic work;
- preservation of unrelated accepted behavior;
- correct capability-gap and authority decisions.

Suites, cases, resolved runs, results, metrics, browser evidence, and immutable
baseline comparison are defined by the
[Builder E2E Evaluation Pipeline](builder-evaluation-pipeline.md). Normal
Builder users do not manage these records. Engineering and CI use the single
`adaos builder e2e` chain for complete or selected declarative cases.

Applications `027` is currently `renderer-qualified` and
`recipe-guided`. The generic-path experiment is not `prompt-autonomous` because
the selected recipe supplied the domain structure, staged plan, and detailed
postconditions.

## External Engineering Baseline

The design follows current provider guidance to keep model context minimal and
high-signal, retrieve detail just in time, expose clear non-overlapping tools,
use schema-constrained outputs, and keep stable prompt prefixes ahead of
dynamic content:

- [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model)
- [OpenAI Evals](https://platform.openai.com/docs/guides/evals)
- [Playwright trace viewer](https://playwright.dev/docs/trace-viewer)
- [Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Anthropic building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [Angular deferred loading](https://angular.dev/guide/templates/defer)

These are engineering inputs, not AdaOS runtime dependencies. The contracts
remain provider-neutral and are qualified against the configured model
profiles.
