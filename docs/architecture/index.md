# Architecture

AdaOS is built as a local-first runtime with a layered Python codebase and a small control surface:

- the CLI builds and uses a shared `AgentContext`
- the FastAPI server exposes the same runtime over HTTP
- services manage skills, scenarios, node state, Yjs webspaces, and runtime lifecycle
- adapters isolate filesystem, database, git, audio, secret, and integration-specific IO

## Main runtime building blocks

- `src/adaos/apps`: CLI, API server, launchers, and process entry points
- `src/adaos/services`: orchestration and runtime logic
- `src/adaos/sdk`: public helpers for skills, scenarios, data access, and decorators
- `src/adaos/adapters`: filesystem, database, git, audio, secrets, and SDK bridge implementations
- `src/adaos/ports`: contracts for infrastructure-facing behavior
- `src/adaos/domain`: core types and registries

## Runtime model

In the current implementation:

- a node can operate as `hub` or `member`
- the local API exposes node, skill, scenario, observe, subnet, join, and service endpoints
- service-type skills are managed through a supervisor and health-aware status API
- Yjs-backed webspaces provide synchronized scenario and desktop state
- autostart and core-update flows are integrated with the runtime lifecycle

The pages in this section primarily summarize the implemented architecture.
When a page is explicitly labeled as a roadmap or target-state design, it captures planned control-plane evolution that should stay compatible with the current runtime.

Current target-state control-plane extensions are documented in:

