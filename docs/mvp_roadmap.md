# AdaOS MVP Roadmap

Status: active planning document.

Snapshot date: 2026-07-23.

This roadmap summarizes the remaining work to reach the current AdaOS MVP
definition. It does not replace the detailed architecture roadmaps. Its role is
to keep MVP sequencing visible across core runtime, skills, browser runtime,
conversations, endpoints, Root governance, and release evidence.

Use this document with:

- [Governed Evolution Roadmap](architecture/governed-evolution-roadmap.md) for
  the longer cross-domain sequence that begins with the governed runtime and
  continues beyond MVP.
- [Roadmap Inventory](architecture/roadmap-inventory.md) to find the
  authoritative architecture and sequencing owner for each domain.
- [Operational Event Model Roadmap](architecture/operational-event-model-roadmap.md)
  as the master delivery track for event, projection, browser/runtime, status,
  and heavy-skill migration work.
- [Issue Tracker](issue-tracker.md) for recent incidents, stand evidence, and
  acceptance notes.

The MVP roadmap owns the current repository-wide completion target. It does
not own long-term product direction, Support Agent/Issue architecture, trusted
multi-Builder collaboration, or marketplace/network claims.

## MVP Definition

AdaOS reaches MVP when a developer/operator can run a hub, attach browsers and
members/endpoints, install and update skills/scenarios, use the browser desktop,
run governed Builder/Teacher/dialog flows, and inspect/recover the runtime
without relying on skill-local projection hacks or hidden Yjs side effects.

The MVP is not a full production platform. It deliberately excludes broad
cross-skill cleanup, generalized model inference, full marketplace maturity,
multi-party realtime media, full sidecar-owned Yjs session authority, and
complete historical subnet reconstruction.

The required MVP properties are:

- Core owns runtime contracts, status/control truth, projection materialization,
  access checks, and operation lifecycle.
- Skills own domain behavior, declared UI/data surfaces, and bounded handlers.
- Browser runtime consumes shared projection, stream, status, and operation
  contracts instead of per-skill compatibility branches.
- Conversations, Builder, Teacher, and Pending Actions share durable context and
  human approval mechanics.
- Endpoints remain reachable across local, cloud-routed, and phone-oriented
  topologies through explicit transport profiles and policy, not through a
  single assumed hub-local route.
- Release candidates have repeatable local and stand evidence for memory,
  reconnect, update, install, browser, dialog, and endpoint paths.

## Progress Rules

- Mark a checklist item complete only when code, docs, and tests or stand
  evidence exist.
- Do not count deferred target-state items as MVP blockers unless this page
  marks them as required.
- Prefer shrinking transition shims before adding new product surfaces.
- Do not migrate a heavy skill just to make one surface look cleaner if the
  shared core contract underneath is still missing.

## Milestone M0: MVP Boundary And Evidence Rails

Goal: make the MVP target measurable before deeper migration work continues.

Checklist:

- [x] Keep this roadmap linked from the documentation home and MkDocs
  navigation.
- [x] Keep [Roadmap Inventory](architecture/roadmap-inventory.md) as the
  authority and routing map and avoid duplicating detailed checklists here.
- [x] Label old broad planning material such as [Roadmap](roadmap.md) as
  historical or secondary when it conflicts with current architecture roadmaps.
- [x] Add a short developer map from common code surfaces to required
  architecture docs before editing them.
- [x] Define one MVP release evidence bundle shape: commands, browser checks,
  stand checks, logs, metrics, and known residual risks.
- [x] Keep [Issue Tracker](issue-tracker.md) as the place for live stand
  evidence and incident closure.

Related docs:

- [Roadmap Inventory](architecture/roadmap-inventory.md)
- [Developer Surface Map](architecture/developer-surface-map.md)
- [MVP Release Evidence](architecture/mvp-release-evidence.md)
- [Post-Deploy E2E Testing](architecture/post-deploy-e2e-testing.md)
- [Runtime Guarding](architecture/runtime-guarding.md)
- [Version Observability](architecture/version-observability.md)

