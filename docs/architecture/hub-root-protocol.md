# Hub-Root Protocol

## Purpose

The hub-root link is AdaOS's primary control plane.
This document defines the target protocol semantics for that link.

It does not prescribe a specific transport.
WebSocket, sidecar relay, HTTP/2, or another transport may carry the protocol, but the protocol obligations stay the same.

Related documents:

- [Channel Semantics](channel-semantics.md)
- [Authority And Degraded Mode](authority-and-degraded-mode.md)
- [Transport Ownership](transport-ownership.md)

## Scope

This protocol governs traffic between hub and root for:

- control commands
- root-facing lifecycle and state reports
- root-routed integration actions
- route-control metadata

This protocol does not define:

- media transport
- raw Yjs payload shape
- browser-local ephemeral presence semantics

## Production Transport Strategy

The production hub-root strategy is protocol-first and transport-flexible:

1. Establish the normal control session through the sidecar/root NATS path.
2. Keep Class A lifecycle/control reports idempotent, acked, and replayable
   within a bounded window.
3. Keep route frames in a separate lower-priority pressure domain. Route
   backpressure must not starve control reports or heartbeats.
4. Use HTTP report/pull endpoints as a recovery and brownout path for
   request-scoped control state, not as the primary route-frame transport.
5. Preserve the sidecar across runtime restart and A/B slot promotion so root
   continuity is not tied to the restartable runtime process.
6. Record transport transitions, route flaps, and local runtime latency as
   domain-attributed incidents. Operators and LLM tools should see whether the
   failure belongs to `hub_root`, `hub_root_browser`, `core.sidecar`,
   `core.runtime`, or a skill/member owner.

This means NATS/sidecar is enough for the current root-hosted topology only if
the protocol obligations above are visible and tested. Adding another subnet
broker is not a substitute for Class A replay, resource isolation, and incident
attribution.

## Traffic classes

Hub-root traffic must be split into real execution classes, not only names.

### Control class

Examples:

- lifecycle transitions
- auth refresh
- update orchestration
- route-install readiness

Requirements:

- highest priority
- bounded queue
- acked
- durable when Class A

### Integration class

Examples:

- Telegram send job
- GitHub update check/report
- LLM task execution state

Requirements:

- per-integration buffering policy
- separate backlog accounting
- independent readiness reporting

### Route class

Examples:

- proxy route frames
- hub route request/reply envelopes

Requirements:

- isolated resource budgets
- slow consumer in route class must not starve control class

### Sync metadata class

Examples:

- sync snapshot requests
- sync cursor negotiation

Requirements:

- replayable within bounded window
- lower priority than control

## Required protocol fields

Every replayable or durable hub-root message must carry:

- `stream_id`
- `message_id`
- `message_type`
- `delivery_class`
- `issued_at`
- `ttl_ms`
- `authority_epoch`

Additionally:

- acked messages carry `ack_required=true`
- replayable messages carry `cursor`
- responses carry `request_id`

## Cursor and replay model

### Streams

Cursoring is defined per logical stream, not globally.

Examples:

- `hub-control:<hub_id>`
- `hub-integration:telegram:<hub_id>`
- `hub-integration:github:<hub_id>`

### Replay window

Replay is bounded.
AdaOS does not attempt infinite replay of all traffic.

Recommended target:

- Class A: durable bounded replay
- Class B: optional bounded replay
- Class C: no replay

### Resume

On reconnect:

1. hub reports last durable cursor per stream
2. root replays eligible messages after that cursor
3. hub dedupes by `message_id`
4. stream returns to steady state only after control readiness is restored

## Outbox and inbox model

### Hub outbox

Required only for Class A outbound messages and selected integration messages.

Properties:

- durable local storage
- retry state
- per-stream ordering where required
- dedupe-safe resend

### Root inbox dedupe

Required for replayable and retryable commands.

Properties:

- bounded dedupe window
- keyed by `message_id` and `stream_id`
- stores completion result where needed

## Idempotency rules

Not every operation can be replayed the same way.
AdaOS needs explicit command taxonomy.

### Retry-safe by idempotent handler

Examples:

- set lifecycle state to value `X`
- publish latest state report version `N`
- set route readiness status

### Retry-safe only with operation key

Examples:

- Telegram send request
- GitHub release acknowledgement
- integration job transition

These require stable operation keys and dedupe-aware completion handling.

### Not replayable

Examples:

- transient route frames
- presence changes
- media signaling hints that are session-local and expired

## Backpressure and resource isolation

Hub-root classes must not share one undifferentiated pressure domain.

The implementation must isolate:

- queue budgets
- worker budgets
- retry budgets
- drop policies

Minimum rule:

- route backlog must not block control acks or heartbeats

## Readiness conditions for hub-root

The hub-root protocol is ready only when all of these are true:

1. authenticated session established
2. authority freshness valid
3. control subscriptions ready
4. control ack path healthy
5. replay reconciliation finished

Route or integration readiness may still be degraded after control becomes ready.

## Observability requirements

The protocol layer must expose:

- current authority epoch
- per-stream last sent cursor
- per-stream last acked cursor
- outbox size by class
- dedupe hit counts
- replay counts
- reconnect count
- degraded reason
- normalized incidents for control, route, local runtime latency, and resource
  pressure

These metrics are protocol metrics, not transport-only metrics.

## Application Development Report Relay

Cross-subnet Application feedback reuses this protocol rather than creating a
stateless HTTP callback path. The semantic contract is defined by
[Application Lifecycle, Distribution, and Feedback](application-lifecycle-and-distribution.md).

Development Report payloads are end-to-end encrypted to the publisher subnet's
purpose-scoped message-encryption key and independently signed by the sender.
Root can read only the bounded routing header required for delivery. It stores
ciphertext while a publisher is offline and therefore acts as a durable
mailbox/relay, not a semantic ticket owner.

Same-zone flow:

```text
guest Hub outbox -> zone Root inbox/mailbox -> publisher Hub inbox
```

Cross-zone flow:

```text
guest Hub
  -> guest home-zone Root
  -> publisher home-zone Root
  -> publisher Hub
```

Each durable hop requires message and stream identity, TTL, idempotent
acceptance, bounded retry, destination ACK, dead-letter disposition, and
backpressure isolation. Cross-zone forwarding additionally carries signed
directory generation, destination zone, hop limit, and previous-hop receipt.
Root-to-Root transport is authenticated and a forwarding ACK means durable
acceptance by the next Root, not delivery to the publisher.

Public report status events use the reverse durable path and a monotonic report
revision. After a cursor gap, the guest requests an authoritative bounded
status snapshot instead of inferring terminal state from missing events.

Same-zone durable relay is the first implementation slice. Cross-zone
store-and-forward is required before inter-zone Application beta testing.
Multiple independent Root relays inside one zone, automatic failover, and
subnet home-zone migration remain deferred.

## Mapping to current code

Current code already contains the transport-oriented pieces:

- hub NATS session issuance on root
- hub bridge and reconnect handling
- route subjects and route proxy

Those pieces must be evolved toward the protocol defined here rather than only patched at the socket layer.
