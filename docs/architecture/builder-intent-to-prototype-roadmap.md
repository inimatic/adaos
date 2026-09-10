# Builder Intent-to-Prototype Roadmap

Status: active corrective roadmap. It blocks new claims of generic Builder
Prototype autonomy.

Last reviewed: 2026-09-10.

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
- [ ] `[must]` Implement one technological CLI entry, `adaos builder e2e`, for
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

RU/EN parity note (2026-09-10): authoring exclusion is now span-aware rather
than clause-wide. A single ordinary turn may say "create an application" and
then name its actual operations in the same sentence; only the Builder command
span is excluded, while later operation evidence keeps its exact source
offsets. The generic lifecycle vocabulary covers common close/complete,
submit/approve/reject/cancel, and status-change forms in both locales. Tests
also prohibit lexical prefix leakage such as interpreting `marketplace` as a
`mark` operation. All four visible RU development archetypes now compile a
non-empty operation set without introducing subject entities into Core.

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

- [ ] `[must]` Replace or promote `webui.semantic.v0` with a complete versioned
  semantic document for the currently supported Prototype component set.
- [ ] `[must]` Represent entity and collection-item granularity explicitly;
  prohibit the design stage from collapsing an accepted repeated collection
  into an opaque scalar unless the brief says it is read-only text.
- [ ] `[must]` Bind every accepted brief requirement to semantic data, view,
  command, state, or typed capability-gap refs and reject unresolved bindings.
- [ ] `[must]` Publish `adaos.builder.prototype_plan.v1` as an adaptive DAG with
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

- [ ] `[must]` Run R8 evidence through `adaos builder e2e` so local, CI, and
  release evaluation use the same resolved run manifest, stages, graders, and
  metric definitions.
- [ ] `[must]` Run structural, behavior, i18n, accessibility, state-coverage,
  and authority checks before browser or visual-model evaluation.
- [ ] `[must]` Add browser task probes for compact and wide layouts using the
  same primary jobs and representative states from the brief.
- [ ] `[must]` Add a bounded screenshot/DOM/accessibility-tree review after
  deterministic checks. Permit at most two targeted repair iterations.
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
- [ ] `[could]` Compare two design candidates only for high-value ambiguous
  layout decisions and within an explicit additional budget.

Prompt-autonomy admission targets:

- at least 80% of held-out prompts pass deterministic checks on first
  candidate;
- `p90` requires at most one targeted repair;
- at least 80% of primary user tasks pass compact and wide browser probes;
- no false claim of a real effect, binding, authority, or supported component;
- EN/RU key-set, long-content, and accessibility gates pass for every admitted
  Prototype.

## R9. Cutover And Cleanup

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

Exit gate: an ordinary user can reach a relevant, executable, accepted
Prototype without understanding AdaOS internals, and the evidence demonstrates
that this result was not supplied by a subject-specific recipe.

## Could And Deferred

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
