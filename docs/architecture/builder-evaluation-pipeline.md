# Builder E2E Evaluation Pipeline

Status: target architecture for reproducible Builder evaluation.

Last reviewed: 2026-09-10.

Architecture owner: [Builder Intent-to-Prototype Architecture](builder-intent-to-prototype.md).
Sequencing owner: [Builder Intent-to-Prototype Roadmap](builder-intent-to-prototype-roadmap.md).
Client prerequisite: [Client Component System Roadmap](client-component-system-roadmap.md).

## Purpose

AdaOS needs a reproducible answer to four separate questions:

1. Did Builder understand an ordinary user's request?
2. Did it produce a truthful, executable, useful Prototype?
3. What time, model work, context, repairs, and money did that require?
4. Is a candidate measurably better than the accepted baseline under matched
   conditions?

The evaluation system therefore executes the real Intent-to-Prototype path,
not a prompt-only approximation. It provisions isolated DEV state, talks to
Builder through public SDK boundaries, drives required review turns, validates
the result, opens it in the Client, performs browser tasks, and retains the
complete evidence chain.

## Product And CLI Boundary

Evaluation contracts and services are internal engineering infrastructure.
They must not expand the normal Builder user interface or expose every
low-level operation as a CLI command.

The only planned operator entry is:

```text
adaos builder e2e SUITE [--case CASE ...] [--tag TAG ...]
                       [--profile PROFILE] [--repetitions N]
                       [--baseline BASELINE] [--browser auto|on|off]
                       [--output PATH]
```

With no selector, the command runs the complete suite. `--case` and `--tag`
run deterministic subsets. Suite validation, environment capture, isolation,
resume, scoring, evidence persistence, baseline comparison, reporting, and
cleanup are stages of this one technological chain. Baseline acceptance is a
reviewed repository operation, not a convenient run flag.

The CLI is a thin adapter over the same evaluation service used by local
development and CI. It must not reimplement Builder workflow transitions or
invoke private skill helpers.

The bootstrap implementation may use the typed
`legacy_dev_chat.v1` compatibility adapter until the R3 public Builder SDK
operations exist. Every resolved run records that adapter and its input
attribution. The same unchanged case digests must then be run through
`sdk.v1`; adapter and implementation revisions are reference metadata, not
cohort equality dimensions. Results produced through the compatibility
adapter can characterize and protect legacy behavior, but cannot establish a
clean generic or prompt-autonomy baseline.

Visible suite and grader sources live under `e2e/builder/`; accepted baseline
manifests live under `e2e/builder/baselines/` and reference immutable result
artifacts by digest. Run artifacts live under `e2e/artifacts/builder/<run_id>/`
and are not source inputs. Sealed holdout payloads are supplied to the runner
from a separately access-controlled bundle and are not mounted into Builder,
component-retrieval, or implementation-agent workspaces.

## Declarative Contracts

### Evaluation suite

`adaos.builder.e2e_suite.v1` declares:

- immutable suite ID, version, digest, visibility, and owner;
- referenced cases and deterministic selection order;
- development, regression, sealed-held-out, or adversarial cohort;
- default execution/model profile, repetitions, concurrency, and seed policy;
- locale, viewport, network, cache, time, token, and cost cohorts;
- required graders, metric definitions, hard gates, and baseline policy;
- fixture and environment requirements;
- permitted tools/domain packs and explicit exclusions.

### Evaluation case

`adaos.builder.e2e_case.v1` declares one user-level job:

- case ID, archetype tags, language, ordinary user request, and optional
  follow-up turns;
- isolated starting fixture and exact reset/provisioning rule;
- a typed sequence of user-visible operations such as create/open Project,
  send chat turn, answer clarification, review assumptions, accept Prototype,
  open route, interact with the browser, and inspect evidence;
- expected intent facts, material unknowns, prohibited assumptions, authority,
  and preservation requirements;
- primary jobs and assertions for compact and wide representative states;
- structural, behavioral, i18n, accessibility, safety, and capability-gap
  rubrics;
- sanitization and retention policy.

Cases state outcomes and permitted authority, not component IDs, AdaOS paths,
prompt phases, recipes, or preferred implementation. Development cases may
name diagnostic fixtures; sealed-held-out generation cases may not.

### Run, result, and baseline

