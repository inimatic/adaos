# AdaOS Product Model

Status: authoritative product model and strategic direction. This page does
not claim that every named solution is implemented or commercially available.

Last reviewed: 2026-08-07.

Russian translation: [Продуктовая модель AdaOS](/ru/product/).

## Purpose

AdaOS is a local-first platform for building and operating distributed
assistant environments that connect people, AI agents, applications, and
devices. The platform supplies one governed runtime foundation; product offers
and domain solutions configure and compose that foundation without creating
separate AdaOS codebases.

This model keeps the following concepts distinct:

| Concept | Meaning |
| --- | --- |
| AdaOS platform | Shared runtime, SDK, lifecycle, identity, policy, artifact, observation, and recovery foundation. |
| Managed deployment | An AdaOS environment installed and operated for a person, household, institution, or organization. |
| Assistant environment | The user-facing collaboration and execution boundary backed internally by an AdaOS subnet. |
| Deployment profile | Versioned defaults for topology, roles, policies, applications, skills, and integrations for a class of deployment. |
| Solution pack | A domain-oriented composition of skills, scenarios, workflows, UI projections, policies, and templates. |
| Solution agent or workbench | A user-facing role and interaction surface over one or more solution packs, such as aResearcher. |
| Endpoint family | A way people, devices, or software participate in an environment, such as Browser or reDevice. |
| Activation channel | A way AdaOS reaches users, such as local installation, an institutional deployment, or a device reuse program. |

## Product Shape

```text
Managed deployment
  -> one or more Assistant environments
     -> Webspaces
        -> Applications
           -> Panels
     -> Skills and scenarios
     -> Devices
        -> Agents and endpoints

Deployment profiles configure the environment.
Solution packs and solution agents deliver domain outcomes.
AdaOS Core installs, runs, observes, guards, updates, and rolls back them.
```

The simplest current topology is one Assistant environment backed by one
hub-managed subnet with optional member nodes and browser or device endpoints.
Institutional and organizational directions may require managed collections or
future federation of multiple environments. That broader topology is a target
direction, not an implemented federation claim.

## Two Independent Product Dimensions

AdaOS directions must not be presented as a flat list. They occupy two
independent dimensions.

### Deployment and governance profiles

- **AdaOS Home**: private person or household ownership, guests, rooms,
  devices, and local routines.
- **AdaOS Campus**: institution-managed teaching, learning, course, seminar,
  and laboratory environments.
- **AdaOS Enterprise**: organization-managed teams, processes, policies,
  directories, integrations, and audit.

### Domain solutions and agents

- **Research**: governed studies, hypotheses, protocols, experiments,
  evidence, analysis, and publication support.
- **aResearcher**: a future solution agent or workbench over the
  [AdaOS Research Fabric](../architecture/research-fabric.md), not a separate
  persistence or execution runtime.
- Future teaching, student, household, and operator assistants may use the same
  solution-agent pattern without becoming new platform kernels.

A domain solution may run in more than one deployment profile. aResearcher, for
example, may operate in a personal environment, a university laboratory, or a
corporate R&D deployment.

## Endpoint Boundary

Browser, reDevice, hub, and member are not application domains. They are
participation and execution surfaces. In particular, reDevice may be used at
home, in a classroom, in a laboratory, or in an office; it must not be coupled
to AdaOS Home in the platform model.

## Shared Capability Layers

Reusable capabilities should remain horizontal where their contracts are
genuinely shared:

- identity, membership, grants, consent, and audit;
- memory, artifacts, provenance, and search;
- calendars, documents, communication, and notifications;
- device discovery, media, and control;
- workflows, approvals, evidence, and recovery;
- model execution, storage, tracking, and external integrations.

Solution packs compose those capabilities with domain schemas and scenarios.
They should not create separate calendars, identity kernels, or lifecycle
systems for every product direction.

## Maturity Vocabulary

Every product or solution page must use one of these explicit maturity labels:

| Label | Meaning |
| --- | --- |
| Implemented | The stated behavior exists in the current repository and has referenced verification. |
| In development | An owned implementation slice exists, but its acceptance boundary remains open. |
| Proposed target architecture | A reviewed design exists; it is not a delivery claim. |
| Strategic direction | A product hypothesis and boundary exist, but detailed delivery is not committed. |
| Long-term direction | The direction informs compatibility and platform choices but has no near-term product commitment. |

Marketing names do not override these labels. A page describing AdaOS Campus or
AdaOS Enterprise must not imply product availability merely because some shared
identity or policy primitives are implemented.

## Solution Definition Contract

Before a direction receives a detailed product page or roadmap, its definition
should identify:

1. the user, operator, and buyer where they differ;
2. the value promise and measurable outcome;
3. deployment topology and trust boundaries;
4. actors, memberships, roles, and consent model;
5. domain objects and data ownership;
6. shared and domain-specific skills;
7. three to five canonical end-to-end scenarios;
8. policy, privacy, retention, and audit requirements;
9. UI and endpoint surfaces;
10. required integrations;
11. maturity, evidence, non-goals, and unresolved decisions.

The current portfolio hypotheses are documented in
[Solution Directions](solution-directions.md).

## Architectural Invariants

Product development must preserve these invariants:

- one shared AdaOS runtime and package lifecycle;
- profiles and solution packs are configuration and composition, not forks;
- user profile, membership, role, and domain assessment remain distinct;
- publication, activation, and authorization remain separate transitions;
- private and shared data scopes remain explicit below the UI;
- devices and channels do not define the platform architecture;
- current implementation, target architecture, and market positioning remain
  visibly distinct.

The detailed user-facing object vocabulary remains in
[AdaOS Product Terminology](../architecture/product-terminology.md). Platform,
managed deployment, channel, and network boundaries are also governed by
[Governed Evolution](../architecture/governed-evolution.md).

## Documentation Authority

English documentation is authoritative. The maintained Russian product pages
are faithful translations of this stable public-facing layer; architecture,
roadmaps, evidence, and implementation references remain English-first. See
[Documentation Language and Translation Policy](../documentation-language-policy.md).
