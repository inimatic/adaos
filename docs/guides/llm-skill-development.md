# Builder-Safe Skill Development Guide

Status: current guidance and target contract.

This guide is written for Builder workflows that create or update AdaOS skills.
The Builder may be a human, an AI-assisted agent, or a human-in-the-loop
combination. Its goal is simple: a generated or assisted skill should be useful
without being able to overload the shared desktop, hide failures, or bypass
runtime governance.

Read this together with:

- [AdaOS Builder](../architecture/builder.md)
- [Skill Factory and Isolated Dev Nodes](../architecture/skill-factory.md)
- the repository note `docs/interfaces/webio.md`
- [UI Addressing](../architecture/ui-addressing.md)
- [Named Entities and Canonical Naming](../architecture/named-entities.md)
- [Semantic State Plane](../architecture/semantic-state-plane.md)
- [Runtime Guarding](../architecture/runtime-guarding.md)
- [Projection Subscription Roadmap](../architecture/projection-subscription-roadmap.md)

## Golden rule

Do not treat the primary Yjs document as a free-form database.

Normal skill-owned browser-visible state must go through governed SDK helpers
and declared projection routes. Direct Yjs mutation from a skill is legacy or
explicitly capability-gated, not the default authoring model.

Preferred data-plane choices:

- `data_projections` plus `ctx_subnet.set()` / `ctx_subnet.set_async()` for
  compact reconnect-stable bootstrap/control state.
- `stream_variable_publish()`, `stream_publish()`, and `webio.receivers` for
  high-churn live variables, append-heavy data, and operator-facing variables.
- skill-local storage for private durable skill state.
- tool/detail endpoints for explicit user-requested details.
- 360log or disk snapshots for later diagnostics, not browser steady-state
  rendering.

## Responsibility model

The skill author chooses the data route. The runtime does not silently move a
skill's data between Yjs and streams.

That choice is part of the skill design and must be visible in `skill.yaml`,
`webui.json`, handler code, and tests. For Builder-authored work, the route
choice must be treated as a reviewable implementation decision, not an
accidental side effect of the helper API used.

Runtime guardrails still enforce shared safety:

- Yjs owner guards count attempted and applied Yjs writes, attribute pressure to
  the skill owner, and may warn, throttle, block, or quarantine unsafe owners.
- Stream guards bound payload size, publish rate, snapshot request bursts, and
  receiver fanout, and must log suppressions or degraded delivery.
- Guards emit diagnostics and quarantine records so the UI and future Builder
  repair loops can explain the failure.
- Guards are emergency control, not a replacement for a well-designed data
  route.

The desired failure mode is explicit: a badly routed skill should become
visible as a design defect and be returned for repair. It should not be hidden
by runtime magic that makes the browser appear healthy while the skill keeps
producing unsafe data.

## Required data route plan

Before editing a browser-facing skill, write down the route plan. A concise
comment in the implementation notes, PR description, or adjacent docs is
enough, but the design must be explicit.

For every widget, modal section, status row, and detail view, answer:

- `surface`: what browser surface consumes this data?
- `route`: `yjs`, `stream`, `tool/details`, `skill-local`, or `disk/360log`.
- `why`: reconnect-stable bootstrap, live variable, explicit drill-down,
  private durable cache, or diagnostic evidence.
- `first_paint`: what does the user see before live data arrives?
- `recovery`: how does the surface recover after room rebuild, reconnect, or
  stream resubscribe?
- `update_source`: which events or commands can update it?
- `budget`: expected payload size, event rate, coalescing window, and maximum
  fanout.
- `guard_visibility`: what warning, degraded state, or incident is shown when
  the route is throttled, blocked, or quarantined?
- `memory_owner`: which module globals, caches, background workers, model
  objects, or file handles can retain data for this route, and how are they
  bounded and cleaned up?

If a route cannot answer these questions, do not add it yet.

## Machine-checkable route contract

Builder-authored browser surfaces must leave a contract that a reviewer or
future validator can check without reading every handler. Treat this as a hard
gate for generated skills, not prose-only guidance.

For every `data_routes` entry:

- `surface` must identify one widget, modal section, status card, table,
  stream receiver, or details action.
- `route` must be one of `yjs`, `stream`, `tool/details`, `skill-local`, or
  `disk/360log`; do not use vague values such as `mixed`, `auto`, or `status`.
- `budget.max_payload_bytes` is required for `yjs`, `stream`, and
  `tool/details` routes.
- `budget.max_items` is required for any route that can return a collection.
- `budget.max_publish_hz` or an equivalent debounce/coalescing statement is
  required for event-driven routes.
- Stream budgets must state the fanout assumption. Use
  `budget.max_fanout` when the surface can be open in more than one browser,
  member, or mirrored desktop, and review the effective budget as
  `max_payload_bytes * max_fanout`. If no better bound is known, assume
  `max_fanout: 3`. The effective bytes must stay below the current owner-guard
  cap with margin; a payload that is safe for one subscriber can still
  quarantine the skill when replicated to several clients.
- `guard_visibility` must name the degraded state or repair evidence that the
  Builder can inspect when the route is guarded, throttled, blocked, or
  quarantined.
- `projection_slot`, `receiver`, or `tool` must point to the actual
  `data_projections`, `webui.receivers`, or `tools` entry used by the route.
- A tool that refreshes a stream or projection must declare and return a
  compact acknowledgement shape. It must not return the same message list,
  table, snapshot, or diagnostic body that it just published through the
  declared route.

For every `data_projections` entry:

- the slot must map to exactly one reviewable `data_routes` entry
- the target path must not be a broad root such as `data`, `data/nodes`,
  `ui`, or `registry`
- the route budget should default to `max_payload_bytes <= 65536` and
  `max_items <= 100` unless the skill explains a tighter reconnect-stable
  reason
- any budget above `262144` bytes or `1000` items requires an explicit
  migration plan to stream/page/details before publish

Generated code should fail review when a browser-facing skill has
`data_projections` without a matching bounded `data_routes` contract, or when a
tool response is the real data transport but the route plan claims Yjs or
stream ownership.

### Causal read policy for tool-backed surfaces

`tool/details` is an explicit read boundary, not a subscription emulation.
Every browser-visible tool route must name the exact `tool` and declare a
`read_policy`:

```yaml
data_routes:
  - surface: widget:project.overview.state
    route: tool/details
    tool: get_project
    first_paint: last successful value or a stable skeleton
    recovery: preserve the last value and expose an explicit retry
    budget:
      max_payload_bytes: 32768
      max_items: 100
    read_policy:
      mode: stale_while_revalidate
      triggers: [mount, explicit_refresh, targeted_invalidation, state_dependency_changed]
      cache_ttl_ms: 60000
      max_request_hz: 0.1
      preserve_last_value: true
      invalidation_tags: [builder.project.metadata, builder.project.lifecycle]
    guard_visibility:
      degraded_state: project state is stale
      metric: data_route.tool_read
```

