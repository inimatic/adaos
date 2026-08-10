# AdaOS Research Fabric

Status: target architecture with every non-deferred ARF0.5 through ARF4 item
implemented and validated locally by the TLP single-experiment vertical. The
research control plane is provider-neutral; TLP execution and primary data now
belong to a separate runner-provider skill. Research-project authoring,
autonomous TLP campaigns, and replication benchmarking remain target work.
Distributed execution remains an optional ARF5 scale lane.

Last reviewed: 2026-08-10.

This page defines a general research framework for AdaOS and uses Tropical
Learnable Pooling (TLP) as its first reference case. It intentionally does not
turn TLP, MLflow, Ray, or a particular database into AdaOS core concepts.

Implementation order and acceptance evidence are owned by the
[Research Fabric Roadmap](research-fabric-roadmap.md).

Portfolio placement and the distinction between Research Fabric, a solution
pack, and aResearcher as a solution agent or workbench are governed by the
[AdaOS Product Model](../product/index.md) and
[Solution Directions](../product/solution-directions.md).

## Decision Summary

1. The working architecture name is **AdaOS Research Fabric** (`ARF`). It
   describes a composition of governed workflows, skills, scenarios, storage
   capabilities, execution providers, trackers, and evidence rather than one
   autonomous research agent.
2. **aResearcher** remains a useful future product or assistant name. It may be
   the conversational surface over the fabric, but it is not the name of the
   persistence and execution contracts.
3. Research is delivered through the existing skill, service-skill, scenario,
   workflow, package, and activation lifecycle. There is no separate
   `adaos research ...` installation or runtime CLI.
4. There is no `.adaos/research` top-level directory. Durable component state
   follows existing AdaOS state and skill-runtime ownership rules.
5. MLflow is an optional typed experiment-tracking provider. It is neither the
   canonical AdaOS state store nor an API over which AdaOS may query MLflow's
   private SQL schema.
6. Ray is an optional execution provider. AdaOS submits and reconciles jobs;
   Ray schedules work. Ray does not own the research protocol, approval state,
   or claim decision.
7. Database support evolves as a capability with scoped bindings. SQLite is
   the local default. PostgreSQL is an optional shared service/provider with
   isolated logical databases or schemas and roles, not one database server
   installed by every integration.
8. TLP is the first end-to-end conformance case. Its domain types and operator
   semantics remain outside core until at least one unrelated research case
   proves that an abstraction is general.
9. A relational binding is private to its owning skill. Cross-skill data is
   published by a specialized owner skill as typed APIs, projections, events,
   or governed logical views; consumers do not receive its SQL binding.
10. A research manager stores governance metadata, not every domain's primary
    data. Each experiment declares a data-owner skill and runner contract; the
    resulting ResearchSpace is logically namespaced by owner and experiment.
11. Scenario help is a channel-neutral contract. A versioned README, Help
    modal, and conversational help/next-step intents all consume one
    workflow-aware projection rather than maintaining separate action advice.
12. Builder is the only software-authoring and adaptation control plane for a
    research project. A research template specializes Builder's existing
    Prototype, Preview, Automation, Trial, and Publication lifecycle; neither
    aResearcher nor a research scenario edits installed package sources.
13. Human/LLM research design produces a versioned `ResearchPrototype`, not an
    authoritative chat transcript. Human consensus accepts an exact digest;
    Automation receives a bounded implementation handoff derived from that
    accepted revision.
14. Full autonomy is a delegated execution mode over the same contracts. A
    signed `ResearchMandate` defines scope, budgets, tools, data access,
    software-mutation authority, stop conditions, and escalation. It never
    creates a privileged agent runtime or bypasses workflow gates.
15. Autonomous exploration and confirmation are separate evidence families.
    An agent may adapt hypotheses, analyses, and code inside the declared
    exploratory envelope, but confirmatory evidence requires a newly locked
    protocol and sealed evaluation resource.
16. Completion is established by evidence coverage, validation, and terminal
    workflow decisions, never by an LLM's final message. Negative,
    inconclusive, and budget-exhausted outcomes are valid completions.
17. Builder software publication and scientific result publication are
    distinct. A `ResearchRelease` fixes claims, evidence, methods, provenance,
    and attribution; a future essay is a read-only projection of that release.
18. TLP is the transparent first harness for the complete mechanism. The next
    evaluation family is a PaperBench-like replication benchmark with frozen
    tasks, target claims, author/expert rubrics, contamination controls, and
    comparable agent/process/cost metrics.

## Why `Research Fabric`

The term separates three concerns that should not share one name:

| Term | Intended use |
| --- | --- |
| AdaOS Research Fabric | Architecture and reusable runtime capabilities |
| Research workbench | Human-facing native UI inside an AdaOS webspace |
| Research project profile | Builder template and artifact profile for creating a study scenario and its owned runner skills |
| aResearcher | Assistant/orchestrator that discusses, proposes, or autonomously operates work within a Research Mandate |

`Research Runtime` is too execution-centric, `Research Workbench` is too
UI-centric, and `Experiment Fabric` is too narrow for non-ML studies. The name
is still provisional until the first vertical slice fixes public package and
capability identifiers.

## Problem

The exploratory TLP notebook is useful scientific reconnaissance, but its
current shape also demonstrates why a reusable research framework is needed:

- operator implementations and experiment variants are repeated inside one
  notebook;
- random seeds, data splits, augmentation streams, and environment identity
  are not one locked protocol;
- training, evaluation, plots, checkpoints, and interpretation are coupled;
- exploratory test transformations can accidentally become part of model
  selection;
- failures and partially executed cells are difficult to distinguish from a
  reproducible trial;
- no portable evidence package binds a conclusion to code, data, parameters,
  metrics, and statistical analysis.

These are general research-lifecycle problems. Solving them in a `tlp` module
alone would reproduce the same problem for the next study.

## Goals

The fabric should make a study:

- explicit: a question, hypotheses, protocol, analysis plan, and stop rules
  exist before confirmatory execution;
- reproducible: code, environment, data, operator, split, and RNG identities
  are recorded by digest;
- governable: risky transitions and test-set unblinding require explicit
  policy and evidence;
- portable: tracker and executor providers can change without changing study
  identity or scientific semantics;
- recoverable: attempts, heartbeats, cancellation, unknown outcomes, and
  checkpoints have durable meanings;
- comparable: paired trials and declared contrasts are first-class;
- authorable: notebooks, prose, code, and prior results can be ingested as
  immutable source evidence and refined into an exact Research Prototype;
- adaptive: an experiment campaign can express dependencies, branches,
  budgets, stop rules, and follow-up proposals without becoming an opaque agent
  scratchpad;
- autonomously operable: after a human locks a Research Mandate, aResearcher
  can plan, build, execute, review, and iterate within delegated authority;
- falsifiable under autonomy: exploration cannot repeatedly consume the same
  confirmatory holdout, and an agent cannot redefine success after unblinding;
