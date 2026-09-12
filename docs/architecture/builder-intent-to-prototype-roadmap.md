# Builder Intent-to-Prototype Roadmap

Status: active corrective roadmap. It blocks new claims of generic Builder
Prototype autonomy.

Last reviewed: 2026-09-11.

Architecture: [Builder Intent-to-Prototype Architecture](builder-intent-to-prototype.md).
Parent lifecycle roadmap: [Builder Roadmap](builder-roadmap.md).
Evaluation contract: [Builder E2E Evaluation Pipeline](builder-evaluation-pipeline.md).
Client dependency: [Client Component System Roadmap](client-component-system-roadmap.md).

This roadmap replaces incremental expansion of the current prompt/recipe path
with a typed intent-to-Prototype compiler. Historical Builder lifecycle,
publication, and renderer evidence remains valid in its stated scope. It does
not close the gates below.

## Priority Rules

- `must`: required before generic prompt-autonomy or further subject-specific
  Builder dogfood claims;
- `should`: required for a maintainable and cost-effective supported path;
- `could`: useful only after must metrics are established;
- `deferred`: explicitly outside this correction.

No task is complete because a schema or test exists. Completion requires the
declared behavior through the public SDK and a retained evaluation result.

## Stop Conditions

Active scope (2026-09-11): reliable small prototypes, then their complete Builder
lifecycle including Automation. Large-prototype orchestration is deferred, not a
prerequisite for the small-application gate. Current work is limited to acceptance,
contract consistency, generation context and minimal visibility in existing Builder.

### Current Iteration Checklist

- [x] `[must]` Separate minimum-working acceptance from non-blocking design
  quality. Unspecified richness, screen count and domain conventions must not
  become mandatory criteria; preserve explicit outcomes and runtime invariants.
- [x] `[must]` Calibrate the eight development archetypes' outcome/state/exclusion
  criteria against exact user requests; retain positive and negative grader probes.
- [x] `[must]` Align candidate/canonical capacity, attachment, relationship and
  representative-state contracts with cross-domain executable regression tests.
- [x] `[must]` Supply an exact required-reference inventory, contextual operations
  and an executable-contract-derived invariant catalog to generation and repair.
- [x] `[must]` Retain dev-only test applications in Builder with searchable
  `[TEST]` and date/unique suffix; prepare the exact revision's owner-managed preview.
  Keep generated, failed and accepted states distinct. Do not publish beta/stable.
- [x] `[must]` Run the full eight-case GPT-5 cohort after local replay tests;
  inspect complete inputs/outputs and actual browser previews, record unresolved debt.
- [x] `[must]` Consolidate the standalone stage review into architecture and roadmap
  ownership; keep measurements in this checklist's evidence, not a parallel report.
- [ ] `[should]` Follow with targeted semantic repair and preservation checks only
  after the current acceptance/context/contract iteration is measured.

Until R8 is complete:

- do not add another subject-specific recipe, lexical domain branch, prompt
  phase, or postcondition block to generic Core or `builder_skill`;
- do not use Applications or another recipe-guided result as evidence of
  prompt autonomy;
- do not start Automation for the corrective reference application;
- do not delete the current path before shadow replay and rollback evidence;
- continue only defect fixes required to keep the current Builder operable and
  observable.

## R0. Forensic Snapshot

Progress note (2026-09-10): the reproducible
[legacy forensic snapshot](builder-forensic-snapshot-2026-09-10.md) freezes
selected Core, DEV Builder, Client, and Applications trees plus canonical
revision usage and repair evidence. It also records ownership/disposition and
correct evidence labels. End-to-end stage spans, immutable compact/wide Client
traces, dynamic reachability, and the executable legacy characterization suite
remain open, so R0 is not yet complete.

- [ ] `[must]` Freeze an exact forensic snapshot of the contaminated current
  path: code revisions, prompt profiles, component catalog, model profiles,
  Applications inputs/outputs, token usage, latency, repairs, and validation
  results. Label it `legacy_recipe_guided`; it is not an autonomy baseline.
- [ ] `[must]` Inventory every subject-specific classifier, recipe, example,
  default, postcondition, repair rule, and test fixture reachable from the
  generic Prototype route. Record its owner and migration disposition.
- [ ] `[must]` Label retained evidence as `renderer-qualified`,
  `recipe-guided`, `brief-compiled`, or `prompt-autonomous`. Correct status
  projections that currently collapse these claims.
- [ ] `[must]` Record current ownership and duplication across Builder skill,
  Core workflow, capability selection, LLM adapter, SDK, and Client registries.
- [ ] `[should]` Add a reproducible trace report that separates orchestration,
  context compilation, provider queue/inference, validation, checkpoint,
  runtime refresh, and browser render time.
- [ ] `[must]` Freeze the current Client registry, semantic adapter, generic
  runtime branches, component coverage, build digest, and compact/wide
  compatibility traces as required by Client roadmap C0.

Exit gate: existing behavior is reproducible and all known contamination is
classified. This is the minimum evidence needed to clean safely, not an
autonomy baseline.

## R1. Domain Decontamination And Compatibility Seam

Progress note (2026-09-10): generic UI capability selection and prompt-rule
selection now start with no domain packs. Applications and research policy was
extracted into versioned compatibility packs; the Applications evaluator is
retained behind an explicit adapter. DEV Projects may bind packs through
`development.domain_packs`, and historical recipe receipts resolve through the
pack registry. Product wording and IDs no longer activate a pack. The
Applications DEV experiments were migrated explicitly. Generic boundary tests
and the retained legacy characterization suite pass. Every current DEV Builder
provider route now writes the exact local request journal plus a compact
`adaos.builder.llm_input_attribution.v1` receipt before network submission.
The receipt content-addresses every message, stable and dynamic segments,
selected contracts/patterns/examples, domain packs, and sanitized generation
options. A new provider route without this receipt reopens R1.

- [x] `[must]` Add characterization tests around the legacy Applications and
  other subject-guided paths before moving them.
- [x] `[must]` Classify catalog entries as atomic component contracts, generic
  composition patterns, or subject domain packs. A board, form, or
  master-detail pattern may remain generic; a finished application's
  information architecture, vocabulary, operations, fixtures, or grading
  rules may not.
- [x] `[must]` Move `recipe.application_manager`, Applications-specific
  vocabulary, lifecycle defaults, phases, and postconditions out of generic
  Core into a versioned compatibility/evaluation domain pack.
- [x] `[must]` Remove shopping-list, todo, recipe-book, and other product-title
  heuristics and examples from the generic Builder execution path. Retain them
  only in explicitly selected development fixtures or domain packs.
- [x] `[must]` Add a generic-profile policy that supplies no domain packs and
  rejects hidden selection by subject ID, project name, prompt wording, or
  postcondition prefix.
- [x] `[must]` Persist an input-attribution receipt proving which component
  contracts, generic patterns, examples, and domain packs were available to
  every model run.
- [x] `[must]` Preserve current user-visible behavior behind one explicit
  legacy adapter and project-scoped profile switch. New generic code must not
  call its private helpers.
- [x] `[should]` Add a static boundary test and allowlist that reject subject
  identifiers and localized product vocabulary in generic Core prompt,
  qualification, catalog, and validation modules.

Exit gate: generic mode cannot retrieve or execute subject-specific recipes,
heuristics, examples, or evaluators, while the explicit legacy profile remains
available for regression and rollback.

The clean baseline also requires Client roadmap C1 and the truthful C2
contract subset for every component reachable by baseline cases. The broader
component-growth phases do not block it.

Client prerequisite progress (2026-09-10): the Client now emits a
content-addressed capability inventory and has extracted the first Builder,
Infrastate, and NLU action branches into explicit multi-provider extensions.
This narrows contamination but does not satisfy the prerequisite: modal/data
recovery and generic-widget domain branches remain, and the inventory exposes
six advertised semantic kinds without a lowering implementation.

## R2. Clean Generic Baseline

Implementation note (2026-09-10): the first ABI/runner slice and model-free
self-tests exist. It deliberately uses a recorded `legacy_dev_chat.v1`
compatibility adapter because the R3 public SDK seam does not yet exist. This
resolves the sequencing dependency without weakening the clean-baseline gate:
legacy-adapter runs are characterization evidence only, and the unchanged
cases must later execute through `sdk.v1`.

The first live generic creation case now validates a scenario and proves
bounded cleanup of the exact Builder draft, DEV component, and Project
aggregate. Cleanup is fail-closed: a negative receipt makes an otherwise
successful case inconclusive. Large redacted step outputs are retained as
compressed digest-bearing evidence instead of being embedded in the case
result. The measured run remained compatibility evidence: it made no model
call and took about 14 seconds, dominated by Builder chat and session reads.
This identifies process/tool startup and missing internal stage spans as
development measurements, not as acceptable target latency.

The hardened runner now uses case/repetition-specific webspaces, pre-step and
post-step checkpoints bound to the immutable run manifest, validated resume,
explicit bounded retry policy and attempt history, actual model-input receipt
checks before scoring, and per-step-type latency. A completed resumed run
returns its existing report byte-for-byte. A second local Notes run passed in
19.3 seconds with zero model calls and exact cleanup; 15.6 seconds was the
legacy chat adapter and 2.55 seconds a second runtime session read. These are
compatibility-adapter overhead, not target SDK latency.

The visible development suite now has eight EN/RU archetype cases expressed as
ordinary user turns. Its first live operations-queue case was deliberately
inspected and is development evidence, not a holdout. Successive runs exposed
and fixed case-webspace propagation, terminal-job synchronization, compact
Project ownership receipts, and usage double counting. The hardened run then
failed for a substantive reason: a generated board combined a static source
with a mutating move action. The exact three model inputs were attributed to a
generic profile with no domain packs. The failure is retained as evidence for
R4/R5 rather than hidden by a fixture-specific rule.

The suite now requires an independent post-generation `prototype.grade` step.
Its oracle data cannot enter Builder generation context, exact evidence
pointers and request digests are retained, and grader usage has separate
metrics. A frozen RU equipment candidate that passed structural validation was
correctly rejected at `0.425` because its checklist was static and photo,
completion guard, and requested states were absent. This closes the immediate
false-green path but is not the R2 outcome gate: the model grader is not yet
calibrated against human labels and deterministic compact/wide browser jobs
remain open.

The public CLI smoke `cli-model-free-20260910-01` executed the declared
`runner-observation` case through `adaos builder e2e`, retained a schema-valid
bundle, and passed both required steps in 18.4 ms with zero model calls. The
focused runner, grader, evaluation, and governed-flow suite passes 33 tests,
including selection, isolation, resume identity, bounded retry accounting,
cleanup failure handling, evidence compaction, attribution, and baseline
comparison. The CLI item remains open only because the current live adapter is
`legacy_dev_chat.v1`; public SDK transitions are an R3 dependency.

Clean-profile suites now set `require_client_profile=true`. Before provisioning
or any model call, the runner compares every Core catalog component type with
the content-addressed Client capability inventory and rejects a missing
generic/shell runtime registration. `cli-client-profile-20260910-01` passed
this gate locally in 16.0 ms. Its run manifest records the Client commit,
inventory digest, Core catalog version/digest, complete generic runtime type
set, and unsupported semantic kinds. This is a reproducibility and fail-closed
compatibility gate; it does not replace the C2 single-source contract.

The first retained clean-profile outcome probe,
`baseline-generic-equipment-ru-20260910-01`, failed its independent grade at
`0.45625` after passing creation, generation, job synchronization, and schema
validation. Its single Builder model call used 5,713 fresh input and 823 output
tokens; no cached tokens were reported. Inspection of the exact request found
unconditional Application CAS instructions, board-only acceptance rules for a
non-board selection, an incorrectly reusable variable "stable" segment, and a
brief that omitted explicit states and capability gaps. The failed candidate
is retained unchanged and these are architecture inputs, not case-specific
prompt tuning.

