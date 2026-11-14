"""Resumable cold relocation of managed E2E roots; originals are retained.

Admission and producer leases close the cooperative writer race. A compatibility
directory link preserves historical absolute evidence references. Recovery roots
need their own writer/restore adapters and are deliberately not admitted here.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import time

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from .storage import digest, now, read, volume

SUPPORTED = {"e2e_evidence", "stand_evidence"}
CHUNK = 4 * 1024**2
TERMINAL = {"completed", "cancelled"}


def gate_paths(root):
    root = Path(root).absolute()
    if (root.name == "artifacts" and root.parent.name == "e2e") or (root.name == "e2e-runs" and root.parent.name == "artifacts"):
        control = root.parent.parent / ".adaos/state/maintenance/producer-gates" / digest(str(root))
        return control.with_suffix(".json"), control.with_suffix(".lock")
    return (root.parent / f".{root.name}.adaos-migration.json",
            root.parent / f".{root.name}.adaos-migration.lock")


@contextmanager
def producer_admission(path):
    """Called before publishing a run's active manifest and lease."""
    path = Path(path).absolute()
    root = next((p for p in (path, *path.parents)
                 if (p.name == "artifacts" and p.parent.name == "e2e")
                 or (p.name == "e2e-runs" and p.parent.name == "artifacts")), None)
    if root is None:
        for parent in (path, *path.parents):
            marker = read(parent / ".adaos-migration-owner.json", {})
            if marker.get("admission_root"):
                root = Path(marker["admission_root"])
                break
    if root is None:
        yield
        return
    gate, lock = gate_paths(root)
    with mutation_lock(lock, timeout_s=2):
        record = read(gate)
        if record and record.get("status") not in TERMINAL:
            raise ValueError("Storage relocation is in progress; resume or cancel it before starting E2E")
        if record and record.get("status") == "completed" and not root.is_dir():
            raise ValueError("Relocated E2E storage is unavailable; restore the destination volume")
        yield


def _plain(path, directory=False):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("Relocation does not follow nested links or reparse points")
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError("Relocation requires regular files and directories")
    return info


def _inside(root, path):
    """Check physical containment as well as every existing parent component."""
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Migration entry escapes its reviewed physical root")
    parent = path.parent
    while parent != root:
        _plain(parent, directory=True)
        if parent == parent.parent:
            raise ValueError("Migration entry is outside its reviewed root")
        parent = parent.parent
    _plain(root, directory=True)


def _mkdir(root, directory):
    _plain(root, directory=True)
    current = root
    for part in directory.relative_to(root).parts:
        current = current / part
        if not current.exists():
            current.mkdir()
        _plain(current, directory=True)


def _link(source, destination):
    if os.name == "nt":
        env = {**os.environ, "ADAOS_LINK_SOURCE": str(source), "ADAOS_LINK_TARGET": str(destination)}
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                        "$ErrorActionPreference='Stop'; New-Item -ItemType Junction "
                        "-Path $env:ADAOS_LINK_SOURCE -Target $env:ADAOS_LINK_TARGET | Out-Null"],
                       env=env, check=True, capture_output=True, creationflags=0x08000000)
    else:
        source.symlink_to(destination, target_is_directory=True)
    if source.resolve() != destination.resolve():
        raise ValueError("Compatibility link verification failed")


