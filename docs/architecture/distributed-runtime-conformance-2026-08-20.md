# Distributed Deployment And Topology Conformance - 2026-08-20

Status: validated locally for the generic `AP8` deployment and `DS1` through
`DS4` runtime boundaries. Physical multi-node and production acceptance remain
separate gates.

This receipt records the implementation proof for
[Artifact milestone AP8](artifact-source-package-activation-roadmap.md#milestone-ap8-distributed-project-deployment)
and the
[Distributed Service and Data Topology Roadmap](distributed-service-and-data-topology-roadmap.md).
It deliberately does not claim that an in-memory or loopback test is a
two-physical-node stand proof.

## Accepted Revisions

| Surface | Revision | Accepted boundary |
| --- | --- | --- |
| AdaOS core contracts and planning | `cc223008`, `93c4b1b4`, `b9186442`, `bc3077b4` | fail-closed deployment and distributed-runtime ABI, planners, durable stores, projections and public SDKs |
| AdaOS core execution | `0758d74f`, `239e5552`, `78fb4709`, `cae7939a`, `b63fe0c6` through `598bc015` | exact component activation, remote receiver/transport, topology adapters, fenced handoff, routed service invocation and bounded service event delivery |
| Planner and operations extensions | `7763169b` | bounded placement recommendations and costed rebalance dry runs |
| Media Center consumer | registry commits `35129f6` through `4e9f7d1` | Project deployment, service membership, opaque root/catalog partitions, agent operations and operator projections through public SDKs |
| Non-media fixture | core test `test_non_media_document_fixture_uses_same_opaque_partition_contract` | document-shard descriptor proves that core owns topology, not media semantics |

## Contract Boundary

Core owns exact software placement, trusted membership, leases, fenced
authority, opaque partition and replica topology, bounded routes, reviewed
operations, transfer witnesses, retention decisions, projections and audit.
Adapters own payload interpretation, checkpoint production, merge/rebuild
semantics and domain health. Original media and other external authoritative
bytes never become core-owned data.

The public boundaries are `adaos.sdk.deployment` and
`adaos.sdk.distributed`. Product skills do not need package-store, Workspace,
supervisor, Hub, Yjs, subnet transport or node-inventory internals.

## Compatibility Matrix

| Boundary | Accepted | Rejected before mutation |
| --- | --- | --- |
| ABI | exact `*.v1` schema with known fields | unknown fields, malformed ids, invalid state/profile combinations, tampered digests |
| Component activation | trusted node, exact ProjectRelease and package digest, compatible architecture/capabilities | untrusted node, inventory revision drift, package/release mismatch, insufficient capacity |
| Service membership | matching activation, release, protocol and runtime generation with renewable lease | stale generation, expired activation, incompatible protocol/release, untrusted node |
| Authority | reviewed handoff, verified checkpoint/watermark and monotonically increasing epoch | dual owner, stale epoch, old-owner writes or promotion before verify |
| Dataset adapter | exact dataset generation and declared adapter profile | adapter/profile mismatch, payload-dependent policy in core |
| Route | authorized caller, eligible leased instance, freshness and topology revision | missing or stale authority; partial participation is reported rather than hidden |
| Update skew | components admitted by the immutable release compatibility lock and declared protocol overlap | undeclared or incompatible skew; no optimistic best-effort activation |

## Failure Matrix

| Failure | Required observable result | Local proof |
| --- | --- | --- |
| Reviewed deployment sees changed inventory | compare-and-switch rejection before adapter mutation | `test_executor_rejects_inventory_drift_after_review` |
| Known transient component failure | bounded retry under the reviewed operation id | `test_executor_journals_multi_component_nodes_retries_and_is_idempotent` |
| Lost remote acknowledgement | terminal `uncertain`, no automatic retry or rollback claim | deployment adapter lost-ack tests and `test_executor_does_not_retry_or_rollback_uncertain_state` |
| Service process is healthy but lease expires | instance becomes ineligible independently from its last health report | `test_membership_expiry_is_independent_from_last_health` |
| Old authority continues writing | receiver rejects its stale fencing epoch | `test_fenced_handoff_rejects_old_owner_and_routes_partial_topology` |
| Partition is missing or replica is stale | bounded partial route names unavailable partitions and fallback reason | contract and handoff route tests |
| Adapter fails with known outcome | bounded retry and secret-safe receipt | `test_topology_operation_retries_known_failure_and_redacts_receipt` |
| Adapter outcome is unknown | terminal `uncertain`, manual reconciliation required | `test_uncertain_topology_phase_is_not_retried` |
| Snapshot transfer is interrupted | resume from checkpoint, bounded chunks and final content witness | `test_bounded_transfer_resumes_and_requires_content_witness` |
| Target receiver identity differs | fail closed before local adapter execution | deployment and topology receiver tests |

## Retention And Recovery Objectives

| Data class/profile | Removal rule | Recovery point | Recovery time contract |
| --- | --- | --- | --- |
| External authority | generic removal must retain bytes; detach only | source remains authoritative, so platform-induced byte loss target is zero | reconnect/reindex is operator- and library-size-bound; no fixed RTO is claimed |
| Derived projection | may retain or rebuild only as reviewed | latest accepted source checkpoint/watermark | bounded by adapter rebuild budget and reported pressure |
| Cache | disposable after route removal | no durability guarantee | repopulated on demand |
| Single authority follower | promote only after verified checkpoint and fenced epoch commit | latest verified checkpoint/watermark | manual reviewed handoff after detection; automatic election is deferred |

A replica is not a backup. Backup policy, media originals, provider secrets and
profile-private history remain owned by their domain policy. Deployment removal
and replica-data removal are independent confirmations.

## Security And Resource Controls

- Planning is read-only and immutable; application checks the reviewed plan,
  desired generation and inventory revision again.
- Remote execution revalidates target node identity, exact component/package
  identity and digest at the receiver.
- SDK calls require capability grants; authority handoff, topology change,
  remote installation, replica removal and retention decisions have distinct
  permissions/approvals.
- Audit receipts are append-only, bounded and secret-safe. Adapter result
  details are normalized before persistence.
- Reservation and capacity checks run before activation or replica admission;
  pressure can pause operations without reporting success.
- Large payloads use bounded adapter transport with checkpoint, cancellation,
  backpressure and content witnesses, not Yjs or command envelopes.

## Reproduction

From the core repository with its `src` directory on `PYTHONPATH`:

```text
python -m pytest tests/test_project_deployment_contracts.py tests/test_project_deployment_service.py tests/test_project_deployment_default_runtime.py tests/test_project_deployment_adapter.py tests/test_distributed_runtime_contracts.py tests/test_distributed_runtime_service.py tests/test_distributed_runtime_adapters.py -q
```

The accepted run passed `59/59`. It covers strict schema round trips,
placement modes, immutable plans, idempotent execution, inventory drift,
uncertain remote outcomes, leases, protocol/capacity admission, fencing,
routes, operations, transfer resume/content witnesses, rebalance planning and
the non-media document fixture.

## Open Acceptance Gates

- `AP8-12` and `DS5-02`: two physical nodes, real process/node loss, separate
  TV/controller placements, staged update, drain and retained data.
- `DS5-04`: compatible rolling adapter upgrade on physical nodes.
- `DS5-05`: security/privacy/resource soak and explicit production decision.
- Automatic election, cross-subnet placement and general multi-writer data are
  deferred by design.
