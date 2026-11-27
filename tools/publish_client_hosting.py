#!/usr/bin/env python3
"""Qualify and optionally publish a clean source-list Client from this workstation."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CLIENT = ROOT / "src/adaos/integrations/adaos-client"


def capture(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(command, cwd=cwd, env=env, text=True,
                            encoding="utf-8", errors="replace", capture_output=True, timeout=90)
    if result.returncode:
        # Authentication commands can return credential details; never echo their output.
        raise RuntimeError(f"{Path(command[0]).name} command failed (exit {result.returncode})")
    return result.stdout.strip()


def git(client: Path, *args: str) -> str:
    return capture(["git", *args], client)


def inspect_source(client: Path, branch: str) -> dict:
    if branch not in {"main", "dev2027"}:
        raise ValueError("Choose the main or dev2027 source list")
    policy = json.loads((client / "hosting-sources.json").read_text(encoding="utf-8"))
    if policy.get("schema") != "adaos.client.hosting_sources.v1":
        raise ValueError("Unknown Client Hosting source policy")
    all_sites = [site for sites in policy["sources"].values() for site in sites.values()]
    if len(all_sites) != len(set(all_sites)):
        raise ValueError("Hosting destinations overlap")
    sites = policy["sources"][branch]
    if set(sites) != {"management", "site-runtime"}:
        raise ValueError("Both Client Hosting destinations are required")
    actual_branch = git(client, "branch", "--show-current")
    revision = git(client, "rev-parse", "HEAD")
    remote = git(client, "ls-remote", "--exit-code", "origin", f"refs/heads/{branch}").split()[0]
    problems = []
    if actual_branch != branch:
        problems.append(f"Client checkout is on {actual_branch or 'detached HEAD'}, expected {branch}")
    if revision != remote:
        problems.append("Client HEAD differs from the current origin source-list head")
    if git(client, "status", "--porcelain=v1", "--untracked-files=all"):
        problems.append("Client checkout has uncommitted files; preserve or reconcile them before publication")
    return {"source_branch": branch, "source_commit": revision,
            "remote_commit": remote, "project": policy["project"], "sites": sites,
            "problems": problems}


def build_environment(source: dict, task: Path) -> dict[str, str]:
    env = os.environ.copy()
    development = source["source_branch"] == "dev2027"
    env.update({
        "SRC_BRANCH": source["source_branch"], "GITHUB_SHA": source["source_commit"],
        "PUBLIC_ROOT_BASE": env.get("PUBLIC_ROOT_BASE") or "https://api.inimatic.com",
        "PUBLIC_APP_BASE": f"https://{source['sites']['management']}.web.app" if development
                           else env.get("PUBLIC_APP_BASE") or "https://inimatic.com",
        "ADAOS_SITE_CLIENT_ORIGIN": f"https://{source['sites']['site-runtime']}.web.app",
        "PUBLIC_ADAOS_BASE": env.get("PUBLIC_ADAOS_BASE", ""),
        "PUBLIC_ADAOS_TOKEN": env.get("PUBLIC_ADAOS_TOKEN", ""),
        "PUBLIC_DEFAULT_ZONE_ID": env.get("PUBLIC_DEFAULT_ZONE_ID") or "ru",
        "PUBLIC_ZONES_JSON": env.get("PUBLIC_ZONES_JSON", ""),
        "ADAOS_HOSTING_STATE_DIR": str(task / "hosting"),
        "ADAOS_NATIVE_RELEASE_DIR": str(task / "native-releases"),
        "TMPDIR": str(task / "temp"), "TMP": str(task / "temp"), "TEMP": str(task / "temp"),
    })
    # Local node settings must not silently become public browser credentials/configuration.
    for name in ("ADAOS_BASE", "ADAOS_TOKEN", "ADAOS_APP_BASE", "ADAOS_ROOT_BASE",
                 "ADAOS_DEFAULT_ZONE_ID", "ADAOS_ZONES_JSON", "BUILD_VERSION", "SOURCE_DATE_EPOCH"):
        env.pop(name, None)
    return env


def browser_path() -> str | None:
    candidates = [os.environ.get("CHROME_BIN"), shutil.which("google-chrome"), shutil.which("chromium")]
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        if os.environ.get(variable):
            candidates.append(str(Path(os.environ[variable]) / "Google/Chrome/Application/chrome.exe"))
    return next((item for item in candidates if item and Path(item).is_file()), None)


def firebase_command(client: Path) -> list[str]:
    executable = client / "node_modules/firebase-tools/lib/bin/firebase.js"
    if not executable.is_file():
        raise RuntimeError("Client dependencies are missing; install the locked dependencies after storage admission")
    return ["node", str(executable)]


def check_firebase(client: Path, project: str) -> None:
    response = json.loads(capture([*firebase_command(client), "projects:list", "--json", "--non-interactive"], client))
    if response.get("status") != "success" or not any(
            item.get("projectId") == project for item in response.get("result", [])):
        raise RuntimeError("Firebase credentials cannot access the configured project")


def run(command: list[str], client: Path, env: dict[str, str]) -> None:
    subprocess.run(command, cwd=client, env=env, check=True)


def storage_command(reserve_gib: float) -> list[str]:
    return [sys.executable, "-m", "adaos", "maintenance", "workspace-check",
            "--workspace-root", str(ROOT), "--reserve-gib", str(reserve_gib), "--json"]


def publish(client: Path, branch: str, *, check_only: bool, deliver: bool, reserve_gib: float) -> dict:
    source = inspect_source(client, branch)
    for name in ("node", "npm", "git"):
        if not shutil.which(name):
            source["problems"].append(f"Required tool is missing: {name}")
    browser = browser_path()
    if not browser:
        source["problems"].append("Chrome is missing; install Chrome or set CHROME_BIN")
    if not deliver and not check_only:
        source["firebase_access"] = "not_required_for_build"
    elif check_only or (client / "node_modules/firebase-tools/lib/bin/firebase.js").is_file():
        try:
            check_firebase(client, source["project"])
            source["firebase_access"] = True
        except (RuntimeError, ValueError, OSError, subprocess.SubprocessError):
            source["firebase_access"] = False
            source["problems"].append("Firebase access is unavailable; run the Client's npm run firebase:login")
    else:
        source["firebase_access"] = "pending_locked_dependency_install"
    if check_only:
        storage = subprocess.run(storage_command(reserve_gib), cwd=ROOT, text=True,
                                 encoding="utf-8", capture_output=True, timeout=720)
        admission = json.loads(storage.stdout)
        source["workspace_admission"] = {key: admission[key] for key in (
            "ok", "reasons", "disk_free_bytes", "reserved_bytes", "scratch_disks")}
        source["workspace_admission"]["min_free_bytes"] = admission["policy"]["min_free_bytes"]
        if not admission["ok"] or storage.returncode:
            source["problems"].append("Workspace storage admission blocks the expected build allocation")
    if source["problems"] or check_only:
        return {"ok": not source["problems"], "mode": "check", **source}

    # Admission precedes npm installation, native downloads and either Angular build.
    subprocess.run(storage_command(reserve_gib), cwd=ROOT, check=True)
    from adaos.services.storage_hygiene import managed_storage_run

    task = ROOT / ".tmp" / ("client-hosting-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                             + "-" + uuid.uuid4().hex[:8])
    with managed_storage_run(task, owner="client-local-hosting", retention_class="client-hosting",
                             success_retain_seconds=7 * 86400, metadata=source) as admitted:
        (admitted / "temp").mkdir()
        env = build_environment(source, admitted)
        env["CHROME_BIN"] = browser
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        run([npm, "ci"], client, env)
        if deliver:
            check_firebase(client, source["project"])
            source["firebase_access"] = True
        run([npm, "run", "test:boundaries"], client, env)
        specs = sorted(str(item.relative_to(client)) for item in (client / "scripts").glob("*.spec.mjs"))
        run(["node", "--test", *specs, "scripts/test-zone-directory.mjs"], client, env)
        run(["node", "node_modules/@angular/cli/bin/ng.js", "test", "--watch=false", "--browsers=ChromeHeadless"], client, env)
        run(["node", "scripts/restore-native-downloads.mjs"], client, env)
        run([npm, "run", "build:hosting"], client, env)
        run(["node", "scripts/hosting-source.mjs", "config"], client, env)
        run(["node", "scripts/hosting-source.mjs", "verify-local"], client, env)
        if git(client, "rev-parse", "HEAD") != source["source_commit"] or git(
                client, "ls-remote", "--exit-code", "origin", f"refs/heads/{branch}").split()[0] != source["source_commit"]:
            raise RuntimeError("Source-list head changed during qualification; qualify the new head")
        if deliver:
            run([*firebase_command(client), "deploy", "--project", source["project"],
                 "--config", str(admitted / "hosting/firebase-selected.json"),
                 "--only", "hosting", "--non-interactive"], client, env)
            run(["node", "scripts/hosting-source.mjs", "verify-remote"], client, env)
        receipt = {"ok": True, "delivered": deliver, **source,
                   "qualification": "boundaries, tooling, browser contracts and both Hosting builds passed",
                   "hosting_receipt": str(admitted / "hosting/source-release.json")}
        (admitted / "local-publication.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", choices=("main", "dev2027"), required=True)
    parser.add_argument("--client-dir", type=Path, default=DEFAULT_CLIENT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Read-only source/toolchain/Firebase preflight")
    mode.add_argument("--publish", action="store_true", help="Publish only after the complete local qualification")
    parser.add_argument("--reserve-gib", type=float, default=3.0)
    args = parser.parse_args()
    if args.reserve_gib < 3:
        parser.error("Reserve at least 3 GiB for the locked dependencies, downloads and both builds")
    try:
        result = publish(args.client_dir.resolve(), args.branch, check_only=args.check,
                         deliver=args.publish, reserve_gib=args.reserve_gib)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result["ok"] else 1
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Client Hosting: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
