# Builder Intent-to-Prototype Architecture

Status: corrective target architecture. The current implementation is a
transitional, recipe-guided prototype path and does not yet satisfy this
contract.

Last reviewed: 2026-09-11.

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

### 3. Adaptive Prototype Plan

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
