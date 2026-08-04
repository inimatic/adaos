# Workflow Reference Persistence Decision

Status: accepted for the bounded single-user reference path; external durable
engine adoption postponed.

Decision date: 2026-07-30.

Reaffirmed: 2026-08-04 after the complete durable/ad-hoc process inventory.

Owners: Governed Data-Driven Workflow Model Roadmap GWR6/GWR7 and the AdaOS runtime.

## Decision

AdaOS will use its shared node-local SQLite database as the reference durable
provider for the current single-user Builder workflow. DBOS, Temporal, Restate,
and JetStream are not added to the runtime now. The provider-neutral resolver,
command/event contracts, activity boundary, and persistence ports remain the
seam for a later adapter.

This is a postponement based on the present evidence, not a permanent product
selection. An external provider experiment is admitted only by a measured
requirement in the admission table below.

The 2026-08-04 inventory added publication/activation journals, Builder
package-cutover migration checkpoints, Automation and Preview execution state,
runtime operations, Skill Factory, Root MCP leases, hub-root acknowledgements,
and process-local schedulers. These surfaces are either governed workflow
evidence or separate canonical lifecycle/transport models. None satisfies an
external-provider admission criterion. GWR6-16 remains open, but it requires a
durable per-hub Telegram ingress inbox and target-zone receipt rather than a
replacement workflow engine.

## Scope

The decision covers bounded node-local development with one logical writer per
workflow instance. It does not claim active-active execution, distributed
consensus, cross-node transactions, multi-user quorum, or automatic recovery
of an external effect whose outcome is unknown.

## Builder Durability Inventory

| Concern | Canonical Builder contract | Reference handling |
| --- | --- | --- |
| Human waits | ConversationInteraction states including partial, completed, expired, cancelled, and superseded | persisted in the shared conversation store with generation guards |
| Long activity waits | `automation_waiting`, `prototype_derivation_waiting`, `publication_waiting`, and `reconciliation_required` | exact snapshot generation plus activity attempt |
| Modifying activities | `builder.codex.run`, `builder.prototype.derive`, `builder.trial.activate`, `builder.publication.publish` | durable intent before execution; effect-start boundary recorded |
| Timers | activity timeout `1800s`, heartbeat `30s`, interaction/route expiry | declarative deadline facts; no hidden in-process timer is authoritative |
| Retry | every modifying Builder activity declares `retry=never` | a new user/operator command creates a new Run; no automatic mutation replay |
| Cancellation | cooperative before external effect; reconciliation after effect start | `cancelled` before effect and `outcome_unknown` after effect start |
| Callback/result | typed workflow command, InteractionResponse, activity outcome | payload-bound idempotent inbox and monotonic generation |
| Async reply | accepted, progress, input-required, terminal, notification | durable ResponseEnvelope outbox with monotonic sequence and progress coalescing |
| Delivery | Web, Telegram, text, or another authorized ReplyRoute | independent DeliveryAttempt retries; delivery never invokes business work |
| Definition upgrade | explicit versioned migration, state map, context delta, authority, and event | compare-and-swap snapshot plus `workflow.definition.migrated` journal record |
| Backup/restore | snapshot and complete canonical journal | versioned export/restore contract; no projection is backup authority |

## Minimum Persistence Contract

One AdaOS SQLite transaction commits the current snapshot, immutable journal
event, payload-bound inbox result, workflow outbox item, and optional activity
intent. Commit revalidates:

- current generation and unchanged snapshot digest;
- invocation permission;
- immutable target digest when supplied;
- approval witness when required;
- exact activity/executor effect binding.

ReplyRoute, ResponseEnvelope, and DeliveryAttempt records use the same AdaOS
database but a separate delivery lifecycle. A failed route can be retried or
replaced without rerunning the workflow command or activity. If no authorized
route exists, the terminal envelope remains queryable as `undeliverable`.

## Fault Evidence and Cost

