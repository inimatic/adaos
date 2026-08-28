# Skill Runtime Lifecycle

AdaOS provisions an isolated runtime per skill with versioned A/B slots. Each installation produces a fully self-contained copy of the skill sources, dependencies, resolved manifest, and metadata that can be activated atomically.

For package-backed project publication, A/B preparation is a runtime projection
of an immutable component package selected by `ProjectRelease` and
`WorkspaceLock`; it is not the publication source of truth. Permission and
migration plans are admitted before the lock switch, and an unknown
state-changing outcome is reconciled explicitly rather than replayed. See
[Artifact Source, Package, and Activation Architecture](architecture/artifact-source-package-activation.md).

Skills obtain path-free runtime and placement identity through
`adaos.sdk.core.runtime_identity()`. Its `node` projection contains only the
canonical `node_id`, `subnet_id`, and role; skills must use that identity for
node-owned records instead of guessing from process-local defaults or importing
node configuration services.

## Directory layout

Every skill lives under `skills/<name>` in the workspace. Runtime artefacts are stored separately:

```
skills/<name>/
    skill.yaml
    requirements.in            # optional dependency input
    handlers/
    migrations/
        data_migration.py      # reserved optional bucket migration file
    tests/

skills/.runtime/<name>/current_version
skills/.runtime/<name>/current_runtime.json
skills/.runtime/<name>/previous_runtime.json
skills/.runtime/<name>/v<major>.<minor>/
    active                      # marker with the current slot (A or B)
    previous                    # marker with the last healthy slot
    meta.json                   # test results, timestamps, history
    vendor/                     # shared pip --target deps for this bucket
    venv/                       # shared service-skill interpreter for this bucket
    data/
        db/
            skill_env.json      # shared state for this compatibility bucket
        files/
            secrets.json        # per-bucket secrets/artifacts
            .skill_env.json     # optional persisted environment snapshot
        internal/               # schema-bound internal data for this bucket
    slots/<A|B>/
        src/                    # snapshot of the skill sources
            skills/<name>/
                skill.yaml
                handlers/
                migrations/
                    data_migration.py  # reserved optional bucket migration file
                tests/
        node_modules/
        bin/
        cache/
        runtime/
            logs/
            tmp/
        resolved.manifest.json
```

Runtime isolation is keyed by semantic `major.minor`, not full SemVer. For example, `0.14.0` and `0.14.3` share `v0.14`; `0.15.0` uses `v0.15`.
Slots are A/B code deployments inside the same bucket. Data, `vendor/`, and `venv/` are not A/B-slotted inside a bucket.

## Version policy

The default publication bump for skills is `patch`. A patch release stays in the same runtime bucket and uses the existing `data/`, `vendor/`, and `venv/` trees.

Local storage capability bindings are concrete to this compatibility bucket,
not merely to the skill name. This lets stable and DEV deployments, or the
current and rollback buckets, remain live in one core process without sharing
SQLite engines or filesystem-blob targets. A handle acquired in one bucket is
stale outside that bucket and fails closed; patch releases inside the same
bucket retain the binding and data target.

If the skill has the reserved migration file, or a legacy manifest data migration hook, a requested/default patch bump is promoted to `minor`. A minor release creates a new `v<major>.<minor>` bucket and prepares a migrated copy of data there before activation.

Major releases are manual. They also land in a new bucket, but the decision to publish one is outside automatic CI/CD policy.

## Install → test → activate

`adaos skill install <name>` performs the pipeline below:

1. Select the inactive slot (A/B) for the target version and wipe any previous contents.
2. Copy the current contents of `skills/<name>` into `slots/<slot>/src`.
3. Build bucket dependencies (either reusing the host interpreter with bucket `vendor/` or creating the bucket `venv/` for service skills).
4. Enrich `manifest.json` into `resolved.manifest.json`, resolving tool entry points, interpreter paths, timeouts, and policy defaults.
5. Prepare bucket data. Patch installs in the same `v<major>.<minor>` bucket reuse the existing shared `data/` tree without copying. A new bucket safely looks for the reserved data migration file and runs it when present.
6. Optionally run `src/skills/<name>/tests/` (`--test`) from the prepared slot. Commands execute inside the staged environment (interpreter, `PYTHONPATH`, `.skill_env.json`), and logs are streamed to `slots/<slot>/logs/tests.log`.
7. Persist slot metadata (tests, timestamps, default tool, data migration result) for status and rollback operations.

