# Builder Streaming Patch Architecture

Status: target architecture with a backward-compatible first implementation
slice.

## Objective

Builder should expose useful progress before a long LLM response is complete
and should avoid regenerating and transferring a complete `webui.json` when a
small logical change is enough. Streaming is an execution optimization, not a
relaxation of the AdaOS UI contract.

The canonical runtime artifact remains a complete `adaos.webui.v1` document.
No partial provider response may be written directly to `webui.json`, Yjs, or
the active dev webspace.

## Three Separate Protocols

The implementation must keep these protocols distinct:

1. **Provider stream**: OpenAI Responses API typed SSE events, including text
   deltas and terminal/error events. Event and network chunk boundaries are
   transport details and are not Builder patch boundaries.
2. **Root job progress**: a bounded, replayable journal owned by Root. It
   reports accepted, provider-started, output-progress, validating, completed,
   and failed phases with monotonic sequence numbers.
3. **Builder semantic output**: either a logical patch batch or a legacy full
   `adaos.webui.v1` document. Builder validates and commits this output.

Root owns the upstream connection. A Hub, browser, or Builder polling
disconnect must not cancel provider execution. Consumers recover from the
bounded Root journal and terminal job snapshot.

## Target Semantic Contract

The preferred model output is a strict object whose `patches` use the generic
RFC 6902 operation vocabulary:

```json
{
  "schema": "adaos.builder.webui_patch_batch.v1",
  "base_revision": "008",
  "base_hash": "sha256:...",
  "patches": [
    {
      "seq": 1,
      "op": "replace",
      "path": "/ui/application/desktop/pageSchema/widgets/2/inputs/title",
      "value": "Recipes"
    }
  ],
  "comment": "Updated the recipe section title.",
  "unable_reason": ""
}
```

The allowed operations are `add`, `remove`, `replace`, `move`, `copy`, and
`test`. Each batch carries the source revision and fingerprint. Operations are
ordered, idempotently journaled by `(job_id, seq)`, and applied to a private
shadow copy. Stable object/widget ids and preceding `test` operations are
preferred when an array index could otherwise target the wrong element.

An incremental parser may expose a patch only after its complete JSON object
has arrived. It must not assume that one SSE delta or one network chunk is one
JSON line.

## Validation And Commit

The target transaction is:

1. Read the selected revision and calculate `base_hash`.
2. Create a private shadow document.
3. Apply each complete patch in strict sequence to the shadow document.
4. Run bounded per-patch structural checks for path, operation, size, and base
   identity.
5. Reconstruct the complete candidate `adaos.webui.v1` document.
6. Run full ABI, component, action, modal, text-integrity, and runtime
   validation.
7. Persist request, provider response, patch journal, before/after documents,
   model/cache/timing telemetry, and validation evidence in the next immutable
   `ui_revisions/NNN.json`.
8. Atomically promote the candidate to `webui.json`, then refresh the paired
   dev webspace.

On cancellation, timeout, invalid sequence, base mismatch, parser failure, or
validation failure, Builder discards the shadow state. The current revision
does not change.

## Compatibility

Streaming capability is selected by the Root development model profile and by
the request. Existing providers and models remain supported:

- A streaming model uses typed provider SSE. Root stores progress and the
  complete terminal response.
- A non-streaming model uses the existing full-response request. Root emits an
  equivalent accepted/running/completed journal around it.
- A model capable of streaming text but not reliable patch batches may stream
  progress while returning a full `adaos.webui.v1` document at completion.
- Builder accepts both `adaos.builder.webui_patch_batch.v1` and the legacy full
  `adaos.webui.v1` output during migration.

The full-response path is a supported compatibility mode, not an error path.

## Prompt Cache Strategy

Provider prompt caching only helps when the repeated prefix is byte-stable.
Builder therefore orders context from stable to dynamic:

1. versioned Builder system contract and safety rules
2. versioned compact `webui.v1` ABI/component capability catalog
3. selected provider/model prompt profile
4. project memory and recent revision evidence
5. current `webui.json`
6. the current user instruction

Builder supplies a stable `prompt_cache_key` derived from provider, model,
prompt-profile version, ABI version, and semantic output mode. Project ids and
user text must not be part of that key. Root passes supported cache controls to
the provider and records input, cached-input, and cache-write token counts.

Cache routing does not replace deterministic request-id deduplication. The two
caches have different purposes: request-id caching prevents duplicate work;
provider prompt caching reuses a stable input prefix across different jobs.

## Chat Projection

One Builder job is represented by one durable chat message with a stable
message id. Its compact pages are updated by phase:

- accepted
- generating
- validating/applying
- completed or failed

The current unfinished page shows a non-interactive loader. Completed pages
are selectable. A terminal result adds revision actions to the same card. The
conversation ledger stores the final message plus bounded phase evidence; it
must not store every token delta as a separate message.

## Observability

At minimum each revision and Root job expose:

- queue, time-to-first-provider-event, generation, validation, apply, refresh,
  and total durations
- model/provider/service tier
- input, cached input, cache write, output, and reasoning tokens
- stream mode, event count, output bytes, last sequence, reconnect/replay count
- retry, tool, and MCP traces
- source revision/hash and committed revision/hash

This evidence distinguishes provider latency from local prompt construction,
Root queueing, Builder validation, and dev-webspace refresh latency.

