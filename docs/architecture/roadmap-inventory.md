# Roadmap Inventory and Documentation Audit

Snapshot date: 2026-05-27.

This page is the current cross-document inventory for AdaOS target
architecture, implementation roadmaps, and high-value documentation gaps. It is
not a replacement for the detailed architecture pages. Its purpose is to make
it clear that every major target area has:

- an authoritative design or roadmap source;
- a current implementation status;
- a checklist that can be used to avoid overstating progress.

Russian documents can be used as historical or explanatory context, but English
documents are the primary planning surface.

## Reading Rules

- `docs/architecture/operational-event-model-roadmap.md` is the master
  sequencing document for event, projection, browser/runtime, status, and
  heavy-skill migration work.
- `docs/issue-tracker.md` records active execution tasks, incidents, and
  acceptance evidence. It is useful for recent progress, but it should not be
  the only place where target architecture is discoverable.
- `docs/roadmap.md` is a historical high-level grouping from 2025. Keep it as
  context or archive it; do not use it as the active execution source.
- A checked item in a specialized roadmap means the current implementation
  slice exists. It does not always mean production-complete maturity.

## Coverage Matrix

### Runtime, Skills, Scenarios, and Install Lifecycle

Authoritative docs:

- [Skill Runtime Lifecycle](../skill_runtime.md)
- [Skills](../skills.md)
- [Scenarios](../scenarios.md)
- [Registry, Marketplace, and Operations Roadmap](registry-marketplace-operations-roadmap.md)

Code anchors:

- `src/adaos/services/skill/manager.py`
- `src/adaos/services/scenario/manager.py`
- `src/adaos/services/operations/manager.py`
- `src/adaos/apps/api/skills.py`
- `src/adaos/apps/api/scenarios.py`
- `src/adaos/apps/api/node_api.py`

Current status:

- [x] A/B skill runtime preparation and activation exist.
- [x] Scenario dependency bootstrap is treated as lifecycle work.
- [x] Install/update API paths can submit async operation records.
- [x] `runtime.operations` and `runtime.notifications` are projected into Yjs.
- [x] Completion/failure notifications mirror into existing desktop toasts.
- [ ] Operation state is still primarily in memory; durable operation recovery
  is not a completed contract.
- [ ] Marketplace read path is not yet a reusable catalog adapter.
- [ ] Registry sync is partially present for local workspace registries; shared
  remote catalog semantics still need tightening and tests.

Developer-doc gap:

- The operations service now exists and should be treated as current
  implementation, not only future architecture.

### Operational Event Model, Projections, Status Plane, and Yjs Shape

Authoritative docs:

- [Operational Event Model](operational-event-model.md)
- [Operational Event Model Reference Plan](operational-event-model-reference-plan.md)
- [Operational Event Model Roadmap](operational-event-model-roadmap.md)
- [Projection Subscription Roadmap](projection-subscription-roadmap.md)
- [Skill Projection Runtime SDK](skill-projection-runtime-sdk.md)
- [Skill Projection and Stream Boundary](skill-projection-and-stream-boundary.md)

Code anchors:

- `src/adaos/services/eventbus.py`
- `src/adaos/services/status/*`
- `src/adaos/sdk/status.py`
- `src/adaos/services/yjs/*`
- `src/adaos/apps/api/node_api.py`
- `src/adaos/abi/skill.schema.json`
- `src/adaos/integrations/adaos-client/src/app/runtime/*`

Current status:

- [x] Communication prerequisites for the current Event Model Phase 0 scope are
  closed.
- [x] Named-entity runtime ABI exists and is projected through
  `registry.named_entities`.
- [x] Status-card ABI exists: `StatusCard`, `StatusRegistry`, SDK publish
  helpers, guard cards, thin summary, ETags, and boundary diagnostics.
- [ ] Status-card population is not yet sufficient for the thin plane to be the
  operator source of truth during memory-profile/runtime-unavailable incidents.
- [x] `status` / `statusPlane` are rejected as data routes in manifest schema
  and docs.
- [ ] Minimal shared event envelope remains open.
- [ ] Core/skill refresh ownership split remains open.
- [ ] Canonical projection record shape remains open.
- [ ] Client subscription shape remains open.
- [ ] Shared dispatcher for demanded projection refresh remains open.
- [ ] Notifications and diagnostics are not fully migrated through the shared
  projection lifecycle contract.

Developer-doc gap:

- The status-card layer is implemented enough to be documented as the first
  platform-emitter slice. The remaining work is shared projection lifecycle
  adoption, not basic status-card contract definition.

### Realtime Reliability, Sidecar, Supervisor, and Media Routes

Authoritative docs:

