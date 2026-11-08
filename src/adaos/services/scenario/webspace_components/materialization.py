from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial
from pathlib import Path
from collections.abc import Callable, Mapping
from typing import Any


_CPU_TIMING_SCOPE: ContextVar[tuple[dict[str, float], str, bool] | None] = ContextVar(
    "materialization_cpu_timing_scope", default=None,
)
_INTERACTIVE_MATERIALIZATION: ContextVar[bool] = ContextVar("interactive_materialization", default=False)


@contextmanager
def materialization_request_priority(request_id: str | None):
    """Carry admission priority across both modern and phased legacy paths."""
    token = _INTERACTIVE_MATERIALIZATION.set(str(request_id or "").startswith("nav:"))
    try:
        yield
    finally:
        _INTERACTIVE_MATERIALIZATION.reset(token)


def source_preparation_timings(external_inputs: Any) -> dict[str, float]:
    """Bounded source-worker observations; merge only on the YDoc owner."""
    details = external_inputs.get("source_stage_timings_ms", {}) if isinstance(external_inputs, Mapping) else {}
    if not isinstance(details, Mapping):
        return {}
    result = {}
    for name in ("dependency_preflight", "skill_declarations", "desktop_scenarios", "selected_ui", "shared_ui",
                 "launcher_metadata", "launcher_icons"):
        value = details.get(name)
        if type(value) in (int, float) and 0 <= value < 600000:
            result["prepare_sources." + name] = value
    dependency = external_inputs.get("dependency_preflight", {}) if isinstance(external_inputs, Mapping) else {}
    skills = dependency.get("skills", []) if isinstance(dependency, Mapping) else []
    for item in skills[:32] if isinstance(skills, list) else []:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        value = item.get("duration_ms")
        if (
            name
            and all(char.isalnum() or char in {"_", "-"} for char in name)
            and type(value) in (int, float)
            and 0 <= value < 600000
        ):
            result[f"prepare_sources.dependency.{name}"] = value
    return result


@contextmanager
def capture_cpu_timings(timings: dict[str, float], stage: str, *, interactive: bool | None = None):
    """Observe one task without changing function arguments or YDoc ownership."""
    priority = _INTERACTIVE_MATERIALIZATION.get() if interactive is None else bool(interactive)
    token = _CPU_TIMING_SCOPE.set((timings, stage, priority))
    try:
        yield
    finally:
        _CPU_TIMING_SCOPE.reset(token)


class _MaterializationQueue:
    """Prioritize admitted navigation without adding workers or starving repair.

    Keep waiting work here, not in ThreadPoolExecutor's FIFO. Four foreground
    admissions yield to one waiting background job. Cancellation only removes
    queued work; an already running callable retains its original semantics.
    Futures/locks (not asyncio primitives) support all caller event loops.
    """
    def __init__(self, executor, capacity, *, pending_limit=256):
        self.executor, self.capacity = executor, capacity
        self.pending_limit = pending_limit
        self.foreground, self.background = deque(), deque()
        self.active = self.burst = 0
        self.closed = self.dispatching = False
        self.lock = threading.RLock()

    def submit(self, call, *, interactive):
        future = Future()
        with self.lock:
            if self.closed:
                raise RuntimeError("materialization executor retired")
            if len(self.foreground) + len(self.background) >= self.pending_limit:
                self.foreground = deque(item for item in self.foreground if not item[0].cancelled())
                self.background = deque(item for item in self.background if not item[0].cancelled())
            if len(self.foreground) + len(self.background) >= self.pending_limit:
                raise RuntimeError("materialization backlog capacity exceeded")
            (self.foreground if interactive else self.background).append((future, call))
            self._dispatch()
        return future

    def _dispatch(self):
        if self.dispatching or self.closed:
            return
        self.dispatching = True
        try:
            while self.active < self.capacity and (self.foreground or self.background):
                if self.foreground and (self.burst < 4 or not self.background):
                    future, call = self.foreground.popleft()
                    interactive = True
                else:
                    future, call = self.background.popleft()
                    interactive = False
                if not future.set_running_or_notify_cancel():
                    continue
                self.burst = self.burst + 1 if interactive else 0
                self.active += 1
                try:
                    worker = self.executor.submit(call)
                except BaseException as exc:
                    self.active -= 1
                    future.set_exception(exc)
                    continue
                worker.add_done_callback(partial(self._complete, future))
        finally:
            self.dispatching = False

    def _complete(self, future, worker):
        try:
            future.set_result(worker.result())
        except BaseException as exc:
            future.set_exception(exc)
        finally:
            with self.lock:
                self.active -= 1
                self._dispatch()

    def close(self):
        with self.lock:
            self.closed = True
            for future, _ in (*self.foreground, *self.background):
                future.cancel()
            self.foreground.clear()
            self.background.clear()


