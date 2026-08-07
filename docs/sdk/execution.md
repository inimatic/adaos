# Durable Execution

Status: experimental ARF3 SDK contract, validated locally.

The execution SDK lets an admitted skill submit one immutable execution
specification, reconcile its physical attempts after restart, and cancel an
attempt without changing scientific trial or run identity. The active skill
context supplies `owner_ref`; a skill cannot submit or reconcile work for
another owner.

## Submit and Reconcile

The skill declares `execution.jobs` and uses the public SDK:

```python
from adaos.sdk.execution import (
    ExecutionBudget,
    ExecutionResourceRequest,
    reconcile,
    spec,
    submit,
)

execution = spec(
    "analysis.v1",
    ("python", "analysis.py", "--output", "result.json"),
    run_id="run-42",
    trial_id="trial-7",
    resources=ExecutionResourceRequest(
        cpu_cores=2,
        memory_mb=2048,
        wall_time_s=900,
        max_log_bytes=1024 * 1024,
    ),
    budget=ExecutionBudget(
        max_attempts=2,
        max_compute_seconds=1200,
        max_storage_bytes=64 * 1024 * 1024,
    ),
    expected_outputs=("result.json",),
)

attempt = submit(execution, idempotency_key="run-42-attempt-1")
attempt = reconcile(attempt.attempt_id)
```

An idempotency key is permanently bound to one spec digest. An ambiguous
provider outcome becomes `unknown`; another attempt for the same run and sample
generation is rejected until reconciliation resolves it. Infrastructure retry
increments `attempt_number` while retaining `trial_id`, `run_id`, and
`sample_generation`.

## Reproducibility and Recovery

Confirmatory specs require immutable code and environment digests plus named
RNG streams for initialization, data ordering, augmentation, operator
initialization, and analysis. Checkpoint manifests bind content, producer
attempt, parent digest, code/environment compatibility, RNG state, and resume
policy. Preemption resume additionally requires an enabled, bounded policy and
a compatible checkpoint.

Attempts persist provider binding, status history, heartbeat/lease,
cancellation handshake, resource observations, bounded log references,
declared output references, failure classification, and checkpoint/allocation
metadata. Provider dashboards or process IDs are diagnostics, not durable
scientific identity.

## Provider Boundaries

The default local-process provider enforces allowed working roots, CPU
affinity, memory, wall time, compute/storage/attempt budgets, bounded logs,
declared outputs, cancellation, heartbeats, and restart reconciliation. It is
not hostile-code isolation and rejects GPU allocation, secret injection,
offline/allowlist networking, and monetary budgets.

The optional OCI provider creates a Docker-compatible command for a
digest-pinned image and can apply CPU, memory, GPU, and offline-network limits.
Allowlisted egress and secret injection remain unavailable until operator
drivers exist. Ray is a future executor adapter; its job/task objects will not
enter this SDK.

See [Research Fabric Core Readiness](../architecture/research-fabric-core-readiness.md)
and the [Research Fabric Roadmap](../architecture/research-fabric-roadmap.md).