## Milestone M1: Runtime Projection Contract Freeze

Goal: remove the most risky transition gap between skills, core, Yjs, WebIO,
and the browser.

MVP outcome:

- Installed skills preserve declared projection metadata.
- Skill activation loads declarations before handlers publish data.
- Core provides the durable sync/async projection bridge.
- Browser surfaces consume shared projection records, lifecycle state, streams,
  and details routes.
- Skills no longer need handler-side fallback projection declarations for normal
  runtime operation.

Checklist:

- [x] Runtime packaging preserves `skill.yaml`, `webui.json`, and projection
  declarations for installed skill artifacts.
- [x] Skill activation loads `data_projections`, `data_routes`, and stream
  receiver metadata before tools, subscriptions, or startup refreshes run.
- [x] Add a first-class `ProjectionService.apply_sync(...)` or equivalent SDK
  bridge for short synchronous handlers.
- [x] Emit diagnostics when a skill returns success but no projection rule
  exists for the slot it attempted to publish.
- [x] Browser runtime consumes projection lifecycle states:
  `pending`, `refreshing`, `ready`, `stale`, and `error`.
- [x] Minimal notifications use the shared projection contract instead of only
  legacy desktop toast branches.
- [x] Keep arbitrary runtime `ProjectionRecord` write surfaces unavailable to
  browser/API callers.
- [x] Add lint or validation warnings for direct skill-owned browser projection
  writes outside the approved SDK path.

Closed local implementation checkpoint, 2026-07-23:

- synchronous handlers now enter the durable core bridge through
  `ProjectionService.apply_sync(...)`; an active event-loop thread must use the
  async API explicitly
- the browser has a first-class `kind: projection` data source backed by
  demanded `data/projectionRecords` and the five-state lifecycle contract
- `platform:notifications` is emitted by the core operations path, refreshed by
  the shared dispatcher, materialized into Yjs, and consumed by the browser;
  legacy notification branches remain compatibility mirrors
- deterministic local acceptance proves runtime and guard status cards plus
  notifications remain visible through the core-owned path without depending
  on `infrastate_skill`
- runtime staging now verifies byte-identical `skill.yaml` and `webui.json`
  artifacts, and activation/startup load projections, routes, and receiver
  policy before target handlers or lifecycle refreshes run
- successful skill publish attempts without a matching projection rule now
  produce bounded, operator-visible diagnostics with skill, scope, slot,
  webspace, payload size, and active-declaration state
- skill validation reports `projection.direct_yjs_write` for direct calls to
  write-capable core Yjs APIs while preserving read-only and SDK access paths
- the original closure recheck passed `80` focused Python tests, `99` focused Angular
  tests, the Angular production build, and strict English/Russian MkDocs builds
- the declaration/diagnostics closure adds `77` focused Python tests and strict
  MkDocs verification. These remain local code/test claims; deployed stand
  validation stays open under `MVP-STAND-001`.

Related docs:

- [Operational Event Model](architecture/operational-event-model.md)
- [Operational Event Model Roadmap](architecture/operational-event-model-roadmap.md)
- [Projection Subscription Roadmap](architecture/projection-subscription-roadmap.md)
- [Skill Projection and Stream Boundary](architecture/skill-projection-and-stream-boundary.md)
- [Skill Projection Runtime SDK](architecture/skill-projection-runtime-sdk.md)
- [WebIO](interfaces/webio.md)
- [Skills](skills.md)

## Milestone M2: Operator Truth Plane

Goal: make status/control communication reliable even when an operational skill
is noisy, blocked, or quarantined.

MVP outcome:

- Runtime, update, Yjs, route, operation, member, and guard state can be read
  from compact core-owned status/control surfaces.
- `infrastate_skill` and similar skills render this truth; they do not own its
  delivery.
- Long-running operations survive reconnect and expose enough recovery evidence
  after runtime restarts.

Checklist:

- [ ] Populate status cards for runtime readiness, core update, active slot,
  route/realtime, Yjs/state sync, operation state, member links, and guard
  pressure.
