# AdaOS Research Fabric

Status: target architecture. The ARF0.5 generic storage, binding, content-ref,
and local-execution foundation is implemented; the research framework itself
is not yet implemented.

Last reviewed: 2026-08-07.

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
   the local default. PostgreSQL is a future shared service/provider with
   isolated logical databases or schemas and roles, not one database server
   installed by every integration.
8. TLP is the first end-to-end conformance case. Its domain types and operator
   semantics remain outside core until at least one unrelated research case
   proves that an abstraction is general.
9. A relational binding is private to its owning skill. Cross-skill data is
   published by a specialized owner skill as typed APIs, projections, events,
   or governed logical views; consumers do not receive its SQL binding.

## Why `Research Fabric`

The term separates three concerns that should not share one name:

| Term | Intended use |
| --- | --- |
| AdaOS Research Fabric | Architecture and reusable runtime capabilities |
| Research workbench | Human-facing native UI inside an AdaOS webspace |
| aResearcher | Optional future assistant/persona that proposes and operates governed work |

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
- make a language model an autonomous approval or publication authority;
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
TLP study scenario
        |
        v
research-manager skill ---- native Research Workbench
        |
        +---- governed workflow / approval / evidence index
        +---- relational-storage capability binding
        +---- experiment-tracker port ---- local tracker
        |                              `-- MLflow service skill
        +---- executor port ------------ local process runner
                                       `-- Ray service skill / cluster

AdaOS core supplies lifecycle, policy, identity, secrets, service discovery,
generic storage/execution seams, artifacts, events, and projections. It does
not supply TLP semantics.
```

The names `research-manager`, `mlflow-tracker`, and `ray-executor` describe
roles, not fixed final package names.

## Responsibility Boundaries

| Owner | Owns | Must not own |
| --- | --- | --- |
| AdaOS core | Skill/scenario lifecycle, workflow rails, identity, policy, secrets, service supervision, capability binding, generic run/attempt records, artifact refs, event envelopes | TLP protocols, statistical conclusions, MLflow schema, Ray scheduler state |
| Research manager skill | Study model, protocol locks, analysis plan, paired trial groups, test access, evidence manifests, claim review | Provider internals, global DB credentials, accelerator scheduling |
| Study scenario | Domain workflow and templates, inputs, required capabilities, study-specific views and actions | New installation semantics or private infrastructure |
| Tracker provider | Parameter, metric, tag, and run-artifact ingestion and query | Protocol authority, approvals, claim truth |
| Executor provider | Submission, scheduling, logs, status, cancellation, resource placement | Study state, statistical plan, tracker identity |
| Model registry | Promoted, versioned model artifacts and serving readiness | Every intermediate training checkpoint |

## Research Domain Model

The first framework skill should own versioned schemas for these concepts:

`Study`
: Stable aggregate for a research question, owners, policy, budget, and
  lifecycle.

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

The model deliberately distinguishes scientific identity (`Trial`, `Run`) from
infrastructure identity (`ExecutionAttempt`). This prevents a preemption or
worker loss from inflating the sample count.

## Workflow

The research manager should express its lifecycle with the existing governed
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

## Core Capability Foundation and Gaps

The framework should not be implemented by expanding the current raw `SQL`
protocol or by putting every research entity in core. The following narrow
capabilities are the useful core seams. The implemented ARF0.5 subset and its
remaining limitations are recorded in
[Research Fabric Core Readiness](research-fabric-core-readiness.md).

### Relational storage provisioning

A component requests requirements and receives a scoped binding. A conceptual
request contains:

```yaml
schema: adaos.storage.relational.requirement.v1
capability: storage.relational
owner_ref: skill:research-manager
logical_name: experiments
requirements:
  durability: durable
  transactions_required: true
  concurrent_writers: 1
  json_required: true
  locality: node
  backup_required: false
  migration_owner: skill:research-manager
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

The ARF0.5 SDK implementation derives `owner_ref` from the active skill
context after checking the existing `storage.relational` capability, returns a
redacted binding, and rechecks that owner for every transaction. A skill cannot
request another skill's binding. When multiple
skills need the same governed dataset, a specialized provider skill owns its
database and publishes stable logical views through typed service APIs or
projections. Direct cross-owner SQL remains out of scope.

### Execution provider

A provider-neutral boundary needs immutable `ExecutionSpec`, durable `Run` and
`ExecutionAttempt` identities, `ResourceRequest`, submission idempotency,
lease/heartbeat, log and artifact streams, cancellation, checkpoint references,
and reconciliation after an unknown outcome.

This contract should align with existing governed-workflow activity semantics
and the `ModelJob` direction in
[Model Runtime and Registry](model-runtime-and-registry.md). It must not create
a second workflow engine or a second model registry.

### Tracker provider

The tracker port supports typed operations for experiment/run registration,
parameters, metrics, tags, artifact references, finalization, export, and
provider links. It is initially owned by the research framework package. It
becomes a core candidate only if a second non-research domain needs the same
contract.