- [Realtime Reliability Roadmap](realtime-reliability-roadmap.md)
- [AdaOS Realtime Sidecar](adaos-realtime-sidecar.md)
- [AdaOS Supervisor](adaos-supervisor.md)
- [Transport Ownership](transport-ownership.md)
- [Channel Semantics](channel-semantics.md)
- [Authority and Degraded Mode](authority-and-degraded-mode.md)
- [Routing](../concepts/routing.md)
- [SDK IO](../sdk/io.md)
- [WebIO](../io/webio.md)

Code anchors:

- `src/adaos/services/bootstrap.py`
- `src/adaos/services/reliability.py`
- `src/adaos/services/nats_ws_transport.py`
- `src/adaos/services/yjs/gateway_ws.py`
- `src/adaos/services/webrtc/*`
- `src/adaos/services/media_library.py`
- `src/adaos/services/capacity.py`
- `src/adaos/integrations/adaos-client/src/app/core/adaos/hub-member-channels.service.ts`
- `src/adaos/integrations/adaos-client/src/app/core/adaos/webrtc-transport.service.ts`

Current status:

- [x] Hub-root protocol hardening is complete for the current flow inventory.
- [x] Sidecar owns current transport-only `/ws` and `/yws` handoff scope.
- [ ] `.30` stand rollout/config for transport-only sidecar handoff was not
  accepted on 2026-05-28: live reliability reported sidecar disabled and Event
  Model Phase 0 communication `in_progress`.
- [x] Browser/member semantic channel ownership exists for the current scope.
- [x] WebRTC data paths for events/Yjs and media loopback are represented in
  client runtime diagnostics.
- [x] Router-owned media route contract is projected to `data.media.route`.
- [x] Member media capability advertisement through `capacity.io` exists.
- [ ] Full sidecar-owned Yjs room/session authority remains deferred.
- [ ] General multi-party media plane is not complete.
- [ ] Browser-member direct media admission/signaling still needs validation and
  hardening beyond the current route/capability contract.

Developer-doc gap:

- `media_library.py` and the media-player widget now make router-owned media
  routing visible to developers. The high-level docs cover the concept, but a
  short developer guide for media-route debugging would be useful.

### Root MCP, Planes, and Agent-Facing Governance

Authoritative docs:

- [Root MCP Foundation](root-mcp-foundation.md)
- [Root MCP Roadmap](root-mcp-roadmap.md)
- [SDK Control Plane](../sdk_control_plane.md)
- [LLM Skill Development](../guides/llm-skill-development.md)

Code anchors:

- `src/adaos/services/root_mcp/*`
- `src/adaos/apps/api/root_endpoints.py`
- `src/adaos/services/root/client.py`
- `src/adaos/sdk/control_plane.py`
- `src/adaos/sdk/data/control_plane.py`

Current status:

- [x] Root MCP foundation skeleton exists.
- [x] Descriptor cache and `AdaOSDevPlane` exist.
- [x] Session leases exist.
- [x] `ProfileOps` read/control/audit paths exist.
- [x] `NLUAuthoringPlane` exposes read-only context and governed device alias
  add/remove/deprecate writes.
- [ ] `NLUTeacherRead`, `NLUTeacherDryRun`, and `NLUTeacherAuthor` capability
  profile names still need final freezing.
- [ ] Redaction policy for NLU authoring prompts/traces is not fully frozen.
- [ ] `nlu.check_phrase`, template list/get, and safe template patch tools
  remain open.
- [ ] Subnet historical reconstruction is still weaker than current snapshot
  inspection.

Developer-doc gap:

- The current Root MCP maturity should be described as implemented planes plus
  incomplete historical observability, not as a generic "future MCP" idea.

### Named Entities, Device Identity, and NLU Canonicalization

Authoritative docs:

- [Named Entities and Canonical Naming](named-entities.md)
- [Device Access and Browsers](device-access-and-browsers.md)
- [Device Access Roadmap](device-access-roadmap.md)
- [NLU Roadmap Checklist](../concepts/nlu-roadmap.md)

Code anchors:

- `src/adaos/services/named_entities.py`
- `src/adaos/services/access_links.py`
- `src/adaos/services/device_inventory.py`
- `src/adaos/services/nlu/entity_resolver_runtime.py`
- `src/adaos/services/root_mcp/service.py`
- `src/adaos/sdk/data/entities.py`

Current status:

- [x] Canonical named-entity read model exists for devices, browsers, nodes,
  webspaces, scenarios, skills, apps, and modals.
- [x] Resolver traces include canonical refs, unresolved spans, and ambiguity
  evidence.
- [x] Runtime aliases do not require model retraining.
- [x] Governed device/browser alias add/remove/deprecate writes exist through
  SDK and Root MCP / NLUAuthoringPlane.
