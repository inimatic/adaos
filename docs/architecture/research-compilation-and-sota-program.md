# Research Compilation and Autonomous-Science Evaluation Program

Status: target architecture and empirical evaluation program.

Last reviewed: 2026-08-18.

This document defines the bridge from heterogeneous scientific source
artifacts to an executable AdaOS research direction and the evidence required
before claiming an improvement in AI-driven science. It is subordinate to the
[AdaOS Research Fabric](research-fabric.md) and sequenced by the
[Research Fabric Roadmap](research-fabric-roadmap.md).

The central decision is:

> AdaOS does not compete by being another monolithic AI Scientist. It provides
> a provider-neutral research control plane that compiles scientific intent
> into governed execution and preserves that intent across humans, models,
> coding agents, executors, trackers, evaluators, and time.

Tropical Learnable Pooling (TLP) is the first transparent calibration case.
Evaluating whether TLP beats MaxPool is not the framework objective. The
objective is to establish, under controlled comparisons, whether staged and
typed research compilation makes AI-driven research more scientifically
faithful, executable, reproducible, and efficient than a direct free-form
handoff to a coding agent.

## Problem and Candidate Contribution

Current autonomous-research systems demonstrate useful pieces of the full
cycle, but commonly start either before or after a hidden expert bridge:

- a hypothesis system may stop at a proposal or protocol;
- an empirical-software system may assume a well-defined scorable objective;
- a replication benchmark may rely on author addenda, expert rubrics, starter
  code, or previously extracted experimental details;
- a coding agent may receive prose that silently mixes scientific decisions,
  engineering advice, and evaluator expectations.

The missing object is not a larger prompt. It is an auditable compilation
boundary:

```text
source artifacts and human intent
  -> source analysis and scientific critique
  -> accepted research problem
  -> operationalized experimental protocol
  -> engineering and evidence contract
  -> bounded autonomous implementation
  -> governed execution and typed observations
  -> independently checkable evidence and claims
```

The candidate contribution is a **research compiler** whose output remains
human-readable but is also versioned, machine-actionable, digest-bound, and
testable. The compiler does not decide that a hypothesis is true and does not
replace scientific judgment. It makes the transfers of responsibility
explicit and exposes where information or validity was lost.

The first falsifiable framework hypothesis is:

> Under declared matched implementation-budget and total-system-budget
> comparisons, with models, tools, source artifacts, and execution environment
> controlled, staged formulation plus a typed execution contract increases
> evidence-valid end-to-end completion and reduces protocol drift and human
> repair relative to a direct free-form coding-agent handoff, without an
> unacceptable loss of task coverage or engineering adaptability.

This is an empirical claim, not an architectural assumption. AdaOS must retain
negative results if the comparison does not support it.

## Research Compilation Stages

The stages are logical contracts. They may initially be stored as sections and
revisions of `ResearchPrototype` and `AutomationBrief`; they do not each earn
a new core entity merely by being named here.

### 1. Source analysis

`SourceAnalysis` records what the selected artifact snapshot actually
contains:

- source inventory, extraction coverage, stable fragment refs, and omissions;
- observed code, configurations, outputs, and environment hints;
- explicit observations separated from author interpretation;
- claims, assumptions, contradictions, ambiguities, and missing information;
- provenance and confidence without treating notebook output as evidence.

This stage answers "what was supplied?", not "what should AdaOS conclude?".

### 2. Scientific problem formulation

`ResearchProblem` records:

- primitives and operationally relevant definitions;
- background and the unresolved tension or gap;
- one primary research question for the first executable slice;
- assumptions and plausible alternative explanations;
- falsifiable hypotheses and prohibited or unsupported claims;
- a minimal decisive test and a failure-update rule;
- unresolved decisions that require a person rather than silent inference.

Free prose remains essential here. Typed facets expose decisions that later
stages must preserve; they do not attempt to encode all scientific reasoning.

### 3. Operationalization and protocol design

`ExperimentalProtocol` maps scientific constructs to observable consequences:

- population, unit of analysis, intervention, comparator, and controlled
  invariants;
- outcomes, primary estimand, uncertainty, multiplicity, and practical
  threshold;
- sampling, allocation, pairing, random streams, data splits, and seals;
- smoke, exploratory, validation, and confirmatory profiles;
- stopping, unblinding, invalidation, amendment, and negative-result rules;
- expected observations, evidence classes, and decision regions.

This is the first boundary that the generic Research Workbench can manage
without knowing TLP or another domain's implementation.

