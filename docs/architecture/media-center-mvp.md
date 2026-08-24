# Media Center MVP

AdaOS Media Center is a product skill and scenario built on top of the shared
media SDK boundary. It is not a new core media subsystem.

This page records the bounded implemented product slice. The distributed target
and delivery sequence are maintained separately in
[Distributed Media Center Target Architecture](media-center-target-architecture.md)
and the [Media Center Roadmap](media-center-roadmap.md).

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
- Non-ASCII media names use an ASCII `Content-Disposition` fallback and an RFC
  5987 UTF-8 filename, so browser streaming headers remain valid.
- Root-routed playback records the installed route subscription explicitly;
  a fresh acknowledged lifecycle stream is valid control authority even when
  the selected topology has no inbound control-class subscription.

Media Center folder imports must use `register_media_file(...)`. This keeps
large and slow libraries in their original location while preserving the same
`adaos.media.resource.v1` playback contract for clients and other skills.

Network-file streaming must also preserve the runtime failure boundary. A
bounded thread executor is sufficient for ordinary local and Linux file I/O,
but it cannot cancel a Windows thread blocked in synchronous UNC/SMB I/O. The
Windows UNC relay therefore reads through a short-lived child process with a
binary pipe protocol, explicit open/read deadlines, one bounded first-read
retry, and termination on browser abort. Source paths travel over the private
stdin pipe and diagnostics retain only source kind and path digest. Reader PID,
active count, starts, timeouts, retries, per-operation latency, chunks, ACKs and
aborts remain observable from runtime reliability status.

## Playback State And Ownership

Playback state has three owners with different persistence and lifecycle
rules:

- Browser audio output volume, mute state, and selected output device belong to
  the browser media scope. Every audio and video element reads and updates the
  same preference; selecting another item must not reset volume.
- Video resume position belongs to `(browser scope, node, media resource)`.
  The browser checkpoints locally at a bounded interval and on pause/source
  detach, restores only an unfinished item, and clears a completed or explicitly
  stopped item. A normal `timeupdate` event must not write Yjs, skill memory, or
  a remote API. Account-level continue-watching can later synchronize the
  compact latest checkpoint on pause, item switch, and session end.
- The active player, queue, output lease, and system-media integration belong to
  a browser-runtime playback coordinator, not to a modal widget. Closing a
  detail modal detaches that view; it does not imply `stop`. An explicit Stop or
  Dismiss playback command releases the source and output lease.

The browser implementation should host one persistent media element in the
application shell and expose modal, compact now-playing, and optional
Picture-in-Picture presentations as controllers of that player. It should
publish metadata, playback state, action handlers, and position through the
[W3C Media Session API](https://www.w3.org/TR/mediasession/). A browser tab can
provide background-page playback but cannot guarantee playback after the OS
kills the browser process. Native Android packaging must put the player and
session in a
[Media3 `MediaSessionService`](https://developer.android.com/media/media3/session/background-playback),
and native Apple packaging must use the playback audio-session category plus
the audio background mode described by
[Apple's `AVAudioSession` documentation](https://developer.apple.com/documentation/avfaudio/avaudiosession).

Background behavior must be explicit and inspectable:

- audio continues when the detail modal closes and remains controllable from a
  persistent now-playing surface and platform media controls
- video continues visibly only in Picture-in-Picture or another attached
  surface; otherwise product policy chooses pause or audio-only continuation
- interruption, output-route change, audio-focus loss, and competing playback
  update the one coordinator instead of creating parallel media elements
- current resource, queue position, checkpoint age, presentation mode, output
  lease, and last interruption reason are available as bounded runtime status

## Product Evolution Beyond The First Slice

The original MVP boundary has now been extended without moving product nouns
into core. The Media Center skill family implements external library roots,
resumable background scans, bounded metadata/artwork jobs, full-text and local
semantic search, series/season/episode, album/disc/track and audiobook
collections, playlists, recommendations, profile-scoped favorites/history,
durable playback sessions, app-shell mini-player/PiP, remote control, and
review-only duplicate evidence. Original bytes still remain at their source.

The remaining product gates are exact-revision browser-compatible renditions
for unsupported codecs, richer subtitles/chapters and provider coverage,
long-running Android TV resource acceptance, and native Android/iOS background
playback. Unattended topology recommendations and automatic authority election
remain deferred. These stay in product skills, scenarios, native shells, or the
domain-neutral distributed runtime described by
[Distributed Service And Data Topology](distributed-service-and-data-topology.md).

## Compatibility Position

`media_indexer` remains an inspiration and compatibility source. Its existing
metadata and playback artifacts can be surfaced through the SDK as media
resources, but FAISS/vector internals and ad hoc index structures do not become
core APIs.

The media center can later consume richer media-indexer outputs as optional
provider metadata. The dependency direction remains one-way: product skills
consume SDK resources; core does not learn media-center catalog concepts.