Later equipment-inspection runs removed those contaminating inputs and exposed
two distinct contract defects. First, the phrase `complete.prototype_records`
was interpreted as a WebUI JSON Pointer, so representative records entered the
renderer patch and one response wrapped them in a transport envelope. The
compatibility contract now states that patches address only WebUI and direct
records belong to a terminal JSONL sidecar; validation rejects envelope-shaped
records, and repair preserves already valid sidecars independently of the
candidate document. The fresh
`generic-equipment-terminal-sidecar-20260910-01` run then reached schema and
postcondition validation, materialized four direct records and EN/RU assets,
and received an independent score of `0.89375`. It still failed the hard task
gate: checklist rows were read-only and no explicit per-inspection defect state
could be edited. The run took 77.3 seconds and four Builder model calls (15,772
fresh input, 5,632 cached input, and 4,467 output tokens), plus one 9.2-second
grader call. This is valid capability-gap evidence, not a pass. It also shows
that repeated repair context can grow to about 96 KB and that malformed JSON
Pointer repair remains a material legacy-path cost.

- [x] `[must]` Publish and validate the declarative E2E suite, case, resolved
  run, case-result, report, and immutable baseline contracts defined by the
  [Builder E2E Evaluation Pipeline](builder-evaluation-pipeline.md). Reuse
  existing Builder evaluation evidence and workflow metrics as referenced
  sources rather than duplicating them.
- [x] `[must]` Implement one technological CLI entry, `adaos builder e2e`, for
  complete suites or selected cases/tags. It must validate, provision isolated
  DEV state, execute public SDK transitions, collect evidence, compare an
  optional baseline, report, and clean up without exposing low-level eval CRUD
  commands to normal Builder users.
- [x] `[must]` Prove runner isolation, deterministic case selection,
  interruption/resume, retry accounting, cleanup, artifact retention, and
  matched-run comparability with model-free self-tests.
- [x] `[must]` Build a visible development suite from the application
  archetypes already used for Client/component analysis. Use it to debug the
  harness and metric collectors; never call it held-out.
- [ ] `[must]` Define and seal an evaluation set of at least 40 ordinary EN/RU
  prompts across at least eight different held-out application archetypes.
  Keep prompts free of component IDs, AdaOS paths, recipe names, and
  implementation phases. Do not expose or tune generation, retrieval,
  components, prompts, or repair rules against the sealed prompts.
- [ ] `[must]` Add mutation cases for ambiguous operations, missing authority,
  conflicting requirements, long content, unsupported capabilities, and
  preservation of unrelated behavior.
- [ ] `[must]` Run the sealed 40-prompt EN/RU set through the decontaminated
  generic profile with fixed model/profile settings and domain packs disabled.
- [ ] `[must]` Verify input-attribution receipts automatically before scoring;
  any subject-pack, held-out example, or product-specific postcondition makes
  the run invalid rather than merely lowering its score.
- [ ] `[must]` Record intent coverage, clarification behavior, structural and
  runtime validity, browser task success, preservation, capability-gap
  accuracy, model calls, fresh/cached/output tokens, repairs, and latency.
- [ ] `[must]` Record actual billed and normalized uncached-equivalent cost,
  per-stage latency, browser/Client readiness, reliability across repetitions,
  and exact environment/component/profile digests. Keep warm/cold cache and
  connected/degraded network runs in separate cohorts.
- [ ] `[must]` Keep failures unchanged during the baseline run. Convert them
  into a taxonomy and prioritized architecture requirements only after the
  complete run finishes.
- [ ] `[must]` When sample-level prompts or results are inspected for
  remediation, reclassify that immutable set from sealed holdout to regression
  and seal a different successor cohort for later prompt-autonomy claims.
- [ ] `[should]` Run the explicit legacy recipe-guided profile as a separately
  labelled control under matched model settings. Do not rank it as generic
  autonomy.

Exit gate: AdaOS has the first uncontaminated measurement of what the current
generic component vocabulary and configured model can achieve. Later stages
must demonstrate measured improvement against this baseline.

## R3. Ownership And Public SDK Seams

Progress note (2026-09-10): `adaos.sdk.builder.intent` is the first target
SDK slice. It exposes exact intent admission and deterministic Prototype Brief
compilation while keeping schema validation and interpretation in Core. The
generation, candidate, evaluation, and artifact-write operations still use the
legacy skill path, so the R3 gate remains open.

- [ ] `[must]` Define public SDK operations for Prototype request admission,
  brief inspection/confirmation, plan execution, candidate status, and
  evaluation. Builder skill calls only these operations.
- [ ] `[must]` Extract provider request/response, structured-output, retry, and
  usage handling into a provider-neutral LLM adapter owned below the SDK.
- [ ] `[must]` Move artifact writes, locale writes, revision creation,
  validation, checkpoint, and runtime refresh out of `builder_skill` into Core
  services with explicit transaction boundaries.
- [ ] `[must]` Move request interpretation orchestration out of
  `services/ui_capabilities.py`; keep that service limited to generic contract
  indexing, selection, validation, and compilation support.
- [ ] `[should]` Reduce `builder_skill` to a thin conversation/projection
  adapter after ownership moves; set and enforce a module dependency rule.
- [ ] `[should]` Split Core services by contracts and authority, not merely by
  file length. Remove duplicate state transformations after parity evidence.

Exit gate: a dependency test proves
`Builder skill -> public SDK -> Core ports/services -> adapters`, with no
domain-specific generic-Core branches.

## R4. Intent And Prototype Brief

Evidence note (2026-09-10): the first visible cross-domain case showed that
the current generic qualifier can return `surface_kind=unspecified` and an
empty requirement set even when the prompt explicitly names primary jobs,
fields, operations, and workflow states. Lexical capability selection then
admits patterns whose data/action invariants are not grounded in a typed
brief. This is a compiler-boundary defect, not evidence that the prompt needs
component terminology.

Implementation note (2026-09-10): Core now validates content-addressed
`adaos.builder.intent.v1` and `adaos.builder.prototype_brief.v1` records. The
deterministic compiler preserves the exact statement, source/scope/authority,
locale, explicit refs, unknown fields, evidence spans, recognized generic
operations, principal job clauses, and explicit workflow states. It excludes
Builder authoring commands such as "create an application" from the
application's own operation set. Generic capability selection consumes these
facts before lexical fallback. Residual model interpretation, accepted-field
persistence, ambiguity policy, and user confirmation remain open.

Progress note (2026-09-11): operation clauses are now split into ordered atomic
principal jobs with exact character evidence. Representative-state compilation
aggregates workflow and later visual/exception states instead of returning on
the first match, and it distinguishes state absence from interaction-continuity
phrases such as "without losing the queue". The visible operations case now
gives the model five atomic jobs and six explicit states and passes from one
generation with no repair. This improves explicit-clause coverage only;
schema-constrained residual interpretation, accepted Brief persistence, and
clarification policy remain open.

RU/EN parity note (2026-09-10): authoring exclusion is now span-aware rather
than clause-wide. A single ordinary turn may say "create an application" and
then name its actual operations in the same sentence; only the Builder command
span is excluded, while later operation evidence keeps its exact source
offsets. The generic lifecycle vocabulary covers common close/complete,
submit/approve/reject/cancel, and status-change forms in both locales. Tests
also prohibit lexical prefix leakage such as interpreting `marketplace` as a
`mark` operation. All four visible RU development archetypes now compile a
non-empty operation set without introducing subject entities into Core.

Atomicity follow-up (2026-09-11): deterministic operation anchors now split a
compound clause into separately addressable principal jobs with exact source
character ranges. Adjacent aliases for the same operation without a linguistic
separator remain one job, while comma/conjunction-separated actions remain
distinct. Representative-state extraction reuses those job statements when
possible. For the visible operations prompt this changes one broad job into
five explicit jobs: scan, inspect one item, create, assign, and transition.
This removes the contract ambiguity in which one requirement could be partly
bound and partly missing. It does not infer actors, entities, targets, or
authority; those remain inputs to schema-constrained residual interpretation.

Cross-entity evidence (2026-09-11): retained run
`sdk-retained-context-20260911-01` shows the remaining Brief defect without
attributing it to the design model. The exact input kept entities and
information requirements unknown, split one clause into the incomplete job
`assign or`, and packed four materially different visual states into one
requirement. The original user turn was still present, so the model recovered
most intent, but requirement binding and architecture selection were not
grounded in a reviewable contract. Residual schema-constrained interpretation
must identify independent record types, relations, outcomes, and state
requirements before semantic generation; lexical clauses alone are not the
target Brief compiler.

Disclosure evidence (2026-09-11): runs `sdk-semantic-v2-20260911-11` through
`-13` show that the deterministic Brief still sends `actors`, `entities`, and
`outcome` as unknown and has no typed disclosure/visibility requirement for
"open contact details only when needed". A general semantic-design rule and a
freshly activated DEV runtime moved contact fields from the collection into a
details view, but one candidate still invented a persisted reveal field. R4
must therefore add a versioned residual-interpretation result for entity,
relationship, information-hierarchy, disclosure, and authority facts with
source spans. It is invoked only for material unresolved fields; low-ambiguity
requests retain the deterministic/one-call route. Its stable contract precedes
the dynamic evidence suffix for cache reuse, and its output is independently
validated before semantic design.

- [x] `[must]` Publish `adaos.builder.intent.v1` and
  `adaos.builder.prototype_brief.v1` schemas with provenance and `unknown`
  semantics.
- [x] `[must]` Implement deterministic extraction for explicit refs, locale,
  source Project/Change, accepted requirements, authority, and recognized
  operation verbs before model interpretation.
- [ ] `[must]` Implement schema-constrained brief compilation for the residual
  user language. The model cannot emit Client components or JSON paths at this
  stage.
- [ ] `[must]` Add an ambiguity/risk policy that asks only questions capable of
  changing primary UX, authority, data ownership, or architecture. Cap each
  clarification turn at three focused questions.
- [ ] `[must]` Persist accepted brief fields and assumptions by semantic ID;
  later user changes produce a delta and do not replay the raw conversation.
- [ ] `[must]` Expose a short localized user confirmation for material
  assumptions without presenting internal schemas or stages.
- [ ] `[should]` Support incremental brief repair when validation identifies a
  missing requirement; do not regenerate accepted fields.

Exit gate: development prompts produce reviewable briefs with measured coverage,
clarification precision, and no renderer vocabulary in the model output.

## R5. Component Contract And Capability Resolution

Progress note (2026-09-10): retained live runs proved that summaries and broad
recipes are insufficient for small but fatal ABI details. Catalog 2.0.2 now
exposes the exact `collection.board` resource-query and button shapes, while
the legacy parser records bounded canonicalization of common representational
aliases. This is evidence for the single-source component contract below, not
its completion: the same facts still exist separately in schema, Client,
catalog, parser, and tests.

The clean RU equipment development case does not yet establish a Client
capability gap. The generated design chose an inspection as its collection
item and collapsed the requested checklist into one `longText` field, even
though the existing generic list/form composition could instead model each
check as a resource record. A nested repeatable form group remains a C2/C4
candidate only if a flat generic composition cannot meet the accepted brief
or a second unrelated archetype demonstrates the same nested semantic need.
The immediate blocker is explicit data granularity and requirement bindings in
the brief/semantic compiler, not admission of a case-shaped component.

- [ ] `[must]` Publish the next component-contract/catalog schema as one source
  for semantic role, properties, events, actions, state, i18n, accessibility,
  responsive behavior, loading class, compiler mapping, compatibility, and
  tests.
- [ ] `[must]` Generate Client registration, Core validation index, Builder
  retrieval units, and documentation indexes from component contracts. Reject
  conflicting handwritten declarations.
- [ ] `[must]` Implement deterministic filtering by brief data shapes,
  operations, authority, view roles, and constraints before optional semantic
  ranking.
- [ ] `[must]` Expose an actual bounded read-only retrieval operation for exact
  selected component contracts and examples. Remove textual drill-down claims
  when no tool is supplied.
- [ ] `[must]` Return a typed capability gap when no admitted composition can
  satisfy a required brief field.
- [ ] `[must]` Add an ABI impact gate that names affected compiler mappings,
  scenarios, Builder evals, migrations, and Client tests for every component
  contract change.
- [ ] `[must]` Complete Client roadmap C2/C3 for admitted components and derive
  the first cross-domain component tranche from clean-baseline gaps rather
  than from the current sealed release holdout.
