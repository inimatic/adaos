# Media Center Stand Validation - 2026-08-20

Status: accepted for a bounded single-node trusted-subnet pilot. Distributed
and broad production acceptance are rejected until the open two-node, browser
and sustained-soak gates are completed.

## Exact Revisions

| Surface | Revision/version |
| --- | --- |
| AdaOS core implementation on `.30` | `598bc015bd04a1400a4172893c876135f8711eb2` |
| AdaOS client pinned by that core | `8f116252f3f57941a85d4f3fa0624d77667ecb89` |
| AdaOS registry workspace | `4e9f7d182405442df0695eed29ceeb3969c3005a` |
| `media_center_skill` | `0.8.9`, active slot A, pytest passed |
| `media_library_agent` | `0.6.3` |
| `media_control_skill` | `0.2.0` |
| `media_center` scenario | `0.6.1`, validation passed |

The registry commits from `35129f6` through `4e9f7d1` are one staged feature
line. Intermediate skill versions were intentionally advanced after failed
candidate or stand acceptance rather than reusing published version bytes.

## Deployment And Runtime

- Node `9161e4df-772a-4795-a6b3-1c4b95158802` is ready in subnet
  `sn_92ffc943`; hub-root control and route relay both reported `ready`.
- Desktop materialization is accepted and names `media_center` as the current
  scenario. Rebuild status is `ready`, source is `loader:workspace`, all eight
  required branches are present, 13 page widgets are materialized, no branch
  failed, and legacy fallback is inactive.
- Compatibility-only registry/data scenario caches remain absent. Runtime
  reports `runtime_removal_ready=true`; these caches are not an active source
  or blocker.
- The materialization diagnostic was read from an expired rebuild cache after
  its three-second TTL, but the rebuild receipt itself was ready and the API
  returned `accepted=true`. This is not recorded as fresh browser evidence.

## In-Place Library

The active library agent owns `/mnt/disk1/Music`:

| Measure | Result |
| --- | ---: |
| Agent sources | 15,803 |
| Referenced source bytes | 93,270,941,196 |
| Coordinator rows | 24,052 |
| Available rows | 16,209 |
| Missing compatibility rows | 7,843 |
| Available agent rows | 15,803 |
| Available cross-source exact-path duplicates | 0 |

Agent state contains SQLite catalog/checkpoint data only. Original media bytes
remain under the mounted source root; no media payload was copied into
`.adaos`. Public catalog and diagnostic responses do not expose the absolute
`/mnt` root. Relative folder paths remain visible because folder navigation and
folder-name search are product behavior.

Initial coordinator catch-up resumed at cursor 6,000, applied 9,803 remaining
deltas and stopped at 15,803 with `has_more=false`. Participation then reported
one expected, available and fresh agent with no unavailable or stale shard.

## Search, Identity And Paging

- Search for `Апулей` returned ten numbered MP3 files using parent folder
  segments, with `has_more=true`, in 1.746 seconds through the production CLI
  path.
- The selected `0.mp3` belongs to the expected Апулей audiobook folder. Its
  playback plan now retains the same exact `source_id`, exposes the same
  relative path and has one candidate instead of selecting a same-named file
  from another author.
- The audio identity migration marker is `1`, coordinator schema revision is
  `2026-08-20.7`, and 15,802 audio rows materialize as 14,744 contextual works
  and 1,608 collections. Replicas with the same contextual identity can still
  be variants; files in different books cannot collide merely because both are
  named `0.mp3`.
- Catalog pages return at most 30 rows with opaque continuation cursors and
  lower-bound counts. Player selectors are bounded to ten rows. No full 20,000
  row list is put into a widget or synchronized document.

## Playback

The verified plan selected the exact agent reference for the searched item in
1.727 seconds. An authenticated byte-range request to the sidecar direct
candidate returned:

```text
HTTP/1.1 206 Partial Content
Accept-Ranges: bytes
Content-Type: audio/mpeg
Content-Range: bytes 4096-8191/579584
Content-Length: 4096
```

The 4,096-byte body was read from the original mounted file through the core
reference resolver. The same request without a hub token returned `401`, as
required by the media-route boundary. The coordinator and controller did not
proxy or copy the source bytes.

## Progress And Resource Evidence

- An incremental agent job entered `running`, published eight progress events
  through the service-to-runtime bridge with zero delivery failures, and was
  canceled cleanly after 301 files and about 1.03 GB of skipped unchanged data.
- Cross-process playback pressure became observable by the persistent agent in
  one second and returned to normal after the test.
- During active enrichment, a 45-second sample kept the agent at 87-90 MB RSS,
  0.3-0.4% CPU and two threads. Runtime RSS stayed near 369 MB; enrichment
  completed 337 jobs while queued work declined by the same amount.
- Concurrent reads remained bounded: three item reads took 1.58-1.67 seconds
  and a 30-row library page took 1.716 seconds. Compact Home publication was 47
  items and 35,259 bytes, without resource descriptors or direct paths.
- No `browser_stream_payload_pressure` or blocked-publication warning appeared
  after compact/coalesced publication was activated.

## Retained Failure Evidence

| Failure | Result and correction |
| --- | --- |
| broad FTS benchmark passed with zero results | benchmark now performs explicit backfill and rejects zero FTS/fuzzy results; broad p95 is below budget |
| same-named audiobook files shared one work | contextual audio identity, stable source-owned variant ids, migration and playback tie-break tests added |
| `0.8.7` candidate test hit the active DB | compound voice planning now returns before catalog initialization; `0.8.8` test enforces the pure path |
| timed-out SSH left an install process alive | only the owned orphan was terminated; the failed activation stayed fail-closed and retained the previous ready runtime |
| first real identity migration consumed CPU for minutes | missing parent/work/alias cleanup indexes were added and a 20,000-work/20,000-collection regression gate was retained |
| final migration was slow despite bounded SQL | `.30` spent most time in kernel I/O QoS flushing about 70 MB WAL; the complete tested install and activation took 582 seconds |
| unauthenticated direct range returned 401 | authenticated route returned 206; access control was preserved |

No source file was deleted by any failed candidate, rollback, migration or
agent cancellation.

## Acceptance Decision

Accepted:

- exact-revision local validation;
- one physical node with one in-place large library;
- bounded single-node trusted-subnet household pilot;
- search, contextual identity, paging, progress delivery and authenticated
  ranged playback on that node.

Not accepted:

- distributed household production;
- unattended multi-node failover or rolling adapter upgrade;
- native background playback guarantees;
- broad production rollout.

The Windows development hub is healthy but belongs to `sn_6acf0c01`; `.30`
belongs to `sn_92ffc943`. Reassigning the parallel-work hub would mutate an
unrelated live environment, so it was not used as a fabricated second member.
Open gates are a second trusted physical node in `sn_92ffc943`, separate TV
and controller browser evidence, sustained playback/indexing/resource soak,
source-node interruption and a compatible rolling adapter upgrade.