class MaterializationExecutorOwner:
    """Own bounded CPU and subprocess execution for materialization."""

    def __init__(self, *, thread_name_prefix: str = "adaos-materialize") -> None:
        self._executor: ThreadPoolExecutor | None = None
        self._queue: _MaterializationQueue | None = None
        self._sources: MaterializationExecutorOwner | None = None
        self._thread_name_prefix = thread_name_prefix
        self._lock = threading.Lock()

    def get(self, *, max_workers: int) -> ThreadPoolExecutor:
        executor = self._executor
        if executor is not None:
            return executor
        with self._lock:
            executor = self._executor
            if executor is None:
                capacity = max(1, int(max_workers))
                executor = ThreadPoolExecutor(
                    max_workers=capacity,
                    thread_name_prefix=self._thread_name_prefix,
                )
                self._executor = executor
                self._queue = _MaterializationQueue(executor, capacity)
        return executor

    def _submit(self, call, *, max_workers, interactive):
        executor = self.get(max_workers=max_workers)
        with self._lock:
            if executor is not self._executor or self._queue is None:
                raise RuntimeError("materialization executor retired")
            return self._queue.submit(call, interactive=interactive)

    def shutdown(self) -> None:
        with self._lock:
            executor = self._executor
            self._executor = None
            queue, self._queue = self._queue, None
            sources = self._sources
            self._sources = None
        if queue is not None:
            queue.close()
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        if sources is not None:
            sources.shutdown()

    async def run_sources(self, function: Callable[..., Any], /, *args: Any,
                          oneshot: bool, **kwargs: Any) -> Any:
        """Filesystem-only preparation must not occupy the serial payload CPU lane.

        Each request owns its ScenarioManager. Only detached declarations leave
        this bounded pool; reading or mutating a live YDoc remains on its owner.
        """
        with self._lock:
            if self._sources is None:
                self._sources = MaterializationExecutorOwner(thread_name_prefix="adaos-materialize-source")
            sources = self._sources
        return await sources.run_cpu(function, *args, max_workers=2, oneshot=oneshot, **kwargs)

    async def run_cpu(
        self,
        function: Callable[..., Any],
        /,
        *args: Any,
        max_workers: int,
        oneshot: bool,
        **kwargs: Any,
    ) -> Any:
        call = partial(function, *args, **kwargs)
        scope = _CPU_TIMING_SCOPE.get()
        if scope is None:
            if oneshot:
                return call()
            return await asyncio.wrap_future(self._submit(call, max_workers=max_workers,
                interactive=_INTERACTIVE_MATERIALIZATION.get()))

        submitted = time.perf_counter()
        # Only monotonic timestamps cross threads; the sink is written on the
        # caller/owner thread after awaiting. No live YDoc moves to a new thread.
        stamps: list[float] = []

        def measured_call():
            stamps.append(time.perf_counter())
            try:
                return call()
            finally:
                stamps.append(time.perf_counter())

        try:
            if oneshot:
                return measured_call()
            return await asyncio.wrap_future(self._submit(measured_call,
                max_workers=max_workers, interactive=scope[2]))
        finally:
            resumed = time.perf_counter()
            sink, stage, interactive = scope
            sink[f"{stage}.interactive"] = float(interactive)
            sink[f"{stage}.dispatch_wall"] = round((resumed - submitted) * 1000, 3)
            if stamps:
                sink[f"{stage}.queue_wait"] = 0.0 if oneshot else round((stamps[0] - submitted) * 1000, 3)
            if len(stamps) == 2:
                sink[f"{stage}.function_wall"] = round((stamps[1] - stamps[0]) * 1000, 3)
                sink[f"{stage}.loop_resume_delay"] = 0.0 if oneshot else round((resumed - stamps[1]) * 1000, 3)
            # Cancellation before completion deliberately emits no fabricated
            # function duration; the still-running worker cannot mutate the sink.

    @staticmethod
    def _elapsed_ms(started_at: float) -> float:
        return round((time.perf_counter() - started_at) * 1000.0, 3)

    async def run_worker(
        self,
        request: Mapping[str, Any],
        *,
        timeout_s: float,
        max_rss_bytes: int,
        max_result_bytes: int,
        result_adapter: Callable[[Mapping[str, Any]], Any] | None = None,
    ) -> dict[str, Any]:
        """Run one isolated materialization request with bounded resources."""
        started = time.perf_counter()
        peak_rss = 0
        with tempfile.TemporaryDirectory(prefix="adaos-materialize-") as temp_dir:
            root = Path(temp_dir)
            request_path = root / "request.json"
            result_path = root / "result.json"
            stdout_path = root / "stdout.log"
            stderr_path = root / "stderr.log"
            # Generated Webspaces can make these files large, and Windows
            # filesystem/antivirus latency is unbounded. Never make the
            # asyncio owner thread wait for staging or result I/O.
            await asyncio.to_thread(
                request_path.write_text,
                json.dumps(dict(request), ensure_ascii=True, separators=(",", ":")),
                encoding="utf-8",
            )
            cmd = [
                sys.executable,
                "-m",
                "adaos.services.scenario.materialization_worker",
                str(request_path),
                str(result_path),
            ]
            env = os.environ.copy()
            env["ADAOS_MATERIALIZATION_WORKER"] = "0"
            creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0

            with stdout_path.open("w", encoding="utf-8") as stdout_file, stderr_path.open(
                "w",
                encoding="utf-8",
            ) as stderr_file:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    env=env,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    creationflags=creationflags,
                )
                try:
                    import psutil

                    process = psutil.Process(proc.pid)
                except Exception:
                    process = None

                def _process_tree() -> list[Any]:
                    if process is None:
                        return []
                    try:
                        return [process, *process.children(recursive=True)]
                    except Exception:
                        return [process]

                def _process_tree_rss() -> int:
                    total = 0
                    for item in _process_tree():
                        try:
                            total += int(item.memory_info().rss)
                        except Exception:
                            continue
                    return total

                async def _stop_process_tree() -> None:
                    descendants = _process_tree()[1:]
                    for child in reversed(descendants):
                        try:
                            child.terminate()
                        except Exception:
                            continue
                    if proc.returncode is None:
                        try:
                            proc.terminate()
                        except ProcessLookupError:
                            pass
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            try:
                                proc.kill()
                            except ProcessLookupError:
                                pass
                            await proc.wait()
                    deadline = time.monotonic() + 5.0
                    alive = list(descendants)
                    while alive and time.monotonic() < deadline:
                        remaining = []
                        for child in alive:
                            try:
                                if child.is_running():
                                    remaining.append(child)
                            except Exception:
                                continue
                        alive = remaining
                        if alive:
                            await asyncio.sleep(0.05)
                    for child in alive:
                        try:
                            child.kill()
                        except Exception:
                            continue

                failure: str | None = None
                wait_task = asyncio.create_task(proc.wait())
                try:
                    while not wait_task.done():
                        elapsed_s = time.perf_counter() - started
                        if elapsed_s > timeout_s:
                            failure = "materialization_worker_timeout"
                            break
                        if process is not None:
                            try:
                                current_rss = _process_tree_rss()
                                peak_rss = max(peak_rss, current_rss)
                                if current_rss > max_rss_bytes:
                                    failure = "materialization_worker_rss_limit"
                                    break
                            except Exception:
                                process = None
                        try:
                            await asyncio.wait_for(asyncio.shield(wait_task), timeout=0.05)
                        except asyncio.TimeoutError:
                            continue
                    if failure:
                        await _stop_process_tree()
                        await wait_task
                        raise RuntimeError(
                            f"{failure}: elapsed_ms={self._elapsed_ms(started)} "
                            f"peak_rss_bytes={peak_rss}"
                        )
                    returncode = int(await wait_task)
                except BaseException:
                    await _stop_process_tree()
                    if not wait_task.done():
                        await asyncio.shield(wait_task)
                    raise

            def _read_worker_files() -> tuple[str, bool, int, str]:
                try:
                    stderr_tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                except Exception:
                    stderr_tail = ""
                result_exists = result_path.exists()
                if not result_exists:
                    return stderr_tail, False, 0, ""
                return (
                    stderr_tail,
                    True,
                    int(result_path.stat().st_size),
                    result_path.read_text(encoding="utf-8"),
                )

            stderr_tail, result_exists, result_size, result_text = await asyncio.to_thread(
                _read_worker_files
            )
            if not result_exists:
                raise RuntimeError(
                    f"materialization_worker_no_result: returncode={returncode} stderr={stderr_tail}"
                )
            if result_size > max_result_bytes:
                raise RuntimeError(f"materialization_worker_result_limit: bytes={result_size}")
            result = json.loads(result_text)
            if not isinstance(result, dict) or returncode != 0 or not bool(result.get("ok")):
                detail = str(result.get("detail") if isinstance(result, dict) else "")
                raise RuntimeError(
                    "materialization_worker_failed: "
                    f"returncode={returncode} detail={detail} stderr={stderr_tail}"
                )
            child_final_rss = int(result.get("worker_rss_bytes") or 0)
            worker_peak_rss = max(peak_rss, child_final_rss)
            if worker_peak_rss > max_rss_bytes:
                raise RuntimeError(
                    f"materialization_worker_rss_limit: peak_rss_bytes={worker_peak_rss}"
                )
            result["worker_peak_rss_bytes"] = worker_peak_rss
            result["worker_result_bytes"] = result_size
            result["worker_parent_elapsed_ms"] = self._elapsed_ms(started)
            snapshot_b64 = result.pop("snapshot_update_b64", None)
            state_vector_b64 = result.pop("state_vector_b64", None)
            if isinstance(snapshot_b64, str):
                result["snapshot_update"] = base64.b64decode(snapshot_b64.encode("ascii"))
            if isinstance(state_vector_b64, str):
                result["state_vector"] = base64.b64decode(state_vector_b64.encode("ascii"))
            payload = result.get("materialized_payload")
            if isinstance(payload, Mapping) and result_adapter is not None:
                result["entry"] = result_adapter(payload)
            return result
