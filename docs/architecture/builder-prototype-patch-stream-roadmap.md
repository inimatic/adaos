# Builder Prototype Patch Stream Roadmap

Status: target architecture and staged implementation plan for live Builder
prototype sketching while an LLM response is streaming.

## Target Architecture

Builder uses three ordered lanes:

- Semantic draft lane: Root LLM streams typed JSONL operations into
  `builder.semantic_graph.patch_stream`. The reducer applies those operations
  to an ephemeral `SemanticGraph` draft:
  `SemanticGraph_0 + ops -> SemanticGraph_t`.
- Preview projection lane: a deterministic projector derives
  `SemanticGraph_t -> pageSchema_t` for visual feedback. The existing
  `builder.prototype.patch_stream` receiver remains the compatibility/debug
  bridge for derived `/pageSchema` patches; it is not the target authoring
  source.
- Commit lane: after the response completes, Builder validates the full
  semantic source, compiles the WebUI artifact, snapshots a revision, and
  materializes the authoritative Yjs UI.

The sketch lane must never be the source of truth. Invalid or incomplete
semantic operations may be ignored or surfaced as draft errors without
corrupting the canonical preview room. The canonical source is the accepted
semantic Application/Prototype revision; `pageSchema` is a compiled or draft
projection.

## Must

- Root LLM jobs support opt-in `patch_stream` forwarding for
  `stream_protocol="jsonl"` responses.
- Forwarded events use `io.out.stream.publish`, not direct browser fan-out, so
  existing WebIO admission, budget, owner, and receiver metadata remain active.
- Define `adaos.builder.semantic_graph.patch_stream.v1` with bounded `meta`,
  `op`, and `complete` events. Operations target stable semantic refs, not
  array indexes or renderer JSON paths.
- Define the target receiver `builder.semantic_graph.patch_stream` and reducer
  contract for `SemanticGraph_0 + ops -> SemanticGraph_t`.
- Include `seq`, `base_hash`, `transaction_id`, and stable `node_id` or
  semantic refs on streamed operations before treating them as replayable
  Builder evidence.
- Compile or project semantic graph drafts deterministically to draft
  `pageSchema` snapshots and retain source maps from requirement refs to
  semantic refs and compiled widget/action refs.
- Keep `builder.prototype.patch_stream` as the current compatibility/debug
  reducer for derived `/pageSchema` patches while the graph reducer is not yet
  implemented.
- Patch values are size-bounded before forwarding from Root.
- Polling clients continue to receive compact job progress summaries without
  storing full patch payloads in Root job progress.
- Debug examples exist for replaying both the target semantic graph stream and
  the current `/pageSchema` compatibility stream by hand.

## Completed Documentation Alignment

- [x] `2026-09-23` Recast the live-stream target as graph-first:
  `SemanticGraph_0 + ops -> SemanticGraph_t -> pageSchema_t`.
- [x] `2026-09-23` Mark `/pageSchema` patch streaming as compatibility/debug
  projection, not semantic authority.
- [x] `2026-09-23` Add a semantic graph debug JSONL example alongside the
  existing pageSchema compatibility example.
- [x] `2026-09-23` Link the non-deferred graph-first work into BIP-11, BIP-12,
  ASC2, WebIO, and the Builder streaming architecture.

## Deferred

- Rendering the semantic draft as a first-class desktop/preview overlay.
- Applying draft state into a controlled Yjs branch.
- Cross-client collaborative semantic draft synchronization.
- Snapshot-on-subscribe recovery for an in-progress semantic graph stream.
- Long-term retention of patch stream events as Builder evidence.
- Product UX for failed patch streams, rollback affordances, and side-by-side
  diff review.
- Full RFC 6902 semantics for the compatibility `/pageSchema` bridge, including
  `move`, `copy`, and `test`.
- Per-operation capability validation beyond bounded event shape, target refs,
  and structural graph checks. Final compile remains authoritative until the
  semantic validator closes that gap.

## First Debug Loop

1. Submit a Root LLM job with `stream=true`, `stream_protocol="jsonl"`, and
   `patch_stream.receiver="builder.semantic_graph.patch_stream"`.
2. The model emits the target JSONL sequence from
   `docs/examples/builder-patch-stream/debug-semantic-graph-stream.ndjson`.
3. Root forwards each accepted semantic event to `io.out.stream.publish`.
4. The router fans out
   `webio.stream.<webspace>.builder.semantic_graph.patch_stream`.
5. The semantic reducer builds `state.draft.graph`.
6. The projector derives `state.draft.pageSchema` for preview-only rendering.
7. The final Builder compiler/materializer replaces the sketch with an
   authoritative revision when validation succeeds.

The currently implemented compatibility loop uses
`builder.prototype.patch_stream` and
`docs/examples/builder-patch-stream/debug-patch-stream.ndjson` to prove Root
forwarding, WebIO fan-out, and browser-side patch reduction before the target
semantic graph reducer lands.