`adaos skill activate <name>` switches the active version/slot markers atomically and records the previous version/slot for `adaos skill rollback`. Without a name, `adaos skill activate` activates every workspace skill currently marked `runtime-behind`. Activation does not run data migration; migration belongs to prepare. Setup flows must run **after activation** so that secrets and runtime paths are stable.

After a successful activation, AdaOS keeps only the current runtime bucket and the previous rollback bucket. Older runtime buckets are pruned automatically because the runtime supports only one rollback step.

`adaos skill rollback <name>` rolls back the active version/slot marker. For a patch rollback this means old code over the same bucket data. For a minor rollback this points back to the previous bucket and therefore to that bucket's older data copy. AdaOS does not try to detect or block writes that happened after the minor activation.

Important architectural note:

- activation is a slot-pointer switch, not a generic live-memory migration
- in-process skills typically pick up new code on the next invocation from the active slot
- the first invocation after a slot switch must invalidate skill-scoped Python
  modules for that skill before importing from the new active slot; requiring an
  API restart to observe installed skill code is a runtime defect
- API install/update/activate paths must refresh live handler subscriptions for
  the installed skill so event handlers and tool entry points converge on the
  same active slot
- service skills are explicitly restarted by the runtime lifecycle
- a service bucket venv is shared by its A/B slots; its dependency marker is
  content-based, so an identical slot switch does not run package installation
  again. A requirements digest or declared dependency change still invalidates
  the marker and refreshes the venv
- durable migration authority belongs to persisted bucket data under `v<major>.<minor>/data`, while derived caches/projections should be rebuilt after activation

Operational signal:

- If a tool call or subscription still executes old behavior after
  `adaos skill install` or `adaos skill activate`, do not hide the symptom with
  a manual API restart. Record the active slot, handler reload result,
  `sys.modules` package name if available, and the skill source path, then fix
  the reload boundary.