The browser must key a read by semantic identity: skill/tool name, normalized
arguments, webspace, and relevant state dependencies. Recreating a schema
object, rerendering a widget, receiving an unrelated Yjs update, expiring a
presentation observable, or completing an unrelated `callSkill` action is not
a valid request cause.

Allowed triggers are explicit and finite:

- `mount`: the first subscription for a semantic read key;
- `explicit_refresh`: a user or operator asks for current data;
- `targeted_invalidation`: a successful mutation invalidates one of the
  route's declared tags;
- `state_dependency_changed`: an argument such as selected project actually
  changes by value;
- `reconnect`: only when the declared consistency mode requires a fresh read.

Do not use an interval, focus event, generic application-state update, global
action completion, or object identity change as an implicit trigger. A tool
route cannot use `read_policy.mode: live` or a subscription
`snapshot_policy`. If the surface genuinely needs live updates, move it to a
bounded stream or Yjs projection.

Invalidation must be addressable. Mutation actions publish concrete tags such
as `builder.project.metadata`; consumers refetch only when their semantic key
and tags match. Keep the last successful value visible while revalidating when
`preserve_last_value` is true. Enforce `max_request_hz` independently of cache
TTL so cache churn or repeated invalidations cannot turn into a server burst.

The scenario must carry the executable side of this contract in `webui.json`.
Map the skill route policy to camelCase browser fields and keep the tags exact:

```json
{
  "dataSource": {
    "kind": "skill",
    "name": "recipes.list_recipes",
    "cacheTtlMs": 0,
    "maxRequestHz": 0.1,
    "invalidationTags": ["recipe.catalog"],
    "preserveLastValue": true
  },
  "actions": [{
    "type": "callSkill",
    "target": "recipes.add_recipe",
    "invalidates": ["recipe.catalog"]
  }]
}
```

`cacheTtlMs: 0` means that a stable semantic read does not expire by wall-clock
time; it is replaced only when its state-dependent arguments change or a
matching invalidation arrives. This is the default safe behavior for runtime
skill/API reads. Use a positive TTL only when domain freshness requires it and
the route budget explicitly permits expiry-driven revalidation. Scenario
validation must cross-check every skill datasource and `callSkill` target
against declared dependencies, exported tools, tool side effects, and exact
`data_routes`; incomplete DEV folders must not shadow a valid workspace skill.

For new or migrated scenarios, enable release-blocking conformance:

```yaml
runtime_data_policy:
  enforcement: strict
```

Strict mode requires `invalidationTags`, `preserveLastValue`, and
`maxRequestHz` to execute the exact `read_policy` declared by the skill. In
advisory mode the same drift is reported as warnings so an existing catalog can
be migrated without disguising incompatibility as success. Runtime source
failure must be presented as stale/unavailable/error; it must never reuse a
domain empty-state message.

Lifecycle suspension is not a normal transient HTTP retry. A system client
waits for the named authoritative capability event and performs no further
tool calls while suspended. Browser-supplied read intent is a routing hint;
the execution node must verify the active resolved manifest before allowing a
read during a mutation-blocking transition. This rule applies after routing:
HTTP proxy and hub/member RPC must carry the intent to the target, and the
member must repeat the manifest check. Do not trust a classification made only
by the browser or an intermediate hub.

An explicit Retry is identity-addressed even when a legacy/advisory source has
no invalidation tags. It must reload only the selected skill/tool plus resolved
arguments and webspace; never implement Retry as a global data-source
invalidation.

Add an idle-soak test for every stable tool-backed widget: after first paint,
leave the selected entity and its dependencies unchanged for at least three
times the cache TTL (or 60 seconds when TTL is zero) and assert that the server
observes zero additional tool calls. Then execute an unrelated mutation and
assert zero calls, followed by one matching targeted invalidation and assert
at most one coalesced call.

## Memory and reload safety

Process memory is a data plane too. Browser-facing skills run inside long-lived
runtime processes, can be smoke-imported before activation, and can be reloaded
without a process restart. Treat every module global as retained until proven
otherwise.

Use these rules for every skill that owns subscriptions, streams, projections,
background work, or heavy resources:

- Keep import time passive. Importing a handler module must not start threads,
  load large models, open sockets, register external callbacks, publish events,
  or mutate persistent state. Smoke imports should be safe to repeat.
- Make reloads idempotent. Runtime-owned bus subscriptions are deduplicated by
  the core, but skill-owned threads, timers, executors, external callbacks, and
  resource handles still need an owner token, stop signal, and cleanup hook.
- Verify slot-switch behavior without a process restart. After install or
  activate, the next tool call and the next subscription callback must use code
  from the active slot. If old behavior remains in memory until API restart,
  treat it as a runtime reload defect and capture it in the repair evidence.
- Expose a cheap version or fingerprint tool for service/debug skills when the
  deployment path is under active development. It should report skill version,
  active slot when provided by runtime metadata, and the loaded handler source
  path without returning large state.
- Bound every cache. Prefer `deque(maxlen=...)`, LRU/TTL caches, and explicit
  byte or item budgets over plain module-level `list` and `dict` accumulators.
  Per-webspace, per-receiver, and per-device state needs an eviction policy.
- Store fingerprints before payloads. A last-good snapshot cache is acceptable
  only when it has a size/TTL budget; otherwise keep compact hashes, freshness,
  and the minimal state needed to avoid duplicate publishes.
- Treat background workers as lifecycle resources. `Thread`, `asyncio` tasks,
  timers, schedulers, `ThreadPoolExecutor`, subprocesses, and playback loops
  must stop during `drain`, `dispose`, quarantine, or deactivation.
- Lazy-load heavy resources and release them. ML models, media indexes, large
  parsers, embeddings, and device sessions should load on demand, expose a
  `dispose` path, and cap both memory and disk caches.
- Do not keep large tool or diagnostic responses alive in globals, logs, or
  exception objects. If the browser needs large detail data, use a details tool,
  stream snapshot, or disk evidence route with bounded retention.
- Log memory-protection actions as normal operational telemetry: cache eviction,
  stale worker cleanup, rejected oversize payloads, disabled stream sections,
  and skipped refreshes under pressure.
- Treat runtime memory evidence as parent/child/family attribution, not one
  process number. When investigating growth, record `managed_pid`,
  `current_process_rss_bytes`, `current_family_rss_bytes`, child RSS by
  `skill_runtime`, `baseline_phase`, `baseline_last_adjustment_reason`, and
  `suspicion_state`. A cold-start baseline in `warming` or
  `maturity_blocked_slope` is not the same evidence as mature-baseline growth.
- Separate API starvation from memory growth. Event-loop lag, listener loss,
  and supervisor self-heal evidence are core signals; do not "fix" a skill for
  a memory leak until the repair packet says whether the runtime was blocked,
  growing, or both.

The minimum verification for a memory-sensitive skill is:

- import the handler module repeatedly and verify no threads, model loads,
  sockets, subscriptions, or persistent writes are created at import time
