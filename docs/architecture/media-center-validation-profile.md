# Media Center Validation Profile

Status: acceptance budgets for the one-subnet household Media Center.

Last reviewed: 2026-08-26.

This profile turns roadmap resource and compatibility statements into explicit
gates. Static scale evidence covers 50,000 and 200,000 sources; sustained
acceptance uses at least 50,000 sources for one hour and remains paired with
stand evidence. A smaller development machine may run functional tests, but it
may not be used to claim these production budgets.

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
| Coordinator FTS, 50k/200k static | p95 <= 150 ms after warmup | local/stand benchmark |
| Coordinator FTS, 50k concurrent | p95 <= 200 ms for one hour beside agent deltas | acceptance soak |
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

## Local Evidence - 2026-08-24

The local single-node functional gate used client `0.0.368`, coordinator
`0.8.50`, agent `0.6.29`, control `0.2.7`, and scenario `0.6.16`. Playwright
proved rail movement, root-first folder drill-down, direct media selection,
modal-to-mini transition, explicit mini-player Close, album navigation without
implicit playback, grouped remote controls, and zero horizontal overflow at
412x915. A generated external video and audio track remained reference-backed;
only a derived 320x180 JPEG was written to managed storage.

The 120-second production-bundle soak reported 0.174 DOM mutations/s, 0.335 MiB
heap growth, no counted long tasks, 3.873% steady main-thread CPU, event-loop p95
0.3 ms, frame-delay p95 17.6 ms, and 80.59 MiB maximum renderer private memory.
Whole-renderer CPU was 5.089% during the first 45-second idle window, 0.089
percentage points above this profile's strict budget. A separate 30-second
sampling profile was idle for 29.479 seconds and found no repeating product
hotspot. Functional acceptance is local-complete, but the idle CPU budget stays
open for the longer Android TV/stand run; it is not waived by the local result.

## Local Evidence - 2026-08-26

The static Windows gates passed at 50,000 and 200,000 generated catalog items.
At 200,000 items, p95 was 130.630 ms for FTS, 35.105 ms for cursor pages,
113.563 ms for Home, 11.879 ms for root folders, 3.956 ms for leaf folders, and
385.059 ms for bounded fuzzy discovery. RSS was 37.82 MiB. The streamed search
and metadata projection backfills took 91.541 and 72.368 seconds; the bounded
50,000-item identity migration took 26.888 seconds.

The enforced one-hour server soak passed with 50,000 items, 289,875 concurrent
agent deltas, and no operation errors. P95 was 80.441 ms for FTS, 73.586 ms for
catalog pages, 38.874 ms for playback plans, and 142.322 ms for delta apply.
RSS peaked at 40.523 MiB with 0.668 MiB sustained growth, aggregate CPU p95 was
15.633%, and WAL retention ended at zero bytes.

During that workload, a one-hour desktop production-bundle run stayed inside
every resource budget: idle CPU 4.498%, steady main-thread CPU 5.473%, renderer
private-memory p95 201.914 MiB, JS heap growth 2.46 MiB, 1.407 DOM mutations/s,
input-delay p95 13.3 ms, and zero dropped frames. It observed eight long tasks
(0.133/min, maximum 832 ms). The original harness result was formally failed by
a locale-dependent playback selector and an expected handled reliability
projection fallback. The repaired selector and explicit compatible-fixture
probe subsequently advanced playback by 29.883 seconds with error code zero and
preserved one shell-owned media element across modal-to-mini transition. These
results close the local desktop performance investigation. They do not replace
the mandatory one-hour physical Android TV/CDP run, which remains open because
no Android TV debugging endpoint was attached to this development machine.
