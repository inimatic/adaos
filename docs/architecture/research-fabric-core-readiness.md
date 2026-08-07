# Research Fabric Core Readiness

Status: ARF0.5 implementation record. SQLite, PostgreSQL, ABI, and
local-execution paths are validated locally.

Last reviewed: 2026-08-07.

This page records the narrow core foundation implemented before the first
Research Fabric skill. It is subordinate to the
[AdaOS Research Fabric](research-fabric.md) architecture and
[roadmap](research-fabric-roadmap.md). It does not introduce research-domain
entities into core.

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
   terminal receipts, logs, and restart reconciliation. It is not a hostile
   code sandbox and rejects resource/secret requests it cannot enforce.
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
| `WorkflowActivityDispatcher` | Dispatch intent from governed transitions | Later consumes an executor/service binding; it does not become the provider |
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
- `adaos.execution.attempt.v1`.

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

The current bootstrap grants this baseline capability through the resolver's
`core` fallback. That makes the gate an integration seam today, not a claim of
fine-grained in-process authorization. Future manifest/profile grant hydration
may narrow admission without changing binding ownership or the SDK contract.

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
  binding, transactions, foreign keys, WAL, busy timeout, and optional JSON
  capability probing. It advertises one concurrent writer and rejects stronger
  requirements.

`postgresql`
: Optional provider enabled by the core-owned
  `ADAOS_RELATIONAL_POSTGRES_URL`. It provisions a deterministic isolated
  logical database per owner/binding. The administrator URL remains inside the
  adapter and the binding contains only a secret reference. Live use installs
  the `adaos[postgres]` extra and requires a server; production roles,
  credential rotation, backup/restore, quotas, and upgrade policy remain ARF2
  work.

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

`ExecutionSpec` captures owner, immutable command, working directory,
non-secret environment, secret references, resources, input `ContentRef`s,
expected outputs, checkpoint, and metadata. `ExecutionAttempt` captures one
physical provider submission. Scientific `Run` identity is deliberately not
part of this ABI.

The local reference provider persists attempt state under:

```text
.adaos/state/executions/local/<attempt-id>/
```

It provides:

- deterministic idempotency-key to attempt identity;
- rejection when one key is reused with a different spec digest;
- detached worker execution and atomic terminal receipt;
- stdout/stderr `ContentRef`s;
- owner checks for reconcile and cancel;
- restart reconciliation using receipt plus PID/create-time identity;
- wall-time failure and explicit cancellation;
- `lost` rather than fabricated success when process and receipt are absent.

CPU, memory, GPU, secret injection, checkpoint resume, heartbeat/lease,
container isolation, remote submission, and unknown-submit recovery remain
ARF3 work. The local provider rejects unenforced CPU, memory, GPU, and secret
requirements.

## Local Evidence

Focused verification:

```text
.venv/Scripts/python.exe -m pytest \
  tests/test_relational_storage_capability.py \
  tests/test_execution_foundation.py \
  tests/test_runtime_bindings.py -q
```

The tests cover:

- per-skill SQLite separation and stale-handle rejection;
- runtime-bucket placement and redacted bindings;
- transaction rollback and named parameters;
- fail-closed capability negotiation;
- migration-owner and path-traversal rejection;
- ABI schema validation;
- execution idempotency, owner isolation, cancellation, timeout, logs, and
  restart reconciliation;
- an environment-gated live PostgreSQL conformance run.

The PostgreSQL test uses `ADAOS_TEST_POSTGRES_URL` and creates a uniquely named
test database. It drops only that exact `adaos_*` database after the test. A
missing server produces an explicit skip and is not PostgreSQL acceptance.

On 2026-08-07 the PostgreSQL case passed locally against the temporary
`postgres:16-alpine` container using the `psycopg` driver. The provider created
and removed its isolated logical database, and the exact `--rm` test container
was stopped afterward.

## Remaining Admission Work

Before ARF1 starts depending on these contracts beyond the validated local
slice:

- decide whether database migration hooks extend the current skill bucket
  migration file or receive a narrower SDK contract;
- bind execution activities to the governed workflow without duplicating
  `OperationManager` authority;
- define provider health/status projection and feature-version negotiation;
- connect `storage.relational` admission to authoritative per-skill
  manifest/profile grants when that policy path is ready;
- keep the first research manager on the public SDK only, with no adapter or
  private-path imports.