`adaos.builder.e2e_run.v1` is an immutable resolved execution manifest. It
captures suite/case digests, AdaOS and Client commits/builds, ABI and component
catalog digests, Builder/profile/model/provider versions, selected tools and
domain packs, input-attribution receipts, locale, viewport, network/cache
mode, seed, budgets, environment capabilities, and isolated Project/Run IDs.

`adaos.builder.e2e_case_result.v1` retains stage outcomes, traces, measurements,
grader versions, evidence refs, and failure ownership. A suite report
aggregates case results without replacing them.

`adaos.builder.e2e_baseline.v1` references accepted immutable run results and
their exact cohort. Baselines are append-only, named, reviewed, and
content-addressed. A comparison is `uncomparable` when material run dimensions
differ; it must not normalize those differences away silently.

The existing `adaos.builder.evaluation_evidence.v1` remains Change-level
admission evidence. Existing workflow metric records remain Run-level source
measurements. The E2E contracts aggregate and reference them; they do not
create competing truths.

### Dataset lifecycle

- `development`: visible cases used for debugging, architecture work, and
  detailed failure analysis;
- `regression`: disclosed failures and accepted behavior that must not regress;
- `baseline`: an immutable accepted result over exact case and environment
  digests, used for matched candidate comparison;
- `sealed-held-out`: undisclosed cases used sparingly for generalization and
  release claims;
- `adversarial`: poisoning, ambiguity, authority, and failure-injection cases.

A once-sealed set ceases to be held-out when its prompts or sample-level
results are inspected for implementation work. Its run remains a valid frozen
baseline, but the cases move to regression and a successor holdout is sealed.
This transition is explicit and never changes historical run metadata.

## Execution Pipeline

For each selected case, the runner:

1. Resolves and validates the suite, cases, profiles, graders, fixtures, and
   baseline, then writes the immutable run manifest.
2. Provisions an isolated DEV Project from a domain-neutral source such as
   `scenario_default`; no subject-scoped template may enter implicitly.
3. Captures the complete environment and input-attribution receipt before the
   first model call.
4. Executes the declarative sequence through public SDK/workflow operations,
   preserving each user turn, clarification, candidate, repair, and status.
5. Runs deterministic schema, semantic, authority, preservation, i18n,
   accessibility, and runtime checks before subjective grading.
6. Runs the same primary jobs in compact and wide Client profiles and retains
   DOM/accessibility snapshots, screenshots, console/network diagnostics, and
   a browser trace on failure or configured sampling.
7. Applies code-based graders first. A model grader is optional, versioned,
   blind to candidate/baseline identity, preferably independent from the
   generation profile, and periodically calibrated against human review.
8. Aggregates distributions, compares the matched baseline, applies hard
   gates, writes a machine-readable report plus concise human report, and
   cleans or retains isolated state according to policy.

The runner resumes only from a validated stage checkpoint with the same run
manifest digest. It writes a pre-step checkpoint before invoking an adapter,
so an interrupted state-changing step is replayed with the same deterministic
request identity and retained as an `interrupted` attempt. A retry is measured
as a retry; it never overwrites the first attempt. Ordinary retries are opt-in
per step by failure class and bounded. Case/repetition webspaces are distinct,
and a completed resumed run returns its existing immutable report.

The executable model-input receipt is
`adaos.builder.llm_input_attribution.v1`. Exact messages remain local in the
scenario-owned `llm_jobs/*.request.json` journal. Reports consume the compact
receipt: per-message digests and byte/token estimates, stable-prefix and
dynamic-suffix digests, selected component/pattern/example refs, domain-pack
receipts, and sanitized generation options. The journal is written before
provider submission so a timeout cannot erase the evaluated input. Provider
usage returned after inference remains authoritative for billing; the
receipt's `utf8_bytes_div_4_ceil` value is only a provider-independent
preflight estimate.

Before cleanup and scoring, the compatibility executor reads the actual
scenario journals, schema-validates every unique receipt, and compares the
observed profile/domain-pack set with the resolved suite policy. A model call
without a receipt or any profile/domain-pack mismatch makes the case
inconclusive as invalid evidence. Compact validated receipts are copied into
the run bundle before scenario cleanup.

## Metric Model

### Outcome and truthfulness

- required intent/operation coverage and unsupported-assumption rate;
- primary browser-job success in compact and wide profiles;
- semantic, runtime, i18n, accessibility, authority, and preservation pass
  rates;
- truthful effect/binding/component claims;
- capability-gap precision and recall;
- structured diagnostic ownership and recovery success.

