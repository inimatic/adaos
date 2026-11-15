"""Core-owned continuing maintenance; durable jobs outlive HTTP requests."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import threading
import uuid

from adaos.services.artifact_pipeline.storage import atomic_write_json
from adaos.services.mutation_lock import MutationLockTimeout, mutation_lock
from adaos.services.runtime_identity import runtime_shared_state_write_authorized
from .storage import ROLES, now, read

log = logging.getLogger("adaos.node_maintenance.worker")
_workers = {}
_guard = threading.Lock()
HEARTBEAT_TIMEOUT_SECONDS = 15


def observation(state):
    """Read-only liveness projection; a retained `running` flag is not liveness."""
    saved = read(Path(state) / "worker.json", {})
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(saved["heartbeat_at"])).total_seconds()
        active = saved.get("status") == "running" and not saved.get("error") and 0 <= age < HEARTBEAT_TIMEOUT_SECONDS
    except (KeyError, ValueError, TypeError):
        active = False
    return {**saved, "active": active}


class MaintenanceWorker:
    def __init__(self, service):
        self.service = service
        self.state = service.locations.state
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.identity = uuid.uuid4().hex
        self.thread = threading.Thread(target=self.run, name="node-maintenance", daemon=True)

    def pulse(self, *, status="running", task=None, error=None):
        atomic_write_json(self.state / "worker.json", {
            "schema": "adaos.node.maintenance_worker.v1", "worker_id": self.identity,
            "pid": os.getpid(), "status": status, "heartbeat_at": now(),
            "task": task, "error": error,
        })

    def tick(self):
        progressed = False
        for job in self.service.migrations.snapshot():
            if self.stop_event.is_set() or not runtime_shared_state_write_authorized():
                break
            if job["status"] != "running":
                continue
            task = {"kind": "migration", "id": job["job_id"]}
            self.pulse(task=task)
            try:
                self.service.migrations.advance(job["job_id"], max_seconds=1)
            except MutationLockTimeout:
                continue
            except Exception as exc:
                log.exception("maintenance migration failed job=%s", job["job_id"])
                self.service.migrations.pause(job["job_id"], f"{type(exc).__name__}: {exc}")
            progressed = True
            self.pulse(task=task)
        for role in ROLES:
            if self.stop_event.is_set() or not runtime_shared_state_write_authorized():
                break
            root = self.service.locations.resolve(role)
            sample = self.service.inventory.snapshot(role, root)
            if sample["status"] != "running":
                continue
            task = {"kind": "inventory", "id": role}
            self.pulse(task=task)
            try:
                self.service.inventory.advance(role, root, max_entries=2000, max_seconds=.25)
            except MutationLockTimeout:
                continue
            except Exception as exc:
                log.exception("maintenance inventory failed role=%s", role)
                self.service.inventory.pause(role, f"{type(exc).__name__}: {exc}")
            progressed = True
            self.pulse(task=task)
        return progressed

    def run(self):
        while not self.stop_event.is_set() and runtime_shared_state_write_authorized():
            if self._own():
                return
            # An overlapping retiring Core must not strand the new owner.
            self.wake_event.wait(1)
            self.wake_event.clear()

    def _own(self):
        try:
            # One physical owner, including during overlapping Core restarts.
            with mutation_lock(self.state / "worker.lock", timeout_s=.1):
                try:
                    while not self.stop_event.is_set() and runtime_shared_state_write_authorized():
                        self.wake_event.clear()
                        try:
                            progressed = self.tick()
                            self.pulse()
                        except Exception as exc:
                            log.exception("maintenance discovery failed")
                            self.pulse(error=f"{type(exc).__name__}: {exc}"[:1000])
                            progressed = False
                        self.wake_event.wait(.02 if progressed else 1)
                finally:
                    self.pulse(status="stopped")
            return True
        except MutationLockTimeout:
            # The physical owner retains its heartbeat. Never overwrite it.
            log.info("maintenance worker is already owned by another Core")
            return False

    def stop(self):
        self.stop_event.set()
        self.wake_event.set()
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            log.warning("maintenance worker is finishing its current checkpoint")


def ensure_worker(service):
    key = str(service.locations.state.resolve())
    with _guard:
        worker = _workers.get(key)
        if worker is None or not worker.thread.is_alive():
            worker = _workers[key] = MaintenanceWorker(service)
            worker.thread.start()
        worker.wake_event.set()
        return worker


def stop_worker(service):
    key = str(service.locations.state.resolve())
    with _guard:
        worker = _workers.get(key)
    if worker is not None:
        worker.stop()
