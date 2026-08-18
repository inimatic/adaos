# AdaOS Research Fabric

Status: target architecture with every non-deferred ARF0.5 through ARF4 item
implemented and validated locally by the TLP single-experiment vertical. The
first ARF7 technical precursor proved typed formulation revisions and an exact
AutomationBrief; ARF7.2 validated bounded Codex realization of the direction
skill. ARF7.1 also validates the pre-Codex mechanics and its strengthened
authoring gate; the final authenticated browser reload receipt remains an
explicit UX acceptance item. ARF7.4 now specifies the required normalization
from the compatible one-Project/one-direction representation to explicit
ResearchDirection/ResearchTask/ImplementationTrack identities:
creation, artifact intake, focus, and formulation begin in a shared Research
Workbench, while implementation opens an explicit Project-scoped Builder
Development Session rather than a research-specific Builder tab. State-scoped full-surface layout variants,
provenance-aware artifact extraction, disclosed LLM context coverage, and a
core-owned deterministic admission review now prevent partial UI state or LLM
self-readiness from becoming an implementation handoff. Clean from-raw
research compilation, autonomous TLP campaigns, and comparative multi-task
evaluation remain target work. Distributed
execution/Ray is explicitly deferred; the current path executes on the
selected AdaOS member node.

Last reviewed: 2026-08-18.

This page defines a general research framework for AdaOS and uses Tropical
Learnable Pooling (TLP) as its first reference case. It intentionally does not
turn TLP, MLflow, Ray, or a particular database into AdaOS core concepts.

Implementation order and acceptance evidence are owned by the
[Research Fabric Roadmap](research-fabric-roadmap.md).

The scientific-problem-to-engineering bridge, controlled typing ablation, and
evidence required for a competitive autonomous-science claim are defined in
[Research Compilation and Autonomous-Science Evaluation Program](research-compilation-and-sota-program.md).

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
3. A `ResearchDirection` is a live scientific domain aggregate, not an AdaOS
   Project, skill, or scenario identity. Its logical owner is the user or
   Assistant; `research_orchestrator_skill` is the custodian and transition
   authority. A direction may reference one or more ProjectReleases containing
   its implementation skills. The current one-component Project/direction-skill
   path remains a readable compatibility form, but target APIs never assume
   `direction_id == project_id`.
4. Research uses the existing skill, optional scenario, workflow, package, and
   activation lifecycle. There is no separate `adaos research ...`
   installation or runtime CLI.
5. There is no `.adaos/research` top-level directory. Durable component state
   follows existing AdaOS state and skill-runtime ownership rules.
6. MLflow is an optional typed experiment-tracking provider. It is neither the
   canonical AdaOS state store nor an API over which AdaOS may query MLflow's
   private SQL schema.
7. Ray is a deferred scale provider, not an admission dependency for the
   end-to-end research loop. Until its lane is resumed, the direction skill
   uses AdaOS execution semantics on the current or selected member node. A
   future Ray adapter may schedule work but will not own protocol, approval,
   or claim state.
8. Database support evolves as a capability with scoped bindings. SQLite is
   the local default. PostgreSQL is an optional shared service/provider with
   isolated logical databases or schemas and roles, not one database server
   installed by every integration.
9. TLP is the first end-to-end conformance case. Its domain types and operator
   semantics remain outside core until at least one unrelated research case
   proves that an abstraction is general.
10. A relational binding is private to its owning skill. Cross-skill data is
   published by a specialized owner skill as typed APIs, projections, events,
   or governed logical views; consumers do not receive its SQL binding.
11. A research manager stores governance metadata, not every domain's primary
   data. Each experiment declares a data-owner skill and runner contract; the
   resulting ResearchSpace is logically namespaced by owner and experiment.
12. Scenario help is a channel-neutral contract. A versioned README, Help
   modal, and conversational help/next-step intents all consume one
   workflow-aware projection rather than maintaining separate action advice.
13. Builder is the only software-authoring and adaptation control plane for a
    research implementation, but it is not the research-management UI. Research
    Workbench invokes domain capabilities and links to a Project-scoped Builder
    Development Session. Neither the orchestrator nor Workbench edits installed
    package sources.
14. Human/LLM research design produces task-scoped, versioned
    `ResearchPrototype` and `ResearchCompilation` artifacts, not an authoritative
    chat transcript. Human consensus accepts exact digests; Automation receives
    a bounded implementation handoff naming the exact direction, task, Project,
    targets, artifact views, read-only context, and accepted revision.
15. Full autonomy is a delegated execution mode over the same contracts. A
    signed `ResearchMandate` defines scope, budgets, tools, data access,
    software-mutation authority, stop conditions, and escalation. It never
    creates a privileged agent runtime or bypasses workflow gates.
16. Autonomous exploration and confirmation are separate evidence families.
    An agent may adapt hypotheses, analyses, and code inside the declared
    exploratory envelope, but confirmatory evidence requires a newly locked
    protocol and sealed evaluation resource.
17. Completion is established by evidence coverage, validation, and terminal
    workflow decisions, never by an LLM's final message. Negative,
    inconclusive, and budget-exhausted outcomes are valid completions.
18. Builder software publication and scientific result publication are
    distinct. A `ResearchRelease` fixes claims, evidence, methods, provenance,
    and attribution; a future essay is a read-only projection of that release.
19. TLP is the transparent first harness for the complete mechanism. The next
    evaluation family is a PaperBench-like replication benchmark with frozen
    tasks, target claims, author/expert rubrics, contamination controls, and
    comparable agent/process/cost metrics.
20. Source material needed by LLM/Codex is local-first. The direction skill
    carries manifested `artifacts/partN` groups, the Skill SDK provides stable
    refs and bounded reads, and Codex receives native filesystem paths. An
    external artifact provider or MCP adapter may later resolve the same refs
    but is not a pre-Codex admission dependency.
21. ResearchDirection, Project distribution, ProjectInstallation, and Builder
    development context are distinct. Projects declare jointly shipped
    components and entry points; mutable scientific state belongs to the
    direction; focus, write targets, read-only dependencies, artifacts, and
    scratch access belong to a Development Session.
22. UI-as-data supports state-selected full-surface layout variants. Page
    state is scoped by webspace, scenario, and page; missing staged defaults
    are healed without overwriting user navigation. A detail view therefore
    cannot appear merely because an absent selection was misread as non-null.
