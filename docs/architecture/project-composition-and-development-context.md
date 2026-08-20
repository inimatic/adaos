# AdaOS Project Composition, Presentation, and Development Context

Status: target architecture. The local contracts required by the first
Research Workbench pre-Codex milestone are specified here; a published Project
catalog and transactional multi-component install/remove flow are follow-on
work.

Last reviewed: 2026-08-18.

This page defines the boundary between distributable AdaOS Projects,
user-facing Applications, skill/scenario components, Builder development
sessions, presentation hosts, and model-facing artifact context. It is the
general architecture extracted from the Research Fabric TLP case; research is
the first consumer, not a special implementation path in AdaOS core.

The package, release, activation, and rollback mechanics remain owned by
[Artifact Source, Package, and Activation Architecture](artifact-source-package-activation.md).
The research-domain workflow remains owned by
[AdaOS Research Fabric](research-fabric.md).

## Decision Summary

1. A **Project** is a versioned declarative distribution definition. It names
   an arbitrary non-empty set of owned skill/scenario components, external
   dependencies, entry points, catalog metadata, and lifecycle policy.
2. A Project is not a chat session, editor tab, Codex prompt, mutable checkout,
   or runtime state store. Those concerns belong to a **Builder Development
   Session**.
3. A **ProjectRelease** is the immutable, dependency-locked release of one
   Project. A one-skill Project is valid; a Project need not contain a scenario.
4. An **Application** is a user-facing launchable projection. In the simple
   case it is backed by one scenario; in the general case a Project entry point
   binds a scenario presentation to one or more skills.
5. A skill may declare explicit `presentations`. The system resolves an
   explicit Project launch target first, then a skill's default presentation,
   then the generic system skill-preview host. It never reuses an unrelated
   previously materialized scenario.
6. Presentation declarations express routing compatibility, not proof. Exact
   scenario/skill versions that passed preview or Trial are recorded as
   separate verification evidence.
7. Registry `kind`, machine-readable profiles/capabilities, user-facing
   categories, free-form tags, and deployment scope are separate axes. The
   main Catalog should lead with Projects/Applications; raw skills and
   scenarios remain available in an advanced component view.
8. Model-facing source artifacts use a local-first contract. A Project-owned
   target skill may carry `artifacts/<group>/manifest.yaml` and ordinary files.
   The Skill SDK provides stable enumeration and resolution. External stores
   and MCP resources may later implement the same logical `ArtifactRef` without
   blocking native Codex filesystem access now.
9. Builder context is least-context and least-write. A Development Session
   explicitly separates selected UI focus, development targets, read-only
   context, artifact inputs, and scratch/runtime access.
10. A research direction is a live domain aggregate, not a Project identity.
    It may reference one or more exact ProjectReleases that implement its
    current research tasks. The current local compatibility path may create a
    one-component Project and use the same human-readable id, but APIs and
    persisted refs must not rely on `direction_id == project_id`.
11. A Development Session carries consumer-owned contract requirements in
    addition to read-only component context. Builder must prove that generated
    providers export the required operations and survive the ordinary package,
    install, and activation boundary; a target-owned mock is not compatibility
    evidence.
12. Builder always develops in Project scope, even when one skill is the only
    writable target. Full Project awareness means exact composition, contracts,
    locks, and compatibility constraints; it does not mean sending every source
    file to the model.
13. A ProjectRelease locks the Project definition as well as component package
    digests. Component roles, exposure, lifecycle, relations, entry points,
    profiles, and resolved Project dependencies are part of release identity.
14. Publishing implementation software, exporting a live domain aggregate,
    and publishing a domain result are separate operations. Project publication
    must not become an implicit export of private runtime or research state.
15. Scenarios do not own skills. A scenario may consume a capability or present
    a binding; Project composition is the authority for jointly shipped/removed
    components and exact dependency locks.
16. A managed/project-only component relation is a distribution and lifecycle
    relation, not inherited data authority. Runtime data, execution telemetry,
    orchestration state, and published artifacts have independently named
    owners. Cross-component access requires an explicit capability,
    `ArtifactRef`, or logical-view projection.

## Vocabulary