- [Governed Evolution](governed-evolution.md): cross-domain target model from human signal and durable issue through Builder, publication, runtime evidence, and repair
- [Governed Evolution Roadmap](governed-evolution-roadmap.md): major product and architecture milestones, proof gates, and references to the roadmaps that own implementation detail
- [Governed Data-Driven Workflow Model and Interaction Architecture](governed-workflow-runtime.md): canonical `workflow.json`, states, transitions, registry and authority boundaries, relationship planes, capability-negotiated interactions, package-atomic activation, and asynchronous reply/delivery contracts that keep workflows consistent across skills, Web, Telegram, NLU, and tests
- [Governed Data-Driven Workflow Model Roadmap](governed-workflow-runtime-roadmap.md): MoSCoW implementation sequence from the metamodel and TransitionDescriptor through LLM authoring admission, package-bound definitions, negotiated interactions, Builder/Project proof, async recovery, and only then evidence-gated persistence choices
- [Governed Workflow Runtime Inventory](governed-workflow-runtime-inventory.md): owner and migration disposition for durable/ad-hoc workflow state, pending responses, retry loops, background task registries, state files, and transport surfaces that must stay distinct from workflow truth
- [Conversational Control Interface](conversational-interface.md): target contract for conversational input, semantic output, NLU data boundaries, Teacher-to-Builder promotion, conversational artifact packaging, and conversation-story tests
- [AdaOS Product Terminology](product-terminology.md): product-facing terms and compatibility rules for Assistant, Webspace, Application, Device, Agent, Skill, Widget/Panel, Interface, and Catalog
- [Infrascope](infrascope.md): human-facing control-plane architecture over the canonical system model
- [UI Addressing](ui-addressing.md): target typed ref vocabulary for browser-facing state, projections, domain identity, and actions
- [Named Entities and Canonical Naming](named-entities.md): target architecture and roadmap for display names, localized labels, observed names, aliases, canonical refs, and NLU entity canonicalization
- [AdaOS Builder](builder.md): canonical role and end-to-end workflow for turning ideas into governed skills, scenarios, UI descriptors, NLU hints, tests, and runtime-ready changes
- [Builder Conversational Development Architecture](builder-conversational-development.md): chat-first, state-backed development control plane; Project/Issue/Change/Run model; statechart; context packets; semantic UI/data changes; negotiated interactions; rich views; and multi-user proposal seams
- [Executable Prototype Architecture](executable-prototype-architecture.md): target model for executable UI/data prototypes, local CRUD and provider/model mocks, semantic composition context, the constrained conversational workflow MVP, Automation requirement handoff, and explicitly deferred general workflow round trips
- [Builder Roadmap](builder-roadmap.md): cross-cutting roadmap for the Builder vertical slice across Root MCP, NLU Teacher, skill/scenario runtime, validation, approval, activation, and repair
- [Skill Factory and Isolated Dev Nodes](skill-factory.md): target architecture and roadmap for remote realization through Root-managed dev queues, isolated AdaOS dev nodes, task-scoped MCP, forge branches, Codex execution packets, and User Hub validation
- [Pending Actions](pending-actions.md): target core plane for durable human-in-the-loop responses, node-aware response routing, localization, and separation from notifications
- [Conversation and Channel Architecture](conversation-and-channel-architecture.md): target architecture for conversation identity, skill-owned chats, transport-independent context, Builder chat isolation, SDK APIs, and migration checklist
- [Web UI Architecture](web-ui-architecture.md): target stable browser-client architecture over `webui.v1`, semantic views, typed actions, Taiga renderers, and Ionic shell concerns
- [Version Observability](version-observability.md): source, served, target, used, and active-registry version planes across AdaOS core, Root, client, ReDevice, skills, and scenarios
- [Operational Event Model](operational-event-model.md): target event, demand, lifecycle, and Yjs materialization contract for browser-facing projections
- [Operational Event Model Reference Plan](operational-event-model-reference-plan.md): top-level coverage gates, required contract shapes, review checklist, and completion definition for implementing the event model correctly
- [Operational Event Model Roadmap](operational-event-model-roadmap.md): master implementation order across communication, runtime contracts, Yjs shape, client adapters, platform emitters, and skill pilots
- [Roadmap Inventory and Authority Map](roadmap-inventory.md): ownership rules and index for cross-domain, MVP, domain, and execution planning
- [Model Runtime and Registry](model-runtime-and-registry.md): target model execution, artifact registry, local/remote backend, session, and job architecture for neural and external model-backed skills
- [Model Runtime Roadmap](model-runtime-roadmap.md): implementation checklist for landing core model infrastructure first, then migrating Neural NLU and face vision pilots
- [AdaOS Research Fabric](research-fabric.md): target governed research framework, storage/tracker/executor boundaries, MLflow and Ray integration, evidence model, and TLP reference case
- [Research Fabric Roadmap](research-fabric-roadmap.md): prioritized delivery and proof gates from a local research kernel through storage capability, MLflow, Ray, TLP, generalization, and the deferred aResearcher assistance layer
- [Projection Subscription Roadmap](projection-subscription-roadmap.md): priority checklist for moving skills and scenarios to demand-driven per-webspace projections
- [Skill Projection and Stream Boundary](skill-projection-and-stream-boundary.md): current stabilization status and target roadmap for skill-owned Yjs projections, stream data, node-aware addressing, and temporary per-skill bridges
- [Skill Projection Runtime SDK](skill-projection-runtime-sdk.md): target SDK/core rails for projection slots, stream receivers, dirty routing, fingerprinted Yjs writes, and skill migration checklists
- [Root MCP Foundation](root-mcp-foundation.md): root-hosted agent-facing foundation for future MCP development and operations surfaces
- [Root MCP Roadmap](root-mcp-roadmap.md): sequencing for planes, descriptor cache, session leases, and companion slices such as `ProfileOps`
- [AdaOS Supervisor](adaos-supervisor.md): local always-on process and update supervision authority above the restartable runtime
- [Runtime Guarding](runtime-guarding.md): target shared guard architecture and roadmap for memory, CPU, Yjs pressure, HTTP health, skill overload, quarantine, supervisor hard safety, and diagnostic snapshots
- [Realtime Rebuild Lag Hardening](realtime-rebuild-lag-hardening.md): implemented coalescing, deferral, activation-admission, YStore, and event-loop lag diagnostics for rebuild and stream snapshot storms
- [Incident Registry](incident-registry.md): production direction for normalizing transport, runtime, skill, sync, and pressure symptoms into domain-attributed incidents for humans and LLM planning
- [Member-Hub Connectivity](member-hub-connectivity.md): target control-plane architecture for member join, member-hub lifecycle ownership, restart-aware health semantics, and QR onboarding
- [Hub-Browser Connectivity](hub-browser-connectivity.md): target guarantees, protocol ladder, quality gates, and implementation checklist for local and root-routed browser links to a hub
- [Browser-Hub Lifecycle Authority](browser-hub-lifecycle.md): authoritative Root lifecycle lease, sidecar diagnostic bridge, browser event-wait policy, capability gates, and runtime-owned WebRTC/YWS boundary
- [Device Access and Browsers](device-access-and-browsers.md): target architecture for durable device identity, browser and member access policy, device-centric desktop UX, and reusable access management surfaces
- [Personalization, Identity, and Access](personalization-identity-access.md): target local-first model for profiles, user keys, devices, memberships, roles, capabilities, QR/link join flows, privacy zones, and audit
- [Personalization, Identity, and Access Roadmap](personalization-identity-access-roadmap.md): MoSCoW checklist for landing backend slices, current-user browser settings, AdaOS Connect join UX, user management, grants, recovery, privacy, and optional external identity
- [Personalization Phase 0 Contracts](personalization-identity-access-phase0-contracts.md): implemented draft contract anchor for scope lattice, versioned schemas, migration stance, threat model, audit, and regression matrix
- [Personalization Phase 1 Access Kernel](personalization-identity-access-phase1-kernel.md): implemented backend store, policy decision, revocation, replay-guard, and audit kernel for Phase 1
- [Personalization Phase 2 Profile and Preferences](personalization-identity-access-phase2-profile-preferences.md): implemented service/SDK slice for profile/preferences, SDK compatibility, header-settings model, projection, and redacted audit; browser settings UI lands in Phase 4
- [Personalization Phase 3 Guest Join and Targeted Invites](personalization-identity-access-phase3-join-invites.md): implemented backend public guest join, targeted invite, consent preview, binding, revoke, and cutoff hooks; AdaOS Connect/Join Browser UI lands in Phase 5
- [Personalization Phase 4 Current-User Settings API and Browser UI](personalization-identity-access-phase4-current-user-ui.md): implemented runtime API and browser header/settings panel for current-user profile/preferences with role/membership read-only
- [Personalization Phase 5 AdaOS Connect Join UX and Link Management](personalization-identity-access-phase5-connect-join-ux.md): implemented browser/API vertical slice for guest links, targeted invites, public preview/claim, link listing, and access-link revocation
- [Endpoint Infrastructure](endpoint-infrastructure.md): target architecture for ReDevice/browser endpoints, endpoint registry, assignments, router-owned commands, events, streams, and the Yjs boundary
- [Endpoint Audio Service](endpoint-audio-service.md): target architecture for endpoint audio sessions, activation, STT routing, Bluetooth audio, dialog/dictation modes, and audio transport policy
- [Device Access Roadmap](device-access-roadmap.md): recommended migration order from bootstrap-only links and ad hoc UI actions to a shared access-link control plane
- [Post-Deploy E2E Testing](post-deploy-e2e-testing.md): target post-deploy browser E2E architecture, evidence bundle model, runtime-contract checks, and roadmap toward rollout gates
- [Semantic State Plane](semantic-state-plane.md): target kernel architecture for separating connectivity, shared-state sync freshness, and Yjs pressure governance without adding redundant status entities
- [Webspace Scenario Pointer/Projection Roadmap](webspace-scenario-pointer-projection-roadmap.md): target architecture and migration checklist for moving scenario switching from materialize-and-copy to pointer-first semantic rebuild
- [Skill Assets and Icons Roadmap](skill-assets-and-icons-roadmap.md): roadmap for loading skill-owned icons and resources without recompiling the browser client