### Autonomy and interaction

- first-candidate success and final success;
- clarification turns, questions, usefulness, and avoidable-question rate;
- user review actions and required manual correction;
- automatic repair count, scope, and success;
- total workflow steps and status-transition correctness.

### Cost and context

- fresh input, cached input, output, and reasoning tokens by stage;
- cache-hit ratio and stable-prefix reuse;
- actual billed cost and normalized uncached-equivalent cost;
- context bytes/items by source and unused retrieved context;
- model/provider calls, tool calls, and browser work per successful case.

Actual and normalized cost are both required so a warm cache does not make a
structurally larger context look cheaper. Warm-cache and cold-cache runs are
separate cohorts.

### Latency and reliability

- queue, context compilation, provider, validation, checkpoint, runtime
  refresh, Client readiness, browser task, and total wall time;
- median, `p90`, `p95`, variance, timeout, retry, reconnect, and flake rates;
- success and cost distributions across repeated nondeterministic runs;
- orphaned state, cleanup failure, and replay/resume divergence.

### Maintainability and growth

- component contracts selected and actually used;
- typed capability gaps by semantic role/data shape/operation;
- compiler approximation or compatibility fallback count;
- Client/ABI impact set size, bundle delta, and conformance regressions;
- domain-contamination and input-attribution violations.

No single weighted score may override a failed truthfulness, authority,
contamination, structural-validity, or primary-task gate. Weighted utility can
support ranking only after hard gates, with metric definitions and weights
versioned in the suite.

## Baseline And Comparison Policy

- Compare candidates only on the same case digests, profile, model settings,
  tool/domain-pack policy, cache/network cohort, locale/viewport matrix, and
  minimum repetition count.
- Report absolute values, candidate-minus-baseline deltas, relative change,
  sample size, dispersion, paired confidence intervals, and `improved`,
  `regressed`, `inconclusive`, or `uncomparable` status per metric family.
- Freeze failures during the first clean baseline. Classify and prioritize
  them only after the complete run, so fixes do not move the starting point.
- Keep deterministic hard gates separate from stochastic quality estimates.
- Audit model graders against retained human judgments and version their
  prompts, rubrics, model settings, and agreement metrics.
- Preserve sample-level evidence. Aggregate dashboards are navigation aids,
  not the audit record.

The initial eight archetypes used for Client capability analysis are a
development suite. A different initial set of at least eight archetypes and at
least 40 EN/RU prompts is sealed before its first clean run. If sample-level
results are opened to guide implementation, that set becomes the immutable
baseline/regression cohort and a different successor set remains sealed for
prompt-autonomy claims.

## Evaluation Tiers

- **Local smoke:** selected deterministic cases, no external model required,
  focused contract and runner self-tests.
- **Development:** visible archetypes and prompt mutations used to diagnose
  architecture, components, context, latency, and cost.
- **Nightly/manual model run:** repeated sampled cases with provider and
  browser evidence; never blocks unrelated unit-test feedback.
- **Release gate:** complete sealed-held-out and regression suites under fixed
  matched profiles, plus reviewed human spot checks.

CI must not publish or deploy merely because a model evaluation ran. Promotion
continues through the existing Builder approval and publication authority.

## Retention And Safety

- Remove credentials, private user data, subnet secrets, and raw unrestricted
  model payloads from retained fixtures and reports.
- Keep sealed cases inaccessible to generation context, retrieval indexes,
  examples, domain packs, and automatic failure-learning pipelines.
- Record permitted network/tool access and fail closed on undeclared access.
- Store large browser traces and screenshots as referenced artifacts with
  digests and retention policy rather than embedding them in workflow records.

## Admission Gates

The first clean baseline may start only when:

- the Builder generic-profile decontamination gate passes;
- Client roadmap C0 and C1 pass, and every component used by the baseline has
  at least the C2 contract subset required for truthful selection and errors;
- suite/case/run/result/baseline schemas and validators exist;
- `adaos builder e2e` passes deterministic self-tests, isolation, interruption,
  resume, and cleanup tests;
- metric collectors distinguish stage latency and fresh/cached/output usage;
- the visible development suite has debugged the harness without exposing the
  sealed held-out prompts.

New cross-domain Client primitives are deliberately not an admission gate.
They are justified by the clean baseline's capability gaps and evaluated by a
matched rerun.

## Implementation Status

