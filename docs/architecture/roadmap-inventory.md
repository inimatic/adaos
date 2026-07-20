# Roadmap Inventory and Authority Map

This page is the routing table for AdaOS planning documentation. It identifies
where direction, sequencing, active execution, and evidence belong. It is not a
roadmap and intentionally contains no implementation-status checklist.

English documents are the primary planning surface. Russian documents and
older concept notes may provide useful context, but they do not override the
owners listed here.

## Planning Authority

When documents disagree, use the narrowest authoritative source in this table.

| Planning surface | Owns | Does not own |
| --- | --- | --- |
| [Governed Evolution](governed-evolution.md) | Long-term roles, invariants, value flow, and boundaries across domains | Delivery dates or domain implementation details |
| [Governed Evolution Roadmap](governed-evolution-roadmap.md) | Cross-domain milestones, ordering constraints, and proof gates | Duplicated technical task lists |
| [MVP Roadmap](../mvp_roadmap.md) | Current repository-wide MVP completion target and release gates | Long-term product direction |
| Domain architecture documents | Contracts, invariants, ownership boundaries, and target design for one domain | Active task priority |
| Domain roadmaps | Technical sequence, acceptance criteria, and maturity within one domain | Cross-domain product priority |
| This inventory | Discovery and ownership mapping | Status, priority, or completion claims |
| [Issue Tracker](../issue-tracker.md) | Active execution records, incidents, evidence links, and concrete follow-up | Target architecture or roadmap policy |
| [Historical Roadmap](../roadmap.md) | Historical context from autumn 2025 | Any current planning or completion claim |

## Conflict Resolution

1. Governed Evolution selects the long-term direction; its roadmap selects the
   cross-domain milestone and proof gate.
2. The MVP Roadmap selects the current repository-wide completion target.
3. A domain architecture document owns its contracts and invariants. A domain
   roadmap owns implementation order and completion evidence inside that scope.
4. The Issue Tracker may schedule work, but cannot redefine architecture or
   mark a roadmap gate complete without the evidence required by its owner.
5. A checked item records only the maturity explicitly stated by its owning
   roadmap. It does not implicitly mean production acceptance.
6. If two domain roadmaps overlap and neither declares the boundary, record the
   ownership gap here before adding another checklist.

## Roadmap Vocabulary

Active roadmaps should use the shared priority tags consistently:

- `[must]`: blocks the proof gate of the current milestone;
- `[should]`: required before broad or repeated use;
- `[could]`: valuable but non-blocking experiment;
- `[deferred]`: intentionally postponed until a named milestone or condition.

Priority is separate from maturity. Where maturity matters, use the progression
defined by the Governed Evolution Roadmap and attach evidence to the claimed
state.

## Domain Directory

The architecture owner defines the domain. The sequencing owner determines the
order of work. Execution and evidence remain in the Issue Tracker, tests, and
the acceptance records required by the owning roadmap.

