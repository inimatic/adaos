# Runtime Data Route Reliability

Status: implemented baseline, validated against Research Workbench on 2026-08-12.

The exact local releases, test counts, live API controls, and residual risks
are recorded in
[Runtime Data Route Reliability: 2026-08-12 Evidence](runtime-data-route-reliability-evidence-2026-08-12.md).

## Purpose

Browser-visible data is delivered through AdaOS system paths: bounded tool
reads, streams, Yjs projections, and direct API reads. A scenario or skill must
not add a private transport to compensate for lifecycle, reconnect, or error
handling defects in the core.

Research Workbench is the reference workload for the bounded `tool/details`
path. Its portfolio is a causal snapshot, not collaborative state and not a
live event feed, so moving it wholesale to Yjs would obscure rather than repair
the transport contract.

## Reliability invariants

1. The route declared by a skill is the route executed by the client.
2. A browser read uses `live_reads`; a mutation uses `accept_mutations`.
3. A client read intent is only a routing hint. The execution node reclassifies
   the tool from the active resolved skill manifest.
4. A read hint for a mutating or undeclared tool fails closed with
   `tool_intent_mismatch`.
5. Lifecycle capabilities are authoritative independently of the aggregate
   lifecycle label. A transition may allow reads while blocking mutations.
6. Lifecycle suspension is event-driven. It does not create exponential HTTP
   polling.
7. A valid empty result is distinct from an unavailable source.
8. `preserve_last_value` and `max_request_hz` are executable policy, not only
   documentation.
9. Mutation invalidation is tag-addressed and must not refresh unrelated
   semantic read keys.
10. A manual retry addresses one semantic read identity even for advisory
    legacy sources that do not yet declare invalidation tags.
11. Every retry, stale result, lifecycle wait, and policy mismatch is visible
    in diagnostics or validation evidence.

## Trusted read intent

The declarative page runtime knows that a `kind: skill` data source is a read
because scenario validation resolves the exact dependency, tool, side-effect
class, and `data_route`. It calls `AdaosClient.callSkill(..., {intent: "read"})`.
The HTTP request is still POST because it is a typed tool invocation; method
alone does not define lifecycle capability.

The API reads `side_effects` from the active resolved manifest before granting
read treatment. During drain, trusted read-only tools may complete while
mutating tools are rejected. This preserves continuity without allowing an
arbitrary browser payload to bypass mutation governance.

The intent survives both HTTP forwarding and the hub-to-member RPC fast path.
The member repeats the resolved-manifest check before execution. The hub's
classification therefore cannot accidentally downgrade a different active
package on the target node, and draining a member does not block its admitted
reads or admit a mutation through the RPC shortcut.

Generic `callSkill` actions retain mutation-safe defaults. They request
`accept_mutations` unless a core-owned, validated read path supplies the read
intent.

## Lifecycle and retry model

`HubMemberChannelsService` evaluates a capability together with freshness,
route readiness, and any routed-authority hold. The aggregate state such as
`warming` or `degraded` is diagnostic context, not an extra implicit gate after
the server has advertised a capability.

When a request is suspended, its error contains the capability, state, reason,
revision, lease validity, route readiness, and authority-hold status.
`PageDataService` waits on `waitForLifecycleCapability(...)`; it does not send
another HTTP request until a newer authoritative lifecycle event releases the
waiter. Ordinary transient transport failures use a bounded four-attempt retry
policy with an eight-second maximum delay.

## Last value, source state, and request rate

Runtime data sources have a session-bounded state:

- `loading`: no value and a request is in flight;
- `refreshing`: a previous value exists and is being revalidated;
- `ready`: the latest read succeeded;
- `stale`: the last successful value remains visible after a failure;
- `unavailable`: no value exists and lifecycle currently suspends the route;
- `error`: no value exists and the failure is not a lifecycle suspension.

The state is exposed separately from domain payloads. Skills do not need to
wrap every result in a client-specific envelope. Generic list rendering hides
empty-state text while the source is unavailable and offers Retry. The widget
host provides the same status affordance to other runtime-backed widgets.

Last values, rate timestamps, status subjects, and request observables are
bounded by the runtime cache budget. `maxRequestHz` is enforced per semantic
identity: route, normalized arguments, webspace, and relevant state values.
Retry invalidates only that identity; it does not turn an absent legacy tag
declaration into a global refresh.

## Manifest and scenario conformance

The skill manifest owns the design contract:

```yaml
read_policy:
  cache_ttl_ms: 0
  max_request_hz: 2
  preserve_last_value: true
  invalidation_tags: [research.portfolio]
```

The scenario owns its executable mapping:

```json
{
  "cacheTtlMs": 0,
  "maxRequestHz": 2,
  "preserveLastValue": true,
  "invalidationTags": ["research.portfolio"]
}
```

Scenario validation checks exact tag sets, last-value behavior, and request
rate. Existing scenarios receive advisory diagnostics. A scenario opts into a
release-blocking contract with:

```yaml
runtime_data_policy:
  enforcement: strict
```

Research Workbench is strict. This staged adoption prevents a core hardening
change from silently invalidating the existing catalog while ensuring that new
or migrated scenarios cannot publish policy drift.

## Verification matrix

| Condition | Expected result |
| --- | --- |
| Mutations blocked, live reads allowed | `tool/details` renders; actions remain blocked |
| Read hint targets a mutating tool | API returns `tool_intent_mismatch` |
| Node drains | trusted read succeeds; mutation returns `node_draining` |
| Lifecycle capability denied | one HTTP attempt, then event wait |
| Capability becomes allowed | suspended read resumes once |
| Refresh fails with preserved value | old value remains and state is `stale` |
| Initial read fails | state is `unavailable` or `error`, never valid empty |
| Repeated invalidation exceeds route rate | request start is delayed to `maxRequestHz` |
| Unrelated invalidation | zero calls |
| Explicit Retry without tags | exactly the selected semantic source reloads |
| Read routed to a member | intent reaches RPC/HTTP target and is re-verified there |
| Manifest/WebUI policy differs in strict mode | scenario validation fails |

The 2026-08-12 catalog audit found no newly introduced policy errors. Two
scenarios remain invalid for pre-existing missing dependency-tool declarations.
Non-strict legacy policy drift is reported as warnings for planned migration.
Research Workbench passes strict validation.

## Compatibility boundary

This contract improves bounded request/response data. It does not turn
`tool/details` into a subscription and does not replace Yjs or streams:

- use Yjs for shared, reconnect-stable collaborative state;
- use streams for ordered or high-frequency observations;
- use `tool/details` for bounded causal snapshots and details;
- use actions for commands and mutations with compact acknowledgements.

MLflow, database providers, or research skills remain above this boundary.
They may provide typed data, but they do not own browser transport, lifecycle
gating, retry, or source-state semantics.