As of 2026-09-10, the bootstrap runner includes validated suite, case, run,
case-result, report, and baseline ABIs; deterministic case/tag selection;
repetition accounting; required-step failure handling; redacted evidence
bundles; token and latency aggregation; matched baseline comparison; cleanup
hooks; and one `adaos builder e2e` entry point. Model-free self-tests cover
these foundations. Cleanup now binds each owned draft to the exact Project
manifest digest and primary component ref, removes both component and Project,
and makes a cleanup refusal render the case inconclusive. Large redacted step
outputs are gzip-compressed behind a digest-bearing evidence ref while the
case result remains compact; in-memory full outputs still drive typed step
references and usage accounting.

Suites that claim the generic Client profile must declare
`defaults.require_client_profile=true`. The runner then records the exact
Client commit and content-addressed capability inventory and verifies that
every component admitted by the Core catalog has a generic/shell runtime
registration before provisioning or model execution. Missing or invalid
inventory evidence and catalog/runtime drift are configuration failures, not
lower prototype scores. Unsupported semantic lowering kinds remain recorded
in the run environment for later C1/C2 gating.

The current adapter is explicitly `legacy_dev_chat.v1`. Case/repetition
webspaces are isolated; checkpoints and run identity support interruption and
resume; retry attempts remain explicit; and actual input receipts are checked
before cleanup. Isolated filesystem/state provisioning, internal
Core/provider/runtime stage spans, browser task assertions, calibrated
multi-grading, sealed datasets, and the target `sdk.v1` adapter remain open.
Therefore this
implementation is an evaluation bootstrap, not the R2 clean-baseline exit
proof.

