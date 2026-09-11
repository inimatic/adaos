# Builder Prototype Readiness Review

Date: 2026-09-11. Status: development evidence, not a clean baseline or release approval.

## Decision

Do not advance to autonomous large-application creation or enable the semantic
route by default yet. The semantic compiler, local resource runtime and retained
evidence are useful foundations, but ordinary prompt-to-working-preview success
is not reliable. This is a pipeline assessment, not a ranking of GPT-5 intelligence.

Prototype acceptance now distinguishes an interactive demonstration from
production enforcement. Business rules and integrations require visible evidence,
an exact Brief binding, EN/RU disclosure and a testable pending Automation
obligation. They are not silently dropped and are not automatically platform
gaps. Supported CRUD, filters, selection and field validation remain executable
Prototype obligations. Copying deferred acceptance into an Automation request
does not yet prove its eventual executable closure.

## Retained Evaluation

Generation used `gpt-5`, minimal reasoning, the generic semantic-v2 route, no
domain packs, and one allowed full-candidate repair. Output limit remained 8,000
tokens per call; it was not shortened. Grader: `gpt-4.1`, version 10. Browser was
off in the automated cohort; the separate visual inspection below is not an
automatic upgrade of its verdicts.

Bundles under `e2e/artifacts/builder/`:

- `sdk-stage-aware-gpt5-20260911-01`: volunteer case.
- `sdk-stage-aware-gpt5-20260911-02`: seven remaining development archetypes.

| Case | Total seconds | Candidate outcome | Final cohort result |
| --- | ---: | --- | --- |
| Volunteer roster EN | 99.59 | Valid first candidate | 0.925, coverage-gap job partial |
| Operations work queue EN | 110.48 | Repair added duplicate editor field | Invalid |
| Service appointments RU | 141.85 | Valid after relationship repair | 0.925, free-time job partial |
| Household budget EN | 227.67 | Local wait expired during repair | Failed; late result reviewed separately |
| Equipment inspections RU | 144.00 | Repair used an editor for collection-state evidence | Invalid |
| Knowledge library EN | 130.96 | Repair introduced inconsistent empty-result evidence | Invalid |
| Media review RU | 223.26 | Repair introduced inconsistent empty-result evidence | Invalid |
| Inventory/procurement RU | 242.39 | Valid after repair | 1.0, passed without browser qualification |

Observed: 1/8 first candidates structurally qualified, 3/8 qualified within the
local workflow, 1/8 passed the complete configured cohort gate, 7/8 needed repair.
These eight development samples are neither 40 held-out prompts nor a reliable
pass-rate estimate. The inventory sample was archived by normal successful-run
cleanup; its evidence remains. Failed samples were retained for diagnosis.

The budget repair eventually completed at Root in 244.898 seconds, with 4 ms
queueing, 3.174 seconds TTFT and 3,330 output tokens. The complete response is in
`-02/evidence/late-results/household-budget-repair.json`. Merging the two original
SDK Brief receipts reproduced the exact original model-context digest; compiling
the late candidate against that Brief succeeded: three resources, six views,
nine requirement bindings. It was not materialized, graded or retroactively
declared a pass. See the adjacent `household-budget-review.json` receipt.

The earlier GPT-5 volunteer reference `sdk-semantic-v2-20260911-13` took 39.24
seconds overall, with 21.02 seconds provider execution; the new volunteer request
took 80.40 seconds provider execution. Its input increased from 2,911 tokens
(2,304 cached) to 3,234 (none cached), output from 3,192 to 3,764. One unmatched
sample cannot attribute the latency increase to the new instructions. Across the
new cohort, primary queueing was milliseconds and initial tokens arrived within
roughly two seconds; long provider execution and full repairs dominate. The
budget's late result proves that shortening a timeout would hide valid work.
Retain late completion and separate provider, queue, local validation and delivery
spans before choosing retry/deadline policy.

The two cohort reports account for 49,908 generation output tokens but omit the
late budget repair's 3,330 tokens. Three grader calls add 33,030 fresh input
tokens. A timeout currently understates incurred usage until reconciliation;
cache-hit percentage or time-to-first-token alone is not an end-to-end cost or
latency metric.

## Input And Output Diagnosis

Read both the retained system/user messages and complete candidates. The original
user request is present, and post-generation case rubrics are absent from the
generation context. However:

1. The Brief exposes some operations as isolated verbs (`review`, `assign`,
   `remove`) beside fuller job statements. Their exact IDs remain independently
   required. Operations, budget and media first candidates missed operation
   bindings despite implementing related commands. An explicit required-reference
   inventory and object/target-aware operations would reduce this ambiguity.
