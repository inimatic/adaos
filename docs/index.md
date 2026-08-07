# AdaOS

AdaOS is a local-first platform for building and operating distributed
assistant environments that connect people, AI agents, applications, and
devices.

This repository contains the shared developer-facing runtime foundation. It
provides the CLI, local control API, SDK, node services, skills, scenarios,
webspaces, device and browser integration contracts, and governed artifact
lifecycle used to build AdaOS solutions.

## One Platform, Several Solution Directions

AdaOS uses one runtime and package lifecycle across different deployment and
application contexts:

```text
AdaOS Core
  -> deployment profiles: Home, Campus, Enterprise
  -> solution frameworks and agents: Research Fabric, aResearcher
  -> solution packs: skills, scenarios, workflows, policies, UI
  -> endpoints: Browser, reDevice, hub and member nodes
```

These names are not separate AdaOS forks. Home, Campus, and Enterprise describe
deployment and governance profiles. Research and aResearcher describe a domain
framework and user-facing solution. reDevice is a cross-cutting endpoint
family.

See [AdaOS Product Model](product/index.md) for the normative distinctions and
[Solution Directions](product/solution-directions.md) for the current portfolio
hypotheses and maturity labels.

## What AdaOS Does Today

The current implementation is centered on local and private deployments:

- run a node as a `hub` or `member` and onboard members with join codes;
- install, validate, activate, run, update, and inspect skills and scenarios;
- expose runtime control through a FastAPI service with local token-based
  authentication;
- supervise service-type skills and runtime lifecycle;
- synchronize webspaces and browser-visible application state through Yjs;
- connect browsers and reDevice-style endpoints through explicit access and
  assignment contracts;
- manage profiles, memberships, grants, invitations, privacy scopes, and audit
  through the evolving personalization and access foundation;
- build, validate, package, publish, activate, observe, and repair governed
  artifacts through developer and Builder workflows.

Implemented foundations do not make every named solution product-complete.
Pages marked as target architecture, roadmap, strategic direction, or
long-term direction must be read according to their stated maturity.

## Core Runtime Model

- An **Assistant** is the user-facing environment backed internally by a
  subnet.
- A **Hub** owns and coordinates a subnet.
- A **Member** is another runtime node joined to that subnet.
- A **Webspace** is an access and projection context inside an Assistant.
- An **Application** is the product-facing projection of a scenario.
- A **Skill** provides a focused executable capability.
- A **Scenario** coordinates multi-step behavior across skills, services,
  people, and nodes.
- A **Device** is a physical or virtual host; an **Agent** or endpoint is a
  software participant running on or through it.

Detailed mappings between product and implementation terminology are in
[AdaOS Product Terminology](architecture/product-terminology.md).

## Choose a Path

| Goal | Start here |
| --- | --- |
| Understand what AdaOS is and where it can be applied | [Product Model](product/index.md) and [Solution Directions](product/solution-directions.md) |
| Install and run a local development environment | [Quickstart](quickstart.md) |
| Deploy or operate a node | [Deployment](deployment.md) and [Runtime and Operations](cli/runtime.md) |
| Build a skill or scenario | [Skills](skills.md), [Scenarios](scenarios.md), and [SDK](sdk/index.md) |
| Understand the implemented runtime | [Architecture Overview](architecture/index.md) |
| Understand long-term governed evolution | [Governed Evolution](architecture/governed-evolution.md) |
| Find current planning authority | [Roadmap Inventory](architecture/roadmap-inventory.md) and [Issue Tracker](issue-tracker.md) |
| Verify Builder end to end | [Builder Verification Guide](guides/builder-verification.md) |

## Documentation Status and Language

Current behavior, target architecture, roadmap items, and evidence records are
kept deliberately distinct. Start with the page status and the
[Roadmap Inventory](architecture/roadmap-inventory.md) before treating a design
or checklist as implemented behavior.

English documentation is authoritative. Maintained translations cover a
small, stable public-facing layer. Translated navigation links directly to
English-only technical sections instead of publishing fallback copies under a
translated URL. See the
[Documentation Language and Translation Policy](documentation-language-policy.md).