- communicable: claims, counterevidence, tables, figures, limitations, and
  attribution can be fixed as a scientific release before narrative writing;
- measurable: the same harness can score human-assisted and autonomous work at
  target, experiment, claim, evidence, cost, and reproducibility levels;
- inspectable: a native AdaOS view explains state and decisions while
  provider UIs remain available for specialist diagnostics;
- extensible: TLP drives the first slice without making neural-network
  experiments the universal domain model.

## Non-Goals

The first framework does not:

- create a new package lifecycle or research-specific CLI;
- replace MLflow, Ray, PyTorch, notebooks, or specialist analysis tools;
- make SQL tables shared integration APIs;
- promise secure execution of untrusted code in the current process sandbox;
- give a language model authority outside a signed Research Mandate or let it
  bypass validation, policy, evidence, and publication contracts;
- autonomously publish research to an external journal, repository, or
  community endpoint in the initial autonomous profile;
- treat the historical TLP notebook results as confirmatory evidence;
- put ordinary trial checkpoints in `.adaos/models` before promotion;
- require PostgreSQL, a distributed cluster, or an object store for local use.

## Architectural Invariants

### One governance truth

AdaOS owns study identity, the locked protocol, workflow state, approvals,
trial and attempt identity, evidence references, and claim decisions. A
provider may own an operationally useful representation, but it cannot advance
the study state by mutating that representation.

### Provider APIs, not provider schemas

Adapters use a provider's supported API or SDK. No skill reads or writes an
MLflow, Ray, or other component's private database schema. Each owner controls
its migrations.

### Reproducibility is an input contract

A seed alone is not a reproducibility record. A confirmatory trial binds:

- source and package digests;
- environment and dependency lock digests;
- dataset version and split manifest digests;
- operator and configuration schema versions;
- named RNG streams for initialization, sampling, augmentation, and analysis;
- hardware and determinism policy;
- parent protocol and trial-group identities.

The runtime records deviations rather than silently normalizing them.

### Exploratory and confirmatory work stay distinct

Exploration may search configurations within a declared budget. Confirmatory
trials use an immutable enumerated plan. Exploration cannot silently add a
comparison to the confirmatory family or consume a sealed test set.

An autonomous loop does not weaken this rule. A result-dependent change to a
hypothesis, estimator, exclusion, stop rule, implementation, or metric creates
an exploratory successor. Promotion to confirmation binds a fresh immutable
candidate and a sealed evaluation policy. If no independent confirmation
resource remains, the final claim stays exploratory or inconclusive.

### Autonomous does not mean ungoverned

Human authority may be delegated in advance, but not omitted. Every unattended
session binds an immutable Research Mandate and `AutonomyProfile`; every action
is admitted by the intersection of workflow state, mandate, actor capability,
risk class, budget, freshness, and executor readiness. A policy-approved action
is still an approval decision with an exact target and evidence.

The controller is a deterministic durable workflow. LLMs propose plans,
interpret evidence, and select among admitted alternatives; they do not own
leases, retries, state transitions, budget accounting, unblinding, or package
activation. Missing authority or an unavailable safe continuation stops or
escalates the session rather than expanding its permissions.

### Conversation and agent memory are evidence, not truth

Chat is the primary design surface, and an experiment journal is useful agent
memory, but neither is canonical research state. Meaningful turns produce
schema-valid candidate revisions, decisions, or evidence references. Bounded
context packets carry exact source and artifact refs to LLM/Codex Runs; raw
transcripts do not grant authority and are not copied as mutable project state.

### Completion is evidence-gated

An autonomous Run may claim success only when every required target is covered
by accepted evidence, validators pass, and the workflow reaches a declared
terminal decision. A polished report, high self-review score, or final agent
message is insufficient. Failed, null, negative, and superseded paths remain in
the research ledger so selection and publication bias can be inspected.

### Evidence is portable

The final evidence bundle contains or references, by digest, all material
required to reproduce the declared result. Provider dashboards are convenient
views, not the sole evidence record.

### Core earns abstractions

Research-domain entities begin in a framework skill. Only contracts that are
also required by a second unrelated domain should be promoted into core.
Storage provisioning, service discovery, execution attempts, resource
requests, and artifact references are likely core candidates. `TropicalPool`,
`Hypothesis`, and a TLP metric are not.

## Target Composition

```text
notebooks / prose / code / papers
        |
        v
Builder research-project profile ---- canonical Builder conversation
        |                               Prototype / Preview / Automation
        |                               isolated Codex / Trial / Publication
        v
published study scenario + owned runner skill(s)
        |
        +---- aResearcher ---- Research Mandate / autonomous controller
        |                        plan / review / decision proposals
        v
research-manager skill -------- native Research Workbench
        |
        +---- governed workflow / campaign / approval / evidence / claims
        +---- relational-storage capability binding
        +---- runner-provider port ------- TLP experiment/data-owner skill
        +---- experiment-tracker port ---- local tracker
        |                              `-- MLflow service skill
        +---- executor port ------------ local process runner
        |                              `-- Ray service skill / cluster
        `---- ResearchRelease ---------- future read-only writer skill