### 4. Engineering compilation

`AutomationBrief` and the direction manifest project the accepted protocol
into bounded implementation obligations:

- exact writable targets and read-only source/context refs;
- provider operations such as `prepare`, `run`, `collect`, and `verify`;
- RunSpec, observation, result, checkpoint, and artifact schemas;
- artifact roles, storage ownership, retries, idempotency, and recovery;
- capability, dependency, environment, and resource constraints;
- executable conformance tests and prohibited actions;
- the exact scientific fields that implementation is not authorized to amend.

Codex remains free to choose internal modules, algorithms, libraries within
policy, optimizations, and diagnostic structures. It is not free to silently
change the question, estimand, comparator, evidence boundary, or confirmation
policy.

### 5. Bidirectional feasibility feedback

Compilation is not a one-way prompt generator. An implementation agent may
return a typed `clarification_required`, `feasibility_constraint`,
`capability_gap`, or `protocol_conflict`. Such a result creates a reviewed new
revision; it never authorizes Codex to repair scientific ambiguity by changing
the accepted protocol in place.

## Traceability Contract

Every material obligation should have a traversable chain:

```text
source fragment or human decision
  -> scientific requirement
  -> protocol element
  -> engineering requirement
  -> runtime observation or artifact
  -> evidence item
  -> acceptance or claim decision
```

Missing links are first-class findings. They identify whether failure belongs
to source understanding, formulation, operationalization, engineering
compilation, implementation, runtime infrastructure, or scientific
evaluation. A fluent final report cannot heal a broken chain.

The compiler also produces a coverage ledger. Every material point from an
expert review is classified as `compiled`, `deferred`, `rejected_with_reason`,
or `unresolved`. Evaluator-only material cannot become a hidden implementation
requirement: if an expectation is absent from the visible accepted contract,
its omission is a formulation failure rather than a Codex failure.

## Stage-Dependent Typing

There is no universal numerical "golden ratio" of schema to prose. Typing
strength changes by stage:

```text
source ideas and discussion       mostly free, provenance-bound
accepted scientific decisions     typed and versioned
implementation and exploration    free inside typed authority boundaries
run, observation, and evidence    strictly typed and content-addressed
scientific interpretation         free, claim-to-evidence constrained
decision and next revision        typed and immutable
```

A field is a candidate for the stable kernel when it crosses an actor, process,
time, storage, or provider boundary; affects reproducibility or claim
interpretation; or is required for deterministic validation. Domain-specific
science remains in a versioned profile or prose. Internal implementation
choices remain free unless they affect an external contract.

Repeated ambiguity or the same agent failure across tasks is evidence of a
missing type. Repeated schema workarounds, irrelevant required fields, or
domain-specific exceptions are evidence of over-typing. Core promotion
requires unchanged use across TLP and materially different research cases.

## Information Boundaries

Artifact visibility is explicit metadata, not a prompt-building convention.
The minimum stages are `formulation`, `implementation`, `execution`, and
`evaluation`; an artifact may also be restricted to human review.

For the clean TLP calibration:

| Material | Formulation | Codex implementation | Evaluation |
| --- | --- | --- | --- |
| Original notebook and user intent | visible | visible through admitted source refs and compact extraction | visible |
| Historical notebook outputs | provenance-only, untrusted | not evidence | visible for contamination checks |
| Existing `initial-review` | hidden in the from-raw arm; visible in the assisted arm | hidden | evaluator oracle |
| Legacy `tlp_research`/`tlp_experiment_skill` and E002 receipts | hidden | blacklisted | semantic oracle only |
| Accepted ResearchPrototype and AutomationBrief | produced/accepted | visible and digest-locked | visible |
| Hidden rubric and judge guidance | hidden | hidden | visible |

The historical TLP path used `initial-review` during formulation. It therefore
proved implementation from an expert-prepared scientific/engineering handoff,
not autonomous source-to-task compilation. That shortcut remains useful
evidence but must not be reported as a clean from-raw result.

## Controlled Typing Ablation

The same task, coding model, tools, environment, time, token, and compute
budgets are exercised under matched delivery arms:

| Arm | Agent-visible task material |
| --- | --- |
| `C0_raw` | raw artifacts and the original free-form request |
| `C1_reviewed_prose` | the same inputs plus a prose expert review |
| `C2_staged` | raw artifacts plus an accepted staged ResearchPrototype |
| `C3_typed_execution` | C2 plus compiled provider/artifact contracts and neutral conformance fixtures |
| `C4_over_specified` | C3 plus a detailed prescribed scaffold or implementation plan |