- activate/reload the same skill at least three times and verify active
  subscription counts, worker counts, cache sizes, and receiver state do not
  grow linearly
- run a short burst/soak using the skill's hottest events and stream subscribe
  requests; RSS should plateau after warmup, and guard logs should explain any
  throttling or dropped oversized payloads
- for child-process skills, confirm child runtimes exit or stay within budget
  after idle, and confirm parent RSS either relaxes or produces a named blocker
  in memory status

### Page-state and full-surface layout rules

For a UI-as-data page with materially different modes, declare
`layout.variants` instead of stacking multiple `role: main` areas and hiding
their widgets one by one. Use one explicit view-mode state plus the selected
entity identity, for example:

```json
{
  "initialState": {"viewMode": "portfolio", "selectedId": null},
  "layout": {
    "type": "single",
    "areas": [{"id": "portfolio", "role": "main"}],
    "variants": [
      {
        "id": "detail",
        "when": "$state.viewMode === 'detail' && $state.selectedId != null",
        "type": "split",
        "pattern": "focus-detail",
        "areas": [
          {"id": "workspace", "role": "main"},
          {"id": "context", "role": "aux"}
        ]
      },
      {
        "id": "portfolio",
        "default": true,
        "type": "single",
        "areas": [{"id": "portfolio", "role": "main"}]
      }
    ]
  }
}
```

Update `viewMode` and `selectedId` in the same declarative action. On back,
clear both in the same action. Never rely on `undefined != null` to reveal a
detail view; the runtime treats loose null comparisons only as explicit
nullish checks. Page state is local to its webspace/scenario/page and is not a
replacement for Yjs, streams, or tool-backed durable domain state.

When an LLM consumes skill-owned source artifacts, use the typed artifact SDK
extraction envelope rather than truncating raw notebook JSON. Report source
coverage and fragment refs to the user; bind generated claims only to supplied
refs. Do not let a model-provided `ready` flag substitute for a deterministic
admission review.

## Data-plane decision table

| Need | Use | Avoid |
| --- | --- | --- |
| Bootstrap/control state needed for first paint | Yjs projection | Full operational snapshot in Yjs |
| Selected ids, compact health badge, latest stable status | Yjs projection | Rewriting `data`, `ui`, or `registry` broadly |
| Operator-facing variables, active operations, logs, telemetry, chat/event tail | Stream receiver | Unbounded arrays in Yjs |
| Big diagnostics or object inspector payload | Details tool / stream snapshot / disk snapshot | Embedding full diagnostics in primary Yjs |
| Small operator health/guard summary | Status card pointing to stream/tool/details route | Treating `statusPlane` as a live data route |
| Durable private skill cache | Skill-local files or DB | Hidden browser-only state as source of truth |
| Command from UI to runtime | `callHost` / tool with small ack | Large command response used as data transport |
| Raw high-frequency evidence | Stream or disk/360log | Smoothed Yjs status that loses diagnostic truth |
| Smoothed operator status | Debounced stream or compact Yjs badge | Flickering every raw transport event |

## Skill manifest checklist

Every browser-facing skill should make its data contract explicit.

Use `skill.yaml` to declare:

- `data_routes` for the reviewable route plan: surface, route, first paint,
  recovery, budget, and guard visibility.
- `tools` with stable input and output schema.
- `exports.tools` for callable public tools.
- `events.subscribe` for command or domain events.
- `data_projections` only for browser-visible Yjs branches the skill owns.
- `webui.receivers` in `webui.json` for live stream variables.
- `memory_budget` for owned caches, per-webspace state, background workers,
  loaded resources, and expected RSS/retention behavior.
- optional lifecycle hooks such as `healthcheck`, `drain`, `dispose`, and
  `onQuarantine` / `on_quarantine` when the skill can clean up or explain a
  guard action.

Every declared Yjs projection should have a reason to be reconnect-stable.
Every stream receiver should have bounded delivery semantics and an initial or
snapshot-on-subscribe story. Every declared worker, cache, and heavy resource
should have an owner, budget, and cleanup path.

Quarantine hooks are discovered as ordinary tools named exactly
`onQuarantine` or `on_quarantine`. Listing a cleanup function only under
`lifecycle` is not enough for owner-guard quarantine; declare the quarantine
tool in `tools` as well when the skill can record compact repair evidence.

Example:

```yaml
data_routes:
- surface: widget:weather_status
  route: yjs
  projection_slot: weather.snapshot
  first_paint: cached compact weather status
  recovery: Yjs replay restores the latest compact status
  update_source: [weather.refresh.completed]
  budget:
    max_payload_bytes: 4096
    max_publish_hz: 0.2
    snapshot_policy: on_subscribe
  guard_visibility:
    degraded_state: weather status shows stale/degraded
    log: service.weather_skill.runtime.log
    quarantine: true
- surface: modal:weather_history
  route: stream
  receiver: weather.history
  first_paint: empty history with loading state
  recovery: bounded stream snapshot requested on subscribe

data_projections:
- scope: subnet
  slot: weather.snapshot
  targets:
  - backend: yjs
    path: data/weather

memory_budget:
  expected_rss_mb: 64
  caches:
  - name: weather.snapshot_cache
    max_items: 32
    ttl_seconds: 300
  background_workers:
    max_threads: 1
    cleanup_hook: dispose

tools:
- name: get_snapshot
  description: Return the compact current weather state.
  entry: handlers.main:get_snapshot
  input_schema:
    type: object
    properties:
      webspace_id:
        type: string
      target_node_id:
        type: string
  output_schema:
    type: object
    required: [ok]
    properties:
      ok:
        type: boolean
      current:
        type: object
```

## Conversation and memory APIs

Generated skills should use the conversation SDK for user-visible dialog
instead of publishing `io.out.chat.append` directly. The runtime can then
persist the ledger, render speech/chat targets, attach trace diagnostics, and
keep browser history durable.

Preferred:

```python
from adaos.sdk import chat, memory


def talk(payload: dict) -> dict:
    meta = payload.get("_meta") or {}
    conversation_id = meta["conversation_id"]
    context = payload.get("conversation_context") or chat.context(
        conversation_id,
        requester_owner="skill:example",
        channel_id=meta.get("dialog_channel_id"),
        agent_id=meta.get("active_agent_id"),
    )

    chat.send(
        "I can help with that.",
        conversation_id=conversation_id,
        webspace_id=payload.get("webspace_id"),
        channel_id=meta.get("dialog_channel_id") or "general",
        owner="skill:example",
        actor_id=meta.get("active_agent_id"),
        actor_label=meta.get("active_agent_label"),
        turn_trace_id=meta.get("turn_trace_id"),
    )
    return {"ok": True, "used_context_tokens": context.get("token_estimate", 0)}
```

A tool may also return a structured response and let the runtime materialize it:

```python
return {
    "ok": True,
    "response_envelope": {
        "conversation_id": meta["conversation_id"],
        "content": [{"type": "text", "text": "Done."}],
        "render_targets": ["text_tail", "speech_text"],
    },
}
```

