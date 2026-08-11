# Media Core Boundary

AdaOS separates the media plane from media product semantics.

The core media plane owns the reusable mechanics that any skill or scenario can
depend on:

- `adaos.media.resource.v1` descriptors
- safe Media Server file names and storage paths
- browser and node content paths
- MIME detection
- byte `Range` parsing and bounded file streaming
- route contracts for local HTTP, root media relay, hub WebRTC, and
  member-browser direct media

Skills own the meaning of media:

- scanning user directories
- catalog records such as movie, album, episode, artist, or collection
- metadata enrichment, transcription, OCR, embeddings, and vector search
- playlists, queues, recommendations, and product UI
- source-specific permissions beyond core path containment

## Current Sources

`media_server` is the current shared core-backed storage adapter. It provides a
small file library and the publication target used by `adaos.sdk.io.media`.

`media_indexer` is a compatibility producer. It may still read legacy
`metadata.json` and `playback.sqlite3` artifacts, but it resolves them into the
same core `MediaResource` contract. Its FAISS/vector artifacts are not part of
the core media plane.

## Skill Interface

Skills should use `adaos.sdk.io.media`:

- `list_media_resources(...)`
- `publish_media_file(...)`
- `media_content_path(...)`
- `media_resource_content_path(...)`
- `media_indexer_content_path(...)`
- `media_resource_descriptor(...)`
- `browser_media_descriptor(...)`

Skills should not import `adaos.services.media_library` or
`adaos.services.media_indexer_library` unless they are implementing a core-owned
compatibility adapter.

`list_media_resources(source='all')` is the discovery boundary for cataloging
skills. It returns descriptors from the shared Media Server store and legacy
media-indexer compatibility adapter. The caller is still responsible for
deduplication, domain metadata, playlists, watch history, and any product-level
catalog state.

The first product slice using this boundary is documented in
[Media Center MVP](media-center-mvp.md).

## Compatibility Rules

The legacy `/api/node/media-indexer/content/{playback_id}` and
`/media/media-indexer/content/{playback_id}` routes remain available. They are
playback compatibility routes, not catalog APIs.

The `/api/node/media/files/content/{filename}` and
`/media/files/content/{filename}` routes serve the shared Media Server store and
may temporarily resolve a legacy media-indexer item by name for old snapshots.
New media-center work should publish explicit resource descriptors with opaque
IDs instead of relying on filename fallback.
