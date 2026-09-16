# Builder E2E Evaluation Pipeline

Status: target architecture for reproducible Builder evaluation.

Last reviewed: 2026-09-14.

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

The public execution entry is `sdk.v1`. The explicit `legacy_dev_chat.v1`
adapter remains a characterization/control profile, not the default generic
baseline. Every run records its entry adapter, actual execution backend,
implementation/profile digests and input attribution. The SDK currently uses
a legacy skill execution port internally: the entry label alone proves neither
decontamination nor Core ownership. Clean-profile admission requires the
boundary and evidence gates below; ownership migration is independently tracked
in [BIP-07](builder-intent-to-prototype-roadmap.md#bip-07).
Compare unchanged case digests, declare any intentionally varied adapter/code
dimension and reject unexplained environment differences. Never hide a legacy
recipe-guided result inside a generic cohort.

Visible suite and grader sources live under `e2e/builder/`; accepted baseline
manifests live under `e2e/builder/baselines/` and reference immutable result
artifacts by digest. Run artifacts live under `e2e/artifacts/builder/<run_id>/`
and are not source inputs. Sealed holdout payloads are supplied to the runner
from a separately access-controlled bundle and are not mounted into Builder,
component-retrieval, or implementation-agent workspaces.

## Declarative Contracts

### Lifecycle Steps

Prototype review targets the exact workflow component and revision. Its resource
namespace can belong to the enclosing application: Core must verify that the
declared aggregate manifest owns that component before collecting acceptance
snapshots. A matching suffix, a dependency edge or a shared title is not ownership.
The original resource owner, change, revision and content digests remain bound.
Locale dictionaries remain separately bound acceptance evidence. They must not
enter the set of queried record sources merely because their evidence identifier
shares the `prototype.` prefix. Admission compares all evidence digests; resource
evaluation receives only the exact record snapshots.

The SDK adapter also admits `builder.workflow`, `prototype.accept`,
`automation.start`, `automation.wait`, `trial.prepare`, `trial.decide` and
`release.promote`. These technological steps require `ENV_TYPE=dev`, retained
test projects and the primary component owned by that exact case. They use the
public Builder SDK gates, not synthetic workflow transitions. A publication
project cannot expand the case's ownership.
`release.promote` requires `confirmed=true`: it activates Workspace and advances
the Root release channel, not merely a local runtime pointer. Publishing source
to the Git registry is a distinct subsequent operation.

Prototype/Trial review is explicit external evidence inside the run bundle,
bound to the current WebUI or candidate digest. `review_file` is run-relative;
an optional bounded `wait_seconds` allows human/agent review while the runner
remains observable. Evidence files must exist. The runner does not invent a
passed review from generated controls, a grader score or a queued worker.
Automation receives a separate `implementation_brief`, records its immutable
input digest and waits for the exact started session. A timeout does not cancel
the durable worker or authorize removing its source. Retained projection markers
advance with the actual accepted lifecycle rather than remaining unapproved
Prototype markers.

These adapters are a qualification slice, not a completed release pipeline:
real browser review, consumer install/update assertions and source-registry
publication remain required by [BIP-28](builder-intent-to-prototype-roadmap.md#bip-28).

For diagnosis after a terminal lifecycle failure,
`e2e/stand/continue-builder-lifecycle.py CHECKPOINT CASE OUTPUT` executes only
the unchanged case's lifecycle tail from its first failed step. Output must be
a new directory under the original bundle's `continuations/`. It records the
parent checkpoint digest and current code revision, uses the same SDK gates and
review files, and stops at the next failure. It never overwrites the original
checkpoint/report or turns continuation success into a fresh-cohort pass.

`prototype.browser` invokes the existing review, command, interaction or
capability probe in an isolated case preview. Each retry retains its own evidence
directory; `browser=off` is unavailable, not a passed review. Probe execution is
not approval and cannot mutate an already accepted Prototype. The explicit
review gate remains responsible for inspecting the resulting evidence.

### Evaluation Suite Contract

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
per step by failure class and bounded. Sequential cases and repetitions reuse
one existing Builder host selected by `ADAOS_BUILDER_E2E_WEBSPACE_ID` (standalone
diagnostics use `--host`). No `e2e-<run>-<case>` host is synthesized. Preflight
checks the registered host, production ancestor and active Builder before model
submission. Resume must keep the same host. A completed resumed run returns its
existing immutable report. Case isolation belongs to projects, data, sessions
and evidence, not webspaces; parallel preview allocation remains deferred.

The executable model-input receipt is
`adaos.builder.llm_input_attribution.v1`. The scenario-owned
`llm_jobs/*.request.json` journal is written before provider submission so a
timeout cannot erase the evaluated input. Before temporary Project cleanup,
the E2E bundle retains a complete sanitized copy of that request, the terminal
journal, every raw provider candidate, and every normalized candidate, each
with source and evidence digests. Reports also consume the compact receipt:
per-message digests and byte/token estimates, stable-prefix and dynamic-suffix
digests, selected component/pattern/example refs, domain-pack receipts, and
sanitized generation options. Provider usage returned after inference remains
authoritative for billing; the receipt's `utf8_bytes_div_4_ceil` value is only
a provider-independent preflight estimate.

Before cleanup and scoring, the compatibility executor reads the actual
scenario journals, schema-validates every unique receipt, and compares the
observed profile/domain-pack set with the resolved suite policy. A model call
without a receipt or any profile/domain-pack mismatch makes the case
inconclusive as invalid evidence. Compact validated receipts are copied into
the run bundle before scenario cleanup.

## Feedback Loop Comparison

The [Automation feedback contract](builder-automation-skill.md#candidate-verification-and-feedback)
applies within implementation as well as between completed attempts. The runner
records each selected check, observation and correction; worker completion is not
the outcome metric. Author-controlled feedback tools do not replace independent
final checks or add hidden UX requirements to a minimal working Prototype.

Start with a visible retained application regression, not the sealed holdout.
Reading List motivates generic cases for setting -> actual view -> reload,
media binding -> loaded image in cards/details, and accepted implementation ->
next semantic input preservation. Use synthetic records and fixed input snapshots;
do not copy Stable user data or put these domain solutions into generic prompts.

Compare predeclared arms from the same source/Brief and clean data state:

1. Current worker checks plus its post-turn deterministic repair feedback.
2. The same task with agent-requested scoped tests and validation during work.
3. The same task with additional candidate browser/runtime observations, including
   images delivered to the implementing model when relevant.

Freeze model/reasoning settings, source/SDK/Client/ABI, fixtures, locale/viewports,
network/cache conditions, independent gates and repetition count. The declared
feedback capability is the experimental difference, not a silent change to task
detail or test difficulty. Retain tool policy and exact observed inputs/outputs
for each arm. Report early failures and every repair; best-of-N is not first-pass
reliability. Complete each comparison batch before platform remediation unless a
real execution blocker prevents continuation, retaining the partial failed batch.

Measure time and model/tool cost to independent acceptance, including failed
attempts, test startup, browser work and repair; report success rate alongside
cost so cheap failures cannot win. Also report first-pass and final success,
escaped regressions, human interventions, false diagnoses, retries, context volume,
fresh/cached/output tokens and phase timings. Small samples are diagnostic and
cannot establish a universal speedup. Promote a feedback profile only with measured
benefit and no weakened authority/preservation gates; then repeat the visible cohort.

Final acceptance checks actual effects, not an agent's prose, saved setting alone,
HTTP 200 alone or a generated test that merely finds a string in a binding. Include
negative controls for inert actions, unsupported expression evaluation, stale
runtime identity, missing images and a setting that saves but changes no view.
Keep infrastructure failure, missing evidence and failed application assertions
distinct. Each repair invalidates receipts for changed source; reusing matching
hermetic results follows the runner's explicit cache policy, never a stale UI pass.

An optional separate visual LLM evaluator remains BIP-34. Feeding browser evidence
to the implementation model is part of BIP-21 and does not wait for that evaluator.
Use wide/compact task probes for UI-bearing qualification, not a mandatory second
model call or full browser suite after every line edit. Feedback to a candidate is
bounded evidence, not access to protected grader code, sealed cases or reference
solutions. Exposed evaluation material follows the existing dataset lifecycle.

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
- Retain complete development-run model requests and candidates only after
  structured redaction, with both source and retained digests and an explicit
  `content_redacted` marker. Sealed holdout artifacts follow their stricter
  access and retention policy.
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

## Stage-Specific Evaluation

Supported disposable CRUD and navigation require executable Prototype proof.
Installed persistence, cross-record business rules and external effects require
Automation evidence; a visible demonstration and a disclosed pending obligation
may qualify the Prototype only when the stage contract permits it.
`automation_requirements` binds an existing explicit requirement, not an excuse
for absent UI or an opportunity to invent additional scope. Preserve provenance
and the exact accepted Brief through the handoff.

Evaluate explicit user outcomes and platform invariants as hard gates.
Unrequested richness, screen count, modal preference or customary confirmations
are quality recommendations, not hidden mandatory criteria. Calibrate both
false positives and false negatives with independent human-labelled examples.
Version rubric/grader changes; a regrade never rewrites the original verdict.

E2E writers emit readable UTF-8, including compressed step evidence. Exact raw
provider strings and content-addressed old artifacts remain unchanged even
when their representation contains escaped characters. Missing evidence and
usage are explicit, not zero cost or a reconstructed historical success.

## Implementation Boundary

The CLI, schema-validated suite/run/result contracts, SDK submission, input
attribution, comparison and standalone browser/HTTP probes exist.
Prototype SDK execution still uses a legacy skill adapter; `sdk.v1` is not
proof of Core ownership. Separate stand journeys must be brought under the same
resolved lifecycle contract before claiming one complete E2E rail.

Current gaps: integrated candidate-selected checks and browser feedback, matched
feedback-loop comparisons, full-stage billing/latency reconciliation, interruption/re-entry
qualification, calibrated human labels, full browser/a11y/authority coverage,
sealed evaluation and installed lifecycle. Owners are BIP-03 through BIP-06,
BIP-13, BIP-15 through BIP-18, BIP-21, BIP-27 and BIP-28 in the
[corrective register](builder-intent-to-prototype-roadmap.md#current-task-register).
Dated measurements belong only in the [engineering journal](builder-engineering-journal.md).

## Diagnostic Tooling

Read-only review and interaction helpers are implementation aids for the public
E2E contract, not alternative user-facing CLIs.

- `e2e/stand/browser/prototype-review.mjs` captures the exact prepared
  scenario at wide/compact viewports, including errors and inner scroll surfaces.
- `e2e/stand/browser/prototype-interactions.mjs` derives interactions from
  an explicit retained checkpoint. Exercise owned records; report unsupported
  combinations as `not_exercised`, never success.
- `e2e/stand/browser/application-journey.mjs` runs independent Automation
  tasks against exact owned source/runtime identity, including real errors
  and preservation. Fake response data is not server-behavior evidence.
- `e2e/stand/replay-builder-state-repair.py` binds replay to an exact
  checkpoint into a new output directory; offline response validation makes
  no additional model call.
- `e2e/stand/compare-builder-effort.py` compares retained inputs, schemas,
  model options, results, timing and usage without regenerating applications.

Require explicit scope/token, `ENV_TYPE=dev`, TEST ownership and the existing
Builder preview relation. A dedicated `adaos_tests` desktop is deferred;
the existing project picker exposes retained TEST/date/unique application names.
Source visibility is not user approval or permission to publish.

Record effective model/effort/output settings on primary and repair requests,
not only requested environment variables. Keep output-budget experiments
separate from matched reasoning/context comparisons. Missing usage is not free;
response length limits must follow observed input/output analysis. Inspect real
media decoding/playback and in-viewport geometry, not merely DOM presence.