- [ ] `[should]` Add held-out retrieval evaluation and compare deterministic,
  lexical, and model-assisted ranking under equal token budgets.

Exit gate: a new component can be registered once and becomes retrievable,
validatable, compilable, documented, tested, and loadable without parallel
manual tables.

## R6. Semantic Prototype Compiler

Progress note (2026-09-11): semantic commands can now carry localized
confirmation text through compilation into the Client action runtime. For
backward-compatible semantic-v1 documents, transition and delete commands
receive a generic fail-closed confirmation default. This fixes the observed
direct-transition omission without teaching Core an application domain. It is
an interim safety policy: R4/R5 must make authority, reversibility, and risk
explicit so the compiler can derive confirmation from accepted semantics
rather than command kind alone.

Semantic-v2 progress (2026-09-11): the provider-native candidate contract now
supports up to four independent resources, globally unique typed fields,
typed relationships, collection presentations, commands, structural state
proof, exact requirement bindings, and capability gaps. The deterministic
compiler canonicalizes IDs, resolves record-id foreign keys, maps
relationships to typed Client selectors, compiles boolean filters without
string coercion, preserves source metadata, and rejects fixture/type/reference
defects. Model-correctable resource, relationship, record, state, and command
ownership findings are aggregated before one bounded repair. This is a real
vertical slice, but the complete semantic ABI, authority model, incremental
semantic edits, and adaptive plan remain open.

Semantic presentation evidence (2026-09-11): the bootstrap candidate now
declares `list`, `table`, or `cards` for collection views. The deterministic
compiler maps dense comparison to the existing `ui.table` contract and maps
every declared collection field into a visible column or list/card metadata;
it no longer drops all fields except the first title. Fresh operations run
`sdk-visible-fields-20260911-02` passed all four primary jobs and all four
states from one generation call in 32.7 seconds total. Generation used 2,890
fresh input and 2,314 output tokens, took 14.5 seconds at the provider, and did
not require repair.

The same change did not make the single-resource semantic subset generally
complete. In `sdk-retained-context-20260911-01`, a volunteer assignment record
could show existing assignments but could not truthfully represent independent
volunteers, shift capacity, an unfilled shift, or overlap prevention. Local
validation accepted a state named `unfilled shift` whose predicates actually
matched existing assignments; the outcome grader rejected the missing visible
coverage/conflict semantics. State labels and fixture counts are therefore not
proof of state meaning.

- [ ] `[must]` Replace or promote `webui.semantic.v0` with a complete versioned
  semantic document for the currently supported Prototype component set.
- [ ] `[must]` Represent entity and collection-item granularity explicitly;
  prohibit the design stage from collapsing an accepted repeated collection
  into an opaque scalar unless the brief says it is read-only text.
- [ ] `[must]` Represent multiple independently inspectable resources and their
  typed relationships when the accepted Brief requires them. Keep the
  single-resource candidate as a bounded fast path, not a universal contract.
- [ ] `[must]` Make representative-state proof structural. A visible state
  must bind to observable fields, predicates, aggregate/relationship facts, or
  an explicit capability gap; a model-authored label and matching fixture
  count are never sufficient evidence.
- [ ] `[must]` Bind every accepted brief requirement to semantic data, view,
  command, state, or typed capability-gap refs and reject unresolved bindings.
- [ ] `[deferred]` Publish `adaos.builder.prototype_plan.v1` as an adaptive DAG with
  typed inputs, outputs, dependencies, validation, budget, and route class.
- [ ] `[must]` Implement `D0` deterministic routing for supported rename, move,
  visibility, declared option, and other typed semantic edits with zero model
  calls.
- [ ] `[must]` Implement a deterministic semantic-document to `webui.v1`
  compiler, including locales, fixture declarations, responsive mappings,
  stable refs, and source maps from requirements through semantic nodes to
  emitted runtime nodes.
- [ ] `[must]` Define one-way authority: new managed Projects edit semantic
  source and compile runtime WebUI; legacy WebUI remains authoritative behind
  the compatibility adapter until an explicit reviewed conversion.
- [ ] `[must]` Reject lossy or unsupported mappings as capability gaps; do not
  approximate with arbitrary low-level widgets.
- [ ] `[must]` Make existing semantic UI changes target semantic refs and
  recompile the smallest affected unit while retaining atomic full-artifact
  promotion.
- [ ] `[must]` Validate preservation of accepted and unrelated semantics across
  incremental compilation.
- [ ] `[should]` Retain direct full-WebUI generation only as an isolated
  comparison profile until the semantic path wins quality and cost gates.

Exit gate: at least four development application archetypes compile from brief to
valid executable Prototype without a subject-specific recipe.

## R7. Context And Model Execution

Progress note (2026-09-10): DEV Builder now removes request-specific
qualification from the stable capability bundle and puts the typed brief in
the dynamic suffix. A characterization test proves that two different prompts
which resolve to the same capability bundle produce byte-identical stable
prompts. Root LLM job polling now reuses one explicitly scoped HTTP connection
pool and still reports provider execution separately from orchestration time.
The terminal-sidecar experiment reported 5,632 cached input tokens, proving
partial reuse of that prefix, but repair requests still accumulated roughly
96 KB of dynamic context. Repairs now retain candidate sidecars explicitly;
the complete-WebUI/project-memory reductions, stage-specific repair packets,
provider-native typed output, and content-addressed retrieval path remain open.

Measurement correction (2026-09-10): the usage collector previously counted
the same generation telemetry again when retained diagnostic copies carried it
through later steps. The collector now deduplicates request/job identity before
aggregation. The first trustworthy retained RU equipment run after this fix
used two Builder model calls, 4,327 fresh input, 7,808 cached input, and 4,551
output tokens. Its local validator took 93 ms; primary provider execution took
36.3 seconds and one repair 4.8 seconds. A validator timeout would therefore
mask no measured bottleneck. Root queueing was 6-8 ms, while the draft
checkpoint upload was about 3.4 seconds and remains a separate orchestration
optimization target.

Performance follow-up (2026-09-11): phase-level evidence, rather than an
arbitrary validator timeout, isolated two local defects. Builder session state
was rewriting a roughly 3.9 MB compatibility JSON file on every mutation, and
workflow inspection repeated admission validation of the same 220 KB
definition. Owner-scoped relational state with lazy compatibility migration
reduced the measured session writes from roughly 0.6 seconds to 7-8 ms. The
workflow service now reuses the exact validation report retained with the
admitted definition; warm state inspection fell to roughly 0.16 seconds.
Copied runtime context is propagated into Builder background workers so those
workers retain the relational-storage authority of the activating skill.

Fresh run `sdk-semantic-operations-20260911-06` then took 49.0 seconds. Local
scenario validation was 109 ms, Root queueing was 4 ms, and provider execution
was 29.0 seconds; the independent grader took 10.3 seconds. Generation reused
2,688 cached input tokens out of 2,786 and emitted 4,517 output tokens. The
candidate covered all four primary jobs and representative states, but the old
grader rejected it at `0.925` because status mutations had no confirmation.
The minimum-working calibration subsequently classified that unrequested UX
convention as non-blocking; this historical rejection is not an acceptance rule.
This separates semantic failure from transport and validator latency. No
validator timeout was added. A timeout may exist only as an outer circuit
breaker after a measured per-route SLO, must name the interrupted phase, and
must not count as a latency improvement or turn an incomplete result into a
pass. The remaining cost target is fewer and smaller model outputs and
stage-specific context, not a shorter local deadline.

Context-retention follow-up (2026-09-11): E2E previously retained compact
message digests and candidate paths, then deleted the temporary Project that
owned the referenced files. That was insufficient for context engineering.
The runner now copies the complete sanitized request, terminal journal, raw
provider candidate, and normalized candidate into content-addressed run
evidence before cleanup. The first retained volunteer request was about 2,853
input tokens, reused 1,792 cached tokens, emitted 3,314 output tokens, and took
22.3 seconds at the provider. Its full trace identified a Brief/data-model
defect that a shorter output limit or validator timeout would only conceal.

Semantic-v2 context evidence (2026-09-11): run `-10` retained the complete
primary and repair outputs and showed that fail-fast diagnostics disclosed a
state-fixture defect first and a pre-existing command-owner defect only after
repair. Aggregated diagnostics now report both. Run `-11` used one generation
call with 577 fresh and 2,304 cached input tokens and 3,567 output tokens;
provider execution remained 26.8 seconds while local create/design work was
about 7.3 seconds. A source edit initially had no effect because the active
runtime still used the old stable-prefix digest. Explicit DEV activation
changed the digest in run `-12`, establishing runtime materialization as part
of experiment identity.

Run `-13` again used one generation call, 607 fresh plus 2,304 cached input
tokens, and 3,192 output tokens. The 39.2 second case separated into 24.3
seconds generation wait, 6.5 seconds grading, 156 ms validation, and 7.6
seconds Builder chat. Queueing remained negligible. These measurements keep
the optimization order explicit: improve semantic/context sufficiency and
output volume first; retain timeout only as an outer phase-labelled circuit
breaker.

Smaller-model probe (2026-09-11): one unchanged volunteer E2E repetition on
`gpt-5-mini` took 88.6 seconds, including 39.3 seconds primary generation and
38.0 seconds automatic repair. The candidate failed semantic validation after
repair; grading was not reached. The reference GPT-5 run `-13` took 39.2 seconds
and passed semantic validation before failing the outcome hard gate. The
system prefix and provider schema are identical, but cache conditions differ.
See [complete comparison and context review](builder-gpt5-mini-evaluation-2026-09-11.md).
This observation does not admit mini as the default or justify smaller output
limits. It exposes missing first-call compiler invariants and repair regressions
in previously satisfied requirement bindings.

- [ ] `[must]` Replace complete WebUI/project-memory/history inclusion with
  brief deltas, semantic slices, accepted constraints, findings, and retrievable
  content-addressed refs.
- [ ] `[must]` Make policy/output/tool contracts and selected capability
  bundles byte-stable by digest; keep Project and user-specific state in the
  dynamic suffix.
- [ ] `[must]` Use provider-native schema-constrained output or typed tool calls
  where supported. Treat text JSON extraction/bracket repair as a compatibility
  failure metric.
- [ ] `[must]` Define separate fresh-input, cached-input, output, wall-time,
  model-attempt, and repair-attempt budgets for `D1`, `D2`, and recovery.
- [ ] `[must]` Add deterministic stop/fallback behavior when a budget is
  exceeded; do not silently widen context, repeat the whole job, or escalate
  model cost.
- [ ] `[must]` Add route selection among zero-model, efficient model, full
  design model, and optional visual model. Promote a route only through matched
  evaluation.
- [ ] `[must]` Include compiler-required cross-object invariants in the selected
  first-call contract bundle, including each resource's required collection
  surface. Verify request sufficiency against actual validator rules.
- [ ] `[must]` Make repair preserve unaffected accepted requirement bindings and
  states through deterministic before/after checks and scoped edits; retain
  the mini run's dropped-state-binding failure as regression evidence.
- [ ] `[should]` Add semantic-delta candidate reuse and exact candidate replay
  before another model request.
- [ ] `[should]` Target at least 60% cached input for repeated profile and
  capability bundles where the configured provider supports caching.

Initial performance targets:

- `D0`: no model, local `p95 < 1 s` excluding runtime refresh;
- `D1`: median fresh input at most 8k tokens, `p95` at most 16k;
- `D2`: median fresh input at most 20k tokens, `p95` at most 35k;
- warm orchestration excluding provider inference: `p95 < 2 s`;
- no more than one automatic structured repair for `p90` successful runs.

Targets may change only from retained matched evaluation, not timeout tuning.

## R8. Evaluation, Browser Review, And User Experience

### Executable-Interface Correction

Critical review of the retained eight-archetype cohort found a compiler/Client
contract mismatch: generated `ui.form.inputs.buttons` were ignored, all submit
actions shared one trigger, and `resourceQuery` did not hydrate the selected
record. Structural acceptance and nonblank screenshots therefore do not prove
working CRUD. Historical generation scores remain unchanged, but must not be
reported as user-task success or readiness for autonomous prototyping.

