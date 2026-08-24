# Project Deployment

`adaos.sdk.deployment` is the only skill-facing boundary for distributed
Project placement. It exposes typed desired state, immutable plans, durable
operations, bounded inventory, and reviewed drain/remove controls without
giving a skill access to supervisor, package-store, subnet-link, or runtime
process internals.

## Authority Boundary

Every SDK operation is marshalled to the active core runtime's deployment
authority. The authority owns the live node inventory and the serialized
deployment runtime. A CLI process, skill worker, candidate update runtime, or
other non-owner is a client of that authority; it must not construct an
authoritative inventory from process-local link state or heartbeat files.

The authority endpoint is loopback-only, authenticated with a process-private
capability, and fail-closed. Candidate runtimes remain passive until promotion;
promotion changes the authority owner before normal deployment work is
accepted. An unreachable or mismatched authority returns an explicit error
instead of planning against an empty subnet.

## Public Operations

- `define(...)` compare-and-switches one `ProjectDeployment` revision.
- `plan(...)` persists an immutable reviewed `DeploymentPlan`.
- `submit(...)` accepts the plan into the durable worker and returns quickly.
- `apply(...)` performs the same reviewed operation synchronously when the
  caller intentionally owns the longer RPC lifetime.
- `get_operation(...)`, `inspect(...)`, and `list_deployments(...)` provide
  bounded read-side progress and desired/observed state.
- `list_nodes(...)` returns `NodeInventoryPage`; it is the same authority-owned
  inventory in API, skill, and CLI contexts.
- `reconcile(...)`, `drain(...)`, and `remove(...)` are journaled mutations with
  explicit capabilities, approvals, and idempotency keys.
- `recommend_nodes(...)` is a bounded dry-run ranking and grants no placement
  authority.

`submit(...)` is preferred for slow disks and multi-node rollout. Interactive
tool timeouts do not define the operation lifetime; callers observe the durable
operation with `get_operation(...)`.

## Capability Rules

Inspection requires `project.deployment.inspect`. Desired-state changes require
`project.deployment.manage`; plan execution requires
`project.deployment.apply`. Remote install, drain, removal, and data deletion
remain separate approvals and capabilities. External authoritative data is
retained by generic removal.

The inventory and operation interfaces are cursor- or limit-bounded. Package
bytes, source data, secrets, and unbounded receipts never cross this SDK.