The objective is a Pareto frontier, not the arm with the largest schema. The
comparison measures scientific fidelity and operational reliability against
context size, cost, human effort, autonomy, and portability. TLP is the first
calibration task; it cannot establish generality or SOTA by itself.

The implementation keeps two digest-linked views of a compilation. The full
`research.compilation_package` is the audit authority for reviewers and
recomputation. A compact `research.compilation_projection` gives a developer
only source stance, the accepted problem, the exact protocol, and the
source-to-protocol trace. `AutomationBrief` v1.4 is the engineering delta: it
contains writable authority, provider obligations, acceptance checks, and
prohibitions, but no duplicate prototype, source inventory, or Builder
checkpoint. Removing audit-only duplication is not allowed to remove a
scientific or acceptance obligation; the predecessor and projection digests
make that check mechanical.

Two budget views are required. A fixed downstream-Codex budget isolates the
quality of the handoff; formulation tokens and expert-review effort remain
additional measured costs. A second fixed total end-to-end budget tests whether
the staged system remains more effective after those costs are charged and lets
the direct arm spend the same budget on planning or repair. Results must report
both views and must not treat the hidden labor that created C1 as free.

The primary endpoint is **evidence-valid completion**: the scientific question
and protocol are preserved, the implementation is executable through the
generic Workbench/manager contract, required observations and artifacts are
produced, lineage is valid, and the permitted conclusion follows from the
declared evidence without manual repair.

Secondary measures include:

- pass@1 and complete-experiment rate;
- protocol drift and unsupported assumption rate;
- source/review-to-contract coverage and unresolved ambiguity;
- conformance, runtime, result-validity, and reproducibility rates;
- sealed-data violations and invalid or fabricated results;
- clarification, intervention, and human repair time;
- tokens, model cost, wall time, compute, storage, and failed attempts;
- cross-model and cross-member-node portability;
- AdaOS-specific code required from the generated direction;
- expert and deterministic evaluator agreement.

All failures are attributed to a stage before aggregation. A platform outage,
bad formulation, invalid experimental design, and Codex defect are not one
undifferentiated agent failure.

### TLP calibration implementation evidence

The first frozen task, `tlp-research-compiler-calibration-v1` at
`sha256:56403379f3a441b250edfa18040d24e9e5f4eac01f12922d043eae1b4af0ff4a`,
is retained as a diagnostic pilot. Its C3 attempt produced a real candidate
and passed Builder validation after one automatic repair, but consumed
4,001,013 input tokens plus 22,644 output tokens across the initial and repair
turns (including cached input), far beyond its frozen 80,000-token budget.
The exact aggregate is recomputed from the retained JSONL journals rather than
copied from this prose. This is a scored budget failure, not evidence for C3.

That pilot also exposed two preregistration defects: the task did not freeze an
exact model/environment, and `paired_seeds` could be misread as model sampling
control even though Codex CLI exposes no such seed. Task schema v1.1 therefore
freezes provider, model, reasoning effort, tool profile, core commit, Python,
platform, executor and measurement policy. Paired seeds are explicitly
scientific-workload seeds; model random-seed control is recorded as
`unsupported_not_claimed`. A new task id/digest is required for the corrected
calibration. The v1 result and budget are immutable and must not be rewritten.

#### Frozen TLP C0-C4 calibration v5

The first complete five-arm execution is
`tlp-research-compiler-calibration-v5`, task digest
`sha256:0a199fa858148e1f670aef13f6fa252a499fa1cf9a95cc64d770defb493b73ff`.
It froze core commit `6bb666aa9c8f69369a3a202cf677e177ec28a059`, skill-workspace
commit `32ee2226499afe7b23e93fab38757d20dc6e46cf`, Python `3.11.9`,
Windows `10.0.19044`, `gpt-5.4` with `high` reasoning, the local executor,
and Research Orchestrator `0.20.0`, Research Evaluator `0.1.11`, and
calibration runner `0.1.7`. Each arm had one candidate and workload seed 17;
automatic Builder repair turns were retained in the cost. No human
intervention or model-random seed control was claimed.