class StorageMigration:
    def __init__(self, locations, inventory):
        self.locations, self.inventory = locations, inventory
        self.state = locations.state / "migrations"

    def _path(self, job_id):
        if len(str(job_id)) != 64 or any(c not in "0123456789abcdef" for c in str(job_id)):
            raise ValueError("Invalid migration identity")
        return self.state / job_id

    def plan(self, role, expected_revision, desired_path=None):
        settings = self.locations.settings()
        if role not in settings["roles"] or settings["revision"] != expected_revision:
            raise ValueError("Storage selection or revision changed; refresh before planning")
        selected = settings["roles"][role]
        source = Path(selected["active_path"])
        reasons = []
        if role not in SUPPORTED:
            reasons.append("Recovery relocation requires its writer and restore adapter; this beta supports E2E evidence")
        if not selected.get("desired_path") or selected["desired_path"] == str(source):
            reasons.append("Save a different desired location first")
        target = self.locations.validate_destination(role, selected.get("desired_path") or str(source))
        if desired_path is not None and self.locations.validate_destination(role, desired_path) != target:
            raise ValueError("Save the edited desired location before reviewing data migration")
        sample = self.inventory.snapshot(role, source)
        if not sample.get("complete"):
            reasons.append("Complete source measurement before reviewing relocation")
        if not source.is_dir() or source.resolve() != source.absolute():
            reasons.append("Source must be an available physical directory; repeated relocation is not yet supported")
        if selected.get("compatibility_path"):
            reasons.append("This beta supports the first relocation only; chaining compatibility roots is not admitted")
        if target.exists():
            reasons.append("Destination must be a new directory; existing data is never merged or overwritten")
        observed = volume(target)
        required = int(sample.get("bytes") or 0) + selected["min_free_bytes"]
        if observed["free_bytes"] < required:
            reasons.append("Destination needs the measured source bytes plus the configured free-space reserve")
        if selected.get("quota_bytes") and int(sample.get("bytes") or 0) > selected["quota_bytes"]:
            reasons.append("Measured source exceeds the configured quota")
        payload = {"schema": "adaos.node.migration_plan.v1", "node_id": self.locations.node_id,
                   "role": role, "expected_revision": expected_revision, "source": str(source),
                   "destination": str(target), "volume_id": observed["id"],
                   "requested_path": desired_path if desired_path is not None else str(target),
                   "source_volume_id": volume(source)["id"], "estimated_bytes": sample.get("bytes"),
                   "min_free_bytes": selected["min_free_bytes"], "created_at": now(),
                   "admitted": not reasons, "reasons": reasons,
                   "effect": "copy_verify_switch_with_compatibility_link", "retains_original": True,
                   "source_space_reclaimed": False}
        plan_id = digest(payload)
        atomic_write_json(self.state / "plans" / f"{plan_id}.json", payload)
        return {**payload, "plan_digest": plan_id}

    def snapshot(self):
        if not self.state.exists():
            return []
        rows = sorted(self.state.glob("*/job.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]
        return [self._public(read(p)) for p in rows]

    @staticmethod
    def _public(job):
        return {k: v for k, v in job.items() if k not in {"pending", "verify_pending", "destination_pending"}}

    def _save(self, job):
        job["updated_at"] = now()
        atomic_write_json(self._path(job["job_id"]) / "job.json", job)

    def start(self, plan_digest):
        folder = self._path(plan_digest)
        with mutation_lock(self.locations.state / "storage-settings.lock", timeout_s=2):
            previous = read(folder / "job.json")
            if previous:
                self._gate(previous)
                return {**self._public(previous), "replayed": True}
            plan = read(self.state / "plans" / f"{plan_digest}.json")
            if not plan or digest(plan) != plan_digest or plan["node_id"] != self.locations.node_id:
                raise ValueError("Exact migration plan is unavailable")
            if not plan["admitted"] or plan["role"] not in SUPPORTED:
                raise ValueError("Migration plan is not admitted")
            if self.locations.settings()["revision"] != plan["expected_revision"]:
                raise ValueError("Migration plan is stale; refresh and review again")
            if any(p.parent != folder and read(p, {}).get("status") not in TERMINAL for p in self.state.glob("*/job.json")):
                raise ValueError("Resume or cancel the active migration before starting another")
            source, target = Path(plan["source"]), self.locations.validate_destination(plan["role"], plan["destination"])
            current = volume(target)
            if current["id"] != plan["volume_id"] or volume(source)["id"] != plan["source_volume_id"]:
                raise ValueError("Migration volume identity changed")
            if current["free_bytes"] < plan["estimated_bytes"] + plan["min_free_bytes"] or target.exists():
                raise ValueError("Destination changed or no longer has sufficient free space")
            gate, lock = gate_paths(source)
            with mutation_lock(lock, timeout_s=2):
                old = read(gate)
                if old and old.get("status") not in TERMINAL and old["job_id"] != plan_digest:
                    raise ValueError("Another migration owns the source")
                job = {**plan, "job_id": plan_digest, "status": "running", "phase": "scan",
                       "pending": [{"path": ".", "offset": 0}], "files": 0, "bytes": 0,
                       "copied_bytes": 0, "verified_bytes": 0, "cursor": 0, "offset": 0,
                       "retained_source": str(source.with_name(source.name + ".retained-" + plan_digest[:16])),
                       "error": None}
                if Path(job["retained_source"]).exists():
                    raise ValueError("Retained source path already exists")
                self._save(job)
                atomic_write_json(gate, {"job_id": plan_digest, "status": "running"})
            return self._public(job)

    def _scan(self, job, db, root, deadline, verify=False, destination=False):
        key = "destination_pending" if destination else "verify_pending" if verify else "pending"
        counter = "destination_entries" if destination else "verified_entries"
        if verify and key not in job:
            job[key] = [{"path": ".", "offset": 0}]
            job[counter] = 0
        count = 0
        while job[key] and time.monotonic() < deadline and count < 2000:
            frame = job[key].pop()
            directory = root / frame["path"]
            _plain(directory, directory=True)
            with os.scandir(directory) as entries:
                for index, entry in enumerate(entries):
                    if index < frame["offset"]:
                        continue
                    if count >= 2000 or time.monotonic() >= deadline:
                        job[key].append({"path": frame["path"], "offset": index})
                        break
                    count += 1
                    path = Path(entry.path)
                    relative = path.relative_to(root).as_posix()
                    if destination and relative == ".adaos-migration-owner.json":
                        if read(path, {}).get("job_id") != job["job_id"]:
                            raise ValueError("Destination ownership marker changed")
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        _plain(path, directory=True)
                        kind, size, mtime = "dir", 0, 0
                        job[key].insert(0, {"path": relative, "offset": 0})
                    else:
                        info = _plain(path)
                        kind, size, mtime = "file", info.st_size, info.st_mtime_ns
                        if entry.name == ".adaos-lease.json":
                            raise ValueError("An E2E producer lease remains active; cancel relocation and finish that run")
                        if entry.name == ".adaos-storage.json" and read(path).get("status") == "active":
                            raise ValueError("An E2E run is active; cancel relocation and finish that run")
                    if verify:
                        row = db.execute("SELECT kind,size,mtime FROM entries WHERE path=?", (relative,)).fetchone()
                        if row is None or (row[:2] != (kind, size) if destination else row != (kind, size, mtime)):
                            raise ValueError("Source changed during relocation; originals are retained")
                        job[counter] += 1
                    else:
                        db.execute("INSERT INTO entries(path,kind,size,mtime) VALUES(?,?,?,?)", (relative, kind, size, mtime))
                        job["files"] += kind == "file"
                        job["bytes"] += size
        if not job[key]:
            if verify:
                if job[counter] != db.execute("SELECT COUNT(*) FROM entries").fetchone()[0]:
                    raise ValueError("Source entries disappeared during relocation")
                job["phase"] = "seal" if destination else "verify_destination"
            else:
                job["phase"] = "copy"

    def _copy(self, job, db, source, target, deadline, verify=False):
        while time.monotonic() < deadline:
            row = db.execute("SELECT rowid,path,kind,size,mtime FROM entries WHERE rowid>? ORDER BY rowid LIMIT 1", (job["cursor"],)).fetchone()
            if not row:
                if verify:
                    checksum = hashlib.sha256()
                    for chunk in db.execute("SELECT entry,offset,size,sha256 FROM chunks ORDER BY entry,offset"):
                        checksum.update(json.dumps(list(chunk), separators=(",", ":")).encode())
                        checksum.update(b"\n")
                    job["content_digest"] = "sha256:" + checksum.hexdigest()
                job.update(phase="verify_source" if verify else "verify_copy", cursor=0, offset=0)
                return
            row_id, relative, kind, size, mtime = row
            src, dst = source / relative, target / relative
            _inside(source, src)
            if kind == "dir":
                if not verify:
                    _mkdir(target, dst)
                _inside(target, dst)
                _plain(dst, directory=True)
                job["cursor"] = row_id
                continue
            info = _plain(src)
            if (info.st_size, info.st_mtime_ns) != (size, mtime):
                raise ValueError("Source file changed during relocation")
            _mkdir(target, dst.parent)
            _inside(target, dst)
            offset = job["offset"]
            if not verify and offset == 0 and not dst.exists():
                dst.touch(exist_ok=False)
            _plain(dst)
            with src.open("rb") as incoming, dst.open("rb" if verify else "r+b") as outgoing:
                incoming.seek(offset)
                outgoing.seek(offset)
                data = incoming.read(min(CHUNK, size-offset))
                if len(data) != min(CHUNK, size-offset):
                    raise ValueError("Source changed while reading a copy chunk")
                if not verify:
                    outgoing.write(data)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                    outgoing.seek(offset)
                if outgoing.read(len(data)) != data:
                    raise ValueError("Copied content failed byte verification")
                chunk_hash = hashlib.sha256(data).hexdigest()
                if verify:
                    if db.execute("SELECT size,sha256 FROM chunks WHERE entry=? AND offset=?", (row_id, offset)).fetchone() != (len(data), chunk_hash):
                        raise ValueError("Copied content no longer matches the recorded SHA256 ledger")
                else:
                    db.execute("INSERT OR REPLACE INTO chunks(entry,offset,size,sha256) VALUES(?,?,?,?)", (row_id, offset, len(data), chunk_hash))
            job["offset"] += len(data)
            job["verified_bytes" if verify else "copied_bytes"] += len(data)
            if job["offset"] == size:
                if dst.stat().st_size != size:
                    raise ValueError("Destination file size mismatch")
                job.update(cursor=row_id, offset=0)

    def advance(self, job_id, max_seconds=2):
        folder = self._path(job_id)
        with mutation_lock(folder / "job.lock", timeout_s=.5):
            job = read(folder / "job.json")
            if not job:
                raise ValueError("Migration job not found")
            if job["status"] in TERMINAL:
                self._gate(job)
                return self._public(job)
            self._gate(job)
            source, target = Path(job["source"]), Path(job["destination"])
            deadline = time.monotonic() + min(max(float(max_seconds), .01), 5)
            with sqlite3.connect(folder / "entries.sqlite") as db:
                db.execute("CREATE TABLE IF NOT EXISTS entries(path TEXT UNIQUE,kind TEXT,size INTEGER,mtime INTEGER)")
                db.execute("CREATE TABLE IF NOT EXISTS progress(id INTEGER PRIMARY KEY,body TEXT)")
                db.execute("CREATE TABLE IF NOT EXISTS chunks(entry INTEGER,offset INTEGER,size INTEGER,sha256 TEXT,PRIMARY KEY(entry,offset))")
                saved = db.execute("SELECT body FROM progress WHERE id=1").fetchone()
                if saved:
                    job = json.loads(saved[0])
            if self.locations.settings().get("last_migration") == job_id:
                job["phase"] = "commit"
            previous_progress = json.loads(json.dumps(job))
            try:
                if target.resolve() != target.absolute():
                    raise ValueError("Destination physical path changed during relocation")
                observed = volume(target)
                if observed["id"] != job["volume_id"] or observed["free_bytes"] < job["min_free_bytes"]:
                    raise ValueError("Destination volume changed, unavailable or below free-space reserve")
                if self.locations.settings()["revision"] != job["expected_revision"] and job["phase"] != "commit":
                    raise ValueError("Storage settings changed; cancel and review a new migration")
                with sqlite3.connect(folder / "entries.sqlite") as db:
                    if job["phase"] == "scan":
                        self._scan(job, db, source, deadline)
                        if job["phase"] == "copy":
                            quota = self.locations.settings()["roles"][job["role"]].get("quota_bytes")
                            if observed["free_bytes"] < job["bytes"] + job["min_free_bytes"] or (quota and job["bytes"] > quota):
                                raise ValueError("Scanned source exceeds destination capacity or quota")
                    elif job["phase"] in {"copy", "verify_copy"}:
                        marker = target / ".adaos-migration-owner.json"
                        if target.exists():
                            if read(marker, {}).get("job_id") != job_id:
                                raise ValueError("Destination is not owned by this migration")
                        else:
                            target.mkdir(parents=True, exist_ok=False)
                            atomic_write_json(marker, {"job_id": job_id, "admission_root": job["source"]})
                        self._copy(job, db, source, target, deadline, verify=job["phase"] == "verify_copy")
                    elif job["phase"] == "verify_source":
                        self._scan(job, db, source, deadline, verify=True)
                    elif job["phase"] == "verify_destination":
                        self._scan(job, db, target, deadline, verify=True, destination=True)
                    elif job["phase"] == "seal":
                        retained = Path(job["retained_source"])
                        if not retained.exists():
                            _plain(source, directory=True)
                            source.rename(retained)
                        if not source.exists():
                            _link(source, target)
                        if source.resolve() != target.resolve():
                            raise ValueError("Compatibility path is occupied; retained originals are safe")
                        job["phase"] = "commit"
                    elif job["phase"] == "commit":
                        self._commit(job)
                    job["error"] = None
                    if job["status"] != "completed":
                        job["status"] = "running"
                    db.execute("INSERT OR REPLACE INTO progress(id,body) VALUES(1,?)", (json.dumps(job),))
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                job = previous_progress
                with sqlite3.connect(folder / "entries.sqlite") as db:
                    saved = db.execute("SELECT body FROM progress WHERE id=1").fetchone()
                    if saved:
                        job = json.loads(saved[0])
                job.update(status="paused", error=str(exc))
            self._save(job)
            return self._public(job)

    def _gate(self, job):
        """Reconcile admission even after a crash between journal and gate writes."""
        gate, lock = gate_paths(Path(job["source"]))
        with mutation_lock(lock, timeout_s=2):
            old = read(gate)
            if old and old["job_id"] != job["job_id"]:
                if job["status"] in TERMINAL:
                    return
                if old["status"] not in TERMINAL:
                    raise ValueError("Another migration owns producer admission")
            atomic_write_json(gate, {"job_id": job["job_id"], "status": job["status"]})

    def _commit(self, job):
        with mutation_lock(self.locations.state / "storage-settings.lock", timeout_s=2):
            settings = self.locations.settings()
            if settings.get("last_migration") != job["job_id"]:
                if settings["revision"] != job["expected_revision"]:
                    raise ValueError("Settings revision changed before cutover; originals are retained")
                settings["roles"][job["role"]].update(active_path=job["destination"], compatibility_path=job["source"],
                    retained_source=job["retained_source"])
                settings.update(revision=settings["revision"]+1, last_migration=job["job_id"], updated_at=now())
                atomic_write_json(self.locations.settings_path, settings)
            job.update(status="completed", revision=settings["revision"], completed_at=now())
            atomic_write_json(gate_paths(Path(job["source"]))[0], {"job_id": job["job_id"], "status": "completed"})

    def cancel(self, job_id):
        folder = self._path(job_id)
        with mutation_lock(folder / "job.lock", timeout_s=2):
            job = read(folder / "job.json")
            if not job or job["status"] == "completed":
                raise ValueError("A completed relocation cannot be cancelled; originals remain retained")
            old = read(gate_paths(Path(job["source"]))[0])
            if old and old["job_id"] != job_id:
                raise ValueError("Another migration owns the source; an old cancellation cannot release it")
            source, retained, target = Path(job["source"]), Path(job["retained_source"]), Path(job["destination"])
            if self.locations.settings().get("last_migration") == job_id:
                raise ValueError("Cutover was committed; resume to reconcile the receipt")
            if retained.exists():
                if os.path.lexists(source):
                    if source.resolve() != target.resolve() or source.absolute() == source.resolve():
                        raise ValueError("Compatibility path changed; originals remain retained")
                    if os.name == "nt":
                        source.rmdir()
                    else:
                        source.unlink()
                retained.rename(source)
            job.update(status="cancelled", error=None, completed_at=now())
            self._save(job)
            atomic_write_json(gate_paths(source)[0], {"job_id": job_id, "status": "cancelled"})
            return self._public(job)
