# Yjs Runtime Ownership

Status: implemented on 2026-07-22.

This document defines native YDoc ownership, dependency delivery, and the
responsibilities of the AdaOS Yjs wrapper. It replaces allocator cleanup and
process-exit workarounds that could reduce RSS but could not release leaked
Yrs stores.

## Root Cause

AdaOS previously used `y-py 0.6.2`, which embeds Yrs 0.12.2. An integrated
Yrs branch retained the document store with a strong `Rc` while the store
owned the same branch:

```text
YDoc -> Rc<Store> -> Branch -> Rc<Store>
```

Dropping the Python `YDoc`, closing a channel, evicting a room, and running
Python GC could not break this native reference-counting cycle. Any code that
called `get_map`, `get_array`, `get_text`, or integrated a nested type made a
document retain its full store. Room churn and detached materialization only
amplified the defect; they were not the owner of the retained memory.

The corrected model stores `Weak<Store>` in `Branch` and upgrades it only when
the branch needs a transaction:

```text
YDoc -> Rc<Store> -> Branch -> Weak<Store>
```

The store now has one forward ownership direction and is released when its
real owners are dropped.

## Fork Policy

AdaOS pins `y-py==0.6.2+adaos.1` and resolves it from `vendor/y-py`. The fork
keeps the 0.6.2 Python API and vendors Yrs 0.12.2 with only the ownership
backport. Provenance and build instructions are in
`vendor/y-py/ADAOS.md`.

Moving directly to `y-py 0.7.0a1` was rejected for this transition. Its Yrs
ownership is corrected, but its transaction behavior is not API-compatible
with current AdaOS call sites. The minimal fork separates memory correctness
from a future API migration.

`.github/workflows/y-py-wheels.yml` builds CPython 3.11 wheels on Linux,
Windows, and macOS. A `y-py-v*` tag publishes the matrix artifacts to a GitHub
release. Normal repository installs build the same pinned source through
`uv.lock`; Rust output must stay outside `vendor/y-py` so Python metadata
discovery never traverses Cargo artifacts.

