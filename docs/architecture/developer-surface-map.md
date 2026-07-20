# Developer Surface Map

Use this map before changing a shared AdaOS surface. It routes a code change to
the documents that own its contract and delivery order; it does not create a
new planning authority. The [Roadmap Inventory](roadmap-inventory.md) remains
the authority map when scopes overlap.

| Code or product surface | Read before editing | Minimum verification |
| --- | --- | --- |
| Core runtime, skill activation, packaging, install, update | [Skill Runtime Lifecycle](../skill_runtime.md), [Registry, Marketplace, and Operations Roadmap](registry-marketplace-operations-roadmap.md) | lifecycle/operation tests; installed-artifact inspection |
| Events, ProjectionRecords, demand, dispatcher, Yjs materialization | [Operational Event Model](operational-event-model.md), [Operational Event Model Roadmap](operational-event-model-roadmap.md), [Projection Subscription Roadmap](projection-subscription-roadmap.md) | ABI, demand, dispatcher, materializer, diagnostics tests |
| Skill-facing projection or stream SDK | [Skill Projection Runtime SDK](skill-projection-runtime-sdk.md), [Skill Projection and Stream Boundary](skill-projection-and-stream-boundary.md) | SDK contract tests plus one real handler path |
| Browser page data, projection lifecycle, WebIO, Yjs observers | [Web UI Architecture](web-ui-architecture.md), [UI Addressing](ui-addressing.md), [Projection Subscription Roadmap](projection-subscription-roadmap.md) | focused Angular tests; browser build; reconnect/lifecycle check |
| Status, operations, notifications, runtime guards | [Runtime Guarding](runtime-guarding.md), [Operational Event Model](operational-event-model.md), [Observability Map](../monitoring/observability.md) | thin status/operator-truth tests; pressure and failure-path checks |
| Builder, Teacher, development workspaces | [Builder](builder.md), [Builder SDK Boundary](builder-sdk-boundary.md), [Builder Roadmap](builder-roadmap.md) | isolated DEV workflow, approval, audit, and publication evidence |
| Root MCP and agent control plane | [Root MCP Foundation](root-mcp-foundation.md), [Root MCP Roadmap](root-mcp-roadmap.md), [SDK Control Plane](../sdk_control_plane.md) | MCP contract, policy, audit, and denial tests |
| Conversations, channels, pending actions | [Conversation and Channel Architecture](conversation-and-channel-architecture.md), [Pending Actions](pending-actions.md), [Channel Semantics](channel-semantics.md) | routing, context, approval, reconnect, and ownership tests |
| Devices, browsers, identity, access, onboarding | [Device Access and Browsers](device-access-and-browsers.md), [Personalization, Identity, and Access](personalization-identity-access.md), [Security](security.md) | access-policy, pairing/join, revocation, and browser-session tests |
| Realtime sidecar, supervisor, routing, media | [AdaOS Realtime Sidecar](adaos-realtime-sidecar.md), [AdaOS Supervisor](adaos-supervisor.md), [Realtime Reliability Roadmap](realtime-reliability-roadmap.md) | reliability tests, bounded soak, recovery and stand evidence |
| NLU, named entities, model runtime | [NLU Target Architecture](../concepts/nlu-target-architecture.md), [Named Entities](named-entities.md), [Model Runtime and Registry](model-runtime-and-registry.md) | resolver/evaluation, artifact-contract, privacy, and fallback tests |

## Change Discipline

1. Identify the owner of the contract being changed, then the roadmap that
   owns sequencing.
2. Add active execution and dated results to the
   [Issue Tracker](../issue-tracker.md), not to a second roadmap.
3. Keep local implementation, stand validation, and production acceptance as
   separate maturity claims.
4. For a release or rollout gate, write evidence in the
   [MVP Release Evidence](mvp-release-evidence.md) shape and use
   [Post-Deploy E2E Testing](post-deploy-e2e-testing.md) for live targets.

