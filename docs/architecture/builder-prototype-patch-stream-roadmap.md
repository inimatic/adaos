# Builder Prototype Patch Stream Roadmap

Status: target architecture and staged implementation plan for live Builder
prototype sketching while an LLM response is streaming.

## Target Architecture

Builder uses two lanes:

- Sketch lane: Root LLM streams semantic JSONL events into
  `builder.prototype.patch_stream`. The browser applies the events to an
  ephemeral draft state so the preview can change while the answer is still
  arriving.
- Commit lane: after the response completes, Builder compiles the full semantic
  prototype candidate, validates the WebUI artifact, snapshots a revision, and
  materializes the authoritative Yjs UI.

The sketch lane must never be the source of truth. Invalid or incomplete
patches may be ignored or surfaced as draft errors without corrupting the
canonical preview room.

## Must

- Root LLM jobs support opt-in `patch_stream` forwarding for
  `stream_protocol="jsonl"` responses.
- Forwarded events use `io.out.stream.publish`, not direct browser fan-out, so
  existing WebIO admission, budget, owner, and receiver metadata remain active.
- Browser stream receivers can declare `reducer:
  "builder.prototype.patch_stream"`.
- The browser reducer supports bounded `meta`, `patch`, and `complete` events
  for an ephemeral draft.
- Patch application is limited to declared JSON Pointer roots; the initial MVP
  root is `/pageSchema`.
- Patch values are size-bounded before forwarding from Root.
- Polling clients continue to receive compact job progress summaries without
  storing full patch payloads in Root job progress.
- Debug examples exist for replaying a small patch stream by hand.

## Deferred

- Rendering the draft as a first-class desktop/preview overlay.
- Applying draft state into a controlled Yjs branch.
- Full RFC 6902 semantics, including `move`, `copy`, and `test`.
- Schema and capability validation on every patch. The MVP validates only
  bounded event shape and path roots; final compile remains authoritative.
- Base-hash conflict recovery beyond dropping stale sequence numbers in the
  browser reducer.
- Snapshot-on-subscribe recovery for an in-progress patch stream.
- Product UX for failed patch streams, rollback affordances, and side-by-side
  diff review.
- Cross-client collaborative draft synchronization.
- Long-term retention of patch stream events as Builder evidence.

## First Debug Loop

1. Submit a Root LLM job with `stream=true`, `stream_protocol="jsonl"`, and
   `patch_stream.receiver="builder.prototype.patch_stream"`.
2. The model emits the debug JSONL sequence from
   `docs/examples/builder-patch-stream/debug-patch-stream.ndjson`.
3. Root forwards each accepted semantic event to `io.out.stream.publish`.
4. The router fans out `webio.stream.<webspace>.builder.prototype.patch_stream`.
5. The browser reducer builds `state.draft.pageSchema`.
6. The final Builder compiler/materializer replaces the sketch with an
   authoritative revision when validation succeeds.