AdaOS core supplies lifecycle, policy, identity, secrets, service discovery,
generic storage/execution seams, artifacts, conversations, governed workflows,
events, and projections. It does not supply TLP semantics or a second
agent-specific authority plane.
```

The first package slice uses `research_manager_skill` and `tlp_research` as
AdaOS identifiers. `research-manager`, `mlflow-tracker`, and `ray-executor`
otherwise describe roles, not a requirement to hard-code one provider.

### Implemented foundation slice

The implementation intentionally follows existing AdaOS extension points:

- `research_manager_skill` is a normal service skill with
  `storage.relational`, `execution.jobs`, and bounded `skills.invoke`
  capabilities, lifecycle migrations, provider-neutral experiment contracts,
  a local typed tracker, and an evidence verifier;
- `tlp_experiment_skill` is the TLP-specific owner of STL-10 primary data and
  implements `adaos.research.runner.v1`; it prepares execution descriptors,
  collects normalized outputs, and verifies its own artifacts;
- `tlp_research` is a normal package-bound workflow scenario with immutable
  protocol, analysis-plan, trial-matrix, evidence-policy, and sanitized
  exploratory-provenance fixtures, plus the shared Scenario Guidance contract;
- skills acquire private databases through `adaos.sdk.data.relational`; owner,
  physical path, DSN, and administrator credentials are not skill inputs;
- skills construct, submit, reconcile, and cancel immutable work through
  `adaos.sdk.execution`; a scientific run may have multiple physical attempts;
- SQLite is the node-local default. The PostgreSQL provider uses one isolated
  database and one least-privilege login/owner role per skill owner inside an
  operator-managed cluster; service credentials are injected only into the
  owning process;
- the local process executor is bounded but not hostile isolation. The optional
  OCI adapter requires a digest-pinned image and provides the stronger boundary
  for third-party or generated workloads.

Mutable research state remains inside the activated skill compatibility
bucket. The scenario package contains definitions and fixtures, not a private
database or copied notebook runtime.

The current ARF4 package set is `research_manager_skill` `0.9.0`,
`tlp_experiment_skill` `0.1.1`, `mlflow_tracker_skill` `0.2.2`, and
`tlp_research` `0.3.3`. The earlier `0.7.0`/`0.2.1` set supplied the accepted
E002 run; the current set preserves that immutable evidence while separating
the reusable control plane from the TLP data/runner boundary. The TLP Desktop
scenario provides a Single Experiment
Workbench for immutable condition revision, review/lock, bounded start,
cancel/retry/reconcile, paired results, artifacts, and result finalization.
Its `Conditions / Runs / Results / Evidence` segmented navigation is the first
main-area widget, so it stays at the top while the experiment command toolbar
keeps the composition's bottom action area.
Its Desktop presence does not weaken protocol, QC, unblind, analysis, or claim
gates. Scenario calls are routed only to declared dependency tools.
Help is now part of the top segmented navigation. Its modal renders the
scenario README and current state guidance; the same deterministic projection
is available in Russian and English text or voice through the admitted
conversational package. See the
[Scenario Guidance and Help Contract](scenario-guidance.md).

The accepted control aggregate is E002. It ran the real STL-10 binary dataset
on CPU for three epochs over a bounded 300-train/100-validation subset with
seed 17. Both arms completed on their first physical attempt with a shared
initial-state digest. Best validation accuracy was 0.31 for MaxPool and 0.29
for centered TLP, a diagnostic delta of -0.02. The result is explicitly
`workflow_validation`, not a scientific conclusion: one seed, a bounded
subset, and three epochs cannot support the TLP claim. E001 remains retained
as rejected instrumentation-QC provenance because its original initialization
digest was not stable across equivalent arms.

The E002 result and normalized tracker export are immutable and independently
verifiable; the export has a separate acceptance record and result
verification now reads that accepted export rather than the live provider.
Eight content-addressed artifacts passed verification after skill version
migration and AdaOS restart. The Desktop application remained installed after
rebuild/restart, and both MLflow attempt runs remained queryable with three
epoch points and their AdaOS run/attempt tags. The expanded provider
conformance matrix closes ARF4 locally; it does not claim the ARF6 scientific
reference proof. E002's historical `1.0-rc1` contract tag is preserved as
immutable provenance; the frozen provider and scenario declarations use
contract `1.0` for new sessions.

## Responsibility Boundaries

| Owner | Owns | Must not own |
| --- | --- | --- |
| AdaOS core | Skill/scenario lifecycle, workflow rails, identity, policy, secrets, service supervision, capability binding, generic run/attempt records, artifact refs, event envelopes | TLP protocols, statistical conclusions, MLflow schema, Ray scheduler state |
| Builder | Source intake, Research Prototype revisions, Preview, implementation handoff, isolated Codex Runs, software Trial, package Publication, dependency locks | Scientific execution state, test unblinding, claim truth, live research data |
| aResearcher | Human/LLM design dialogue, mandate-bound planning, candidate hypotheses/campaigns/analyses, admitted autonomous decisions, evidence-grounded synthesis | Direct source mutation, implicit permission growth, tracker/executor authority, external publication |
| Research manager skill | Provider-neutral Study/Experiment model, protocol locks, analysis plan, trial/run/attempt identity, tracker journal, evidence manifests, claim review, workflow guidance | Domain runner code, primary datasets, provider internals, global DB credentials, accelerator scheduling |
| Domain runner/data-owner skill | Domain preparation, primary data binding, execution descriptor, normalized output collection, owned-artifact verification | Research approvals, tracker authority, another skill's database |
| Study scenario | Domain workflow and templates, inputs, required capabilities, study-specific views and actions | New installation semantics or private infrastructure |
| Tracker provider | Parameter, metric, tag, and run-artifact ingestion and query | Protocol authority, approvals, claim truth |
| Executor provider | Submission, scheduling, logs, status, cancellation, resource placement | Study state, statistical plan, tracker identity |
| Model registry | Promoted, versioned model artifacts and serving readiness | Every intermediate training checkpoint |
| Future writer skill | Read-only rendering of an accepted ResearchRelease into a versioned draft essay/report | Reanalysis, new claims, mutable tracker access, journal submission authority |

## Research Domain Model

The research-manager skill owns versioned schemas for these concepts:

`SourceBundle`
: Content-addressed manifest of notebooks, prose, papers, repositories, data
  references, and extracted metadata supplied to a research project. Trust,
  license, sensitivity, origin, and exploratory/authoritative status are
  explicit; notebook outputs are never silently promoted to observations.

`ResearchPrototype`
: Builder-owned design-time candidate containing the research brief,
  hypotheses, campaign, analysis plan, capability requirements, assumptions,
  and open questions. Acceptance fixes one digest for Automation and later
  Study instantiation; chat remains linked provenance rather than the object.

`Study`
: Stable aggregate for a research question, owners, policy, budget, and
  lifecycle.

`ResearchMandate`
: Human- or policy-approved authority envelope for one assisted or autonomous
  research session: objective, scope, source refs, allowed hypothesis and code
  mutations, data-access policy, budgets, stop/escalation rules, output
  contract, and exact `AutonomyProfile`.

`ExperimentCampaign`
: Versioned directed graph of experiments, dependencies, branch predicates,
  evidence gates, resource budgets, stopping rules, and exploratory or
  confirmatory families. The user-facing term may remain "experiment series";
  the graph model prevents a series from being reduced to a mutable list.

`Hypothesis`
: Falsifiable statement with declared estimand or decision criterion.

`Protocol`
: Immutable-after-lock experimental design: datasets, variants, controls,
  resources, sample-size rationale, randomization, stop rules, and access
  policy.

`AnalysisPlan`
: Primary and secondary contrasts, metrics, aggregation, uncertainty,
  multiplicity handling, exclusions, and missing/failure treatment.

`TrialGroup`
: Trials coupled for comparison, such as TLP and MaxPool sharing the same
  split, initialization lineage, and named stochastic streams.

`Trial`
: One immutable variant specification within a group. It is not a process.

`Run`
: Logical execution of a trial. A retry does not create a scientifically new
  trial or a new run by default.

`ExecutionAttempt`
: One provider submission for a run, including lease, heartbeat, resource,
  failure, cancellation, and checkpoint state.

`Observation`
: Typed measured value or artifact reference with step, unit, split, and
  provenance.

`EvidenceBundle`
: Content-addressed manifest that snapshots the protocol, trials, required
  tracker exports, artifacts, analyses, and environment records used by a
  decision.

`ClaimDecision`
: Accepted, rejected, inconclusive, or follow-up-required decision with actor,
  policy, evidence bundle, and rationale.

`ClaimSet`
: Immutable synthesis projection over one or more Claim Decisions. Each claim
  distinguishes predeclared, exploratory, computed, and interpreted content
  and links supporting and contradicting evidence, uncertainty, limitations,
  and unresolved alternatives. It introduces no second claim authority.

`AgentDecision`
: One versioned proposal and admitted outcome in an autonomous session,
  including observed state, alternatives, rationale, confidence, mandate and
  budget preconditions, selected action, validator evidence, and model/tool
  provenance. An LLM note without this admission record cannot advance work.

`ResearchRelease`
: Content-addressed scientific result snapshot binding the Study/Campaign,
  accepted ClaimSet, evidence, methods, software/environment/data identities,
  generated tables/figures, negative results, deviations, attribution, and
  release policy. It is distinct from Builder's software `ProjectRelease`.

`DraftEssay`
: Future versioned, read-only narrative projection from one ResearchRelease,
  with section/sentence evidence links and LLM provenance. It may be a short
  essay or generic article draft; journal-specific adaptation and external
  submission are deferred.

The model deliberately distinguishes scientific identity (`Trial`, `Run`) from
infrastructure identity (`ExecutionAttempt`). This prevents a preemption or
worker loss from inflating the sample count. It also distinguishes design-time
source (`ResearchPrototype`), live scientific state (`Study`/`Campaign`),
software publication (`ProjectRelease`), and result publication
(`ResearchRelease`) so none becomes a second mutable copy of another.

## Workflow

The research manager expresses its lifecycle with the existing governed
workflow model:

```text
draft
  -> protocol_review
  -> protocol_locked
  -> smoke_validation
  -> confirmatory_execution
  -> quality_control
  -> test_unblind
  -> analysis
  -> claim_review
  -> accepted | rejected | inconclusive | follow_up
