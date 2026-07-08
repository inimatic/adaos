# Roadmap Inventory and Documentation Audit

Snapshot date: 2026-06-30.

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
- [WebIO](../interfaces/webio.md)

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

### Conversations, Channels, Skill Chats, and Messenger Integrations

Authoritative docs:

- [Conversation and Channel Architecture](conversation-and-channel-architecture.md)
- [Channel Semantics](channel-semantics.md)
- [SDK IO](../sdk/io.md)
- [AdaOS Builder](builder.md)
- [Builder Roadmap](builder-roadmap.md)
- [Pending Actions](pending-actions.md)

Code anchors:

- `src/adaos/services/chat_io/*`
- `src/adaos/services/router/service.py`
- `src/adaos/services/nlu/*`
- `src/adaos/integrations/telegram/*`
- `src/adaos/sdk/io/out.py`
- `src/adaos/integrations/adaos-client/src/app/renderer/*`

Current status:

- [x] Generic chat IO dataclasses and Telegram normalization exist.
- [x] Telegram text can be bridged into NLU with transport routing metadata.
- [x] Router projects SDK chat output into the current browser-visible chat
  path.
- [x] Target documentation now distinguishes transport channels, dialog
  channels, conversations, owners, initiators, and browser projections.
- [x] A first node-local `Conversation` service MVP exists for the Voice slice:
  SQLite conversations, dialog channels, agent registry, append-only messages,
  memory items, and turn traces.
- [ ] Conversation identity, thread identity, owner, surface, context policy,
  and routing policy schemas are not frozen.
- [ ] There is no first-class Dialog Runtime / Tracker yet; current text input
  still flows through Voice compatibility routing before the future
  conversation-owned lifecycle. The first durable turn trace envelope exists,
  but active frame, repair state, and response planner ownership are not frozen.
- [ ] Dialog act, task-frame/form, response envelope, and final turn trace
  schemas are not frozen.
- [ ] The node-local conversation/memory store still lacks FTS, segment
  summaries, delivery attempts, redaction/export flows, and strict
  policy-checked retrieval. The append-only ledger and memory item tables exist
  for the Voice slice.
- [ ] LLM Builder and skill runtimes do not yet receive budgeted context
  packets from a shared retrieval service.
- [ ] Skill/agent personalization is still early. Scoped memory item records
  now exist with consent/source/policy fields, but extraction, approval, search,
  and retrieval policies are not implemented.
- [x] There is a minimal active dialog-channel registry for the
  `conversation_companions` Voice pilot. It activates `conversational`, routes
  active Voice turns directly to the owner default tool before NLU/Teacher
  fallback, handles explicit exit/general commands, persists the selected
  channel in the node conversation store, restores it after API restart, and
  emits `dialog.channel.*` lifecycle events.
- [x] Router projects a compact browser-visible `data/dialog` snapshot for the
  Voice pilot, including current channel, owner, active agent, and projected
  memory scopes. The Voice widget can switch `general`/`conversational` via
  `dialog.channel.select` and displays the active agent.
- [x] The `general` channel now has a core-owned default agent identity
  (`agent:core:general`, displayed as `Ада` by default). Addressing it by name
  exits an active companion channel and routes the remaining text through the
  general Voice/NLU path.
- [x] Router now has a static pilot named-agent registry for the core general
  agent plus the `conversation_companions` agents. Addressing Arseni, Nika, or
  Mira by name from the Voice shell switches to `conversational` and delegates
  to the owning skill before NLU/Teacher fallback.
- [x] The companion pilot projects `gender`, `voice`, `voice_profile`, and
  `icon` hints through the dialog snapshot and chat messages. The browser chat
  uses those hints to label the active agent and choose an installed
  speech-synthesis voice when auto-speak is enabled.
- [x] The global app header now uses a compact Voice entry point with the
  active agent chip next to the listen button. Explicit `general` /
  `conversational` switching stays inside the Voice dialog surface.
