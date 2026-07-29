# Builder Preview Runtime

Status: implemented architecture contract.

This document defines project selection, Builder preview ownership, and
webspace materialization. It replaces the former convention where a preview
was inferred by appending `-dev` and where one project event represented both
UI selection and artifact mutation.

## Invariants

- A Builder host and its preview are paired by a persisted relation, never by
  parsing either webspace ID.
- Selecting a project changes Builder context and only the paired preview. It
  does not reload the Builder host or scan unrelated DEV webspaces.
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
| `builder.preview.desired` | paired preview should converge to a scenario | yes, paired target only |
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
one incoming owner per preview. New preview IDs are opaque `preview-*` values.
Existing binding files may adopt a legacy ID such as `dev1-dev` during
migration, but the suffix has no runtime semantics.

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
the child relation is detached; its workspace is retained for explicit
cleanup or diagnostics.

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

The old path started a complete second AdaOS runtime for every scenario switch.
On the measured Windows host this spent about 1.9 seconds importing bootstrap
and scenario modules and another 1.2-1.8 seconds rediscovering unchanged skill
UI declarations before resolving the scenario. Process exit was not an
ownership requirement after the patched Yrs release, so ordinary switches no
longer cross that duplicate boundary.

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
| Publication | workspace artifact | current published version only | `public:` |

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

Prototype and Automation use DEV skill declarations; Publication uses
workspace declarations. The Automation snapshot lives outside the DEV
artifact tree, so publication cannot accidentally package Builder runtime
history. Materialization applies the selected scenario content as an explicit
payload override without rewriting the scenario pointer or the selected
Lifecycle node.

`follow_active=true` means the target is initially resolved from
`workflow.active_phase`; an explicit historical selection is read-only and
does not change that phase. The Builder header presents the editable process
(`WORKING`) and the rendered target (`VIEWING`) separately so a user can move
through the tree without mistaking navigation for a state transition.

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

## Local Verification Evidence

The implementation smoke on 2026-07-21 used the real `dev1-dev` preview with
`prototype_app_c6b08e41` in five separate `payload_only` worker processes:

- wall time: 4.06-4.33 seconds per operation;
- resolver/materialization time: 3.04-3.17 seconds;
- child interpreter RSS: 99.4-101.0 MiB;
- serialized result: 0.599 MiB;
- parent RSS reached 61.9 MiB by the third operation and remained there;
- total parent RSS increase over the series: 5.75 MiB.

A supervisor smoke measured 115.3 MiB peak RSS for the complete launcher plus
interpreter process tree. Cancelling an active materialization terminated both
PIDs; no descendant remained alive after cancellation.

This historical run established that process isolation bounded memory, but the
new phase timings subsequently showed 1.8-2.7 seconds outside the measured
resolver: interpreter/module startup plus repeated declaration discovery. Native
YDoc heap release is owned by the patched Yrs store model documented in
[Yjs Runtime Ownership](yjs-runtime-ownership.md), not by process churn. The
acceptance suite also covers 100 identical selections
(one generation/apply), 100 distinct sequential selections (one bounded state
file), and a superseded in-flight generation converging to the latest target.

The 2026-07-22 browser acceptance used the real `dev1` Builder surface and its
paired `dev1-dev` preview:

- **Choose project** rendered all 21 projects in 879 ms without a loading state
  or spinner;
- selecting `Prototype App E5` kept `dev1` on `builder/ready` for every poll;
- only `dev1-dev` entered pending materialization and converged to
  `prototype_app_4d5758e5/ready` in 6.49 seconds;
- the Builder page remained mounted and displayed the selected project while
  the preview rebuilt;
- a 52-switch live soak reached a bounded private-memory plateau; the final
  40-switch window was 304.1-318.5 MiB with a -0.019 MiB/switch slope over its
  last 20 samples.

This verifies that project selection is a Builder data/context change. It does
not switch or reload the Builder host scenario. Scenario materialization is
owned only by the explicitly related preview webspace.

The 2026-07-28 local runtime acceptance used the real DEV Builder scenario
`0.2.23` and control skill `0.1.32`. Explicit selection of the retained nodes
materialized and persisted matching labels for all target kinds:

- Prototype `042`: `proto: builder · UI 042`;
- current Automation task: `active: builder · 0.2.20`;
- current Publication: `public: builder · 0.2.20`.

The same live projection confirmed one Lifecycle root with dependent stages,
Automation `0.2.20` nested under its source Prototype `041`, and older
provenance-free publications represented as non-previewable inferred lineage.

The 2026-07-23 root-cause benchmark cleared resolved/materialized caches after
building the process-owned declaration catalog, then materialized the real
`prototype_app_c6b08e41` scenario. The final API acceptance run on a new DEV
webspace measured:

- pointer/rebuild acceptance: 54 ms internally and 84 ms over local HTTP;
- complete cold rebuild: 0.692 seconds, including 0.264 seconds resolver and
  0.271 seconds cold YRoom creation plus payload apply;
- second DEV webspace in the isolated resolver benchmark: 0.436 seconds total
  with a shared-core hit;
- skill declaration lookup in both operations: below 0.4 ms;
- the same payload through the retained one-shot worker: 3.269 seconds even
  with declarations supplied, proving that duplicate runtime startup rather
  than generated scenario complexity caused the former 4-6 second switch.

The final cold trace contained no `scenario_projection_sync` for the selected
scenario and no materialization worker. Its full snapshot exposed
`pageSchema.id=prototype_app_c6b08e41`, proving that the previous scenario's
structural overlay was not retained. A 40-switch alternating run stabilized at
0.17-0.24 seconds per hot rebuild; process private bytes moved from 341.2 MiB
to 342.1 MiB and reached a plateau after the first cache allocations.

Overlay isolation and access-scope separation are regression-tested. A shared
core hit cannot carry installed/layout state across webspaces, and a different
user/roles/policy identity forces a different core entry.

Cold live-room creation is part of the same ownership contract. When the
switch already has a materialized payload, room bootstrap loads persisted Yjs
state without scenario seeding and applies the payload exactly once. It does
not emit `scenarios.synced` or invoke the in-room semantic materializer. The
pre-fix live trace spent 1.139 seconds creating the room and ran an additional
0.649-second `scenario_projection_sync`; the same stage after the fix took
0.183 seconds with no duplicate rebuild.