For the target kernel-facing migration architecture, including rehydrate and rollback semantics for stateful skills, see [AdaOS Supervisor](architecture/adaos-supervisor.md#skill-runtime-migration-lifecycle).

## Background runtime migration

Skill runtime migration is a normal background process, not a core handoff
precondition. Core A/B switch validation must bring the runtime API up first;
only after that may AdaOS migrate skill runtimes. This keeps the subnet
available while individual skills install dependencies, prepare slots, run
tests, or restart service processes.

The core-update prepare path marks skill migration as deferred in the target
slot manifest. During post-boot runtime startup, AdaOS schedules the migration
worker through the live API process. The worker persists observable status under
`state/skill_runtime_migration/status.json` and exposes it through
`/api/skills/runtime/migration/status`.

Migration selection is bounded:

- only installed workspace skills whose runtime version is behind the current
  workspace/GitHub version are selected by default
- `force` may be used for operator/debug repair, but normal background runs do
  not reinstall every skill
- successful migration clears transient process state during activation and
  should not leave persistent UI noise

Project-managed activations have a stricter ownership boundary. When an active
local `ComponentActivation` selects an exact skill package, manifest and runtime
slot from a `ProjectRelease`, that Project deployment owns the pointer. The
background workspace migration worker records
`migration_owner=project_deployment`, reports the selected runtime as not behind,
and skips it even when migration was requested with `force`. Core updates may
restart or rehydrate the selected service process, but they must not replace the
Project-selected slot with a workspace/GitHub runtime.

Drift or a failed Project-owned runtime is repaired through Project deployment
reconciliation: drain/remove the affected activation when required, compute a
new desired/observed deployment plan, and activate the exact immutable package
again. The generic skill migration endpoint is not a Project conformance or
repair mechanism.

Handler discovery is read-only with respect to installed skill runtime slots.
For an explicit local development workflow, `ADAOS_SKILL_RUNTIME_SOURCE_SYNC=1`
allows the loader to synchronize workspace sources before discovery. The sync
runs outside the event loop and is always disabled in a prewarmed core
`candidate`, because candidate startup must not mutate runtime state shared with
the active process. Normal install, update, activation, and background migration
remain the production mutation paths.

During prepare/activate, the affected skill is temporarily deactivated with
`status=disabled`, `reason=runtime_migration_in_progress`, and an operation id.
If migration fails, AdaOS leaves the skill deactivated with
`status=quarantined`, `reason=runtime_migration_failed`, failure stage, source,
and human-readable comment. This is the same kernel-level disabled/quarantine
contract that service supervision and tool dispatch already respect; web
desktop should only present this state.

## Deactivate lifecycle

AdaOS may keep the core switch committed while quarantining a subset of skills.
For that case the runtime lifecycle now includes explicit deactivation:

- a deactivated skill remains installed
- its prepared slot and metadata remain inspectable
- tool execution is blocked with a clear `skill is deactivated` error
- ordinary `activate` clears the deactivation marker and returns the skill to service

This is intended for post-commit checks where rolling back the whole core is unnecessary, but continuing to serve a broken skill would be unsafe.
Core-update orchestration may trigger this automatically after a successful runtime switch if post-commit skill checks fail.
When that happens, the deactivation record now persists the failure contract itself, including `failure_kind`, `failed_stage`, `source`, and whether the core switch was already committed.
The same record can carry process-state fields: `status` (`disabled` or
`quarantined`), `comment`, `operation_id`, and `transient`. A successful
activation clears the marker; failed migration keeps it for operator action.

## Optional internal data migration

This feature is optional. A skill can ignore `data/internal` completely and continue using only:

- `data/db/skill_env.json`
- `data/files/*`

Use `data/internal` only for state that must evolve together with runtime schema changes.

### Default behavior

If a skill has no migration file, AdaOS does not copy data during patch prepare. The prepared slot uses the same bucket-level `data/` directory as the currently active slot.

When preparing a new minor/major bucket without a migration file, AdaOS writes a warning to the AdaOS log and copies the previous bucket `data/` tree into the target bucket without schema mutation.

### Reserved migration file

The standard migration source is reserved at:

```text
skills/<name>/migrations/data_migration.py
```

In the staged runtime this becomes:

```text
slots/<A|B>/src/skills/<name>/migrations/data_migration.py
```

The file should expose `migrate(payload: dict) -> dict | None`. During prepare of a new compatibility bucket, AdaOS runs it against the staged skill sources for the target slot. The migration file owns target data population: it should copy the source data it wants to preserve and mutate schema-bound state as needed.

Manifest-level `data_migration_tool` declarations are legacy-compatible, but LLM-authored skills should prefer the reserved file so core and generated code agree without extra manifest wiring.

The hook receives a payload with:

- `source_version`
- `target_version`
- `source_runtime_bucket`
- `target_runtime_bucket`
- `source_data_root`
- `target_data_root`
- `source_internal_dir`
- `target_internal_dir`
- `data_root`
- `internal_root`
- `runtime_slot`
- `version`

AdaOS also exposes convenience environment variables while the hook runs:

- `ADAOS_SKILL_INTERNAL_DATA_ROOT`
- `ADAOS_SKILL_INTERNAL_ACTIVE_PATH`
- `ADAOS_SKILL_INTERNAL_TARGET_PATH`

Important notes:

- the hook is optional
- if the reserved file is absent on a minor/major bucket change, AdaOS logs a warning and falls back to a plain data copy
- the hook is expected to populate the target bucket data it owns
- on migration failure, AdaOS clears the target bucket data and fails `prepare_runtime`

### Target direction

The target AdaOS migration model separates state classes:

- canonical durable state:
  must survive restart, rollback, and rebuild
- bucket-bound schema state:
  belongs under `v<major>.<minor>/data/internal`
- derived runtime state:
  caches, indexes, projections, and similar rebuildable material
- live memory:
  in-flight objects and subscriptions that should be drained and recreated, not migrated implicitly

This means the reserved data migration file should be used for schema-sensitive persisted state, not as a platform promise that arbitrary process memory can be moved across activation.

After activation, stateful skills are expected to rebuild derived runtime state from durable truth.

## Vendor vs venv

`vendor/` and `venv/` are separated because they solve different dependency problems:

- `vendor/` is a bucket-level `pip --target` package overlay. It is added to `PYTHONPATH` for ordinary Python skills that can run in the hub interpreter but need extra pure-Python packages.
- `venv/` is a bucket-level isolated Python environment. Service skills use it when they need their own interpreter process, ABI boundary, or dependencies that must not be installed into the hub runtime.

Both live under `v<major>.<minor>` so patch A/B deployments do not duplicate dependency environments. A minor or major bucket gives the skill a fresh dependency boundary when the migration model says compatibility changed.

Dependency admission policy:

- In-process Python skills use the bucket `vendor/` overlay by default.
- `runtime.env.mode: shared` is an explicit legacy/diagnostic mode that installs into the current core interpreter. It is not used as an automatic fallback.
- Heavy/native dependency stacks such as Torch, TensorFlow, OpenCV, FAISS, EasyOCR, and transformer runtimes are rejected for in-process skills by default. They should be declared as `runtime.kind: service` with `runtime.env.mode: venv`, or later as a core-owned dependency profile.
- `runtime.env.allow_heavy_dependencies: true` is a transitional override for controlled stand work. It keeps the operator-visible risk explicit and should not be used as the production shape for ML/model skills.

### Declaration staging and activation order

Runtime preparation copies `skill.yaml` and `webui.json` into the slot source
artifact and verifies that their bytes match the source files. The resolved
manifest retains `data_projections` and `data_routes`.

Activation loads projection rules plus stream/Yjs receiver metadata from the
target slot before smoke import and target lifecycle hooks. Startup discovery
does the same before importing runtime or workspace handlers, so subscriptions
and import-time refreshes see the active declaration set. A missing projection
rule during a skill-owned publish is available in
`/api/node/projection-diagnostics`; direct calls to write-capable core Yjs APIs
also produce `projection.direct_yjs_write` validation warnings.

For `runtime.kind: service`, the active manifest's exact `events.publish`
topics also scope the child process's rotating service-event capability.
`adaos.sdk.io` and `adaos.sdk.data.events.publish()` transparently use that
loopback bridge whenever the supervisor supplies the service capability. This
also applies when a tool invocation initializes a process-local SDK context:
that local bus is not the owner runtime bus and must not receive cross-skill
events. The bridge remains bounded and output-only; declaring a service does
not grant arbitrary bus or root-runtime access.

### Runtime lifecycle hooks

AdaOS now supports optional lifecycle hooks in the resolved skill manifest.

Preferred declaration shape:

```yaml
lifecycle:
  persist_before_switch: persist_state
  after_activate: after_activate
  rehydrate: rehydrate
  drain: drain
  dispose: dispose
  before_deactivate: before_deactivate
```

The hook names resolve through the ordinary skill `tools` table. Data migration itself should use the reserved `migrations/data_migration.py` file for new skills.

Current behavior:

- `persist_before_switch` runs against the currently active slot before pointer cutover when an active prepared runtime exists
- `after_activate` runs after the new slot becomes active
- `rehydrate` runs after activation to rebuild derived runtime state
- `drain` runs before rollback/deactivate or activation-failure cleanup when declared
- `dispose` runs after `drain` and before `before_deactivate` when declared
- `before_deactivate` runs before explicit deactivate or rollback of the current slot
- global runtime drain now reuses the same contract:
  `subnet.draining` triggers active-skill `drain`, and `subnet.stopping` triggers `dispose` then `before_deactivate` as best-effort shutdown hooks for active installed runtimes
- service-skill supervision is also quiesced on `subnet.stopping`: the service watchdog and health loops stop before child service processes are terminated, so service skills are not auto-respawned while a runtime update or restart is waiting for shutdown completion

Lifecycle diagnostics are persisted into slot metadata and surfaced by `adaos skill status --json` through `runtime_status().lifecycle`.

If activation already switched to a new version/slot and `rehydrate` then fails, AdaOS now attempts to restore:

- the previous active version marker
- the previous active slot selection
- the previous runtime bucket data, by restoring the previous active version/slot marker
- the previous deactivation state

The failed target slot keeps its lifecycle diagnostics so operators can inspect the failed `rehydrate`, shutdown hooks, and rollback result.

Post-commit migration checks now also consume these lifecycle diagnostics.
That means a skill may be marked failed or selectively deactivated because `rehydrate` / `healthcheck` is already unhealthy even before any explicit post-commit test suite runs.

Operator-facing migration reports also surface lifecycle failures separately from test failures, so a `lifecycle/rehydrate` failure is visible as a first-class shutdown/migration issue rather than only as a generic failed skill.
This same metadata is also written into the deactivation marker when a skill is selectively quarantined after a committed core switch.
Supervisor-facing validation status and operator projections now also surface a compact quarantine summary, so post-commit status can show which skill was quarantined and at which lifecycle/test stage.

## Tool execution and setup

`adaos skill run <name> [<tool>]` reads the active slot’s `resolved.manifest.json`, adds the staged source directory to `sys.path`, and executes the tool callable with per-invocation timeouts. `adaos skill test <name>` reuses the same active slot to execute `src/skills/<name>/tests` without preparing a new build. If a skill declares a `setup` tool it is available via `adaos skill setup <name>` **only after activation**; attempting to run setup while the version is pending reports a clear error instructing the operator to activate first.

### Durable action approval scopes

Repeated device or resource actions may declare a reusable approval boundary in
the public SDK:

```python
@tool(
    side_effects="device_control",
    approval_scope={
        "name": "media.playback.control",
        "resource_argument": "target_id",
        "principal_meta_key": "controller_device_id",
        "local_resource_argument": "target_endpoint_id",
        "local_principal_meta_key": "controller_endpoint_id",
        "ttl_seconds": 31_536_000,
        "presentation": {
            "title_i18n_key": "runtime.media.approval.title",
            "summary_i18n_key": "runtime.media.approval.summary",
            "waiting_i18n_key": "runtime.media.approval.waiting",
        },
    },
)
def play_on(target_id: str, target_endpoint_id: str, **kwargs): ...
```

Core reads this declaration from the resolved manifest, not from caller
arguments. The first approved Pending Action creates a durable grant scoped to
subject, action name, resource and webspace. Later matching calls may reuse it;
another controller, resource, webspace or expired/revoked grant requires a new
approval. The grant store and policy remain core-owned. Skills receive only the
normal approval result and must not persist authorization copies themselves.
Grant I/O is moved off the API event loop.

`local_resource_argument` is an optional exact self-target boundary. Core
compares it with the trusted caller metadata named by
`local_principal_meta_key`; an exact match is classified as a local write and
does not ask a person to approve control of the surface they are already using.
The skill must still resolve the resource and reject an endpoint mismatch
before producing any remote effect. Approval wording and i18n keys are declared
by the owning skill, while Pending Actions, durable grants, and retry policy
remain generic core/client responsibilities.

### Device presence projection

Core device registry owns the reusable liveness model. Registry entities keep
their raw heartbeat and connection witnesses; consumers obtain the normalized
projection through `adaos.sdk.data.devices.get_device_presence(device_ref,
grace_seconds=...)`. The result distinguishes `online`, `grace`, and `offline`
and includes bounded age and availability fields.

When a registry mutation changes projected presence, core emits
`device.presence.changed` with the device reference plus previous and current
projections. Skills subscribe to that transition when they need reactive
placement or UI updates. A skill may add a shorter service heartbeat or a
capability profile, but it must not persist a competing general-purpose device
online registry. Reads still return the current projection immediately, so a
new subscriber does not need to wait for the next heartbeat event.

## Secrets management

Secrets are stored under `skills/.runtime/<name>/v<major>.<minor>/data/files/secrets.json` and are never copied into the source tree. Runtime execution injects secrets at process start and keeps placeholders (`${secret:NAME}`) inside `resolved.manifest.json`.

SDK secret operations resolve that store from the context-local active skill
runtime. Process environment variables remain a compatibility mechanism for an
isolated worker only; they are not ownership authority for concurrent
in-process skill calls. This prevents one skill invocation from transiently
reading another skill's secret backend.

Use the CLI to manage secrets either globally or per skill:

```
adaos secrets set WEATHER_API_KEY <value> --skill weather_skill
adaos secrets list                      # lists all skills with stored secrets
adaos secrets export > backup.json      # exports secrets grouped by skill (values redacted)
adaos secrets import backup.json        # restores secrets into installed skills
adaos secrets list --skill weather_skill
adaos secrets export --skill weather_skill --show
adaos secrets import dump.json --skill weather_skill
```

`adaos skill setup weather_skill` is a thin wrapper around the skill-defined setup tool that typically requests credentials and persists them via the per-skill secrets backend.

## Observability

Every install/test/activate/run operation logs under `slots/<slot>/logs/`. `adaos skill status --json` surfaces runtime state (active version/slot/readiness/tests). For progress checks:

An unpartitioned runtime pytest suite has a 180-second default budget, bounded
to 60-900 seconds by `ADAOS_SKILL_PYTEST_TIMEOUT_SECONDS`. Named smoke,
contract, and dry-run suites retain their shorter fixed budgets. The timeout
terminates only the owned test subprocess and fails slot preparation.

- Workspace: `adaos skill status <NAME>` compares `skills/<NAME>` against the workspace registry remote (`adaos-registry.git` main) and best-effort refreshes that remote-tracking ref before computing path divergence.
- Workspace with operator diagnostics: `adaos skill status <NAME> --fetch --diff` additionally prints fetch warnings and renders the exact path diff.
- Dev: `adaos skill status --space dev <NAME> --fetch --diff` compares the dev folder against the hub draft state via Root API (requires hub mTLS keys from the bootstrap `node.yaml`).

Workspace markers are split by state plane. `git-dirty` means local filesystem
changes are not committed, while `git-ahead` means path-level commits exist
locally and still need a registry push. `git-behind` means the registry base has
newer path-level commits, and `git-different` is the fallback when the path
differs but Git cannot classify the divergence. `git-error` means the CLI could
not compute the Git comparison. Runtime markers are separate: `runtime-ahead`
means the workspace skill version is ahead of the active runtime slot,
`runtime-behind` means the active slot is newer than the workspace source, and
`runtime-different` means the versions differ but cannot be ordered.

`adaos skill push` without a skill name is a batch release command, not a raw
Git transport command. It finds skills with dirty files, committed `git-ahead`
changes, or an explicit manifest-vs-registry version drift, then runs the same
version bump, registry update, commit, and push flow used by
`adaos skill push <name> -m ...`. Batch release commits use the standard message
`chore(<skill>): release workspace changes`.

Yjs owner-flow warnings are load-mark diagnostics, not install/activation
failures. Skill and SDK owners still use the normal peak and sustained
thresholds because they can create write amplification. Core webspace semantic
rebuilds (`webspace_runtime.rebuild_async` / `webspace_runtime.rebuild_sync`)
publish normal bulk snapshots after skill or scenario activation; those core
rebuild buckets are classified by sustained window pressure by default. Their
per-second peaks remain visible in snapshots and history. Set
`ADAOS_YJS_LOAD_MARK_CORE_REBUILD_PEAK_ALERTS=1` only when debugging core
rebuild bursts themselves.

## Weather skill reference

`.adaos/skills/weather_skill/` demonstrates the complete lifecycle:

1. Install the reference skill with tests: `adaos skill install weather_skill --test`.
2. Activate the freshly prepared slot: `adaos skill activate weather_skill`.
3. Run setup to capture the API key via secrets: `adaos skill setup weather_skill`.
4. Execute the default tool: `adaos skill run weather_skill --json '{"city": "Paris"}'`.

The repository contains smoke and contract tests under `src/skills/<name>/tests/` and an optional health probe that can be used by the platform for readiness checks.