- [ ] `[must]` Execute separately selected form commands, hydrate the selected
  record, reject stale selection responses, validate required fields and keep
  edits after errors. Verify create/update/delete and no unintended mutations.
- [ ] `[must]` Test the compiler/renderer boundary with the actual compiled
  artifact, including wide/compact browser interactions and EN/RU rendering.
- [ ] `[must]` Repeat the eight-archetype GPT-5 cohort after correcting the
  boundary. Inspect complete model inputs/outputs and distinguish generation,
  compiler, renderer, task and UX findings. No guaranteed reliability claim
  from a single successful generation per archetype.
- [x] `[must]` Show canonical project/component development timestamps in the
  Builder catalog, newest first, with sortable Application and Updated columns.
  Do not infer freshness from GUIDs, preview visits or source checkout mtimes.
- [x] `[should]` Expose inline/modal/side-sheet editor choice and provide a small
  generic progressive-disclosure recommendation. The compiler owns openers,
  selection, save/error/dismissal and source-map relocation. Surface preference
  is qualitative unless the user explicitly requests it.
- [x] `[must]` Make state operands mutually exclusive in the provider schema;
  explicitly teach query binding in initial and repair contexts.

Correction evidence: `executable-surfaces-gpt5-20260911-01/evidence/picker`
passes Updated/Application sorting, pagination, test filtering and exact preview
selection. `evidence/previous-media-interactions-04` in that run exercises two
retained media editors on wide/compact screens: select, edit, save, reopen and
restore. This exposed and corrected a late-record-load draft overwrite and
missing resource-query unwrapping in details. A third editor was not exercised;
this is not full CRUD or eight-archetype qualification.

The fresh `-01` cohort stopped at a compiler-owned localized opener defect after
three failed cases; no aggregate pass rate is inferred. Operations repair also
rewrote a valid fixture and broke another state. A digest-bound state-only repair
now preserves unrelated data and receives an explicit patch context/schema.
Compiler defects are classified separately and do not trigger model repair.
The complete raw input/output evidence is retained; a fresh run is required.

Run `executable-surfaces-gpt5-20260911-02` then stopped at the first modal
contract blocker: the compiler emitted `pageSchema` instead of the established
modal `schema`. This is a platform defect, not model failure. The ABI now rejects
that misspelling, modal query validation inherits desktop selection defaults,
and offline replay through the actual Builder parser passes. The scoped repair
also over-emphasized empty-proof examples; its context now starts from the
requested state's meaning and forbids substituting emptiness for a populated
condition. These fixes require another fresh cohort, not relabelling `-02`.

Run `executable-surfaces-gpt5-20260911-03` completed all eight cases: 2 passed
(appointments, media), 6 failed. There were 14 generation/repair calls, 39,109
output tokens, 66,456 fresh and 28,800 cached input tokens, with zero reasoning
tokens under the hard-coded GPT-5 `minimal` profile. Case p50 was 59.0 seconds,
p90 83.1 seconds. Five cases failed state/fixture repair; budget compiled but
lacked requested visible totals and limit indications. This does not meet R8.

The appointments browser proof now covers side-sheet dismissal, focus restore,
update/save/reopen/restore and cancelling both destructive confirmations on wide
and compact layouts (`evidence/appointments-confirmations-03`). It exposed
read-only record fields missing from form guards and numeric `.length > 0`
conditions unsupported by the Client evaluator. Both were fixed without adding
hidden fields to mutation payloads; focused Client tests now total 127.

- [x] `[must]` Compare explicit GPT-5 reasoning profiles on the same eight
  prompts before fixing a cost/latency default. Record requested and actual
  options; never attribute a profile change to context engineering alone.
- [ ] `[must]` Complete all independent state-proof findings before repair;
  query reachability failures must not remain hidden behind fixture mismatches.
  Proof rules, hidden predicates, unreachable queries and missing empty views
  now share the validation/diagnostic implementation. Patch schemas include only
  reachable definitions; regression tests cover digest and unrelated-view edits.
  Reopened after the model-capacity high inventory run: two malformed choice
  operands existed in the original response, but only the populated-state
  mismatch reached repair. Its correct scoped patch then hit an unreported
  empty-state choice error. Aggregate canonicalization, type and proof findings
  together before fixing the permitted repair scope.
- [ ] `[must]` Reduce state-repair input to affected evidence, typed fields,
  readable fixture values and affected Brief requirements; retain full original
  input/output for audit. Qualify preservation and meaning, not only patch size.

One retained operations repair replay at `low` compiled successfully with the
same messages/schema that failed at `minimal` (16.1 seconds provider execution).
Artifacts: `state-repair-effort-low-20260911-01` and its `-validated` offline
receipt. The initial replay helper omitted the authoritative Brief; validation
was rerun with the captured checkpoint, without a second model call. This is
single-case diagnostic evidence, not a changed historical verdict or a baseline.

The complete matched `executable-surfaces-gpt5-low-20260911-01` run passed 4/8
(operations, appointments, budget, media), versus 2/8 at `minimal`. Both used the
same 8,000 total-output-token ceiling. Case p50/p90 increased to 95.0/132.1
seconds. Equipment and knowledge failed repair; volunteer and inventory were
provider-incomplete (`max_output_tokens`), not semantic rejections. No default
profile change or reliability claim follows from this single comparison.

Audit also found that E2E ignored Root's flat `reasoning_tokens` usage field.
The collector now supports flat and nested usage without counting both; the
retained successful-response records contain 22,656 reasoning tokens, not zero.
Usage for the two incomplete responses is absent from compact Root telemetry,
so the reported token totals are a lower bound, not complete billing evidence.
Historical artifacts are not overwritten. Diagnostics now expose provider
incompleteness and missing usage explicitly. An opt-in run-specific output
budget allows a measured 12,000-token follow-up without changing user defaults.

`executable-surfaces-gpt5-20260911-03/evidence/appointments-crud-02` additionally
passes create/read/delete using only a new probe-owned record on both viewports,
alongside update/restore and cancelled confirmations. This proves local CRUD for
one compiled editor, not every requested operation in all eight archetypes.
The first create probe incorrectly treated radio choices as select elements;
its failed evidence remains retained. Required-field, upload, localized browser
rendering and complete eight-case interaction coverage remain open.

- [ ] `[must]` Preserve structured partial output and usage for provider
  incompleteness, separating generated JSON and reasoning token budgets before
  choosing capacity defaults. Never shorten the response to disguise failures.
- [ ] `[must]` Qualify the same eight cases under the selected profile with
  repeated fresh generations and complete user-task probes. A diagnostic subset
  or a union of best results across runs is not an eight-case baseline.

Capacity follow-up `executable-surfaces-gpt5-low-12k-20260911-01` completed four
previously failing cases: equipment passed; knowledge, volunteer and inventory
failed. All provider responses completed, so increasing the measured ceiling
removed truncation, not semantic failures. Knowledge again emitted stringified
attachment arrays, then its whole-candidate repair lost a search binding.
Volunteer and inventory exposed misleading lowering diagnostics: an ineligible
Automation reference became an empty compiler-owned statement. Validation now
reports the exact reference and eligible job/residual refs before that error,
and initial context explicitly states the same boundary. Offline regressions
pass; the corrected diagnostic has not yet qualified a fresh cohort.

Input review also found a deeper acceptance risk in lexical Brief extraction:
`open an article without losing their search` was split before the noun `search`,
creating another mandatory search operation. `avoid overlapping assignments`
was classified as assignment from a noun stem. These are not missing user
instructions and must not be repaired by application-specific prompt rules.

- [ ] `[must]` Replace promotion of ambiguous lexical mentions into mandatory
  atomic jobs with provenance-preserving interpretation. Keep complete source
  clauses, distinguish operation hypotheses from explicit required outcomes,
  and validate polarity, noun/verb use and dependent clauses. Include ordinary
  EN/RU paraphrases and ambiguous noun mentions in negative controls. Qualify the
  interpretation and its acceptance effects before retuning generation.
- [ ] `[must]` Extend bounded repairs to typed fixture-value and binding defects
  with preservation checks, rather than rewriting the full valid candidate.
  Prefer a schema constrained by the chosen data model; do not silently coerce
  arbitrary strings into attachment arrays or invent an omitted operation.
- [ ] `[must]` Derive executable postconditions from outcome bindings and the
  chosen resource model, not a fixed mapping from verbs to CRUD names. The
  model-capacity `low` volunteer case compiled assignment as link-record
  creation, then failed a generic `assign -> update` postcondition. Preserve
  this failed verdict, add an equivalent-create/update regression, and still
  test actual editable inputs and record effects before declaring task success.
- [ ] `[should]` Evaluate a smaller authoritative requirement representation
  and deduplicated stable instructions against the retained full context.
  Repeated job/operation/state bindings must not become a substitute for user
  outcomes. Measure token savings, omissions and repair preservation before
  removing context or reducing generation capacity.
- [ ] `[must]` Calibrate the grader against browser-proven false positives.
  `effort-low-provider-max-20260911-01/browser-selected-ru/media-review-ru`
  renders only filenames after selection, with no image/video surface, yet
  grader v11 awards the viewing outcome `supported` from `item.details`.
  Preserve the score as historical evidence, require real media/display
  evidence for that outcome, and add negative controls for property-only views.
- [ ] `[must]` Accept scoped view-only repairs when they resolve every reported
  state defect without changing unrelated content. The model-capacity `high`
  media repair made the missing predicate field visible, but failed because
  `apply_state_repair` also requires an unchanged state echo that the provider
  schema does not require. Offline replay with only that echo added compiles
  against the original Brief (`evidence/media-view-only-repair-audit.json` in
  the high run). Keep both the failed run and this diagnostic; do not relabel
  it as a fresh success or as proof of media playback.

Local correction verification: 407 focused Core/Builder tests passed before the
final diagnostic change; its focused suite adds 374 passing checks and the E2E
diagnostic suite has 41 passing checks (overlapping suites, not an additive total).
Client has 127 focused command/form/table/guard tests and an additional overlapping
99-test modal/form/action run. `appointments-crud-settled-06` retains the wide/
compact interaction and final-opacity screenshot proof. Builder source is
checkpointed as `builder@0.2.113`, ProjectRelease
`sha256:e245fff69078098198cb9863d852c4f32d948ea125ac6a8c4614087d81ca754e`.
No test prototype or unqualified Builder revision is promoted to Workspace.

### Model-Capacity Effort Comparison

- [x] `[must]` Repeat all eight visible cases with GPT-5 `low` and `high` at
  the same explicit 128,000-token provider maximum. Inspect actual primary and
  repair requests and keep the normal user's defaults unchanged.
- [x] `[should]` Retain a reproducible comparison of per-case model/pipeline
  times, fresh/cached input, total/reasoning output, repairs and verdicts, with
  links to admitted DEV previews and separate qualitative findings.

Runs `effort-low-provider-max-20260911-01` and
`effort-high-provider-max-20260911-01` completed without provider truncation or
timeouts. Cases, primary schema, stable context and grader match; the comparison
helper checks those inputs. Low passed 6/8, high 5/8. First candidates compiled
4/8 and 5/8 respectively; these are not task-success rates. Model calls total
12/11, output 79,508/193,965, reasoning 32,576/152,384 and non-reasoning output
46,932/41,581. Median full-case time is 138.09/250.72 seconds; total model
execution is 925.20/1,992.91 seconds. Largest individual response is
8,605/29,627 output tokens, well below the experiment ceiling.

Evidence and per-application interpretation:
`e2e/artifacts/builder/effort-comparison-20260911-01/comparison.md` and
`qualitative-review.md`. All eleven admitted prototypes have wide/compact
captures. Matched queue, appointments and budget probes pass selected edit,
save, reopen, restore and overlay dismissal on both layouts; unsupported
create/delete combinations remain explicitly untested. The high queue initially
exceeded the text-only probe's coverage; an added date-field probe passes,
without changing the prototype or counting an unsupported probe as model failure.