- [x] Lifecycle events exist for first authoritative device/browser sources.
- [ ] Profile-owned aliases remain open.
- [ ] Conflict-resolution UX remains open.
- [ ] Consumer migration away from ad hoc name fallback remains open.
- [ ] `EntityResolver` cache ownership still needs full
  `entity.registry.changed` invalidation handling.

Developer-doc gap:

- Developers need one short "use canonical refs, display labels are not routing
  keys" checklist linked from skill/scenario docs.

### NLU Runtime, Teacher, Neural, Rasa, and Neuro Lite

Authoritative docs:

- [NLU in AdaOS](../concepts/nlu.md)
- [NLU Roadmap Checklist](../concepts/nlu-roadmap.md)
- [NLU Teacher MVP](../concepts/nlu-teacher-llm.md)
- [NLU Target Architecture](../concepts/nlu-target-architecture.md)
- [NLU Service Skills](../concepts/nlu-service-skills.md)

Code anchors:

- `src/adaos/services/nlu/*`
- `src/adaos/apps/api/nlu_teacher_api.py`
- `.adaos/workspace/skills/neural_nlu_service_skill`
- `.adaos/workspace/skills/rasa_nlu_service_skill`
- `.adaos/workspace/skills/neuro_nlu_lite_skill`
- `.adaos/workspace/skills/voice_chat_skill`

Current status:

- [x] Regex-first pipeline exists.
- [x] Named-entity canonicalization feeds NLU traces and provider requests.
- [x] Rasa service-skill fallback exists.
- [x] Neural service-skill provider boundary, readiness, usage stats, reindex,
  curated rebuild, and diagnostics exist.
- [x] `neuro_nlu_lite_skill` exists as an experimental weak-device provider
  with a separate `neuro_lite` stage and runtime flag/policy.
- [x] Teacher probe, lookup, example save, candidate apply, and dataset update
  backend APIs exist.
- [ ] Teacher request/thread and candidate lifecycle contracts still need to be
  frozen as the governing product contract.
- [ ] Teacher UI check phrase, trace, ranking, entity, and action preview are
  not complete.
- [ ] Stable template ids and stale-write fingerprints are not complete.
- [ ] Root MCP phrase-check and safe template apply tools remain open.
- [ ] Full neural promotion gates using macro-F1, abstain rate, latency, and
  false-positive checks remain open.

Developer-doc gap:

- The NLU docs should consistently mention `neuro_nlu_lite_skill` as an
  experimental stage separate from the production Neural NLU provider.

### Model Runtime and Registry

Authoritative docs:

- [Model Runtime and Registry](model-runtime-and-registry.md)
- [Model Runtime Roadmap](model-runtime-roadmap.md)

Code anchors:

- `src/adaos/services/models/artifacts.py`
- `src/adaos/sdk/data/models.py`
- `src/adaos/services/skill/manager.py`
- `src/adaos/services/root/client.py`
- `src/adaos/integrations/adaos-backend/backend/app.ts`
- `.adaos/workspace/skills/media_indexer_skill`
- `.adaos/workspace/skills/new_face_vision_skill`
- `.adaos/workspace/skills/neural_nlu_service_skill`

Current status:

- [x] Skill manifests can declare `models.artifacts.<key>`.
- [x] Install copies local declared artifacts into skill runtime
  `data/files/models`.
- [x] Skill push can upload changed non-private model artifacts to Root.
- [x] Root client supports current/previous manifest, upload, chunked upload,
  and download.
- [x] SDK helpers exist for upload, update-if-changed, current/previous info,
  and download.
- [ ] Shared `ModelRegistry` lookup by model id/capability is not complete.
- [ ] Shared dependency profiles and shared Python environments are not
  complete.
- [ ] `ctx.models.infer`, `ctx.models.session`, and model jobs are target-state
  only.
- [ ] Neural NLU and face vision are not migrated to a shared model runtime;
  they still own execution.
- [ ] `adaos models ...` CLI is not complete.

Developer-doc gap:

- The model roadmap must not say "0%" anymore. The artifact-control MVP exists,
  but the shared model runtime remains mostly open.

### UI Runtime, Webspace, and Browser Architecture

Authoritative docs:

- [Web UI Architecture](web-ui-architecture.md)
- [Webspace Scenario Pointer/Projection Roadmap](webspace-scenario-pointer-projection-roadmap.md)
- [Webspace Evolution Roadmap](webspace-evolution-roadmap.md)
- [UI Runtime Diagnostics](ui-runtime-diagnostics.md)
- [UI Addressing](ui-addressing.md)
- [WebIO](../io/webio.md)

Code anchors:

- `src/adaos/services/io_web/*`
- `src/adaos/services/yjs/*`
- `src/adaos/apps/api/node_api.py`
- `src/adaos/integrations/adaos-client/src/app/runtime/*`
- `src/adaos/integrations/adaos-client/src/app/renderer/*`

Current status:

