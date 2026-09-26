# AdaOS Product Terminology

AdaOS is a platform for personal and shared assistant environments. Internally
the runtime still uses subnets, scenarios, widgets, browser sessions,
hub/member roles, and Yjs webspaces, but normal product UI should lead with
named user-facing entities.

This page governs user-facing object names inside an AdaOS environment. The
portfolio-level distinction between platform, deployment profile, solution
pack, solution agent, endpoint, and channel is governed by the
[AdaOS Product Model](../product/index.md).

The canonical product and distribution boundary is governed by
[Application Lifecycle, Distribution, and Feedback](application-lifecycle-and-distribution.md).
Semantic capability, binding, package, local state, and resolution identities
are governed by
[Capability, Binding, and State Separation](capability-binding-state-separation.md).
Application permissions, Application-defined roles, per-user/child/guest
Application access, Builder final verification, and the Users & Access product
projection are governed by
[Application Access, Permissions, and Roles](application-access-permissions.md).
[Project Composition, Presentation, and Development Context](project-composition-and-development-context.md)
retains the current `Project*` implementation vocabulary during migration.

## Primary Model

The primary user-facing hierarchy is:

```text
Assistant -> Webspace -> Application -> Panel
```

An Application that creates launchable subordinate work may additionally
present managed Projects:

```text
Application -> Project -> Panel
```

A Project is a managed Application kind, not a new top-level Catalog product.
See [Managed Applications and Projects](managed-applications-and-projects.md).

The runtime/device hierarchy is:

```text
Assistant -> Device -> Agent
```

Combined:

```text
Assistant
  -> Webspaces
     -> Applications
        -> Panels
  -> Skills
  -> Devices
     -> Agents
  -> Interfaces
  -> Catalog
```

The Catalog is product-first. It presents installable Applications. Skills,
scenarios, workflows, providers, and launch targets are component-level
entities available in an advanced/developer view rather than peer products
that every user must understand.

A managed deployment may contain one or more Assistant environments. The
simplest current product shape is one Assistant backed by one subnet. Campus
and Enterprise may eventually manage collections or federations of Assistants;
that future topology must not change the meaning of the current user-facing
objects prematurely.

## Portfolio-Level Terms

| Term | Product meaning | Boundary |
| --- | --- | --- |
| AdaOS platform | Shared runtime and governed capability foundation | Not one deployment offer, endpoint, or vertical solution |
| Managed deployment | An operated AdaOS installation for a person or organization | May contain one or more Assistant environments |
| Deployment profile | Versioned topology, role, policy, application, skill, and integration defaults | Configuration and composition, not a new runtime kind |
| Solution pack | Domain skills, scenarios, workflows, policies, templates, and projections | Uses the shared package and activation lifecycle |
| Solution agent or workbench | User-facing role and interaction surface over solution packs | Does not own a second persistence or workflow truth |
| Endpoint family | Browser, reDevice, or another participation surface | Cross-cutting; it does not define an application domain |
| Activation channel | How AdaOS reaches users | Distribution experiment, not platform architecture |

AdaOS Home, Campus, and Enterprise are deployment-profile directions.
aResearcher is a solution-agent direction over the Research Fabric. reDevice is
an endpoint family. These names must not be presented as peers at the same
architectural layer.

## Builder

`Builder` is the user-facing creation role for turning an idea into governed
AdaOS artifacts: skills, scenarios, manifests, UI descriptors, NLU hints, tests,
and runtime-ready changes.

Builder is executor-neutral. It may be a human, an AI-assisted agent, or a
human-in-the-loop workflow. Product UI can use phrases such as "Let's build it"
for capability creation, while advanced/developer docs should link to
[AdaOS Builder](builder.md) for the precise architecture boundary.

Do not introduce separate role names such as `LLM programmer` for this
capability creation path. If an implementation detail needs to mention LLM
assistance, describe it as a Builder mode.

Builder opens an Application through a mutable **Development Session**. The
session records current targets, read-only context, model-facing artifacts, and
run/checkpoint state; it is not itself installed or published. Existing
`Project` commands and records are compatibility implementations of this
Application-scoped flow. Neither Application nor Project names an arbitrary
chat tab or Codex process.

## Domain Workspaces

Domain objects are not automatically Applications. For Research
Fabric, a **Research direction** is a live body of scientific work, a
**Research task** is one bounded scientific question inside it, and an
**Implementation track** is one engineering line that realizes a task. A
portable Application supplies versioned software for one or more tracks; it does
not own the live direction merely because Workbench created both during the
same user action.

The Research Workbench is the Application. Its home surface is the direction
portfolio. Individual directions are selected domain workspaces/deep links,
not automatically Desktop Applications. The same rule should be reused by
future workbenches whose domain instances outnumber useful launchable apps.
When a direction produces a launchable and independently versioned
implementation, Research Workbench may own it as a managed **Project**. The
Project is not identical to the direction, task, candidate, or experiment.

## Term Mapping