- [ ] Keep critical status/control subscriptions inside a bounded control-plane
  budget when normal skill communication is guarded.
- [ ] Make thin reliability/status summaries sufficient for first operator
  diagnosis without mandatory full `infrastate/snapshot` reads.
- [ ] Persist accepted/running operations enough to recover as
  completed/failed/recoverable after API restart.
- [ ] Define cancellation, retry, and operator recovery policy for operations.
- [ ] Mirror operation notifications through the new notification/projection
  path while keeping legacy toasts only as compatibility.
- [ ] Add acceptance evidence that a noisy or quarantined `infrastate_skill`
  cannot hide active slot, update, Yjs, or member-status truth.

Related docs:

- [Registry, Marketplace, and Operations Roadmap](architecture/registry-marketplace-operations-roadmap.md)
- [Skill Projection Runtime SDK](architecture/skill-projection-runtime-sdk.md)
- [Runtime Guarding](architecture/runtime-guarding.md)
- [AdaOS Supervisor](architecture/adaos-supervisor.md)
- [Client Availability Status](architecture/client-availability-status.md)
- [Observability](monitoring/observability.md)

## Milestone M3: Activation Service And Skill Migration Wave

Goal: make startup and runtime load proportional to active scenarios, active
clients, and explicit demand.

MVP outcome:

- The runtime distinguishes `loaded` from `active`.
- Lazy and on-demand skills stay cheap while inactive.
- Heavy browser-facing skills use shared projection/stream contracts instead of
  monolithic snapshots or local executors.

Checklist:

- [ ] Add a shared activation runtime that tracks loaded/active state per skill
  and relevant webspace.
- [ ] Centrally enforce `startup_allowed`, `background_refresh`, and
  `client_presence` from `skill.runtime.activation`.
- [ ] Keep inactive lazy/on-demand handlers cheap: no broad git, filesystem,
  repository, YDoc, or inventory refresh work.
- [ ] Migrate `browsers_skill` as the reference compact projection plus stream
  implementation.
- [ ] Migrate `infrastate_skill` from remaining broad snapshots to compact
  status/control plus stream/details receivers.
- [ ] Migrate `infrascope_skill` object details to active demand with lifecycle
  and byte-size diagnostics.
- [ ] Remove per-skill projection executors after the core sync bridge is
  accepted.
- [ ] Remove handler-side fallback projection declarations after manifest
  preservation and activation loading are accepted.
- [ ] Clean up recursive or stale node-prefixed modal ids in existing Yjs
  documents.

Related docs:

- [Skill Activation And Scenario Binding](architecture/skill-activation-and-scenario-binding.md)
- [Skill Projection Runtime SDK](architecture/skill-projection-runtime-sdk.md)
- [Infrastate Data Route Plan](architecture/infrastate-data-route-plan.md)
- [Infrascope Roadmap](architecture/infrascope-roadmap.md)
- [Skill Runtime Lifecycle](skill_runtime.md)

## Milestone M4: Webspace And Browser Runtime Stabilization

Goal: make browser recovery, semantic rebuild, and progressive UI readiness
predictable.

MVP outcome:

- Webspace identity and home/current scenario semantics are explicit.
- Semantic rebuild is backend-owned and reusable.
- Browser resync and backend reload/reset mean different, documented things.
- The client can show partial readiness without treating every deferred surface
  as degraded.

Checklist:

- [ ] Keep `WebspaceManifest` metadata as the canonical source for kind,
  `home_scenario`, source mode, and desktop overlay boundaries.
- [ ] Use one backend semantic rebuild primitive for bootstrap, reload, reset,
  scenario switch, snapshot restore reconcile, and activation refresh.
- [ ] Keep projection refresh an explicit ordered rebuild step.
- [ ] Publish rebuild/materialization lifecycle state for browser and CLI
  diagnostics.
- [ ] Clarify browser controls: YJS resync is transport recovery; YJS reload is
  semantic rebuild plus optional resync.
- [ ] Add phase-aware readiness for structure, focused interaction, deferred
  hydration, ready, and degraded states.
