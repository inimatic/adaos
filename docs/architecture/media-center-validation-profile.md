# Media Center Validation Profile

Status: acceptance budgets for the one-subnet household Media Center.

Last reviewed: 2026-08-20.

This profile turns roadmap resource and compatibility statements into explicit
gates. It applies to the representative 20,000-source fixture and to stand
evidence. A smaller development machine may run functional tests, but it may
not be used to claim these production budgets.

## Supported Baseline

### Nodes

- Linux x86-64 or arm64, Python 3.11, two logical CPU cores, and 2 GiB RAM.
- 2 GiB free AdaOS working space plus the configured derived-rendition quota.
  Original media capacity is external and is never counted as AdaOS storage.
- A readable local or mounted filesystem with stable file identity. Removable
  and network filesystems are allowed to become unavailable and must surface as
  stale/partial rather than disappear from catalog identity.
- `ffprobe` is optional for detailed technical facts. `ffmpeg` is optional for
  rendition and opt-in perceptual sampling; absence must produce an observable
  capability/result, not block scans or ordinary playback.

### Clients And Input

- Current and previous major Chromium/Edge, Firefox, and Safari where their
  Media Source/Media Session capabilities admit the selected source.
- TV profile: Chromium-class browser with keyboard/D-pad events and a 1280x720
  minimum viewport. Overscan-safe content inset is at least 32 CSS pixels.
- Desktop: 1024x640 minimum viewport with keyboard and pointer.
- Mobile control: 360x640 minimum viewport with touch; it is a controller and
  never relays source bytes to the playback target.
- Unsupported codecs must select an exact-revision derived rendition or return
  an explicit incompatibility decision.

### Network

- One trusted subnet, endpoint-to-agent reachability, and median command RTT
  below 100 ms. Route fallback remains valid up to 500 ms RTT.
- Sustained source bandwidth target: 20 Mbit/s for FHD and 100 Mbit/s for UHD.
  The planner must reject or select a lower variant when declared endpoint or
  network limits are insufficient.
- Coordinator, controller, and Yjs paths carry metadata/control only. Media
  bytes flow from the selected source agent to the playback endpoint.

## Resource Budgets

| Surface | Budget | Evidence |
| --- | --- | --- |
| Catalog page | at most 30 rows and 512 KiB encoded response | contract and 20k fixture test |
| Player/short queue | at most 10 rows | contract test |
| Durable queue snapshot | at most 500 source refs | contract test |
| Coordinator FTS, 20k | p95 <= 150 ms after warmup | local/stand benchmark |
| Deep-search local discovery | at most 5,000 candidates by default, 20,000 hard maximum; p95 <= 500 ms | local benchmark |
| Agent search | at most 100 rows per call and four agents by default | contract test |
| Folder/catalog UI | first useful render <= 1 s; no long task above 100 ms | browser trace |
| Idle renderer | <= 5% of one CPU core over 60 s and <= 350 MiB private memory | browser trace |
| Scan | one worker; <= 50% aggregate CPU on a two-core minimum node; configurable byte-rate | process metrics |
| Probe | one file at a time; 5 s default and 30 s hard timeout | worker test/metrics |
| Perceptual sample | opt-in, one thread, <= 512 KiB output, 10 s default and 30 s hard timeout | worker test |
| Rendition | one worker, one thread default, <= 1 GiB RSS, 2 h default timeout, explicit disk/output quotas | worker test/metrics |
| Playback command | p95 acknowledgement <= 250 ms on baseline LAN | QoE summary |
| First frame | p95 <= 2 s for a directly playable LAN source | endpoint QoE |
| Seek | p95 <= 1 s, excluding source spin-up disclosed by the route | endpoint QoE |
| Synchronized projections | bounded pages/snapshots only; no catalog, queue, or high-frequency position series in one Yjs value | contract and pressure test |

Playback, command transport, and synchronization take priority over scan,
probe, enrichment, embedding, perceptual sampling, and rendition. A resource
worker must enter `waiting_resources` under playback/critical pressure.

## Representative Library

The versioned fixture
`scenarios/media_center/tests/fixtures/library-profile.v1.json` contains 20,000
movie, series/season/episode, album/disc/track, audiobook/part/chapter, playlist,
alternative, duplicate, non-ASCII, unavailable-agent, slow/blocked-root,
unsupported-codec, overlap, symlink-cycle, and concurrent-change cases.

Private media is not a test dependency. Exact byte behavior uses tiny generated
files, while scale behavior uses generated descriptors and revisions. Stand
playback uses operator-owned compatible and incompatible samples and records
only hashes, technical facts, and route evidence.

## Acceptance Rules

1. Report p50/p95/max and sample count, not a single successful timing.
2. Keep cold-start, warm, playback-under-indexing, and degraded-agent runs
   separate.
3. A missing optional backend is a supported degraded result only when the UI
   and diagnostics explain it.
4. A budget miss leaves the relevant roadmap task open even if functional tests
   pass.
5. Browser, local process, and stand evidence must name exact core, client,
   ProjectRelease, skill, and scenario revisions.
