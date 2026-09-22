# CBS7 `booking.reserve` Domain Proof

Status: implemented and validated locally.

`booking.reserve` is the second executable Capability/Binding/State proof. It
keeps the semantic Application package-neutral while exercising behavior that
the CRUD proof intentionally did not cover.

## Frozen contracts

The primary `capability:booking.reserve@1.0.0` composes
`capability:availability.check@1.0.0` and declares three ports:

| Port | State contract | Access | Required guarantee |
| --- | --- | --- | --- |
| `availability` | `state-contract:booking.availability` | read | snapshot, external authority |
| `bookings` | `state-contract:booking.records` | write | serializable, single writer |
| `audit` | `state-contract:booking.audit` | append | serializable, append only |

Availability is `external_authoritative`; booking and audit state are portable.
The Application requirement contains no package, binding, provider, storage,
endpoint, or credential identity. The resolver selects the supporting
capability explicitly, includes it in `ApplicationResolution`, and requires its
digest in capability-conformance evidence.

Simulation and production use separate `BindingDefinition` records but execute
the same contract scenario suite. Package delivery remains outside both the
capability and Application requirement.

## Runtime semantics

The proof uses half-open time intervals and enforces:

- `starts_at < ends_at` and a future start time;
- one command payload per idempotency key;
- one durable result for duplicate successful requests;
- no overlapping confirmed bookings for one resource;
- an exact availability generation at local commit;
- an immutable external effect receipt for every observed side effect;
- compensation after a stale read or losing concurrent decision;
- a durable reconciliation item when compensation cannot be proven.

The provider boundary can deterministically inject timeout before effect,
timeout after effect, compensation failure, and an availability generation
change. A two-thread barrier proves that overlapping decisions may both create
external effects, but only one commits BookingState; the losing effect is
compensated.

Provider rebinding reuses the same portable BookingState and AuditState
identities and records. The semantic Application digest remains unchanged.
External evidence drift is admitted through the CBS6 assessment path; stale
calendar evidence fails production resolution.

## Executable evidence

`tests/test_capability_binding_state_booking.py` covers:

- contract/state-port and composition shape;
- shared simulation/production scenarios;
- temporal and idempotency invariants;
- concurrent interval conflict;
- timeout before and after external effect;
- compensation and reconciliation;
- stale availability generation;
- provider rebinding with state continuity;
- composed resolver output and stale-evidence rejection.

The domain proof required one generic improvement: semantic resolution now
selects mandatory capability dependencies and requires their digests in
conformance evidence. No workflow, package, provider, or storage detail was
added to the capability contract or Application requirement.

## Deferred claim

This proof does not establish universal domain coverage. `order.approve` stays
deferred until it demonstrates materially different workflow or compensation
semantics.