| Arm | Mandatory checks | Model tokens | Wall seconds | Builder turns | EVC | Immutable result digest | First observed failure |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- |
| C0 raw | 1/6 | 5,841,343 | 1,380 | 2 | false | `sha256:fd4f2872baa0d891326bd6747d48ed98caf0605f7876d6c3b9420fa6f86c4911` | no installable runner; dependency/isolation contract absent |
| C1 reviewed prose | 1/6 | 5,332,699 | 993 | 2 | false | `sha256:e12602ea9fa0db9aa96568b8bf2a80b31449957d1d4a1f4249b4ee4fcd7a93aa` | no installable runner; dependency/isolation contract absent |
| C2 staged | 2/6 | 7,230,520 | 1,725 | 2 | false | `sha256:b38c1160d9b5ff874a9e59cbb3300dbc64adc70cc0e6fe44cad0555d8795263a` | semantically similar surface did not implement the exact runner ABI |
| C3 typed execution | 1/6 | 5,074,543 | 1,556 | 1 | false | `sha256:372bae2269bc69e1e0496adc3a7be1fff9bb30523a357cb724783c7de518e7db` | Forge rejected task-created runtime state outside its admitted source scope |
| C4 over-specified | 1/6 | 11,416,407 | 2,110 | 2 | false | `sha256:ac5b497de784905baee8b3139209ccf44f5cffa4f8b4d99a252f46df5c892c75` | packaging failed because candidate `operator.py` shadowed the Python stdlib module during dependency installation |

The fixed-downstream summary is complete but negative: `0/5`
evidence-valid completions, digest
`sha256:b28705843a23f4300d267f1d42948ac6535594306c1682b3f211dca220e6ea85`.
The result does not show that extra typing is ineffective: C3 produced the
exact runner operations, passed its tests, and completed a real three-epoch CPU
smoke before the delivery boundary rejected it; C4 also reached strict native
validation and a real smoke before packaging failed. It does show that no arm
crossed the preregistered end-to-end acceptance boundary and that C4's larger
context/scaffold was neither cheaper nor sufficient. One candidate per arm is
diagnostic calibration, not a variance estimate, comparative superiority, or
SOTA evidence.

The frozen evaluator attributed all terminal failures to operationalization
and, after a terminal Builder failure, could report secondary protocol drift.
Root-cause audit must therefore be recorded beside, not substituted for, the
immutable score. Later evaluator versions preserve the terminal Builder stage
and error so a packaging/platform failure cannot be masked by downstream
checks. The run exposed and drove general core fixes for retained workers,
terminal-state monotonicity, deterministic checkpoint reconciliation, exact
manifest ABI delivery, task-scoped automation runtime, explicit checkpoint
recovery, and authoritative isolated test bases (`651ddb6e`, `97d5ee35`,
`d66a1a63`, `d623b2f0`, `e753495a`, `7fb02713`, `d462cceb`). These fixes do not
retroactively change v5.

Research Evaluator `0.1.13` exports the task, all five exact packets, all five
immutable results, and the recomputed summary as
`sha256:44dab2cab3bba2705e59264bd6ddddca1030e51b49b8c29a2bd54d094a110be9`.
Its stored `ContentRef` has content digest
`sha256:31ac1fc627b53eae10c7c760bb7979699531348441ce61c8979c460e5aacf6ac`
and size 41,031 bytes. This makes the score recomputable from one bound object,
but a portable public release must still materialize admitted input bytes,
source licenses, frozen runtime artifacts, and an external verification recipe.

The independent evaluator derives checks from the frozen session, Builder
state, native validation, public runner operations, a bounded CPU trial and
content identities. Candidate-authored claims cannot mark a check passed. The
local trial receipt explicitly says `hostile_isolation=false` and
`network_enforced=false`; it is developer/workflow evidence, not a hostile-code
sandbox or confirmatory scientific run.

## Evidence Required for a Competitive Claim

A TLP receipt is a case study. A defensible comparative or SOTA claim requires:

1. a frozen AdaOS/compiler version, prompts, schemas, tools, and evaluator;
2. preregistered primary and secondary metrics and exclusion rules;
3. held-out tasks and evaluator-only materials with contamination controls;
4. matched models, budgets, environments, tools, and retry policies;
5. repeated paired runs with uncertainty rather than one successful trace;
6. deterministic checks wherever possible and calibrated LLM judging only for
   residual semantic criteria;
7. independent domain-expert review of formulation and scientific validity;
8. published negative outcomes, complete traces, costs, and intervention logs;
9. multiple task archetypes and at least one non-neural computational domain;
10. a portable result package from which scores can be recomputed.