2. The compact Brief has empty entity/collection facts for requests that clearly
   imply them. The original text prevents total information loss, but generation
   must still interpret, design, create fixtures and maintain IDs in one response.
   The current compiler cannot be credited with a complete semantic intent model.
3. Instructions say foreign-key values must match the declared target field.
   Appointments and procurement nevertheless mixed ID-like values with a
   name-field target. The contract should distinguish identity from display labels
   explicitly; repair should receive the relevant relationship and records only.
4. Attachment values and representative-state proof have legal combinations not
   fully expressed by the provider schema. Primary/repair failures expose these
   contract gaps. Derive a concise invariant catalog from executable validation,
   rather than accumulate application-specific warning prose.
5. The volunteer request explicitly asked for coverage. The model emitted required
   headcount and a pending aggregate obligation, but no convincing actual
   filled/unfilled demonstration. A required amount is not a result. The pending
   obligation correctly preserves future work, but does not excuse this UI gap.
6. Full-candidate repair fixes one area and damages another. Operations repaired
   missing bindings but duplicated a field; knowledge/media repaired attachments
   but damaged empty-state proof. Retain the complete first answer, then address
   typed findings with a minimal semantic edit and preservation checks.

Grader calibration is also unfinished. It accepted volunteer state claims despite
weak actual coverage evidence, while appointments feedback introduced the word
"automatically" into a free-time criterion. A visible day/week list does not
necessarily provide a usable free-time workflow, but an automatic solver must not
be invented by the evaluator either. Calibrate both false passes and false fails
against human-labelled artifact/task pairs. Do not relax a rubric to make a run
green or treat a weighted score as proof of readiness.

## Visual And Primitive-Automation Readiness

The actual local Client (`http://127.0.0.1:8100/`) was inspected at 1440x1000 and
390x844 using the same volunteer candidate. Initial screenshots exposed three
resource-query HTTP 400 responses and a stuck loading indicator: the compiler
generated field filters, while SDK resource definitions admitted only `id/search`.
This was a Core/SDK defect, not a reason to change the application prompt.

The fix derives declared filters per resource and rejects unknown properties.
Real resource-workbench tests cover table/board projections, filtering, empty
filters and isolation from another resource's filter vocabulary. The exact
synthetic candidate was rematerialized without changing its WebUI or generating
again. Both layouts then had 12 widget hosts, two tables, zero loading indicators,
zero resource API failures and zero JavaScript errors. Shell diagnostics still
included node-status 401 and one transient reliability 503; they are retained,
not described as a completely clean browser session.

Evidence: `-01/evidence/browser-diagnostic` before the fix and
`-01/evidence/browser-sdk-final` after it, including top and bottom screenshots of
the inner scrolling surface. Explicit preview: webspace
`dev-e2e-stage-review-20260911`, scenario `volunteer_roster_e2e5d340eb90cc8`.

Visual quality is not accepted: overly long flow layout, raw enum values such as
`info_desk`, technical fixture wording, clipped card labels, and compact tables
requiring awkward inspection. Absence of document-level overflow does not prove
readability inside a card or table. The browser capture is a diagnostic tool, not
yet an EN/RU interactive acceptance suite or an autonomous visual reviewer.

| Primitive | Current readiness | Missing proof or boundary |
| --- | --- | --- |
| Local typed records and CRUD | Implemented substrate with regression tests | Per-generated-case create/edit/delete browser tasks |
| Selection, detail reveal, confirmation | Compiler/Client contracts exist | Required-task reachability, mobile ergonomics, preservation after updates |
| Search and field filtering | Implemented; SDK declaration mismatch fixed here | Case-level negative/empty/filter-reset probes |
| Required fields, simple local guards and state changes | Supported finite primitives | Do not confuse a stored fixture flag with enforced business logic |
| Typed relationships | Modeled and compiled to selectors | Identity/display clarity and end-to-end relationship tests |
| Joins, aggregates, cross-record constraints, external effects | Not generic Prototype primitives | Visible synthetic outcomes plus preserved Automation obligations |

Prototype should reuse the supported substrate immediately. It should not ask a
model to generate CRUD implementation code or defer already available actions.
Primitive automation needs an executable capability catalog: inputs, outputs,
preconditions, side effects, composition rules, failure states and fixtures tied
to the renderer/runtime version. A pattern name alone is not evidence that its
composition works.

## Large Prototypes And Granulation

Readiness: not demonstrated; current execution is not a scalable planning engine.
The SDK still delegates to `LegacyDevSkillPrototypeExecution`; semantic generation
and repair remain substantially orchestrated by the large Builder handler. The
semantic route is experimental, not the default ordinary-chat path. Multiple
resources or request phases do not constitute independently resumable work units.