Do not select high as the universal default from this run. It improves some
first candidates and business-rule disclosure, but shared acceptance/repair
defects dominate several failures and grader v11 has a browser-proven media
false positive. Neither cohort qualifies autonomous prototyping. This is one
development-sample comparison, not a sealed baseline or repeated reliability proof.

Handoff: DEV `builder@0.2.114` is published to the personal source repository
as ProjectRelease `sha256:43ab1f602b1141feadde179c416718aa5f02013c2d406efae48f91b3552f13a4`.
All sixteen experiment projects are also checkpointed via `adaos dev project
push`, including failed candidates' source scaffolds; they remain unapproved
tests. `dev-push-receipts.json` in the comparison bundle records all seventeen
successful publications. No Workspace/stable Builder replacement is implied.


Current correction evidence (2026-09-11): the eight visible rubrics now trace
mandatory outcomes to their original user turns. Unrequested status confirmation,
role systems, automatic slot optimization and production aggregation were removed
from the Prototype gate; requested cancellation confirmation, read-only state and
conditional comment checks remain required. User prompts are unchanged.

Grader v11 live calibration at
`e2e/artifacts/builder/grader-minimum-v11-20260911-02` agreed with all four
engineering labels: minimal and richer browse passed, executable update passed,
missing update failed. Both full requests and grades are retained. This checks a
narrow acceptance boundary, not general accuracy or user satisfaction. An earlier
`-01` attempt used pytest's temporary node identity and was denied before model
execution; live probes now require an explicitly configured enrolled dev node.
Reproduce with `ADAOS_E2E_LIVE_GRADER=1`, `ADAOS_E2E_LIVE_BASE_DIR=<node base>`,
and a fresh `ADAOS_E2E_CALIBRATION_OUTPUT`, then run
`pytest tests/test_builder_e2e_grader_calibration.py`. Offline, four fixture checks
run and the live probes are skipped.

Local verification before the GPT-5 cohort: 128 context/compiler/runner/grader
tests and 278 DEV Builder tests pass. Candidate capacity follows the existing
canonical limits (8 resources, 16 relationships/views, 24 commands); generation
guidance is derived from that ABI, not separate prompt constants. Exact record
identity and attachment value forms are explained; missing relationship refs
produce typed findings. A `query_empty` proof requires zero matching fixtures
and an exposed matching filter. Repeated operations retain their object-bearing
source clause instead of collapsing into one bare verb. All of these mechanisms
are measured by the live model and browser checks below.

#### Minimum-Working Cohort

Runs `minimum-working-gpt5-20260911-02` (operations) and `-03` (remaining seven)
retain one complete eight-case cohort on GPT-5, with unchanged user prompts.
Run `-01` was rejected before generation because the provider schema contained
an annotated `$ref`; the projection fix has regression coverage and that run
does not count as model-quality evidence.

| Case | Seconds | Outcome within one generation and at most one repair |
| --- | ---: | --- |
| Operations queue EN | 73.90 | Failed; repair regressed the previously valid overdue fixture |
| Service appointments RU | 118.07 | Passed after repair; lexical interpretation had over-required text search |
| Household budget EN | 133.68 | Passed after repair; business computation remains an Automation obligation |
| Equipment inspections RU | 129.84 | Failed; missing collection, then unreachable empty-query proof |
| Knowledge library EN | 90.07 | Failed; attachment/empty-query defects plus a compiler foreign-key normalization bug |
| Media review RU | 86.43 | Passed first candidate |
| Volunteer roster EN | 79.52 | Passed first candidate, with explicit pending overlap enforcement |
| Inventory procurement RU | 138.95 | Failed; repair left an empty-state declaration missing |

Result: 2/8 first candidates and 4/8 after the bounded repair path passed the
configured non-browser gate. These are development observations, not an autonomy
baseline or human acceptance. Four independent grader calls took 36.6 seconds;
14 generation/repair calls consumed 59,252 fresh input, 35,328 cached input and
58,386 output tokens. Full inputs and responses were inspected. Schema rejection,
compiler defects and missing working controls are attributed separately from
non-blocking visual quality. No output limit or timeout was reduced.

Post-cohort corrections have cross-domain regressions: `find`/`найти` does not
force a text-search widget, and record-ID normalization also updates foreign-key
choices, fixed values, guards, conditional visibility and representative-state
literals. The knowledge candidate used internally consistent raw foreign keys;
Core had changed their fixture values without changing the choice options.
This part of its failure belongs to the compiler, not the model. Previously
recorded failed runs remain failed. Atomic UTF-8 checkpoint replacement also
prevents readers from observing partially written JSON.

Browser evidence under `-03/evidence/browser-picker-final` exercises the existing
DEV Builder: page navigation, test filter, absent-result search, lookup beyond
the former first-50 boundary, row selection and local new-window preview.
`browser-appointments` retains wide/compact generated screens. The picker now
uses generic `ui.table`, pagination, a test/non-test filter and archive toggle;
SDK search runs before the result limit. Catalog errors are not empty results.
Local combined verification: 603 passed, four opt-in live probes skipped.

Focused follow-up `minimum-working-gpt5-20260911-04` after the compiler/context
fixes is a separate two-case sample, not a replacement for that cohort.
Appointments passed its first candidate in 110.66 seconds (one generation,
4,943 input and 4,158 output tokens; cold cache, about 21 seconds grading).
Knowledge failed in 83.08 seconds: its first state operand violated the
value-versus-field contract, and repair lost a required search-control binding.
The search control still existed in the repaired candidate, but the requirement
pointed to an unrelated details view; do not describe this as an absent search
UI. No foreign-key choice error recurred. This sample is 1/2, not evidence of
uniform reliability or a latency improvement.

DEV checkpoint: `adaos dev project push builder` retained Project `0.2.108`,
scenario `0.2.84`, `builder_skill` `0.3.165` and control skill `0.1.109`.
Release digest: `sha256:fbf73b0bc560ab6dac0029c1e0672bfd6266bc5236478abf51dedf1c2c09d471`.
All 18 test Projects created by runs `-01` through `-04` were also checkpointed
with that CLI into private DEV storage. These are source checkpoints, not
Marketplace publication, Trial admission or user acceptance. Workspace Builder
and published stable applications were not replaced.

The Client checkout used by browser review is `203aeb9805f2695e7c5d5ebf1befef8383764100`;
it differs from the pinned `20f0aca573941be1134b9b20203e86c30608fce0` only by
package-version metadata. The Core gitlink and `.sha` remain equal. Neither
Client nor Backend received source changes in this iteration.

Remaining debt, not closed by these observations:

- [ ] `[must]` Reach the fixed small-application reliability gate with repeated
  matched runs; do not promote the experimental DEV Builder into Workspace yet.
- [ ] `[must]` Preserve already valid fixtures, predicates and bindings during
  repair; eliminate the observed unrelated date changes. Measure scoped repair
  against full-candidate regeneration before expanding orchestration.
- [ ] `[must]` Make empty-state evidence directly executable and reachable;
  declaration text without a working filter/empty result is not a pass.
- [ ] `[must]` Encode value-versus-field state operands as mutually exclusive
  provider-contract variants, so schema-admitted output cannot violate the
  compiler's `field_ref=null` requirement for a literal value. Keep typed
  diagnostics and preservation checks for cases that still require repair.
- [ ] `[should]` Revisit the current collection-per-resource compiler constraint.
  A referenced lookup resource may need only an inspectable selector, not its own
  list screen. Relax only with renderer and requirement-coverage regressions.
- [ ] `[should]` Address display semantics for foreign keys, times, choice labels
  and unit-bearing numbers through the Client contract roadmap, not domain widgets.
- [ ] `[should]` Measure compact task completion, form-first vertical bulk and
  EN/RU consistency across viewport changes. Nonblank screenshots alone do not
  establish usability or successful CRUD.
- [ ] `[could]` Add cursor-backed catalog paging when the bounded 5,000-match
  catalog or measured response size warrants it; keep search/filter semantics
  global when moving filtering from the current Client table to the server.
- [ ] `[should]` Verify owner-side test-Webspace deletion by absence, not a
  boolean receipt. A cleanup probe found that room prewarming could recreate
  the just-deleted manifest; the one review-only manifest was removed explicitly.
  Add a deletion/reconnect regression before automatic preview cleanup is enabled.

Stage-boundary correction (2026-09-11):

- [x] `[must]` Put Prototype versus Automation evidence expectations in the
  generation context, semantic-v2 contract, and independent grader v10.
- [x] `[must]` Preserve pending business-rule/integration requirements with
  exact Brief references, visible prototype bindings, bilingual disclosure and
  testable Automation acceptance. Carry them through Prototype acceptance into
  Automation checks. Do not allow UI-operation deferral or silent omission.
- [x] `[must]` Write persisted E2E JSON and compressed step evidence as readable
  UTF-8 (`ensure_ascii=False`). Do not alter historical evidence hashes.
- [ ] `[must]` Prove obligation closure using executable success/failure tests
  at Automation/Trial gates; merely copying acceptance text is not enforcement.
- [ ] `[deferred]` Measure large-prototype planning separately: dependency graph,
  stable cross-slice identities, scoped context, resumability, unaffected-binding
  preservation and integrated browser tasks. Do not infer scalability from one
  small schema-constrained response.
- [ ] `[must]` Cover the reusable local CRUD substrate and primitive automation
  with browser probes: create/select/edit/delete, required fields, confirmation,
  search/filter, local guard, state update and navigation; report unsupported
  joins, computed values and cross-record rules separately.

### AdaOS Tests Review Workbench

Decision: use the existing Builder project selector first. Retain explicitly named
test applications and their development previews on dev nodes, including successful
cases, instead of deleting them automatically. The name includes `[TEST]` and
`YYYYMMDD-uid` so search works in the existing Builder. Its picker now uses the
generic table rather than a first-page-only list. No ordinary desktop or
Marketplace beta promotion. Historical evidence stays immutable; manual removal of
test applications remains separate from run evidence retention.

The specialized latest-run-only workbench below is deferred. It must not be built
as a prerequisite for the small-application Prototype/Automation cycle.

- [ ] `[deferred]` Implement a dev-only `adaos_tests` scenario. Enforce
  `ENV_TYPE=dev` on the node at creation, listing and opening, not only in Client.
  No ordinary user desktop icons or marketplace entries.
- [ ] `[deferred]` Project only the latest run into the workbench; retain immutable
  historical run evidence outside that projection. Use a generation/run token
  and atomic switch so late results from an older run cannot repopulate it.
  Delete only projection-owned materialization, never a user's development.
- [ ] `[deferred]` Show each case's explicit stage, model, run/revision, result,
  preview, wide/compact screenshots, pending obligations and error diagnosis.
  A failed or unavailable preview must not silently show a prior revision.
- [ ] `[deferred]` Mark samples `test`; add `beta` only after a genuine Trial gate.
  Capture human verdict and notes against exact source and renderer digests.
- [ ] `[deferred]` Add run/baseline comparison, replay of one case, scenario
  selector and EN/RU switch. Keep launch privileges and budgets node-controlled.
- [ ] `[could]` Add a bounded screenshot reviewer after deterministic/browser
  checks and translate renderer shortcomings into owning-layer Dev Tickets.

### Earlier Stage-Aware Evaluation

Before the minimum-working correction above, stage-aware review across eight
development archetypes found 1/8 first candidates
qualified structurally, 3/8 qualified within the local workflow, and 1/8 passed
the configured non-browser gate. A late budget repair compiled successfully
after its local timeout; it is retained separately, not retroactively passed.
The browser review found and fixed a generic SDK filter-declaration defect and
still rejected visual quality. Large-prototype granulation remains unproven.
Retained evidence is summarized below; target decisions live in the architecture.

| Development case | Seconds | Stage-aware result before this correction |
| --- | ---: | --- |
| Volunteer roster EN | 99.59 | First candidate valid; grade 0.925, coverage job partial |
| Operations work queue EN | 110.48 | Repair fixed bindings but duplicated an editor field |
| Service appointments RU | 141.85 | Relationship repair valid; grade 0.925, free-time job partial |
| Household budget EN | 227.67 | Local repair wait expired; late candidate subsequently compiled |
| Equipment inspections RU | 144.00 | Repair used editor instead of collection-state evidence |
| Knowledge library EN | 130.96 | Attachment repair damaged empty-result evidence |
| Media review RU | 223.26 | Attachment repair damaged empty-result evidence |
| Inventory/procurement RU | 242.39 | Repaired candidate passed grade 1.0; no browser qualification |