```

Exploratory execution is a separate branch with an explicit budget and no
implicit transition to confirmatory evidence. Protocol amendments create a new
version and record which trials are invalidated; they never rewrite a locked
protocol in place.

Test-set access is a governed effect. A test credential or data binding is
released only after the configured prerequisites are satisfied. This makes
test leakage an auditable policy violation rather than a notebook convention.

## Research Project Authoring and Builder Integration

Research starts in Builder when the required scenario, skills, and executable
base do not yet exist. The target `research_study` project profile specializes
the existing Builder lifecycle rather than introducing a research IDE or a
second source manager:

```text
SourceBundle
  -> deterministic notebook/document inventory
  -> optional bounded source-assessment Run
  -> research_study scenario Prototype and Preview
  -> human + LLM Research Prototype revisions
  -> accept exact Research Prototype digest
  -> derive capability gaps and Automation handoff
  -> isolated Codex implementation
  -> software validation and CPU Trial
  -> ProjectRelease and Workspace activation
  -> instantiate Study and ExperimentCampaign from the accepted seed
```

The source-assessment Run may inventory notebook cells, imports, outputs,
environment hints, duplicate implementations, likely data leakage, and code
that could be extracted. It may not decide that an exploratory output is true
or silently define the research direction. The Prototype LLM and human develop
the question, competing hypotheses, analysis plan, and campaign through typed
semantic patches whose diffs are visible in Preview.

The Automation packet contains only the accepted design digest, selected
source refs, capability requirements, allowed project paths, permissions,
scientific invariants, acceptance tests, and bounded implementation context.
Codex implements the experimental base; it does not receive authority to amend
the scientific objective to fit the code or observed result.

A published research scenario continues to evolve scientific state without
Builder when the existing contracts can express the change. New parameters,
experiments, campaign branches, and protocol amendments use Research Fabric.
A missing metric, runner, schema, visualization, data adapter, or execution
capability creates a typed `CapabilityGap` and a linked Builder Change against
the exact installed ProjectRelease. After Trial/Publication, the autonomous or
human-operated session may explicitly adopt the new package digest; historical
runs retain their original software identities.

The first implementation may use a scenario-rooted Builder project with one
project-owned runner companion and ordinary installed dependencies. The target
template catalogue should later describe composite roles explicitly so a
research template selects `research_study` for the scenario and a compatible
runner/data-owner template for newly required project-owned skills instead of
scaffolding every companion from `skill_default`.

## Autonomous Research Sessions

`aResearcher` is an orchestrator above Research Fabric and Builder, not an
alternate runtime. One `AutonomousResearchSession` binds a Study, exact
Research Mandate, source and release refs, workflow generation, budget ledger,
agent/model profiles, and terminal result. It survives process, model, and
conversation changes without reconstructing authority from chat.

### Autonomy profiles

| Level | Admitted outcome |
| --- | --- |
| `A0_assisted` | LLM proposes; a human accepts each state-changing scientific or software decision |
| `A1_execute` | The system executes and reconciles one already locked campaign without human turns |
| `A2_adaptive_exploration` | The system creates and selects exploratory branches within a locked search, data, and budget envelope |
| `A3_autonomous_engineering` | Capability gaps may create isolated Builder/Codex Changes and session-scoped Trial releases under policy approval |
| `A4_autonomous_research` | The session may iterate through research, engineering, evidence review, ClaimSet, and ResearchRelease; an optional later writer may derive a DraftEssay candidate |
| `A5_external_publication` | External submission or public dissemination; prohibited by the initial profile and requires a separate authority contract |

An autonomy level is a ceiling, not a permission bundle. The mandate still
declares exact allowed actions, paths, data, providers, dependencies, budgets,
and escalation triggers. Raising the level or budget creates a new approved
mandate revision; an agent cannot self-authorize it.

### Durable autonomous loop

```text
mandate_locked
  -> plan_candidate
  -> scientific_validation
  -> capability_check
       -> capable
       -> CapabilityGap -> Builder Change -> Codex -> Trial candidate
  -> experiment_revision_locked
  -> execute / reconcile
  -> evidence_and_QC
  -> deterministic_analysis
  -> analyst_proposal
  -> critic_and_policy_admission
       -> next exploratory node
       -> replication or ablation
       -> promote fresh confirmatory candidate
       -> stop rejected / inconclusive / exhausted / blocked
       -> synthesize ClaimSet and ResearchRelease