- [x] The active dialog-channel registry is persisted for `general` and
  `conversational`; `builder` and future dynamic skill-owned channels remain
  pending.
- [x] Per-conversation durable visible history is implemented for the Voice
  slice. `Еще истории` pages from the node-local ledger by `conversation_id` /
  `dialog_channel_id`, survives API restart, and keeps Yjs/WebIO as only the
  compact active projection.
- [x] Voice input has a conservative pre-NLU autocorrection stage for common
  local typing/recognition errors, with the original text preserved in request
  diagnostics metadata.
- [x] The Voice toolbox has an initial read-only `Memory` inspector for the
  current agent/channel projection and node-store memory preview. Canonical
  memory editing, search, and policy-checked retrieval remain future work.
- [x] The Voice debug panel can now inspect the latest durable turn trace from
  `data.dialog.last_turn_trace`: selected channel, active agent, action target,
  routing reason, and renderer/materialization status.
- [x] The dispatcher now materializes a successful skill tool result `message`
  into the current Voice chat tail when the skill did not already publish a
  matching chat append.
- [ ] The dispatcher/conversation service does not yet provide the same
  guarantee for canonical conversations, typed content, and non-Voice dialog
  surfaces.
- [ ] A broad dialog-level golden conversation suite is still missing for
  Builder isolation, Teacher clarification, endpoint audio dialog mode, and
  long-context retrieval. The first companion Voice golden flow now covers
  start, follow-up, addressed agent switch, skill-owned profile correction, and
  return to `general`.
- [ ] Response planning is not centralized yet; skills and compatibility paths
  can still choose user-visible rendering directly.
- [x] Skill-owned pilot chats are declarable in the
  `conversation_companions` manifest. General manifest schema validation and
  marketplace-wide registration remain pending.
- [x] The SDK now exposes low-level `adaos.sdk.conversation` and
  `adaos.sdk.memory` facades for the first node-store slice.
- [ ] Builder does not yet own a separate conversation context.
- [ ] NLU Teacher clarification sessions are still chat-local rather than
  conversation-owned.
- [ ] Voice, Telegram, and browser chat still have compatibility paths where
  transport or route ids imply context.
- [ ] `voice_chat_skill` still contains semantic fallback behavior that should
  move to conversation owner/surface policies.

Developer-doc gap:

- LLM skill-development guidance must move from low-level chat/voice helpers to
  conversation-first examples so generated skills can create private skill
  chats without binding themselves to Telegram, voice, or browser transport.
- Skill-development guidance also needs examples for skill-initiated dialogs,
  core-governed initiator evidence, scoped memory, context packets, and when to
  use Pending Actions instead of chat questions.
- Documentation now needs to teach Dialog Runtime concepts explicitly: turn
  lifecycle, dialog acts, task frames/forms, repair states, response envelopes,
  and how NLU evidence differs from a final dialog decision.

### Personalization, Identity, Access, and Privacy

Authoritative docs:

- [Personalization, Identity, and Access](personalization-identity-access.md)
- [Personalization, Identity, and Access Roadmap](personalization-identity-access-roadmap.md)
- [Personalization Phase 0 Contracts](personalization-identity-access-phase0-contracts.md)
- [Personalization Phase 1 Access Kernel](personalization-identity-access-phase1-kernel.md)
- [Personalization Phase 2 Profile and Preferences](personalization-identity-access-phase2-profile-preferences.md)
- [Personalization Phase 3 Guest Join and Targeted Invites](personalization-identity-access-phase3-join-invites.md)
- [Personalization Phase 4 Current-User Settings API and Browser UI](personalization-identity-access-phase4-current-user-ui.md)
- [Personalization Phase 5 AdaOS Connect Join UX and Link Management](personalization-identity-access-phase5-connect-join-ux.md)
- [User and Scenario Personalization](../concepts/personalization.md)
- [Device Access and Browsers](device-access-and-browsers.md)
- [Pending Actions](pending-actions.md)
- [SDK IO](../sdk/io.md)