- [x] Yjs-backed webspaces and desktop runtime exist.
- [x] Pointer/projection work has materialized several current compatibility
  paths.
- [x] UI runtime diagnostics ingest exists.
- [x] Browser page runtime consumes communication/materialization/reliability
  transforms.
- [ ] Final projection record/subscription shapes remain open.
- [ ] Widget/panel/modal projection consumption is still transitional.
- [ ] Legacy compatibility branches still need cleanup after shared projection
  ABI adoption.

Developer-doc gap:

- Developers need a compact "which branch owns which Yjs data" guide that
  links to the projection roadmap and WebIO docs.

### Security, Access, Onboarding, and mTLS

Authoritative docs:

- [Security](security.md)
- [Join-codes and mTLS Notes](../security/join-code-and-mtls.md)
- [Member Node Onboarding](../onboarding/member-node-phase1.md)
- [Browser and Member](../onboarding/browser-and-member.md)
- [Member-Hub Connectivity](member-hub-connectivity.md)
- [Device Access Roadmap](device-access-roadmap.md)

Code anchors:

- `src/adaos/apps/api/join_api.py`
- `src/adaos/apps/api/subnet_api.py`
- `src/adaos/services/access_links.py`
- `src/adaos/services/node_config.py`
- `src/adaos/services/policy/*`

Current status:

- [x] Join-code based member onboarding exists.
- [x] Browser/member access links and device identity are represented.
- [x] Root-issued leases exist for MCP surfaces.
- [ ] Full mTLS provisioning/rotation remains follow-on work.
- [ ] Browser/device detach immediate logout and shared access settings UX
  remain incomplete.
- [ ] Policy boundaries for profile-owned aliases and remote target routing
  remain open.

### Observability, Diagnostics, and Post-Deploy Testing

Authoritative docs:

- [Observability](../monitoring/observability.md)
- [Post-Deploy E2E Testing](post-deploy-e2e-testing.md)
- [Runtime Guarding](runtime-guarding.md)
- [Supervisor Update Attempts](../guides/supervisor-update-attempts.md)
- [360 log / Root MCP references in Root MCP Roadmap](root-mcp-roadmap.md)

Code anchors:

- `src/adaos/apps/api/observe_api.py`
- `src/adaos/services/diag360.py`
- `src/adaos/services/eventbus.py`
- `src/adaos/services/status/*`
- `src/adaos/services/hmg_incident_summary.py`
- `src/adaos/apps/api/node_api.py`

Current status:

- [x] Observe ingest/tail/stream endpoints exist.
- [x] Eventbus backlog and selected hot-topic guardrails exist.
- [x] Status-card boundary diagnostics expose oversized-card misuse.
- [x] Root MCP diagnostic snapshots and initial typed subnet timeline exist.
- [ ] Managed memory-profile policy and memory containment are not accepted on
  `.30`: the first 180-second polling soak triggered sampled-profile restart
  near the default small-machine threshold; a repeat with relaxed thresholds
  avoided restart but still showed active-runtime RSS growth from about
  `345 MiB` to about `850 MiB` and `infrastate/snapshot` timeouts. A high-water
  follow-up reached active runtime RSS about `3.07 GiB`; after the load stopped,
  a 15-minute idle tail did not release memory materially.
- [ ] Post-deploy browser E2E is still a roadmap, not a universal rollout gate.
- [ ] Full subnet historical reconstruction remains incomplete.

## Important Documentation Gaps Found

The following gaps are high-value and should be fixed before broad
restructuring:

- Operations service status was stale in the registry/marketplace roadmap.
- Model runtime roadmap understated the current artifact-control MVP.
- Operational Event Model roadmap understated completed status-card ABI work.
- NLU docs underrepresented the experimental `neuro_nlu_lite_skill` stage.
- Developer docs do not yet have one concise index from code surfaces to
  architecture docs.
- Some concept documents are historical target-state notes and need labels so a
  developer can distinguish "implemented now" from "design idea".

## Restructure Recommendation

Documentation should be restructured, but only lightly.

Recommended target shape:

1. Keep `docs/architecture/` as the authoritative target-state and roadmap
   area.
2. Add or keep one cross-roadmap index, this page, and link it from
   `architecture/index.md` and `docs/index.md`.
3. Move old high-level planning docs such as `docs/roadmap.md` into an
   `archive` or label them explicitly as historical.
4. Split active roadmaps from concept drafts:
   - active: files with phase checklists and status;
   - concepts: exploratory product/design notes that are not execution plans.
5. For every major target area, keep exactly one "source of sequencing" and let
   supporting documents link to it instead of duplicating priority order.
6. Add a short `docs/developer-map.md` later that maps common code areas to the
   architecture docs developers should read before editing them.

Do not do a large folder migration yet. The current problem is not primarily
file placement; it is status discoverability and duplicate roadmap authority.