23. The formulation LLM cannot author its own admission. AdaOS owns context
    coverage, provenance allowlists, and deterministic scientific/automation
    checks. A failed candidate remains an inspectable draft after bounded
    repair and cannot create an AutomationBrief.
24. Structured model output is a transport constraint, not a trust boundary.
    Formulation requests use provider-native JSON output when available;
    parsing errors and repair inputs are bounded, normalization is limited to
    mechanical contract-shape recovery, and schema plus deterministic semantic
    admission still run after every model response. Missing scientific choices
    are never invented by normalization.
25. Runtime execution location and package source are independent. Ordinary
    install resolves a published registry release; pre-publication verification
    explicitly selects the current workspace tree with
    `skill install NAME --source workspace`. Both paths use the same validation,
    slot, test, activation, and isolation contracts.
26. Research formulation includes an explicit **research compilation** bridge.
    Source analysis, scientific problem formulation, operationalization, and
    engineering compilation remain distinguishable revisions with a traversable
    source-to-evidence chain; a prose review is useful input, not an implicit
    substitute for that bridge.
27. Typing strength is stage-dependent. AdaOS strictly types accepted decisions,
    authority boundaries, executions, observations, evidence, and revisions,
    while preserving free scientific reasoning and engineering implementation
    inside those boundaries. There is no assumed universal schema/prose ratio.
28. The historical TLP review and implementation are evaluator oracles in a
    clean from-raw calibration and are hidden from autonomous implementation.
    Evaluator-only material cannot impose a requirement absent from the visible
    accepted contract.
29. TLP calibrates the mechanism; proving TLP efficacy is not the product goal
    and one TLP trace cannot establish autonomous-research SOTA. Competitive
    claims require frozen matched baselines, controlled typing ablations,
    repeated held-out tasks, uncertainty, independent review, and portable
    evidence packages.
30. A direction contains a dependency-aware `ResearchAgenda` of bounded
    `ResearchTask` objects. Formulation, compilation, implementation tracks,
    Studies, evidence, reviews, and decisions are scoped to a task and retain
    parent/branch lineage. The first UI may admit one active task, but the ABI
    must not encode a universal one-direction/one-task/one-skill relation.
31. Workbench navigation is a projection, not lifecycle authority. Portfolio
    cards select a direction; the selected direction uses a generic outline/tree
    plus a full-page selected-node view. Timeline, lineage graph, and run tables
    remain separate projections rather than being forced into one tree.
32. Software publication, direction export, and scientific-result publication
    are three explicit effects: `ProjectRelease`, `ResearchDirectionSnapshot`,
    and `ResearchRelease`. Export/import creates a derived local direction;
    true multi-writer federation is deferred until authority, ACL, conflict,
    and artifact-ownership contracts exist.
33. Branching is typed at the narrowest scientific or engineering level. A new
    question creates a ResearchTask; a protocol change creates a revision; an
    alternative engineering architecture creates an ImplementationTrack; seeds
    create Trials/Runs; and infrastructure retries create ExecutionAttempts.

## Why `Research Fabric`

The term separates three concerns that should not share one name:

| Term | Intended use |
| --- | --- |
| AdaOS Research Fabric | Architecture and reusable runtime capabilities |
| Research workbench | Human-facing native UI inside an AdaOS webspace |
| Research direction profile | Builder skill template for domain code, runner entrypoints, and skill-owned primary data |
| Research orchestrator | Shared durable skill that turns source bundles and dialogue into accepted pre-Codex handoffs |
| aResearcher | Future assistant/product surface that discusses, proposes, or autonomously operates work within a Research Mandate |

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
shared Research Workbench ----------- portfolio / selected direction
        |
        +---- research orchestrator --- ResearchDirection + ResearchAgenda
        |                               bounded ResearchTask(s)
        |                               artifacts/partN custody + manifests
        |                               dialogue / joined activity journal
        |                               task-scoped staged formulation
        |                               accepted ResearchCompilation
        |                               ImplementationTrack + AutomationBrief
        v
Builder Project Development Session - exact Project compatibility envelope
        |                             admitted implementation-skill targets
        |                             read-only presentation/dependencies
        |                             native read-only artifact paths
        v
isolated Builder/Codex change -------- validation / Trial / Publication
        v
candidate/published ProjectRelease ---- implementation + project-only helpers
        |
        +---- aResearcher ------------ Research Mandate / autonomous controller
        |                        plan / review / decision proposals
        v
research-manager skill --------------- deterministic research governance
        |
        +---- governed workflow / campaign / approval / evidence / claims
        +---- relational-storage capability binding
        +---- runner-provider port ------- research implementation skill
        +---- experiment-tracker port ---- local tracker
        |                              `-- MLflow service skill
        +---- executor port ------------ local process runner
        |                              `-- selected AdaOS member node
        |                              `-- Ray provider (deferred)
        `---- ResearchRelease ---------- future read-only writer skill

ResearchDirection --------------------- references exact ProjectRelease(s),
                                         Study/Campaign, evidence and decisions;
                                         never aliases any one of them