### Generic service UI surface

A supervised service skill may advertise an optional UI endpoint with routing,
health, authorization, and embedding policy. This is a generic service
capability, not an MLflow-specific iframe feature.

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

An initial local MLflow service skill may therefore use:

```text
.../.runtime/mlflow-tracker/vX.Y/data/db/mlflow.db
.../.runtime/mlflow-tracker/vX.Y/data/files/artifacts/
```

This is a component-owned SQLite database file, not a separately installed
database server. When PostgreSQL is available, a binding may instead point to
one shared PostgreSQL cluster. Isolation remains logical and explicit:

- separate database or schema per migration owner;
- separate roles and least-privilege credentials;
- no cross-owner table access;
- coordinated backup and service lifecycle;
- independent schema migrations and restore tests.

One shared server does not mean one shared schema. Conversely, local SQLite
files do not violate the rule against installing a DBMS per integration.

Large immutable artifacts should later use a blob/object-storage capability.
The relational store holds metadata and content-addressed references, not
unbounded checkpoint bytes.

## MLflow Integration

[MLflow Tracking](https://mlflow.org/docs/latest/ml/tracking/) provides a useful
typed vocabulary and API for experiments, runs, parameters, metrics, tags, and
artifacts. AdaOS should integrate it as a service skill and tracker adapter.

### Semantic mapping

| AdaOS | MLflow projection |
| --- | --- |
| Study or study version | Experiment plus AdaOS identity tags |
| Trial group | Parent run or group identity tag |
| Trial / logical run | Child run |
| Immutable configuration | Parameters and configuration artifact |
| Observations | Metrics with split/unit/step conventions |
| Evidence artifacts | Logged artifacts plus content digest tags |
| Claim decision | AdaOS-only state; optional summary tag for discovery |

MLflow owns query-optimized experiment telemetry during execution. AdaOS owns
the protocol and decision record. At a quality-control or claim gate, the
research manager exports the required MLflow run data into an immutable
`EvidenceBundle`. That distinction is more precise than calling MLflow either
the AdaOS database or merely a disposable UI projection.

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

MLflow's backend store can start as the service skill's SQLite file and later
use a provisioned PostgreSQL binding. Its artifact store starts in the skill's
`data/files` and may later use an object-storage binding.

### UI

The primary UI is a native AdaOS Research Workbench generated from canonical
study and evidence state. It covers protocol review, trial matrix, progress,
comparisons, evidence, and approvals.

The full MLflow UI may be exposed as an advanced tool through the generic
service UI surface. An iframe is acceptable only behind an AdaOS-controlled
same-origin proxy or equivalently governed route with authentication,
authorization, origin/CSP policy, health, and lifecycle handling. A plain
iframe to an unauthenticated tracker port is not an integration design.

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
- `protocol_digest` and `analysis_plan_digest`;
- `trial_group_id`, `trial_id`, and logical `run_id`;
- `attempt_id` and provider job id;
- `trace_id` and workflow instance/generation;
- code/package, environment, dataset, split, and operator digests;
- tracker provider and run id;
- parent checkpoint or model artifact digest when applicable.

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

## Security and Trust

The current process sandbox is an operational limit, not a hostile-code
security boundary. Before third-party or agent-generated training code runs
unattended, the executor needs stronger isolation, read-only input mounts,
explicit writable outputs, network policy, resource quotas, secret scoping,
and artifact scanning.

Remote MLflow and Ray endpoints require authenticated service bindings, TLS as
appropriate, allowlisted origins/routes, and least-privilege credentials.
Research participants receive study actions, not raw database, tracker-admin,
or cluster-admin access.

## Generalization Gate

TLP may validate the framework, but it cannot by itself justify every core
abstraction. Before promoting research-manager contracts into core, a second
case should exercise different data and analysis shapes, for example a
non-neural simulation, retrieval evaluation, or device experiment.

Promotion requires evidence that the candidate contract:

1. is used unchanged by both cases;
2. has provider-neutral conformance tests;
3. has stable ownership and migration semantics;
4. cannot remain safely versioned in a skill or SDK package;
5. does not duplicate the governed workflow, model runtime, artifact, or event
   authorities.

## State-of-the-Art Alignment

The target follows current research-infrastructure practice without adopting a
monolithic platform:

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
- explicit protocols, held-out evaluation, evidence bundles, and human gates
  before adding autonomous experiment-tree generation.

## Related AdaOS Documents

- [Governed Data-Driven Workflow Model and Interaction Architecture](governed-workflow-runtime.md)
- [Artifact Source, Package, and Activation Architecture](artifact-source-package-activation.md)
- [Model Runtime and Registry](model-runtime-and-registry.md)
- [Skill Runtime Lifecycle](../skill_runtime.md)
- [Operational Event Model](operational-event-model.md)
- [Web UI Architecture](web-ui-architecture.md)
- [Security](security.md)
