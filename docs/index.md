# AdaOS

AdaOS is a Python platform for building distributed assistant systems out of skills, scenarios, node services, and local control APIs.

The current repository is the developer and runtime core of the project. It includes:

- a local CLI exposed as `adaos`
- a FastAPI-based control API
- runtime services for node, skill, scenario, and webspace management
- SDK modules for skills, data access, events, and control-plane integration
- bootstrap scripts, tests, and MkDocs documentation

## What AdaOS does today

The current implementation is centered around local and private deployments:

- run a node as a `hub` or `member`
- manage skills and scenarios from the CLI
- expose runtime control over HTTP with token-based local authentication
- operate service-type skills through a supervisor and `/api/services/*`
- manage Yjs-backed webspaces and desktop state through `adaos node yjs ...`
- support subnet onboarding with join codes and member updates
- provide developer workflows for Root and Forge-style publishing

## Main areas

- [Quickstart](quickstart.md): installation, bootstrap, and first commands
- [Architecture](architecture/index.md): how the runtime is organized
- [MVP Roadmap](mvp_roadmap.md): current milestone checklist for finishing the
  MVP across runtime, skills, browser, conversations, endpoints, and release
  evidence
- [Governed Evolution](architecture/governed-evolution.md): long-term product
  and architecture model connecting human intent, durable issues, Builder,
  publication, runtime evidence, and repair
- [Governed Evolution Roadmap](architecture/governed-evolution-roadmap.md):
  cross-domain milestones and proof gates; detailed implementation work remains
  in the domain roadmaps
- [Roadmap Inventory](architecture/roadmap-inventory.md): authority map and
  index of the domain architecture and roadmap documents
- [Artifact Source, Package, and Activation](architecture/artifact-source-package-activation.md):
  immutable source/package/release contracts, isolated trial, subscriptions,
  and transactional Workspace activation
- [Artifact Pipeline Evidence — 2026-07-24](architecture/artifact-pipeline-local-evidence-2026-07-24.md):
  reproducible local proof plus deployed backend-route and live Builder
  publication identities
- [CLI](cli/index.md): command groups and operational workflows
- [SDK](sdk/index.md): public Python-facing building blocks
- [Skills](skills.md): skill lifecycle and runtime behavior
- [Scenarios](scenarios.md): scenario lifecycle and execution model
- [DevPortal](devportal.md): developer workflows for Root-backed environments
- [Codex Project Recipes](guides/codex-project-recipes.md): practical local
  commands for Codex-assisted debugging, tests, workspace skills, UTF-8, and
  nested Git repositories

## Current scope

AdaOS already includes real operational features such as node roles, autostart,
core update orchestration, service supervision, monitoring, and Yjs webspace
control. Target-state architecture and implementation status are deliberately
separate: start with the [roadmap authority map](architecture/roadmap-inventory.md)
before interpreting a checklist or design proposal as current behavior.