AdaOS core supplies lifecycle, policy, identity, secrets, service discovery,
generic storage/execution seams, Project/presentation/session contracts,
artifacts, conversations, governed workflows, events, and projections. It does
not supply TLP semantics or a second agent-specific authority plane.
```

The runtime proof uses `research_manager_skill`, `tlp_experiment_skill`, and
the earlier `tlp_research` Workbench scenario. The authoring precursor adds
`research_orchestrator_skill` and a `research_direction` Builder skill
template. The target authoring path moves that domain surface into the shared
`research_workbench` scenario and creates the Project/skill through Builder
SDK. The earlier scenario remains a useful dedicated experiment UI, but new
directions do not need to generate a scenario. `research-manager`,
`mlflow-tracker`, and `ray-executor` otherwise describe roles, not a
requirement to hard-code one provider.

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

The published authoring set is `research_orchestrator_skill` `0.2.0`,
`research_workbench` `0.0.3`, `builder_sdk_control_skill` `0.1.59`, generic
`skill_preview` `0.0.1`, Builder scenario `0.2.60`, and AdaOS client
`0.0.310`. The earlier precursor's
common Research view inside Builder is no longer the product boundary.
Portfolio, focus, intake, dialogue, and acceptance belong to Workbench;
Builder retains Project SDK, scoped source development, Codex, Trial, and
publication. Neither path replaces the runtime research manager or makes the
earlier dedicated TLP scenario mandatory for new directions.

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
| AdaOS core | Skill/scenario/Project lifecycle, presentation and Development Session contracts, workflow rails, identity, policy, secrets, service supervision, capability binding, generic run/attempt records, artifact refs, event envelopes | TLP protocols, statistical conclusions, MLflow schema, Ray scheduler state |
| Builder | Project/template creation SDK, explicit development targets/context, source mutation, Preview, isolated Codex Runs, software Trial, ProjectRelease Publication, dependency locks | Research portfolio/focus, formulation workflow, scientific execution state, test unblinding, claim truth, live research data |
| aResearcher | Human/LLM design dialogue, mandate-bound planning, candidate hypotheses/campaigns/analyses, admitted autonomous decisions, evidence-grounded synthesis | Direct source mutation, implicit permission growth, tracker/executor authority, external publication |
| ResearchDirection aggregate | Direction identity, metadata, source manifests, agenda/tasks, accepted decisions, implementation/release refs, domain activity lineage | Project package composition, Builder source mutation, provider-private state |
| Research orchestrator skill | Durable custody and transition authority for directions, task formulation/compilation, activity normalization, Workbench commands, export/import policy | Logical ownership of a user's science, component source mutation, tracker/executor internals |
| Research manager skill | Provider-neutral Study/Experiment model, protocol locks, analysis plan, trial/run/attempt identity, tracker journal, evidence manifests, claim review, workflow guidance | Domain runner code, primary datasets, provider internals, global DB credentials, accelerator scheduling |
| Domain runner/data-owner skill | Domain preparation, logical source/artifact custody through scoped SDK bindings, primary data binding, execution descriptor, normalized output collection, owned-artifact verification | Research approvals, tracker authority, another skill's database |
| Shared Research Workbench | UI projection of portfolio/focus, artifact intake/inspection, formulation, activity, exact acceptance, Builder handoff, and progress; commands are delegated to owning capabilities | Direction persistence/identity, direct source mutation, Codex implementation, scientific truth, a second workflow state |
| Optional domain scenario | Specialized post-publication workflow/views when the shared Workbench cannot express a domain need | Default direction identity, new installation semantics, or private infrastructure |
| Tracker provider | Parameter, metric, tag, and run-artifact ingestion and query | Protocol authority, approvals, claim truth |
| Executor provider | Submission, scheduling, logs, status, cancellation, resource placement | Study state, statistical plan, tracker identity |
| Model registry | Promoted, versioned model artifacts and serving readiness | Every intermediate training checkpoint |
| Future writer skill | Read-only rendering of an accepted ResearchRelease into a versioned draft essay/report | Reanalysis, new claims, mutable tracker access, journal submission authority |

## Research Domain Model

Research-domain skills own versioned schemas and transition capabilities for
these concepts. Logical ownership remains with the named aggregate; the shared
Workbench is only their projection.

`ResearchDirection`
: Long-lived scientific workspace containing identity/description, source
  manifests, `ResearchAgenda`, decisions, implementation and release refs,
  aggregate status, and activity lineage. It may outlive or reuse any one
  software Project. Its current Project and skill ids are explicit refs, not
  aliases.

`ResearchAgenda`
: Versioned dependency-aware portfolio (DAG) of bounded ResearchTasks,
  alternatives, priorities, prerequisites, contribution ownership, and
  integration goals. It makes parallel and follow-up questions visible without
  turning an entire direction into one oversized prompt.

`ResearchTask`
: One bounded scientific question with objective, inputs, expected artifacts,
  hypotheses/falsifiers, protocol/evaluation requirements, boundary
  constraints, dependencies, order, status, and parent/branch lineage. A task
  is the primary scope of formulation and engineering compilation.

`ResearchCompilation`
: Immutable accepted bridge for one ResearchTask. It binds SourceAnalysis,
  ResearchProblem, ExperimentalProtocol, traceability/coverage, exact source
  views, unresolved decisions, and the AutomationBrief used by Builder.

`ImplementationTrack`
: One engineering line for realizing a ResearchTask, including architecture
  choice, implementation Project, writable targets, Development Sessions,
  candidate/ProjectRelease lineage, validation status, and feasibility feedback.
  Multiple tracks may compete or provide independent replications without
  duplicating the scientific task.

`SourceBundle`
: Immutable logical selection of manifested `artifacts/partN` items, optional
  additional ArtifactRefs, and deterministic extracted metadata supplied to a
  ResearchDirection or ResearchTask. Local files are the first provider and
  remain directly readable by Codex. Trust, license, sensitivity, origin,
  publication policy, and exploratory/authoritative status are explicit;
  notebook outputs are never silently promoted to observations.

`ResearchPrototype`
: Research-orchestrator-owned design-time candidate containing the research brief,
  hypotheses, campaign, analysis plan, capability requirements, assumptions,
  and open questions for one ResearchTask. Acceptance fixes one digest plus the
  exact direction/task, artifact groups, implementation Project, and target
  policy for Automation and later Study
  instantiation; chat remains linked provenance rather than the object.

`Study`
: Stable execution/evaluation aggregate instantiated from one accepted task and
  protocol, with owners, policy, budget, and lifecycle. A direction may contain
  multiple Studies over time.

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
source (`ResearchPrototype`/`ResearchCompilation`), live portfolio state
(`ResearchDirection`/`ResearchAgenda`), execution state (`Study`/`Campaign`),
engineering state (`ImplementationTrack`/Development Session), software
publication (`ProjectRelease`), and result publication (`ResearchRelease`) so
none becomes a second mutable copy of another.

The aggregate hierarchy is intentionally small:

```text
ResearchDirection
  -> ResearchAgenda (DAG)
     -> ResearchTask
        -> formulation and ResearchCompilation revisions
        -> ImplementationTrack(s)
           -> Project + DevelopmentSession(s) + candidate ProjectRelease(s)
        -> Study/Campaign
           -> TrialGroup -> Trial -> Run -> ExecutionAttempt
        -> EvidenceBundle -> ClaimDecision -> ClaimSet
