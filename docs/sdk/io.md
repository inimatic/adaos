# SDK IO

SDK module: `adaos.sdk.io`

## Output (unified)

These helpers publish events onto the local bus. They do not write to Yjs directly.

These helpers are low-level IO/event primitives. For ordinary user-visible
dialog the target API is `adaos.sdk.conversation`, described in
[Conversation and Channel Architecture](../architecture/conversation-and-channel-architecture.md).
The `adaos.sdk.chat`, `adaos.sdk.conversation`, and `adaos.sdk.memory` modules
are now the default path for generated skills. `io.out.chat.append` remains a
compatibility bridge for older skills and transport adapters.

- `io.out.chat.append(text, from_='hub', _meta={...})`
  - RouterService projects into `data.voice_chat.messages` of the target webspace.
- `io.out.say(text, lang='ru-RU', _meta={...})`
  - RouterService projects into `data.tts.queue` of the target webspace.
- `io.out.media.route(need='scenario_response_media', _meta={...})`
  - RouterService normalizes the media route intent/contract and projects it into `data.media.route` of the target webspace.

### Routeless skills via `_meta` context

When a tool is invoked with a payload that contains `_meta`, AdaOS automatically
sets an execution context so `io.out.*` helpers inherit it.

This means a skill can stay stateless and "routeless":

- no direct Yjs writes
- no explicit `_meta=...` in `chat_append()` / `say()` (unless you want to override)

RouterService also supports broadcasting by setting `_meta.webspace_ids = ['w1', 'w2', ...]`.
For dynamic runtime routing without changing skills, you can also set `_meta.route_id`
and configure targets in Yjs: `data.routing.routes[route_id] = { webspace_ids: [...] }`.

## Conversation and Memory SDKs

SDK modules: `adaos.sdk.chat`, `adaos.sdk.conversation`, `adaos.sdk.memory`

These modules are the skill-facing facade over the node-local conversation
ledger, scoped memory store, response materializer, and context-packet builder.
Skills should use them instead of keeping transcript files, writing directly to
Yjs, or storing user-visible history in process-local collections.

### `adaos.sdk.conversation`

- `current(webspace_id=None, channel_id='general')` returns the persisted
  dialog-channel pointer for the current node/webspace.
- `open(conversation_id, owner, webspace_id=None, channel_id=None, ...)`
  creates or updates a node-local conversation and optionally binds it to a
  dialog channel.
- `append(conversation_id, text, role, ...)` appends a ledger message. Prefer
  `adaos.sdk.chat.send` for user-visible assistant responses.
- `get(conversation_id, thread_id=None, before_cursor=None, limit=50)` returns
  a bounded visible-tail projection with `before_cursor` and
  `has_more_before`.
- `start_thread(conversation_id, thread_id=None, ...)` creates a durable topic
  or project thread inside a shared conversation.
- `context(conversation_id, requester_owner, ...)` returns a budgeted context
  packet: recent messages, segment summaries, memory items, evidence refs,
  diagnostics, and policy denials.

Design rules:

- The physical store is owned by the node; logical ownership is expressed with
  `owner` and `agent_id`.
- Cross-owner memory reuse is deny-by-default unless a caller passes an
  explicit policy override.
- `thread_id` is the first topic/project discriminator. Builder and IDE-style
  flows should pass it on every turn.
- Yjs/WebIO tails are projections. The ledger remains the source of truth.

### `adaos.sdk.chat`

- `send(content, conversation_id, owner, ...)` materializes a structured
  response into text tail/speech targets and appends the assistant message to
  the ledger.
- `ask(prompt, conversation_id, owner, ...)` is a bounded question helper. It
  marks the response as expecting an immediate/process-bounded reply and must
  not be kept alive across a task or process restart.
- `request(interaction, conversation_id, owner, ...)` persists a semantic
  `ConversationInteraction`, negotiates a Web/Telegram/text presentation, and
  returns an `InteractionHandle`; it never holds a process-local waiter.