The local suite injects a crash both before and after the effect-start boundary
for all four modifying activity classes. Its observed contract is:

| Boundary | Recovery classification | Automatic effect retry | Operator work |
| --- | --- | --- | --- |
| Durable activity intent exists; effect not started | `safe_resume` | none; an executor may explicitly claim the recorded attempt | zero semantic repair |
| Effect may have started; result absent | `reconciliation_required` | forbidden | inspect external result and record one typed outcome |

Measured reference-path complexity is two recovery branches and zero automatic
unknown-outcome retries. The eight crash-injection cases produced zero unsafe
repeats. Delivery failure followed by successful redelivery produced zero
business-command repeats. Storage uses eight tables in the existing AdaOS
SQLite database (five workflow and three reply/delivery tables). One accepted
transition writes one snapshot, one journal event, one inbox result, one outbox
item, and zero or one activity intent. Delivered workflow outbox rows have a
bounded compactor; canonical journals and terminal results are retained.

Reproducible local command:

```powershell
.venv\Scripts\python.exe -m pytest `
  tests\test_workflow_persistence.py `
  tests\test_durable_delivery.py -q
```

Accepted on 2026-07-30: `16 passed`. Definition migration and composition are
covered by `tests/test_governed_workflow.py`.

## Candidate Assessment

The products solve real but currently unmeasured needs:

- [DBOS architecture](https://docs.dbos.dev/architecture) provides a library
  model that checkpoints workflows and steps in PostgreSQL and can coordinate
  distributed workers with its control plane. It is the first candidate if a
  Python/PostgreSQL deployment needs multi-process recovery without a separate
  orchestration service.
- [Temporal workflow execution](https://docs.temporal.io/workflow-execution)
  provides durable replay, timers, activities, child workflows, and very large
  execution scale. Its
  [self-hosted deployment](https://docs.temporal.io/self-hosted-guide/deployment)
  introduces a critical service, persistence, upgrade, security, and
  observability surface. It is a candidate for Root-coordinated distributed or
  multi-user workflows, not for every local node.
- [Restate self-hosting](https://docs.restate.dev/server/overview) provides a
  durable server and is a candidate when keyed single-writer service/actor
  semantics materially simplify a sidecar deployment.
- JetStream may transport outbox work, but does not replace AdaOS workflow
  semantics or solve uncertain external effects by itself.

Adding any candidate now would increase packaging, supervision, upgrade,
backup, and diagnosis surface while preserving the same commit-time authority,
idempotency, target-digest, and reconciliation obligations. No present fault
case demonstrates compensating benefit.

## Admission Criteria for Re-evaluation

Open a provider experiment only when a representative workload demonstrates at
least one of these unmet requirements:

1. a workflow must remain owned and recoverable across multiple processes or
   nodes and the single-writer local provider is no longer valid;
2. Root must coordinate multi-user approvals, quorum, or child workflows while
   a node is offline;
3. measured concurrent instance volume or journal size misses an agreed SLO
   after bounded SQLite tuning and compaction;
4. long timers, schedules, or worker placement create repeatable operator
   defects that the reference port cannot close economically;
5. high availability requires automatic failover with a measured recovery
   objective unavailable from node-local backup/restore.

Before evaluation, freeze the existing resolver, composition, durability, and
cross-channel conformance suites. A candidate must run them behind a feature
flag, keep provider handles below the AdaOS SDK, report resource and operations
cost, and include migration and exit tests. Provider-native state can never
become a second domain truth.

## Consequences

- Builder can continue locally without Root or another infrastructure service.
- Uncertain external effects remain visible and require reconciliation; the
  system does not pretend to provide exactly-once external side effects.
- GWR7 product integrations remain deferred, while the adapter boundary and
  evidence gate prevent future lock-in.
- Distributed consensus, active-active execution, federated collaboration, and
  multi-user promotion remain explicitly outside the current acceptance claim.