```

This is not a single storage tree. Full event history is a timeline, task and
artifact derivation is a lineage graph, and repeated executions are tables.
The hierarchy supplies stable addressing and ownership for navigation and
policy.

### Branch taxonomy

The type of change determines where a branch belongs:

| Change | New object/revision |
| --- | --- |
| New scientific question or result-derived follow-up | `ResearchTask` with dependency/derivation edge |
| Alternative scientific framing | ResearchPrototype/ResearchCompilation branch |
| Protocol or analysis change | new immutable Protocol/AnalysisPlan revision |
| Alternative engineering architecture | `ImplementationTrack` |
| Independent code implementation | candidate/ProjectRelease within a track or sibling track |
| Seed, fold, dataset slice, or declared configuration | `Trial`/`Run` |
| Preemption, retry, or provider resubmission | `ExecutionAttempt` |

Cloning a skill is not the default way to branch scientific meaning. The first
implementation may constrain one accepted task to one active track and one
project-only target skill, but that is a UI/policy limit, not the universal ABI.

## Workflow

The end-to-end pipeline crosses several authorities without collapsing them:

```text
Direction intake and source admission
  -> Problem landscape
  -> ResearchAgenda DAG and bounded ResearchTask selection
  -> task-scoped staged formulation
  -> accepted ResearchCompilation
  -> implementation planning and capability-gap check
  -> Project-scoped Builder Development Session
  -> candidate ProjectRelease and project-wide conformance
  -> Study/Campaign activation
  -> Trials/Runs/ExecutionAttempts
  -> EvidenceBundle and independent analysis/review
  -> ClaimDecision/ClaimSet
  -> typed follow-up task, implementation revision, or ResearchRelease
```

The Research Orchestrator owns direction/task transitions; Builder owns the
development segment; Research Manager owns Study/Campaign execution and
evidence gates. Their activity is joined by stable refs and normalized into one
durable journal. A UI transition or agent message cannot impersonate a domain
transition.

Within one accepted Study, the research manager expresses its lifecycle with
the existing governed workflow model:

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

Research starts in the shared Research Workbench, not in a generic Builder
modal. The Workbench home is a portfolio of direction cards. Cards expose an
aggregate status, user-facing domain tags, last activity, and blocker/next step;
search and filtering operate on displayed metadata. `recent` and `pinned` are
per-user preferences, not scientific tags or portable direction truth.

Creating a direction asks for minimal identity metadata and calls the Research
Orchestrator to create the canonical ResearchDirection plus an admitted local
artifact custodian. The creation modal clears only after success; selection is
an explicit second action based on the returned canonical id. The current
compatibility implementation may atomically ask Builder SDK for a one-skill
draft Project at the same time. That Project is recorded as the first
implementation ref; it is not the direction identity.

Portfolio and direction workspace are mutually exclusive full-width Workbench
layouts expressed as `layout.variants`, not as coincident widgets in duplicate
`main` areas. In a selected direction, the header shows the direction title;
activating it opens the Direction selector modal (the same searchable/filterable
portfolio) without permanently reserving screen width for the portfolio.

The direction workspace dedicates its left region to a curated navigation
outline and its remaining space to the selected node's full-page view. Stable
top-level nodes are Sources, Research Agenda/Tasks, Formulation,
Implementations, Studies, Evidence, Review/Decisions, Releases, and Activity.
Nodes appear only when admitted by data/policy. The outline does not copy
Builder or LLM stage state and does not become a workflow engine. History is a
timeline, derivation is a graph, and runs are tables shown in the full-page
view.

The layout is a general core ABI, not Workbench-specific rendering. A reusable
outline/navigation source supplies typed node ids, parent ids, labels, icons,
badges/errors, lazy-child refs, and navigation targets. Core supplies persisted
selection scoped by webspace/scenario/page/entity, URL/deep-link and back/reload
semantics, keyboard/accessibility behavior, virtualization, and responsive
drawer collapse. Research skills supply the nodes and domain views.

`researchViewMode` chooses portfolio or direction workspace and
`selectedDirectionId` supplies identity; the direction variant requires both.
Local page state is isolated by webspace/scenario/page and healed for missing
defaults. A paired Builder selection may be offered as navigation context, but
it never overwrites Workbench focus. Identity-bearing sources clear or mark the
old value stale while another direction loads, so one direction's content
cannot appear under another title. Discussion remains durable human/LLM
provenance; Current consensus is the human-readable projection of the selected
task's structured formulation, not a second source of truth.

```text
Research Workbench: create direction
  -> Research Orchestrator creates ResearchDirection + artifact custody
  -> optional compatibility draft Project is linked, not aliased
  -> clear/close creation modal and refresh portfolio
  -> user selects the canonical direction identity
  -> add ordinary files under artifacts/part0
  -> validate manifest and form immutable SourceBundle selection
  -> deterministic notebook/document inventory
  -> derive Problem Landscape and ResearchAgenda
  -> select/create one bounded ResearchTask
  -> durable human/LLM task-formulation dialogue
  -> schema-valid task-scoped ResearchPrototype revisions
  -> accept exact ResearchCompilation digest
  -> create/select ImplementationTrack and implementation Project
  -> freeze a private local code checkpoint + exact artifact manifests,
     prototype, and target policy (no Forge upload)
  -> emit immutable AutomationBrief and Builder Development Session
     (Codex has not started)
  -> user opens the linked Project in a named Builder window
  -> isolated Codex implementation
  -> software validation and CPU Trial
  -> ProjectRelease and Workspace activation
  -> link the candidate ProjectRelease to the ImplementationTrack
  -> instantiate Study and ExperimentCampaign from the accepted task
```

The first user-facing implementation is local-first. Source payloads are
ordinary files under the owned direction skill's `artifacts/partN` groups, and
each group has a deterministic manifest. The Skill SDK lists, validates,
resolves, and extracts bounded text from those files. Notebook JSON is parsed
before bounding into semantic units: Markdown, imports, definitions, literal
configuration, query-relevant code windows, near-duplicate revisions, and
bounded historical-output summaries. Raw output payloads are not forwarded;
summaries are explicitly exploratory/untrusted and cannot become confirmatory
evidence. Selected units are rendered back in notebook order. Source cells
receive stable `#cell=N` refs and text fragments receive stable line refs.
Every extraction returns
a coverage envelope with represented/truncated/unreadable sources and selected
and omitted unit ids, character/unit counts, query digest, and notebook
inventory. PDF extraction will implement the same prepared-source/provenance
contract with page/section and OCR-quality metadata rather than a PDF-specific
LLM path. Codex receives native
read-only paths; the conversational LLM receives bounded extraction through
the orchestrator. Logical `ArtifactRef` addressing is provider-neutral so an
external store or MCP adapter can be added later without being an admission
dependency now.