```

The workflow service owns transitions, idempotency, leases, and recovery. The
budget service accounts for model tokens, monetary cost, wall time, compute,
trial count, storage, and external requests. The agent chooses only among
currently admitted semantic actions and receives the updated projection after
each decision. Repeated implementation failure, evidence mismatch, lack of a
fresh holdout, risk expansion, or budget exhaustion terminates or escalates
the session; it never relaxes a guard.

### Adaptive search and confirmation firewall

Autonomous exploration may use tree search, tournaments, ranking, or a linear
agenda, but the search method is declared and its nodes are ordinary Campaign
revisions with identities, costs, and evidence. Search objectives must include
validity constraints and cannot be reduced to one leaderboard metric when the
scientific claim is broader. Candidate selection records both successful and
discarded branches so Goodhart effects and selection bias remain inspectable.

Confirmation starts from one frozen candidate and predeclared analysis plan.
Its evaluator receives a fresh or still sealed data binding and cannot expose
that binding to planning, coding, or exploratory contexts. A result-dependent
change after unblinding starts a new exploratory lineage. Sequential or
adaptive confirmation is permitted only when its spending/stopping rule was
locked before observations arrived.

### Agent roles and context separation

Planner, source analyst, Builder/Codex executor, experiment manager, analyst,
critic, and writer are semantic roles, not a requirement for seven concurrent
models. The baseline should use the smallest topology that passes evaluation.
Separate immutable context packets prevent the same mutable narrative from
serving simultaneously as hypothesis, implementation requirement, validation,
claim decision, and prose.

Multi-agent debate or parallel search is enabled only for naturally
decomposable work and only when matched-budget evaluation shows a gain. A
central validator/admission bottleneck remains even when planning is
distributed, because independent agents can amplify rather than correct shared
errors.

## Scientific Synthesis and Communication

Deterministic analyzers execute the locked Analysis Plan and emit typed results,
tables, and figure data with evidence refs. An LLM may explain those outputs,
identify contradictions, and propose follow-ups, but any post-hoc analysis is
labelled exploratory. The analyst proposal and critic response precede human
or mandate-authorized Claim Decisions.

`ClaimSet` is the machine-readable bridge between evidence and communication.
It keeps accepted, rejected, null, negative, and unresolved claims together;
records supporting and contradicting evidence; and separates observations,
computed results, interpretations, and external prior art. A fluent narrative
cannot replace this layer.

`ResearchRelease` is the stable object that a community can reproduce,
criticize, or cite. It fixes:

- the Study, Research Mandate, Campaign, protocols, and analyses;
- accepted ClaimSet and all required Evidence Bundles;
- source, package, environment, data, model, and agent/tool identities;
- exact table/figure generators and output digests;
- negative results, failed branches, exclusions, deviations, and limitations;
- human, LLM, Codex, and tool contributions and approvals;
- visibility, license, retention, embargo, and external-release policy.

The future `research_writer_skill` consumes only an accepted ResearchRelease
and creates a versioned `DraftEssay` in Markdown or another neutral format. It
does not query live MLflow, rerun statistics, create claims, select a journal,
or submit externally. If writing exposes a missing or contradictory fact, the
writer creates a synthesis issue and returns to Claim Review; it cannot repair
the record in prose. RO-Crate is a plausible future export mapping for a
portable ResearchRelease, not AdaOS's internal authority.

External feedback is imported as a versioned `ExternalReview` against an exact
ResearchRelease. Comments may create a scientific FollowUpProposal or a
Builder Issue, but do not mutate the published release. This closes the loop
from community criticism to a new Research Prototype or Campaign revision.

## Why AdaOS Has Structural Leverage for Autonomous Science

AdaOS does not gain an advantage by choosing a permanently better LLM. Its
advantage is the environment in which changing models must work:

1. **One governance spine.** Human and autonomous operations use the same
   workflow commands, expected generations, approvals, locks, and event
   evidence; autonomy is a policy profile, not a parallel implementation.
2. **Builder and science remain joined but distinct.** An agent can adapt real
   experimental software through isolated Codex, validation, Trial, package
   digest, and rollback without confusing a software success with scientific
   evidence.
3. **Evidence-gated completion.** Targets, runs, claims, tables, and reports
   must resolve to immutable evidence. Agent self-report is diagnostic text,
   not completion authority.
4. **Scientific and infrastructure identity are separate.** Retries,
   preemption, tracker replay, and package rebuilds cannot inflate samples or
   rewrite experimental lineage.
5. **Capability and data isolation are native.** Skills receive scoped
   storage, secrets, tools, and execution bindings; owner-qualified data and
   sealed evaluation can remain unavailable to planner and code-generation
   contexts.
6. **Durable long-horizon operation.** Runs, budgets, checkpoints, pending
   interactions, unknown outcomes, and provider reconciliation survive model,
   process, and conversation changes.
7. **Provider neutrality with specialist observability.** Local/MLflow and
   local/Ray can be compared under one semantic contract while their native UIs
   remain diagnostic surfaces.
8. **The product is benchmarkable.** The same task, tool surface, budgets,
   model profiles, and rubrics can compare A0-A4, single/multi-agent topology,
   and AdaOS versus an external baseline without changing scientific truth.

These properties turn AdaOS into a research-agent harness and governance
substrate. They do not prove that an agent has generated a novel or correct
idea; they make its process measurable, interruptible, reproducible, and
falsifiable.

## Core Capability Foundation and Gaps

The framework is not implemented by expanding the current raw `SQL` protocol
or by putting every research entity in core. The following narrow capabilities
are the implemented core seams. Their convergence boundaries and remaining
limitations are recorded in
[Research Fabric Core Readiness](research-fabric-core-readiness.md).

### Relational storage provisioning

A component requests requirements and receives a scoped binding. A conceptual
request contains:

```yaml
schema: adaos.storage.relational.requirement.v1
capability: storage.relational
owner_ref: skill:research_manager_skill
logical_name: experiments
requirements:
  durability: durable
  transactions_required: true
  concurrent_writers: 1
  json_required: true
  locality: node
  backup_required: false
  migration_owner: skill:research_manager_skill
