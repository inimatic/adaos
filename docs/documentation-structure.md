# Documentation Structure and Authority

Status: normative information-architecture policy.

Last reviewed: 2026-08-07.

AdaOS documentation is organized by authority and lifecycle, not by how early
an idea appeared. Every durable page must have a clear owner and belong to one
of the contexts below.

## Documentation Contexts

| Context | Owns | Change profile |
| --- | --- | --- |
| Home and Product | stable public framing, product vocabulary, solution directions, and maturity claims | changes deliberately and infrequently |
| Architecture | current runtime boundaries, target contracts, ownership, invariants, and explicitly labeled implementation status | changes with accepted architecture decisions |
| Roadmaps | sequence, dependencies, proof gates, and remaining work | changes as evidence lands or priorities are decided |
| Guides | task-oriented procedures that a developer or operator can execute | changes when the supported workflow changes |
| CLI, SDK, and IO reference | callable interfaces, configuration, schemas, and compatibility behavior | changes with the corresponding interface |
| Evidence and historical records | dated verification, audits, release evidence, and superseded planning retained for traceability | immutable or explicitly marked historical |

The [Roadmap Inventory and Authority Map](architecture/roadmap-inventory.md)
identifies architecture and sequencing owners for cross-domain work. The
[Issue Tracker](issue-tracker.md) owns active execution records; it is not a
substitute for architecture or roadmap authority.

## Placement Rules

1. Describe current and target technical behavior in Architecture. State which
   one is being described; do not blend an implemented baseline with an
   unlabeled aspiration.
2. Put delivery order and incomplete checklists in a roadmap owned by the
   corresponding architecture page.
3. Put repeatable operator or developer steps in Guides or the relevant
   interface reference.
4. Put stable portfolio framing in Product. Technical pages must not invent
   product maturity or portfolio priority.
5. Keep dated proof as evidence. Do not continually rewrite an evidence record
   to represent the latest state.
6. Promote useful material to its owning English page. Do not create a generic
   holding area for unowned ideas.

## Retirement of `Concepts`

The former `concepts` tree mixed early vision, current implementation notes,
roadmaps, Russian-only drafts, and superseded designs. It is retired rather
than preserved as a parallel authority.

| Former subject | Current authority or disposition |
| --- | --- |
| NLU runtime, providers, Teacher, and delivery sequence | [NLU Runtime](architecture/nlu.md), [NLU Target Architecture](architecture/nlu-target-architecture.md), [NLU Teacher](architecture/nlu-teacher-llm.md), and the two NLU roadmaps |
| Context compression | [Context Compression Layer](architecture/context-compression.md) |
| Event primer | [Event Management](architecture/event-management.md), subordinate to the [Operational Event Model](architecture/operational-event-model.md) |
| IO, skill, and endpoint routing | [Routing](architecture/routing.md), [Endpoint Infrastructure](architecture/endpoint-infrastructure.md), and transport ownership documents |
| Managed service skills | [Service Skills](architecture/service-skills.md) |
| Personalization, identity, and access | [Personalization, Identity, and Access](architecture/personalization-identity-access.md) and its roadmap |
| Application lifecycle, Catalog, publication, installation, subscriptions, and external feedback | [Application Lifecycle, Distribution, and Feedback](architecture/application-lifecycle-and-distribution.md) and its roadmap |
| Web client, UI-as-data, and browser connection drafts | [Web UI Architecture](architecture/web-ui-architecture.md), [UI Addressing](architecture/ui-addressing.md), and browser/hub lifecycle architecture |
| Skill/scenario development stages and code-flow drafts | [Builder](architecture/builder.md), [Skill Factory](architecture/skill-factory.md), [Governed Evolution](architecture/governed-evolution.md), and task-oriented guides |
| Procedural scenario and first-launch drafts | [Governed Data-Driven Workflow Model](architecture/governed-workflow-runtime.md), [Skill Activation and Scenario Binding](architecture/skill-activation-and-scenario-binding.md), and [Scenarios](scenarios.md) |
| ARL draft | no general locator contract is retained; use typed refs from [UI Addressing](architecture/ui-addressing.md), canonical identities from [Named Entities](architecture/named-entities.md), and domain-owned schemas |
| Early TrustHub and device-initialization drafts | [Security](architecture/security.md), [Device Access and Browsers](architecture/device-access-and-browsers.md), and current onboarding guides |

Material that was obsolete, internally contradictory, or already fully
represented by these authorities remains available in Git history. It must not
be cited as current AdaOS design.

## Language Boundary

English is authoritative. The Russian tree translates only the stable pages
listed in the [Documentation Language and Translation Policy](documentation-language-policy.md).
For English-only contexts, the Russian navigation links directly to the
canonical English section instead of publishing fallback copies under Russian
URLs.