The
[`y-py-v0.6.2-adaos.1` release](https://github.com/inimatic/adaos/releases/tag/y-py-v0.6.2-adaos.1)
is the canonical binary distribution for this transition. Its Linux artifact
targets `manylinux_2_17` rather than the build host glibc, so it remains
loadable on the Ubuntu 20.04 acceptance host.
The workflow can be rerun with `workflow_dispatch` and an existing
`release_tag`; reruns replace release assets instead of moving the version
tag.

Release wheels are delivery artifacts for packaged runtimes. The platform
requirements in `pyproject.toml` point Linux x86-64, Windows AMD64, and macOS
ARM64 installs at those immutable release assets; a normal `pip install` must
not depend on a private package index or silently fall back to public
`y-py 0.6.2`. Repository development intentionally overrides that dependency
with `[tool.uv.sources]` and resolves the vendored source, so a local Rust
toolchain remains required even if a release wheel was installed separately.

## Thread Ownership

`y-py 0.6.2` objects are thread-affine. Every live `YDoc` is created on its
YRoom owner thread, and all mutations, observer removal, room stop, and final
reference release must run on that same thread. `reset_live_webspace_room`
hands the complete reset coroutine to the recorded owner loop before touching
the room. If a foreign caller finds that the owner loop has already stopped,
the reset fails explicitly and keeps the room referenced instead of invoking
an unsafe destructor on the caller thread.

Temporary documents created by materialization and snapshot workers are also
created and dropped inside one worker invocation. Tests treat PyO3 unraisable
exceptions as failures because a wrong-thread `YDoc` destructor is skipped by
PyO3 and therefore becomes another native memory leak.

## Wrapper Responsibilities

| Mechanism | Decision | Responsibility |
| --- | --- | --- |
| YStore replay compaction | keep | bound replay entries/bytes and persist a recoverable base snapshot |
| idle room eviction | keep | end unused transport/session ownership and release room resources |
| fresh-doc materialization worker | keep | isolate full snapshot creation and enforce timeout/RSS/result budgets |
| payload resolver CPU executor | keep | run non-YDoc resolution off the event loop with a bounded process-owned pool |
| realtime/YWS sidecar boundary | keep as target architecture | preserve browser channels across A/B core updates |
| snapshot preflight subprocess | keep | reject corrupt native updates before they enter the runtime |
| scenario-switch subprocess | removed | payload-only resolution runs in the long-lived runtime; CPU work is threaded and YDoc ownership stays explicit |
| `gc.collect` after Yjs operations | remove | Python GC cannot collect a native Rust `Rc` cycle |
| `malloc_trim` after Yjs operations | remove | changes allocator RSS presentation, not object ownership |

The sidecar must eventually own the long-lived YWS transport and YDoc runtime,
not semantic Builder routing or scenario policy. Core A/B handoff transfers an
explicit channel/session contract. It must not infer topology from webspace
suffixes.

## Yjs Operation Contract

| Operation | Input | May mutate a live YDoc | Transport effect |
| --- | --- | --- | --- |
| bootstrap load | durable YStore snapshot | only static environment fields when changed | preserves the room/channel |
| payload resolve | canonical scenario, skills, and overlays | no | none |
| payload apply | already-resolved payload | yes, one selector/projection transaction | preserves the room/channel |
| reconcile | current selector plus canonical sources | yes, only as explicit repair | preserves the room/channel |
| reset/restore | explicit recovery command | replaces or recreates state | reconnect is expected |

`apply_materialized_payload_to_live_room` and
`reconcile_live_webspace_effective_branches` are intentionally separate public
APIs. Reconcile cannot accept a prepared payload, and apply cannot silently
fall back to source resolution. Ordinary scenario switching uses resolve then
apply. Hard room reset is not a switch primitive.

### Browser reconnect admission

Initial browser sync is an untrusted native boundary. Malformed frames and
payloads that fail isolated native preflight are rejected before the live YDoc
is touched. The current bounded MVP additionally uses server-authoritative
initial reconnect: browser `SYNC_STEP1` and the first client state update are
validated but not applied, and the server replays its durable effective state.
Subsequent updates follow the normal live-room path.

This policy prevents a stale or concurrently materialized browser document from
aborting or corrupting the shared native YDoc. Its explicit tradeoff is that an
offline-only browser draft present at reconnect is not merged. Safe offline
merge requires generation/checkpoint identity, conflict policy, and a bounded
reconciliation protocol; disabling this guard is not a substitute for that
design.

Bootstrap preserves runtime-owned `runtime.environment.materialization`
metadata while refreshing static environment fields. A durable snapshot is
reused without decoding large effective branches when its ready marker,
selector, materialized scenario, and required top-level branches agree. A
missing or stale marker takes the full validation/repair path. Bootstrap does
not emit `scenarios.synced` or schedule a hidden semantic rebuild.

Non-YDoc resolver work uses a bounded executor. The default is one worker;
`ADAOS_MATERIALIZATION_CPU_WORKERS` may be set from 1 through 4 after profiling
the target host. Live YDocs remain on their room owner loop regardless of this
setting.

## Acceptance Evidence

The migration has a process-isolated regression test in
`tests/test_yjs_native_memory.py`. It materializes and drops repeated 2 MB
documents after warm-up and rejects final private-memory growth of 16 MiB or
more. The package version is asserted so the public 0.6.2 wheel cannot pass by
accident.

Measured on the 1,701,543-byte `desktop` snapshot with the final release fork:

- 300 create/apply/access/drop cycles;
- USS samples remained within 51.95-69.80 MiB and finished at the 67.79 MiB
  warm-up baseline, for 0.00 MiB final growth;
- no cycle-correlated growth was present;
- mean single-thread materialization time was 217.600 ms in the acceptance
  container.

The public 0.6.2 build retained roughly 13-14 MiB per cycle on the same
snapshot. The bounded range in the fork is allocator reuse, not retained YDoc
stores. Materialization latency remains the baseline for the separate
parallelization work; memory cleanup must not be reintroduced as a performance
strategy.

### Release acceptance, 2026-07-22

The release matrix built and tested CPython 3.11 wheels for Windows x86-64,
Linux x86-64 (`manylinux_2_17`), and macOS arm64. The Windows and Linux wheels
both passed `tests/test_yjs_native_memory.py`; the broader Builder/Yjs/webspace
set passed 501 tests on Ubuntu 20.04 with Python 3.11.15.

The Linux release-wheel soak applied, accessed, and dropped a 2 MB YDoc 300
times after warm-up. USS changed by 8 KiB in total, with a last-150-cycle slope
of 409.6 bytes per ten cycles. Apply latency was 37.17 ms p50 and 40.67 ms p95.

A live Windows A/B used the real `builder_sdk_control_skill:select_preview`
path and alternated `builder` with `streaming_recipe_book_eval`:

- public 0.6.2: 12 switches grew private commit by 38.3 MiB and RSS by
  46.8 MiB; private growth was 3.19 MiB per switch;
- fork warm-up: the first 12 switches grew private commit by 20.5 MiB, then
  reached allocator reuse;
- fork plateau: another 40 switches stayed within 304.1-318.5 MiB private
  commit; the last-20-switch slope was -0.019 MiB per switch;
- the Angular dev server stayed at 1298.3 MiB private commit throughout both
  runs, so the measured change belongs to the Python runtime;
- after the old-runtime run, room buffers and waiting senders were zero;
  waiting receivers equaled the five active rooms. The growth was therefore
  not a queue blocked on a closed channel.

On the 40-switch plateau, end-to-end readiness was 5.770 s p50 and 6.995 s
p95. Rebuild time was 4.175 s p50 and 4.936 s p95. These timings remain the
input to materialization parallelism work; they are not part of the ownership
fix.

### Switch-path acceptance, 2026-07-23

After resolver, bootstrap, and live-apply ownership were simplified, the local
Windows runtime completed a 20-switch warm-up followed by three blocks of 120
alternating `web_desktop` / generated DEV scenario switches:

- all 360 measured switches reached `ready`; no timeout or transient control
  failure occurred;
- the final 120-switch block held RSS at 298.6 -> 298.4 MiB and private memory
  at 314.5 -> 311.3 MiB, with 14 threads before and after;
- the room stayed at generation 1 with zero reset/drop and zero pending
  send/store tasks;
- average server-ready time was 184 ms, 171 ms, and 222 ms for the three
  blocks; the final block ranged from 107 to 997 ms under concurrent browser
  activity;
- replay pressure auto-backup converged to zero replay entries/bytes and an
  up-to-date persisted snapshot.

The compact Yjs snapshot still grew by about 0.8 KiB per alternating switch.
That is CRDT struct/history growth, not retained process memory or a blocked
channel. It requires a future generation-aware server/client checkpoint
protocol that also invalidates stale browser IndexedDB history. An implicit
room reset on ordinary switch would reconnect the UI and allow an old client
document to merge the discarded history back, so it is not an acceptable
workaround.

## Upgrade Rules

- Keep the fork patch minimal and trace every native change to upstream or an
  AdaOS regression test.
- Build and test wheels for every supported OS before changing the runtime
  dependency tag.
- A future Yrs/y-py upgrade must pass the native-memory regression plus AdaOS
  Yjs, YStore, Builder preview, and scenario-switch tests.
- Never put a `YDoc` in a Python class-level attribute or a cross-thread
  garbage-collection cycle; keep it as an explicitly released owner instance
  attribute.
- Do not add global GC, allocator trim, room reset, or process restart as a
  response to unexplained memory growth. First prove the retaining ownership
  path with a repeatable document lifecycle test.
