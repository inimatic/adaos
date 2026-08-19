# Media Center Local Validation - 2026-08-20

Status: feature implementation is validated locally. Sustained resource/browser
evidence, physical-node stand deployment, and explicit pilot/production
acceptance are still open.

## Exact Revisions

| Repository | Revision | Scope |
| --- | --- | --- |
| AdaOS core | `67ec6a93` | distributed deployment/topology SDK/runtime and pinned client |
| AdaOS client | `8f116252f3f57941a85d4f3fa0624d77667ecb89` | app-shell playback, cursor collections, spatial focus and presentation profiles |
| AdaOS registry | `a44f830a7ffc4f041d2391782ea6cc7f967b1d95` | Project/scenario, coordinator, library agent and control skill |

The core gitlink and `src/adaos/integrations/adaos-client.sha` both name the
exact client revision above.

## Reproduction And Results

| Gate | Result |
| --- | --- |
| Generic AP8/DS conformance | `49 passed` |
| Media scenario/agent/coordinator/control suite | `83 passed` after the decoder allowlist and reversible split changes |
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
| Coordinator FTS | 18.629 ms | 29.730 ms | 30.996 ms / 150 ms p95 |
| Cursor catalog page | 14.060 ms | 28.492 ms | 46.864 ms / 100 ms p95 |
| Local fuzzy/semantic discovery | 325.063 ms | 397.705 ms | 410.366 ms / 500 ms p95 |
| Encoded 30-row page | - | - | 40,728 bytes / 524,288 bytes |
| Process RSS after run | - | - | 47.629 MiB |

One-time FTS/trigram backfill was 4,167.633 ms. The local discovery index
admitted at most 5,000 candidates and scored 600. Two earlier failed runs are
retained as engineering evidence: repeated read-side index maintenance caused
FTS/page p95 up to 352/495 ms and an unindexed 5,000-row Python scan caused
discovery p95 above 3 s. Moving maintenance to the write boundary produced the
accepted result without relaxing budgets.

## Local Failure Evidence

| Failure | Observable proof |
| --- | --- |
| deployment inventory drift/lost acknowledgement | generic deployment conformance rejects or records terminal uncertain |
| coordinator/agent loss and stale shard | catalog keeps identity and reports bounded partial participation |
| interrupted scan/restart/cancel | durable job recovery, checkpoint and terminal cancellation tests |
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

## Open Stand Gates

1. Install exact releases through normal channels on `.30` and record package
   digests, deployment generation, activations, shard/catalog revisions and
   routes.
2. Capture desktop, TV-like and mobile-control screenshots and input traces.
3. Exercise real source playback, seek, modal close/mini-player, route fallback,
   node interruption, scan-under-playback and restart recovery.
4. Record CPU/RSS/I/O and browser long-task evidence during a sustained run.
5. Make an explicit bounded-pilot, production-accept, or rejection decision.