| Term | Meaning | Not this |
| --- | --- | --- |
| Component | Versioned skill or scenario package selected by a Project | User-facing solution identity |
| Project | Declarative distribution, entry-point, and lifecycle definition | Mutable Builder workspace or process |
| ProjectRelease | Immutable resolved set of component packages and locks | Registry channel or installed runtime state |
| ProjectInstallation | Local activation/reference to one exact ProjectRelease | Project source definition or live domain aggregate |
| Application | Launchable user-facing projection supplied by a Project entry point | Necessarily one package or one scenario identity |
| Presentation | Explicit binding from a skill/project entry point to a scenario host | Ownership, dependency, or validation evidence |
| Builder Development Session | Mutable, policy-scoped overlay for one development iteration | Distributable Project manifest |
| Development target | Component that the current session may change | Whatever is selected in the UI |
| Context member | Read-only or filtered dependency visible to an agent | Implicit write authority |
| Artifact group | Manifested local source material intended for human/LLM/Codex context | Mutable experiment/runtime data |
| Runtime data | Skill-owned operational state under its activated runtime bucket | Project source or intake material |
| Execution telemetry | Logs, traces, test diagnostics, and lifecycle receipts owned by the executing service/session | A child skill's primary data or an implicit parent database |
| Managed component | Project member whose discovery/install/remove lifecycle is governed by the Project | A component whose data or credentials are inherited by another member |
| Profile | Stable machine-readable semantic role such as `adaos.research.implementation.v1` | Localized catalog category |
| Category | User-facing discovery facet such as `research` or `media` | Capability or deployment contract |
| Domain aggregate | Live user-owned state such as a ResearchDirection | Installable Project or Builder session |

## Project Distribution Contract

The target source contract is additive to the existing component manifests:

```yaml
schema: adaos.project.v1
kind: project
id: tlp_research_implementation
version: 0.1.0

profiles:
  - adaos.research.implementation.v1

components:
  owned:
    - ref: skill:tlp_research_skill
      role: primary
      exposure: project_only
      lifecycle: bound
      relations: [realizes]
    - ref: skill:tlp_experiment_skill
      role: implementation
      exposure: project_only
      lifecycle: bound
      relations: [uses]

  dependencies:
    - ref: project:adaos_research_platform
      version: ^0.2
      lifecycle: shared
      relations: [presents, uses]

entrypoints:
  - id: implementation-diagnostics
    presentation: scenario:research_workbench
    bindings:
      implementation_ref: skill:tlp_research_skill

catalog:
  title: TLP Research Implementation
  description: Governed implementation and experimental base for TLP tasks.
  categories: [research, machine-learning]
  tags: [tlp, max-plus, pooling]

lifecycle:
  uninstall:
    components: remove_if_unreferenced
    runtime_data: retain
    source_artifacts: retain
```

`components.owned` are released as part of the Project compatibility set.
Dependencies are resolved and locked by ProjectRelease; they are not copied
into the Project or granted source ownership. A Project may contain any
non-empty combination of skills and scenarios, including one standalone
headless skill.

Owned members have orthogonal metadata:

- `role` states composition responsibility (`primary`, `implementation`, or
  `supporting`), not UI visibility;
- `exposure` states discovery (`application`, `project_only`, or `advanced`),
  not authorization;
- `lifecycle` states whether the member is bound to this Project or shared;
- `relations` state why the member participates (`realizes`, `presents`,
  `evaluates`, or `uses`).

`project_only` components remain ordinary immutable packages with versions,
digests, signatures, dependency locks, and direct diagnostic addressing. They
are omitted from normal Catalog/Desktop discovery and are not independently
installed or removed. Hiding a component is never a security boundary.

### Data, telemetry, and managed-component ownership

Project composition does not create a parent-to-child storage namespace. The
core resolves four orthogonal identities for every material effect:

| Plane | Canonical owner | Permitted sharing |
| --- | --- | --- |
| Runtime data | the producing skill and its compatibility bucket | typed provider capability, `ArtifactRef`, or governed logical view |
| Execution telemetry | the execution/Automation/DevelopmentSession journal | read projection governed by session and participant policy |
| Orchestration state | the workflow or domain aggregate that authorized the work | command/query contract; never direct database lending |
| Published result | the aggregate/release named by its evidence manifest | immutable content reference plus provenance and policy |

