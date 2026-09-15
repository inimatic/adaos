# Builder Preview Runtime

Status: paired scenario-preview contract with locally qualified slices.
Project presentation, deletion/reconnect recovery and end-to-end readiness
must retain their specific acceptance limits; this is not a blanket runtime
health claim. Current remaining work is tracked in
[BIP-03](builder-intent-to-prototype-roadmap.md#bip-03),
[BIP-22](builder-intent-to-prototype-roadmap.md#bip-22) and the parent
[Phase 12](builder-roadmap.md#phase-12-project-composition-and-scoped-development).

This document defines project selection, Builder preview ownership, and
webspace materialization. It replaces the former convention where a preview
was inferred by appending `-dev` and where one project event represented both
UI selection and artifact mutation.

## Invariants

- A Builder host and its preview are paired by a persisted relation, never by
  parsing either webspace ID.
- Selecting a project changes Builder context and only the paired preview. It
  does not reload the Builder host or scan unrelated DEV webspaces.
- A focused skill resolves an explicit Project entry point/presentation or the
  generic system skill-preview host. It never leaves an unrelated previous
  scenario as the apparent preview.
- A desired preview scenario is not reported as observed until reconcile has
  completed. Every new desired state has a monotonic generation and operation
  ID.
- YDoc-heavy materialization runs outside the API process in production. The
  API process remains the only owner that applies a returned snapshot to
  YStore or a live room.
- The workspace catalog is authoritative in SQLite and has a monotonic
  version. Compatibility projection to Yjs may update existing rooms but must
  never open every workspace document.

## Event Contracts

The canonical topics have one meaning each:

| Topic | Meaning | May rebuild preview |
| --- | --- | --- |
| `builder.context.selected` | Builder UI selected a skill or scenario | no |
| `builder.preview.desired` | paired preview should converge to an exact scenario/presentation target and bindings | yes, paired target only |
| `builder.preview.observed` | desired generation is now materialized | no; this is a fact |
| `project.content.changed` | project files or metadata changed | yes, explicit subscribers only |

`prompt.project.changed` remains a compatibility input. Reasons
`project_loaded`, `project_selected`, `builder_project_created`, and
`builder_project_switched` map to context selection; other reasons map to
content change. This matters for installed Builder versions that emit the
legacy selection event together with `builder.preview.selected`: the former
must not trigger a duplicate reload. New code must publish canonical topics
directly.

Skill and API page data sources derive their request identity from the
resolved target, parameters, body, current webspace, and referenced page
state. A state update reloads a source only when this identity changes.
Targeted invalidation remains available for content writes and is not needed
for ordinary selection.

## Explicit Topology

`webspace_relations` stores one outgoing preview relation per Builder host and
one incoming owner per preview. A registered production host `W` creates
`W-dev` on the first explicit Preview request and reuses it across applications,
Prototype and Automation. Only Builder running in `W-dev` can own
`W-dev-dev`. Naming is deterministic; persisted relationships and registered
manifest kinds, not suffix parsing, establish ownership. Existing opaque IDs
may be preserved during migration, but are never allocated for new previews.
Missing production parents and standalone DEV hosts are rejected before
relationship or runtime creation. Binding/dialog reads never allocate topology.

Two purposes are valid:

- `builder_project_preview`: an ordinary terminal preview;
- `builder_self_host`: a preview that runs the Builder scenario and can itself
  act as a Builder host.

The only permitted nested shape is:

```text
production Builder host
  -> builder_self_host (Builder loaded from DEV)
       -> builder_project_preview (scenario selected in that Builder)
```

An ordinary preview cannot own a child, and the child preview cannot own a
grandchild. When the outer host switches from Builder to an ordinary scenario,
its child remains paired but dormant. It cannot execute as a preview host
until Builder is active again; it must not become an orphan on demotion.

The runtime may promote an existing outer `builder_project_preview` relation
to `builder_self_host` only when the scenario actually running in its target
is the configured Builder scenario. This explicit scenario claim is required:
neither a `-dev` suffix nor the current browser route is sufficient evidence.
For the self-hosted local development case, the terminal child uses the
deterministic `<builder-preview>-dev` identifier (for example,
`dev1-dev-dev`) so the operator can inspect it directly. The persisted
relation remains authoritative; the suffix still has no general identity
semantics. The same deterministic allocation rule applies to the first level.

Sequential E2E work uses one explicitly selected existing Builder and its one
preview. Application IDs, revisions, sessions and evidence bundles isolate
cases, not fabricated webspace IDs. Queues, parallel execution and any future
preview leasing require a separate design and are deferred. Direct scenario
materialization requires an explicitly paired target and cannot create
`dev-<scenario>` or select another host's preview by matching its scenario.

Deleting a source or preview workspace removes every incident relation in the
same SQLite transaction as the catalog row. Catalog reset clears relations as
well. Content-change fanout ignores relation targets that no longer have a
workspace manifest, so stale topology cannot recreate deleted previews.

## Reconcile State

Builder preview state is persisted under
`state/builder/workbench/runtime/<source>.json` with schema
`adaos.builder.preview_runtime.v1`. Important fields are:

- `source_webspace_id` and `preview_webspace_id`;
- selected project and desired/observed scenarios;
- `generation` and `operation_id`;
- `status`: `idle`, `requested`, `running`, `accepted`, `ready`, or `failed`;
- timestamps, error, and the bounded apply result.

Repeated identical requests coalesce. A newer generation supersedes an older
result and is reconciled next. The apply lease is process-wide per Builder
source, so a skill daemon thread and the main runtime event loop cannot start
parallel materializations for the same preview. State updates use a separate
thread-safe lock and remain writable while apply is running. `accepted` means
the scenario switch was accepted for background rebuild; only `ready` advances
`observed_scenario`.

## Materialization Boundary

Production keeps `ADAOS_MATERIALIZATION_WORKER=1` for materializations that
actually need a fresh isolated document. Two execution classes are supported:

- ordinary `scenario_switch_rebuild` resolves `payload_only` in the long-lived
  core runtime; skill declaration discovery and resolver CPU work run in worker
  threads, while live YDoc access remains on its owner loop;
- `fresh_doc` and explicitly isolated materialization run through
  `adaos.services.scenario.materialization_worker` and additionally return an
  encoded snapshot update and state vector.

Ordinary switching must not launch a second full AdaOS runtime or rediscover
unchanged skill UI declarations. Use the bounded resolver/catalog path under
explicit YDoc ownership; require process isolation only for the declared
fresh-document/materialization boundary, not as a default reload mechanism.

The process-owned skill declaration catalog is built during API startup and is
invalidated by skill source changes. An isolated worker receives that bounded
catalog and its fingerprint in the request instead of scanning every active
skill again. The child does not mutate the parent live room. The parent checks
the current request generation and applies the result. Fresh-document workers
retain explicit timeout, RSS, result-size, and process-tree cancellation
budgets. Native YDoc lifetime is handled by the patched Yrs ownership model and
does not depend on worker exit.

The parent supervises the complete Windows process tree (venv launcher and
base interpreter), not just the launcher PID. Timeout, cancellation, and RSS
limit failures terminate all descendants before the reconcile slot is
released, so superseding selections cannot accumulate orphan workers.

Operational limits:

| Setting | Default |
| --- | --- |
| `ADAOS_MATERIALIZATION_WORKER_TIMEOUT_S` | 180 seconds |
| `ADAOS_MATERIALIZATION_WORKER_MAX_RSS_MB` | 2048 MiB |
| `ADAOS_MATERIALIZATION_WORKER_MAX_RESULT_MB` | 512 MiB |
| `ADAOS_WEBSPACE_RESOLVED_CACHE_MAX_MB` | 32 MiB |
| `ADAOS_WEBSPACE_MATERIALIZATION_CACHE_MAX_MB` | 64 MiB |
| `ADAOS_WEBSPACE_MATERIALIZATION_CACHE_LIMIT` | 8 entries |
| `ADAOS_WEBSPACE_SKILL_SOURCE_FINGERPRINT_TTL_S` | 600 seconds |
| `ADAOS_WEBSPACE_TRUST_PREVIOUS_MATERIALIZED_BRANCH_FINGERPRINTS` | enabled |

Tests disable the process boundary by default under `ADAOS_TESTING`; tests for
the boundary may explicitly enable it.

Scenario-switch materialization uses an explicit identity derived from
`webspace_id`, `scenario_id`, source mode, scenario file stamps, active skill UI
declaration stamps, user/roles, and policy. Resolver caching is split into a
scenario-invariant core and a per-webspace overlay. The core may be reused by
two preview webspaces only when scenario, source, skill, user/roles, and policy
fingerprints match; installed state, pinned widgets, ordering/visibility, and the
output webspace id are cloned and applied separately. Scenario-owned topbar and
page schema always come from the new core; stale structural values collected
from the previous webspace state are not overlaid. Full fresh-doc snapshots
remain in a separate cache namespace, so a fast switch cannot reuse a payload
where a Yjs snapshot is required. Runtime mutations without a specific
scenario id invalidate all materialized entries for the webspace;
scenario-specific mutations may drop only that scenario.

An ordinary switch preserves the existing YStore base and persists the
live-room branch diff. It no longer clears YStore and then blocks the switch on
a full-state snapshot rewrite. Hard reset/restore paths retain full snapshot
semantics. Browser scenario and go-home commands accept the pointer change in
background by default; `wait_for_rebuild=true` remains an explicit diagnostic
or recovery request.
Skill source fingerprinting is event-invalidated by skill install/update/
rollback and uses `ADAOS_WEBSPACE_SKILL_SOURCE_FINGERPRINT_TTL_S` only as a
fallback for out-of-band file edits. It should not rescan active skill source
trees during ordinary hot scenario switching.

Live-room materialized payload apply trusts runtime-owned
`registry.runtime_meta.effective_branch_fingerprints` by default when the
previous materialized payload supplies the same branch fingerprint. This avoids
reading and hashing large live YDoc branches during ordinary
builder/web_desktop toggles. The flag
`ADAOS_WEBSPACE_TRUST_PREVIOUS_MATERIALIZED_BRANCH_FINGERPRINTS=0` is a
diagnostic escape hatch, not the target architecture.

## Lifecycle Preview Targets

Builder persists an explicit `adaos.builder.preview_target.v1` alongside the
workbench binding. Clicking a Lifecycle node does not materialize it. The
separate **Show in Preview** action selects one of these sources:

| Target | Source | Version policy | Header prefix |
| --- | --- | --- | --- |
| Prototype | exact DEV `ui_revisions/NNN.json` snapshot | any retained UI revision | `proto:` |
| Automation | single retained Builder runtime snapshot | current completed result only | `active:` |

A project-backed Preview retains the aggregate `object_type=project` /
`object_id` and a separate primary `scenario_id`. The URL and materializer use
that scenario; selecting or following a revision must not replace the selected
Application with its component. Recreating a deleted Preview retains both the
exact revision and the `follow_active` choice. A failed or superseded
materialization must not be recorded as the selected ready target.

Automation materialization verifies the retained snapshot's scenario/task
identity under the same cross-process lock used by its writer. New snapshots
include content digests. A missing, changed or replaced snapshot fails closed;
current DEV content cannot stand in for the requested Automation result.

The visual tree is a provenance projection, not three independent lists:

```text
Prototype revision
  -> Automation result whose source_prototype_revision matches
       -> Publication whose source Automation identity matches
```

Legacy Publication records without exact provenance may be attached only to an
explicitly inferred historical Automation node. They must never make an old
release appear to be the output of the current Automation merely because it is
the only retained runtime snapshot.

Prototype and Automation use DEV skill declarations. Trial and Publication
are delivery nodes, not Preview targets: they open through an admitted
Application RuntimeSelection in an existing production Webspace. Builder owns
local Beta placement after each local development cycle, including upgrades of
installed Stable. Its admitted cutover updates Applications without a second UI
approval. Applications owns external update subscriptions, not a veto over the
publisher's local Builder lifecycle. The Automation snapshot lives outside the DEV
artifact tree, so publication cannot accidentally package Builder runtime
history. Materialization applies the selected scenario content as an explicit
payload override without rewriting the scenario pointer or the selected
Lifecycle node.

`follow_active=true` means the target is initially resolved from
`workflow.active_phase`; an explicit historical selection is read-only and
does not change that phase. The Builder header presents the editable process
(`WORKING`) and the rendered target (`VIEWING`) separately so a user can move
through the tree without mistaking navigation for a state transition.

## Project And Skill Presentation Preview

The Lifecycle target above selects a revision; the Project presentation selects
the scenario host and bindings used to render it. Resolution order is:

1. explicit Project entry point;
2. focused skill's default `presentation`;
3. `adaos.system.skill-preview` fallback;
4. explicit diagnostic failure.

The generic fallback renders standard metadata, README/help, icon,
capabilities, declared widgets, and empty-state explanations. Presentation
declaration and exact preview verification are separate records.

Open Preview and QR render the same Navigation SDK destination. It carries the
actual preview webspace rather than the Builder host webspace, plus exact
presentation bindings, zone/subnet, and applicable authentication policy. The
two controls cannot maintain separate URL templates.

The full Project, presentation, Development Session, and local artifact-context
boundary is defined by
[Project Composition, Presentation, and Development Context](project-composition-and-development-context.md).

## Workspace Catalog

`workspace_catalog_state.version` increments in the same SQLite transaction
as a real create, manifest update, delete, normalization, or reset. Idempotent
manifest writes do not increment it. `GET /api/node/yjs/webspaces` returns
`catalog_version` with the full catalog.

The Yjs compatibility payload is:

```json
{
  "schema": "adaos.workspace_catalog.v1",
  "version": 42,
  "items": []
}
```

Catalog projection uses `mutate_live_room` and only existing targeted or active
rooms. It must not call `async_get_ydoc` to fan out over catalog rows.

## Acceptance Checks

A project-selection regression test or smoke run must demonstrate:

1. Builder request identity changes from the old project to the selected one.
2. The Builder host YWS connection is not closed or recreated.
3. Only the explicitly paired preview receives a scenario switch/materialize
   operation; stale or unrelated DEV workspaces receive none.
4. Repeating the same selection 100 times produces one generation/apply and a
   bounded runtime-state file set.
5. Rapid superseding selection converges to the latest generation.
6. Repeated process-isolated materialization reaches a parent RSS plateau;
   worker peak RSS, result size, and phase timings are present in diagnostics.
7. Catalog updates do not create Yjs rooms and idempotent metadata writes do
   not advance catalog version.
8. A skill without an explicit presentation renders the generic skill-preview
   host rather than the previously selected scenario.
9. Open Preview and QR resolve to the same canonical destination, including
   development webspace and bindings.

Implementation and acceptance limits are tracked in the Builder roadmaps;
measurements are retained only in the [engineering journal](builder-engineering-journal.md).