Evidence roots: `e2e/artifacts/builder/sdk-stage-aware-gpt5-20260911-01` and `-02`.
Generation: GPT-5, minimal reasoning, generic semantic-v2, no domain packs, one
full repair, 8,000-token cap. Grader: GPT-4.1 v10. These are eight development
samples, not the held-out baseline. Complete requests expose isolated operation
verbs, empty entity facts, ambiguous identity/display contracts and incomplete
state/attachment instructions. Complete repairs show preservation failures.

- [x] `[must]` Inspect full late budget output, not only timeout: Root job
  `llm_job_921c0a60a4bf4f20bccf2b2c` completed in 244.898s (queue 4ms, TTFT 3.174s,
  output 3,330 tokens). Original SDK receipts reconstruct its exact Brief; three
  resources, six views and nine bindings compile. The run verdict is unchanged;
  replay evidence is in `-02/evidence/late-results/`, not materialized/graded.
- [x] `[must]` Inspect actual Client at 1440x1000 and 390x844. The same volunteer
  candidate initially produced three resource API 400s and a stuck loader. After
  the SDK filter fix it displays records with no resource errors or JS errors.
  Evidence: `-01/evidence/browser-diagnostic` and `browser-sdk-final`, including
  inner-scroll bottom screenshots. Node-status 401 and transient reliability 503
  remain visible shell diagnostics, not hidden successful checks.
- [ ] `[must]` Fix remaining visual/task defects at their owners: raw choice
  values, clipped card labels, excessive flow layout, technical fixture wording,
  compact table usability and untested CRUD interactions. Document-level width
  alone does not establish readability or user-task success.
- [ ] `[should]` Reconcile usage of late jobs: the two reports' 49,908 generation
  output tokens omit the budget's 3,330 late tokens. Three grader calls add 33,030
  fresh input tokens. No arbitrary shorter timeout is accepted as optimization.
- [x] `[must]` Retain a DEV checkpoint for the stage correction: `builder@0.2.107`,
  `builder_skill@0.3.164`, source `d26f48ce06f1cb487f82a1507d80cfe6d32b30cf5d685daab8353e3a02b935ba`,
  release `471a0e91cbd1edd936ad0d6461007bd7c3285d4b44cdada28ac7c525146d59c5`.
  Workspace remains 0.2.106. Local verification: 360 Core/SDK and 278 DEV skill
  tests pass; this does not establish model autonomy.
- [x] `[must]` Verify readable UTF-8 writers and mechanically reformat 121 mutable
  checkpoints without changing values or historical hashes. Exact provider strings
  remain exact, including provider-authored escapes.
- [x] `[should]` Inspect `.env` without speculative edits: 104 CRLF, 11 LF-only,
  no bare CR, final newline; ENV_TYPE, ADAOS_LANG and ADAOS_PROFILE parse separately.
  LF/CRLF append/replace tests with/without final newline pass. Reported glued lines
  were not reproduced; mixed endings alone do not establish their cause.

- [x] `[must]` Derive resource filter admission from each projection's declared
  fields; test the actual workbench with multiple resources and unknown filters.
- [ ] `[must]` Expose one exact required-reference inventory and object-aware
  operations in generation context; test omission and unrelated-binding cases.
- [ ] `[must]` Align authoring/provider/canonical capacity constraints and
  relationship identity/display contracts before capacity-based plan routing.
- [ ] `[must]` Calibrate false-positive state proof and false-negative implicit
  Automation requirements against human-labelled artifact/task pairs.
- [ ] `[should]` Reconcile late Root completions idempotently without discarding
  complete responses or reclassifying a timed-out case as a historical pass.

Evaluation progress (2026-09-11): grader v9 keeps its Structured Output schema
byte-stable and supplies candidate-specific evidence pointers in the dynamic
payload instead of embedding them as a schema enum. Rubric entries may carry
explicit `statement`, `acceptance`, and `exclusions`; the runner injects them
only after candidate generation, so they cannot leak into Builder context.
Run `sdk-semantic-v2-20260911-13` cited only existing pointers, supported all
four primary jobs and all three states, and scored 0.925. The historical hard gate
remained red because it required an executable overlap guard. Review found that
this mixed Prototype and Automation acceptance. Grader v10 distinguishes a
demonstrated, disclosed, preserved Automation obligation from a platform gap.
The old run is not retroactively a pass: it lacks the new structured obligation.
This is development evidence, not a clean baseline or a prompt-autonomy pass.

- [ ] `[must]` Run R8 evidence through `adaos builder e2e` so local, CI, and
  release evaluation use the same resolved run manifest, stages, graders, and
  metric definitions.
- [ ] `[must]` Run structural, behavior, i18n, accessibility, state-coverage,
  and authority checks before browser or visual-model evaluation.
- [ ] `[must]` Add browser task probes for compact and wide layouts using the
  same primary jobs and representative states from the brief.
- [ ] `[must]` Add a bounded screenshot/DOM/accessibility-tree review after
  deterministic checks. Permit at most two targeted repair iterations.
- [ ] `[must]` Feed typed deterministic and calibrated outcome findings into a
  bounded semantic repair unit only after retaining the complete candidate.
  The repair receives the smallest authoritative Brief/semantic slice that can
  address the findings; it does not replay or truncate an unexamined response.
- [ ] `[must]` Convert unresolved renderer, component-contract, compiler, or
  Core failures into owning-layer Development Feedback/Dev Tickets instead of
  broadening the application prompt.
- [ ] `[must]` Show users outcome-oriented progress, material assumptions,
  clarification, preview readiness, and recovery. Hide provider retries,
  prompt phases, component IDs, and JSON patch terminology.
- [ ] `[must]` Separate Prototype statuses: interpreted, planned, generated,
  structurally qualified, visually qualified, user accepted, and Automation
  ready. Never collapse them into `complete`.
- [ ] `[must]` Compare matched candidates with immutable baselines using hard
  truthfulness/authority/validity/task gates plus per-family distributions.
  Report `improved`, `regressed`, `inconclusive`, or `uncomparable`; do not let
  one weighted score hide a hard-gate failure.
- [ ] `[should]` Capture sanitized failure patterns and accepted fixes as an
  evaluation corpus; promotion into generic rules requires cross-domain proof.
- [ ] `[should]` After the minimum-working gate, curate a small versioned set of
  domain-neutral UX golden rules as recommendations with applicability,
  rationale and alternatives. Keep explicit user requirements and enforced
  platform safety policy separate from optional UX quality preferences.
- [ ] `[should]` Evaluate rules on paired simple/richer prototypes and user
  reviews; measure usefulness and interaction cost, not just detail counts.
  An unsolicited convention must not become a hidden mandatory grader item.
- [ ] `[could]` Add inspectable project-scoped feedback memory with provenance,
  confirmation, supersession and forgetting. Require confirmation before reuse
  across applications; isolate users/subnets and keep evaluation cohorts clean.
- [ ] `[could]` Retrieve relevant confirmed preferences by stage and request,
  with attribution and token accounting, instead of appending all feedback to
  every generation prompt. Report conflicts with current explicit instructions.
- [ ] `[could]` Compare two design candidates only for high-value ambiguous
  layout decisions and within an explicit additional budget.

Prompt-autonomy admission targets:

- at least 80% of held-out prompts pass deterministic checks on first
  candidate;
- `p90` requires at most one targeted repair;
- at least 80% of primary user tasks pass compact and wide browser probes;
- no false claim of a real effect, binding, authority, or supported component;
- requested-locale completeness, long-content, and accessibility gates pass for every admitted
  Prototype.

### Context Engineering Qualification (2026-09-11)

The next development experiment holds GPT-5 `low` and its explicit output
budget constant. Media review is the first diagnostic case: its previous
property-only viewer passed grading without rendering media. Do not optimize
the prompt against that false-positive gate or copy a domain solution into Core.

- [ ] `[must]` Align model-visible capabilities with compiled behavior: layout
  placement, collection selection/details/editor entry, and real media viewing.
  Separate compiler defects from model errors before requesting a repair.
- [x] `[must]` Use one authoritative requirement inventory, a stable cacheable
  contract prefix, and stage-specific dynamic facts. Eliminate repeated prose
  without hiding type, effect, relationship, or acceptance constraints.
- [x] `[must]` Permit repairs to related views without echoing unchanged states;
  collect independent predicate/type findings before defining repair scope.
- [ ] `[must]` Generate the user's current locale by default, retaining stable
  localization keys and scenario-owned assets. Explicit multilingual requests
  and existing translations remain authoritative; never label copied text as
  a translation. Verify both EN and RU single-locale paths and the bilingual path.
- [x] `[must]` Compare the unchanged short media request with a moderately
  elaborated request describing the same outcomes, without ABI vocabulary or
  privileged solution hints. Preserve model I/O, timings and browser evidence.
- [ ] `[must]` Repeat all eight development archetypes after the diagnostic
  fixes; require executable browser tasks, not merely successful compilation.
  A single 8/8 run is not a stability or held-out autonomy claim.
- [x] `[must]` Remove pre-comparison owned test developments using provenance
  and checked paths. Keep both low/high comparison cohorts, ordinary user
  applications, source archives and historical evaluation evidence intact.
- [x] `[must]` Preserve typed record schemas for empty/null-only resources and
  resolve literal dotted fields consistently in Client and Prototype queries.
  Treat zero/false as filled values and permit fixed-command read-only editors.
- [x] `[must]` Close unambiguous ownership bindings before validation and collect
  independent binding defects with state/type defects before choosing repair scope.
- [ ] `[should]` Measure request-specific reference enums against their potential
  schema-cache cost; keep the authoritative requirement inventory unchanged.
- [x] `[should]` Review mandatory standalone collections for lookup resources.
  Viewless resources now require a typed reachable selector, materialize validated
  records and participate in final dependency checks; no collection is fabricated.
- [x] `[must]` Qualify real attachment capture, not filename metadata: preserve
  scalar/multiple value shape, store bytes in Preview-owned state, return a
  durable scoped reference, reopen the content and reject failed uploads without
  overwriting the previous value. Never put runtime attachments in Builder's
  development SourceBundle or embed binary payloads in record JSON.
- [x] `[must]` Expose simple command availability conditions in the semantic
  contract, including archived read-only records. Pair local UI behavior with
  truthful Automation debt for durable authorization; a status field alone is
  not a prohibition on editing.
- [x] `[must]` Preserve false/zero query operands and qualify numeric equality
  filters. The second cohort found a false/zero-as-clear provider bug and an
  unsupported numeric-filter failure appearing only after repair.
- [x] `[must]` Remove redundant node.yaml writes and use the established atomic
  replacement helper for real writes. A Windows sharing collision interrupted
  creation in the second cohort; classify it as platform/stand, not model failure.
- [x] `[must]` Replace full-view echoes in bounded state repair with mutable-only
  view patches. Preserve immutable properties by construction and retain legacy
  replay compatibility; qualify with fresh generation, not retrospective scores.
- [ ] `[should]` Tie Prototype attachment reclamation to owned revision cleanup.
  Per-resource storage is bounded; aggregate retention still needs lifecycle GC.
- [ ] `[must]` Replace frozen relationship fixture enums with typed live lookup
  options and runtime reference validation. Creating a related record must make
  it selectable without regenerating the Prototype; reject dangling references.
  Qualify create-related/select/save/reopen across unrelated archetypes.
  Form lookup and atomic provider checks are implemented with component/Core
  tests. Run 05 now proves create-related/select/save and visible child records
  on both layouts. Viewless lookups also pass typed materialization tests. Live
  collection filters/display labels and full related-record reopen remain open;
  do not mark the whole task complete.

Qualification progress: `context-media-paired-low-20260911-02` passed all four
development attempts (short 2/2, detailed 2/2). Each prompt needed one repair
across its two attempts. Pipeline ranges were 138-181 seconds (short) and
128-183 seconds (detailed); this sample does not establish a speed or accuracy
advantage from elaboration. The preceding paired run failed both detailed
attempts because excluded functionality became mandatory and descriptive image
text became an upload requirement. Exact original instructions remain in the
revised context; exclusions are separately evidenced, not Automation obligations.