```

The returned binding describes a provider, logical scope, secret reference,
capabilities, and lifecycle metadata. It does not publish a password in the
manifest or promise that SQL dialects and migrations are portable.

Required semantics include:

- private/shared scope and owning component;
- durability, transaction, concurrency, JSON, and size requirements;
- migration owner and schema/version compatibility;
- backup, restore, retention, rollback, and deletion policy;
- locality and network policy;
- read/write role and secret reference;
- readiness and degraded-state reporting.

The current `ctx.sql.connect()`-style boundary remains a legacy SQLite path
until repositories are separated from provider-specific behavior. Merely
changing a DSN is not PostgreSQL support.

The ARF2 SDK implementation derives `owner_ref` from the active skill context
after checking the existing `storage.relational` capability, returns a
redacted binding with the negotiated requirements, and rechecks that owner for
every transaction, migration, backup, and restore. A skill cannot request
another skill's binding. Unsupported capacity, retention, role, dialect, or
locality requirements fail closed. When multiple
skills need the same governed dataset, a specialized provider skill owns its
database and publishes stable logical views through typed service APIs or
projections. Direct cross-owner SQL remains out of scope.

### Execution provider

A provider-neutral boundary now supplies immutable `ExecutionSpec`, durable
`ExecutionAttempt` identity, resource/network/determinism/budget contracts,
submission idempotency, lease/heartbeat, bounded log and artifact streams,
cancellation, checkpoint/preemption contracts, accelerator inventory and
allocation records, and reconciliation after an unknown outcome. `Run` remains
research-domain state and is referenced rather than redefined by core.

This contract aligns with existing governed-workflow activity semantics
and the `ModelJob` direction in
[Model Runtime and Registry](model-runtime-and-registry.md). It must not create
a second workflow engine or a second model registry. Skill-facing usage and
provider limits are documented in [Durable Execution](../sdk/execution.md).

### Tracker provider

Tracker contract `1.0` binds every physical `ExecutionAttempt` to its own
tracking session while retaining the logical AdaOS `Run` across retries. It
normalizes metric namespace/name, value type, unit, direction, split role,
dataset digest, structured step, aggregation, observation time, producer
attempt/sequence, and evidence role. Sessions also carry immutable parameters,
AdaOS identity tags, dataset inputs, artifact references, completeness, and a
deterministic export.

The reference provider persists that contract through the neutral relational
SDK. The MLflow provider uses the same journal as a transactional outbox and
projects accepted events through supported MLflow REST endpoints. AdaOS owns
the replay/deduplication coordinate and evidence digest; the provider owns its
native query indexes and UI. Required terminal delivery fails explicitly, and
provider-native deletion is allowed only after the complete normalized export
has been accepted into immutable AdaOS evidence. The frozen operation,
identity, and failure semantics are documented in
[Research Tracker Contract 1.0](research-tracker-contract-v1.md). A tracker
contract becomes a core candidate only if a second non-research domain needs
the same semantics.

### Generic service UI surface

A supervised service skill may advertise an optional UI endpoint with routing,
health, authorization, and embedding policy. This is a generic service
capability, not an MLflow-specific iframe feature. Core now exposes only a
redacted surface descriptor and an authenticated same-origin proxy. The
generic `visual.serviceFrame` accepts a service id, not an arbitrary URL, and
uses a short-lived cookie bootstrap plus CSP, origin, request-size, health, and
lifecycle enforcement.

## Storage Topology

The target respects current AdaOS ownership:

```text
.adaos/
  state/
    adaos.db                         # existing core state; SQLite initially
  workspace/
    skills/.runtime/<skill>/vX.Y/
      data/
        db/                          # skill-owned DB files or binding metadata
        files/                       # skill-owned artifacts/exports
        internal/                    # schema-bound skill state
      runtime/logs/                  # derived operational logs
  models/                            # promoted model registry/artifacts only
