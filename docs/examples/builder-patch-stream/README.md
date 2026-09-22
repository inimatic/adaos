# Builder Patch Stream Debug Example

`debug-patch-stream.ndjson` is a minimal semantic stream for the
`builder.prototype.patch_stream` receiver.

Use it when testing a Root LLM job prompt, a local mock, or a manual WebIO
publisher. The browser-side draft should end with one `ui.markdown` widget in
`draft.pageSchema.widgets`.

The corresponding Root LLM job options are:

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

The stream is intentionally small and only touches `/pageSchema`, which is the
initial allowed patch root.