The retained media browser evidence proves native video playback, loaded images
and unavailable-file states on 1440px and 390px viewports. The command probe
also exercised status transitions and persisted comments. Probe cleanup initially
called an undeclared delete operation; that is a stand defect, not a generation
failure. Generated test records are explicitly retained where deletion is not
part of the application's declared operations. Full eight-archetype browser
qualification remains open: the next cohort exposed dotted-field lookup defects
in Client and incomplete collection-binding diagnostics in Core.

`context-archetypes-low-20260911-01` completed 16 attempts: 9 passed, 7 failed.
Operations, appointments, budget and media passed twice; volunteers passed once;
inspections, library and procurement did not pass. Browser checks exposed a
numeric `.length` guard and literal dotted-key display/submission defects despite
formal passes. Client fixes made the unchanged appointments candidate pass edit,
save, reopen and cancel checks on both viewports. Compiler replay is stored as
separate evidence, never substituted for original outcomes or a fresh generation.

Cleanup receipts under `e2e/artifacts/builder/cleanup-before-effort-low-provider-max-20260911-01*`
record 174 archived local test developments removed, with all 16 last low/high
comparison applications preserved. Ordinary applications and historical run
evidence were not removed. Later diagnostic developments remain searchable.

`context-archetypes-low-20260911-02` completed 16 attempts: 10 passed, 6 failed.
Operations, budget, inspections and volunteers passed twice; appointments and
media once; library and procurement did not pass. One library creation failed
before inference on a Windows config replacement. The remaining failures exposed
numeric filters, immutable-property echoes, a missing record-lock primitive,
relationship identity references and unfiltered linked-record inspection.
Browser tasks proved operations, budget and inspection edit/save/reopen paths on
both viewports, but not file-byte capture. Native media inspection must allow a
legitimate empty field; only the explicit media fixture probe requires image,
video playback and unavailable-file evidence together.

The next batch supplies record locks, true scoped file storage, attachment links,
sanitized Markdown fields, numeric query inputs and mutable-only repairs.
Candidate fixtures admit up to 12 records per resource (previously 6): both budget
attempts spent a repair solely because useful 8/9-record examples crossed an
internal cap. This bounded capacity change is recorded in the contract digest;
it is not a reduction of required outcomes or a claim of better UX.
Automated Core/Client tests are prerequisites, not proof of eight-archetype
autonomy. Full fresh-generation and browser qualification remain open.

`context-primitives-low-20260912-01` passed library and procurement formal grades;
media failed after repair on a visible Automation binding not included in the
initial findings. Library needed no repair but split real and sample collections:
the generation guidance failed to explain an already implemented empty-response
fixture. This is a context defect, not a need for a new resource type. Procurement
passed the browser command probe on both layouts; the text-edit probe correctly
reported no applicable task for its create/transition forms. Record this as a
probe limitation, not as either a working edit path or a generation failure.
- [x] `[must]` Explain empty states as variants of the same populated resource,
  repair table empty-state evidence paths, and diagnose missing Automation
  visibility bindings alongside the other first-pass findings.
- [x] `[must]` Normalize only unambiguous typed JSON fixture scalars with retained
  original evidence; reject unit/timecode interpretations and text-ID coercion.
- [x] `[must]` Qualify empty render fixtures separately from real mutations and
  repeat library/media generation under the corrected state context.
- [x] `[must]` Permit field-predicate evidence in selected-record details/editors,
  keeping collection-specific proof kinds strict. Decode exact typed JSON arrays
  with original evidence rather than spending a repair on unambiguous quoting.

`context-state-low-20260912-01` passed 3/4 attempts: library 1/2, media 2/2.
The remaining library failure exposed a valid details-state rejected as if every
proof required a collection. Its first response also encoded attachment arrays
as JSON strings. These have separate compiler/normalization regressions; fresh
eight-archetype qualification is still required. The passing library uses one
populated collection rather than duplicate sample resources.

Library browser tasks now prove modal cancel/focus restore, edit/save/reopen,
and actual file upload/save/reopen/download with identical SHA-256 bytes on
1440px and 390px viewports. The first captures hung when a Client file list
recreated attachment components on each Angular render; stable item tracking
fixed the unchanged generated artifact. Earlier captures remain failures, not
rewritten passes. Procurement empty-response fixtures passed both viewports
separately from its real create/status-transition command tests. These results
qualify the named primitives, not every workflow or polished layout.

- [x] `[must]` Reject an E2E reasoning override without an explicit model; pass
  the model through SDK metadata and record model plus primary/repair wait
  settings. Add a reproducible local experiment launcher and inspect actual
  saved provider requests before accepting a model comparison.
- [x] `[must]` Derive supported filter types from the validator constant and
  report cross-resource guard references before repair. Clarify that deferred
  computations need inspectable representative outputs, not only raw inputs.

`context-archetypes-low-20260912-03` is invalidated, not a GPT-5 score. After a
restart the invocation omitted an explicit model and inherited a 150-second
wait; saved requests contain model=null/reasoning=null. The runner was stopped,
with checkpoints and browser evidence retained. Its budget and guard observations
motivate generic regressions, but cannot support a claim about GPT-5 low. The
next controlled cohort must verify actual model/effort in the first request and
retain all terminal response evidence. Previous explicit GPT-5 runs are unchanged.

`context-archetypes-gpt5-low-20260912-04` verified GPT-5 low in actual requests,
but was stopped at a deterministic integration blocker after four completed
attempts, not treated as a completed eight-archetype score. The compiler emitted
live dropdown sources while DEV Builder still required static options. One
additional budget request had already been submitted; its terminal Root response
is retained separately and was not applied or counted as a completed attempt.
Operations attempt 2 compiled and passed create/visible-record browser tasks on
both layouts, but grader v12 cited a nonexistent path and downgraded its own
positive verdict. Original verdicts and response artifacts remain unchanged.

- [x] `[must]` Align DEV Builder's component preflight with live resource dropdowns;
  run retained candidates through the complete Builder payload boundary, not
  only semantic compilation and resource schema derivation.
- [x] `[must]` Permit viewless lookup resources only when a reachable editor
  consumes their relationship selector. Preserve record validation and read-only
  materialization; choose explicit safe label fields, with identity fallback,
  rather than exposing arbitrary target fields or inventing a collection.
- [x] `[must]` Collect misplaced query controls alongside missing collections
  before repair; describe collection-only query controls in the shared context.
- [x] `[must]` Remove the duplicate semantic-rule checklist from repair prompts.
  Repairs use the same shared capability contract as generation, plus preservation
  and scope instructions, so stale rules cannot contradict new primitives.
- [x] `[must]` Constrain grader evidence to existing pointers. Grader v13 hashes
  and retains its exact response schema, bounds its evidence index, and groups
  enums within provider limits. It does not forgive invalid evidence in old runs.
- [ ] `[must]` Qualify the combined lookup/compiler/Builder fixes with fresh
  GPT-5 low generation and create-related/select/save/reopen browser tasks.
  Replay is diagnostic evidence, not a replacement for fresh generation.

`context-archetypes-gpt5-low-20260912-05` completed at 4/8: library, media,
volunteer roster and procurement passed. Three first-pass candidates compiled
but failed because a separate final postcondition scanner omitted lookup sources
inside forms. Appointments failed on repeated field names across resources;
replay after qualification still correctly rejects its unselectable client
details. These are distinct causes, not a justification to relax acceptance.

- [x] `[must]` Share executable query-slot discovery between resource
  materialization and postcondition acceptance, including modal lookup fields.
  Add public SDK regressions for missing and unconsumed resource sidecars.
- [x] `[must]` Owner-qualify repeated resource-local candidate field names and
  all typed references. Preserve fixture text and identities; reject ambiguous
  evidence bindings and collisions. Honor complete explicit relationship labels.
- [x] `[must]` Replay retained responses through semantic, resource, DEV Builder
  payload and public postcondition checks before another paid cohort. Replays
  are immutable diagnostics and do not replace the recorded fresh-run scores.
- [x] `[must]` Browser-prove stored-record read-only policy independently of
  mutable CRUD: disabled form/actions plus a provider-side rejection on both
  layouts. The retained library state cohort passed; fresh library run 05 also
  passed edit/cancel/focus and real file upload/download integrity checks.
- [ ] `[must]` Repeat all eight fresh cases after this boundary-alignment batch;
  qualify related-record creation/selection using live options, and retain any
  browser-task coverage gaps explicitly.
- [x] `[must]` Align owner-qualified field references with the model-facing
  contract for both repeated and unique local names. Run 06 budget attempt 2
  exposed qualified unique-field references rejected serially in primary and
  repair responses; collect independent unresolved references in one preflight.
- [x] `[must]` Classify the consumed quoted application name as authoring
  metadata, not a residual implementation requirement. Run 06 retained a
  `named "...<test-id>" for ...` residual, making otherwise matched response
  schemas differ. Preserve the original request and audience/outcome context;
  exclude only the recognized authoring/name span, with EN/RU regressions.
- [x] `[must]` Count native details media source/poster fields as rendered state
  evidence without accepting a hidden dispatch field as visible output.
- [ ] `[must]` Make interrupted E2E resume reconcile the durable Builder operation
  before polling its terminal journal. Run 06 resumed into a blind file wait
  after its worker had ended during semantic repair; retain the Root response
  and classify interruption separately, without automatically submitting again.
- [ ] `[should]` Evaluate named fixture cells or a narrowly typed fixture repair
  against positional value arrays. Run 06 inspections emitted six values for
  seven fields; do not guess where a missing value belongs or regenerate all
  unchanged application design solely for fixture alignment.

Run 06 was interrupted during the final procurement repair: 13 completed passes,
two completed validation failures, one interrupted attempt. It is not a complete
13/16 generation score. Full-boundary replay after qualified-reference repair
admits both previously rejected terminal candidates; original evidence remains
unchanged. The batch has 276 passing focused Core tests and 121 Client tests.
Fresh generation and browser qualification remain separate gates.

- [x] `[must]` Keep explicit identity-field values consistent with normalized
  record IDs and typed references. Run 07 library attempt 2 retained a matching
  raw `record.id` and `values[id]`; Core normalized only the former, then asked
  the model to fix each row. Preserve mismatches as errors, not guessed renames.
- [x] `[must]` Derive the state-repair envelope name in Builder context from the
  actual SDK output schema. The transport now declares v2 while the adapter's
  stable/system text still names v1. Cover the real version contract in tests;
  a stub without schema identity concealed the drift.
- [x] `[must]` Do not present editable fields whose values are ignored by every
  command of that editor. Run 07 queue creation presents an editable status but
  always submits its fixed `New` value. Keep field editability scoped to the
  editor's declared command inputs; preserve useful read-only context.
- [x] `[must]` Close a filter binding over all controls of its one explicitly
  bound collection. Retained library run 07 had all requested controls but was
  rejected for omitting repeated control IDs. Ambiguous collections remain
  unresolved; closure does not replace semantic grading of the controls.
- [x] `[should]` Report fixture-arity failures across resources with ordered
  expected field IDs in one diagnostic batch; preserve all raw cell values.

Run 07 completed at 14/16, with grader v13 and actual GPT-5/low requests. The
two failures were library identity normalization/binding closure and media
metadata/command ownership. After the next batch, both retained library outputs
pass the full replay boundary; media command ownership still correctly fails
and needs a fresh model correction. The new batch has 283 focused Core tests
and 285 DEV Builder tests passing. Browser tasks for the eight admitted first
attempts cover creation, typed edits, assignment/status choices, numeric values,
attachments, native media, and related records on 1440px/390px; each probe keeps
unsupported tasks explicit. Generation acceptance is still not full UX approval.

The matched operations requests now have identical stable context and output
schema. Their provider generation times were 34.1/28.3 seconds; the second
reused 4,352 of 5,426 input tokens. This proves cache reuse, not that the whole
latency difference was caused by caching. Keep raw first-pass and repair costs
separate when comparing future cohorts.