Use `chat.send(...)` instead of a bare response envelope when the caller is a
direct fallback path that does not run `materialize_tool_result` after the tool
returns. Voice `nlp.intent.not_obtained` fallback is one such path: the skill
must materialize its visible reply during the tool call, while still letting the
router project the compact `voice_chat.messages` tail from the ledger.

Use scoped memory helpers instead of arbitrary transcript files:

```python
policy = memory.write_policy(
    "agent_preference",
    owner="skill:example",
    agent_id=meta.get("active_agent_id"),
)
memory.remember(
    scope=policy["scope"],
    owner=policy["owner"],
    subject_id=policy["subject_id"],
    key="style",
    text="prefers concise answers",
    confidence=0.8,
    consent_state=policy["consent_state"],
    policy=policy["policy"],
    source_ref={"conversation_id": meta["conversation_id"]},
)
```

Avoid:

- direct `io.out.chat.append` for ordinary replies
- writing raw chat history to skill-local files
- reading cross-owner memory without an explicit policy decision

## Browser-visible Yjs writes

Use logical slots, not raw paths, in handler code.

Yjs is for the minimum reconnect-stable state needed to bootstrap the surface,
preserve collaborative/control state, and explain health. It is not the normal
transport for changing variables, diagnostic tables, event tails, or raw
runtime evidence.

Preferred:

```python
from adaos.sdk.data import ctx_subnet

ctx_subnet.set(
    "weather.snapshot",
    {"current": current},
    webspace_id=webspace_id,
)
```

For async handlers:

```python
await ctx_subnet.set_async(
    "adaos_connect.current",
    current,
    webspace_id=webspace_id,
)
```

Avoid in normal skills:

- `webspace_ydoc`
- `get_ydoc()`
- `async_get_ydoc()`
- direct `y_py` transactions
- replacing broad roots such as `data`, `ui`, `registry`,
  `data.catalog`, `data.installed`, or `data.desktop`
- writing hot telemetry, logs, session churn, transport events, or stream tails
  into Yjs because a widget needs to see them

If a legacy skill still needs direct Yjs access, document why and keep it on a
short migration path toward `ProjectionService` / `ctx_subnet`.

Make hot projection writes idempotent before calling the SDK helper. Runtime
projection code can skip physical no-op mutations, but guard/governance checks
still see the attempted write. For refresh-heavy skills, keep a small
per-`(webspace_id, slot)` fingerprint and do not call `ctx_subnet.set*()` when
the semantic payload has not changed. Keep an explicit recovery path, such as a
user/API `refresh_snapshot`, that can bypass this fingerprint when the browser
reports a missing projection after room rebuild or reconnect.

Do not fan out routine projection refreshes to every webspace by default.
Target the webspace from event metadata or the UI action. Reserve all-webspace
fanout for boot, activation, migration, or explicit resync events.

Yjs payloads should be small enough to inspect in logs and reason about in code
review. If a projection is hard to summarize in one short schema paragraph, it
is probably too large for Yjs and should be split into stream variables or
details.

### Large list and table pattern

Never publish a full library, inventory, search result, history, log, or
diagnostic table into Yjs. This rule applies even when the current test data is
small. Design for the plausible upper bound of the domain.

Use this split:

- Yjs summary: `ok`, `state`, `count`, aggregate bytes, freshness,
  capabilities, degraded/quarantined marker, and route references.
- Page/search route: bounded rows with `limit`, `cursor` or `offset`, filters,
  stable sort, and a response budget.
- Details route: one object or one small batch by id.
- Stream receiver: only active progress, tail, or replace-mode current state
  that is subscribed and bounded.

Treat these as hard collection gates for generated skills:

- If a collection can plausibly exceed 100 rows, Yjs may contain only aggregate
  counters, freshness, small capability metadata, and route references.
- A page/search tool must default to a small page size and cap `limit` at 100
  unless a tighter domain budget is declared.
- Cursor pagination is preferred for large collections; offsets are acceptable
  only for small diagnostic jumps or when the backing store can seek cheaply.
- Refresh actions must return compact acknowledgements. They must not return the
  just-published collection or page payload.
- Tests must include a synthetic large-library/table case. The Yjs summary size
  should be effectively constant at 10k, 100k, and the skill's stress envelope.

Example for a media library:

```yaml
data_routes:
- surface: widget:media_summary
  route: yjs
  projection_slot: mediaserver.library
  first_paint: compact media count and scan state
  recovery: Yjs replay restores only summary and route refs
  budget:
    max_payload_bytes: 8192
    max_items: 16
    max_publish_hz: 0.1
  guard_visibility:
    degraded_state: media widget shows library summary unavailable
    repair_evidence: runtime.yjs_projection_guard
- surface: modal:media_library
  route: tool/details
  tool: list_media_page
  first_paint: empty table with loading state
  recovery: request first page after modal open or reconnect
  budget:
    max_payload_bytes: 65536
    max_items: 100
    max_publish_hz: 0.0

data_projections:
- scope: subnet
  slot: mediaserver.library
  targets:
  - backend: yjs
    path: data/media/library

tools:
- name: list_media_page
  description: Return one bounded media table page.
```

`refresh_snapshot` and similar commands must return a compact acknowledgement,
not the page data:

```json
{"ok": true, "accepted": true, "status": "refresh_scheduled", "count": 125000}
```

For household media, design the normal route for at least 25k-100k rows and
stress with a safety margin toward 500k synthetic metadata rows. The Yjs
summary must stay effectively constant size across that range.

## Stream data

Use streams for data that changes often, grows by appending, or represents
operator-facing variables that should not be durable collaborative state.

Streams are not a free replacement for Yjs. They are active volatile delivery:
messages can be missed during reconnect, subscriptions can flap, and duplicate
or out-of-order payloads can happen around recovery. Design every stream as a
bounded replace or append channel with explicit recovery.

For ordinary chat replies do not declare a custom message stream. Use
`adaos.sdk.chat.send(...)` or a response envelope so the runtime writes the
conversation ledger, updates `data.dialog.visible_tail`, and keeps memory/trace
metadata attached to the turn. Declare streams only for volatile status,
telemetry, progress, or media-style payloads.

Compatibility Voice surfaces may still declare a bounded receiver such as:

```json
{
  "webio": {
    "receivers": {
      "voice_chat.messages": {
        "mode": "append",
        "collectionKey": "items",
        "dedupeBy": "id",
        "maxItems": 100,
        "initialState": { "items": [] }
      }
    }
  }
}
```

For Voice compatibility receivers, the node conversation ledger remains the
source of truth. The skill may keep a bounded local cache only as a degraded
snapshot fallback; normal `get_snapshot`/stream snapshot code should read a
bounded ledger projection first. Keep `skill.yaml`, `webui.json`, handler
constants, and router-visible tail size aligned (`max_items` must match the
largest normal publish window), and verify the payload stays below the declared
fanout-adjusted budget.