Consequently, a `project_only` implementation skill may be installed and
removed with its Project without becoming a private subdirectory or database
of the primary member. The primary member cannot read the managed member's
`data` merely because both appear in `components.owned`. If it needs a result,
the managed member publishes a typed artifact or view and the consuming
contract records both producer and consumer identities.

Skill runtime `data` survives ordinary A/B activation and compatible patch
increments under the existing bucket migration policy. It is appropriate for
the skill's operational state and primary outputs. Builder candidate logs and
packaged-test diagnostics instead belong to Builder Automation evidence. They
must be atomically copied, bounded, and digested into the terminal session
receipt before an ephemeral candidate runtime is purged. This keeps diagnostic
provenance durable without turning skill data into a generic log store.

For coordinator/managed-component workflows, this separation is intentional.
The coordinator does not inherit the managed component's durable bucket, and a
managed component does not write its execution trace into the coordinator's
bucket. The portable handoff is a typed, content-addressed output or governed
logical view with producer, consumer, session, release, retention, and
idempotency identities. Core may later provide an SDK convenience for this
handoff, but it must compile to those existing ownership and capability rules;
it must not create a shared parent/child filesystem namespace. This lets a
managed implementation be upgraded, removed, retried, or federated without
silently transferring its database or logs to the Project's primary member.

Joint development is a practical consequence of Project ownership, but the
Project manifest does not record a transient current editing task. Publication
turns the Project definition plus exact component packages into an immutable
ProjectRelease.

### Definition, release, installation, and live-state boundary

The four identities must remain separate:

| Object | Mutability | Owns |
| --- | --- | --- |
| `ProjectDefinition` | Versioned source | intended composition, entry points, compatibility, lifecycle |
| `ProjectRelease` | Immutable | exact definition digest, member packages, dependency closure, validation locks |
| `ProjectInstallation` | Mutable by governed activation | selected release, workspace slot, reference counts, activation evidence |
| domain aggregate | Mutable by its domain workflow | user data, scientific state, conversations, decisions, runtime refs |

One ProjectRelease may be installed in multiple Assistants and used by multiple
domain aggregates. Conversely, one aggregate may reference several releases
over time or compose releases for different tasks. Therefore a Project id is
never a universal owner key for live state.

Project-to-Project dependencies are locked to exact ProjectRelease identities
or to an equivalent fully resolved closure. A release is incomplete if it locks
only leaf skill/scenario packages while dropping the dependency Project,
composition roles, entry points, or lifecycle policy that made the set valid.

### Install and remove semantics

Project installation is a durable transaction over the existing package and
WorkspaceLock lifecycle. Removing a Project:

- removes only component packages no longer referenced by another active
  Project/ProjectRelease;
- does not automatically remove shared dependency Projects;
- applies the declared data retention policy separately from package removal;
- never treats deleting source artifacts or skill runtime data as an implicit
  consequence of removing a catalog entry;
- records enough release and operation evidence to retry or roll back safely.

The first Research Workbench milestone needs a local Project definition and
Builder ProjectRelease compatibility only. Remote Project registry entries and
full transactional multi-component removal are not admission dependencies for
the pre-Codex handoff.

### Distributed deployment and live service topology

Project release, distributed deployment, presentation placement, and live
service/data topology are separate identities:

| Object | Question answered |
| --- | --- |
| `ProjectRelease` | What exact compatible software set is deliverable? |
| `ProjectDeployment` | On which nodes should each component be active? |
| `ComponentActivation` | What exact component is observed active on one node? |
| `ProjectPlacement` | In which webspace/presentation context is an entry point exposed? |
| `ServiceGroup` / `ServiceInstance` | Which activated runtimes currently form one logical service? |
| `Dataset` / `Partition` / `Replica` | Where is distributed state authoritative, derived, cached, fresh, or unavailable? |

The current `adaos.project.placement.v1` contract is webspace-oriented and must
not be overloaded with per-component node activation. The target core adds a
durable `ProjectDeployment` desired-state record, immutable reviewed deployment
plans, per-node activation evidence, and journaled install/update/drain/remove
operations. Initial placement modes include explicit singleton,
`selected_nodes`, capability-based `all_matching`, `per_endpoint`, and
`co_located_with` policies. Webspace exposure remains a separate
`ProjectPlacement` decision.

