# AdaOS Project Composition, Presentation, and Development Context

Status: target architecture. The local contracts required by the first
Research Workbench pre-Codex milestone are specified here; a published Project
catalog and transactional multi-component install/remove flow are follow-on
work.

Last reviewed: 2026-08-16.

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
10. A research direction is represented by a Project whose primary owned
    component is a `research.direction` skill. It normally uses the shared
    Research Workbench presentation and does not generate a scenario per
    direction.
11. A Development Session carries consumer-owned contract requirements in
    addition to read-only component context. Builder must prove that generated
    providers export the required operations and survive the ordinary package,
    install, and activation boundary; a target-owned mock is not compatibility
    evidence.

## Vocabulary

| Term | Meaning | Not this |
| --- | --- | --- |
| Component | Versioned skill or scenario package selected by a Project | User-facing solution identity |
| Project | Declarative distribution, entry-point, and lifecycle definition | Mutable Builder workspace or process |
| ProjectRelease | Immutable resolved set of component packages and locks | Registry channel or installed runtime state |
| Application | Launchable user-facing projection supplied by a Project entry point | Necessarily one package or one scenario identity |
| Presentation | Explicit binding from a skill/project entry point to a scenario host | Ownership, dependency, or validation evidence |
| Builder Development Session | Mutable, policy-scoped overlay for one development iteration | Distributable Project manifest |
| Development target | Component that the current session may change | Whatever is selected in the UI |
| Context member | Read-only or filtered dependency visible to an agent | Implicit write authority |
| Artifact group | Manifested local source material intended for human/LLM/Codex context | Mutable experiment/runtime data |
| Runtime data | Skill-owned operational state under its activated runtime bucket | Project source or intake material |
| Profile | Stable machine-readable semantic role such as `adaos.research.direction.v1` | Localized catalog category |
| Category | User-facing discovery facet such as `research` or `media` | Capability or deployment contract |

## Project Distribution Contract

The target source contract is additive to the existing component manifests:

```yaml
schema: adaos.project.v1
kind: project
id: tlp_research
version: 0.1.0

profiles:
  - adaos.research.direction.v1

components:
  owned:
    - ref: skill:tlp_research_skill
      role: primary
    - ref: skill:tlp_experiment_skill
      role: supporting

  dependencies:
    - ref: project:adaos_research_platform
      version: ^0.2

entrypoints:
  - id: research
    presentation: scenario:research_workbench
    bindings:
      direction_ref: skill:tlp_research_skill

catalog:
  title: TLP Research
  description: Governed TLP research direction and experimental base.
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

Joint development is a practical consequence of Project ownership, but the
Project manifest does not record a transient current editing task. Publication
turns the Project definition plus exact component packages into an immutable
ProjectRelease.

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

## Application and Presentation Model

Scenario remains the implementation term for a UI/workflow host. Application
is the product-facing launchable projection. The mapping is no longer assumed
to be globally one-to-one.

A skill can advertise presentations independently of the Project that later
selects an entry point:

```yaml
presentations:
  - id: research-workbench
    scenario: research_workbench
    contract: adaos.research.direction.v1
    default: true
    bindings:
      direction_ref: skill:self
```

One shared scenario may therefore present many skill instances. Launching an
installed research direction resolves to a bound application location such as:

```text
scenario:research_workbench + direction_ref=skill:tlp_research_skill
```

It does not copy `research_workbench` into every direction and does not turn a
"first parent scenario" into an ordering-sensitive runtime rule.

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

scratch:
  owner: session
  access: read-write
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

The Development Session therefore provides four different constraints:

| Constraint | Purpose |
| --- | --- |
| target scope | which source Codex may change |
| read-only context | which exact dependencies and artifacts it may inspect |
| contract requirements | which external ABI the target must satisfy |
| system specification | which scientific structure it must preserve |

All four are digest-bound. Package installation is part of realization:
dependency declarations and isolation policy are validated before checkpoint,
then the ordinary package/install/activate lifecycle supplies the runtime
receipt.

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
profiles: [adaos.research.direction.v1]
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

Research Workbench discovers local directions through the installed/local
Project and profile index. It must not scan public registry descriptions for
the word "research". A local draft is not added to the public registry until
ordinary publication.

The normal Catalog leads with Projects/Applications. An advanced Components
view may expose raw skills, scenarios, providers, capabilities, versions, and
dependency diagnostics.

## Research Reference Composition

The general contracts map to Research Fabric as follows:

```text
project:adaos_research_platform
  owns scenario:research_workbench
  owns skill:research_orchestrator_skill

project:<direction>
  owns skill:<direction-skill>              # research identity and code target
  may own supporting experiment skills
  depends on project:adaos_research_platform
  launches research_workbench(direction_ref=<direction-skill>)
```

The Research Workbench is the product entry point. It lists directions, keeps
one direction in session focus, creates a new Project and direction skill via
Builder SDK, adds local artifact groups, supports formulation and exact
acceptance, and creates the bounded Builder Development Session. Builder owns
source mutation and Codex; the Workbench links to that session and observes its
durable state rather than embedding a Research tab into Builder.

Every direction need not appear as a Desktop Application. One Research
Workbench application and compact activity widget are sufficient. Notifications
and optional shortcuts may deep-link to a focused direction.

## Current-Milestone Boundary

The pre-Codex research milestone requires:

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

## Related Documents

- [AdaOS Product Terminology](product-terminology.md)
- [AdaOS Builder](builder.md)
- [Builder Roadmap](builder-roadmap.md)
- [Artifact Source, Package, and Activation Architecture](artifact-source-package-activation.md)
- [Registry, Marketplace, and Operations Roadmap](registry-marketplace-operations-roadmap.md)
- [AdaOS Research Fabric](research-fabric.md)
- [Research Fabric Roadmap](research-fabric-roadmap.md)
- [Research Project pre-Codex walkthrough](research-project-pre-codex-walkthrough.md)