Publish volatile state from the skill:

```python
from adaos.sdk.io import stream_publish, stream_variable_publish

stream_variable_publish(
    "voice_chat.status",
    {"state": "ready", "peer_count": 1},
    var_id="status",
    ttl_ms=30000,
    _meta={"webspace_id": webspace_id},
)

stream_publish(
    "example.progress",
    {"items": [{"id": "step-1", "state": "running"}]},
    _meta={"webspace_id": webspace_id, "target_node_id": target_node_id},
)
```

Stream rules:

- keep payloads bounded
- size streams against `payload_bytes * subscriber fanout`, not only against a
  single payload. A stream that is safe at one subscriber can hit guard pressure
  when multiple browsers or node-scoped mirrors are active.
- dedupe events with stable ids
- provide snapshot-on-subscribe for widgets that should not open empty
- coalesce repeated snapshot requests per receiver/webspace/node
- include `updated_at`, `seq`, stable ids, or a content fingerprint when the
  receiver needs to reject stale or duplicate payloads
- prefer `stream_variable_publish()` for replace-mode current-state variables;
  it wraps `id`, `value`, `seq`, `updated_at`, `fingerprint`, and optional
  `ttl_ms` consistently
- use `mode: "replace"` for current-state variables and include a complete
  bounded current value in each snapshot
- use `mode: "append"` only for true tails, with `maxItems`, `dedupeBy`, and
  a clear truncation policy
- provide an honest `initialState` such as `loading`, `stale`, `degraded`, or
  an empty bounded collection
- do not eager-publish a replace stream for the same state that the widget is
  already reading from Yjs; use streams for separate high-churn state or
  snapshot-on-subscribe recovery
- do not publish full replace snapshots from generic
  `subscription.changed` handlers. Treat subscription changes as demand
  bookkeeping; deliver initial state through the receiver's declared
  snapshot-on-subscribe or explicit refresh tool, with normal coalescing and
  byte-budget checks.
- do not copy stream tails back into Yjs just to make them visible
- for table/list inventory streams, publish a compact row shape containing only
  rendered columns, button predicates, stable ids, and action-feedback fields.
  Keep catalog metadata, dependency lists, diagnostics, and full version
  records behind details/tools unless the table renders them directly.
- keep the latest command result as a bounded acknowledgement, not as the
  original tool response. Persist only action id, target id/code, command id,
  status, error, and a short message. Large diagnostics, manifests, media
  payloads, policy objects, and transport traces must remain in details tools
  or diagnostic evidence.
- stream snapshot builders must read from bounded read models or compact caches.
  They must not call slow discovery, root relay, remote sync, or legacy fallback
  paths during browser state rebuilds; those belong behind explicit refresh,
  repair, or details actions with visible progress and failure.

Stream variables should be demand-aware. A stream receiver that is not
subscribed should not keep rebuilding full snapshots just in case a browser
opens later. Prefer receiver-specific builders over one monolithic skill
snapshot.

## Status cards

Use status cards for small operator summaries that must be cheap to poll,
stream, or project. A card is not a detail payload. It carries identity,
current state, freshness, and a pointer to the details route.

`statusPlane` is not a third data route. It is a compact index over the routes
you already declared in `data_routes`, `data_projections`, and
`webui.receivers`. If a card needs rows, inventories, logs, diagnostics, or a
tail, put those values in a stream receiver or details tool and put only the
reference in the card.

```python
from adaos.sdk.status import publish_status, publish_status_stream

publish_status(
    id="runtime",
    kind="runtime",
    scope="infrastate",
    status="ready",
    summary="runtime ready",
    ttl_ms=30000,
    details_ref={"kind": "stream", "receiver": "infrastate.runtime"},
    route={"kind": "stream", "receiver": "infrastate.runtime"},
    webspace_id=webspace_id,
)

publish_status_stream(
    "infrastate.runtime",
    id="runtime",
    kind="runtime",
    scope="infrastate",
    status="warning",
    summary="route reconnecting",
    ttl_ms=30000,
    webspace_id=webspace_id,
    _meta={"webspace_id": webspace_id},
)
```

Status card rules:

- use `status` values that normalize through `CanonicalStatus`: `ready`,
  `online`, `warning`, `degraded`, `down`, `offline`, or `unknown`
- keep `summary` short and operator-facing
- include `ttl_ms` for live runtime cards so stale UI can degrade honestly
- use `incident_id` only when the card represents a real active warning or
  incident
- put stream/tool references in `details_ref`; do not embed logs, tables,
  inventories, or tails into the card
- never declare `route: status` or `route: statusPlane`; the route belongs to
  Yjs, stream, details/tool, skill-local storage, or diagnostic evidence
- put the design-time data route in `route` so guard diagnostics can map
  pressure back to the skill route plan
- use `publish_status_stream()` when the card itself should also be available
  as a replace-mode stream variable
- verify cards through `GET /api/node/status/cards`; the compatibility
  `/api/node/reliability/summary` surface also carries a compact `statusPlane`
  block for badge/status UI during migration
- polling clients should prefer
  `GET /api/node/reliability/summary?mode=thin&webspace_id=<id>` and send
  `If-None-Match` on the next request; unchanged snapshots return `304`
  without rebuilding the full reliability payload
- use `GET /api/node/reliability/summary/metrics` during soak/debug runs to
  verify thin/full mode counts, response bytes, `304` reuse, and the compact
  `acceptance` block with status-registry, Yjs owner-guard/quarantine, stream
  guard, stream-control, and per-receiver pressure counters
- for a human-readable check, use
  `adaos node reliability-metrics --webspace <id> --receiver <stream>` and
  include the `acceptance.*` lines in soak notes
- verify `statusPlane.diagnostics.oversizedCardTotal == 0`; a nonzero value
  means a status card is being used as a payload container and needs a route
  redesign
- Yjs pressure, stream guard, and stream-control pressure are also projected as
  compact guard cards in `statusPlane`; use their `guardRef` to map observed
  pressure back to owner, route, receiver/path, budget, and quarantine context

## Hot events and smoothing

Some events are useful evidence but terrible UI clocks. Examples include:

- `browser.session.changed`
- `device.registered`
- `webrtc.peer.state.changed`
- YWS open, close, guard, quarantine, and reconnect events
- network route flaps
- fast operation progress ticks

Handle these as two different products:

- Raw evidence goes to diagnostics streams, bounded logs, or 360log so the
  operator and LLM repair loop can see what really happened.
- Operator-facing state is smoothed through debounce, coalescing, or a small
  state machine so short transport bumps do not shake the UI.

Recommended rules:

- coalesce by the narrowest useful key, usually
  `(webspace_id, device_id, receiver)` or `(webspace_id, node_id, section)`
- set an explicit burst window, for example 10-15 seconds for browser session
  churn
- publish the latest stable state, plus counters such as `flap_count`,
  `last_raw_state`, and `last_raw_at` when useful
- let hard states bypass smoothing: revoked, denied, auth required, guard
  quarantined, explicit user disconnect, or admin shutdown
