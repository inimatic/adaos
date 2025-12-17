from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import subprocess


@dataclass(frozen=True, slots=True)
class GitCommitInfo:
    sha: str
    timestamp: int
    subject: str

    @property
    def iso(self) -> str:
        try:
            return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat()
        except Exception:
            return ""


@dataclass(slots=True)
class GitPathStatus:
    path: str
    exists: bool
    dirty: bool
    base_ref: Optional[str] = None
    changed_vs_base: Optional[bool] = None
    base_last_commit: Optional[GitCommitInfo] = None
    local_last_commit: Optional[GitCommitInfo] = None
    error: Optional[str] = None


def _run_git(workdir: Path, args: list[str], *, timeout_s: float = 10.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(workdir), *args],
        text=True,
        capture_output=True,
        timeout=timeout_s,
    )


def _git_ok(proc: subprocess.CompletedProcess[str]) -> bool:
    return proc.returncode == 0


def resolve_base_ref(workdir: Path, *, remote: str = "origin") -> Optional[str]:
    """
    Best-effort resolution of a remote-tracking base ref:
      1) refs/remotes/<remote>/HEAD symbolic-ref (e.g. origin/main)
      2) @{u} if configured
      3) <remote>/main or <remote>/master if present
    """
    proc = _run_git(workdir, ["symbolic-ref", "-q", "--short", f"refs/remotes/{remote}/HEAD"])
    if _git_ok(proc):
        ref = (proc.stdout or "").strip()
        if ref:
            return ref

    proc = _run_git(workdir, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if _git_ok(proc):
        ref = (proc.stdout or "").strip()
        if ref:
            return ref

    for candidate in (f"{remote}/main", f"{remote}/master"):
        proc = _run_git(workdir, ["rev-parse", "--verify", "--quiet", candidate])
        if _git_ok(proc):
            return candidate

    return None


def fetch_remote(workdir: Path, *, remote: str = "origin") -> Optional[str]:
    proc = _run_git(workdir, ["fetch", "--prune", remote], timeout_s=60.0)
    if _git_ok(proc):
        return None
    err = (proc.stderr or proc.stdout or "").strip()
    return err or f"git fetch {remote} failed"


def read_last_commit(workdir: Path, *, rev: str, path: str) -> Optional[GitCommitInfo]:
    fmt = "%H%x1f%ct%x1f%s"
    proc = _run_git(workdir, ["log", "-1", f"--format={fmt}", rev, "--", path])
    if not _git_ok(proc):
        return None
    raw = (proc.stdout or "").strip()
    if not raw:
        return None
    parts = raw.split("\x1f")
    if len(parts) != 3:
        return None
    sha, ts, subject = parts
    try:
        ts_int = int(ts)
    except Exception:
        ts_int = 0
    return GitCommitInfo(sha=sha, timestamp=ts_int, subject=subject)


def compute_path_status(
    *,
    workdir: Path,
    path: Path,
    base_ref: Optional[str],
) -> GitPathStatus:
    try:
        rel = path.relative_to(workdir).as_posix()
    except Exception:
        rel = path.as_posix()
    status = GitPathStatus(path=rel, exists=path.exists(), dirty=False, base_ref=base_ref)

    proc = _run_git(workdir, ["status", "--porcelain", "--", rel])
    if _git_ok(proc):
        status.dirty = bool((proc.stdout or "").strip())
    else:
        status.error = (proc.stderr or proc.stdout or "").strip() or "git status failed"
        return status

    status.local_last_commit = read_last_commit(workdir, rev="HEAD", path=rel)
    if base_ref:
        status.base_last_commit = read_last_commit(workdir, rev=base_ref, path=rel)
        proc = _run_git(workdir, ["diff", "--name-only", base_ref, "--", rel])
        if _git_ok(proc):
            status.changed_vs_base = bool((proc.stdout or "").strip())
        else:
            status.changed_vs_base = None

    return status


def render_diff(workdir: Path, *, base_ref: str, path: str) -> str:
    proc = _run_git(workdir, ["diff", "--no-color", base_ref, "--", path], timeout_s=60.0)
    if _git_ok(proc):
        return proc.stdout or ""
    err = (proc.stderr or proc.stdout or "").strip()
    raise RuntimeError(err or "git diff failed")