Suite version 2 adds the post-generation `prototype.grade` step. The runner
injects the case jobs, representative states, prohibited assumptions, and
actual user turns only after the candidate is immutable, records the exact
grader request by digest, requires valid RFC 6901 evidence pointers, and keeps
grader latency/tokens separate from generation metrics. The resolved run and
accepted baseline cohort include the grader model and rubric version. The
visible suite fails to load when its outcome-grade gate is enabled but a case
omits this step. This follows the composite-grader direction supported by the
[OpenAI grader API](https://developers.openai.com/api/reference/resources/graders),
but local deterministic and browser graders remain authoritative for
executable behavior; a model judgment alone cannot establish a clean pass.

The visible development suite now contains eight ordinary EN/RU archetype
cases. They contain no component IDs, recipe names, AdaOS paths, or internal
implementation phases. A `builder.wait` step binds to the case webspace and,
when the Builder returns an artifact root, waits for the exact durable
`adaos.builder.llm_job_result.v1` journal instead of treating a reconciled
session revision as provider completion. This closes the observed race where
cleanup could remove a scenario while its worker was still validating or
writing telemetry. Compact Project creation receipts also retain the primary
component ref required for fail-closed cleanup.

The first live visible case produced three distinct diagnostic runs on
2026-09-10. The first exposed incorrect webspace propagation and a 420-second
poll timeout. The second completed functionally in 33.5 seconds but exposed
the session-reconciliation/cleanup race. After adding the durable terminal
barrier, the third reached a genuine Builder validation failure in 42.9
seconds: the model selected a useful work-queue board, repaired malformed
action structure, but left a static data source attached to a mutating move
action. Input attribution proved a generic profile with no domain packs and
three actual model requests. Provider usage was 24,320 input tokens, of which
14,976 were cached, and 2,001 output tokens. The validator now returns the
inconsistent source/action values and the exact expected contract, but this
case remains intentionally red until a generic architecture change passes a
fresh run.

This evidence also exposes a pre-R4 limitation: deterministic qualification
returned an unspecified surface and no structured requirements, while lexical
capability ranking selected board patterns. The model therefore received
strict component constraints without a typed statement of the user's jobs,
data, operations, or authority. Improving a repair prompt alone would be
case-level tuning; the required correction is the typed intent/Prototype Brief
and deterministic capability filtering defined by R4 and R5.

A fourth retained run, `operations-typed-20260910`, exercised the first typed
Prototype Brief. It selected the resource-board, master-detail, and data-entry
contracts from explicit inspect/create/assign/transition operations and passed
input attribution with no domain packs. The primary JSONL response was
syntactically malformed; one repair produced the intended query-backed board,
details, create/edit forms, six representative records, and all required
operations, but omitted the required `areas` property from two single-area
modal layouts. Total duration was 63.9 seconds with two model calls, 11,630
fresh input tokens, 4,480 cached input tokens, and 3,594 output tokens. Offline
validation proved that four unambiguous structural normalizations, adding the
single `main` area and binding each modal form to it, make that exact retained
candidate pass every generic request postcondition. The generic flow-layout
contract now exposes this exact shape and the parser records this bounded
normalization instead of spending another model call. The run remains failed
evidence; only a fresh run may establish the correction.

The fresh `operations-typed-normalized-20260910` run then passed creation,
generation, durable completion, schema validation, every request postcondition,
and input attribution with one model call. It took 46.4 seconds, used 7,146
fresh input tokens and 2,289 output tokens, and required no model repair. The
case was correctly reported as inconclusive rather than passed because the
compact Builder creation receipt omitted the Project primary component ref and
fail-closed cleanup refused to guess ownership. The DEV Builder receipt now
derives that ref from the Project aggregate's owned primary component.

The next retained run, `operations-typed-cleanup-20260910`, exposed two more
representational aliases in an otherwise relevant board candidate: the model
placed `resourceType` inside the resource query object and used `ghost` as a
button kind. One repair corrected only the first issue and the run failed in
43.8 seconds after two model calls (7,858 fresh, 9,088 cached input tokens and
2,144 output tokens). Catalog version 2.0.2 now gives the selected board
contract the exact resource-query and button shapes. The compatibility parser
also deterministically canonicalizes these unambiguous aliases and records the
normalization; this remains a bridge to the typed compiler, not permission to
silently approximate unsupported semantics.

The subsequent `operations-typed-contract-20260910` run again produced a
relevant resource-backed board but grouped two field descriptors under
`inputs.i18n`; WebUI requires the descriptors beside their scalar fallbacks as
`<field>_i18n`. The repair retained the invalid grouping, so the run failed in
52.3 seconds after two model calls (10,736 fresh, 6,400 cached input tokens and
2,508 output tokens). This happened despite the stable prompt already stating
the sibling-key rule, which makes further prose repetition a poor remedy.
Catalog 2.0.3 now enumerates the board's localizable input pairs, and the
compatibility parser moves a grouped descriptor only when its scalar sibling
exists and the canonical target is free; conflicts remain validation errors.

`operations-typed-i18n-20260910` is the first fresh end-to-end pass for this
visible case: all four required steps, input attribution, validation, and exact
draft/component/Project cleanup passed, and no DEV object remained. The run
took 78.7 seconds and used three model calls, 11,334 fresh input tokens, 4,608
cached input tokens, and 2,831 output tokens. A first repair addressed a
candidate defect but emitted a JSON Pointer to a non-existent stable id; the
legacy parser then made a second repair call. This pass proves functional and
cleanup correctness, but it fails the intended one-repair cost discipline and
must not be treated as a clean performance baseline. The typed plan/compiler
route must remove structural patch authorship from the design model, and the
legacy route needs an explicit repair-attempt budget in the interim.

The same live traces showed that the SDK opened a new TCP/TLS connection for
every Root job poll. `wait_response_job` now owns one bounded keep-alive session
for the exact Root that accepted the job and closes it when waiting ends; retry
classification, identity headers, request timeout, and Root ownership remain
unchanged. This removes local transport overhead and connection churn, while
provider `queue_ms` and `execution_ms` remain separately reported so the
optimization cannot be mistaken for reduced model inference time.

A subsequent RU equipment-inspection candidate exposed a false-positive
structural pass. It validated as WebUI and used one generation call in 43.1
seconds, but manual artifact inspection found only a draft form and static
check table: there was no row editing, photo input, completion transition,
conditional comment guard, or requested empty state. Running the immutable
artifact through `prototype.grade` rejected it. The first inexpensive
`gpt-4o-mini` judge scored `0.4375` but over-credited two static affordances;
the versioned `gpt-4.1` judge with stricter generic evidence rules scored
`0.425`, correctly rejected check editing and treated completion as partial.
That cold grader call took 13.7 seconds and reported 1,694 fresh input and 938
output tokens. These results validate the harness failure signal, not grader
calibration. Human labels, deterministic interaction proof, and compact/wide
browser tasks are still required before baseline admission.

The next fresh generation run failed before grading after three candidate and
repair calls: the final repair was invalid JSON. It took 61.8 seconds and
confirms that the legacy design-model-owned patch path is both unstable and
too expensive. The failure remains retained for the typed compiler work; it
must not be hidden by raising retry or output budgets.