| Internal term | Product term | Notes |
| --- | --- | --- |
| `subnet`, `subnet_id` | Assistant, Assistant ID | Show the display name by default. Keep IDs for diagnostics. |
| `webspace`, `default`, `main` | Webspace, Main | Webspace is an access/projection context, not a folder. |
| `scenario` | Application host | Scenario remains the implementation/authoring term; an Application launch target may bind one host to different skills, so the mapping is not always one-to-one. |
| `web_desktop` | Capabilities | Default overview application. Keep `web_desktop` as the stable ID. |
| `capability_contract` | Capability | Stable semantic behavior an Application requires; independent of package, provider, node, and credential. Normal UI uses a localized capability title and keeps the ref in advanced detail. |
| `application_requirement` | App requirement | Versioned requirement for a capability/state contract plus policy and evidence constraints. It is not a skill or package dependency. |
| `semantic_application_revision` | App definition revision | Immutable implementation-free product behavior, requirements, state needs, launch/access intent, UI/workflow semantics, and requirement traceability accepted for one revision. |
| `skill` | Skill | Executable runtime component that may implement or support one or more capabilities. It is not the semantic capability identity. |
| `binding_definition` | Implementation | Portable implementation/adapter contract that satisfies a capability under declared conditions. |
| `binding_instance` | Connected implementation | Local configured instance of an implementation, including opaque provider/account references. |
| `state_space` | App data space | Stable local data identity with independent owner, lifecycle, custodian, portability, and physical location. |
| `application_resolution` | Resolved composition | Immutable admitted mapping from requirements to contracts, implementations, exact packages, local bindings/state obligations, and evidence. |
| `package_release`, `ArtifactPackageRef` | Implementation package | Immutable delivery bytes. Do not confuse this with a Capability, Application, or portfolio-level Solution pack. |
| `project` | Application source composition (compatibility) | Current internal physical ownership/composition of skills/scenarios, launch targets, and lifecycle policy. New semantic Application source declares requirements; keep Project for compatibility diagnostics and APIs. |
| `managed Application`, `kind=project` | Project | Launchable/versioned Application owned and governed through another Application; hidden from ordinary inventory and Catalog by default. Do not use it for every domain record. |
| `project_release` | Application release (compatibility) | Current immutable component/dependency-locked release record. Native `ApplicationRelease` binds the semantic revision and portable delivery metadata; the environment-specific exact selection belongs to `ApplicationResolution` and `WorkspaceLock`. Preserve legacy digest identity. |
| `builder development session` | Development session | Mutable Builder overlay with explicit targets and read-only context. Never shown as an installed application. |
| `presentation` | Application view or launch target | Explicit scenario host/binding for a skill or Project entry point. |
| `research direction` | Research direction | Live scientific aggregate presented by Research Workbench; not a Project, skill, or scenario identity. |
| `research task` | Research task | Bounded scientific question with formulation, protocol, evaluation, and lineage. |
| `implementation track` | Implementation track | Engineering path and Application/Development Session lineage for one task; do not label its skills as separate research directions. |
| `widget` | Widget, later Panel | Current UI may keep Widget while the broader product model reserves Panel. |
| `browser`, `member`, `hub`, `subnet endpoint` | Agent | Software participant of the assistant subnet. |
| `device` | Device | Physical or virtual host. One device may host multiple agents. |
| `marketplace` | Catalog | Place to add Applications, with skills, scenarios, widgets/panels, interfaces, agents, and integrations available in advanced views. Prerelease is selected from Application detail, not global search. |
| `install` | Add to assistant | Use install/deploy wording only in advanced or developer UI. |
| `application role` | App role | Role declared by an Application, such as viewer, editor, assignee, student, teacher, reviewer, or moderator. It is distinct from subnet role presets such as owner, member, child, and guest. |
| `permission profile` | App permissions | Human-facing summary of what an Application may do, derived from structured Application permission declarations and enforced by platform policy. |
| `ApplicationVerificationReport` | Release readiness report | Builder-produced release-bound evidence for permission declarations, observed access, app roles, regression tests, access matrix, secrets, disclosures, Pending Action fallback, auditability, warnings, attestations, and residual risks. |
| `user access management` | Users & Access | Product surface for people, children, guests, devices, sessions, Application access, Application roles, approvals, and audit. |

## UI Rules

Use named entities first. For example, render `subnet_id` as `My Assistant` or the user-defined assistant name, `default` as `Main`, and `web_desktop` as `Capabilities`.

The primary top-bar formula is:

```text
Brand | Assistant | Webspace | Application | Status | Actions
```

In compact layouts, the assistant name may be hidden when it is the default `My Assistant`, leaving:

```text
Webspace / Application
```

Debug-first labels such as raw subnet IDs, endpoint IDs, `LINK OK`, or low-level Yjs state belong in diagnostics and advanced mode.

Catalog categories are discovery labels, not runtime types. For example,
`research` may be a localized category, while
`adaos.research.implementation.v1` is a machine-readable profile and `member` or
`home_subnet` is a deployment scope. UI filters must not collapse those three
axes into one category field.

Applications presents product behavior first. Capability requirements may be
shown as a concise `Capabilities` summary, while selected implementations,
packages, providers, state spaces, and runtime components remain advanced
details. The UI must not use `Skill`, `Capability`, `Integration`, and
`Application` as interchangeable labels.

## Compatibility Policy

Do not break the current API or Yjs schema while migrating terminology. Add public aliases and projections first:

- `web.application.*` may delegate to existing `web.desktop.*`.
- `application_id` may alias `scenario_id`.
- `pinned_panels` may alias `pinned_widgets` if and when Panel becomes the visible term.
- New product kinds such as Assistant, Application, Agent, and Panel can exist next to older internal/debug kinds.
- Application catalog entries may be added alongside the existing `projects`,
  skill, and scenario registry arrays; legacy Project/component APIs remain
  compatible during the migration.

Device/Agent migration should happen through projections and catalog views before changing connectivity or pairing data structures.
