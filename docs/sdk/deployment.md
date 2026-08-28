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
- `reconcile(...)` accepts a fresh authority-owned plan into the durable worker
  and returns an operation immediately. `drain(...)` and `remove(...)` are
  journaled mutations with explicit capabilities, approvals, and idempotency
  keys.
- `recommend_nodes(...)` is a bounded dry-run ranking and grants no placement
  authority.

`submit(...)` and `reconcile(...)` are safe for slow disks and multi-node
rollout. Interactive tool timeouts do not define the operation lifetime;
callers observe the durable operation with `get_operation(...)`. Synchronous
`apply(...)` is reserved for bounded operator flows that deliberately own the
complete RPC lifetime.

## Distributed Release Admission

An active distributed `ServiceGroup` is an admission boundary for every
Project component named by its immutable `ServiceDefinition`. Before a plan is
ready, core verifies that the definition accepts the desired exact
`ProjectRelease` digest. A missing definition or a release outside the current
definition's bounded overlap produces a `blocked` plan; component activation
does not begin.

The required rolling-upgrade order is:

1. compare-and-switch the desired `ProjectDeployment` to the new release;
2. publish a reviewed immutable service definition for that release, retaining
   the currently active release in `compatible_release_digests`;
3. advance the service group to the new definition revision;
4. plan and submit the Project rollout;
5. remove the old overlap only after all required instances report the new
   release.

Core does not silently mutate the allowlist. This keeps service compatibility
an operator-reviewed topology decision and prevents a valid Project package
from leaving a distributed service alive but unrouteable after activation.
Deployment phase errors retain a bounded, secret-redacted `detail` when it is
more specific than the stable machine error code.

`selected_nodes` is durable placement intent, not a snapshot of currently
eligible nodes. If a selected node is temporarily offline, untrusted, or lacks
reported capacity, the plan is blocked and any existing activation on that
node is retained. Removing an activation requires removing its node from the
desired selected set and approving `component_remove`; transient inventory
state never implies destructive reconciliation.

## Capability Rules

Inspection requires `project.deployment.inspect`. Desired-state changes require
`project.deployment.manage`; plan execution requires
`project.deployment.apply`. Remote install, drain, removal, and data deletion
remain separate approvals and capabilities. External authoritative data is
retained by generic removal.

The inventory and operation interfaces are cursor- or limit-bounded. Package
bytes, source data, secrets, and unbounded receipts never cross this SDK.
