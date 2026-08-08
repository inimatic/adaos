# Research Fabric Core Readiness

Status: ARF0.5 plus ARF2/ARF3 implementation record. SQLite, PostgreSQL,
relational lifecycle, execution ABI, local process, and OCI-admission paths
are validated locally.

Last reviewed: 2026-08-08.

This page records the narrow core foundation now consumed by the first
Research Fabric skill. It is subordinate to the
[AdaOS Research Fabric](research-fabric.md) architecture and
[roadmap](research-fabric-roadmap.md). It does not introduce research-domain
entities into core.

Core readiness is intentionally not Research Fabric readiness. Passing the
storage and execution conformance suites does not close ARF1 until an operator
can manage one complete experiment through the packaged Desktop scenario.

## Decisions

1. Relational storage is acquired as `storage.relational`, using a typed
   requirement and a redacted binding rather than the legacy raw `SQL` port.
2. The SDK requires the existing `storage.relational` capability and derives
   the owner from the active skill context. A skill cannot name another skill
   as database owner or provide a physical database path/DSN.
3. Every logical database is private to one skill and one compatibility
   bucket. SQLite uses one file per logical binding under that skill's
   `data/db`; PostgreSQL uses one logical database per binding.
4. The migration owner must equal the binding owner. Cross-skill table/schema
   access is not part of the capability.
5. If several skills need shared data, a specialized provider skill owns its
   own database and publishes typed APIs, events, projections, or logical
   views. Consumers receive a `ServiceBinding`, not the provider's SQL binding.
6. `ContentRef`, `ServiceBinding`, `ExecutionSpec`, and `ExecutionAttempt` are
   generic ABI. `EvidenceBundle`, scientific `Run`, `Trial`, and protocol
   semantics remain research-manager entities.
7. The local executor is a reference adapter for idempotency, cancellation,
   terminal receipts, bounded resources/logs/outputs, heartbeats, unknown
   reconciliation, checkpoints, preemption admission, and restart recovery. It
   is not a hostile code sandbox and rejects GPU, network, cost, or secret
   requests it cannot enforce.
8. Existing `OperationManager`, governed-workflow activities, model jobs, and
   the executor ABI are related but are not collapsed into a new global job
   manager in this slice.

## Existing Runtime Inventory and Disposition

| Existing surface | Current role | ARF0.5 disposition |
| --- | --- | --- |
| `ports.SQL.connect() -> Any` | Legacy core SQLite access | Retained for existing repositories; not exposed as the new skill capability |
| `SkillRuntimeEnvironment.data/db` | Versioned skill-owned structured state | Canonical local scope for SQLite bindings |
| `ResourceTicket` | Operator/resource request stored in KV | Remains a request record; not an execution attempt |
| `Process` port | Start/stop/status for process handles | Remains process lifecycle; not durable scientific or execution identity |
| `OperationManager` | User-visible install/update operations and notifications | Remains its current authority; later maps to generic operation references where useful |
| `WorkflowActivityDispatcher` | Dispatch intent from governed transitions | Uses the executor activity adapter without becoming the provider |
| `ProcSandbox` | Synchronous bounded process execution | Remains an operational limit; no hostile-code claim |
| `ArtifactKind = skill/scenario` | Release/package identity | Unchanged; `ContentRef` does not add a `research` release kind |
| Future `ModelJob` | Model-runtime long-running operation | Must reuse the execution attempt/provider semantics rather than fork them |

## Implemented Contract Surface

The packaged ABI now includes:

- `adaos.storage.relational.requirement.v1`;
- `adaos.storage.relational.binding.v1`;
- `adaos.content.ref.v1`;
- `adaos.service.binding.v1`;
- `adaos.execution.spec.v1`;
- `adaos.execution.attempt.v1`;
- `adaos.execution.checkpoint.v1`;
- `adaos.storage.blob.requirement.v1`.

Python domain types validate the same identities and serialize to those ABI
payloads. The generic owner-reference validator is shared by storage, service
bindings, content references, and execution attempts. Provider-facing ports
are `RelationalStorageProvider`, `RelationalStorageBrokerPort`, and
`ExecutorProvider`.

## Relational Capability

The public skill path is:

```python
from adaos.sdk.data.relational import (
    RelationalStorageRequirements,
    database,
)

db = database(
    "experiments",
    requirements=RelationalStorageRequirements(
        durability="durable",
        transactions_required=True,
        concurrent_writers=1,
        json_required=True,
        locality="node",
    ),
)

db.execute(
    "CREATE TABLE IF NOT EXISTS runs "
    "(run_id TEXT PRIMARY KEY, status TEXT NOT NULL)",
)
db.execute(
    "INSERT INTO runs(run_id, status) VALUES (:run_id, :status)",
    {"run_id": "run-1", "status": "queued"},
)
```