The authoring schema advertises four resources/eight views, while its provider
projection removes cardinality constraints and lowering admits the broader
canonical contract. The procurement pass actually contains five resources. This
is a contract-consistency issue, not proof of capacity planning. Do not merely
raise limits or hard-code one phase sequence for every request.

Target: a small request may use one generation unit; a large request should first
produce a dependency-aware plan with shared entity IDs, navigation, locale keys,
requirement ownership and integration boundaries. Each unit receives the relevant
Brief slice, shared contracts and current dependency digests. It emits a semantic
delta, evidence and unresolved obligations. Core validates references, preserves
unaffected units and commits a coherent revision atomically. Interrupted units
resume without regenerating accepted work; invalidating a dependency replans only
its affected closure. This is an orchestrated workflow, not a requirement for
multiple agents or unrestricted parallel model calls.

Measure at least three sizes, including an application exceeding one-unit
resource/view capacity. Compare one-unit versus planned execution on total cost,
critical-path latency, first-pass/task success, context sufficiency, repair scope,
resume correctness and preserved bindings. Include a late change to a shared
entity and one failed unit. A technically valid partial screen must not be
presented as the completed application.

## Next Work Before Broader Rollout

1. **Must:** make the admitted requirements and all executable invariants
   unambiguous in stage-specific context; align provider/canonical capacity and
   relationship identity contracts. Add adversarial cross-domain fixtures.
2. **Must:** calibrated Prototype outcome grading plus deterministic wide/compact
   browser tasks for CRUD, state evidence, negative behavior and EN/RU. Verify
   actual scenario identity through the owner runtime before opening previews.
3. **Must:** semantic targeted repair with preservation checks, then optional
   dependency-aware planning for large requests. Prove resume and shared-contract
   updates; do not deploy a universal chain of LLM calls.
4. **Must:** prove pending-obligation closure at Automation/Trial with executable
   positive/negative tests. Prototype fixture evidence cannot close that gate.
5. **Should:** reconcile late Root results idempotently, with complete response
   retention, cancellation semantics and phase timings. Optimize measured work,
   not just deadlines. Pin cohort/compiler/renderer/model context digests.
6. **Should:** dev-only `adaos_tests` review workbench with latest-run projection
   and immutable history, as specified in the roadmap. No automatic beta badges
   or ordinary user desktop pollution for unqualified prototypes.
7. **Could:** bounded screenshot/DOM feedback with at most two targeted iterations;
   route component/compiler defects to their owners and retain them as contract
   comprehension examples. Never fix a renderer bug by domain-specific prompting.

This ordering follows evidence-driven evaluation and human calibration, rather
than selecting architecture by intuition. See the official
[evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices).
Stable contract prefixes should remain byte-stable for cache reuse; task-specific
slices belong after them. Cache savings do not justify irrelevant context or
full-document repairs. See
[prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).
These are engineering recommendations for this pipeline, not claims that a
particular provider feature implements AdaOS planning automatically.

## Environment And Reproducibility

DEV Builder checkpoint `builder@0.2.107` was uploaded through
`adaos dev project push builder`, with `builder_skill@0.3.164` and source revision
`sha256:d26f48ce06f1cb487f82a1507d80cfe6d32b30cf5d685daab8353e3a02b935ba`.
ProjectRelease digest:
`sha256:471a0e91cbd1edd936ad0d6461007bd7c3285d4b44cdada28ac7c525146d59c5`.
The DEV runtime is active on slot B. Workspace remains `builder@0.2.106`;
this iteration does not claim a new Trial/Workspace qualification.

Local regression: 360 Core/SDK tests passed in 232.03 seconds (semantic
compilation, context, E2E/grader, acceptance/handoff, workflow, CRUD workbench,
dotenv and Automation/worker); all 278 DEV Builder tests passed in 23.24 seconds.
These tests validate implementation behavior, not model autonomy or visual quality.

Persisted E2E JSON and compressed step evidence now use readable UTF-8. The 121
existing mutable checkpoint files were mechanically reformatted without changing
JSON values. Historical content-hashed evidence is unchanged; exact provider
strings are not rewritten. JSON Unicode escapes were valid encoding, not corrupt
Russian text, but they were an unnecessary readability problem.

The local `.env` contains 104 CRLF and 11 LF-only terminators, no bare CR, and a
final newline. The dotenv parser reads `ENV_TYPE=dev`, `ADAOS_LANG=en` and
`ADAOS_PROFILE=default` as separate values. The CLI writer passes append/replace
tests for LF/CRLF, with and without a final newline. No glued-line defect was
reproduced; `.env` was left untouched. Mixed endings alone do not establish the
reported cause.