- never trigger a full skill snapshot rebuild for each raw hot event
- do not write raw hot-event churn into Yjs
- use the shared `HotEventBudget` helper when turning hot raw events into
  status cards or stream variables; keep the raw event trail in diagnostics
  and publish only coalesced operator state

```python
from adaos.services.status import HotEventBudget

budget = HotEventBudget(debounce_ms=1000, window_ms=10000, max_events=5)
decision = budget.admit(
    "browser.session.changed",
    key=f"{webspace_id}:{device_id}",
)
if not decision.admitted:
    return
```

This smoothing is part of the skill design. Runtime guards may limit abusive
bursts, but they should not be the main mechanism that keeps the UI calm.

## Minimal UI plus details

The primary desktop Yjs document should contain the minimum state needed to
render the surface and explain whether it is healthy.

For heavy skills, prefer this split:

- minimal bootstrap/control state in Yjs
- operator-facing variables, active rows, and event tails in stream receivers
- details behind a `Details` action or modal
- full diagnostic evidence in disk snapshots or 360log

Good shape:

```text
data/infrastate/state
data/infrastate/subscriptions
stream:infrastate.summary
stream:infrastate.nodes
stream:infrastate.operations.active
tool:infrastate.get_details(section="logs")
```

Bad shape:

```text
data/infrastate = <full multi-thousand-line snapshot every refresh>
```

## Tool and action responses

UI actions should return small acknowledgements.

Preferred response:

```json
{
  "ok": true,
  "accepted": true,
  "status": "refresh_scheduled",
  "trace_id": "..."
}
```

Avoid returning:

- full browser snapshots
- full log files
- full scenario materialization payloads
- data already published into Yjs or stream receivers

If the UI needs the data, publish it through the declared data plane and return
only enough metadata for the user and logs to correlate the action.

When an action publishes a stream or projection update, the action response must
not also return that same snapshot. Use this shape instead:

```json
{
  "ok": true,
  "status": "refreshed",
  "receiver": "redevice_settings.state",
  "selected_ref": "redevice:endpoint-1",
  "count": 1,
  "updated_at": "..."
}
```

This keeps the action channel as a command/ack channel and the declared data
route as the only state delivery channel. Returning the full just-published
stream snapshot duplicates bytes, confuses guard attribution, and can quarantine
an otherwise correct skill under browser stream/action pressure.

## Member-aware skills

Member skills do not own transport. The runtime, router, hub-member link, and
browser choose the best delivery path.

Skill tools and handlers should accept optional routing fields:

```python
def get_snapshot(
    webspace_id: str | None = None,
    node_id: str | None = None,
    target_node_id: str | None = None,
    _meta: dict | None = None,
    **_: object,
) -> dict:
    ...
```

Rules:

- preserve `_meta.webspace_id` and `_meta.target_node_id`
- do not infer target node from global process state if the request already
  contains explicit routing metadata
- keep node-owned Yjs state node-scoped when it enters the shared desktop
- publish member stream data with `_meta.webspace_id` and node identity

## Names, aliases, and localization

Generated skills should treat human-facing names as presentation and input
resolution data, not as routing identity.

Use canonical refs for actions and storage:

- `device:member:<node_id>`
- `device:browser:<device_id>`
- `webspace:<webspace_id>`
- `scenario:<scenario_id>`
- `skill:<skill_name>`

Do not parse or persist a localized label as the only target id.
If a skill receives a phrase such as `work browser` or `рабочий браузер`, it
should let the named-entity resolver produce the canonical ref before dispatch.

Localization rules for generated skills:

- preserve exact user-confirmed names instead of translating them
- use localized aliases as resolver input, not as storage keys
- keep language-neutral observed labels such as hostnames under `locale: "und"`
- accept `request_locale` or `preferred_locales` metadata when the runtime
  provides it
- return canonical refs plus display labels in responses when humans need to
  see what was targeted
- treat runtime alias resolution as model-training neutral: aliases should
  appear in `entity_resolution` / trace evidence, not as required Rasa or
  neural retraining inputs
- propose alias changes through `sdk.data.entities.propose_alias_add`,
  `propose_alias_remove`, or `propose_alias_deprecate` plus the matching apply
  helper instead of mutating projected registry data directly; the apply result
  returns lifecycle event envelopes that the authoritative write path can
  persist and publish
- when adding an alias for an actual browser/member device, prefer
  `sdk.data.entities.add_device_alias(device_ref, alias, locale=...)`; use
  `remove_device_alias` to stop accepting an alias, and
  `deprecate_device_alias` to keep compatibility while marking the alias as
  old vocabulary. These helpers write through the governed access-link source
  and keep Yjs as a read-only projection
- when applying an alias change from a previously read registry item, pass the
  item's `fingerprint` as `base_fingerprint`; if the result is `stale`, reread
  the registry instead of retrying blindly
- MCP clients can use `add_device_alias`, `remove_device_alias`, and
  `deprecate_device_alias` from NLUAuthoringPlane only with a write-capable
  session such as `ProfileOpsControl`; read-only sessions should use
  `get_nlu_authoring_context` and `get_named_entity_registry`

## NLU and LLM hints

Prepare NLU/LLM hints while developing the skill. They are part of the skill's
human input contract, alongside `skill.yaml`, `webui.json`, and `redui.json`.
The runtime can derive a baseline from app titles, modal ids, actions, and
routes, but skill authors should add domain aliases and intent preferences that
cannot be inferred safely.

Recommended `webui.json` shape:

```json
{
  "nlu": {
    "llm_hints": {
      "aliases": {
        "app_id": {
          "browsers": ["browser sessions", "browser list"]
        },
        "modal_id": {
          "browsers_modal": ["browser sessions", "browser list"]
        }
      },
      "entities": [
        {
          "type": "modal_id",
          "value": "browsers_modal",
          "aliases": ["browser sessions", "browser list"]
        }
      ],
      "primary_actions": [
        {
          "intent": "desktop.open_modal",
          "slot": "modal_id",
          "value": "browsers_modal",
          "notes": "Use this when the user asks to show or open the skill UI."
        }
      ]
    }
  }
}
```

Rules:

- prefer canonical ids in `value`; aliases are only input labels
- include common localized names, product names, abbreviations, and operator
  slang for apps, modals, scenarios, and node/device refs
- describe the primary interface action when a skill exposes a modal; for
  example, "show/open <skill name>" should usually map to
  `desktop.open_modal`, not to scenario switching
- avoid giving the LLM direct SDK execution instructions; hints are used to
  build candidates that AdaOS previews, applies, traces, and rolls back through
  the normal NLU Teacher path
- keep hints compact and stable; runtime observations and user-confirmed
  aliases belong in the governed entity registry, not in ad-hoc prompt text

## Pending Actions

Use the core Pending Actions plane when a skill needs a human response that may
change runtime behavior later. Do not model this as a notification-only toast or
as a chat-local "yes/no" prompt.