A subnet-wide rollout is not one atomic filesystem transaction. Each node
activation is transactional and idempotent within its authority boundary; the
deployment records partial success, compatible version skew, stop policy,
bounded retry, and rollback evidence truthfully. Stateful component removal
cordons and drains the runtime before package removal. Runtime/derived-data
retention remains an independent declared decision.

Trusted members publish explicit bounded deployment capabilities in their node
snapshot. The ordinary hub control plane may route reviewed component phases
over the authenticated member link, so Project deployment does not require a
skill-owned installer or a LAN-exposed runtime endpoint. Package frames remain
bounded; oversized packages require a declared chunked/direct transport and
fail before remote mutation.

An active package does not by itself prove a ready distributed service or a
fresh replica. `ComponentActivation` feeds the generic
[Distributed Service And Data Topology](distributed-service-and-data-topology.md),
which owns service membership, authority leases/epochs, partitions, replicas,
freshness and route facts. Domain adapters own partition meaning, payloads,
snapshot/delta implementation, conflict resolution and query merge.

The source Project definition may declare required service and placement
profiles, but mutable node selection, service instances, topology generations,
capacity observations, leases and replica checkpoints remain outside the
immutable ProjectRelease.

## Application and Presentation Model

Scenario remains the implementation term for a UI/workflow host. Application
is the product-facing launchable projection. The mapping is no longer assumed
to be globally one-to-one.

A skill can advertise presentations independently of the Project that later
selects an entry point:

```yaml
presentations:
  - id: research-implementation-diagnostics
    scenario: research_workbench
    contract: adaos.research.implementation.v1
    default: false
    bindings:
      implementation_ref: skill:self
```

One shared scenario may therefore present many skill or Project instances.
Launching a live research direction is a Workbench domain deep link such as:

```text
scenario:research_workbench + direction_id=research-direction:tlp-01
```

The Research Orchestrator resolves the direction's current implementation refs;
the Project entry point does not manufacture domain identity. Legacy
`direction_ref=skill:*` destinations remain migration inputs only. None of these
forms copies `research_workbench` into every direction or turns a "first parent
scenario" into an ordering-sensitive runtime rule.

### Preview resolution

Builder and Desktop use one deterministic resolution order:

1. explicit `project.entrypoints[*]` launch/presentation target;
2. explicit default presentation on the focused skill;
3. `adaos.system.skill-preview` fallback;
4. a diagnostic result if the target is invalid.

The generic skill-preview host is intentionally similar to a minimal Desktop
surface. It renders standard metadata, README/help, icon, capabilities, and
declared widgets. Missing icons or widgets produce explicit empty states. The
host is a platform fallback, not a compatibility claim written into every
skill.

Open Preview and its QR code must be projections of one canonical navigation
destination. The destination contains the exact development webspace,
scenario/presentation, bindings, zone/subnet, and applicable authentication
policy. A button must not substitute the current Builder host webspace, and a
QR code must not independently reconstruct a weaker URL.

Preview or Trial evidence records exact Project/component/presentation
revisions. A declaration that a skill can use a scenario is not proof that the
combination was tested.

## Builder Development Session

Builder opens a Project, never an unscoped component. A single-skill edit is a
Project-scoped session whose `targets` contains one component. The session
still receives the exact Project definition/base release, read-only contracts
for non-target members and dependencies, entry-point compatibility, and
project-wide validation requirements. Publication produces a candidate
ProjectRelease and validates affected entry points and consumers, not merely a
patch that passes the target skill's self-tests.

Builder opens a Project through a mutable session overlay:

