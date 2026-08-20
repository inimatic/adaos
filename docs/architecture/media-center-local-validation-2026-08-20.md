# Media Center Local Validation - 2026-08-20

Status: feature implementation is validated locally and the single-node
library/agent path has bounded stand evidence. Sustained browser/playback
evidence and distributed production acceptance remain open.

## Exact Revisions

| Repository | Revision | Scope |
| --- | --- | --- |
| AdaOS core | `598bc015` | distributed deployment/topology SDK/runtime, service-event bridge, production test budgets and pinned client |
| AdaOS client | `8f116252f3f57941a85d4f3fa0624d77667ecb89` | app-shell playback, cursor collections, spatial focus and presentation profiles |
| AdaOS registry | `4e9f7d182405442df0695eed29ceeb3969c3005a` | Project/scenario, coordinator, library agent, control skill and contextual audio migration |

The core gitlink and `src/adaos/integrations/adaos-client.sha` both name the
exact client revision above.

## Reproduction And Results

| Gate | Result |
| --- | --- |
| Generic AP8/DS/service-event conformance | `59 passed` |
| Media scenario/agent/coordinator/control suite | `97 passed` |
| Strict skill validation | `media_library_agent`, `media_center_skill`, `media_control_skill`: passed |
| Scenario validation | `media_center`: valid |
| Client Chrome Headless | `1111/1111 SUCCESS` |
| Client production build | passed; existing CSS/initial-bundle budget warnings remain non-fatal |
| English/Russian docs | `mkdocs build --strict`: passed |

The exact generic command is recorded in
[Distributed Deployment And Topology Conformance](distributed-runtime-conformance-2026-08-20.md).
The media suite command is:

```text
python -m pytest scenarios/media_center/tests \
  skills/media_library_agent/tests/test_media_library_agent.py \
  skills/media_center_skill/tests/test_media_center_skill.py \
  skills/media_control_skill/tests/test_media_control_skill.py -q
```

## 20,000-Source Budget Gate

`scenarios/media_center/benchmarks/run_library_benchmark.py --enforce` passed
on the Windows development node:

| Metric | p50 | p95 | max/bound |
| --- | ---: | ---: | ---: |
| Coordinator FTS | 23.280 ms | 65.884 ms | 123.621 ms / 150 ms p95 |
| Cursor catalog page | 16.461 ms | 33.322 ms | 57.481 ms / 100 ms p95 |
| Local fuzzy/semantic discovery | 309.262 ms | 368.821 ms | 373.798 ms / 500 ms p95 |
| Encoded 30-row page | - | - | 50,298 bytes / 524,288 bytes |
| Contextual identity migration | - | - | 5,818.833 ms / 60,000 ms |
| Process RSS after run | - | - | 55.477 MiB |

One-time FTS/trigram backfill was 4,845.279 ms. The identity fixture migrated
20,000 audio rows, removed 20,000 legacy works and collections, and rebuilt
20,000 distinct works and memberships. The local discovery index admitted at
most 5,000 candidates and scored 600. Earlier failed runs are
retained as engineering evidence: repeated read-side index maintenance caused
FTS/page p95 up to 352/495 ms and an unindexed 5,000-row Python scan caused
discovery p95 above 3 s; a formally passing run with zero FTS results exposed a
missing correctness assertion; and a one-work migration fixture failed to
expose quadratic cleanup on real collections. The accepted gate checks result
counts and cleanup cardinality without relaxing budgets.

## Local Failure Evidence

| Failure | Observable proof |
| --- | --- |
| deployment inventory drift/lost acknowledgement | generic deployment conformance rejects or records terminal uncertain |
| coordinator/agent loss and stale shard | catalog keeps identity and reports bounded partial participation |
| interrupted scan/restart/cancel | durable job recovery, single service owner, shared pressure state, checkpoint and terminal cancellation tests |
| blocked/unmounted filesystem and root overlap | root failure/overlap tests preserve external files |
| source changes during rendition | output is invalidated, cleaned and never advertised |
| unsupported codec | endpoint plan selects exact-source rendition or explicit incompatibility |
| playback route failure/browser reconnect | app-shell fallback/reconcile tests retain one session and avoid duplicate seek |
| conflicting controllers | lease and expected command revision reject stale commands |
| Yjs/large list pressure | pages are 30, selectors 10, durable queues 500; client collection virtualization tests pass |
| decoder egress attempt | ffmpeg/ffprobe command uses `file,pipe` protocol allowlist |

## Product Coverage

- One Project owns coordinator, project-only node agents, control skill and
  desktop/TV/mobile/embedded entrypoints. Public deployment/topology SDKs own
  placement, versions, operations, leases, fencing and routing.
- Agents own roots, schedules/watch reconciliation, bounded scans/probes,
  exact source revisions, deltas, deep technical search, perceptual sampling
  and renditions. Original bytes remain external references.
- Coordinator owns global FTS/fuzzy/semantic discovery, folders, typed works,
  variants and collections, playlists, reversible merge/split/regroup,
  profiles/policy, recommendations, enrichment claims and diagnostics.
- App shell owns persistent playback, queue recovery, Media Session, PiP/audio
  background policy and target route fallback. TV/desktop/mobile views consume
  the same UI-as-data contracts and restore semantic focus.
- Editorial featured rails are deliberately not admitted because this fixture
  has no reviewed artwork/metadata source; ordinary Home and grid browsing do
  not depend on fabricated hero content.
- Voice uses existing catalog/control tools. Compound requests are bounded
  governed-workflow requests requiring confirmation; they are not directly
  executed by the speech handler.

## Stand Evidence

The physical-node receipt is recorded in
[Media Center Stand Validation - 2026-08-20](media-center-stand-validation-2026-08-20.md).
It includes an in-place 15,803-source/93.27 GB library, folder-name search,
cursor paging, public-path redaction, staged skill activation, bounded worker
resources and terminal progress delivery through the service bridge.

## Open Stand Gates

1. Capture desktop, TV-like and mobile-control screenshots and input traces.
2. Exercise sustained real source playback, seek, modal close/mini-player, route fallback,
   node interruption, scan-under-playback and restart recovery.
3. Record browser CPU/RSS and long-task evidence during a sustained run.
4. Execute a two-physical-node handoff and compatible rolling adapter upgrade.