Acquisition passes through the current AdaOS SDK capability resolver for
`storage.relational`; ownership isolation is enforced separately by the
binding and active skill context.

Admission is fail-closed and per skill: the installed `skill.yaml` must declare
`storage.relational`, and an optional node profile may narrow it with explicit
allow/deny rules. A profile cannot invent an undeclared capability. Binding
ownership and stale-handle checks remain independent defense in depth.

### Provider status and workflow binding

Relational and execution providers expose protocol `1.0`, typed feature sets,
and redacted `ProviderStatus` records. `ProviderStatusRegistry` produces one
bounded projection and rejects incompatible major/minor requirements before a
binding is used.

`ExecutionWorkflowActivityAdapter` is a handler for the existing durable
`WorkflowActivityRunner`. It submits an immutable `ExecutionSpec` through the
generic executor port and, when requested, creates a reference in the existing
`OperationManager`. Workflow activity identity, physical execution identity,
and user-visible operation identity remain separate; no second workflow or
operation authority was introduced.

The SQL facade uses SQLAlchemy-style named parameters. The interface is
provider-neutral; SQL dialect and owner migrations are not magically portable.
A skill that needs both SQLite and PostgreSQL must keep its migrations and
queries inside the common subset or explicitly maintain provider variants.

### Isolation

For skill `experiment-manager` and logical name `experiments`, the local
binding resolves internally to:

```text
.adaos/workspace/skills/.runtime/experiment-manager/vX.Y/data/db/experiments.db
```

The returned binding exposes only an opaque locator such as
`skill-data:db/experiments.db`. It does not expose the absolute path, engine,
DSN, username, or password.

Each database operation rechecks the active skill context. Passing a handle
from one skill invocation into another skill context fails with an isolation
error. The in-process runtime is still not a hostile-code security boundary;
this guard protects supported SDK use and makes ownership mistakes explicit.

### Providers

`sqlite`
: Default node-local provider. It supplies one database file per logical
  binding, transactions, owner migrations, foreign keys, WAL, busy timeout,
  capacity enforcement, backup/restore, explicit retention, and optional JSON
  capability probing. It advertises one concurrent writer and rejects stronger
  requirements.

`postgresql`
: Optional provider enabled by the core-owned
  `ADAOS_RELATIONAL_POSTGRES_URL`. It provisions a deterministic isolated
  logical database per owner/binding and a least-privilege owner role per
  skill. Ordinary SDK callers receive only an opaque binding. If the owner is
  a supervised service, core generates a login credential in memory and
  injects its DSN only into that service process. The administrator URL remains
  inside the adapter. Bounded pools, health, operator credential refresh,
  backup/restore, and migration upgrade evidence are implemented. Capacity and
  TTL requirements are rejected because this provider does not enforce them.

The broker never silently weakens a requirement. If no provider supports the
requested concurrency, locality, JSON, transaction, durability, or backup
profile, acquisition fails with a typed capability error.

## Cross-Skill Data

Direct cross-skill binding access is intentionally absent. The target pattern
is:

```text
consumer skill A ---\
                    -> typed API/projection -> shared-data provider skill
consumer skill B ---/                         -> its private DB binding
```

The provider skill owns schema, migrations, authorization, row/field policy,
stable logical views, audit, and compatibility. This keeps database structure
private and makes shared semantics reviewable. A later read-only analytical
view capability may be added only with an explicit owner, consumer grants,
lineage, revocation, and snapshot-consistency contract.

## Execution Foundation

`ExecutionSpec` captures owner, immutable command/package/code/environment,
working directory, non-secret environment, secret references, resources,
network policy, named RNG determinism, budgets, input `ContentRef`s, declared
outputs, checkpoint, preemption policy, and metadata. `ExecutionAttempt`
captures one physical provider submission and references, but does not own,
scientific trial/run identity.

The local reference provider persists attempt state under:

```text
.adaos/state/executions/local/<attempt-id>/
```

It provides:

- deterministic idempotency-key to attempt identity;
- rejection when one key is reused with a different spec digest;
- detached worker execution and atomic terminal receipt;
- bounded stdout/stderr and declared-output `ContentRef`s;
- owner checks for reconcile and cancel;
- restart reconciliation using receipt plus PID/create-time identity;
- CPU affinity, memory, wall, compute, storage, log, and attempt budgets;
- heartbeat/lease observations and explicit cancellation handshake;
- `unknown` reconciliation before retry and then `lost` rather than fabricated
  success when process and receipt are absent;
- compatible checkpoint and bounded preemption-resume admission.

The local provider rejects GPU allocation, secret injection, restricted
networking, and monetary budgets. An optional OCI adapter builds a
digest-pinned Docker-compatible invocation with CPU, memory, GPU, offline
network, and declared-output boundaries. Allowlisted egress and secret drivers
remain fail-closed; Ray/remote submission remains ARF5.

## Local Evidence

Focused verification:

```text
.venv/Scripts/python.exe -m pytest \
  tests/test_relational_storage_capability.py \
  tests/test_execution_foundation.py \
  tests/test_provider_status_and_execution_workflow.py \
  tests/test_research_manager_storage_conformance.py \
  tests/test_runtime_bindings.py -q
```

The tests cover:

- per-skill SQLite separation and stale-handle rejection;
- runtime-bucket placement and redacted bindings;
- transaction rollback, owner migrations, capacity, backup/restore, retention,
  deletion, and named parameters;
- fail-closed capability negotiation;
- migration-owner and path-traversal rejection;
- ABI schema validation;
- execution idempotency, owner isolation, budgets, declared outputs,
  cancellation, timeout, heartbeat loss, unknown outcomes, preemption,
  accelerator contracts, OCI admission, and restart reconciliation;
- an environment-gated live PostgreSQL conformance run.

The PostgreSQL test uses `ADAOS_TEST_POSTGRES_URL` and creates a uniquely named
test database. It drops only that exact `adaos_*` database after the test. A
missing server produces an explicit skip and is not PostgreSQL acceptance.

On 2026-08-07 the 35-test focused core/research suite passed locally against a
temporary `postgres:16-alpine` container using the `psycopg` driver. The live
cases covered isolated databases and roles, pool/health and credential-refresh
behavior, migrations, backup/restore, and research-manager/local-tracker
conformance. The provider removed its exact databases/backups and the exact
`--rm` test container was stopped afterward.

On 2026-08-08 the ARF4 follow-up added an explicit service-login case. Core
generated a separate `adaos_owner_*` login URI for the owning service, the
login connected only to its isolated `adaos_*` database and completed a
transaction, and the public binding remained free of DSN/password material.
The updated relational and research-manager integration cases passed 2/2 on a
temporary `postgres:16-alpine` container; the exact verified container was
removed afterward. The non-PostgreSQL focused matrix passed 71 tests with the
two PostgreSQL cases skipped before that live run.

Native package/lifecycle verification used the existing AdaOS commands rather
than a research-specific CLI:

```text
adaos skill validate research_manager_skill --strict --probe-tools
adaos skill test research_manager_skill --json
adaos scenario validate tlp_research --json
adaos scenario test tlp_research
adaos scenario install tlp_research
adaos scenario run tlp_research
adaos tests run --only-sdk ...
```

In the initial 2026-08-07 slice, published `research_manager_skill` `0.4.0`
passed six package tests and was
healthy in its active service slot. Its bounded `get_study` browser route is
read-only. The published desktop scenario `tlp_research` `0.1.3` passed five
package tests; installation activated the matching dependency runtime,
execution reached the idempotent `protocol_review` state, and a live Desktop
reload projected `scenario:tlp_research` into the installed app catalog. The
focused native SDK run passed with two environment-gated PostgreSQL skips; the
same PostgreSQL coverage passed in the separate live run above.

The lifecycle exercise also hardened generic core paths: quarantine can be
recovered by a strictly newer skill version, scenarios dispatch only skill
routes declared by their package dependencies, repeated scenario installation
reuses a healthy exact-version service runtime, and `adaos tests run` resolves
suites and its working directory through `CTX.paths.repo_root()`.

## Preserved Boundaries

The completed slice deliberately leaves these items to their owning milestones:

- existing core repositories remain on their current SQLite boundaries;
- research schemas remain owned by `research_manager_skill` and use only the
  public storage/execution SDKs;
- cross-skill data sharing requires a specialized provider skill and typed
  views/APIs, never another skill's SQL binding;
- Ray, provider-specific cloud credential drivers, and broad production
  operations remain ARF5+ work. ARF4 adds the generic service-facing
  relational/blob injection and governed UI proxy without changing the
  skill-facing relational SDK boundary described here.