- [ ] Add browser E2E checks for login/attach, webspace switch, reload/reset,
  PWA profile mismatch, runtime debug export, and Yjs red/green interpretation.

Related docs:

- [Webspace Evolution Roadmap](architecture/webspace-evolution-roadmap.md)
- [Webspace Scenario Pointer/Projection Roadmap](architecture/webspace-scenario-pointer-projection-roadmap.md)
- [Web UI Architecture](architecture/web-ui-architecture.md)
- [UI Runtime Diagnostics](architecture/ui-runtime-diagnostics.md)
- [UI Addressing](architecture/ui-addressing.md)

## Milestone M5: Conversation, Builder, Teacher, And Pending Actions

Goal: finish the user-facing governance loop for dialog, generated changes,
training corrections, and deferred human decisions.

MVP outcome:

- Builder, NLU Teacher, and skill dialogs use durable conversation context.
- Pending Actions are the human decision mechanism, not notifications.
- Runtime action-risk gates exist outside Builder preview, not only inside it.
- Memory and retrieval have consent and policy visibility for the first
  reusable slices.

Checklist:

- [ ] Treat `builder_skill` as the reference conversation-native skill.
- [ ] Complete Builder apply/release/rollback evidence through Pending Actions.
- [ ] Keep Builder context packets as the only supported LLM context input for
  Builder runtime calls.
- [ ] Keep NLU Teacher clarification, candidate confirmation, and safe apply
  tied to `kind=teacher` conversations and source refs.
- [ ] Freeze NLU Teacher Root MCP capability names and redaction policy.
- [ ] Add safe template preview/apply with stable `template_id` and
  `base_fingerprint`.
- [ ] Enforce action-risk approval gates in runtime paths for filesystem,
  network, device-control, credential, destructive, and cross-node effects.
- [ ] Add consent grant/revoke records for reusable global, core, skill, and
  agent memory.
- [ ] Add policy inspection for the last turn: selected channel, owner, agent,
  action target, risk class, memory refs, fallback path, and denial reasons.
- [ ] Broaden golden conversation fixtures for Builder apply/reject, Teacher
  correction, no-input repair, memory consent, and long-context retrieval.

Related docs:

- [Conversation and Channel Architecture](architecture/conversation-and-channel-architecture.md)
- [Pending Actions](architecture/pending-actions.md)
- [AdaOS Builder](architecture/builder.md)
- [Builder Roadmap](architecture/builder-roadmap.md)
- [NLU in AdaOS](concepts/nlu.md)
- [NLU Teacher MVP](concepts/nlu-teacher-llm.md)
- [Root MCP Roadmap](architecture/root-mcp-roadmap.md)
- [SDK IO](sdk/io.md)
- [LLM Skill Development](guides/llm-skill-development.md)

## Milestone M6: Endpoint And Device Reachability Matrix

Goal: keep devices reachable across local hub, cloud/root-routed hub, and
phone-oriented topologies while moving commands/events toward generic endpoint
infrastructure.

This milestone intentionally avoids a narrow "hub-local only" interpretation.
Hub-local command/event polling is required for local deployments, but it must
not close off cloud hub, phone hub, legacy Android, or Root-routed operation
where direct hub access is unavailable.

MVP outcome:

- Endpoint policy advertises an ordered, observable transport profile.
- The runtime can route basic display/audio commands through the same endpoint
  router contract across local and cloud-routed deployments.
- Root/legacy polling remains a governed route in the transport ladder, not a
  hidden side channel.
- Skills target endpoint roles/services, not physical ReDevice transport
  details.

Checklist:

- [ ] Define the MVP endpoint topology matrix:
  local hub, root/cloud-routed hub, phone-oriented hub/control, legacy
  Root-polling endpoint, and direct LAN/media candidates.
- [ ] Add hub-local ReDevice command/event queue routes for local deployments.
- [ ] Keep Root-routed command/event polling available for cloud hub,
  phone-oriented, NAT-blocked, or legacy TLS-limited deployments.