- `respond(...)` appends a generation-bound, idempotent
  `InteractionResponse`. `accept(...)` records that the owning workflow
  consumed the answer, while `pending(...)` exposes resumable requests.
- `history(...)`, `context(...)`, and `start_thread(...)` are convenience
  wrappers over `adaos.sdk.conversation`.

Generated skills should return the materialization result in their tool output
when practical, so diagnostics can explain renderer and ledger status.

### `adaos.sdk.memory`

- `remember(scope, owner, ...)` writes a scoped memory item to the node store.
- `list(...)` and `search(query, ...)` read owner-scoped memory with bounded
  limits.
- `forget(...)` soft-redacts or deletes memory by id or scope filters.
- `record_consent(...)` appends a durable consent audit event.
- `write_policy(kind, owner, ...)` maps high-level memory intents to canonical
  scopes: `conversation`, `skill_user`, `agent_user`, and `global_user`.
- `propose_write(kind, owner, ...)` publishes a `memory.write.review` Pending
  Action instead of silently storing reusable long-term memory.

Default generated-skill policy:

- transient conversation facts: `conversation` scope;
- skill preferences: `skill_user` scope;
- character/agent preferences: `agent_user` scope;
- reusable user profile facts: proposal first, then approved `global_user`
  storage by policy.

Skill manifests should declare a `conversation` contract and either
`conversation.memory` policy or a skill-local memory `data_routes` entry when
using the memory SDK. The validator warns if SDK usage and manifest policy are
out of sync.

## WebIO data contracts (MVP)

### `data/voice_chat`

Compatibility status: this is the current browser-visible Voice tail, not the
target canonical conversation history store. The target architecture keeps
conversation history and memory in a node-local conversation/memory store with
core-governed policy and owner-scoped access, and projects only compact active
tails into Yjs/WebIO.

`data.voice_chat` is a JSON object:

```
{
  "messages": [
    { "id": "m.123", "from": "user|hub", "text": "…", "ts": 1730000000.0 }
  ]
}
```

### `data/tts`

`data.tts` is a JSON object:

```
{
  "queue": [
    { "id": "t.123", "text": "…", "ts": 1730000000.0, "lang": "ru-RU", "voice": "…", "rate": 1.0 }
  ]
}
```

### `data/media`

`data.media` is a JSON object.
Today the router-owned route contract lives under `data.media.route`:

```
{
  "route": {
    "route_intent": "scenario_response_media|live_stream|upload|playback",
    "preferred_route": "local_http|root_media_relay|hub_webrtc_loopback|member_browser_direct",
    "active_route": "local_http|root_media_relay|hub_webrtc_loopback|member_browser_direct|null",
    "producer_authority": "hub|member|shared|none",
    "producer_target": { "kind": "hub|member", "member_id": "...", "webspace_id": "..." },
    "selection_reason": "....",
    "degradation_reason": "....",
    "member_browser_direct": {
      "possible": true,
      "admitted": false,
      "ready": false,
      "reason": "member_browser_direct_policy_not_admitted_yet",
      "candidate_member_total": 1,
      "browser_session_total": 2
    },
    "monitoring": {
      "watch_signals": ["..."],
      "observed_failure": null
    },
    "route_administrator": "router",
    "updated_at": 1730000000.0
  }
}
```

`io.out.media.route(...)` may publish either:

- a minimal route intent with capability/ability hints, which the router normalizes
- a precomputed route contract, which the router re-targets to the destination webspace and republishes as browser-visible state

## Media Resource SDK

SDK module: `adaos.sdk.io.media`

Skills should use the SDK media helpers instead of importing core services.
The core-owned media plane is intentionally limited to resource descriptors,
safe content paths, publication into the shared Media Server store, and playback
delivery contracts. Catalog semantics such as albums, similarity search,
transcription, playlists, and enrichment belong to skills.

Canonical descriptor shape:

```json
{
  "schema": "adaos.media.resource.v1",
  "id": "clip.mp4",
  "resource_id": "clip.mp4",
  "source": "media_server",
  "name": "clip.mp4",
  "size_bytes": 42000,
  "mime_type": "video/mp4",
  "modified_at": "2026-08-11T10:00:00+00:00",
  "content_path": "/api/node/media/files/content/clip.mp4",
  "routed_content_path": "/media/files/content/clip.mp4"
}
```

Skill-facing helpers:

- `list_media_resources(source='media_server', include_internal=False, limit=None)`
  returns normalized `adaos.media.resource.v1` descriptors from core-backed
  producers. Use `source='all'` to discover the shared Media Server store plus
  compatibility media-indexer entries without importing service internals.
- `publish_media_file(path, content_ref, namespace='media', variant='media', mime='image/jpeg')`
  copies a generated or prepared file into the shared Media Server store and
  returns a `adaos.media.resource.v1` descriptor plus legacy URL fields.
- `media_content_path(filename, browser=True)` returns the safe Media Server
  content path for an already published filename.
- `media_resource_content_path(resource_id, source='media_server', browser=True)`
  builds a content path for a known core source. Current core sources are
  `media_server` and compatibility `media_indexer`.
- `media_indexer_content_path(playback_id, browser=True)` builds the legacy
  media-indexer playback path without making a skill depend on
  `adaos.services.media_indexer_library`.
- `media_resource_descriptor(...)` builds a normalized descriptor when a skill
  owns the catalog/search semantics but wants to publish browser-safe media
  rows into Yjs/WebIO.

Compatibility: `media_indexer` remains a legacy producer of `MediaResource`
descriptors through `playback_id`. It is not the canonical media catalog.

## Voice (local mock)

- `io.voice.stt.listen(timeout='20s')`
- `io.voice.tts.speak(text)`

## Web STT (frontend)

`ui.voiceInput` supports pluggable STT providers via `widget.inputs.stt`:

- `provider: 'browser'` — Web Speech API (`SpeechRecognition`) with partials
- `provider: 'hub'` — records audio, uses `/api/stt/transcribe` (WAV mono 16kHz)

Common options:
- `pushToTalk: true` (press-and-hold)
- `vad: true`, `vadThreshold`, `vadSilenceMs` (hub provider only)
- `autoSend: true` (send without confirmation)

## Node STT providers and models

`node.voice.listening.v1.stt.provider_mode` accepts `system`, `vosk`, or
`auto`. `system` is the initial/default mode. `auto` selects only an installed
Vosk model carrying verification evidence for the current device; it otherwise
continues to use system STT.

Stationary model management is exposed at:

- `GET /api/stt/models`;
- `POST /api/stt/models/install` with `model_id` and optional custom
  `descriptor` containing an exact `archive_sha256`;
- `POST /api/stt/models/select` with `language` and `model_id`;
- `POST /api/stt/models/verify` with `device_id` and bounded metrics.

Android exposes the same catalog and selection semantics at
`/api/node/voice/stt/models*`; installation is asynchronous and its state is
returned by the GET endpoint. Model files live under the node data root and are
not Python packages or part of core A/B slots.

## ReDevice Endpoint Transport

ReDevice skills should address endpoint services, not Android, iOS, browser, or
relay implementation details. The SDK exposes transport facts as a versioned
profile and a per-command selection result:

```python
from adaos.sdk.redevice import list_endpoints, select_transport

endpoint = list_endpoints()[0]
transport = select_transport(endpoint, intent="display.slideshow", content_bytes=42000)
```

The runtime selection ladder is:

```text
webrtc_p2p
  -> local_ws
  -> local_http
  -> http_chunked / mjpeg / segment_upload
  -> redevice_poll
  -> root_relay_inline
  -> root_relay
```

`webrtc_p2p` is the preferred realtime route for audio/video/data once the
endpoint has negotiated signaling and compatibility. Legacy Android agents may
fall back to `redevice_poll` for commands/events and `root_relay_inline` for
small degraded content. Skills may record the selected transport for diagnostics
but should not branch on platform-specific transport names unless they are
implementing an endpoint adapter.