Implementation-sensitive rules for generated skills:

- use `adaos.sdk.data.publish_pending_action` and
  `respond_pending_action`; bus-only integrations should publish command events
  to `pending_actions.publish.request` or `pending_actions.respond.request`
- identify the publisher and response handler with `node_id` plus skill or
  system actor identity; `skill_id` alone is not unique
- omit `ttl_s` or pass `None` when the action should not expire
  automatically; do not pass `0`, `""`, or `"0"` for "no TTL"
- publish stable action ids such as `approve`, `refuse`, `test`, and
  `postpone`; labels are presentation only
- set `allowed_actions[].terminal=false` for dry-run actions such as `test`,
  `preview`, or `postpone`; terminal responses close the Pending Action
- provide `label_i18n`, `title_i18n`, or `summary_i18n` plus fallback text for
  system-authored strings
- expect browser UI responses to arrive through
  `pending_actions.respond.request`; voice yes/no may be bound to the latest
  suitable Pending Action when `default_text_binding=true`
- keep human-confirmed names and device labels as params or display values, not
  translation keys or storage ids
- make response handlers idempotent; a repeated or late response must not apply
  the same mutation twice
- use explicit `response_route` for cross-skill handling; delegated
  subscription negotiation is deferred architecture work
- use `test` or `preview` for non-mutating checks when an approval would change
  durable state
- emit notifications only as pointers or outcome summaries; the Pending Action
  remains the source of truth for response state and audit

NLU Teacher specific runtime knobs:

- `data.nlu_runtime.flags.nlu_teacher_enabled=false` disables LLM Teacher use
  for the webspace without changing root policy; skills should not bypass this
  gate with direct LLM calls for Teacher-style repairs
- provider-specific examples such as `rasa_example`, `neuro_lite_example`, and
  `neural_example` are only verifiable for engines active in
  `data.nlu_runtime.flags`; prefer `descriptor_fix`, `entity_alias`, or
  `development_task` when the relevant engine is disabled

System-level producers include NLU Teacher confirmations, Builder review,
pairing/admission, runtime operation recovery, capability elevation,
destructive or external-IO actions, ambiguous routing, and guard/quarantine
recovery.

See [Pending Actions](../architecture/pending-actions.md).

## Guarding and quarantine

The runtime may warn, throttle, block, or quarantine a skill owner when either
Yjs or stream routes apply unsafe pressure.

Generated skills must not hide that state.

Recommended behavior:

- implement `onQuarantine` or `on_quarantine` when the skill can release
  resources or record context
- accept `ttl_s`, `reason`, `metrics`, `webspace_id`, and `owner`
- write a compact skill-local incident log for later LLM repair
- return structured errors such as `skill_owner_quarantined`
- let the Web UI render disabled/quarantined state instead of silently
  pretending the action succeeded
- expose which route was guarded: `yjs`, `stream`, `tool`, or `mixed`
- include the affected slot or receiver when safe to disclose
- keep enough local context to repair the data route, not just the symptom

The runtime owns the shared quarantine projection, for example `data.yjs_qrnt`.
Skills should not write that service branch directly.

Guard responsibilities:

- Yjs guard protects the primary document from oversized, too frequent, or
  poorly attributed writes.
- Stream guard protects event delivery from oversized payloads, receiver fanout,
  snapshot request storms, and publish loops.
- Both guards should produce bounded logs and operator-visible degraded state.
- Neither guard decides the normal data route for the skill.

Builder repair evidence must include the guard source, not just a symptom.
When a guard or migration quarantine appears, collect this packet before
editing the skill:

- `runtime.yjs_projection_guard`: owner, slot, path, payload bytes, item count,
  degraded bytes, route budget, encoded Yjs update bytes, amplification ratio,
  recovery mode, and selected-webspace YStore replay-tail context
- `runtime.yjs_projection_guard.builder_repair_packets`: the machine-readable
  repair packet for LLM Builder. Prefer it when present; it names the skill,
  route, evidence, recovery state, and recommended bounded-route/compaction
  actions.
- `runtime.yjs_pressure`: current writer, affected roots, and
  `last_write_amplification_suspects` when a sibling branch caused the large
  update domain
- `runtime.webio_stream_guard`: receiver, owner, payload bytes, fanout, and
  suppression/throttle counters
- `runtime.skill_runtime_migration.diagnostics`: current skill/stage, stale
  age, suspected blocker, host disk/PSI hints, and recommended operator checks
- supervisor public memory status: managed PID, process RSS, child RSS, family
  RSS, top child `skill_runtime` entries, baseline phase, baseline adjustment
  reason, RSS growth, and suspicion state
- runtime event-loop lag and supervisor self-heal evidence when the symptom is
  API unready, listener lost, or slow boot rather than sustained RSS growth
- skill-local incident log or 360log reference when the payload or traceback is
  too large for browser state

The repair output should say which route changes: keep compact Yjs summary,
move full data to page/search/details, add stream snapshot-on-subscribe,
tighten budgets, add cleanup/dispose, or add a child-runtime memory cap. A
repair that only raises the guard budget is incomplete unless the data is
genuinely reconnect-stable and bounded by domain rules.

## Observability rules

Every skill should make failures diagnosable.

Use:

- stable error codes
- compact `trace_id` or operation id
- bounded logs
- explicit `retryable` flags
- visible `degraded` / `unavailable` states when data cannot be fetched
- disk/360log snapshot references for large evidence

For browser reads, emit one compact causal record per attempted request with:

- `route_id`, `surface`, semantic `read_key_hash`, tool, and owner;
- `cause`: one of the declared `read_policy.triggers`;
- `invalidation_tag` and source action when the cause is targeted invalidation;
- cache age, TTL, request count, suppression count, and coalescing decision;
- duration, payload bytes, result (`success`, `error`, `cache_hit`,
  `rate_limited`, or `coalesced`), and `trace_id`.

Aggregate counters by route and cause. Alert on an undeclared cause, a stable
read key exceeding `max_request_hz`, repeated mount causes without unmount, or
tool calls during an idle-soak window. Do not put raw arguments or response
payloads in metrics; use bounded identifiers and hashes.

Do not:

- swallow exceptions and return stale success
- fall back to another data plane without surfacing that fallback
- report a command `ack` as if browser-visible state is already delivered
- retry in tight loops
- perform expensive snapshot rebuilds for every browser poll

## LLM implementation workflow

Before coding:

- read `skill.yaml`
- read `webui.json`
- identify every browser-visible state branch
- write the data route plan for every browser-visible branch or receiver
- choose Yjs projection, stream, details tool, or skill-local storage for each
  branch, and record why
- check whether the skill is node-aware
- define size and frequency expectations
- identify hot events and define debounce/budget behavior before writing
  handlers
- list module globals, caches, background workers, and heavy resources that can
  survive reload
- define the memory budget, eviction policy, and lifecycle cleanup path for
  each retained object

When coding:

