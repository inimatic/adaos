"""Retire verified duplicates; retain changed run directories for manual recovery.

This is separate from cutover: completed relocation never implicitly authorizes
deletion. The owner reviews a durable classification before applying its digest.
Windows deletion uses the same handle that excludes writers during verification.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import stat
import time

from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from .storage import digest, now, read, volume


@lru_cache(maxsize=1)
def _windows_files():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    final = kernel.GetFinalPathNameByHandleW
    final.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    final.restype = wintypes.DWORD
    attributes = kernel.GetFileInformationByHandleEx
    attributes.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    attributes.restype = wintypes.BOOL
    dispose = kernel.SetFileInformationByHandle
    dispose.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    dispose.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    return create, final, attributes, dispose, close


@contextmanager
def locked_file(path, *, delete=False):
    if os.name != "nt":
        raise ValueError("Verified source retirement currently requires Windows file sharing locks")
    import ctypes
    from ctypes import wintypes
    import msvcrt
    create, final, attributes, dispose, close = _windows_files()
    # Share readers only: existing or new writers/deleters make this fail closed.
    handle = create(str(path), 0x80000000 | (0x10000 if delete else 0), 1, None, 3, 0x00200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise
    with os.fdopen(fd, "rb") as stream:
        # Resolve the opened object once, rather than repeatedly resolving paths
        # before opening. Both intermediate aliases and a reparse leaf fail.
        buffer = ctypes.create_unicode_buffer(32768)
        length = final(handle, buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            raise ctypes.WinError(ctypes.get_last_error())
        physical = buffer.value.removeprefix("\\\\?\\")
        tags = (wintypes.DWORD * 2)()
        if not attributes(handle, 9, ctypes.byref(tags), ctypes.sizeof(tags)):
            raise ctypes.WinError(ctypes.get_last_error())
        if Path(physical) != path.absolute() or tags[0] & 0x400 or not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Opened retirement file is linked or outside its reviewed physical path")
        def remove():
            flag = wintypes.BOOL(True)
            if not dispose(handle, 4, ctypes.byref(flag), ctypes.sizeof(flag)):
                raise ctypes.WinError(ctypes.get_last_error())
        yield stream, remove


class StorageRetirement:
    def __init__(self, migrations):
        self.migrations = migrations
        self.locations = migrations.locations

    def folder(self, job_id):
        return self.migrations._path(job_id) / "retirement"

    def snapshot(self, job_id):
        return read(self.folder(job_id) / "job.json", {})

    def _save(self, record):
        record["updated_at"] = now()
        atomic_write_json(self.folder(record["job_id"]) / "job.json", record)

    def _validate(self, record):
        from .migration import _plain
        job = read(self.migrations._path(record["job_id"]) / "job.json")
        settings = self.locations.settings()
        placement = settings["roles"][job["role"]]
        source, target, alias = Path(job["retained_source"]), Path(job["destination"]), Path(job["source"])
        expected = alias.with_name(alias.name + ".retained-" + job["job_id"][:16])
        if (job["status"] != "completed" or source != expected or source.absolute() != source.resolve()
                or source == target or source.is_relative_to(target) or target.is_relative_to(source)
                or target.absolute() != target.resolve()):
            raise ValueError("Retirement requires the exact physical retained root of a completed migration")
        _plain(source, directory=True)
        _plain(target, directory=True)
        if (settings["revision"] != record["expected_revision"]
                or placement.get("active_path") != str(target)
                or placement.get("retained_source") != str(source)
                or placement.get("compatibility_path") != str(alias)
                or alias.absolute() == alias.resolve() or alias.resolve() != target):
            raise ValueError("Placement or compatibility path changed; no deletion is admitted")
        if (volume(source)["id"] != job["source_volume_id"] or volume(target)["id"] != job["volume_id"]
                or read(target / ".adaos-migration-owner.json", {}).get("job_id") != job["job_id"]
                or (source.stat().st_dev, source.stat().st_ino) != tuple(record["source_identity"])
                or (target.stat().st_dev, target.stat().st_ino) != tuple(record["target_identity"])):
            raise ValueError("Retirement storage identity changed")
        ledger_info = (self.migrations._path(record["job_id"]) / "entries.sqlite").stat()
        if record.get("ledger_identity") != [ledger_info.st_size, ledger_info.st_mtime_ns]:
            raise ValueError("Completed migration ledger changed")
        return job, source, target

    def review(self, job_id):
        from .migration import _plain
        folder = self.folder(job_id)
        with mutation_lock(folder / "job.lock", timeout_s=2):
            previous = self.snapshot(job_id)
            if previous:
                return previous
            job = read(self.migrations._path(job_id) / "job.json", {})
            if job.get("status") != "completed":
                raise ValueError("Complete migration before reviewing original retirement")
            source, target = Path(job["retained_source"]), Path(job["destination"])
            _plain(source, directory=True)
            _plain(target, directory=True)
            ledger_path = self.migrations._path(job_id) / "entries.sqlite"
            checksum = hashlib.sha256()
            with sqlite3.connect(ledger_path.as_uri() + "?mode=ro", uri=True) as ledger:
                for chunk in ledger.execute("SELECT entry,offset,size,sha256 FROM chunks ORDER BY entry,offset"):
                    checksum.update(json.dumps(list(chunk), separators=(",", ":")).encode())
                    checksum.update(b"\n")
            if (job.get("content_digest") != "sha256:" + checksum.hexdigest()
                    or job.get("verified_bytes") != job.get("bytes") or job.get("copied_bytes") != job.get("bytes")):
                raise ValueError("Migration verification receipt does not match the chunk ledger")
            ledger_info = ledger_path.stat()
            record = {"schema": "adaos.node.retirement.v1", "job_id": job_id,
                      "expected_revision": self.locations.settings()["revision"],
                      "source_identity": [source.stat().st_dev, source.stat().st_ino],
                      "target_identity": [target.stat().st_dev, target.stat().st_ino],
                      "ledger_identity": [ledger_info.st_size, ledger_info.st_mtime_ns],
                      "started_at": now(), "migration_started_at": job["created_at"],
                      "status": "running", "phase": "scan", "cursor": 0,
                      "pending": [{"path": ".", "offset": 0}], "scanned_files": 0,
                      "deleted_files": 0, "deleted_bytes": 0, "preserved_files": 0,
                      "preserved_bytes": 0, "error": None,
                      "effect": "delete_verified_duplicates_preserve_changed_directories"}
            self._validate(record)
            with sqlite3.connect(folder / "entries.sqlite") as db:
                db.execute("CREATE TABLE entries(path TEXT UNIQUE,kind TEXT,size INTEGER,mtime INTEGER,identity TEXT,state TEXT)")
                db.execute("CREATE INDEX directories ON entries(kind,state,length(path) DESC)")
                db.execute("CREATE TABLE preserved(path TEXT PRIMARY KEY,reason TEXT)")
                db.execute("CREATE TABLE progress(id INTEGER PRIMARY KEY,body TEXT)")
                db.execute("INSERT INTO progress VALUES(1,?)", (json.dumps(record),))
            self._save(record)
            return record

    def pause(self, job_id, error):
        folder = self.folder(job_id)
        with mutation_lock(folder / "job.lock", timeout_s=2):
            record = self.snapshot(job_id)
            if record.get("status") == "running":
                with sqlite3.connect(folder / "entries.sqlite") as db:
                    record = json.loads(db.execute("SELECT body FROM progress WHERE id=1").fetchone()[0])
                    if record["status"] == "running":
                        record.update(status="paused", error=str(error)[:1000])
                        db.execute("UPDATE progress SET body=? WHERE id=1", (json.dumps(record),))
                self._save(record)

    def apply(self, job_id, plan_digest):
        folder = self.folder(job_id)
        with mutation_lock(folder / "job.lock", timeout_s=2):
            record = self.snapshot(job_id)
            if not plan_digest or record.get("plan_digest") != plan_digest:
                raise ValueError("The exact reviewed retirement digest is required")
            if record["status"] == "completed" or record["phase"] == "delete":
                return record
            if record["status"] != "ready":
                raise ValueError("Finish reviewing retained originals before deleting duplicates")
            self._validate(record)
            record.update(status="running", phase="delete", cursor=0, applied_at=now(), error=None)
            with sqlite3.connect(folder / "entries.sqlite") as db:
                db.execute("UPDATE progress SET body=? WHERE id=1", (json.dumps(record),))
            self._save(record)
            return record

    def resume(self, job_id):
        folder = self.folder(job_id)
        with mutation_lock(folder / "job.lock", timeout_s=2):
            record = self.snapshot(job_id)
            if record.get("status") == "paused":
                self._validate(record)
                record.update(status="running", error=None)
                with sqlite3.connect(folder / "entries.sqlite") as db:
                    db.execute("UPDATE progress SET body=? WHERE id=1", (json.dumps(record),))
                self._save(record)
            return record

    @staticmethod
    def _relative(root, relative, *, check_parents=True):
        from .migration import _inside
        value = PurePosixPath(relative)
        if value.is_absolute() or ".." in value.parts or "\\" in relative or ":" in relative:
            raise ValueError("Retirement entry escapes its physical root")
        path = root / relative
        if check_parents:
            _inside(root, path)
        return path

    @staticmethod
    def _identity(info):
        return json.dumps([info.st_dev, info.st_ino, info.st_ctime_ns])

    def _preserve(self, db, ledger, relative, reason):
        # Prefer a registered run; legacy evidence falls back to its containing
        # folder. Keep the entire selected subtree, including unchanged siblings.
        path = PurePosixPath(relative)
        group = path.parent
        for parent in path.parents:
            marker = (parent / ".adaos-storage.json").as_posix()
            if ledger.execute("SELECT 1 FROM entries WHERE path=?", (marker,)).fetchone():
                group = parent
                break
        db.execute("INSERT OR IGNORE INTO preserved VALUES(?,?)", (group.as_posix(), reason))

    @staticmethod
    def _protected(db, relative):
        path = PurePosixPath(relative)
        return any(db.execute("SELECT 1 FROM preserved WHERE path=?", (p.as_posix(),)).fetchone()
                   for p in (path, *path.parents))

    def _scan(self, record, db, ledger, source, deadline):
        from .migration import _plain
        started = int(datetime.fromisoformat(record["migration_started_at"]).timestamp() * 1_000_000_000)
        count = 0
        while record["pending"] and time.monotonic() < deadline and count < 2000:
            frame = record["pending"].pop()
            directory = source if frame["path"] == "." else self._relative(source, frame["path"])
            _plain(directory, directory=True)
            with os.scandir(directory) as entries:
                for index, entry in enumerate(entries):
                    if index < frame["offset"]:
                        continue
                    if time.monotonic() >= deadline or count >= 2000:
                        record["pending"].append({"path": frame["path"], "offset": index})
                        break
                    count += 1
                    path = Path(entry.path)
                    relative = path.relative_to(source).as_posix()
                    directory_entry = entry.is_dir(follow_symlinks=False)
                    info = _plain(path, directory=directory_entry, info=entry.stat(follow_symlinks=False))
                    kind = "dir" if directory_entry else "file"
                    db.execute("INSERT INTO entries VALUES(?,?,?,?,?,?)", (relative, kind, info.st_size,
                               info.st_mtime_ns, self._identity(info), "pending"))
                    if directory_entry:
                        record["pending"].insert(0, {"path": relative, "offset": 0})
                        if not ledger.execute("SELECT 1 FROM entries WHERE path=? AND kind='dir'", (relative,)).fetchone():
                            db.execute("INSERT OR IGNORE INTO preserved VALUES(?,?)", (relative, "directory_added_since_migration"))
                    else:
                        record["scanned_files"] += 1
                        old = ledger.execute("SELECT kind,size,mtime FROM entries WHERE path=?", (relative,)).fetchone()
                        if (info.st_mtime_ns >= started or getattr(info, "st_birthtime_ns", 0) >= started
                                or old != ("file", info.st_size, info.st_mtime_ns)):
                            self._preserve(db, ledger, relative, "created_or_changed_since_migration_start")
                        if entry.name == ".adaos-lease.json":
                            self._preserve(db, ledger, relative, "producer_lease")
        if not record["pending"]:
            record.update(phase="verify", cursor=0)

    def _verify(self, ledger, relative, source, target, *, remove=False):
        src = self._relative(source, relative, check_parents=False)
        dst = self._relative(target, relative, check_parents=False)
        old = ledger.execute("SELECT rowid,size,mtime FROM entries WHERE path=? AND kind='file'", (relative,)).fetchone()
        if old is None:
            raise ValueError("File is absent from the migration ledger")
        with locked_file(src, delete=remove) as (incoming, delete), locked_file(dst) as (outgoing, _):
            src_info, dst_info = os.fstat(incoming.fileno()), os.fstat(outgoing.fileno())
            if (src_info.st_size, src_info.st_mtime_ns) != old[1:] or dst_info.st_size != old[1]:
                raise ValueError("File metadata differs from verified migration")
            offset = 0
            for start, size, checksum in ledger.execute("SELECT offset,size,sha256 FROM chunks WHERE entry=? ORDER BY offset", (old[0],)):
                if start != offset:
                    raise ValueError("Migration chunk ledger has a gap")
                data = incoming.read(size)
                if (len(data) != size or hashlib.sha256(data).hexdigest() != checksum or outgoing.read(size) != data):
                    raise ValueError("File content differs from verified migration")
                offset += size
            if offset != old[1] or incoming.read(1) or outgoing.read(1):
                raise ValueError("Migration chunk ledger does not cover the complete file")
            if remove:
                delete()

    def advance(self, job_id, max_seconds=1):
        folder = self.folder(job_id)
        with mutation_lock(folder / "job.lock", timeout_s=.5):
            projection = self.snapshot(job_id)
            if projection.get("status") != "running":
                return projection
            with sqlite3.connect(folder / "entries.sqlite") as db:
                record = json.loads(db.execute("SELECT body FROM progress WHERE id=1").fetchone()[0])
                try:
                    _, source, target = self._validate(record)
                    deadline = time.monotonic() + min(max(float(max_seconds), .01), 5)
                    with sqlite3.connect((self.migrations._path(job_id) / "entries.sqlite").as_uri() + "?mode=ro", uri=True) as ledger:
                        if record["phase"] == "scan":
                            self._scan(record, db, ledger, source, deadline)
                        elif record["phase"] in {"verify", "delete"}:
                            self._files(record, db, ledger, source, target, deadline)
                        elif record["phase"] == "directories":
                            self._directories(record, db, source, deadline)
                        elif record["phase"] == "audit":
                            self._audit(record, db, ledger, source, deadline)
                except (OSError, ValueError) as exc:
                    record.update(status="paused", error=str(exc)[:1000])
                db.execute("UPDATE progress SET body=? WHERE id=1", (json.dumps(record),))
            self._save(record)
            return record

    def _files(self, record, db, ledger, source, target, deadline):
        deleting = record["phase"] == "delete"
        while time.monotonic() < deadline:
            row = self._next_file(db, record["cursor"])
            if row is None:
                if deleting:
                    record.update(phase="directories", cursor=0)
                else:
                    summary = hashlib.sha256()
                    for item in db.execute("SELECT path,kind,size,mtime,identity,state FROM entries ORDER BY path"):
                        summary.update(json.dumps(list(item), separators=(",", ":")).encode())
                    groups = list(db.execute("SELECT path,reason FROM preserved ORDER BY path"))
                    record.update(status="ready", phase="reviewed", plan_digest=digest({
                        "job_id": record["job_id"], "revision": record["expected_revision"],
                        "source": record["source_identity"], "target": record["target_identity"],
                        "entries": summary.hexdigest(), "preserved": groups}))
                self._summary(record, db)
                return
            row_id, relative, size = row
            if not self._protected(db, relative):
                try:
                    self._verify(ledger, relative, source, target, remove=deleting)
                    state = "deleted" if deleting else "verified"
                except (OSError, ValueError) as exc:
                    # An already unlinked file after a crash has no user bytes
                    # left to endanger. Keep its uncertainty visible in receipts.
                    if deleting and not os.path.lexists(source / relative):
                        state = "absent"
                    else:
                        self._preserve(db, ledger, relative, str(exc)[:300])
                        state = "preserved"
                db.execute("UPDATE entries SET state=? WHERE rowid=?", (state, row_id))
                if state == "deleted":
                    record["deleted_files"] += 1
                    record["deleted_bytes"] += size
            record["cursor"] = row_id

    @staticmethod
    def _next_file(db, cursor):
        # The directory-cleanup index starts with kind. Without this constraint
        # SQLite can scan/sort every file for EACH cursor step (quadratic work).
        # NOT INDEXED still admits the intrinsic rowid range scan.
        return db.execute("SELECT rowid,path,size FROM entries NOT INDEXED "
                          "WHERE rowid>? AND kind='file' ORDER BY rowid LIMIT 1", (cursor,)).fetchone()

    def _summary(self, record, db):
        groups = list(db.execute("SELECT path,reason FROM preserved ORDER BY path"))
        record["preserved_directories"] = [{"path": path, "reason": reason} for path, reason in groups]
        count, size = 0, 0
        for path, amount, state in db.execute("SELECT path,size,state FROM entries WHERE kind='file'"):
            if state != "deleted" and self._protected(db, path):
                count += 1
                size += amount
        record.update(preserved_files=count, preserved_bytes=size)

    def _directories(self, record, db, source, deadline):
        from .migration import _plain
        while time.monotonic() < deadline:
            row = db.execute("SELECT rowid,path FROM entries WHERE kind='dir' AND state='pending' ORDER BY length(path) DESC LIMIT 1").fetchone()
            if row is None:
                self._summary(record, db)
                record.update(phase="audit", pending=[{"path": ".", "offset": 0}],
                              preserved_files=0, preserved_bytes=0)
                return
            row_id, relative = row
            path = self._relative(source, relative)
            if not self._protected(db, relative) and path.exists():
                _plain(path, directory=True)
                try:
                    path.rmdir()  # Never recursively delete unreviewed children.
                except OSError:
                    pass  # Includes new late entries: left in place for recovery.
            db.execute("UPDATE entries SET state='retained_or_empty' WHERE rowid=?", (row_id,))

    def _audit(self, record, db, ledger, source, deadline):
        from .migration import _plain
        while record["pending"] and time.monotonic() < deadline:
            frame = record["pending"].pop()
            path = source if frame["path"] == "." else self._relative(source, frame["path"])
            _plain(path, directory=True)
            with os.scandir(path) as entries:
                for index, entry in enumerate(entries):
                    if index < frame["offset"]:
                        continue
                    if time.monotonic() >= deadline:
                        record["pending"].append({"path": frame["path"], "offset": index})
                        break
                    relative = Path(entry.path).relative_to(source).as_posix()
                    directory = entry.is_dir(follow_symlinks=False)
                    info = _plain(Path(entry.path), directory=directory, info=entry.stat(follow_symlinks=False))
                    if directory:
                        record["pending"].insert(0, {"path": relative, "offset": 0})
                    else:
                        record["preserved_files"] += 1
                        record["preserved_bytes"] += info.st_size
                        if not self._protected(db, relative):
                            self._preserve(db, ledger, relative, "remaining_or_late_entry")
        if not record["pending"]:
            record["preserved_directories"] = [dict(path=p, reason=r) for p, r in db.execute("SELECT path,reason FROM preserved ORDER BY path")]
            record.update(status="completed", phase="completed", completed_at=now(),
                          source_space_reclaimed=record["deleted_bytes"] > 0,
                          retains_changed_originals=record["preserved_files"] > 0)
