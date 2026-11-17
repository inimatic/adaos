"""Frozen development source, including tests, shared by checkpoint transports."""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import io
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Callable
import zipfile

from adaos.domain.artifact_release import canonical_payload_digest, sha256_digest
from .packages import _assert_no_portable_collisions, _assert_publishable_file, _normalized_member_name
from .source_checkpoints import source_archive, source_tree


class SourceSnapshotError(RuntimeError):
    pass


SOURCE_POLICY = "adaos.development.source.regular-files.v1"
MAX_FILES = 20_000
MAX_BYTES = 50_000_000


def _open_source(path: Path):
    """Hold captured files against concurrent writes/deletion on Windows.

    On other platforms source writers must participate in Core admission; the
    open-handle/stat and second inventory check additionally detect drift.
    """
    if os.name != "nt":
        return os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb")
    import ctypes
    from ctypes import wintypes
    import msvcrt

    create = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(wintypes.HANDLE(handle))
        raise
    return os.fdopen(descriptor, "rb")


def _inventory_paths(root: Path, skip: Callable[[Path], bool]) -> list[Path]:
    result = []
    for parent, directories, names in os.walk(root, followlinks=False):
        base = Path(parent)
        directories[:] = sorted(d for d in directories if not skip((base / d).relative_to(root) / ".source-probe"))
        for name in directories:
            path = base / name
            if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
                raise SourceSnapshotError("Source directories must not contain links")
        for name in sorted(names):
            path = base / name
            if skip(path.relative_to(root)):
                continue
            if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
                raise SourceSnapshotError("Source must contain regular files only")
            result.append(path)
            if len(result) > MAX_FILES:
                raise SourceSnapshotError("Development source exceeds file limit")
    return sorted(result)


@dataclass(frozen=True)
class SourceSnapshot:
    entries: tuple[tuple[str, bytes], ...]

    def __post_init__(self):
        object.__setattr__(self, 'entries', tuple((name, bytes(data)) for name, data in self.entries))
        names = [name for name, _ in self.entries]
        if not names or len(names) > MAX_FILES or sum(len(data) for _, data in self.entries) > MAX_BYTES:
            raise SourceSnapshotError("Empty or oversized development source")
        if len(set(names)) != len(names):
            raise SourceSnapshotError("Duplicate development source paths")
        for name, data in self.entries:
            path = PurePosixPath(name)
            if (path.is_absolute() or "\\" in name or ":" in name
                    or path.as_posix() != name or any(p.casefold() in {"", ".", "..", ".git"} for p in path.parts)):
                raise SourceSnapshotError("Unsafe development source path")
            if _normalized_member_name(name, error_type=SourceSnapshotError) != name:
                raise SourceSnapshotError("Noncanonical development source path")
            _assert_publishable_file(name, data, error_type=SourceSnapshotError)
        _assert_no_portable_collisions(names, error_type=SourceSnapshotError)
        keys = {name.casefold() for name in names}
        if any(parent.as_posix().casefold() in keys for name in names
               for parent in PurePosixPath(name).parents if parent.as_posix() != "."):
            raise SourceSnapshotError("Source path is both a file and a directory")

    @property
    def inventory(self) -> dict:
        return {"schema": "adaos.development.source_inventory.v1", "policy": SOURCE_POLICY,
                "files": [{"path": name, "size": len(data), "digest": sha256_digest(data)}
                          for name, data in sorted(self.entries)]}

    @property
    def digest(self) -> str:
        return canonical_payload_digest(self.inventory)

    def tree(self, expected: str = "0" * 40) -> str:
        return source_tree(dict(self.entries), expected)

    def archive(self) -> bytes:
        return source_archive(dict(self.entries))

    def materialize(self, destination: Path) -> None:
        target = Path(destination)
        if target.exists() and any(target.iterdir()):
            raise SourceSnapshotError("Source staging destination must be empty")
        target.mkdir(parents=True, exist_ok=True)
        for name, data in self.entries:
            path = target.joinpath(*PurePosixPath(name).parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    @classmethod
    def from_archive(cls, data: bytes) -> SourceSnapshot:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > MAX_FILES or sum(e.file_size for e in entries) > MAX_BYTES:
                    raise SourceSnapshotError("Development source archive exceeds limits")
                for entry in entries:
                    if entry.orig_filename != entry.filename or "\\" in entry.orig_filename:
                        raise SourceSnapshotError("Noncanonical development source archive path")
                    mode = entry.external_attr >> 16
                    if entry.is_dir() or (stat.S_IFMT(mode) not in {0, stat.S_IFREG}):
                        raise SourceSnapshotError("Source archive must contain regular files only")
                return cls(tuple(sorted((entry.filename, archive.read(entry)) for entry in entries)))
        except (zipfile.BadZipFile, KeyError, OSError) as exc:
            raise SourceSnapshotError("Invalid development source archive") from exc

    @classmethod
    def capture(cls, root: Path, *, skip: Callable[[Path], bool] = lambda _: False) -> SourceSnapshot:
        root = Path(root).expanduser().resolve()
        if not root.is_dir():
            raise SourceSnapshotError("Development source directory is missing")
        try:
            paths = _inventory_paths(root, skip)
            with ExitStack() as stack:
                handles = [(path, stack.enter_context(_open_source(path))) for path in paths]
                entries = []
                total = 0
                for path, handle in handles:
                    before = os.fstat(handle.fileno())
                    if not stat.S_ISREG(before.st_mode):
                        raise SourceSnapshotError("Development source is not a regular file")
                    data = handle.read(MAX_BYTES - total + 1)
                    total += len(data)
                    after = os.fstat(handle.fileno())
                    if total > MAX_BYTES:
                        raise SourceSnapshotError("Development source exceeds size limit")
                    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                        raise SourceSnapshotError("Development source changed during capture")
                    entries.append((path.relative_to(root).as_posix(), data))
                if _inventory_paths(root, skip) != paths:
                    raise SourceSnapshotError("Development source paths changed during capture")
                # A second byte/identity read also detects non-cooperating
                # writers on platforms without Windows sharing admission.
                for (path, handle), (_, data) in zip(handles, entries):
                    handle.seek(0)
                    current = path.stat()
                    held = os.fstat(handle.fileno())
                    if current.st_ino != held.st_ino or handle.read(len(data) + 1) != data:
                        raise SourceSnapshotError("Development source changed during capture")
                return cls(tuple(entries))
        except OSError as exc:
            raise SourceSnapshotError("Development source is busy or changed during capture") from exc
