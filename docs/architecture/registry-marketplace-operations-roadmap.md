# Registry, Marketplace, and Operations Roadmap

Status: domain roadmap for registry, publication, installation, and durable
operation mechanics.

Last reviewed: 2026-08-18.

This roadmap owns those mechanics. Their position in the broader managed
deployment and verified-capability sequence is defined by
[Governed Evolution](governed-evolution.md) and the
[Governed Evolution Roadmap](governed-evolution-roadmap.md). ReDevice and other
activation paths are proof cases; they do not redefine registry semantics.

This note fixes the target architecture for four related tracks:

- registry synchronization for published Projects and component skills/scenarios
- product-first Catalog UX with advanced component inspection
- Project install/remove lifecycle over ProjectRelease and shared dependencies
- reusable long-running operation projection for hub-client interaction

It is intentionally evolutionary and tied to the AdaOS codebase as it exists today.

Implementation alignment (2026-07-24): the single-user artifact path now has
content-addressed scenario/skill packages, dependency-locked ProjectRelease
records, isolated candidates and trials, stable channel records, subscriptions,
and transactional WorkspaceLock activation. Backend PR
[inimatic/adaos-backend#1](https://github.com/inimatic/adaos-backend/pull/1)
is deployed as `0.1.137`, and live Forge tree lookup matches persisted source
trees. This closes the local contract and production-route slices, not the
default-route rollout or marketplace UX gates. A subsequent isolated clean
stand passed external package/release/channel round-trip and package-only
Workspace activation through deployed backend `0.1.142`. See
[Artifact Pipeline Local Evidence](artifact-pipeline-local-evidence-2026-07-24.md).

The governing rule is:

> Yjs is a live projection layer for clients, not the execution transport and not the source of truth for orchestration.

## Why This Note Exists

The current codebase already has most of the raw building blocks, but they are split across different layers:

- local workspace catalog snapshot helpers in [workspace_registry.py](/d:/git/adaos/src/adaos/services/workspace_registry.py)
- draft and publish flows in [service.py](/d:/git/adaos/src/adaos/services/root/service.py)
- synchronous install endpoints in [skills.py](/d:/git/adaos/src/adaos/apps/api/skills.py) and [scenarios.py](/d:/git/adaos/src/adaos/apps/api/scenarios.py)
- hub-only infrastructure snapshot and action endpoints in [node_api.py](/d:/git/adaos/src/adaos/apps/api/node_api.py)
- `InfrastateSkill` snapshot/action composition in [main.py](/d:/git/adaos/.adaos/workspace/skills/infrastate_skill/handlers/main.py)
- client modal, action, and notification plumbing in [page-modal.service.ts](/d:/git/adaos/src/adaos/integrations/adaos-client/src/app/runtime/page-modal.service.ts), [page-action.service.ts](/d:/git/adaos/src/adaos/integrations/adaos-client/src/app/runtime/page-action.service.ts), and [notification-log.service.ts](/d:/git/adaos/src/adaos/integrations/adaos-client/src/app/runtime/notification-log.service.ts)

What is missing is one coherent contract that connects them.

## Current Implementation Slice

Current MVP priority:

- `[must]` preserve runtime declarations during packaging and load them before
  activation side effects
- `[must]` make accepted/running operations durable and recoverable
- `[must]` expose core-owned inventory, lifecycle, scenario-health, and
  operation-detail contracts to UI, API, and MCP
- `[should]` finish git-mode policy, catalog/source/runtime drift
  classification, and member catalog snapshot sync
- `[deferred]` expand marketplace UX beyond the install/update surfaces needed
  to prove the MVP lifecycle
- `[should]` keep production runtime refresh slot-bound, including same-version
  source revisions, and restrict source-copy `runtime_update` to explicit
  dev/debug use; complete the atomic activation and live-reload stand proof

## What Already Exists

### Registry and publish path

- AdaOS already has a deterministic `registry.json` helper for a workspace in [workspace_registry.py](/d:/git/adaos/src/adaos/services/workspace_registry.py).
- The current shape is machine-readable and normalized as:
  - top-level `version`, `updated_at`, `skills`, `scenarios`
  - per-entry fields such as `kind`, `name`, `version`, `updated_at`, `path`, `manifest`, plus kind-specific metadata
- `upsert_workspace_registry_entry(...)` already gives us the right local abstraction for create-or-update semantics.
- Root publish flow already reuses that helper after publish-to-workspace in [service.py](/d:/git/adaos/src/adaos/services/root/service.py#L2370).

### Marketplace-adjacent UI

- `InfrastateSkill` already builds infrastructure-facing tables and actions from a snapshot model in [main.py](/d:/git/adaos/.adaos/workspace/skills/infrastate_skill/handlers/main.py).
- It already exposes `Update skills & scenarios` as an action row via `_update_actions(...)`.
- The client already supports:
  - static modal routing in [page-modal.service.ts](/d:/git/adaos/src/adaos/integrations/adaos-client/src/app/runtime/page-modal.service.ts)
  - schema-backed transient modals
  - host-action dispatch with HTTP fallback in [page-action.service.ts](/d:/git/adaos/src/adaos/integrations/adaos-client/src/app/runtime/page-action.service.ts)
  - local notification history plus Yjs-fed toasts in [notification-log.service.ts](/d:/git/adaos/src/adaos/integrations/adaos-client/src/app/runtime/notification-log.service.ts)

### Runtime and Yjs projection

- AdaOS already uses Yjs as the browser-visible runtime state and persists it through YStore snapshots.
- `node_api` already exposes a hub-only `infrastate/snapshot` and `infrastate/action` surface.
- `InfrastateSkill` already uses `skill_memory_*` for local UI state and supports background refresh, which is a useful precedent for non-blocking UI updates.
- Notifications already have a separation between transient UI display and persisted history, and Yjs already carries `data/desktop/toasts`.

## What Does Not Exist Yet

- a published `projects` collection and canonical `adaos.project.v1`
  normalization path
- independent machine profiles, localized catalog categories, free tags, and
  deployment scopes; those concepts must not be inferred from descriptions
- product-first Catalog entries/entry points that hide component composition
  from ordinary users while retaining an advanced component view
- complete Project-level install/remove and shared-dependency reference
  accounting in Catalog UX
- Project member role/exposure/lifecycle/relations and composition-locked
  ProjectRelease projection
- exact Project-to-Project release dependency locks
- a Project-level publish path; current `skill push`/`scenario push` remain
  component-first compatibility paths
- shared remote `adaos-registry` catalog semantics are still not fully
  normalized across every skill/scenario push path
- marketplace content is not yet modeled as a client-facing catalog adapter separate from raw `registry.json`
- complete client affordances for the core-owned cancellation and retry API are
  not yet wired into every operations UI

## Current Implementation Update: 2026-05-27

The operations part of this roadmap now has a first implementation slice:

- `src/adaos/services/operations/manager.py` defines `OperationManager`,
  `OperationState`, `OperationHandle`, and `OperationNotification`.
- `/api/skills/install`, `/api/scenarios/install`, `/api/scenarios/update`, and
  `node_api` infrastate actions can submit accepted async operations.
- active and recent operations are projected into Yjs under
  `runtime.operations`.
- operation notifications are projected under `runtime.notifications` and also
  mirrored into existing desktop toasts for compatibility.
- skill install can run through an isolated subprocess path; scenario
  install/update runs through bounded lifecycle phases and webspace rebuild.

The 2026-07-23 durability slice additionally stores bounded operation history
under runtime state, atomically rewrites it on every transition, restores
terminal history, and reclassifies interrupted active work as `recoverable`
with `retryable=true` and an operator notification. It never auto-retries an
unknown side effect after restart.

The 2026-07-24 activation slice extends that rule to Forge checkpoints,
permission admission, migrations, package materialization, runtime reload, and
health verification. Unknown outcomes require explicit one-shot
reconciliation. Builder Automation also uses one change identity per iteration;
a complete pre-commit checkpoint failure can be reconciled without rerunning
Codex, while a partially committed pair fails closed for manual recovery.

The same slice now exposes authenticated `GET /api/operations`, operation
detail, `POST /api/operations/{operation_id}/cancel`, and
`POST /api/operations/{operation_id}/retry`. Cancellation is deliberately
limited to isolated installer subprocesses, because cancelling an await on
`asyncio.to_thread` does not stop its side effect. Retry is limited to known
idempotent install/update kinds. Each retry records `retry_of` and `attempt`;
repeating retry against the same source operation returns the existing child
instead of launching duplicate work.

That means Phase 3 and the operation-projection part of Phase 4 are no longer
pure target-state work. The remaining gaps are stand restart evidence,
complete UI affordances, marketplace catalog binding, and registry-sync
normalization.

## Target Architecture

## 1. Registry Sync

`adaos-registry/registry.json` should be treated as a published catalog snapshot.

It should not become:

- a runtime state store
- an execution queue
- a per-client session artifact

The target contract is a single shared normalization/upsert path for Projects
and component skills/scenarios:

1. read artifact manifest
2. normalize into one catalog entry model
3. upsert by stable identity
4. write sorted deterministic `registry.json`

### Target component entry shape

The target entry should preserve current useful fields and converge on a richer stable model:

```json
{
  "kind": "skill",
  "id": "infrastate_skill",
  "name": "Infra State",
  "version": "1.4.0",
  "description": "Infrastructure and runtime control surface",
  "tags": ["infra", "ops"],
  "source": {
    "registry": "adaos-registry",
    "path": "skills/infrastate_skill",
    "manifest": "skills/infrastate_skill/skill.yaml"
  },
  "publisher": {
    "owner_id": "owner-123",
    "node_id": "hub-1"
  },
  "install": {
    "kind": "skill",
    "name": "infrastate_skill"
  },
  "updated_at": "2026-04-07T12:00:00+00:00"
}
```

Rules:

- reuse canonical manifest fields where they already exist
- keep deterministic ordering by `kind` then stable id/name
- preserve backward compatibility with current top-level `skills` and
  `scenarios` arrays while adding a `projects` collection
- introduce shared normalization code instead of separate skill/scenario serializers

### Target Project entry shape

```json
{
  "kind": "project",
  "id": "tlp_research_implementation",
  "version": "0.1.0",
  "profiles": ["adaos.research.implementation.v1"],
  "catalog": {
    "title": "TLP Research Implementation",
    "description": "Governed implementation for bounded TLP research tasks",
    "categories": ["research", "machine-learning"],
    "tags": ["tlp", "max-plus"]
  },
  "deployment": {
    "scopes": ["member"]
  },
  "entrypoints": [
    {
      "id": "research",
      "presentation": "scenario:research_workbench"
    }
  ],
  "release": {
    "project_release_digest": "sha256:...",
    "project_definition_digest": "sha256:...",
    "composition_digest": "sha256:..."
  },
  "install": {
    "kind": "project",
    "id": "tlp_research_implementation"
  }
}
```

The Project manifest is authoritative. `registry.json` is a deterministic
published projection and must not become Project runtime state. Profiles and
capabilities are machine contracts; catalog categories/tags are discovery
metadata; deployment scope is placement compatibility. See
[Project Composition, Presentation, and Development Context](project-composition-and-development-context.md).

Live ResearchDirections, conversations, task status, user pins/recent state,
and scientific evidence are not Project catalog entries. Their owning domain
may export snapshots or ResearchReleases that reference the immutable
ProjectRelease; registry sync must not infer them from Project names or copy
them into `registry.json`.

### Current anchors

- local registry helper: [workspace_registry.py](/d:/git/adaos/src/adaos/services/workspace_registry.py)
- push entrypoints: [dev.py](/d:/git/adaos/src/adaos/apps/cli/commands/dev.py)
- root push orchestration: [service.py](/d:/git/adaos/src/adaos/services/root/service.py)

### Recommended implementation shape

- extract a typed `registry catalog entry` normalizer from [workspace_registry.py](/d:/git/adaos/src/adaos/services/workspace_registry.py)
- reuse it both for local workspace registry and remote `adaos-registry` updates
- make Project publication, `push_skill`, and `push_scenario` call one shared
  `upsert_registry_catalog_entry(kind, source_dir, target_repo)` path after
  artifact/release upload succeeds

## 2. Catalog in InfrastateSkill

The Catalog should be modeled as a thin adapter over the registry catalog, not
as a direct UI binding to raw `registry.json`. Its default surface lists
Projects/Applications and their entry points. Skills, scenarios, providers,
capabilities, and dependency versions belong to an advanced Components view.

### Target flow

1. hub loads registry catalog from `adaos-registry`
2. adapter maps raw entries into UI-facing rows
3. Project rows are compared with installed ProjectRelease/WorkspaceLock state
4. `InfrastateSkill` exposes a `Catalog` action next to update operations
5. client opens Projects/Applications by default and Components in advanced mode
6. Project row action dispatches one Project install operation

### UI model

The UI-facing model should be small and explicit:

```json
{
  "kind": "project",
  "id": "tlp_research_implementation",
  "title": "TLP Research Implementation",
  "version": "0.1.0",
  "description": "Governed implementation for bounded TLP research tasks",
  "profiles": ["adaos.research.implementation.v1"],
  "categories": ["research", "machine-learning"],
  "tags": ["tlp", "max-plus"],
  "entrypoints": [{"id": "implementation-diagnostics", "title": "Open diagnostics"}],
  "publisher": "owner-123",
  "installed": false,
  "install_action": {
    "target": "infrastate.action",
    "id": "catalog_install",
    "value": {
      "target_kind": "project",
      "target_id": "tlp_research"
    }
  }
}
```

### Current anchors

- installed skills list: `_skills_items()` in [main.py](/d:/git/adaos/.adaos/workspace/skills/infrastate_skill/handlers/main.py)
- installed scenarios list: `_scenario_items()` in [main.py](/d:/git/adaos/.adaos/workspace/skills/infrastate_skill/handlers/main.py)
- modal infrastructure: [page-modal.service.ts](/d:/git/adaos/src/adaos/integrations/adaos-client/src/app/runtime/page-modal.service.ts)

### Recommended implementation shape

- add a small marketplace catalog service on the hub side
- have `InfrastateSkill` consume a UI-ready marketplace snapshot instead of parsing registry payload inline
- keep filtering logic as a pure function over:
  - catalog entries
  - installed ProjectRelease/WorkspaceLock identities
  - component inventories for advanced diagnostics

## 3. Long-Running Operations

The install/update architecture should follow this lifecycle:

1. client sends command
2. hub validates and accepts quickly
3. hub creates operation record
4. runtime executes work asynchronously
5. runtime updates canonical operation state
6. projection service mirrors that state into Yjs
7. UI reacts to Yjs only for visibility and affordances

The canonical execution state must stay in runtime memory and/or durable runtime storage, not in Yjs.

### Operation model

Core naming should stay reusable:

- `OperationManager`
- `OperationState`
- `OperationProjectionService`
- `OperationNotification`

Minimum operation fields:

```json
{
  "operation_id": "op_20260407_001",
  "kind": "skill.install",
  "target_kind": "skill",
  "target_id": "infrastate_skill",
  "status": "running",
  "progress": 40,
  "message": "Preparing runtime",
  "current_step": "runtime.prepare",
  "started_at": "2026-04-07T12:00:00+00:00",
  "updated_at": "2026-04-07T12:00:12+00:00",
  "finished_at": null,
  "result": null,
  "error": null,
  "initiator": {
    "kind": "user",
    "id": "local"
  },
  "scope": [
    "global",
    "skill.install",
    "skill:infrastate_skill"
  ],
  "can_cancel": false,
  "can_retry": false,
  "retry_of": null,
  "attempt": 1
}
```

### Current anchors

- synchronous install endpoints: [skills.py](/d:/git/adaos/src/adaos/apps/api/skills.py) and [scenarios.py](/d:/git/adaos/src/adaos/apps/api/scenarios.py)
- existing `infrastate.action` acceptance path: [node_api.py](/d:/git/adaos/src/adaos/apps/api/node_api.py)
- current non-blocking precedent: background refresh logic in [main.py](/d:/git/adaos/.adaos/workspace/skills/infrastate_skill/handlers/main.py)

### Recommended implementation shape

- add `src/adaos/services/operations/` with:
  - `model.py`
  - `manager.py`
  - `projection.py`
  - `notifications.py`
- keep API request handling thin:
  - create operation
  - schedule worker task
  - return `202 accepted` style payload with `operation_id`

## 4. Yjs Projection Shape

Yjs should expose a client-facing projection subtree with enough information for:

- button disabling
- progress display
- active operations list
- reconnect recovery
- recent completion notifications

### Target shape

```json
{
  "runtime": {
    "operations": {
      "by_id": {
        "op_20260407_001": {
          "operation_id": "op_20260407_001",
          "kind": "skill.install",
          "target_kind": "skill",
          "target_id": "infrastate_skill",
          "status": "running",
          "progress": 40,
          "message": "Preparing runtime",
          "current_step": "runtime.prepare",
          "scope": ["global", "skill.install", "skill:infrastate_skill"],
          "started_at": "...",
          "updated_at": "...",
          "finished_at": null
        }
      },
      "order": ["op_20260407_001"],
      "active": ["op_20260407_001"]
    },
    "notifications": [
      {
        "id": "notif_1",
        "level": "success",
        "message": "Skill infrastate_skill installed",
        "operation_id": "op_20260407_001",
        "target_kind": "skill",
        "target_id": "infrastate_skill",
        "ts": "..."
      }
    ]
  }
}
```

### Retention policy

- active operations stay projected while active
- finished operations remain in a bounded recent-history window
- notifications remain separate from operation state
- YStore snapshot persistence is enough for reconnect/reload recovery

### Current anchors

- Yjs persistence and room lifecycle: `src/adaos/services/yjs/*`
- current client-side Yjs toast ingestion: [notification-log.service.ts](/d:/git/adaos/src/adaos/integrations/adaos-client/src/app/runtime/notification-log.service.ts)

## 5. Notifications

Operation projection and notifications should stay separate.

Operation state answers:

- what is running
- what step is active
- whether a button must be disabled

Notification state answers:

- what should be shown to the user after completion or failure

For the current client, the simplest compatible path is:

- project operation notifications into `runtime.notifications`
- mirror completion/failure into existing `data/desktop/toasts` until the client is fully switched to the new subtree

That gives backward compatibility with current toast behavior while establishing the new contract.

## Phased Roadmap

## Phase 0: Fix Contracts

### Goal

Define stable contracts before wiring UI and background workers.

### Deliverables

- [ ] `[must]` shared catalog entry model for skill, scenario, and Project
  registry sync with backward-compatible component arrays
- [ ] `[must]` keep `kind`, profiles/capabilities, categories/tags, and
  deployment scope as separate validated fields
- [ ] `[must]` Project entry points/presentations resolve to catalog
  Applications without making a scenario or skill name the product identity
- [ ] `[must]` Project member schema separates role, Catalog exposure,
  bound/shared lifecycle, and semantic relations; `project_only` is a discovery
  rule rather than a security or package-integrity shortcut
- [ ] `[must]` ProjectRelease catalog identity includes the exact Project
  definition/composition digest and Project dependency locks, not only the set
  of component package digests
- [ ] `[must]` Project, ProjectRelease, ProjectInstallation, Builder
  DevelopmentSession, and domain aggregate ids remain distinct in APIs and
  projections
- [x] shared operation state model
- [x] Yjs projection schema for `runtime.operations` and `runtime.notifications`
- [x] explicit rule that Yjs is projection-only

### Current anchors

- [workspace_registry.py](/d:/git/adaos/src/adaos/services/workspace_registry.py)
- [node_api.py](/d:/git/adaos/src/adaos/apps/api/node_api.py)
- [main.py](/d:/git/adaos/.adaos/workspace/skills/infrastate_skill/handlers/main.py)

## Phase 1: Registry Sync

### Goal

Make Project publication plus `skill push` and `scenario push` update
`adaos-registry/registry.json` through one normalizer.

### Deliverables

- [ ] `[should]` shared upsert helper reused by Project, skill, and scenario
  entries across local and remote registry sync
- [ ] `[must]` publish deterministic Project entries from accepted
  ProjectRelease records without scanning live Builder sessions
- [ ] `[should]` add one Project publication SDK/CLI path and make existing
  component push commands backward-compatible one-component projections; do
  not add domain-specific publication CLIs
- [ ] `[should]` preserve raw component discovery for advanced tooling while
  making Project/Application the default catalog read model
- [ ] `[should]` tests for create/update behavior
- [x] deterministic local workspace registry output ordering

### Current anchors

- [service.py](/d:/git/adaos/src/adaos/services/root/service.py)
- [dev.py](/d:/git/adaos/src/adaos/apps/cli/commands/dev.py)

## Phase 2: Catalog Read Path

### Goal

Expose a product-first Catalog in `InfrastateSkill`.

### Deliverables

- [ ] `[deferred]` `Catalog` action next to `Update skills & scenarios`
- [ ] `[deferred]` catalog adapter service
- [ ] `[deferred]` default Projects/Applications view plus advanced
  skills/scenarios/components view
- [ ] `[deferred]` filtering against installed artifacts
- [ ] `[deferred]` filters that use profiles for semantic selection,
  categories/tags for discovery, and deployment scope for compatibility

## Phase 3: Async Install Operations

### Goal

Convert install/update flows from blocking request/response into accepted async operations.

### Deliverables

- [ ] `[must]` Project add/update/remove operations resolve one exact
  ProjectRelease, acquire the ordinary Workspace writer/operation leases, and
  use the transactional Artifact Pipeline rather than sequencing component
  install endpoints in the client
- [ ] `[must]` durable ProjectInstallation/reference accounting preserves
  shared dependencies, removes bound members only when unreferenced, and keeps
  runtime/domain data under declared retention policy
- [ ] `[must]` project-only members are materialized and verified as part of
  their Project but cannot be independently added/removed from ordinary UI
- [x] `OperationManager`
- [x] async install command handlers
- [x] `operation_id` response contract
- [x] operation projection into Yjs
- [x] durable recovery for accepted/running operations after runtime restart
- [x] `[must]` governed subprocess cancellation and idempotent retry policy
- [ ] `[must]` canary restart/cancel/retry evidence

## Phase 4: UI Binding and Notifications

### Goal

Make the client react to projected operations instead of waiting on request completion.

### Deliverables

- [ ] `[should]` disable install button for same target while active
- [ ] `[should]` present one Project/Application operation with expandable
  component/dependency plan instead of unrelated per-skill progress rows
- [x] show progress and current step through projected operation state
- [x] show active operations list in infra UI
- [x] show success/error notifications on completion
- [ ] `[deferred]` remove transitional per-skill active-operation mirrors after status-card
  and operation projection consumers are fully migrated

## Suggested File and Module Changes

Smallest coherent change set:

- `src/adaos/services/workspace_registry.py`
  - extract shared catalog-entry normalization so local and remote registry sync do not drift
- `src/adaos/services/root/service.py`
  - hook shared registry upsert into push flow for remote `adaos-registry`
- `src/adaos/apps/api/skills.py`
  - current install endpoint returns accepted operation metadata
- `src/adaos/apps/api/scenarios.py`
  - current install/update endpoints return accepted operation metadata
- `src/adaos/apps/api/node_api.py`
  - expose marketplace snapshot and operation-aware infrastate actions
- `src/adaos/services/operations/*`
  - new reusable operation runtime layer
- `.adaos/workspace/skills/infrastate_skill/handlers/main.py`
  - add product-first Catalog action/snapshot, advanced Components section, and operation projection consumption
- `src/adaos/integrations/adaos-client/src/app/runtime/page-modal.service.ts`
  - register marketplace modal
- `src/adaos/integrations/adaos-client/src/app/runtime/page-action.service.ts`
  - dispatch install commands and stop assuming immediate completion
- `src/adaos/integrations/adaos-client/src/app/runtime/notification-log.service.ts`
  - optionally read new `runtime.notifications` projection in addition to legacy toasts

## Backward Compatibility and Migration Risks

- Keep `registry.json` backward compatible by preserving `skills` and
  `scenarios` arrays while adding Project entries additively.
- Generate Project categories/profiles/deployment fields from canonical
  manifests; never infer machine semantics from description text.
- Keep existing sync install APIs working during migration; async operation mode can be introduced behind new response fields first.
- Keep existing `data/desktop/toasts` projection until the client fully consumes `runtime.notifications`.
- Keep `infrastate.action` as the UX entry point if desired, but route it internally to reusable operation services instead of embedding install-specific orchestration in the skill handler.

Main risk areas:

- letting registry schema drift between local workspace helper and shared registry publisher
- making Project and Application catalog identity collapse back into one
  scenario or skill id
- mixing machine profiles, UI categories, and deployment scope in one
  unvalidated tag list
- letting operation status become duplicated between runtime memory and Yjs
- making `InfrastateSkill` parse raw catalog payloads directly instead of using an adapter
- introducing install-specific naming that prevents later reuse for node update, diagnostics, model download, or migrations

## Architectural Decision Summary

AdaOS should evolve this area by reusing what already exists:

- local deterministic registry helpers
- hub-side `InfrastateSkill` snapshot/action composition
- client modal and notification plumbing
- Yjs persistence and reconnect behavior

But it should add two explicit layers that do not exist yet:

- a Project/Application catalog adapter over backward-compatible raw component
  entries and immutable ProjectRelease state
- a reusable operation runtime model whose state is projected into Yjs for visibility, never executed through it

That gives a coherent path from the current implementation to:

- registry-backed Project/Application Catalog discovery with advanced
  component inspection
- Project-level UI-triggered install/remove flows
- non-blocking hub operations
- reconnect-safe live progress and notifications