The first multi-task suite may be called `ResearchCompilerBench` while it is
internal. It evaluates the artifact-to-experiment bridge. The broader AdaOS
Research Replication Benchmark evaluates reproduction and follow-up work. The
two tracks share manifests, visibility controls, evaluators, budgets, and
result packages; neither name implies a competitive result before matched
baselines pass.

## Position Relative to Current Work

AdaOS should reuse proven ideas and standards rather than claim them as new:

- [FirstResearch](https://arxiv.org/abs/2607.05682) makes question derivation
  inspectable through assumptions, mechanism, falsifier, minimal decisive
  test, expected observations, and a failure-update rule. Its reported
  evaluation is preliminary and judge-heavy, but the certificate is direct
  prior art for the formulation boundary.
- [DiscoveryBench](https://arxiv.org/abs/2407.01725) demonstrates a pragmatic
  facet-based formalism that is constrained enough for reproducible evaluation
  while spanning diverse data-driven discovery tasks.
- [The AI Scientist](https://www.nature.com/articles/s41586-026-10265-5)
  demonstrates end-to-end ideation, experiment execution, journaling, writing,
  and review, including template-free agentic search.
- [Co-Scientist](https://www.nature.com/articles/s41586-026-10644-y)
  demonstrates generation, reflection, ranking, evolution, and expert feedback
  for hypotheses and proposals.
- [Empirical Research Assistance](https://www.nature.com/articles/s41586-026-10658-6)
  demonstrates tree-search-based empirical software development when a
  scientific task and quality objective are already well defined.
- [Kosmos](https://arxiv.org/abs/2511.02824) demonstrates a structured world
  model for long-running coordination among literature and data-analysis
  trajectories with claim traceability.
- [PaperBench](https://openai.com/index/paperbench/) demonstrates author-informed
  hierarchical rubrics, hidden evaluation, judge evaluation, and realistic
  paper-to-code replication tasks.
- [EXP-Bench](https://arxiv.org/abs/2505.24785) explicitly extracts structured
  experimental details from papers and code and reports a large gap between
  partial task competence and complete executable experiments.
- [ScienceAgentBench](https://proceedings.iclr.cc/paper_files/paper/2025/hash/f12b4df26344f3be803c06b555252efe-Abstract-Conference.html)
  argues for stagewise assessment before end-to-end autonomy claims and
  validates tasks with domain experts.
- [MLGym](https://arxiv.org/abs/2502.14499) and
  [AIRS-Bench](https://arxiv.org/abs/2602.06855) provide open research-agent
  environments and full-lifecycle task suites useful for later matched
  external evaluation.
- [OpenML](https://docs.openml.org/concepts/) provides mature ML-specific
  `Dataset`, `Task`, `Flow`, and `Run` semantics and independently evaluated
  run results.
- [Workflow Run RO-Crate](https://arxiv.org/abs/2312.07852),
  [W3C PROV](https://www.w3.org/TR/prov-overview/), and
  [Common Workflow Language](https://www.commonwl.org/specification/) provide
  portable workflow and provenance concepts. AdaOS should map/export to these
  standards rather than invent a closed archival vocabulary.

The architectural inference, not a novelty claim, is that these capabilities
are still fragmented. AdaOS may contribute a durable integration layer where
the scientific contract, software authority, execution state, evidence, and
evaluation remain coherent while the underlying agent or provider changes.

## Next Research Workbench Iteration

Before treating the generated TLP direction as the start of an autonomous
Study, the Workbench and core contracts should support one clean research-
compilation proof:

- show Source Analysis, Problem, Protocol, and Engineering Contract as related
  revisions rather than one opaque consensus block;
- show source/review coverage, unresolved decisions, and traceability gaps;
- record stage visibility and exclude evaluator-only artifacts from generated
  model/Codex context by construction;
- distinguish human acceptance from model readiness at every stage;
- compile the accepted protocol into a narrow task envelope, artifact flow,
  provider bindings, and acceptance rubric;
- let Codex return typed feasibility feedback without mutating science;
- run the TLP `C0` through `C4` comparison in clean workspaces with the legacy
  implementation blacklisted;
- preserve all directives, attempts, costs, repairs, failures, and releases;
- use the generic Research Workbench and manager as the hidden integration
  evaluator, with no TLP-specific UI required from Codex;
- freeze the receipt as the first calibration package, explicitly without a
  TLP efficacy or cross-domain SOTA claim.

This proof is the admission gate for widening autonomous execution. It makes
the next implementation iteration serve the long-term AI-driven-science claim
rather than only completing another TLP workflow.