| Domain | Architecture owner | Sequencing / roadmap owner | Execution / evidence surface |
| --- | --- | --- | --- |
| Runtime, skills, scenarios, install lifecycle | [Skill Runtime Lifecycle](../skill_runtime.md), [Skills](../skills.md), [Scenarios](../scenarios.md) | [Registry, Marketplace, and Operations Roadmap](registry-marketplace-operations-roadmap.md) | Issue Tracker; lifecycle and operations tests; release records |
| Governed capability development | [AdaOS Builder](builder.md), [Builder SDK Boundary](builder-sdk-boundary.md) | [Builder Roadmap](builder-roadmap.md), [Skill Factory](skill-factory.md) | Builder acceptance evidence; isolated DEV runs; Git and publication records |
| Root MCP and agent-facing governance | [Root MCP Foundation](root-mcp-foundation.md), [SDK Control Plane](../sdk_control_plane.md) | [Root MCP Roadmap](root-mcp-roadmap.md) | MCP contract tests; audit records; Issue Tracker |
| Events, projections, status, and Yjs shape | [Operational Event Model](operational-event-model.md), [Skill Projection Runtime SDK](skill-projection-runtime-sdk.md), [Skill Projection and Stream Boundary](skill-projection-and-stream-boundary.md) | [Operational Event Model Roadmap](operational-event-model-roadmap.md), [Reference Plan](operational-event-model-reference-plan.md), [Projection Subscription Roadmap](projection-subscription-roadmap.md) | ABI tests; projection integration tests; stand evidence |
| Realtime transport, sidecar, supervisor, and media | [AdaOS Realtime Sidecar](adaos-realtime-sidecar.md), [AdaOS Supervisor](adaos-supervisor.md), [Transport Ownership](transport-ownership.md), [Channel Semantics](channel-semantics.md), [Authority and Degraded Mode](authority-and-degraded-mode.md) | [Realtime Reliability Roadmap](realtime-reliability-roadmap.md) | Reliability tests; soak and stand reports; supervisor attempt records |
| Conversations, channels, and pending responses | [Conversation and Channel Architecture](conversation-and-channel-architecture.md), [Channel Semantics](channel-semantics.md), [Pending Actions](pending-actions.md) | Conversation document migration checklist; related Builder milestones | Conversation and routing tests; Issue Tracker; end-to-end dialog evidence |
| Personalization, identity, access, and privacy | [Personalization, Identity, and Access](personalization-identity-access.md), [Device Access and Browsers](device-access-and-browsers.md) | [Personalization Roadmap](personalization-identity-access-roadmap.md), [Device Access Roadmap](device-access-roadmap.md) | Phase acceptance docs; policy and API tests; audit evidence |
| Named entities and canonical naming | [Named Entities and Canonical Naming](named-entities.md) | [Device Access Roadmap](device-access-roadmap.md) and [NLU Roadmap Checklist](../concepts/nlu-roadmap.md) within their scopes | Resolver tests; registry projection tests; ambiguity cases |
| NLU runtime and Teacher | [NLU Target Architecture](../concepts/nlu-target-architecture.md), [NLU Service Skills](../concepts/nlu-service-skills.md) | [NLU Roadmap Checklist](../concepts/nlu-roadmap.md), [NLU Teacher MVP](../concepts/nlu-teacher-llm.md) | NLU and Teacher tests; clarification cases; model evaluation evidence |
| Model runtime and artifact registry | [Model Runtime and Registry](model-runtime-and-registry.md) | [Model Runtime Roadmap](model-runtime-roadmap.md) | Artifact contract tests; backend integration evidence; Issue Tracker |
| Browser UI, webspaces, and semantic addressing | [Web UI Architecture](web-ui-architecture.md), [UI Addressing](ui-addressing.md) | [Webspace Scenario Pointer/Projection Roadmap](webspace-scenario-pointer-projection-roadmap.md), [Webspace Evolution Roadmap](webspace-evolution-roadmap.md) | Client tests; UI runtime diagnostics; browser E2E evidence |
| Security, onboarding, and mTLS | [Security](security.md), [Member-Hub Connectivity](member-hub-connectivity.md), [Device Access and Browsers](device-access-and-browsers.md) | [Device Access Roadmap](device-access-roadmap.md) | Security and join tests; threat-model evidence; onboarding acceptance |
| Observability, incidents, guarding, and post-deploy validation | [Incident Registry](incident-registry.md), [Runtime Guarding](runtime-guarding.md), [Post-Deploy E2E Testing](post-deploy-e2e-testing.md) | Roadmaps embedded in those architecture documents | Deterministic symptom checks; incident records; diagnostics; post-deploy evidence |
| Product terminology and human-facing control plane | [AdaOS Product Terminology](product-terminology.md), [Infrascope](infrascope.md) | [Infrascope Roadmap](infrascope-roadmap.md) | Contract and UI tests; terminology review; Issue Tracker |
| Devices, endpoints, and audio | [Endpoint Infrastructure](endpoint-infrastructure.md), [Endpoint Audio Service](endpoint-audio-service.md), [Device Access and Browsers](device-access-and-browsers.md) | [Device Access Roadmap](device-access-roadmap.md) plus endpoint-local checklists | Endpoint contract tests; routing diagnostics; device acceptance evidence |