This path separates logical from physical ownership. The logical owner key is
the ResearchDirection (and, when narrowed, the ResearchTask); the activated
direction/data-owner skill is the current storage custodian and enforces its
isolated SDK binding. Artifacts are classified as `owned`, `inherited`,
`admitted_read_only`, `generated`, or `evidence`. A future domain-aggregate
storage scope or ContentRef provider may move bytes without changing those refs.
Cross-skill access is granted through a typed view/API or a specialized owner
skill, never by handing another skill the custodian's database/path binding.

Accepting a ResearchPrototype binds exact group/item digests. A changed file or
newly selected artifact makes the unaccepted candidate stale. Directory/archive
expansion, malware/secret scans, external artifact providers, complete license
policy, and sensitivity editing remain later intake gates and must not be
implied by the first slice. Publication policy decides whether each source
payload is included, excluded, or represented only by its manifest/reference.

The source assessment may inventory notebook cells, imports, bounded output
summaries,
environment hints, duplicate implementations, likely data leakage, and code
that could be extracted. It may not decide that an exploratory output is true
or silently define the research direction. The formulation LLM and human
develop the question, falsifiable hypotheses, experiment stages, evidence
classes, analysis rules, budgets, stops, implementation requirements, and
acceptance checks. LLM `ready_for_automation` is a proposal only: schema and
semantic admission are repeated at acceptance.

Formulation is staged because the complete ResearchPrototype is too rich for a
single reliable inference. `problem_frame` extracts one falsifiable scientific
question and separates observations, interpretations, hypotheses, and gaps.
`protocol_design` resolves nine required decision areas and specifies data,
pairing, budgets, estimation, and inference. `implementation_contract`
translates the accepted semantics into independently observable obligations.
Each stage has its own strict schema, local semantic gate, durable payload and
telemetry, and one bounded stage-local repair. Provider schemas are a projected
portable subset; the complete local JSON Schema remains authoritative.

Problem-stage questions are discovery records, not automatically blockers. A
protocol decision is explicitly `source_derived`, `policy_default`, `proposed`,
or `unresolved`; only the last may carry a blocking question. This prevents a
later concrete proposal from coexisting with a stale question that incorrectly
blocks Codex. AdaOS, not the model, compiles exact refs and ids, readiness,
human-facing lifecycle text, the checkpoint-selection rule, and interval
decision inequalities from typed effect direction and practical threshold.

`context_coverage` and `admission_review` are orchestrator-managed
ResearchPrototype fields. The LLM cannot emit or override them. Source-grounded
claims may cite only fragment refs present in the disclosed extraction
allowlist. The deterministic review separately records quality gates and the
final admission gates. It requires source coverage and grounding, smoke versus
confirmatory separation, comparator isolation, named RNG streams, paired
invariants/varied fields, data sealing/leakage controls, explicit development
selection versus one-shot sealed final-test access, one operationalized
primary estimand/outcome, uncertainty, multiplicity, practical significance,
predeclared stopping, negative-result retention, and independently verifiable
implementation/acceptance records. Per-epoch final-test observation is a hard
semantic failure. A schema-valid candidate may still be a
draft. Bounded repair receives the exact gate findings; exhausted repair stores
the draft with blockers instead of either discarding useful work or pretending
it is ready.

### Research compilation boundary

The transition from a scientific problem to a coding task is a first-class
research stage, not prompt preparation. Its logical products are:

```text
SourceAnalysis
  -> ResearchProblem
  -> ExperimentalProtocol
  -> accepted ResearchCompilation for ResearchTask
  -> AutomationBrief plus ImplementationTrack/Project bindings
```

SourceAnalysis, ResearchProblem, and ExperimentalProtocol may remain typed
facets/revisions inside ResearchPrototype until cross-domain evidence justifies
more core entities. ResearchTask, accepted ResearchCompilation, and
ImplementationTrack cross actor/time/authority boundaries and are durable
research-domain records now. The important contract is that every material
source or human decision can be traced through a task, scientific requirement,
protocol element, engineering obligation, runtime observation/artifact, and
later evidence or claim decision. Missing links remain visible findings.

The historical TLP `initial-review` performed much of this scientific critique
and operationalization before AdaOS received the task. It therefore shortened
the first proof. In an ordinary project such a review is valid user-supplied
source material. In the clean TLP research-compilation evaluation it is marked
`formulation`-hidden and `implementation`-hidden, retained only as an evaluator
oracle; the legacy TLP scenario, skills, and E002 receipts are also excluded
from agent context. An assisted arm may expose the review, but its result must
not be confused with the from-raw arm.

Artifact stage visibility is enforced when context is materialized. A hidden
review or rubric cannot become a hidden acceptance requirement: if its material
point was not compiled into the visible accepted contract, the omission is a
formulation defect rather than a Codex defect. Feasibility feedback travels in
the reverse direction as a typed clarification, constraint, capability gap, or
protocol conflict and creates a reviewed revision instead of silently changing
accepted science.

Typing is deliberately uneven across the path. Source discussion and internal
implementation stay expressive; accepted scientific decisions, authority,
Run/Observation/Evidence records, and revisions are strict. The controlled
`C0_raw` through `C4_over_specified` ablation and its primary
evidence-valid-completion endpoint are specified in
[Research Compilation and Autonomous-Science Evaluation Program](research-compilation-and-sota-program.md).

The accepted `AutomationBrief` contains the exact direction, ResearchTask,
ResearchCompilation, ImplementationTrack, Project and primary target,
SourceBundle/group/item digests and native paths, exact ResearchPrototype
digest and content, implementation requirements, acceptance tests, and
prohibited actions. It creates a Builder Development Session in which the
direction skill is the read/write primary target, artifacts are read-only, and
Research Workbench/orchestrator dependencies are exposed only as declared
contracts/docs. Acceptance is optimistic and idempotent, rejects stale input,
and explicitly records `codex_started=false`. Codex later implements the
experimental base from this handoff; it cannot amend the scientific objective
or silently mutate a dependency outside the admitted development scope.

Development is selected by ImplementationTrack. Builder always opens the whole
Project compatibility context while the session grants write access only to
declared targets. Non-target members and shared Workbench/manager contracts are
provided as digests, public schemas, docs, or on-demand read-only source. The
model does not receive the entire workspace merely because compatibility is
project-scoped. A successful run yields a candidate ProjectRelease plus
project-wide conformance evidence; it does not mutate the accepted task.

