# Distributed Runtime

`adaos.sdk.distributed` is the skill-facing boundary for distributed services
and opaque replicated data. Skills do not import package stores, member-link
clients, supervisor state, node tables, or topology persistence internals.

## Control Interfaces

- `define_service`, `define_group`, `register`, `renew`, and `drain` manage
  release-bound service membership.
- `define_dataset`, `put_partition`, and `observe_replica` publish opaque
  desired and observed topology.
- `plan_replica_change`, `plan_rebalance`, `save_plan`, and `apply_plan` use
  immutable reviewed operations with explicit revisions and approvals.
- `route` and `invoke` resolve authorized instances without exposing subnet
  transport details.
- `inspect` and `explain_route` provide bounded operator projections.

Every function requires an active skill context and its declared distributed
capability. Authority handoff, removal, and data deletion remain separate
permissions and approvals.

Service definitions are immutable and release-bound. Version 2 may admit up to
eight exact prior `ProjectRelease` digests for a reviewed rolling upgrade. The
Project deployment planner consults active groups before activation and emits a
blocking warning when a governed component's target release is not admitted.
The distributed runtime never auto-expands this overlap from observed package
state.

## Adapter Interfaces

Domain adapters implement `TopologyAdapter` phase callbacks and return bounded
machine receipts. Large snapshot or delta payloads use:

- `AuthenticatedTransferSource.authorize/read`;
- `AuthenticatedTransferSink.authorize/write`;
- `BoundedTransferController.pump`;
- `TransferChunk` and `TransferRecord` checkpoints and witnesses.

Core limits a topology chunk to 96 KiB over the built-in member-link/HTTP
transport and limits each pump. A transfer can pause under cancellation or
resource pressure and resume from its durable checkpoint. Completion requires
the source and sink to return the reviewed manifest digest. Payload encoding,
temporary derived-data layout, import semantics, and cleanup belong to the
dataset adapter. Original externally authoritative bytes must not be copied by
a derived projection adapter.

Small bounded values may remain in a topology phase receipt. Large values must
not be placed in command envelopes, Yjs state, synchronized projections, logs,
or operation receipts.
