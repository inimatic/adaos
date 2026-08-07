# Relational Storage

Status: experimental ARF0.5 SDK contract.

The relational-storage SDK gives a skill a private logical database without
exposing a filesystem path, SQLAlchemy engine, DSN, username, or password.
AdaOS selects a provider from declared requirements and derives ownership from
the active skill context.

## Acquire a Database

The caller needs the existing AdaOS capability `storage.relational` and an
active skill context:

```python
from adaos.sdk.data import (
    RelationalStorageRequirements,
    relational_database,
)

db = relational_database(
    "experiments",
    requirements=RelationalStorageRequirements(
        durability="durable",
        transactions_required=True,
        concurrent_writers=1,
        json_required=True,
        locality="node",
    ),
)
```

Skill code supplies only a logical name and requirements. It cannot supply an
owner, physical path, DSN, or migration identity for another skill. Unknown
providers and unsupported requirements fail closed.

## Query and Transaction API

Statements use SQLAlchemy-style named parameters:

```python
db.execute(
    "CREATE TABLE IF NOT EXISTS runs "
    "(run_id TEXT PRIMARY KEY, status TEXT NOT NULL)",
)

with db.transaction() as tx:
    tx.execute(
        "INSERT INTO runs(run_id, status) VALUES (:run_id, :status)",
        {"run_id": "run-1", "status": "queued"},
    )
    row = tx.fetch_one(
        "SELECT run_id, status FROM runs WHERE run_id = :run_id",
        {"run_id": "run-1"},
    )
```

`execute`, `fetch_one`, `fetch_all`, `scalar`, `transaction`, and `health` are
the current small surface. A transaction commits on normal exit and rolls back
when its block raises.

The handle is provider-neutral, but arbitrary SQL is not dialect-neutral. A
skill supporting both SQLite and PostgreSQL must use a shared SQL subset or
own explicit provider variants and conformance tests.

## Isolation Rules

- The capability gate is checked when the handle is acquired.
- `owner_ref` is derived from the active skill; it is never a skill argument.
- A binding is private to one owner and logical name.
- Every operation rechecks the current skill, so a handle retained across a
  skill-context switch is rejected.
- SQLite data is placed under the active compatibility bucket's `data/db`.
- Public bindings contain opaque locators and secret references only.
- The in-process Python runtime is not a hostile-code security boundary.

The skill must declare `storage.relational` in `skill.yaml:capabilities`.
An optional node profile at `state/capabilities/skill_grants.json` may narrow
that declaration with per-skill allow/deny rules; a profile cannot grant a
capability absent from the signed/installed manifest. Database ownership and
stale-handle checks remain independent, defense-in-depth enforcement.

Direct cross-skill SQL access is deliberately unsupported. Shared data should
be owned by a specialized provider skill that publishes typed APIs, events,
projections, or governed logical views. Consumer skills receive a service
binding, not the provider skill's database binding.

## Providers

SQLite is the default node-local provider. It offers transactions, foreign-key
enforcement, WAL mode, a busy timeout, optional JSON probing, and one advertised
concurrent writer.

PostgreSQL is optional and operator-configured. The preparatory adapter uses
the core-owned `ADAOS_RELATIONAL_POSTGRES_URL` and provisions one deterministic
logical database per binding. The administrator URL remains private to the
adapter. Per-owner roles, credential rotation, migrations, backup/restore,
quotas, retention, and lifecycle health projections are later roadmap work.
Install the `adaos[postgres]` optional dependency and use a SQLAlchemy URL such
as `postgresql+psycopg://...`; the URL belongs to the operator, not to a skill.

Provider preference is a constraint, not a connection selection escape hatch:

```python
requirements = RelationalStorageRequirements(
    concurrent_writers=4,
    json_required=True,
    locality="network",
    preferred_providers=("postgresql",),
)
```

See [Research Fabric Core Readiness](../architecture/research-fabric-core-readiness.md)
for the architectural boundary and [Research Fabric Roadmap](../architecture/research-fabric-roadmap.md)
for the remaining migration and operational gates.
