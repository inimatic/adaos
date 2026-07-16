# Builder Streaming Patch Architecture

Status: target architecture with a backward-compatible first implementation
slice validated end to end in July 2026.

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

The preferred model output is newline-delimited JSON. Every physical line is a
complete object, and patch lines use the generic RFC 6902 operation vocabulary:

```jsonl
{"schema":"adaos.builder.webui_patch_stream.v1","type":"meta","base_hash":"sha256:..."}
{"type":"patch","seq":1,"op":"replace","path":"/ui/application/desktop/pageSchema/widgets/@recipe-list/inputs/title","value":"Recipes"}
{"type":"complete","comment":"Updated the recipe section title.","unable_reason":""}
```

The allowed operations are `add`, `remove`, `replace`, `move`, `copy`, and
`test`. The meta line carries the source fingerprint; the Builder session owns
the source revision. Operations are
ordered, idempotently journaled by `(job_id, seq)`, and applied to a private
shadow copy. An existing member of an id-bearing array should be addressed with
the AdaOS stable pointer token `@<id>`. `add` creates a missing object member;
`replace` requires the member to exist after all preceding operations.

`adaos.builder.webui_patch_batch.v1` remains an accepted compact batch form for
providers that produce one complete object instead of reliable JSONL.

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

The target representation is one durable Builder chat message with a stable
message id. Its compact pages are updated by phase:

- accepted
- generating
- validating/applying
- completed or failed

The current unfinished page shows a non-interactive loader. Completed pages
are selectable. A terminal result adds revision actions to the same card. The
conversation ledger stores the final message plus bounded phase evidence; it
must not store every token delta as a separate message.

The first implementation slice emits bounded phase messages carrying the same
`progress_group_id`; the client coalesces them into one card and exposes each
completed phase as a compact page. The browser projection is also bounded by
actual UTF-8 payload bytes and retains `progress_seq`; full messages stay in the
conversation store. Durable in-place ledger update is the next
compatibility-preserving step. Polling remains the replay/recovery mechanism.

RFC 6902 patch lines must create missing intermediate containers explicitly.
For example, if `ui.application.modals` is absent, the stream first adds that
object and only then adds a concrete modal below it. Builder rejects rather than
silently normalizes a malformed stream, and one repair request receives the
exact missing-parent diagnostic. Component contracts remain ABI-driven; for
example every `pageSchema.autoActions` item wraps the executable action in its
required `action` member.

## July 2026 Evaluation

The reference run created `streaming_recipe_book_eval` from the generic Builder
scaffold and produced a responsive recipe catalog with deterministic Picsum
cards, category controls, search, selected-recipe details, and a local favorite
action. The final follow-up used stable widget paths, declared
`ui.application.modals.recipe_detail_modal`, attached selection/open actions to
the recipe cards, preserved the modal while adding two catalog rows, passed the
complete ABI/component/action validator, promoted revision `009`, and refreshed
`desktop-dev`.

Measured evidence from the run:

- provider TTFT: 0.74-1.25 seconds for the measured `gpt-5` jobs
- cold full-prototype generation: 12.8 seconds at Root
- small warm generation: 3.8 seconds at Root with 8,832 of 11,593 input tokens
  served from the provider prompt cache
- final post-profile-change correction: 4.6 seconds at Root before one bounded
  repair pass
- local context construction: 0.05-0.14 seconds; Root submit: 1.2-1.9 seconds
- validated apply plus dev-webspace materialization: approximately 3.1 seconds,
  of which semantic runtime rebuild was approximately 2.1-2.7 seconds
- modal conversion after the missing-parent contract fix: 15.1 seconds at Root,
  16.9 seconds through validated local apply, without a repair pass
- warm two-row catalog update: 7.5 seconds at Root and 8.2 seconds through apply,
  with 9,344 of 11,991 input tokens served from the provider cache

`gpt-4o-mini` produced syntactically recoverable output but materially weaker
layout and interaction choices for the same broad prototype request. The model
profile remains selectable; the reference quality run used `gpt-5`.

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