Opening Builder durably binds the Development Session, opens a reusable named
browser window, and emits `builder.context.selected` through the API host in
the same UI action chain. The host process projects the canonical Project
selection into the live Builder Yjs document; an isolated skill process is not
treated as the UI-state authority. The Builder header and project picker must
therefore be projections of that same selection event. WebUI
consumers defensively normalize legacy singleton `actions`, `buttons`,
`widgets`, and `layout.areas`, while newly published schemas remain valid
`adaos.webui.v1` arrays. This compatibility boundary prevents one malformed
one-element projection from hiding project identity or producing an empty
picker.

A published research scenario continues to evolve scientific state without
Builder when the existing contracts can express the change. New parameters,
experiments, campaign branches, and protocol amendments use Research Fabric.
A missing metric, runner, schema, visualization, data adapter, or execution
capability creates a typed `CapabilityGap` and a linked Builder Change against
the exact installed ProjectRelease. After Trial/Publication, the autonomous or
human-operated session may explicitly adopt the new package digest; historical
runs retain their original software identities.

The former technical precursor exposed `research_direction` as a dynamic
Builder skill template and rendered a Research view inside Builder. It remains
useful contract evidence but is not the target product boundary. The target
uses the general composite Project definition and presentation contract from
[Project Composition, Presentation, and Development Context](project-composition-and-development-context.md).
Research Workbench owns portfolio/intake/formulation; Builder owns development.
A direction-specific scenario is justified only when post-publication
interaction cannot be represented by the shared Workbench and declared skill
tools; it is never generated merely to name a study.

### Why the structured handoff is not just a longer prompt

For a small one-off task, giving Codex a carefully written Markdown file can be
faster and entirely adequate. Research Fabric adds value when work must survive
multiple agents, implementation iterations, executions, reviews, or restarts:

| Property | Markdown plus direct Codex | Accepted ResearchPrototype and AutomationBrief |
| --- | --- | --- |
| Input identity | The visible file may change; attachment selection can be implicit | Exact SourceBundle and every source payload are fixed by digest |
| Scientific meaning | Question, hypothesis, smoke test, confirmation, and claims are prose conventions | Falsification, evidence class, inference permission, estimands, stops, and negative-result policy are typed fields |
| Decision coherence | Direction, threshold, and interval interpretation can contradict each other across paragraphs | Effect direction and threshold are typed; AdaOS compiles mutually exclusive supported/contradicted/inconclusive regions |
| Leakage control | “Untouched test” is an instruction Codex may accidentally violate | Development selection and final-test access are typed; per-epoch test observation fails admission |
| Implementation scope | Codex infers missing requirements and may optimize for the apparent desired result | Required modules, provider boundaries, forbidden actions, and executable acceptance checks are explicit |
| Consent | “Looks good” is conversational context | A human accepts one exact revision and observed generation |
| Stale-input safety | A later attachment/edit may be overlooked | Acceptance fails if either the source bundle or prototype changed |
| Re-entry | A new session must reconstruct intent from prose and history | A capability gap or new Codex run can cite the same immutable handoff and checkpoint |
| Audit and comparison | Success is mainly the resulting patch | Source-to-prototype coverage, corrections, implementation fidelity, tests, and outcomes can be measured across cases |

This structure does not make a scientific judgment correct. A bad hypothesis
can be perfectly schema-valid, and an LLM can still omit or fabricate important
details. The TLP proof exposed exactly this distinction: early LLM candidates
declared themselves ready but failed strengthened admission and were not
accepted. The advantage is therefore not “more detailed prose”; it is an
enforceable, reviewable boundary between source interpretation, human decision,
software authority, and later evidence. Its schema and review overhead is
worth paying for repeatable or autonomous research, not automatically for every
small coding task.

### Publication, export, and federation

Workbench may present three adjacent actions, but each delegates to a different
owner and produces a different immutable object:

1. **Publish implementation** invokes the ordinary Artifact Pipeline and
   produces a `ProjectRelease`. It contains software composition and
   compatibility locks, not private direction state.
2. **Export direction** invokes the Research Orchestrator and produces a
   `ResearchDirectionSnapshot`: metadata, source manifests and inclusion
   policy, agenda/tasks, accepted formulations/compilations, decisions,
   provenance, and referenced ProjectReleases. Import creates a new local
   direction id with `derived_from`; it does not silently join two live owners.
3. **Publish research result** invokes Research Fabric release policy and
   produces a `ResearchRelease`: Study/Campaign, EvidenceBundles, ClaimSet,
   methods, exact ProjectRelease, data/environment ids, attribution, and
   publication policy.

This three-way separation enables reproducibility without leaking local data or
turning the software registry into a research database. The Workbench does not
implement Git, package upload, registry mutation, or scientific release storage
itself; it requests capabilities and projects their operation/activity state.

True federation of one mutable direction across Assistants is deferred. It
requires distributed authority and ACLs, compare-and-swap revisions/conflict
handling, participant attribution, activity convergence, and explicit ownership
of artifacts and sealed data. Snapshot export/import is portable derivation,
not collaborative multi-writer federation.

### TLP pre-Codex contract precursor evidence

On 2026-08-10 the reference machine created `tlp_direction_skill` from the
normal `research_direction` Builder template and attached the original notebook
and review as two immutable source objects. This proves the formulation and
handoff contracts, not the revised Research Workbench operator milestone. The
notebook inventory contains 63
cells (47 code, 16 Markdown) and 5,106 output records; those outputs are marked
untrusted. The accepted bundle is
`sha256:e3dc926450ec58291a85242a5925c1d7743041c2c3ac6f448f8e8f85f60e43e7`.

The development formulation ledger retained rejected LLM candidates as
history. After contract strengthening, an earlier candidate that claimed
`ready_for_automation` was rejected because it lacked the required experiment
stage and acceptance semantics. The same human-reviewed content was then
replayed through the published `0.0.1` orchestrator's public tools, without
copying its private DEV database, and accepted as revision 1 with digest
`sha256:18cbbbe33b1755328762f0ddd73e11650281e702809326c8c84d81b1d25d0578`.
It separates a three-epoch, one-seed CPU `workflow_smoke` with inference
disabled from a future locked paired confirmatory stage.

Acceptance fixed Builder package checkpoint
`sha256:2f379c1ef49a5569ec9b5dd739faa208d2f37411e48654a8c78583837d2a813c`
and emitted AutomationBrief
`sha256:1b1a149c841a6598b9d58ccad1f0fe800117b1ffc4252e49f7348efb28b48c29`.
Repeated acceptance returned generation 3 and the same digests; it neither
created a scientific run nor started Codex. This closes only the ARF7.0
contract precursor, not the revised Research Workbench ARF7.1 milestone,
implementation, ARF6 scientific proof, or autonomy.

