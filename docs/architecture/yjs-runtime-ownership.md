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

Release wheels are delivery artifacts for packaged runtimes. Repository
development intentionally resolves the vendored source, so a local Rust
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
| materialization worker | keep | protect event-loop latency and enforce timeout/RSS/result budgets |
| realtime/YWS sidecar boundary | keep as target architecture | preserve browser channels across A/B core updates |
| snapshot preflight subprocess | keep | reject corrupt native updates before they enter the runtime |
| scenario-switch subprocess | remove | duplicate process boundary with no independent responsibility |
| `gc.collect` after Yjs operations | remove | Python GC cannot collect a native Rust `Rc` cycle |
| `malloc_trim` after Yjs operations | remove | changes allocator RSS presentation, not object ownership |

The sidecar must eventually own the long-lived YWS transport and YDoc runtime,
not semantic Builder routing or scenario policy. Core A/B handoff transfers an
explicit channel/session contract. It must not infer topology from webspace
suffixes.

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