Code anchors:

- `src/adaos/services/user/profile.py`
- `src/adaos/domain/personalization_access.py`
- `src/adaos/services/personalization_access.py`
- `src/adaos/services/personalization_runtime.py`
- `src/adaos/apps/api/personalization.py`
- `src/adaos/sdk/data/profile.py`
- `src/adaos/sdk/data/ctx.py`
- `src/adaos/services/scenario/projection_registry.py`
- `src/adaos/services/scenario/projection_service.py`
- `src/adaos/services/conversation_store.py`
- `src/adaos/services/conversation_context.py`
- `src/adaos/services/access_links.py`
- `src/adaos/services/named_entities.py`
- `src/adaos/integrations/adaos-client/src/app/runtime/scoped-storage.service.ts`

Current status:

- [x] Phase 0 versioned contract anchor exists in
  `src/adaos/domain/personalization_access.py`, with scope lattice, role
  presets, capability vocabulary, join-flow contracts, data zones, audit record
  shape, migration sources, and security regression matrix.
- [x] MVP profile settings exist through `UserProfileService`, SDK helpers, KV
  storage, profile-changed events, and scenario projection rules.
- [x] Current-user profile settings can be projected through KV/Yjs targets for
  the `web_desktop` scenario.
- [x] Scoped conversation memory records include consent, retention, redaction,
  source metadata, and policy fields.
- [x] Memory write proposal flow exists through Pending Actions for generated
  skills that use the default template.
- [x] Browser/member access-link work covers device identity, lifetime,
  revocation, and first observability surfaces.
- [x] Named entities and aliases provide localized names for device/browser
  resolution and can be extended to profile-owned aliases.
- [x] Phase 1 access kernel exists in
  `src/adaos/services/personalization_access.py`: JSON-backed users/profile,
  user-key, device-key, session, membership, grant, invite, recovery,
  revocation, and audit facts; owner implicit admin; role-preset capability
  expansion; structured allow/deny decisions; session/device-aware evaluation;
  replay guards; and audit query helpers.
- [x] The existing `local-owner` baseline has a regression test for implicit
  owner admin without UI or skill-local state.
- [x] Phase 2 profile/preferences backend slice preserves the old settings SDK
  API, writes versioned profile/preference records, rejects role/membership
  profile fields, exposes current-user profile/preference/header helpers,
  projects preferences through KV, and emits redacted audit.
- [x] Phase 3 join/invite backend slice separates public guest joins from
  targeted invites, provides consent preview data, rejects profile-bound guest
  joins and wrong-scope/reused/expired material, issues backing
  grants/memberships, and bulk-revokes guest sessions through
  session/access-link cutoff hooks.
- [x] Phase 4 current-user settings API routes and browser header/settings UI
  adopt the Phase 1-2 services for profile/preference editing.
- [x] Phase 5 AdaOS Connect/browser shell UI adopts the Phase 3 services for
  guest links, targeted invite, preview/consent, link listing, and revocation
  UX.
- [ ] QR image rendering, profile picker/create UX, and audit-history
  drill-down remain open after Phase 5/7.
- [x] Direct websocket disconnect orchestration for revoked browser sessions
  now bridges `access_links.deny_link("browser", ...)` to the YJS gateway and
  closes active connections by device id or browser-session id.
- [ ] Runtime API/UI paths do not yet enforce the personalization access kernel
  globally.
- [x] Device pairing and admin-assisted recovery have Phase 6 backend/API
  foundations, tests, session revocation, and live browser/YJS cutoff.
- [ ] Optional WebAuthn/passkey authenticators, device key rotation hooks, and
  recovery codes remain open after Phase 6.
- [x] Phase 8 first slice classifies personalization data zones and gates
  profile/preference private-content reads through the access kernel; owner/admin
  paths get metadata-only profile views unless they are the subject user.
- [ ] Memory, conversation, projection, export/redaction, and product UI privacy
  zones remain open after the Phase 8 first slice.