### TLP Research Workbench pre-Codex evidence

On 2026-08-11 the revised path created the local
`project:tlp_research_direction` composition and its sole primary
`skill:tlp_research_direction` through the same orchestrator tools exposed by
the shared Workbench. The original notebook (3,442,233 bytes) and review
(35,660 bytes) are ordinary files under the direction-owned
`artifacts/part0/`; the group manifest is
`sha256:715091ac119c29a3b9d823b9c68c610bf7bc0b4b86201e3da97fdd4bd07fa73e`.
The accepted SourceBundle is
`sha256:637e32518f33ba75dd231acd794ff126bb39edda1f3034cf9410f8d17b6b83cf`.

To keep this private intake out of Forge, acceptance made a local code-only
checkpoint with source tree
`sha256:e5980181dbe9ac956274c325271c4bd11282ce9b008888a17fcbf0a585439158`
and package identity
`sha256:e8f6d91c2dfc11fda26e1b0cb685622cd49c845d8a77ec7cd61c1cad53c64258`;
`bytes_uploaded=0`. Artifact identity is not weakened by the exclusion from the
code tree: the AutomationBrief and session independently bind the exact group
manifest and native read-only root.

The hand-authored, schema-admitted ResearchPrototype replayed through the
published runtime is
`sha256:ad3a9f531edfe13143efc9626c959732dcda56b690f0a305778c621f9bbd7778`;
the resulting AutomationBrief is
`sha256:139cb04deda7569777c9ebac603fc4838fdeec6bacbd8cb1e6344452594c513b`.
Development Session `dev_tlp_research_direction_139cb04deda75697` admits only
the direction skill for writes, exposes Workbench/orchestrator contracts and
`artifact://skill/tlp_research_direction/part0` read-only, and records
`codex_started=false`. A real scope review admitted the direction handler and
rejected the notebook plus an AdaOS root file with distinct read-only/outside
scope reasons.

The Builder binding rematerialized `desktop-dev` to `research_workbench` rather
than retaining the previous unrelated scenario. Open Preview, QR, and Return
to Research use the same Navigation SDK destination including zone, subnet,
development webspace, and `expected_scenario_id`. This closes the pre-Codex
Project milestone, not Codex realization, an experiment, a scientific claim,
or autonomous research. Full receipts and reproduction commands are in
[Research Project pre-Codex walkthrough](research-project-pre-codex-walkthrough.md).

The strengthened 2026-08-12 acceptance uses the newer
`project:tlp_research_03` fixture rather than silently grandfathering the
earlier hand-authored contract. Root-LLM drafts remained durable and visibly
failed deterministic checks until a human-reviewed revision 5 separated
observations from hypotheses, enumerated paired allocation, declared the data
seal/RNG/stopping/uncertainty policy, and covered execution, data,
reproducibility, observability, evidence, recovery, and analysis. The admitted
prototype is
`sha256:50d83bb5896697ca112925a6931436a858eb9afc4690737a2d0f4e3d7000c47c`;
its AutomationBrief is
`sha256:8679e07b69980cb95b614b425dbb13baf5c409b3648f61fa11544bc89aacc2d7`.
Development Session `dev_tlp_research_03_8679e07b69980cb9` exposes only
`skill:tlp_research_03` read-write, keeps both direction-owned artifacts
read-only, and prohibits scientific execution during code generation.

This consensus is sufficient to start bounded autonomous implementation of
the experimental base: Codex need not infer the intervention, paired units,
smoke/confirmatory distinction, primary estimand, evidence boundary, or
acceptance tests. It is not sufficient to claim a TLP result, select a result
after seeing test data, or start the confirmatory series without the later
workflow decision. The distinction is the practical value of the structured
handoff over simply attaching the original review Markdown.

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

Research source context and activated runtime data are distinct. A direction
skill draft may contain manifested model-facing inputs:

```text
<direction-skill>/artifacts/part0/
  manifest.yaml
  notebook.ipynb
  review.md
```

These files are development/reproducibility inputs governed by Project
publication policy. They are not copied into the mutable runtime data tree and
are not the experiment result store. The activated target respects current
AdaOS ownership:

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

For the current compatibility path, the skill id is both physical custodian
and storage isolation key. The target SDK admits a domain-qualified logical
owner such as `{owner_kind: research_direction, owner_id: ...}` while retaining
an opaque custodian binding. Skills receive only their authorized logical view,
never another component's physical path or DSN. This general owner-scope
extension belongs in core storage/blob capabilities; Research Fabric supplies
the owner kind and access policy.

Changing ownership or custodian is a versioned migration, not a path rewrite.
The old data is deleted only when the migration declares cleanup, the copy and
digests have been verified, rollback/retention policy permits deletion, and the
owning skill's migration receipt is durable.

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
skills and optional Workbench scenario through existing commands, execute
paired trials on the current or selected member node, inspect the same study
through the native UI and optional MLflow view, restart and reconcile the run,
and export a portable evidence bundle that independently recomputes the
declared primary comparison. Ray portability is a later independent sub-gate.

The notebook remains exploratory provenance. Its current outputs do not pass
this gate and are not imported as confirmatory trials.

### TLP compiler-calibration projection

The C0-C4 compiler calibration is represented inside one direction, not as a
portfolio of synthetic directions or independently published skills:

```text
ResearchDirection: TLP Research Compiler Calibration
  -> ResearchTask: does structured formulation improve autonomous realization?
     -> Study: matched C0-C4 handoff conditions
        -> condition packet -> DevelopmentSession -> candidate implementation
        -> independent evaluation result
```

Condition workspaces and generated skills are task-internal candidate artifacts
or project-only components. Workbench shows their journals, artifacts,
compilations, Builder sessions, costs, failures, and evaluator results below the
task's Development/Evaluation nodes. They do not appear as top-level research
directions or ordinary Desktop/Catalog applications.

The 2026-08-18 local checkpoint realizes this projection for the records that
survived the frozen v5 calibration: one canonical Direction and Task, the
notebook as an owned/admitted source, one accepted ResearchCompilation, five
immutable failed ImplementationTracks, and one evaluator-owned Study ref. The
generic outline/full-page layout and exact task/track data routes are reusable
core ABI; TLP node construction remains Orchestrator-owned. Historical runs did
not retain DevelopmentSession or candidate ProjectRelease identities, so those
links are explicitly absent rather than reconstructed from names. Publication
and portable Direction/Research releases remain later capability effects.

