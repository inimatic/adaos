# Webspace SharedWorker Evaluation

Status: evaluated; no default broker adoption in Phase 11.

## Decision

Keep one page-scoped browser transport identity per open page. Do not replace
it with a SharedWorker, BroadcastChannel leader, or server-side collapsed
device identity in the current Builder lifecycle refactoring.

The present model already fixes the failure that motivated this evaluation:
`peer_id`, `browser_session_id`, build identity, adapter ownership, and
cancellation are page scoped, while all pages in one `webspace_id` converge on
one authoritative Yjs document. Scoped map-leaf projection writes and bounded
structural compaction address payload amplification independently of transport
sharing.

## Options considered

| Option | Benefit | Cost or risk | Phase 11 decision |
| --- | --- | --- | --- |
| One transport per page | Explicit ownership, cancellation, authorization and diagnostics | More sockets for many tabs | Keep as default |
| SharedWorker per origin | Can share WS/YWS/WebRTC signaling between same-origin tabs | Adds worker lifecycle, upgrade skew, route multiplexing, per-tab backpressure and recovery authority | Do not adopt without measured pressure |
| BroadcastChannel leader | No dedicated worker requirement | Leader election, split brain, hidden-tab throttling and failover ambiguity | Reject as an authority layer |
| Collapse peers by `device_id` on the server | Fewer peers | Recreates the proven cross-tab peer replacement defect and loses per-tab evidence | Prohibited |

## Admission contract for a future broker

A broker experiment is allowed only when production evidence shows that exact
live page sessions, after leaked-adapter cleanup and delta projection, still
cause unacceptable connection, CPU, memory, or reconnect pressure. The
experiment must preserve:

- page-scoped authorization, `browser_session_id`, cancellation and delivery
  receipts;
- explicit per-tab subscription/backpressure and bounded queues;
- make-before-break worker upgrade across client builds;
- direct fallback to a page-owned transport when the worker is unavailable;
- one shared authoritative Yjs room, with no local fork or worker-owned source
  of truth;
- diagnostics that distinguish physical connections from logical pages;
- browser acceptance for worker startup, tab close, crash, refresh, version
  skew, offline recovery and sign-out.

Until those gates are met, SharedWorker is an optional transport optimization,
not part of workflow correctness or Builder readiness.