```yaml
schema: adaos.builder.development_session.v1
session_id: dev_tlp_001
project_ref: project:tlp_research
base_release:
  scope: local
  source_tree: sha256:<direction-code-tree>
  package_digest: sha256:<local-checkpoint>

focus:
  ref: skill:tlp_research_skill

targets:
  primary:
    - ref: skill:tlp_research_skill
      access: read-write
      context: full
  secondary: []

context_members:
  - ref: scenario:research_workbench
    relation: presentation
    access: read-only
    context: contract
  - ref: skill:research_orchestrator_skill
    relation: dependency
    access: read-only
    context: contract
  - ref: skill:research_manager_skill
    relation: consumer
    access: read-only
    context: contract

artifact_inputs:
  - ref: artifact://skill/tlp_research_skill/part0
    access: read-only
    audience: research.implementation
    manifest_digest: sha256:<source-manifest>
    context_digest: sha256:<filtered-view>

scratch:
  owner: session
  access: read-write

handoff:
  execution_budget:
    budget_view: fixed_downstream
    max_wall_seconds: 10800
    max_model_tokens: 5000000
    max_attempts: 2
    max_human_interventions: 0
  agent_profile:
    provider: openai-codex-cli
    model: gpt-5.4
    reasoning_effort: high
    tool_profile: adaos-local-bounded-v1
```

The three controls are independent:

- `focus` is what the user currently inspects;
- `targets` are what the development run may change;
- `context_members` and `artifact_inputs` are what it may read and at what
  detail level.

Changing focus does not enlarge write authority. If Codex discovers that a
read-only dependency must change, it returns a typed scope-expansion request;
the Project or orchestrator must explicitly admit a new target.

Access is enforced by Builder tool/capability scope and final change-set
validation, not only by prompt text. A run that changes a file outside admitted
targets fails review even if the patch would otherwise pass tests.

The first enforcement surface is
`builder_sdk_control_skill.review_development_changes`. It resolves every
absolute changed path against the session's exact target and scratch roots.
Artifact roots take precedence over their enclosing skill source and therefore
remain read-only. `request_development_scope` persists a deterministic
`adaos.builder.scope_expansion_request.v1` with `approved=false`; it does not
silently enlarge the session.

### Context projection

Context exposure is deliberately tiered:

| Exposure | Contents |
| --- | --- |
| `none` | identity only |
| `contract` | manifest, capabilities, public schemas, presentations |
| `docs` | contract plus selected documentation/examples |
| `paths` | explicit read-only path allow-list |
| `full` | complete component source for an admitted target |

The formulation LLM normally receives artifact-derived text, ResearchPrototype
state, decisions, and public contracts rather than all implementation source.
Codex receives full target source, read-only artifacts, and contract/docs views
of dependencies; additional source is hydrated on demand. This reduces context
load without hiding the exact objects and revisions on which the task depends.

An artifact group may contain material for different consumers. Each item may
therefore declare a generic `context_policy` with `default`, exact `allow` and
`deny` audiences, and an operator-readable reason. The SDK materializes an
immutable audience view under CTX state, verifies every admitted file digest,
and gives Builder only the view's `files` root. Filtering extracted prompt text
alone is not an isolation boundary because a filesystem-capable agent could
otherwise read a hidden sibling file. The source-manifest digest and filtered
view digest are both retained in the Development Session.

Audience names and their meaning are consumer-owned. Core enforces exact
membership and materialization but does not know what
`research.formulation`, `research.implementation`, or
`research.evaluation` mean. Legacy items without a policy remain visible for
compatibility; a clean evaluation must assign explicit policies before its
frozen receipt is admitted.

### Contract and scientific-system projection

Read-only context controls what Codex may inspect; it does not by itself state
what the generated target must implement. The immutable AutomationBrief also
projects typed `contract_requirements` from each real consumer. A provider
requirement names the contract/capability identity, required public operations,
consumer component, ownership boundary, and conformance evidence. Builder
checks the resulting provider declaration and exported operations before it
accepts a checkpoint. Consumer-owned normalization and summary paths remain
authoritative; target-owned mocks cannot replace them.

Research directions additionally carry a source-grounded
`system_specification`: ordered components and exact settings, locked
invariants, the intended intervention boundary, decision status and source
refs. This separates a scientific object from the prose used to discuss it.
Admission rejects unresolved choices and analogy-only descriptions before
Codex is allowed to fill them with plausible defaults.

The Development Session therefore provides six different constraints:

| Constraint | Purpose |
| --- | --- |
| target scope | which source Codex may change |
| read-only context | which exact dependencies and artifacts it may inspect |
| contract requirements | which external ABI the target must satisfy |
| system specification | which scientific structure it must preserve |
| execution budget | which wall-time, token, retry, and intervention envelope is scored |
| agent profile | which exact provider/model/reasoning/tool configuration performs the work |