### Autonomous TLP acceptance

TLP is also the first transparent autonomous-research harness. After the local
scientific base is valid, a human should be able to create one Builder
`research_direction` skill, supply the notebook, review, and research objective,
discuss and accept one Research Prototype and Research Mandate, then leave an
A4-bounded session to:

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

TLP validates and calibrates the transparent mechanism, but evaluating TLP is
not the framework objective and it cannot by itself establish competitive
autonomous-research performance or justify every core abstraction. Before the
full replication family, TLP supplies a controlled research-compilation
ablation that compares raw, reviewed-prose, staged, typed-execution, and
over-specified handoffs under matched budgets. The next multi-task validation
family combines this `ResearchCompilerBench` track with a PaperBench-like
replication benchmark whose tasks are imported as immutable benchmark releases
and executed through the same Research Fabric and Builder paths as TLP.

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

- auditable question derivation through definitions, assumptions, mechanism,
  falsifier, minimal decisive test, expected observations, and a failure-update
  rule proposed by [FirstResearch](https://arxiv.org/abs/2607.05682). The paper
  was withdrawn on 28 July 2026 for further improvement/consolidation, so its
  certificate is retained only as non-authoritative design prior art and its
  LLM-judge results are not SOTA evidence;
- project-to-task planning, bounded task contracts, explicit contribution
  ownership, and dependency-aware lineage from the August 2026
  [Project2Task preprint](https://arxiv.org/abs/2608.05225). Its reported
  portfolio/downstream gains directly motivate ResearchAgenda/ResearchTask,
  but the small recent preprint is emerging evidence, not an established
  standard or proof of AdaOS's design;
- pragmatic facet-based formalization and controlled task construction from
  [DiscoveryBench](https://arxiv.org/abs/2407.01725), supporting a small
  semantic waist rather than an attempt to encode all science;

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
  [W3C PROV-O](https://www.w3.org/TR/prov-o/),
  [OpenLineage](https://openlineage.io/docs/), and
  [OpenTelemetry](https://opentelemetry.io/docs/);
- portable result-export compatibility informed by
  [RO-Crate](https://www.researchobject.org/ro-crate/specification/1.3/index.html),
  including its Entity/Action/workflow/provenance conventions, without making
  JSON-LD the internal governance model;
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
- structured extraction of research questions, procedures, and executable
  acceptance from [EXP-Bench](https://arxiv.org/abs/2505.24785), whose reported
  gap between partial design/implementation scores and complete executable
  experiments motivates an explicit research-compilation stage;
- expert-validated, stagewise scientific coding assessment from
  [ScienceAgentBench](https://proceedings.iclr.cc/paper_files/paper/2025/hash/f12b4df26344f3be803c06b555252efe-Abstract-Conference.html),
  reinforcing that component and end-to-end failures must be attributed
  separately;
- open research-agent environments and full-lifecycle task families from
  [MLGym](https://arxiv.org/abs/2502.14499) and
  [AIRS-Bench](https://arxiv.org/abs/2602.06855), candidates for later matched
  evaluation rather than AdaOS-specific task definitions;
- long-horizon structured memory and claim traceability from
  [Kosmos](https://arxiv.org/abs/2511.02824), while keeping an agent's world
  model distinct from accepted governance and evidence state;
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

Mature research infrastructure also constrains what AdaOS should not reinvent.
[OpenML](https://docs.openml.org/concepts/) supplies useful ML-specific
Dataset/Task/Flow/Run semantics, while
[Workflow Run RO-Crate](https://arxiv.org/abs/2312.07852) provides portable
prospective/retrospective workflow-run provenance profiles aligned with W3C
PROV. AdaOS may keep a governance-oriented internal model, but its export and
interoperability mappings should reuse these concepts where their semantics
match.

The architectural inference is deliberate: current systems demonstrate that
end-to-end autonomy is possible, but benchmark results still show a large
capability-reliability gap. AdaOS therefore designs for A4 autonomy now while
requiring typed targets, durable state, bounded authority, independent
validators, fresh confirmation, and comparable evaluation before making an
autonomous-science product claim. The proposed SOTA target is correspondingly
narrow and falsifiable: improve evidence-valid artifact-to-experiment
completion at fixed models and budgets. It is not a claim that AdaOS, TLP, or
one successful Codex trace already advances scientific SOTA.

The resulting SOTA-aligned stack is deliberately compositional rather than a
copy of one agent system:

| Layer | External evidence | AdaOS responsibility |
| --- | --- | --- |
| Direction and bounded task portfolio | Co-Scientist; emerging Project2Task | ResearchDirection, ResearchAgenda, task contracts, human acceptance |
| Empirical implementation/search | ERA; AI Scientist | Project-scoped Builder, explicit targets, candidate ProjectRelease |
| Execution and tracking | MLflow and provider APIs | provider-neutral Run/Attempt/Observation identities and reconciliation |
| Durable memory and provenance | Kosmos; W3C PROV-O | structured domain state, normalized activity, traceability and evidence gates |
| Reproducible exchange | RO-Crate/Workflow Run RO-Crate | ProjectRelease, DirectionSnapshot, ResearchRelease export mappings |
| Evaluation | PaperBench; AIRS-Bench | frozen tasks, hierarchical criteria, matched budgets, independent validators |

AdaOS's candidate advantage is the continuity of contracts and evidence across
these layers while agents, models, executors, and trackers remain replaceable.
That advantage must be measured, not inferred from architectural richness.

## Related AdaOS Documents

- [Builder](builder.md)
- [Builder Conversational Development Architecture](builder-conversational-development.md)
- [Research Compilation and Autonomous-Science Evaluation Program](research-compilation-and-sota-program.md)
- [Scenario Guidance and Help Contract](scenario-guidance.md)
- [Governed Data-Driven Workflow Model and Interaction Architecture](governed-workflow-runtime.md)
- [Artifact Source, Package, and Activation Architecture](artifact-source-package-activation.md)
- [Project Composition, Presentation, and Development Context](project-composition-and-development-context.md)
- [Model Runtime and Registry](model-runtime-and-registry.md)
- [Skill Runtime Lifecycle](../skill_runtime.md)
- [Operational Event Model](operational-event-model.md)
- [Web UI Architecture](web-ui-architecture.md)
- [Security](security.md)
