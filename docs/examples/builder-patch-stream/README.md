# Builder Patch Stream Debug Examples

`debug-semantic-graph-stream.ndjson` is the target graph-first debug stream for
the future `builder.semantic_graph.patch_stream` receiver. It exercises semantic
operations first; a projector derives preview-only `pageSchema` from the reduced
graph.

`debug-patch-stream.ndjson` is the current compatibility stream for the
implemented `builder.prototype.patch_stream` receiver. It directly patches
`draft.pageSchema` and should remain useful for testing Root forwarding, WebIO
fan-out and browser-side reducer behavior before the semantic graph reducer is
available.

Use either file when testing a Root LLM job prompt, a local mock, or a manual
WebIO publisher.

The target graph-first Root LLM job options are:

```json
{
  "stream": true,
  "stream_protocol": "jsonl",
  "patch_stream": {
    "receiver": "builder.semantic_graph.patch_stream",
    "webspace_id": "desktop",
    "owner": "builder:debug"
  }
}
```

The current compatibility Root LLM job options are:

```json
{
  "stream": true,
  "stream_protocol": "jsonl",
  "patch_stream": {
    "receiver": "builder.prototype.patch_stream",
    "webspace_id": "desktop",
    "owner": "builder:debug"
  }
}
```

The compatibility stream is intentionally small and only touches `/pageSchema`,
which is the initial allowed patch root of the implemented reducer. It is not
the target authoring authority.