All six are digest-bound. Package installation is part of realization:
dependency declarations and isolation policy are validated before checkpoint,
then the ordinary package/install/activate lifecycle supplies the runtime
receipt.

The core Development Session ABI is domain-neutral. It should accept typed
`subject_refs`, `contract_inputs`, and `acceptance_profiles`; Research Fabric
projects its AutomationBrief, ResearchCompilation, artifact visibility, and
scientific prohibitions through those generic fields. Requiring every Builder
session to carry a `ResearchPrototype` or a specific Codex provider would turn
the first consumer into core semantics and is therefore a migration defect.

## Local-First Artifact Context

The first implementation stores model-facing intake as ordinary files in the
owned direction skill:

```text
<direction-skill>/
  artifacts/
    part0/
      manifest.yaml
      TropicalMaxPoo1.ipynb
      initial-review.md
    part1/
      manifest.yaml
      ...
```

`part0`, `part1`, and later groups are stable artifact-set identifiers, not
mutable "current folder" pointers. Accepting a ResearchPrototype binds exact
group and file digests. Adding a group or changing a file creates a new source
revision and makes an unaccepted formulation stale.

This source tree is distinct from mutable activated data:

```text
skill source/artifacts/*                      # human/LLM/Codex intake
.adaos/workspace/skills/.runtime/*/data/*     # runtime and experiment state
```

The group manifest records at least stable item id, relative path, MIME/media
type, role, digest, size, origin, trust, sensitivity, license/redistribution
policy when known, and publication policy. Notebook outputs and retrieved text
remain untrusted inputs; they are never promoted to scientific evidence merely
because a model can read them.

The optional `context_policy` is independent of `role`, trust, sensitivity,
and publication. A role describes what an item is; an audience policy controls
which bounded consumer context can see it. Domain skills may offer convenient
profiles, but enforcement and filesystem materialization remain SDK behavior.

The Skill SDK owns a neutral interface such as:

```text
ctx.artifacts.groups()
ctx.artifacts.list(group)
ctx.artifacts.resolve(group, artifact_id)
ctx.artifacts.read_text(group, artifact_id, bounds)
```

The first resolver returns a native local path. Builder puts the exact artifact
root, manifest, and required item paths into the AutomationBrief, so Codex uses
its normal filesystem tools. A conversation LLM without filesystem authority
receives bounded extraction through the orchestrator/SDK.

The logical reference remains provider-neutral:

```text
artifact://skill/<skill-id>/<group>/<artifact-id>
```

A later `additional_artifacts` binding, object-store provider, or MCP resource
adapter may resolve the same ref. That extension must not force the local
first milestone to duplicate files in a new database or require Codex to
discover an undocumented MCP server.

Skill-internal immutable files use the adjacent neutral `storage.blob`
capability. The SDK returns an owner-scoped opaque binding and
content-addressed object receipt; a local filesystem provider may materialize
the verified object for a Development Session without revealing or accepting
another skill's data root. Relational storage and blob storage remain separate
capabilities under one owner boundary, so a component never installs its own
database merely to retain structured metadata plus files.

## Digest-Bound Traceability

`adaos.traceability.graph.v1` is a domain-neutral graph for connecting exact
inputs, decisions, implementation tasks, executions, observations, artifacts,
and acceptance decisions. Nodes and edges are immutable references with a
canonical graph digest. The core validator checks uniqueness, endpoint
integrity, self-references, and digest drift. Its path evaluator accepts
domain-supplied source, target, and ordered node-kind requirements; it does not
embed research-stage names in the platform.

The research compiler uses this primitive to require a chain from source
material through scientific and engineering decisions to observations and
acceptance. Other skills can use the same primitive for operational evidence,
release provenance, or governed content generation without inheriting research
semantics.

### Private pre-Codex checkpoint

Acceptance needs an immutable base identity, but it must not publish a private
notebook merely to create that identity. `builder_artifacts.local_checkpoint`
therefore hashes the direction component's local code/config source into CTX
Builder state and reports `scope=local`, `bytes_uploaded=0`. It excludes
`artifacts/` from the code tree because AutomationBrief and Development Session
bind each artifact group by its separately validated manifest digest and native
read-only root.

