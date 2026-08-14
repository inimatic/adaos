# Media Center MVP

AdaOS Media Center is a product skill and scenario built on top of the shared
media SDK boundary. It is not a new core media subsystem.

The first production-oriented slice is intentionally small:

- `media_center_skill` owns catalog state, reconciliation, favorites,
  play-count metadata, facets, details, and playback planning.
- `media_center` owns the user-facing workbench: source/kind/search/sort
  controls, catalog selection, playback surface, item details, and next-step
  guidance.
- `adaos.sdk.io.media.list_media_resources(...)` is the discovery seam used by
  the skill. `register_media_file(...)` registers an original file in place;
  core remains responsible for descriptors, root-bound path validation,
  content paths, MIME detection, route contracts, and ranged streaming.

This gives us a durable base for a state-of-the-art media center without moving
movie, album, episode, recommendation, or watch-history semantics into core.

## Implemented Slice

The MVP can scan current media producers and persist a local catalog in SQLite.
It supports:

- discovery from the shared Media Server store and the media-indexer
  compatibility adapter
- stable media-center item IDs derived from producer source and resource ID
- normalized kind detection for video, audio, image, and other resources
- keyword search across names, titles, tags, people, source, and MIME type
- filters by kind, source, favorite, and availability
- sorting by recent scan time, title, size, or play count
- favorite toggles
- playback planning through the core media content path
- a read-only browser media surface for catalog-backed playback
- in-place library registration: AdaOS stores only reference metadata in
  `state/media_references.sqlite3`; audio and video bytes remain under the
  configured source folder

The skill keeps the original resource descriptor JSON. That preserves producer
details for future migrations while letting the media center add its own product
state independently.

## Storage Semantics

The SDK exposes two intentionally different operations:

- `publish_media_file(...)` copies a generated or uploaded artifact into the
  managed Media Server store. The managed copy becomes the source of truth.
- `register_media_file(...)` registers a file owned by an external library.
  It records the resolved file and root paths, revalidates that boundary on
  every read, and streams the original file with HTTP Range support. It never
  copies media bytes into `.adaos`.

Media Center folder imports must use `register_media_file(...)`. This keeps
large and slow libraries in their original location while preserving the same
`adaos.media.resource.v1` playback contract for clients and other skills.

## Deferred Product Capabilities

The MVP deliberately defers production media-center features that need their own
contracts:

- library roots and scheduled background scans
- metadata providers for movies, shows, music, books, and photos
- episode/season/album/person collection models
- transcoding, subtitle extraction, preview sprites, and remote quality
  selection
- recommendations, queues, playlists, continue-watching, and multi-user state
- parental controls and media-specific authorization policy
- full-text, embedding, and perceptual duplicate search

These belong in the media-center skill family or future product scenarios. They
should depend on core media descriptors and routing instead of expanding the
core plane.

## Compatibility Position

`media_indexer` remains an inspiration and compatibility source. Its existing
metadata and playback artifacts can be surfaced through the SDK as media
resources, but FAISS/vector internals and ad hoc index structures do not become
core APIs.

The media center can later consume richer media-indexer outputs as optional
provider metadata. The dependency direction remains one-way: product skills
consume SDK resources; core does not learn media-center catalog concepts.