- use SDK helpers instead of direct Yjs primitives
- keep tool responses small
- make updates idempotent
- fingerprint or coalesce heavy projections
- keep arrays bounded
- build stream payloads per receiver when possible, rather than rebuilding the
  whole skill snapshot
- keep raw diagnostic evidence separate from smoothed operator state
- accept routing metadata and unknown keyword args
- preserve owner attribution where helper APIs require it
- for Builder-like long LLM work, submit a Root async job with
  `adaos.sdk.llm.llm_client.submit_response_job()`, store the `job_id` with the
  current artifact/session, poll with `wait_response_job()`, and apply the
  result only after deterministic schema validation; reserve synchronous
  `send_response()` for small calls where an HTTP read timeout is acceptable
- avoid import-time side effects
- give every thread, task, executor, timer, subprocess, model, and external
  callback an explicit stop/dispose path
- log cache eviction, stale worker cleanup, oversize rejects, and degraded
  stream sections

Before publishing:

- verify `data_routes` exists for browser-facing Yjs, stream, details, or
  diagnostic surfaces
- verify every tool-backed surface names its exact tool and has a causal
  `read_policy`, addressable invalidation tags, and a frequency budget
- run the idle-soak and targeted-invalidation checks for stable tool-backed
  widgets; schema rerenders and unrelated Yjs/action updates must produce zero
  additional calls
- verify `data_projections` exist for Yjs state
- verify stream receivers have bounded modes and snapshot-on-subscribe behavior
- verify stream payloads stay within budget after multiplying by expected
  browser/node fanout
- verify receiver budgets and handler constants agree: `webui.json`,
  `skill.yaml`, and the actual stream builder must use the same item/text/byte
  caps, including `max_fanout`
- verify stream receivers have `initialState`, freshness metadata, and a
  recovery path after resubscribe
- verify `webui.json` declares shared interaction behavior for first focus,
  Enter/default submit actions, pending action feedback, element loading
  states, and skill UI resources instead of hiding these rules in widget
  special cases
- verify browser-facing icons, avatars, preview images, templates, and i18n
  dictionaries are declared in top-level `resources` and referenced as
  `resource:<id>`; core-delivered files must live under the skill `assets/`
  tree, localized dictionaries should use `kind: "data"`, `role: "i18n"`, and
  an explicit `locale`, and large public assets should use `delivery:
  "external"` with an authored URL rather than a skill-specific static endpoint
- verify no handler rewrites broad Yjs roots
- verify collection routes keep Yjs summaries constant-size under synthetic
  large-row tests
- verify hot events have debounce/budget tests
- verify SDK projection diagnostics show the expected `by_event` pressure
  counters for dirty refresh paths before optimizing a noisy event source
- verify stream request bursts cannot rebuild every skill section by default
- verify generic subscription-change hooks do not publish full snapshots or
  bypass stream coalescing; they may update demand counters, but initial state
  must use the declared snapshot/refresh path
- verify stream snapshot builders use bounded read models or compact caches and
  do not call remote discovery, root relay, sync repair, or legacy fallback
  paths during browser state rebuilds
- verify status cards stay small and point to details instead of embedding
  detail payloads
- verify status-card compact-boundary diagnostics stay clean:
  `oversizedCardTotal == 0` and observed card bytes are comfortably below the
  card budget
- verify no action returns a large payload when a projection/stream is the
  real data path
- verify actions that publish streams return only compact acks and do not
  include `state`, `items`, `sections`, logs, diagnostics, or full snapshots
- verify cached `last_command` / `last_result` fields are bounded
  acknowledgements and cannot grow with original command responses
- verify snapshot/request tools publish the bounded stream/projection payload
  and return only `ok`, `status`, `receiver` or `projection_slot`, counts,
  freshness, and a trace id
- verify Yjs and stream guard errors are visible to the UI
- verify any `runtime.yjs_projection_guard.builder_repair_packets` entry is
  either absent after the fix or explains the remaining bounded-route/compaction
  work before increasing budgets
- import and smoke-validate handlers repeatedly with no import-time workers,
  model loads, bus mutations, or persistent writes
- reload/reactivate the same skill repeatedly and verify subscription counts,
  worker counts, cache sizes, and receiver state remain bounded
- install/activate a changed slot and verify the next tool call uses the new
  handler code without restarting the API process
- run a short RSS soak for the hottest event and stream-subscribe paths
- inspect supervisor memory status after the soak: baseline should be `mature`,
  top child skill runtimes should be explainable, and `suspicion_state` should
  either be `stable` or include repair evidence naming the owner/blocker

## Anti-patterns

Treat these as defects in LLM-generated skills:

- direct skill writes to the primary Yjs document
- broad replacement of `data`, `ui`, `registry`, `data.catalog`,
  `data.installed`, or `data.desktop`
- unbounded chat/log/event arrays in Yjs
- returning a huge snapshot from `refresh_snapshot`
- polling a heavy snapshot endpoint to keep normal UI alive
- duplicating the same data in a tool response and Yjs
- duplicating the same replace-state in both eager stream publishes and Yjs
  projections on every refresh
- using HTTP/API fallback as steady-state transport for Yjs-rendered data
- hiding degraded state behind "successful" empty UI
- controlling WebRTC/Yjs channel lifecycle from business logic
- doing continuous profiling, deep JSON normalization, or full snapshot
  serialization inside hot handlers
- treating stream delivery as durable state without snapshot-on-subscribe
- letting subscription flaps rewrite Yjs on every subscribe/unsubscribe
- using runtime quarantine as the normal way to quiet a noisy skill
- starting anonymous non-daemon threads, timers, executors, subprocesses, or
  playback loops without a stop/dispose path
- loading ML models, media indexes, sockets, or external sessions at import time
- keeping unbounded module-level caches, per-webspace dictionaries, receiver
  state, or diagnostics lists
- retaining full snapshots or large tool responses in globals when a compact
  fingerprint, stream snapshot, details tool, or disk evidence route would do
- assuming a skill reload frees Python objects without explicit cleanup

## Current migration priorities

The current workspace audit suggests this priority order:

1. split `infrastate_skill` into minimal summary plus details/streams, reduce
   broad event subscriptions, and add reload/RSS soak checks for its stream
   control paths
2. split `infrascope_skill` into demanded projection families, keep expensive
   snapshot sections request-driven, and verify subscription counts stay flat
   across reloads
3. make `browsers_skill` and `infra_access_skill` projection refreshes
   idempotent, avoid all-webspace fanout for routine events, and add cleanup
   for owned executors, threads, and caches
4. keep the `voice_chat_skill` conversation-ledger bridge as the Voice
   compatibility template: compact Yjs status, bounded ledger-backed stream
   history for existing Voice surfaces, and no skill-local transcript as the
   primary store
5. add explicit cache/model/playback lifecycle budgets to
   `new_face_vision_skill`
6. decide whether `mediaserver` and `prompt_engineer_skill` should remain
   tool-driven or adopt browser-facing projection contracts, and cap large
   library, scan, and diagnostic payloads either way