## Supporting References

These pages are useful inputs but do not independently own cross-domain
sequence:

- [SDK IO](../sdk/io.md) and [WebIO](../interfaces/webio.md) describe public IO
  surfaces used by conversation, transport, and browser domains.
- [UI Runtime Diagnostics](ui-runtime-diagnostics.md) describes diagnostic
  interpretation, not feature priority.
- [Version Observability](version-observability.md) defines version planes used
  by Builder, runtime, and publication evidence.
- [Semantic State Plane](semantic-state-plane.md) defines connectivity, sync,
  and pressure semantics used by runtime and browser work.
- [Post-Deploy E2E Testing](post-deploy-e2e-testing.md) defines evidence that
  domain roadmaps may require at rollout gates.
- [LLM Skill Development](../guides/llm-skill-development.md) is an operational
  guide, not the authority for Builder or Skill Factory architecture.

## Where New Work Goes

Route a new item by the kind of decision it represents:

1. Put a durable role, boundary, or invariant in the owning architecture
   document.
2. Put a technical dependency, priority, acceptance criterion, or migration
   step in the owning domain roadmap.
3. Put a cross-domain outcome in the Governed Evolution Roadmap only when it is
   a milestone or proof gate, not merely a large technical task.
4. Put a concrete defect, request, investigation, or implementation run in the
   Issue Tracker and link it back to the owning roadmap item.
5. Put transient progress in the issue or automation record. Do not copy it to
   architecture pages.
6. Put test output, stand observations, publication records, migration reports,
   and production acceptance in an evidence artifact linked by the issue and
   owning roadmap.

If an item appears to belong to several domains, choose the owner of the
contract being changed. Other domain roadmaps reference that item as a
dependency rather than cloning it.

## Evidence Discipline

A roadmap checkbox is meaningful only when its scope and evidence are clear.
Prefer evidence that can be reproduced or independently inspected:

- automated contract, unit, integration, and end-to-end test results;
- immutable commit, release, publication, or migration identifiers;
- dated stand or production reports with environment identity;
- deterministic symptom-checker output and linked incident records;
- explicit human acceptance where policy, safety, credentials, or UX judgment
  requires it.

Local implementation, stand validation, and production acceptance are distinct
maturity claims. A later maturity must not be inferred from an earlier one.

## Known Ownership Gaps

Keep this section small. A gap belongs here only when no existing owner can
accept it without creating conflicting authority.

- Observability spans Incident Registry, Runtime Guarding, Root diagnostics,
  and post-deploy testing. A concise observability contract is still needed to
  define shared signal and evidence boundaries; it should reference those
  owners rather than duplicate their roadmaps.
- NLU sequencing currently lives under `docs/concepts/`. When it is next
  materially revised, promote or replace it with one clearly authoritative
  architecture roadmap and retain redirects for existing links.
- The two webspace roadmaps overlap. Their next revisions should state which
  owns semantic pointer migration and which owns broader client evolution.

## Historical and Archived Planning

- [Historical Roadmap](../roadmap.md) is a redirect retained for old links; its
  original autumn 2025 checklist remains in Git history.
- [LLM Skill Creation Roadmap](../llm-skill-creation-roadmap.md) is historical
  context. Builder Roadmap and Skill Factory own current autonomous-development
  sequencing.
- Dated audit findings and implementation snapshots should be recovered from
  Git history when needed, not copied back into this authority map.
- Concept and Russian-language documents remain explanatory unless this page
  explicitly names them as an architecture or sequencing owner.

## Maintenance Rules

- Keep exactly one architecture owner and one sequencing owner per scope. A
  single document may own both.
- Add new roadmap tasks to the owning domain roadmap; add active work and
  evidence to the Issue Tracker.
- Cross-domain roadmaps reference stable domain sections or task IDs instead of
  copying their checklists.
- Every completion claim links to the evidence required by its owning roadmap.
- Update this page only when ownership or document routing changes.
- Prefer redirects and Git history over retaining stale checklists in active
  navigation.