Engineering basis: [OpenAI text-generation guidance](https://developers.openai.com/api/docs/guides/text),
[prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching), and
[Anthropic context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents).
Apply their guidance as testable hypotheses: sufficient high-signal context,
consistent contracts, representative evaluations and stable prefixes, not a
promise that shorter prompts alone produce reliable software.

Run 08 (`context-archetypes-gpt5-low-20260912-08`) is complete but not qualified:
11 passed, three candidate-validation failures and two inconclusive grades.
Thirteen of sixteen candidates reached generation admission. The failed grades
are infrastructure/evaluator outcomes: library attempt 1 exhausted the grader's
1800 output-token budget; procurement attempt 2 received Root
`insufficient_quota / credit_balance_exhausted`. Failed grading calls are absent
from this old report's usage totals. Preserve that limitation and the separate
Root receipts rather than rewriting the report or counting either as model failure.

- [x] `[must]` Orient scalar reference validation, identity normalization and
  provider policies by cardinality; enforce one-to-one uniqueness atomically.
  Preserve implicit identities on either end and require an explicit link
  resource for many-to-many. Retained media replay now gets past inverse
  identity checks, but still fails independent state visibility/label checks;
  this is not a new successful generation.
- [x] `[must]` Expose omitted nested authoring assertions from the ABI in the
  portable generation context. The volunteer response exceeded a hidden
  three-field label limit. The derived inventory adds 34 constraint entries
  (2304 serialized characters); fresh token/repair impact remains to be measured.
- [x] `[must]` Introduce additive state-repair v3 so adding a visible field
  cannot erase existing queries or an empty presentation. Preserve old repair
  semantics for replay and test Builder's v1/v2/v3 envelope agreement.
- [x] `[must]` Retain grader inputs, responses and immutable pre-interaction
  snapshots per attempt. Include output settings in request identity. Grader
  v14 indexes executable read-only policies instead of nested schema-property
  noise and uses the default GPT-4.1 output ceiling of 32768, not 1800.
  Its complete-response quality still needs live qualification.
- [ ] `[must]` Restore Root provider credits, then qualify the prepared schema
  canary, grader v14 and fresh GPT-5/low generation separately. The first canary
  also failed for quota; no provider schema-support conclusion is justified.
  Do not send repeated paid requests while that failure remains unresolved.
- [ ] `[must]` Regrade the retained library/procurement artifacts without
  regenerating or modifying their source, retaining the new grader version.
  All 13 admitted artifacts have snapshots taken before browser mutations.
  The old grader's case-only input filename overwrote library attempt 1's
  exact input; its retained Root response is not a replacement for that input.
- [ ] `[must]` Aggregate failed grader usage into run totals as well as retaining
  it in response evidence. Report unavailable usage explicitly; an inconclusive
  call is not necessarily free and must not vanish from cost comparisons.
- [ ] `[must]` Qualify mutable relationship-label workflows, including category
  rename with existing transactions. Run 08 budget browser edits fail because
  links use the mutable category name. Prefer immutable IDs in context, expose
  incompatible editable-key designs before admission, and do not hide the
  failure by weakening referential integrity or silently cascading data changes.
- [ ] `[must]` Continue all-independent-defect diagnostics before choosing a
  repair scope. Run 08 media and volunteer reveal further state/label failures
  after their initial identity/bounds failures are removed.

Offline verification of this batch: 249 focused Core tests and 286 DEV Builder
tests pass. Review probes open all eight selected prototypes at 1440px/390px;
library read-only UI/provider checks pass. This does not prove complete CRUD:
budget rename fails, and unsupported scalar probes for media/volunteer are
explicitly unexercised. Native media commands pass; the volunteer command probe
exposed Client `readOnly`/`readonly` disagreement. Browser evidence and renderer
fix qualification are tracked in the Client roadmap. The new Core changes have
not yet been evaluated by a fresh live generation cohort or promoted to Workspace.

Local recovery checkpoint: `adaos dev project push builder --local-only`
retained ProjectRelease `builder@0.2.115`, digest
`sha256:bd1810f24bfcf89b4caa8eb31add8e4fa2f169e9b4f706e7b6f6e4803873ddca`,
with source revision
`sha256:2a99bfce8ea17523e4c3ac1c121ce9d0f78e136af0aad076b22e1b4ee434e877`.
This includes the DEV Builder repair-envelope regression test. It is a local
content-addressed checkpoint, not a Root/GitHub publication or runtime promotion.

The grader/bounds changes follow the supported-subset distinction in
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
and the documented [GPT-4.1 output ceiling](https://developers.openai.com/api/docs/models/gpt-4.1).
Provider capability, context cost and task success remain measured gates, not
assumptions inferred from a provider name or a valid JSON response.

## R9. Cutover And Cleanup

### Small-Prototype Capability Qualification (2026-09-12)

This tranche extends small prototypes, not deferred large-project orchestration.
Keep generation generic and use human behavioral prompts, never renderer IDs.
Retain the existing eight-archetype cohort; extra capability cases form a separate
cohort with their own request, schema, model settings and browser evidence.

- [x] `[must]` Implement the foundation contracts: native date commit, typed query
  toolbar, explicit related-context editing, text display policy, board/tree/chart/
  accordion lowering and optional tab/settings sections. Offline compiler and
  component tests are not browser or fresh-generation qualification.
- [x] `[must]` Preserve SDK/E2E prompt provenance for Builder project Conversation;
  exclude already-recorded browser ingress and retain idempotent message identity.
- [x] `[must]` Verify the restored GPT-5 provider with a structured-output canary:
  nested alternatives and array/string/number bounds passed through Root in
  `structured-bounds-gpt5-20260912-02`. This does not qualify new UI generation.

- [ ] `[must]` Fix date filters for native calendar commits as well as keyboard
  edits, without duplicate blur actions or changes to explicit manual commit.
- [ ] `[must]` Provide a compact, extensible collection query toolbar with
  responsive disclosure, active-filter visibility and reset; reuse the same
  primitive for lists, tables and other collection presentations.
- [ ] `[must]` Separate row selection, related-detail navigation and editing.
  Declare explicit actions when selection has multiple useful consequences;
  test the inspection/check-item example without domain-specific compiler rules.
- [ ] `[must]` Expose per-view text wrapping/truncation and alignment, preserving
  complete values and an accessible route to any deliberately truncated text.
- [ ] `[must]` Qualify tabs, modals, hierarchical navigation, accordion disclosure,
  numeric charts and board drag/move through schema, lowering and browser tasks.
  A rendered control alone does not qualify persistence or drag semantics.
- [ ] `[must]` Expose application settings through the existing host contract;
  test their actual effect and persistence scope rather than a Settings heading.
- [ ] `[must]` Make Builder Conversation retain project-scoped user instructions
  and generation results, including technological E2E entrypoints, without
  inheriting another project's transcript or duplicating chat messages.
- [ ] `[should]` Inspect existing prototype inheritance/revision isolation before
  consolidating test applications. Reuse one project only if current source,
  runtime data and evaluation identities already isolate revisions safely.
- [ ] `[must]` Finish known relationship/lookup and grader-observability defects,
  then run matched GPT-5/low repeats and compare short/moderate prompts on the
  difficult cases. Record initial success, repair success, task success and cost.
- [ ] `[must]` Audit generation inputs and Core/Client changes for subject-scoped
  branches, fixture leakage and rubric leakage before accepting the tranche.
- [ ] `[should]` Discuss a finite vocabulary of composable layout strategies,
  distinguishing user-selectable preferences from hard acceptance requirements.

Design references: [Carbon data-table usage](https://carbondesignsystem.com/components/data-table/usage/)
separates row, selection and toolbar actions; [PatternFly toolbar guidance](https://www.patternfly.org/components/toolbar/design-guidelines/)
provides responsive grouping and filter disclosure. Adapt these generic patterns
to AdaOS rather than introducing either system's component framework.

Promotion evidence (2026-09-11): the pre-promotion comparison found no
Workspace-only product capability that needed to be carried forward. The four
Workspace-only helpers were shopping-list, todo-list, and Applications-specific
localization heuristics; DEV replaces them with generic semantic and
localization processing. Scenario and SDK differences outside Builder logic
were version-only. DEV component tests and the functional-parity suite passed
before checkpointing. The exact checkpoint produced scenario `builder@0.2.83`,
`builder_skill@0.3.163`, and `builder_sdk_control_skill@0.1.107`; ProjectRelease
`builder@0.2.106` has digest
`sha256:d65a27b619ea3ef25a030f25e3fe308b5b7da2fbcfff7ad9b4359128e88d0358`.
Trial `builder-0-2-106-9128e88d0358` materialized under `.adaos/trials`, was
accepted against the retained test and E2E evidence, and promoted into
WorkspaceLock revision 45. Reload and health receipts matched all three exact
component versions. This proves the local DEV -> beta -> Workspace mechanism;
it does not by itself satisfy the prompt-autonomy gate above.

The three-component checkpoint took 41.7 seconds. Its largest phase was the
Builder skill at about 24.0 seconds: 8.0 seconds local validation, 4.7 seconds
package validation, 3.2 seconds Root upload, and 7.1 seconds local receipt
write. The observed delay is therefore distributed work, not evidence for a
shorter timeout. Preflight also reported declared `max_request_hz` and
invalidation-tag policies that are not yet executed exactly by several
Builder SDK reads and mutations. They remain visible follow-up defects rather
than being suppressed for promotion.

- [ ] `[must]` Shadow-run the new route beside the legacy path on retained and
  held-out prompts without changing the user-visible Prototype.
- [ ] `[must]` Compare quality, user-task success, cost, latency, repairs,
  preservation, and capability-gap accuracy under matched model settings.
- [ ] `[must]` Enable the semantic route for new generic Projects, retain
  per-Project rollback, and leave existing accepted revisions immutable.
- [ ] `[must]` Recreate Applications from `scenario_default` using only an
  ordinary user request and follow-up review. The application-manager domain
  pack may grade the result after generation but cannot enter generation
  context or routing.
- [ ] `[must]` Complete human EN/RU compact/wide acceptance, then accept the
  exact Prototype and proceed through Automation, Trial, Publication, and
  Workspace only after this roadmap's gates pass.
- [ ] `[must]` Remove generic-path calls to the legacy prompt compiler,
  Applications qualification, cumulative recipe phases, and text JSON repair.
- [ ] `[should]` Archive compatibility fixtures and migration telemetry after
  the supported rollback window.
- [ ] `[should]` Make every Builder SDK data route execute its declared request
  frequency and invalidation-tag policy, then turn the current preflight
  warnings into a promotion gate. Measure validation, Root transfer, and local
  receipt persistence independently before optimizing checkpoint latency.

Exit gate: an ordinary user can reach a relevant, executable, accepted
Prototype without understanding AdaOS internals, and the evidence demonstrates
that this result was not supplied by a subject-specific recipe.

## Could And Deferred

- [ ] `[deferred]` Large-prototype decomposition, shared entity/navigation/locale
  contracts, dependency invalidation, resumable work units and atomic assembly.
  After the small lifecycle passes, compare at least three sizes, including beyond
  single-unit capacity, and test one failed unit plus a shared-entity change.

- [ ] `[could]` Learn capability ranking from accepted traces after a
  deterministic baseline and anti-leakage evaluation exist.
- [ ] `[could]` Add speculative parallel design candidates when measured task
  value exceeds added inference cost.
- [ ] `[could]` Add reusable domain packs to a governed registry with explicit
  installation, trust, attribution, versioning, and removal.
- [ ] `[deferred]` Autonomous generation of new Client renderer code during
  Prototype work.
- [ ] `[deferred]` General multi-agent orchestration. Admit it only for a task
  class where matched evaluation beats one orchestrated agent on quality and
  total cost.
- [ ] `[deferred]` General reverse engineering of product intent from arbitrary
  legacy code or screenshots.
- [ ] `[deferred]` Unbounded autonomous visual iteration or automatic Prototype
  acceptance.
- [ ] `[deferred]` Multi-user concurrent semantic editing and conflict-free UI
  document collaboration.
