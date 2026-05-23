from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import typer

from adaos.services.agent_context import get_ctx
from adaos.services.git.availability import autodetect_git, get_git_availability, set_git_disabled, set_git_enabled

app = typer.Typer(help="Git availability and archive fallback (local capacity projection io:git).")
remote_app = typer.Typer(help="Inspect and rewrite git remotes for the core checkout and submodules.")
app.add_typer(remote_app, name="remote")

DEFAULT_CORE_REPO_URL = "https://github.com/inimatic/adaos.git"
DEFAULT_CORE_REV = "rev2026"
REQUIRED_SUBMODULES = {"src/adaos/integrations/rasa-port": "pyproject.toml"}
_GITHUB_HTTPS_RE = re.compile(r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/#?]+?)(?:\.git)?/?$")
_GITHUB_SSH_RE = re.compile(r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/#?]+?)(?:\.git)?$")
_GITHUB_SSH_URL_RE = re.compile(r"^ssh://git@github\.com/(?P<owner>[^/]+)/(?P<repo>[^/#?]+?)(?:\.git)?/?$")


class GitCommandError(RuntimeError):
    pass


def _run_git(repo: Path, args: list[str], *, check: bool = True, timeout_s: float = 60.0) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or f"git {' '.join(args)} failed"
        raise GitCommandError(detail)
    return proc


def _run_external(args: list[str], *, cwd: Path | None = None, timeout_s: float = 10.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )


def _is_git_repo(path: Path) -> bool:
    if not path.exists():
        return False
    proc = _run_git(path, ["rev-parse", "--is-inside-work-tree"], check=False, timeout_s=10.0)
    return proc.returncode == 0 and proc.stdout.strip().lower() == "true"


def _resolve_repo_path(path: Path) -> Path:
    path = path.expanduser().resolve()
    if _is_git_repo(path):
        proc = _run_git(path, ["rev-parse", "--show-toplevel"], check=False, timeout_s=10.0)
        if proc.returncode == 0 and proc.stdout.strip():
            return Path(proc.stdout.strip()).expanduser().resolve()
    return path


def _looks_like_adaos_tree(path: Path) -> bool:
    return (path / "tools" / "bootstrap.ps1").exists() and (path / "pyproject.toml").exists()


def _has_items(path: Path) -> bool:
    return path.exists() and any(path.iterdir())


def _origin_url(repo: Path) -> str:
    proc = _run_git(repo, ["remote", "get-url", "origin"], check=False, timeout_s=10.0)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _ensure_origin(repo: Path, url: str) -> str:
    current = _origin_url(repo)
    if not current:
        _run_git(repo, ["remote", "add", "origin", url])
        return "added"
    if current != url:
        _run_git(repo, ["remote", "set-url", "origin", url])
        return "updated"
    return "unchanged"


def _current_branch(repo: Path) -> str:
    proc = _run_git(repo, ["branch", "--show-current"], check=False, timeout_s=10.0)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _upstream_ref(repo: Path) -> str:
    proc = _run_git(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], check=False, timeout_s=10.0)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _head_sha(repo: Path) -> str:
    proc = _run_git(repo, ["rev-parse", "--short", "HEAD"], check=False, timeout_s=10.0)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _dirty(repo: Path) -> bool:
    proc = _run_git(repo, ["status", "--porcelain"], check=False, timeout_s=20.0)
    return bool(proc.stdout.strip()) if proc.returncode == 0 else False


def _github_remote_parts(url: str) -> tuple[str, str] | None:
    text = str(url or "").strip()
    for pattern in (_GITHUB_HTTPS_RE, _GITHUB_SSH_RE, _GITHUB_SSH_URL_RE):
        match = pattern.match(text)
        if match:
            repo = match.group("repo").removesuffix(".git").rstrip("/")
            return match.group("owner"), repo
    return None


def _remote_scheme(url: str) -> str:
    if not url:
        return "missing"
    if _GITHUB_SSH_RE.match(url) or _GITHUB_SSH_URL_RE.match(url):
        return "ssh"
    if _GITHUB_HTTPS_RE.match(url):
        return "https"
    return "other"


def _convert_github_url(url: str, scheme: str) -> str:
    parts = _github_remote_parts(url)
    if not parts:
        return url
    owner, repo = parts
    if scheme == "ssh":
        return f"git@github.com:{owner}/{repo}.git"
    if scheme == "https":
        return f"https://github.com/{owner}/{repo}.git"
    raise ValueError(f"unsupported scheme: {scheme}")


def _submodule_entries(repo: Path) -> list[dict[str, str]]:
    modules = repo / ".gitmodules"
    if not modules.exists():
        return []
    proc = _run_git(
        repo,
        ["config", "--file", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$"],
        check=False,
        timeout_s=10.0,
    )
    entries: list[dict[str, str]] = []
    if proc.returncode != 0:
        return entries
    for line in proc.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        key, path_value = parts[0], parts[1].strip()
        match = re.match(r"^submodule\.(?P<name>.+)\.path$", key)
        if not match or not path_value:
            continue
        entries.append({"name": match.group("name"), "path": path_value})
    return entries


def _submodule_url(repo: Path, name: str) -> str:
    proc = _run_git(repo, ["config", "--get", f"submodule.{name}.url"], check=False, timeout_s=10.0)
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    proc = _run_git(repo, ["config", "--file", ".gitmodules", "--get", f"submodule.{name}.url"], check=False, timeout_s=10.0)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _repo_entries(repo: Path, *, recursive: bool) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = [{"kind": "core", "name": "core", "path": repo, "initialized": _is_git_repo(repo)}]
    if not recursive or not _is_git_repo(repo):
        return entries
    for sub in _submodule_entries(repo):
        sub_path = repo / sub["path"]
        entries.append(
            {
                "kind": "submodule",
                "name": sub["name"],
                "path": sub_path,
                "relative_path": sub["path"],
                "initialized": _is_git_repo(sub_path),
                "configured_url": _submodule_url(repo, sub["name"]),
            }
        )
    return entries


def _entry_status(entry: dict[str, Any]) -> dict[str, Any]:
    path = Path(entry["path"])
    payload: dict[str, Any] = {
        "kind": entry["kind"],
        "name": entry["name"],
        "path": str(path),
        "relative_path": entry.get("relative_path") or ".",
        "initialized": bool(entry.get("initialized")),
    }
    if entry.get("configured_url"):
        payload["configured_url"] = entry["configured_url"]
        payload["configured_scheme"] = _remote_scheme(entry["configured_url"])
    if not entry.get("initialized"):
        payload["error"] = "not a git worktree"
        return payload
    url = _origin_url(path)
    payload.update(
        {
            "origin": url,
            "scheme": _remote_scheme(url),
            "branch": _current_branch(path),
            "upstream": _upstream_ref(path),
            "head": _head_sha(path),
            "dirty": _dirty(path),
        }
    )
    return payload


def _ssh_github_diagnostic(repo: Path) -> dict[str, Any]:
    if not shutil.which("ssh"):
        return {"available": False, "ok": False, "message": "ssh not found"}
    proc = _run_external(["ssh", "-o", "BatchMode=yes", "-T", "git@github.com"], cwd=repo, timeout_s=12.0)
    output = (proc.stdout + proc.stderr).strip()
    return {
        "available": True,
        "ok": "successfully authenticated" in output,
        "returncode": proc.returncode,
        "message": output,
    }


def _print_remote_rows(payload: dict[str, Any]) -> None:
    for item in payload["repositories"]:
        marker = "ok" if item.get("initialized") else "missing"
        typer.echo(
            f"{item['relative_path']}: {marker} "
            f"scheme={item.get('scheme') or item.get('configured_scheme') or '-'} "
            f"branch={item.get('branch') or '-'} upstream={item.get('upstream') or '-'}"
        )
        if item.get("origin"):
            typer.echo(f"  origin: {item['origin']}")
        elif item.get("configured_url"):
            typer.echo(f"  configured: {item['configured_url']}")
        if item.get("dirty"):
            typer.secho("  dirty: yes", fg=typer.colors.YELLOW)
        if item.get("error"):
            typer.secho(f"  {item['error']}", fg=typer.colors.YELLOW)
    if payload.get("ssh"):
        ssh = payload["ssh"]
        color = typer.colors.GREEN if ssh.get("ok") else typer.colors.YELLOW
        typer.secho(f"ssh github: {ssh.get('message') or 'unavailable'}", fg=color)


def _rewrite_remotes(repo: Path, *, scheme: str, recursive: bool, dry_run: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {"repo": str(repo), "scheme": scheme, "dry_run": dry_run, "changes": []}
    for entry in _repo_entries(repo, recursive=recursive):
        if not entry.get("initialized"):
            continue
        path = Path(entry["path"])
        old = _origin_url(path)
        new = _convert_github_url(old, scheme)
        changed = bool(old and new != old)
        payload["changes"].append(
            {
                "path": str(path),
                "relative_path": entry.get("relative_path") or ".",
                "old": old,
                "new": new,
                "changed": changed,
            }
        )
        if changed and not dry_run:
            _run_git(path, ["remote", "set-url", "origin", new])
    if recursive and _is_git_repo(repo):
        for sub in _submodule_entries(repo):
            old = _submodule_url(repo, sub["name"])
            new = _convert_github_url(old, scheme)
            changed = bool(old and new != old)
            payload["changes"].append(
                {
                    "path": str(repo),
                    "relative_path": f".git/config:submodule.{sub['name']}.url",
                    "old": old,
                    "new": new,
                    "changed": changed,
                }
            )
            if changed and not dry_run:
                _run_git(repo, ["config", f"submodule.{sub['name']}.url", new])
    return payload


def _ensure_required_submodules(repo: Path) -> list[str]:
    messages: list[str] = []
    if not _is_git_repo(repo):
        return messages
    for sub_path, marker in REQUIRED_SUBMODULES.items():
        marker_path = repo / sub_path / marker
        if marker_path.exists():
            messages.append(f"{sub_path}: present")
            continue
        _run_git(repo, ["submodule", "sync", "--", sub_path], check=False, timeout_s=30.0)
        _run_git(repo, ["submodule", "update", "--init", "--recursive", sub_path], timeout_s=180.0)
        if marker_path.exists():
            messages.append(f"{sub_path}: initialized")
            continue
        if (repo / sub_path / ".git").exists():
            _run_git(repo / sub_path, ["restore", "--source=HEAD", "--worktree", "."], check=False, timeout_s=60.0)
            _run_git(repo / sub_path, ["restore", "--source=HEAD", "--staged", "."], check=False, timeout_s=60.0)
        messages.append(f"{sub_path}: {'present' if marker_path.exists() else 'missing'}")
    return messages


def _echo(av, *, json_output: bool) -> None:
    base_dir = get_ctx().paths.base_dir()
    base_dir = Path(base_dir() if callable(base_dir) else base_dir).expanduser().resolve()
    payload = {
        "enabled": bool(av.enabled),
        "git_path": av.git_path,
        "mode": av.mode,
        "reason": av.reason,
        "source": av.source,
        "base_dir": str(base_dir),
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    typer.echo(f"enabled: {payload['enabled']}")
    if payload.get("git_path"):
        typer.echo(f"git_path: {payload['git_path']}")
    if payload.get("mode"):
        typer.echo(f"mode: {payload['mode']}")
    if payload.get("reason"):
        typer.echo(f"reason: {payload['reason']}")
    if payload.get("source"):
        typer.echo(f"source: {payload['source']}")
    typer.echo(f"base_dir: {payload['base_dir']}")


@app.command("status")
def status(json_output: bool = typer.Option(False, "--json", help="JSON output")) -> None:
    ctx = get_ctx()
    av = get_git_availability(base_dir=ctx.settings.base_dir)
    _echo(av, json_output=json_output)


@app.command("autodetect")
def autodetect(json_output: bool = typer.Option(False, "--json", help="JSON output")) -> None:
    ctx = get_ctx()
    av = autodetect_git(base_dir=ctx.settings.base_dir)
    _echo(av, json_output=json_output)


@app.command("enable")
def enable(json_output: bool = typer.Option(False, "--json", help="JSON output")) -> None:
    ctx = get_ctx()
    av = set_git_enabled(base_dir=ctx.settings.base_dir)
    _echo(av, json_output=json_output)
    if not av.enabled:
        raise typer.Exit(1)


@app.command("disable")
def disable(
    reason: str = typer.Option("disabled by operator", "--reason", help="Reason stored in local capacity projection"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
) -> None:
    ctx = get_ctx()
    av = set_git_disabled(base_dir=ctx.settings.base_dir, reason=reason)
    _echo(av, json_output=json_output)


@remote_app.command("status")
def remote_status(
    repo: Path = typer.Option(Path("."), "--repo", help="Core checkout path."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Include submodules listed in .gitmodules."),
    check_ssh: bool = typer.Option(False, "--check-ssh", help="Probe GitHub SSH auth with ssh -T git@github.com."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
) -> None:
    root = _resolve_repo_path(repo)
    payload: dict[str, Any] = {
        "repo": str(root),
        "repositories": [_entry_status(entry) for entry in _repo_entries(root, recursive=recursive)],
    }
    if check_ssh:
        payload["ssh"] = _ssh_github_diagnostic(root)
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    _print_remote_rows(payload)


@remote_app.command("use-ssh")
def remote_use_ssh(
    repo: Path = typer.Option(Path("."), "--repo", help="Core checkout path."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Rewrite initialized submodules too."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show changes without writing git config."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
) -> None:
    root = _resolve_repo_path(repo)
    payload = _rewrite_remotes(root, scheme="ssh", recursive=recursive, dry_run=dry_run)
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for change in payload["changes"]:
        if change["changed"]:
            action = "would set" if dry_run else "set"
            typer.echo(f"{change['relative_path']}: {action} {change['new']}")
        else:
            typer.echo(f"{change['relative_path']}: unchanged")


@remote_app.command("use-https")
def remote_use_https(
    repo: Path = typer.Option(Path("."), "--repo", help="Core checkout path."),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Rewrite initialized submodules too."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show changes without writing git config."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
) -> None:
    root = _resolve_repo_path(repo)
    payload = _rewrite_remotes(root, scheme="https", recursive=recursive, dry_run=dry_run)
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for change in payload["changes"]:
        if change["changed"]:
            action = "would set" if dry_run else "set"
            typer.echo(f"{change['relative_path']}: {action} {change['new']}")
        else:
            typer.echo(f"{change['relative_path']}: unchanged")


@app.command("repair-core")
def repair_core(
    repo: Path = typer.Option(Path("."), "--repo", help="AdaOS checkout path."),
    rev: str = typer.Option(DEFAULT_CORE_REV, "--rev", help="Core branch/ref to track."),
    url: str = typer.Option("", "--url", help="Core repository URL. Defaults to current origin or upstream HTTPS."),
    hard: bool = typer.Option(False, "--hard", help="Reset tracked files to origin/<rev>."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
) -> None:
    if not shutil.which("git"):
        typer.secho("git not found", fg=typer.colors.RED)
        raise typer.Exit(1)

    root = repo.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    effective_url = url.strip() or (_origin_url(root) if _is_git_repo(root) else "") or DEFAULT_CORE_REPO_URL
    actions: list[str] = []
    if not _is_git_repo(root):
        if _has_items(root) and not _looks_like_adaos_tree(root):
            typer.secho(f"refusing to adopt non-AdaOS non-empty directory: {root}", fg=typer.colors.RED)
            raise typer.Exit(1)
        if _has_items(root):
            _run_git(root, ["init"])
            actions.append("initialized git repository")
            _ensure_origin(root, effective_url)
            _run_git(root, ["fetch", "origin", rev], timeout_s=180.0)
            _run_git(root, ["symbolic-ref", "HEAD", f"refs/heads/{rev}"])
            _run_git(root, ["reset", "--mixed", f"origin/{rev}"])
            actions.append(f"adopted existing tree at origin/{rev}")
        else:
            clone = _run_external(["git", "clone", "-b", rev, effective_url, str(root)], timeout_s=300.0)
            if clone.returncode != 0:
                typer.secho((clone.stderr or clone.stdout or "git clone failed").strip(), fg=typer.colors.RED)
                raise typer.Exit(1)
            actions.append(f"cloned {effective_url} ({rev})")
    else:
        _ensure_origin(root, effective_url)
        _run_git(root, ["fetch", "origin", rev], timeout_s=180.0)
        if _run_git(root, ["show-ref", "--verify", "--quiet", f"refs/heads/{rev}"], check=False).returncode == 0:
            _run_git(root, ["checkout", rev])
        else:
            _run_git(root, ["checkout", "-B", rev, f"origin/{rev}"])
        pull = _run_git(root, ["pull", "--ff-only", "origin", rev], check=False, timeout_s=180.0)
        if pull.returncode != 0 and not hard:
            typer.secho((pull.stderr or pull.stdout or "git pull failed").strip(), fg=typer.colors.RED)
            raise typer.Exit(1)
        actions.append(f"updated checkout from origin/{rev}")

    if hard:
        _run_git(root, ["reset", "--hard", f"origin/{rev}"], timeout_s=120.0)
        actions.append(f"hard reset to origin/{rev}")
    _run_git(root, ["branch", "--set-upstream-to", f"origin/{rev}", rev], check=False, timeout_s=20.0)
    actions.append(f"tracking origin/{rev}")
    actions.extend(_ensure_required_submodules(root))
    payload = {"repo": str(root), "rev": rev, "origin": _origin_url(root), "actions": actions}
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for action in actions:
        typer.echo(action)