- [ ] Record both local and root/legacy route availability in
  `EndpointPolicy` or transport profile, including fallback reason and limits.
- [ ] Make Android/ReDevice prefer the best available route from policy instead
  of hardcoding public Root or hub-local API assumptions.
- [ ] Store durable `EndpointAssignment` records with audit, conflict handling,
  node-qualified owner, role, service binding, and policy constraints.
- [ ] Promote display commands to generic `EndpointRouter` APIs:
  `display.text`, `display.card`, `display.choice`, `display.image`,
  `display.slideshow`, and `display.progress`.
- [ ] Promote audio basics to the endpoint audio service:
  `voice.prompt`, `audio.play_content`, basic input activation, response
  routing, and multi-endpoint arbitration.
- [ ] Ensure media/content URLs are endpoint-reachable per topology; local
  sidecar URLs, phone/cloud routes, and Root relay must be explicit candidates
  with byte limits and diagnostics.
- [ ] Keep ReDevice scenario skills on generic SDK/device-access APIs rather
  than direct transport calls.
- [ ] Add acceptance checks for a device reachable only through Root/cloud
  polling and a device reachable through hub-local routes.

Related docs:

- [Endpoint Infrastructure](architecture/endpoint-infrastructure.md)
- [Endpoint Audio Service](architecture/endpoint-audio-service.md)
- [Device Access and Browsers](architecture/device-access-and-browsers.md)
- [Device Access Roadmap](architecture/device-access-roadmap.md)
- [Channel Semantics](architecture/channel-semantics.md)
- [AdaOS Realtime Sidecar](architecture/adaos-realtime-sidecar.md)
- [Transport Ownership](architecture/transport-ownership.md)

## Milestone M7: Model And NLU Provider Baseline

Goal: keep model-backed NLU and vision pilots installable and inspectable
without trying to finish a generalized inference platform before MVP.

MVP outcome:

- Model artifact delivery is reliable enough for declared skill-owned model
  files.
- Heavy dependency strategy is explicit and operator-visible.
- NLU provider status is inspectable, and safe Teacher authoring does not
  mutate code or bypass governed apply paths.

Checklist:

- [ ] Enforce Root artifact retention for `current` and `previous` model slots.
- [ ] Add model manifest validation for capability, artifact, checksum, backend,
  and optional dependency profile metadata.
- [ ] Add the first shared dependency profile contracts for heavy ML stacks
  without forcing immediate migration of every model skill.
- [ ] Describe Neural NLU as an `intent-detection` provider in model/NLU
  diagnostics.
- [ ] Describe face vision uploaded artifacts through model metadata while
  preserving current skill behavior.
- [ ] Keep `ctx.models.infer`, model sessions, and model jobs as post-MVP
  target-state unless a pilot needs a smaller slice.
- [ ] Add NLU provider freshness/readiness diagnostics for regex, Rasa, neural,
  and neuro-lite stages.
- [ ] Add promotion gates for neural provider changes after deterministic NLU
  Teacher apply is safe.

Related docs:

- [Model Runtime and Registry](architecture/model-runtime-and-registry.md)
- [Model Runtime Roadmap](architecture/model-runtime-roadmap.md)
- [NLU Target Architecture](concepts/nlu-target-architecture.md)
- [NLU Roadmap Checklist](concepts/nlu-roadmap.md)
- [NLU Service Skills](concepts/nlu-service-skills.md)

## Milestone M8: Root Governance And Builder Tooling

Goal: make external agent/developer access governed, auditable, and useful
without requiring mature subnet-forensics completeness.

MVP outcome:

- Root MCP descriptors and session leases are usable for Builder/Codex-style
  work.
- Operational planes expose current status and bounded actions with audit.
- Known observability gaps are explicit, especially historical reconstruction.
- Remote Skill Factory / Isolated Dev Node realization has a Root-side
  foundation: normalized Builder realize requests, local dev queue,
  dev-node registry, task assignment/result contracts, and diagnostics.
  MVP still does not require an external dev-node pool, task-scoped
  credentials, or User Hub branch validation loop.