- [x] User management has a shared owner/admin control-plane surface for
  users, profiles, grants/memberships, devices, sessions, invites, recovery,
  revocation, and audit metadata.
- [ ] Pending Actions for conversational admin requests and custom capability
  editing remain open after Phase 7 and are carried into Phase 9.
- [x] Phase 9 first slice extends `skill.yaml` validation with
  `personalization` declarations and policy capability checks against the
  access-kernel vocabulary.
- [ ] Runtime skill/tool invocation enforcement, SDK policy helpers, and
  sensitive tool denial paths remain open after the Phase 9 first slice.

Developer-doc gap:

- The new architecture page should become the vocabulary anchor for generated
  skills, user management, profile-aware UI work, and future auth-provider work.
- Skill-development docs need examples for declaring personalization usage,
  role-aware behavior, and required/optional capabilities in manifests.
- Product docs need to explain that root-server identity can verify a global
  key, but subnet membership and grants are local owner/admin decisions.

### Root MCP, Planes, and Agent-Facing Governance

Authoritative docs:

- [AdaOS Builder](builder.md)
- [Builder Roadmap](builder-roadmap.md)
- [Skill Factory and Isolated Dev Nodes](skill-factory.md)
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

- [x] `Builder` is the canonical role name for human, AI-assisted, or hybrid
  capability creation workflows.
- [x] Root MCP foundation skeleton exists.
- [x] Descriptor cache and `AdaOSDevPlane` exist.
- [x] Session leases exist.
- [x] `ProfileOps` read/control/audit paths exist.
- [x] `NLUAuthoringPlane` exposes read-only context, read-only phrase check,
  and governed device alias add/remove/deprecate writes.
- [x] NLU authoring handlers receive Root MCP bearer/session scope and return
  target/subnet evidence in results.
- [x] Skill Factory target architecture is documented as the future remote
  realization layer for Builder work through Root dev queue, isolated dev
  nodes, task-scoped MCP, forge branches, Codex execution packets, and User
  Hub validation.
- [ ] `NLUTeacherRead`, `NLUTeacherDryRun`, and `NLUTeacherAuthor` capability
  profile names still need final freezing.
- [ ] Redaction policy for NLU authoring prompts/traces is not fully frozen.
- [x] `nlu_authoring.check_phrase` exists as the current read-only phrase check.
- [ ] Template list/get and safe template patch tools remain open.
- [ ] `SkillFactoryTaskPlane`, `realize_request`, Root dev queue, dev-node
  registry, task assignment, task-scoped forge credentials, and User Hub
  result validation are target-state only.
- [ ] Subnet historical reconstruction is still weaker than current snapshot
  inspection.
- [ ] End-to-end Builder draft/preview/apply/repair workflow is not yet a
  complete product surface.

Developer-doc gap:

- The current Root MCP maturity should be described as implemented planes plus
  incomplete historical observability, not as a generic "future MCP" idea.
- Builder should be treated as the source of truth for capability creation
  terminology. Domain docs should link back to [AdaOS Builder](builder.md)
  instead of introducing separate names such as "LLM programmer".
- Skill Factory should be described as a future remote realization layer, not
  as current Codex-in-VS-Code bridge functionality or as a runtime action
  gateway.

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
- [x] Teacher probe, lookup, example save, candidate apply/rollback, and dataset update
  backend APIs exist.
- [ ] Teacher request/thread and candidate lifecycle contracts still need to be
  frozen as the governing product contract.
- [ ] Teacher UI check phrase, trace, ranking, entity, and action preview are
  not complete.
- [ ] Stable template ids and stale-write fingerprints are not complete.
- [x] Root MCP read-only phrase check exists as `nlu_authoring.check_phrase`.
- [x] Teacher regex candidate tests cover repeatable `skill_action` and
  `interface_action` training loops with rollback.
- [ ] Root MCP safe template apply tools remain open.
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
- [WebIO](../interfaces/webio.md)

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