```

The local MLflow service skill uses:

```text
.../.runtime/mlflow_tracker_skill/vX.Y/data/db/mlflow.db
.../.runtime/mlflow_tracker_skill/vX.Y/data/files/artifacts/
```

This is a local-development embedded SQLite file, not a separately installed
database server. The binding is confined to the provider skill; the research
manager never receives or constructs the backend DSN. The same service-facing
contract can select provisioned PostgreSQL and inject a generated
least-privilege login URI into only the MLflow process without weakening the
opaque SQL SDK used by ordinary skills. Isolation remains logical and explicit:

- separate database or schema per migration owner;
- separate roles and least-privilege credentials;
- no cross-owner table access;
- coordinated backup and service lifecycle;
- independent schema migrations and restore tests.

One shared server does not mean one shared schema. Conversely, local SQLite
files do not violate the rule against installing a DBMS per integration.

Large immutable artifacts use an independently negotiated blob binding. The
local provider resolves to the owning runtime's `data/files`; a provisioned
provider resolves an owner/logical-name-isolated object URI. The relational
store holds metadata and content-addressed references, not unbounded checkpoint
bytes.

## MLflow Integration

[MLflow Tracking](https://mlflow.org/docs/latest/ml/tracking/) provides a useful
typed vocabulary and API for experiments, runs, parameters, metrics, tags, and
artifacts. AdaOS should integrate it as a service skill and tracker adapter.

### Semantic mapping

| AdaOS | MLflow projection |
| --- | --- |
| Study | Identity tag and experiment namespace |
| AdaOS Experiment revision | MLflow Experiment plus immutable digest tags |
| Trial group / Trial / logical Run | Identity tags on each provider run |
| ExecutionAttempt | One MLflow Run and one tracker session |
| Immutable configuration | Parameters and configuration artifact |
| Observations | Metrics with split/unit/step conventions |
| Evidence artifacts | AdaOS content references and digest tags; binary ownership remains AdaOS |
| Claim decision | AdaOS-only state; optional summary tag for discovery |

MLflow owns query-optimized experiment telemetry during execution. AdaOS owns
the protocol, outbox journal, normalized export, artifact bytes, and decision
record. At a quality-control or claim gate, the research manager fixes the
normalized tracker export into immutable evidence. MLflow is therefore a real
tracker/query provider, but not the canonical research database or evidence
authority.

Integration rules:

- use the supported REST API or SDK, never MLflow backend tables;
- assign AdaOS identities before provider submission and record provider ids
  as bindings;
- include `study_id`, `protocol_digest`, `trial_id`, `run_id`, `attempt_id`,
  source, environment, dataset, and trace identities as tags or artifacts;
- keep secrets in AdaOS secret bindings;
- tolerate provider unavailability with bounded buffering and explicit
  degraded state; do not silently drop confirmatory observations;
- support a minimal local tracker so the framework does not require MLflow.

MLflow's backend store starts as the service skill's SQLite file and switches
to a provisioned PostgreSQL binding when the node advertises one. Its artifact
store likewise starts in the skill's `data/files` and switches to a
provisioned object binding. These physical locations are process-only; public
bindings and status remain opaque.

### UI

The primary UI is a native AdaOS Research Workbench generated from canonical
study and evidence state. It covers protocol review, trial matrix, progress,
comparisons, evidence, and approvals.

The TLP Workbench opens MLflow in a separate top-level tab so the experiment
composition remains native and compact. AdaOS also supports optional embedding
after introducing an authenticated same-origin proxy with authorization,
origin/CSP policy, request bounds, health, and lifecycle handling. The generic
iframe client and gateway have both passed browser/API tests; the live MLflow
React application, its JS/CSS assets, and its query API also loaded through the
gateway without a redirect loop, upstream URL leak, or CSP violation. A
scenario may opt in with `visual.serviceFrame` without learning the upstream
endpoint.

## Ray Integration

Ray is an executor, not the research manager. The first adapter should use the
[Ray Jobs API](https://docs.ray.io/en/latest/cluster/running-applications/job-submission/index.html)
or its supported client boundary:

1. AdaOS locks a trial and creates stable run and attempt identities.
2. The adapter submits an immutable entrypoint/package and resource request.
3. Ray returns a provider job id bound to the AdaOS attempt.
4. AdaOS reconciles status, logs, cancellation, and terminal outcome.
5. Trial code writes observations through the tracker contract using the
   preassigned AdaOS identities.
6. Retry creates a new `ExecutionAttempt`, not a new scientific sample.

Confirmatory trials are enumerated by the locked AdaOS protocol. Ray Tune may
be used for exploratory search within a locked search space, resource budget,
and scheduler policy. A chosen candidate becomes a new protocol input; its
search history is not retroactively confirmatory evidence.

The Ray Dashboard is an operator/debug surface. It should not be the normal
research UI and should not be exposed to ordinary study participants.

## Identity, Lineage, and Observability

Every provider record and emitted event should carry enough correlation to
join the planes without using timestamps or display names:

- `study_id` and `study_version`;
- source-bundle and accepted Research Prototype digests;
- Research Mandate, autonomy-profile, session, budget-ledger, and Agent Decision
  identities where assistance or autonomy is enabled;
- Experiment Campaign id/version and parent decision/branch refs;
- `protocol_digest` and `analysis_plan_digest`;
- `trial_group_id`, `trial_id`, and logical `run_id`;
- `attempt_id` and provider job id;
- `trace_id` and workflow instance/generation;
- code/package, environment, dataset, split, and operator digests;
- tracker provider and run id;
- parent checkpoint or model artifact digest when applicable;
- Builder Change/Run, ProjectRelease, ClaimSet, and ResearchRelease refs when
  the research base or scientific result has crossed those gates.

OpenTelemetry-compatible traces and OpenLineage-compatible dataset/job events
are desirable adapters, but AdaOS ids and evidence manifests remain stable
without either collector.

## Failure and Recovery Semantics

- Submission uses an idempotency key derived from attempt identity.
- A timeout after submission is `unknown`, not `failed`; reconciliation checks
  the provider before another attempt is created.
- Heartbeat loss and provider-reported failure are distinct reason codes.
- Cancellation is requested, acknowledged, and terminal as separate states.
- Checkpoint resume records the parent checkpoint digest and preserves logical
  run identity.
- Metrics from an abandoned attempt are retained for diagnostics but excluded
  from the declared analysis unless the locked policy admits them.
- Tracker export failure blocks evidence finalization; it does not create a
  successful empty bundle.
- Provider deletion cannot delete an accepted AdaOS evidence bundle.
- A timeout after an LLM or Codex mutation is `unknown` until the exact Run and
  workspace generation are reconciled; recovery never repeats a possibly
  completed change under a new identity.
- Agent planning may be recomputed after interruption, but an already admitted
  action, unblind, provider submission, package activation, or external request
  is reconciled from durable state before another action is selected.
- Model/provider nondeterminism is recorded. AdaOS requires reproducibility of
  the accepted experiment and evidence, not bit-for-bit replay of hidden model
  reasoning.

## TLP Reference Case

TLP is the conformance and architecture-debugging case for the first vertical
slice. The goal is not to automate the existing notebook unchanged. The study
must first be reconstructed as a protocol-driven package.

### Operator contract

- one canonical non-flat morphological pooling implementation;
- a centered spatial parameterization, with any scalar level shift represented
  explicitly rather than hidden in an unidentifiable kernel offset;
- zero shape parameters reproduce ordinary MaxPool within declared numerical
  tolerance;
- explicit padding, stride, dilation, dtype, device, and tie behavior;
- unit, gradient, serialization, CPU/GPU parity, and property tests;
- operator schema and implementation digest recorded in every trial.

### Study design

- clean train/validation/test separation;
- a frozen deterministic evaluation suite and a separately declared robustness
  transformation suite;
- paired MaxPool/TLP trials using the same split, initialization lineage, and
  named RNG streams;
- baseline matrix including ordinary MaxPool, parameter-count controls, and
  relevant fixed or constrained morphological variants;
- primary contrast, effect size, interval, exclusions, multiplicity treatment,
  and failure policy fixed before confirmatory execution;
- sample size justified by a pilot or sequential/power plan; ten paired seeds
  may be an engineering baseline, not an automatic statistical guarantee;
- test-set access sealed until validation and quality-control gates pass.

### Mechanistic evidence

Accuracy alone is insufficient for the proposed mechanism. The study should
also record:

- learned spatial kernel shape by layer and seed;
- winner/argmax spatial distributions and entropy;
- activation and gradient statistics;
- translation/shift sensitivity under fixed transforms;
- ablations that center, freeze, permute, or remove the learned kernel;
- relations between kernel phase bias, winner selection, and downstream
  performance.

### Reference acceptance

The slice passes only when a clean AdaOS node can install/activate the required
skills and scenario through existing commands, execute local paired trials,
optionally repeat them through Ray, inspect the same study through the native
UI and optional MLflow view, restart and reconcile the run, and export a
portable evidence bundle that independently recomputes the declared primary
comparison.

The notebook remains exploratory provenance. Its current outputs do not pass
this gate and are not imported as confirmatory trials.

### Autonomous TLP acceptance

TLP is also the first transparent autonomous-research harness. After the local
scientific base is valid, a human should be able to supply the notebook,
review, and research objective to a Builder `research_study` project, discuss
and accept one Research Prototype and Research Mandate, then leave an A4-bounded
session to:

1. instantiate and execute an exploratory Campaign;
2. identify a missing metric or implementation capability;
3. create an isolated Builder/Codex change and validate a session candidate;
4. continue without losing protocol, run, or evidence lineage;
5. select and lock a fresh confirmatory candidate without test leakage;
6. reach a positive, negative, inconclusive, exhausted, or blocked terminal
   outcome through evidence gates;
7. produce an independently verifiable ClaimSet and ResearchRelease;
8. prepare the fixed input contract for a deferred DraftEssay writer.

This proof measures workflow correctness, intervention rate, evidence
coverage, leakage, invalid implementation rate, recovery, cost, and result
reproducibility. It does not require TLP to beat MaxPool or the agent to discover
a novel mechanism.

## Security and Trust

The current process sandbox is an operational limit, not a hostile-code
security boundary. Before third-party or agent-generated training code runs
unattended, the executor needs stronger isolation, read-only input mounts,
explicit writable outputs, network policy, resource quotas, secret scoping,
and artifact scanning.

Untrusted papers, notebooks, repositories, logs, tracker artifacts, and web
content are also prompt-injection inputs. Source ingestion preserves bytes and
provenance but supplies only normalized, labelled excerpts through bounded
context packets. Source text cannot grant capabilities, change the Research
Mandate, reveal sealed bindings, or override system/package policy.

Autonomous Builder changes additionally require dependency allowlists, license
and secret scanning, command/network policy, bounded writable paths, package
diff and migration checks, and session-scoped activation. Public package or
scientific release remains a separately authorized effect.

Remote MLflow and Ray endpoints require authenticated service bindings, TLS as
appropriate, allowlisted origins/routes, and least-privilege credentials.
Research participants receive study actions, not raw database, tracker-admin,
or cluster-admin access.

## Replication Benchmark and Generalization Gate

TLP validates the transparent mechanism, but it cannot by itself establish
competitive autonomous-research performance or justify every core abstraction.
The next validation family is a PaperBench-like replication benchmark whose
tasks are imported as immutable benchmark releases and executed through the
same Research Fabric and Builder paths as TLP.

Each benchmark task contains or references:

- a paper/source snapshot, supplements, repository and data/environment refs;
- an expert-curated `TargetClaimSet` and hierarchical rubric;
- declared reproduction level and permitted source visibility/masking;
- expected outputs/tolerances without revealing hidden evaluator evidence;
- compute, time, tool, network, and model budgets;
- contamination/cutoff metadata and an immutable evaluator version;
- sandbox image or environment contract and licensing/release constraints.

AdaOS distinguishes progressive task levels:

| Level | Evaluation target |
| --- | --- |
| `R0_artifact_audit` | Identify whether supplied artifacts are sufficient and record ambiguities without execution |
| `R1_original_reproduction` | Run the exact released code/data/environment path and compare target claims |
| `R2_minimal_repair` | Apply separately classified compatibility fixes and repeat without methodological change |
| `R3_independent_replication` | Reconstruct and implement the method independently from the admitted paper materials |
| `R4_robustness` | Test declared sensitivity to seeds, platforms, variants, or additional data |
| `R5_follow_up` | Propose and test a new, explicitly exploratory extension after replication evidence is fixed |

Original, compatibility-repair, method-correction, and independent
implementation tracks never share a mutable source tree or silently replace
one another. A failure to reproduce is classified as reproduced,
reproduced-with-repairs, partially reproduced, not reproduced, or indeterminate
with explicit missing-artifact, environment, ambiguity, budget, or validity
reasons; it is not automatically evidence that the source paper is false.

The benchmark reports more than one final score: weighted target coverage,
numerical fidelity, protocol and evidence match, valid/invalid result rate,
claim calibration, reproducibility, intervention and clarification rate,
elapsed/compute/model cost, safety violations, and report evidence coverage.
AdaOS should export the task manifest, submitted ResearchRelease, evaluator
version, per-target results, and complete cost/process evidence so results can
be compared with PaperBench-like baselines rather than presented as an
uncontrolled demonstration.

At least one later task should exercise a materially different data and
analysis shape, for example scientific simulation, retrieval evaluation, or
device data. Before promoting research-manager contracts into core, the same
candidate contract must survive TLP and multiple replication tasks without
domain-specific exceptions.

Promotion requires evidence that the candidate contract:

1. is used unchanged by both cases;
2. has provider-neutral conformance tests;
3. has stable ownership and migration semantics;
4. cannot remain safely versioned in a skill or SDK package;
5. does not duplicate the governed workflow, model runtime, artifact, or event
   authorities.

## State-of-the-Art Alignment

The target follows current research-infrastructure and autonomous-science
practice without adopting a monolithic agent or confusing a research demo with
reliable evidence:

- typed tracking and remote tracking-server boundaries from
  [MLflow Tracking](https://mlflow.org/docs/latest/ml/tracking/) and its
  [backend-store architecture](https://mlflow.org/docs/latest/self-hosting/architecture/backend-store/);
- job submission and cluster reconciliation through supported
  [Ray Jobs](https://docs.ray.io/en/latest/cluster/running-applications/job-submission/index.html)
  boundaries;
- reproducibility controls consistent with
  [PyTorch reproducibility guidance](https://docs.pytorch.org/docs/stable/notes/randomness.html),
  while acknowledging that cross-release and cross-platform bitwise identity
  is not universally guaranteed;
- provenance-compatible ids and adapters informed by
  [W3C PROV](https://www.w3.org/TR/prov-overview/),
  [OpenLineage](https://openlineage.io/docs/), and
  [OpenTelemetry](https://opentelemetry.io/docs/);
- portable result-export compatibility informed by
  [RO-Crate](https://www.researchobject.org/ro-crate/specification/1.3/index.html),
  without making JSON-LD the internal governance model;
- the end-to-end ideation, code mutation, experiment-tree, journal, writing,
  and review loop demonstrated by
  [The AI Scientist](https://www.nature.com/articles/s41586-026-10265-5),
  while retaining its reported limitations: inconsistent workshop-level
  quality, incorrect implementations, methodological weakness, and citation
  hallucinations;
- hypothesis generation, reflection, ranking, evolution, and expert feedback
  demonstrated by
  [Co-Scientist](https://www.nature.com/articles/s41586-026-10644-y), but
  model roles remain optional topologies rather than new authorities;
- scorable sandboxed software search and explicit quality objectives from
  [Empirical Research Assistance](https://www.nature.com/articles/s41586-026-10658-6),
  generalized with scientific validity constraints, holdouts, and evidence
  rather than assuming one optimized score proves a claim;
- target decomposition, author/expert rubrics, and judge calibration from
  [PaperBench](https://openai.com/index/paperbench/), including process and
  cost evidence rather than only aggregate replication score;
- stagewise and end-to-end measurement from
  [MLR-Bench](https://proceedings.neurips.cc/paper_files/paper/2025/hash/ab8dd000d6f87f40061a73f8bca7fae4-Abstract-Datasets_and_Benchmarks_Track.html),
  whose reported high invalid/fabricated-result rate makes independent
  experiment/evidence validation a mandatory gate;
- long-horizon reliability metrics and failure categories from
  [ResearchGym](https://arxiv.org/abs/2602.15112), especially impatience,
  resource mismanagement, weak-hypothesis overconfidence, parallel-work
  coordination, and context limits;
- evidence-target workspaces and validation-gated completion from
  [Paper-replication](https://arxiv.org/abs/2607.02134): each claim must link a
  reconstruction, successful run, provenance, comparison, and report coverage,
  and completion cannot depend on an agent's final message;
- multi-domain hidden-target, raw-data, protocol/evidence-match evaluation from
  [ResearchClawBench](https://arxiv.org/abs/2606.07591), used as a later
  complement to PaperBench-like code/paper replication;
- matched-budget topology selection rather than an assumed multi-agent benefit,
  consistent with evidence that coordination can help decomposable tasks yet
  amplify errors and harm sequential ones in
  [controlled agent-scaling experiments](https://www.nature.com/articles/s42256-026-01268-y).

The architectural inference is deliberate: current systems demonstrate that
end-to-end autonomy is possible, but benchmark results still show a large
capability-reliability gap. AdaOS therefore designs for A4 autonomy now while
requiring typed targets, durable state, bounded authority, independent
validators, fresh confirmation, and comparable evaluation before making an
autonomous-science product claim.

## Related AdaOS Documents

- [Builder](builder.md)
- [Builder Conversational Development Architecture](builder-conversational-development.md)
- [Scenario Guidance and Help Contract](scenario-guidance.md)
- [Governed Data-Driven Workflow Model and Interaction Architecture](governed-workflow-runtime.md)
- [Artifact Source, Package, and Activation Architecture](artifact-source-package-activation.md)
- [Model Runtime and Registry](model-runtime-and-registry.md)
- [Skill Runtime Lifecycle](../skill_runtime.md)
- [Operational Event Model](operational-event-model.md)
- [Web UI Architecture](web-ui-architecture.md)
- [Security](security.md)