Checklist:

- [ ] Keep Root MCP descriptor cache freshness visible for architecture, SDK,
  schema, template, skill, and scenario descriptors.
- [ ] Add token/session UX for NLU Teacher authoring sessions.
- [ ] Expose list/revoke for NLU authoring sessions with audit.
- [ ] Publish `nlu.describe_pipeline`, template list/get, desktop registry
  lookup, skill tool descriptors, and training-target descriptors.
- [ ] Enrich typed subnet timeline with runtime switches, bounded log
  references, and route incident detail.
- [ ] Keep `adaosmcp` self-check explicit about degraded analysis channels.
- [ ] Ensure Builder draft/preview/apply/repair flow uses Root MCP descriptors
  and Pending Actions, not unguided direct mutation.
- [x] Keep the future Skill Factory boundary explicit: local Builder flows must
  remain compatible with `realize_request` task envelopes, task-scoped MCP, and
  forge task-branch evidence without depending on external dev nodes for MVP.

Related docs:

- [Root MCP Foundation](architecture/root-mcp-foundation.md)
- [Root MCP Roadmap](architecture/root-mcp-roadmap.md)
- [Skill Factory and Isolated Dev Nodes](architecture/skill-factory.md)
- [SDK Control Plane](sdk_control_plane.md)
- [AdaOS Builder](architecture/builder.md)
- [Builder Roadmap](architecture/builder-roadmap.md)

## Milestone M9: MVP Release Candidate Acceptance

Goal: prove the MVP behaves coherently under real runtime use.

Required evidence:

- one local developer run;
- one hub stand run;
- one browser-attached run;
- one update/install run;
- one conversation/Builder/Teacher/Pending Action run;
- one endpoint reachability run with at least one non-hub-local route;
- one memory/reconnect soak.

Checklist:

- [ ] Bootstrap a hub and verify `adaos node status`, `adaos node reliability`,
  and thin status summaries.
- [ ] Attach a browser and verify login/session state, Yjs local-doc signal,
  runtime debug export, and visible desktop readiness.
- [ ] Install or update one skill through async operation projection and verify
  post-restart recovery evidence.
- [ ] Switch or reload a webspace and verify semantic rebuild state plus browser
  resync behavior.
- [ ] Run a general Voice turn, a Builder review turn, a Teacher correction, and
  one Pending Action response.
- [ ] Run endpoint display/audio basics through EndpointRouter with route
  evidence.
- [ ] Run an endpoint case where hub-local direct routing is unavailable but a
  Root/cloud or legacy route remains usable.
- [ ] Run a reconnect/idle soak with browser attached and record RSS, Yjs owner
  pressure, stream pressure, route pressure, and operation/status freshness.
- [ ] Verify a noisy operational skill can be guarded or quarantined without
  hiding operator truth.
- [ ] Record residual risks and explicitly classify post-MVP items.

Related docs:

- [Issue Tracker](issue-tracker.md)
- [Post-Deploy E2E Testing](architecture/post-deploy-e2e-testing.md)
- [Runtime Guarding](architecture/runtime-guarding.md)
- [Observability](monitoring/observability.md)
- [Deployment](deployment.md)
- [Common Commands](operations/common-commands.md)

## Post-MVP Or Deferred

These are important, but they should not block MVP unless a current milestone
pulls in a smaller required slice.

- Full sidecar-owned Yjs room/session authority.
- General multi-party media plane.
- Full marketplace catalog UX and remote catalog adapter maturity.
- Broad cross-skill compatibility path deletion.
- Full SQL projection backend.
- Generalized `ctx.models.infer`, model sessions, model jobs, and OCI model
  distribution.
- Full subnet historical reconstruction and incident forensics.
- Complete profile-aware personalization and overlay precedence.
- iOS client and broad mobile-native feature parity.
- Full generated-skill marketplace/public ecosystem.
- Full remote Skill Factory with Root dev queue, isolated dev-node pool,
  task-scoped forge credentials, and automatic remote realization.