This checkpoint is not a Forge release and is not a distribution claim. Forge
publication begins only after Codex implementation, validation, human review,
and the ordinary publish decision. The separation avoids both a 413-sized
private upload failure and a more serious accidental disclosure of research
intake during formulation.

## Registry and Catalog Classification

The registry is a published catalog snapshot, not the inventory of live
research directions and not runtime state. Its target schema may add a
backward-compatible `projects` collection while preserving existing `skills`
and `scenarios` arrays for component tooling.

Classification uses separate fields:

```yaml
kind: project
profiles: [adaos.research.implementation.v1]
catalog:
  categories: [research]
  tags: [ml, tlp]
deployment:
  scopes: [member]
```

- `kind` drives artifact lifecycle;
- `profiles` and provided capabilities drive machine selection;
- `categories` drive localized Catalog browsing;
- `tags` support non-authoritative search;
- `deployment.scopes` express placement/compatibility, not subject domain.

Research Workbench discovers local directions through the research-domain
index. It resolves each implementation ref through installed/local Projects
and profiles; it must not discover live directions by scanning Project manifests
or public registry descriptions for the word "research". A local direction is
not added to the public registry merely because its implementation was
published.

The normal Catalog leads with Projects/Applications. An advanced Components
view may expose raw skills, scenarios, providers, capabilities, versions, and
dependency diagnostics.

## Research Reference Composition

The general contracts map to Research Fabric as follows:

```text
project:adaos_research_platform
  owns scenario:research_workbench
  owns skill:research_orchestrator_skill

project:tlp_research_implementation
  owns skill:tlp_research_skill             # bounded task implementation
  may own project-only supporting skills
  depends on project:adaos_research_platform
  may expose a Workbench-compatible diagnostic entry point

ResearchDirection:tlp
  owns live direction metadata, source manifests, agenda/tasks, and decisions
  references ProjectRelease:tlp_research_implementation@<digest>
  is presented by research_workbench(direction_id=<direction-instance>)
```

The Research Workbench is the product entry point. It lists directions, keeps
one direction in session focus, creates the live direction through the
Research Orchestrator, admits local artifact groups, supports task formulation
and exact acceptance, and asks Builder SDK for a Project-scoped Development
Session when implementation is required. The current compatibility path may
also create a one-component draft Project at direction intake; that shortcut
must be represented as a linked implementation ref, not as identity equality.
Builder owns source mutation and Codex; the Workbench links to the session and
observes its durable state rather than embedding a Research tab into Builder.

Every direction need not appear as a Desktop Application. One Research
Workbench application and compact activity widget are sufficient. Notifications
and optional shortcuts may deep-link to a focused direction.

## Current-Milestone Boundary

The already proven compatibility form of the pre-Codex research milestone
requires:

- local Project definition with one primary direction skill;
- Research Workbench portfolio/list and selected-direction detail;
- Workbench-driven creation through Builder SDK;
- `artifacts/part0` upload, manifest, inspection, and exact digest binding;
- durable formulation revisions and human acceptance;
- AutomationBrief with exact Project, target, artifact, prototype, and
  prohibited-action refs;
- Builder Development Session with explicit read/write context policy;
- canonical skill presentation/fallback preview and navigation destination;
- a reproducible TLP walkthrough ending before Codex starts.

It does not require remote Project catalog publication, an external artifact
store, MCP artifact access, directory/archive ingestion, autonomous Codex,
experiment execution, or one Desktop icon/scenario per direction.

The target migration additionally requires an explicit ResearchDirection id,
task id, implementation Project ref, and Development Session ref; existing
records that used one id for all four remain readable but must be normalized at
the domain boundary.

## Related Documents

- [AdaOS Product Terminology](product-terminology.md)
- [AdaOS Builder](builder.md)
- [Builder Roadmap](builder-roadmap.md)
- [Artifact Source, Package, and Activation Architecture](artifact-source-package-activation.md)
- [Registry, Marketplace, and Operations Roadmap](registry-marketplace-operations-roadmap.md)
- [AdaOS Research Fabric](research-fabric.md)
- [Research Fabric Roadmap](research-fabric-roadmap.md)
- [Research Project pre-Codex walkthrough](research-project-pre-codex-walkthrough.md)
